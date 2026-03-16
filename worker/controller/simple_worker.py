#!/usr/bin/env python3
"""Simple queue worker that polls Azure Queue and runs batch processing.
Supports concurrent processing of multiple jobs using ThreadPoolExecutor.

Queue Design (2026-03 improvement):
  message → worker → fail → retry (with delay) → retry → retry → dead-letter queue
  POISON messages are NEVER deleted. They are moved to a dead-letter queue
  for investigation and manual re-processing.

Worker Safety:
  - File lock prevents duplicate worker instances
  - Crash guard kills orphaned ffmpeg processes on startup
  - Graceful shutdown waits for active jobs to complete
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
from concurrent.futures import ThreadPoolExecutor, Future
from threading import Lock, Thread
from azure.storage.queue import QueueClient
from dotenv import load_dotenv

# Load .env from project root
project_root = Path(__file__).parent.parent.parent
load_dotenv(project_root / ".env")

# Add batch directory to path so we can import if needed
BATCH_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "batch"))
REALTIME_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "realtime"))
sys.path.insert(0, BATCH_DIR)

# Maximum concurrent jobs
MAX_WORKERS = int(os.getenv("WORKER_MAX_CONCURRENT", "2"))

# Maximum retry attempts before moving message to dead-letter queue
MAX_DEQUEUE_COUNT = int(os.getenv("WORKER_MAX_RETRIES", "3"))

# Visibility timeout: 15 minutes (renewed every 5 min while job is active)
VISIBILITY_TIMEOUT = 15 * 60  # 900 seconds

# Visibility renewal interval: renew every 5 minutes to keep message invisible
VISIBILITY_RENEW_INTERVAL = 5 * 60  # 300 seconds

# Track active jobs: job_id -> {"future": Future, "msg_id": str, "pop_receipt": str}
active_jobs: dict[str, dict] = {}
active_jobs_lock = Lock()

# Separate executor for lightweight live_monitor jobs (not subject to MAX_WORKERS)
live_monitor_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="live-monitor")
live_monitor_jobs: dict[str, dict] = {}
live_monitor_lock = Lock()

# Separate executor for clip generation jobs (not subject to MAX_WORKERS)
# Clip jobs are lightweight (10min timeout) and should not be blocked by heavy video analysis
clip_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="clip-gen")
clip_jobs: dict[str, dict] = {}
clip_jobs_lock = Lock()

# Graceful shutdown flag
shutdown_requested = False

# Worker instance identifier for tracing
WORKER_INSTANCE_ID = f"{socket.gethostname()}-{os.getpid()}"

# --- Poison job log (local backup) ---
POISON_LOG = Path(__file__).parent.parent / "poison_jobs.jsonl"

# --- Dead Letter Queue name ---
DEAD_LETTER_QUEUE_NAME = os.getenv("AZURE_DEAD_LETTER_QUEUE_NAME", "video-jobs-dead")


# =============================================================================
# Dead Letter Queue
# =============================================================================

def get_dead_letter_queue_client():
    """Get or create the dead-letter queue client."""
    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not conn_str:
        raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING required")
    client = QueueClient.from_connection_string(conn_str, DEAD_LETTER_QUEUE_NAME)
    # Ensure the dead-letter queue exists (idempotent)
    try:
        client.create_queue()
        print(f"[worker] Dead-letter queue '{DEAD_LETTER_QUEUE_NAME}' created")
    except Exception:
        pass  # Queue already exists
    return client


def move_to_dead_letter_queue(payload: dict, reason: str, dequeue_count: int):
    """Move a failed message to the dead-letter queue instead of deleting it.
    The original payload is wrapped with metadata for investigation."""
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
        print(f"[worker] Moved job {job_id} to dead-letter queue '{DEAD_LETTER_QUEUE_NAME}' "
              f"(reason={reason}, dequeue_count={dequeue_count})")
        return True
    except Exception as e:
        print(f"[worker] CRITICAL: Failed to move message to dead-letter queue: {e}")
        return False


# =============================================================================
# Crash Guard
# =============================================================================

def crash_guard_kill_orphan_ffmpeg():
    """Kill orphaned ffmpeg processes from previous worker crashes.
    Only kills ffmpeg processes that are NOT children of the current worker.
    This prevents zombie ffmpeg processes from consuming resources."""
    my_pid = os.getpid()
    killed = 0
    try:
        # Find all ffmpeg processes
        result = subprocess.run(
            ["pgrep", "-a", "ffmpeg"],
            capture_output=True, text=True, timeout=5
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
            # Don't kill our own children (shouldn't exist at startup, but safe)
            try:
                ppid_result = subprocess.run(
                    ["ps", "-o", "ppid=", "-p", str(pid)],
                    capture_output=True, text=True, timeout=5
                )
                ppid = int(ppid_result.stdout.strip())
                if ppid == my_pid:
                    continue
            except Exception as _e:
                print(f"Suppressed: {_e}")

            # Kill the orphan
            try:
                os.kill(pid, signal.SIGKILL)
                killed += 1
                cmd_info = parts[1] if len(parts) > 1 else "unknown"
                print(f"[worker][crash-guard] Killed orphan ffmpeg pid={pid}: {cmd_info[:100]}")
            except (ProcessLookupError, PermissionError) as _e:
                print(f"Suppressed: {_e}")

    except Exception as e:
        print(f"[worker][crash-guard] Error during orphan cleanup: {e}")

    if killed > 0:
        print(f"[worker][crash-guard] Killed {killed} orphan ffmpeg process(es)")
    else:
        print("[worker][crash-guard] No orphan ffmpeg processes found")


# =============================================================================
# Logging & Error Tracking
# =============================================================================

def log_error_type(job_id: str, job_type: str, error_type: str, detail: str = ""):
    """Log structured error type for every failure. Enables error classification."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[worker] ERROR_TYPE={error_type} job={job_id} type={job_type} detail={detail}")


