"""
Video Performance API - TikTok Screenshot OCR → Auto-match → DB Registration

Endpoints:
  POST /video-performance/ocr-screenshot   - Upload screenshot, OCR extract metrics, auto-match video
  POST /video-performance/confirm           - Confirm match and save to DB
  GET  /video-performance/list              - List all performance records
  GET  /video-performance/video/{video_id}  - Get performance data for a specific video
  POST /video-performance/manual            - Manually input performance data
"""

import os
import re
import json
import base64
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query, Header
from pydantic import BaseModel
from sqlalchemy import text

from app.core.db import AsyncSessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/video-performance", tags=["Video Performance"])


# ─── Pydantic Models ───────────────────────────────────────────────────────────

class OCRResult(BaseModel):
    """Extracted data from TikTok screenshot"""
    views: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    saves: Optional[int] = None
    purchases: Optional[int] = None
    revenue: Optional[float] = None
    caption: Optional[str] = None
    hashtags: Optional[list[str]] = None
    posted_date: Optional[str] = None
    account_name: Optional[str] = None
    product_name: Optional[str] = None
    avg_watch_time: Optional[float] = None
    retention_curve: Optional[list[dict]] = None


class VideoCandidate(BaseModel):
    """A candidate video that might match the screenshot"""
    video_id: str
    original_filename: str
    created_at: Optional[str] = None
    phase_descriptions: Optional[list[str]] = None
    match_score: float = 0.0
    match_reason: str = ""


class OCRScreenshotResponse(BaseModel):
    """Response from OCR screenshot endpoint"""
    ocr_data: OCRResult
    candidates: list[VideoCandidate]
    best_match: Optional[VideoCandidate] = None
    raw_ocr: Optional[dict] = None


class ConfirmMatchRequest(BaseModel):
    """Request to confirm a match and save performance data"""
    video_id: str
    ocr_data: OCRResult
    platform: str = "tiktok"
    tiktok_video_id: Optional[str] = None
    recorded_at: Optional[str] = None


class PerformanceRecord(BaseModel):
    id: str
    video_id: str
    platform: str
    views: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    saves: Optional[int] = None
    purchases: Optional[int] = None
    revenue: Optional[float] = None
    engagement_rate: Optional[float] = None
    conversion_rate: Optional[float] = None
    caption: Optional[str] = None
    hashtags: Optional[list] = None
    recorded_at: Optional[str] = None
    created_at: Optional[str] = None


class ManualInputRequest(BaseModel):
    """Manual performance data input"""
    video_id: str
    platform: str = "tiktok"
    views: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None
    saves: Optional[int] = None
    purchases: Optional[int] = None
    revenue: Optional[float] = None
    avg_watch_time_seconds: Optional[float] = None
    tiktok_video_id: Optional[str] = None


# ─── Helper: DB Session ────────────────────────────────────────────────────────

from contextlib import asynccontextmanager

@asynccontextmanager
async def get_session():
    async with AsyncSessionLocal() as session:
        yield session
        await session.commit()


# ─── Helper: Admin Auth ────────────────────────────────────────────────────────

def verify_admin(x_admin_key: Optional[str] = Header(None)):
    expected = os.getenv("ADMIN_API_KEY", "aither:hub")
    if x_admin_key != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ─── Helper: OCR with GPT-4o Vision ───────────────────────────────────────────

