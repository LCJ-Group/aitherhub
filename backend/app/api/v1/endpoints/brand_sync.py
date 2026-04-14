"""
Brand Sync API — Receives webhook from LCJ Mall when brands are created/updated/deleted.

Automatically creates/updates widget_clients records in AitherHub,
sets brand_keywords from brand name, and auto-assigns matching clips.

Endpoints:
  POST /sync/brand           — Create or update a widget_client from LCJ Mall brand data
  POST /sync/brands/bulk     — Bulk import all brands from LCJ Mall
  GET  /sync/brands/status   — Get sync status (list of synced brands)

Authentication: Shared secret via X-Sync-Secret header.
"""

import hashlib
import logging
import os
import secrets
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db

logger = logging.getLogger("brand_sync")
router = APIRouter()

# ─── Authentication ───
SYNC_SECRET = os.getenv("BRAND_SYNC_SECRET", "aitherhub-brand-sync-2026")


def _verify_sync_secret(x_sync_secret: str = Header(None)):
    """Verify the shared sync secret."""
    if not x_sync_secret or x_sync_secret != SYNC_SECRET:
        raise HTTPException(status_code=401, detail="Invalid sync secret")
    return True


def _generate_client_id() -> str:
    """Generate a short unique client_id (8 hex chars)."""
    return secrets.token_hex(4)


def _generate_password() -> str:
    """Generate a random password for brand portal access."""
    return secrets.token_urlsafe(12)


def _hash_password(password: str) -> str:
    """Hash password with SHA-256 + salt."""
    salt = secrets.token_hex(16)
    hashed = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return f"{salt}:{hashed}"


def _build_keywords(name: str, name_ja: str = "", category: str = "") -> str:
    """Build brand_keywords from brand name and category."""
    keywords = set()
    if name:
        keywords.add(name.strip())
        # Add lowercase version too
        keywords.add(name.strip().lower())
    if name_ja and name_ja != name:
        keywords.add(name_ja.strip())
    if category:
        keywords.add(category.strip())
    return ", ".join(sorted(keywords))


# ─── Request/Response Models ───

class BrandSyncRequest(BaseModel):
    """Payload from LCJ Mall when a brand is created/updated."""
    lcj_brand_id: int
    name: str
    name_ja: Optional[str] = None
    company_name: Optional[str] = None
    category: Optional[str] = None
    logo_url: Optional[str] = None
    email: Optional[str] = None
    contact_person: Optional[str] = None
    status: Optional[str] = "進行中"
    action: str = "upsert"  # "upsert" or "delete"


class BrandBulkSyncRequest(BaseModel):
    """Payload for bulk brand import."""
    brands: List[BrandSyncRequest]


class BrandSyncResponse(BaseModel):
    success: bool
    client_id: Optional[str] = None
    action: str = ""
    message: str = ""
    portal_url: Optional[str] = None
    password: Optional[str] = None  # Only returned on first creation


# ─── Endpoints ───