def record_poison_job(job_id: str, job_type: str, error_type: str,
                      dequeue_count: int = 0, payload: dict | None = None):
    """Append a poison (permanently failed) job to poison_jobs.jsonl for local backup.
    The primary record is in the dead-letter queue."""
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
        print(f"[worker] Recorded poison job {job_id} to {POISON_LOG}")
    except Exception as e:
        print(f"[worker] Warning: Failed to write poison log: {e}")


# =============================================================================
# Signal Handling
# =============================================================================

def signal_handler(signum, frame):
    global shutdown_requested
    print(f"\n[worker] Received signal {signum}, shutting down gracefully...")
    print(f"[worker] Waiting for {get_active_count()} active jobs to complete before exit...")
    shutdown_requested = True


# =============================================================================
# Queue Operations
# =============================================================================

def get_queue_client():
    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    queue_name = os.getenv("AZURE_QUEUE_NAME", "video-jobs")
    if not conn_str:
        raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING required")
    return QueueClient.from_connection_string(conn_str, queue_name)


def delete_message_safe(msg_id: str, pop_receipt: str):
    """Safely delete a message from the queue after job completion."""
    try:
        client = get_queue_client()
        client.delete_message(msg_id, pop_receipt)
        return True
    except Exception as e:
        print(f"[worker] Warning: Failed to delete message {msg_id}: {e}")
        return False


def renew_visibility(msg_id: str, pop_receipt: str, job_id: str):
    """Renew message visibility to prevent it from reappearing while processing.
    Returns the new pop_receipt or None on failure."""
    try:
        client = get_queue_client()
        result = client.update_message(
            msg_id,
            pop_receipt,
            visibility_timeout=VISIBILITY_TIMEOUT,
        )
        return result.pop_receipt
    except Exception as e:
        print(f"[worker] Warning: Failed to renew visibility for job {job_id}: {e}")
        return None


def visibility_renewal_loop():
    """Background thread that periodically renews visibility for active jobs."""
    while not shutdown_requested:
        time.sleep(VISIBILITY_RENEW_INTERVAL)
        with active_jobs_lock:
            for job_id, info in list(active_jobs.items()):
                if info["future"].done():
                    continue
                new_receipt = renew_visibility(
                    info["msg_id"], info["pop_receipt"], job_id
                )
                if new_receipt:
                    info["pop_receipt"] = new_receipt


# =============================================================================
# DB Status Helpers
# =============================================================================

def update_video_status_to_error(video_id: str, error_code: str = "UNKNOWN",
                                  error_step: str = "UNKNOWN",
                                  error_message: str = ""):
    """Mark a video as ERROR in the database and record error log."""
    try:
        sys.path.insert(0, BATCH_DIR)
        from db_ops import init_db_sync, update_video_status_sync, close_db_sync, insert_video_error_log_sync
        from video_status import VideoStatus
        init_db_sync()
        update_video_status_sync(video_id, VideoStatus.ERROR)
        # Also record error log so UI can display it
        try:
            insert_video_error_log_sync(
                video_id=video_id,
                error_code=error_code,
                error_step=error_step,
                error_message=error_message[:2000] if error_message else "Worker marked video as ERROR",
                source="worker",
            )
        except Exception as log_err:
            print(f"[worker] Failed to record error log: {log_err}")
        close_db_sync()
        print(f"[worker] Marked video {video_id} as ERROR")
    except Exception as db_err:
        print(f"[worker] Failed to mark video as ERROR: {db_err}")


