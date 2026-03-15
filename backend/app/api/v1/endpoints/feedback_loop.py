"""
feedback_loop.py — Feedback Loop System v1

Human-in-the-Loop AI improvement APIs:
  ① Clip Rating:        POST /api/v1/feedback/{video_id}/clip-rating
  ② Edit Tracking:      POST /api/v1/feedback/{video_id}/edit-log
  ③ Sales Confirmation: POST /api/v1/feedback/{video_id}/sales-confirmation
  ④ Training Export:    GET  /api/v1/feedback/training-export

Data classification:
  - Raw Data:   NEVER modified (video, csv_metrics, transcript, etc.)
  - Human Data: NEVER modified (clip_review, manual_tags, etc.)
  - Feedback:   Append-only (this module creates new records only)
"""

import uuid
import json
import logging
from datetime import datetime, timezone
from typing import Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


# ─── Schemas ─────────────────────────────────────────────────────────────────

# ① Clip Rating
class ClipRatingRequest(BaseModel):
    phase_index: Optional[Union[int, str]] = Field(None, description="Phase index (int or string)")
    time_start: float = Field(..., description="Clip start time in seconds")
    time_end: float = Field(..., description="Clip end time in seconds")
    rating: str = Field(..., description="'good' or 'bad'")
    reason_tags: Optional[list[str]] = Field(
        None,
        description="Reason tags: hook_weak, too_long, too_short, cut_position, subtitle, audio, irrelevant",
    )
    clip_id: Optional[str] = Field(None, description="UUID of video_clips row")
    reviewer_name: Optional[str] = Field(None, description="Name of reviewer")
    ai_score_at_feedback: Optional[float] = Field(None, description="AI score at time of feedback")
    score_breakdown: Optional[dict] = Field(None, description="Score breakdown dict")


class ClipRatingResponse(BaseModel):
    id: str
    video_id: str
    phase_index: int
    rating: str
    reason_tags: Optional[list[str]]
    created_at: str


# ② Edit Tracking
class EditLogRequest(BaseModel):
    clip_id: str = Field(..., description="UUID of the clip being edited")
    edit_type: str = Field(
        ...,
        description="Type of edit: trim_start | trim_end | caption_edit | re_export",
    )
    before_value: dict = Field(..., description="Value before edit")
    after_value: dict = Field(..., description="Value after edit")
    delta_seconds: Optional[float] = Field(None, description="Time delta for trim edits")


class EditLogResponse(BaseModel):
    id: str
    clip_id: str
    video_id: str
    edit_type: str
    before_value: dict
    after_value: dict
    delta_seconds: Optional[float]
    created_at: str


# ③ Sales Confirmation
class SalesConfirmationRequest(BaseModel):
    phase_index: Optional[Union[int, str]] = Field(None, description="Phase index (int or string)")
    time_start: float = Field(..., description="Clip start time in seconds")
    time_end: float = Field(..., description="Clip end time in seconds")
    is_sales_moment: bool = Field(..., description="True if this is a selling moment")
    clip_id: Optional[str] = Field(None, description="UUID of video_clips row")
    confidence: Optional[int] = Field(None, ge=1, le=5, description="Confidence 1-5")
    note: Optional[str] = Field(None, description="Optional note")
    reviewer_name: Optional[str] = Field(None, description="Name of reviewer")


class SalesConfirmationResponse(BaseModel):
    id: str
    video_id: str
    phase_index: int
    is_sales_moment: bool
    confidence: Optional[int]
    note: Optional[str]
    created_at: str


# ─── ① Clip Rating ──────────────────────────────────────────────────────────