async def extract_metrics_from_screenshot(image_bytes: bytes, content_type: str = "image/jpeg") -> dict:
    """
    Use GPT-4o Vision to extract TikTok performance metrics from a screenshot.
    Returns structured data with views, likes, comments, shares, saves, purchases, caption, etc.
    """
    import openai

    b64_image = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:{content_type};base64,{b64_image}"

    client = openai.AsyncOpenAI()

    system_prompt = """You are a TikTok screenshot data extraction assistant.
Analyze the TikTok video screenshot and extract ALL visible metrics and information.

Return ONLY a valid JSON object with these fields:
- "views": total view count (integer, parse "1.7K" as 1700, "1.2M" as 1200000)
- "likes": like/heart count (integer)
- "comments": comment count (integer)
- "shares": share count (integer)
- "saves": bookmark/save count (integer)
- "purchases": purchase count if visible (integer, from shopping cart icon)
- "revenue": revenue amount if visible (float, in local currency)
- "caption": the video caption/description text
- "hashtags": array of hashtags (e.g., ["#kyogoku", "#TikTokShop"])
- "posted_date": posting date if visible (e.g., "5-3" or "2026-05-03")
- "account_name": the account/creator name
- "product_name": product name if a shopping link is visible
- "avg_watch_time": average watch time in seconds if visible from insights
- "retention_curve": array of {second, retention_pct} if retention graph is visible

Rules:
- Parse abbreviated numbers: 1.7K=1700, 12.5K=12500, 1.2M=1200000
- If a metric is not visible, use null
- For Japanese/Chinese numbers, convert to integers
- Look for the shopping cart icon (🛒) for purchase data
- The bottom bar typically shows: views, shares, "プロモートする", "他のインサイト"
- Do NOT wrap the JSON in markdown code blocks."""

    response = await client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "このTikTokスクリーンショットからすべてのパフォーマンスデータを抽出してください。"},
                    {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                ],
            },
        ],
        max_tokens=1500,
        temperature=0.1,
    )

    raw_text = response.choices[0].message.content.strip()

    # Parse JSON from response
    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw_text)
    if json_match:
        raw_text = json_match.group(1).strip()

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError:
        logger.error(f"Failed to parse OCR response: {raw_text}")
        result = {"error": "Failed to parse OCR response", "raw": raw_text}

    return result


# ─── Helper: Auto-match video ─────────────────────────────────────────────────

async def find_matching_videos(ocr_data: dict, limit: int = 5) -> list[dict]:
    """
    Find videos in the database that match the OCR-extracted data.
    Uses caption text, hashtags, posted date, and phase descriptions for matching.
    """
    candidates = []

    async with get_session() as session:
        # Strategy 1: Search by caption/description keywords in video_phases
        caption = ocr_data.get("caption", "") or ""
        hashtags = ocr_data.get("hashtags", []) or []
        posted_date = ocr_data.get("posted_date", "") or ""
        product_name = ocr_data.get("product_name", "") or ""

        # Build search terms from caption + product name
        search_terms = []
        if caption:
            # Extract meaningful words (skip short ones)
            words = re.findall(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]+|[a-zA-Z]{3,}', caption)
            search_terms.extend(words[:5])
        if product_name:
            search_terms.append(product_name)

        # Strategy 1: Match by phase_description content
        if search_terms:
            search_conditions = " OR ".join(
                [f"vp.phase_description ILIKE :term{i}" for i in range(len(search_terms))]
            )
            params = {f"term{i}": f"%{term}%" for i, term in enumerate(search_terms)}

            query = f"""
                SELECT DISTINCT v.id, v.original_filename, v.created_at,
                       array_agg(DISTINCT vp.phase_description) FILTER (WHERE vp.phase_description IS NOT NULL) as descriptions
                FROM videos v
                JOIN video_phases vp ON vp.video_id = v.id
                WHERE ({search_conditions})
                GROUP BY v.id, v.original_filename, v.created_at
                ORDER BY v.created_at DESC
                LIMIT :limit
            """
            params["limit"] = limit

            result = await session.execute(text(query), params)
            rows = result.fetchall()

            for row in rows:
                score = 0.0
                reasons = []

                # Score based on number of matching terms
                descs = row[3] or []
                desc_text = " ".join([d for d in descs if d])
                for term in search_terms:
                    if term.lower() in desc_text.lower():
                        score += 0.2
                        reasons.append(f"keyword:{term}")

                candidates.append({
                    "video_id": str(row[0]),
                    "original_filename": row[1] or "",
                    "created_at": row[2].isoformat() if row[2] else None,
                    "phase_descriptions": [d for d in descs if d][:5],
                    "match_score": min(score, 1.0),
                    "match_reason": ", ".join(reasons[:3]),
                })

        # Strategy 2: Match by date proximity
        if posted_date:
            # Parse date like "5-3" or "2026-05-03"
            date_query = """
                SELECT v.id, v.original_filename, v.created_at,
                       array_agg(DISTINCT vp.phase_description) FILTER (WHERE vp.phase_description IS NOT NULL) as descriptions
                FROM videos v
                LEFT JOIN video_phases vp ON vp.video_id = v.id
                WHERE v.created_at::date >= (CURRENT_DATE - INTERVAL '30 days')
                GROUP BY v.id, v.original_filename, v.created_at
                ORDER BY v.created_at DESC
                LIMIT :limit
            """
            result = await session.execute(text(date_query), {"limit": limit * 2})
            date_rows = result.fetchall()

            existing_ids = {c["video_id"] for c in candidates}
            for row in date_rows:
                vid = str(row[0])
                if vid in existing_ids:
                    # Boost existing candidate
                    for c in candidates:
                        if c["video_id"] == vid:
                            c["match_score"] += 0.1
                            c["match_reason"] += ", date_proximity"
                else:
                    descs = row[3] or []
                    candidates.append({
                        "video_id": vid,
                        "original_filename": row[1] or "",
                        "created_at": row[2].isoformat() if row[2] else None,
                        "phase_descriptions": [d for d in descs if d][:5],
                        "match_score": 0.1,
                        "match_reason": "date_proximity",
                    })

        # Strategy 3: If hashtags mention product keywords, search by those
        if hashtags:
            product_keywords = [h.replace("#", "") for h in hashtags if len(h) > 3]
            if product_keywords:
                for kw in product_keywords[:3]:
                    for c in candidates:
                        descs_text = " ".join(c.get("phase_descriptions", []))
                        if kw.lower() in descs_text.lower() or kw.lower() in c.get("original_filename", "").lower():
                            c["match_score"] += 0.15
                            c["match_reason"] += f", hashtag:{kw}"

    # Sort by score descending
    candidates.sort(key=lambda x: x["match_score"], reverse=True)
    return candidates[:limit]


# ─── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/ocr-screenshot", response_model=OCRScreenshotResponse)
async def ocr_screenshot(
    screenshot: UploadFile = File(..., description="TikTok screenshot image"),
    x_admin_key: Optional[str] = Header(None),
):
    """
    Upload a TikTok screenshot → OCR extract metrics → Auto-match with videos in DB.
    Returns extracted data and candidate video matches.
    """
    verify_admin(x_admin_key)

    # Read image
    content = await screenshot.read()
    if len(content) > 10 * 1024 * 1024:  # 10MB limit
        raise HTTPException(status_code=400, detail="Image too large (max 10MB)")

    content_type = screenshot.content_type or "image/jpeg"
    if content_type not in ("image/jpeg", "image/png", "image/webp", "image/heic"):
        content_type = "image/jpeg"

    # Step 1: OCR extraction
    try:
        raw_ocr = await extract_metrics_from_screenshot(content, content_type)
    except Exception as e:
        logger.error(f"OCR extraction failed: {e}")
        raise HTTPException(status_code=500, detail=f"OCR extraction failed: {str(e)}")

    # Step 2: Build structured OCR result
    ocr_data = OCRResult(
        views=raw_ocr.get("views"),
        likes=raw_ocr.get("likes"),
        comments=raw_ocr.get("comments"),
        shares=raw_ocr.get("shares"),
        saves=raw_ocr.get("saves"),
        purchases=raw_ocr.get("purchases"),
        revenue=raw_ocr.get("revenue"),
        caption=raw_ocr.get("caption"),
        hashtags=raw_ocr.get("hashtags"),
        posted_date=raw_ocr.get("posted_date"),
        account_name=raw_ocr.get("account_name"),
        product_name=raw_ocr.get("product_name"),
        avg_watch_time=raw_ocr.get("avg_watch_time"),
        retention_curve=raw_ocr.get("retention_curve"),
    )

    # Step 3: Auto-match with videos
    try:
        candidates_raw = await find_matching_videos(raw_ocr)
        candidates = [VideoCandidate(**c) for c in candidates_raw]
    except Exception as e:
        logger.error(f"Video matching failed: {e}")
        candidates = []

    best_match = candidates[0] if candidates and candidates[0].match_score >= 0.3 else None

    return OCRScreenshotResponse(
        ocr_data=ocr_data,
        candidates=candidates,
        best_match=best_match,
        raw_ocr=raw_ocr,
    )


