"""
disk_guard.py – Centralised disk-space management for the worker VM.

Every directory that can accumulate large temporary files is registered
here in **one place**.  Both process_video.py and simple_worker.py
call into this module so that cleanup logic is never duplicated and
new directories cannot be "forgotten".

Usage
-----
    from disk_guard import (
        cleanup_video_files,
        cleanup_old_files,
        periodic_disk_check,
        ensure_disk_space,
    )
"""

import logging
import os
import shutil
import time

logger = logging.getLogger("disk_guard")

# ---------------------------------------------------------------------------
# Registry of ALL directories that hold temporary / large files.
# Key   = directory path (relative to BATCH_DIR / cwd)
# Value = dict with cleanup strategy metadata
# ---------------------------------------------------------------------------
_TEMP_DIRS = {
    "uploadedvideo": {
        "description": "Downloaded source videos",
        "pattern": "files",          # contains individual files
        "max_age_hours": 1,           # aggressive: 1h (videos are re-downloadable from Blob)
    },
    "/tmp/aitherhub": {
        "description": "Temp working directory for pipeline jobs",
        "pattern": "subdirs",
        "heavy_subdirs": None,       # remove entire subdir
        "max_age_hours": 0.5,         # 30 min – temp files should be short-lived
    },
    "output": {
        "description": "Per-video output artifacts (frames, audio, cache)",
        "pattern": "subdirs",        # contains {video_id}/ subdirectories
        "heavy_subdirs": ["frames", "audio", "audio_text", "cache"],
        "max_age_hours": 1,           # aggressive: 1h (frames are the biggest disk consumer)
    },
    "splitvideo": {
        "description": "Phase-split video segments",
        "pattern": "subdirs",
        "heavy_subdirs": None,       # remove entire subdir
        "max_age_hours": 1,           # aggressive: 1h
    },
    "artifacts": {
        "description": "Live-capture temporary outputs",
        "pattern": "subdirs",
        "heavy_subdirs": None,
        "max_age_hours": 4,           # reduced from 12h
    },
}

# Thresholds
DISK_WARN_PCT = 70          # Log a warning
DISK_CLEANUP_PCT = 80       # Trigger automatic cleanup
DISK_CRITICAL_PCT = 90      # Aggressive cleanup (ignore age)
DISK_MIN_FREE_GB = 2.0      # Minimum free space to start a new job (lowered from 5.0 – actual need is ~1-2GB per video)
LOG_MAX_SIZE_MB = 200        # Rotate logs bigger than this


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_disk_info(path: str = ".") -> dict:
    """Return disk usage info as a dict."""
    d = shutil.disk_usage(path)
    return {
        "total_gb": d.total / (1024 ** 3),
        "used_gb": d.used / (1024 ** 3),
        "free_gb": d.free / (1024 ** 3),
        "used_pct": (d.used / d.total) * 100,
    }


def ensure_disk_space(min_free_gb: float = DISK_MIN_FREE_GB,
                      current_video_id: str = None) -> bool:
    """Ensure there is enough free disk space.  Runs cleanup if needed.
    Returns True if space is sufficient, raises RuntimeError otherwise."""
    info = get_disk_info()
    logger.info("[DISK] Pre-check: %.1f GB free (%.0f%% used)",
                info["free_gb"], info["used_pct"])

    if info["free_gb"] >= min_free_gb:
        return True

    # Try normal cleanup first
    logger.warning("[DISK] Low space (%.1f GB). Running cleanup...", info["free_gb"])
    cleanup_old_files(current_video_id=current_video_id)

    info = get_disk_info()
    if info["free_gb"] >= min_free_gb:
        logger.info("[DISK] After cleanup: %.1f GB free – OK", info["free_gb"])
        return True

    # Aggressive: remove ALL non-active files regardless of age
    logger.warning("[DISK] Still low (%.1f GB). Aggressive cleanup...", info["free_gb"])
    cleanup_old_files(current_video_id=current_video_id, max_age_hours=0)

    info = get_disk_info()
    if info["free_gb"] >= min_free_gb:
        logger.info("[DISK] After aggressive cleanup: %.1f GB free – OK", info["free_gb"])
        return True

    # Nuclear option: clear pip cache, apt cache, and any other system caches
    logger.warning("[DISK] Still low (%.1f GB). Nuclear cleanup...", info["free_gb"])
    _nuclear_cleanup()

    info = get_disk_info()
    if info["free_gb"] >= min_free_gb:
        logger.info("[DISK] After nuclear cleanup: %.1f GB free – OK", info["free_gb"])
        return True

    raise RuntimeError(
        f"Insufficient disk space: {info['free_gb']:.1f} GB free, "
        f"need {min_free_gb:.1f} GB. Manual intervention required."
    )


