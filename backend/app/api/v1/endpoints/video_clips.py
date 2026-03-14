"""Video API — Clips & Subtitles

Split from video.py for maintainability.
"""
from typing import List, Optional
import json
import uuid as uuid_module
import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, select
from loguru import logger

from app.core.dependencies import get_db, get_current_user
from app.models.orm.video import Video
from app.api.v1.endpoints.video import _replace_blob_url_to_cdn

router = APIRouter(
    prefix="/videos",
    tags=["videos"],
)

# =========================
# Clip generation endpoints
# =========================

@router.post("/{video_id}/clips")
async def request_clip_generation(
    video_id: str,
    request_body: dict,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Request TikTok-style clip generation for a specific phase.
    
    Body:
    {
        "phase_index": 0,
        "time_start": 0.0,
        "time_end": 51.0,
        "speed_factor": 1.2  // optional, default 1.0 (1.0-1.5x)
    }
    """
    try:
        user_id = user.get("user_id") or user.get("id")
        phase_index = request_body.get("phase_index")
        time_start = request_body.get("time_start")
        time_end = request_body.get("time_end")
        speed_factor = float(request_body.get("speed_factor", 1.0))

        if phase_index is None or time_start is None or time_end is None:
            raise HTTPException(status_code=400, detail="phase_index, time_start, time_end are required")
        # Ensure phase_index is always a string (DB column is text)
        phase_index = str(phase_index)

        # Clamp speed_factor to safe range
        speed_factor = max(0.5, min(2.0, speed_factor))

        time_start = float(time_start)
        time_end = float(time_end)

        if time_end <= time_start:
            raise HTTPException(status_code=400, detail="time_end must be greater than time_start")

        # Check if clip already exists for this phase
        existing_sql = text("""
            SELECT id, status, clip_url
            FROM video_clips
            WHERE video_id = :video_id AND phase_index = CAST(:phase_index AS text)
            ORDER BY created_at DESC
            LIMIT 1
        """)
        existing = await db.execute(existing_sql, {"video_id": video_id, "phase_index": phase_index})
        existing_row = existing.fetchone()

        if existing_row:
            if existing_row.status == "completed" and existing_row.clip_url:
                # Already generated - return existing
                return {
                    "clip_id": str(existing_row.id),
                    "status": "completed",
                    "clip_url": _replace_blob_url_to_cdn(existing_row.clip_url),
                    "message": "Clip already generated",
                }
            elif existing_row.status in ("pending", "processing"):
                # Check if stuck (pending/processing for > 5 minutes)
                from datetime import datetime, timedelta, timezone
                stuck_threshold = datetime.now(timezone.utc) - timedelta(minutes=5)
                check_stuck_sql = text("""
                    SELECT id, created_at, updated_at FROM video_clips
                    WHERE id = :clip_id
                    AND COALESCE(updated_at, created_at) < :threshold
                """)
                stuck_result = await db.execute(check_stuck_sql, {
                    "clip_id": str(existing_row.id),
                    "threshold": stuck_threshold,
                })
                stuck_row = stuck_result.fetchone()
                if stuck_row:
                    # Stuck clip - delete old record and create new one
                    logger.warning(f"Clip {existing_row.id} stuck in {existing_row.status} for >5min, retrying")
                    delete_sql = text("DELETE FROM video_clips WHERE id = :clip_id")
                    await db.execute(delete_sql, {"clip_id": str(existing_row.id)})
                    await db.commit()
                    # Fall through to create new clip
                else:
                    # Recently created, still in progress
                    return {
                        "clip_id": str(existing_row.id),
                        "status": existing_row.status,
                        "message": "Clip generation already in progress",
                    }
            # If failed or stuck, create a new one

        # Verify video belongs to user
        video_sql = text("SELECT id, user_id, original_filename FROM videos WHERE id = :video_id")
        vres = await db.execute(video_sql, {"video_id": video_id})
        video_row = vres.fetchone()

        if not video_row:
            raise HTTPException(status_code=404, detail="Video not found")
        if video_row.user_id != user_id:
            raise HTTPException(status_code=403, detail="Not authorized")

        # Get user email for blob path
        user_sql = text("SELECT email FROM users WHERE id = :user_id")
        ures = await db.execute(user_sql, {"user_id": user_id})
        user_row = ures.fetchone()
        email = user_row.email if user_row else None

        if not email:
            raise HTTPException(status_code=400, detail="User email not found")

        # Generate download SAS URL for source video
        from app.services.storage_service import generate_download_sas
        download_url, _ = await generate_download_sas(
            email=email,
            video_id=video_id,
            filename=video_row.original_filename,
            expires_in_minutes=1440,
        )

        # Create clip record with job_payload for worker DB fallback
        clip_id = str(uuid_module.uuid4())
        job_payload = {
            "job_type": "generate_clip",
            "clip_id": clip_id,
            "video_id": video_id,
            "blob_url": download_url,
            "time_start": time_start,
            "time_end": time_end,
            "phase_index": phase_index,
            "speed_factor": speed_factor,
        }
        import json as _json
        insert_sql = text("""
            INSERT INTO video_clips (id, video_id, user_id, phase_index, time_start, time_end, status, job_payload)
            VALUES (:id, :video_id, :user_id, :phase_index, :time_start, :time_end, 'pending', CAST(:job_payload AS jsonb))
        """)
        await db.execute(insert_sql, {
            "id": clip_id,
            "video_id": video_id,
            "user_id": user_id,
            "phase_index": phase_index,
            "time_start": time_start,
            "time_end": time_end,
            "job_payload": _json.dumps(job_payload, ensure_ascii=False),
        })
        await db.commit()

        # Enqueue clip generation job
        from app.services.queue_service import enqueue_job
        enqueue_result = await enqueue_job(job_payload)
        if not enqueue_result.success:
            logger.warning(f"Queue enqueue failed for clip {clip_id}: {enqueue_result.error}. Worker DB fallback will pick it up.")

        logger.info(f"Clip generation requested: clip_id={clip_id}, video_id={video_id}, phase={phase_index}")

        return {
            "clip_id": clip_id,
            "status": "pending",
            "message": "Clip generation started",
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to request clip generation: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to request clip generation: {exc}")


@router.get("/{video_id}/clips/{phase_index}")
async def get_clip_status(
    video_id: str,
    phase_index: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Get clip generation status and download URL for a specific phase."""
    try:
        user_id = user.get("user_id") or user.get("id")

        sql = text("""
            SELECT id, status, clip_url, sas_token, sas_expireddate, error_message, created_at, captions,
                   subtitle_style, subtitle_position_x, subtitle_position_y,
                   COALESCE(progress_pct, 0) as progress_pct, COALESCE(progress_step, '') as progress_step
            FROM video_clips
            WHERE video_id = :video_id AND phase_index = CAST(:phase_index AS text)
            ORDER BY created_at DESC
            LIMIT 1
        """)
        result = await db.execute(sql, {"video_id": video_id, "phase_index": str(phase_index)})
        row = result.fetchone()

        if not row:
            return {
                "status": "not_found",
                "message": "No clip found for this phase",
            }

        response = {
            "clip_id": str(row.id),
            "status": row.status,
            "progress_pct": row.progress_pct if hasattr(row, 'progress_pct') else 0,
            "progress_step": row.progress_step if hasattr(row, 'progress_step') else "",
        }

        if row.status == "completed" and row.clip_url:
            # Generate or reuse SAS download URL
            clip_download_url = None

            # Check if existing SAS is still valid
            if row.sas_token and row.sas_expireddate:
                now = datetime.now(timezone.utc)
                expiry = row.sas_expireddate
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                if expiry > now:
                    clip_download_url = row.sas_token

            if not clip_download_url:
                # Generate new SAS URL for clip
                try:
                    from app.services.storage_service import generate_read_sas_from_url
                    sas_url = generate_read_sas_from_url(row.clip_url)
                    if sas_url:
                        clip_download_url = _replace_blob_url_to_cdn(sas_url)
                        # Cache the SAS token
                        expiry_dt = datetime.now(timezone.utc) + timedelta(hours=24)
                        update_sql = text("""
                            UPDATE video_clips
                            SET sas_token = :sas_token, sas_expireddate = :expiry
                            WHERE id = :id
                        """)
                        await db.execute(update_sql, {
                            "sas_token": clip_download_url,
                            "expiry": expiry_dt,
                            "id": row.id,
                        })
                        await db.commit()
                except Exception as e:
                    logger.warning(f"Failed to generate clip SAS: {e}")

            response["clip_url"] = clip_download_url or _replace_blob_url_to_cdn(row.clip_url)

        elif row.status == "failed":
            response["error_message"] = row.error_message
        elif row.status == "dead":
            response["error_message"] = row.error_message or "Job moved to dead-letter queue after max retries"

        # Include captions (subtitle data) if available
        if hasattr(row, 'captions') and row.captions:
            response["captions"] = row.captions

        # Include subtitle style preferences if available
        if hasattr(row, 'subtitle_style') and row.subtitle_style:
            response["subtitle_style"] = row.subtitle_style
        if hasattr(row, 'subtitle_position_x') and row.subtitle_position_x is not None:
            response["subtitle_position_x"] = row.subtitle_position_x
        if hasattr(row, 'subtitle_position_y') and row.subtitle_position_y is not None:
            response["subtitle_position_y"] = row.subtitle_position_y

        return response

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to get clip status: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to get clip status: {exc}")


@router.get("/{video_id}/clips")
async def list_clips(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """List all clips for a video."""
    try:
        user_id = user.get("user_id") or user.get("id")

        sql = text("""
            SELECT id, phase_index, time_start, time_end, status, clip_url, sas_token, sas_expireddate, created_at, captions,
                   COALESCE(progress_pct, 0) as progress_pct, progress_step
            FROM video_clips
            WHERE video_id = :video_id
            ORDER BY phase_index ASC, created_at DESC
        """)
        result = await db.execute(sql, {"video_id": video_id})
        rows = result.fetchall()

        clips = []
        seen_phases = set()
        for row in rows:
            # Only include the latest clip per phase
            if row.phase_index in seen_phases:
                continue
            seen_phases.add(row.phase_index)

            clip = {
                "clip_id": str(row.id),
                "phase_index": row.phase_index,
                "time_start": row.time_start,
                "time_end": row.time_end,
                "status": row.status,
                "progress_pct": row.progress_pct if hasattr(row, 'progress_pct') else 0,
                "progress_step": row.progress_step if hasattr(row, 'progress_step') else None,
            }
            if row.status == "completed" and row.clip_url:
                # Generate or reuse SAS download URL (same logic as get_clip_status)
                clip_download_url = None

                # Check if existing SAS is still valid
                if row.sas_token and row.sas_expireddate:
                    now = datetime.now(timezone.utc)
                    expiry = row.sas_expireddate
                    if expiry.tzinfo is None:
                        expiry = expiry.replace(tzinfo=timezone.utc)
                    if expiry > now:
                        clip_download_url = row.sas_token

                if not clip_download_url:
                    try:
                        from app.services.storage_service import generate_read_sas_from_url
                        sas_url = generate_read_sas_from_url(row.clip_url)
                        if sas_url:
                            clip_download_url = _replace_blob_url_to_cdn(sas_url)
                            expiry_dt = datetime.now(timezone.utc) + timedelta(hours=24)
                            update_sql = text("""
                                UPDATE video_clips
                                SET sas_token = :sas_token, sas_expireddate = :expiry
                                WHERE id = :id
                            """)
                            await db.execute(update_sql, {
                                "sas_token": clip_download_url,
                                "expiry": expiry_dt,
                                "id": row.id,
                            })
                            await db.commit()
                    except Exception as e:
                        logger.warning(f"Failed to generate clip SAS in list: {e}")

                clip["clip_url"] = clip_download_url or _replace_blob_url_to_cdn(row.clip_url)
            # Include captions if available
            if hasattr(row, 'captions') and row.captions:
                clip["captions"] = row.captions
            clips.append(clip)

        return {"clips": clips}

    except Exception as exc:
        logger.exception(f"Failed to list clips: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to list clips: {exc}")



# ──────────────────────────────────────────────────────────────
# Phase Rating (Human Feedback)
# ──────────────────────────────────────────────────────────────

@router.put("/{video_id}/phases/{phase_index}/rating")

# =========================
# Lightning Clip Editor APIs
# =========================

@router.patch("/{video_id}/clips/{clip_id}/trim")
async def trim_clip(
    video_id: str,
    clip_id: str,
    request_body: dict,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Adjust clip start/end time (±3 seconds max per adjustment).
    Re-queues clip generation with new boundaries.

    Body:
    {
        "time_start": float,  // new start time (full video seconds)
        "time_end": float,    // new end time (full video seconds)
        "speed_factor": 1.2   // optional
    }
    """
    try:
        user_id = user.get("user_id") or user.get("id")
        new_start = float(request_body.get("time_start", 0))
        new_end = float(request_body.get("time_end", 0))
        speed_factor = float(request_body.get("speed_factor", 1.2))

        if new_end <= new_start:
            raise HTTPException(status_code=400, detail="time_end must be greater than time_start")

        # Clamp speed_factor
        speed_factor = max(0.5, min(2.0, speed_factor))

        # Get existing clip
        sql = text("""
            SELECT id, video_id, phase_index, time_start, time_end
            FROM video_clips
            WHERE id = :clip_id AND video_id = :video_id
        """)
        result = await db.execute(sql, {"clip_id": clip_id, "video_id": video_id})
        clip_row = result.fetchone()

        if not clip_row:
            raise HTTPException(status_code=404, detail="Clip not found")

        # Verify video ownership
        video_sql = text("SELECT id, user_id, original_filename FROM videos WHERE id = :video_id")
        vres = await db.execute(video_sql, {"video_id": video_id})
        video_row = vres.fetchone()
        if not video_row or video_row.user_id != user_id:
            raise HTTPException(status_code=403, detail="Not authorized")

        # Create new clip record (keep old one for history)
        new_clip_id = str(uuid_module.uuid4())
        insert_sql = text("""
            INSERT INTO video_clips (id, video_id, user_id, phase_index, time_start, time_end, status)
            VALUES (:id, :video_id, :user_id, :phase_index, :time_start, :time_end, 'pending')
        """)
        await db.execute(insert_sql, {
            "id": new_clip_id,
            "video_id": video_id,
            "user_id": user_id,
            "phase_index": clip_row.phase_index,
            "time_start": new_start,
            "time_end": new_end,
        })
        await db.commit()

        # Get user email for blob path
        user_sql = text("SELECT email FROM users WHERE id = :user_id")
        ures = await db.execute(user_sql, {"user_id": user_id})
        user_row = ures.fetchone()
        email = user_row.email if user_row else None

        if not email:
            raise HTTPException(status_code=400, detail="User email not found")

        # Generate download SAS URL
        from app.services.storage_service import generate_download_sas
        download_url, _ = await generate_download_sas(
            email=email,
            video_id=video_id,
            filename=video_row.original_filename,
            expires_in_minutes=1440,
        )

        # Enqueue clip generation job
        from app.services.queue_service import enqueue_job
        await enqueue_job({
            "job_type": "generate_clip",
            "clip_id": new_clip_id,
            "video_id": video_id,
            "blob_url": download_url,
            "time_start": new_start,
            "time_end": new_end,
            "phase_index": clip_row.phase_index,
            "speed_factor": speed_factor,
        })

        logger.info(
            f"[TRIM] Clip trimmed: {clip_id} → {new_clip_id}, "
            f"time={new_start:.1f}-{new_end:.1f}s"
        )

        return {
            "clip_id": new_clip_id,
            "old_clip_id": clip_id,
            "status": "pending",
            "time_start": new_start,
            "time_end": new_end,
            "message": "Clip re-generation started with new boundaries",
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to trim clip: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to trim clip: {exc}")


@router.patch("/{video_id}/clips/{clip_id}/captions")
async def update_clip_captions(
    video_id: str,
    clip_id: str,
    request_body: dict,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Update clip caption text (stored for next re-generation).

    Body:
    {
        "captions": [
            {"start": 0.0, "end": 2.5, "text": "修正後テキスト", "emphasis": false},
            ...
        ]
    }
    """
    try:
        user_id = user.get("user_id") or user.get("id")
        captions = request_body.get("captions", [])

        if not captions:
            raise HTTPException(status_code=400, detail="captions array is required")

        # Verify clip exists
        sql = text("""
            SELECT id, video_id FROM video_clips
            WHERE id = :clip_id AND video_id = :video_id
        """)
        result = await db.execute(sql, {"clip_id": clip_id, "video_id": video_id})
        clip_row = result.fetchone()
        if not clip_row:
            raise HTTPException(status_code=404, detail="Clip not found")

        # Store captions as JSON in dedicated captions column
        import json as _json
        captions_json = _json.dumps(captions, ensure_ascii=False)

        update_sql = text("""
            UPDATE video_clips
            SET captions = CAST(:captions_json AS jsonb), updated_at = NOW()
            WHERE id = :clip_id
        """)
        await db.execute(update_sql, {"captions_json": captions_json, "clip_id": clip_id})
        await db.commit()

        logger.info(f"[CAPTIONS] Updated {len(captions)} captions for clip {clip_id}")

        return {
            "clip_id": clip_id,
            "captions_count": len(captions),
            "message": "Captions updated successfully",
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to update captions: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to update captions: {exc}")



# =========================
# Subtitle Feedback & Style API
# =========================

@router.post("/{video_id}/clips/{clip_id}/subtitle-feedback")
async def save_subtitle_feedback(
    video_id: str,
    clip_id: str,
    request_body: dict,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Save user feedback on subtitle style.

    Body:
    {
        "style": "box",
        "vote": "up",          // "up" | "down" | null
        "tags": ["見やすい", "おしゃれ"],
        "position": {"x": 50, "y": 85},
        "ai_recommended_style": "gradient"
    }
    """
    try:
        user_id = user.get("user_id") or user.get("id")
        style = request_body.get("style", "box")
        vote = request_body.get("vote")
        tags = request_body.get("tags", [])
        position = request_body.get("position", {})
        ai_recommended = request_body.get("ai_recommended_style")

        import json as _json
        tags_json = _json.dumps(tags, ensure_ascii=False)

        sql = text("""
            INSERT INTO subtitle_feedback
                (video_id, clip_id, user_id, subtitle_style, vote, tags,
                 position_x, position_y, ai_recommended_style)
            VALUES
                (:video_id, :clip_id, :user_id, :style, :vote, CAST(:tags AS jsonb),
                 :pos_x, :pos_y, :ai_recommended)
            RETURNING id
        """)
        result = await db.execute(sql, {
            "video_id": video_id,
            "clip_id": clip_id,
            "user_id": user_id,
            "style": style,
            "vote": vote,
            "tags": tags_json,
            "pos_x": position.get("x", 50),
            "pos_y": position.get("y", 85),
            "ai_recommended": ai_recommended,
        })
        row = result.fetchone()
        await db.commit()

        logger.info(f"[SUBTITLE_FEEDBACK] Saved feedback for clip {clip_id}: style={style}, vote={vote}, tags={tags}")

        return {
            "id": str(row.id) if row else None,
            "message": "Feedback saved successfully",
        }

    except Exception as exc:
        await db.rollback()
        logger.exception(f"Failed to save subtitle feedback: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to save subtitle feedback: {exc}")


@router.patch("/{video_id}/clips/{clip_id}/subtitle-style")
async def save_subtitle_style(
    video_id: str,
    clip_id: str,
    request_body: dict,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Save subtitle style and position for a clip.

    Body:
    {
        "style": "gradient",
        "position_x": 50,
        "position_y": 85
    }
    """
    try:
        style = request_body.get("style", "box")
        pos_x = request_body.get("position_x", 50)
        pos_y = request_body.get("position_y", 85)

        sql = text("""
            UPDATE video_clips
            SET subtitle_style = :style,
                subtitle_position_x = :pos_x,
                subtitle_position_y = :pos_y,
                updated_at = NOW()
            WHERE id = :clip_id AND video_id = :video_id
        """)
        await db.execute(sql, {
            "style": style,
            "pos_x": pos_x,
            "pos_y": pos_y,
            "clip_id": clip_id,
            "video_id": video_id,
        })
        await db.commit()

        logger.info(f"[SUBTITLE_STYLE] Saved style for clip {clip_id}: {style} at ({pos_x}, {pos_y})")

        return {
            "clip_id": clip_id,
            "subtitle_style": style,
            "position_x": pos_x,
            "position_y": pos_y,
            "message": "Subtitle style saved successfully",
        }

    except Exception as exc:
        await db.rollback()
        logger.exception(f"Failed to save subtitle style: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to save subtitle style: {exc}")


@router.get("/{video_id}/subtitle-recommend")
async def get_subtitle_recommendation(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Get AI-recommended subtitle style based on video metadata and user feedback history.
    Uses aggregated feedback data to personalize recommendations.
    """
    try:
        user_id = user.get("user_id") or user.get("id")

        # Get video metadata
        video_sql = text("""
            SELECT title, tags, status FROM videos WHERE id = :video_id
        """)
        vres = await db.execute(video_sql, {"video_id": video_id})
        video = vres.fetchone()

        # Get user's feedback history (most popular style by upvotes)
        feedback_sql = text("""
            SELECT subtitle_style, COUNT(*) as cnt
            FROM subtitle_feedback
            WHERE user_id = :user_id AND vote = 'up'
            GROUP BY subtitle_style
            ORDER BY cnt DESC
            LIMIT 3
        """)
        fres = await db.execute(feedback_sql, {"user_id": user_id})
        user_prefs = fres.fetchall()

        # Build recommendation
        recommendation = {
            "style": "box",
            "reason": "万能型・どんな動画にも合う",
            "confidence": 0.5,
            "source": "default",
        }

        # If user has feedback history, use their preferred style
        if user_prefs and len(user_prefs) > 0:
            top_style = user_prefs[0].subtitle_style
            recommendation = {
                "style": top_style,
                "reason": f"あなたが最もよく使うスタイル（{len(user_prefs)}件のフィードバックに基づく）",
                "confidence": min(0.9, 0.5 + len(user_prefs) * 0.1),
                "source": "user_feedback",
            }
        elif video:
            # Fallback to content-based recommendation
            title = (video.title or "").lower()
            tags = video.tags if video.tags else []
            tags_str = str(tags).lower()

            if any(kw in title or kw in tags_str for kw in ["美容", "コスメ", "スキンケア", "beauty"]):
                recommendation = {
                    "style": "gradient",
                    "reason": "美容系コンテンツに最適",
                    "confidence": 0.7,
                    "source": "content_analysis",
                }
            elif any(kw in title or kw in tags_str for kw in ["エンタメ", "お笑い", "バラエティ", "funny"]):
                recommendation = {
                    "style": "pop",
                    "reason": "エンタメ系に最適・インパクト大",
                    "confidence": 0.7,
                    "source": "content_analysis",
                }
            elif any(kw in title or kw in tags_str for kw in ["ビジネス", "解説", "教育"]):
                recommendation = {
                    "style": "simple",
                    "reason": "ビジネス系・読みやすさ重視",
                    "confidence": 0.7,
                    "source": "content_analysis",
                }

        return {
            "video_id": video_id,
            "recommendation": recommendation,
            "user_feedback_count": len(user_prefs) if user_prefs else 0,
        }

    except Exception as exc:
        logger.exception(f"Failed to get subtitle recommendation: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to get subtitle recommendation: {exc}")



# ─────────────────────────────────────────────────────────────────────
# ERROR LOG HISTORY
# ─────────────────────────────────────────────────────────────────────

@router.get("/{video_id}/error-logs")
async def get_video_error_logs(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Return all error log entries for a given video, newest first.
    Each entry includes: error_code, error_step, error_message, source, created_at.
    """
    try:
        # Verify the video belongs to the current user
        ownership = await db.execute(
            text("SELECT id FROM videos WHERE id = :vid AND user_id = :uid"),
            {"vid": video_id, "uid": user["id"]},
        )
        if not ownership.fetchone():
            raise HTTPException(status_code=404, detail="Video not found")

        # Fetch error logs
        result = await db.execute(
            text("""
                SELECT id, error_code, error_step, error_message, error_detail,
                       source, created_at
                FROM video_error_logs
                WHERE video_id = :vid
                ORDER BY created_at DESC
                LIMIT 100
            """),
            {"vid": video_id},
        )
        rows = result.fetchall()

        logs = []
        for r in rows:
            logs.append({
                "id": r.id,
                "error_code": r.error_code,
                "error_step": r.error_step,
                "error_message": r.error_message,
                "error_detail": r.error_detail,
                "source": r.source,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })

        return {"video_id": video_id, "error_logs": logs, "total": len(logs)}

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to get error logs for video {video_id}: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to get error logs: {exc}")

