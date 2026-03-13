#!/usr/bin/env python3
"""
AitherHub Queue Worker — Independent Entrypoint
=================================================
Polls Azure Storage Queue and dispatches jobs to processors.

This is the NEW entrypoint that replaces worker/controller/simple_worker.py.
It imports ONLY from shared/ and worker/ — NEVER from backend/app/.

Start:
    python -m worker.entrypoints.queue_worker

Design:
    - Queue polling → job dispatch → subprocess execution
    - Dead Letter Queue for poison messages
    - Crash guard for orphaned ffmpeg processes
    - File lock to prevent duplicate instances
    - Graceful shutdown on SIGTERM/SIGINT
"""
import os
import sys
import json
import time
import subprocess
import fcntl
import signal
import socket
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Lock, Thread

# Ensure project root is in sys.path for shared imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# BUILD 36b: Ensure venv site-packages are available even when running
# under system Python (/usr/bin/python3). The systemd service may use
# system Python, but dependencies are installed in the venv.
_VENV_SP = PROJECT_ROOT / ".venv" / "lib"
if _VENV_SP.exists():
    for _pydir in sorted(_VENV_SP.iterdir(), reverse=True):
        _sp = _pydir / "site-packages"
        if _sp.is_dir() and str(_sp) not in sys.path:
            sys.path.insert(1, str(_sp))
            break

from shared.config import (
    WORKER_MAX_CONCURRENT,
    WORKER_MAX_RETRIES,
    WORKER_VIDEO_TIMEOUT,
    WORKER_CLIP_TIMEOUT,
    AZURE_QUEUE_NAME,
    AZURE_DEAD_LETTER_QUEUE_NAME,
    ENVIRONMENT,
    AZURE_STORAGE_CONNECTION_STRING,
)
from shared.queue.client import (
    get_queue_client,
    get_dead_letter_queue_client,
)
from shared.schemas.video_status import VideoStatus, ClipStatus

# =============================================================================
# Constants
# =============================================================================

MAX_WORKERS = WORKER_MAX_CONCURRENT
MAX_DEQUEUE_COUNT = WORKER_MAX_RETRIES
VISIBILITY_TIMEOUT = 15 * 60  # 900 seconds
VISIBILITY_RENEW_INTERVAL = 5 * 60  # 300 seconds

# Paths to subprocess scripts (legacy batch dir)
BATCH_DIR = str(PROJECT_ROOT / "worker" / "batch")
REALTIME_DIR = str(PROJECT_ROOT / "worker" / "realtime")

# Track active jobs
active_jobs: dict = {}
active_jobs_lock = Lock()

# Separate executor for lightweight live_monitor jobs
live_monitor_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="live-monitor")
live_monitor_jobs: dict = {}
live_monitor_lock = Lock()

# Graceful shutdown flag
shutdown_requested = False

# Worker instance identifier
WORKER_INSTANCE_ID = f"{socket.gethostname()}-{os.getpid()}"

# Poison job log (local backup)
POISON_LOG = PROJECT_ROOT / "worker" / "poison_jobs.jsonl"

# Disk cleanup interval
DISK_CLEANUP_INTERVAL = 30 * 60
_last_disk_cleanup = 0


# =============================================================================
# Dead Letter Queue
# =============================================================================

