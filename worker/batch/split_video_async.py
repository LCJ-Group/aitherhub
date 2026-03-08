import os, sys
import shutil as _shutil
import json
import argparse
import subprocess
import logging
import shutil
import time
from urllib.parse import urlparse, unquote
from dotenv import load_dotenv

from db_ops import (
    init_db_sync,
    close_db_sync,
    get_video_split_status_sync,
    update_video_split_status_sync,
    load_video_phases_sync,
    get_user_id_of_video_sync
)

# =====================
# ENV & LOGGER
# =====================
load_dotenv()
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOG_DIR, "split_video.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(), 
    ],
)

logger = logging.getLogger("split_video_async")

# =====================
# BIN RESOLVE
# =====================
def resolve_bin(name, win_fallback=None, linux_fallback=None):
    if sys.platform.startswith("win"):
        return _shutil.which(name) or win_fallback
    return _shutil.which(name) or linux_fallback

FFMPEG = resolve_bin(
    "ffmpeg",
    win_fallback=r"C:\ffmpeg\bin\ffmpeg.exe",
    linux_fallback="/usr/bin/ffmpeg",
)

# FFPROBE = resolve_bin(
#     "ffprobe",
#     win_fallback=r"C:\ffmpeg\bin\ffprobe.exe",
#     linux_fallback="/usr/bin/ffprobe",
# )

if not FFMPEG:
    raise RuntimeError("ffmpeg not found")
# if not FFPROBE:
#     raise RuntimeError("ffprobe not found")

# =====================
# PATHS
# =====================
ART_ROOT = "output"

def cache_dir(video_id: str):
    return os.path.join(ART_ROOT, video_id, "cache")

def step1_cache_path(video_id: str):
    return os.path.join(cache_dir(video_id), "step1_phases.json")

SPLIT_VIDEO_DIR = os.path.join(os.path.dirname(__file__), "splitvideo")

# =====================
# AZURE CONFIG
# =====================
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
AZURE_BLOB_CONTAINER = os.getenv("AZURE_BLOB_CONTAINER", "videos")
SAS_EXP_MINUTES = int(os.getenv("AZURE_BLOB_SAS_EXP_MINUTES", "60"))


# =====================
# HELPERS
# =====================
def parse_blob_url(blob_url: str) -> dict:
    if "?" in blob_url:
        base_url, _ = blob_url.split("?", 1)
    else:
        base_url = blob_url

    parsed = urlparse(base_url)
    path_parts = parsed.path.lstrip("/").split("/", 1)

    blob_path = unquote(path_parts[1]) if len(path_parts) > 1 else ""
    parent_path = "/".join(blob_path.split("/")[:-1]) if "/" in blob_path else ""

    return {
        "blob_path": blob_path,
        "parent_path": parent_path,
    }


# def probe_fps(video_path: str) -> float:
#     cmd = [
#         FFPROBE,
#         # "ffprobe",
#         "-v", "error",
#         "-select_streams", "v:0",
#         "-show_entries", "stream=avg_frame_rate",
#         "-of", "default=noprint_wrappers=1:nokey=1",
#         video_path,
#     ]
#     out = subprocess.check_output(cmd, text=True).strip()
#     if "/" in out:
#         num, den = out.split("/")
#         return float(num) / float(den)
#     return float(out)


# def cut_segment(input_path, out_path, start_sec, end_sec, crf=23, preset="ultrafast"):

#     logger.info(
#         "[CUT] %s | %.2f -> %.2f | out=%s",
#         input_path,
#         start_sec,
#         end_sec,
#         out_path,
#     )

#     duration = end_sec - start_sec

#     if duration <= 0:
#         return False

#     # cmd = [
#     #     # "ffmpeg", 
#     #     FFMPEG,
#     #     "-y",
#     #     "-ss", str(start_sec),
#     #     "-i", input_path,
#     #     "-t", str(duration),
#     #     "-map", "0:v:0",
#     #     "-map", "0:a?",
#     #     "-c:v", "libx264",
#     #     "-preset", preset,
#     #     "-crf", str(crf),
#     #     # "-c:a", "copy",
#     #     "-c:a", "aac",

#     #     "-movflags", "+faststart",
#     #     out_path,
#     # ]

#     cmd = [
#         FFMPEG,
#         "-y",
#         "-i", input_path,       
#         "-ss", str(start_sec),   
#         "-t", str(duration),
#         "-map", "0:v:0",
#         "-map", "0:a?",
#         "-c:v", "libx264",
#         "-preset", preset,
#         "-crf", str(crf),
#         "-c:a", "aac",          
#         "-movflags", "+faststart",
#         out_path,
#     ]


