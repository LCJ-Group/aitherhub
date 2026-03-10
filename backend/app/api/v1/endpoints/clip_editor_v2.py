"""
clip_editor_v2.py — Intelligent Clip Editor v2 APIs

APIs for the new editor:
  ① Segment Scores:     GET  /api/v1/editor/{video_id}/segments
  ② Video Score:        GET  /api/v1/editor/{video_id}/score
  ③ Segment Feedback:   POST /api/v1/editor/{video_id}/segment-feedback
  ④ List Feedback:      GET  /api/v1/editor/{video_id}/segment-feedback
  ⑤ Timeline Data:      GET  /api/v1/editor/{video_id}/timeline
"""

import uuid
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


# ─── Schemas ─────────────────────────────────────────────────────────────────

class SegmentFeedbackRequest(BaseModel):
    start_sec: float = Field(..., description="Segment start time in seconds")
    end_sec: float = Field(..., description="Segment end time in seconds")
    segment_id: Optional[str] = Field(None, description="UUID of clip_segments row")
    feedback_type: str = Field(
        ...,
        description="Feedback type: good, weak, sold_well, skip, used",
    )
    label: Optional[str] = Field(
        None,
        description="Label: sales_moment, comment_explosion, strong_hook, "
                    "clear_explanation, product_appeal, too_long, weak, dropout",
    )
    note: Optional[str] = Field(None, description="Optional note")


class SegmentFeedbackResponse(BaseModel):
    id: str
    video_id: str
    start_sec: float
    end_sec: float
    feedback_type: str
    label: Optional[str]
    created_at: str


# ─── ① Segment Scores ─────────────────────────────────────────────────────

