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
        _vid, upload_url, blob_url, expiry = await generate_upload_sas(
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

    try:
        # Brand-uploaded clips have no parent video; video_id / phase_index
        # NOT NULL constraints are dropped at startup so we can omit them.
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
    except Exception as e:
        logger.exception(f"brand_register_clip INSERT failed for {client_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Clip registration failed: {type(e).__name__}: {e}")

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
                   COALESCE(wca.is_pinned, FALSE) as is_pinned,
                   CASE WHEN vc.uploaded_by_brand = :cid THEN TRUE ELSE FALSE END as is_own_upload
            FROM video_clips vc
            LEFT JOIN widget_clip_assignments wca
                ON vc.id::text = wca.clip_id AND wca.client_id = :cid
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
                ON vc.id::text = wca.clip_id AND wca.client_id = :cid
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


@router.put("/brand/widget/clips/{clip_id}/pin")
async def brand_toggle_pin(
    clip_id: str,
    client_id: str = Depends(_get_brand_client_id),
    db: AsyncSession = Depends(get_db),
):
    """Toggle pin status for a clip in the brand's widget.
    Pinned clips are displayed first in the widget."""
    # Get current pin status
    result = await db.execute(
        text("""
            SELECT COALESCE(is_pinned, FALSE) as is_pinned
            FROM widget_clip_assignments
            WHERE client_id = :cid AND clip_id = :clip_id AND is_active = TRUE
        """),
        {"cid": client_id, "clip_id": clip_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Clip not assigned to your widget")

    new_pinned = not row["is_pinned"]
    await db.execute(
        text("""
            UPDATE widget_clip_assignments
            SET is_pinned = :pinned
            WHERE client_id = :cid AND clip_id = :clip_id
        """),
        {"cid": client_id, "clip_id": clip_id, "pinned": new_pinned},
    )
    await db.commit()
    return {"status": "updated", "clip_id": clip_id, "is_pinned": new_pinned}


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
                   COALESCE(wca.is_pinned, FALSE) as is_pinned,
                   vc.clip_url, vc.exported_url, vc.thumbnail_url, vc.transcript_text,
                   vc.duration_sec, vc.liver_name
            FROM widget_clip_assignments wca
            JOIN video_clips vc ON vc.id::text = wca.clip_id
            WHERE wca.client_id = :cid AND wca.is_active = TRUE
            ORDER BY COALESCE(wca.is_pinned, FALSE) DESC, wca.sort_order ASC
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
    1. video_product_exposures.brand_name matching brand keywords (fast, indexed)
    2. transcript_text search only when user provides additional query (q param)
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

        if not brand_keywords and not q:
            return {"clips": [], "total": 0, "message": "ブランドキーワードが設定されていません。設定タブでキーワードを追加してください。"}

        # Get clips already assigned to this brand's widget (to mark them)
        assigned_result = await db.execute(
            text("SELECT clip_id FROM widget_clip_assignments WHERE client_id = :cid AND is_active = TRUE"),
            {"cid": client_id},
        )
        assigned_ids = {str(r["clip_id"]) for r in assigned_result.mappings().all()}

        # Two-step approach for speed:
        # Step 1: Get matching video_ids from VPE (small indexed table, fast)
        # Step 2: Fetch clips by those video_ids (indexed lookup)
        all_keywords = list(brand_keywords)
        if q:
            all_keywords.append(q.strip())

        # Step 1: Query VPE for matching video_ids
        vpe_conditions = []
        vpe_params = {}
        for i, kw in enumerate(all_keywords):
            vpe_conditions.append(f"vpe.brand_name ILIKE :bkw{i} OR vpe.product_name ILIKE :bkw{i}")
            vpe_params[f"bkw{i}"] = f"%{kw}%"
        where_vpe = " OR ".join(vpe_conditions)

        vpe_result = await db.execute(
            text(f"""
                SELECT DISTINCT vpe.video_id FROM video_product_exposures vpe
                WHERE {where_vpe}
            """),
            vpe_params,
        )
        matching_video_ids = [str(r["video_id"]) for r in vpe_result.mappings().all()]

        if not matching_video_ids:
            return {
                "clips": [],
                "total": 0,
                "keywords": all_keywords,
                "message": "マッチするクリップが見つかりませんでした。",
            }

        # Step 2: Fetch clips for those video_ids (fast indexed lookup)
        # Use ANY() with array parameter for efficient IN clause
        clip_params = {"vids": matching_video_ids, "lim": limit, "off": offset}

        result = await db.execute(
            text("""
                SELECT vc.id as clip_id, vc.clip_url, vc.exported_url, vc.thumbnail_url,
                       vc.product_name, vc.product_price, vc.transcript_text,
                       vc.duration_sec, vc.liver_name, vc.created_at, vc.video_id
                FROM video_clips vc
                WHERE vc.clip_url IS NOT NULL
                  AND vc.status != 'deleted'
                  AND vc.video_id = ANY(:vids)
                ORDER BY vc.created_at DESC
                LIMIT :lim OFFSET :off
            """),
            clip_params,
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
                for url_key in ["clip_url", "original_clip_url", "thumbnail_url"]:
                    if clip.get(url_key) and "blob.core.windows.net" in (clip[url_key] or ""):
                        try:
                            clip[url_key] = generate_read_sas_from_url(clip[url_key])
                        except Exception:
                            pass
            clips.append(clip)

        # Fast count
        count_result = await db.execute(
            text("""
                SELECT COUNT(*) FROM video_clips vc
                WHERE vc.clip_url IS NOT NULL
                  AND vc.status != 'deleted'
                  AND vc.video_id = ANY(:vids)
            """),
            {"vids": matching_video_ids},
        )
        total = count_result.scalar() or 0

        return {
            "clips": clips,
            "total": total,
            "keywords": all_keywords,
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



# ═══════════════════════════════════════════════════════════════════
# Enhanced Analytics & AI Learning Endpoints
# ═══════════════════════════════════════════════════════════════════


@router.get("/brand/analytics/funnel")
async def brand_analytics_funnel(
    client_id: str = Depends(_get_brand_client_id),
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """
    Funnel analysis: video_play → cta_click/product_click → add_to_cart → purchase_click → conversion
    Returns stage counts and drop-off rates per session.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # Count unique sessions that reached each funnel stage
    funnel_sql = text("""
        WITH session_events AS (
            SELECT session_id, event_type
            FROM widget_tracking_events
            WHERE client_id = :cid AND created_at >= :since
                  AND event_type IN ('video_play', 'video_progress', 'cta_click', 'product_click',
                                     'add_to_cart', 'purchase_click', 'conversion')
        ),
        session_stages AS (
            SELECT session_id,
                MAX(CASE WHEN event_type = 'video_play' THEN 1 ELSE 0 END) as played,
                MAX(CASE WHEN event_type = 'video_progress' THEN 1 ELSE 0 END) as watched_deep,
                MAX(CASE WHEN event_type IN ('cta_click', 'product_click') THEN 1 ELSE 0 END) as clicked,
                MAX(CASE WHEN event_type = 'add_to_cart' THEN 1 ELSE 0 END) as carted,
                MAX(CASE WHEN event_type = 'purchase_click' THEN 1 ELSE 0 END) as purchased,
                MAX(CASE WHEN event_type = 'conversion' THEN 1 ELSE 0 END) as converted
            FROM session_events
            GROUP BY session_id
        )
        SELECT
            COUNT(*) as total_sessions,
            SUM(played) as play_sessions,
            SUM(watched_deep) as deep_watch_sessions,
            SUM(clicked) as click_sessions,
            SUM(carted) as cart_sessions,
            SUM(purchased) as purchase_sessions,
            SUM(converted) as conversion_sessions
        FROM session_stages
    """)
    result = await db.execute(funnel_sql, {"cid": client_id, "since": since})
    row = result.mappings().first()

    total = row["play_sessions"] or 1
    stages = [
        {"stage": "動画再生", "stage_key": "play", "count": row["play_sessions"] or 0, "rate": 100.0},
        {"stage": "深い視聴 (50%+)", "stage_key": "deep_watch", "count": row["deep_watch_sessions"] or 0,
         "rate": round(((row["deep_watch_sessions"] or 0) / total) * 100, 1)},
        {"stage": "商品クリック", "stage_key": "click", "count": row["click_sessions"] or 0,
         "rate": round(((row["click_sessions"] or 0) / total) * 100, 1)},
        {"stage": "カート追加", "stage_key": "cart", "count": row["cart_sessions"] or 0,
         "rate": round(((row["cart_sessions"] or 0) / total) * 100, 1)},
        {"stage": "購入クリック", "stage_key": "purchase", "count": row["purchase_sessions"] or 0,
         "rate": round(((row["purchase_sessions"] or 0) / total) * 100, 1)},
        {"stage": "コンバージョン", "stage_key": "conversion", "count": row["conversion_sessions"] or 0,
         "rate": round(((row["conversion_sessions"] or 0) / total) * 100, 1)},
    ]

    return {"period_days": days, "funnel": stages}


@router.get("/brand/analytics/clip-performance")
async def brand_analytics_clip_performance(
    client_id: str = Depends(_get_brand_client_id),
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """
    Per-clip performance: plays, completion rate, CTA clicks, conversions, avg watch duration.
    This is the core data for AI learning.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    perf_sql = text("""
        WITH clip_plays AS (
            SELECT clip_id, COUNT(*) as play_count
            FROM widget_tracking_events
            WHERE client_id = :cid AND event_type = 'video_play'
                  AND clip_id IS NOT NULL AND created_at >= :since
            GROUP BY clip_id
        ),
        clip_progress AS (
            SELECT
                e.clip_id,
                COUNT(*) FILTER (WHERE e.extra_data IS NOT NULL AND (e.extra_data->>'progress_pct')::int >= 50) as watched_50,
                COUNT(*) FILTER (WHERE e.extra_data IS NOT NULL AND (e.extra_data->>'progress_pct')::int >= 75) as watched_75,
                COUNT(*) FILTER (WHERE e.extra_data IS NOT NULL AND (e.extra_data->>'progress_pct')::int >= 100) as watched_100,
                AVG(
                    CASE WHEN e.extra_data IS NOT NULL
                        AND (e.extra_data->>'progress_pct')::int >= 100
                        AND e.extra_data->>'watch_duration_sec' IS NOT NULL
                    THEN (e.extra_data->>'watch_duration_sec')::float
                    ELSE NULL END
                ) as avg_watch_sec
            FROM widget_tracking_events e
            WHERE e.client_id = :cid AND e.event_type = 'video_progress'
                  AND e.clip_id IS NOT NULL AND e.created_at >= :since
            GROUP BY e.clip_id
        ),
        clip_replays AS (
            SELECT clip_id, COUNT(*) as replay_count,
                   MAX(CASE WHEN extra_data IS NOT NULL AND extra_data->>'loop_count' IS NOT NULL
                       THEN (extra_data->>'loop_count')::int ELSE NULL END) as max_loops
            FROM widget_tracking_events
            WHERE client_id = :cid AND event_type = 'video_replay'
                  AND clip_id IS NOT NULL AND created_at >= :since
            GROUP BY clip_id
        ),
        clip_clicks AS (
            SELECT clip_id,
                COUNT(*) FILTER (WHERE event_type IN ('cta_click', 'product_click')) as click_count,
                COUNT(*) FILTER (WHERE event_type = 'add_to_cart') as cart_count,
                COUNT(*) FILTER (WHERE event_type = 'purchase_click') as purchase_count,
                COUNT(*) FILTER (WHERE event_type = 'like') as like_count,
                COUNT(*) FILTER (WHERE event_type = 'share') as share_count
            FROM widget_tracking_events
            WHERE client_id = :cid AND clip_id IS NOT NULL AND created_at >= :since
                  AND event_type IN ('cta_click', 'product_click', 'add_to_cart', 'purchase_click', 'like', 'share')
            GROUP BY clip_id
        ),
        clip_info AS (
            SELECT vc.id::text as clip_id, vc.product_name, vc.thumbnail_url,
                   vc.liver_name, vc.duration_sec
            FROM video_clips vc
            INNER JOIN widget_clip_assignments wca ON wca.clip_id = vc.id::text
            WHERE wca.client_id = :cid AND wca.is_active = TRUE
        )
        SELECT
            ci.clip_id,
            ci.product_name,
            ci.thumbnail_url,
            ci.liver_name,
            ci.duration_sec,
            COALESCE(cp.play_count, 0) as plays,
            COALESCE(cpr.watched_50, 0) as watched_50,
            COALESCE(cpr.watched_75, 0) as watched_75,
            COALESCE(cpr.watched_100, 0) as completions,
            COALESCE(cpr.avg_watch_sec, 0) as avg_watch_sec,
            COALESCE(cr.replay_count, 0) as replays,
            COALESCE(cr.max_loops, 0) as max_loops,
            COALESCE(cc.click_count, 0) as clicks,
            COALESCE(cc.cart_count, 0) as carts,
            COALESCE(cc.purchase_count, 0) as purchases,
            COALESCE(cc.like_count, 0) as likes,
            COALESCE(cc.share_count, 0) as shares
        FROM clip_info ci
        LEFT JOIN clip_plays cp ON cp.clip_id = ci.clip_id
        LEFT JOIN clip_progress cpr ON cpr.clip_id = ci.clip_id
        LEFT JOIN clip_replays cr ON cr.clip_id = ci.clip_id
        LEFT JOIN clip_clicks cc ON cc.clip_id = ci.clip_id
        ORDER BY COALESCE(cp.play_count, 0) DESC
    """)
    result = await db.execute(perf_sql, {"cid": client_id, "since": since})
    rows = result.mappings().all()

    clips = []
    for r in rows:
        plays = r["plays"] or 1
        completion_rate = round((r["completions"] / plays) * 100, 1) if plays > 0 else 0
        ctr = round((r["clicks"] / plays) * 100, 1) if plays > 0 else 0
        cvr = round((r["purchases"] / plays) * 100, 2) if plays > 0 else 0

        # Engagement score (0-100): weighted combination for AI learning
        engagement = min(100, round(
            (completion_rate * 0.35) +
            (min(ctr, 50) * 0.25 * 2) +
            (min(r["replays"], plays) / plays * 100 * 0.15 if plays > 0 else 0) +
            (min(r["likes"], plays) / plays * 100 * 0.10 if plays > 0 else 0) +
            (min(r["shares"], plays) / plays * 100 * 0.15 if plays > 0 else 0)
        , 1))

        # Conversion score (0-100)
        conversion_score = min(100, round(
            (ctr * 0.30) +
            (min(r["carts"], plays) / plays * 100 * 0.30 if plays > 0 else 0) +
            (cvr * 10 * 0.40)
        , 1))

        clips.append({
            "clip_id": r["clip_id"],
            "product_name": r["product_name"],
            "thumbnail_url": r["thumbnail_url"],
            "liver_name": r["liver_name"],
            "duration_sec": r["duration_sec"],
            "plays": r["plays"],
            "completions": r["completions"],
            "completion_rate": completion_rate,
            "avg_watch_sec": round(r["avg_watch_sec"], 1) if r["avg_watch_sec"] else 0,
            "replays": r["replays"],
            "clicks": r["clicks"],
            "ctr": ctr,
            "carts": r["carts"],
            "purchases": r["purchases"],
            "cvr": cvr,
            "likes": r["likes"],
            "shares": r["shares"],
            "engagement_score": engagement,
            "conversion_score": conversion_score,
        })

    return {"period_days": days, "clips": clips}


@router.get("/brand/analytics/page-matrix")
async def brand_analytics_page_matrix(
    client_id: str = Depends(_get_brand_client_id),
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """
    Page × Clip performance matrix: which clips perform best on which pages.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    matrix_sql = text("""
        WITH page_clip_events AS (
            SELECT
                COALESCE(
                    REGEXP_REPLACE(page_url, '\\?.*$', ''),
                    page_url
                ) as clean_url,
                clip_id,
                event_type
            FROM widget_tracking_events
            WHERE client_id = :cid AND created_at >= :since
                  AND clip_id IS NOT NULL
                  AND event_type IN ('video_play', 'cta_click', 'product_click', 'add_to_cart', 'purchase_click', 'conversion')
        )
        SELECT
            clean_url as page_url,
            clip_id,
            COUNT(*) FILTER (WHERE event_type = 'video_play') as plays,
            COUNT(*) FILTER (WHERE event_type IN ('cta_click', 'product_click')) as clicks,
            COUNT(*) FILTER (WHERE event_type = 'add_to_cart') as carts,
            COUNT(*) FILTER (WHERE event_type IN ('purchase_click', 'conversion')) as conversions
        FROM page_clip_events
        GROUP BY clean_url, clip_id
        HAVING COUNT(*) FILTER (WHERE event_type = 'video_play') > 0
        ORDER BY plays DESC
        LIMIT 100
    """)
    result = await db.execute(matrix_sql, {"cid": client_id, "since": since})
    rows = result.mappings().all()

    matrix = []
    for r in rows:
        plays = r["plays"] or 1
        matrix.append({
            "page_url": r["page_url"],
            "clip_id": r["clip_id"],
            "plays": r["plays"],
            "clicks": r["clicks"],
            "carts": r["carts"],
            "conversions": r["conversions"],
            "ctr": round((r["clicks"] / plays) * 100, 1),
            "cvr": round((r["conversions"] / plays) * 100, 2),
        })

    return {"period_days": days, "matrix": matrix}


@router.get("/brand/analytics/time-heatmap")
async def brand_analytics_time_heatmap(
    client_id: str = Depends(_get_brand_client_id),
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """
    Hourly performance heatmap: which hours of day have the most engagement.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    heatmap_sql = text("""
        SELECT
            EXTRACT(HOUR FROM created_at AT TIME ZONE 'Asia/Tokyo') as hour_jst,
            EXTRACT(DOW FROM created_at AT TIME ZONE 'Asia/Tokyo') as dow,
            COUNT(*) FILTER (WHERE event_type = 'video_play') as plays,
            COUNT(*) FILTER (WHERE event_type IN ('cta_click', 'product_click')) as clicks,
            COUNT(*) FILTER (WHERE event_type IN ('purchase_click', 'conversion')) as conversions
        FROM widget_tracking_events
        WHERE client_id = :cid AND created_at >= :since
        GROUP BY hour_jst, dow
        ORDER BY dow, hour_jst
    """)
    result = await db.execute(heatmap_sql, {"cid": client_id, "since": since})
    rows = result.mappings().all()

    dow_names = ["日", "月", "火", "水", "木", "金", "土"]
    heatmap = []
    for r in rows:
        heatmap.append({
            "hour": int(r["hour_jst"]),
            "day_of_week": int(r["dow"]),
            "day_name": dow_names[int(r["dow"])],
            "plays": r["plays"],
            "clicks": r["clicks"],
            "conversions": r["conversions"],
        })

    return {"period_days": days, "heatmap": heatmap}


# ─── Brand Clip Feedback (for AI Learning) ───

class BrandClipFeedbackRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5, description="Star rating 1-5")
    tags: Optional[list] = Field(default=None, description="Feedback tags")
    comment: Optional[str] = Field(default=None, description="Free-text comment")


@router.post("/brand/clips/{clip_id}/feedback")
async def brand_clip_feedback(
    clip_id: str,
    payload: BrandClipFeedbackRequest,
    client_id: str = Depends(_get_brand_client_id),
    db: AsyncSession = Depends(get_db),
):
    """
    Submit brand feedback for a clip. Used for AI learning signal.
    Upserts: one feedback per brand per clip.
    """
    # Ensure table exists
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS brand_clip_feedback (
            id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
            client_id VARCHAR(20) NOT NULL,
            clip_id VARCHAR(36) NOT NULL,
            rating INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 5),
            tags JSONB,
            comment TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(client_id, clip_id)
        )
    """))
    await db.commit()

    # Upsert
    await db.execute(text("""
        INSERT INTO brand_clip_feedback (id, client_id, clip_id, rating, tags, comment)
        VALUES (gen_random_uuid(), :cid, :clip_id, :rating, :tags, :comment)
        ON CONFLICT (client_id, clip_id)
        DO UPDATE SET rating = :rating, tags = :tags, comment = :comment, updated_at = NOW()
    """), {
        "cid": client_id,
        "clip_id": clip_id,
        "rating": payload.rating,
        "tags": json.dumps(payload.tags) if payload.tags else None,
        "comment": payload.comment,
    })
    await db.commit()

    return {"status": "ok", "clip_id": clip_id, "rating": payload.rating}


@router.get("/brand/analytics/overview")
async def brand_analytics_overview(
    client_id: str = Depends(_get_brand_client_id),
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """
    Dashboard overview: KPI summary with period-over-period comparison.
    """
    now = datetime.now(timezone.utc)
    current_start = now - timedelta(days=days)
    prev_start = current_start - timedelta(days=days)

    # Current period
    current_sql = text("""
        SELECT
            COUNT(*) FILTER (WHERE event_type = 'video_play') as plays,
            COUNT(DISTINCT session_id) FILTER (WHERE event_type = 'video_play') as unique_viewers,
            COUNT(*) FILTER (WHERE event_type = 'video_progress'
                AND extra_data IS NOT NULL AND (extra_data->>'progress_pct')::int >= 100) as completions,
            COUNT(*) FILTER (WHERE event_type IN ('cta_click', 'product_click')) as clicks,
            COUNT(*) FILTER (WHERE event_type = 'add_to_cart') as carts,
            COUNT(*) FILTER (WHERE event_type = 'purchase_click') as purchases,
            COUNT(*) FILTER (WHERE event_type = 'conversion') as conversions,
            COUNT(*) FILTER (WHERE event_type = 'like') as likes,
            COUNT(*) FILTER (WHERE event_type = 'share') as shares,
            COUNT(*) FILTER (WHERE event_type = 'video_replay') as replays,
            AVG(
                CASE WHEN event_type = 'video_progress'
                    AND extra_data IS NOT NULL
                    AND (extra_data->>'progress_pct')::int >= 100
                    AND extra_data->>'watch_duration_sec' IS NOT NULL
                THEN (extra_data->>'watch_duration_sec')::float
                ELSE NULL END
            ) as avg_watch_sec
        FROM widget_tracking_events
        WHERE client_id = :cid AND created_at >= :since
    """)
    current_result = await db.execute(current_sql, {"cid": client_id, "since": current_start})
    curr = current_result.mappings().first()

    # Previous period (for comparison)
    prev_result = await db.execute(current_sql, {"cid": client_id, "since": prev_start})
    # Re-run with different date range
    prev_sql = text("""
        SELECT
            COUNT(*) FILTER (WHERE event_type = 'video_play') as plays,
            COUNT(DISTINCT session_id) FILTER (WHERE event_type = 'video_play') as unique_viewers,
            COUNT(*) FILTER (WHERE event_type IN ('cta_click', 'product_click')) as clicks,
            COUNT(*) FILTER (WHERE event_type = 'purchase_click') as purchases,
            COUNT(*) FILTER (WHERE event_type = 'conversion') as conversions
        FROM widget_tracking_events
        WHERE client_id = :cid AND created_at >= :prev_start AND created_at < :curr_start
    """)
    prev_result = await db.execute(prev_sql, {
        "cid": client_id, "prev_start": prev_start, "curr_start": current_start
    })
    prev = prev_result.mappings().first()

    def growth(current_val, prev_val):
        c = current_val or 0
        p = prev_val or 0
        if p == 0:
            return None
        return round(((c - p) / p) * 100, 1)

    plays = curr["plays"] or 0
    completions = curr["completions"] or 0
    clicks = curr["clicks"] or 0

    return {
        "period_days": days,
        "kpi": {
            "plays": plays,
            "plays_growth": growth(plays, prev["plays"]),
            "unique_viewers": curr["unique_viewers"] or 0,
            "completion_rate": round((completions / plays) * 100, 1) if plays > 0 else 0,
            "avg_watch_sec": round(curr["avg_watch_sec"], 1) if curr["avg_watch_sec"] else 0,
            "clicks": clicks,
            "clicks_growth": growth(clicks, prev["clicks"]),
            "ctr": round((clicks / plays) * 100, 1) if plays > 0 else 0,
            "carts": curr["carts"] or 0,
            "purchases": curr["purchases"] or 0,
            "purchases_growth": growth(curr["purchases"], prev["purchases"]),
            "conversions": curr["conversions"] or 0,
            "conversions_growth": growth(curr["conversions"], prev["conversions"]),
            "cvr": round(((curr["conversions"] or 0) / plays) * 100, 2) if plays > 0 else 0,
            "likes": curr["likes"] or 0,
            "shares": curr["shares"] or 0,
            "replays": curr["replays"] or 0,
        },
    }


@router.get("/brand/analytics/daily")
async def brand_analytics_daily(
    client_id: str = Depends(_get_brand_client_id),
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """
    Daily breakdown of key metrics for chart display.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    daily_sql = text("""
        SELECT
            DATE(created_at AT TIME ZONE 'Asia/Tokyo') as day,
            COUNT(*) FILTER (WHERE event_type = 'video_play') as plays,
            COUNT(*) FILTER (WHERE event_type = 'video_progress'
                AND extra_data IS NOT NULL AND (extra_data->>'progress_pct')::int >= 100) as completions,
            COUNT(*) FILTER (WHERE event_type IN ('cta_click', 'product_click')) as clicks,
            COUNT(*) FILTER (WHERE event_type IN ('purchase_click', 'conversion')) as conversions
        FROM widget_tracking_events
        WHERE client_id = :cid AND created_at >= :since
        GROUP BY DATE(created_at AT TIME ZONE 'Asia/Tokyo')
        ORDER BY day ASC
    """)
    result = await db.execute(daily_sql, {"cid": client_id, "since": since})
    rows = result.mappings().all()

    daily = []
    for r in rows:
        daily.append({
            "day": str(r["day"]),
            "plays": r["plays"],
            "completions": r["completions"],
            "clicks": r["clicks"],
            "conversions": r["conversions"],
        })

    return {"period_days": days, "daily": daily}