#     try:
#         subprocess.run(cmd, check=True, capture_output=True, text=True)

#         if os.path.exists(out_path):
#             logger.info("[CUT OK] file created: %s (%.2f MB)", out_path, os.path.getsize(out_path) / 1024 / 1024)
#         else:
#             logger.error("[CUT FAIL] ffmpeg returned but file not found: %s", out_path)


#         return True
#     except subprocess.CalledProcessError as e:
#         logger.error("ffmpeg failed: %s", e.stderr)
#         if os.path.exists(out_path):
#             os.remove(out_path)
#         return False

def cut_segment(
    input_path,
    out_path,
    start_sec,
    end_sec,
    crf=23,
    preset="ultrafast",
    safe_seek=False,
):
    """Cut a segment from the source video.

    Strategy (fast → slow fallback):
      1. Stream-copy with -ss before -i  (fastest, ~seconds)
      2. Stream-copy with -ss after  -i  (slower seek but still no re-encode)
      3. Re-encode with libx264           (slowest, only if copy fails)
    """
    logger.info(
        "[CUT] %s | %.2f -> %.2f | out=%s | safe_seek=%s",
        input_path,
        start_sec,
        end_sec,
        out_path,
        safe_seek,
    )

    duration = end_sec - start_sec
    if duration <= 0:
        return False

    # --- Attempt 1: Fast stream-copy (-ss before -i) ---
    fast_copy_cmd = [
        FFMPEG, "-y",
        "-ss", str(start_sec),
        "-i", input_path,
        "-t", str(duration),
        "-map", "0:v:0",
        "-map", "0:a?",
        "-c", "copy",
        "-movflags", "+faststart",
        out_path,
    ]

    try:
        subprocess.run(fast_copy_cmd, check=True, capture_output=True, text=True)
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            logger.info(
                "[CUT OK] stream-copy (fast): %s (%.2f MB)",
                out_path,
                os.path.getsize(out_path) / 1024 / 1024,
            )
            return True
    except subprocess.CalledProcessError as e:
        logger.warning("[CUT] stream-copy (fast) failed, trying slow copy: %s", e.stderr[:200] if e.stderr else "")
        if os.path.exists(out_path):
            os.remove(out_path)

    # --- Attempt 2: Slow stream-copy (-ss after -i, more accurate) ---
    slow_copy_cmd = [
        FFMPEG, "-y",
        "-i", input_path,
        "-ss", str(start_sec),
        "-t", str(duration),
        "-map", "0:v:0",
        "-map", "0:a?",
        "-c", "copy",
        "-movflags", "+faststart",
        out_path,
    ]

    try:
        subprocess.run(slow_copy_cmd, check=True, capture_output=True, text=True)
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            logger.info(
                "[CUT OK] stream-copy (slow): %s (%.2f MB)",
                out_path,
                os.path.getsize(out_path) / 1024 / 1024,
            )
            return True
    except subprocess.CalledProcessError as e:
        logger.warning("[CUT] stream-copy (slow) failed, falling back to re-encode: %s", e.stderr[:200] if e.stderr else "")
        if os.path.exists(out_path):
            os.remove(out_path)

    # --- Attempt 3: Re-encode fallback (slowest but most compatible) ---
    logger.info("[CUT] Falling back to re-encode for %.2f-%.2f", start_sec, end_sec)

    if safe_seek:
        reencode_cmd = [
            FFMPEG, "-y",
            "-i", input_path,
            "-ss", str(start_sec),
            "-t", str(duration),
        ]
    else:
        reencode_cmd = [
            FFMPEG, "-y",
            "-ss", str(start_sec),
            "-i", input_path,
            "-t", str(duration),
        ]

    reencode_cmd += [
        "-map", "0:v:0",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf),
        "-c:a", "aac",
        "-movflags", "+faststart",
        out_path,
    ]

    try:
        subprocess.run(reencode_cmd, check=True, capture_output=True, text=True)
        if os.path.exists(out_path):
            logger.info(
                "[CUT OK] re-encode: %s (%.2f MB)",
                out_path,
                os.path.getsize(out_path) / 1024 / 1024,
            )
        else:
            logger.error("[CUT FAIL] ffmpeg returned but file not found: %s", out_path)
        return True

    except subprocess.CalledProcessError as e:
        logger.error("ffmpeg re-encode failed (safe_seek=%s)", safe_seek)
        logger.error("stdout: %s", e.stdout)
        logger.error("stderr: %s", e.stderr)
        if os.path.exists(out_path):
            os.remove(out_path)
        return False


