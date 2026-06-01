"""
Clip DB – Searchable clip database API.

Turns generated clips from "disposable assets" into a "searchable weapon"
by exposing structured search (SQL filters/tags) and semantic search (Qdrant).

Endpoints:
  GET  /clip-db/search          – structured search with filters, tags, sorting
  GET  /clip-db/semantic-search – AI-powered semantic search via Qdrant embeddings
  GET  /clip-db/stats           – aggregate statistics for admin dashboard
  POST /clip-db/enrich/{clip_id} – manually trigger metadata enrichment for a clip
  POST /clip-db/enrich-all      – batch enrich all un-enriched clips (admin)
  GET  /clip-db/tags            – list all unique tags across clips
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db

ADMIN_ID = "aither"
ADMIN_PASS = "hub"


def _check_admin_or_user(
    user: dict = None,
    x_admin_key: str = None,
) -> bool:
    """Return True if admin (via X-Admin-Key or admin email)."""
    expected = f"{ADMIN_ID}:{ADMIN_PASS}"
    if x_admin_key == expected:
        return True
    if user:
        email = user.get("email", "")
        if email in ("admin@aitherhub.com", "ryuhairartist@gmail.com"):
            return True
    return False

logger = logging.getLogger("clip_db")

router = APIRouter()


# ─── Helpers ───

import re as _re
import unicodedata as _unicodedata

def _normalize_brand_name(name: str) -> str:
    """Normalize brand name for duplicate detection.
    Handles: case, spaces, 'Professional'/'Pro' suffixes,
    common Japanese/English brand name variations (KYOUGOKU→KYOGOKU etc.)
    """
    if not name:
        return ""
    # Lowercase
    n = name.strip().lower()
    # Normalize unicode (full-width → half-width)
    n = _unicodedata.normalize("NFKC", n)
    # Remove common suffixes
    for suffix in [" professional", " pro", " inc", " co", " ltd", " corp", " 株式会社", " co.", " inc."]:
        if n.endswith(suffix):
            n = n[:-len(suffix)]
    # Remove all spaces, hyphens, underscores
    n = _re.sub(r"[\s\-_\.]+", "", n)
    # Common romanization normalizations
    # OU → O (e.g., KYOUGOKU → KYOGOKU)
    n = n.replace("ou", "o")
    # UU → U
    n = n.replace("uu", "u")
    # II → I
    n = n.replace("ii", "i")
    return n


def _replace_blob_url_to_cdn(url: str) -> str:
    """Replace Azure Blob URL with CDN URL if configured."""
    if not url:
        return url
    import os
    cdn_host = os.getenv("AZURE_CDN_HOST", "")
    if cdn_host and "blob.core.windows.net" in url:
        return url.replace(
            url.split("/")[2],
            cdn_host,
        )
    return url


def _parse_json_safe(val):
    """Parse JSON string safely, returning None on failure."""
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return None
    return None


# ─── Request/Response Models ───

class ClipSearchResult(BaseModel):
    clip_id: str
    video_id: str
    phase_index: str
    time_start: Optional[float] = None
    time_end: Optional[float] = None
    duration_sec: Optional[float] = None
    clip_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    transcript_text: Optional[str] = None
    product_name: Optional[str] = None
    product_category: Optional[str] = None
    tags: Optional[list] = None
    is_sold: Optional[bool] = None
    gmv: Optional[float] = None
    viewer_count: Optional[int] = None
    liver_name: Optional[str] = None
    stream_date: Optional[str] = None
    phase_description: Optional[str] = None
    cta_score: Optional[int] = None
    importance_score: Optional[float] = None
    # From video_phases (JOINed)
    sales_psychology_tags: Optional[list] = None
    human_sales_tags: Optional[list] = None
    # Feedback
    rating: Optional[str] = None
    # Video metadata
    video_filename: Optional[str] = None
    created_at: Optional[str] = None
    # Brand assignments
    brand_assignments: Optional[list] = None  # [{client_id, brand_name}]
    # Popularity / edit status
    has_subtitle: Optional[bool] = None  # True if exported_url exists
    download_count: Optional[int] = None  # Number of times downloaded
    # Subtitle / editor state (auto-saved from ClipEditorV2)
    subtitle_style: Optional[str] = None
    subtitle_font_size: Optional[int] = None
    caption_offset: Optional[float] = None
    trim_data: Optional[dict] = None
    subtitle_language: Optional[str] = None
    subtitle_position_x: Optional[float] = None
    subtitle_position_y: Optional[float] = None
    # Unusable marking
    is_unusable: Optional[bool] = None
    unusable_reason: Optional[str] = None
    unusable_comment: Optional[str] = None
    # Language detection
    detected_language: Optional[str] = None
    # AI model version
    ml_model_version: Optional[str] = None
    # Playlists
    playlists: Optional[list] = None  # [{id, name, color, icon}]
    # Regeneration status
    has_regeneration: Optional[bool] = None  # True if clip has been regenerated
    # Batch regen skip status
    regen_skipped: Optional[bool] = None  # True if GPT prefilter skipped this clip
    skip_reason: Optional[str] = None  # Reason for skipping (e.g. "商品訴求力不足")


class ClipSearchResponse(BaseModel):
    clips: List[ClipSearchResult]
    total: int
    page: int
    page_size: int


class ClipStatsResponse(BaseModel):
    total_clips: int
    sold_clips: int
    unsold_clips: int
    unknown_clips: int
    total_gmv: float
    avg_gmv: float
    avg_cta_score: Optional[float] = None
    top_tags: list  # [{tag: str, count: int}]
    top_products: list  # [{product: str, count: int, gmv: float}]
    top_livers: list  # [{liver: str, count: int, gmv: float}]
    clips_by_date: list  # [{date: str, count: int}]
    # NG statistics
    ng_clips: Optional[int] = 0
    no_brand_clips: Optional[int] = 0
    subtitle_clips: Optional[int] = 0
    trimmed_clips: Optional[int] = 0
    downloaded_clips: Optional[int] = 0
    not_downloaded_clips: Optional[int] = 0
    skipped_clips: Optional[int] = 0
    ng_by_reason: Optional[list] = None  # [{reason: str, count: int}]
    skip_by_reason: Optional[list] = None  # [{reason: str, count: int}]
    language_stats: Optional[list] = None  # [{language: str, count: int}]


class EnrichResult(BaseModel):
    clip_id: str
    enriched: bool
    message: str


# ─── Endpoints ───

@router.get("/search", response_model=ClipSearchResponse)
async def search_clips(
    # Text search
    q: Optional[str] = Query(None, description="Full-text search in transcript"),
    # Filters
    tag: Optional[str] = Query(None, description="Filter by tag (e.g. 共感, 権威, 限定性)"),
    product: Optional[str] = Query(None, description="Filter by product name"),
    category: Optional[str] = Query(None, description="Filter by product category"),
    liver: Optional[str] = Query(None, description="Filter by liver name"),
    is_sold: Optional[bool] = Query(None, description="Filter by sold status"),
    min_gmv: Optional[float] = Query(None, description="Minimum GMV"),
    max_gmv: Optional[float] = Query(None, description="Maximum GMV"),
    min_cta: Optional[int] = Query(None, description="Minimum CTA score"),
    rating: Optional[str] = Query(None, description="Filter by rating (good/bad)"),
    video_id: Optional[str] = Query(None, description="Filter by video ID"),
    clip_id: Optional[str] = Query(None, description="Filter by specific clip ID (vc.id)"),
    brand: Optional[str] = Query(None, description="Filter by brand client_id"),
    is_unusable: Optional[bool] = Query(None, description="Filter by unusable status"),
    is_skipped: Optional[bool] = Query(None, description="Filter by regen_skipped status (GPT prefilter skip)"),
    no_brand: Optional[bool] = Query(None, description="Filter clips with no brand assigned"),
    has_subtitle: Optional[bool] = Query(None, description="Filter clips with/without subtitle export"),
    has_trim: Optional[bool] = Query(None, description="Filter clips with/without trim data"),
    not_downloaded: Optional[bool] = Query(None, description="Filter clips never downloaded (download_count=0)"),
    language: Optional[str] = Query(None, description="Filter by detected language: ja, zh-TW, zh-CN, en, ko, th"),
    ai_version: Optional[str] = Query(None, description="Filter by AI model version (e.g. v7.20260501, pre-ai for no version)"),
    playlist_id: Optional[str] = Query(None, description="Filter by playlist ID (show only clips in this playlist)"),
    # Sorting
    sort_by: str = Query("uploaded_at", description="Sort field: uploaded_at, created_at, gmv, cta_score, importance_score, duration_sec, rating, stream_date"),
    sort_order: str = Query("desc", description="Sort order: asc or desc"),
    # Pagination
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """
    Search clips with structured filters, tags, and sorting.
    Returns clips enriched with metadata from video_phases and clip_feedback.
    """
    conditions = ["vc.status = 'completed'", "vc.clip_url IS NOT NULL"]
    params = {}

    is_admin = _check_admin_or_user(x_admin_key=x_admin_key)

    # Text search in transcript
    if q:
        conditions.append("vc.transcript_text ILIKE :q")
        params["q"] = f"%{q}%"

    # Tag filter - search in both vc.tags and vp.sales_psychology_tags
    if tag:
        conditions.append("""(
            vc.tags::text ILIKE :tag_like
            OR vp.sales_psychology_tags::text ILIKE :tag_like
        )""")
        params["tag_like"] = f"%{tag}%"

    # Product filter
    if product:
        conditions.append("""(
            vc.product_name ILIKE :product
            OR vp.product_names ILIKE :product
        )""")
        params["product"] = f"%{product}%"

    # Category filter
    if category:
        conditions.append("vc.product_category ILIKE :category")
        params["category"] = f"%{category}%"

    # Liver filter
    if liver:
        conditions.append("vc.liver_name ILIKE :liver")
        params["liver"] = f"%{liver}%"

    # Sold status
    if is_sold is not None:
        conditions.append("vc.is_sold = :is_sold")
        params["is_sold"] = is_sold

    # GMV range
    if min_gmv is not None:
        conditions.append("COALESCE(vc.gmv, 0) >= :min_gmv")
        params["min_gmv"] = min_gmv
    if max_gmv is not None:
        conditions.append("COALESCE(vc.gmv, 0) <= :max_gmv")
        params["max_gmv"] = max_gmv

    # CTA score
    if min_cta is not None:
        conditions.append("COALESCE(vc.cta_score, 0) >= :min_cta")
        params["min_cta"] = min_cta

    # Rating filter (from clip_feedback)
    if rating:
        conditions.append("cf.rating = :rating")
        params["rating"] = rating

    # Video ID filter
    if video_id:
        conditions.append("vc.video_id = :video_id")
        params["video_id"] = video_id

    # Clip ID filter (filter by specific clip, or find all clips from same video)
    if clip_id:
        # Find the video_id for this clip, then show all clips from that video
        conditions.append("vc.video_id = (SELECT video_id FROM video_clips WHERE id = CAST(:clip_id AS uuid))")
        params["clip_id"] = clip_id

    # Brand filter (via widget_clip_assignments)
    if brand:
        conditions.append("""
            vc.id::text IN (
                SELECT wca.clip_id FROM widget_clip_assignments wca
                WHERE wca.client_id = :brand_filter AND wca.is_active = TRUE
            )
        """)
        params["brand_filter"] = brand

    # NG filter logic:
    # - is_unusable=true  → show ONLY NG clips
    # - is_unusable=false → show ONLY non-NG clips (explicit)
    # - is_unusable=None  → DEFAULT: exclude NG clips (NG are hidden unless explicitly requested)
    if is_unusable is True:
        conditions.append("COALESCE(vc.is_unusable, FALSE) = TRUE")
    elif is_unusable is False:
        conditions.append("COALESCE(vc.is_unusable, FALSE) = FALSE")
    else:
        # Default: exclude NG clips from normal listing
        conditions.append("COALESCE(vc.is_unusable, FALSE) = FALSE")

    # Skipped filter (GPT prefilter skip)
    if is_skipped is True:
        conditions.append("COALESCE(vc.regen_skipped, FALSE) = TRUE")
    elif is_skipped is False:
        conditions.append("COALESCE(vc.regen_skipped, FALSE) = FALSE")

    # No brand assigned filter (always excludes NG clips)
    if no_brand is True:
        conditions.append("""
            vc.id::text NOT IN (
                SELECT wca.clip_id FROM widget_clip_assignments wca
                WHERE wca.is_active = TRUE
            )
        """)
    elif no_brand is False:
        conditions.append("""
            vc.id::text IN (
                SELECT wca.clip_id FROM widget_clip_assignments wca
                WHERE wca.is_active = TRUE
            )
        """)

    # Has subtitle export filter
    if has_subtitle is True:
        conditions.append("vc.exported_url IS NOT NULL")
    elif has_subtitle is False:
        conditions.append("vc.exported_url IS NULL")

    # Has trim data filter
    if has_trim is True:
        conditions.append("vc.trim_data IS NOT NULL")
    elif has_trim is False:
        conditions.append("vc.trim_data IS NULL")

    # Not downloaded filter (clips with zero downloads)
    # Match by clip_id OR by (video_id + phase_index) for legacy records where clip_id was NULL
    if not_downloaded is True:
        conditions.append("""
            vc.id::text NOT IN (SELECT DISTINCT clip_id::text FROM clip_download_log WHERE clip_id IS NOT NULL)
            AND NOT EXISTS (
                SELECT 1 FROM clip_download_log cdl2
                WHERE cdl2.video_id = vc.video_id
                AND cdl2.phase_index = vc.phase_index::text
                AND cdl2.clip_id IS NULL
            )
        """)
    elif not_downloaded is False:
        conditions.append("""
            (
                vc.id::text IN (SELECT DISTINCT clip_id::text FROM clip_download_log WHERE clip_id IS NOT NULL)
                OR EXISTS (
                    SELECT 1 FROM clip_download_log cdl2
                    WHERE cdl2.video_id = vc.video_id
                    AND cdl2.phase_index = vc.phase_index::text
                    AND cdl2.clip_id IS NULL
                )
            )
        """)

    # Language filter
    if language:
        conditions.append("vc.detected_language = :language")
        params["language"] = language

    # AI version filter
    if ai_version:
        if ai_version.lower() in ('pre-ai', 'pre_ai', 'none'):
            conditions.append("vc.ml_model_version IS NULL")
        else:
            conditions.append("vc.ml_model_version = :ai_version")
            params["ai_version"] = ai_version

    # Playlist filter
    if playlist_id:
        conditions.append("""
            vc.id IN (
                SELECT cpi.clip_id FROM clip_playlist_items cpi
                WHERE cpi.playlist_id = CAST(:playlist_id AS uuid)
            )
        """)
        params["playlist_id"] = playlist_id

    where_clause = " AND ".join(conditions)

    # Validate sort
    allowed_sorts = {
        "uploaded_at": "v.created_at",
        "created_at": "vc.created_at",
        "gmv": "COALESCE(vc.gmv, 0)",
        "cta_score": "COALESCE(vc.cta_score, 0)",
        "importance_score": "COALESCE(vc.importance_score, 0)",
        "duration_sec": "COALESCE(vc.duration_sec, 0)",
        "rating": "cf.rating",
        "stream_date": "vc.stream_date",
    }
    sort_col = allowed_sorts.get(sort_by, "v.created_at")
    sort_dir = "DESC" if sort_order.lower() == "desc" else "ASC"
    # For the outer ORDER BY after subquery, map to alias
    outer_sort_map = {
        "uploaded_at": "video_uploaded_at",
        "created_at": "created_at",
        "gmv": "sort_gmv",
        "cta_score": "sort_cta_score",
        "importance_score": "sort_importance_score",
        "duration_sec": "sort_duration_sec",
        "rating": "rating",
        "stream_date": "stream_date",
    }
    outer_sort_col = outer_sort_map.get(sort_by, "video_uploaded_at")

    # Count query
    count_sql = text(f"""
        SELECT COUNT(DISTINCT vc.id)
        FROM video_clips vc
        LEFT JOIN video_phases vp ON vp.video_id = vc.video_id
            AND vp.phase_index = CASE
                WHEN vc.phase_index ~ '^[0-9]+$' THEN CAST(vc.phase_index AS INTEGER)
                ELSE -1
            END
        LEFT JOIN clip_feedback cf ON cf.video_id = vc.video_id
            AND cf.phase_index = vc.phase_index
        WHERE {where_clause}
    """)

    # Main query
    offset = (page - 1) * page_size
    params["limit"] = page_size
    params["offset"] = offset

    main_sql = text(f"""
        SELECT * FROM (
            SELECT DISTINCT ON (vc.id)
                vc.id as clip_id,
                vc.video_id,
                vc.phase_index,
                vc.time_start,
                vc.time_end,
                vc.duration_sec,
                vc.clip_url,
                vc.sas_token,
                vc.sas_expireddate,
                vc.thumbnail_url,
                vc.transcript_text,
                vc.product_name,
                vc.product_category,
                vc.tags,
                vc.is_sold,
                vc.gmv,
                vc.viewer_count,
                vc.liver_name,
                vc.stream_date,
                vc.phase_description,
                vc.cta_score,
                vc.importance_score,
                vc.created_at,
                vc.captions,
                vc.subtitle_style,
                vc.subtitle_font_size,
                vc.caption_offset,
                vc.trim_data,
                vc.subtitle_language,
                vc.subtitle_position_x,
                vc.subtitle_position_y,
                vp.sales_psychology_tags,
                vp.human_sales_tags,
                vp.product_names as vp_product_names,
                COALESCE(vp.gmv, 0) as vp_gmv,
                COALESCE(vp.viewer_count, 0) as vp_viewer_count,
                cf.rating,
                v.original_filename as video_filename,
                v.created_at as video_uploaded_at,
                vc.exported_url,
                COALESCE(cdl.download_count, 0) as download_count,
                COALESCE(vc.is_unusable, FALSE) as is_unusable,
                vc.unusable_reason,
                vc.unusable_comment,
                vc.detected_language,
                vc.ml_model_version,
                COALESCE(vc.regen_skipped, FALSE) as regen_skipped,
                vc.skip_reason,
                COALESCE(vc.gmv, 0) as sort_gmv,
                COALESCE(vc.cta_score, 0) as sort_cta_score,
                COALESCE(vc.importance_score, 0) as sort_importance_score,
                COALESCE(vc.duration_sec, 0) as sort_duration_sec,
                EXISTS(
                    SELECT 1 FROM ai_clip_jobs acj
                    WHERE acj.config->>'source_clip_id' = vc.id::text
                      AND acj.config->>'type' = 'regenerate_from_source'
                      AND acj.status = 'done'
                ) as has_regeneration
            FROM video_clips vc
            LEFT JOIN video_phases vp ON vp.video_id = vc.video_id
                AND vp.phase_index = CASE
                    WHEN vc.phase_index ~ '^[0-9]+$' THEN CAST(vc.phase_index AS INTEGER)
                    ELSE -1
                END
            LEFT JOIN clip_feedback cf ON cf.video_id = vc.video_id
                AND cf.phase_index = vc.phase_index
            LEFT JOIN videos v ON v.id = vc.video_id
            LEFT JOIN LATERAL (
                SELECT COUNT(*) as download_count
                FROM clip_download_log cdl_inner
                WHERE cdl_inner.clip_id = vc.id
                   OR (cdl_inner.clip_id IS NULL AND cdl_inner.video_id = vc.video_id AND cdl_inner.phase_index = vc.phase_index::text)
            ) cdl ON TRUE
            WHERE {where_clause}
            ORDER BY vc.id
        ) sub
        ORDER BY {outer_sort_col} {sort_dir} NULLS LAST
        LIMIT :limit OFFSET :offset
    """)

    try:
        count_result = await db.execute(count_sql, params)
        total = count_result.scalar() or 0

        result = await db.execute(main_sql, params)
        rows = result.fetchall()

        # Batch load brand assignments for all clip_ids
        clip_ids_for_brands = [str(row.clip_id) for row in rows]
        brand_map = {}  # clip_id -> [{client_id, brand_name}]
        if clip_ids_for_brands:
            brand_sql = text("""
                SELECT wca.clip_id, wca.client_id, wc.name as brand_name
                FROM widget_clip_assignments wca
                JOIN widget_clients wc ON wc.client_id = wca.client_id
                WHERE wca.clip_id = ANY(:cids) AND wca.is_active = TRUE
            """)
            brand_result = await db.execute(brand_sql, {"cids": clip_ids_for_brands})
            for br in brand_result.mappings().all():
                cid = br["clip_id"]
                if cid not in brand_map:
                    brand_map[cid] = []
                brand_map[cid].append({"client_id": br["client_id"], "brand_name": br["brand_name"]})

        # Batch load playlist assignments for all clip_ids
        playlist_map = {}  # clip_id -> [{id, name, color, icon}]
        if clip_ids_for_brands:
            try:
                playlist_sql = text("""
                    SELECT cpi.clip_id::text as clip_id, p.id::text as playlist_id, p.name, p.color, p.icon
                    FROM clip_playlist_items cpi
                    JOIN clip_playlists p ON p.id = cpi.playlist_id
                    WHERE cpi.clip_id::text = ANY(:cids)
                """)
                pl_result = await db.execute(playlist_sql, {"cids": clip_ids_for_brands})
                for pr in pl_result.mappings().all():
                    cid = pr["clip_id"]
                    if cid not in playlist_map:
                        playlist_map[cid] = []
                    playlist_map[cid].append({"id": pr["playlist_id"], "name": pr["name"], "color": pr["color"], "icon": pr["icon"]})
            except Exception:
                pass  # Table may not exist yet on first deploy

        clips = []
        for row in rows:
            # Build clip URL (with SAS if needed)
            clip_url = None
            if row.clip_url:
                if row.sas_token and row.sas_expireddate:
                    now = datetime.now(timezone.utc)
                    expiry = row.sas_expireddate
                    if expiry.tzinfo is None:
                        expiry = expiry.replace(tzinfo=timezone.utc)
                    if expiry > now:
                        clip_url = row.sas_token
                if not clip_url:
                    try:
                        from app.services.storage_service import generate_read_sas_from_url
                        sas_url = generate_read_sas_from_url(row.clip_url)
                        clip_url = _replace_blob_url_to_cdn(sas_url) if sas_url else _replace_blob_url_to_cdn(row.clip_url)
                    except Exception:
                        clip_url = _replace_blob_url_to_cdn(row.clip_url)

            # Merge tags from vc.tags and vp.sales_psychology_tags
            vc_tags = _parse_json_safe(row.tags) or []
            sp_tags = _parse_json_safe(row.sales_psychology_tags) or []
            hs_tags = _parse_json_safe(row.human_sales_tags) or []
            merged_tags = list(set(
                (vc_tags if isinstance(vc_tags, list) else []) +
                (sp_tags if isinstance(sp_tags, list) else []) +
                (hs_tags if isinstance(hs_tags, list) else [])
            ))

            # Build transcript from captions if transcript_text is empty
            transcript = row.transcript_text
            if not transcript and row.captions:
                caps = _parse_json_safe(row.captions)
                if caps and isinstance(caps, list):
                    transcript = " ".join(c.get("text", "") for c in caps if c.get("text"))

            # Use vp data as fallback
            product = row.product_name or (row.vp_product_names if hasattr(row, 'vp_product_names') else None)
            gmv_val = row.gmv if row.gmv else (row.vp_gmv if hasattr(row, 'vp_gmv') else 0)

            clips.append(ClipSearchResult(
                clip_id=str(row.clip_id),
                video_id=str(row.video_id),
                phase_index=str(row.phase_index),
                time_start=row.time_start,
                time_end=row.time_end,
                duration_sec=row.duration_sec or (
                    (row.time_end - row.time_start) if row.time_start is not None and row.time_end is not None else None
                ),
                clip_url=clip_url,
                thumbnail_url=row.thumbnail_url,
                transcript_text=transcript[:500] if transcript else None,
                product_name=product,
                product_category=row.product_category,
                tags=merged_tags if merged_tags else None,
                is_sold=row.is_sold,
                gmv=gmv_val,
                viewer_count=row.viewer_count or (row.vp_viewer_count if hasattr(row, 'vp_viewer_count') else 0),
                liver_name=row.liver_name,
                stream_date=str(row.stream_date) if row.stream_date else None,
                phase_description=row.phase_description,
                cta_score=row.cta_score,
                importance_score=row.importance_score,
                sales_psychology_tags=sp_tags if sp_tags else None,
                human_sales_tags=hs_tags if hs_tags else None,
                rating=row.rating,
                video_filename=row.video_filename,
                created_at=row.created_at.isoformat() if row.created_at else None,
                brand_assignments=brand_map.get(str(row.clip_id)),
                has_subtitle=bool(row.exported_url) if hasattr(row, 'exported_url') else None,
                download_count=row.download_count if hasattr(row, 'download_count') else 0,
                subtitle_style=row.subtitle_style if hasattr(row, 'subtitle_style') else None,
                subtitle_font_size=row.subtitle_font_size if hasattr(row, 'subtitle_font_size') else None,
                caption_offset=row.caption_offset if hasattr(row, 'caption_offset') else None,
                trim_data=_parse_json_safe(row.trim_data) if hasattr(row, 'trim_data') and row.trim_data else None,
                subtitle_language=row.subtitle_language if hasattr(row, 'subtitle_language') else None,
                subtitle_position_x=row.subtitle_position_x if hasattr(row, 'subtitle_position_x') else None,
                subtitle_position_y=row.subtitle_position_y if hasattr(row, 'subtitle_position_y') else None,
                is_unusable=bool(row.is_unusable) if hasattr(row, 'is_unusable') else False,
                unusable_reason=row.unusable_reason if hasattr(row, 'unusable_reason') else None,
                unusable_comment=row.unusable_comment if hasattr(row, 'unusable_comment') else None,
                detected_language=row.detected_language if hasattr(row, 'detected_language') else None,
                ml_model_version=row.ml_model_version if hasattr(row, 'ml_model_version') else None,
                playlists=playlist_map.get(str(row.clip_id)),
                has_regeneration=bool(row.has_regeneration) if hasattr(row, 'has_regeneration') else None,
                regen_skipped=bool(row.regen_skipped) if hasattr(row, 'regen_skipped') else None,
                skip_reason=row.skip_reason if hasattr(row, 'skip_reason') else None,
            ))

        return ClipSearchResponse(
            clips=clips,
            total=total,
            page=page,
            page_size=page_size,
        )

    except Exception as e:
        logger.error(f"[clip-db] Search failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@router.get("/stats", response_model=ClipStatsResponse)
async def get_clip_stats(
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """
    Get aggregate statistics for the clip database.
    Used by admin dashboard and clip DB overview page.
    """
    try:
        # Basic counts
        stats_sql = text("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE vc.is_sold = true) as sold,
                COUNT(*) FILTER (WHERE vc.is_sold = false) as unsold,
                COUNT(*) FILTER (WHERE vc.is_sold IS NULL) as unknown,
                COALESCE(SUM(vc.gmv), 0) as total_gmv,
                COALESCE(AVG(vc.gmv) FILTER (WHERE vc.gmv > 0), 0) as avg_gmv,
                COALESCE(AVG(vc.cta_score) FILTER (WHERE vc.cta_score IS NOT NULL), 0) as avg_cta
            FROM video_clips vc
            WHERE vc.status = 'completed' AND vc.clip_url IS NOT NULL
        """)
        result = await db.execute(stats_sql)
        stats_row = result.fetchone()

        # Top tags (from video_phases.sales_psychology_tags for all clips)
        tags_sql = text("""
            SELECT vp.sales_psychology_tags
            FROM video_clips vc
            JOIN video_phases vp ON vp.video_id = vc.video_id
                AND vp.phase_index = CASE
                    WHEN vc.phase_index ~ '^[0-9]+$' THEN CAST(vc.phase_index AS INTEGER)
                    ELSE -1
                END
            WHERE vc.status = 'completed' AND vc.clip_url IS NOT NULL
                AND vp.sales_psychology_tags IS NOT NULL
        """)
        tags_result = await db.execute(tags_sql)
        tag_counts = {}
        for row in tags_result.fetchall():
            parsed = _parse_json_safe(row.sales_psychology_tags)
            if parsed and isinstance(parsed, list):
                for t in parsed:
                    if isinstance(t, str) and t.strip():
                        tag_counts[t.strip()] = tag_counts.get(t.strip(), 0) + 1
        top_tags = sorted(
            [{"tag": k, "count": v} for k, v in tag_counts.items()],
            key=lambda x: x["count"],
            reverse=True,
        )[:20]

        # Top products
        products_sql = text("""
            SELECT
                COALESCE(vc.product_name, vp.product_names) as product,
                COUNT(*) as cnt,
                COALESCE(SUM(COALESCE(vc.gmv, vp.gmv, 0)), 0) as total_gmv
            FROM video_clips vc
            LEFT JOIN video_phases vp ON vp.video_id = vc.video_id
                AND vp.phase_index = CASE
                    WHEN vc.phase_index ~ '^[0-9]+$' THEN CAST(vc.phase_index AS INTEGER)
                    ELSE -1
                END
            WHERE vc.status = 'completed' AND vc.clip_url IS NOT NULL
                AND COALESCE(vc.product_name, vp.product_names) IS NOT NULL
                AND COALESCE(vc.product_name, vp.product_names) != ''
            GROUP BY COALESCE(vc.product_name, vp.product_names)
            ORDER BY cnt DESC
            LIMIT 10
        """)
        products_result = await db.execute(products_sql)
        top_products = [
            {"product": r.product, "count": r.cnt, "gmv": float(r.total_gmv)}
            for r in products_result.fetchall()
        ]

        # Top livers
        livers_sql = text("""
            SELECT
                vc.liver_name,
                COUNT(*) as cnt,
                COALESCE(SUM(COALESCE(vc.gmv, 0)), 0) as total_gmv
            FROM video_clips vc
            WHERE vc.status = 'completed' AND vc.clip_url IS NOT NULL
                AND vc.liver_name IS NOT NULL AND vc.liver_name != ''
            GROUP BY vc.liver_name
            ORDER BY cnt DESC
            LIMIT 10
        """)
        livers_result = await db.execute(livers_sql)
        top_livers = [
            {"liver": r.liver_name, "count": r.cnt, "gmv": float(r.total_gmv)}
            for r in livers_result.fetchall()
        ]

        # Clips by date (last 30 days)
        date_sql = text("""
            SELECT DATE(vc.created_at) as dt, COUNT(*) as cnt
            FROM video_clips vc
            WHERE vc.status = 'completed' AND vc.clip_url IS NOT NULL
                AND vc.created_at >= NOW() - INTERVAL '30 days'
            GROUP BY DATE(vc.created_at)
            ORDER BY dt ASC
        """)
        date_result = await db.execute(date_sql)
        clips_by_date = [
            {"date": str(r.dt), "count": r.cnt}
            for r in date_result.fetchall()
        ]

        # NG / status statistics
        ng_stats_sql = text("""
            SELECT
                COUNT(*) FILTER (WHERE COALESCE(vc.is_unusable, FALSE) = TRUE) as ng_count,
                COUNT(*) FILTER (WHERE COALESCE(vc.regen_skipped, FALSE) = TRUE) as skipped_count,
                COUNT(*) FILTER (WHERE COALESCE(vc.is_unusable, FALSE) = FALSE AND vc.exported_url IS NOT NULL) as subtitle_count,
                COUNT(*) FILTER (WHERE COALESCE(vc.is_unusable, FALSE) = FALSE AND vc.trim_data IS NOT NULL) as trimmed_count,
                COUNT(*) FILTER (WHERE COALESCE(vc.is_unusable, FALSE) = FALSE AND vc.id::text NOT IN (
                    SELECT wca.clip_id FROM widget_clip_assignments wca WHERE wca.is_active = TRUE
                )) as no_brand_count,
                COUNT(*) FILTER (WHERE COALESCE(vc.is_unusable, FALSE) = FALSE AND (
                    vc.id::text IN (SELECT DISTINCT clip_id::text FROM clip_download_log WHERE clip_id IS NOT NULL)
                    OR EXISTS (
                        SELECT 1 FROM clip_download_log cdl2
                        WHERE cdl2.video_id = vc.video_id
                        AND cdl2.phase_index = vc.phase_index::text
                        AND cdl2.clip_id IS NULL
                    )
                )) as downloaded_count
            FROM video_clips vc
            WHERE vc.status = 'completed' AND vc.clip_url IS NOT NULL
        """)
        ng_stats_result = await db.execute(ng_stats_sql)
        ng_stats_row = ng_stats_result.fetchone()

        # NG by reason breakdown
        ng_reason_sql = text("""
            SELECT vc.unusable_reason, COUNT(*) as cnt
            FROM video_clips vc
            WHERE vc.status = 'completed' AND vc.clip_url IS NOT NULL
                AND COALESCE(vc.is_unusable, FALSE) = TRUE
                AND vc.unusable_reason IS NOT NULL
            GROUP BY vc.unusable_reason
            ORDER BY cnt DESC
        """)
        ng_reason_result = await db.execute(ng_reason_sql)
        ng_by_reason = [
            {"reason": r.unusable_reason, "count": r.cnt}
            for r in ng_reason_result.fetchall()
        ]

        # Skip by reason breakdown
        skip_reason_sql = text("""
            SELECT vc.skip_reason, COUNT(*) as cnt
            FROM video_clips vc
            WHERE vc.status = 'completed' AND vc.clip_url IS NOT NULL
                AND COALESCE(vc.regen_skipped, FALSE) = TRUE
                AND vc.skip_reason IS NOT NULL
            GROUP BY vc.skip_reason
            ORDER BY cnt DESC
        """)
        skip_reason_result = await db.execute(skip_reason_sql)
        skip_by_reason = [
            {"reason": r.skip_reason, "count": r.cnt}
            for r in skip_reason_result.fetchall()
        ]

        # Language statistics
        lang_sql = text("""
            SELECT COALESCE(vc.detected_language, 'unknown') as lang, COUNT(*) as cnt
            FROM video_clips vc
            WHERE vc.status = 'completed' AND vc.clip_url IS NOT NULL
                AND COALESCE(vc.is_unusable, FALSE) = FALSE
            GROUP BY COALESCE(vc.detected_language, 'unknown')
            ORDER BY cnt DESC
        """)
        lang_result = await db.execute(lang_sql)
        language_stats = [
            {"language": r.lang, "count": r.cnt}
            for r in lang_result.fetchall()
        ]

        return ClipStatsResponse(
            total_clips=stats_row.total or 0,
            sold_clips=stats_row.sold or 0,
            unsold_clips=stats_row.unsold or 0,
            unknown_clips=stats_row.unknown or 0,
            total_gmv=float(stats_row.total_gmv or 0),
            avg_gmv=float(stats_row.avg_gmv or 0),
            avg_cta_score=float(stats_row.avg_cta or 0) if stats_row.avg_cta else None,
            top_tags=top_tags,
            top_products=top_products,
            top_livers=top_livers,
            clips_by_date=clips_by_date,
            ng_clips=ng_stats_row.ng_count or 0,
            skipped_clips=ng_stats_row.skipped_count or 0,
            no_brand_clips=ng_stats_row.no_brand_count or 0,
            subtitle_clips=ng_stats_row.subtitle_count or 0,
            trimmed_clips=ng_stats_row.trimmed_count or 0,
            downloaded_clips=ng_stats_row.downloaded_count or 0,
            not_downloaded_clips=(stats_row.total or 0) - (ng_stats_row.ng_count or 0) - (ng_stats_row.downloaded_count or 0),
            ng_by_reason=ng_by_reason,
            skip_by_reason=skip_by_reason,
            language_stats=language_stats,
        )

    except Exception as e:
        logger.error(f"[clip-db] Stats failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Stats failed: {str(e)}")


