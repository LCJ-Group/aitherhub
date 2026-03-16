"""
clip_editor_v2.py — Intelligent Clip Editor v2 APIs

APIs for the new editor:
  ① Segment Scores:     GET  /api/v1/editor/{video_id}/segments
  ② Video Score:        GET  /api/v1/editor/{video_id}/score
  ③ Segment Feedback:   POST /api/v1/editor/{video_id}/segment-feedback
  ④ List Feedback:      GET  /api/v1/editor/{video_id}/segment-feedback
  ⑤ Timeline Data:      GET  /api/v1/editor/{video_id}/timeline
  ⑥ Transcribe Clip:    POST /api/v1/editor/{video_id}/transcribe
"""

import uuid
import json
import os
import logging
import tempfile
import asyncio
from typing import Optional
from datetime import datetime, timezone, timedelta

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


class TranscribeRequest(BaseModel):
    clip_url: str = Field(..., description="URL of the clip video to transcribe")
    time_start: float = Field(..., description="Clip start time in seconds (absolute)")
    time_end: float = Field(..., description="Clip end time in seconds (absolute)")
    phase_index: Optional[int] = Field(None, description="Phase index of the clip")


class SubtitleCaption(BaseModel):
    start: float
    end: float
    text: str
    words: Optional[list] = None