def cleanup_video_files(video_id: str):
    """Remove ALL local files for a specific video (called after job completes
    or on error).  This is the per-job cleanup."""
    if not video_id:
        return

    removed_total = 0

    # 1. uploadedvideo/{video_id}.mp4 and {video_id}_preview.mp4
    upload_dir = "uploadedvideo"
    for suffix in [".mp4", "_preview.mp4"]:
        fp = os.path.join(upload_dir, f"{video_id}{suffix}")
        removed_total += _safe_remove_file(fp)

    # 2. output/{video_id}/ – remove heavy subdirs
    art_dir = os.path.join("output", video_id)
    if os.path.isdir(art_dir):
        for subdir in ["frames", "audio", "audio_text", "cache"]:
            subpath = os.path.join(art_dir, subdir)
            removed_total += _safe_remove_dir(subpath)

    # 3. splitvideo/{video_id}/
    split_dir = os.path.join("splitvideo", video_id)
    removed_total += _safe_remove_dir(split_dir)

    # 4. artifacts/{video_id}/
    art_capture_dir = os.path.join("artifacts", video_id)
    removed_total += _safe_remove_dir(art_capture_dir)

    if removed_total > 0:
        logger.info("[CLEANUP] Removed %d items for video %s", removed_total, video_id)


def cleanup_old_files(current_video_id: str = None,
                      active_ids: set = None,
                      max_age_hours: float = None):
    """Remove old files from ALL registered temp directories.
    Called before starting a new job and periodically by the worker."""
    now = time.time()
    if active_ids is None:
        active_ids = set()
    if current_video_id:
        active_ids.add(current_video_id)

    total_removed = 0

    for dir_name, meta in _TEMP_DIRS.items():
        age_limit = max_age_hours if max_age_hours is not None else meta["max_age_hours"]

        if not os.path.isdir(dir_name):
            continue

        if meta["pattern"] == "files":
            # Directory contains individual files
            for f in os.listdir(dir_name):
                vid = f.replace(".mp4", "").replace("_preview", "")
                if vid in active_ids:
                    continue
                fp = os.path.join(dir_name, f)
                if not os.path.isfile(fp):
                    continue
                try:
                    age_hours = (now - os.path.getmtime(fp)) / 3600
                    if age_hours > age_limit:
                        size_mb = os.path.getsize(fp) / (1024 ** 2)
                        os.remove(fp)
                        total_removed += 1
                        logger.info("[CLEANUP-OLD] Removed %s/%s (%.0f MB, %.1fh old)",
                                    dir_name, f, size_mb, age_hours)
                except Exception as e:
                    logger.warning("[CLEANUP-OLD] Could not remove %s/%s: %s", dir_name, f, e)

        elif meta["pattern"] == "subdirs":
            # Directory contains {video_id}/ subdirectories
            for d in os.listdir(dir_name):
                if d in active_ids:
                    continue
                dp = os.path.join(dir_name, d)
                if not os.path.isdir(dp):
                    continue
                try:
                    age_hours = (now - os.path.getmtime(dp)) / 3600
                    if age_hours > age_limit:
                        heavy = meta.get("heavy_subdirs")
                        if heavy is None:
                            # Remove entire subdirectory
                            total_removed += _safe_remove_dir(dp)
                        else:
                            # Remove only heavy subdirectories
                            for subdir in heavy:
                                subpath = os.path.join(dp, subdir)
                                total_removed += _safe_remove_dir(subpath)
                except Exception as e:
                    logger.warning("[CLEANUP-OLD] Could not clean %s/%s: %s", dir_name, d, e)

    # Also rotate large log files
    _rotate_logs()

    if total_removed > 0:
        info = get_disk_info()
        logger.info("[CLEANUP-OLD] Removed %d items. Disk: %.1f GB free (%.0f%% used)",
                    total_removed, info["free_gb"], info["used_pct"])


