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


def _build_keywords(name: str, name_ja: str = "", category: str = "", company_name: str = "") -> str:
    """Build brand_keywords from brand name, company name, and category."""
    keywords = set()
    if name:
        keywords.add(name.strip())
        # Add lowercase version too
        keywords.add(name.strip().lower())
    if name_ja and name_ja != name:
        keywords.add(name_ja.strip())
    if company_name and company_name != name:
        keywords.add(company_name.strip())
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
            payload.category or "",
            payload.company_name or ""
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
                        company_name = :company_name,
                        name_ja = :name_ja,
                        updated_at = NOW()
                    WHERE lcj_brand_id = :bid
                """),
                {
                    "name": payload.name,
                    "keywords": keywords,
                    "logo_url": payload.logo_url or "",
                    "company_name": payload.company_name or "",
                    "name_ja": payload.name_ja or "",
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
                     password_hash, brand_keywords, lcj_brand_id, logo_url, company_name, name_ja, created_at, updated_at)
                    VALUES 
                    (:client_id, :name, :domain, '#FF2D55', 'bottom-right', '購入する', TRUE,
                     :password_hash, :keywords, :lcj_brand_id, :logo_url, :company_name, :name_ja, NOW(), NOW())
                """),
                {
                    "client_id": client_id,
                    "name": payload.name,
                    "domain": domain,
                    "password_hash": password_hash,
                    "keywords": keywords,
                    "lcj_brand_id": payload.lcj_brand_id,
                    "logo_url": payload.logo_url or "",
                    "company_name": payload.company_name or "",
                    "name_ja": payload.name_ja or "",
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
                brand.category or "",
                brand.company_name or ""
            )

            if existing:
                client_id = existing[0]
                await db.execute(
                    text("""
                        UPDATE widget_clients 
                        SET name = :name, brand_keywords = :keywords,
                            logo_url = :logo_url, company_name = :company_name,
                            name_ja = :name_ja, updated_at = NOW()
                        WHERE lcj_brand_id = :bid
                    """),
                    {
                        "name": brand.name,
                        "keywords": keywords,
                        "logo_url": brand.logo_url or "",
                        "company_name": brand.company_name or "",
                        "name_ja": brand.name_ja or "",
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
                         password_hash, brand_keywords, lcj_brand_id, logo_url, company_name, name_ja, created_at, updated_at)
                        VALUES 
                        (:client_id, :name, :domain, '#FF2D55', 'bottom-right', '購入する', TRUE,
                         :password_hash, :keywords, :lcj_brand_id, :logo_url, :company_name, :name_ja, NOW(), NOW())
                    """),
                    {
                        "client_id": client_id,
                        "name": brand.name,
                        "domain": domain,
                        "password_hash": password_hash,
                        "keywords": keywords,
                        "lcj_brand_id": brand.lcj_brand_id,
                        "logo_url": brand.logo_url or "",
                        "company_name": brand.company_name or "",
                        "name_ja": brand.name_ja or "",
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

            # Commit the brand upsert FIRST, before auto-assigning clips
            # (auto_assign_clips has its own try/except with rollback,
            #  which would undo the upsert if it ran before commit)
            await db.commit()

            # Auto-assign clips for this brand (after commit)
            await _auto_assign_clips(db, client_id, keywords)

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

    # Deactivate brands that exist in AitherHub but were NOT in the bulk sync payload
    # This handles cases where brands were deleted in LCJ Mall or IDs changed
    deactivated = 0
    try:
        synced_lcj_ids = [b.lcj_brand_id for b in payload.brands]
        if synced_lcj_ids:
            # Find active widget_clients with lcj_brand_id NOT in the synced list
            # Build dynamic placeholders for NOT IN clause (TiDB/MySQL compatible)
            placeholders = ", ".join([f":id_{i}" for i in range(len(synced_lcj_ids))])
            id_params = {f"id_{i}": lid for i, lid in enumerate(synced_lcj_ids)}

            stale_result = await db.execute(
                text(f"""
                    SELECT client_id, name, lcj_brand_id
                    FROM widget_clients
                    WHERE lcj_brand_id IS NOT NULL
                      AND is_active = TRUE
                      AND lcj_brand_id NOT IN ({placeholders})
                """),
                id_params
            )
            stale_brands = stale_result.fetchall()
            if stale_brands:
                stale_ids = [row[2] for row in stale_brands]
                await db.execute(
                    text(f"""
                        UPDATE widget_clients
                        SET is_active = FALSE, updated_at = NOW()
                        WHERE lcj_brand_id IS NOT NULL
                          AND lcj_brand_id NOT IN ({placeholders})
                          AND is_active = TRUE
                    """),
                    id_params
                )
                await db.commit()
                deactivated = len(stale_brands)
                for row in stale_brands:
                    logger.info(f"Deactivated stale brand: {row[1]} (lcj_brand_id={row[2]}, client_id={row[0]})")
                    results.append({
                        "lcj_brand_id": row[2],
                        "name": row[1],
                        "client_id": row[0],
                        "action": "deactivated",
                    })
    except Exception as e:
        logger.error(f"Bulk sync deactivation error: {e}")
        await db.rollback()

    return {
        "success": True,
        "summary": {
            "total": len(payload.brands),
            "created": created,
            "updated": updated,
            "deactivated": deactivated,
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
        SELECT REPLACE(UUID(), '-', ''), :client_id, vc.id, TRUE, NOW()
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
        await db.commit()
        logger.info(f"Auto-assigned {count} clips to client {client_id}")
        return count
    except Exception as e:
        logger.error(f"Auto-assign clips error for {client_id}: {e}")
        await db.rollback()
        return 0


# ─── Brand Clips API (for LCJ Mall to display clips in brand detail page) ───

@router.get("/sync/brand/{lcj_brand_id}/clips")
async def get_brand_clips(
    lcj_brand_id: int,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    _auth: bool = Depends(_verify_sync_secret),
):
    """
    Get clips assigned to a brand (identified by lcj_brand_id).
    Returns clip list with SAS URLs for display in LCJ Mall brand detail page.
    """
    try:
        from app.services.storage_service import generate_read_sas_from_url
    except ImportError:
        generate_read_sas_from_url = None

    # Find widget_client by lcj_brand_id
    client_result = await db.execute(
        text("SELECT client_id, name, brand_keywords FROM widget_clients WHERE lcj_brand_id = :bid AND is_active = TRUE"),
        {"bid": lcj_brand_id}
    )
    client_row = client_result.mappings().first()
    if not client_row:
        return {
            "success": True,
            "client_id": None,
            "brand_name": None,
            "clips": [],
            "total": 0,
            "message": "ブランドがAitherHubに同期されていません"
        }

    client_id = client_row["client_id"]

    # Get assigned clips with video info
    clips_result = await db.execute(
        text("""
            SELECT wca.clip_id, wca.sort_order,
                   COALESCE(wca.product_name, vc.product_name) as product_name,
                   wca.product_price, wca.product_image_url,
                   wca.product_url, wca.product_cart_url,
                   vc.clip_url, vc.thumbnail_url, vc.transcript_text,
                   vc.duration_sec, vc.liver_name, vc.created_at,
                   wca.is_active
            FROM widget_clip_assignments wca
            LEFT JOIN video_clips vc ON vc.id::text = wca.clip_id
            WHERE wca.client_id = :cid AND wca.is_active = TRUE
            ORDER BY wca.sort_order ASC
            LIMIT :lim OFFSET :off
        """),
        {"cid": client_id, "lim": limit, "off": offset}
    )

    clips = []
    for row in clips_result.mappings().all():
        clip = dict(row)
        # Generate SAS URLs
        if generate_read_sas_from_url:
            if clip.get("clip_url") and "blob.core.windows.net" in (clip["clip_url"] or ""):
                try:
                    clip["clip_url"] = generate_read_sas_from_url(clip["clip_url"])
                except Exception:
                    pass
            if clip.get("thumbnail_url") and "blob.core.windows.net" in (clip["thumbnail_url"] or ""):
                try:
                    clip["thumbnail_url"] = generate_read_sas_from_url(clip["thumbnail_url"])
                except Exception:
                    pass
        clips.append(clip)

    # Get total count
    count_result = await db.execute(
        text("SELECT COUNT(*) FROM widget_clip_assignments WHERE client_id = :cid AND is_active = TRUE"),
        {"cid": client_id}
    )
    total = count_result.scalar() or 0

    return {
        "success": True,
        "client_id": client_id,
        "brand_name": client_row["name"],
        "brand_keywords": client_row["brand_keywords"],
        "clips": clips,
        "total": total,
        "portal_url": f"https://www.aitherhub.com/brand?id={client_id}",
    }