class ExportSubtitledClipRequest(BaseModel):
    clip_url: str = Field(..., description="URL of the source clip video")
    captions: list[SubtitleCaption] = Field(..., description="List of caption segments")
    style: str = Field(default="box", description="Subtitle style preset name")
    position_x: float = Field(default=50.0, description="Subtitle X position (0-100%)")
    position_y: float = Field(default=85.0, description="Subtitle Y position (0-100%)")
    time_start: float = Field(default=0.0, description="Clip start time for offset calculation")


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
    }
    if req.label and req.label not in valid_labels:
        raise HTTPException(
            status_code=422,
            detail=f"label must be one of: {sorted(valid_labels)}",
        )

    feedback_id = str(uuid.uuid4())

    sql = text("""
        INSERT INTO segment_feedback
            (id, video_id, segment_id, start_sec, end_sec, feedback_type, label, note)
        VALUES
            (:id, :video_id, :segment_id, :start_sec, :end_sec, :feedback_type, :label, :note)
    """)

    try:
        await db.execute(sql, {
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
    except Exception as e:
        logger.error(f"[segment_feedback] Insert failed: {e}")
        try:
            await db.rollback()
        except Exception as _e:
            logger.debug(f"Non-critical error suppressed: {_e}")
        raise HTTPException(status_code=500, detail="Failed to save feedback")

    return SegmentFeedbackResponse(
        id=feedback_id,
        video_id=video_id,
        start_sec=req.start_sec,
        end_sec=req.end_sec,
        feedback_type=req.feedback_type,
        label=req.label,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


# ─── ④ List Feedback ─────────────────────────────────────────────────────

@router.get("/{video_id}/segment-feedback")
async def list_segment_feedback(
    video_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    List all feedback for a video's segments.
    """
    try:
        uuid.UUID(video_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid video_id UUID")

    sql = text("""
        SELECT id, start_sec, end_sec, feedback_type, label, note, created_at
        FROM segment_feedback
        WHERE video_id = :video_id
        ORDER BY start_sec ASC
    """)

    try:
        result = await db.execute(sql, {"video_id": video_id})
        rows = result.fetchall()
    except Exception as e:
        logger.warning(f"[list_feedback] Table may not exist: {e}")
        return {"feedback": [], "count": 0}

    feedback = []
    for r in rows:
        feedback.append({
            "id": str(r.id),
            "start_sec": r.start_sec,
            "end_sec": r.end_sec,
            "feedback_type": r.feedback_type,
            "label": r.label,
            "note": r.note,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })

    return {"feedback": feedback, "count": len(feedback)}


# ─── ⑤ Timeline Data ─────────────────────────────────────────────────────

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
    - Transcripts (speech-to-text data)
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
        except Exception as _e:
            logger.debug(f"Non-critical error suppressed: {_e}")
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
            except Exception as _e:
                logger.debug(f"Non-critical error suppressed: {_e}")

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
        except Exception as _e:
            logger.debug(f"Non-critical error suppressed: {_e}")

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
        except Exception as _e:
            logger.debug(f"Non-critical error suppressed: {_e}")

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
        except Exception as _e:
            logger.debug(f"Non-critical error suppressed: {_e}")

    # 5. Transcripts (actual speech-to-text data for subtitles)
    transcripts = []
    transcript_source = "none"
    try:
        # First try: video_transcripts table (fine-grained Whisper segments)
        tr_sql = text("""
            SELECT segment_index, start_time, end_time, text, confidence
            FROM video_transcripts
            WHERE video_id = :video_id
            ORDER BY segment_index ASC
        """)
        tr_result = await db.execute(tr_sql, {"video_id": video_id})
        tr_rows = tr_result.fetchall()
        for r in tr_rows:
            transcripts.append({
                "start": r.start_time,
                "end": r.end_time,
                "text": r.text,
                "confidence": r.confidence,
            })
        if transcripts:
            transcript_source = "video_transcripts"
    except Exception as e:
        logger.warning(f"[timeline] video_transcripts query failed: {e}")
        try:
            await db.rollback()
        except Exception as _e:
            logger.debug(f"Non-critical error suppressed: {_e}")

    # Fallback: use audio_text from video_phases
    if not transcripts:
        try:
            at_sql = text("""
                SELECT phase_index, time_start, time_end, audio_text
                FROM video_phases
                WHERE video_id = :video_id
                  AND audio_text IS NOT NULL
                  AND audio_text != ''
                ORDER BY phase_index ASC
            """)
            at_result = await db.execute(at_sql, {"video_id": video_id})
            at_rows = at_result.fetchall()
            for r in at_rows:
                if r.audio_text and r.audio_text.strip():
                    transcripts.append({
                        "start": r.time_start,
                        "end": r.time_end,
                        "text": r.audio_text.strip(),
                        "confidence": None,
                    })
            if transcripts:
                transcript_source = "audio_text"
        except Exception as e:
            logger.warning(f"[timeline] audio_text fallback failed: {e}")
            try:
                await db.rollback()
            except Exception as _e:
                logger.debug(f"Non-critical error suppressed: {_e}")

    return {
        "phases": phases,
        "markers": markers,
        "event_scores": event_scores,
        "feedback": feedback,
        "transcripts": transcripts,
        "transcript_source": transcript_source,
        "phase_count": len(phases),
    }


# ─── ⑥ Transcribe Clip (On-demand Whisper) ──────────────────────────────

@router.post("/{video_id}/transcribe")
async def transcribe_clip(
    video_id: str,
    req: TranscribeRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    On-demand speech transcription for a clip.
    Downloads the clip video, sends to OpenAI Whisper API,
    saves results to video_transcripts table, and returns segments.
    """
    try:
        uuid.UUID(video_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid video_id UUID")

    logger.info(f"[transcribe] Starting transcription for video={video_id}, "
                f"time={req.time_start}-{req.time_end}")

    # Step 1: Download the clip video to a temp file
    import httpx
    tmp_dir = tempfile.mkdtemp(prefix="transcribe_")
    video_path = os.path.join(tmp_dir, "clip.mp4")

    try:
        download_url = req.clip_url

        # If clip_url has no SAS token, generate one on the backend
        if "?" not in download_url or "sig=" not in download_url:
            logger.info(f"[transcribe] clip_url has no SAS token, generating one")
            try:
                from app.services.storage_service import generate_read_sas_from_url
                sas_url = generate_read_sas_from_url(download_url, expires_hours=1)
                if sas_url:
                    download_url = sas_url
                    logger.info(f"[transcribe] Generated SAS download URL (expires in 1h)")
                else:
                    logger.warning(f"[transcribe] generate_read_sas_from_url returned None, trying original URL")
            except Exception as sas_err:
                logger.warning(f"[transcribe] Failed to generate SAS token: {sas_err}, trying original URL")

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.get(download_url)
            resp.raise_for_status()
            with open(video_path, "wb") as f:
                f.write(resp.content)
        logger.info(f"[transcribe] Downloaded clip: {os.path.getsize(video_path)} bytes")
    except Exception as e:
        logger.error(f"[transcribe] Failed to download clip: {e}")
        _cleanup_tmp(tmp_dir)
        raise HTTPException(status_code=400, detail=f"Failed to download clip video: {str(e)}")

    # Step 2: Extract audio and send to Azure OpenAI Whisper API
    try:
        import openai

        # Use Azure OpenAI Whisper (deployed as 'whisper' model)
        azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "https://aoai-kyogoku-service.openai.azure.com/")
        azure_key = os.getenv("AZURE_OPENAI_KEY", "")

        # Clean up endpoint URL - remove any path/query params, keep just base URL
        from urllib.parse import urlparse as _urlparse
        _parsed = _urlparse(azure_endpoint)
        clean_endpoint = f"{_parsed.scheme}://{_parsed.netloc}/"

        logger.info(f"[transcribe] Using Azure OpenAI endpoint: {clean_endpoint}")

        openai_client = openai.AsyncAzureOpenAI(
            api_key=azure_key,
            api_version="2024-06-01",
            azure_endpoint=clean_endpoint,
        )

        file_size = os.path.getsize(video_path)
        max_size = 25 * 1024 * 1024  # 25MB Whisper API limit
        logger.info(f"[transcribe] Video file size: {file_size} bytes ({file_size/1024/1024:.1f} MB, max: 25 MB)")

        async def _call_whisper(file_path: str) -> list:
            """Call Azure OpenAI Whisper and return segments."""
            fsize = os.path.getsize(file_path)
            logger.info(f"[transcribe] Sending to Whisper: {file_path} ({fsize/1024/1024:.1f} MB)")
            with open(file_path, "rb") as f:
                response = await openai_client.audio.transcriptions.create(
                    model="whisper",
                    file=f,
                    response_format="verbose_json",
                    language="ja",
                    timestamp_granularities=["segment", "word"],
                )

            segs = []
            # Extract word-level timestamps if available (for karaoke-style highlighting)
            words_list = []
            if hasattr(response, "words") and response.words:
                for w in response.words:
                    ws = getattr(w, "start", 0) if hasattr(w, "start") else w.get("start", 0)
                    we = getattr(w, "end", 0) if hasattr(w, "end") else w.get("end", 0)
                    wt = getattr(w, "word", "") if hasattr(w, "word") else w.get("word", "")
                    words_list.append({"start": float(ws), "end": float(we), "word": wt.strip()})
                logger.info(f"[transcribe] Got {len(words_list)} word-level timestamps")

            if hasattr(response, "segments") and response.segments:
                for seg in response.segments:
                    s = getattr(seg, "start", 0) if hasattr(seg, "start") else seg.get("start", 0)
                    e = getattr(seg, "end", 0) if hasattr(seg, "end") else seg.get("end", 0)
                    t = getattr(seg, "text", "") if hasattr(seg, "text") else seg.get("text", "")
                    # Attach word-level timestamps that fall within this segment
                    seg_words = [w for w in words_list if w["start"] >= float(s) and w["end"] <= float(e)]
                    seg_data = {"start": float(s), "end": float(e), "text": t.strip()}
                    if seg_words:
                        seg_data["words"] = seg_words
                    segs.append(seg_data)
            elif hasattr(response, "text") and response.text:
                duration = req.time_end - req.time_start
                seg_data = {"start": 0.0, "end": duration, "text": response.text.strip()}
                if words_list:
                    seg_data["words"] = words_list
                segs.append(seg_data)
            return segs

        # Always extract audio first to reduce file size (video track is not needed)
        # Use mp3 compression for much smaller files
        audio_path = os.path.join(tmp_dir, "audio.mp3")
        whisper_file = video_path  # fallback

        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", video_path,
                "-vn",  # no video
                "-acodec", "libmp3lame",  # mp3 encoding
                "-ar", "16000",  # 16kHz sample rate (optimal for Whisper)
                "-ac", "1",  # mono
                "-b:a", "64k",  # 64kbps bitrate (good enough for speech)
                audio_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0 and os.path.exists(audio_path):
                audio_size = os.path.getsize(audio_path)
                logger.info(f"[transcribe] Extracted audio: {audio_size/1024/1024:.1f} MB (from {file_size/1024/1024:.1f} MB video)")
                whisper_file = audio_path
            else:
                logger.warning(f"[transcribe] ffmpeg failed (rc={proc.returncode}), stderr: {stderr.decode()[:500]}")
        except FileNotFoundError:
            logger.warning("[transcribe] ffmpeg not found, trying WAV extraction with Python")
            # Fallback: try extracting audio with Python (moviepy or raw approach)
            try:
                wav_path = os.path.join(tmp_dir, "audio.wav")
                proc2 = await asyncio.create_subprocess_exec(
                    "python3", "-c",
                    f"from moviepy.editor import VideoFileClip; "
                    f"clip = VideoFileClip('{video_path}'); "
                    f"clip.audio.write_audiofile('{wav_path}', fps=16000, nbytes=2, codec='pcm_s16le'); "
                    f"clip.close()",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc2.communicate()
                if proc2.returncode == 0 and os.path.exists(wav_path):
                    whisper_file = wav_path
                    logger.info(f"[transcribe] Extracted WAV audio: {os.path.getsize(wav_path)/1024/1024:.1f} MB")
            except Exception as py_err:
                logger.warning(f"[transcribe] Python audio extraction failed: {py_err}")
        except Exception as ffmpeg_err:
            logger.warning(f"[transcribe] ffmpeg error: {ffmpeg_err}")

        # Check final file size
        final_size = os.path.getsize(whisper_file)
        if final_size > max_size:
            logger.warning(f"[transcribe] File still too large ({final_size/1024/1024:.1f} MB), Whisper may reject it")

        segments = await _call_whisper(whisper_file)
        logger.info(f"[transcribe] Got {len(segments)} raw segments from Azure OpenAI Whisper")

        # Deduplicate segments: Whisper sometimes returns near-duplicate segments
        # (same or very similar text within a short time window)
        if len(segments) > 1:
            deduped = [segments[0]]
            for seg in segments[1:]:
                prev = deduped[-1]
                # Skip if same text and start time is within 15 seconds of previous
                if seg["text"].strip() == prev["text"].strip() and abs(seg["start"] - prev["start"]) < 15:
                    logger.info(f"[transcribe] Removing duplicate segment: '{seg['text'][:40]}' at {seg['start']:.1f}s (dup of {prev['start']:.1f}s)")
                    continue
                deduped.append(seg)
            if len(deduped) < len(segments):
                logger.info(f"[transcribe] Deduplicated: {len(segments)} -> {len(deduped)} segments")
            segments = deduped

    except Exception as e:
        logger.error(f"[transcribe] Whisper API failed: {e}", exc_info=True)
        _cleanup_tmp(tmp_dir)
        raise HTTPException(status_code=500, detail=f"Whisper transcription failed: {str(e)}")

    # Step 3: Convert local times to absolute times and save to DB
    # Whisper returns times relative to the clip (0-based)
    # We need to store them as absolute times (offset by time_start)
    absolute_segments = []
    for i, seg in enumerate(segments):
        abs_start = req.time_start + seg["start"]
        abs_end = req.time_start + seg["end"]
        seg_data = {
            "segment_index": i,
            "start": abs_start,
            "end": abs_end,
            "local_start": seg["start"],
            "local_end": seg["end"],
            "text": seg["text"],
        }
        # Include word-level timestamps (for karaoke-style highlighting)
        if "words" in seg and seg["words"]:
            seg_data["words"] = [
                {"start": w["start"], "end": w["end"], "word": w["word"]}
                for w in seg["words"]
            ]
        absolute_segments.append(seg_data)

    # Save to video_transcripts table
    saved_count = 0
    try:
        # Check if video_transcripts table exists and has data for this range
        # Delete existing transcripts for this time range first
        del_sql = text("""
            DELETE FROM video_transcripts
            WHERE video_id = :video_id
              AND start_time >= :time_start
              AND end_time <= :time_end
        """)
        await db.execute(del_sql, {
            "video_id": video_id,
            "time_start": req.time_start - 1,
            "time_end": req.time_end + 1,
        })

        # Insert new transcripts
        for seg in absolute_segments:
            ins_sql = text("""
                INSERT INTO video_transcripts
                    (id, video_id, segment_index, start_time, end_time, text, confidence)
                VALUES
                    (:id, :video_id, :segment_index, :start_time, :end_time, :text, :confidence)
            """)
            await db.execute(ins_sql, {
                "id": str(uuid.uuid4()),
                "video_id": video_id,
                "segment_index": seg["segment_index"],
                "start_time": seg["start"],
                "end_time": seg["end"],
                "text": seg["text"],
                "confidence": 0.9,  # Whisper doesn't return per-segment confidence in this mode
            })
            saved_count += 1

        await db.commit()
        logger.info(f"[transcribe] Saved {saved_count} segments to video_transcripts")
    except Exception as e:
        logger.warning(f"[transcribe] Failed to save to video_transcripts: {e}")
        try:
            await db.rollback()
        except Exception as _e:
            logger.debug(f"Non-critical error suppressed: {_e}")
        # Still return the segments even if DB save fails

    # Also update video_clips captions if clip_id exists
    try:
        # Build captions array for video_clips
        captions_json = json.dumps([{
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["text"],
        } for seg in absolute_segments], ensure_ascii=False)

        # Find the clip in video_clips
        clip_sql = text("""
            SELECT id FROM video_clips
            WHERE video_id = :video_id
              AND time_start = :time_start
              AND time_end = :time_end
            LIMIT 1
        """)
        clip_result = await db.execute(clip_sql, {
            "video_id": video_id,
            "time_start": req.time_start,
            "time_end": req.time_end,
        })
        clip_row = clip_result.fetchone()
        if clip_row:
            update_sql = text("""
                UPDATE video_clips SET captions = :captions WHERE id = :id
            """)
            await db.execute(update_sql, {
                "captions": captions_json,
                "id": clip_row.id,
            })
            await db.commit()
            logger.info(f"[transcribe] Updated video_clips captions for clip {clip_row.id}")
    except Exception as e:
        logger.warning(f"[transcribe] Failed to update video_clips captions: {e}")
        try:
            await db.rollback()
        except Exception as _e:
            logger.debug(f"Non-critical error suppressed: {_e}")

    # Cleanup
    _cleanup_tmp(tmp_dir)

    return {
        "video_id": video_id,
        "segments": absolute_segments,
        "segment_count": len(absolute_segments),
        "time_start": req.time_start,
        "time_end": req.time_end,
        "source": "whisper",
    }


# ─── ⑦ Export Subtitled Clip ──────────────────────────────────────────────

# ASS subtitle style presets (matching frontend SUBTITLE_PRESETS)
_ASS_STYLES = {
    'simple': {
        'fontsize': 48, 'bold': 1, 'primary_color': '&H00FFFFFF',
        'outline_color': '&H00000000', 'outline': 2, 'shadow': 3,
        'border_style': 1, 'back_color': '&H00000000',
    },
    'box': {
        'fontsize': 48, 'bold': 1, 'primary_color': '&H00FFFFFF',
        'outline_color': '&H00000000', 'outline': 0, 'shadow': 0,
        'border_style': 3, 'back_color': '&HCC000000',
    },
    'outline': {
        'fontsize': 50, 'bold': 1, 'primary_color': '&H00FFFFFF',
        'outline_color': '&H00000000', 'outline': 4, 'shadow': 0,
        'border_style': 1, 'back_color': '&H00000000',
    },
    'pop': {
        'fontsize': 54, 'bold': 1, 'primary_color': '&H0035E1FF',
        'outline_color': '&H00356BFF', 'outline': 3, 'shadow': 3,
        'border_style': 1, 'back_color': '&H00000000',
    },
    'gradient': {
        'fontsize': 48, 'bold': 1, 'primary_color': '&H00FFFFFF',
        'outline_color': '&H00000000', 'outline': 0, 'shadow': 0,
        'border_style': 3, 'back_color': '&HAA8B5CF6',
    },
    'karaoke': {
        'fontsize': 50, 'bold': 1, 'primary_color': '&H8AFFFFFF',
        'outline_color': '&H00000000', 'outline': 0, 'shadow': 0,
        'border_style': 3, 'back_color': '&HB3000000',
        'secondary_color': '&H0035E1FF',  # karaoke highlight color
    },
}


def _seconds_to_ass_time(seconds: float) -> str:
    """Convert seconds to ASS time format: H:MM:SS.cc"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _seconds_to_srt_time(seconds: float) -> str:
    """Convert seconds to SRT time format: HH:MM:SS,mmm"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _generate_srt_content(captions: list, time_offset: float = 0) -> str:
    """Generate SRT subtitle file content from captions."""
    srt = ""
    idx = 1
    for cap in captions:
        local_start = cap.start - time_offset if time_offset > 0 else cap.start
        local_end = cap.end - time_offset if time_offset > 0 else cap.end
        if local_start < 0:
            local_start = 0
        if local_end <= local_start:
            continue
        start_ts = _seconds_to_srt_time(local_start)
        end_ts = _seconds_to_srt_time(local_end)
        text = cap.text.replace('\n', '\n')  # preserve newlines
        srt += f"{idx}\n{start_ts} --> {end_ts}\n{text}\n\n"
        idx += 1
    return srt


def _generate_ass_content(captions: list, style: str, position_x: float, position_y: float, time_offset: float = 0) -> str:
    """Generate ASS subtitle file content."""
    s = _ASS_STYLES.get(style, _ASS_STYLES['box'])
    is_karaoke = style == 'karaoke'

    # Calculate ASS position (\pos tag)
    # ASS uses pixel coordinates, but we'll use \an (alignment) + \pos for percentage-based
    # Map position_y to alignment: top(\an8), middle(\an5), bottom(\an2)
    if position_y < 33:
        alignment = 8  # top-center
    elif position_y < 66:
        alignment = 5  # middle-center
    else:
        alignment = 2  # bottom-center

    ass = "[Script Info]\n"
    ass += "ScriptType: v4.00+\n"
    ass += "PlayResX: 1080\n"
    ass += "PlayResY: 1920\n"
    ass += "WrapStyle: 0\n\n"
    ass += "[V4+ Styles]\n"
    ass += "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"

    secondary = s.get('secondary_color', '&H0000FFFF')
    ass += f"Style: Default,Noto Sans JP,{s['fontsize']},{s['primary_color']},{secondary},{s['outline_color']},{s['back_color']},{s['bold']},0,0,0,100,100,0,0,{s['border_style']},{s['outline']},{s['shadow']},{alignment},20,20,30,1\n\n"

    ass += "[Events]\n"
    ass += "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"

    for cap in captions:
        # Calculate local time (relative to clip start)
        local_start = cap.start - time_offset if time_offset > 0 else cap.start
        local_end = cap.end - time_offset if time_offset > 0 else cap.end
        if local_start < 0:
            local_start = 0
        if local_end <= local_start:
            continue

        start_ts = _seconds_to_ass_time(local_start)
        end_ts = _seconds_to_ass_time(local_end)

        if is_karaoke and cap.words:
            # Generate karaoke timing tags (\kf for smooth fill)
            karaoke_text = ""
            for w in cap.words:
                w_start = w.get('start', 0) - time_offset if time_offset > 0 else w.get('start', 0)
                w_end = w.get('end', 0) - time_offset if time_offset > 0 else w.get('end', 0)
                duration_cs = max(1, int((w_end - w_start) * 100))
                karaoke_text += f"{{\\kf{duration_cs}}}{w.get('word', '')}"
            ass += f"Dialogue: 0,{start_ts},{end_ts},Default,,0,0,0,,{karaoke_text}\n"
        else:
            # Escape ASS special characters
            text = cap.text.replace('\n', '\\N')
            ass += f"Dialogue: 0,{start_ts},{end_ts},Default,,0,0,0,,{text}\n"

    return ass


# ─── File-based export job store (survives worker recycle) ────────────────────
_EXPORT_JOB_DIR = os.path.join(tempfile.gettempdir(), "aitherhub_export_jobs")
os.makedirs(_EXPORT_JOB_DIR, exist_ok=True)

def _save_job(job_id: str, data: dict):
    """Persist job state to a JSON file."""
    path = os.path.join(_EXPORT_JOB_DIR, f"{job_id}.json")
    with open(path, "w") as f:
        json.dump(data, f)

def _load_job(job_id: str) -> dict | None:
    """Load job state from file."""
    path = os.path.join(_EXPORT_JOB_DIR, f"{job_id}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None

def _update_job(job_id: str, **kwargs):
    """Update specific fields in a job."""
    data = _load_job(job_id) or {}
    data.update(kwargs)
    _save_job(job_id, data)


async def _run_export_job(job_id: str, video_id: str, clip_url: str, captions, style: str,
                          position_x: float, position_y: float, time_start: float):
    """Background task: download clip, burn subtitles, upload result."""
    import shutil
    import functools
    from azure.storage.blob import BlobServiceClient, ContentSettings
    from app.services.storage_service import (
        CONNECTION_STRING, ACCOUNT_NAME, CONTAINER_NAME,
        generate_read_sas_from_url,
    )
    from urllib.parse import unquote

    _CDN_HOST = os.getenv("CDN_HOST", "https://cdn.aitherhub.com")
    _BLOB_HOST = f"https://{ACCOUNT_NAME}.blob.core.windows.net" if ACCOUNT_NAME else ""
    FFMPEG_TIMEOUT = 600  # 10 minutes max for encoding

    tmp_dir = tempfile.mkdtemp(prefix="export_sub_")
    try:
        # ── Step 1: Download clip from Azure Blob ──
        _update_job(job_id, status="downloading")
        video_path = os.path.join(tmp_dir, "source.mp4")

        # Extract blob_name from clip_url (CDN or Blob URL)
        url_path = clip_url
        if _CDN_HOST and url_path.startswith(_CDN_HOST):
            url_path = url_path[len(_CDN_HOST):]
        elif f"blob.core.windows.net/{CONTAINER_NAME}" in url_path:
            url_path = url_path.split(f"/{CONTAINER_NAME}", 1)[-1]
        url_path = url_path.lstrip("/")
        if url_path.startswith(f"{CONTAINER_NAME}/"):
            url_path = url_path[len(CONTAINER_NAME) + 1:]
        if "?" in url_path:
            url_path = url_path.split("?", 1)[0]
        blob_name = unquote(url_path)
        logger.info(f"[export-job {job_id}] Downloading blob: {blob_name}")

        # Download using BlobServiceClient (wrapped in thread to avoid blocking event loop)
        def _download_blob():
            blob_service = BlobServiceClient.from_connection_string(CONNECTION_STRING)
            blob_client = blob_service.get_blob_client(container=CONTAINER_NAME, blob=blob_name)
            with open(video_path, "wb") as f:
                download_stream = blob_client.download_blob()
                f.write(download_stream.readall())

        await asyncio.get_event_loop().run_in_executor(None, _download_blob)
        file_size = os.path.getsize(video_path)
        logger.info(f"[export-job {job_id}] Downloaded: {file_size/1024/1024:.1f} MB")

        # ── Step 2: Generate ASS subtitle file ──
        _update_job(job_id, status="encoding")

        # Normalize position_y: if value is 0-1 (ratio), convert to 0-100 (percent)
        pos_y_pct = position_y * 100 if position_y <= 1.0 else position_y

        ass_path = os.path.join(tmp_dir, "subtitles.ass")
        ass_content = _generate_ass_content(captions, style, position_x, pos_y_pct, time_start)
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(ass_content)
        logger.info(f"[export-job {job_id}] Generated ASS with {len(captions)} captions, style={style}, pos_y={pos_y_pct}")
        logger.info(f"[export-job {job_id}] ASS content preview: {ass_content[:300]}")

        # ── Step 3: Burn subtitles with ffmpeg ──
        output_path = os.path.join(tmp_dir, "output_subtitled.mp4")

        # Build subtitle filter using ASS file directly (more reliable than SRT+force_style)
        ass_escaped = ass_path.replace('\\', '/').replace(':', '\\:')

        # Check available fonts on the system
        try:
            fc_proc = await asyncio.create_subprocess_exec(
                "fc-list", ":lang=ja",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            fc_out, _ = await asyncio.wait_for(fc_proc.communicate(), timeout=10)
            font_list = fc_out.decode(errors='replace')[:500]
            logger.info(f"[export-job {job_id}] Available JP fonts: {font_list}")
        except Exception as font_err:
            logger.warning(f"[export-job {job_id}] Could not list fonts: {font_err}")

        # Use ass filter (not subtitles) for direct ASS rendering
        vf = f"ass={ass_escaped}"

        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-threads", "2",
            "-c:a", "copy",
            "-movflags", "+faststart",
            output_path,
        ]
        logger.info(f"[export-job {job_id}] Running ffmpeg cmd: {' '.join(cmd)}")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=FFMPEG_TIMEOUT
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.error(f"[export-job {job_id}] ffmpeg timed out after {FFMPEG_TIMEOUT}s")
            _update_job(job_id, status="failed", error=f"Encoding timed out ({FFMPEG_TIMEOUT}s)")
            return

        ffmpeg_stderr = stderr.decode(errors='replace')
        logger.info(f"[export-job {job_id}] ffmpeg stderr (last 500): {ffmpeg_stderr[-500:]}")

        if proc.returncode != 0:
            err_msg = ffmpeg_stderr[:500]
            logger.error(f"[export-job {job_id}] ffmpeg failed (rc={proc.returncode}): {err_msg}")
            _update_job(job_id, status="failed", error=f"ffmpeg error: {err_msg[:200]}")
            return

        output_size = os.path.getsize(output_path)
        logger.info(f"[export-job {job_id}] Encoded: {output_size/1024/1024:.1f} MB (source was {file_size/1024/1024:.1f} MB)")

        # ── Step 4: Upload to Azure Blob ──
        _update_job(job_id, status="uploading")
        if not CONNECTION_STRING:
            _update_job(job_id, status="failed", error="Azure storage not configured")
            return

        upload_blob_name = f"exports/{video_id}/subtitled_{uuid.uuid4().hex[:8]}.mp4"

        def _upload_blob():
            svc = BlobServiceClient.from_connection_string(CONNECTION_STRING)
            bc = svc.get_blob_client(container=CONTAINER_NAME, blob=upload_blob_name)
            with open(output_path, "rb") as data:
                bc.upload_blob(data, overwrite=True,
                               content_settings=ContentSettings(content_type="video/mp4"))

        await asyncio.get_event_loop().run_in_executor(None, _upload_blob)
        logger.info(f"[export-job {job_id}] Uploaded: {upload_blob_name}")

        blob_url = f"https://{ACCOUNT_NAME}.blob.core.windows.net/{CONTAINER_NAME}/{upload_blob_name}"
        download_url = generate_read_sas_from_url(blob_url, expires_hours=72)
        if not download_url:
            download_url = blob_url
        if _BLOB_HOST and _CDN_HOST:
            download_url = download_url.replace(_BLOB_HOST, _CDN_HOST)

        _update_job(job_id, status="done", download_url=download_url, file_size=output_size)
        logger.info(f"[export-job {job_id}] Complete! URL: {download_url[:80]}...")

    except Exception as e:
        logger.error(f"[export-job {job_id}] Failed: {e}", exc_info=True)
        _update_job(job_id, status="failed", error=str(e)[:300])
    finally:
        _cleanup_tmp(tmp_dir)


@router.post("/{video_id}/export-subtitled")
async def export_subtitled_clip(
    video_id: str,
    req: ExportSubtitledClipRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Start a background export job. Returns job_id immediately.
    Poll GET /{video_id}/export-subtitled/{job_id} for status.
    """
    try:
        uuid.UUID(video_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid video_id")

    if not req.captions:
        raise HTTPException(status_code=400, detail="No captions provided")

    job_id = uuid.uuid4().hex[:12]
    _save_job(job_id, {
        "status": "queued",
        "video_id": video_id,
        "style": req.style,
        "caption_count": len(req.captions),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    # Launch background task
    asyncio.create_task(_run_export_job(
        job_id, video_id, req.clip_url, req.captions,
        req.style, req.position_x, req.position_y, req.time_start,
    ))

    return {
        "job_id": job_id,
        "status": "queued",
        "video_id": video_id,
    }


@router.get("/{video_id}/export-subtitled/{job_id}")
async def get_export_status(video_id: str, job_id: str):
    """
    Poll export job status. Returns download_url when done.
    """
    job = _load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Export job not found")

    result = {
        "job_id": job_id,
        "status": job["status"],
        "video_id": video_id,
    }
    if job["status"] == "done":
        result["download_url"] = job.get("download_url")
        result["file_size"] = job.get("file_size")
    elif job["status"] == "failed":
        result["error"] = job.get("error", "Unknown error")

    return result


def _cleanup_tmp(tmp_dir: str):
    """Clean up temporary files."""
    try:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception as _e:
        logger.debug(f"Non-critical error suppressed: {_e}")
