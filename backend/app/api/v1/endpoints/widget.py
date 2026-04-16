"""
Widget API — External widget endpoints for GTM-based video player embedding.

Serves the widget configuration, receives DOM context data (Hack 1),
receives tracking events (Hack 3: Shadow Tracking), and provides
clip/video data for the floating player.

Endpoints:
  GET  /widget/config/{client_id}     — Get widget config for a client site
  POST /widget/page-context           — Receive DOM-scraped page data (Hack 1)
  POST /widget/track                  — Receive tracking events (views, clicks, CV)
  GET  /widget/clips/{client_id}      — Get clips to display for a client
  GET  /widget/loader.js              — Serve the widget loader script

Admin endpoints (require X-Admin-Key):
  GET  /widget/admin/clients          — List all widget clients
  POST /widget/admin/clients          — Create a new widget client
  PUT  /widget/admin/clients/{id}     — Update a widget client
  GET  /widget/admin/clients/{id}/tag — Generate GTM tag snippet
  GET  /widget/admin/analytics        — Widget analytics dashboard data
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db

logger = logging.getLogger("widget")
router = APIRouter()

ADMIN_KEY = "aither:hub"


def _add_cors_headers(response: Response, request: Request) -> Response:
    """Add permissive CORS headers for widget endpoints (called from any client domain)."""
    origin = request.headers.get("origin", "*")
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Admin-Key"
    response.headers["Access-Control-Max-Age"] = "86400"
    return response


def _check_admin(x_admin_key: str = None) -> bool:
    return x_admin_key == ADMIN_KEY


# ─── Pydantic Schemas ───


class WidgetClientCreate(BaseModel):
    name: str = Field(..., description="Client/brand name")
    domain: str = Field(..., description="Allowed domain (e.g. example.com)")
    theme_color: str = Field(default="#FF2D55", description="Widget accent color")
    position: str = Field(default="bottom-right", description="Widget position")
    cta_text: str = Field(default="購入する", description="CTA button text")
    cta_url_template: Optional[str] = Field(default=None, description="CTA URL template")
    cart_selector: Optional[str] = Field(default=None, description="CSS selector for add-to-cart button (Hack 2)")
    brand_keywords: Optional[str] = Field(default=None, description="Comma-separated brand keywords for recommended clips")


class WidgetClientUpdate(BaseModel):
    name: Optional[str] = None
    domain: Optional[str] = None
    theme_color: Optional[str] = None
    position: Optional[str] = None
    is_active: Optional[bool] = None
    cta_text: Optional[str] = None
    cta_url_template: Optional[str] = None
    cart_selector: Optional[str] = None
    brand_keywords: Optional[str] = None
    lcj_brand_id: Optional[int] = None
    assigned_clip_ids: Optional[List[str]] = None


class PageContextPayload(BaseModel):
    client_id: str
    page_url: str
    canonical_url: Optional[str] = None
    title: Optional[str] = None
    og_title: Optional[str] = None
    og_image: Optional[str] = None
    h1_text: Optional[str] = None
    product_price: Optional[str] = None
    meta_description: Optional[str] = None
    session_id: Optional[str] = None


class TrackEventPayload(BaseModel):
    client_id: str
    session_id: str
    event_type: str  # page_view, widget_open, video_play, video_complete, cta_click, conversion
    page_url: Optional[str] = None
    clip_id: Optional[str] = None
    video_current_time: Optional[float] = None
    extra_data: Optional[dict] = None


# ─── Public Endpoints (called from widget JS on client sites) ───


@router.get("/widget/config/{client_id}")
async def get_widget_config(
    client_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return widget configuration for a given client. Called by loader.js."""
    result = await db.execute(
        text("SELECT * FROM widget_clients WHERE client_id = :cid AND is_active = TRUE"),
        {"cid": client_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Widget client not found or inactive")

    # Get assigned clips (with product info from widget_clip_assignments)
    clips_result = await db.execute(
        text("""
            SELECT wca.clip_id, wca.sort_order, wca.page_url_pattern,
                   vc.clip_url, vc.exported_url, vc.thumbnail_url,
                   COALESCE(wca.product_name, vc.product_name) as product_name,
                   vc.transcript_text, vc.duration_sec, vc.liver_name,
                   wca.product_price, wca.product_image_url,
                   wca.product_url, wca.product_cart_url,
                   vc.captions
            FROM widget_clip_assignments wca
            LEFT JOIN video_clips vc ON vc.id::text = wca.clip_id
            WHERE wca.client_id = :cid AND wca.is_active = TRUE
            ORDER BY wca.sort_order ASC
        """),
        {"cid": client_id},
    )
    clips = [dict(r) for r in clips_result.mappings().all()]

    # Use exported_url (subtitled version) if available, fallback to clip_url
    for clip in clips:
        if clip.get("exported_url"):
            clip["original_clip_url"] = clip["clip_url"]
            clip["clip_url"] = clip["exported_url"]

    # Filter out clips without a playable clip_url
    clips = [c for c in clips if c.get("clip_url")]

    # Parse captions JSON if stored as string
    for clip in clips:
        if clip.get("captions") and isinstance(clip["captions"], str):
            try:
                clip["captions"] = json.loads(clip["captions"])
            except Exception:
                clip["captions"] = None

    # Generate SAS URLs for clips if needed
    from app.services.storage_service import generate_read_sas_from_url
    for clip in clips:
        if clip.get("clip_url") and "blob.core.windows.net" in (clip["clip_url"] or ""):
            try:
                clip["clip_url"] = generate_read_sas_from_url(clip["clip_url"])
            except Exception:
                pass  # Keep original URL
        # Also generate SAS for original_clip_url (fallback)
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
        if clip.get("product_image_url") and "blob.core.windows.net" in (clip["product_image_url"] or ""):
            try:
                clip["product_image_url"] = generate_read_sas_from_url(clip["product_image_url"])
            except Exception:
                pass

    return {
        "client_id": client_id,
        "name": row["name"],
        "theme_color": row["theme_color"],
        "position": row["position"],
        "cta_text": row["cta_text"],
        "cta_url_template": row.get("cta_url_template"),
        "cart_selector": row.get("cart_selector"),
        "clips": clips,
    }


@router.post("/widget/page-context")
async def receive_page_context(
    payload: PageContextPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Hack 1: Receive DOM-scraped page context data.
    Called silently by loader.js on every page load.
    """
    try:
        await db.execute(
            text("""
                INSERT INTO widget_page_contexts
                    (id, client_id, page_url, canonical_url, title, og_title,
                     og_image, h1_text, product_price, meta_description,
                     session_id, visitor_ip, user_agent, created_at)
                VALUES
                    (:id, :client_id, :page_url, :canonical_url, :title, :og_title,
                     :og_image, :h1_text, :product_price, :meta_description,
                     :session_id, :visitor_ip, :user_agent, NOW())
            """),
            {
                "id": str(uuid.uuid4()),
                "client_id": payload.client_id,
                "page_url": payload.page_url,
                "canonical_url": payload.canonical_url,
                "title": payload.title,
                "og_title": payload.og_title,
                "og_image": payload.og_image,
                "h1_text": payload.h1_text,
                "product_price": payload.product_price,
                "meta_description": payload.meta_description,
                "session_id": payload.session_id,
                "visitor_ip": request.client.host if request.client else None,
                "user_agent": request.headers.get("user-agent", "")[:500],
            },
        )
        await db.commit()
    except Exception as e:
        logger.warning(f"Failed to save page context: {e}")
        # Don't fail the request — this is a best-effort data collection
        await db.rollback()

    return {"status": "ok"}


@router.post("/widget/track")
async def receive_tracking_event(
    payload: TrackEventPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Hack 3: Receive tracking events (Shadow Tracking).
    Events: page_view, widget_open, video_play, video_complete, cta_click, conversion
    """
    try:
        await db.execute(
            text("""
                INSERT INTO widget_tracking_events
                    (id, client_id, session_id, event_type, page_url,
                     clip_id, video_current_time, extra_data,
                     visitor_ip, user_agent, created_at)
                VALUES
                    (:id, :client_id, :session_id, :event_type, :page_url,
                     :clip_id, :video_current_time, :extra_data,
                     :visitor_ip, :user_agent, NOW())
            """),
            {
                "id": str(uuid.uuid4()),
                "client_id": payload.client_id,
                "session_id": payload.session_id,
                "event_type": payload.event_type,
                "page_url": payload.page_url,
                "clip_id": payload.clip_id,
                "video_current_time": payload.video_current_time,
                "extra_data": json.dumps(payload.extra_data) if payload.extra_data else None,
                "visitor_ip": request.client.host if request.client else None,
                "user_agent": request.headers.get("user-agent", "")[:500],
            },
        )
        await db.commit()
    except Exception as e:
        logger.warning(f"Failed to save tracking event: {e}")
        await db.rollback()

    return {"status": "ok"}


# ─── Admin Endpoints ───


@router.get("/widget/admin/clients")
async def list_widget_clients(
    x_admin_key: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """List all widget clients."""
    if not _check_admin(x_admin_key):
        raise HTTPException(status_code=403, detail="Admin access required")

    # 1) All clients
    result = await db.execute(
        text("SELECT * FROM widget_clients ORDER BY created_at DESC")
    )
    rows = result.mappings().all()
    client_ids = [r["client_id"] for r in rows]

    # 2) Batch: clip counts per client
    clip_counts = {}
    if client_ids:
        cc_result = await db.execute(
            text("""
                SELECT client_id, COUNT(*) as cnt
                FROM widget_clip_assignments
                WHERE client_id = ANY(:cids) AND is_active = TRUE
                GROUP BY client_id
            """),
            {"cids": client_ids},
        )
        for r in cc_result.mappings().all():
            clip_counts[r["client_id"]] = r["cnt"]

    # 3) Batch: clip previews (up to 5 per client) using window function
    clips_by_client = {}
    if client_ids:
        # Only query clients that have clips
        active_cids = [cid for cid in client_ids if clip_counts.get(cid, 0) > 0]
        if active_cids:
            cp_result = await db.execute(
                text("""
                    SELECT * FROM (
                        SELECT wca.client_id, wca.clip_id, vc.thumbnail_url,
                               wca.product_name, vc.duration_sec,
                               ROW_NUMBER() OVER (PARTITION BY wca.client_id ORDER BY wca.sort_order ASC, wca.created_at DESC) as rn
                        FROM widget_clip_assignments wca
                        LEFT JOIN video_clips vc ON vc.id = wca.clip_id
                        WHERE wca.client_id = ANY(:cids) AND wca.is_active = TRUE
                    ) sub WHERE rn <= 5
                """),
                {"cids": active_cids},
            )
            for cr in cp_result.mappings().all():
                cid = cr["client_id"]
                if cid not in clips_by_client:
                    clips_by_client[cid] = []
                clips_by_client[cid].append({
                    "clip_id": cr["clip_id"],
                    "thumbnail_url": cr.get("thumbnail_url") or "",
                    "product_name": cr.get("product_name"),
                    "duration_sec": cr.get("duration_sec"),
                })

    # 5) Assemble response
    clients = []
    for row in rows:
        cid = row["client_id"]
        clients.append({
            **dict(row),
            "clip_count": clip_counts.get(cid, 0),
            "clips_preview": clips_by_client.get(cid, []),
        })

    return {"clients": clients}


@router.post("/widget/admin/clients")
async def create_widget_client(
    payload: WidgetClientCreate,
    x_admin_key: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """Create a new widget client."""
    if not _check_admin(x_admin_key):
        raise HTTPException(status_code=403, detail="Admin access required")

    import secrets as _secrets
    client_id = str(uuid.uuid4())[:8]  # Short ID for easy embedding
    # Auto-generate brand portal password
    brand_password = _secrets.token_urlsafe(12)  # e.g. "xK3m9Qw2pL1n"
    from app.api.v1.endpoints.brand_portal import _hash_password
    password_hash = _hash_password(brand_password)

    await db.execute(
        text("""
            INSERT INTO widget_clients
                (client_id, name, domain, theme_color, position, cta_text,
                 cta_url_template, cart_selector, brand_keywords, is_active, created_at, updated_at, password_hash)
            VALUES
                (:client_id, :name, :domain, :theme_color, :position, :cta_text,
                 :cta_url_template, :cart_selector, :brand_keywords, TRUE, NOW(), NOW(), :password_hash)
        """),
        {
            "client_id": client_id,
            "name": payload.name,
            "domain": payload.domain,
            "theme_color": payload.theme_color,
            "position": payload.position,
            "cta_text": payload.cta_text,
            "cta_url_template": payload.cta_url_template,
            "cart_selector": payload.cart_selector,
            "brand_keywords": payload.brand_keywords,
            "password_hash": password_hash,
        },
    )
    await db.commit()

    return {
        "client_id": client_id,
        "name": payload.name,
        "domain": payload.domain,
        "brand_password": brand_password,  # Show once on creation
        "brand_portal_url": f"https://www.aitherhub.com/brand?id={client_id}",
        "gtm_tag": f'<script src="https://www.aitherhub.com/widget/loader.js" data-client-id="{client_id}" async></script>',
    }


@router.put("/widget/admin/clients/{client_id}")
async def update_widget_client(
    client_id: str,
    payload: WidgetClientUpdate,
    x_admin_key: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """Update a widget client."""
    if not _check_admin(x_admin_key):
        raise HTTPException(status_code=403, detail="Admin access required")

    # Build dynamic UPDATE
    updates = []
    params = {"cid": client_id}
    for field_name, value in payload.model_dump(exclude_none=True).items():
        if field_name == "assigned_clip_ids":
            continue  # Handle separately
        updates.append(f"{field_name} = :{field_name}")
        params[field_name] = value
    updates.append("updated_at = NOW()")

    if updates:
        sql = f"UPDATE widget_clients SET {', '.join(updates)} WHERE client_id = :cid"
        await db.execute(text(sql), params)

    # Handle clip assignments (preserves existing product info on re-assignment)
    if payload.assigned_clip_ids is not None:
        # Deactivate all existing
        await db.execute(
            text("UPDATE widget_clip_assignments SET is_active = FALSE WHERE client_id = :cid"),
            {"cid": client_id},
        )
        # Insert new assignments (ON CONFLICT preserves product info columns)
        for i, clip_id in enumerate(payload.assigned_clip_ids):
            await db.execute(
                text("""
                    INSERT INTO widget_clip_assignments (id, client_id, clip_id, sort_order, is_active, created_at)
                    VALUES (:id, :cid, :clip_id, :sort_order, TRUE, NOW())
                    ON CONFLICT (client_id, clip_id) DO UPDATE
                    SET sort_order = :sort_order, is_active = TRUE
                """),
                {"id": str(uuid.uuid4()), "cid": client_id, "clip_id": clip_id, "sort_order": i},
            )

    await db.commit()
    return {"status": "updated", "client_id": client_id}


@router.get("/widget/admin/clients/{client_id}/tag")
async def get_widget_tag(
    client_id: str,
    x_admin_key: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """Generate the GTM tag snippet for a client."""
    if not _check_admin(x_admin_key):
        raise HTTPException(status_code=403, detail="Admin access required")

    result = await db.execute(
        text("SELECT name, domain FROM widget_clients WHERE client_id = :cid"),
        {"cid": client_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Client not found")

    tag = f'<script src="https://www.aitherhub.com/widget/loader.js" data-client-id="{client_id}" async></script>'

    return {
        "client_id": client_id,
        "client_name": row["name"],
        "domain": row["domain"],
        "gtm_tag": tag,
        "gtm_custom_html": tag,
        "direct_embed": f'<!-- AitherHub Widget for {row["name"]} -->\n{tag}',
        "instructions": (
            "GTMで「タグの新規作成」→「カスタムHTML」を選択し、"
            "上記のタグをコピー＆ペーストして「公開」を押してください。"
            "Facebookの広告タグを追加するのと全く同じ手順です。"
        ),
    }


@router.get("/widget/admin/analytics")
async def get_widget_analytics(
    client_id: Optional[str] = Query(None),
    days: int = Query(default=7, ge=1, le=90),
    x_admin_key: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """Widget analytics dashboard data."""
    if not _check_admin(x_admin_key):
        raise HTTPException(status_code=403, detail="Admin access required")

    # Validate days to prevent SQL injection (already validated by Query ge/le)
    safe_days = max(1, min(90, days))
    client_filter = "AND client_id = :cid" if client_id else ""
    params = {"cid": client_id} if client_id else {}

    # Summary stats
    try:
        result = await db.execute(
            text(f"""
                SELECT event_type, COUNT(*) as count
                FROM widget_tracking_events
                WHERE created_at > NOW() - INTERVAL '{safe_days} days'
                {client_filter}
                GROUP BY event_type
                ORDER BY count DESC
            """),
            params,
        )
        summary = [dict(r) for r in result.mappings().all()]
    except Exception as e:
        logger.warning(f"Analytics summary query failed: {e}")
        summary = []

    # Per-client breakdown
    try:
        per_client_result = await db.execute(
            text(f"""
                SELECT client_id, event_type, COUNT(*) as count
                FROM widget_tracking_events
                WHERE created_at > NOW() - INTERVAL '{safe_days} days'
                {client_filter}
                GROUP BY client_id, event_type
                ORDER BY client_id, count DESC
            """),
            params,
        )
        per_client = [dict(r) for r in per_client_result.mappings().all()]
    except Exception as e:
        logger.warning(f"Analytics per-client query failed: {e}")
        per_client = []

    # Page context stats (Hack 1 data)
    try:
        pages_result = await db.execute(
            text(f"""
                SELECT
                    canonical_url, og_title, og_image, h1_text,
                    COUNT(*) as view_count,
                    MAX(created_at) as last_seen
                FROM widget_page_contexts
                {"WHERE client_id = :cid" if client_id else "WHERE 1=1"}
                GROUP BY canonical_url, og_title, og_image, h1_text
                ORDER BY view_count DESC
                LIMIT 50
            """),
            {"cid": client_id} if client_id else {},
        )
        pages = [dict(r) for r in pages_result.mappings().all()]
    except Exception as e:
        logger.warning(f"Analytics pages query failed: {e}")
        pages = []

    return {
        "period_days": safe_days,
        "summary": summary,
        "per_client": per_client,
        "top_pages": pages,
    }


@router.get("/widget/admin/clips/search")
async def search_clips_for_widget(
    q: str = Query(default="", description="Search query (product_name, liver_name, transcript)"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    x_admin_key: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """Search video_clips for widget assignment. Returns clips with SAS URLs."""
    if not _check_admin(x_admin_key):
        raise HTTPException(status_code=403, detail="Admin access required")

    from app.services.storage_service import generate_read_sas_from_url

    if q.strip():
        search_term = f"%{q.strip()}%"
        result = await db.execute(
            text("""
                SELECT id, clip_url, thumbnail_url, product_name, liver_name,
                       transcript_text, duration_sec, created_at,
                       video_id
                FROM video_clips
                WHERE (product_name ILIKE :q OR liver_name ILIKE :q
                       OR transcript_text ILIKE :q OR id::text ILIKE :q)
                ORDER BY created_at DESC
                LIMIT :lim OFFSET :off
            """),
            {"q": search_term, "lim": limit, "off": offset},
        )
    else:
        result = await db.execute(
            text("""
                SELECT id, clip_url, thumbnail_url, product_name, liver_name,
                       transcript_text, duration_sec, created_at,
                       video_id
                FROM video_clips
                WHERE clip_url IS NOT NULL AND clip_url != ''
                ORDER BY created_at DESC
                LIMIT :lim OFFSET :off
            """),
            {"lim": limit, "off": offset},
        )

    clips = []
    for row in result.mappings().all():
        clip = dict(row)
        # Generate SAS URLs
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
    if q.strip():
        count_result = await db.execute(
            text("""
                SELECT COUNT(*) FROM video_clips
                WHERE (product_name ILIKE :q OR liver_name ILIKE :q
                       OR transcript_text ILIKE :q OR id::text ILIKE :q)
            """),
            {"q": search_term},
        )
    else:
        count_result = await db.execute(
            text("SELECT COUNT(*) FROM video_clips WHERE clip_url IS NOT NULL AND clip_url != ''")
        )
    total = count_result.scalar() or 0

    return {"clips": clips, "total": total, "limit": limit, "offset": offset}


@router.post("/widget/admin/clients/{client_id}/reset-password")
async def reset_brand_password(
    client_id: str,
    payload: Optional[dict] = None,
    x_admin_key: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """Reset brand portal password for a client. Optionally accepts {"password": "..."} in body."""
    if not _check_admin(x_admin_key):
        raise HTTPException(status_code=403, detail="Admin access required")

    import secrets as _secrets
    new_password = (payload or {}).get("password") or _secrets.token_urlsafe(12)
    from app.api.v1.endpoints.brand_portal import _hash_password
    password_hash = _hash_password(new_password)

    await db.execute(
        text("UPDATE widget_clients SET password_hash = :ph WHERE client_id = :cid"),
        {"ph": password_hash, "cid": client_id},
    )
    await db.commit()

    return {
        "client_id": client_id,
        "new_password": new_password,
        "brand_portal_url": f"https://www.aitherhub.com/brand?id={client_id}",
    }


@router.post("/widget/admin/clients/{client_id}/clips")
async def assign_clip_to_client(
    client_id: str,
    payload: dict,
    x_admin_key: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """Assign a clip to a widget client."""
    if not _check_admin(x_admin_key):
        raise HTTPException(status_code=403, detail="Admin access required")

    clip_id = payload.get("clip_id")
    if not clip_id:
        raise HTTPException(status_code=400, detail="clip_id is required")

    page_url_pattern = payload.get("page_url_pattern")
    product_name = payload.get("product_name")
    product_price = payload.get("product_price")
    product_image_url = payload.get("product_image_url")
    product_url = payload.get("product_url")
    product_cart_url = payload.get("product_cart_url")

    # Get current max sort_order
    max_order_result = await db.execute(
        text("SELECT COALESCE(MAX(sort_order), -1) + 1 as next_order FROM widget_clip_assignments WHERE client_id = :cid"),
        {"cid": client_id},
    )
    next_order = max_order_result.scalar() or 0

    await db.execute(
        text("""
            INSERT INTO widget_clip_assignments
                (id, client_id, clip_id, page_url_pattern, sort_order, is_active, created_at,
                 product_name, product_price, product_image_url, product_url, product_cart_url)
            VALUES
                (:id, :cid, :clip_id, :page_url_pattern, :sort_order, TRUE, NOW(),
                 :product_name, :product_price, :product_image_url, :product_url, :product_cart_url)
            ON CONFLICT (client_id, clip_id) DO UPDATE
            SET is_active = TRUE, page_url_pattern = :page_url_pattern, sort_order = :sort_order,
                product_name = :product_name, product_price = :product_price,
                product_image_url = :product_image_url, product_url = :product_url,
                product_cart_url = :product_cart_url
        """),
        {
            "id": str(uuid.uuid4()),
            "cid": client_id,
            "clip_id": clip_id,
            "page_url_pattern": page_url_pattern,
            "sort_order": next_order,
            "product_name": product_name,
            "product_price": product_price,
            "product_image_url": product_image_url,
            "product_url": product_url,
            "product_cart_url": product_cart_url,
        },
    )
    await db.commit()

    return {"status": "assigned", "client_id": client_id, "clip_id": clip_id}


# ── Unassign (delete) a clip from a client ──
@router.delete("/widget/admin/clients/{client_id}/clips/{clip_id}")
async def unassign_clip_from_client(
    client_id: str,
    clip_id: str,
    x_admin_key: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """Remove a clip assignment from a widget client."""
    if not _check_admin(x_admin_key):
        raise HTTPException(status_code=403, detail="Admin access required")

    result = await db.execute(
        text("DELETE FROM widget_clip_assignments WHERE client_id = :cid AND clip_id = :clip_id"),
        {"cid": client_id, "clip_id": clip_id},
    )
    await db.commit()

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Clip assignment not found")

    return {"status": "removed", "client_id": client_id, "clip_id": clip_id}


# ── Reassign a clip to a different client (brand) ──
@router.post("/widget/admin/clips/{clip_id}/reassign")
async def reassign_clip_to_client(
    clip_id: str,
    payload: dict,
    x_admin_key: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """Move a clip assignment from one client to another."""
    if not _check_admin(x_admin_key):
        raise HTTPException(status_code=403, detail="Admin access required")

    from_client_id = payload.get("from_client_id")
    to_client_id = payload.get("to_client_id")

    if not to_client_id:
        raise HTTPException(status_code=400, detail="to_client_id is required")

    try:
        # Get existing assignment data (product info etc.)
        existing = None
        if from_client_id:
            row = await db.execute(
                text("""SELECT product_name, product_price, product_image_url, product_url, product_cart_url, page_url_pattern
                        FROM widget_clip_assignments WHERE client_id = :cid AND clip_id = :clip_id"""),
                {"cid": from_client_id, "clip_id": clip_id},
            )
            existing = row.mappings().first()

            # Remove from old client
            await db.execute(
                text("DELETE FROM widget_clip_assignments WHERE client_id = :cid AND clip_id = :clip_id"),
                {"cid": from_client_id, "clip_id": clip_id},
            )

        # Get next sort_order for target client
        max_order_result = await db.execute(
            text("SELECT COALESCE(MAX(sort_order), -1) + 1 as next_order FROM widget_clip_assignments WHERE client_id = :cid"),
            {"cid": to_client_id},
        )
        next_order = max_order_result.scalar() or 0

        # Insert into new client
        await db.execute(
            text("""
                INSERT INTO widget_clip_assignments
                    (id, client_id, clip_id, page_url_pattern, sort_order, is_active, created_at,
                     product_name, product_price, product_image_url, product_url, product_cart_url)
                VALUES
                    (:id, :cid, :clip_id, :page_url_pattern, :sort_order, TRUE, NOW(),
                     :product_name, :product_price, :product_image_url, :product_url, :product_cart_url)
                ON CONFLICT (client_id, clip_id) DO UPDATE
                SET is_active = TRUE, sort_order = :sort_order,
                    product_name = COALESCE(:product_name, widget_clip_assignments.product_name),
                    product_price = COALESCE(:product_price, widget_clip_assignments.product_price),
                    product_image_url = COALESCE(:product_image_url, widget_clip_assignments.product_image_url),
                    product_url = COALESCE(:product_url, widget_clip_assignments.product_url),
                    product_cart_url = COALESCE(:product_cart_url, widget_clip_assignments.product_cart_url)
            """),
            {
                "id": str(uuid.uuid4()),
                "cid": to_client_id,
                "clip_id": clip_id,
                "page_url_pattern": existing["page_url_pattern"] if existing else None,
                "sort_order": next_order,
                "product_name": existing["product_name"] if existing else None,
                "product_price": existing["product_price"] if existing else None,
                "product_image_url": existing["product_image_url"] if existing else None,
                "product_url": existing["product_url"] if existing else None,
                "product_cart_url": existing["product_cart_url"] if existing else None,
            },
        )
        await db.commit()

        return {"status": "reassigned", "clip_id": clip_id, "from_client_id": from_client_id, "to_client_id": to_client_id}
    except Exception as e:
        await db.rollback()
        logger.error(f"Reassign clip error: {e}")
        raise HTTPException(status_code=500, detail=f"Reassign failed: {str(e)}")