@router.post("/sync/brand", response_model=BrandSyncResponse)
async def sync_brand(
    payload: BrandSyncRequest,
    db: AsyncSession = Depends(get_db),
    _auth: bool = Depends(_verify_sync_secret),
):
    """
    Create or update a widget_client from LCJ Mall brand data.
    
    - If lcj_brand_id already exists → update name, keywords, logo
    - If lcj_brand_id is new → create widget_client with auto-generated client_id and password
    - If action is "delete" → soft-delete (set is_active=false)
    """
    try:
        # Check if brand already synced
        result = await db.execute(
            text("SELECT client_id, name FROM widget_clients WHERE lcj_brand_id = :bid"),
            {"bid": payload.lcj_brand_id}
        )
        existing = result.fetchone()

        if payload.action == "delete":
            if existing:
                await db.execute(
                    text("UPDATE widget_clients SET is_active = FALSE, updated_at = NOW() WHERE lcj_brand_id = :bid"),
                    {"bid": payload.lcj_brand_id}
                )
                await db.commit()
                return BrandSyncResponse(
                    success=True,
                    client_id=existing[0],
                    action="deactivated",
                    message=f"ブランド '{existing[1]}' を無効化しました"
                )
            return BrandSyncResponse(
                success=True,
                action="skipped",
                message=f"LCJ brand_id={payload.lcj_brand_id} は未同期のためスキップ"
            )

        keywords = _build_keywords(
            payload.name,
            payload.name_ja or "",
            payload.category or ""
        )

        if existing:
            # UPDATE existing
            client_id = existing[0]
            await db.execute(
                text("""
                    UPDATE widget_clients 
                    SET name = :name,
                        brand_keywords = :keywords,
                        logo_url = :logo_url,
                        updated_at = NOW()
                    WHERE lcj_brand_id = :bid
                """),
                {
                    "name": payload.name,
                    "keywords": keywords,
                    "logo_url": payload.logo_url or "",
                    "bid": payload.lcj_brand_id,
                }
            )
            await db.commit()

            # Auto-assign matching clips
            assigned_count = await _auto_assign_clips(db, client_id, keywords)

            return BrandSyncResponse(
                success=True,
                client_id=client_id,
                action="updated",
                message=f"ブランド '{payload.name}' を更新しました（{assigned_count}件のクリップを自動紐付け）",
                portal_url=f"https://www.aitherhub.com/brand?id={client_id}",
            )
        else:
            # CREATE new widget_client
            client_id = _generate_client_id()
            raw_password = _generate_password()
            password_hash = _hash_password(raw_password)

            # Determine domain placeholder (brand's own EC site, to be configured later)
            domain = f"{payload.name.lower().replace(' ', '-')}.example.com"

            await db.execute(
                text("""
                    INSERT INTO widget_clients 
                    (client_id, name, domain, theme_color, position, cta_text, is_active,
                     password_hash, brand_keywords, lcj_brand_id, logo_url, created_at, updated_at)
                    VALUES 
                    (:client_id, :name, :domain, '#FF2D55', 'bottom-right', '購入する', TRUE,
                     :password_hash, :keywords, :lcj_brand_id, :logo_url, NOW(), NOW())
                """),
                {
                    "client_id": client_id,
                    "name": payload.name,
                    "domain": domain,
                    "password_hash": password_hash,
                    "keywords": keywords,
                    "lcj_brand_id": payload.lcj_brand_id,
                    "logo_url": payload.logo_url or "",
                }
            )
            await db.commit()

            # Auto-assign matching clips
            assigned_count = await _auto_assign_clips(db, client_id, keywords)

            return BrandSyncResponse(
                success=True,
                client_id=client_id,
                action="created",
                message=f"ブランド '{payload.name}' を新規作成しました（{assigned_count}件のクリップを自動紐付け）",
                portal_url=f"https://www.aitherhub.com/brand?id={client_id}",
                password=raw_password,  # Return password only on creation
            )

    except Exception as e:
        logger.exception(f"brand_sync error: {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sync/brands/bulk")
async def sync_brands_bulk(
    payload: BrandBulkSyncRequest,
    db: AsyncSession = Depends(get_db),
    _auth: bool = Depends(_verify_sync_secret),
):
    """
    Bulk import brands from LCJ Mall.
    Processes each brand individually and returns summary.
    """
    results = []
    created = 0
    updated = 0
    errors = 0

    for brand in payload.brands:
        try:
            # Check if already exists
            result = await db.execute(
                text("SELECT client_id FROM widget_clients WHERE lcj_brand_id = :bid"),
                {"bid": brand.lcj_brand_id}
            )
            existing = result.fetchone()

            keywords = _build_keywords(
                brand.name,
                brand.name_ja or "",
                brand.category or ""
            )

            if existing:
                client_id = existing[0]
                await db.execute(
                    text("""
                        UPDATE widget_clients 
                        SET name = :name, brand_keywords = :keywords,
                            logo_url = :logo_url, updated_at = NOW()
                        WHERE lcj_brand_id = :bid
                    """),
                    {
                        "name": brand.name,
                        "keywords": keywords,
                        "logo_url": brand.logo_url or "",
                        "bid": brand.lcj_brand_id,
                    }
                )
                updated += 1
                results.append({
                    "lcj_brand_id": brand.lcj_brand_id,
                    "name": brand.name,
                    "client_id": client_id,
                    "action": "updated",
                })
            else:
                client_id = _generate_client_id()
                raw_password = _generate_password()
                password_hash = _hash_password(raw_password)
                domain = f"{brand.name.lower().replace(' ', '-')}.example.com"

                await db.execute(
                    text("""
                        INSERT INTO widget_clients 
                        (client_id, name, domain, theme_color, position, cta_text, is_active,
                         password_hash, brand_keywords, lcj_brand_id, logo_url, created_at, updated_at)
                        VALUES 
                        (:client_id, :name, :domain, '#FF2D55', 'bottom-right', '購入する', TRUE,
                         :password_hash, :keywords, :lcj_brand_id, :logo_url, NOW(), NOW())
                    """),
                    {
                        "client_id": client_id,
                        "name": brand.name,
                        "domain": domain,
                        "password_hash": password_hash,
                        "keywords": keywords,
                        "lcj_brand_id": brand.lcj_brand_id,
                        "logo_url": brand.logo_url or "",
                    }
                )
                created += 1
                results.append({
                    "lcj_brand_id": brand.lcj_brand_id,
                    "name": brand.name,
                    "client_id": client_id,
                    "action": "created",
                    "password": raw_password,
                    "portal_url": f"https://www.aitherhub.com/brand?id={client_id}",
                })

            # Auto-assign clips for this brand
            await _auto_assign_clips(db, client_id, keywords)

            # Commit after each successful brand
            await db.commit()

        except Exception as e:
            logger.error(f"Bulk sync error for brand {brand.lcj_brand_id}: {e}")
            await db.rollback()  # Rollback failed transaction before continuing
            errors += 1
            results.append({
                "lcj_brand_id": brand.lcj_brand_id,
                "name": brand.name,
                "action": "error",
                "error": str(e),
            })

    return {
        "success": True,
        "summary": {
            "total": len(payload.brands),
            "created": created,
            "updated": updated,
            "errors": errors,
        },
        "results": results,
    }


