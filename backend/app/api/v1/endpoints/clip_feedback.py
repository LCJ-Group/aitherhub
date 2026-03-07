"""
clip_feedback.py — Clip Adopt/Reject Feedback API

FROZEN API CONTRACT (do not change routes without team review):
  POST   /api/v1/clips/{video_id}/feedback          — submit adopt/reject feedback
  GET    /api/v1/clips/{video_id}/feedback           — list feedback for a video
  GET    /api/v1/clips/{video_id}/feedback/summary   — summary stats for a video
  GET    /api/v1/clips/feedback/export               — export all feedback (admin, for AI training)
  DELETE /api/v1/clips/{video_id}/feedback/{phase_index} — remove feedback

This data is the training signal for the future Clip Rank AI model.
"""

import uuid
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


# ─── Schemas ─────────────────────────────────────────────────────────────────

class ClipFeedbackRequest(BaseModel):
    phase_index: int = Field(..., description="Phase index (matches video_phases.phase_index)")
    time_start: float = Field(..., description="Clip start time in seconds")
    time_end: float = Field(..., description="Clip end time in seconds")
    feedback: str = Field(..., description="'adopted' or 'rejected'")
    clip_id: Optional[str] = Field(None, description="UUID of video_clips row (if clip was generated)")
    posted_platform: Optional[str] = Field(None, description="tiktok | reels | youtube_shorts | other")
    reviewer_name: Optional[str] = Field(None, description="Name of reviewer")
    ai_score_at_feedback: Optional[float] = Field(None, description="AI score at time of feedback")
    score_breakdown: Optional[dict] = Field(None, description="Score breakdown dict")
    ai_reasons_at_feedback: Optional[list] = Field(None, description="AI reason tags at time of feedback")


class ClipFeedbackResponse(BaseModel):
    id: str
    video_id: str
    phase_index: int
    time_start: float
    time_end: float
    feedback: str
    clip_id: Optional[str]
    posted_platform: Optional[str]
    reviewer_name: Optional[str]
    ai_score_at_feedback: Optional[float]
    score_breakdown: Optional[dict]
    ai_reasons_at_feedback: Optional[list]
    created_at: str