@router.post("/confirm")
async def confirm_and_save(
    req: ConfirmMatchRequest,
    x_admin_key: Optional[str] = Header(None),
):
    """
    Confirm a video match and save performance data to the database.
    """
    verify_admin(x_admin_key)

    # Calculate rates
    engagement_rate = None
    conversion_rate = None
    views = req.ocr_data.views

    if views and views > 0:
        likes = req.ocr_data.likes or 0
        comments = req.ocr_data.comments or 0
        shares = req.ocr_data.shares or 0
        engagement_rate = (likes + comments + shares) / views

        if req.ocr_data.purchases:
            conversion_rate = req.ocr_data.purchases / views

    async with get_session() as session:
        query = text("""
            INSERT INTO video_performance (
                video_id, platform, views, likes, comments, shares, saves,
                purchases, revenue, engagement_rate, conversion_rate,
                avg_watch_time_seconds, retention_curve,
                matched_by, match_confidence, tiktok_video_id,
                caption, hashtags, posted_at, ocr_raw, recorded_at
            ) VALUES (
                :video_id, :platform, :views, :likes, :comments, :shares, :saves,
                :purchases, :revenue, :engagement_rate, :conversion_rate,
                :avg_watch_time, :retention_curve,
                :matched_by, :match_confidence, :tiktok_video_id,
                :caption, :hashtags, :posted_at, :ocr_raw, :recorded_at
            )
            RETURNING id, created_at
        """)

        # Parse posted_at
        posted_at = None
        if req.ocr_data.posted_date:
            try:
                # Handle formats like "5-3" → current year
                parts = req.ocr_data.posted_date.split("-")
                if len(parts) == 2:
                    month, day = int(parts[0]), int(parts[1])
                    posted_at = datetime(datetime.now().year, month, day, tzinfo=timezone.utc)
                elif len(parts) == 3:
                    posted_at = datetime.fromisoformat(req.ocr_data.posted_date)
            except (ValueError, TypeError):
                pass

        recorded_at = datetime.now(timezone.utc)
        if req.recorded_at:
            try:
                recorded_at = datetime.fromisoformat(req.recorded_at)
            except (ValueError, TypeError):
                pass

        result = await session.execute(query, {
            "video_id": req.video_id,
            "platform": req.platform,
            "views": req.ocr_data.views,
            "likes": req.ocr_data.likes,
            "comments": req.ocr_data.comments,
            "shares": req.ocr_data.shares,
            "saves": req.ocr_data.saves,
            "purchases": req.ocr_data.purchases,
            "revenue": req.ocr_data.revenue,
            "engagement_rate": engagement_rate,
            "conversion_rate": conversion_rate,
            "avg_watch_time": req.ocr_data.avg_watch_time,
            "retention_curve": json.dumps(req.ocr_data.retention_curve) if req.ocr_data.retention_curve else None,
            "matched_by": "ocr_auto_match",
            "match_confidence": 0.8,
            "tiktok_video_id": req.tiktok_video_id,
            "caption": req.ocr_data.caption,
            "hashtags": json.dumps(req.ocr_data.hashtags) if req.ocr_data.hashtags else None,
            "posted_at": posted_at,
            "ocr_raw": json.dumps(req.ocr_data.model_dump()),
            "recorded_at": recorded_at,
        })

        row = result.fetchone()

    logger.info(f"Performance data saved for video {req.video_id}: views={views}, engagement={engagement_rate}")

    return {
        "status": "saved",
        "id": str(row[0]),
        "video_id": req.video_id,
        "engagement_rate": engagement_rate,
        "conversion_rate": conversion_rate,
        "created_at": row[1].isoformat() if row[1] else None,
    }