def _parse_account_from_conn_str(conn_str: str) -> dict:
    parts = conn_str.split(";")
    out = {"AccountName": None, "AccountKey": None}
    for p in parts:
        if p.startswith("AccountName="):
            out["AccountName"] = p.split("=", 1)[1]
        if p.startswith("AccountKey="):
            out["AccountKey"] = p.split("=", 1)[1]
    return out


def upload_to_blob(local_path: str, blob_name: str) -> bool:
    if not AZURE_STORAGE_CONNECTION_STRING:
        return False

    try:
        conn = _parse_account_from_conn_str(AZURE_STORAGE_CONNECTION_STRING)
        account_name = conn["AccountName"]
        account_key = conn["AccountKey"]

        from azure.storage.blob import generate_blob_sas, BlobSasPermissions
        from datetime import datetime, timedelta

        expiry = datetime.utcnow() + timedelta(minutes=SAS_EXP_MINUTES)
        sas = generate_blob_sas(
            account_name=account_name,
            container_name=AZURE_BLOB_CONTAINER,
            blob_name=blob_name,
            account_key=account_key,
            permission=BlobSasPermissions(read=True, write=True, create=True),
            expiry=expiry,
        )

        dest_url = (
            f"https://{account_name}.blob.core.windows.net/"
            f"{AZURE_BLOB_CONTAINER}/{blob_name}?{sas}"
        )

        # azcopy = shutil.which("azcopy") or "/usr/local/bin/azcopy"
        azcopy = shutil.which("azcopy") or r"D:\azcopy\azcopy.exe"

        subprocess.run(
            [azcopy, "copy", local_path, dest_url, "--overwrite=true"],
            check=True,
            capture_output=True,
            text=True,
        )

        return True

    except Exception as e:
        logger.error("Upload failed: %s", e)
        return False


# =====================
# LOAD PHASES
# =====================
# def load_phases_from_step1(video_id: str, video_path: str):
#     path = step1_cache_path(video_id)
#     if not os.path.exists(path):
#         raise RuntimeError("Missing STEP1 cache")

#     with open(path, "r", encoding="utf-8") as f:
#         data = json.load(f)

#     keyframes = data["keyframes"]
#     total_frames = data["total_frames"]
#     fps = probe_fps(video_path)

#     extended = [0] + keyframes + [total_frames]

#     phases = []
#     for i in range(len(extended) - 1):
#         phases.append({
#             "phase_index": i + 1,
#             "start_sec": extended[i] / fps,
#             "end_sec": (extended[i + 1] - 1) / fps,
#         })
#     return phases

def load_phases_from_step1(video_id: str) -> list[dict]:
    path = step1_cache_path(video_id)
    if not os.path.exists(path):
        raise RuntimeError("Missing STEP1 cache")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    keyframes = data["keyframes"]
    total_frames = data["total_frames"]

    # STEP 0 extract_frames dùng fps = 1
    fps = 1.0

    # keyframes là frame index bắt đầu phase mới
    # phase boundaries = [0] + keyframes + [total_frames]
    boundaries = [0] + keyframes + [total_frames]

    phases = []
    for i in range(len(boundaries) - 1):
        start_frame = boundaries[i]
        end_frame = boundaries[i + 1] - 1

        if end_frame <= start_frame:
            continue

        phases.append({
            "phase_index": i + 1,
            "time_start": start_frame / fps,
            "time_end": end_frame / fps,
        })

    return phases



def load_phases_from_db(video_id: str):
    user_id = get_user_id_of_video_sync(video_id)
    rows = load_video_phases_sync(video_id, user_id)
    phases = []
    for r in rows:
        phases.append({
            "phase_index": r["phase_index"],
            "start_sec": float(r["time_start"]),
            "end_sec": float(r["time_end"]),
        })
    return phases


