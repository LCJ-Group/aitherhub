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
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
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
    event_type: str  # page_view, widget_open, video_play, video_progress, video_replay, video_complete, cta_click, conversion
    page_url: Optional[str] = None
    clip_id: Optional[str] = None
    video_current_time: Optional[float] = None
    extra_data: Optional[dict] = None


class CommentPostPayload(BaseModel):
    client_id: str
    session_id: str
    clip_id: str = Field(..., description="Clip UUID")
    nickname: str = Field(default="ゲスト", max_length=30, description="Display name (auto-generated if empty)")
    comment_text: str = Field(..., min_length=1, max_length=500, description="Comment body")


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
                   vc.clip_url, vc.exported_url, vc.thumbnail_url, vc.widget_url,
                   vc.video_id,
                   COALESCE(wca.product_name, vc.product_name) as product_name,
                   vc.transcript_text, vc.duration_sec, vc.liver_name,
                   wca.product_price, wca.product_image_url,
                   wca.product_url, wca.product_cart_url,
                   vc.captions,
                   vc.subtitle_style, vc.subtitle_font_size,
                   vc.caption_offset, vc.trim_data,
                   vc.subtitle_language,
                   vc.subtitle_position_x, vc.subtitle_position_y,
                   COALESCE(wca.is_pinned, FALSE) as is_pinned
            FROM widget_clip_assignments wca
            LEFT JOIN video_clips vc ON vc.id::text = wca.clip_id
            WHERE wca.client_id = :cid AND wca.is_active = TRUE
            ORDER BY COALESCE(wca.is_pinned, FALSE) DESC, wca.sort_order ASC
        """),
        {"cid": client_id},
    )
    clips = [dict(r) for r in clips_result.mappings().all()]

    # Provide both quality URLs: clip_url (720p for fast load) + clip_url_hd (1080p for quality)
    for clip in clips:
        # Build HD URL: exported_url (subtitled 1080p) > original clip_url
        hd_url = clip.get("exported_url") or clip["clip_url"]
        clip["clip_url_hd"] = hd_url
        # Build default URL: widget_url (720p) > exported_url > clip_url
        clip["original_clip_url"] = clip["clip_url"]
        if clip.get("widget_url"):
            clip["clip_url"] = clip["widget_url"]
        elif clip.get("exported_url"):
            clip["clip_url"] = clip["exported_url"]

    # Filter out clips without a playable clip_url
    clips = [c for c in clips if c.get("clip_url")]

    # Note: Previously filtered out unprocessed raw uploads here, but this was too aggressive
    # and removed all clips. Instead, loader.js v3.1+ has checkVideoHealth() that auto-skips
    # clips with videoWidth===0 (unsupported codecs) after 3 seconds.

    # Parse captions JSON if stored as string
    for clip in clips:
        if clip.get("captions") and isinstance(clip["captions"], str):
            try:
                clip["captions"] = json.loads(clip["captions"])
            except Exception:
                clip["captions"] = None
        # Parse trim_data JSON if stored as string
        if clip.get("trim_data") and isinstance(clip["trim_data"], str):
            try:
                clip["trim_data"] = json.loads(clip["trim_data"])
            except Exception:
                clip["trim_data"] = None

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
        # Generate SAS for HD URL
        if clip.get("clip_url_hd") and "blob.core.windows.net" in (clip["clip_url_hd"] or ""):
            try:
                clip["clip_url_hd"] = generate_read_sas_from_url(clip["clip_url_hd"])
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

    # Sanitize product_name: remove file extensions and "None" strings
    import re
    for clip in clips:
        pn = clip.get("product_name")
        if pn and (pn == "None" or re.search(r'\.(mp4|mov|avi|webm|mkv)$', pn, re.IGNORECASE)):
            clip["product_name"] = None

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


# ─── Comment Endpoints (public, called from widget) ───


async def _ensure_comments_table(db: AsyncSession):
    """Create widget_clip_comments table if not exists."""
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS widget_clip_comments (
            id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
            clip_id VARCHAR(36) NOT NULL,
            client_id VARCHAR(20) NOT NULL,
            session_id VARCHAR(100),
            nickname VARCHAR(30) NOT NULL,
            comment_text TEXT NOT NULL,
            visitor_ip VARCHAR(45),
            is_visible BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """))
    await db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_wcc_clip_id
        ON widget_clip_comments (clip_id, created_at DESC)
    """))
    await db.commit()


@router.get("/widget/comments/{clip_id}")
async def get_clip_comments(
    clip_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """
    Get comments for a specific clip.
    Returns newest first, limited to 50 by default.
    """
    await _ensure_comments_table(db)
    try:
        result = await db.execute(
            text("""
                SELECT id, nickname, comment_text, created_at
                FROM widget_clip_comments
                WHERE clip_id = :clip_id AND is_visible = TRUE
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            {"clip_id": clip_id, "limit": limit, "offset": offset},
        )
        rows = result.mappings().all()

        count_result = await db.execute(
            text("SELECT COUNT(*) as cnt FROM widget_clip_comments WHERE clip_id = :clip_id AND is_visible = TRUE"),
            {"clip_id": clip_id},
        )
        total = count_result.scalar() or 0

        comments = []
        for r in rows:
            comments.append({
                "id": str(r["id"]),
                "nickname": r["nickname"],
                "comment_text": r["comment_text"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            })

        resp = {"comments": comments, "total": total}
        response = Response(
            content=json.dumps(resp, ensure_ascii=False),
            media_type="application/json",
        )
        return _add_cors_headers(response, request)
    except Exception as e:
        logger.warning(f"Failed to get comments: {e}")
        resp = {"comments": [], "total": 0}
        response = Response(
            content=json.dumps(resp),
            media_type="application/json",
        )
        return _add_cors_headers(response, request)


@router.post("/widget/comments")
async def post_clip_comment(
    payload: CommentPostPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Post a comment on a clip. Public endpoint (no auth required).
    Rate limited by session_id: max 5 comments per minute.
    """
    await _ensure_comments_table(db)

    # Simple rate limiting: max 5 comments per session per minute
    try:
        rate_result = await db.execute(
            text("""
                SELECT COUNT(*) as cnt FROM widget_clip_comments
                WHERE session_id = :sid AND created_at > NOW() - INTERVAL '1 minute'
            """),
            {"sid": payload.session_id},
        )
        recent_count = rate_result.scalar() or 0
        if recent_count >= 5:
            resp = {"status": "error", "message": "コメントの投稿が多すぎます。少し待ってからお試しください。"}
            response = Response(
                content=json.dumps(resp, ensure_ascii=False),
                media_type="application/json",
                status_code=429,
            )
            return _add_cors_headers(response, request)
    except Exception:
        pass  # Don't block comment on rate-limit check failure

    # Sanitize: strip HTML tags from nickname and comment
    import re as _re
    clean_nickname = _re.sub(r'<[^>]+>', '', payload.nickname).strip()[:30]
    if not clean_nickname:
        clean_nickname = "ゲスト"
    clean_text = _re.sub(r'<[^>]+>', '', payload.comment_text).strip()[:500]

    if not clean_text:
        resp = {"status": "error", "message": "コメントを入力してください。"}
        response = Response(
            content=json.dumps(resp, ensure_ascii=False),
            media_type="application/json",
            status_code=400,
        )
        return _add_cors_headers(response, request)

    comment_id = str(uuid.uuid4())
    try:
        await db.execute(
            text("""
                INSERT INTO widget_clip_comments
                    (id, clip_id, client_id, session_id, nickname, comment_text, visitor_ip, created_at)
                VALUES
                    (:id, :clip_id, :client_id, :session_id, :nickname, :comment_text, :visitor_ip, NOW())
            """),
            {
                "id": comment_id,
                "clip_id": payload.clip_id,
                "client_id": payload.client_id,
                "session_id": payload.session_id,
                "nickname": clean_nickname,
                "comment_text": clean_text,
                "visitor_ip": request.client.host if request.client else None,
            },
        )
        await db.commit()

        resp = {
            "status": "ok",
            "comment": {
                "id": comment_id,
                "nickname": clean_nickname,
                "comment_text": clean_text,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        }
        response = Response(
            content=json.dumps(resp, ensure_ascii=False),
            media_type="application/json",
        )
        return _add_cors_headers(response, request)
    except Exception as e:
        logger.warning(f"Failed to save comment: {e}")
        await db.rollback()
        resp = {"status": "error", "message": "コメントの保存に失敗しました。"}
        response = Response(
            content=json.dumps(resp, ensure_ascii=False),
            media_type="application/json",
            status_code=500,
        )
        return _add_cors_headers(response, request)


# ─── Admin Endpoints ───


@router.get("/widget/admin/clients")
async def list_widget_clients(
    x_admin_key: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """List all widget clients."""
    if not _check_admin(x_admin_key):
        raise HTTPException(status_code=403, detail="Admin access required")

    import logging
    logger = logging.getLogger(__name__)

    try:
        # 1) All clients
        result = await db.execute(
            text("""
                SELECT client_id, name, domain, theme_color, position, cta_text, 
                       is_active, brand_keywords, lcj_brand_id, logo_url,
                       created_at::text as created_at, updated_at::text as updated_at
                FROM widget_clients ORDER BY created_at DESC
            """)
        )
        rows = result.mappings().all()

        # 2) Batch: clip counts
        clip_counts = {}
        cc_result = await db.execute(
            text("""
                SELECT client_id, COUNT(*) as cnt
                FROM widget_clip_assignments
                WHERE is_active = TRUE
                GROUP BY client_id
            """)
        )
        for r in cc_result.mappings().all():
            clip_counts[r["client_id"]] = r["cnt"]

        # 2b) Batch: page_view counts + last_seen for connection status
        page_view_stats = {}
        try:
            pv_result = await db.execute(
                text("""
                    SELECT client_id,
                           COUNT(*) as page_view_count,
                           MAX(created_at)::text as last_seen_at
                    FROM widget_tracking_events
                    WHERE event_type = 'page_view'
                    GROUP BY client_id
                """)
            )
            for r in pv_result.mappings().all():
                page_view_stats[r["client_id"]] = {
                    "page_view_count": r["page_view_count"],
                    "last_seen_at": r["last_seen_at"],
                }
        except Exception as pv_err:
            logger.warning(f"page_view stats query failed: {pv_err}")

        # 3) Batch: clip previews (up to 5 per client with clips)
        clips_by_client = {}
        active_cids = [r["client_id"] for r in rows if clip_counts.get(r["client_id"], 0) > 0]
        if active_cids:
            cp_result = await db.execute(
                text("""
                    SELECT * FROM (
                        SELECT wca.client_id, wca.clip_id, vc.thumbnail_url,
                               wca.product_name, vc.duration_sec,
                               vc.clip_url, vc.exported_url,
                               ROW_NUMBER() OVER (PARTITION BY wca.client_id ORDER BY wca.sort_order ASC, wca.created_at DESC) as rn
                        FROM widget_clip_assignments wca
                        LEFT JOIN video_clips vc ON vc.id::text = wca.clip_id
                        WHERE wca.client_id = ANY(:cids) AND wca.is_active = TRUE
                    ) sub WHERE rn <= 5
                """),
                {"cids": active_cids},
            )
            from app.services.storage_service import generate_read_sas_from_url as _sas
            for cr in cp_result.mappings().all():
                cid = cr["client_id"]
                if cid not in clips_by_client:
                    clips_by_client[cid] = []
                # Prefer exported_url (subtitled) over clip_url
                raw_url = cr.get("exported_url") or cr.get("clip_url") or ""
                thumb_url = cr.get("thumbnail_url") or ""
                try:
                    if raw_url and "blob.core.windows.net" in raw_url:
                        raw_url = _sas(raw_url)
                    if thumb_url and "blob.core.windows.net" in thumb_url:
                        thumb_url = _sas(thumb_url)
                except Exception:
                    pass
                clips_by_client[cid].append({
                    "clip_id": cr["clip_id"],
                    "clip_url": raw_url,
                    "thumbnail_url": thumb_url,
                    "product_name": cr.get("product_name"),
                    "duration_sec": cr.get("duration_sec"),
                })

        # 4) Assemble response
        clients = []
        for row in rows:
            cid = row["client_id"]
            pv = page_view_stats.get(cid, {})
            clients.append({
                **dict(row),
                "clip_count": clip_counts.get(cid, 0),
                "clips_preview": clips_by_client.get(cid, []),
                "page_view_count": pv.get("page_view_count", 0),
                "last_seen_at": pv.get("last_seen_at"),
            })

        return {"clients": clients}

    except Exception as e:
        logger.error(f"list_widget_clients error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


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



# ─── Widget Video Optimization (720p re-encode for fast mobile playback) ───


@router.post("/widget/admin/optimize-clips")
async def optimize_clips_for_widget(
    payload: Optional[dict] = None,
    x_admin_key: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
    background_tasks: BackgroundTasks = None,
):
    """
    Re-encode clips to 720p/1.5Mbps for fast widget playback.
    Accepts optional clip_ids list; if empty, processes all assigned widget clips
    that don't have a widget_url yet.
    """
    if not _check_admin(x_admin_key):
        raise HTTPException(status_code=403, detail="Admin access required")

    payload = payload or {}
    clip_ids = payload.get("clip_ids", [])

    if clip_ids:
        # Specific clips
        result = await db.execute(
            text("""
                SELECT id::text as clip_id, clip_url, exported_url
                FROM video_clips
                WHERE id::text = ANY(:ids) AND (clip_url IS NOT NULL AND clip_url != '')
            """),
            {"ids": clip_ids},
        )
    else:
        # All widget-assigned clips without widget_url
        result = await db.execute(
            text("""
                SELECT DISTINCT vc.id::text as clip_id, vc.clip_url, vc.exported_url
                FROM video_clips vc
                JOIN widget_clip_assignments wca ON vc.id::text = wca.clip_id
                WHERE wca.is_active = TRUE
                  AND (vc.widget_url IS NULL OR vc.widget_url = '')
                  AND (vc.clip_url IS NOT NULL AND vc.clip_url != '')
            """),
        )

    clips_to_process = [dict(r) for r in result.mappings().all()]

    if not clips_to_process:
        return {"status": "no_clips", "message": "No clips need optimization"}

    # Start background processing
    import asyncio
    asyncio.ensure_future(_optimize_clips_background(clips_to_process, db))

    return {
        "status": "started",
        "clips_queued": len(clips_to_process),
        "clip_ids": [c["clip_id"] for c in clips_to_process],
    }


async def _optimize_clips_background(clips: list, db: AsyncSession):
    """Background task to re-encode clips to 720p for widget delivery."""
    import asyncio
    import tempfile
    import os
    import aiohttp
    from app.services.storage_service import generate_read_sas_from_url

    for clip in clips:
        clip_id = clip["clip_id"]
        # Prefer exported_url (has subtitles) over clip_url
        source_url = clip.get("exported_url") or clip.get("clip_url")
        if not source_url:
            continue

        try:
            # Generate read SAS for source
            if "blob.core.windows.net" in source_url:
                source_url = generate_read_sas_from_url(source_url) or source_url

            logger.info(f"[optimize] Starting clip {clip_id}")

            with tempfile.TemporaryDirectory() as tmpdir:
                input_path = os.path.join(tmpdir, "input.mp4")
                output_path = os.path.join(tmpdir, "widget.mp4")

                # Download source video
                async with aiohttp.ClientSession() as session:
                    async with session.get(source_url) as resp:
                        if resp.status != 200:
                            logger.warning(f"[optimize] Failed to download clip {clip_id}: HTTP {resp.status}")
                            continue
                        with open(input_path, "wb") as f:
                            async for chunk in resp.content.iter_chunked(1024 * 1024):
                                f.write(chunk)

                input_size = os.path.getsize(input_path)
                logger.info(f"[optimize] Downloaded clip {clip_id}: {input_size / 1024 / 1024:.1f}MB")

                # Re-encode to 720p with faststart
                proc = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-y",
                    "-i", input_path,
                    "-vf", "scale=-2:720",
                    "-c:v", "libx264",
                    "-preset", "fast",
                    "-crf", "28",
                    "-maxrate", "1.5M",
                    "-bufsize", "3M",
                    "-c:a", "aac",
                    "-b:a", "64k",
                    "-movflags", "+faststart",
                    output_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await proc.communicate()

                if proc.returncode != 0:
                    logger.warning(f"[optimize] ffmpeg failed for clip {clip_id}: {stderr.decode()[:500]}")
                    continue

                output_size = os.path.getsize(output_path)
                ratio = output_size / input_size * 100 if input_size > 0 else 0
                logger.info(f"[optimize] Encoded clip {clip_id}: {output_size / 1024 / 1024:.1f}MB ({ratio:.0f}% of original)")

                # Upload to blob storage
                # Derive email and video_id from original clip_url path
                original_url = clip.get("clip_url") or ""
                # URL pattern: .../videos/{email}/{video_id}/clips/clip_xxx.mp4
                parts = original_url.split("/")
                email_idx = None
                for pi, p in enumerate(parts):
                    if p == "videos" and pi + 2 < len(parts):
                        email_idx = pi + 1
                        break

                if email_idx and email_idx + 1 < len(parts):
                    email = parts[email_idx]
                    video_id = parts[email_idx + 1]
                else:
                    email = "widget"
                    video_id = clip_id

                from app.services.storage_service import generate_upload_sas
                _, upload_url, blob_url, _ = await generate_upload_sas(
                    email=email,
                    video_id=video_id,
                    filename=f"clips/widget_{clip_id}.mp4",
                )

                # Upload the optimized clip
                async with aiohttp.ClientSession() as session:
                    with open(output_path, "rb") as f:
                        data = f.read()
                    async with session.put(
                        upload_url,
                        data=data,
                        headers={
                            "x-ms-blob-type": "BlockBlob",
                            "Content-Type": "video/mp4",
                        },
                    ) as resp:
                        if resp.status in (200, 201):
                            # Update DB with widget_url
                            from sqlalchemy import text as _text
                            async with db.begin():
                                await db.execute(
                                    _text("UPDATE video_clips SET widget_url = :url WHERE id::text = :cid"),
                                    {"url": blob_url, "cid": clip_id},
                                )
                            logger.info(f"[optimize] Saved widget_url for clip {clip_id}: {blob_url}")
                        else:
                            logger.warning(f"[optimize] Upload failed for clip {clip_id}: HTTP {resp.status}")

        except Exception as e:
            logger.error(f"[optimize] Error processing clip {clip_id}: {e}")
            continue

    logger.info(f"[optimize] Completed optimization of {len(clips)} clips")


# ─── OGP Product Preview API ───

# In-memory cache for OGP data (TTL-based)
_ogp_cache: dict = {}  # url -> {"data": {...}, "ts": float}
_OGP_CACHE_TTL = 3600  # 1 hour

class OGPPreviewResponse(BaseModel):
    url: str
    title: Optional[str] = None
    image: Optional[str] = None
    images: List[str] = []  # Multiple product images for gallery
    description: Optional[str] = None
    price: Optional[str] = None
    site_name: Optional[str] = None
    favicon: Optional[str] = None
    success: bool = True
    error: Optional[str] = None


async def _fetch_ogp(url: str) -> dict:
    """Fetch and parse OGP meta tags from a URL.

    Enhanced with:
    - Double-slash fix in OGP image URLs
    - Fallback image extraction from page JS data, img tags, and JSON-LD
    - HEAD-request validation to ensure the returned image URL is accessible
    - Multiple images returned in 'images' array for gallery support
    """
    import re
    import time
    from urllib.parse import urljoin, urlparse

    # Check cache
    cached = _ogp_cache.get(url)
    if cached and (time.time() - cached["ts"]) < _OGP_CACHE_TTL:
        return cached["data"]

    result = {
        "url": url,
        "title": None,
        "image": None,
        "images": [],  # Multiple product images for gallery
        "description": None,
        "price": None,
        "site_name": None,
        "favicon": None,
        "success": False,
        "error": None,
    }

    try:
        import httpx
        from bs4 import BeautifulSoup

        async with httpx.AsyncClient(
            timeout=10.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; AitherHubBot/1.0; +https://www.aitherhub.com)",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ja,en;q=0.9",
            },
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html_text = resp.text

        soup = BeautifulSoup(html_text, "html.parser")
        parsed_url = urlparse(url)
        base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"

        # OGP tags
        og_title = soup.find("meta", property="og:title")
        og_image = soup.find("meta", property="og:image")
        og_desc = soup.find("meta", property="og:description")
        og_site = soup.find("meta", property="og:site_name")

        # Fallback: standard meta tags
        meta_desc = soup.find("meta", attrs={"name": "description"})
        title_tag = soup.find("title")

        result["title"] = (og_title["content"] if og_title and og_title.get("content") else
                          (title_tag.get_text(strip=True) if title_tag else None))
        raw_ogp_image = og_image["content"] if og_image and og_image.get("content") else None
        result["description"] = (og_desc["content"] if og_desc and og_desc.get("content") else
                                (meta_desc["content"] if meta_desc and meta_desc.get("content") else None))
        result["site_name"] = og_site["content"] if og_site and og_site.get("content") else None

        # Make OGP image URL absolute
        if raw_ogp_image and not raw_ogp_image.startswith("http"):
            raw_ogp_image = urljoin(base_url, raw_ogp_image)

        # ── Fix double-slash in OGP image URL ──
        # e.g. "https://kyogokupro.com//html/upload/..." → "https://kyogokupro.com/html/upload/..."
        if raw_ogp_image:
            raw_ogp_image = re.sub(r'(?<!:)//', '/', raw_ogp_image)

        # ── Collect candidate image URLs (ordered by priority) ──
        candidate_images = []

        # Strategy 1: JSON-LD structured data images (most reliable)
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                ld = json.loads(script.string or "")
                items = ld if isinstance(ld, list) else [ld]
                for item in items:
                    if isinstance(item, dict) and item.get("@graph"):
                        items.extend(item["@graph"])
                for item in items:
                    if isinstance(item, dict):
                        ld_img = item.get("image")
                        if isinstance(ld_img, str) and ld_img.startswith("http"):
                            if ld_img not in candidate_images:
                                candidate_images.append(ld_img)
                        elif isinstance(ld_img, list):
                            for li in ld_img:
                                li_url = li if isinstance(li, str) else (li.get("url") if isinstance(li, dict) else None)
                                if li_url and li_url.startswith("http") and li_url not in candidate_images:
                                    candidate_images.append(li_url)
                        elif isinstance(ld_img, dict) and ld_img.get("url"):
                            if ld_img["url"] not in candidate_images:
                                candidate_images.append(ld_img["url"])
            except Exception:
                continue

        # Strategy 2: Product gallery slide images (e.g. EC-CUBE slide-item)
        slide_imgs = soup.select(".slide-item img, .item_visual img, .product-gallery img")
        for img_tag in slide_imgs:
            src = img_tag.get("src") or img_tag.get("data-src")
            if src:
                if not src.startswith("http"):
                    src = urljoin(base_url, src)
                src = re.sub(r'(?<!:)//', '/', src)
                if src not in candidate_images:
                    candidate_images.append(src)

        # Strategy 3: Product-related img tags (broader selectors)
        product_img_selectors = [
            {"class_": re.compile(r"product|main-image", re.I)},
            {"id": re.compile(r"product|main-image", re.I)},
        ]
        for selector in product_img_selectors:
            for img_tag in soup.find_all("img", **selector):
                src = img_tag.get("src") or img_tag.get("data-src")
                if src:
                    if not src.startswith("http"):
                        src = urljoin(base_url, src)
                    src = re.sub(r'(?<!:)//', '/', src)
                    if src not in candidate_images:
                        candidate_images.append(src)

        # Strategy 4: OGP image (after double-slash fix) as last resort
        if raw_ogp_image and raw_ogp_image not in candidate_images:
            candidate_images.append(raw_ogp_image)

        # ── Validate image URLs with HEAD requests ──
        validated_images = []
        primary_image = None

        async with httpx.AsyncClient(
            timeout=5.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AitherHubBot/1.0)"},
        ) as img_client:
            for img_url in candidate_images[:10]:  # Check up to 10 candidates
                try:
                    head_resp = await img_client.head(img_url)
                    content_type = head_resp.headers.get("content-type", "")
                    if head_resp.status_code == 200 and (
                        "image" in content_type or content_type == ""
                    ):
                        validated_images.append(img_url)
                        if primary_image is None:
                            primary_image = img_url
                except Exception:
                    continue

        # Set the primary image
        result["image"] = primary_image
        result["images"] = validated_images[:8]  # Return up to 8 validated images

        # If no validated images but we have the OGP image, use it anyway
        if not result["image"] and raw_ogp_image:
            result["image"] = raw_ogp_image
            logger.warning(f"[OGP] No validated images found, falling back to OGP image: {raw_ogp_image}")

        # Try to extract price (multiple strategies)
        # 1. og:price:amount
        og_price = soup.find("meta", property="og:price:amount")
        if og_price and og_price.get("content"):
            result["price"] = og_price["content"]
        else:
            # 2. product:price:amount (common in e-commerce)
            prod_price = soup.find("meta", property="product:price:amount")
            if prod_price and prod_price.get("content"):
                result["price"] = prod_price["content"]

        # Try to find price currency
        og_currency = soup.find("meta", property="og:price:currency") or soup.find("meta", property="product:price:currency")
        if og_currency and og_currency.get("content") and result.get("price"):
            currency = og_currency["content"]
            if currency == "JPY":
                result["price"] = f"¥{result['price']}"
            elif currency == "USD":
                result["price"] = f"${result['price']}"

        # 3. If no OGP price, try JSON-LD structured data
        if not result.get("price"):
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    ld = json.loads(script.string or "")
                    # Handle @graph arrays
                    items = ld if isinstance(ld, list) else [ld]
                    for item in items:
                        if isinstance(item, dict) and item.get("@graph"):
                            items.extend(item["@graph"])
                    for item in items:
                        if isinstance(item, dict):
                            offers = item.get("offers", {})
                            if isinstance(offers, list):
                                offers = offers[0] if offers else {}
                            if isinstance(offers, dict):
                                p = offers.get("price")
                                if p:
                                    curr = offers.get("priceCurrency", "")
                                    if curr == "JPY":
                                        result["price"] = f"¥{p}"
                                    elif curr == "USD":
                                        result["price"] = f"${p}"
                                    else:
                                        result["price"] = str(p)
                                    break
                except Exception:
                    continue

        # 4. If still no price, try to find price from HTML elements
        if not result.get("price"):
            price_patterns = [
                soup.find(class_=re.compile(r"price|product-price|item-price", re.I)),
                soup.find(id=re.compile(r"price|product-price", re.I)),
            ]
            for price_el in price_patterns:
                if price_el:
                    price_text = price_el.get_text(strip=True)
                    # Extract price-like pattern (e.g. ¥7,260 or $19.99)
                    price_match = re.search(r'[¥$€][\d,]+\.?\d*|[\d,]+\.?\d*\s*円', price_text)
                    if price_match:
                        result["price"] = price_match.group(0)
                        break

        # Favicon
        icon_link = soup.find("link", rel=lambda x: x and "icon" in (x if isinstance(x, str) else " ".join(x)))
        if icon_link and icon_link.get("href"):
            favicon = icon_link["href"]
            if not favicon.startswith("http"):
                favicon = urljoin(base_url, favicon)
            result["favicon"] = favicon
        else:
            result["favicon"] = f"{base_url}/favicon.ico"

        # Clean up description (remove &nbsp; and excessive whitespace)
        if result["description"]:
            result["description"] = result["description"].replace("&nbsp;", " ").replace("\u00a0", " ")
            result["description"] = re.sub(r"\s+", " ", result["description"]).strip()
            # Limit to 300 chars
            if len(result["description"]) > 300:
                result["description"] = result["description"][:297] + "..."

        result["success"] = True
        logger.info(f"[OGP] Successfully fetched OGP for {url}: image={result['image']}, images_count={len(result['images'])}")

    except httpx.TimeoutException:
        result["error"] = "Request timed out"
    except httpx.HTTPStatusError as e:
        result["error"] = f"HTTP {e.response.status_code}"
    except Exception as e:
        result["error"] = str(e)[:200]
        logger.warning(f"OGP fetch error for {url}: {e}")

    # Cache the result
    import time
    _ogp_cache[url] = {"data": result, "ts": time.time()}

    # Limit cache size
    if len(_ogp_cache) > 500:
        oldest_key = min(_ogp_cache, key=lambda k: _ogp_cache[k]["ts"])
        del _ogp_cache[oldest_key]

    return result


@router.get("/widget/product-preview")
async def get_product_preview(
    url: str = Query(..., description="Product page URL to fetch OGP data from"),
    request: Request = None,
):
    """
    Fetch OGP meta tags from a product page URL.
    Returns title, image, description, price, site_name, and favicon.
    Used by the widget's product detail panel to show rich product previews.
    
    This endpoint is public (called from widget JS on client sites) and includes
    CORS headers for cross-origin access.
    """
    if not url or not url.startswith("http"):
        raise HTTPException(status_code=400, detail="Invalid URL")

    data = await _fetch_ogp(url)

    response = Response(
        content=json.dumps(data, ensure_ascii=False),
        media_type="application/json",
    )
    # Add CORS headers
    if request:
        _add_cors_headers(response, request)
    return response



# ═══════════════════════════════════════════════════════════════════
# AI Learning: Clip Performance Scores & Auto-Ranking
# ═══════════════════════════════════════════════════════════════════


async def _ensure_performance_tables(db: AsyncSession):
    """Create clip_performance_scores table if not exists."""
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS clip_performance_scores (
            id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
            clip_id VARCHAR(36) NOT NULL,
            client_id VARCHAR(20),
            period_start TIMESTAMPTZ NOT NULL,
            period_end TIMESTAMPTZ NOT NULL,
            play_count INTEGER DEFAULT 0,
            completion_count INTEGER DEFAULT 0,
            avg_watch_duration_sec REAL,
            cta_click_count INTEGER DEFAULT 0,
            cart_add_count INTEGER DEFAULT 0,
            purchase_count INTEGER DEFAULT 0,
            conversion_count INTEGER DEFAULT 0,
            share_count INTEGER DEFAULT 0,
            like_count INTEGER DEFAULT 0,
            replay_count INTEGER DEFAULT 0,
            engagement_score REAL,
            conversion_score REAL,
            overall_score REAL,
            brand_rating INTEGER,
            brand_comment TEXT,
            score_version VARCHAR(10) DEFAULT 'v1',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """))
    await db.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_cps_clip_client
        ON clip_performance_scores (clip_id, client_id, period_end DESC)
    """))
    # Unique constraint needed for ON CONFLICT upsert
    try:
        await db.execute(text("""
            ALTER TABLE clip_performance_scores
            ADD CONSTRAINT uq_cps_clip_client_period UNIQUE (clip_id, client_id, period_end)
        """))
        await db.commit()
    except Exception:
        await db.rollback()  # Rollback failed ALTER before continuing
    await db.commit()


@router.post("/widget/admin/recalculate-scores")
async def admin_recalculate_scores(
    days: int = Query(default=30, ge=1, le=365),
    x_admin_key: str = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Recalculate clip_performance_scores for all active widget clips.
    Designed to be called daily via cron or manually.
    """
    if not _check_admin(x_admin_key):
        raise HTTPException(status_code=403, detail="Admin key required")

    try:
        await _ensure_performance_tables(db)
    except Exception as e:
        logger.error(f"Failed to ensure performance tables: {e}")
        return {"status": "error", "detail": f"Table setup failed: {str(e)}"}

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    # Get all active widget clips grouped by client
    try:
        clips_result = await db.execute(text("""
            SELECT wca.client_id, wca.clip_id
            FROM widget_clip_assignments wca
            WHERE wca.is_active = TRUE
        """))
        active_clips = clips_result.mappings().all()
    except Exception as e:
        logger.error(f"Failed to get active clips: {e}")
        return {"status": "error", "detail": f"Get clips failed: {str(e)}"}

    if not active_clips:
        return {"status": "ok", "message": "No active clips found", "updated": 0}

    updated = 0
    errors = []
    for clip_row in active_clips:
        cid = clip_row["client_id"]
        clip_id = clip_row["clip_id"]

        try:
            # Aggregate events for this clip
            stats_result = await db.execute(text("""
                SELECT
                    COUNT(*) FILTER (WHERE event_type = 'video_play') as plays,
                    COUNT(*) FILTER (WHERE event_type = 'video_progress'
                        AND extra_data IS NOT NULL
                        AND (extra_data->>'progress_pct')::int >= 100) as completions,
                    AVG(
                        CASE WHEN event_type = 'video_progress'
                            AND extra_data IS NOT NULL
                            AND (extra_data->>'progress_pct')::int >= 100
                            AND extra_data->>'watch_duration_sec' IS NOT NULL
                        THEN (extra_data->>'watch_duration_sec')::float
                        ELSE NULL END
                    ) as avg_watch_sec,
                    COUNT(*) FILTER (WHERE event_type IN ('cta_click', 'product_click')) as clicks,
                    COUNT(*) FILTER (WHERE event_type = 'add_to_cart') as carts,
                    COUNT(*) FILTER (WHERE event_type = 'purchase_click') as purchases,
                    COUNT(*) FILTER (WHERE event_type = 'conversion') as conversions,
                    COUNT(*) FILTER (WHERE event_type = 'share') as shares,
                    COUNT(*) FILTER (WHERE event_type = 'like') as likes,
                    COUNT(*) FILTER (WHERE event_type = 'video_replay') as replays
                FROM widget_tracking_events
                WHERE client_id = :cid AND clip_id = :clip_id AND created_at >= :since
            """), {"cid": cid, "clip_id": clip_id, "since": since})
            stats = stats_result.mappings().first()

            plays = stats["plays"] or 0
            completions = stats["completions"] or 0
            clicks = stats["clicks"] or 0
            purchases = stats["purchases"] or 0
            replays = stats["replays"] or 0
            likes = stats["likes"] or 0
            shares = stats["shares"] or 0

            # Calculate scores
            if plays > 0:
                completion_rate = (completions / plays) * 100
                ctr = (clicks / plays) * 100
                cvr = (purchases / plays) * 100
                replay_rate = (min(replays, plays) / plays) * 100
                like_rate = (min(likes, plays) / plays) * 100
                share_rate = (min(shares, plays) / plays) * 100

                engagement = min(100, round(
                    completion_rate * 0.35 +
                    min(ctr, 50) * 0.25 * 2 +
                    replay_rate * 0.15 +
                    like_rate * 0.10 +
                    share_rate * 0.15
                , 1))

                conversion_score = min(100, round(
                    ctr * 0.30 +
                    (min(stats["carts"] or 0, plays) / plays * 100) * 0.30 +
                    cvr * 10 * 0.40
                , 1))

                overall = round(engagement * 0.4 + conversion_score * 0.6, 1)
            else:
                engagement = 0
                conversion_score = 0
                overall = 0

            # Get brand feedback if exists
            try:
                fb_result = await db.execute(text("""
                    SELECT rating, comment FROM brand_clip_feedback
                    WHERE client_id = :cid AND clip_id = :clip_id
                """), {"cid": cid, "clip_id": clip_id})
                fb = fb_result.mappings().first()
                brand_rating = fb["rating"] if fb else None
                brand_comment = fb["comment"] if fb else None
            except Exception:
                brand_rating = None
                brand_comment = None

            # If brand gave feedback, factor it into overall score
            if brand_rating is not None:
                brand_bonus = (brand_rating - 3) * 5  # -10 to +10
                overall = min(100, max(0, overall + brand_bonus))

            # Upsert score
            await db.execute(text("""
                INSERT INTO clip_performance_scores
                    (id, clip_id, client_id, period_start, period_end,
                     play_count, completion_count, avg_watch_duration_sec,
                     cta_click_count, cart_add_count, purchase_count, conversion_count,
                     share_count, like_count, replay_count,
                     engagement_score, conversion_score, overall_score,
                     brand_rating, brand_comment, score_version)
                VALUES
                    (gen_random_uuid(), :clip_id, :cid, :period_start, :period_end,
                     :plays, :completions, :avg_watch_sec,
                     :clicks, :carts, :purchases, :conversions,
                     :shares, :likes, :replays,
                     :engagement, :conversion_score, :overall,
                     :brand_rating, :brand_comment, 'v1')
                ON CONFLICT (clip_id, client_id, period_end)
                DO UPDATE SET
                    play_count = :plays, completion_count = :completions,
                    avg_watch_duration_sec = :avg_watch_sec,
                    cta_click_count = :clicks, cart_add_count = :carts,
                    purchase_count = :purchases, conversion_count = :conversions,
                    share_count = :shares, like_count = :likes, replay_count = :replays,
                    engagement_score = :engagement, conversion_score = :conversion_score,
                    overall_score = :overall,
                    brand_rating = :brand_rating, brand_comment = :brand_comment,
                    updated_at = NOW()
            """), {
                "clip_id": clip_id, "cid": cid,
                "period_start": since, "period_end": now,
                "plays": plays, "completions": completions,
                "avg_watch_sec": round(stats["avg_watch_sec"], 1) if stats["avg_watch_sec"] else None,
                "clicks": clicks, "carts": stats["carts"] or 0,
                "purchases": purchases, "conversions": stats["conversions"] or 0,
                "shares": shares, "likes": likes, "replays": replays,
                "engagement": engagement, "conversion_score": conversion_score,
                "overall": overall,
                "brand_rating": brand_rating, "brand_comment": brand_comment,
            })
            updated += 1
        except Exception as e:
            errors.append(f"{clip_id}: {str(e)}")
            logger.error(f"Failed to process clip {clip_id}: {e}")
            try:
                await db.rollback()
            except Exception:
                pass
            continue

    try:
        await db.commit()
    except Exception as e:
        logger.error(f"Commit failed: {e}")
        return {"status": "error", "detail": f"Commit failed: {str(e)}", "errors": errors}

    # Auto-ranking: update sort_order based on overall_score
    try:
        await _auto_rank_clips(db)
    except Exception as e:
        logger.warning(f"Auto-rank failed (non-critical): {e}")

    result = {"status": "ok", "updated": updated, "period_days": days}
    if errors:
        result["errors"] = errors
    return result


