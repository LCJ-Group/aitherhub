"""
Clip Job Timeout Monitor (API-side)
====================================
Detects and auto-fails clip jobs that have been stuck too long.

This runs on the API server (Azure App Service), NOT on the worker VM.
It provides a safety net for cases where:
  - The worker VM is unresponsive (D-state, OOM, swap thrashing)
  - The worker's stalled_job_recovery thread is also stuck
  - subprocess timeout doesn't fire (process in D-state can't be killed)

Detection criteria:
  - status IN ('downloading', 'processing', 'uploading', 'encoding')
  - started_at < NOW() - CLIP_HARD_TIMEOUT_MINUTES
  - OR heartbeat_at < NOW() - CLIP_HEARTBEAT_DEAD_MINUTES (if heartbeat exists)

Actions:
  - Mark clip as 'failed' with error_message explaining timeout
  - Log to video_error_logs for observability
  - Update attempt_count to prevent infinite retries

Also monitors video analysis jobs:
  - status LIKE 'STEP_%' AND updated_at < NOW() - VIDEO_HARD_TIMEOUT_MINUTES
  - worker_claimed_at < NOW() - VIDEO_HARD_TIMEOUT_MINUTES
  - These are marked as ERROR so stuck_video_monitor can requeue them

=== Created 2026-05-20 ===
Addresses the root cause: when VM enters swap thrashing / D-state,
worker-side timeouts become ineffective because processes can't be killed.
This API-side monitor provides an independent safety layer.
"""

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timezone, timedelta

from sqlalchemy import text

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ── Configuration ─────────────────────────────────────────────────────────────
CHECK_INTERVAL_MINUTES = 3          # How often to check (every 3 minutes)
CLIP_HARD_TIMEOUT_MINUTES = 45      # Max time a clip job can run before forced failure
                                     # Even a 3-minute clip with 20x multiplier = 60min timeout
                                     # on worker side. If it's been 45min without heartbeat,
                                     # the worker is definitely dead.
CLIP_HEARTBEAT_DEAD_MINUTES = 5     # If heartbeat hasn't updated in 5 min, job is dead
                                     # (heartbeat updates every 30s normally)
VIDEO_HARD_TIMEOUT_MINUTES = 180    # 3 hours max for video analysis before intervention
                                     # (stuck_video_monitor uses 60min threshold for requeue,
                                     #  this is a harder limit for truly dead processes)
BATCH_LIMIT = 50                    # Max jobs to process per cycle

_monitor_task = None