@router.get("/{video_id}/segments")
async def get_segments(
    video_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Get all segment-level AI scores for a video.
    Used to render the score heatmap on the timeline.
    """
    try:
        uuid.UUID(video_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid video_id UUID")

    sql = text("""
        SELECT id, video_id, start_sec, end_sec, phase_index,
               viral_score, hook_score, sales_score,
               comment_score, retention_score, speech_energy,
               marker_type, marker_meta
        FROM clip_segments
        WHERE video_id = :video_id
        ORDER BY start_sec ASC
    """)

    try:
        result = await db.execute(sql, {"video_id": video_id})
        rows = result.fetchall()
    except Exception as e:
        logger.warning(f"[segments] Table may not exist yet: {e}")
        # Fallback: generate segments from existing phase data
        return await _generate_segments_from_phases(video_id, db)

    segments = []
    for r in rows:
        segments.append({
            "id": str(r.id),
            "video_id": str(r.video_id),
            "start_sec": r.start_sec,
            "end_sec": r.end_sec,
            "phase_index": r.phase_index,
            "viral_score": r.viral_score,
            "hook_score": r.hook_score,
            "sales_score": r.sales_score,
            "comment_score": r.comment_score,
            "retention_score": r.retention_score,
            "speech_energy": r.speech_energy,
            "marker_type": r.marker_type,
            "marker_meta": r.marker_meta,
        })

    if not segments:
        # No segments yet: generate from existing phase data
        return await _generate_segments_from_phases(video_id, db)

    return {"segments": segments, "count": len(segments)}


async def _generate_segments_from_phases(video_id: str, db: AsyncSession):
    """
    Fallback: generate segment scores from existing video_phases + phase_insights data.
    This allows the heatmap to work even before the clip_segments table is populated.
    """
    sql = text("""
        SELECT vp.phase_index, vp.time_start, vp.time_end,
               pi.hook_score, pi.viral_score,
               pi.engagement_score, pi.speech_energy
        FROM video_phases vp
        LEFT JOIN phase_insights pi
            ON pi.video_id = vp.video_id AND pi.phase_index = vp.phase_index
        WHERE vp.video_id = :video_id
        ORDER BY vp.phase_index ASC
    """)

    try:
        result = await db.execute(sql, {"video_id": video_id})
        rows = result.fetchall()
    except Exception as e:
        logger.warning(f"[segments_fallback] Error: {e}")
        return {"segments": [], "count": 0, "source": "empty"}

    segments = []
    for r in rows:
        segments.append({
            "id": None,
            "video_id": video_id,
            "start_sec": r.time_start if r.time_start else 0,
            "end_sec": r.time_end if r.time_end else 0,
            "phase_index": r.phase_index,
            "viral_score": r.viral_score,
            "hook_score": r.hook_score,
            "sales_score": None,
            "comment_score": r.engagement_score,
            "retention_score": None,
            "speech_energy": r.speech_energy,
            "marker_type": None,
            "marker_meta": None,
        })

    return {"segments": segments, "count": len(segments), "source": "phase_fallback"}


# ─── ② Video Score ─────────────────────────────────────────────────────────

@router.get("/{video_id}/score")
async def get_video_score(
    video_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Get overall video-level evaluation scores.
    """
    try:
        uuid.UUID(video_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid video_id UUID")

    sql = text("""
        SELECT id, video_id, overall_score, viral_potential,
               clipability_score, sales_density, hook_density,
               reusability_score, score_breakdown,
               strong_segment_count, clip_candidate_count,
               best_hook_time, best_sales_time
        FROM video_scores
        WHERE video_id = :video_id
    """)

    try:
        result = await db.execute(sql, {"video_id": video_id})
        row = result.fetchone()
    except Exception as e:
        logger.warning(f"[video_score] Table may not exist yet: {e}")
        # Fallback: compute from existing data
        return await _compute_video_score_fallback(video_id, db)

    if not row:
        return await _compute_video_score_fallback(video_id, db)

    return {
        "id": str(row.id),
        "video_id": str(row.video_id),
        "overall_score": row.overall_score,
        "viral_potential": row.viral_potential,
        "clipability_score": row.clipability_score,
        "sales_density": row.sales_density,
        "hook_density": row.hook_density,
        "reusability_score": row.reusability_score,
        "score_breakdown": row.score_breakdown,
        "strong_segment_count": row.strong_segment_count,
        "clip_candidate_count": row.clip_candidate_count,
        "best_hook_time": row.best_hook_time,
        "best_sales_time": row.best_sales_time,
        "source": "video_scores",
    }


async def _compute_video_score_fallback(video_id: str, db: AsyncSession):
    """
    Compute video-level scores from existing phase_insights and event_scores data.
    """
    sql = text("""
        SELECT
            COUNT(*) as phase_count,
            AVG(pi.hook_score) as avg_hook,
            AVG(pi.viral_score) as avg_viral,
            AVG(pi.engagement_score) as avg_engagement,
            MAX(pi.hook_score) as max_hook,
            MAX(pi.viral_score) as max_viral
        FROM phase_insights pi
        WHERE pi.video_id = :video_id
    """)

    try:
        result = await db.execute(sql, {"video_id": video_id})
        row = result.fetchone()
    except Exception as e:
        logger.warning(f"[video_score_fallback] Error: {e}")
        return {"overall_score": None, "source": "empty"}

    if not row or not row.phase_count:
        return {"overall_score": None, "source": "empty"}

    # Simple composite score
    avg_hook = row.avg_hook or 0
    avg_viral = row.avg_viral or 0
    avg_engagement = row.avg_engagement or 0
    overall = (avg_hook * 0.3 + avg_viral * 0.4 + avg_engagement * 0.3)

    # Count strong segments (hook > 70 or viral > 70)
    strong_sql = text("""
        SELECT COUNT(*) as cnt
        FROM phase_insights
        WHERE video_id = :video_id
          AND (hook_score > 70 OR viral_score > 70)
    """)
    strong_result = await db.execute(strong_sql, {"video_id": video_id})
    strong_count = strong_result.scalar() or 0

    return {
        "video_id": video_id,
        "overall_score": round(overall, 1),
        "viral_potential": round(avg_viral, 1),
        "clipability_score": round(min(100, strong_count * 15), 1),
        "sales_density": None,
        "hook_density": round(avg_hook, 1),
        "reusability_score": None,
        "score_breakdown": {
            "avg_hook": round(avg_hook, 1),
            "avg_viral": round(avg_viral, 1),
            "avg_engagement": round(avg_engagement, 1),
            "max_hook": round(row.max_hook or 0, 1),
            "max_viral": round(row.max_viral or 0, 1),
        },
        "strong_segment_count": strong_count,
        "clip_candidate_count": None,
        "best_hook_time": None,
        "best_sales_time": None,
        "source": "phase_fallback",
    }


# ─── ③ Segment Feedback ───────────────────────────────────────────────────

@router.post("/{video_id}/segment-feedback", response_model=SegmentFeedbackResponse)
async def submit_segment_feedback(
    video_id: str,
    req: SegmentFeedbackRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Submit feedback on a timeline segment.
    This is the primary learning signal for the intelligent timeline.
    """
    valid_types = {"good", "weak", "sold_well", "skip", "used"}
    if req.feedback_type not in valid_types:
        raise HTTPException(
            status_code=422,
            detail=f"feedback_type must be one of: {sorted(valid_types)}",
        )

    valid_labels = {
        "sales_moment", "comment_explosion", "strong_hook",
        "clear_explanation", "product_appeal", "too_long", "weak", "dropout",
        None,
    }
    if req.label not in valid_labels:
        raise HTTPException(
            status_code=422,
            detail=f"label must be one of: {sorted(l for l in valid_labels if l)}",
        )

    try:
        uuid.UUID(video_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid video_id UUID")

    feedback_id = str(uuid.uuid4())

    sql = text("""
        INSERT INTO segment_feedback (
            id, video_id, segment_id, start_sec, end_sec,
            feedback_type, label, note, created_at
        ) VALUES (
            :id, :video_id, :segment_id, :start_sec, :end_sec,
            :feedback_type, :label, :note, NOW()
        )
        RETURNING id, video_id, start_sec, end_sec,
                  feedback_type, label, created_at
    """)

    try:
        result = await db.execute(sql, {
            "id": feedback_id,
            "video_id": video_id,
            "segment_id": req.segment_id,
            "start_sec": req.start_sec,
            "end_sec": req.end_sec,
            "feedback_type": req.feedback_type,
            "label": req.label,
            "note": req.note,
        })
        await db.commit()
        row = result.fetchone()
    except Exception as e:
        await db.rollback()
        logger.error(f"[segment_feedback] DB error: {e}")
        raise HTTPException(status_code=500, detail="Failed to save feedback")

    return SegmentFeedbackResponse(
        id=str(row.id),
        video_id=str(row.video_id),
        start_sec=row.start_sec,
        end_sec=row.end_sec,
        feedback_type=row.feedback_type,
        label=row.label,
        created_at=row.created_at.isoformat() if row.created_at else "",
    )


@router.get("/{video_id}/segment-feedback")
async def list_segment_feedback(
    video_id: str,
    db: AsyncSession = Depends(get_db),
):
    """List all segment feedback for a video."""
    try:
        uuid.UUID(video_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid video_id UUID")

    sql = text("""
        SELECT id, video_id, segment_id, start_sec, end_sec,
               feedback_type, label, note, created_at
        FROM segment_feedback
        WHERE video_id = :video_id
        ORDER BY start_sec ASC
    """)

    try:
        result = await db.execute(sql, {"video_id": video_id})
        rows = result.fetchall()
    except Exception as e:
        logger.warning(f"[segment_feedback] Table may not exist yet: {e}")
        return {"feedback": [], "count": 0}

    feedback = []
    for r in rows:
        feedback.append({
            "id": str(r.id),
            "video_id": str(r.video_id),
            "segment_id": str(r.segment_id) if r.segment_id else None,
            "start_sec": r.start_sec,
            "end_sec": r.end_sec,
            "feedback_type": r.feedback_type,
            "label": r.label,
            "note": r.note,
            "created_at": r.created_at.isoformat() if r.created_at else "",
        })

    return {"feedback": feedback, "count": len(feedback)}


# ─── ⑤ Timeline Data (aggregated) ─────────────────────────────────────────

@router.get("/{video_id}/timeline")
async def get_timeline_data(
    video_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Get all data needed to render the intelligent timeline in one call:
    - Phases with timestamps
    - Segment scores (or phase-level fallback)
    - AI markers (sales moments, hooks, etc.)
    - Existing feedback
    """
    try:
        uuid.UUID(video_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid video_id UUID")

    # 1. Phases (with phase_insights JOIN, fallback to phases-only)
    phases = []
    try:
        phases_sql = text("""
            SELECT vp.phase_index, vp.time_start, vp.time_end,
                   vp.phase_description,
                   pi.hook_score, pi.viral_score,
                   pi.engagement_score, pi.speech_energy,
                   pi.key_actions
            FROM video_phases vp
            LEFT JOIN phase_insights pi
                ON pi.video_id = vp.video_id AND pi.phase_index = vp.phase_index
            WHERE vp.video_id = :video_id
            ORDER BY vp.phase_index ASC
        """)
        phases_result = await db.execute(phases_sql, {"video_id": video_id})
        phases_rows = phases_result.fetchall()

        for r in phases_rows:
            key_actions = r.key_actions
            if isinstance(key_actions, str):
                try:
                    key_actions = json.loads(key_actions)
                except Exception:
                    key_actions = None

            phases.append({
                "phase_index": r.phase_index,
                "time_start": r.time_start,
                "time_end": r.time_end,
                "description": r.phase_description,
                "hook_score": r.hook_score,
                "viral_score": r.viral_score,
                "engagement_score": r.engagement_score,
                "speech_energy": r.speech_energy,
                "key_actions": key_actions,
            })
    except Exception as e:
        logger.warning(f"[timeline] phases+insights query failed, trying phases-only: {e}")
        try:
            await db.rollback()
        except Exception:
            pass
        try:
            phases_sql = text("""
                SELECT phase_index, time_start, time_end, phase_description
                FROM video_phases
                WHERE video_id = :video_id
                ORDER BY phase_index ASC
            """)
            phases_result = await db.execute(phases_sql, {"video_id": video_id})
            phases_rows = phases_result.fetchall()
            for r in phases_rows:
                phases.append({
                    "phase_index": r.phase_index,
                    "time_start": r.time_start,
                    "time_end": r.time_end,
                    "description": r.phase_description,
                    "hook_score": None,
                    "viral_score": None,
                    "engagement_score": None,
                    "speech_energy": None,
                    "key_actions": None,
                })
        except Exception as e2:
            logger.warning(f"[timeline] phases-only query also failed: {e2}")
            try:
                await db.rollback()
            except Exception:
                pass

    # 2. Sales moments (AI markers)
    markers = []
    try:
        markers_sql = text("""
            SELECT time_start, time_end, moment_type, confidence,
                   product_name, description
            FROM sales_moments
            WHERE video_id = :video_id
            ORDER BY time_start ASC
        """)
        markers_result = await db.execute(markers_sql, {"video_id": video_id})
        markers_rows = markers_result.fetchall()
        for r in markers_rows:
            markers.append({
                "time_start": r.time_start,
                "time_end": r.time_end,
                "type": r.moment_type if hasattr(r, 'moment_type') else "sales",
                "confidence": r.confidence if hasattr(r, 'confidence') else None,
                "label": r.product_name if hasattr(r, 'product_name') else None,
                "description": r.description if hasattr(r, 'description') else None,
            })
    except Exception as e:
        logger.warning(f"[timeline] sales_moments query failed: {e}")
        try:
            await db.rollback()
        except Exception:
            pass

    # 3. Event scores
    event_scores = []
    try:
        scores_sql = text("""
            SELECT phase_index, ai_score, score_source, rank
            FROM event_scores
            WHERE video_id = :video_id
            ORDER BY phase_index ASC
        """)
        scores_result = await db.execute(scores_sql, {"video_id": video_id})
        scores_rows = scores_result.fetchall()
        for r in scores_rows:
            event_scores.append({
                "phase_index": r.phase_index,
                "ai_score": r.ai_score,
                "score_source": r.score_source,
                "rank": r.rank,
            })
    except Exception as e:
        logger.warning(f"[timeline] event_scores query failed: {e}")
        try:
            await db.rollback()
        except Exception:
            pass

    # 4. Segment feedback
    feedback = []
    try:
        fb_sql = text("""
            SELECT id, start_sec, end_sec, feedback_type, label, note
            FROM segment_feedback
            WHERE video_id = :video_id
            ORDER BY start_sec ASC
        """)
        fb_result = await db.execute(fb_sql, {"video_id": video_id})
        fb_rows = fb_result.fetchall()
        for r in fb_rows:
            feedback.append({
                "id": str(r.id),
                "start_sec": r.start_sec,
                "end_sec": r.end_sec,
                "feedback_type": r.feedback_type,
                "label": r.label,
                "note": r.note,
            })
    except Exception as e:
        logger.warning(f"[timeline] segment_feedback query failed: {e}")
        try:
            await db.rollback()
        except Exception:
            pass

    return {
        "phases": phases,
        "markers": markers,
        "event_scores": event_scores,
        "feedback": feedback,
        "phase_count": len(phases),
    }