@router.get("/list")
async def list_performance(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    video_id: Optional[str] = Query(None),
    x_admin_key: Optional[str] = Header(None),
):
    """List performance records with optional video_id filter."""
    verify_admin(x_admin_key)

    where = ""
    params: dict = {"limit": limit, "offset": offset}

    if video_id:
        where = "WHERE vp.video_id = :video_id"
        params["video_id"] = video_id

    query = text(f"""
        SELECT vp.id, vp.video_id, vp.platform, vp.views, vp.likes,
               vp.comments, vp.shares, vp.saves, vp.purchases, vp.revenue,
               vp.engagement_rate, vp.conversion_rate, vp.caption,
               vp.hashtags, vp.recorded_at, vp.created_at,
               v.original_filename
        FROM video_performance vp
        LEFT JOIN videos v ON v.id = vp.video_id
        {where}
        ORDER BY vp.recorded_at DESC
        LIMIT :limit OFFSET :offset
    """)

    async with get_session() as session:
        result = await session.execute(text(f"SELECT COUNT(*) FROM video_performance {'WHERE video_id = :video_id' if video_id else ''}"), params if video_id else {})
        total = result.scalar()

        result = await session.execute(query, params)
        rows = result.fetchall()

    records = []
    for row in rows:
        records.append({
            "id": str(row[0]),
            "video_id": str(row[1]),
            "platform": row[2],
            "views": row[3],
            "likes": row[4],
            "comments": row[5],
            "shares": row[6],
            "saves": row[7],
            "purchases": row[8],
            "revenue": row[9],
            "engagement_rate": row[10],
            "conversion_rate": row[11],
            "caption": row[12],
            "hashtags": row[13],
            "recorded_at": row[14].isoformat() if row[14] else None,
            "created_at": row[15].isoformat() if row[15] else None,
            "original_filename": row[16],
        })

    return {"records": records, "total": total, "limit": limit, "offset": offset}


@router.get("/video/{video_id}")
async def get_video_performance(
    video_id: str,
    x_admin_key: Optional[str] = Header(None),
):
    """Get all performance records for a specific video."""
    verify_admin(x_admin_key)

    query = text("""
        SELECT vp.id, vp.platform, vp.views, vp.likes, vp.comments,
               vp.shares, vp.saves, vp.purchases, vp.revenue,
               vp.engagement_rate, vp.conversion_rate,
               vp.avg_watch_time_seconds, vp.retention_curve,
               vp.caption, vp.hashtags, vp.posted_at, vp.recorded_at,
               vp.matched_by, vp.match_confidence, vp.tiktok_video_id
        FROM video_performance vp
        WHERE vp.video_id = :video_id
        ORDER BY vp.recorded_at DESC
    """)

    async with get_session() as session:
        result = await session.execute(query, {"video_id": video_id})
        rows = result.fetchall()

    if not rows:
        return {"video_id": video_id, "records": [], "summary": None}

    records = []
    for row in rows:
        records.append({
            "id": str(row[0]),
            "platform": row[1],
            "views": row[2],
            "likes": row[3],
            "comments": row[4],
            "shares": row[5],
            "saves": row[6],
            "purchases": row[7],
            "revenue": row[8],
            "engagement_rate": row[9],
            "conversion_rate": row[10],
            "avg_watch_time_seconds": row[11],
            "retention_curve": row[12],
            "caption": row[13],
            "hashtags": row[14],
            "posted_at": row[15].isoformat() if row[15] else None,
            "recorded_at": row[16].isoformat() if row[16] else None,
            "matched_by": row[17],
            "match_confidence": row[18],
            "tiktok_video_id": row[19],
        })

    # Summary: latest record stats
    latest = records[0]
    summary = {
        "total_records": len(records),
        "latest_views": latest["views"],
        "latest_engagement_rate": latest["engagement_rate"],
        "latest_conversion_rate": latest["conversion_rate"],
        "avg_engagement_rate": sum(r["engagement_rate"] for r in records if r["engagement_rate"]) / max(1, sum(1 for r in records if r["engagement_rate"])),
    }

    return {"video_id": video_id, "records": records, "summary": summary}


@router.post("/manual")
async def manual_input(
    req: ManualInputRequest,
    x_admin_key: Optional[str] = Header(None),
):
    """Manually input performance data for a video."""
    verify_admin(x_admin_key)

    # Calculate rates
    engagement_rate = None
    conversion_rate = None
    if req.views and req.views > 0:
        likes = req.likes or 0
        comments = req.comments or 0
        shares = req.shares or 0
        engagement_rate = (likes + comments + shares) / req.views
        if req.purchases:
            conversion_rate = req.purchases / req.views

    async with get_session() as session:
        query = text("""
            INSERT INTO video_performance (
                video_id, platform, views, likes, comments, shares, saves,
                purchases, revenue, engagement_rate, conversion_rate,
                avg_watch_time_seconds, matched_by, tiktok_video_id, recorded_at
            ) VALUES (
                :video_id, :platform, :views, :likes, :comments, :shares, :saves,
                :purchases, :revenue, :engagement_rate, :conversion_rate,
                :avg_watch_time, :matched_by, :tiktok_video_id, NOW()
            )
            RETURNING id
        """)

        result = await session.execute(query, {
            "video_id": req.video_id,
            "platform": req.platform,
            "views": req.views,
            "likes": req.likes,
            "comments": req.comments,
            "shares": req.shares,
            "saves": req.saves,
            "purchases": req.purchases,
            "revenue": req.revenue,
            "engagement_rate": engagement_rate,
            "conversion_rate": conversion_rate,
            "avg_watch_time": req.avg_watch_time_seconds,
            "matched_by": "manual_input",
            "tiktok_video_id": req.tiktok_video_id,
        })

        row = result.fetchone()

    return {
        "status": "saved",
        "id": str(row[0]),
        "video_id": req.video_id,
        "engagement_rate": engagement_rate,
        "conversion_rate": conversion_rate,
    }


@router.get("/stats")
async def performance_stats(
    x_admin_key: Optional[str] = Header(None),
):
    """Get overall performance statistics."""
    verify_admin(x_admin_key)

    async with get_session() as session:
        result = await session.execute(text("""
            SELECT 
                COUNT(*) as total_records,
                COUNT(DISTINCT video_id) as unique_videos,
                AVG(engagement_rate) as avg_engagement,
                AVG(conversion_rate) as avg_conversion,
                AVG(views) as avg_views,
                SUM(views) as total_views,
                SUM(purchases) as total_purchases,
                SUM(revenue) as total_revenue
            FROM video_performance
        """))
        row = result.fetchone()

    return {
        "total_records": row[0],
        "unique_videos": row[1],
        "avg_engagement_rate": float(row[2]) if row[2] else None,
        "avg_conversion_rate": float(row[3]) if row[3] else None,
        "avg_views": float(row[4]) if row[4] else None,
        "total_views": row[5],
        "total_purchases": row[6],
        "total_revenue": float(row[7]) if row[7] else None,
    }
