"""
Background task that periodically detects stuck videos and auto-requeues them.

A video is considered "stuck" when:
  - Its status is 'uploaded', QUEUED, or STEP_* (processing)
  - Its `updated_at` has not changed for more than STUCK_THRESHOLD_MINUTES

Additionally, videos that failed during batch upload (status=ERROR but
enqueue_status=FAILED, meaning they were never sent to the worker queue)
are also detected and re-enqueued.

The monitor runs every CHECK_INTERVAL_MINUTES and requeues stuck videos
by generating a fresh SAS URL and pushing a new job to the Azure queue.
Each video is retried at most MAX_AUTO_RETRIES times to avoid infinite loops.
"""

import asyncio
import logging
import time
from contextlib import suppress
from datetime import datetime, timezone, timedelta

from sqlalchemy import text

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ── Configuration ─────────────────────────────────────────────────────────────
CHECK_INTERVAL_MINUTES = 5        # How often to check for stuck videos (was 10)
STUCK_THRESHOLD_MINUTES = 60      # Minutes without update → considered stuck
                                   # Raised from 30 to 60 to avoid false positives
                                   # during long video processing (9h+ videos have
                                   # slow FFmpeg steps that don't update DB frequently)
MAX_AUTO_RETRIES = 5              # Max auto-requeue attempts per video (was 3)
WORKER_GUARD_HOURS = 24           # Hours since worker_claimed_at to consider stale
                                   # Raised from 2 to 24 to match WORKER_VIDEO_TIMEOUT
                                   # (9h+ videos may take 17-33h to process)
NEVER_ENQUEUED_THRESHOLD_MINUTES = 10  # Minutes after creation to detect never-enqueued videos

_monitor_task = None


async def _requeue_video(db, row, video_id, old_status, current_retries, reason_prefix):
    """
    Shared logic to re-enqueue a single video.
    Returns True if successfully requeued, False otherwise.
    """
    try:
        from app.services.storage_service import generate_download_sas
        download_url, _expiry = await generate_download_sas(
            email=row.user_email,
            video_id=video_id,
            filename=row.original_filename,
            expires_in_minutes=1440,
        )

        # Keep current STEP_* status for resume, only reset
        # step_progress and increment retry counter.
        # For 'uploaded' or ERROR status, set to 'uploaded' so worker starts from step 0.
        new_status = old_status if old_status.startswith("STEP_") else "uploaded"
        await db.execute(
            text("""
                UPDATE videos
                SET status = :new_status,
                    step_progress = 0,
                    error_message = NULL,
                    enqueue_status = NULL,
                    enqueue_error = NULL,
                    dequeue_count = COALESCE(dequeue_count, 0) + 1,
                    updated_at = NOW()
                WHERE id = :vid
            """),
            {"vid": video_id, "new_status": new_status},
        )
        await db.commit()

        # Enqueue analysis job
        from app.services.queue_service import enqueue_job
        enqueue_result = await enqueue_job({
            "video_id": video_id,
            "blob_url": download_url,
            "original_filename": row.original_filename,
        })

        if enqueue_result.success:
            logger.info(
                f"[stuck-monitor] {reason_prefix} requeued video {video_id} "
                f"({row.original_filename}) was={old_status} "
                f"retry={current_retries + 1}/{MAX_AUTO_RETRIES}"
            )
            # Record event as error log
            try:
                await db.execute(
                    text("""
                        INSERT INTO video_error_logs
                            (video_id, error_code, error_step, error_message, source)
                        VALUES
                            (:vid, :code, :step, :msg, 'monitor')
                    """),
                    {
                        "vid": video_id,
                        "code": "STUCK_REQUEUE" if "stuck" in reason_prefix.lower() else "NEVER_ENQUEUED_REQUEUE",
                        "step": old_status or "UNKNOWN",
                        "msg": f"{reason_prefix}: video was {old_status}. "
                               f"Auto-requeued (retry {current_retries + 1}/{MAX_AUTO_RETRIES}).",
                    },
                )
                await db.commit()
            except Exception as log_err:
                logger.warning(f"[stuck-monitor] Failed to record error log: {log_err}")
            return True
        else:
            logger.warning(
                f"[stuck-monitor] Enqueue failed for {video_id}: "
                f"{enqueue_result.error}"
            )
            # Rollback status change
            await db.execute(
                text("""
                    UPDATE videos
                    SET status = :old_status,
                        updated_at = NOW()
                    WHERE id = :vid
                """),
                {"vid": video_id, "old_status": old_status},
            )
            await db.commit()
            return False

    except Exception as e:
        logger.warning(
            f"[stuck-monitor] Failed to requeue video {video_id}: {e}"
        )
        try:
            await db.rollback()
        except Exception as _e:
            logger.debug(f"Suppressed: {_e}")
        return False