# =====================
# MAIN
# =====================
def main():
    parser = argparse.ArgumentParser("split_video_async")
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--video-path", required=True)
    parser.add_argument("--blob-url")
    parser.add_argument(
        "--phase-source",
        choices=["step1", "db"],
        required=True,
        help="step1 = early split, db = resume split",
    )
    args = parser.parse_args()

    video_id = args.video_id
    video_path = args.video_path
    blob_url = args.blob_url or ""

    logger.info("args.blob_url = %r", args.blob_url)

    init_db_sync()

    try:
        split_status = get_video_split_status_sync(video_id)
        if split_status == "done":
            logger.info("Split already DONE → skip")
            return

        start_phase = 1
        if split_status and split_status not in ("new", ""):
            start_phase = int(split_status) + 1

        # ---- LOAD PHASES ----
        if args.phase_source == "step1":
            phases = load_phases_from_step1(video_id)
        else:
            phases = load_phases_from_db(video_id)


        # ===== DEBUG LOG PHASES (ADD HERE) =====
        logger.info("TOTAL PHASES = %d", len(phases))
        for p in phases:
            ts = float(p.get("time_start", 0))
            te = float(p.get("time_end", 0))
            logger.info(
                "PHASE %s | start=%.2f | end=%.2f | duration=%.2f",
                p.get("phase_index"),
                ts,
                te,
                te - ts,
            )
        # ===== END DEBUG LOG =====

        out_dir = os.path.join(SPLIT_VIDEO_DIR, video_id)
        os.makedirs(out_dir, exist_ok=True)

        blob_info = parse_blob_url(blob_url) if blob_url else None

        # ---- CUT LOOP ----
 
        # for p in phases:
        #     idx = p["phase_index"]
        #     if idx < start_phase:
        #         continue

        #     # out_name = f"{idx}.mp4"
        #     # out_path = os.path.join(out_dir, out_name)

        #     time_start = p["time_start"]
        #     time_end = p["time_end"]

        #     out_name = f"{time_start}_{time_end}.mp4"
        #     out_path = os.path.join(out_dir, out_name)


        #     logger.info("[CUT] phase=%s out=%s", idx, out_path)

        #     if not cut_segment(video_path, out_path, p["time_start"], p["time_end"]):
        #         logger.error("[CUT FAIL] phase=%s", idx)
        #         break

        total_phases = len(phases)
        split_start_time = time.time()
        phases_to_process = [p for p in phases if p["phase_index"] >= start_phase]
        logger.info("[SPLIT] Starting: %d phases to process (total=%d, start_phase=%d)",
                    len(phases_to_process), total_phases, start_phase)

        for i, p in enumerate(phases_to_process):
            idx = p["phase_index"]
            time_start = p["time_start"]
            time_end = p["time_end"]

            out_name = f"{time_start}_{time_end}.mp4"
            out_path = os.path.join(out_dir, out_name)

            phase_start = time.time()
            logger.info("[CUT] phase=%s/%s out=%s (%.1fs segment)",
                        idx, total_phases, out_path, time_end - time_start)

            is_last_phase = (idx == total_phases)

            if not cut_segment(
                video_path,
                out_path,
                time_start,
                time_end,
                safe_seek=is_last_phase, 
            ):
                logger.error("[CUT FAIL] phase=%s", idx)
                break

            if not os.path.exists(out_path):
                logger.error("[CUT NO FILE] %s", out_path)
                break
            else:
                cut_elapsed = time.time() - phase_start
                logger.info("[CUT OK] %s (%.2f MB, cut took %.1fs)",
                            out_path, os.path.getsize(out_path) / 1024 / 1024, cut_elapsed)

            if blob_info:
                upload_start = time.time()
                logger.info("[UPLOAD] start upload %s", out_path)

                dest = f"{blob_info['parent_path']}/reportvideo/{out_name}"
                if not upload_to_blob(out_path, dest):
                    logger.error("[UPLOAD FAIL] %s", out_path)
                    break

                upload_elapsed = time.time() - upload_start
                logger.info("[UPLOAD OK] %s (%.1fs)", out_path, upload_elapsed)

            # Clean up local file after upload to save disk space
            if os.path.exists(out_path):
                try:
                    os.remove(out_path)
                except Exception:
                    pass

            update_video_split_status_sync(video_id, str(idx))
            total_elapsed = time.time() - split_start_time
            logger.info("[PROGRESS] phase %d/%d done (total elapsed: %.1fs)",
                        i + 1, len(phases_to_process), total_elapsed)

        else:
            total_elapsed = time.time() - split_start_time
            update_video_split_status_sync(video_id, "done")
            logger.info("[SPLIT DONE] %d phases in %.1fs (avg %.1fs/phase)",
                        len(phases_to_process), total_elapsed,
                        total_elapsed / max(len(phases_to_process), 1))

    finally:
        close_db_sync()
        shutil.rmtree(os.path.join(SPLIT_VIDEO_DIR, video_id), ignore_errors=True)


if __name__ == "__main__":
    main()