def update_clip_status_to_dead(clip_id: str, error_message: str):
    """Mark a clip as 'dead' in the database (moved to dead-letter queue)."""
    try:
        sys.path.insert(0, BATCH_DIR)
        from db_ops import init_db_sync, close_db_sync, get_event_loop, get_session
        from sqlalchemy import text
        init_db_sync()
        loop = get_event_loop()

        async def _update():
            async with get_session() as session:
                sql = text("""
                    UPDATE video_clips
                    SET status = :status, error_message = :error_message, updated_at = NOW()
                    WHERE id = :clip_id
                """)
                await session.execute(sql, {
                    "status": "dead",
                    "error_message": error_message[:500],
                    "clip_id": clip_id,
                })

        loop.run_until_complete(_update())
        close_db_sync()
        print(f"[worker] Marked clip {clip_id} as 'dead'")
    except Exception as db_err:
        print(f"[worker] Failed to mark clip as dead: {db_err}")


# =============================================================================
# Job Processors
# =============================================================================

def process_job(payload: dict, msg_id: str, pop_receipt: str):
    """Process a single job. Runs in a thread.
    Deletes the queue message only after successful completion.
    On failure, the message will reappear after visibility timeout."""
    job_type = payload.get("job_type", "video_analysis")
    job_id = payload.get("video_id", payload.get("clip_id", "unknown"))

    try:
        if job_type == "generate_clip":
            success = process_clip_job(payload)
        elif job_type == "live_capture":
            success = process_live_capture_job(payload)
        elif job_type == "live_monitor":
            success = process_live_monitor_job(payload)
        elif job_type == "live_analysis":
            success = process_live_analysis_job(payload)
        else:
            success = process_video_job(payload)

        if success:
            # Only delete message from queue after successful processing
            with active_jobs_lock:
                info = active_jobs.get(job_id, {})
                current_receipt = info.get("pop_receipt", pop_receipt)
            delete_message_safe(msg_id, current_receipt)
        else:
            print(f"[worker] Job {job_id} failed, message will reappear after visibility timeout for retry")

        return success
    except Exception as e:
        exc_name = type(e).__name__
        log_error_type(job_id, job_type, "UNKNOWN", f"EXC={exc_name} {e}")
        return False
    finally:
        with active_jobs_lock:
            active_jobs.pop(job_id, None)


