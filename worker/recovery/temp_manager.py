"""
Temp Directory Manager
=======================
Manages temporary files for clip/video processing jobs.

Each job gets an isolated directory:
    /tmp/aitherhub/{job_id}/

This directory holds:
    - downloaded source video
    - trimmed/split segments
    - encoded clips
    - temporary audio files

Cleanup strategy:
    1. Per-job: cleaned up in try/finally after each job completes or fails
    2. Startup: delete all temp dirs older than 6 hours
    3. Periodic: integrated with existing disk_guard.py

Usage:
    with JobTempDir("clip-123") as tmp:
        download_path = tmp.download_path("source.mp4")
        # ... process ...
    # Automatically cleaned up here, even on exception
"""
import os
import sys
import time
import shutil
import logging
from pathlib import Path
from contextlib import contextmanager

logger = logging.getLogger("worker.temp")

# Base temp directory for all worker jobs
TEMP_BASE = Path(os.getenv("WORKER_TEMP_BASE", "/tmp/aitherhub"))

# Max age for temp dirs at startup cleanup (hours)
STARTUP_CLEANUP_MAX_AGE_HOURS = float(
    os.getenv("WORKER_TEMP_MAX_AGE_HOURS", "6")
)


class JobTempDir:
    """Manages a temporary directory for a single job.

    Use as a context manager for automatic cleanup:
        with JobTempDir("clip-123") as tmp:
            path = tmp.path / "video.mp4"
            ...
    """

    def __init__(self, job_id: str):
        self.job_id = job_id
        self.path = TEMP_BASE / job_id
        self._created = False

    def __enter__(self):
        self.create()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
        return False  # Do not suppress exceptions

    def create(self):
        """Create the temp directory."""
        self.path.mkdir(parents=True, exist_ok=True)
        self._created = True
        logger.debug("[temp] Created %s", self.path)

    def cleanup(self):
        """Remove the temp directory and all contents."""
        if self.path.exists():
            try:
                size = _dir_size(self.path)
                shutil.rmtree(self.path, ignore_errors=True)
                logger.info(
                    "[temp] Cleaned up %s (%.1f MB)",
                    self.path,
                    size / (1024 * 1024),
                )
            except Exception as e:
                logger.warning("[temp] Failed to clean %s: %s", self.path, e)

    def download_path(self, filename: str = "source.mp4") -> Path:
        """Get path for downloaded source video."""
        return self.path / filename

    def trim_path(self, filename: str = "trimmed.mp4") -> Path:
        """Get path for trimmed video segment."""
        return self.path / filename

    def encode_path(self, filename: str = "encoded.mp4") -> Path:
        """Get path for encoded clip."""
        return self.path / filename

    def audio_path(self, filename: str = "audio.wav") -> Path:
        """Get path for extracted audio."""
        return self.path / filename

    @property
    def exists(self) -> bool:
        return self.path.exists()


@contextmanager
def job_temp_dir(job_id: str):
    """Context manager shorthand for JobTempDir.

    Usage:
        with job_temp_dir("clip-123") as tmp:
            download_to = tmp.download_path("video.mp4")
    """
    tmp = JobTempDir(job_id)
    try:
        tmp.create()
        yield tmp
    finally:
        tmp.cleanup()


def startup_cleanup():
    """Remove all temp directories older than STARTUP_CLEANUP_MAX_AGE_HOURS.

    Called once at worker startup to clean up leftovers from previous
    crashes or unclean shutdowns.
    """
    if not TEMP_BASE.exists():
        logger.info("[temp] No temp base dir %s, nothing to clean", TEMP_BASE)
        return

    now = time.time()
    max_age_seconds = STARTUP_CLEANUP_MAX_AGE_HOURS * 3600
    removed = 0
    total_size = 0

    for entry in TEMP_BASE.iterdir():
        if not entry.is_dir():
            continue
        try:
            age_seconds = now - entry.stat().st_mtime
            if age_seconds > max_age_seconds:
                size = _dir_size(entry)
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
                total_size += size
                age_hours = age_seconds / 3600
                logger.info(
                    "[temp] Startup cleanup: removed %s (%.1f MB, %.1fh old)",
                    entry.name,
                    size / (1024 * 1024),
                    age_hours,
                )
        except Exception as e:
            logger.warning("[temp] Could not clean %s: %s", entry.name, e)

    if removed > 0:
        logger.info(
            "[temp] Startup cleanup complete: removed %d dir(s), freed %.1f MB",
            removed,
            total_size / (1024 * 1024),
        )
    else:
        logger.info("[temp] Startup cleanup: no stale temp dirs found")


def get_temp_stats() -> dict:
    """Get statistics about current temp directory usage."""
    if not TEMP_BASE.exists():
        return {"dir_count": 0, "total_size_mb": 0.0, "base_path": str(TEMP_BASE)}

    dir_count = 0
    total_size = 0
    for entry in TEMP_BASE.iterdir():
        if entry.is_dir():
            dir_count += 1
            total_size += _dir_size(entry)

    return {
        "dir_count": dir_count,
        "total_size_mb": round(total_size / (1024 * 1024), 1),
        "base_path": str(TEMP_BASE),
    }


def _dir_size(path: Path) -> int:
    """Calculate total size of a directory in bytes."""
    total = 0
    try:
        for f in path.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    except Exception as _e:
        logger.debug(f"Suppressed: {_e}")
    return total