async def _auto_rank_clips(db: AsyncSession):
    """
    Auto-rank widget clips by overall_score.
    Updates widget_clip_assignments.sort_order for each client.
    Only affects clients with auto_rank enabled (default: all).
    """
    try:
        # Get latest scores per client+clip
        rank_result = await db.execute(text("""
            WITH latest_scores AS (
                SELECT DISTINCT ON (clip_id, client_id)
                    clip_id, client_id, overall_score
                FROM clip_performance_scores
                ORDER BY clip_id, client_id, period_end DESC
            ),
            ranked AS (
                SELECT
                    clip_id, client_id,
                    ROW_NUMBER() OVER (PARTITION BY client_id ORDER BY overall_score DESC NULLS LAST) as new_rank
                FROM latest_scores
            )
            UPDATE widget_clip_assignments wca
            SET sort_order = r.new_rank
            FROM ranked r
            WHERE wca.clip_id = r.clip_id AND wca.client_id = r.client_id AND wca.is_active = TRUE
        """))
        await db.commit()
        logger.info(f"Auto-ranked clips: {rank_result.rowcount} assignments updated")
    except Exception as e:
        logger.warning(f"Auto-rank failed (non-critical): {e}")
        await db.rollback()


# ── Share Landing Page: clip metadata for /v/{clip_id} ──────────────
@router.get("/widget/share/{clip_id}")
async def get_share_clip_meta(clip_id: str, db: AsyncSession = Depends(get_db)):
    """Return clip metadata for the share landing page & OGP tags.
    Public endpoint – no auth required so crawlers can read OGP."""
    try:
        return await _get_share_clip_meta_impl(clip_id, db)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Share endpoint error for clip {clip_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


async def _get_share_clip_meta_impl(clip_id: str, db: AsyncSession):
    result = await db.execute(
        text("""
            SELECT vc.id::text as clip_id,
                   vc.clip_url, vc.exported_url, vc.widget_url,
                   vc.thumbnail_url, vc.duration_sec,
                   vc.product_name as vc_product_name,
                   vc.liver_name,
                   wca.product_name as wca_product_name,
                   wca.product_price, wca.product_url,
                   wca.product_image_url, wca.product_cart_url,
                   wc.name as brand_name, wc.client_id as client_id,
                   wc.logo_url as brand_logo_url,
                   wc.theme_color as theme_color
            FROM video_clips vc
            LEFT JOIN widget_clip_assignments wca
                ON vc.id::text = wca.clip_id AND wca.is_active = TRUE
            LEFT JOIN widget_clients wc
                ON wca.client_id = wc.client_id AND wc.is_active = TRUE
            WHERE vc.id::text = :cid
            LIMIT 1
        """),
        {"cid": clip_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Clip not found")

    row = dict(row)
    product_name = row.get("wca_product_name") or row.get("vc_product_name") or ""
    product_price = row.get("product_price") or ""
    product_image_url = row.get("product_image_url") or ""
    product_description = ""
    brand_name = row.get("brand_name") or ""
    theme_color = row.get("theme_color") or "#FF2D55"
    product_url = row.get("product_url") or ""

    # ── OGP enrichment: fetch product info from product_url when missing ──
    # If product_name is empty or looks like a raw filename, try to enrich from OGP
    _is_raw_filename = product_name and (
        product_name.endswith(".mp4") or product_name.endswith(".mov")
        or product_name.endswith(".webm") or product_name.endswith(".avi")
    )
    if (not product_name or _is_raw_filename) and product_url:
        try:
            ogp_data = await _fetch_ogp(product_url)
            if ogp_data.get("success"):
                if ogp_data.get("title"):
                    product_name = ogp_data["title"]
                if ogp_data.get("price") and not product_price:
                    product_price = ogp_data["price"]
                if ogp_data.get("image") and not product_image_url:
                    product_image_url = ogp_data["image"]
                if ogp_data.get("description"):
                    product_description = ogp_data["description"][:200]
                logger.info(f"Share OGP enrichment for {clip_id}: title={product_name}")
        except Exception as e:
            logger.warning(f"Share OGP enrichment failed for {clip_id}: {e}")

    # Build title
    title = product_name if product_name else brand_name
    if brand_name and product_name:
        title = f"{product_name} | {brand_name}"
    elif brand_name:
        title = brand_name

    # Pick best thumbnail
    thumbnail = row.get("thumbnail_url") or product_image_url or ""

    # Pick best video URL
    video_url = row.get("widget_url") or row.get("exported_url") or row.get("clip_url") or ""

    # Generate SAS if Azure blob
    try:
        from app.services.storage_service import generate_read_sas_from_url
        if thumbnail and "blob.core.windows.net" in thumbnail:
            sas_thumb = generate_read_sas_from_url(thumbnail)
            if sas_thumb:
                thumbnail = sas_thumb
        if video_url and "blob.core.windows.net" in video_url:
            sas_video = generate_read_sas_from_url(video_url)
            if sas_video:
                video_url = sas_video
    except Exception as e:
        logger.warning(f"SAS generation failed in share endpoint: {e}")

    # Build OGP description
    og_description = ""
    if product_description:
        og_description = product_description
    elif product_name and brand_name:
        og_description = f"{brand_name}の「{product_name}」を動画でチェック！"
    elif brand_name:
        og_description = f"{brand_name}の動画をチェック！"
    else:
        og_description = "動画をチェック！"

    return {
        "clip_id": row["clip_id"],
        "client_id": row.get("client_id") or "",
        "title": title,
        "brand_name": brand_name,
        "brand_logo_url": row.get("brand_logo_url") or "",
        "theme_color": theme_color,
        "product_name": product_name,
        "product_price": product_price,
        "product_url": product_url,
        "product_image_url": product_image_url,
        "product_cart_url": row.get("product_cart_url") or "",
        "product_description": product_description,
        "thumbnail_url": thumbnail,
        "video_url": video_url,
        "duration_sec": row.get("duration_sec"),
        "liver_name": row.get("liver_name") or "",
        "og": {
            "title": title or "AitherHub Video",
            "description": og_description,
            "image": thumbnail,
            "video": video_url,
            "url": f"https://www.aitherhub.com/v/{clip_id}",
            "type": "video.other",
        },
    }



# ── OGP HTML for SNS crawlers: /v/{clip_id} server-side rendered ──────
@router.get("/ogp/{clip_id}", response_class=HTMLResponse)
async def share_ogp_page(clip_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Return a minimal HTML page with proper OGP meta tags for SNS crawlers.

    If the request comes from a real browser (not a crawler), redirect to the
    SPA frontend. Crawlers (LINE, Twitter, Facebook, Slack, Discord, etc.)
    get a static HTML page with OGP tags so link previews work correctly.
    """
    from starlette.responses import RedirectResponse

    user_agent = (request.headers.get("user-agent") or "").lower()
    crawler_keywords = [
        "bot", "crawler", "spider", "facebookexternalhit", "twitterbot",
        "slackbot", "discordbot", "linebot", "line/", "linkedinbot", "whatsapp",
        "telegrambot", "applebot", "googlebot", "bingbot", "yandex",
        "pinterest", "redditbot", "embedly", "quora", "outbrain",
        "vkshare", "skypeuripreview", "nuzzel", "w3c_validator",
    ]
    is_crawler = any(kw in user_agent for kw in crawler_keywords)

    if not is_crawler:
        # Real browser → redirect to SPA
        return RedirectResponse(
            url=f"https://www.aitherhub.com/v/{clip_id}",
            status_code=302,
        )

    # Crawler → render OGP HTML
    try:
        meta = await _get_share_clip_meta_impl(clip_id, db)
    except HTTPException:
        return HTMLResponse(
            content="<html><head><title>Not Found</title></head><body>Video not found</body></html>",
            status_code=404,
        )

    og = meta.get("og", {})
    title = _escape_html(og.get("title") or "AitherHub Video")
    description = _escape_html(og.get("description") or "")
    image = _escape_html(og.get("image") or "")
    video = _escape_html(og.get("video") or "")
    url = _escape_html(og.get("url") or f"https://www.aitherhub.com/v/{clip_id}")
    og_type = _escape_html(og.get("type") or "video.other")

    html = f"""<!DOCTYPE html>
<html lang="ja" prefix="og: https://ogp.me/ns#">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<meta name="description" content="{description}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:type" content="{og_type}">
<meta property="og:url" content="{url}">
<meta property="og:site_name" content="AitherHub">
<meta property="og:locale" content="ja_JP">
{"<meta property='og:image' content='" + image + "'>" if image else ""}
{"<meta property='og:image:width' content='1200'>" if image else ""}
{"<meta property='og:image:height' content='630'>" if image else ""}
{"<meta property='og:video' content='" + video + "'>" if video else ""}
{"<meta property='og:video:type' content='video/mp4'>" if video else ""}
<meta name="twitter:card" content="{'summary_large_image' if image else 'summary'}">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{description}">
{"<meta name='twitter:image' content='" + image + "'>" if image else ""}
{"<meta name='twitter:player' content='" + video + "'>" if video else ""}
<link rel="canonical" href="{url}">
</head>
<body>
<h1>{title}</h1>
<p>{description}</p>
<p><a href="{url}">動画を見る</a></p>
</body>
</html>"""

    return HTMLResponse(content=html, status_code=200)


def _escape_html(s: str) -> str:
    """Escape HTML special characters for safe embedding in meta tags."""
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;")
             .replace("'", "&#x27;"))
