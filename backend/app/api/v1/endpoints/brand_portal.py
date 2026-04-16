"""
Brand Portal API — Self-service endpoints for widget clients (brands).

Allows brands to:
  - Log in with client_id + password
  - Upload videos directly to Azure Blob
  - Manage their clips (list, assign to widget, set product info)
  - View analytics for their widget
  - Get their GTM tag snippet

Authentication: JWT token issued on login, validated via Authorization header.

Endpoints:
  POST /brand/login                    — Authenticate with client_id + password
  GET  /brand/me                       — Get current brand profile
  POST /brand/upload/sas               — Get SAS URL for direct video upload
  POST /brand/clips                    — Register uploaded video as a clip
  GET  /brand/clips                    — List brand's clips (uploaded + assigned)
  PUT  /brand/clips/{clip_id}          — Update clip product info
  POST /brand/widget/clips             — Assign clip to widget
  DELETE /brand/widget/clips/{clip_id} — Remove clip from widget
  GET  /brand/widget/clips             — List widget-assigned clips
  GET  /brand/analytics                — Brand analytics summary
  GET  /brand/gtm-tag                  — Get GTM tag snippet
"""
import hashlib
import hmac
import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db

try:
    from app.services.storage_service import generate_read_sas_from_url, generate_upload_sas
except ImportError:
    generate_read_sas_from_url = None
    generate_upload_sas = None

logger = logging.getLogger("brand_portal")
router = APIRouter()

# ─── JWT-like token (simple HMAC-based, no external deps) ───

BRAND_SECRET = os.getenv("BRAND_JWT_SECRET", "aitherhub-brand-secret-2026")
TOKEN_EXPIRY_HOURS = 72  # 3 days


def _hash_password(password: str) -> str:
    """Hash password with SHA-256 + salt."""
    salt = secrets.token_hex(16)
    hashed = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return f"{salt}:{hashed}"


def _verify_password(password: str, stored_hash: str) -> bool:
    """Verify password against stored hash."""
    if ":" not in stored_hash:
        return False
    salt, expected_hash = stored_hash.split(":", 1)
    actual_hash = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return hmac.compare_digest(actual_hash, expected_hash)