@router.get("/sync/brands/status")
async def sync_brands_status(
    db: AsyncSession = Depends(get_db),
    _auth: bool = Depends(_verify_sync_secret),
):
    """Get list of all synced brands with their AitherHub client_ids."""
    result = await db.execute(
        text("""
            SELECT client_id, name, lcj_brand_id, brand_keywords, is_active,
                   logo_url, created_at, updated_at
            FROM widget_clients
            WHERE lcj_brand_id IS NOT NULL
            ORDER BY created_at DESC
        """)
    )
    rows = result.fetchall()

    brands = []
    for row in rows:
        brands.append({
            "client_id": row[0],
            "name": row[1],
            "lcj_brand_id": row[2],
            "brand_keywords": row[3],
            "is_active": row[4],
            "logo_url": row[5],
            "created_at": row[6].isoformat() if row[6] else None,
            "updated_at": row[7].isoformat() if row[7] else None,
            "portal_url": f"https://www.aitherhub.com/brand?id={row[0]}",
        })

    return {
        "success": True,
        "count": len(brands),
        "brands": brands,
    }


# ─── Helper: Auto-assign matching clips ───

async def _auto_assign_clips(db: AsyncSession, client_id: str, keywords: str) -> int:
    """
    Auto-assign clips that match brand keywords.
    Uses video_product_exposures.brand_name and video_clips.transcript_text.
    Returns the number of newly assigned clips.
    """
    if not keywords:
        return 0

    keyword_list = [k.strip() for k in keywords.split(",") if k.strip()]
    if not keyword_list:
        return 0

    # Build ILIKE conditions for each keyword
    conditions = []
    params = {"client_id": client_id}
    for i, kw in enumerate(keyword_list):
        param_name = f"kw_{i}"
        conditions.append(f"""
            vc.id IN (
                SELECT DISTINCT vpe.clip_id FROM video_product_exposures vpe
                WHERE LOWER(vpe.brand_name) LIKE LOWER(:{param_name}_pct)
            )
            OR LOWER(vc.transcript_text) LIKE LOWER(:{param_name}_pct)
        """)
        params[f"{param_name}_pct"] = f"%{kw}%"

    where_clause = " OR ".join(conditions)

    # Find matching clips not already assigned
    query = f"""
        INSERT INTO widget_clip_assignments (id, client_id, clip_id, is_active, created_at)
        SELECT gen_random_uuid()::text, :client_id, vc.id, TRUE, NOW()
        FROM video_clips vc
        WHERE ({where_clause})
          AND vc.id NOT IN (
              SELECT clip_id FROM widget_clip_assignments WHERE client_id = :client_id
          )
          AND vc.clip_url IS NOT NULL
        LIMIT 50
    """

    try:
        result = await db.execute(text(query), params)
        count = result.rowcount or 0
        logger.info(f"Auto-assigned {count} clips to client {client_id}")
        return count
    except Exception as e:
        logger.error(f"Auto-assign clips error for {client_id}: {e}")
        return 0
