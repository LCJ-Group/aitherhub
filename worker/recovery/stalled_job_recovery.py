"""
Stalled Job Recovery
=====================
Detects and recovers stalled clip jobs (processing but no heartbeat).

Runs as a background thread in the worker process.
Checks every RECOVERY_CHECK_INTERVAL seconds for jobs where:
    - status IN ('downloading', 'processing', 'uploading')
    - heartbeat_at < NOW() - STALE_THRESHOLD seconds

Recovery actions:
    - If attempt_count < MAX_ATTEMPTS: set status = 'retrying', re-enqueue
    - If attempt_count >= MAX_ATTEMPTS: set status = 'dead'

Usage:
    recovery = StalledJobRecovery()
    recovery.start()
    # ...
    recovery.stop()
"""
import os
import sys
import json
import logging
from pathlib import Path
from threading import Thread, Event

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("worker.recovery")

# How often to check for stalled jobs (seconds)
RECOVERY_CHECK_INTERVAL = int(os.getenv("WORKER_RECOVERY_INTERVAL", "60"))

# A job is considered stalled if heartbeat is older than this (seconds)
STALE_THRESHOLD = int(os.getenv("WORKER_STALE_THRESHOLD", "120"))

# Maximum retry attempts before marking as dead
MAX_ATTEMPTS = int(os.getenv("WORKER_MAX_ATTEMPTS", "3"))


class StalledJobRecovery:
    """Background thread that detects and recovers stalled clip jobs."""

    def __init__(self, worker_id: str = "unknown"):
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._worker_id = worker_id

    def start(self):
        """Start the recovery background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = Thread(
            target=self._recovery_loop,
            daemon=True,
            name="stalled-recovery",
        )
        self._thread.start()
        logger.info(
            "[recovery] Started (check_interval=%ds, stale_threshold=%ds, max_attempts=%d)",
            RECOVERY_CHECK_INTERVAL, STALE_THRESHOLD, MAX_ATTEMPTS,
        )

    def stop(self):
        """Stop the recovery background thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("[recovery] Stopped")

    def _recovery_loop(self):
        """Background loop: check for stalled jobs and recover them."""
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=RECOVERY_CHECK_INTERVAL)
            if self._stop_event.is_set():
                break

            try:
                self._check_and_recover()
            except Exception as e:
                logger.error("[recovery] Unexpected error: %s", e)

    def _check_and_recover(self):
        """Find stalled jobs and take recovery action."""
        try:
            from shared.db.session import get_session, run_sync
            from shared.schemas.clip_job import get_stale_clip_jobs
            from sqlalchemy import text

            stale_jobs = run_sync(self._fetch_stale_jobs())

            if not stale_jobs:
                return

            logger.warning(
                "[recovery] Found %d stalled job(s)", len(stale_jobs)
            )

            for job in stale_jobs:
                clip_id = job["id"]
                attempt_count = job.get("attempt_count", 0)
                video_id = job.get("video_id", "unknown")
                old_worker = job.get("worker_id", "unknown")

                if attempt_count >= MAX_ATTEMPTS:
                    # Mark as dead — no more retries
                    run_sync(self._mark_dead(clip_id, attempt_count, old_worker))
                    logger.error(
                        "[recovery] DEAD: clip=%s video=%s attempts=%d/%d (was on worker=%s)",
                        clip_id, video_id, attempt_count, MAX_ATTEMPTS, old_worker,
                    )
                else:
                    # Mark as retrying — will be picked up by queue again
                    run_sync(self._mark_retrying(clip_id, attempt_count, old_worker))
                    self._re_enqueue_clip(clip_id, video_id, job.get("job_payload"))
                    logger.warning(
                        "[recovery] RETRY: clip=%s video=%s attempt=%d/%d (was on worker=%s)",
                        clip_id, video_id, attempt_count + 1, MAX_ATTEMPTS, old_worker,
                    )

        except Exception as e:
            logger.error("[recovery] Failed to check stale jobs: %s", e)

    async def _fetch_stale_jobs(self) -> list:
        """Fetch stalled jobs from DB."""
        from shared.db.session import get_session
        from sqlalchemy import text

        async with get_session() as session:
            result = await session.execute(
                text("""
                    SELECT id, video_id, worker_id, status,
                           heartbeat_at, started_at, attempt_count, job_payload
                    FROM video_clips
                    WHERE status IN ('downloading', 'processing', 'uploading')
                      AND (
                          heartbeat_at IS NULL
                          OR heartbeat_at < NOW() - MAKE_INTERVAL(secs => :stale_seconds)
                      )
                    ORDER BY started_at ASC
                """),
                {"stale_seconds": STALE_THRESHOLD},
            )
            return [dict(row._mapping) for row in result.fetchall()]

    async def _mark_dead(self, clip_id: str, attempt_count: int, old_worker: str):
        """Mark a clip job as dead (exhausted all retries)."""
        from shared.db.session import get_session
        from sqlalchemy import text

        async with get_session() as session:
            await session.execute(
                text("""
                    UPDATE video_clips
                    SET status = 'dead',
                        last_error_code = 'STALLED_DEAD',
                        last_error_message = :msg,
                        finished_at = NOW(),
                        updated_at = NOW()
                    WHERE id = :clip_id
                """),
                {
                    "clip_id": clip_id,
                    "msg": f"Job stalled {attempt_count} times. "
                           f"Last worker: {old_worker}. Marked dead by recovery.",
                },
            )

    async def _mark_retrying(self, clip_id: str, attempt_count: int, old_worker: str):
        """Mark a clip job as retrying."""
        from shared.db.session import get_session
        from sqlalchemy import text

        async with get_session() as session:
            await session.execute(
                text("""
                    UPDATE video_clips
                    SET status = 'retrying',
                        worker_id = NULL,
                        heartbeat_at = NULL,
                        last_error_code = 'STALLED_RETRY',
                        last_error_message = :msg,
                        updated_at = NOW()
                    WHERE id = :clip_id
                """),
                {
                    "clip_id": clip_id,
                    "msg": f"Job stalled on worker {old_worker}. "
                           f"Attempt {attempt_count}/{MAX_ATTEMPTS}. Retrying.",
                },
            )

    def _re_enqueue_clip(self, clip_id: str, video_id: str, job_payload: dict = None):
        """Re-enqueue a clip job to the queue for retry.

        Uses job_payload from DB if available (contains blob_url and other fields).
        Falls back to minimal payload if job_payload is not available.
        """
        try:
            from shared.queue.client import get_queue_client

            if job_payload and isinstance(job_payload, dict):
                # Use the full payload from DB (has blob_url, time_start, etc.)
                payload = {**job_payload, "retry": True}
            else:
                # Fallback: minimal payload (DB fallback will pick it up instead)
                payload = {
                    "job_type": "generate_clip",
                    "clip_id": clip_id,
                    "video_id": video_id,
                    "retry": True,
                }
                logger.warning("[recovery] No job_payload for clip %s, using minimal payload", clip_id)

            client = get_queue_client()
            client.send_message(json.dumps(payload, ensure_ascii=False))
            logger.info("[recovery] Re-enqueued clip %s for retry", clip_id)
        except Exception as e:
            logger.error("[recovery] Failed to re-enqueue clip %s: %s", clip_id, e)