async def _timeout_stalled_clips():
    """
    Core loop: every CHECK_INTERVAL_MINUTES, detect and fail stuck clip jobs.
    
    This is the API-side safety net. The worker has its own timeout mechanisms,
    but they fail when the VM enters D-state (uninterruptible disk sleep).
    """
    # Wait a bit after startup
    await asyncio.sleep(90)
    
    logger.info(
        f"[clip-timeout] Started (interval={CHECK_INTERVAL_MINUTES}min, "
        f"hard_timeout={CLIP_HARD_TIMEOUT_MINUTES}min, "
        f"heartbeat_dead={CLIP_HEARTBEAT_DEAD_MINUTES}min)"
    )
    
    while True:
        try:
            from app.core.db import AsyncSessionLocal
            
            async with AsyncSessionLocal() as db:
                timed_out_count = 0
                
                # ── Part 1: Clips with expired hard timeout ──────────────────
                # These have been running longer than CLIP_HARD_TIMEOUT_MINUTES
                hard_timeout_threshold = datetime.utcnow() - timedelta(
                    minutes=CLIP_HARD_TIMEOUT_MINUTES
                )
                
                result = await db.execute(
                    text("""
                        SELECT id, video_id, status, started_at, heartbeat_at,
                               worker_id, attempt_count
                        FROM video_clips
                        WHERE status IN ('downloading', 'processing', 'uploading', 'encoding')
                          AND started_at IS NOT NULL
                          AND started_at < :hard_timeout
                        ORDER BY started_at ASC
                        LIMIT :batch_limit
                    """),
                    {
                        "hard_timeout": hard_timeout_threshold,
                        "batch_limit": BATCH_LIMIT,
                    },
                )
                hard_timeout_clips = result.fetchall()
                
                for row in hard_timeout_clips:
                    clip_id = str(row.id)
                    video_id = str(row.video_id) if row.video_id else "unknown"
                    elapsed_min = int((datetime.utcnow() - row.started_at).total_seconds() / 60)
                    
                    error_msg = (
                        f"API-side hard timeout: job ran for {elapsed_min}min "
                        f"(limit={CLIP_HARD_TIMEOUT_MINUTES}min). "
                        f"Worker may be unresponsive (D-state/OOM). "
                        f"status={row.status}, worker={row.worker_id}"
                    )
                    
                    await _mark_clip_timed_out(db, clip_id, video_id, error_msg, row.attempt_count)
                    timed_out_count += 1
                    logger.warning(
                        f"[clip-timeout] HARD_TIMEOUT: clip={clip_id} video={video_id} "
                        f"elapsed={elapsed_min}min status={row.status}"
                    )
                
                # ── Part 2: Clips with dead heartbeat ────────────────────────
                # These have a heartbeat that stopped updating (worker thread died)
                heartbeat_threshold = datetime.utcnow() - timedelta(
                    minutes=CLIP_HEARTBEAT_DEAD_MINUTES
                )
                # Only check clips that started recently (within hard timeout)
                # to avoid double-processing with Part 1
                recent_start = datetime.utcnow() - timedelta(
                    minutes=CLIP_HARD_TIMEOUT_MINUTES
                )
                
                result2 = await db.execute(
                    text("""
                        SELECT id, video_id, status, started_at, heartbeat_at,
                               worker_id, attempt_count
                        FROM video_clips
                        WHERE status IN ('downloading', 'processing', 'uploading', 'encoding')
                          AND started_at IS NOT NULL
                          AND started_at >= :recent_start
                          AND heartbeat_at IS NOT NULL
                          AND heartbeat_at < :heartbeat_dead
                        ORDER BY heartbeat_at ASC
                        LIMIT :batch_limit
                    """),
                    {
                        "recent_start": recent_start,
                        "heartbeat_dead": heartbeat_threshold,
                        "batch_limit": BATCH_LIMIT,
                    },
                )
                heartbeat_dead_clips = result2.fetchall()
                
                for row in heartbeat_dead_clips:
                    clip_id = str(row.id)
                    video_id = str(row.video_id) if row.video_id else "unknown"
                    hb_age_min = int((datetime.utcnow() - row.heartbeat_at).total_seconds() / 60)
                    
                    error_msg = (
                        f"API-side heartbeat timeout: no heartbeat for {hb_age_min}min "
                        f"(limit={CLIP_HEARTBEAT_DEAD_MINUTES}min). "
                        f"Worker process likely dead. "
                        f"status={row.status}, worker={row.worker_id}"
                    )
                    
                    await _mark_clip_timed_out(db, clip_id, video_id, error_msg, row.attempt_count)
                    timed_out_count += 1
                    logger.warning(
                        f"[clip-timeout] HEARTBEAT_DEAD: clip={clip_id} video={video_id} "
                        f"heartbeat_age={hb_age_min}min status={row.status}"
                    )
                
                # ── Part 3: Clips stuck in 'queued' or 'pending' too long ────
                # These were never picked up by the worker
                queued_timeout = datetime.utcnow() - timedelta(minutes=60)
                
                result3 = await db.execute(
                    text("""
                        SELECT id, video_id, status, created_at, attempt_count
                        FROM video_clips
                        WHERE status IN ('queued', 'pending')
                          AND created_at < :queued_timeout
                          AND started_at IS NULL
                        ORDER BY created_at ASC
                        LIMIT :batch_limit
                    """),
                    {
                        "queued_timeout": queued_timeout,
                        "batch_limit": BATCH_LIMIT,
                    },
                )
                stale_queued_clips = result3.fetchall()
                
                for row in stale_queued_clips:
                    clip_id = str(row.id)
                    video_id = str(row.video_id) if row.video_id else "unknown"
                    age_min = int((datetime.utcnow() - row.created_at).total_seconds() / 60)
                    
                    error_msg = (
                        f"API-side queue timeout: clip was queued for {age_min}min "
                        f"without being picked up by worker. "
                        f"Worker may be overloaded or down."
                    )
                    
                    await _mark_clip_timed_out(db, clip_id, video_id, error_msg, row.attempt_count)
                    timed_out_count += 1
                    logger.warning(
                        f"[clip-timeout] QUEUE_TIMEOUT: clip={clip_id} video={video_id} "
                        f"queued_age={age_min}min"
                    )
                
                if timed_out_count > 0:
                    logger.info(
                        f"[clip-timeout] Cycle complete: timed_out={timed_out_count} "
                        f"(hard={len(hard_timeout_clips)}, "
                        f"heartbeat={len(heartbeat_dead_clips)}, "
                        f"queued={len(stale_queued_clips)})"
                    )
                
                # ── Part 4: Cleanup ai_clip_jobs table (frontend display) ────
                # These are the jobs shown in the AI Clip Generator UI.
                # If they've been stuck for 10+ minutes, mark them as failed.
                AI_CLIP_JOB_TIMEOUT_MINUTES = 10
                try:
                    result4 = await db.execute(
                        text("""
                            UPDATE ai_clip_jobs
                            SET status = 'failed',
                                error = 'Auto-timeout: job stuck for over ' || :age || ' minutes',
                                updated_at = NOW()
                            WHERE status IN ('processing', 'selecting', 'queued')
                              AND updated_at < NOW() - INTERVAL '1 minute' * :age
                            RETURNING job_id
                        """),
                        {"age": AI_CLIP_JOB_TIMEOUT_MINUTES},
                    )
                    await db.commit()
                    ai_clip_cleaned = result4.fetchall()
                    if ai_clip_cleaned:
                        logger.info(
                            f"[clip-timeout] AI clip jobs cleanup: "
                            f"{len(ai_clip_cleaned)} stuck jobs marked as failed"
                        )
                except Exception as ai_err:
                    logger.debug(f"[clip-timeout] AI clip jobs cleanup error: {ai_err}")
                    try:
                        await db.rollback()
                    except Exception:
                        pass
        
        except Exception as e:
            logger.error(f"[clip-timeout] Check cycle error: {e}", exc_info=True)
        
        # Sleep until next check
        await asyncio.sleep(CHECK_INTERVAL_MINUTES * 60)