class FeedbackSummary(BaseModel):
    video_id: str
    total_feedback: int
    adopted_count: int
    rejected_count: int
    adoption_rate_pct: float
    # Per-phase feedback: { phase_index: { feedback, ai_score, created_at } }
    per_phase: dict


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.post("/{video_id}/feedback", response_model=ClipFeedbackResponse)
async def submit_clip_feedback(
    video_id: str,
    req: ClipFeedbackRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Submit adopt/reject feedback for an AI clip candidate.
    This data becomes training signal for the Clip Rank AI model.
    """
    if req.feedback not in ("adopted", "rejected"):
        raise HTTPException(status_code=422, detail="feedback must be 'adopted' or 'rejected'")

    try:
        uuid.UUID(video_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid video_id UUID")

    feedback_id = str(uuid.uuid4())

    # Upsert: if feedback already exists for this video+phase, update it
    upsert_sql = text("""
        INSERT INTO clip_feedback (
            id, video_id, phase_index, time_start, time_end,
            feedback, clip_id, posted_platform, reviewer_name,
            ai_score_at_feedback, score_breakdown, ai_reasons_at_feedback,
            created_at, updated_at
        ) VALUES (
            :id, :video_id, :phase_index, :time_start, :time_end,
            :feedback, :clip_id, :posted_platform, :reviewer_name,
            :ai_score, :score_breakdown, :ai_reasons,
            NOW(), NOW()
        )
        ON CONFLICT (video_id, phase_index)
        DO UPDATE SET
            feedback = EXCLUDED.feedback,
            clip_id = COALESCE(EXCLUDED.clip_id, clip_feedback.clip_id),
            posted_platform = COALESCE(EXCLUDED.posted_platform, clip_feedback.posted_platform),
            reviewer_name = COALESCE(EXCLUDED.reviewer_name, clip_feedback.reviewer_name),
            ai_score_at_feedback = COALESCE(EXCLUDED.ai_score_at_feedback, clip_feedback.ai_score_at_feedback),
            score_breakdown = COALESCE(EXCLUDED.score_breakdown, clip_feedback.score_breakdown),
            ai_reasons_at_feedback = COALESCE(EXCLUDED.ai_reasons_at_feedback, clip_feedback.ai_reasons_at_feedback),
            updated_at = NOW()
        RETURNING id, video_id, phase_index, time_start, time_end,
                  feedback, clip_id, posted_platform, reviewer_name,
                  ai_score_at_feedback, score_breakdown, ai_reasons_at_feedback,
                  created_at
    """)

    import json as _json

    try:
        result = await db.execute(upsert_sql, {
            "id": feedback_id,
            "video_id": video_id,
            "phase_index": req.phase_index,
            "time_start": req.time_start,
            "time_end": req.time_end,
            "feedback": req.feedback,
            "clip_id": req.clip_id,
            "posted_platform": req.posted_platform,
            "reviewer_name": req.reviewer_name,
            "ai_score": req.ai_score_at_feedback,
            "score_breakdown": _json.dumps(req.score_breakdown) if req.score_breakdown else None,
            "ai_reasons": _json.dumps(req.ai_reasons_at_feedback) if req.ai_reasons_at_feedback else None,
        })
        await db.commit()
        row = result.fetchone()
    except Exception as e:
        await db.rollback()
        logger.error(f"[clip_feedback] DB error: {e}")
        raise HTTPException(status_code=500, detail="Failed to save feedback")

    return ClipFeedbackResponse(
        id=str(row.id),
        video_id=str(row.video_id),
        phase_index=row.phase_index,
        time_start=row.time_start,
        time_end=row.time_end,
        feedback=row.feedback,
        clip_id=str(row.clip_id) if row.clip_id else None,
        posted_platform=row.posted_platform,
        reviewer_name=row.reviewer_name,
        ai_score_at_feedback=row.ai_score_at_feedback,
        score_breakdown=row.score_breakdown,
        ai_reasons_at_feedback=row.ai_reasons_at_feedback,
        created_at=row.created_at.isoformat() if row.created_at else "",
    )


@router.get("/{video_id}/feedback", response_model=list[ClipFeedbackResponse])
async def list_clip_feedback(
    video_id: str,
    db: AsyncSession = Depends(get_db),
):
    """List all feedback for a video (used to restore UI state)."""
    try:
        uuid.UUID(video_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid video_id UUID")

    sql = text("""
        SELECT id, video_id, phase_index, time_start, time_end,
               feedback, clip_id, posted_platform, reviewer_name,
               ai_score_at_feedback, score_breakdown, ai_reasons_at_feedback,
               created_at
        FROM clip_feedback
        WHERE video_id = :video_id
        ORDER BY phase_index ASC
    """)
    result = await db.execute(sql, {"video_id": video_id})
    rows = result.fetchall()

    return [
        ClipFeedbackResponse(
            id=str(r.id),
            video_id=str(r.video_id),
            phase_index=r.phase_index,
            time_start=r.time_start,
            time_end=r.time_end,
            feedback=r.feedback,
            clip_id=str(r.clip_id) if r.clip_id else None,
            posted_platform=r.posted_platform,
            reviewer_name=r.reviewer_name,
            ai_score_at_feedback=r.ai_score_at_feedback,
            score_breakdown=r.score_breakdown,
            ai_reasons_at_feedback=r.ai_reasons_at_feedback,
            created_at=r.created_at.isoformat() if r.created_at else "",
        )
        for r in rows
    ]


@router.get("/{video_id}/feedback/summary", response_model=FeedbackSummary)
async def get_feedback_summary(
    video_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get adoption rate and per-phase feedback summary for a video."""
    try:
        uuid.UUID(video_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid video_id UUID")

    sql = text("""
        SELECT phase_index, feedback, ai_score_at_feedback, created_at
        FROM clip_feedback
        WHERE video_id = :video_id
        ORDER BY phase_index ASC
    """)
    result = await db.execute(sql, {"video_id": video_id})
    rows = result.fetchall()

    adopted = sum(1 for r in rows if r.feedback == "adopted")
    rejected = sum(1 for r in rows if r.feedback == "rejected")
    total = len(rows)
    rate = round(adopted / total * 100, 1) if total > 0 else 0.0

    per_phase = {
        r.phase_index: {
            "feedback": r.feedback,
            "ai_score": r.ai_score_at_feedback,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    }

    return FeedbackSummary(
        video_id=video_id,
        total_feedback=total,
        adopted_count=adopted,
        rejected_count=rejected,
        adoption_rate_pct=rate,
        per_phase=per_phase,
    )


@router.get("/feedback/export")
async def export_all_feedback(
    limit: int = Query(1000, le=10000),
    offset: int = Query(0),
    feedback_type: Optional[str] = Query(None, description="'adopted' or 'rejected' to filter"),
    db: AsyncSession = Depends(get_db),
):
    """
    Export all feedback data for AI training.
    Returns structured training data: features (score_breakdown) + label (feedback).
    """
    where = "WHERE 1=1"
    params: dict = {"limit": limit, "offset": offset}
    if feedback_type in ("adopted", "rejected"):
        where += " AND feedback = :feedback_type"
        params["feedback_type"] = feedback_type

    sql = text(f"""
        SELECT
            cf.id,
            cf.video_id,
            cf.phase_index,
            cf.time_start,
            cf.time_end,
            cf.feedback,
            cf.ai_score_at_feedback,
            cf.score_breakdown,
            cf.ai_reasons_at_feedback,
            cf.actual_views,
            cf.actual_sales,
            cf.created_at
        FROM clip_feedback cf
        {where}
        ORDER BY cf.created_at DESC
        LIMIT :limit OFFSET :offset
    """)
    result = await db.execute(sql, params)
    rows = result.fetchall()

    # Count total
    count_sql = text(f"SELECT COUNT(*) FROM clip_feedback cf {where}")
    count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    count_result = await db.execute(count_sql, count_params)
    total = count_result.scalar()

    training_data = []
    for r in rows:
        training_data.append({
            "id": str(r.id),
            "video_id": str(r.video_id),
            "phase_index": r.phase_index,
            "time_start": r.time_start,
            "time_end": r.time_end,
            # Label for AI training
            "label": 1 if r.feedback == "adopted" else 0,
            "feedback": r.feedback,
            # Features for AI training
            "features": r.score_breakdown or {},
            "ai_score": r.ai_score_at_feedback,
            "reasons": r.ai_reasons_at_feedback or [],
            # Actual performance (if available)
            "actual_views": r.actual_views,
            "actual_sales": r.actual_sales,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "training_data": training_data,
    }


@router.delete("/{video_id}/feedback/{phase_index}")
async def delete_clip_feedback(
    video_id: str,
    phase_index: int,
    db: AsyncSession = Depends(get_db),
):
    """Remove feedback for a specific phase (undo adopt/reject)."""
    try:
        uuid.UUID(video_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid video_id UUID")

    sql = text("""
        DELETE FROM clip_feedback
        WHERE video_id = :video_id AND phase_index = :phase_index
        RETURNING id
    """)
    result = await db.execute(sql, {"video_id": video_id, "phase_index": phase_index})
    await db.commit()
    deleted = result.fetchone()

    if not deleted:
        raise HTTPException(status_code=404, detail="Feedback not found")

    return {"status": "deleted", "phase_index": phase_index}