def _create_token(client_id: str) -> str:
    """Create a simple HMAC-signed token."""
    payload = {
        "client_id": client_id,
        "exp": (datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRY_HOURS)).isoformat(),
    }
    payload_json = json.dumps(payload, sort_keys=True)
    import base64
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode()
    signature = hmac.new(BRAND_SECRET.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{signature}"


def _verify_token(token: str) -> Optional[str]:
    """Verify token and return client_id, or None if invalid."""
    try:
        import base64
        parts = token.split(".")
        if len(parts) != 2:
            return None
        payload_b64, signature = parts
        expected_sig = hmac.new(BRAND_SECRET.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return None
        payload_json = base64.urlsafe_b64decode(payload_b64).decode()
        payload = json.loads(payload_json)
        # Check expiry
        exp = datetime.fromisoformat(payload["exp"])
        if datetime.now(timezone.utc) > exp:
            return None
        return payload.get("client_id")
    except Exception:
        return None


async def _get_brand_client_id(
    authorization: Optional[str] = Header(None),
) -> str:
    """Extract and verify brand client_id from Authorization header."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")
    token = authorization.replace("Bearer ", "").strip()
    client_id = _verify_token(token)
    if not client_id:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return client_id


# ─── Pydantic Schemas ───

class BrandLoginRequest(BaseModel):
    client_id: str = Field(..., description="Brand client ID")
    password: str = Field(..., description="Brand password")


class ClipProductUpdate(BaseModel):
    product_name: Optional[str] = None
    product_price: Optional[str] = None
    product_image_url: Optional[str] = None
    product_url: Optional[str] = None
    product_cart_url: Optional[str] = None
    page_url_pattern: Optional[str] = None


class ClipRegisterRequest(BaseModel):
    blob_url: str = Field(..., description="Azure Blob URL of uploaded video")
    title: Optional[str] = Field(default=None, description="Clip title")
    product_name: Optional[str] = None
    product_price: Optional[str] = None
    product_url: Optional[str] = None


# ─── Auth Endpoints ───

@router.post("/brand/login")
async def brand_login(
    payload: BrandLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate brand with client_id + password."""
    result = await db.execute(
        text("SELECT client_id, name, domain, password_hash FROM widget_clients WHERE client_id = :cid"),
        {"cid": payload.client_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid client ID or password")

    if not row["password_hash"]:
        raise HTTPException(status_code=401, detail="Brand portal not activated. Contact admin.")

    if not _verify_password(payload.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid client ID or password")

    token = _create_token(payload.client_id)
    return {
        "token": token,
        "client_id": row["client_id"],
        "name": row["name"],
        "domain": row["domain"],
        "expires_in_hours": TOKEN_EXPIRY_HOURS,
    }


@router.get("/brand/me")
async def brand_profile(
    client_id: str = Depends(_get_brand_client_id),
    db: AsyncSession = Depends(get_db),
):
    """Get current brand profile."""
    result = await db.execute(
        text("""
            SELECT client_id, name, domain, theme_color, position,
                   cta_text, cta_url_template, cart_selector, is_active, created_at
            FROM widget_clients WHERE client_id = :cid
        """),
        {"cid": client_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Brand not found")

    # Count assigned clips
    clip_count = await db.execute(
        text("SELECT COUNT(*) FROM widget_clip_assignments WHERE client_id = :cid AND is_active = TRUE"),
        {"cid": client_id},
    )

    # Count uploaded clips
    upload_count = await db.execute(
        text("SELECT COUNT(*) FROM video_clips WHERE uploaded_by_brand = :cid"),
        {"cid": client_id},
    )

    return {
        **dict(row),
        "assigned_clip_count": clip_count.scalar() or 0,
        "uploaded_clip_count": upload_count.scalar() or 0,
    }


# ─── Upload Endpoints ───

@router.post("/brand/upload/sas")
async def brand_upload_sas(
    payload: dict,
    client_id: str = Depends(_get_brand_client_id),
    db: AsyncSession = Depends(get_db),
):
    """Generate SAS URL for direct video upload to Azure Blob."""

    filename = payload.get("filename", "video.mp4")
    # Use brand-specific folder: brand_{client_id}/{uuid}/filename
    brand_email = f"brand_{client_id}"
    video_id = str(uuid.uuid4())

    try:
        upload_url, blob_url, _vid, expiry = await generate_upload_sas(
            email=brand_email,
            video_id=video_id,
            filename=filename,
        )
        return {
            "upload_url": upload_url,
            "blob_url": blob_url,
            "video_id": video_id,
            "expiry": expiry.isoformat(),
        }
    except Exception as e:
        logger.error(f"SAS generation failed for brand {client_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate upload URL")


@router.post("/brand/clips")
async def brand_register_clip(
    payload: ClipRegisterRequest,
    client_id: str = Depends(_get_brand_client_id),
    db: AsyncSession = Depends(get_db),
):
    """Register an uploaded video as a clip in the database."""
    clip_id = str(uuid.uuid4())

    await db.execute(
        text("""
            INSERT INTO video_clips (id, clip_url, product_name, product_price,
                                     uploaded_by_brand, status, created_at)
            VALUES (:id, :clip_url, :product_name, :product_price,
                    :brand_id, 'uploaded', NOW())
        """),
        {
            "id": clip_id,
            "clip_url": payload.blob_url,
            "product_name": payload.product_name or payload.title,
            "product_price": payload.product_price,
            "brand_id": client_id,
        },
    )
    await db.commit()

    return {
        "clip_id": clip_id,
        "clip_url": payload.blob_url,
        "status": "uploaded",
    }


# ─── Clip Management Endpoints ───

@router.get("/brand/clips")
async def brand_list_clips(
    client_id: str = Depends(_get_brand_client_id),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List all clips available to this brand (uploaded by brand + assigned to widget)."""
    try:
        return await _brand_list_clips_impl(client_id, limit, offset, db)
    except Exception as e:
        logger.exception(f"brand_list_clips error for {client_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _brand_list_clips_impl(client_id: str, limit: int, offset: int, db: AsyncSession):

    # Get clips uploaded by this brand
    result = await db.execute(
        text("""
            SELECT vc.id as clip_id, vc.clip_url, vc.exported_url, vc.thumbnail_url,
                   vc.product_name, vc.product_price, vc.transcript_text,
                   vc.duration_sec, vc.liver_name, vc.created_at,
                   wca.id as assignment_id,
                   wca.is_active as widget_active,
                   wca.product_name as widget_product_name,
                   wca.product_price as widget_product_price,
                   wca.product_image_url as widget_product_image_url,
                   wca.product_url as widget_product_url,
                   wca.product_cart_url as widget_product_cart_url,
                   wca.sort_order,
                   CASE WHEN vc.uploaded_by_brand = :cid THEN TRUE ELSE FALSE END as is_own_upload
            FROM video_clips vc
            LEFT JOIN widget_clip_assignments wca
                ON wca.clip_id::uuid = vc.id AND wca.client_id = :cid
            WHERE vc.uploaded_by_brand = :cid
               OR (wca.client_id = :cid AND wca.is_active = TRUE)
            ORDER BY vc.created_at DESC
            LIMIT :lim OFFSET :off
        """),
        {"cid": client_id, "lim": limit, "off": offset},
    )

    clips = []
    for row in result.mappings().all():
        clip = dict(row)
        # Use exported_url (subtitled version) if available
        if clip.get("exported_url"):
            clip["original_clip_url"] = clip["clip_url"]
            clip["clip_url"] = clip["exported_url"]
        # Generate SAS URLs
        if generate_read_sas_from_url:
            if clip.get("clip_url") and "blob.core.windows.net" in (clip["clip_url"] or ""):
                try:
                    clip["clip_url"] = generate_read_sas_from_url(clip["clip_url"])
                except Exception:
                    pass
            if clip.get("original_clip_url") and "blob.core.windows.net" in (clip["original_clip_url"] or ""):
                try:
                    clip["original_clip_url"] = generate_read_sas_from_url(clip["original_clip_url"])
                except Exception:
                    pass
            if clip.get("thumbnail_url") and "blob.core.windows.net" in (clip["thumbnail_url"] or ""):
                try:
                    clip["thumbnail_url"] = generate_read_sas_from_url(clip["thumbnail_url"])
                except Exception:
                    pass
        clips.append(clip)

    # Total count
    count_result = await db.execute(
        text("""
            SELECT COUNT(DISTINCT vc.id)
            FROM video_clips vc
            LEFT JOIN widget_clip_assignments wca
                ON wca.clip_id::uuid = vc.id AND wca.client_id = :cid
            WHERE vc.uploaded_by_brand = :cid
               OR (wca.client_id = :cid AND wca.is_active = TRUE)
        """),
        {"cid": client_id},
    )
    total = count_result.scalar() or 0

    return {"clips": clips, "total": total}


@router.put("/brand/clips/{clip_id}")
async def brand_update_clip_product(
    clip_id: str,
    payload: ClipProductUpdate,
    client_id: str = Depends(_get_brand_client_id),
    db: AsyncSession = Depends(get_db),
):
    """Update product info for a clip assigned to this brand's widget."""
    # Verify clip belongs to this brand (either uploaded or assigned)
    check = await db.execute(
        text("""
            SELECT 1 FROM widget_clip_assignments
            WHERE client_id = :cid AND clip_id = :clip_id AND is_active = TRUE
        """),
        {"cid": client_id, "clip_id": clip_id},
    )
    if not check.first():
        raise HTTPException(status_code=404, detail="Clip not assigned to your widget")

    updates = {}
    if payload.product_name is not None:
        updates["product_name"] = payload.product_name
    if payload.product_price is not None:
        updates["product_price"] = payload.product_price
    if payload.product_image_url is not None:
        updates["product_image_url"] = payload.product_image_url
    if payload.product_url is not None:
        updates["product_url"] = payload.product_url
    if payload.product_cart_url is not None:
        updates["product_cart_url"] = payload.product_cart_url
    if payload.page_url_pattern is not None:
        updates["page_url_pattern"] = payload.page_url_pattern

    if not updates:
        return {"status": "no changes"}

    set_clauses = ", ".join(f"{k} = :{k}" for k in updates)
    updates["cid"] = client_id
    updates["clip_id"] = clip_id

    await db.execute(
        text(f"""
            UPDATE widget_clip_assignments
            SET {set_clauses}
            WHERE client_id = :cid AND clip_id = :clip_id
        """),
        updates,
    )
    await db.commit()

    return {"status": "updated", "clip_id": clip_id}


# ─── Widget Assignment Endpoints ───

@router.post("/brand/widget/clips")
async def brand_assign_clip_to_widget(
    payload: dict,
    client_id: str = Depends(_get_brand_client_id),
    db: AsyncSession = Depends(get_db),
):
    """Assign a clip to the brand's widget."""
    clip_id = payload.get("clip_id")
    if not clip_id:
        raise HTTPException(status_code=400, detail="clip_id is required")

    # Verify clip exists
    clip_check = await db.execute(
        text("SELECT id FROM video_clips WHERE id = :id"),
        {"id": clip_id},
    )
    if not clip_check.first():
        raise HTTPException(status_code=404, detail="Clip not found")

    # Get next sort order
    max_order = await db.execute(
        text("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM widget_clip_assignments WHERE client_id = :cid"),
        {"cid": client_id},
    )
    next_order = max_order.scalar() or 0

    await db.execute(
        text("""
            INSERT INTO widget_clip_assignments
                (id, client_id, clip_id, sort_order, is_active, created_at,
                 product_name, product_price, product_image_url, product_url, product_cart_url)
            VALUES
                (:id, :cid, :clip_id, :sort_order, TRUE, NOW(),
                 :product_name, :product_price, :product_image_url, :product_url, :product_cart_url)
            ON CONFLICT (client_id, clip_id) DO UPDATE
            SET is_active = TRUE, sort_order = :sort_order
        """),
        {
            "id": str(uuid.uuid4()),
            "cid": client_id,
            "clip_id": clip_id,
            "sort_order": next_order,
            "product_name": payload.get("product_name"),
            "product_price": payload.get("product_price"),
            "product_image_url": payload.get("product_image_url"),
            "product_url": payload.get("product_url"),
            "product_cart_url": payload.get("product_cart_url"),
        },
    )
    await db.commit()

    return {"status": "assigned", "clip_id": clip_id}


@router.delete("/brand/widget/clips/{clip_id}")
async def brand_remove_clip_from_widget(
    clip_id: str,
    client_id: str = Depends(_get_brand_client_id),
    db: AsyncSession = Depends(get_db),
):
    """Remove a clip from the brand's widget."""
    await db.execute(
        text("""
            UPDATE widget_clip_assignments
            SET is_active = FALSE
            WHERE client_id = :cid AND clip_id = :clip_id
        """),
        {"cid": client_id, "clip_id": clip_id},
    )
    await db.commit()
    return {"status": "removed", "clip_id": clip_id}


@router.get("/brand/widget/clips")
async def brand_list_widget_clips(
    client_id: str = Depends(_get_brand_client_id),
    db: AsyncSession = Depends(get_db),
):
    """List clips currently assigned to the brand's widget."""
    try:
        return await _brand_list_widget_clips_impl(client_id, db)
    except Exception as e:
        logger.exception(f"brand_list_widget_clips error for {client_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _brand_list_widget_clips_impl(client_id: str, db: AsyncSession):
    result = await db.execute(
        text("""
            SELECT wca.clip_id, wca.sort_order, wca.page_url_pattern,
                   wca.product_name, wca.product_price, wca.product_image_url,
                   wca.product_url, wca.product_cart_url,
                   vc.clip_url, vc.exported_url, vc.thumbnail_url, vc.transcript_text,
                   vc.duration_sec, vc.liver_name
            FROM widget_clip_assignments wca
            JOIN video_clips vc ON vc.id = wca.clip_id::uuid
            WHERE wca.client_id = :cid AND wca.is_active = TRUE
            ORDER BY wca.sort_order ASC
        """),
        {"cid": client_id},
    )

    clips = []
    for row in result.mappings().all():
        clip = dict(row)
        # Use exported_url (subtitled version) if available
        if clip.get("exported_url"):
            clip["original_clip_url"] = clip["clip_url"]
            clip["clip_url"] = clip["exported_url"]
        if generate_read_sas_from_url:
            if clip.get("clip_url") and "blob.core.windows.net" in (clip["clip_url"] or ""):
                try:
                    clip["clip_url"] = generate_read_sas_from_url(clip["clip_url"])
                except Exception:
                    pass
            if clip.get("original_clip_url") and "blob.core.windows.net" in (clip["original_clip_url"] or ""):
                try:
                    clip["original_clip_url"] = generate_read_sas_from_url(clip["original_clip_url"])
                except Exception:
                    pass
            if clip.get("thumbnail_url") and "blob.core.windows.net" in (clip["thumbnail_url"] or ""):
                try:
                    clip["thumbnail_url"] = generate_read_sas_from_url(clip["thumbnail_url"])
                except Exception:
                    pass
        clips.append(clip)

    return {"clips": clips}


# ─── Analytics Endpoint ───

@router.get("/brand/analytics")
async def brand_analytics(
    client_id: str = Depends(_get_brand_client_id),
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """Get analytics summary for the brand's widget."""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # Event counts by type
    events_result = await db.execute(
        text("""
            SELECT event_type, COUNT(*) as count
            FROM widget_tracking_events
            WHERE client_id = :cid AND created_at >= :since
            GROUP BY event_type
            ORDER BY count DESC
        """),
        {"cid": client_id, "since": since},
    )
    events = {row["event_type"]: row["count"] for row in events_result.mappings().all()}

    # Daily views
    daily_result = await db.execute(
        text("""
            SELECT DATE(created_at) as day, COUNT(*) as views
            FROM widget_tracking_events
            WHERE client_id = :cid AND event_type = 'video_play' AND created_at >= :since
            GROUP BY DATE(created_at)
            ORDER BY day DESC
            LIMIT 30
        """),
        {"cid": client_id, "since": since},
    )
    daily_views = [{"day": str(row["day"]), "views": row["views"]} for row in daily_result.mappings().all()]

    # Top clips by plays
    top_clips_result = await db.execute(
        text("""
            SELECT clip_id, COUNT(*) as plays
            FROM widget_tracking_events
            WHERE client_id = :cid AND event_type = 'video_play' AND created_at >= :since
                  AND clip_id IS NOT NULL
            GROUP BY clip_id
            ORDER BY plays DESC
            LIMIT 10
        """),
        {"cid": client_id, "since": since},
    )
    top_clips = [{"clip_id": row["clip_id"], "plays": row["plays"]} for row in top_clips_result.mappings().all()]

    return {
        "period_days": days,
        "events": events,
        "total_views": events.get("video_play", 0),
        "total_clicks": events.get("cta_click", 0) + events.get("purchase_click", 0) + events.get("add_to_cart", 0),
        "total_conversions": events.get("conversion", 0),
        "daily_views": daily_views,
        "top_clips": top_clips,
    }


# ─── Recommended Clips Endpoint (auto-match by brand keywords + product exposures) ───

@router.get("/brand/recommended-clips")
async def brand_recommended_clips(
    client_id: str = Depends(_get_brand_client_id),
    q: Optional[str] = Query(default=None, description="Additional search query"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Get recommended clips for this brand based on:
    1. video_product_exposures.brand_name matching brand keywords
    2. transcript_text containing brand keywords
    3. Additional search query (optional)
    """
    try:
        # Get brand keywords
        kw_result = await db.execute(
            text("SELECT brand_keywords FROM widget_clients WHERE client_id = :cid"),
            {"cid": client_id},
        )
        kw_row = kw_result.mappings().first()
        brand_keywords = []
        if kw_row and kw_row["brand_keywords"]:
            brand_keywords = [k.strip() for k in kw_row["brand_keywords"].split(",") if k.strip()]

        if q:
            brand_keywords.append(q.strip())

        if not brand_keywords:
            return {"clips": [], "total": 0, "message": "ブランドキーワードが設定されていません。設定タブでキーワードを追加してください。"}

        # Build ILIKE conditions for transcript_text
        # Use two separate fast queries instead of slow LEFT JOIN
        conditions = []
        params = {"cid": client_id, "lim": limit, "off": offset}
        for i, kw in enumerate(brand_keywords):
            conditions.append(f"vc.transcript_text ILIKE :kw{i}")
            params[f"kw{i}"] = f"%{kw}%"

        # VPE conditions (for subquery)
        vpe_conditions = []
        vpe_params = {}
        for i, kw in enumerate(brand_keywords):
            vpe_conditions.append(f"vpe.brand_name ILIKE :bkw{i} OR vpe.product_name ILIKE :bkw{i}")
            vpe_params[f"bkw{i}"] = f"%{kw}%"

        where_transcript = " OR ".join(conditions)
        where_vpe = " OR ".join(vpe_conditions)

        # Get clips already assigned to this brand's widget (to mark them)
        assigned_result = await db.execute(
            text("SELECT clip_id FROM widget_clip_assignments WHERE client_id = :cid AND is_active = TRUE"),
            {"cid": client_id},
        )
        assigned_ids = {str(r["clip_id"]) for r in assigned_result.mappings().all()}

        # Optimized: Use UNION of two fast queries instead of slow LEFT JOIN
        # Query 1: Match by transcript_text
        # Query 2: Match by video_product_exposures (subquery for video_ids)
        all_params = {**params, **vpe_params}

        result = await db.execute(
            text(f"""
                SELECT id as clip_id, clip_url, exported_url, thumbnail_url,
                       product_name, product_price, transcript_text,
                       duration_sec, liver_name, created_at, video_id
                FROM (
                    SELECT DISTINCT vc.id, vc.clip_url, vc.exported_url, vc.thumbnail_url,
                           vc.product_name, vc.product_price, vc.transcript_text,
                           vc.duration_sec, vc.liver_name, vc.created_at, vc.video_id
                    FROM video_clips vc
                    WHERE vc.clip_url IS NOT NULL
                      AND vc.status != 'deleted'
                      AND ({where_transcript})
                    UNION
                    SELECT DISTINCT vc.id, vc.clip_url, vc.exported_url, vc.thumbnail_url,
                           vc.product_name, vc.product_price, vc.transcript_text,
                           vc.duration_sec, vc.liver_name, vc.created_at, vc.video_id
                    FROM video_clips vc
                    WHERE vc.clip_url IS NOT NULL
                      AND vc.status != 'deleted'
                      AND vc.video_id IN (
                          SELECT DISTINCT vpe.video_id FROM video_product_exposures vpe
                          WHERE {where_vpe}
                      )
                ) sub
                ORDER BY created_at DESC
                LIMIT :lim OFFSET :off
            """),
            all_params,
        )

        clips = []
        for row in result.mappings().all():
            clip = dict(row)
            clip["is_assigned"] = str(clip["clip_id"]) in assigned_ids
            # Use exported_url (subtitled version) if available
            if clip.get("exported_url"):
                clip["original_clip_url"] = clip["clip_url"]
                clip["clip_url"] = clip["exported_url"]
            # Generate SAS URLs
            if generate_read_sas_from_url:
                if clip.get("clip_url") and "blob.core.windows.net" in (clip["clip_url"] or ""):
                    try:
                        clip["clip_url"] = generate_read_sas_from_url(clip["clip_url"])
                    except Exception:
                        pass
                if clip.get("original_clip_url") and "blob.core.windows.net" in (clip["original_clip_url"] or ""):
                    try:
                        clip["original_clip_url"] = generate_read_sas_from_url(clip["original_clip_url"])
                    except Exception:
                        pass
                if clip.get("thumbnail_url") and "blob.core.windows.net" in (clip["thumbnail_url"] or ""):
                    try:
                        clip["thumbnail_url"] = generate_read_sas_from_url(clip["thumbnail_url"])
                    except Exception:
                        pass
            clips.append(clip)

        # Optimized total count using same UNION approach
        count_result = await db.execute(
            text(f"""
                SELECT COUNT(*) FROM (
                    SELECT vc.id FROM video_clips vc
                    WHERE vc.clip_url IS NOT NULL AND vc.status != 'deleted'
                      AND ({where_transcript})
                    UNION
                    SELECT vc.id FROM video_clips vc
                    WHERE vc.clip_url IS NOT NULL AND vc.status != 'deleted'
                      AND vc.video_id IN (
                          SELECT DISTINCT vpe.video_id FROM video_product_exposures vpe
                          WHERE {where_vpe}
                      )
                ) cnt
            """),
            all_params,
        )
        total = count_result.scalar() or 0

        return {
            "clips": clips,
            "total": total,
            "keywords": brand_keywords,
        }
    except Exception as e:
        logger.exception(f"brand_recommended_clips error for {client_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Download Endpoint ───

@router.get("/brand/clips/{clip_id}/download")
async def brand_download_clip(
    clip_id: str,
    client_id: str = Depends(_get_brand_client_id),
    db: AsyncSession = Depends(get_db),
):
    """Get a download URL for a clip (SAS URL with longer expiry for download)."""
    # Verify clip exists
    result = await db.execute(
        text("SELECT clip_url, product_name, thumbnail_url FROM video_clips WHERE id = :id"),
        {"id": clip_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Clip not found")

    clip_url = row["clip_url"]
    if not clip_url:
        raise HTTPException(status_code=404, detail="No video URL available")

    # Generate SAS URL with longer expiry for download
    download_url = clip_url
    if generate_read_sas_from_url and "blob.core.windows.net" in clip_url:
        try:
            download_url = generate_read_sas_from_url(clip_url)
        except Exception:
            pass

    return {
        "clip_id": clip_id,
        "download_url": download_url,
        "product_name": row["product_name"],
        "filename": f"{row['product_name'] or 'clip'}_{clip_id[:8]}.mp4",
    }


# ─── Brand Keywords Update Endpoint ───

@router.put("/brand/keywords")
async def brand_update_keywords(
    payload: dict,
    client_id: str = Depends(_get_brand_client_id),
    db: AsyncSession = Depends(get_db),
):
    """Update brand keywords for recommended clip matching."""
    keywords = payload.get("keywords", "")
    await db.execute(
        text("UPDATE widget_clients SET brand_keywords = :kw WHERE client_id = :cid"),
        {"cid": client_id, "kw": keywords},
    )
    await db.commit()
    return {"status": "updated", "keywords": keywords}


# ─── GTM Tag Endpoint ───

@router.get("/brand/gtm-tag")
async def brand_gtm_tag(
    client_id: str = Depends(_get_brand_client_id),
    db: AsyncSession = Depends(get_db),
):
    """Get GTM tag snippet for the brand."""
    result = await db.execute(
        text("SELECT name, domain FROM widget_clients WHERE client_id = :cid"),
        {"cid": client_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Brand not found")

    script_url = "https://www.aitherhub.com/widget/loader.js"
    tag_html = f"""<!-- AitherHub Widget for {row['name']} -->
<script>
(function(){{
  var s=document.createElement('script');
  s.src='{script_url}';
  s.dataset.clientId='{client_id}';
  s.async=true;
  document.head.appendChild(s);
}})();
</script>"""

    return {
        "client_id": client_id,
        "name": row["name"],
        "domain": row["domain"],
        "tag_html": tag_html,
        "instructions": "このタグをGTMのカスタムHTMLタグとして追加するか、サイトの<head>に直接貼り付けてください。",
    }
