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
WORKER_GUARD_HOURS = 4            # Hours since worker_claimed_at to consider stale
                                   # Reduced from 24 to 4 to detect deploy-interrupted
                                   # videos faster. Even the longest videos (9h+) should
                                   # complete within 4h on GPU-accelerated worker.
NEVER_ENQUEUED_THRESHOLD_MINUTES = 10  # Minutes after creation to detect never-enqueued videos
SAS_RETRY_COUNT = 3               # Number of retries for SAS URL generation
SAS_RETRY_DELAY_SECONDS = 5       # Delay between SAS retries

_monitor_task = None


async def _generate_sas_with_retry(email, video_id, filename, retries=SAS_RETRY_COUNT):
    """
    Generate a download SAS URL with retry logic.
    Azure Blob Storage can have transient connection errors; retrying
    prevents a single network hiccup from permanently failing the video.
    """
    from app.services.storage_service import generate_download_sas

    last_error = None
    for attempt in range(1, retries + 1):
        try:
            download_url, expiry = await generate_download_sas(
                email=email,
                video_id=video_id,
                filename=filename,
                expires_in_minutes=1440,
            )
            if attempt > 1:
                logger.info(
                    f"[stuck-monitor] SAS URL generated on attempt {attempt}/{retries} "
                    f"for video {video_id}"
                )
            return download_url, expiry
        except Exception as e:
            last_error = e
            logger.warning(
                f"[stuck-monitor] SAS URL generation attempt {attempt}/{retries} "
                f"failed for video {video_id}: {e}"
            )
            if attempt < retries:
                await asyncio.sleep(SAS_RETRY_DELAY_SECONDS)

    raise last_error


async def _increment_dequeue_count(db, video_id):
    """
    Increment dequeue_count for a video, even when requeue fails.
    This prevents infinite retry loops where dequeue_count never increases
    because the SAS URL generation or enqueue keeps failing.
    """
    try:
        await db.execute(
            text("""
                UPDATE videos
                SET dequeue_count = COALESCE(dequeue_count, 0) + 1,
                    updated_at = NOW()
                WHERE id = :vid
            """),
            {"vid": video_id},
        )
        await db.commit()
    except Exception as e:
        logger.warning(f"[stuck-monitor] Failed to increment dequeue_count for {video_id}: {e}")
        try:
            await db.rollback()
        except Exception:
            pass


async def _record_requeue_error(db, video_id, error_code, old_status, error_message, current_retries):
    """
    Record a requeue failure to video_error_logs for observability.
    Also updates last_error_code and last_error_message on the video record.
    """
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
                "code": error_code,
                "step": old_status or "UNKNOWN",
                "msg": f"{error_message} (retry {current_retries + 1}/{MAX_AUTO_RETRIES})",
            },
        )
        await db.commit()
    except Exception as log_err:
        logger.warning(f"[stuck-monitor] Failed to record error log for {video_id}: {log_err}")
        try:
            await db.rollback()
        except Exception:
            pass

    # Also update last_error_code on the video for AI context visibility
    try:
        await db.execute(
            text("""
                UPDATE videos
                SET last_error_code = :code,
                    last_error_message = :msg
                WHERE id = :vid
            """),
            {
                "vid": video_id,
                "code": error_code,
                "msg": error_message[:2000] if error_message else "",
            },
        )
        await db.commit()
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass


async def _requeue_video(db, row, video_id, old_status, current_retries, reason_prefix):
    """
    Shared logic to re-enqueue a single video.
    Returns True if successfully requeued, False otherwise.

    Key improvement: dequeue_count is ALWAYS incremented, even on failure,
    to prevent infinite retry loops. Previously, if SAS URL generation failed
    with an exception, dequeue_count was never incremented because the DB
    update was rolled back.
    """
    try:
        # Step 1: Generate SAS URL with retry
        try:
            download_url, _expiry = await _generate_sas_with_retry(
                email=row.user_email,
                video_id=video_id,
                filename=row.original_filename,
            )
        except Exception as sas_err:
            # SAS generation failed after all retries
            logger.error(
                f"[stuck-monitor] SAS URL generation failed for {video_id} "
                f"after {SAS_RETRY_COUNT} retries: {sas_err}"
            )
            # CRITICAL FIX: Always increment dequeue_count to prevent infinite loops
            await _increment_dequeue_count(db, video_id)
            # Record the error for observability
            await _record_requeue_error(
                db, video_id, "SAS_GENERATION_FAILED", old_status,
                f"SAS URL generation failed after {SAS_RETRY_COUNT} retries: {sas_err}",
                current_retries,
            )
            return False

        # Step 2: Update video status and increment retry counter
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
                    last_error_code = NULL,
                    last_error_message = NULL,
                    dequeue_count = COALESCE(dequeue_count, 0) + 1,
                    updated_at = NOW()
                WHERE id = :vid
            """),
            {"vid": video_id, "new_status": new_status},
        )
        await db.commit()

        # Step 3: Enqueue analysis job
        from app.services.queue_service import enqueue_job
        enqueue_result = await enqueue_job({
            "video_id": video_id,
            "blob_url": download_url,
            "original_filename": row.original_filename,
        })

        if enqueue_result.success:
            # Update enqueue_status to OK
            try:
                await db.execute(
                    text("""
                        UPDATE videos
                        SET enqueue_status = 'OK',
                            queue_message_id = :msg_id,
                            queue_enqueued_at = :enqueued_at,
                            enqueue_error = NULL
                        WHERE id = :vid
                    """),
                    {
                        "vid": video_id,
                        "msg_id": enqueue_result.message_id,
                        "enqueued_at": enqueue_result.enqueued_at,
                    },
                )
                await db.commit()
            except Exception as db_err:
                logger.warning(f"[stuck-monitor] Failed to update enqueue_status: {db_err}")
                try:
                    await db.rollback()
                except Exception:
                    pass

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
            # Rollback status change but keep the incremented dequeue_count
            await db.execute(
                text("""
                    UPDATE videos
                    SET status = :old_status,
                        enqueue_status = 'FAILED',
                        enqueue_error = :error,
                        updated_at = NOW()
                    WHERE id = :vid
                """),
                {
                    "vid": video_id,
                    "old_status": old_status,
                    "error": (enqueue_result.error or "")[:2000],
                },
            )
            await db.commit()
            # Record the error
            await _record_requeue_error(
                db, video_id, "ENQUEUE_FAILED", old_status,
                f"Queue enqueue failed: {enqueue_result.error}",
                current_retries,
            )
            return False

    except Exception as e:
        logger.warning(
            f"[stuck-monitor] Failed to requeue video {video_id}: {e}"
        )
        try:
            await db.rollback()
        except Exception as _e:
            logger.debug(f"Suppressed: {_e}")

        # CRITICAL FIX: Even on unexpected exception, try to increment dequeue_count
        # to prevent infinite retry loops
        try:
            await _increment_dequeue_count(db, video_id)
        except Exception:
            pass

        # Record the error
        try:
            await _record_requeue_error(
                db, video_id, "REQUEUE_EXCEPTION", old_status,
                f"Unexpected error during requeue: {e}",
                current_retries,
            )
        except Exception:
            pass

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

                # ── Part 1b: Deploy-interrupted videos ─────────────────────
                # Videos that were interrupted by SIGTERM during deployment.
                # These have last_error_code='DEPLOY_SIGTERM' and are NOT in ERROR status.
                # They should be requeued immediately regardless of worker_guard.
                sql_deploy_interrupted = text("""
                    SELECT v.id, v.original_filename, v.status, v.user_id,
                           v.updated_at, v.dequeue_count,
                           v.worker_claimed_at,
                           u.email as user_email
                    FROM videos v
                    LEFT JOIN users u ON v.user_id = u.id
                    WHERE v.last_error_code = 'DEPLOY_SIGTERM'
                      AND v.status NOT IN ('ERROR', 'completed')
                      AND COALESCE(v.dequeue_count, 0) < :max_retries
                    ORDER BY v.updated_at ASC
                    LIMIT 10
                """)
                result_deploy = await db.execute(sql_deploy_interrupted, {
                    "max_retries": MAX_AUTO_RETRIES,
                })
                deploy_interrupted = result_deploy.fetchall()

                if deploy_interrupted:
                    logger.info(
                        f"[stuck-monitor] Found {len(deploy_interrupted)} deploy-interrupted video(s)"
                    )

                for row in deploy_interrupted:
                    video_id = str(row.id)
                    old_status = row.status
                    current_retries = row.dequeue_count or 0
                    await _requeue_video(
                        db, row, video_id, old_status, current_retries,
                        f"Deploy-interrupted (SIGTERM) at {old_status}"
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
