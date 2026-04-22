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
    target_language: Optional[str] = Field("ja", description="Target language for transcription: 'ja' (Japanese), 'zh-TW' (Traditional Chinese), 'zh' (Simplified Chinese), 'auto' (original language auto-detect)")


class SubtitleCaption(BaseModel):
    start: float
    end: float
    text: str
    words: Optional[list] = None


class SplitSegment(BaseModel):
    start: float = Field(..., description="Segment start time in seconds (local to clip)")
    end: float = Field(..., description="Segment end time in seconds (local to clip)")
    enabled: bool = Field(default=True, description="Whether this segment is included in export")


class ExportSubtitledClipRequest(BaseModel):
    clip_url: str = Field(..., description="URL of the source clip video")
    captions: list[SubtitleCaption] = Field(..., description="List of caption segments")
    style: str = Field(default="box", description="Subtitle style preset name")
    position_x: float = Field(default=50.0, description="Subtitle X position (0-100%)")
    position_y: float = Field(default=85.0, description="Subtitle Y position (0-100%)")
    time_start: float = Field(default=0.0, description="Clip start time for offset calculation")
    split_segments: Optional[list[SplitSegment]] = Field(default=None, description="Split segments for selective export. If provided, only enabled segments are included.")


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

    # Determine Whisper language based on target_language
    target_lang = (req.target_language or "ja").strip().lower()
    
    # 'auto' mode: let Whisper auto-detect the original language (no translation)
    is_auto_detect = target_lang == "auto"
    
    # Map target language to Whisper language parameter
    whisper_lang_map = {
        "ja": "ja",        # Japanese output (Whisper translates Chinese audio to Japanese)
        "zh-tw": "zh",    # Traditional Chinese: first transcribe as Chinese, then convert
        "zh": "zh",       # Simplified Chinese
    }
    
    if is_auto_detect:
        whisper_language = None  # None = Whisper auto-detects the language
    else:
        whisper_language = whisper_lang_map.get(target_lang, "ja")
    needs_traditional_chinese = target_lang == "zh-tw"  # Only for explicit zh-TW selection

    logger.info(f"[transcribe] Starting transcription for video={video_id}, "
                f"time={req.time_start}-{req.time_end}, target_lang={target_lang}, "
                f"whisper_lang={whisper_language}, auto_detect={is_auto_detect}, needs_trad_zh={needs_traditional_chinese}")

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

        # Whisper prompt hints to improve recognition accuracy for domain-specific terms
        whisper_prompts = {
            "zh": (
                "全頭漂、漂髮、染髮、護髮、角蛋白、胺基酸、膠原蛋白、"
                "頭皮、毛囊、毛髮、髮質、受損、修復、保養、洗髮精、"
                "潤髮乳、護髮素、髮膜、KYOGOKU、京極、"
                "直播、下單、加購、優惠、限時、蝦皮、立即購買、"
                "漂白、退色、補色、挑染、片染、全頭染、"
                "沙龍、美髮師、設計師、造型師"
            ),
            "ja": (
                "全頭ブリーチ、カラーリング、ヘアケア、ケラチン、アミノ酸、コラーゲン、"
                "頭皮、毛穴、髪質、ダメージ、補修、シャンプー、トリートメント、"
                "ヘアマスク、KYOGOKU、京極、"
                "ライブ配信、ライブコマース、蝦皮、Shopee、"
                "ブリーチ、リタッチ、ハイライト、サロン、美容師、スタイリスト"
            ),
        }
        whisper_prompt = whisper_prompts.get(whisper_language, "") if whisper_language else ""

        async def _call_whisper(file_path: str) -> list:
            """Call Azure OpenAI Whisper and return segments."""
            fsize = os.path.getsize(file_path)
            logger.info(f"[transcribe] Sending to Whisper: {file_path} ({fsize/1024/1024:.1f} MB), prompt_lang={whisper_language}, auto_detect={is_auto_detect}")
            with open(file_path, "rb") as f:
                whisper_kwargs = dict(
                    model="whisper",
                    file=f,
                    response_format="verbose_json",
                    timestamp_granularities=["segment", "word"],
                )
                # For auto-detect mode, omit language param so Whisper detects it
                if whisper_language is not None:
                    whisper_kwargs["language"] = whisper_language
                if whisper_prompt:
                    whisper_kwargs["prompt"] = whisper_prompt
                response = await openai_client.audio.transcriptions.create(**whisper_kwargs)

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

        # ─── Split long segments into readable subtitle chunks ───
        # Whisper segment-level output often produces very long segments (30s+, 100+ chars)
        # that are unreadable as subtitles. Split them into 8-15 char chunks using
        # word-level timestamps when available, falling back to character-proportional splitting.
        MAX_CHARS_PER_LINE = 15  # Maximum characters per subtitle line
        MIN_CHARS_PER_LINE = 4   # Minimum characters (avoid tiny fragments)
        MAX_DURATION_PER_LINE = 4.0  # Maximum seconds per subtitle line

        split_segments = []
        for seg in segments:
            text_val = seg.get("text", "").strip()
            seg_start = seg.get("start", 0)
            seg_end = seg.get("end", 0)
            seg_words = seg.get("words", [])
            seg_duration = seg_end - seg_start

            # If segment is already short enough, keep as-is
            if len(text_val) <= MAX_CHARS_PER_LINE and seg_duration <= MAX_DURATION_PER_LINE:
                split_segments.append(seg)
                continue

            # Strategy 1: Use word-level timestamps to split at natural boundaries
            if seg_words:
                current_text = ""
                current_words = []
                current_start = None

                for w in seg_words:
                    w_text = w.get("word", "").strip()
                    if not w_text:
                        continue
                    w_start = w.get("start", 0)
                    w_end = w.get("end", 0)

                    if current_start is None:
                        current_start = w_start

                    test_text = current_text + w_text
                    test_duration = w_end - current_start

                    # Break if adding this word would exceed limits
                    if (len(test_text) > MAX_CHARS_PER_LINE or test_duration > MAX_DURATION_PER_LINE) and len(current_text) >= MIN_CHARS_PER_LINE:
                        split_segments.append({
                            "start": round(current_start, 3),
                            "end": round(current_words[-1]["end"], 3),
                            "text": current_text,
                            "words": current_words[:],
                        })
                        current_text = w_text
                        current_words = [w]
                        current_start = w_start
                    else:
                        current_text = test_text
                        current_words.append(w)

                # Flush remaining
                if current_text and current_words:
                    # If remaining is too short, merge with previous
                    if len(current_text) < MIN_CHARS_PER_LINE and split_segments:
                        prev = split_segments[-1]
                        prev["text"] += current_text
                        prev["end"] = round(current_words[-1]["end"], 3)
                        prev["words"] = prev.get("words", []) + current_words
                    else:
                        split_segments.append({
                            "start": round(current_start, 3),
                            "end": round(current_words[-1]["end"], 3),
                            "text": current_text,
                            "words": current_words[:],
                        })
            else:
                # Strategy 2: No word timestamps - split by character count proportionally
                chars = list(text_val)
                total_chars = len(chars)
                num_chunks = max(1, -(-total_chars // MAX_CHARS_PER_LINE))  # ceil division
                chars_per_chunk = max(MIN_CHARS_PER_LINE, -(-total_chars // num_chunks))

                for c_idx in range(0, total_chars, chars_per_chunk):
                    chunk_text = "".join(chars[c_idx:c_idx + chars_per_chunk])
                    if not chunk_text.strip():
                        continue
                    ratio_start = c_idx / total_chars
                    ratio_end = min((c_idx + len(chunk_text)) / total_chars, 1.0)
                    chunk_start = seg_start + seg_duration * ratio_start
                    chunk_end = seg_start + seg_duration * ratio_end
                    split_segments.append({
                        "start": round(chunk_start, 3),
                        "end": round(chunk_end, 3),
                        "text": chunk_text,
                    })

        if len(split_segments) != len(segments):
            logger.info(f"[transcribe] Split long segments: {len(segments)} -> {len(split_segments)} segments")
        segments = split_segments

        # ─── Traditional Chinese conversion + post-processing ────────────
        # If target language is zh-TW:
        # 1. Fix Whisper misrecognitions (especially beauty/haircare terms)
        # 2. Convert Simplified Chinese to Traditional Chinese (Taiwan usage)
        # 3. Make subtitles more natural and human-like
        if needs_traditional_chinese and segments:
            try:
                logger.info(f"[transcribe] Converting {len(segments)} segments to Traditional Chinese with post-processing")
                # Batch all texts for efficient conversion
                all_texts = [seg.get("text", "") for seg in segments]
                batch_text = "\n".join([f"{i}|{t}" for i, t in enumerate(all_texts)])

                gpt_client = openai.AsyncAzureOpenAI(
                    api_key=azure_key,
                    api_version="2024-06-01",
                    azure_endpoint=clean_endpoint,
                )
                gpt_response = await gpt_client.chat.completions.create(
                    model=os.getenv("AZURE_OPENAI_GPT_DEPLOYMENT", "gpt-4.1-mini"),
                    messages=[
                        {"role": "system", "content": (
                            "你是一位專業的繁體中文字幕校對與翻譯助手，專精於美容、護髮、直播帶貨領域。\n\n"
                            "你的任務有三個：\n"
                            "1. **修正語音辨識錯誤**：Whisper語音辨識經常把中文美容/護髮專業術語辨識錯誤，"
                            "你必須根據上下文修正這些錯誤。常見的錯誤包括：\n"
                            "   - 「拳頭嫖」→「全頭漂」（全頭漂髮/漂白）\n"
                            "   - 「毛囊」可能是正確的，但要根據上下文判斷\n"
                            "   - 角蛋白、胺基酸、膠原蛋白等護髮成分名稱要正確\n"
                            "   - 品牌名「KYOGOKU」「京極」要保留原文\n"
                            "   - 直播用語如「下單」「加購」「優惠」「限時」等要正確\n"
                            "   - 同音字錯誤要根據美容護髮的語境來修正\n\n"
                            "2. **轉換為台灣繁體中文**：使用台灣慣用的詞彙和用語，不是港式繁體。\n\n"
                            "3. **讓字幕更自然**：保持口語化但清晰易讀，適當修正語句使其更通順，"
                            "但不要改變原意或添加原文沒有的內容。\n\n"
                            "格式規則：\n"
                            "- 每行格式為 '序號|文字'，只替換文字部分\n"
                            "- 不要添加任何解釋，只輸出轉換後的結果\n"
                            "- 保持原有的行數和序號不變"
                        )},
                        {"role": "user", "content": batch_text},
                    ],
                    temperature=0.2,
                    max_tokens=4000,
                )
                converted_text = gpt_response.choices[0].message.content.strip()
                converted_lines = converted_text.split("\n")

                # Parse converted lines back to segments
                converted_map = {}
                for line in converted_lines:
                    if "|" in line:
                        parts = line.split("|", 1)
                        try:
                            idx = int(parts[0].strip())
                            converted_map[idx] = parts[1].strip()
                        except (ValueError, IndexError):
                            continue

                # Apply converted text to segments
                converted_count = 0
                for i, seg in enumerate(segments):
                    if i in converted_map and converted_map[i]:
                        seg["text"] = converted_map[i]
                        converted_count += 1
                        # Also update word-level text if available
                        if seg.get("words"):
                            # Word-level timestamps can't be easily converted,
                            # so remove them to avoid mismatch
                            del seg["words"]

                logger.info(f"[transcribe] Converted {converted_count}/{len(segments)} segments to Traditional Chinese")
            except Exception as trad_err:
                logger.warning(f"[transcribe] Traditional Chinese conversion failed: {trad_err}, keeping Simplified")

    except Exception as e:
        logger.error(f"[transcribe] Whisper API failed: {e}", exc_info=True)
        _cleanup_tmp(tmp_dir)
        raise HTTPException(status_code=500, detail=f"Whisper transcription failed: {str(e)}")

    # Step 3: Build both absolute and local segment lists
    # Whisper returns times relative to the clip (0-based = "local")
    # video_transcripts table needs absolute times (offset by time_start)
    # video_clips.captions and API response should use LOCAL times (0-based)
    # so the frontend can directly match them to clip video playback (currentTime is 0-based)
    absolute_segments = []  # For video_transcripts DB table
    local_segments = []     # For video_clips.captions and API response
    for i, seg in enumerate(segments):
        abs_start = req.time_start + seg["start"]
        abs_end = req.time_start + seg["end"]

        # Word-level timestamps (already local/0-based from Whisper)
        local_words = []
        if "words" in seg and seg["words"]:
            local_words = [
                {"start": round(w["start"], 3), "end": round(w["end"], 3), "word": w["word"]}
                for w in seg["words"]
            ]

        # Absolute segment (for video_transcripts)
        abs_seg = {
            "segment_index": i,
            "start": abs_start,
            "end": abs_end,
            "local_start": seg["start"],
            "local_end": seg["end"],
            "text": seg["text"],
        }
        if local_words:
            abs_seg["words"] = [{"start": req.time_start + w["start"], "end": req.time_start + w["end"], "word": w["word"]} for w in local_words]
        absolute_segments.append(abs_seg)

        # Local segment (for video_clips.captions and API response)
        local_seg = {
            "segment_index": i,
            "start": round(seg["start"], 3),
            "end": round(seg["end"], 3),
            "text": seg["text"],
            "source": "whisper",
        }
        if local_words:
            local_seg["words"] = local_words
        local_segments.append(local_seg)

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
    # IMPORTANT: Save LOCAL times (0-based) to video_clips.captions
    # because the frontend plays clip_url where currentTime is 0-based
    try:
        # Build captions array for video_clips using LOCAL times
        captions_json = json.dumps([{
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["text"],
            "source": "whisper",
            "language": req.target_language or "ja",
            **({
                "words": seg["words"]
            } if seg.get("words") else {}),
        } for seg in local_segments], ensure_ascii=False)

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
            # Build transcript_text from segments for clip-db display
            transcript_text = " ".join(
                seg["text"] for seg in local_segments if seg.get("text")
            ).strip()[:500] or None

            update_sql = text("""
                UPDATE video_clips
                SET captions = CAST(:captions AS jsonb),
                    transcript_text = COALESCE(:transcript_text, transcript_text),
                    updated_at = NOW()
                WHERE id = :id
            """)
            await db.execute(update_sql, {
                "captions": captions_json,
                "transcript_text": transcript_text,
                "id": clip_row.id,
            })
            await db.commit()
            logger.info(f"[transcribe] Updated video_clips captions + transcript_text for clip {clip_row.id}")
    except Exception as e:
        logger.warning(f"[transcribe] Failed to update video_clips captions: {e}")
        try:
            await db.rollback()
        except Exception as _e:
            logger.debug(f"Non-critical error suppressed: {_e}")

    # Cleanup
    _cleanup_tmp(tmp_dir)

    # Return LOCAL time segments to frontend
    # Frontend plays clip_url where currentTime is 0-based,
    # so captions must also be 0-based for correct sync
    return {
        "video_id": video_id,
        "segments": local_segments,
        "segment_count": len(local_segments),
        "time_start": req.time_start,
        "time_end": req.time_end,
        "source": "whisper",
    }


# ─── ⑦ Export Subtitled Clip ──────────────────────────────────────────────

# ASS subtitle style presets (matching frontend SUBTITLE_PRESETS)
# ASS color format: &HAABBGGRR  (AA: 00=opaque, FF=transparent — opposite of CSS)
_ASS_STYLES = {
    'simple': {
        # CSS: white text, text-shadow for depth
        'fontsize': 48, 'bold': 1, 'primary_color': '&H00FFFFFF',
        'outline_color': '&H00000000', 'outline': 3, 'shadow': 2,
        'border_style': 1, 'back_color': '&H80000000',
    },
    'box': {
        # CSS: white text, rgba(0,0,0,0.80) background box
        # ASS alpha = (1-0.80)*255 = 51 = 0x33
        # Outline acts as padding when BorderStyle=3
        'fontsize': 48, 'bold': 1, 'primary_color': '&H00FFFFFF',
        'outline_color': '&H00000000', 'outline': 14, 'shadow': 0,
        'border_style': 3, 'back_color': '&H33000000',
    },
    'outline': {
        # CSS: white text, thick black stroke
        'fontsize': 50, 'bold': 1, 'primary_color': '&H00FFFFFF',
        'outline_color': '&H00000000', 'outline': 5, 'shadow': 0,
        'border_style': 1, 'back_color': '&H00000000',
    },
    'pop': {
        # CSS: #FFE135 text (yellow), #FF6B35 stroke (orange)
        # ASS BGR: FFE135 -> R=FF,G=E1,B=35 -> &H0035E1FF
        # ASS BGR: FF6B35 -> R=FF,G=6B,B=35 -> &H00356BFF
        'fontsize': 54, 'bold': 1, 'primary_color': '&H0035E1FF',
        'outline_color': '&H00356BFF', 'outline': 4, 'shadow': 3,
        'border_style': 1, 'back_color': '&H70000000',
    },
    'gradient': {
        # CSS: white text, linear-gradient(135deg, rgba(139,92,246,0.85), rgba(236,72,153,0.85)) background
        # Use purple midpoint: rgba(187,82,200,0.85) -> ASS alpha=(1-0.85)*255=38=0x26
        # ASS BGR: BB52C8 -> R=BB,G=52,B=C8 -> &H26C852BB
        # Outline acts as padding when BorderStyle=3
        'fontsize': 48, 'bold': 1, 'primary_color': '&H00FFFFFF',
        'outline_color': '&H00000000', 'outline': 14, 'shadow': 0,
        'border_style': 3, 'back_color': '&H26C852BB',
    },
    'karaoke': {
        # CSS: rgba(255,255,255,0.5) text, rgba(0,0,0,0.70) background, #FFE135 highlight
        # Primary alpha = (1-0.5)*255 = 127 = 0x7F
        # Back alpha = (1-0.70)*255 = 76 = 0x4C
        # Outline acts as padding when BorderStyle=3
        'fontsize': 50, 'bold': 1, 'primary_color': '&H7FFFFFFF',
        'outline_color': '&H00000000', 'outline': 14, 'shadow': 0,
        'border_style': 3, 'back_color': '&H4C000000',
        'secondary_color': '&H0035E1FF',  # karaoke highlight color (yellow in BGR)
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


def _cap_get(cap, field, default=None):
    """Access caption field from dict or object."""
    if isinstance(cap, dict):
        return cap.get(field, default)
    return getattr(cap, field, default)


def _generate_srt_content(captions: list, time_offset: float = 0) -> str:
    """Generate SRT subtitle file content from captions."""
    srt = ""
    idx = 1
    for cap in captions:
        cap_start = _cap_get(cap, 'start', 0)
        cap_end = _cap_get(cap, 'end', 0)
        cap_text = _cap_get(cap, 'text', '')
        local_start = cap_start - time_offset if time_offset > 0 else cap_start
        local_end = cap_end - time_offset if time_offset > 0 else cap_end
        if local_start < 0:
            local_start = 0
        if local_end <= local_start:
            continue
        start_ts = _seconds_to_srt_time(local_start)
        end_ts = _seconds_to_srt_time(local_end)
        text = cap_text.replace('\n', '\n')  # preserve newlines
        srt += f"{idx}\n{start_ts} --> {end_ts}\n{text}\n\n"
        idx += 1
    return srt


def _generate_ass_content(captions: list, style: str, position_x: float, position_y: float,
                          time_offset: float = 0, video_width: int = 1080, video_height: int = 1920) -> str:
    """Generate ASS subtitle file content matching frontend preview styles.
    
    Uses auto-detection for local vs absolute caption times (same logic as drawtext).
    Supports all 6 frontend styles: simple, box, outline, pop, gradient, karaoke.
    """
    s = _ASS_STYLES.get(style, _ASS_STYLES['box'])
    is_karaoke = style == 'karaoke'

    # ── Auto-detect local vs absolute caption times ──
    if time_offset > 0 and captions:
        max_start = max(float(_cap_get(c, 'start', 0)) for c in captions)
        if max_start < time_offset:
            logger.info(f"[ass] Captions are LOCAL times "
                        f"(max_start={max_start:.2f} < time_offset={time_offset:.2f}), "
                        f"skipping offset subtraction")
            effective_offset = 0
        else:
            logger.info(f"[ass] Captions are ABSOLUTE times "
                        f"(max_start={max_start:.2f} >= time_offset={time_offset:.2f}), "
                        f"subtracting offset")
            effective_offset = time_offset
    else:
        effective_offset = 0

    # ── Calculate position ──
    # Map position_y percentage to ASS alignment + MarginV
    if position_y < 33:
        alignment = 8  # top-center
        margin_v = max(20, int(video_height * position_y / 100))
    elif position_y < 66:
        alignment = 5  # middle-center
        margin_v = 30
    else:
        alignment = 2  # bottom-center
        margin_v = max(20, int(video_height * (100 - position_y) / 100))

    # ── Scale font size to video resolution ──
    # Frontend uses CSS px on a ~360px-wide preview; video is 1080px wide
    # Scale factor ≈ 3x, but ASS fontsize is already set for 1080p in _ASS_STYLES
    fontsize = s['fontsize']

    ass = "[Script Info]\n"
    ass += "ScriptType: v4.00+\n"
    ass += f"PlayResX: {video_width}\n"
    ass += f"PlayResY: {video_height}\n"
    ass += "WrapStyle: 0\n\n"
    ass += "[V4+ Styles]\n"
    ass += "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"

    secondary = s.get('secondary_color', '&H0000FFFF')
    ass += (f"Style: Default,Noto Sans CJK JP,{fontsize},{s['primary_color']},{secondary},"
            f"{s['outline_color']},{s['back_color']},{s['bold']},0,0,0,100,100,2,0,"
            f"{s['border_style']},{s['outline']},{s['shadow']},{alignment},"
            f"40,40,{margin_v},1\n\n")

    ass += "[Events]\n"
    ass += "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"

    # ── Pre-process captions: local time, sort, extend short segments ──
    MIN_DISPLAY = 3.0
    processed = []
    for cap in captions:
        cap_start = float(_cap_get(cap, 'start', 0))
        cap_end = float(_cap_get(cap, 'end', 0))
        cap_text = _cap_get(cap, 'text', '')
        cap_words = _cap_get(cap, 'words', None)
        if not cap_text or not cap_text.strip():
            continue
        local_start = cap_start - effective_offset if effective_offset > 0 else cap_start
        local_end = cap_end - effective_offset if effective_offset > 0 else cap_end
        if local_start < 0:
            local_start = 0
        if local_end <= local_start:
            continue
        processed.append({'start': local_start, 'end': local_end, 'text': cap_text, 'words': cap_words})

    processed.sort(key=lambda c: c['start'])

    # Extend short captions, cap at next caption's start
    for i, cap in enumerate(processed):
        extended_end = max(cap['end'], cap['start'] + MIN_DISPLAY)
        if i + 1 < len(processed):
            extended_end = min(extended_end, processed[i + 1]['start'])
        cap['end'] = max(extended_end, cap['start'] + 0.1)

    logger.info(f"[ass] Processed {len(processed)} captions (from {len(captions)} raw), style={style}")

    for cap in processed:
        start_ts = _seconds_to_ass_time(cap['start'])
        end_ts = _seconds_to_ass_time(cap['end'])

        if is_karaoke and cap.get('words'):
            karaoke_text = ""
            for w in cap['words']:
                w_start = float(w.get('start', 0))
                w_end = float(w.get('end', 0))
                if effective_offset > 0:
                    w_start -= effective_offset
                    w_end -= effective_offset
                duration_cs = max(1, int((w_end - w_start) * 100))
                word_text = w.get('word', '')
                word_text = word_text.replace('\\', '\\\\').replace('{', '\\{').replace('}', '\\}')
                karaoke_text += f"{{\\kf{duration_cs}}}{word_text}"
            ass += f"Dialogue: 0,{start_ts},{end_ts},Default,,0,0,0,,{karaoke_text}\n"
        else:
            text = cap['text'].replace('\n', '\\N')
            ass += f"Dialogue: 0,{start_ts},{end_ts},Default,,0,0,0,,{text}\n"

    return ass


# ─── Font discovery helper ─────────────────────────────────────────────────
_FONT_SEARCH_PATHS = [
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc',
    '/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc',
    '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc',
]

def _find_cjk_font() -> str:
    """Find a CJK-capable font, installing if necessary."""
    import glob, subprocess, time
    
    # Try known paths first
    for p in _FONT_SEARCH_PATHS:
        if os.path.exists(p):
            return p
    
    # Search for any Noto CJK font
    noto_fonts = glob.glob('/usr/share/fonts/**/NotoSans*CJK*', recursive=True)
    if noto_fonts:
        return noto_fonts[0]
    
    # Fonts not found — startup.sh may still be installing in background.
    # Wait up to 60s for the background install to finish.
    logger.warning("[font] CJK fonts not found, waiting for background install...")
    for i in range(12):
        time.sleep(5)
        for p in _FONT_SEARCH_PATHS:
            if os.path.exists(p):
                logger.info(f"[font] CJK font appeared after {(i+1)*5}s: {p}")
                return p
        noto_fonts = glob.glob('/usr/share/fonts/**/NotoSans*CJK*', recursive=True)
        if noto_fonts:
            logger.info(f"[font] CJK font appeared after {(i+1)*5}s: {noto_fonts[0]}")
            return noto_fonts[0]
    
    # Still not found — try installing synchronously
    logger.warning("[font] CJK fonts still missing, installing synchronously...")
    try:
        subprocess.run(
            ["apt-get", "install", "-y", "-qq", "--no-install-recommends", "fonts-noto-cjk"],
            timeout=120, capture_output=True,
        )
        subprocess.run(["fc-cache", "-f"], timeout=30, capture_output=True)
        for p in _FONT_SEARCH_PATHS:
            if os.path.exists(p):
                logger.info(f"[font] CJK font installed: {p}")
                return p
    except Exception as e:
        logger.error(f"[font] Failed to install CJK fonts: {e}")
    
    # Last resort: use any available font
    all_fonts = glob.glob('/usr/share/fonts/**/*.ttf', recursive=True) + \
               glob.glob('/usr/share/fonts/**/*.ttc', recursive=True)
    fallback = all_fonts[0] if all_fonts else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    logger.warning(f"[font] Using fallback font (CJK may not render): {fallback}")
    return fallback


# ─── Drawtext filter styles (matching frontend SUBTITLE_PRESETS) ─────────────
_DRAWTEXT_STYLES = {
    'simple': {
        'fontsize': 48, 'fontcolor': 'white', 'borderw': 3,
        'bordercolor': 'black', 'shadowx': 2, 'shadowy': 2,
        'shadowcolor': 'black@0.5', 'box': 0,
    },
    'box': {
        'fontsize': 48, 'fontcolor': 'white', 'borderw': 0,
        'bordercolor': 'black', 'shadowx': 0, 'shadowy': 0,
        'shadowcolor': 'black@0.0', 'box': 1,
        'boxcolor': 'black@0.8', 'boxborderw': 12,
    },
    'outline': {
        'fontsize': 50, 'fontcolor': 'white', 'borderw': 4,
        'bordercolor': 'black', 'shadowx': 0, 'shadowy': 0,
        'shadowcolor': 'black@0.0', 'box': 0,
    },
    'pop': {
        'fontsize': 54, 'fontcolor': '#FFE135', 'borderw': 3,
        'bordercolor': '#FF6B35', 'shadowx': 3, 'shadowy': 3,
        'shadowcolor': 'black@0.5', 'box': 0,
    },
    'gradient': {
        'fontsize': 48, 'fontcolor': 'white', 'borderw': 0,
        'bordercolor': 'black', 'shadowx': 0, 'shadowy': 0,
        'shadowcolor': 'black@0.0', 'box': 1,
        'boxcolor': '#6B5CF8@0.67', 'boxborderw': 12,
    },
    'karaoke': {
        'fontsize': 50, 'fontcolor': 'white@0.55', 'borderw': 0,
        'bordercolor': 'black', 'shadowx': 0, 'shadowy': 0,
        'shadowcolor': 'black@0.0', 'box': 1,
        'boxcolor': 'black@0.7', 'boxborderw': 12,
    },
}


def _build_drawtext_filter(captions: list, style: str, position_y: float, time_offset: float = 0) -> str:
    """Build ffmpeg drawtext filter chain for subtitle burning.
    
    Uses drawtext filter (built-in to ffmpeg) instead of ass/subtitles filter
    which requires libass and may not be available in all environments.
    
    Key design decisions:
    - Resolves overlapping time ranges so only ONE caption is visible at any time
    - Extends short captions (Whisper ASR often produces <2s segments) to min 3s
    - Caps extended end at the next caption's start to prevent overlap
    """
    s = _DRAWTEXT_STYLES.get(style, _DRAWTEXT_STYLES['box'])
    
    # Find font file - try multiple common paths
    fontfile = _find_cjk_font()
    logger.info(f"[drawtext] Using font: {fontfile}")
    
    # Calculate Y position based on position_y percentage
    if position_y < 33:
        y_expr = '50'  # top
    elif position_y < 66:
        y_expr = '(h-th)/2'  # middle
    else:
        y_expr = 'h-th-100'  # bottom
    
    # ── Pre-process captions: convert to local time, sort, de-overlap ──
    MIN_DISPLAY = 3.0  # minimum display duration in seconds (matches frontend)
    
    # Auto-detect if captions are in absolute or local time.
    # generate_clip saves LOCAL times (0-based, relative to clip start).
    # clip_editor_v2 transcribe saves ABSOLUTE times (offset by time_start).
    # If the maximum caption start time is less than time_offset,
    # the captions are already local and we should NOT subtract time_offset.
    if time_offset > 0 and captions:
        max_start = max(float(_cap_get(c, 'start', 0)) for c in captions)
        if max_start < time_offset:
            logger.info(f"[drawtext] Captions appear to be LOCAL times "
                        f"(max_start={max_start:.2f} < time_offset={time_offset:.2f}), "
                        f"skipping offset subtraction")
            effective_offset = 0
        else:
            logger.info(f"[drawtext] Captions appear to be ABSOLUTE times "
                        f"(max_start={max_start:.2f} >= time_offset={time_offset:.2f}), "
                        f"subtracting offset")
            effective_offset = time_offset
    else:
        effective_offset = 0
    
    processed = []
    for cap in captions:
        cap_start = float(_cap_get(cap, 'start', 0))
        cap_end = float(_cap_get(cap, 'end', 0))
        cap_text = _cap_get(cap, 'text', '')
        if not cap_text or not cap_text.strip():
            continue
        local_start = cap_start - effective_offset if effective_offset > 0 else cap_start
        local_end = cap_end - effective_offset if effective_offset > 0 else cap_end
        if local_start < 0:
            local_start = 0
        if local_end <= local_start:
            continue
        processed.append({'start': local_start, 'end': local_end, 'text': cap_text})
    
    # Sort by start time
    processed.sort(key=lambda c: c['start'])
    
    # Extend short captions and resolve overlaps:
    # Each caption's end = max(original_end, start + MIN_DISPLAY), but capped at next caption's start
    for i, cap in enumerate(processed):
        raw_end = cap['end']
        extended_end = max(raw_end, cap['start'] + MIN_DISPLAY)
        if i + 1 < len(processed):
            # Don't overlap with next caption
            extended_end = min(extended_end, processed[i + 1]['start'])
        cap['end'] = extended_end
        # Safety: ensure end > start
        if cap['end'] <= cap['start']:
            cap['end'] = cap['start'] + 0.1
    
    logger.info(f"[drawtext] Processed {len(processed)} captions (from {len(captions)} raw)")
    for i, cap in enumerate(processed):
        logger.info(f"[drawtext]   [{i}] {cap['start']:.2f}-{cap['end']:.2f} \"{cap['text'][:30]}\"")
    
    filters = []
    for cap in processed:
        # Escape text for drawtext filter
        # In filter_complex_script, we need to escape: ' \ : and special chars
        text = cap['text']
        text = text.replace('\\', '\\\\')
        text = text.replace("'", "\u2019")  # Replace apostrophe with unicode right single quote
        text = text.replace(':', '\\:')
        text = text.replace('%', '%%')
        text = text.replace('\n', ' ')
        
        # Build drawtext params
        params = [
            f"fontfile='{fontfile}'",
            f"text='{text}'",
            f"fontsize={s['fontsize']}",
            f"fontcolor={s['fontcolor']}",
            f"borderw={s['borderw']}",
            f"bordercolor={s['bordercolor']}",
            f"shadowx={s['shadowx']}",
            f"shadowy={s['shadowy']}",
            f"shadowcolor={s['shadowcolor']}",
            f"x=(w-text_w)/2",
            f"y={y_expr}",
            f"enable='between(t,{cap['start']:.2f},{cap['end']:.2f})'",
        ]
        
        # Add box background if needed
        if s.get('box'):
            params.append(f"box=1")
            params.append(f"boxcolor={s.get('boxcolor', 'black@0.5')}")
            params.append(f"boxborderw={s.get('boxborderw', 10)}")
        
        filters.append('drawtext=' + ':'.join(params))
    
    if not filters:
        logger.warning(f"[drawtext] No valid captions after processing "
                       f"({len(captions)} raw → 0 processed). "
                       f"time_offset={time_offset}, effective_offset={effective_offset}")
        return '[0:v]copy[v]'  # pass-through filter with [v] label
    
    return '[0:v]' + ','.join(filters) + '[v]'


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
    # Clamp progress_pct to 0-100 to prevent abnormal display values
    # (ffmpeg out_time_us can return negative or overflow values)
    if "progress_pct" in kwargs:
        try:
            kwargs["progress_pct"] = max(0, min(100, int(kwargs["progress_pct"])))
        except (TypeError, ValueError):
            kwargs["progress_pct"] = 0
    data = _load_job(job_id) or {}
    data.update(kwargs)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_job(job_id, data)


# Maximum age (seconds) before a non-terminal job is considered stale
_STALE_JOB_TIMEOUT = 300  # 5 minutes — detect stuck jobs faster (background task should update within seconds)

# ─── Concurrency limiter for exports ──────────────────────────────────────
_EXPORT_SEMAPHORE = asyncio.Semaphore(2)  # Max 2 concurrent exports to prevent OOM
_GPU_HEALTHY = None  # None = unknown, True/False = last check result
_GPU_LAST_CHECK = 0.0  # timestamp of last GPU health check
_GPU_CHECK_INTERVAL = 120  # re-check GPU every 2 minutes

# ─── Export cache (avoid re-encoding identical exports) ────────────────────
_EXPORT_CACHE_DIR = os.path.join(tempfile.gettempdir(), "aitherhub_export_cache")
os.makedirs(_EXPORT_CACHE_DIR, exist_ok=True)

def _export_cache_key(clip_url: str, captions: list, style: str, position_x: float, position_y: float) -> str:
    """Generate a deterministic cache key from export parameters."""
    import hashlib
    # Normalize captions to just text+timing for cache key
    cap_data = [(c.get('text',''), round(float(c.get('start',0)),1), round(float(c.get('end',0)),1)) for c in (captions if isinstance(captions, list) else [])]
    raw = json.dumps({'url': clip_url.split('?')[0], 'caps': cap_data, 'style': style, 'px': round(position_x,1), 'py': round(position_y,1)}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

def _get_cached_export(cache_key: str) -> dict | None:
    """Check if a cached export exists and return its data."""
    path = os.path.join(_EXPORT_CACHE_DIR, f"{cache_key}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        # Cache entries expire after 72 hours (matching SAS token expiry)
        created = datetime.fromisoformat(data.get('created_at', ''))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - created).total_seconds() / 3600
        if age_hours > 72:
            os.remove(path)
            return None
        return data
    except Exception:
        return None

def _save_cached_export(cache_key: str, download_url: str, file_size: int):
    """Save export result to cache."""
    path = os.path.join(_EXPORT_CACHE_DIR, f"{cache_key}.json")
    with open(path, 'w') as f:
        json.dump({'download_url': download_url, 'file_size': file_size, 'created_at': datetime.now(timezone.utc).isoformat()}, f)


async def _check_gpu_health(gpu_url: str) -> bool:
    """Quick health check for GPU VM. Caches result for _GPU_CHECK_INTERVAL seconds."""
    global _GPU_HEALTHY, _GPU_LAST_CHECK
    import time as _time
    now = _time.time()
    if now - _GPU_LAST_CHECK < _GPU_CHECK_INTERVAL and _GPU_HEALTHY is not None:
        return _GPU_HEALTHY
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{gpu_url}/health")
            _GPU_HEALTHY = resp.status_code == 200
    except Exception:
        _GPU_HEALTHY = False
    _GPU_LAST_CHECK = now
    logger.info(f"[gpu-health] GPU VM at {gpu_url} healthy={_GPU_HEALTHY}")
    return _GPU_HEALTHY


async def _run_export_job(job_id: str, video_id: str, clip_url: str, captions, style: str,
                          position_x: float, position_y: float, time_start: float,
                          split_segments: list = None):
    """Background task: download clip, burn subtitles, upload result.
    
    First tries to offload encoding to the GPU VM (NVENC) for 10-50x faster
    processing. Falls back to local ffmpeg (libx264) on App Service if the
    GPU VM is unavailable.
    
    Uses a semaphore to limit concurrent exports and prevent OOM.
    """
    import shutil
    import functools
    import httpx
    from azure.storage.blob import BlobServiceClient, ContentSettings
    from app.services.storage_service import (
        CONNECTION_STRING, ACCOUNT_NAME, CONTAINER_NAME,
        generate_read_sas_from_url,
    )
    from urllib.parse import unquote

    # ── Wait for semaphore (limit concurrent exports) ──
    _update_job(job_id, status="queued", progress_pct=0)
    logger.info(f"[export-job {job_id}] Waiting for export slot (semaphore)...")
    async with _EXPORT_SEMAPHORE:
        logger.info(f"[export-job {job_id}] Got export slot, starting...")
        await _run_export_job_inner(
            job_id, video_id, clip_url, captions, style,
            position_x, position_y, time_start, split_segments,
        )


async def _run_export_job_inner(job_id: str, video_id: str, clip_url: str, captions, style: str,
                                position_x: float, position_y: float, time_start: float,
                                split_segments: list = None):
    """Inner export logic, runs inside semaphore."""
    import shutil
    import functools
    import httpx
    from azure.storage.blob import BlobServiceClient, ContentSettings
    from app.services.storage_service import (
        CONNECTION_STRING, ACCOUNT_NAME, CONTAINER_NAME,
        generate_read_sas_from_url,
    )
    from urllib.parse import unquote

    _CDN_HOST = os.getenv("CDN_HOST", "https://cdn.aitherhub.com")
    _BLOB_HOST = f"https://{ACCOUNT_NAME}.blob.core.windows.net" if ACCOUNT_NAME else ""

    # ── GPU VM Offload: Try encoding on GPU VM first ──
    GPU_ENCODING_URL = os.getenv("GPU_ENCODING_URL", "")  # e.g. http://10.0.0.4:8765
    if GPU_ENCODING_URL and await _check_gpu_health(GPU_ENCODING_URL):
        try:
            logger.info(f"[export-job {job_id}] Attempting GPU VM offload to {GPU_ENCODING_URL}")
            _update_job(job_id, status="encoding", progress_pct=5)

            # Ensure clip_url has SAS token for GPU VM to download
            gpu_clip_url = clip_url
            if "?" not in clip_url or "sig=" not in clip_url:
                try:
                    sas_url = generate_read_sas_from_url(clip_url, expires_hours=2)
                    if sas_url:
                        gpu_clip_url = sas_url
                except Exception:
                    pass

            # Submit encoding job to GPU VM
            encode_payload = {
                "job_id": job_id,
                "video_id": video_id,
                "clip_url": gpu_clip_url,
                "captions": [c.dict() if hasattr(c, 'dict') else c for c in captions],
                "style": style,
                "position_x": position_x,
                "position_y": position_y,
                "split_segments": split_segments,
            }

            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{GPU_ENCODING_URL}/encode", json=encode_payload)
                resp.raise_for_status()
                gpu_job = resp.json()
                logger.info(f"[export-job {job_id}] GPU job submitted: {gpu_job}")

            # Poll GPU VM for completion (max 30 min)
            GPU_POLL_INTERVAL = 3  # seconds
            GPU_MAX_POLLS = 600    # 30 min
            for poll_i in range(GPU_MAX_POLLS):
                await asyncio.sleep(GPU_POLL_INTERVAL)
                try:
                    async with httpx.AsyncClient(timeout=10) as client:
                        status_resp = await client.get(f"{GPU_ENCODING_URL}/encode/{job_id}")
                        status_resp.raise_for_status()
                        gpu_status = status_resp.json()
                except Exception as poll_err:
                    logger.warning(f"[export-job {job_id}] GPU poll error: {poll_err}")
                    if poll_i > 5:  # Allow a few transient failures
                        raise
                    continue

                # Forward progress to our job store
                _update_job(
                    job_id,
                    status=gpu_status.get("status", "encoding"),
                    progress_pct=gpu_status.get("progress_pct", 0),
                )

                if gpu_status.get("status") == "done":
                    _update_job(
                        job_id,
                        status="done",
                        download_url=gpu_status.get("download_url"),
                        file_size=gpu_status.get("file_size"),
                        progress_pct=100,
                    )
                    encoder = gpu_status.get("encoder", "nvenc")
                    enc_time = gpu_status.get("encode_time_sec", 0)
                    logger.info(
                        f"[export-job {job_id}] GPU encoding complete! "
                        f"encoder={encoder}, time={enc_time}s"
                    )
                    return  # Success — skip local encoding

                if gpu_status.get("status") == "failed":
                    gpu_error = gpu_status.get("error", "unknown")
                    logger.warning(
                        f"[export-job {job_id}] GPU encoding failed: {gpu_error}. "
                        f"Falling back to local encoding."
                    )
                    break  # Fall through to local encoding

            else:
                logger.warning(
                    f"[export-job {job_id}] GPU encoding timed out after "
                    f"{GPU_MAX_POLLS * GPU_POLL_INTERVAL}s. Falling back to local."
                )

        except Exception as gpu_err:
            logger.warning(
                f"[export-job {job_id}] GPU VM offload failed: {gpu_err}. "
                f"Falling back to local encoding."
            )

    # ── Local Encoding Fallback (App Service B1) ──
    # Dynamic timeout: base 600s + 60s per minute of video
    FFMPEG_BASE_TIMEOUT = 600
    FFMPEG_MAX_TIMEOUT = 3600

    tmp_dir = tempfile.mkdtemp(prefix="export_sub_")
    try:
        # ── Step 1: Download clip via HTTP (SAS URL or direct) ──
        _update_job(job_id, status="downloading", progress_pct=5)
        video_path = os.path.join(tmp_dir, "source.mp4")

        download_url = clip_url
        logger.info(f"[export-job {job_id}] clip_url received: {clip_url[:120]}...")

        # If clip_url has no SAS token, generate one server-side
        if "?" not in download_url or "sig=" not in download_url:
            logger.info(f"[export-job {job_id}] No SAS token in clip_url, generating one")
            try:
                sas_url = generate_read_sas_from_url(clip_url, expires_hours=1)
                if sas_url:
                    download_url = sas_url
                    # Convert blob URL to CDN URL for faster download
                    if _BLOB_HOST and _CDN_HOST and _BLOB_HOST in download_url:
                        download_url = download_url.replace(_BLOB_HOST, _CDN_HOST)
                    logger.info(f"[export-job {job_id}] Generated SAS URL")
            except Exception as sas_err:
                logger.warning(f"[export-job {job_id}] SAS generation failed: {sas_err}")

        # Download via HTTP using httpx (handles CDN, Blob, and SAS URLs)
        import httpx
        def _download_http():
            with httpx.Client(timeout=120, follow_redirects=True) as client:
                with client.stream("GET", download_url) as resp:
                    resp.raise_for_status()
                    with open(video_path, "wb") as f:
                        for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                            f.write(chunk)

        await asyncio.get_event_loop().run_in_executor(None, _download_http)
        file_size = os.path.getsize(video_path)
        logger.info(f"[export-job {job_id}] Downloaded: {file_size/1024/1024:.1f} MB")

        # ── Step 1b: Handle split segments (cut & concat) ──
        if split_segments:
            enabled_segs = [s for s in split_segments if s.get('enabled', True)]
            if enabled_segs and len(enabled_segs) < len(split_segments):
                logger.info(f"[export-job {job_id}] Split export: {len(enabled_segs)}/{len(split_segments)} segments enabled")
                # Build ffmpeg concat filter for enabled segments
                concat_parts = []
                concat_list_path = os.path.join(tmp_dir, "concat_list.txt")
                for si, seg in enumerate(enabled_segs):
                    seg_path = os.path.join(tmp_dir, f"seg_{si:03d}.mp4")
                    seg_start = seg.get('start', 0)
                    seg_end = seg.get('end', 0)
                    seg_dur = seg_end - seg_start
                    if seg_dur <= 0:
                        continue
                    # Extract segment with ffmpeg
                    seg_cmd = [
                        "ffmpeg", "-y", "-hide_banner",
                        "-ss", str(seg_start), "-t", str(seg_dur),
                        "-i", video_path,
                        "-c", "copy", "-avoid_negative_ts", "make_zero",
                        seg_path,
                    ]
                    seg_proc = await asyncio.create_subprocess_exec(
                        *seg_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await asyncio.wait_for(seg_proc.wait(), timeout=60)
                    if seg_proc.returncode == 0 and os.path.exists(seg_path):
                        concat_parts.append(seg_path)
                    else:
                        logger.warning(f"[export-job {job_id}] Segment {si} extraction failed")

                if concat_parts:
                    # Write concat list
                    with open(concat_list_path, 'w') as f:
                        for p in concat_parts:
                            f.write(f"file '{p}'\n")
                    # Concat segments
                    merged_path = os.path.join(tmp_dir, "merged.mp4")
                    concat_cmd = [
                        "ffmpeg", "-y", "-hide_banner",
                        "-f", "concat", "-safe", "0",
                        "-i", concat_list_path,
                        "-c", "copy",
                        merged_path,
                    ]
                    concat_proc = await asyncio.create_subprocess_exec(
                        *concat_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await asyncio.wait_for(concat_proc.wait(), timeout=120)
                    if concat_proc.returncode == 0 and os.path.exists(merged_path):
                        # Replace source with merged file
                        os.replace(merged_path, video_path)
                        file_size = os.path.getsize(video_path)
                        logger.info(f"[export-job {job_id}] Merged {len(concat_parts)} segments: {file_size/1024/1024:.1f} MB")
                    else:
                        logger.warning(f"[export-job {job_id}] Concat failed, using original")

        # ── Step 2: Generate ASS subtitle file ──
        _update_job(job_id, status="encoding", progress_pct=15)

        # Normalize position_y: if value is 0-1 (ratio), convert to 0-100 (percent)
        pos_y_pct = position_y * 100 if position_y <= 1.0 else position_y

        output_path = os.path.join(tmp_dir, "output_subtitled.mp4")

        # ── Step 2a: Detect video dimensions with ffprobe ──
        video_w, video_h = 1080, 1920  # defaults for vertical clips
        try:
            probe_proc = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0", video_path,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            probe_out, _ = await asyncio.wait_for(probe_proc.communicate(), timeout=15)
            dims = probe_out.decode().strip().split(',')
            if len(dims) == 2:
                video_w, video_h = int(dims[0]), int(dims[1])
            logger.info(f"[export-job {job_id}] Video dimensions: {video_w}x{video_h}")
        except Exception as e:
            logger.warning(f"[export-job {job_id}] ffprobe failed, using defaults: {e}")

        # ── Step 2b: Generate ASS subtitle content ──
        ass_content = _generate_ass_content(
            captions, style, position_x, pos_y_pct, time_start,
            video_width=video_w, video_height=video_h,
        )
        ass_path = os.path.join(tmp_dir, "subtitles.ass")
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(ass_content)
        logger.info(f"[export-job {job_id}] Generated ASS subtitle: {len(captions)} captions, "
                    f"style={style}, pos_y={pos_y_pct}, res={video_w}x{video_h}")
        logger.info(f"[export-job {job_id}] ASS content (first 500): {ass_content[:500]}")

        # ── Step 3: Burn subtitles with ffmpeg ASS filter ──
        # Pre-flight check: verify ffmpeg has ASS filter support
        try:
            check_proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-hide_banner", "-filters",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            check_out, _ = await asyncio.wait_for(check_proc.communicate(), timeout=10)
            has_ass = b" ass " in check_out or b"ass" in check_out
            logger.info(f"[export-job {job_id}] ffmpeg ASS filter available: {has_ass}")
            if not has_ass:
                logger.warning(f"[export-job {job_id}] ASS filter not found, will try anyway")
        except Exception as e:
            logger.warning(f"[export-job {job_id}] ffmpeg pre-check failed: {e}")

        # Escape the ASS path for ffmpeg -vf
        ass_path_escaped = ass_path.replace(':', '\\:').replace("'", "'\\''")
        # Find font directory
        font_dir = '/usr/share/fonts/opentype/noto'
        if not os.path.isdir(font_dir):
            font_dir = '/usr/share/fonts'

        vf_filter = f"ass='{ass_path_escaped}':fontsdir='{font_dir}'"
        logger.info(f"[export-job {job_id}] VF filter: {vf_filter}")

        # If video is high-res (>1080p height or >1920p width), scale down to speed up encoding
        scale_filter = None
        if video_w > 1920 or video_h > 1920:
            # Scale to max 1080p while maintaining aspect ratio
            if video_w > video_h:
                scale_filter = "scale=1920:-2"
            else:
                scale_filter = "scale=-2:1920"
            logger.info(f"[export-job {job_id}] High-res video ({video_w}x{video_h}), adding scale filter: {scale_filter}")

        # Combine filters: scale (if needed) + ASS subtitle
        if scale_filter:
            combined_vf = f"{scale_filter},{vf_filter}"
        else:
            combined_vf = vf_filter

        cmd = [
            "ffmpeg", "-y", "-hide_banner",
            "-progress", "pipe:1",  # output progress to stdout
            "-i", video_path,
            "-vf", combined_vf,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-threads", "0",
            "-c:a", "copy",
            "-movflags", "+faststart",
            output_path,
        ]
        logger.info(f"[export-job {job_id}] Running ffmpeg cmd: {' '.join(cmd)}")

        _update_job(job_id, status="encoding", progress_pct=20)

        # Get source duration for progress calculation
        source_duration_us = 0
        try:
            dur_proc = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "csv=p=0", video_path,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            dur_out, _ = await asyncio.wait_for(dur_proc.communicate(), timeout=10)
            source_duration_us = int(float(dur_out.decode().strip()) * 1_000_000)
        except Exception:
            pass

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Read ffmpeg progress from stdout in background
        async def _read_ffmpeg_progress():
            """Parse ffmpeg -progress output to update job progress_pct."""
            try:
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    decoded = line.decode(errors='replace').strip()
                    if decoded.startswith('out_time_us=') and source_duration_us > 0:
                        try:
                            current_us = int(decoded.split('=')[1])
                            # Map encoding progress to 20-80% range
                            ratio = min(current_us / source_duration_us, 1.0)
                            pct = int(20 + ratio * 60)  # 20% to 80%
                            _update_job(job_id, status="encoding", progress_pct=pct)
                        except (ValueError, ZeroDivisionError):
                            pass
            except Exception:
                pass

        progress_task = asyncio.create_task(_read_ffmpeg_progress())

        # Calculate dynamic timeout based on source duration
        if source_duration_us > 0:
            source_duration_sec = source_duration_us / 1_000_000
            # Base 600s + 60s per minute of video, capped at max
            FFMPEG_TIMEOUT = min(
                FFMPEG_MAX_TIMEOUT,
                max(FFMPEG_BASE_TIMEOUT, int(FFMPEG_BASE_TIMEOUT + source_duration_sec))
            )
        else:
            FFMPEG_TIMEOUT = FFMPEG_MAX_TIMEOUT  # Unknown duration: use max
        logger.info(f"[export-job {job_id}] Dynamic ffmpeg timeout: {FFMPEG_TIMEOUT}s "
                    f"(source duration: {source_duration_us/1_000_000:.1f}s)")

        try:
            # Read stderr in parallel (avoid pipe deadlock)
            stderr_task = asyncio.create_task(proc.stderr.read())
            # Wait for ffmpeg to finish
            await asyncio.wait_for(proc.wait(), timeout=FFMPEG_TIMEOUT)
            # Cancel progress reader and get stderr
            progress_task.cancel()
            stderr = await stderr_task
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.error(f"[export-job {job_id}] ffmpeg timed out after {FFMPEG_TIMEOUT}s")
            _update_job(job_id, status="failed", error=f"Encoding timed out ({FFMPEG_TIMEOUT}s). "
                        f"The video may be too long or complex for this server. "
                        f"Try exporting a shorter clip.")
            return

        ffmpeg_stderr = stderr.decode(errors='replace')
        logger.info(f"[export-job {job_id}] ffmpeg stderr (last 1000): {ffmpeg_stderr[-1000:]}")

        if proc.returncode != 0:
            # Extract meaningful error: skip version banner, take last lines
            stderr_lines = ffmpeg_stderr.strip().split('\n')
            # Filter out blank lines and take last 10 meaningful lines
            meaningful = [l for l in stderr_lines if l.strip()][-10:]
            err_msg = '\n'.join(meaningful)
            logger.error(f"[export-job {job_id}] ffmpeg failed (rc={proc.returncode}): {err_msg}")
            _update_job(job_id, status="failed", error=f"ffmpeg error: {err_msg[-500:]}")
            return

        output_size = os.path.getsize(output_path)
        logger.info(f"[export-job {job_id}] Encoded: {output_size/1024/1024:.1f} MB (source was {file_size/1024/1024:.1f} MB)")

        # ── Step 4: Upload to Azure Blob ──
        _update_job(job_id, status="uploading", progress_pct=85)
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

        _update_job(job_id, status="done", download_url=download_url, file_size=output_size, progress_pct=100)
        logger.info(f"[export-job {job_id}] Complete! URL: {download_url[:80]}...")
        # ── Save exported_url to video_clips for widget delivery ──
        try:
            from app.core.db import async_engine
            from sqlalchemy import text as _text
            async with async_engine.begin() as conn:
                # blob_url is the permanent URL (without SAS token)
                # Use clip_url to find the exact clip row
                # Strip SAS token from clip_url for matching
                import re as _re
                clean_clip_url = _re.sub(r'\?.*$', '', clip_url)
                await conn.execute(_text("""
                    UPDATE video_clips
                    SET exported_url = :exported_url, exported_at = NOW()
                    WHERE id = (
                        SELECT id FROM video_clips
                        WHERE video_id = :video_id::uuid
                        AND (clip_url LIKE :clip_url_pattern OR clip_url = :clean_clip_url)
                        ORDER BY created_at DESC
                        LIMIT 1
                    )
                """), {
                    "exported_url": blob_url,
                    "video_id": video_id,
                    "clip_url_pattern": clean_clip_url + "%",
                    "clean_clip_url": clean_clip_url,
                })
            logger.info(f"[export-job {job_id}] Saved exported_url to video_clips for video {video_id}")
        except Exception as db_err:
            logger.warning(f"[export-job {job_id}] Failed to save exported_url to DB: {db_err}")
        # Save to cache for instant re-exportt
        job_data = _load_job(job_id) or {}
        cache_key = job_data.get("cache_key")
        if cache_key:
            _save_cached_export(cache_key, download_url, output_size)
            logger.info(f"[export-job {job_id}] Cached export (key={cache_key})")

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

    # ── Cache check: return cached export instantly if available ──
    cap_dicts = [c.dict() if hasattr(c, 'dict') else c for c in req.captions]
    cache_key = _export_cache_key(req.clip_url, cap_dicts, req.style, req.position_x, req.position_y)
    cached = _get_cached_export(cache_key)
    if cached:
        logger.info(f"[export] Cache HIT for {video_id} (key={cache_key})")
        job_id = f"cached_{cache_key}"
        _save_job(job_id, {
            "status": "done",
            "video_id": video_id,
            "download_url": cached["download_url"],
            "file_size": cached.get("file_size", 0),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        return {
            "job_id": job_id,
            "status": "done",
            "download_url": cached["download_url"],
            "file_size": cached.get("file_size", 0),
            "video_id": video_id,
        }

    logger.info(f"[export] Cache MISS for {video_id} (key={cache_key}), starting encode")
    job_id = uuid.uuid4().hex[:12]
    _save_job(job_id, {
        "status": "queued",
        "video_id": video_id,
        "style": req.style,
        "caption_count": len(req.captions),
        "cache_key": cache_key,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    # Launch background task with exception safety
    split_segs = [s.dict() if hasattr(s, 'dict') else s for s in req.split_segments] if req.split_segments else None

    async def _safe_export_wrapper():
        """Wrapper to catch and log any unhandled exceptions from background export."""
        try:
            await _run_export_job(
                job_id, video_id, req.clip_url, cap_dicts,
                req.style, req.position_x, req.position_y, req.time_start,
                split_segments=split_segs,
            )
        except Exception as bg_err:
            logger.error(f"[export-job {job_id}] Background task crashed: {bg_err}", exc_info=True)
            try:
                _update_job(job_id, status="failed",
                            error=f"Internal error: {str(bg_err)[:200]}. Please try again.")
            except Exception:
                pass  # Last resort: can't even update job status

    asyncio.create_task(_safe_export_wrapper())

    return {
        "job_id": job_id,
        "status": "queued",
        "video_id": video_id,
    }


@router.get("/{video_id}/export-subtitled/{job_id}")
async def get_export_status(video_id: str, job_id: str):
    """
    Poll export job status. Returns download_url when done.
    Detects stale jobs (stuck in non-terminal state for too long) and marks them failed.
    """
    job = _load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Export job not found")

    status = job["status"]

    # Stale job detection: if a non-terminal job hasn't been updated for too long,
    # the background task likely crashed (container restart, OOM, etc.)
    if status not in ("done", "failed"):
        updated_at = job.get("updated_at") or job.get("created_at")
        if updated_at:
            try:
                last_update = datetime.fromisoformat(updated_at)
                if last_update.tzinfo is None:
                    last_update = last_update.replace(tzinfo=timezone.utc)
                age_seconds = (datetime.now(timezone.utc) - last_update).total_seconds()
                if age_seconds > _STALE_JOB_TIMEOUT:
                    logger.warning(f"[export-job {job_id}] Stale job detected: "
                                   f"status={status}, age={age_seconds:.0f}s > {_STALE_JOB_TIMEOUT}s")
                    _update_job(job_id, status="failed",
                                error=f"Job timed out (stuck in '{status}' for {int(age_seconds)}s). "
                                      f"The server may have restarted. Please try again.")
                    status = "failed"
                    job = _load_job(job_id)
            except (ValueError, TypeError):
                pass

    result = {
        "job_id": job_id,
        "status": status,
        "video_id": video_id,
        "progress_pct": max(0, min(100, int(job.get("progress_pct", 0)))),
    }
    if status == "done":
        result["download_url"] = job.get("download_url")
        result["file_size"] = job.get("file_size")
        result["progress_pct"] = 100
    elif status == "failed":
        result["error"] = job.get("error", "Unknown error")

    return result


def _cleanup_tmp(tmp_dir: str):
    """Clean up temporary files."""
    try:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception as _e:
        logger.debug(f"Non-critical error suppressed: {_e}")


@router.get("/export-jobs/active")
async def list_active_export_jobs():
    """Diagnostic endpoint: list all active (non-terminal) export jobs.
    Useful for debugging stuck exports."""
    active_jobs = []
    try:
        for fname in os.listdir(_EXPORT_JOB_DIR):
            if not fname.endswith('.json'):
                continue
            job_id = fname[:-5]
            job = _load_job(job_id)
            if not job:
                continue
            status = job.get("status", "unknown")
            if status in ("done", "failed"):
                continue
            # Include basic info
            active_jobs.append({
                "job_id": job_id,
                "status": status,
                "video_id": job.get("video_id"),
                "progress_pct": job.get("progress_pct", 0),
                "created_at": job.get("created_at"),
                "updated_at": job.get("updated_at"),
            })
    except Exception as e:
        logger.warning(f"[export-jobs] Failed to list jobs: {e}")
    return {
        "active_jobs": active_jobs,
        "count": len(active_jobs),
        "semaphore_available": _EXPORT_SEMAPHORE._value,
        "gpu_healthy": _GPU_HEALTHY,
    }
