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
        except Exception:
            pass
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
        except Exception:
            pass

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
            except Exception:
                pass

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
                from app.services.storage_service import (
                    _parse_account_key,
                    CONNECTION_STRING,
                    ACCOUNT_NAME,
                    CONTAINER_NAME,
                )
                from azure.storage.blob import BlobSasPermissions, generate_blob_sas
                from datetime import datetime, timedelta, timezone

                # Extract blob name from clip_url
                # URL formats:
                #   https://cdn.aitherhub.com/videos/email/video_id/clips/clip_xxx.mp4
                #   https://account.blob.core.windows.net/videos/email/video_id/clips/clip_xxx.mp4
                from urllib.parse import urlparse, unquote
                parsed_url = urlparse(download_url)
                url_path = unquote(parsed_url.path)

                # Remove leading /videos/ or /container_name/ prefix
                if url_path.startswith(f"/{CONTAINER_NAME}/"):
                    blob_name = url_path[len(f"/{CONTAINER_NAME}/"):]
                elif url_path.startswith("/videos/"):
                    blob_name = url_path[len("/videos/"):]
                else:
                    blob_name = url_path.lstrip("/")

                logger.info(f"[transcribe] Extracted blob_name: {blob_name}")

                account_key = _parse_account_key(CONNECTION_STRING)
                expiry = datetime.now(timezone.utc) + timedelta(minutes=60)

                sas_token = generate_blob_sas(
                    account_name=ACCOUNT_NAME,
                    container_name=CONTAINER_NAME,
                    blob_name=blob_name,
                    account_key=account_key,
                    permission=BlobSasPermissions(read=True),
                    expiry=expiry,
                )

                # Build download URL with SAS token using actual blob storage URL
                download_url = f"https://{ACCOUNT_NAME}.blob.core.windows.net/{CONTAINER_NAME}/{blob_name}?{sas_token}"
                logger.info(f"[transcribe] Generated SAS download URL (expires in 60 min)")
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

    # Step 2: Send to Azure OpenAI Whisper API
    try:
        import openai

        # Use Azure OpenAI Whisper (deployed as 'whisper' model)
        azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "https://aoai-kyogoku-service.openai.azure.com/")
        azure_key = os.getenv("AZURE_OPENAI_KEY", "")

        # Clean up endpoint URL - remove any path/query params, keep just base URL
        from urllib.parse import urlparse
        parsed = urlparse(azure_endpoint)
        clean_endpoint = f"{parsed.scheme}://{parsed.netloc}/"

        logger.info(f"[transcribe] Using Azure OpenAI endpoint: {clean_endpoint}")

        openai_client = openai.AsyncAzureOpenAI(
            api_key=azure_key,
            api_version="2024-06-01",
            azure_endpoint=clean_endpoint,
        )

        file_size = os.path.getsize(video_path)
        max_size = 25 * 1024 * 1024  # 25MB Whisper API limit
        logger.info(f"[transcribe] File size: {file_size} bytes (max: {max_size})")

        async def _call_whisper(file_path: str) -> list:
            """Call Azure OpenAI Whisper and return segments."""
            with open(file_path, "rb") as f:
                response = await openai_client.audio.transcriptions.create(
                    model="whisper",
                    file=f,
                    response_format="verbose_json",
                    language="ja",
                    timestamp_granularities=["segment"],
                )

            segs = []
            if hasattr(response, "segments") and response.segments:
                for seg in response.segments:
                    s = getattr(seg, "start", 0) if hasattr(seg, "start") else seg.get("start", 0)
                    e = getattr(seg, "end", 0) if hasattr(seg, "end") else seg.get("end", 0)
                    t = getattr(seg, "text", "") if hasattr(seg, "text") else seg.get("text", "")
                    segs.append({"start": float(s), "end": float(e), "text": t.strip()})
            elif hasattr(response, "text") and response.text:
                duration = req.time_end - req.time_start
                segs.append({"start": 0.0, "end": duration, "text": response.text.strip()})
            return segs

        if file_size <= max_size:
            segments = await _call_whisper(video_path)
        else:
            # File too large - try extracting audio with ffmpeg first
            audio_path = os.path.join(tmp_dir, "audio.wav")
            try:
                proc = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-y", "-i", video_path,
                    "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                    audio_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
                if proc.returncode == 0 and os.path.exists(audio_path):
                    segments = await _call_whisper(audio_path)
                else:
                    raise RuntimeError("ffmpeg not available or failed")
            except Exception:
                # ffmpeg not available - try sending mp4 directly
                segments = await _call_whisper(video_path)

        logger.info(f"[transcribe] Got {len(segments)} segments from Azure OpenAI Whisper")

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
        absolute_segments.append({
            "segment_index": i,
            "start": abs_start,
            "end": abs_end,
            "local_start": seg["start"],
            "local_end": seg["end"],
            "text": seg["text"],
        })

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
        except Exception:
            pass
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
        except Exception:
            pass

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


def _cleanup_tmp(tmp_dir: str):
    """Clean up temporary files."""
    try:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass
