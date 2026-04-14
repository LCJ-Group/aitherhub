"""
Heartbeat Manager
==================
Manages heartbeat updates for active clip jobs.

Usage:
    hb = HeartbeatManager()
    hb.start()

    # When a job starts processing
    hb.register_job("clip-123")

    # When a job finishes (success or failure)
    hb.unregister_job("clip-123")

    # On shutdown
    hb.stop()

The manager runs a background thread that updates heartbeat_at in the DB
every HEARTBEAT_INTERVAL seconds for all registered jobs.
"""
import os
import sys
import time
import logging
from pathlib import Path
from threading import Thread, Lock, Event

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("worker.heartbeat")

HEARTBEAT_INTERVAL = int(os.getenv("WORKER_HEARTBEAT_INTERVAL", "30"))


class HeartbeatManager:
    """Background heartbeat updater for active clip jobs."""

    def __init__(self):
        self._active_jobs: set = set()
        self._lock = Lock()
        self._stop_event = Event()
        self._thread: Thread | None = None

    def start(self):
        """Start the heartbeat background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name="heartbeat-manager",
        )
        self._thread.start()
        logger.info(
            "[heartbeat] Started (interval=%ds)", HEARTBEAT_INTERVAL
        )

    def stop(self):
        """Stop the heartbeat background thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("[heartbeat] Stopped")

    def register_job(self, clip_id: str):
        """Register a clip job for heartbeat updates."""
        with self._lock:
            self._active_jobs.add(clip_id)
        logger.debug("[heartbeat] Registered job %s", clip_id)

    def unregister_job(self, clip_id: str):
        """Unregister a clip job from heartbeat updates."""
        with self._lock:
            self._active_jobs.discard(clip_id)
        logger.debug("[heartbeat] Unregistered job %s", clip_id)

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._active_jobs)

    def _heartbeat_loop(self):
        """Background loop: update heartbeat for all active jobs."""
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=HEARTBEAT_INTERVAL)
            if self._stop_event.is_set():
                break

            with self._lock:
                jobs = list(self._active_jobs)

            if not jobs:
                continue

            try:
                self._update_heartbeats(jobs)
            except Exception as e:
                logger.error("[heartbeat] Failed to update heartbeats: %s", e)

    def _update_heartbeats(self, clip_ids: list):
        """Batch-update heartbeat_at for all active clip jobs."""
        try:
            from shared.db.session import get_session, run_sync
            from sqlalchemy import text

            async def _batch_update():
                async with get_session() as session:
                    # Use a single UPDATE with IN clause for efficiency
                    if len(clip_ids) == 1:
                        await session.execute(
                            text("""
                                UPDATE video_clips
                                SET heartbeat_at = NOW(), updated_at = NOW()
                                WHERE id = :clip_id
                                  AND status IN ('downloading', 'processing', 'uploading')
                            """),
                            {"clip_id": clip_ids[0]},
                        )
                    else:
                        # For multiple jobs, use ANY array
                        await session.execute(
                            text("""
                                UPDATE video_clips
                                SET heartbeat_at = NOW(), updated_at = NOW()
                                WHERE id = ANY(:clip_ids)
                                  AND status IN ('downloading', 'processing', 'uploading')
                            """),
                            {"clip_ids": clip_ids},
                        )

            # Use asyncio.run() instead of run_sync() because this runs in a
            # separate daemon thread that doesn't share the main event loop
            import asyncio
            asyncio.run(_batch_update())
            logger.info(
                "[heartbeat] Updated %d job(s): %s",
                len(clip_ids),
                ", ".join(clip_ids[:5]) + ("..." if len(clip_ids) > 5 else ""),
            )
        except Exception as e:
            logger.error("[heartbeat] DB update failed: %s", e)
