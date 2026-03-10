"""
Background task that periodically detects stuck videos and auto-requeues them.

A video is considered "stuck" when:
  - Its status is QUEUED or STEP_* (processing)
  - Its `updated_at` has not changed for more than STUCK_THRESHOLD_MINUTES

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

# ── Configuration ─────────────────────────────────────────────────────
CHECK_INTERVAL_MINUTES = 10       # How often to check for stuck videos
STUCK_THRESHOLD_MINUTES = 30      # Minutes without update → considered stuck
MAX_AUTO_RETRIES = 3              # Max auto-requeue attempts per video

_monitor_task = None


async def _check_and_requeue_stuck_videos():
    """
    Core loop: every CHECK_INTERVAL_MINUTES, query for stuck videos and requeue.
    """
    # Wait a bit after startup before first check
    await asyncio.sleep(60)

    while True:
        try:
            from app.core.db import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                threshold = datetime.now(timezone.utc) - timedelta(minutes=STUCK_THRESHOLD_MINUTES)

                # Find videos that are stuck:
                # - Status is QUEUED or starts with STEP_
                # - updated_at is older than threshold
                # - dequeue_count (auto-retry counter) < MAX_AUTO_RETRIES
                sql = text("""
                    SELECT v.id, v.original_filename, v.status, v.user_id,
                           v.updated_at, v.dequeue_count,
                           u.email as user_email
                    FROM videos v
                    LEFT JOIN users u ON v.user_id = u.id
                    WHERE (v.status = 'QUEUED' OR v.status LIKE 'STEP_%')
                      AND v.updated_at < :threshold
                      AND COALESCE(v.dequeue_count, 0) < :max_retries
                    ORDER BY v.updated_at ASC
                    LIMIT 10
                """)
                result = await db.execute(sql, {
                    "threshold": threshold,
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

                    try:
                        # Generate fresh SAS URL
                        from app.services.storage_service import generate_download_sas
                        download_url, _expiry = await generate_download_sas(
                            email=row.user_email,
                            video_id=video_id,
                            filename=row.original_filename,
                            expires_in_minutes=1440,
                        )

                        # Reset status and increment retry counter
                        await db.execute(
                            text("""
                                UPDATE videos
                                SET status = 'uploaded',
                                    step_progress = 0,
                                    error_message = NULL,
                                    dequeue_count = COALESCE(dequeue_count, 0) + 1,
                                    updated_at = NOW()
                                WHERE id = :vid
                            """),
                            {"vid": video_id},
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
                                f"[stuck-monitor] Auto-requeued video {video_id} "
                                f"({row.original_filename}) was={old_status} "
                                f"retry={current_retries + 1}/{MAX_AUTO_RETRIES}"
                            )
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

                    except Exception as e:
                        logger.warning(
                            f"[stuck-monitor] Failed to requeue video {video_id}: {e}"
                        )
                        try:
                            await db.rollback()
                        except Exception:
                            pass

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