def move_to_dead_letter_queue(payload: dict, reason: str, dequeue_count: int) -> bool:
    """Move a failed message to the dead-letter queue."""
    envelope = {
        "original_payload": payload,
        "dead_letter_reason": reason,
        "dequeue_count": dequeue_count,
        "worker_instance": WORKER_INSTANCE_ID,
        "moved_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        dlq_client = get_dead_letter_queue_client()
        dlq_client.send_message(json.dumps(envelope, ensure_ascii=False))
        job_id = payload.get("video_id", payload.get("clip_id", "unknown"))
        print(f"[worker] Moved job {job_id} to dead-letter queue "
              f"(reason={reason}, dequeue_count={dequeue_count})")
        return True
    except Exception as e:
        print(f"[worker] CRITICAL: Failed to move to dead-letter queue: {e}")
        return False


# =============================================================================
# Crash Guard
# =============================================================================

def crash_guard_kill_orphan_ffmpeg():
    """Kill orphaned ffmpeg processes from previous worker crashes."""
    my_pid = os.getpid()
    killed = 0
    try:
        result = subprocess.run(
            ["pgrep", "-a", "ffmpeg"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            print("[worker][crash-guard] No orphan ffmpeg processes found")
            return

        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split(None, 1)
            if len(parts) < 1:
                continue
            pid = int(parts[0])
            try:
                ppid_result = subprocess.run(
                    ["ps", "-o", "ppid=", "-p", str(pid)],
                    capture_output=True, text=True, timeout=5,
                )
                ppid = int(ppid_result.stdout.strip())
                if ppid == my_pid:
                    continue
            except Exception as _e:
                print(f"Suppressed: {_e}")
            try:
                os.kill(pid, signal.SIGKILL)
                killed += 1
                cmd_info = parts[1] if len(parts) > 1 else "unknown"
                print(f"[worker][crash-guard] Killed orphan ffmpeg pid={pid}: {cmd_info[:100]}")
            except (ProcessLookupError, PermissionError) as _e:
                print(f"Suppressed: {_e}")
    except Exception as e:
        print(f"[worker][crash-guard] Error: {e}")

    if killed > 0:
        print(f"[worker][crash-guard] Killed {killed} orphan ffmpeg process(es)")
    else:
        print("[worker][crash-guard] No orphan ffmpeg processes found")


# =============================================================================
# Logging & Error Tracking
# =============================================================================

def log_error_type(job_id: str, job_type: str, error_type: str, detail: str = ""):
    print(f"[worker] ERROR_TYPE={error_type} job={job_id} type={job_type} detail={detail}")


def record_poison_job(job_id: str, job_type: str, error_type: str,
                      dequeue_count: int = 0, payload: dict = None):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "job_id": job_id,
        "job_type": job_type,
        "error_type": error_type,
        "dequeue_count": dequeue_count,
        "payload": payload or {},
    }
    try:
        with open(POISON_LOG, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[worker] Warning: Failed to write poison log: {e}")


# =============================================================================
# Signal Handling
# =============================================================================

def signal_handler(signum, frame):
    global shutdown_requested
    print(f"\n[worker] Received signal {signum}, shutting down gracefully...")
    shutdown_requested = True


# =============================================================================
# Queue Operations
# =============================================================================

def delete_message_safe(msg_id: str, pop_receipt: str) -> bool:
    try:
        client = get_queue_client()
        client.delete_message(msg_id, pop_receipt)
        return True
    except Exception as e:
        print(f"[worker] Warning: Failed to delete message {msg_id}: {e}")
        return False


def renew_visibility(msg_id: str, pop_receipt: str, job_id: str):
    try:
        client = get_queue_client()
        result = client.update_message(msg_id, pop_receipt, visibility_timeout=VISIBILITY_TIMEOUT)
        return result.pop_receipt
    except Exception as e:
        print(f"[worker] Warning: Failed to renew visibility for {job_id}: {e}")
        return None


def visibility_renewal_loop():
    while not shutdown_requested:
        time.sleep(VISIBILITY_RENEW_INTERVAL)
        with active_jobs_lock:
            for job_id, info in list(active_jobs.items()):
                if info["future"].done():
                    continue
                new_receipt = renew_visibility(info["msg_id"], info["pop_receipt"], job_id)
                if new_receipt:
                    info["pop_receipt"] = new_receipt


# =============================================================================
# DB Status Helpers (using shared.db)
# =============================================================================

def update_video_status_to_error(video_id: str):
    """Mark a video as ERROR in the database."""
    try:
        from shared.db.session import get_session, run_sync
        from sqlalchemy import text

        async def _update():
            async with get_session() as session:
                await session.execute(
                    text("UPDATE videos SET status = :status WHERE id = :vid"),
                    {"status": VideoStatus.ERROR, "vid": video_id},
                )

        run_sync(_update())
        print(f"[worker] Marked video {video_id} as ERROR")
    except Exception as db_err:
        print(f"[worker] Failed to mark video as ERROR: {db_err}")


def update_clip_status_to_dead(clip_id: str, error_message: str):
    """Mark a clip as 'dead' in the database."""
    try:
        from shared.db.session import get_session, run_sync
        from sqlalchemy import text

        async def _update():
            async with get_session() as session:
                await session.execute(
                    text("""
                        UPDATE video_clips
                        SET status = :status, error_message = :error_message, updated_at = NOW()
                        WHERE id = :clip_id
                    """),
                    {"status": ClipStatus.DEAD, "error_message": error_message[:500], "clip_id": clip_id},
                )

        run_sync(_update())
        print(f"[worker] Marked clip {clip_id} as 'dead'")
    except Exception as db_err:
        print(f"[worker] Failed to mark clip as dead: {db_err}")


def update_worker_claimed(video_id: str, instance_id: str, dequeue_count: int):
    """Record worker claim evidence in DB."""
    try:
        from shared.db.session import get_session, run_sync
        from sqlalchemy import text

        async def _update():
            async with get_session() as session:
                await session.execute(
                    text("""
                        UPDATE videos
                        SET worker_claimed_at = NOW(),
                            worker_instance_id = :instance_id,
                            dequeue_count = :dq
                        WHERE id = :vid
                    """),
                    {"instance_id": instance_id, "dq": dequeue_count, "vid": video_id},
                )

        run_sync(_update())
    except Exception as e:
        print(f"[worker] Failed to record worker_claimed: {e}")


# =============================================================================
# Job Processors (subprocess dispatch)
# =============================================================================

def process_job(payload: dict, msg_id: str, pop_receipt: str) -> bool:
    """Process a single job. Runs in a thread."""
    job_type = payload.get("job_type", "video_analysis")
    job_id = payload.get("video_id", payload.get("clip_id", "unknown"))

    try:
        if job_type == "generate_clip":
            success = _run_clip_job(payload)
        elif job_type == "video_pipeline":
            success = _run_pipeline_job(payload)
        elif job_type == "live_capture":
            success = _run_live_capture_job(payload)
        elif job_type == "live_monitor":
            success = _run_live_monitor_job(payload)
        elif job_type == "live_analysis":
            success = _run_live_analysis_job(payload)
        else:
            # Default: run legacy process_video.py, then optionally run pipeline
            success = _run_video_job(payload)

        if success:
            with active_jobs_lock:
                info = active_jobs.get(job_id, {})
                current_receipt = info.get("pop_receipt", pop_receipt)
            delete_message_safe(msg_id, current_receipt)
        else:
            print(f"[worker] Job {job_id} failed, will retry after visibility timeout")

        return success
    except Exception as e:
        log_error_type(job_id, job_type, "UNKNOWN", f"EXC={type(e).__name__} {e}")
        return False
    finally:
        with active_jobs_lock:
            active_jobs.pop(job_id, None)


def _run_clip_job(payload: dict) -> bool:
    """Run clip generation as subprocess with heartbeat, metrics, and temp cleanup."""
    clip_id = payload.get("clip_id")
    video_id = payload.get("video_id")
    blob_url = payload.get("blob_url")
    time_start = payload.get("time_start")
    time_end = payload.get("time_end")

    if not all([clip_id, video_id, blob_url, time_start is not None, time_end is not None]):
        log_error_type(clip_id or "unknown", "generate_clip", "INPUT_INVALID", "missing fields")
        return False

    phase_index = payload.get("phase_index", -1)
    speed_factor = payload.get("speed_factor", 1.0)

    # ── Task 4: Metrics ──
    try:
        from worker.recovery.metrics_logger import JobMetrics
        metrics = JobMetrics(job_id=clip_id, job_type="generate_clip")
        metrics.start()
        metrics.set_metadata(
            clip_length=float(time_end) - float(time_start) if time_end and time_start else 0.0,
        )
    except Exception:
        metrics = None

    # ── Task 1: Register heartbeat ──
    if _heartbeat_manager:
        _heartbeat_manager.register_job(clip_id)

    print(f"[worker] Starting clip generation: clip_id={clip_id}")
    cmd = [
        sys.executable,
        os.path.join(BATCH_DIR, "generate_clip.py"),
        "--clip-id", clip_id,
        "--video-id", video_id,
        "--blob-url", blob_url,
        "--time-start", str(time_start),
        "--time-end", str(time_end),
        "--phase-index", str(phase_index),
        "--speed-factor", str(speed_factor),
    ]

    try:
        if metrics:
            metrics.start_phase("processing")

        proc = subprocess.Popen(
            cmd, cwd=BATCH_DIR,
            env={**os.environ, "PYTHONPATH": f"{str(PROJECT_ROOT)}:{BATCH_DIR}"},
            start_new_session=True,
        )
        try:
            proc.wait(timeout=WORKER_CLIP_TIMEOUT)
        except subprocess.TimeoutExpired:
            print(f"[worker] Clip timeout — killing pid={proc.pid}")
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError) as _e:
                print(f"Suppressed: {_e}")
            proc.wait()
            log_error_type(clip_id, "generate_clip", "TIMEOUT_CLIP", f"timeout={WORKER_CLIP_TIMEOUT}s")
            if metrics:
                metrics.end_phase("processing")
                metrics.finish(status="timeout")
            return False

        if metrics:
            metrics.end_phase("processing")

        if proc.returncode == 0:
            print(f"[worker] Clip completed: {clip_id}")
            if metrics:
                metrics.finish(status="completed")
            return True
        else:
            log_error_type(clip_id, "generate_clip", "FFMPEG_FAIL", f"exit={proc.returncode}")
            if metrics:
                metrics.finish(status="failed")
            return False
    except Exception as e:
        log_error_type(clip_id, "generate_clip", "UNKNOWN", f"EXC={type(e).__name__} {e}")
        if metrics:
            metrics.finish(status="error")
        return False
    finally:
        # ── Task 1: Unregister heartbeat ──
        if _heartbeat_manager:
            _heartbeat_manager.unregister_job(clip_id)

        # ── Task 2: Temp cleanup ──
        try:
            from worker.recovery.temp_manager import JobTempDir
            tmp = JobTempDir(clip_id)
            if tmp.exists:
                tmp.cleanup()
        except Exception as e:
            print(f"[worker] Warning: Temp cleanup failed for {clip_id}: {e}")


def _run_video_job(payload: dict) -> bool:
    """Run video analysis as subprocess with metrics and temp cleanup."""
    video_id = payload.get("video_id")
    blob_url = payload.get("blob_url")

    if not video_id or not blob_url:
        log_error_type(video_id or "unknown", "video_analysis", "INPUT_INVALID", "missing fields")
        return False

    # ── Task 4: Metrics ──
    try:
        from worker.recovery.metrics_logger import JobMetrics
        metrics = JobMetrics(job_id=video_id, job_type="video_analysis")
        metrics.start()
        metrics.start_phase("processing")
    except Exception:
        metrics = None

    print(f"[worker] Starting video analysis: video_id={video_id}")
    cmd = [
        sys.executable,
        os.path.join(BATCH_DIR, "process_video.py"),
        "--video-id", video_id,
        "--blob-url", blob_url,
    ]

    try:
        proc = subprocess.Popen(
            cmd, cwd=BATCH_DIR,
            env={**os.environ, "PYTHONPATH": f"{str(PROJECT_ROOT)}:{BATCH_DIR}"},
            start_new_session=True,
        )
        try:
            proc.wait(timeout=WORKER_VIDEO_TIMEOUT)
        except subprocess.TimeoutExpired:
            print(f"[worker] Video timeout — killing pid={proc.pid}")
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError) as _e:
                print(f"Suppressed: {_e}")
            proc.wait()
            log_error_type(video_id, "video_analysis", "TIMEOUT_VIDEO", f"timeout={WORKER_VIDEO_TIMEOUT}s")
            update_video_status_to_error(video_id)
            if metrics:
                metrics.end_phase("processing")
                metrics.finish(status="timeout")
            return False

        if metrics:
            metrics.end_phase("processing")

        if proc.returncode == 0:
            print(f"[worker] Video analysis completed: {video_id}")
            if metrics:
                metrics.finish(status="completed")

            # ── Pipeline post-processing (opt-in via PIPELINE_ENABLED) ──
            if PIPELINE_ENABLED:
                try:
                    _run_post_analysis_pipeline(video_id, blob_url)
                except Exception as pipe_err:
                    print(f"[worker] Post-analysis pipeline error (non-fatal): {pipe_err}")

            return True
        elif proc.returncode == 2:
            print(f"[worker] ORPHAN_VIDEO skip: {video_id}")
            if metrics:
                metrics.finish(status="skipped")
            return True
        else:
            log_error_type(video_id, "video_analysis", "SUBPROCESS_FAIL", f"exit={proc.returncode}")
            if metrics:
                metrics.finish(status="failed")
            return False
    except Exception as e:
        log_error_type(video_id, "video_analysis", "UNKNOWN", f"EXC={type(e).__name__} {e}")
        if metrics:
            metrics.finish(status="error")
        return False
    finally:
        # ── Task 2: Temp cleanup for video analysis ──
        try:
            from worker.recovery.temp_manager import JobTempDir
            tmp = JobTempDir(video_id)
            if tmp.exists:
                tmp.cleanup()
        except Exception as e:
            print(f"[worker] Warning: Temp cleanup failed for {video_id}: {e}")


# =============================================================================
# Pipeline Integration (Phase 4)
# =============================================================================

# Enable pipeline as post-processing step after legacy video analysis
PIPELINE_ENABLED = os.getenv("PIPELINE_ENABLED", "false").lower() in ("true", "1", "yes")


def _run_pipeline_job(payload: dict) -> bool:
    """Run the full video intelligence pipeline (new job type).

    This is for jobs explicitly requesting pipeline processing via
    job_type='video_pipeline'. Runs scene detection, speech extraction,
    transcript segmentation, event detection, sales moment detection,
    and clip generation.
    """
    video_id = payload.get("video_id")
    blob_url = payload.get("blob_url", "")
    user_id = str(payload.get("user_id", ""))

    if not video_id:
        log_error_type("unknown", "video_pipeline", "INPUT_INVALID", "missing video_id")
        return False

    # ── Metrics ──
    try:
        from worker.recovery.metrics_logger import JobMetrics
        metrics = JobMetrics(job_id=video_id, job_type="video_pipeline")
        metrics.start()
    except Exception:
        metrics = None

    # ── Heartbeat ──
    if _heartbeat_manager:
        _heartbeat_manager.register_job(video_id)

    print(f"[worker] Starting video pipeline: video_id={video_id}")

    try:
        # Resolve video path: download from blob if needed
        video_path = _resolve_video_path(video_id, blob_url)
        if not video_path:
            log_error_type(video_id, "video_pipeline", "DOWNLOAD_FAIL", "could not resolve video path")
            if metrics:
                metrics.finish(status="failed")
            return False

        # Run the pipeline
        from worker.pipeline.pipeline_runner import run_pipeline
        ctx = run_pipeline(
            video_id=video_id,
            video_path=video_path,
            blob_url=blob_url,
            user_id=user_id,
        )

        # Save results to DB
        try:
            from worker.pipeline.pipeline_db import save_pipeline_results
            save_pipeline_results(ctx)
        except Exception as db_err:
            print(f"[worker] Warning: Failed to save pipeline results to DB: {db_err}")

        # Log pipeline metrics
        if metrics:
            for step_name, duration in ctx.step_timings.items():
                if not step_name.startswith("_"):
                    metrics.start_phase(step_name)
                    metrics.end_phase(step_name)
                    # Override with actual timing
                    metrics._phases[step_name]["duration_s"] = duration
            metrics.finish(
                status="completed" if not ctx.has_error() else "completed_with_errors"
            )

        summary = ctx.summary()
        print(
            f"[worker] Pipeline completed: video_id={video_id} "
            f"scenes={summary['scenes_count']} "
            f"transcript={summary['transcript_segments']} "
            f"events={summary['events_count']} "
            f"sales_moments={summary['sales_moments_count']} "
            f"clips={summary['clips_count']} "
            f"errors={len(summary['errors'])} "
            f"total_time={summary.get('total_time', 0):.1f}s"
        )
        return True

    except Exception as e:
        log_error_type(video_id, "video_pipeline", "UNKNOWN", f"EXC={type(e).__name__} {e}")
        if metrics:
            metrics.finish(status="error")
        return False
    finally:
        if _heartbeat_manager:
            _heartbeat_manager.unregister_job(video_id)
        try:
            from worker.recovery.temp_manager import JobTempDir
            tmp = JobTempDir(video_id)
            if tmp.exists:
                tmp.cleanup()
        except Exception as e:
            print(f"[worker] Warning: Temp cleanup failed for {video_id}: {e}")


def _resolve_video_path(video_id: str, blob_url: str) -> str:
    """Download video from blob storage and return local path.

    Returns empty string if download fails.
    """
    if not blob_url:
        return ""

    try:
        from worker.recovery.temp_manager import JobTempDir
        tmp = JobTempDir(video_id)
        tmp.create()
        local_path = str(tmp.download_path("source.mp4"))

        # Use the existing download logic from batch
        import subprocess as sp
        result = sp.run(
            ["curl", "-sS", "-L", "-o", local_path, blob_url],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode == 0 and os.path.exists(local_path):
            size_mb = os.path.getsize(local_path) / (1024 * 1024)
            print(f"[worker] Downloaded video: {size_mb:.1f} MB -> {local_path}")
            return local_path
        else:
            print(f"[worker] Download failed: exit={result.returncode} stderr={result.stderr[:200]}")
            return ""
    except Exception as e:
        print(f"[worker] Download error: {e}")
        return ""


def _run_post_analysis_pipeline(video_id: str, blob_url: str):
    """Run pipeline as a post-processing step after legacy video analysis.

    Called only when PIPELINE_ENABLED=true.
    This is a non-blocking, best-effort operation — failures here
    do not affect the success/failure of the main video analysis.
    """
    print(f"[worker] Running post-analysis pipeline for video_id={video_id}")
    try:
        video_path = _resolve_video_path(video_id, blob_url)
        if not video_path:
            print(f"[worker] Post-analysis pipeline skipped: could not download video")
            return

        from worker.pipeline.pipeline_runner import run_pipeline
        ctx = run_pipeline(
            video_id=video_id,
            video_path=video_path,
            blob_url=blob_url,
        )

        try:
            from worker.pipeline.pipeline_db import save_pipeline_results
            save_pipeline_results(ctx)
        except Exception as db_err:
            print(f"[worker] Warning: Failed to save post-analysis pipeline results: {db_err}")

        summary = ctx.summary()
        print(
            f"[worker] Post-analysis pipeline completed: video_id={video_id} "
            f"sales_moments={summary['sales_moments_count']} "
            f"clips={summary['clips_count']}"
        )
    except Exception as e:
        print(f"[worker] Post-analysis pipeline failed (non-fatal): {e}")
    finally:
        try:
            from worker.recovery.temp_manager import JobTempDir
            tmp = JobTempDir(f"{video_id}-pipeline")
            if tmp.exists:
                tmp.cleanup()
        except Exception as _e:
            print(f"Suppressed: {_e}")


def _run_live_capture_job(payload: dict) -> bool:
    """Run live stream capture as subprocess."""
    video_id = payload.get("video_id")
    live_url = payload.get("live_url")
    email = payload.get("email", "")
    user_id = str(payload.get("user_id", ""))
    duration = payload.get("duration", 0)

    if not video_id or not live_url:
        log_error_type(video_id or "unknown", "live_capture", "INPUT_INVALID", "missing fields")
        return False

    import re
    match = re.search(r"@([^/]+)", live_url)
    username = match.group(1) if match else ""

    # Start live monitor as background subprocess
    monitor_proc = None
    if username:
        try:
            monitor_cmd = [
                sys.executable,
                os.path.join(REALTIME_DIR, "live_monitor.py"),
                "--unique-id", username,
                "--video-id", video_id,
            ]
            monitor_proc = subprocess.Popen(
                monitor_cmd, cwd=REALTIME_DIR,
                env={**os.environ, "PYTHONPATH": f"{REALTIME_DIR}:{BATCH_DIR}"},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True,
            )
            print(f"[worker] Live monitor started for @{username} (pid={monitor_proc.pid})")
        except Exception as e:
            print(f"[worker] Warning: Failed to start live monitor: {e}")

    print(f"[worker] Starting live capture: video_id={video_id}")
    cmd = [
        sys.executable,
        os.path.join(BATCH_DIR, "tiktok_stream_capture.py"),
        "--video-id", video_id,
        "--live-url", live_url,
        "--email", email,
        "--user-id", str(user_id),
    ]
    if duration > 0:
        cmd.extend(["--duration", str(duration)])

    result = subprocess.run(
        cmd, cwd=BATCH_DIR,
        env={**os.environ, "PYTHONPATH": BATCH_DIR},
        start_new_session=True,
    )

    if monitor_proc and monitor_proc.poll() is None:
        try:
            os.killpg(os.getpgid(monitor_proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError) as _e:
            print(f"Suppressed: {_e}")

    if result.returncode == 0:
        return True
    elif result.returncode == 2:
        return True  # User offline
    else:
        log_error_type(video_id, "live_capture", "SUBPROCESS_FAIL", f"exit={result.returncode}")
        return False


def _run_live_monitor_job(payload: dict) -> bool:
    """Run live monitor as subprocess."""
    video_id = payload.get("video_id")
    username = payload.get("username", "")

    if not video_id or not username:
        log_error_type(video_id or "unknown", "live_monitor", "INPUT_INVALID", "missing fields")
        return False

    print(f"[worker] Starting live monitor for @{username}")
    cmd = [
        sys.executable,
        os.path.join(REALTIME_DIR, "live_monitor.py"),
        "--unique-id", username,
        "--video-id", video_id,
    ]

    result = subprocess.run(
        cmd, cwd=REALTIME_DIR,
        env={**os.environ, "PYTHONPATH": f"{REALTIME_DIR}:{BATCH_DIR}"},
        start_new_session=True,
    )

    return result.returncode == 0


def _run_live_analysis_job(payload: dict) -> bool:
    """Run LiveBoost analysis pipeline as subprocess.

    The pipeline (assembling → audio → STT → OCR → sales detection → clips)
    runs inside backend/app/ via run_live_analysis.py because it depends on
    backend-only modules (app.services.live_analysis_pipeline).
    """
    job_id = payload.get("job_id")
    video_id = payload.get("video_id")
    email = payload.get("email", "")
    total_chunks = payload.get("total_chunks")
    stream_source = payload.get("stream_source", "tiktok_live")

    if not job_id or not video_id:
        log_error_type(
            video_id or "unknown", "live_analysis", "INPUT_INVALID",
            f"missing fields: job_id={job_id} video_id={video_id}",
        )
        return False

    # ── Metrics ──
    try:
        from worker.recovery.metrics_logger import JobMetrics
        metrics = JobMetrics(job_id=job_id, job_type="live_analysis")
        metrics.start()
        metrics.start_phase("processing")
    except Exception:
        metrics = None

    # ── Heartbeat ──
    if _heartbeat_manager:
        _heartbeat_manager.register_job(job_id)

    print(f"[worker] Starting live analysis: job={job_id} video={video_id} chunks={total_chunks}")

    cmd = [
        sys.executable,
        os.path.join(BATCH_DIR, "run_live_analysis.py"),
        "--job-id", str(job_id),
        "--video-id", str(video_id),
        "--email", email,
    ]
    if total_chunks is not None:
        cmd.extend(["--total-chunks", str(total_chunks)])
    if stream_source:
        cmd.extend(["--stream-source", stream_source])

    # PYTHONPATH must include both project root (for shared/) and backend/ (for app/)
    backend_dir = str(PROJECT_ROOT / "backend")
    env = {
        **os.environ,
        "PYTHONPATH": f"{str(PROJECT_ROOT)}:{backend_dir}:{BATCH_DIR}",
    }

    try:
        proc = subprocess.Popen(
            cmd, cwd=BATCH_DIR,
            env=env,
            start_new_session=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            stdout_data, _ = proc.communicate(timeout=WORKER_VIDEO_TIMEOUT)
        except subprocess.TimeoutExpired:
            print(f"[worker] Live analysis timeout — killing pid={proc.pid}")
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError) as _e:
                print(f"Suppressed: {_e}")
            proc.wait()
            log_error_type(job_id, "live_analysis", "TIMEOUT", f"timeout={WORKER_VIDEO_TIMEOUT}s")
            if metrics:
                metrics.end_phase("processing")
                metrics.finish(status="timeout")
            return False

        if metrics:
            metrics.end_phase("processing")

        # Log subprocess output for debugging
        output = stdout_data.decode("utf-8", errors="replace") if stdout_data else ""
        if output:
            for line in output.strip().split("\n")[-30:]:
                print(f"[worker][live_analysis][{job_id}] {line}")

        if proc.returncode == 0:
            print(f"[worker] Live analysis completed: job={job_id}")
            if metrics:
                metrics.finish(status="completed")
            return True
        elif proc.returncode == 2:
            print(f"[worker] Live analysis skipped (input error): job={job_id}")
            if metrics:
                metrics.finish(status="skipped")
            return True
        else:
            # Log last 5 lines of output for error diagnosis
            tail = "\n".join(output.strip().split("\n")[-5:]) if output else "no output"
            log_error_type(
                job_id, "live_analysis", "SUBPROCESS_FAIL",
                f"exit={proc.returncode} tail={tail}",
            )
            if metrics:
                metrics.finish(status="failed")
            return False
    except Exception as e:
        log_error_type(job_id, "live_analysis", "UNKNOWN", f"EXC={type(e).__name__} {e}")
        if metrics:
            metrics.finish(status="error")
        return False
    finally:
        # ── Unregister heartbeat ──
        if _heartbeat_manager:
            _heartbeat_manager.unregister_job(job_id)
        # ── Temp cleanup ──
        try:
            from worker.recovery.temp_manager import JobTempDir
            tmp = JobTempDir(job_id)
            if tmp.exists:
                tmp.cleanup()
        except Exception as e:
            print(f"[worker] Temp cleanup warning: {e}")


# =============================================================================
# Main Loop
# =============================================================================

def get_active_count() -> int:
    with active_jobs_lock:
        completed = [k for k, v in active_jobs.items() if v["future"].done()]
        for k in completed:
            active_jobs.pop(k, None)
        return len(active_jobs)


def poll_and_process(executor: ThreadPoolExecutor):
    """Poll queue and submit jobs to thread pool."""
    active_count = get_active_count()
    heavy_slots_full = active_count >= MAX_WORKERS

    client = get_queue_client()
    messages = client.receive_messages(
        messages_per_page=5,
        visibility_timeout=VISIBILITY_TIMEOUT,
    )

    for msg in messages:
        try:
            payload = json.loads(msg.content)
            job_type = payload.get("job_type", "video_analysis")
            job_id = payload.get("video_id", payload.get("clip_id", "unknown"))

            # --- Dead Letter Queue ---
            if hasattr(msg, "dequeue_count") and msg.dequeue_count is not None:
                if msg.dequeue_count >= MAX_DEQUEUE_COUNT:
                    reason = f"POISON_MAX_RETRY (dequeue_count={msg.dequeue_count})"
                    print(f"[worker] POISON: job={job_id}, dequeue={msg.dequeue_count}")

                    moved = move_to_dead_letter_queue(payload, reason, msg.dequeue_count)
                    log_error_type(job_id, job_type, "POISON_MAX_RETRY", f"dequeue={msg.dequeue_count}")
                    record_poison_job(job_id, job_type, "POISON_MAX_RETRY",
                                      dequeue_count=msg.dequeue_count, payload=payload)

                    if moved:
                        delete_message_safe(msg.id, msg.pop_receipt)
                    else:
                        print(f"[worker] CRITICAL: DLQ move failed for {job_id}")
                        delete_message_safe(msg.id, msg.pop_receipt)

                    if job_type in ("video_analysis", None) and job_id != "unknown":
                        update_video_status_to_error(job_id)
                    elif job_type == "generate_clip":
                        update_clip_status_to_dead(payload.get("clip_id", job_id), reason)

                    continue

            # --- live_monitor: separate executor ---
            if job_type == "live_monitor":
                with live_monitor_lock:
                    if job_id in live_monitor_jobs and not live_monitor_jobs[job_id]["future"].done():
                        continue
                future = live_monitor_executor.submit(process_job, payload, msg.id, msg.pop_receipt)
                with live_monitor_lock:
                    live_monitor_jobs[job_id] = {
                        "future": future, "msg_id": msg.id, "pop_receipt": msg.pop_receipt,
                    }
                continue

            # --- Heavy jobs: subject to MAX_WORKERS ---
            if heavy_slots_full:
                continue

            with active_jobs_lock:
                if job_id in active_jobs and not active_jobs[job_id]["future"].done():
                    continue

            print(f"[worker] Received: type={job_type}, id={job_id} (active: {get_active_count()}/{MAX_WORKERS})")

            # Record worker claim
            if job_type in ("video_analysis", None) and job_id != "unknown":
                dq_count = getattr(msg, "dequeue_count", None) or 0
                update_worker_claimed(job_id, WORKER_INSTANCE_ID, dq_count)

            future = executor.submit(process_job, payload, msg.id, msg.pop_receipt)
            with active_jobs_lock:
                active_jobs[job_id] = {
                    "future": future, "msg_id": msg.id, "pop_receipt": msg.pop_receipt,
                }
            heavy_slots_full = get_active_count() >= MAX_WORKERS

        except Exception as e:
            print(f"[worker] Error parsing message: {e}")


def acquire_lock():
    """Acquire file lock to prevent duplicate instances.
    
    Uses fcntl.flock which is automatically released when the process dies
    (even via SIGKILL), because the kernel releases flock locks when the
    file descriptor is closed (process termination closes all FDs).
    
    Additionally checks for stale PID in the lock file as a safety net.
    """
    lock_file = Path("/tmp/simple_worker.lock")
    fp = open(lock_file, "w")
    try:
        fcntl.flock(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fp.write(str(os.getpid()))
        fp.flush()
        return fp
    except IOError:
        # Check if the lock holder is still alive (stale lock detection)
        try:
            with open(lock_file, "r") as rf:
                old_pid = int(rf.read().strip())
            os.kill(old_pid, 0)  # signal 0 = check existence
            print(f"[worker] Another worker instance is already running (PID {old_pid}). Exiting.")
            sys.exit(1)
        except (ValueError, ProcessLookupError, PermissionError, FileNotFoundError):
            # PID is invalid or process is dead -> stale lock
            print(f"[worker] Stale lock detected. Removing and re-acquiring...")
            fp.close()
            lock_file.unlink(missing_ok=True)
            fp = open(lock_file, "w")
            try:
                fcntl.flock(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fp.write(str(os.getpid()))
                fp.flush()
                return fp
            except IOError:
                print("[worker] Failed to acquire lock even after stale removal. Exiting.")
                sys.exit(1)


def periodic_disk_cleanup():
    """Periodically check disk space and clean up old files."""
    global _last_disk_cleanup
    now = time.time()
    if now - _last_disk_cleanup < DISK_CLEANUP_INTERVAL:
        return
    _last_disk_cleanup = now

    try:
        original_cwd = os.getcwd()
        os.chdir(BATCH_DIR)
        sys.path.insert(0, BATCH_DIR)
        from disk_guard import periodic_disk_check
        active_ids = set()
        with active_jobs_lock:
            active_ids = set(active_jobs.keys())
        periodic_disk_check(active_ids=active_ids)
        os.chdir(original_cwd)
    except Exception as e:
        print(f"[worker][disk] Cleanup error: {e}")


# =============================================================================
# Stability modules (Task 1-5)
# =============================================================================

_heartbeat_manager = None
_stalled_recovery = None


def main():
    global _heartbeat_manager, _stalled_recovery

    lock_fp = acquire_lock()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # ── Task 5: Startup Self Check ──
    # Verifies ffmpeg, temp dir, queue, DB before accepting any jobs.
    # Exits immediately if any check fails.
    try:
        from worker.recovery.startup_check import run_startup_checks
        run_startup_checks()
    except SystemExit:
        lock_fp.close()
        raise

    # ── Task 2: Temp startup cleanup ──
    # Remove stale temp dirs from previous crashes (>6 hours old)
    try:
        from worker.recovery.temp_manager import startup_cleanup
        startup_cleanup()
    except Exception as e:
        print(f"[worker] Warning: Temp startup cleanup failed: {e}")

    # Crash guard
    print("[worker] Running crash guard...")
    crash_guard_kill_orphan_ffmpeg()

    # Log connection details
    storage_account = "UNKNOWN"
    for part in AZURE_STORAGE_CONNECTION_STRING.split(";"):
        if part.startswith("AccountName="):
            storage_account = part.split("=", 1)[1]
            break

    print(f"[worker] === AitherHub Queue Worker ===")
    print(f"[worker] Instance: {WORKER_INSTANCE_ID}")
    print(f"[worker] Max concurrent: {MAX_WORKERS}")
    print(f"[worker] Storage account: {storage_account}")
    print(f"[worker] Queue: {AZURE_QUEUE_NAME}")
    print(f"[worker] Dead-letter queue: {AZURE_DEAD_LETTER_QUEUE_NAME}")
    print(f"[worker] Environment: {ENVIRONMENT}")
    print(f"[worker] Visibility timeout: {VISIBILITY_TIMEOUT}s")
    print(f"[worker] Video timeout: {WORKER_VIDEO_TIMEOUT}s")
    print(f"[worker] Clip timeout: {WORKER_CLIP_TIMEOUT}s")
    print(f"[worker] Max retries: {MAX_DEQUEUE_COUNT}")
    print(f"[worker] Entrypoint: worker.entrypoints.queue_worker (independent)")

    # Ensure dead-letter queue exists
    try:
        get_dead_letter_queue_client()
    except Exception as e:
        print(f"[worker] Warning: Could not init dead-letter queue: {e}")

    # Background visibility renewal
    renewal_thread = Thread(target=visibility_renewal_loop, daemon=True)
    renewal_thread.start()

    # ── Task 1: Start Heartbeat Manager ──
    # Updates heartbeat_at every 30s for all active clip jobs
    try:
        from worker.recovery.heartbeat_manager import HeartbeatManager
        _heartbeat_manager = HeartbeatManager()
        _heartbeat_manager.start()
    except Exception as e:
        print(f"[worker] Warning: Heartbeat manager failed to start: {e}")

    # ── Task 1: Start Stalled Job Recovery ──
    # Checks every 60s for jobs with stale heartbeats, retries or marks dead
    try:
        from worker.recovery.stalled_job_recovery import StalledJobRecovery
        _stalled_recovery = StalledJobRecovery(worker_id=WORKER_INSTANCE_ID)
        _stalled_recovery.start()
    except Exception as e:
        print(f"[worker] Warning: Stalled recovery failed to start: {e}")

    # Initial disk cleanup
    periodic_disk_cleanup()

    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

    try:
        while not shutdown_requested:
            try:
                periodic_disk_cleanup()
                poll_and_process(executor)
                time.sleep(5)
            except Exception as e:
                print(f"[worker] Unexpected error: {e}")
                time.sleep(10)
    finally:
        print(f"[worker] Waiting for {get_active_count()} active jobs...")
        executor.shutdown(wait=True)

        # Stop stability modules
        if _heartbeat_manager:
            _heartbeat_manager.stop()
        if _stalled_recovery:
            _stalled_recovery.stop()

        lock_fp.close()
        print("[worker] Worker shut down.")


if __name__ == "__main__":
    main()