async def _check_and_requeue_stuck_videos():
    """
    Core loop: every CHECK_INTERVAL_MINUTES, query for stuck videos and requeue.
    Also detects never-enqueued videos (batch upload failures) and requeues them.
    """
    # Wait a bit after startup before first check
    await asyncio.sleep(60)

    while True:
        try:
            from app.core.db import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                # ── Part 1: Stuck processing videos ──────────────────────────
                threshold = datetime.now(timezone.utc) - timedelta(minutes=STUCK_THRESHOLD_MINUTES)
                worker_guard = datetime.now(timezone.utc) - timedelta(hours=WORKER_GUARD_HOURS)

                sql = text("""
                    SELECT v.id, v.original_filename, v.status, v.user_id,
                           v.updated_at, v.dequeue_count,
                           v.worker_claimed_at,
                           u.email as user_email
                    FROM videos v
                    LEFT JOIN users u ON v.user_id = u.id
                    WHERE (v.status IN ('uploaded', 'QUEUED')
                           OR v.status LIKE 'STEP_%')
                      AND v.updated_at < :threshold
                      AND (v.worker_claimed_at IS NULL
                           OR v.worker_claimed_at < :worker_guard)
                      AND COALESCE(v.dequeue_count, 0) < :max_retries
                    ORDER BY v.updated_at ASC
                    LIMIT 10
                """)
                result = await db.execute(sql, {
                    "threshold": threshold,
                    "worker_guard": worker_guard,
                    "max_retries": MAX_AUTO_RETRIES,
                })
                stuck_videos = result.fetchall()

                if stuck_videos:
                    logger.info(
                        f"[stuck-monitor] Found {len(stuck_videos)} stuck video(s), "
                        f"threshold={STUCK_THRESHOLD_MINUTES}min"
                    )

                for row in stuck_videos:
                    video_id = str(row.id)
                    old_status = row.status
                    current_retries = row.dequeue_count or 0
                    await _requeue_video(
                        db, row, video_id, old_status, current_retries,
                        f"Stuck at {old_status} for >{STUCK_THRESHOLD_MINUTES}min"
                    )

                # ── Part 2: Never-enqueued videos (batch upload failures) ────
                # These are ERROR videos where enqueue_status='FAILED' or
                # enqueue_status IS NULL, meaning the worker queue never received them.
                # They were set to ERROR by a previous monitor cycle or by the
                # batch_upload_complete cascade failure.
                never_enqueued_threshold = datetime.now(timezone.utc) - timedelta(
                    minutes=NEVER_ENQUEUED_THRESHOLD_MINUTES
                )

                sql_never_enqueued = text("""
                    SELECT v.id, v.original_filename, v.status, v.user_id,
                           v.updated_at, v.dequeue_count,
                           v.worker_claimed_at, v.enqueue_status,
                           u.email as user_email
                    FROM videos v
                    LEFT JOIN users u ON v.user_id = u.id
                    WHERE v.status = 'ERROR'
                      AND (v.enqueue_status = 'FAILED' OR v.enqueue_status IS NULL)
                      AND v.worker_claimed_at IS NULL
                      AND v.created_at < :threshold
                      AND COALESCE(v.dequeue_count, 0) < :max_retries
                    ORDER BY v.created_at ASC
                    LIMIT 10
                """)
                result2 = await db.execute(sql_never_enqueued, {
                    "threshold": never_enqueued_threshold,
                    "max_retries": MAX_AUTO_RETRIES,
                })
                never_enqueued = result2.fetchall()

                if never_enqueued:
                    logger.info(
                        f"[stuck-monitor] Found {len(never_enqueued)} never-enqueued ERROR video(s)"
                    )

                for row in never_enqueued:
                    video_id = str(row.id)
                    old_status = row.status
                    current_retries = row.dequeue_count or 0
                    await _requeue_video(
                        db, row, video_id, old_status, current_retries,
                        f"Never enqueued (enqueue_status={row.enqueue_status})"
                    )

        except Exception as e:
            logger.warning(f"[stuck-monitor] Check cycle error: {e}")

        # Sleep until next check
        await asyncio.sleep(CHECK_INTERVAL_MINUTES * 60)


def start_stuck_video_monitor():
    """Start the background stuck video monitor. Call from app startup."""
    global _monitor_task
    if _monitor_task is None:
        _monitor_task = asyncio.create_task(_check_and_requeue_stuck_videos())
        logger.info(
            f"[stuck-monitor] Started (check every {CHECK_INTERVAL_MINUTES}min, "
            f"threshold {STUCK_THRESHOLD_MINUTES}min, max retries {MAX_AUTO_RETRIES})"
        )


def stop_stuck_video_monitor():
    """Stop the background stuck video monitor. Call from app shutdown."""
    global _monitor_task
    if _monitor_task:
        _monitor_task.cancel()
        with suppress(asyncio.CancelledError):
            pass
        _monitor_task = None
        logger.info("[stuck-monitor] Stopped")