@router.post("/{video_id}/clip-rating", response_model=ClipRatingResponse)
async def submit_clip_rating(
    video_id: str,
    req: ClipRatingRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Submit quick rating (👍 good / 👎 bad) with optional reason tags.
    This is the primary training signal for Clip Rank AI.
    """
    if req.rating not in ("good", "bad"):
        raise HTTPException(status_code=422, detail="rating must be 'good' or 'bad'")

    valid_reasons = {
        "hook_weak", "too_long", "too_short", "cut_position",
        "subtitle", "audio", "irrelevant", "perfect", "other",
    }
    if req.reason_tags:
        invalid = [t for t in req.reason_tags if t not in valid_reasons]
        if invalid:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid reason tags: {invalid}. Valid: {sorted(valid_reasons)}",
            )

    try:
        uuid.UUID(video_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid video_id UUID")

    feedback_id = str(uuid.uuid4())

    # Map rating to feedback for backward compatibility with clip_feedback table
    feedback_value = "adopted" if req.rating == "good" else "rejected"

    # Ensure phase_index has a value for ON CONFLICT (video_id, phase_index)
    # If not provided, use clip_id or generate from time range
    phase_index_val = req.phase_index
    if phase_index_val is None:
        if req.clip_id:
            phase_index_val = f"clip_{req.clip_id[:8]}"
        else:
            phase_index_val = f"t_{int(req.time_start)}_{int(req.time_end)}"

    upsert_sql = text("""
        INSERT INTO clip_feedback (
            id, video_id, phase_index, time_start, time_end,
            feedback, rating, reason_tags, clip_id,
            reviewer_name, ai_score_at_feedback, score_breakdown,
            created_at, updated_at
        ) VALUES (
            :id, :video_id, :phase_index, :time_start, :time_end,
            :feedback, :rating, :reason_tags, :clip_id,
            :reviewer_name, :ai_score, :score_breakdown,
            NOW(), NOW()
        )
        ON CONFLICT (video_id, phase_index)
        DO UPDATE SET
            feedback = EXCLUDED.feedback,
            rating = EXCLUDED.rating,
            reason_tags = EXCLUDED.reason_tags,
            clip_id = COALESCE(EXCLUDED.clip_id, clip_feedback.clip_id),
            reviewer_name = COALESCE(EXCLUDED.reviewer_name, clip_feedback.reviewer_name),
            ai_score_at_feedback = COALESCE(EXCLUDED.ai_score_at_feedback, clip_feedback.ai_score_at_feedback),
            score_breakdown = COALESCE(EXCLUDED.score_breakdown, clip_feedback.score_breakdown),
            updated_at = NOW()
        RETURNING id, video_id, phase_index, rating, reason_tags, created_at
    """)

    try:
        result = await db.execute(upsert_sql, {
            "id": feedback_id,
            "video_id": video_id,
            "phase_index": str(phase_index_val),
            "time_start": req.time_start,
            "time_end": req.time_end,
            "feedback": feedback_value,
            "rating": req.rating,
            "reason_tags": json.dumps(req.reason_tags) if req.reason_tags else None,
            "clip_id": req.clip_id,
            "reviewer_name": req.reviewer_name,
            "ai_score": req.ai_score_at_feedback,
            "score_breakdown": json.dumps(req.score_breakdown) if req.score_breakdown else None,
        })
        await db.commit()
        row = result.fetchone()
    except Exception as e:
        await db.rollback()
        logger.error(f"[clip_rating] DB error: {e}")
        raise HTTPException(status_code=500, detail="Failed to save rating")

    reason_tags_parsed = None
    if row.reason_tags:
        reason_tags_parsed = row.reason_tags if isinstance(row.reason_tags, list) else json.loads(row.reason_tags)

    return ClipRatingResponse(
        id=str(row.id),
        video_id=str(row.video_id),
        phase_index=row.phase_index,
        rating=row.rating,
        reason_tags=reason_tags_parsed,
        created_at=row.created_at.isoformat() if row.created_at else "",
    )


@router.get("/{video_id}/clip-ratings")
async def list_clip_ratings(
    video_id: str,
    db: AsyncSession = Depends(get_db),
):
    """List all clip ratings for a video (to restore UI state)."""
    try:
        uuid.UUID(video_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid video_id UUID")

    sql = text("""
        SELECT id, video_id, phase_index, rating, reason_tags,
               feedback, time_start, time_end, created_at
        FROM clip_feedback
        WHERE video_id = :video_id AND rating IS NOT NULL
        ORDER BY phase_index ASC
    """)
    result = await db.execute(sql, {"video_id": video_id})
    rows = result.fetchall()

    ratings = []
    for r in rows:
        reason_tags = None
        if r.reason_tags:
            reason_tags = r.reason_tags if isinstance(r.reason_tags, list) else json.loads(r.reason_tags)
        ratings.append({
            "id": str(r.id),
            "video_id": str(r.video_id),
            "phase_index": r.phase_index,
            "rating": r.rating,
            "reason_tags": reason_tags,
            "time_start": r.time_start,
            "time_end": r.time_end,
            "created_at": r.created_at.isoformat() if r.created_at else "",
        })

    return {"ratings": ratings}


# ─── ② Edit Tracking ────────────────────────────────────────────────────────

@router.post("/{video_id}/edit-log", response_model=EditLogResponse)
async def log_clip_edit(
    video_id: str,
    req: EditLogRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Log a user edit to an AI-generated clip.
    This data teaches AI where its cuts/captions are wrong.
    """
    valid_types = {"trim_start", "trim_end", "caption_edit", "re_export"}
    if req.edit_type not in valid_types:
        raise HTTPException(
            status_code=422,
            detail=f"edit_type must be one of: {sorted(valid_types)}",
        )

    try:
        uuid.UUID(video_id)
        uuid.UUID(req.clip_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid UUID format")

    log_id = str(uuid.uuid4())

    sql = text("""
        INSERT INTO clip_edit_log (
            id, clip_id, video_id, edit_type,
            before_value, after_value, delta_seconds, created_at
        ) VALUES (
            :id, :clip_id, :video_id, :edit_type,
            :before_value, :after_value, :delta_seconds, NOW()
        )
        RETURNING id, clip_id, video_id, edit_type,
                  before_value, after_value, delta_seconds, created_at
    """)

    try:
        result = await db.execute(sql, {
            "id": log_id,
            "clip_id": req.clip_id,
            "video_id": video_id,
            "edit_type": req.edit_type,
            "before_value": json.dumps(req.before_value),
            "after_value": json.dumps(req.after_value),
            "delta_seconds": req.delta_seconds,
        })
        await db.commit()
        row = result.fetchone()
    except Exception as e:
        await db.rollback()
        logger.error(f"[edit_log] DB error: {e}")
        raise HTTPException(status_code=500, detail="Failed to log edit")

    before_val = row.before_value if isinstance(row.before_value, dict) else json.loads(row.before_value)
    after_val = row.after_value if isinstance(row.after_value, dict) else json.loads(row.after_value)

    return EditLogResponse(
        id=str(row.id),
        clip_id=str(row.clip_id),
        video_id=str(row.video_id),
        edit_type=row.edit_type,
        before_value=before_val,
        after_value=after_val,
        delta_seconds=row.delta_seconds,
        created_at=row.created_at.isoformat() if row.created_at else "",
    )


@router.get("/{video_id}/edit-log")
async def list_edit_logs(
    video_id: str,
    clip_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List edit logs for a video, optionally filtered by clip_id."""
    try:
        uuid.UUID(video_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid video_id UUID")

    where = "WHERE video_id = :video_id"
    params: dict = {"video_id": video_id}
    if clip_id:
        where += " AND clip_id = :clip_id"
        params["clip_id"] = clip_id

    sql = text(f"""
        SELECT id, clip_id, video_id, edit_type,
               before_value, after_value, delta_seconds, created_at
        FROM clip_edit_log
        {where}
        ORDER BY created_at DESC
        LIMIT 100
    """)
    result = await db.execute(sql, params)
    rows = result.fetchall()

    logs = []
    for r in rows:
        before_val = r.before_value if isinstance(r.before_value, dict) else json.loads(r.before_value)
        after_val = r.after_value if isinstance(r.after_value, dict) else json.loads(r.after_value)
        logs.append({
            "id": str(r.id),
            "clip_id": str(r.clip_id),
            "video_id": str(r.video_id),
            "edit_type": r.edit_type,
            "before_value": before_val,
            "after_value": after_val,
            "delta_seconds": r.delta_seconds,
            "created_at": r.created_at.isoformat() if r.created_at else "",
        })

    return {"edit_logs": logs, "total": len(logs)}


# ─── ③ Sales Confirmation ───────────────────────────────────────────────────

@router.post("/{video_id}/sales-confirmation", response_model=SalesConfirmationResponse)
async def submit_sales_confirmation(
    video_id: str,
    req: SalesConfirmationRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    User confirms whether a clip captures the actual selling moment.
    YES answers become 'Sales DNA' — the core training data for AitherHub.
    """
    try:
        uuid.UUID(video_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid video_id UUID")

    conf_id = str(uuid.uuid4())

    # Ensure phase_index has a value for ON CONFLICT (video_id, phase_index)
    phase_index_val = req.phase_index
    if phase_index_val is None:
        if req.clip_id:
            phase_index_val = f"clip_{req.clip_id[:8]}"
        else:
            phase_index_val = f"t_{int(req.time_start)}_{int(req.time_end)}"

    upsert_sql = text("""
        INSERT INTO sales_confirmation (
            id, video_id, phase_index, time_start, time_end,
            is_sales_moment, clip_id, confidence, note, reviewer_name,
            created_at, updated_at
        ) VALUES (
            :id, :video_id, :phase_index, :time_start, :time_end,
            :is_sales_moment, :clip_id, :confidence, :note, :reviewer_name,
            NOW(), NOW()
        )
        ON CONFLICT (video_id, phase_index)
        DO UPDATE SET
            is_sales_moment = EXCLUDED.is_sales_moment,
            clip_id = COALESCE(EXCLUDED.clip_id, sales_confirmation.clip_id),
            confidence = COALESCE(EXCLUDED.confidence, sales_confirmation.confidence),
            note = COALESCE(EXCLUDED.note, sales_confirmation.note),
            reviewer_name = COALESCE(EXCLUDED.reviewer_name, sales_confirmation.reviewer_name),
            updated_at = NOW()
        RETURNING id, video_id, phase_index, is_sales_moment, confidence, note, created_at
    """)

    try:
        result = await db.execute(upsert_sql, {
            "id": conf_id,
            "video_id": video_id,
            "phase_index": str(phase_index_val),
            "time_start": req.time_start,
            "time_end": req.time_end,
            "is_sales_moment": req.is_sales_moment,
            "clip_id": req.clip_id,
            "confidence": req.confidence,
            "note": req.note,
            "reviewer_name": req.reviewer_name,
        })
        await db.commit()
        row = result.fetchone()
    except Exception as e:
        await db.rollback()
        logger.error(f"[sales_confirmation] DB error: {e}")
        raise HTTPException(status_code=500, detail="Failed to save confirmation")

    return SalesConfirmationResponse(
        id=str(row.id),
        video_id=str(row.video_id),
        phase_index=row.phase_index,
        is_sales_moment=row.is_sales_moment,
        confidence=row.confidence,
        note=row.note,
        created_at=row.created_at.isoformat() if row.created_at else "",
    )


@router.get("/{video_id}/sales-confirmations")
async def list_sales_confirmations(
    video_id: str,
    db: AsyncSession = Depends(get_db),
):
    """List all sales confirmations for a video."""
    try:
        uuid.UUID(video_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid video_id UUID")

    sql = text("""
        SELECT id, video_id, phase_index, time_start, time_end,
               is_sales_moment, clip_id, confidence, note,
               reviewer_name, created_at
        FROM sales_confirmation
        WHERE video_id = :video_id
        ORDER BY phase_index ASC
    """)
    result = await db.execute(sql, {"video_id": video_id})
    rows = result.fetchall()

    confirmations = []
    for r in rows:
        confirmations.append({
            "id": str(r.id),
            "video_id": str(r.video_id),
            "phase_index": r.phase_index,
            "time_start": r.time_start,
            "time_end": r.time_end,
            "is_sales_moment": r.is_sales_moment,
            "clip_id": str(r.clip_id) if r.clip_id else None,
            "confidence": r.confidence,
            "note": r.note,
            "reviewer_name": r.reviewer_name,
            "created_at": r.created_at.isoformat() if r.created_at else "",
        })

    # Summary stats
    total = len(confirmations)
    yes_count = sum(1 for c in confirmations if c["is_sales_moment"])
    no_count = total - yes_count

    return {
        "confirmations": confirmations,
        "summary": {
            "total": total,
            "yes_count": yes_count,
            "no_count": no_count,
            "sales_dna_rate_pct": round(yes_count / total * 100, 1) if total > 0 else 0.0,
        },
    }


# ─── ④ Training Data Export ──────────────────────────────────────────────────

@router.get("/training-export")
async def export_training_data(
    limit: int = Query(1000, le=10000),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
):
    """
    Export combined feedback data for AI training.
    Merges: clip_rating + edit_tracking + sales_confirmation
    into a single training dataset.
    """
    # ① Clip ratings
    ratings_sql = text("""
        SELECT cf.video_id, cf.phase_index, cf.time_start, cf.time_end,
               cf.rating, cf.reason_tags, cf.feedback,
               cf.ai_score_at_feedback, cf.score_breakdown,
               cf.created_at,
               sc.is_sales_moment, sc.confidence AS sales_confidence
        FROM clip_feedback cf
        LEFT JOIN sales_confirmation sc
          ON cf.video_id = sc.video_id AND cf.phase_index = sc.phase_index
        WHERE cf.rating IS NOT NULL
        ORDER BY cf.created_at DESC
        LIMIT :limit OFFSET :offset
    """)
    result = await db.execute(ratings_sql, {"limit": limit, "offset": offset})
    rows = result.fetchall()

    # ② Edit patterns (aggregated)
    edits_sql = text("""
        SELECT video_id, edit_type, COUNT(*) as edit_count,
               AVG(ABS(delta_seconds)) as avg_delta
        FROM clip_edit_log
        GROUP BY video_id, edit_type
        ORDER BY edit_count DESC
        LIMIT 500
    """)
    edit_result = await db.execute(edits_sql)
    edit_rows = edit_result.fetchall()

    edit_patterns = {}
    for er in edit_rows:
        vid = str(er.video_id)
        if vid not in edit_patterns:
            edit_patterns[vid] = {}
        edit_patterns[vid][er.edit_type] = {
            "count": er.edit_count,
            "avg_delta_sec": round(er.avg_delta, 2) if er.avg_delta else None,
        }

    # Build training records
    training_data = []
    for r in rows:
        vid = str(r.video_id)
        reason_tags = None
        if r.reason_tags:
            reason_tags = r.reason_tags if isinstance(r.reason_tags, list) else json.loads(r.reason_tags)
        score_breakdown = None
        if r.score_breakdown:
            score_breakdown = r.score_breakdown if isinstance(r.score_breakdown, dict) else json.loads(r.score_breakdown)

        training_data.append({
            "video_id": vid,
            "phase_index": r.phase_index,
            "time_start": r.time_start,
            "time_end": r.time_end,
            "duration": round(r.time_end - r.time_start, 2),
            # Labels
            "label_rating": 1 if r.rating == "good" else 0,
            "label_adopted": 1 if r.feedback == "adopted" else 0,
            "label_sales_dna": r.is_sales_moment if r.is_sales_moment is not None else None,
            "sales_confidence": r.sales_confidence,
            # Feedback details
            "rating": r.rating,
            "reason_tags": reason_tags,
            # Features
            "ai_score": r.ai_score_at_feedback,
            "features": score_breakdown,
            # Edit patterns for this video
            "edit_patterns": edit_patterns.get(vid, {}),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })

    # Count total
    count_sql = text("SELECT COUNT(*) FROM clip_feedback WHERE rating IS NOT NULL")
    count_result = await db.execute(count_sql)
    total = count_result.scalar()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "training_data": training_data,
        "edit_pattern_summary": {
            "total_videos_with_edits": len(edit_patterns),
            "patterns": edit_patterns,
        },
    }
