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
from datetime import datetime, timezone
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
    brand: Optional[str] = Query(None, description="Filter by brand client_id"),
    # Sorting
    sort_by: str = Query("created_at", description="Sort field: created_at, gmv, cta_score, importance_score, duration_sec"),
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

    # Brand filter (via widget_clip_assignments)
    if brand:
        conditions.append("""
            vc.id::text IN (
                SELECT wca.clip_id FROM widget_clip_assignments wca
                WHERE wca.client_id = :brand_filter AND wca.is_active = TRUE
            )
        """)
        params["brand_filter"] = brand

    where_clause = " AND ".join(conditions)

    # Validate sort
    allowed_sorts = {
        "created_at": "vc.created_at",
        "gmv": "COALESCE(vc.gmv, 0)",
        "cta_score": "COALESCE(vc.cta_score, 0)",
        "importance_score": "COALESCE(vc.importance_score, 0)",
        "duration_sec": "COALESCE(vc.duration_sec, 0)",
    }
    sort_col = allowed_sorts.get(sort_by, "vc.created_at")
    sort_dir = "DESC" if sort_order.lower() == "desc" else "ASC"

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
            vp.sales_psychology_tags,
            vp.human_sales_tags,
            vp.product_names as vp_product_names,
            COALESCE(vp.gmv, 0) as vp_gmv,
            COALESCE(vp.viewer_count, 0) as vp_viewer_count,
            cf.rating,
            v.original_filename as video_filename
        FROM video_clips vc
        LEFT JOIN video_phases vp ON vp.video_id = vc.video_id
            AND vp.phase_index = CASE
                WHEN vc.phase_index ~ '^[0-9]+$' THEN CAST(vc.phase_index AS INTEGER)
                ELSE -1
            END
        LEFT JOIN clip_feedback cf ON cf.video_id = vc.video_id
            AND cf.phase_index = vc.phase_index
        LEFT JOIN videos v ON v.id = vc.video_id
        WHERE {where_clause}
        ORDER BY vc.id, {sort_col} {sort_dir}
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
            SELECT wc.client_id, wc.name,
                   COUNT(wca.id) FILTER (WHERE wca.is_active = TRUE) as clip_count
            FROM widget_clients wc
            LEFT JOIN widget_clip_assignments wca ON wca.client_id = wc.client_id
            WHERE wc.is_active = TRUE
            GROUP BY wc.client_id, wc.name
            ORDER BY wc.name
        """)
    )
    brands = [
        {"client_id": r["client_id"], "name": r["name"], "clip_count": r["clip_count"]}
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