def process_live_analysis_job(payload: dict):
    """Handle LiveBoost analysis pipeline job.
    Runs run_live_analysis.py which assembles chunks, extracts audio,
    transcribes, runs OCR, detects sales moments, and generates clips.
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

    # PYTHONPATH must include project root (for shared/) and backend/ (for app/)
    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
    env = {
        **os.environ,
        "PYTHONPATH": f"{project_root}:{backend_dir}:{BATCH_DIR}",
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
            stdout_data, _ = proc.communicate(timeout=VIDEO_PROCESS_TIMEOUT)
        except subprocess.TimeoutExpired:
            print(f"[worker] Live analysis timeout — killing pid={proc.pid}")
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError) as _e:
                print(f"Suppressed: {_e}")
            stdout_data, _ = proc.communicate()
            log_error_type(job_id, "live_analysis", "TIMEOUT", f"timeout={VIDEO_PROCESS_TIMEOUT}s")
            return False

        output = stdout_data.decode("utf-8", errors="replace") if stdout_data else ""
        # Always log last 2000 chars of output for debugging
        if output:
            print(f"[worker] live_analysis output (last 2000 chars):\n{output[-2000:]}")

        if proc.returncode == 0:
            print(f"[worker] Live analysis completed: job={job_id} video={video_id}")
            return True
        elif proc.returncode == 2:
            print(f"[worker] Live analysis skipped (input error): job={job_id}")
            return True  # Delete queue message
        else:
            log_error_type(job_id, "live_analysis", "SUBPROCESS_FAIL", f"exit_code={proc.returncode} output_tail={output[-500:]}")
            return False
    except Exception as e:
        exc_name = type(e).__name__
        log_error_type(job_id, "live_analysis", "UNKNOWN", f"EXC={exc_name} {e}")
        return False


def process_live_monitor_job(payload: dict):
    """Handle TikTok live real-time monitoring job.
    Runs the live_monitor.py script which connects to TikTok WebSocket,
    collects metrics, generates AI advice, and pushes to backend SSE."""
    video_id = payload.get("video_id")
    live_url = payload.get("live_url", "")
    username = payload.get("username", "")

    if not video_id or not username:
        log_error_type(video_id or "unknown", "live_monitor", "INPUT_INVALID", "missing video_id or username")
        return False

    print(f"[worker] Starting live monitor for @{username} (video_id={video_id})")
    cmd = [
        sys.executable,
        os.path.join(REALTIME_DIR, "live_monitor.py"),
        "--unique-id", username,
        "--video-id", video_id,
    ]

    result = subprocess.run(
        cmd,
        cwd=REALTIME_DIR,
        env={**os.environ, "PYTHONPATH": f"{REALTIME_DIR}:{BATCH_DIR}"},
        start_new_session=True,
    )

    if result.returncode == 0:
        print(f"[worker] Live monitor completed for @{username} (video_id={video_id})")
        return True
    else:
        log_error_type(video_id, "live_monitor", "SUBPROCESS_FAIL", f"exit_code={result.returncode}")
        return False


def process_live_capture_job(payload: dict):
    """Handle TikTok live stream capture job.
    Captures the stream, uploads to blob, then enqueues a video_analysis job.
    Also starts a live_monitor subprocess in parallel for real-time analysis."""
    video_id = payload.get("video_id")
    live_url = payload.get("live_url")
    email = payload.get("email", "")
    user_id = str(payload.get("user_id", ""))
    duration = payload.get("duration", 0)

    if not video_id or not live_url:
        log_error_type(video_id or "unknown", "live_capture", "INPUT_INVALID", "missing video_id or live_url")
        return False

    # Extract username from URL for live monitor
    import re
    match = re.search(r"@([^/]+)", live_url)
    username = match.group(1) if match else ""

    # Start live monitor as a background subprocess (non-blocking)
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
                monitor_cmd,
                cwd=REALTIME_DIR,
                env={**os.environ, "PYTHONPATH": f"{REALTIME_DIR}:{BATCH_DIR}"},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            print(f"[worker] Live monitor started for @{username} (pid={monitor_proc.pid})")
        except Exception as e:
            print(f"[worker] Warning: Failed to start live monitor: {e}")

    print(f"[worker] Starting live capture for video_id={video_id}, url={live_url}")
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
        cmd,
        cwd=BATCH_DIR,
        env={**os.environ, "PYTHONPATH": BATCH_DIR},
        start_new_session=True,
    )

    # Stop live monitor when capture ends — kill entire process group
    if monitor_proc and monitor_proc.poll() is None:
        print(f"[worker] Stopping live monitor process group (pid={monitor_proc.pid})")
        try:
            os.killpg(os.getpgid(monitor_proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError) as _e:
            print(f"Suppressed: {_e}")

    if result.returncode == 0:
        print(f"[worker] Live capture completed for {video_id}")
        return True
    elif result.returncode == 2:
        print(f"[worker] Live capture: user is not currently live (video_id={video_id})")
        return True  # Don't retry - user is offline
    else:
        log_error_type(video_id, "live_capture", "SUBPROCESS_FAIL", f"exit_code={result.returncode}")
        return False


# Timeout for video analysis subprocess
# Must match shared/config WORKER_VIDEO_TIMEOUT (24h for 9h+ recordings)
VIDEO_PROCESS_TIMEOUT = int(os.getenv("WORKER_VIDEO_TIMEOUT", str(1440 * 60)))

# Timeout for clip generation subprocess (10 minutes)
CLIP_PROCESS_TIMEOUT = int(os.getenv("WORKER_CLIP_TIMEOUT", str(10 * 60)))


def process_clip_job(payload: dict):
    """Handle clip generation job."""
    clip_id = payload.get("clip_id")
    video_id = payload.get("video_id")
    blob_url = payload.get("blob_url")
    time_start = payload.get("time_start")
    time_end = payload.get("time_end")

    if not all([clip_id, video_id, blob_url, time_start is not None, time_end is not None]):
        log_error_type(clip_id or "unknown", "generate_clip", "INPUT_INVALID", "missing required clip fields")
        return False

    phase_index = payload.get("phase_index", -1)
    speed_factor = payload.get("speed_factor", 1.0)

    print(f"[worker] Starting clip generation for clip_id={clip_id} (speed={speed_factor}x, timeout={CLIP_PROCESS_TIMEOUT}s)")
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
        proc = subprocess.Popen(
            cmd,
            cwd=BATCH_DIR,
            env={**os.environ, "PYTHONPATH": BATCH_DIR},
            start_new_session=True,
        )
        try:
            proc.wait(timeout=CLIP_PROCESS_TIMEOUT)
        except subprocess.TimeoutExpired:
            print(f"[worker] Clip timeout — killing process group (pid={proc.pid})")
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError) as _e:
                print(f"Suppressed: {_e}")
            proc.wait()
            log_error_type(clip_id, "generate_clip", "TIMEOUT_CLIP", f"timeout={CLIP_PROCESS_TIMEOUT}s")
            return False

        if proc.returncode == 0:
            print(f"[worker] Clip generation completed for {clip_id}")
            return True
        else:
            log_error_type(clip_id, "generate_clip", "FFMPEG_FAIL", f"exit_code={proc.returncode}")
            return False
    except Exception as e:
        exc_name = type(e).__name__
        log_error_type(clip_id, "generate_clip", "UNKNOWN", f"EXC={exc_name} {e}")
        return False


def process_video_job(payload: dict):
    """Handle video analysis job."""
    video_id = payload.get("video_id")
    blob_url = payload.get("blob_url")

    if not video_id or not blob_url:
        log_error_type(video_id or "unknown", "video_analysis", "INPUT_INVALID", "missing video_id or blob_url")
        return False

    print(f"[worker] Starting batch for video_id={video_id}")
    cmd = [
        sys.executable,
        os.path.join(BATCH_DIR, "process_video.py"),
        "--video-id", video_id,
        "--blob-url", blob_url,
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=BATCH_DIR,
            env={**os.environ, "PYTHONPATH": BATCH_DIR},
            start_new_session=True,
        )
        try:
            proc.wait(timeout=VIDEO_PROCESS_TIMEOUT)
        except subprocess.TimeoutExpired:
            print(f"[worker] Video timeout — killing process group (pid={proc.pid})")
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError) as _e:
                print(f"Suppressed: {_e}")
            proc.wait()
            log_error_type(video_id, "video_analysis", "TIMEOUT_VIDEO", f"timeout={VIDEO_PROCESS_TIMEOUT}s")
            update_video_status_to_error(
                video_id,
                error_code="TIMEOUT_VIDEO",
                error_step="VIDEO_PROCESSING",
                error_message=f"Video processing timed out after {VIDEO_PROCESS_TIMEOUT}s",
            )
            return False

        if proc.returncode == 0:
            print(f"[worker] Batch completed successfully for {video_id}")
            return True
        elif proc.returncode == 2:
            # Exit code 2 = ORPHAN_VIDEO: DB record not found (deleted or never created)
            print(f"[worker] ORPHAN_VIDEO skip: video_id={video_id} not found in DB. "
                  f"Deleting queue message (no retry).")
            log_error_type(video_id, "video_analysis", "ORPHAN_VIDEO",
                           "DB record not found, message will be deleted")
            return True  # Return True so process_job() deletes the queue message
        else:
            log_error_type(video_id, "video_analysis", "SUBPROCESS_FAIL", f"exit_code={proc.returncode}")
            return False
    except Exception as e:
        exc_name = type(e).__name__
        log_error_type(video_id, "video_analysis", "UNKNOWN", f"EXC={exc_name} {e}")
        return False


# =============================================================================
# DB Fallback: Poll pending clips directly from database
# =============================================================================

_last_clip_fallback_check = 0
CLIP_FALLBACK_INTERVAL = 60  # Check every 60 seconds
CLIP_FALLBACK_AGE = 120  # Clips pending for > 2 minutes


def poll_pending_clips_from_db():
    """Fallback: check DB for pending clips that may have lost their queue message.
    If a clip has been pending for > CLIP_FALLBACK_AGE seconds and is not already
    being processed by clip_executor, submit it directly."""
    global _last_clip_fallback_check
    now = time.time()
    if now - _last_clip_fallback_check < CLIP_FALLBACK_INTERVAL:
        return
    _last_clip_fallback_check = now

    try:
        sys.path.insert(0, BATCH_DIR)
        from db_ops import init_db_sync, get_event_loop, get_session
        from sqlalchemy import text
        init_db_sync()
        loop = get_event_loop()

        async def _fetch_pending():
            async with get_session() as session:
                sql = text(f"""
                    SELECT id, job_payload
                    FROM video_clips
                    WHERE status = 'pending'
                    AND COALESCE(updated_at, created_at) < NOW() - INTERVAL '{CLIP_FALLBACK_AGE} seconds'
                    AND job_payload IS NOT NULL
                    LIMIT 5
                """)
                result = await session.execute(sql)
                return result.fetchall()

        rows = loop.run_until_complete(_fetch_pending())

        if not rows:
            return

        for row in rows:
            clip_id = str(row.id)
            payload = row.job_payload if isinstance(row.job_payload, dict) else json.loads(row.job_payload)

            # Skip if already being processed
            with clip_jobs_lock:
                if clip_id in clip_jobs and not clip_jobs[clip_id]["future"].done():
                    continue

            print(f"[worker] DB fallback: found pending clip {clip_id}, submitting to clip_executor")

            # Mark as processing to prevent duplicate pickup
            _cid = clip_id  # capture for closure
            try:
                async def _mark_processing(cid=_cid):
                    async with get_session() as session:
                        sql = text("""
                            UPDATE video_clips
                            SET status = 'processing', progress_step = 'queued_by_fallback', updated_at = NOW()
                            WHERE id = :clip_id AND status = 'pending'
                        """)
                        await session.execute(sql, {"clip_id": cid})
                loop.run_until_complete(_mark_processing())
            except Exception as mark_err:
                print(f"[worker] DB fallback: failed to mark {clip_id} as processing: {mark_err}")

            # Submit to clip_executor (no queue message to delete, use dummy msg_id/pop_receipt)
            future = clip_executor.submit(process_clip_job, payload)
            with clip_jobs_lock:
                clip_jobs[clip_id] = {
                    "future": future,
                    "msg_id": None,
                    "pop_receipt": None,
                }

    except Exception as e:
        print(f"[worker] DB fallback error: {e}")


# =============================================================================
# Main Loop
# =============================================================================

def get_active_count():
    """Get the number of currently active jobs."""
    with active_jobs_lock:
        # Clean up completed futures
        completed = [k for k, v in active_jobs.items() if v["future"].done()]
        for k in completed:
            active_jobs.pop(k, None)
        return len(active_jobs)


def poll_and_process(executor: ThreadPoolExecutor):
    """Poll queue and submit jobs to the thread pool.
    live_monitor jobs bypass MAX_WORKERS and run on a separate executor.

    Dead Letter Queue flow:
      dequeue_count >= MAX_DEQUEUE_COUNT
        → move message to dead-letter queue
        → delete from main queue
        → mark job as 'dead' in DB
        → log to poison_jobs.jsonl (local backup)
    """
    active_count = get_active_count()
    heavy_slots_full = active_count >= MAX_WORKERS

    client = get_queue_client()

    # Always peek up to 5 messages (we may still accept live_monitor even when heavy slots full)
    messages = client.receive_messages(
        messages_per_page=5,
        visibility_timeout=VISIBILITY_TIMEOUT,
    )

    for msg in messages:
        try:
            payload = json.loads(msg.content)
            job_type = payload.get("job_type", "video_analysis")
            job_id = payload.get("video_id", payload.get("clip_id", "unknown"))

            # --- Dead Letter Queue: move after too many retries (NEVER delete) ---
            if hasattr(msg, 'dequeue_count') and msg.dequeue_count is not None:
                if msg.dequeue_count >= MAX_DEQUEUE_COUNT:
                    reason = f"POISON_MAX_RETRY (dequeue_count={msg.dequeue_count} >= {MAX_DEQUEUE_COUNT})"
                    print(f"[worker] POISON MESSAGE detected: job={job_id}, type={job_type}, "
                          f"dequeue_count={msg.dequeue_count} >= {MAX_DEQUEUE_COUNT}. "
                          f"Moving to dead-letter queue.")

                    # Step 1: Move to dead-letter queue (preserve the message)
                    moved = move_to_dead_letter_queue(payload, reason, msg.dequeue_count)

                    # Step 2: Log locally as backup
                    log_error_type(job_id, job_type, "POISON_MAX_RETRY",
                                   f"dequeue_count={msg.dequeue_count}")
                    record_poison_job(job_id, job_type, "POISON_MAX_RETRY",
                                      dequeue_count=msg.dequeue_count, payload=payload)

                    # Step 3: Delete from main queue (only after successful DLQ move)
                    if moved:
                        delete_message_safe(msg.id, msg.pop_receipt)
                    else:
                        # If DLQ move failed, still delete to prevent infinite loop
                        # but log a CRITICAL warning
                        print(f"[worker] CRITICAL: DLQ move failed for {job_id}, "
                              f"deleting from main queue anyway (data preserved in poison_jobs.jsonl)")
                        delete_message_safe(msg.id, msg.pop_receipt)

                    # Step 4: Mark job as 'dead' in DB
                    if job_type in ("video_analysis", None) and job_id != "unknown":
                        update_video_status_to_error(
                            job_id,
                            error_code="POISON_MAX_RETRY",
                            error_step="WORKER_QUEUE",
                            error_message=f"Video processing failed after {msg.dequeue_count} retries. Moved to dead-letter queue.",
                        )
                    elif job_type == "generate_clip":
                        clip_id = payload.get("clip_id", job_id)
                        update_clip_status_to_dead(clip_id, reason)

                    continue

            # --- live_monitor: runs on separate lightweight executor ---
            if job_type == "live_monitor":
                with live_monitor_lock:
                    if job_id in live_monitor_jobs and not live_monitor_jobs[job_id]["future"].done():
                        print(f"[worker] Live monitor {job_id} already running, skipping")
                        continue
                print(f"[worker] Received live_monitor job: id={job_id} (bypasses MAX_WORKERS)")
                future = live_monitor_executor.submit(process_job, payload, msg.id, msg.pop_receipt)
                with live_monitor_lock:
                    live_monitor_jobs[job_id] = {
                        "future": future,
                        "msg_id": msg.id,
                        "pop_receipt": msg.pop_receipt,
                    }
                continue

            # --- generate_clip: runs on separate clip executor (bypasses MAX_WORKERS) ---
            if job_type == "generate_clip":
                clip_id = payload.get("clip_id", job_id)
                with clip_jobs_lock:
                    if clip_id in clip_jobs and not clip_jobs[clip_id]["future"].done():
                        print(f"[worker] Clip {clip_id} already in progress, skipping")
                        continue
                # Clean up completed clip jobs
                with clip_jobs_lock:
                    done_clips = [k for k, v in clip_jobs.items() if v["future"].done()]
                    for k in done_clips:
                        clip_jobs.pop(k, None)
                print(f"[worker] Received generate_clip job: clip_id={clip_id} (bypasses MAX_WORKERS, uses clip_executor)")
                future = clip_executor.submit(process_job, payload, msg.id, msg.pop_receipt)
                with clip_jobs_lock:
                    clip_jobs[clip_id] = {
                        "future": future,
                        "msg_id": msg.id,
                        "pop_receipt": msg.pop_receipt,
                    }
                continue

            # --- Heavy jobs: subject to MAX_WORKERS ---
            if heavy_slots_full:
                # Put message back by not processing it (visibility will expire)
                continue

            # Check if this job is already being processed
            with active_jobs_lock:
                if job_id in active_jobs and not active_jobs[job_id]["future"].done():
                    print(f"[worker] Job {job_id} already in progress, skipping duplicate")
                    continue

            print(f"[worker] Received job: type={job_type}, id={job_id} (active: {get_active_count()}/{MAX_WORKERS})")

            # --- Record worker_claimed evidence to DB ---
            if job_type in ("video_analysis", None) and job_id != "unknown":
                try:
                    from db_ops import init_db_sync, update_worker_claimed_sync, close_db_sync
                    init_db_sync()
                    dq_count = getattr(msg, 'dequeue_count', None) or 0
                    update_worker_claimed_sync(job_id, WORKER_INSTANCE_ID, dq_count)
                    close_db_sync()
                    print(f"[worker] Claimed video {job_id} (instance={WORKER_INSTANCE_ID}, dequeue={dq_count})")
                except Exception as claim_err:
                    print(f"[worker] Failed to record worker_claimed: {claim_err}")

            # Submit job to thread pool
            future = executor.submit(process_job, payload, msg.id, msg.pop_receipt)
            with active_jobs_lock:
                active_jobs[job_id] = {
                    "future": future,
                    "msg_id": msg.id,
                    "pop_receipt": msg.pop_receipt,
                }
            heavy_slots_full = get_active_count() >= MAX_WORKERS

        except Exception as e:
            print(f"[worker] Error parsing message: {e}")
            # Don't delete on parse error; message will reappear after visibility timeout


def acquire_lock():
    """Acquire a file lock to prevent multiple worker instances.
    
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
            # Check if process exists
            os.kill(old_pid, 0)  # signal 0 = check existence
            print(f"[worker] Another worker instance is already running (PID {old_pid}). Exiting.")
            sys.exit(1)
        except (ValueError, ProcessLookupError, PermissionError, FileNotFoundError):
            # PID is invalid or process is dead → stale lock
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