def periodic_disk_check(active_ids: set = None):
    """Called periodically by simple_worker.py.
    Logs disk usage and triggers cleanup if needed."""
    info = get_disk_info()
    logger.info("[DISK] Periodic check: %.1f GB free / %.1f GB total (%.0f%% used)",
                info["free_gb"], info["total_gb"], info["used_pct"])

    if info["used_pct"] > DISK_CRITICAL_PCT:
        logger.error("[DISK] CRITICAL: %.0f%% used! Aggressive cleanup...", info["used_pct"])
        cleanup_old_files(active_ids=active_ids, max_age_hours=0)
    elif info["used_pct"] > DISK_CLEANUP_PCT:
        logger.warning("[DISK] High usage: %.0f%%. Running cleanup...", info["used_pct"])
        cleanup_old_files(active_ids=active_ids)
    elif info["used_pct"] > DISK_WARN_PCT:
        logger.warning("[DISK] Warning: %.0f%% used.", info["used_pct"])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_remove_file(path: str) -> int:
    """Remove a single file. Returns 1 if removed, 0 otherwise."""
    try:
        if os.path.isfile(path):
            size_mb = os.path.getsize(path) / (1024 ** 2)
            os.remove(path)
            logger.info("[CLEANUP] Removed file: %s (%.0f MB)", path, size_mb)
            return 1
    except Exception as e:
        logger.warning("[CLEANUP] Could not remove %s: %s", path, e)
    return 0


def _safe_remove_dir(path: str) -> int:
    """Remove a directory tree. Returns 1 if removed, 0 otherwise."""
    try:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            logger.info("[CLEANUP] Removed dir: %s", path)
            return 1
    except Exception as e:
        logger.warning("[CLEANUP] Could not remove %s: %s", path, e)
    return 0


def _nuclear_cleanup():
    """Last-resort cleanup: clear system caches, pip cache, apt cache, etc."""
    import subprocess
    commands = [
        ["pip", "cache", "purge"],
        ["sudo", "apt-get", "clean"],
        ["sudo", "rm", "-rf", "/tmp/pip-*"],
        ["sudo", "rm", "-rf", "/var/cache/apt/archives/*.deb"],
    ]
    # Also clean /tmp files older than 1 hour (excluding /tmp/aitherhub which is handled above)
    try:
        for entry in os.listdir("/tmp"):
            if entry == "aitherhub":
                continue
            fp = os.path.join("/tmp", entry)
            try:
                age_hours = (time.time() - os.path.getmtime(fp)) / 3600
                if age_hours > 0.5:
                    if os.path.isfile(fp):
                        os.remove(fp)
                        logger.info("[NUCLEAR] Removed /tmp file: %s", entry)
                    elif os.path.isdir(fp):
                        shutil.rmtree(fp, ignore_errors=True)
                        logger.info("[NUCLEAR] Removed /tmp dir: %s", entry)
            except Exception:
                pass
    except Exception:
        pass

    for cmd in commands:
        try:
            subprocess.run(cmd, capture_output=True, timeout=30)
            logger.info("[NUCLEAR] Ran: %s", " ".join(cmd))
        except Exception:
            pass

    info = get_disk_info()
    logger.info("[NUCLEAR] After nuclear cleanup: %.1f GB free (%.0f%% used)",
                info["free_gb"], info["used_pct"])


def _rotate_logs():
    """Remove log files that are too large."""
    log_dir = "logs"
    if not os.path.isdir(log_dir):
        return
    for f in os.listdir(log_dir):
        fp = os.path.join(log_dir, f)
        if not os.path.isfile(fp):
            continue
        try:
            size_mb = os.path.getsize(fp) / (1024 ** 2)
            if size_mb > LOG_MAX_SIZE_MB:
                # Truncate instead of delete (keep the file for appending)
                with open(fp, "w") as fh:
                    fh.write(f"[LOG ROTATED] File exceeded {LOG_MAX_SIZE_MB} MB, truncated.\n")
                logger.info("[LOG-ROTATE] Truncated %s (was %.0f MB)", f, size_mb)
        except Exception as _e:
            logger.debug(f"Suppressed: {_e}")