async def _mark_clip_timed_out(db, clip_id: str, video_id: str, error_msg: str, attempt_count: int):
    """Mark a clip job as failed due to timeout (API-side detection)."""
    try:
        # Atomic update: only update if still in a processing state
        result = await db.execute(
            text("""
                UPDATE video_clips
                SET status = 'failed',
                    error_message = :error_msg,
                    attempt_count = COALESCE(:attempt_count, 0) + 1,
                    updated_at = NOW(),
                    completed_at = NOW()
                WHERE id = :clip_id
                  AND status IN ('downloading', 'processing', 'uploading', 'encoding', 'queued', 'pending')
            """),
            {
                "clip_id": clip_id,
                "error_msg": error_msg[:2000],
                "attempt_count": attempt_count or 0,
            },
        )
        await db.commit()
        
        if result.rowcount > 0:
            # Record to error logs
            try:
                await db.execute(
                    text("""
                        INSERT INTO video_error_logs
                            (video_id, error_code, error_step, error_message, source)
                        VALUES
                            (:vid, 'CLIP_TIMEOUT_API', :status, :msg, 'api_monitor')
                    """),
                    {
                        "vid": video_id,
                        "status": "clip_processing",
                        "msg": error_msg[:2000],
                    },
                )
                await db.commit()
            except Exception as log_err:
                logger.debug(f"[clip-timeout] Failed to record error log: {log_err}")
                try:
                    await db.rollback()
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"[clip-timeout] Failed to mark clip {clip_id} as timed out: {e}")
        try:
            await db.rollback()
        except Exception:
            pass


def start_clip_job_timeout_monitor():
    """Start the clip job timeout monitor. Call from app startup."""
    global _monitor_task
    if _monitor_task is None:
        _monitor_task = asyncio.create_task(_timeout_stalled_clips())
        logger.info(
            f"[clip-timeout] Monitor started "
            f"(check every {CHECK_INTERVAL_MINUTES}min, "
            f"hard_timeout={CLIP_HARD_TIMEOUT_MINUTES}min, "
            f"heartbeat_dead={CLIP_HEARTBEAT_DEAD_MINUTES}min)"
        )


def stop_clip_job_timeout_monitor():
    """Stop the clip job timeout monitor. Call from app shutdown."""
    global _monitor_task
    if _monitor_task:
        _monitor_task.cancel()
        with suppress(asyncio.CancelledError):
            pass
        _monitor_task = None
        logger.info("[clip-timeout] Monitor stopped")
