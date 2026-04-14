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
import asyncio
import os
import sys
import logging
from pathlib import Path
from threading import Thread, Lock, Event

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("worker.heartbeat")

HEARTBEAT_INTERVAL = int(os.getenv("WORKER_HEARTBEAT_INTERVAL", "30"))


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


class HeartbeatManager:
    """Background heartbeat updater for active clip jobs."""

    def __init__(self):
        self._active_jobs: set = set()
        self._lock = Lock()
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._engine = None

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
        # Cleanup the dedicated engine
        if self._engine and self._loop and not self._loop.is_closed():
            try:
                self._loop.run_until_complete(self._engine.dispose())
            except Exception:
                pass
            self._loop.close()
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
        """Background loop: update heartbeat for all active jobs.
        
        Creates a dedicated event loop and DB engine for this thread.
        """
        # Create a dedicated event loop for this thread
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._engine = _create_thread_local_engine()
        except Exception as e:
            logger.error("[heartbeat] Failed to create DB engine: %s", e)
            return

        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=HEARTBEAT_INTERVAL)
            if self._stop_event.is_set():
                break

            with self._lock:
                jobs = list(self._active_jobs)

            if not jobs:
                continue

            try:
                self._loop.run_until_complete(self._update_heartbeats(jobs))
            except Exception as e:
                logger.error("[heartbeat] Failed to update heartbeats: %s", e)

    async def _update_heartbeats(self, clip_ids: list):
        """Batch-update heartbeat_at for all active clip jobs."""
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy import text

        factory = sessionmaker(bind=self._engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            try:
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
                    await session.execute(
                        text("""
                            UPDATE video_clips
                            SET heartbeat_at = NOW(), updated_at = NOW()
                            WHERE id = ANY(:clip_ids)
                              AND status IN ('downloading', 'processing', 'uploading')
                        """),
                        {"clip_ids": clip_ids},
                    )
                await session.commit()
                logger.info(
                    "[heartbeat] Updated %d job(s): %s",
                    len(clip_ids),
                    ", ".join(clip_ids[:5]) + ("..." if len(clip_ids) > 5 else ""),
                )
            except Exception as e:
                await session.rollback()
                logger.error("[heartbeat] DB update failed: %s", e)