@router.get("/tags")
async def get_all_tags(
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """Get all unique tags across clips (from both vc.tags and vp.sales_psychology_tags)."""
    try:
        sql = text("""
            SELECT vp.sales_psychology_tags
            FROM video_clips vc
            JOIN video_phases vp ON vp.video_id = vc.video_id
                AND vp.phase_index = CASE
                    WHEN vc.phase_index ~ '^[0-9]+$' THEN CAST(vc.phase_index AS INTEGER)
                    ELSE -1
                END
            WHERE vc.status = 'completed' AND vc.clip_url IS NOT NULL
                AND vp.sales_psychology_tags IS NOT NULL
        """)
        result = await db.execute(sql)
        tag_counts = {}
        for row in result.fetchall():
            parsed = _parse_json_safe(row.sales_psychology_tags)
            if parsed and isinstance(parsed, list):
                for t in parsed:
                    if isinstance(t, str) and t.strip():
                        tag_counts[t.strip()] = tag_counts.get(t.strip(), 0) + 1

        # Also check vc.tags
        sql2 = text("""
            SELECT vc.tags
            FROM video_clips vc
            WHERE vc.status = 'completed' AND vc.clip_url IS NOT NULL
                AND vc.tags IS NOT NULL
        """)
        result2 = await db.execute(sql2)
        for row in result2.fetchall():
            parsed = _parse_json_safe(row.tags)
            if parsed and isinstance(parsed, list):
                for t in parsed:
                    if isinstance(t, str) and t.strip():
                        tag_counts[t.strip()] = tag_counts.get(t.strip(), 0) + 1

        tags = sorted(
            [{"tag": k, "count": v} for k, v in tag_counts.items()],
            key=lambda x: x["count"],
            reverse=True,
        )

        return {"tags": tags, "total": len(tags)}

    except Exception as e:
        logger.error(f"[clip-db] Tags failed: {e}", exc_info=True)
        return {"tags": [], "total": 0}


@router.post("/enrich/{clip_id}", response_model=EnrichResult)
async def enrich_clip(
    clip_id: str,
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """
    Enrich a single clip with metadata from video_phases, video, and captions.
    Copies sales_psychology_tags, gmv, product_names, etc. into video_clips columns.
    """
    try:
        enriched = await _enrich_clip_metadata(db, clip_id)
        if enriched:
            return EnrichResult(clip_id=clip_id, enriched=True, message="Clip enriched successfully")
        else:
            return EnrichResult(clip_id=clip_id, enriched=False, message="Clip not found or already enriched")
    except Exception as e:
        logger.error(f"[clip-db] Enrich failed for {clip_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/enrich-all")
async def enrich_all_clips(
    force: bool = Query(False, description="Force re-enrich even if already enriched"),
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """
    Batch enrich all completed clips that haven't been enriched yet.
    Admin-only endpoint.
    """
    if not _check_admin_or_user(x_admin_key=x_admin_key):
        raise HTTPException(status_code=403, detail="Admin only")

    try:
        condition = "vc.status = 'completed' AND vc.clip_url IS NOT NULL"
        if not force:
            condition += " AND vc.enriched_at IS NULL"

        sql = text(f"SELECT vc.id FROM video_clips vc WHERE {condition}")
        result = await db.execute(sql)
        clip_ids = [str(row.id) for row in result.fetchall()]

        enriched_count = 0
        failed_count = 0
        for cid in clip_ids:
            try:
                ok = await _enrich_clip_metadata(db, cid)
                if ok:
                    enriched_count += 1
                else:
                    failed_count += 1
            except Exception as e:
                logger.warning(f"[clip-db] Enrich failed for {cid}: {e}")
                failed_count += 1

        return {
            "total": len(clip_ids),
            "enriched": enriched_count,
            "failed": failed_count,
            "message": f"Enriched {enriched_count}/{len(clip_ids)} clips",
        }

    except Exception as e:
        logger.error(f"[clip-db] Enrich-all failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/semantic-search")
async def semantic_search_clips(
    q: str = Query(..., description="Natural language query for semantic search"),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """
    Semantic search using Qdrant vector DB.
    Finds clips with similar speech patterns, selling techniques, or tones.
    """
    try:
        from app.services.rag.embedding_service import create_analysis_embedding
        from app.services.rag.rag_client import get_qdrant_client, COLLECTION_NAME

        # Create embedding for the query
        query_embedding = create_analysis_embedding(
            speech_text=q,
            visual_context="",
            phase_type="",
            ai_insight="",
            sales_context="",
        )

        client = get_qdrant_client()

        # Search Qdrant
        search_results = client.search(
            collection_name=COLLECTION_NAME,
            query_vector=query_embedding,
            limit=limit * 2,  # Get extra to filter
        )

        # Match Qdrant results to video_clips
        clips = []
        for hit in search_results:
            payload = hit.payload or {}
            vid = payload.get("video_id")
            pidx = payload.get("phase_index")
            if vid is None or pidx is None:
                continue

            # Find matching clip
            clip_sql = text("""
                SELECT vc.id, vc.video_id, vc.phase_index, vc.time_start, vc.time_end,
                       vc.clip_url, vc.sas_token, vc.sas_expireddate,
                       vc.transcript_text, vc.product_name, vc.tags, vc.gmv,
                       vc.liver_name, vc.captions, vc.duration_sec,
                       v.original_filename as video_filename
                FROM video_clips vc
                LEFT JOIN videos v ON v.id = vc.video_id
                WHERE vc.video_id = :vid AND vc.phase_index = :pidx
                    AND vc.status = 'completed' AND vc.clip_url IS NOT NULL
                LIMIT 1
            """)
            clip_result = await db.execute(clip_sql, {"vid": vid, "pidx": str(pidx)})
            clip_row = clip_result.fetchone()

            if clip_row:
                # Build URL
                clip_url = None
                if clip_row.clip_url:
                    if clip_row.sas_token and clip_row.sas_expireddate:
                        now = datetime.now(timezone.utc)
                        expiry = clip_row.sas_expireddate
                        if expiry and expiry.tzinfo is None:
                            expiry = expiry.replace(tzinfo=timezone.utc)
                        if expiry and expiry > now:
                            clip_url = clip_row.sas_token
                    if not clip_url:
                        clip_url = _replace_blob_url_to_cdn(clip_row.clip_url)

                transcript = clip_row.transcript_text
                if not transcript and clip_row.captions:
                    caps = _parse_json_safe(clip_row.captions)
                    if caps and isinstance(caps, list):
                        transcript = " ".join(c.get("text", "") for c in caps if c.get("text"))

                clips.append({
                    "clip_id": str(clip_row.id),
                    "video_id": str(clip_row.video_id),
                    "phase_index": str(clip_row.phase_index),
                    "time_start": clip_row.time_start,
                    "time_end": clip_row.time_end,
                    "duration_sec": clip_row.duration_sec,
                    "clip_url": clip_url,
                    "transcript_text": transcript[:500] if transcript else None,
                    "product_name": clip_row.product_name,
                    "tags": _parse_json_safe(clip_row.tags),
                    "gmv": clip_row.gmv,
                    "liver_name": clip_row.liver_name,
                    "video_filename": clip_row.video_filename,
                    "score": hit.score,
                    # Qdrant payload extras
                    "speech_text": payload.get("speech_text", "")[:300],
                    "phase_type": payload.get("phase_type", ""),
                    "ai_insight": payload.get("ai_insight", "")[:300],
                })

                if len(clips) >= limit:
                    break

        return {"clips": clips, "total": len(clips), "query": q}

    except Exception as e:
        logger.error(f"[clip-db] Semantic search failed: {e}", exc_info=True)
        # Graceful fallback: return empty results instead of 500
        return {"clips": [], "total": 0, "query": q, "error": str(e)}


# ─── Internal: Enrich clip metadata ───

async def _enrich_clip_metadata(db: AsyncSession, clip_id: str) -> bool:
    """
    Enrich a clip by copying metadata from video_phases, videos, and captions.
    Returns True if enrichment was performed.
    """
    # Get clip
    clip_sql = text("""
        SELECT vc.id, vc.video_id, vc.phase_index, vc.time_start, vc.time_end,
               vc.captions, vc.transcript_text, vc.enriched_at
        FROM video_clips vc
        WHERE vc.id = :clip_id AND vc.status = 'completed'
    """)
    result = await db.execute(clip_sql, {"clip_id": clip_id})
    clip = result.fetchone()
    if not clip:
        return False

    video_id = str(clip.video_id)
    phase_index = str(clip.phase_index)

    # Get video metadata
    video_sql = text("""
        SELECT v.original_filename, v.user_id, v.created_at,
               v.top_products
        FROM videos v WHERE v.id = :vid
    """)
    v_result = await db.execute(video_sql, {"vid": video_id})
    video = v_result.fetchone()

    # Get phase metadata (only for numeric phase_index)
    phase = None
    if phase_index.isdigit():
        phase_sql = text("""
            SELECT vp.phase_description, vp.gmv, vp.order_count, vp.viewer_count,
                   vp.product_names, vp.importance_score, vp.cta_score,
                   vp.sales_psychology_tags, vp.human_sales_tags, vp.conversion_rate
            FROM video_phases vp
            WHERE vp.video_id = :vid AND vp.phase_index = :pidx
        """)
        p_result = await db.execute(phase_sql, {"vid": video_id, "pidx": int(phase_index)})
        phase = p_result.fetchone()

    # Build transcript from captions
    transcript = clip.transcript_text
    if not transcript and clip.captions:
        caps = _parse_json_safe(clip.captions)
        if caps and isinstance(caps, list):
            transcript = " ".join(c.get("text", "") for c in caps if c.get("text"))

    # Build update dict
    updates = {
        "transcript_text": transcript,
        "duration_sec": (clip.time_end - clip.time_start) if clip.time_start is not None and clip.time_end is not None else None,
        "enriched_at": datetime.now(timezone.utc),
    }

    if phase:
        updates["phase_description"] = phase.phase_description
        updates["gmv"] = phase.gmv or 0
        updates["viewer_count"] = phase.viewer_count or 0
        updates["product_name"] = phase.product_names
        updates["cta_score"] = phase.cta_score
        updates["importance_score"] = phase.importance_score
        updates["is_sold"] = (phase.gmv or 0) > 0 or (phase.order_count or 0) > 0

        # Parse tags
        sp_tags = _parse_json_safe(phase.sales_psychology_tags)
        if sp_tags and isinstance(sp_tags, list):
            updates["tags"] = json.dumps(sp_tags)

    if video:
        # Extract stream date from filename or created_at
        if video.created_at:
            updates["stream_date"] = video.created_at.date() if hasattr(video.created_at, 'date') else None

    # Build SET clause
    set_parts = []
    params = {"clip_id": clip_id}
    for key, val in updates.items():
        if val is not None:
            set_parts.append(f"{key} = :{key}")
            params[key] = val

    if not set_parts:
        return False

    update_sql = text(f"""
        UPDATE video_clips SET {', '.join(set_parts)}
        WHERE id = :clip_id
    """)
    await db.execute(update_sql, params)
    await db.commit()

    logger.info(f"[clip-db] Enriched clip {clip_id}: {list(updates.keys())}")
    return True


# ─── Brand-related endpoints ───

@router.get("/brands")
async def list_brands_for_clips(
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """List all active widget clients (brands) for clip assignment dropdown."""
    if not _check_admin_or_user(x_admin_key=x_admin_key):
        raise HTTPException(status_code=403, detail="Admin only")

    result = await db.execute(
        text("""
            SELECT wc.client_id, wc.name, wc.logo_url, wc.theme_color,
                   wc.company_name, wc.name_ja, wc.lcj_brand_id, wc.brand_keywords,
                   wc.source,
                   COUNT(wca.id) FILTER (WHERE wca.is_active = TRUE AND vc.id IS NOT NULL AND vc.status = 'completed' AND vc.clip_url IS NOT NULL AND COALESCE(vc.is_unusable, FALSE) = FALSE) as clip_count,
                   COUNT(wca.id) FILTER (WHERE wca.is_active = TRUE AND vc.id IS NOT NULL AND vc.status = 'completed' AND vc.clip_url IS NOT NULL AND COALESCE(vc.is_unusable, FALSE) = FALSE AND vc.is_sold = TRUE) as sold_count,
                   COALESCE(SUM(vc.gmv) FILTER (WHERE wca.is_active = TRUE AND vc.id IS NOT NULL AND vc.status = 'completed' AND vc.clip_url IS NOT NULL AND COALESCE(vc.is_unusable, FALSE) = FALSE), 0) as total_gmv,
                   COUNT(wca.id) FILTER (WHERE wca.is_active = TRUE AND vc.id IS NOT NULL AND vc.status = 'completed' AND vc.clip_url IS NOT NULL AND COALESCE(vc.is_unusable, FALSE) = FALSE AND vc.exported_url IS NOT NULL) as subtitle_count
            FROM widget_clients wc
            LEFT JOIN widget_clip_assignments wca ON wca.client_id = wc.client_id
            LEFT JOIN video_clips vc ON vc.id::text = wca.clip_id AND wca.is_active = TRUE
            WHERE wc.is_active = TRUE
            GROUP BY wc.client_id, wc.name, wc.logo_url, wc.theme_color,
                     wc.company_name, wc.name_ja, wc.lcj_brand_id, wc.brand_keywords,
                     wc.source
            ORDER BY clip_count DESC, wc.name
        """)
    )
    brands = [
        {
            "client_id": r["client_id"],
            "name": r["name"],
            "logo_url": r["logo_url"],
            "theme_color": r["theme_color"],
            "company_name": r["company_name"] or "",
            "name_ja": r["name_ja"] or "",
            "lcj_brand_id": r["lcj_brand_id"],
            "brand_keywords": r["brand_keywords"] or "",
            "source": r["source"] or "",
            "clip_count": r["clip_count"],
            "sold_count": r["sold_count"],
            "total_gmv": float(r["total_gmv"] or 0),
            "subtitle_count": r["subtitle_count"],
        }
        for r in result.mappings().all()
    ]
    return {"brands": brands}


@router.post("/assign-brand")
async def assign_clip_to_brand(
    clip_id: str = Query(..., description="Clip ID to assign"),
    client_id: str = Query(..., description="Brand client_id to assign to"),
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """Assign a clip to a brand (widget client). Creates widget_clip_assignment."""
    if not _check_admin_or_user(x_admin_key=x_admin_key):
        raise HTTPException(status_code=403, detail="Admin only")

    import uuid

    # Check clip exists
    clip_check = await db.execute(
        text("SELECT id FROM video_clips WHERE id = :cid"),
        {"cid": clip_id},
    )
    if not clip_check.first():
        raise HTTPException(status_code=404, detail="Clip not found")

    # Check brand exists
    brand_check = await db.execute(
        text("SELECT client_id FROM widget_clients WHERE client_id = :bid AND is_active = TRUE"),
        {"bid": client_id},
    )
    if not brand_check.first():
        raise HTTPException(status_code=404, detail="Brand not found")

    # Get next sort order
    max_order = await db.execute(
        text("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM widget_clip_assignments WHERE client_id = :cid"),
        {"cid": client_id},
    )
    next_order = max_order.scalar() or 0

    # Insert or reactivate
    await db.execute(
        text("""
            INSERT INTO widget_clip_assignments (id, client_id, clip_id, sort_order, is_active, created_at)
            VALUES (:id, :client_id, :clip_id, :sort_order, TRUE, NOW())
            ON CONFLICT (client_id, clip_id) DO UPDATE
            SET is_active = TRUE, sort_order = :sort_order
        """),
        {
            "id": str(uuid.uuid4()),
            "client_id": client_id,
            "clip_id": clip_id,
            "sort_order": next_order,
        },
    )
    await db.commit()

    return {"status": "assigned", "clip_id": clip_id, "client_id": client_id}


@router.delete("/unassign-brand")
async def unassign_clip_from_brand(
    clip_id: str = Query(..., description="Clip ID to unassign"),
    client_id: str = Query(..., description="Brand client_id to unassign from"),
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """Remove a clip from a brand assignment."""
    if not _check_admin_or_user(x_admin_key=x_admin_key):
        raise HTTPException(status_code=403, detail="Admin only")

    await db.execute(
        text("""
            UPDATE widget_clip_assignments
            SET is_active = FALSE
            WHERE client_id = :client_id AND clip_id = :clip_id
        """),
        {"client_id": client_id, "clip_id": clip_id},
    )
    await db.commit()

    return {"status": "unassigned", "clip_id": clip_id, "client_id": client_id}


# ─── Manual Brand Creation ───

class CreateBrandRequest(BaseModel):
    name: str = Field(..., description="Brand name (English)")
    company_name: Optional[str] = Field(None, description="Company name (Japanese)")
    name_ja: Optional[str] = Field(None, description="Brand name in Japanese")


@router.post("/brands/create")
async def create_brand_manual(
    req: CreateBrandRequest,
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """Create a new brand manually from AitherHub admin (not from LCJ Mall sync)."""
    if not _check_admin_or_user(x_admin_key=x_admin_key):
        raise HTTPException(status_code=403, detail="Admin only")

    import uuid
    import secrets as _secrets

    # Check for existing brand with similar name (prevent duplicates)
    normalized = _normalize_brand_name(req.name)
    existing_brands = await db.execute(
        text("SELECT client_id, name FROM widget_clients WHERE is_active = TRUE"),
    )
    for row in existing_brands.fetchall():
        if _normalize_brand_name(row.name) == normalized:
            return {
                "status": "exists",
                "client_id": row.client_id,
                "name": row.name,
                "message": f"Similar brand '{row.name}' already exists (normalized: {normalized})",
            }

    client_id = str(uuid.uuid4())[:8]
    brand_password = _secrets.token_urlsafe(12)
    from app.api.v1.endpoints.brand_portal import _hash_password
    password_hash = _hash_password(brand_password)

    # Build brand_keywords from name, name_ja, company_name
    kw_parts = [req.name, req.name.lower()]
    if req.name_ja:
        kw_parts.append(req.name_ja)
    if req.company_name:
        kw_parts.append(req.company_name)
    keywords = ", ".join(dict.fromkeys(kw_parts))  # deduplicate preserving order

    await db.execute(
        text("""
            INSERT INTO widget_clients
                (client_id, name, domain, theme_color, position, cta_text, is_active,
                 password_hash, brand_keywords, lcj_brand_id, logo_url, company_name, name_ja,
                 source, created_at, updated_at)
            VALUES
                (:client_id, :name, '', '#FF2D55', 'bottom-right', '購入する', TRUE,
                 :password_hash, :keywords, NULL, '', :company_name, :name_ja,
                 'aitherhub', NOW(), NOW())
        """),
        {
            "client_id": client_id,
            "name": req.name,
            "password_hash": password_hash,
            "keywords": keywords,
            "company_name": req.company_name or "",
            "name_ja": req.name_ja or "",
        },
    )
    await db.commit()

    return {
        "status": "created",
        "client_id": client_id,
        "name": req.name,
        "company_name": req.company_name or "",
        "name_ja": req.name_ja or "",
        "source": "aitherhub",
    }


# ─── Unusable Clip Marking ───

UNUSABLE_REASONS = [
    "low_quality",       # 画質が悪い
    "audio_bad",         # 音声が悪い
    "irrelevant",        # 内容が無関係
    "too_short",         # 短すぎる
    "too_long",          # 長すぎる
    "cut_position_bad",  # カット位置が悪い
    "duplicate",         # 重複
    "no_product",        # 商品が映っていない
    "blurry",            # ぼやけている
    "other",             # その他
]

class MarkUnusableRequest(BaseModel):
    reason: str = Field(..., description="Reason for marking as unusable")
    note: Optional[str] = Field(None, description="Optional free-text note")
    comment: Optional[str] = Field(None, description="Detailed free-text comment for AI learning")


@router.post("/mark-unusable")
async def mark_clip_unusable(
    clip_id: str = Query(..., description="Clip ID to mark as unusable"),
    req: MarkUnusableRequest = None,
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """
    Mark a clip as unusable with a reason.
    This is used for AI learning — unusable clips are excluded from
    training data and widget distribution.
    """
    if not _check_admin_or_user(x_admin_key=x_admin_key):
        raise HTTPException(status_code=403, detail="Admin only")

    if req.reason not in UNUSABLE_REASONS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid reason: {req.reason}. Valid: {UNUSABLE_REASONS}",
        )

    reason_text = req.reason
    if req.note:
        reason_text = f"{req.reason}: {req.note}"

    try:
        await db.execute(
            text("""
                UPDATE video_clips
                SET is_unusable = TRUE,
                    unusable_reason = :reason,
                    unusable_comment = :comment,
                    unusable_at = NOW()
                WHERE id = CAST(:clip_id AS uuid)
            """),
            {"clip_id": clip_id, "reason": reason_text, "comment": req.comment},
        )
        await db.commit()

        # Also record as 'bad' rating in clip_feedback for AI training
        try:
            feedback_id = str(__import__('uuid').uuid4())
            await db.execute(
                text("""
                    INSERT INTO clip_feedback (
                        id, video_id, phase_index, feedback, rating,
                        reason_tags, clip_id, created_at, updated_at
                    )
                    SELECT
                        :fid, vc.video_id, vc.phase_index, 'rejected', 'bad',
                        :reason_tags, :clip_id, NOW(), NOW()
                    FROM video_clips vc WHERE vc.id = CAST(:clip_id_uuid AS uuid)
                    ON CONFLICT (video_id, phase_index)
                    DO UPDATE SET
                        feedback = 'rejected',
                        rating = 'bad',
                        reason_tags = EXCLUDED.reason_tags,
                        updated_at = NOW()
                """),
                {
                    "fid": feedback_id,
                    "clip_id": clip_id,
                    "clip_id_uuid": clip_id,
                    "reason_tags": json.dumps([req.reason]),
                },
            )
            await db.commit()
        except Exception as e:
            logger.warning(f"[clip-db] Failed to record feedback for unusable clip: {e}")

        logger.info(f"[clip-db] Marked clip {clip_id} as unusable: {reason_text}")
        return {"status": "marked", "clip_id": clip_id, "reason": reason_text}

    except Exception as e:
        await db.rollback()
        logger.error(f"[clip-db] Failed to mark unusable: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to mark unusable: {str(e)}")


@router.post("/unmark-unusable")
async def unmark_clip_unusable(
    clip_id: str = Query(..., description="Clip ID to unmark"),
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """Remove the unusable mark from a clip."""
    if not _check_admin_or_user(x_admin_key=x_admin_key):
        raise HTTPException(status_code=403, detail="Admin only")

    try:
        await db.execute(
            text("""
                UPDATE video_clips
                SET is_unusable = FALSE,
                    unusable_reason = NULL,
                    unusable_comment = NULL,
                    unusable_at = NULL
                WHERE id = CAST(:clip_id AS uuid)
            """),
            {"clip_id": clip_id},
        )
        await db.commit()
        logger.info(f"[clip-db] Unmarked clip {clip_id} as usable")
        return {"status": "unmarked", "clip_id": clip_id}

    except Exception as e:
        await db.rollback()
        logger.error(f"[clip-db] Failed to unmark unusable: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to unmark: {str(e)}")


@router.get("/unusable-reasons")
async def list_unusable_reasons(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """Return the list of valid unusable reasons with Japanese labels."""
    REASON_LABELS = {
        "low_quality": "画質が悪い",
        "audio_bad": "音声が悪い",
        "irrelevant": "内容が無関係",
        "too_short": "短すぎる",
        "too_long": "長すぎる",
        "cut_position_bad": "カット位置が悪い",
        "duplicate": "重複",
        "no_product": "商品が映っていない",
        "blurry": "ぼやけている",
        "other": "その他",
    }
    return {"reasons": [{"key": k, "label": v} for k, v in REASON_LABELS.items()]}


# ─── Language Detection ───

import re as _re
import unicodedata as _ud

def _detect_language(text_str: str) -> str:
    """
    Detect language from transcript text using Unicode character analysis.
    Returns: 'ja', 'zh-TW', 'zh-CN', 'en', 'ko', 'th', or 'unknown'
    """
    if not text_str or len(text_str.strip()) < 5:
        return "unknown"

    text_str = text_str.strip()

    # Count character types
    hiragana = 0
    katakana = 0
    cjk = 0
    hangul = 0
    thai = 0
    latin = 0

    # Traditional Chinese specific characters (not used in Japanese or Simplified Chinese)
    trad_only = set("這個們對會說請問還從點裡買賣價錢東關學與對應當經過區體發現問題認為開關實際點選單項導對話視窗確認選擇設計資訊連結頁面內容標題圖片檔案資料庫")
    # Simplified Chinese specific characters
    simp_only = set("这个们对会说请问还从点里买卖价钱东关学与对应当经过区体发现问题认为开关实际点选单项导对话视窗确认选择设计资讯连结页面内容标题图片档案资料库")

    trad_count = 0
    simp_count = 0

    for ch in text_str:
        cp = ord(ch)
        name = _ud.name(ch, "")

        if 0x3040 <= cp <= 0x309F:
            hiragana += 1
        elif 0x30A0 <= cp <= 0x30FF:
            katakana += 1
        elif 0xAC00 <= cp <= 0xD7AF or 0x1100 <= cp <= 0x11FF:
            hangul += 1
        elif 0x0E00 <= cp <= 0x0E7F:
            thai += 1
        elif 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF:
            cjk += 1
            if ch in trad_only:
                trad_count += 1
            if ch in simp_only:
                simp_count += 1
        elif ch.isalpha() and cp < 0x0250:
            latin += 1

    total_meaningful = hiragana + katakana + cjk + hangul + thai + latin
    if total_meaningful < 3:
        return "unknown"

    # Japanese: has hiragana or katakana
    if hiragana + katakana > 2:
        return "ja"

    # Korean
    if hangul > 3:
        return "ko"

    # Thai
    if thai > 3:
        return "th"

    # Chinese: CJK without hiragana/katakana
    if cjk > 5:
        if trad_count > simp_count:
            return "zh-TW"
        elif simp_count > trad_count:
            return "zh-CN"
        # Ambiguous — default to zh-TW for Taiwan market
        return "zh-TW"

    # Mostly Latin
    if latin > total_meaningful * 0.5:
        return "en"

    return "unknown"


@router.post("/detect-languages")
async def detect_languages_batch(
    dry_run: bool = Query(False, description="If true, only return detection results without updating DB"),
    limit: int = Query(0, description="Max clips to process (0 = all)"),
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """
    Batch detect languages for all clips with transcript_text.
    Updates detected_language column in video_clips.
    """
    _check_admin_or_user(x_admin_key=x_admin_key)

    try:
        # Fetch clips that need language detection
        where = "vc.status = 'completed' AND vc.clip_url IS NOT NULL AND vc.transcript_text IS NOT NULL AND vc.transcript_text != ''"
        if not dry_run:
            # Only process clips without detected_language
            where += " AND (vc.detected_language IS NULL OR vc.detected_language = '')"

        limit_clause = f"LIMIT {limit}" if limit > 0 else ""

        sql = text(f"""
            SELECT vc.id, vc.transcript_text
            FROM video_clips vc
            WHERE {where}
            {limit_clause}
        """)
        result = await db.execute(sql)
        rows = result.fetchall()

        detections = []
        update_count = 0

        for row in rows:
            lang = _detect_language(row.transcript_text)
            detections.append({
                "clip_id": str(row.id),
                "detected_language": lang,
                "transcript_preview": (row.transcript_text or "")[:80],
            })

            if not dry_run and lang != "unknown":
                await db.execute(
                    text("UPDATE video_clips SET detected_language = :lang WHERE id = :cid"),
                    {"lang": lang, "cid": row.id},
                )
                update_count += 1

        if not dry_run:
            await db.commit()

        # Summary
        from collections import Counter
        lang_counts = Counter(d["detected_language"] for d in detections)

        return {
            "total_processed": len(detections),
            "updated": update_count,
            "dry_run": dry_run,
            "language_counts": dict(lang_counts),
            "samples": detections[:20],  # Return first 20 for review
        }

    except Exception as e:
        await db.rollback()
        logger.error(f"[clip-db] Language detection failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Language detection failed: {str(e)}")


# ─── Brand Merge ───

class BrandMergeRequest(BaseModel):
    target_client_id: str = Field(..., description="The client_id to merge INTO (the surviving brand)")
    source_client_ids: list[str] = Field(..., description="List of client_ids to merge FROM (will be deactivated)")
    deactivate_sources: bool = Field(True, description="Whether to deactivate source brands after merge")

@router.post("/brands/merge")
async def merge_brands(
    req: BrandMergeRequest,
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """Merge multiple brands into one. Moves all clip assignments to target brand and deactivates sources."""
    if not _check_admin_or_user(x_admin_key=x_admin_key):
        raise HTTPException(status_code=403, detail="Admin only")
    import uuid as _uuid
    try:
        target = req.target_client_id
        sources = req.source_client_ids

        # Verify target exists
        t_check = await db.execute(
            text("SELECT client_id, name FROM widget_clients WHERE client_id = :cid"),
            {"cid": target},
        )
        t_row = t_check.first()
        if not t_row:
            raise HTTPException(status_code=404, detail=f"Target brand {target} not found")

        moved_total = 0
        skipped_total = 0
        deactivated = []

        for src in sources:
            if src == target:
                continue

            # Get all active clip assignments from source
            src_clips = await db.execute(
                text("""
                    SELECT clip_id FROM widget_clip_assignments
                    WHERE client_id = :src AND is_active = TRUE
                """),
                {"src": src},
            )
            clip_ids = [str(r[0]) for r in src_clips.fetchall()]

            moved = 0
            skipped = 0
            for cid in clip_ids:
                # Check if already assigned to target
                existing = await db.execute(
                    text("""
                        SELECT id, is_active FROM widget_clip_assignments
                        WHERE client_id = :target AND clip_id = :cid
                    """),
                    {"target": target, "cid": cid},
                )
                ex_row = existing.first()
                if ex_row and ex_row.is_active:
                    skipped += 1
                elif ex_row:
                    # Reactivate existing assignment
                    await db.execute(
                        text("""
                            UPDATE widget_clip_assignments
                            SET is_active = TRUE
                            WHERE client_id = :target AND clip_id = :cid
                        """),
                        {"target": target, "cid": cid},
                    )
                    moved += 1
                else:
                    # Get next sort order
                    max_order = await db.execute(
                        text("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM widget_clip_assignments WHERE client_id = :cid"),
                        {"cid": target},
                    )
                    next_order = max_order.scalar() or 0
                    # Create new assignment
                    await db.execute(
                        text("""
                            INSERT INTO widget_clip_assignments (id, client_id, clip_id, sort_order, is_active, created_at)
                            VALUES (:id, :client_id, :clip_id, :sort_order, TRUE, NOW())
                        """),
                        {
                            "id": str(_uuid.uuid4()),
                            "client_id": target,
                            "clip_id": cid,
                            "sort_order": next_order,
                        },
                    )
                    moved += 1

                # Deactivate source assignment
                await db.execute(
                    text("""
                        UPDATE widget_clip_assignments
                        SET is_active = FALSE
                        WHERE client_id = :src AND clip_id = :cid
                    """),
                    {"src": src, "cid": cid},
                )

            moved_total += moved
            skipped_total += skipped

            # Deactivate source brand
            if req.deactivate_sources:
                await db.execute(
                    text("UPDATE widget_clients SET is_active = FALSE WHERE client_id = :cid"),
                    {"cid": src},
                )
                deactivated.append(src)

        await db.commit()
        return {
            "status": "merged",
            "target": target,
            "target_name": t_row.name,
            "clips_moved": moved_total,
            "clips_skipped_already_assigned": skipped_total,
            "sources_deactivated": deactivated,
        }
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"[clip-db] Brand merge failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Brand merge failed: {str(e)}")


# ─── Brand Protection: Backup & Restore ───

BRANDS_BACKUP_PATH = Path(__file__).parent.parent.parent.parent / "data" / "brands_backup.json"


@router.post("/brands/backup")
async def backup_brands(
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """Save current brand data to backup JSON file. Admin only."""
    if not _check_admin_or_user(x_admin_key=x_admin_key):
        raise HTTPException(status_code=403, detail="Admin only")

    result = await db.execute(
        text("""
            SELECT client_id, name, logo_url, theme_color, company_name,
                   name_ja, lcj_brand_id, brand_keywords, source, is_active
            FROM widget_clients
            WHERE is_active = TRUE
            ORDER BY name
        """)
    )
    brands = [dict(r) for r in result.mappings().all()]

    BRANDS_BACKUP_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BRANDS_BACKUP_PATH, "w", encoding="utf-8") as f:
        json.dump(brands, f, indent=2, ensure_ascii=False)

    logger.info(f"[clip-db] Brand backup saved: {len(brands)} brands to {BRANDS_BACKUP_PATH}")
    return {"status": "backed_up", "count": len(brands), "path": str(BRANDS_BACKUP_PATH)}


@router.post("/brands/restore")
async def restore_brands(
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """Restore brands from backup JSON file. Only restores missing/deactivated brands. Admin only."""
    if not _check_admin_or_user(x_admin_key=x_admin_key):
        raise HTTPException(status_code=403, detail="Admin only")

    if not BRANDS_BACKUP_PATH.exists():
        raise HTTPException(status_code=404, detail="No backup file found")

    with open(BRANDS_BACKUP_PATH, "r", encoding="utf-8") as f:
        backup_brands = json.load(f)

    restored = 0
    skipped = 0
    for brand in backup_brands:
        # Check if brand exists
        existing = await db.execute(
            text("SELECT client_id, is_active FROM widget_clients WHERE client_id = :cid"),
            {"cid": brand["client_id"]},
        )
        row = existing.first()
        if row and row.is_active:
            skipped += 1
            continue
        elif row and not row.is_active:
            # Reactivate
            await db.execute(
                text("UPDATE widget_clients SET is_active = TRUE WHERE client_id = :cid"),
                {"cid": brand["client_id"]},
            )
            restored += 1
        else:
            # Insert new
            await db.execute(
                text("""
                    INSERT INTO widget_clients (client_id, name, logo_url, theme_color,
                        company_name, name_ja, lcj_brand_id, brand_keywords, source, is_active)
                    VALUES (:client_id, :name, :logo_url, :theme_color,
                        :company_name, :name_ja, :lcj_brand_id, :brand_keywords, :source, TRUE)
                """),
                {
                    "client_id": brand["client_id"],
                    "name": brand["name"],
                    "logo_url": brand.get("logo_url", ""),
                    "theme_color": brand.get("theme_color", "#FF2D55"),
                    "company_name": brand.get("company_name", ""),
                    "name_ja": brand.get("name_ja", ""),
                    "lcj_brand_id": brand.get("lcj_brand_id"),
                    "brand_keywords": brand.get("brand_keywords", ""),
                    "source": brand.get("source", ""),
                },
            )
            restored += 1

    await db.commit()
    logger.info(f"[clip-db] Brand restore: {restored} restored, {skipped} already active")
    return {"status": "restored", "restored": restored, "skipped": skipped, "total_in_backup": len(backup_brands)}


# ─── Brand Protection: Bulk Delete Guard ───

@router.delete("/brands/delete")
async def delete_brand(
    client_id: str = Query(..., description="Brand client_id to deactivate"),
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """Soft-delete a brand (set is_active=FALSE). NEVER hard-deletes. Admin only.
    Also automatically backs up before deactivation."""
    if not _check_admin_or_user(x_admin_key=x_admin_key):
        raise HTTPException(status_code=403, detail="Admin only")

    # Safety: check clip count before deactivation
    clip_check = await db.execute(
        text("""
            SELECT COUNT(*) as cnt FROM widget_clip_assignments
            WHERE client_id = :cid AND is_active = TRUE
        """),
        {"cid": client_id},
    )
    clip_count = clip_check.scalar() or 0

    if clip_count > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot deactivate brand with {clip_count} active clip assignments. "
                   f"Unassign all clips first or use /brands/merge to move them."
        )

    # Soft delete only
    await db.execute(
        text("UPDATE widget_clients SET is_active = FALSE WHERE client_id = :cid"),
        {"cid": client_id},
    )
    await db.commit()

    logger.info(f"[clip-db] Brand soft-deleted: {client_id}")
    return {"status": "deactivated", "client_id": client_id}


@router.post("/brands/bulk-delete-guard")
async def bulk_delete_guard(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """This endpoint exists to BLOCK any bulk delete attempts.
    Bulk deletion of brands is NEVER allowed."""
    raise HTTPException(
        status_code=403,
        detail="BLOCKED: Bulk deletion of brands is permanently disabled. "
               "Use /brands/merge or individual /brands/delete (soft-delete) instead."
    )


# ─── Daily Review Stats (採点日ごとの集計) ───

@router.get("/review-stats")
async def get_review_stats(
    days: int = Query(30, description="Number of days to look back"),
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """
    Get daily review/scoring statistics.
    Returns counts of clips reviewed (brand-assigned + NG-marked) per day.
    """
    if not _check_admin_or_user(x_admin_key=x_admin_key):
        raise HTTPException(status_code=403, detail="Admin only")

    # Sanitize days to prevent SQL injection (must be positive int)
    days = max(1, min(int(days), 365))

    try:
        # Brand assignments per day (using widget_clip_assignments.created_at)
        brand_sql = text(f"""
            SELECT DATE(wca.created_at) as dt, COUNT(*) as cnt
            FROM widget_clip_assignments wca
            WHERE wca.is_active = TRUE
                AND wca.created_at >= NOW() - INTERVAL '{days} days'
            GROUP BY DATE(wca.created_at)
            ORDER BY dt ASC
        """)
        brand_result = await db.execute(brand_sql)
        brand_by_date = {str(r.dt): r.cnt for r in brand_result.fetchall()}

        # NG marks per day (using video_clips.unusable_at)
        ng_sql = text(f"""
            SELECT DATE(vc.unusable_at) as dt, COUNT(*) as cnt
            FROM video_clips vc
            WHERE vc.status = 'completed' AND vc.clip_url IS NOT NULL
                AND COALESCE(vc.is_unusable, FALSE) = TRUE
                AND vc.unusable_at IS NOT NULL
                AND vc.unusable_at >= NOW() - INTERVAL '{days} days'
            GROUP BY DATE(vc.unusable_at)
            ORDER BY dt ASC
        """)
        ng_result = await db.execute(ng_sql)
        ng_by_date = {str(r.dt): r.cnt for r in ng_result.fetchall()}

        # Merge into daily stats
        all_dates = sorted(set(list(brand_by_date.keys()) + list(ng_by_date.keys())))
        daily_stats = []
        for dt in all_dates:
            brand_count = brand_by_date.get(dt, 0)
            ng_count = ng_by_date.get(dt, 0)
            daily_stats.append({
                "date": dt,
                "brand_assigned": brand_count,
                "ng_marked": ng_count,
                "total_reviewed": brand_count + ng_count,
            })

        # Summary totals
        total_brand = sum(brand_by_date.values())
        total_ng = sum(ng_by_date.values())

        return {
            "daily": daily_stats,
            "total_brand_assigned": total_brand,
            "total_ng_marked": total_ng,
            "total_reviewed": total_brand + total_ng,
            "days": days,
        }

    except Exception as e:
        logger.error(f"[clip-db] Review stats failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Review stats failed: {str(e)}")
# PLAYLIST FEATURE - Create/manage playlists and assign clips to them
# ═══════════════════════════════════════════════════════════════════════════════

class PlaylistCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    color: str = Field(default="#6366f1")
    icon: str = Field(default="tag")
    description: Optional[str] = None

class PlaylistUpdate(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    icon: Optional[str] = None
    description: Optional[str] = None

class PlaylistResponse(BaseModel):
    id: str
    name: str
    color: str
    icon: str
    description: Optional[str] = None
    clip_count: int = 0
    created_at: Optional[str] = None


@router.get("/playlists")
async def list_playlists(
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """List all playlists with clip counts."""
    if not _check_admin_or_user(x_admin_key=x_admin_key):
        raise HTTPException(status_code=403, detail="Admin only")

    try:
        result = await db.execute(text("""
            SELECT p.id, p.name, p.color, p.icon, p.description, p.created_at,
                   COALESCE(cnt.clip_count, 0) as clip_count
            FROM clip_playlists p
            LEFT JOIN (
                SELECT playlist_id, COUNT(*) as clip_count
                FROM clip_playlist_items
                GROUP BY playlist_id
            ) cnt ON cnt.playlist_id = p.id
            ORDER BY p.name ASC
        """))
        rows = result.fetchall()
        playlists = []
        for row in rows:
            playlists.append(PlaylistResponse(
                id=str(row.id),
                name=row.name,
                color=row.color or "#6366f1",
                icon=row.icon or "tag",
                description=row.description,
                clip_count=row.clip_count,
                created_at=row.created_at.isoformat() if row.created_at else None,
            ))
        return {"playlists": playlists}
    except Exception as e:
        logger.error(f"[clip-db] List playlists failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/playlists")
async def create_playlist(
    body: PlaylistCreate,
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """Create a new playlist."""
    if not _check_admin_or_user(x_admin_key=x_admin_key):
        raise HTTPException(status_code=403, detail="Admin only")

    try:
        result = await db.execute(
            text("""
                INSERT INTO clip_playlists (name, color, icon, description)
                VALUES (:name, :color, :icon, :description)
                RETURNING id, name, color, icon, description, created_at
            """),
            {"name": body.name, "color": body.color, "icon": body.icon, "description": body.description}
        )
        row = result.fetchone()
        await db.commit()
        return PlaylistResponse(
            id=str(row.id),
            name=row.name,
            color=row.color,
            icon=row.icon,
            description=row.description,
            clip_count=0,
            created_at=row.created_at.isoformat() if row.created_at else None,
        )
    except Exception as e:
        await db.rollback()
        logger.error(f"[clip-db] Create playlist failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/playlists/{playlist_id}")
async def update_playlist(
    playlist_id: str,
    body: PlaylistUpdate,
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """Update a playlist's name, color, icon, or description."""
    if not _check_admin_or_user(x_admin_key=x_admin_key):
        raise HTTPException(status_code=403, detail="Admin only")

    updates = []
    params = {"pid": playlist_id}
    if body.name is not None:
        updates.append("name = :name")
        params["name"] = body.name
    if body.color is not None:
        updates.append("color = :color")
        params["color"] = body.color
    if body.icon is not None:
        updates.append("icon = :icon")
        params["icon"] = body.icon
    if body.description is not None:
        updates.append("description = :description")
        params["description"] = body.description

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates.append("updated_at = NOW()")

    try:
        result = await db.execute(
            text(f"UPDATE clip_playlists SET {', '.join(updates)} WHERE id = CAST(:pid AS uuid) RETURNING id"),
            params
        )
        if not result.fetchone():
            raise HTTPException(status_code=404, detail="Playlist not found")
        await db.commit()
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"[clip-db] Update playlist failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/playlists/{playlist_id}")
async def delete_playlist(
    playlist_id: str,
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """Delete a playlist (cascade removes all clip assignments)."""
    if not _check_admin_or_user(x_admin_key=x_admin_key):
        raise HTTPException(status_code=403, detail="Admin only")

    try:
        result = await db.execute(
            text("DELETE FROM clip_playlists WHERE id = CAST(:pid AS uuid) RETURNING id"),
            {"pid": playlist_id}
        )
        if not result.fetchone():
            raise HTTPException(status_code=404, detail="Playlist not found")
        await db.commit()
        return {"status": "ok", "deleted": playlist_id}
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"[clip-db] Delete playlist failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/playlists/{playlist_id}/clips")
async def add_clip_to_playlist(
    playlist_id: str,
    clip_id: str = Query(..., description="Clip UUID to add"),
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """Add a clip to a playlist."""
    if not _check_admin_or_user(x_admin_key=x_admin_key):
        raise HTTPException(status_code=403, detail="Admin only")

    try:
        await db.execute(
            text("""
                INSERT INTO clip_playlist_items (clip_id, playlist_id)
                VALUES (CAST(:clip_id AS uuid), CAST(:playlist_id AS uuid))
                ON CONFLICT (clip_id, playlist_id) DO NOTHING
            """),
            {"clip_id": clip_id, "playlist_id": playlist_id}
        )
        await db.commit()
        return {"status": "ok", "clip_id": clip_id, "playlist_id": playlist_id}
    except Exception as e:
        await db.rollback()
        logger.error(f"[clip-db] Add clip to playlist failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/playlists/{playlist_id}/clips")
async def remove_clip_from_playlist(
    playlist_id: str,
    clip_id: str = Query(..., description="Clip UUID to remove"),
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """Remove a clip from a playlist."""
    if not _check_admin_or_user(x_admin_key=x_admin_key):
        raise HTTPException(status_code=403, detail="Admin only")

    try:
        await db.execute(
            text("""
                DELETE FROM clip_playlist_items
                WHERE clip_id = CAST(:clip_id AS uuid)
                  AND playlist_id = CAST(:playlist_id AS uuid)
            """),
            {"clip_id": clip_id, "playlist_id": playlist_id}
        )
        await db.commit()
        return {"status": "ok", "clip_id": clip_id, "playlist_id": playlist_id}
    except Exception as e:
        await db.rollback()
        logger.error(f"[clip-db] Remove clip from playlist failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/playlists/clip/{clip_id}")
async def get_clip_playlists(
    clip_id: str,
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """Get all playlists that a specific clip belongs to."""
    if not _check_admin_or_user(x_admin_key=x_admin_key):
        raise HTTPException(status_code=403, detail="Admin only")

    try:
        result = await db.execute(
            text("""
                SELECT p.id, p.name, p.color, p.icon, p.description, cpi.added_at
                FROM clip_playlist_items cpi
                JOIN clip_playlists p ON p.id = cpi.playlist_id
                WHERE cpi.clip_id = CAST(:clip_id AS uuid)
                ORDER BY p.name ASC
            """),
            {"clip_id": clip_id}
        )
        rows = result.fetchall()
        playlists = []
        for row in rows:
            playlists.append({
                "id": str(row.id),
                "name": row.name,
                "color": row.color or "#6366f1",
                "icon": row.icon or "tag",
                "description": row.description,
                "added_at": row.added_at.isoformat() if row.added_at else None,
            })
        return {"playlists": playlists}
    except Exception as e:
        logger.error(f"[clip-db] Get clip playlists failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))



# ═══════════════════════════════════════════════════════════════════════════════
# 再生成一覧 & 採点 API
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/regenerations")
async def list_regenerations(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    grade: Optional[str] = Query(None, description="Filter by grade: ok, ng, retry, ungraded"),
    sort_by: str = Query("created_at", description="Sort field"),
    sort_order: str = Query("desc", description="asc or desc"),
    x_admin_key: str = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """再生成済みクリップ一覧（Before/After比較付き）"""
    if x_admin_key != f"{ADMIN_ID}:{ADMIN_PASS}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        offset = (page - 1) * per_page
        order = "DESC" if sort_order == "desc" else "ASC"

        # Grade filter condition
        grade_condition = ""
        if grade == "ok":
            grade_condition = "AND j.config->>'regen_grade' = 'ok'"
        elif grade == "ng":
            grade_condition = "AND j.config->>'regen_grade' = 'ng'"
        elif grade == "retry":
            grade_condition = "AND j.config->>'regen_grade' = 'retry'"
        elif grade == "ungraded":
            grade_condition = "AND (j.config->>'regen_grade' IS NULL OR j.config->>'regen_grade' = '')"

        # Count total
        count_sql = text(f"""
            SELECT COUNT(*) as total
            FROM ai_clip_jobs j
            WHERE j.status = 'done'
              AND j.config->>'type' = 'regenerate_from_source'
              {grade_condition}
        """)
        count_result = await db.execute(count_sql)
        total = count_result.scalar() or 0

        # Fetch jobs (no JOIN - safer, handle clip lookup in Python)
        sql = text(f"""
            SELECT 
                j.job_id,
                j.status,
                j.results,
                j.config,
                j.source_clip,
                j.created_at as job_created_at
            FROM ai_clip_jobs j
            WHERE j.status = 'done'
              AND j.config->>'type' = 'regenerate_from_source'
              {grade_condition}
            ORDER BY j.created_at {order}
            LIMIT :limit OFFSET :offset
        """)
        result = await db.execute(sql, {"limit": per_page, "offset": offset})
        rows = result.fetchall()

        # Collect all source clip IDs to batch-fetch their clip_urls
        parsed_rows = []
        source_clip_ids = set()
        for row in rows:
            job_results = row.results if isinstance(row.results, list) else json.loads(row.results or "[]")
            job_config = row.config if isinstance(row.config, dict) else json.loads(row.config or "{}")
            source_clip = row.source_clip if isinstance(row.source_clip, dict) else json.loads(row.source_clip or "{}") if row.source_clip else {}
            clip_id = job_config.get("source_clip_id")
            if clip_id:
                source_clip_ids.add(clip_id)
            parsed_rows.append((row, job_results, job_config, source_clip, clip_id))

        # Batch lookup clip_url from video_clips for Before videos
        clip_url_map = {}  # clip_id -> signed url
        if source_clip_ids:
            try:
                from app.services.storage_service import generate_read_sas_from_url
                ids_list = list(source_clip_ids)
                # Build IN clause with positional params for compatibility
                placeholders = ", ".join([f"'{cid}'" for cid in ids_list])
                clip_lookup_sql = text(f"""
                    SELECT id::text as clip_id, clip_url, sas_token, sas_expireddate, thumbnail_url, duration_sec
                    FROM video_clips
                    WHERE id::text IN ({placeholders})
                """)
                clip_result = await db.execute(clip_lookup_sql)
                for cr in clip_result.fetchall():
                    url = None
                    if cr.clip_url:
                        # Try cached SAS first
                        if cr.sas_token and cr.sas_expireddate:
                            now = datetime.now(timezone.utc)
                            expiry = cr.sas_expireddate
                            if expiry.tzinfo is None:
                                expiry = expiry.replace(tzinfo=timezone.utc)
                            if expiry > now:
                                url = cr.sas_token
                        if not url:
                            try:
                                sas_url = generate_read_sas_from_url(cr.clip_url)
                                url = _replace_blob_url_to_cdn(sas_url) if sas_url else _replace_blob_url_to_cdn(cr.clip_url)
                            except Exception:
                                url = _replace_blob_url_to_cdn(cr.clip_url)
                    clip_url_map[cr.clip_id] = {
                        "clip_url": url,
                        "thumbnail_url": cr.thumbnail_url,
                        "duration_sec": cr.duration_sec,
                    }
            except Exception as e:
                logger.warning(f"[clip-db] Batch clip URL lookup failed: {e}")

        items = []
        for (row, job_results, job_config, source_clip, clip_id) in parsed_rows:
            regen_result = job_results[0] if job_results else {}

            # Get before clip_url from batch lookup (fallback to source_clip snapshot)
            before_lookup = clip_url_map.get(clip_id, {}) if clip_id else {}
            before_clip_url = before_lookup.get("clip_url") or source_clip.get("clip_url")
            before_thumbnail = before_lookup.get("thumbnail_url") or source_clip.get("thumbnail_url")
            before_duration = source_clip.get("duration_sec") or before_lookup.get("duration_sec")

            items.append({
                "job_id": row.job_id,
                "clip_id": clip_id,
                "created_at": row.job_created_at.isoformat() if row.job_created_at else None,
                "grade": job_config.get("regen_grade"),
                "grade_comment": job_config.get("regen_grade_comment"),
                # Before (original) - from video_clips lookup + source_clip snapshot
                "before": {
                    "clip_url": before_clip_url,
                    "thumbnail_url": before_thumbnail,
                    "quality_score": regen_result.get("original_quality_score") or source_clip.get("quality_score"),
                    "duration_sec": before_duration,
                    "product_name": source_clip.get("product_name"),
                    "liver_name": source_clip.get("liver_name"),
                    "transcript_text": (source_clip.get("transcript_text") or "")[:200],
                },
                # After (regenerated)
                "after": {
                    "download_url": regen_result.get("download_url"),
                    "blob_url": regen_result.get("blob_url"),
                    "quality_score": regen_result.get("new_quality_score"),
                    "duration_sec": regen_result.get("duration_sec"),
                    "hook_text": regen_result.get("hook_text"),
                    "cta_text": regen_result.get("cta_text"),
                    "captions_count": regen_result.get("captions_count"),
                    "transcript_text": (regen_result.get("transcript_text") or "")[:200],
                    "effects_applied": regen_result.get("effects_applied"),
                    "content_evaluation": regen_result.get("content_evaluation") or job_config.get("content_evaluation"),
                    "sellability": (regen_result.get("content_evaluation") or job_config.get("content_evaluation") or {}).get("sellability"),
                },
                # Metadata
                "current_version": source_clip.get("ml_model_version"),
                "tags": source_clip.get("tags") if isinstance(source_clip.get("tags"), list) else [],
            })

        # Stats (simple, no JOIN)
        stats_sql = text("""
            SELECT 
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE j.config->>'regen_grade' = 'ok') as ok_count,
                COUNT(*) FILTER (WHERE j.config->>'regen_grade' = 'ng') as ng_count,
                COUNT(*) FILTER (WHERE j.config->>'regen_grade' = 'retry') as retry_count,
                COUNT(*) FILTER (WHERE j.config->>'regen_grade' IS NULL OR j.config->>'regen_grade' = '') as ungraded_count
            FROM ai_clip_jobs j
            WHERE j.status = 'done'
              AND j.config->>'type' = 'regenerate_from_source'
        """)
        stats_result = await db.execute(stats_sql)
        stats_row = stats_result.fetchone()

        # Calculate avg score improvement from items
        improvements = []
        for item in items:
            before_score = item["before"].get("quality_score")
            after_score = item["after"].get("quality_score")
            if before_score is not None and after_score is not None:
                try:
                    improvements.append(float(after_score) - float(before_score))
                except (ValueError, TypeError):
                    pass
        avg_improvement = round(sum(improvements) / len(improvements), 1) if improvements else 0

        return {
            "items": items,
            "total": total,
            "page": page,
            "per_page": per_page,
            "stats": {
                "total": stats_row.total if stats_row else 0,
                "ok": stats_row.ok_count if stats_row else 0,
                "ng": stats_row.ng_count if stats_row else 0,
                "retry": stats_row.retry_count if stats_row else 0,
                "ungraded": stats_row.ungraded_count if stats_row else 0,
                "avg_score_improvement": avg_improvement,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[clip-db] List regenerations failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/regenerations/{job_id}/grade")
async def grade_regeneration(
    job_id: str,
    x_admin_key: str = Header(None),
    db: AsyncSession = Depends(get_db),
    grade: str = Query(..., description="Grade: ok, ng, retry"),
    comment: Optional[str] = Query(None, description="Optional comment"),
):
    """再生成クリップを採点する"""
    if x_admin_key != f"{ADMIN_ID}:{ADMIN_PASS}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    if grade not in ("ok", "ng", "retry"):
        raise HTTPException(status_code=400, detail="Grade must be: ok, ng, retry")

    # Update the job's config with the grade
    try:
        result = await db.execute(text("""
            UPDATE ai_clip_jobs
            SET config = jsonb_set(
                jsonb_set(
                    config,
                    '{regen_grade}',
                    :grade_val
                ),
                '{regen_grade_comment}',
                :comment_val
            ),
            updated_at = NOW()
            WHERE job_id = :job_id
            RETURNING job_id
        """), {
            "job_id": job_id,
            "grade_val": json.dumps(grade),
            "comment_val": json.dumps(comment or ""),
        })
        await db.commit()
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Job not found")
        return {"status": "ok", "job_id": job_id, "grade": grade}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[clip-db] Grade regen failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─── Skip Review Endpoints ───

@router.post("/unskip")
async def unskip_clip(
    clip_id: str = Query(..., description="Clip ID to unskip (restore from skipped)"),
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """
    Restore a skipped clip (set regen_skipped=FALSE).
    Human review determined the clip is actually usable.
    """
    _check_admin_or_user(x_admin_key=x_admin_key)
    try:
        result = await db.execute(text("""
            UPDATE video_clips
            SET regen_skipped = FALSE,
                skip_reason = NULL,
                skip_evaluated_at = NULL,
                updated_at = NOW()
            WHERE id = CAST(:clip_id AS uuid)
            RETURNING id
        """), {"clip_id": clip_id})
        await db.commit()
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Clip not found")
        logger.info(f"[clip-db] Unskipped clip {clip_id} (human review: usable)")
        return {"status": "ok", "clip_id": clip_id, "action": "unskipped"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[clip-db] Unskip failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/confirm-skip")
async def confirm_skip_clip(
    clip_id: str = Query(..., description="Clip ID to confirm as skipped"),
    reason: Optional[str] = Query(None, description="Optional updated reason"),
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """
    Confirm a skipped clip as truly unusable (mark as NG).
    Human review confirmed the AI's skip decision was correct.
    """
    _check_admin_or_user(x_admin_key=x_admin_key)
    try:
        # Mark as both skipped AND unusable (confirmed by human)
        update_parts = [
            "is_unusable = TRUE",
            "unusable_reason = COALESCE(:reason, skip_reason, 'GPT判定確認済み')",
            "updated_at = NOW()",
        ]
        result = await db.execute(text(f"""
            UPDATE video_clips
            SET {', '.join(update_parts)}
            WHERE id = CAST(:clip_id AS uuid)
            RETURNING id
        """), {"clip_id": clip_id, "reason": reason})
        await db.commit()
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Clip not found")
        logger.info(f"[clip-db] Confirmed skip for clip {clip_id} → marked as NG (human review)")
        return {"status": "ok", "clip_id": clip_id, "action": "confirmed_skip"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[clip-db] Confirm skip failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/batch-unskip")
async def batch_unskip_clips(
    clip_ids: list = [],
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """
    Batch restore multiple skipped clips.
    """
    _check_admin_or_user(x_admin_key=x_admin_key)
    if not clip_ids:
        raise HTTPException(status_code=400, detail="clip_ids is required")
    try:
        restored = 0
        for cid in clip_ids:
            result = await db.execute(text("""
                UPDATE video_clips
                SET regen_skipped = FALSE,
                    skip_reason = NULL,
                    skip_evaluated_at = NULL,
                    updated_at = NOW()
                WHERE id = CAST(:clip_id AS uuid) AND COALESCE(regen_skipped, FALSE) = TRUE
                RETURNING id
            """), {"clip_id": cid})
            if result.fetchone():
                restored += 1
        await db.commit()
        logger.info(f"[clip-db] Batch unskipped {restored}/{len(clip_ids)} clips")
        return {"status": "ok", "restored": restored, "total": len(clip_ids)}
    except Exception as e:
        logger.error(f"[clip-db] Batch unskip failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
