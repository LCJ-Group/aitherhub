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
import asyncio
import os
import sys
import json
import logging
from datetime import datetime
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


def _create_thread_local_engine():
    """Create a dedicated async engine for the daemon thread.
    
    This avoids sharing the main thread's event loop and connection pool,
    which causes 'event loop is already running' or 'attached to a different loop' errors.
    """
    from shared.config import DATABASE_URL, prepare_database_url
    from sqlalchemy.ext.asyncio import create_async_engine

    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set.")

    cleaned_url, connect_args = prepare_database_url(DATABASE_URL)
    return create_async_engine(
        cleaned_url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=3,
        pool_recycle=300,
        echo=False,
        connect_args=connect_args,
    )


class StalledJobRecovery:
    """Background thread that detects and recovers stalled clip jobs."""

    def __init__(self, worker_id: str = "unknown"):
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._worker_id = worker_id
        self._loop: asyncio.AbstractEventLoop | None = None
        self._engine = None

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
        # Cleanup the dedicated engine
        if self._engine and self._loop and not self._loop.is_closed():
            try:
                self._loop.run_until_complete(self._engine.dispose())
            except Exception:
                pass
            self._loop.close()
        logger.info("[recovery] Stopped")

    def _recovery_loop(self):
        """Background loop: check for stalled jobs and recover them.
        
        Creates a dedicated event loop and DB engine for this thread.
        """
        # Create a dedicated event loop for this thread
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._engine = _create_thread_local_engine()
        except Exception as e:
            logger.error("[recovery] Failed to create DB engine: %s", e)
            return

        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=RECOVERY_CHECK_INTERVAL)
            if self._stop_event.is_set():
                break

            try:
                self._loop.run_until_complete(self._check_and_recover())
            except Exception as e:
                logger.error("[recovery] Unexpected error: %s", e)

    async def _check_and_recover(self):
        """Find stalled jobs and take recovery action."""
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy import text

        factory = sessionmaker(bind=self._engine, class_=AsyncSession, expire_on_commit=False)

        try:
            stale_jobs = await self._fetch_stale_jobs(factory)

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
                    await self._mark_dead(factory, clip_id, attempt_count, old_worker)
                    logger.error(
                        "[recovery] DEAD: clip=%s video=%s attempts=%d/%d (was on worker=%s)",
                        clip_id, video_id, attempt_count, MAX_ATTEMPTS, old_worker,
                    )
                else:
                    # Mark as retrying — will be picked up by queue again
                    await self._mark_retrying(factory, clip_id, attempt_count, old_worker)
                    self._re_enqueue_clip(clip_id, video_id, job.get("job_payload"))
                    logger.warning(
                        "[recovery] RETRY: clip=%s video=%s attempt=%d/%d (was on worker=%s)",
                        clip_id, video_id, attempt_count + 1, MAX_ATTEMPTS, old_worker,
                    )

        except Exception as e:
            logger.error("[recovery] Failed to check stale jobs: %s", e)

    async def _fetch_stale_jobs(self, factory) -> list:
        """Fetch stalled jobs from DB."""
        from sqlalchemy import text

        async with factory() as session:
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

    async def _mark_dead(self, factory, clip_id: str, attempt_count: int, old_worker: str):
        """Mark a clip job as dead (exhausted all retries)."""
        from sqlalchemy import text

        async with factory() as session:
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
            await session.commit()

    async def _mark_retrying(self, factory, clip_id: str, attempt_count: int, old_worker: str):
        """Mark a clip job as retrying."""
        from sqlalchemy import text

        async with factory() as session:
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
            await session.commit()

    @staticmethod
    def _refresh_sas_url(blob_url: str) -> str:
        """Regenerate a fresh SAS token for an Azure Blob URL if expired or expiring soon.

        Returns the original URL if no SAS token is present, regeneration fails,
        or the token is still valid (>30 min remaining).
        """
        if not blob_url or "?" not in blob_url or "sig=" not in blob_url:
            return blob_url

        try:
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(blob_url)
            params = parse_qs(parsed.query)
            se_values = params.get("se", [])
            if se_values:
                expiry_str = se_values[0]
                expiry_dt = datetime.strptime(expiry_str, "%Y-%m-%dT%H:%M:%SZ")
                now = datetime.utcnow()
                remaining = (expiry_dt - now).total_seconds()
                if remaining > 1800:  # More than 30 min remaining
                    return blob_url
                logger.warning(
                    "[recovery][SAS] Token expired/expiring (%d min), regenerating for clip",
                    int(remaining / 60),
                )
        except Exception as e:
            logger.warning("[recovery][SAS] Could not parse SAS expiry: %s", e)

        # Regenerate
        try:
            conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
            if not conn_str:
                logger.warning("[recovery][SAS] AZURE_STORAGE_CONNECTION_STRING not set, skipping")
                return blob_url

            from urllib.parse import urlparse, unquote
            from azure.storage.blob import generate_blob_sas, BlobSasPermissions

            base_url = blob_url.split("?")[0]
            parsed = urlparse(base_url)
            path_parts = parsed.path.lstrip("/").split("/", 1)
            container = path_parts[0] if path_parts else "videos"
            blob_path = unquote(path_parts[1]) if len(path_parts) > 1 else ""

            if not blob_path:
                return blob_url

            account_name = account_key = None
            for part in conn_str.split(";"):
                if part.startswith("AccountName="):
                    account_name = part.split("=", 1)[1]
                if part.startswith("AccountKey="):
                    account_key = part.split("=", 1)[1]

            if not account_name or not account_key:
                return blob_url

            from datetime import timedelta
            expiry = datetime.utcnow() + timedelta(hours=24)
            sas_token = generate_blob_sas(
                account_name=account_name,
                container_name=container,
                blob_name=blob_path,
                account_key=account_key,
                permission=BlobSasPermissions(read=True),
                expiry=expiry,
            )
            new_url = f"https://{account_name}.blob.core.windows.net/{container}/{blob_path}?{sas_token}"
            logger.info("[recovery][SAS] Regenerated fresh SAS URL (expires in 24h)")
            return new_url
        except Exception as e:
            logger.error("[recovery][SAS] Failed to regenerate: %s", e)
            return blob_url

    def _re_enqueue_clip(self, clip_id: str, video_id: str, job_payload: dict = None):
        """Re-enqueue a clip job to the queue for retry.

        Uses job_payload from DB if available (contains blob_url and other fields).
        Refreshes SAS token on blob_url before re-enqueuing to avoid download failures.
        Falls back to minimal payload if job_payload is not available.
        """
        try:
            from shared.queue.client import get_queue_client

            if job_payload and isinstance(job_payload, dict):
                # Refresh SAS token on blob_url before re-enqueuing
                if "blob_url" in job_payload:
                    job_payload["blob_url"] = self._refresh_sas_url(job_payload["blob_url"])
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