# Disk cleanup interval: run every 30 minutes
DISK_CLEANUP_INTERVAL = 5 * 60  # 300 seconds (was 30min, reduced to handle batch uploads)
_last_disk_cleanup = 0


def periodic_disk_cleanup():
    """Periodically check disk space and clean up old files.
    Delegates to the centralised disk_guard module so that ALL temp
    directories (uploadedvideo, output, splitvideo, artifacts, logs)
    are covered in one place."""
    global _last_disk_cleanup
    now = time.time()
    if now - _last_disk_cleanup < DISK_CLEANUP_INTERVAL:
        return
    _last_disk_cleanup = now

    try:
        # Ensure disk_guard runs with the correct cwd
        original_cwd = os.getcwd()
        os.chdir(BATCH_DIR)

        from disk_guard import periodic_disk_check

        # Collect currently active video IDs
        active_ids = set()
        with active_jobs_lock:
            active_ids = set(active_jobs.keys())

        periodic_disk_check(active_ids=active_ids)

        os.chdir(original_cwd)
    except Exception as e:
        print(f"[worker][disk] Cleanup error: {e}")


def main():
    # Acquire lock to prevent duplicate instances
    lock_fp = acquire_lock()

    # Setup signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # --- Crash Guard: kill orphaned ffmpeg processes ---
    print("[worker] Running crash guard...")
    crash_guard_kill_orphan_ffmpeg()

    # --- Log queue connection details for debugging ENV mismatches ---
    conn_str = os.getenv('AZURE_STORAGE_CONNECTION_STRING', '')
    storage_account = 'UNKNOWN'
    for part in conn_str.split(';'):
        if part.startswith('AccountName='):
            storage_account = part.split('=', 1)[1]
            break
    queue_name = os.getenv('AZURE_QUEUE_NAME', 'video-jobs')
    env_label = os.getenv('ENVIRONMENT', 'unknown')

    print(f"[worker] Starting simple queue worker (max_concurrent={MAX_WORKERS})...")
    print(f"[worker] Instance: {WORKER_INSTANCE_ID}")
    print(f"[worker] Storage account: {storage_account}")
    print(f"[worker] Queue: {queue_name}")
    print(f"[worker] Dead-letter queue: {DEAD_LETTER_QUEUE_NAME}")
    print(f"[worker] Environment: {env_label}")
    print(f"[worker] Visibility timeout: {VISIBILITY_TIMEOUT}s ({VISIBILITY_TIMEOUT // 60}min, renewed every {VISIBILITY_RENEW_INTERVAL // 60}min)")
    print(f"[worker] Video process timeout: {VIDEO_PROCESS_TIMEOUT}s ({VIDEO_PROCESS_TIMEOUT // 60}min)")
    print(f"[worker] Clip process timeout: {CLIP_PROCESS_TIMEOUT}s ({CLIP_PROCESS_TIMEOUT // 60}min)")
    print(f"[worker] Max retries (dequeue count): {MAX_DEQUEUE_COUNT}")
    print(f"[worker] Message deletion: after successful completion only (retry on failure)")
    print(f"[worker] Poison handling: move to dead-letter queue (NEVER delete without backup)")
    print(f"[worker] Clip executor: 2 dedicated threads (bypasses MAX_WORKERS)")
    print(f"[worker] DB fallback: check pending clips every {CLIP_FALLBACK_INTERVAL}s (age > {CLIP_FALLBACK_AGE}s)")

    # Ensure dead-letter queue exists
    try:
        get_dead_letter_queue_client()
    except Exception as e:
        print(f"[worker] Warning: Could not initialize dead-letter queue: {e}")

    # Start background visibility renewal thread
    renewal_thread = Thread(target=visibility_renewal_loop, daemon=True)
    renewal_thread.start()

    # Initial disk cleanup on startup
    periodic_disk_cleanup()

    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

    try:
        while not shutdown_requested:
            try:
                periodic_disk_cleanup()
                poll_and_process(executor)
                poll_pending_clips_from_db()  # DB fallback for lost queue messages
                time.sleep(5)  # Poll every 5 seconds
            except Exception as e:
                print(f"[worker] Unexpected error: {e}")
                time.sleep(10)
    finally:
        print(f"[worker] Waiting for {get_active_count()} active jobs to complete...")
        executor.shutdown(wait=True)
        clip_executor.shutdown(wait=True)
        lock_fp.close()
        print("[worker] Worker shut down.")


if __name__ == "__main__":
    main()
