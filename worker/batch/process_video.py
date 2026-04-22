import os, time, sys
import argparse
import json
import resource
import shutil
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv
from ultralytics import YOLO
import subprocess
import requests

from vision_pipeline import caption_keyframes
from db_ops import init_db_sync, close_db_sync


LOG_DIR = "logs"
# DOWNLOAD_LOG = os.path.join(LOG_DIR, "download.log")

os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "process_video.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),  # vẫn ra console
    ],
)
logger = logging.getLogger("process_video")

# Load environment variables
load_dotenv()

from video_frames import extract_frames, detect_phases
from disk_guard import cleanup_video_files, cleanup_old_files, ensure_disk_space, get_disk_info
from phase_pipeline import (
    extract_phase_stats,
    build_phase_units,
    build_phase_descriptions,
)
from audio_pipeline import extract_audio_chunks, extract_audio_full, transcribe_audio_chunks
from audio_features_pipeline import analyze_phase_audio_features
from grouping_pipeline import (
    embed_phase_descriptions,
    assign_phases_to_groups,
)
from best_phase_pipeline import (
    load_group_best_phases,
    update_group_best_phases,
    save_group_best_phases,
)
from report_pipeline import (
    build_report_1_timeline,
    build_report_2_phase_insights_raw,
    rewrite_report_2_with_gpt,
    save_reports,
)

from db_ops import (
    update_phase_group_for_video_phase_sync,
    upsert_phase_insight_sync,
    insert_video_insight_sync,
    update_video_status_sync,
    update_video_step_progress_sync,
    get_video_status_sync,
    load_video_phases_sync,
    update_video_phase_description_sync,
    update_video_phase_audio_text_sync,
    update_video_phase_csv_metrics_sync,
    update_video_phase_cta_score_sync,
    update_video_phase_audio_features_sync,
    update_video_phase_sales_tags_sync,
    update_phase_group_sync,
    get_video_structure_group_id_of_video_sync,
    bulk_upsert_group_best_phases_sync,
    bulk_refresh_phase_insights_sync,
    get_video_split_status_sync,
    get_user_id_of_video_sync,
    get_video_excel_urls_sync,
    ensure_product_exposures_table_sync,
    bulk_insert_product_exposures_sync,
    ensure_sales_moments_table_sync,
    bulk_insert_sales_moments_sync,
    insert_video_error_log_sync,
)

from video_structure_features import build_video_structure_features
from video_structure_grouping import assign_video_structure_group
from video_structure_group_stats import recompute_video_structure_group_stats
from best_video_pipeline import process_best_video

from excel_parser import load_excel_data, match_sales_to_phase, build_phase_stats_from_csv
from csv_slot_filter import get_important_time_ranges, filter_phases_by_importance, detect_sales_moments
from screen_moment_extractor import detect_screen_moments
from video_status import VideoStatus
from video_compressor import compress_and_replace  # v6: generate_analysis_video removed (direct frame extraction)
from product_detection_pipeline import detect_product_timeline


# =========================
# Artifact layout (PERSISTENT)
# =========================

ART_ROOT = "output"

def video_root(video_id: str):
    return os.path.join(ART_ROOT, video_id)

def frames_dir(video_id: str):
    return os.path.join(video_root(video_id), "frames")

def cache_dir(video_id: str):
    return os.path.join(video_root(video_id), "cache")

def step1_cache_path(video_id: str):
    return os.path.join(cache_dir(video_id), "step1_phases.json")

def audio_dir(video_id: str):
    return os.path.join(video_root(video_id), "audio")

def audio_text_dir(video_id: str):
    return os.path.join(video_root(video_id), "audio_text")

# =========================
# STEP 1 cache helpers
# =========================

def save_step1_cache(video_id, keyframes, rep_frames, total_frames):
    os.makedirs(cache_dir(video_id), exist_ok=True)
    path = step1_cache_path(video_id)
    data = {
        "keyframes": keyframes,
        "rep_frames": rep_frames,
        "total_frames": total_frames,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)

def load_step1_cache(video_id):
    path = step1_cache_path(video_id)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

# =========================
# Pipeline error helper
# =========================

class PipelineStepError(Exception):
    """Wraps an exception with step context for error logging."""
    def __init__(self, step_name: str, error_code: str, original: Exception):
        self._error_step = step_name
        self._error_code = error_code
        self.original = original
        super().__init__(f"[{step_name}] {error_code}: {original}")

def _record_step_error(video_id, step_name, error_code, exc, update_last_error=False):
    """Record a per-step error to video_error_logs without stopping the pipeline.

    Args:
        update_last_error: If True, also update videos.last_error_code.
            Defaults to False because non-fatal step errors (e.g. PRODUCT_DATA_MISMATCH)
            should NOT overwrite last_error_code — doing so masks the real fatal error
            that later sets status=ERROR.
    """
    import traceback as _tb
    try:
        insert_video_error_log_sync(
            video_id=video_id,
            error_code=error_code,
            error_step=step_name,
            error_message=str(exc)[:2000],
            error_detail=_tb.format_exc()[:10000],
            source="worker",
            update_last_error=update_last_error,
        )
    except Exception as log_err:
        logger.warning("[ERROR_LOG] Failed to record step error: %s", log_err)

# =========================
# Resume helpers
# =========================

STEP_ORDER = [
    VideoStatus.STEP_0_EXTRACT_FRAMES,
    VideoStatus.STEP_1_DETECT_PHASES,
    VideoStatus.STEP_2_EXTRACT_METRICS,
    VideoStatus.STEP_3_TRANSCRIBE_AUDIO,
    VideoStatus.STEP_4_IMAGE_CAPTION,
    VideoStatus.STEP_5_BUILD_PHASE_UNITS,
    VideoStatus.STEP_6_BUILD_PHASE_DESCRIPTION,
    VideoStatus.STEP_7_GROUPING,
    VideoStatus.STEP_8_UPDATE_BEST_PHASE,

    VideoStatus.STEP_9_BUILD_VIDEO_STRUCTURE_FEATURES,
    VideoStatus.STEP_10_ASSIGN_VIDEO_STRUCTURE_GROUP,
    VideoStatus.STEP_11_UPDATE_VIDEO_STRUCTURE_GROUP_STATS,
    VideoStatus.STEP_12_UPDATE_VIDEO_STRUCTURE_BEST,

    VideoStatus.STEP_12_5_PRODUCT_DETECTION,

    VideoStatus.STEP_13_BUILD_REPORTS,
    VideoStatus.STEP_14_FINALIZE
]

def status_to_step_index(status: str | None):
    if not status:
        return 0
    if status == VideoStatus.DONE:
        return len(STEP_ORDER)
    # Handle legacy STEP_COMPRESS_1080P status → restart from 0
    if status == VideoStatus.STEP_COMPRESS_1080P:
        return 0
    if status in STEP_ORDER:
        return STEP_ORDER.index(status)
    return 0

# =========================
# Utils
# =========================

def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def _regenerate_sas_url(blob_url: str) -> str:
    """Regenerate a fresh SAS URL from an expired blob URL.
    Uses AZURE_STORAGE_CONNECTION_STRING to generate a new read SAS token.
    Returns the new URL, or raises if regeneration is not possible."""
    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not conn_str:
        raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING not set, cannot regenerate SAS")

    from urllib.parse import urlparse, unquote
    from azure.storage.blob import generate_blob_sas, BlobSasPermissions
    from datetime import datetime, timedelta

    # Parse blob URL to extract container and blob path
    base_url = blob_url.split("?")[0] if "?" in blob_url else blob_url
    parsed = urlparse(base_url)
    path_parts = parsed.path.lstrip("/").split("/", 1)
    container = path_parts[0] if path_parts else "videos"
    blob_path = unquote(path_parts[1]) if len(path_parts) > 1 else ""

    if not blob_path:
        raise RuntimeError(f"Cannot parse blob_path from URL: {blob_url}")

    # Parse account info from connection string
    account_name = None
    account_key = None
    for part in conn_str.split(";"):
        if part.startswith("AccountName="):
            account_name = part.split("=", 1)[1]
        if part.startswith("AccountKey="):
            account_key = part.split("=", 1)[1]

    if not account_name or not account_key:
        raise RuntimeError("Cannot parse AccountName/AccountKey from connection string")

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
    logger.info("[SAS] Regenerated fresh SAS URL (expires in 24h)")
    return new_url


def _download_blob(blob_url: str, dest_path: str):
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    logger.info(f"START download")
    logger.info(f"URL = {blob_url}")
    logger.info(f"DEST = {dest_path}")

    # --- v5: Pre-download disk space check ---
    # Get file size via HEAD request and ensure enough disk space
    try:
        head_resp = requests.head(blob_url, timeout=30, allow_redirects=True)
        content_length = int(head_resp.headers.get("content-length", 0))
        if content_length > 0:
            needed_gb = (content_length / (1024 ** 3)) * 1.5  # 1.5x for safety margin
            needed_gb = max(needed_gb, 2.0)  # minimum 2GB
            logger.info("[DL] File size: %.2f GB, need %.1f GB free",
                        content_length / (1024 ** 3), needed_gb)
            try:
                from disk_guard import ensure_disk_space
                ensure_disk_space(min_free_gb=needed_gb)
            except RuntimeError as disk_err:
                logger.error("[DL] Disk space insufficient for download: %s", disk_err)
                raise PipelineStepError("DOWNLOAD", "DOWNLOAD_DISK_FULL", disk_err) from disk_err
        else:
            logger.info("[DL] Could not determine file size from HEAD, proceeding anyway")
    except PipelineStepError:
        raise
    except Exception as head_err:
        logger.warning("[DL] HEAD request failed (non-fatal): %s", head_err)

    # Try download with original URL first, then regenerate SAS if 403
    urls_to_try = [blob_url]

    for attempt, url in enumerate(urls_to_try):
        try:
            logger.info("Try AzCopy... (attempt %d)", attempt + 1)

            result = subprocess.run(
                ["/usr/local/bin/azcopy", "copy", url, dest_path, "--overwrite=true"],
                check=True,
                capture_output=True,
                text=True
            )

            logger.info("AzCopy SUCCESS")
            logger.info("AzCopy STDOUT:")
            logger.info(result.stdout or "<empty>")
            logger.info("AzCopy STDERR:")
            logger.info(result.stderr or "<empty>")

            return

        except FileNotFoundError as e:
            logger.info("AzCopy NOT FOUND")
            logger.info(f"Exception: {repr(e)}")
            break  # No point retrying if azcopy is not installed

        except subprocess.CalledProcessError as e:
            logger.warning("AzCopy FAILED (attempt %d)", attempt + 1)
            logger.info("AzCopy STDOUT:")
            logger.info(e.stdout or "<empty>")
            logger.info("AzCopy STDERR:")
            logger.info(e.stderr or "<empty>")
            logger.info(f"Return code: {e.returncode}")

            # Check if it's a 403/auth error → try regenerating SAS
            combined_output = (e.stdout or "") + (e.stderr or "")
            if "403" in combined_output or "AuthenticationFailed" in combined_output or "expired" in combined_output.lower():
                if attempt == 0:
                    try:
                        logger.info("[SAS] Detected expired/invalid SAS, regenerating...")
                        new_url = _regenerate_sas_url(blob_url)
                        urls_to_try.append(new_url)
                        continue
                    except Exception as regen_err:
                        logger.error("[SAS] Failed to regenerate SAS URL: %s", regen_err)

        except Exception as e:
            logger.info("AzCopy UNKNOWN ERROR")
            logger.info(f"Exception: {repr(e)}")

    # ---- fallback: requests.get ----
    # Try with the last URL in the list (which may be a regenerated SAS URL)
    final_url = urls_to_try[-1]
    logger.info("Fallback to requests.get")

    try:
        with requests.get(final_url, stream=True, timeout=60) as r:
            r.raise_for_status()

            total = int(r.headers.get("content-length", 0))
            downloaded = 0

            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8 * 1024 * 1024):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)

            logger.info(f"Requests SUCCESS: downloaded {downloaded} bytes (total={total})")

    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 403 and final_url == blob_url:
            # Original URL failed with 403, try regenerated SAS
            try:
                logger.info("[SAS] requests.get got 403, regenerating SAS...")
                new_url = _regenerate_sas_url(blob_url)
                with requests.get(new_url, stream=True, timeout=60) as r2:
                    r2.raise_for_status()
                    total = int(r2.headers.get("content-length", 0))
                    downloaded = 0
                    with open(dest_path, "wb") as f:
                        for chunk in r2.iter_content(chunk_size=8 * 1024 * 1024):
                            if chunk:
                                f.write(chunk)
                                downloaded += len(chunk)
                    logger.info(f"Requests SUCCESS (regenerated SAS): downloaded {downloaded} bytes (total={total})")
                    logger.info("END download")
                    return
            except Exception as regen_err:
                logger.error("[SAS] Regenerated SAS also failed: %s", regen_err)
        logger.info("Requests FAILED")
        logger.info(f"Exception: {repr(e)}")
        raise PipelineStepError("DOWNLOAD", "DOWNLOAD_FAIL", e) from e

    except Exception as e:
        logger.info("Requests FAILED")
        logger.info(f"Exception: {repr(e)}")
        raise PipelineStepError("DOWNLOAD", "DOWNLOAD_FAIL", e) from e

    logger.info("END download")



def _resolve_inputs(args) -> tuple[str, str]:
    video_id = args.video_id
    video_path = args.video_path
    blob_url = args.blob_url

    if video_path:
        if not video_id:
            video_id = os.path.splitext(os.path.basename(video_path))[0]
        return video_path, video_id

    if not video_id:
        raise RuntimeError("Must provide --video-id (Azure Batch always has this).")

    local_dir = "uploadedvideo"
    _ensure_dir(local_dir)
    local_path = os.path.join(local_dir, f"{video_id}.mp4")

    # Check if local file exists AND is non-empty (0-byte files are invalid)
    if os.path.exists(local_path):
        file_size = os.path.getsize(local_path)
        if file_size > 0:
            logger.info(f"[DL] Local file exists: {local_path} ({file_size} bytes)")
            return local_path, video_id
        else:
            logger.warning(f"[DL] Local file is 0 bytes, will re-download: {local_path}")
            os.remove(local_path)

    if blob_url:
        logger.info(f"[DL] Downloading video from blob: {blob_url}")
        _download_blob(blob_url, local_path)
        # Verify downloaded file is not empty
        if os.path.exists(local_path):
            file_size = os.path.getsize(local_path)
            if file_size == 0:
                logger.error(f"[DL] Downloaded file is 0 bytes! Blob may be empty: {local_path}")
                raise PipelineStepError("DOWNLOAD", "DOWNLOAD_EMPTY_FILE",
                    RuntimeError(
                        f"Downloaded video file is 0 bytes. "
                        f"The video may not have been uploaded correctly to Blob Storage. "
                        f"video_id={video_id}"
                    )
                )
            logger.info(f"[DL] Download complete: {local_path} ({file_size} bytes, {file_size/(1024**3):.2f} GB)")
        return local_path, video_id

    raise PipelineStepError("DOWNLOAD", "NO_VIDEO_SOURCE",
        FileNotFoundError("No local video and no blob_url provided.")
    )


def fire_split_async(args, video_id, video_path, phase_source):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    split_script = os.path.join(script_dir, "split_video_async.py")

    logger.info("[ASYNC] Fire split_video")
    logger.info("[ASYNC] python = %s", sys.executable)
    logger.info("[ASYNC] script = %s", split_script)
    logger.info("[ASYNC] video_id = %s | source = %s", video_id, phase_source)

    url = args.blob_url if getattr(args, "blob_url", None) else video_path

    subprocess.Popen(
        [
            sys.executable,
            split_script,
            "--video-id", video_id,
            "--video-path", video_path,
            "--phase-source", phase_source,
            "--blob-url", url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


# =========================
# Background compression helper
# =========================

def fire_compress_async(video_path, blob_url, video_id):
    """
    Fire compression as a background subprocess.
    Compression runs independently and does NOT block the analysis pipeline.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    compress_script = os.path.join(script_dir, "compress_background.py")

    logger.info("[ASYNC] Fire background compression")
    logger.info("[ASYNC] video_path = %s", video_path)

    subprocess.Popen(
        [
            sys.executable,
            compress_script,
            "--video-path", video_path,
            "--video-id", video_id,
            "--blob-url", blob_url or "",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


# =========================
# CLEANUP HELPER (delegated to disk_guard.py)
# =========================


# =========================
# MAIN
# =========================

def main():
    # ── Memory optimization: limit MKL/OMP threads to reduce peak memory ──
    # Intel MKL (used by NumPy/Whisper) allocates per-thread buffers.
    # Limiting threads from default (all cores) to 4 saves ~2GB on long videos.
    for _env_key in ("MKL_NUM_THREADS", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        if _env_key not in os.environ:
            os.environ[_env_key] = "4"
    # ── v9: RLIMIT_AS DISABLED ──
    # Previously set to 8GB, then 14GB, but this causes:
    # 1. mkl_malloc failures (Intel MKL can't allocate per-thread buffers)
    # 2. CUDNN_STATUS_SUBLIBRARY_LOADING_FAILED (cuDNN can't mmap .so files)
    # The root issue is that RLIMIT_AS limits *virtual* address space, not
    # physical RAM. GPU libraries (cuDNN, CUDA) map large .so files into
    # virtual memory via dlopen/mmap, which counts against RLIMIT_AS even
    # though they don't consume physical RAM.
    #
    # Protection is already provided by:
    # - systemd MemoryMax=14GB (kills process if physical RAM exceeds limit)
    # - systemd MemoryHigh=12GB (triggers kernel memory reclaim)
    # These are sufficient OOM guards without breaking GPU library loading.
    _mem_limit_gb = int(os.getenv("FFMPEG_MEM_LIMIT_GB", "0"))  # 0 = disabled
    if _mem_limit_gb > 0:
        _mem_limit_bytes = _mem_limit_gb * 1024 * 1024 * 1024
        try:
            resource.setrlimit(resource.RLIMIT_AS, (_mem_limit_bytes, _mem_limit_bytes))
            logging.getLogger("process_video").info(f"Memory limit set: {_mem_limit_gb}GB per process")
        except (ValueError, OSError):
            pass
    else:
        logging.getLogger("process_video").info("RLIMIT_AS disabled (systemd MemoryMax provides OOM protection)")

    parser = argparse.ArgumentParser(description="Process a livestream video")
    parser.add_argument("--video-id", dest="video_id", type=str, required=True)
    parser.add_argument("--video-path", dest="video_path", type=str)
    parser.add_argument("--blob-url", dest="blob_url", type=str)
    args = parser.parse_args()

    # Pre-initialize video_id from args so except/finally can always reference it
    video_id = args.video_id
    # Track current step for better error reporting on resume
    _current_step_name = "PRE_FLIGHT"

    logger.info("[DB] Initializing database connection...")
    init_db_sync()

    try:
        # --- PRE-FLIGHT: Check DB record exists BEFORE downloading ---
        # This prevents wasting bandwidth on orphan videos (deleted after queue enqueue)
        try:
            pre_user_id = get_user_id_of_video_sync(video_id)
        except Exception as db_err:
            # DB connection error → retry-able (exit code 1)
            logger.error(
                "[DB_ERROR] Cannot connect to DB to check video_id=%s: %s", video_id, db_err,
            )
            raise PipelineStepError("PRE_FLIGHT", "DB_CONNECTION_FAIL", db_err) from db_err

        if pre_user_id is None:
            # Not found on first check — sleep and recheck once to guard against
            # DB replication lag or a race with upload_complete commit.
            logger.info(
                "[ORPHAN_RECHECK] video_id=%s not found on first check. "
                "Sleeping 3s and rechecking once...", video_id,
            )
            time.sleep(3)
            try:
                pre_user_id = get_user_id_of_video_sync(video_id)
            except Exception as db_err2:
                logger.error(
                    "[DB_ERROR] Recheck failed for video_id=%s: %s", video_id, db_err2,
                )
                raise PipelineStepError("PRE_FLIGHT", "DB_RECHECK_FAIL", db_err2) from db_err2

            if pre_user_id is None:
                # Still not found after recheck → confirmed orphan (exit code 2)
                logger.warning(
                    "[ORPHAN_VIDEO] video_id=%s not found in DB after recheck. "
                    "Skipping download and processing. Message should be deleted.",
                    video_id,
                )
                sys.exit(2)  # Special exit code: orphan video, no retry
            else:
                logger.info(
                    "[ORPHAN_RECHECK] video_id=%s found on recheck (user_id=%s). "
                    "Proceeding with processing.", video_id, pre_user_id,
                )

        # ── Early status update: mark that worker has started processing ──
        # This prevents the UI from showing "uploaded" (= 圧縮中) forever
        # if the worker crashes during download or pre-flight.
        # SKIP if already in a STEP_* status (resume scenario) to avoid
        # resetting the resume point.
        try:
            _pre_status = get_video_status_sync(video_id)
            if _pre_status and _pre_status.startswith("STEP_") and _pre_status != VideoStatus.STEP_COMPRESS_1080P:
                logger.info("[STATUS] Skipping early status update (resume from %s)", _pre_status)
            else:
                update_video_status_sync(video_id, VideoStatus.STEP_COMPRESS_1080P)
                logger.info("[STATUS] Early status update → STEP_COMPRESS_1080P (worker started)")
        except Exception as e:
            logger.warning("[STATUS] Failed early status update: %s", e)

        # --- PRE-FLIGHT: Clean old files and check disk space BEFORE download ---
        logger.info("=== PRE-FLIGHT DISK CLEANUP ===")
        ensure_disk_space(min_free_gb=3.0, current_video_id=args.video_id)

        video_path, video_id = _resolve_inputs(args)

        current_status = get_video_status_sync(video_id)
        raw_start_step = status_to_step_index(current_status)

        user_id = pre_user_id  # Already resolved above

        # =========================
        # LOAD EXCEL DATA (if clean video)
        # =========================
        excel_data = None
        time_offset_seconds = 0
        is_screen_recording = True  # default: screen recording
        try:
            excel_urls = get_video_excel_urls_sync(video_id)
            if excel_urls and excel_urls.get("upload_type") == "clean_video":
                is_screen_recording = False
                logger.info("[EXCEL] Clean video detected, loading Excel data...")
                time_offset_seconds = excel_urls.get("time_offset_seconds", 0)
                logger.info("[EXCEL] Time offset for this video: %.1f seconds", time_offset_seconds)
                excel_data = load_excel_data(video_id, excel_urls)
                if excel_data:
                    logger.info(
                        "[EXCEL] Loaded: %d products, %d trend entries",
                        len(excel_data.get("products", [])),
                        len(excel_data.get("trends", [])),
                    )
                else:
                    logger.warning("[EXCEL] load_excel_data returned None/empty for clean_video")
            else:
                logger.info("[EXCEL] Screen recording mode, no Excel data")
        except Exception as e:
            logger.warning("[EXCEL] Failed to load Excel data: %s", e)
            excel_data = None
            # Still set is_screen_recording correctly based on upload_type
            try:
                _ut = get_video_excel_urls_sync(video_id)
                if _ut and _ut.get("upload_type") == "clean_video":
                    is_screen_recording = False
                    time_offset_seconds = _ut.get("time_offset_seconds", 0)
            except Exception:
                pass

        # Resume from the last completed step instead of restarting from 0.
        # Previously only allowed resume from step >= 7, now allows any step.
        if raw_start_step > 0:
            start_step = raw_start_step

            keyframes = None
            rep_frames = None
            total_frames = None
            phase_stats = None
            keyframe_captions = None

            logger.info(f"[RESUME] resume from step {start_step} (status={current_status})")

            # Ensure artifact directory exists for resumed jobs
            my_art_dir = video_root(video_id)
            os.makedirs(my_art_dir, exist_ok=True)

            if start_step >= 7:
                fire_split_async(args, video_id, video_path, "db")

        else:
            start_step = 0
            logger.info(f"[RESUME] starting from STEP 0 (status={current_status})")

            # Preserve artifact folder on retry to allow resume from cached data.
            # Each step overwrites its outputs, so stale files are harmless.
            my_art_dir = video_root(video_id)
            if os.path.exists(my_art_dir):
                logger.info("[RESUME] Keeping existing artifact folder for %s (overwrite mode)", video_id)
            os.makedirs(my_art_dir, exist_ok=True)

        # =========================
        # v6: SKIP analysis video generation entirely.
        # GPU NVDEC extracts frames directly from RAW video at near-realtime speed.
        # This eliminates the biggest bottleneck (analysis.mp4 generation took 30-60min).
        # Background compression is DEFERRED to after frame extraction to avoid GPU contention.
        # =========================
        blob_url_for_compress = args.blob_url if getattr(args, "blob_url", None) else None

        if start_step <= 0:
            _current_step_name = VideoStatus.STEP_0_EXTRACT_FRAMES
            update_video_status_sync(video_id, VideoStatus.STEP_0_EXTRACT_FRAMES)
            logger.info("=== v6: DIRECT FRAME EXTRACTION (no analysis video) ===")

        # =========================
        # STEP 0 + STEP 3 – PARALLEL: EXTRACT FRAMES & AUDIO TRANSCRIPTION
        # =========================
        frame_dir = frames_dir(video_id)
        ad = audio_dir(video_id)
        atd = audio_text_dir(video_id)

        # v6: Always use RAW video directly (GPU NVDEC handles it efficiently)
        _frames_source = video_path

        if start_step <= 0:
            # Status already updated to STEP_0 before analysis video generation
            logger.info("[FRAMES] Source: %s (direct RAW, v6)", _frames_source)

            # ── v8: Adaptive parallel/sequential based on video duration ──
            # Long videos (>30min) run SEQUENTIALLY to halve peak memory.
            # Short videos (<30min) run in PARALLEL for speed.
            # Root cause: Whisper large-v3 (~3-4GB) + ffmpeg buffers exceeded
            # the 8GB RLIMIT_AS on 1h+ videos, causing mkl_malloc failures.
            import gc
            from video_frames import _get_video_duration
            _vid_duration = _get_video_duration(_frames_source)
            _SEQUENTIAL_THRESHOLD_SEC = int(os.getenv("SEQUENTIAL_THRESHOLD_SEC", "1800"))  # 30 min
            _use_sequential = _vid_duration > _SEQUENTIAL_THRESHOLD_SEC
            logger.info(
                "=== STEP 0+3 %s – EXTRACT FRAMES & AUDIO TRANSCRIPTION ==="
                " (duration=%.0fs, threshold=%ds)",
                "SEQUENTIAL" if _use_sequential else "PARALLEL",
                _vid_duration, _SEQUENTIAL_THRESHOLD_SEC,
            )

            # Combined progress: frames=50%, audio=50%
            _parallel_progress = {"frames": 0, "audio": 0}

            def _update_combined_progress():
                combined = int(_parallel_progress["frames"] * 0.5 + _parallel_progress["audio"] * 0.5)
                try:
                    update_video_step_progress_sync(video_id, combined)
                except Exception as _e:
                    logger.debug(f"Suppressed: {_e}")

            def _on_frames_progress(pct):
                _parallel_progress["frames"] = pct
                _update_combined_progress()

            def _on_audio_progress(pct):
                _parallel_progress["audio"] = pct
                _update_combined_progress()

            def _do_extract_frames():
                logger.info("[STEP0] Starting frame extraction (fps=1) from %s", _frames_source)
                extract_frames(
                    video_path=_frames_source,
                    fps=1,
                    frames_root=video_root(video_id),
                    on_progress=_on_frames_progress,
                )
                logger.info("[STEP0] Frame extraction DONE")

            def _do_audio_transcription():
                logger.info("[STEP3] Starting audio extraction + transcription")
                # Audio progress split: extraction=20%, transcription=80%
                def _on_transcription_progress(pct):
                    # Map transcription 0-100 to overall audio 20-100
                    _on_audio_progress(20 + int(pct * 0.8))

                # v7: Extract full audio first (for BatchedInferencePipeline)
                _on_audio_progress(0)
                full_path = extract_audio_full(video_path, ad)
                _on_audio_progress(10)

                if full_path:
                    logger.info("[STEP3] Full audio extracted, skipping chunk extraction")
                    _on_audio_progress(20)
                else:
                    logger.info("[STEP3] Full audio failed, extracting chunks")
                    extract_audio_chunks(video_path, ad)
                    _on_audio_progress(20)

                transcribe_audio_chunks(ad, atd, on_progress=_on_transcription_progress)
                logger.info("[STEP3] Audio transcription DONE")

            if _use_sequential:
                # ── SEQUENTIAL MODE (long videos >30min) ──
                # Run frame extraction first, then free memory, then Whisper.
                # This halves peak memory: ffmpeg never runs alongside Whisper.
                logger.info("[SEQUENTIAL] Phase 1/2: Frame extraction")
                try:
                    _do_extract_frames()
                except Exception as e:
                    logger.error("[SEQUENTIAL] Frame extraction failed: %s", e)
                    raise PipelineStepError("STEP_0_EXTRACT_FRAMES", "FRAME_EXTRACT_FAIL", e) from e

                # Force garbage collection + GPU VRAM release between heavy steps.
                # ffmpeg NVDEC uses GPU VRAM for hardware decoding; releasing it
                # prevents CUDA OOM when Whisper loads its model (~3-4GB VRAM).
                gc.collect()
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                        torch.cuda.synchronize()
                        _free, _total = torch.cuda.mem_get_info()
                        logger.info("[SEQUENTIAL] GPU VRAM after cleanup: %.1fGB free / %.1fGB total",
                                    _free / 1e9, _total / 1e9)
                except Exception as _gpu_e:
                    logger.debug("[SEQUENTIAL] GPU cleanup skipped: %s", _gpu_e)
                logger.info("[SEQUENTIAL] Phase 2/2: Audio transcription (gc.collect + cuda.empty_cache done)")

                try:
                    _do_audio_transcription()
                except Exception as e:
                    logger.error("[SEQUENTIAL] Audio transcription failed: %s", e)
                    raise PipelineStepError("STEP_0_EXTRACT_FRAMES", "FRAME_EXTRACT_FAIL", e) from e
            else:
                # ── PARALLEL MODE (short videos <30min) ──
                with ThreadPoolExecutor(max_workers=2) as pool:
                    fut_frames = pool.submit(_do_extract_frames)
                    fut_audio = pool.submit(_do_audio_transcription)

                    for fut in as_completed([fut_frames, fut_audio]):
                        try:
                            fut.result()
                        except Exception as e:
                            logger.error("[PARALLEL] Task failed: %s", e)
                            raise PipelineStepError("STEP_0_EXTRACT_FRAMES", "FRAME_EXTRACT_FAIL", e) from e

            update_video_step_progress_sync(video_id, 100)
            logger.info("=== STEP 0+3 COMPLETE (%s mode) ===", "SEQUENTIAL" if _use_sequential else "PARALLEL")

            # Force GC before next heavy step (YOLO)
            gc.collect()

            # v6: Fire background compression AFTER frame extraction completes
            # This avoids GPU contention during the critical extraction phase
            if blob_url_for_compress:
                logger.info("=== FIRE BACKGROUND COMPRESSION (deferred, non-blocking) ===")
                fire_compress_async(video_path, blob_url_for_compress, video_id)

        elif start_step <= 1:
            # Only frames needed (audio already done in previous run)
            _current_step_name = VideoStatus.STEP_0_EXTRACT_FRAMES
            update_video_status_sync(video_id, VideoStatus.STEP_0_EXTRACT_FRAMES)
            logger.info("=== STEP 0 RESUME – EXTRACT FRAMES ONLY ===")
            def _on_frames_only_progress(pct):
                try:
                    update_video_step_progress_sync(video_id, pct)
                except Exception as _e:
                    logger.debug(f"Suppressed: {_e}")
            # v6: Extract frames directly from RAW video (no analysis video needed)
            extract_frames(
                video_path=video_path,
                fps=1,
                frames_root=video_root(video_id),
                on_progress=_on_frames_only_progress,
            )
        else:
            logger.info("[SKIP] STEP 0")

        # =========================
        # STEP 1 – PHASE DETECTION (YOLO)
        # =========================
        # Disk check before heavy GPU/CPU step
        ensure_disk_space(min_free_gb=1.5, current_video_id=video_id)
        if start_step <= 1:
            _current_step_name = VideoStatus.STEP_1_DETECT_PHASES
            update_video_status_sync(video_id, VideoStatus.STEP_1_DETECT_PHASES)

            logger.info("=== STEP 1 – PHASE DETECTION (YOLO) ===")
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
            logger.info(f"[YOLO] Using device: {device}")
            model = YOLO("yolov8n.pt", verbose=False)
            model.to(device)
            def _on_step1_progress(pct):
                try:
                    update_video_step_progress_sync(video_id, pct)
                except Exception as _e:
                    logger.debug(f"Suppressed: {_e}")
            keyframes, rep_frames, total_frames = detect_phases(
                frame_dir=frame_dir,
                model=model,
                on_progress=_on_step1_progress,
            )

            save_step1_cache(
                video_id=video_id,
                keyframes=keyframes,
                rep_frames=rep_frames,
                total_frames=total_frames,
            )

            fire_split_async(args, video_id, video_path, "step1")

        else:
            logger.info("[SKIP] STEP 1")
            keyframes = None
            rep_frames = None
            total_frames = None

        # =========================
        # CSV SLOT FILTER – 注目タイムスロットの検出
        # =========================
        important_ranges = []
        phase_importance = None
        if excel_data and excel_data.get("has_trend_data") and keyframes is not None:
            logger.info("=== CSV SLOT FILTER – Detecting important time ranges ===")
            try:
                important_ranges = get_important_time_ranges(
                    trends=excel_data["trends"],
                    video_duration_sec=float(total_frames),  # fps=1
                    margin_sec=600,  # 前後10分
                    min_score=1,
                )
                if important_ranges:
                    phase_importance = filter_phases_by_importance(
                        keyframes=keyframes,
                        total_frames=total_frames,
                        important_ranges=important_ranges,
                    )
                    important_count = sum(phase_importance) if phase_importance else 0
                    total_count = len(phase_importance) if phase_importance else 0
                    logger.info(
                        "[CSV_FILTER] Will analyze %d/%d phases (skipping %d)",
                        important_count, total_count, total_count - important_count,
                    )
                else:
                    logger.info("[CSV_FILTER] No important ranges found, analyzing all phases")
            except Exception as e:
                logger.warning("[CSV_FILTER] Failed to compute important ranges: %s", e)
                important_ranges = []
                phase_importance = None

        # =========================
        # STEP 2 – PHASE METRICS
        # =========================
        # Disk check before metrics extraction
        ensure_disk_space(min_free_gb=1.0, current_video_id=video_id)
        if start_step <= 2:
            _current_step_name = VideoStatus.STEP_2_EXTRACT_METRICS
            update_video_status_sync(video_id, VideoStatus.STEP_2_EXTRACT_METRICS)
            logger.info("=== STEP 2 – PHASE METRICS ===")

            # クリーン動画 + CSVトレンドデータあり → GPT Vision不要、CSVで代替
            if excel_data and excel_data.get("has_trend_data"):
                logger.info("[STEP2] Clean video with CSV data → skipping GPT Vision entirely")
                logger.info("[STEP2] Using CSV trend data for viewer_count / like_count")
                phase_stats = build_phase_stats_from_csv(
                    trends=excel_data["trends"],
                    keyframes=keyframes,
                    total_frames=total_frames,
                    video_start_time_sec=time_offset_seconds if time_offset_seconds else None,
                )
                logger.info("[STEP2] CSV-based stats built for %d phases (0 API calls)", len(phase_stats))
            else:
                # 画面収録 or CSVなし → 従来のGPT Vision読み取り
                logger.info("[STEP2] Screen recording mode → using GPT Vision")
                phase_stats = extract_phase_stats(
                    keyframes=keyframes,
                    total_frames=total_frames,
                    frame_dir=frame_dir,

                )
        else:
            logger.info("[SKIP] STEP 2")
            phase_stats = None

        # =========================
        # STEP 3 – AUDIO → TEXT (already done in parallel above if start_step <= 0)
        # =========================
        if start_step > 0 and start_step <= 3:
            # Only run if we're resuming and audio wasn't done in parallel
            _current_step_name = VideoStatus.STEP_3_TRANSCRIBE_AUDIO
            update_video_status_sync(video_id, VideoStatus.STEP_3_TRANSCRIBE_AUDIO)
            logger.info("=== STEP 3 – AUDIO TO TEXT ===")
            # v7: Extract full audio first, skip chunks if successful
            full_path = extract_audio_full(video_path, ad)
            if not full_path:
                extract_audio_chunks(video_path, ad)
            transcribe_audio_chunks(ad, atd)
        elif start_step <= 0:
            # Already done in parallel above
            logger.info("[SKIP] STEP 3 (already done in parallel)")
        else:
            logger.info("[SKIP] STEP 3")

        # =========================
        # STEP 4 – IMAGE CAPTION (filtered by CSV importance)
        # =========================
        if start_step <= 4:
            _current_step_name = VideoStatus.STEP_4_IMAGE_CAPTION
            update_video_status_sync(video_id, VideoStatus.STEP_4_IMAGE_CAPTION)
            logger.info("=== STEP 4 – IMAGE CAPTION ===")

            # Filter rep_frames to only important phases
            filtered_rep_frames = rep_frames
            if phase_importance and rep_frames:
                filtered_rep_frames = [
                    rf for i, rf in enumerate(rep_frames)
                    if i < len(phase_importance) and phase_importance[i]
                ]
                logger.info(
                    "[CSV_FILTER] Image caption: %d/%d rep_frames (filtered)",
                    len(filtered_rep_frames), len(rep_frames),
                )

            def _on_step4_progress(pct):
                try:
                    update_video_step_progress_sync(video_id, pct)
                except Exception as _e:
                    logger.debug(f"Suppressed: {_e}")
            keyframe_captions = caption_keyframes(
                frame_dir=frame_dir,
                rep_frames=filtered_rep_frames if filtered_rep_frames else rep_frames,
                on_progress=_on_step4_progress,
            )

        else:
            logger.info("[SKIP] STEP 4")
            keyframe_captions = None

        # =========================
        # STEP 5 – BUILD PHASE UNITS (DB CHECKPOINT)
        # =========================
        if start_step <= 5:
            _current_step_name = VideoStatus.STEP_5_BUILD_PHASE_UNITS
            update_video_status_sync(video_id, VideoStatus.STEP_5_BUILD_PHASE_UNITS)
            logger.info("=== STEP 5 – BUILD PHASE UNITS ===")
            phase_units = build_phase_units(
                user_id,
                keyframes=keyframes,
                rep_frames=rep_frames,
                keyframe_captions=keyframe_captions,
                phase_stats=phase_stats,
                total_frames=total_frames,
                frame_dir=frame_dir,
                audio_text_dir=atd,
                video_id=video_id,
            )

            # --- Persist audio_text (speech_text) to DB ---
            logger.info("[DB] Persist audio_text (speech_text) to video_phases")
            audio_text_count = 0
            for p in phase_units:
                speech = p.get("speech_text")
                if speech and str(speech).strip():
                    try:
                        update_video_phase_audio_text_sync(
                            video_id=video_id,
                            phase_index=p["phase_index"],
                            audio_text=str(speech).strip(),
                        )
                        audio_text_count += 1
                    except Exception as e:
                        logger.warning("[DB][WARN] audio_text save failed phase %s: %s", p["phase_index"], e)
            logger.info("[DB] Saved audio_text for %d/%d phases", audio_text_count, len(phase_units))

            # --- CLEANUP: Remove frames and audio to free disk space ---
            # Frames are no longer needed after STEP 5 (product detection uses them later,
            # but we keep them until after STEP 12.5)
            logger.info("[CLEANUP] Remove step1 cache + audio full WAV")
            try:
                cache_path = cache_dir(video_id)
                if os.path.isdir(cache_path):
                    shutil.rmtree(cache_path, ignore_errors=True)
                    logger.info("[CLEANUP] Removed cache: %s", cache_path)
                # Remove full audio WAV (large file, ~500MB for 1h video)
                # Keep audio_text (small .txt files) for product detection
                audio_path = audio_dir(video_id)
                if os.path.isdir(audio_path):
                    for f in os.listdir(audio_path):
                        if f.endswith('.wav') or f.endswith('.mp3'):
                            fp = os.path.join(audio_path, f)
                            os.remove(fp)
                            logger.info("[CLEANUP] Removed audio file: %s", fp)
            except Exception as e:
                logger.warning("[CLEANUP][WARN] Failed to clean cache/audio: %s", e)

        else:
            logger.info("[SKIP] STEP 5")
            # raise RuntimeError("Resume from STEP >=5 should load phase_units from DB (not implemented yet).")
            phase_units = load_video_phases_sync(video_id, user_id)

        # =========================
        # STEP 5.5 – MERGE EXCEL DATA INTO PHASE UNITS + PERSIST CSV METRICS
        # =========================
        if excel_data and excel_data.get("has_trend_data"):
            logger.info("[EXCEL] Merging sales/trend data into phase_units...")
            from csv_slot_filter import (
                _find_key, _safe_float, _parse_time_to_seconds,
                _detect_time_key, compute_slot_scores, KPI_ALIASES,
            )

            trends = excel_data["trends"]
            scored_slots = compute_slot_scores(trends)
            time_key = _detect_time_key(trends)
            sample = trends[0] if trends else {}

            # ---- Column Normalizer 統合 ----
            # スコアリングベースで全メトリクスを一括検出し、未検出時はアラートを出す
            try:
                from column_normalizer import (
                    detect_all_columns, log_detection_result,
                    check_critical_metrics, find_best_column,
                )
                _use_normalizer = True
            except ImportError:
                logger.warning("[CSV_METRICS] column_normalizer not available, using legacy _find_key")
                _use_normalizer = False

            if _use_normalizer and sample:
                # 全メトリクスを一括検出
                detection_result = detect_all_columns(sample)
                log_detection_result(detection_result, video_id=str(video_id))

                # クリティカルメトリクスのチェック（gmv, orders, viewers, likes）
                critical_ok, critical_missing = check_critical_metrics(detection_result)
                if not critical_ok:
                    logger.error(
                        "[CSV_METRICS] CRITICAL: Missing essential metrics %s for video %s. "
                        "Excel column headers may have changed. Available columns: %s",
                        critical_missing, video_id, list(sample.keys()),
                    )

                detected = detection_result["detected"]
                gmv_key = detected.get("gmv")
                order_key = detected.get("order_count")
                viewer_key = detected.get("viewer_count")
                like_key = detected.get("like_count")
                comment_key = detected.get("comment_count")
                share_key = detected.get("share_count")
                follower_key = detected.get("new_followers")
                click_key = detected.get("product_clicks")
                conv_key = detected.get("ctor")
                gpm_key = detected.get("gpm")
            else:
                # フォールバック: KPI_ALIASES経由の_find_key
                gmv_key = _find_key(sample, KPI_ALIASES["gmv"])
                order_key = _find_key(sample, KPI_ALIASES["order_count"])
                viewer_key = _find_key(sample, KPI_ALIASES["viewer_count"])
                like_key = _find_key(sample, KPI_ALIASES["like_count"])
                comment_key = _find_key(sample, KPI_ALIASES["comment_count"])
                share_key = _find_key(sample, KPI_ALIASES["share_count"])
                follower_key = _find_key(sample, KPI_ALIASES["new_followers"])
                click_key = _find_key(sample, KPI_ALIASES["product_clicks"])
                conv_key = _find_key(sample, KPI_ALIASES["ctor"])
                gpm_key = _find_key(sample, KPI_ALIASES["gpm"])

            logger.info("[CSV_METRICS] Detected keys: gmv=%s, order=%s, viewer=%s, like=%s, comment=%s, share=%s, follower=%s, click=%s, conv=%s, gpm=%s",
                gmv_key, order_key, viewer_key, like_key, comment_key, share_key, follower_key, click_key, conv_key, gpm_key)

            # CSVエントリを時刻順にソート
            timed_entries = []
            if time_key:
                for entry in trends:
                    t_sec = _parse_time_to_seconds(entry.get(time_key))
                    if t_sec is not None:
                        timed_entries.append({"time_sec": t_sec, "entry": entry})
                timed_entries.sort(key=lambda x: x["time_sec"])

            # video_start_sec: CSVの最初のタイムスタンプ
            # time_offset_seconds: この動画がCSVタイムライン内のどこから始まるか
            csv_first_sec = timed_entries[0]["time_sec"] if timed_entries else 0
            video_start_sec = csv_first_sec + time_offset_seconds
            csv_last_sec = timed_entries[-1]["time_sec"] if timed_entries else 0
            logger.info(
                "[CSV_METRICS] timed_entries=%d, csv_first=%.1f, csv_last=%.1f, "
                "time_offset=%.1f, video_start=%.1f",
                len(timed_entries), csv_first_sec, csv_last_sec,
                time_offset_seconds, video_start_sec,
            )
            # Log sample time values for debugging
            if timed_entries:
                sample_times = [te["time_sec"] for te in timed_entries[:5]]
                logger.info("[CSV_METRICS] First 5 CSV time_sec values: %s", sample_times)

            # スコア付きスロットをtime_secでインデックス化
            score_map = {s["time_sec"]: s["score"] for s in scored_slots}

            # ── CSV スロット区間の構築 ──
            # CSVは30分間隔等の粗い粒度。各CSVエントリが「次のエントリまで」の
            # 区間を代表すると見なし、フェーズとの重なり時間で按分する。
            csv_slots = []  # [{"start": float, "end": float, "entry": dict}]
            for i, te in enumerate(timed_entries):
                slot_start = te["time_sec"]
                if i + 1 < len(timed_entries):
                    slot_end = timed_entries[i + 1]["time_sec"]
                else:
                    # 最後のスロット: 動画の最後まで
                    video_end_abs = csv_first_sec + time_offset_seconds + (phase_units[-1].get("time_range", {}).get("end_sec", 0) if phase_units else 0)
                    slot_end = max(slot_start + 1800, video_end_abs)  # 最低30分
                csv_slots.append({"start": slot_start, "end": slot_end, "entry": te["entry"]})

            logger.info("[CSV_METRICS] Built %d CSV slots for interpolation", len(csv_slots))
            if csv_slots:
                logger.info("[CSV_METRICS] Slot ranges: %s",
                    [(f"{s['start']:.0f}-{s['end']:.0f}") for s in csv_slots])

            for p in phase_units:
                tr = p.get("time_range", {})
                start_sec = tr.get("start_sec", 0)
                end_sec = tr.get("end_sec", 0)

                phase_abs_start = csv_first_sec + time_offset_seconds + start_sec
                phase_abs_end   = csv_first_sec + time_offset_seconds + end_sec
                sales_info = match_sales_to_phase(trends, phase_abs_start, phase_abs_end)
                p["sales_data"] = sales_info

                # ── 按分ロジック ──
                # フェーズとCSVスロットの重なり時間に基づいてメトリクスを按分
                phase_gmv = 0.0
                phase_orders = 0.0
                phase_viewers = 0
                phase_likes = 0
                phase_comments = 0.0
                phase_shares = 0.0
                phase_followers = 0.0
                phase_clicks = 0.0
                phase_conv = 0.0
                phase_gpm = 0.0
                phase_score = 0
                match_count = 0

                phase_dur = max(phase_abs_end - phase_abs_start, 1)  # ゼロ除算防止

                for slot in csv_slots:
                    # フェーズとスロットの重なりを計算
                    overlap_start = max(phase_abs_start, slot["start"])
                    overlap_end = min(phase_abs_end, slot["end"])
                    overlap = max(0, overlap_end - overlap_start)
                    if overlap <= 0:
                        continue

                    slot_dur = max(slot["end"] - slot["start"], 1)
                    ratio = overlap / slot_dur  # このフェーズが受け取るスロットの割合
                    e = slot["entry"]
                    match_count += 1

                    # 加算型メトリクス: 按分
                    if gmv_key:
                        phase_gmv += (_safe_float(e.get(gmv_key)) or 0) * ratio
                    if order_key:
                        phase_orders += (_safe_float(e.get(order_key)) or 0) * ratio
                    if comment_key:
                        phase_comments += (_safe_float(e.get(comment_key)) or 0) * ratio
                    if share_key:
                        phase_shares += (_safe_float(e.get(share_key)) or 0) * ratio
                    if follower_key:
                        phase_followers += (_safe_float(e.get(follower_key)) or 0) * ratio
                    if click_key:
                        phase_clicks += (_safe_float(e.get(click_key)) or 0) * ratio

                    # スナップショット型メトリクス: 最大値（按分しない）
                    if viewer_key:
                        phase_viewers = max(phase_viewers, int(_safe_float(e.get(viewer_key)) or 0))
                    if like_key:
                        phase_likes = max(phase_likes, int(_safe_float(e.get(like_key)) or 0))
                    if conv_key:
                        cv = _safe_float(e.get(conv_key)) or 0
                        phase_conv = max(phase_conv, cv)
                    if gpm_key:
                        gv = _safe_float(e.get(gpm_key)) or 0
                        phase_gpm = max(phase_gpm, gv)
                    phase_score = max(phase_score, score_map.get(slot["start"], 0))

                # 加算型を整数に丸める
                phase_orders = int(round(phase_orders))
                phase_comments = int(round(phase_comments))
                phase_shares = int(round(phase_shares))
                phase_followers = int(round(phase_followers))
                phase_clicks = int(round(phase_clicks))

                if p.get("phase_index", 0) <= 3 or match_count > 0:
                    logger.info(
                        "[CSV_METRICS] Phase %s: start=%.0f end=%.0f abs_start=%.0f abs_end=%.0f "
                        "match_count=%d gmv=%.1f orders=%d viewers=%d",
                        p.get("phase_index", "?"), start_sec, end_sec,
                        phase_abs_start, phase_abs_end,
                        match_count, phase_gmv, phase_orders, phase_viewers,
                    )

                # sales_dataから商品名を取得
                phase_product_names = sales_info.get("products_sold", []) if sales_info else []

                # phase_unitにCSV指標を追加
                p["csv_metrics"] = {
                    "gmv": phase_gmv,
                    "order_count": phase_orders,
                    "viewer_count": phase_viewers,
                    "like_count": phase_likes,
                    "comment_count": phase_comments,
                    "share_count": phase_shares,
                    "new_followers": phase_followers,
                    "product_clicks": phase_clicks,
                    "conversion_rate": phase_conv,
                    "gpm": phase_gpm,
                    "importance_score": phase_score,
                }

                # DBに保存（product_namesはJSON配列文字列として保存）
                product_names_json = json.dumps(phase_product_names, ensure_ascii=False) if phase_product_names else None
                try:
                    update_video_phase_csv_metrics_sync(
                        video_id=str(video_id),
                        phase_index=p["phase_index"],
                        product_names=product_names_json,
                        **p["csv_metrics"],
                    )
                except Exception as e:
                    logger.warning("[CSV_METRICS] Failed to persist metrics for phase %d: %s", p["phase_index"], e)

            logger.info("[EXCEL] Sales data + CSV metrics merged into %d phases", len(phase_units))
        if excel_data and excel_data.get("has_product_data"):
            logger.info("[EXCEL] Product data available: %d products", len(excel_data["products"]))

        # =========================
        # STEP 5.6 – SALES MOMENT DETECTION: CSV (Feature Flag)
        # =========================
        # ルールB: 失敗しても全体は成功扱い
        # ルールC: ENABLE_SALES_MOMENT=true で有効化
        enable_sales_moment = os.environ.get("ENABLE_SALES_MOMENT", "true").lower() == "true"
        if enable_sales_moment and excel_data and excel_data.get("has_trend_data"):
            try:
                logger.info("=== STEP 5.6 – SALES MOMENT DETECTION (CSV) ===")
                ensure_sales_moments_table_sync()

                moments = detect_sales_moments(
                    trends=excel_data["trends"],
                    time_offset_seconds=time_offset_seconds if time_offset_seconds else 0,
                )

                if moments:
                    bulk_insert_sales_moments_sync(
                        video_id=str(video_id),
                        moments=moments,
                        source="csv",
                    )
                    logger.info(
                        "[SALES_MOMENT] Saved %d CSV moments for video %s",
                        len(moments), video_id,
                    )
                else:
                    logger.info("[SALES_MOMENT] No CSV sales moments detected for video %s", video_id)
            except Exception as e:
                # ルールB: 失敗しても全体は成功扱い
                logger.warning(
                    "[SALES_MOMENT] ERROR_TYPE=CSV_SALES_MOMENT_FAIL – %s (video %s). "
                    "Continuing with remaining pipeline.",
                    e, video_id,
                )
                _record_step_error(video_id, "STEP_5_6_SALES_MOMENT", "CSV_SALES_MOMENT_FAIL", e)
        elif not enable_sales_moment:
            logger.info("[SALES_MOMENT] Feature flag ENABLE_SALES_MOMENT is disabled, skipping")

        # =========================
        # STEP 5.7 – SCREEN MOMENT EXTRACTION (screen_recording only)
        # =========================
        # ルールB: 失敗しても全体は成功扱い
        # ルールD: upload_type != clean_video の場合のみ実行
        enable_screen_moment = os.environ.get("ENABLE_SCREEN_MOMENT", "true").lower() == "true"
        if enable_sales_moment and enable_screen_moment and is_screen_recording:
            try:
                logger.info("=== STEP 5.7 – SCREEN MOMENT EXTRACTION ===")
                ensure_sales_moments_table_sync()

                screen_moments = detect_screen_moments(
                    frame_dir=frame_dir,
                    keyframes=keyframes,
                    fps=1.0,
                    sample_interval_sec=5.0,
                    max_frames=30,
                )

                if screen_moments:
                    bulk_insert_sales_moments_sync(
                        video_id=str(video_id),
                        moments=screen_moments,
                        source="screen",
                    )
                    logger.info(
                        "[SCREEN_MOMENT] Saved %d screen moments for video %s",
                        len(screen_moments), video_id,
                    )
                else:
                    logger.info("[SCREEN_MOMENT] No screen moments detected for video %s", video_id)
            except Exception as e:
                # ルールB: 失敗しても全体は成功扱い
                logger.warning(
                    "[SCREEN_MOMENT] ERROR_TYPE=SCREEN_MOMENT_FAIL – %s (video %s). "
                    "Continuing with remaining pipeline.",
                    e, video_id,
                )
                _record_step_error(video_id, "STEP_5_7_SCREEN_MOMENT", "SCREEN_MOMENT_FAIL", e)
        elif is_screen_recording and not enable_screen_moment:
            logger.info("[SCREEN_MOMENT] Feature flag ENABLE_SCREEN_MOMENT is disabled, skipping")

        # =========================
        # POST-STEP 5.7: EARLY FRAME CLEANUP
        # =========================
        # All frame-dependent steps (1, 2, 4, 5, 5.7) are now complete.
        # STEP 12.5 (product_detection v4.1) works without frames.
        # Remove frames directory to free disk space (0.5-3 GB depending on video).
        try:
            if os.path.isdir(frame_dir):
                _frame_count = len([f for f in os.listdir(frame_dir) if f.endswith('.jpg')])
                shutil.rmtree(frame_dir, ignore_errors=True)
                logger.info(
                    "[FRAME_CLEANUP] Removed %d frames from %s after STEP 5.7. "
                    "All frame-dependent steps complete.",
                    _frame_count, frame_dir,
                )
        except Exception as _fc_err:
            logger.warning("[FRAME_CLEANUP] Non-fatal error: %s", _fc_err)

        # =========================
        # STEP 6 – PHASE DESCRIPTION
        # =========================

        if start_step <= 6:
            _current_step_name = VideoStatus.STEP_6_BUILD_PHASE_DESCRIPTION
            update_video_status_sync(video_id, VideoStatus.STEP_6_BUILD_PHASE_DESCRIPTION)
            logger.info("=== STEP 6 – PHASE DESCRIPTION ===")

            # Get video language for phase description generation
            from db_ops import get_video_language_sync as _get_lang_sync
            _video_lang = _get_lang_sync(video_id)
            logger.info("[STEP6] Video language: %s", _video_lang)

            def _on_step6_progress(pct):
                try:
                    update_video_step_progress_sync(video_id, pct)
                except Exception as _e:
                    logger.debug(f"Suppressed: {_e}")
            phase_units = build_phase_descriptions(phase_units, on_progress=_on_step6_progress, language=_video_lang)

            logger.info("[DB] Persist phase_description to video_phases")
            for p in phase_units:
                if p.get("phase_description"):
                    update_video_phase_description_sync(
                        video_id=video_id,
                        phase_index=p["phase_index"],
                        phase_description=p["phase_description"],
            )

            # --- CTA Score persistence ---
            logger.info("[DB] Persist cta_score to video_phases")
            cta_count = 0
            for p in phase_units:
                cta = p.get("cta_score")
                if cta is not None:
                    try:
                        update_video_phase_cta_score_sync(
                            video_id=video_id,
                            phase_index=p["phase_index"],
                            cta_score=int(cta),
                        )
                        cta_count += 1
                    except Exception as e:
                        logger.warning("[DB][WARN] cta_score save failed phase %s: %s", p["phase_index"], e)
            logger.info("[DB] Saved cta_score for %d/%d phases", cta_count, len(phase_units))

            # --- Sales Psychology Tags persistence ---
            logger.info("[DB] Persist sales_psychology_tags to video_phases")
            tags_count = 0
            for p in phase_units:
                tags = p.get("sales_tags")
                if tags and isinstance(tags, list) and len(tags) > 0:
                    try:
                        update_video_phase_sales_tags_sync(
                            video_id=video_id,
                            phase_index=p["phase_index"],
                            sales_tags_json=json.dumps(tags),
                        )
                        tags_count += 1
                    except Exception as e:
                        logger.warning("[DB][WARN] sales_tags save failed phase %s: %s", p["phase_index"], e)
            logger.info("[DB] Saved sales_tags for %d/%d phases", tags_count, len(phase_units))
        else:
            logger.info("[SKIP] STEP 6")

        # =========================
        # STEP 6.5 – AUDIO PARALINGUISTIC FEATURES (filtered)
        # =========================

        if start_step <= 6:
            logger.info("=== STEP 6.5 – AUDIO PARALINGUISTIC FEATURES ===")
            try:
                phase_units = analyze_phase_audio_features(
                    phase_units=phase_units,
                    video_path=video_path,
                )

                # Persist audio features to DB
                af_count = 0
                for p in phase_units:
                    af = p.get("audio_features")
                    if af is not None:
                        try:
                            update_video_phase_audio_features_sync(
                                video_id=video_id,
                                phase_index=p["phase_index"],
                                audio_features_json=json.dumps(af),
                            )
                            af_count += 1
                        except Exception as e:
                            logger.warning("[DB][WARN] audio_features save failed phase %s: %s", p["phase_index"], e)
                logger.info("[DB] Saved audio_features for %d/%d phases", af_count, len(phase_units))
            except Exception as e:
                logger.warning("[AUDIO-FEATURES][WARN] Skipped due to error: %s", e)
                _record_step_error(video_id, "STEP_6_5_AUDIO_FEATURES", "AUDIO_FEATURES_FAIL", e)
        else:
            logger.info("[SKIP] STEP 6.5")

        # =========================
        # STEP 7 – GLOBAL GROUPING
        # =========================
        if start_step <= 7:
            _current_step_name = VideoStatus.STEP_7_GROUPING
            update_video_status_sync(video_id, VideoStatus.STEP_7_GROUPING)
            logger.info("=== STEP 7 – GLOBAL PHASE GROUPING ===")
            phase_units = embed_phase_descriptions(phase_units)

            from grouping_pipeline import load_global_groups_from_db
            groups = load_global_groups_from_db(user_id)
            phase_units, groups = assign_phases_to_groups(phase_units, groups, user_id)

            for g in groups:
                update_phase_group_sync(
                    group_id=g["group_id"],
                    centroid=g["centroid"].tolist(),
                    size=g["size"],
            )

            for p in phase_units:
                if p.get("group_id"):
                    update_phase_group_for_video_phase_sync(
                        video_id=video_id,
                        phase_index=p["phase_index"],
                        group_id=p["group_id"],
                    )
        else:
            logger.info("[SKIP] STEP 7")

        # =========================
        # STEP 8 – GROUP BEST PHASES
        # =========================
       
        if start_step <= 8:
            _current_step_name = VideoStatus.STEP_8_UPDATE_BEST_PHASE
            update_video_status_sync(video_id, VideoStatus.STEP_8_UPDATE_BEST_PHASE)
            logger.info("=== STEP 8 – GROUP BEST PHASES (BULK) ===")

            best_data = load_group_best_phases(ART_ROOT, video_id)

            best_data = update_group_best_phases(
                phase_units=phase_units,
                best_data=best_data,
                video_id=video_id,
            )

            save_group_best_phases(best_data, ART_ROOT, video_id)

            # --------- Build bulk rows ---------
            bulk_rows = []

            for gid, g in best_data["groups"].items():
                if not g["phases"]:
                    continue

                gid = int(gid)
                best = g["phases"][0]
                m = best["metrics"]

                bulk_rows.append({
                    "group_id": gid,
                    "video_id": best["video_id"],
                    "phase_index": best["phase_index"],
                    "score": best["score"],
                    "view_velocity": m.get("view_velocity"),
                    "like_velocity": m.get("like_velocity"),
                    "like_per_viewer": m.get("like_per_viewer"),
                })

            logger.info(f"[STEP8] Bulk upsert {len(bulk_rows)} group best phases")


            bulk_upsert_group_best_phases_sync(user_id,bulk_rows)
            bulk_refresh_phase_insights_sync( user_id,bulk_rows)

        else:
            logger.info("[SKIP] STEP 8")

       
        # =========================
        # STEP 9 – BUILD VIDEO STRUCTURE FEATURES
        # =========================
        if start_step <= 9:
            _current_step_name = VideoStatus.STEP_9_BUILD_VIDEO_STRUCTURE_FEATURES
            update_video_status_sync(video_id, VideoStatus.STEP_9_BUILD_VIDEO_STRUCTURE_FEATURES)
            logger.info("=== STEP 9 – BUILD VIDEO STRUCTURE FEATURES ===")
            build_video_structure_features(video_id, user_id)
        else:
            logger.info("[SKIP] STEP 9")


        # =========================
        # STEP 10 – ASSIGN VIDEO STRUCTURE GROUP
        # =========================
        if start_step <= 10:
            update_video_status_sync(video_id, VideoStatus.STEP_10_ASSIGN_VIDEO_STRUCTURE_GROUP)
            logger.info("=== STEP 10 – ASSIGN VIDEO STRUCTURE GROUP ===")
            try:
                assign_video_structure_group(video_id, user_id)
            except Exception as e:
                logger.warning("[STEP10] Non-fatal error (continuing): %s", e)
                _record_step_error(video_id, "STEP_10_ASSIGN_VIDEO_STRUCTURE_GROUP", "STRUCTURE_GROUP_FAIL", e)
        else:
            logger.info("[SKIP] STEP 10")


        # =========================
        # STEP 11 – UPDATE VIDEO STRUCTURE GROUP STATS
        # =========================
        if start_step <= 11:
            update_video_status_sync(video_id, VideoStatus.STEP_11_UPDATE_VIDEO_STRUCTURE_GROUP_STATS)
            logger.info("=== STEP 11 – UPDATE VIDEO STRUCTURE GROUP STATS ===")
            try:
                group_id = get_video_structure_group_id_of_video_sync(video_id, user_id)
                if group_id:
                    recompute_video_structure_group_stats(group_id, user_id)
            except Exception as e:
                logger.warning("[STEP11] Non-fatal error (continuing): %s", e)
                _record_step_error(video_id, "STEP_11_UPDATE_VIDEO_STRUCTURE_GROUP_STATS", "STRUCTURE_STATS_FAIL", e)
        else:
            logger.info("[SKIP] STEP 11")

        # =========================
        # STEP 12 – UPDATE VIDEO STRUCTURE BEST
        # =========================
        if start_step <= 12:
            update_video_status_sync(video_id, VideoStatus.STEP_12_UPDATE_VIDEO_STRUCTURE_BEST)
            logger.info("=== STEP 12 – UPDATE VIDEO STRUCTURE BEST ===")
            try:
                process_best_video(video_id, user_id)
            except Exception as e:
                logger.warning("[STEP12] Non-fatal error (continuing): %s", e)
                _record_step_error(video_id, "STEP_12_UPDATE_VIDEO_STRUCTURE_BEST", "BEST_VIDEO_FAIL", e)
        else:
            logger.info("[SKIP] STEP 12")


        # ---------- ensure best_data for resume ----------
        # ---------- ensure best_data for resume ----------
        if 'best_data' not in locals() or best_data is None:
            logger.info("[RESUME] Reload best_data from artifact")
            best_data = load_group_best_phases(ART_ROOT, video_id)

        # =========================
        # STEP 12.5 – PRODUCT DETECTION
        # =========================
        exposures = []  # Initialize for use in Report 3
        if start_step <= 13:  # index 13 in STEP_ORDER
            _current_step_name = VideoStatus.STEP_12_5_PRODUCT_DETECTION
            update_video_status_sync(video_id, VideoStatus.STEP_12_5_PRODUCT_DETECTION)
            logger.info("=== STEP 12.5 – PRODUCT DETECTION ===")

            def _on_product_progress(pct):
                try:
                    update_video_step_progress_sync(video_id, pct)
                except Exception as _e:
                    logger.debug(f"Suppressed: {_e}")

            try:
                # Ensure table exists
                ensure_product_exposures_table_sync()

                # Get product list from excel_data
                product_list = []
                if excel_data and excel_data.get("has_product_data"):
                    product_list = excel_data.get("products", [])
                    logger.info("[PRODUCT] Using %d products from Excel", len(product_list))

                # ★ FALLBACK: excel_dataが初期ロードで失敗した場合、再ロードを試みる
                # SASトークン期限切れが主な原因なので、force_regenerateで新しいトークンを取得
                if not product_list:
                    try:
                        _fb_urls = get_video_excel_urls_sync(video_id)
                        if _fb_urls and _fb_urls.get("excel_product_blob_url"):
                            logger.info(
                                "[PRODUCT] excel_data missing, retrying Excel load (fallback)... "
                                "product_url_prefix=%s",
                                _fb_urls.get('excel_product_blob_url', '')[:80]
                            )
                            _fb_excel = load_excel_data(video_id, _fb_urls)
                            if _fb_excel and _fb_excel.get("has_product_data"):
                                product_list = _fb_excel.get("products", [])
                                excel_data = _fb_excel  # Update for Report 3
                                if not time_offset_seconds:
                                    time_offset_seconds = _fb_urls.get("time_offset_seconds", 0)
                                logger.info("[PRODUCT] Fallback loaded %d products from Excel", len(product_list))
                            else:
                                logger.warning(
                                    "[PRODUCT] Fallback Excel load returned no products "
                                    "(has_product=%s, products=%d)",
                                    _fb_excel.get('has_product_data') if _fb_excel else None,
                                    len(_fb_excel.get('products', [])) if _fb_excel else 0,
                                )
                        else:
                            logger.info(
                                "[PRODUCT] No excel_product_blob_url in DB for video %s",
                                video_id
                            )
                    except Exception as _fb_err:
                        logger.warning("[PRODUCT] Fallback Excel load failed: %s (type=%s)", _fb_err, type(_fb_err).__name__)

                if product_list:
                    # Load transcription segments from audio_text .txt files
                    transcription_segments = None
                    atd_path = audio_text_dir(video_id)
                    if os.path.isdir(atd_path):
                        from phase_pipeline import load_all_audio_segments
                        raw_segments = load_all_audio_segments(atd_path)
                        if raw_segments:
                            transcription_segments = raw_segments
                            logger.info("[PRODUCT] Loaded %d transcription segments from audio_text", len(transcription_segments))

                    # ★v4.3: Fallback - load from DB when audio_text files don't exist
                    # (e.g., after deploy/VM restart that deletes local files)
                    if not transcription_segments:
                        try:
                            from db_ops import get_event_loop, AsyncSessionLocal
                            async def _load_transcription_from_db():
                                async with AsyncSessionLocal() as sess:
                                    r = await sess.execute(
                                        text(
                                            "SELECT phase_index, time_start, time_end, audio_text "
                                            "FROM video_phases "
                                            "WHERE video_id = :vid AND audio_text IS NOT NULL AND audio_text != '' "
                                            "ORDER BY phase_index"
                                        ),
                                        {"vid": str(video_id)}
                                    )
                                    rows = r.fetchall()
                                    if not rows:
                                        return []
                                    segments = []
                                    for row in rows:
                                        t_start = float(row[1]) if row[1] else 0
                                        t_end = float(row[2]) if row[2] else 0
                                        txt = str(row[3]).strip()
                                        if txt and t_end > t_start:
                                            segments.append({
                                                "start": t_start,
                                                "end": t_end,
                                                "text": txt
                                            })
                                    return segments
                            db_segments = get_event_loop().run_until_complete(_load_transcription_from_db())
                            if db_segments:
                                transcription_segments = db_segments
                                logger.info("[PRODUCT] Loaded %d transcription segments from DB (fallback)", len(transcription_segments))
                            else:
                                logger.warning("[PRODUCT] No transcription segments found in DB either")
                        except Exception as _db_err:
                            logger.warning("[PRODUCT] Failed to load transcription from DB: %s", _db_err)

                    # Run product detection (v4.1: audio-first + sales + minimal image)
                    # ★v4.2: duration_secを複数ソースから取得（フレーム数 > _vid_duration > DB > 0）
                    _product_duration = 0
                    if total_frames:
                        _product_duration = float(total_frames)
                    elif locals().get('_vid_duration'):
                        _product_duration = float(locals()['_vid_duration'])
                    else:
                        # DBからduration_secを取得（resume時にtotal_framesがない場合）
                        # 優先順位: videos.duration_sec > video_phases.MAX(time_end)
                        try:
                            from db_ops import get_event_loop, AsyncSessionLocal
                            async def _get_duration_from_db():
                                async with AsyncSessionLocal() as sess:
                                    # まずvideos.duration_secを確認（最も信頼性が高い）
                                    r1 = await sess.execute(
                                        text("SELECT duration_sec FROM videos WHERE id = :vid"),
                                        {"vid": str(video_id)}
                                    )
                                    row1 = r1.fetchone()
                                    if row1 and row1[0] and float(row1[0]) > 0:
                                        return float(row1[0])
                                    # フォールバック: video_phasesのMAX(time_end)
                                    r2 = await sess.execute(
                                        text("SELECT COALESCE(MAX(time_end), 0) as max_t FROM video_phases WHERE video_id = :vid"),
                                        {"vid": str(video_id)}
                                    )
                                    row2 = r2.fetchone()
                                    return float(row2[0]) if row2 and row2[0] else 0
                            _product_duration = get_event_loop().run_until_complete(_get_duration_from_db())
                            if _product_duration > 0:
                                logger.info("[PRODUCT] Duration from DB: %.1f sec", _product_duration)
                        except Exception as _dur_err:
                            logger.warning("[PRODUCT] Failed to get duration from DB: %s", _dur_err)
                    exposures = detect_product_timeline(
                        frame_dir=frames_dir(video_id),
                        product_list=product_list,
                        transcription_segments=transcription_segments,
                        sample_interval=5,
                        on_progress=_on_product_progress,
                        excel_data=excel_data,
                        time_offset_seconds=time_offset_seconds,
                        duration_sec=_product_duration,
                    )

                    logger.info("[PRODUCT] Detected %d product exposure segments", len(exposures))

                    # Save to DB
                    if exposures:
                        bulk_insert_product_exposures_sync(video_id, user_id, exposures)
                        logger.info("[PRODUCT] Saved %d exposures to DB", len(exposures))

                    # Save artifact
                    art_path = os.path.join(video_root(video_id), "product_exposures.json")
                    with open(art_path, "w", encoding="utf-8") as f:
                        json.dump(exposures, f, ensure_ascii=False, indent=2)
                else:
                    # Check if Excel product data exists but wasn't loaded
                    try:
                        _excel_urls = get_video_excel_urls_sync(video_id)
                        _has_excel = bool(_excel_urls and _excel_urls.get("excel_product_blob_url"))
                    except Exception:
                        _has_excel = False
                    if _has_excel:
                        logger.warning(
                            "[PRODUCT] \u26a0 Excel product data EXISTS in DB but excel_data is empty/missing! "
                            "This video may need re-processing from STEP_0 to load Excel data. "
                            "video_id=%s", video_id
                        )
                        _record_step_error(
                            video_id, "STEP_12_5_PRODUCT_DETECTION",
                            "PRODUCT_DATA_MISMATCH",
                            Exception("Excel product data exists but was not loaded into pipeline")
                        )
                    else:
                        logger.info("[PRODUCT] No product list available, skipping detection")
            except Exception as e:
                logger.warning("[STEP12.5] Non-fatal error (continuing): %s", e)
                _record_step_error(video_id, "STEP_12_5_PRODUCT_DETECTION", "PRODUCT_DETECTION_FAIL", e)
        else:
            logger.info("[SKIP] STEP 12.5")

        # --- CLEANUP: Remove frames after product detection (last step that needs them) ---
        try:
            fd = frames_dir(video_id)
            if os.path.isdir(fd):
                shutil.rmtree(fd, ignore_errors=True)
                logger.info("[CLEANUP] Removed frames directory: %s", fd)
            # Also remove audio_text (no longer needed)
            atd_cleanup = audio_text_dir(video_id)
            if os.path.isdir(atd_cleanup):
                shutil.rmtree(atd_cleanup, ignore_errors=True)
                logger.info("[CLEANUP] Removed audio_text directory: %s", atd_cleanup)
            # Remove audio directory entirely
            ad_cleanup = audio_dir(video_id)
            if os.path.isdir(ad_cleanup):
                shutil.rmtree(ad_cleanup, ignore_errors=True)
                logger.info("[CLEANUP] Removed audio directory: %s", ad_cleanup)
        except Exception as e:
            logger.warning("[CLEANUP][WARN] Failed to clean frames/audio: %s", e)

        # =========================
        # STEP 13 – BUILD REPORTS
        # =========================
        if start_step <= 14:  # index 14 in STEP_ORDER (shifted +1)
            _current_step_name = VideoStatus.STEP_13_BUILD_REPORTS
            update_video_status_sync(video_id, VideoStatus.STEP_13_BUILD_REPORTS)
            logger.info("=== STEP 13 – BUILD REPORTS ===")

            # ---------- REPORT 1 ----------
            r1 = build_report_1_timeline(phase_units)

            # ---------- REPORT 2 & 3 (PARALLEL GPT CALLS) ----------
            from report_pipeline import (
                build_report_3_structure_vs_benchmark_raw,
                rewrite_report_3_structure_with_gpt,
            )
            from db_ops import (
                get_video_structure_features_sync,
                get_video_structure_group_best_video_sync,
                get_video_structure_group_stats_sync,
                get_video_language_sync,
            )

            # Get video language setting for report generation
            video_language = get_video_language_sync(video_id)
            logger.info("[REPORT] Video language: %s", video_language)

            # Enrich phase_units with human_sales_tags from DB if not already present
            # (human_sales_tags are set by experts via the UI, not by the pipeline)
            _has_human_tags = any(p.get("human_sales_tags") for p in phase_units)
            if not _has_human_tags:
                try:
                    from db_ops import get_phase_human_sales_tags_sync
                    _ht_map = get_phase_human_sales_tags_sync(video_id, user_id)
                    if _ht_map:
                        for p in phase_units:
                            pi = p["phase_index"]
                            if pi in _ht_map and _ht_map[pi]:
                                p["human_sales_tags"] = _ht_map[pi]
                        logger.info("[REPORT] Enriched %d phases with human_sales_tags from DB", len(_ht_map))
                except Exception as e:
                    logger.warning("[REPORT][WARN] Failed to load human_sales_tags: %s", e)

            # Enrich phase_units with NG (unusable) clip info from DB
            # so GPT can factor in which phases were marked as bad
            try:
                from db_ops import get_unusable_phases_sync
                _ng_map = get_unusable_phases_sync(video_id)
                if _ng_map:
                    for p in phase_units:
                        pi = p["phase_index"]
                        if pi in _ng_map:
                            val = _ng_map[pi]
                            p["is_unusable"] = True
                            if isinstance(val, dict):
                                p["unusable_reason"] = val.get("reason", "unknown")
                                if val.get("comment"):
                                    p["unusable_comment"] = val["comment"]
                            else:
                                p["unusable_reason"] = val
                    logger.info("[REPORT] Enriched %d phases with NG (unusable) flags from DB", len(_ng_map))
            except Exception as e:
                logger.warning("[REPORT][WARN] Failed to load unusable flags: %s", e)

            r2_raw = build_report_2_phase_insights_raw(
                phase_units, best_data, excel_data=excel_data
            )

            # Prepare Report 3 raw data (fast, no GPT)
            r3_raw = None
            r3_gpt = None

            group_id = get_video_structure_group_id_of_video_sync(video_id, user_id)
            if not group_id:
                logger.info("[REPORT3] No structure group, skip")
            else:
                best = get_video_structure_group_best_video_sync(group_id, user_id)
                if not best:
                    logger.info("[REPORT3] No benchmark video, skip")
                else:
                    best_video_id = best["video_id"]
                    current_features = get_video_structure_features_sync(video_id, user_id)
                    best_features = get_video_structure_features_sync(best_video_id, user_id)
                    group_stats = get_video_structure_group_stats_sync(group_id, user_id)
                    if not current_features or not best_features:
                        logger.info("[REPORT3] Missing structure features, skip")
                    else:
                        r3_raw = build_report_3_structure_vs_benchmark_raw(
                            current_features=current_features,
                            best_features=best_features,
                            group_stats=group_stats,
                            phase_units=phase_units,
                            product_exposures=exposures,
                        )

            # Run Report 2 GPT and Report 3 GPT in parallel
            logger.info("[REPORT] Running Report 2 & 3 GPT rewrites in parallel")
            r2_gpt = None
            with ThreadPoolExecutor(max_workers=2) as report_pool:
                fut_r2 = report_pool.submit(rewrite_report_2_with_gpt, r2_raw, excel_data=excel_data, language=video_language)
                fut_r3 = None
                if r3_raw is not None:
                    fut_r3 = report_pool.submit(rewrite_report_3_structure_with_gpt, r3_raw, language=video_language)

                r2_gpt = fut_r2.result()
                if fut_r3 is not None:
                    r3_gpt = fut_r3.result()

            # Persist Report 2
            for item in r2_gpt:
                upsert_phase_insight_sync(
                    user_id,
                    video_id=video_id,
                    phase_index=item["phase_index"],
                    group_id=int(item["group_id"]) if item.get("group_id") else None,
                    insight=item["insight"],
                )

            # Persist Report 3
            if r3_gpt is not None:
                save_reports(
                    video_id,
                    r1,
                    r2_raw,
                    r2_gpt,
                    r3_raw,
                    r3_gpt,
                )
                insert_video_insight_sync(
                    video_id=video_id,
                    title="Video Structure Analysis",
                    content=json.dumps(r3_gpt, ensure_ascii=False),
                )

        else:
            logger.info("[SKIP] STEP 13")

        if start_step <= 15:  # index 15 in STEP_ORDER (shifted +1)
            _current_step_name = VideoStatus.STEP_14_FINALIZE
            update_video_status_sync(video_id, VideoStatus.STEP_14_FINALIZE)
            update_video_step_progress_sync(video_id, 0)
            logger.info("=== STEP 14 \u2013 FINALIZE PIPELINE (WAIT SPLIT) ===")

            CHECK_INTERVAL = 5
            STALL_TIMEOUT = 60 * 60   # 60 min stall detection (long videos have slow splits)

            # Count total phases for progress calculation
            try:
                total_split_phases = len(load_video_phases_sync(video_id, user_id))
            except Exception:
                total_split_phases = 0

            # Dynamic timeout: scale with number of phases (min 2h, max 8h)
            MAX_WAIT_SEC = max(60 * 120, min(max(total_split_phases, 1) * 120, 60 * 480))  # 2h-8h
            logger.info("[STEP14] total_split_phases=%d, MAX_WAIT_SEC=%ds (%.1fh)",
                        total_split_phases, MAX_WAIT_SEC, MAX_WAIT_SEC / 3600)

            waited = 0
            last_progress_status = None
            last_progress_time = time.time()
            last_heartbeat_time = time.time()
            HEARTBEAT_INTERVAL = 60  # Update updated_at every 60 seconds

            _db_error_count = 0
            _MAX_DB_ERRORS = 10  # Allow up to 10 consecutive DB errors before giving up

            while True:
                # ── DB-resilient split status check ──
                try:
                    split_status = get_video_split_status_sync(video_id)
                    _db_error_count = 0  # Reset on success
                except Exception as _db_err:
                    _db_error_count += 1
                    logger.warning(
                        "[FINALIZE] DB error #%d/%d getting split_status: %s",
                        _db_error_count, _MAX_DB_ERRORS, _db_err,
                    )
                    if _db_error_count >= _MAX_DB_ERRORS:
                        logger.error(
                            "[FINALIZE] Too many consecutive DB errors (%d) → mark DONE (partial split)",
                            _db_error_count,
                        )
                        # Try one last time to mark as DONE after pool reset
                        try:
                            from db_ops import reset_pool_sync
                            reset_pool_sync()
                            update_video_step_progress_sync(video_id, 100)
                            update_video_status_sync(video_id, VideoStatus.DONE)
                        except Exception as _final_err:
                            logger.error("[FINALIZE] Final DONE update also failed: %s", _final_err)
                        break
                    # Reset pool and retry after a short delay
                    try:
                        from db_ops import reset_pool_sync
                        reset_pool_sync()
                    except Exception:
                        pass
                    time.sleep(min(10 * _db_error_count, 60))  # Backoff: 10s, 20s, ..., 60s
                    waited += min(10 * _db_error_count, 60)
                    continue

                # Heartbeat: periodically touch updated_at to prevent
                # stuck_video_monitor from misidentifying this as stuck
                if time.time() - last_heartbeat_time >= HEARTBEAT_INTERVAL:
                    try:
                        # Re-write current progress; updated_at is set by the SQL
                        update_video_step_progress_sync(video_id, 0)
                    except Exception as _e:
                        logger.debug(f"Suppressed heartbeat error: {_e}")
                    last_heartbeat_time = time.time()

                if split_status == "done":
                    logger.info("[FINALIZE] Split DONE → mark video DONE")
                    update_video_step_progress_sync(video_id, 100)
                    update_video_status_sync(video_id, VideoStatus.DONE)
                    break

                # Handle error status from split process
                if split_status and str(split_status).lower() in ("error", "failed"):
                    logger.warning("[FINALIZE] Split reported error status=%s → mark DONE anyway (partial split)", split_status)
                    update_video_step_progress_sync(video_id, 100)
                    update_video_status_sync(video_id, VideoStatus.DONE)
                    break

                # Detect stall: if split_status hasn't changed for STALL_TIMEOUT
                if split_status != last_progress_status:
                    last_progress_status = split_status
                    last_progress_time = time.time()
                elif time.time() - last_progress_time >= STALL_TIMEOUT:
                    logger.warning(
                        "[FINALIZE] Split stalled for %ds at status=%s → mark DONE (partial split)",
                        int(time.time() - last_progress_time), split_status
                    )
                    update_video_step_progress_sync(video_id, 100)
                    update_video_status_sync(video_id, VideoStatus.DONE)
                    break

                if waited >= MAX_WAIT_SEC:
                    # Instead of raising error, mark as DONE with partial split
                    logger.warning(
                        "[FINALIZE] Split timeout after %ds (split_status=%s) → mark DONE (partial split)",
                        MAX_WAIT_SEC, split_status
                    )
                    update_video_step_progress_sync(video_id, 100)
                    update_video_status_sync(video_id, VideoStatus.DONE)
                    break

                # Update step_progress based on split_status (phase number)
                if total_split_phases > 0 and split_status and split_status not in ("new", "", None):
                    try:
                        completed_phases = int(split_status)
                        pct = min(int(completed_phases / total_split_phases * 100), 99)
                        update_video_step_progress_sync(video_id, pct)
                    except (ValueError, TypeError) as _e:
                        logger.debug(f"Suppressed: {_e}")

                logger.info("[FINALIZE] Waiting split... current=%s (waited=%ds)", split_status, waited)
                time.sleep(CHECK_INTERVAL)
                waited += CHECK_INTERVAL
                

        # =========================
        # CLEANUP – CLEAR THIS video's files
        # =========================
        cleanup_video_files(video_id)


    except Exception as exc:
        # Set error status AND error_message so UI can display it
        _err_msg = str(exc)[:500]
        try:
            from db_ops import update_video_error_message_sync
            update_video_error_message_sync(video_id, _err_msg)
        except Exception as _e:
            logger.debug(f"Suppressed: {_e}")
        update_video_status_sync(video_id, VideoStatus.ERROR)
        logger.exception("Video processing failed: %s", _err_msg)

        # Record error log to DB
        _current_step = getattr(exc, '_error_step', None) or _current_step_name or 'UNKNOWN'
        _error_code = getattr(exc, '_error_code', None) or type(exc).__name__
        try:
            import traceback as _tb
            insert_video_error_log_sync(
                video_id=video_id,
                error_code=_error_code,
                error_step=_current_step,
                error_message=str(exc)[:2000],
                error_detail=_tb.format_exc()[:10000],
                source="worker",
                update_last_error=True,  # Fatal error: update last_error_code
            )
        except Exception as log_err:
            logger.warning("[ERROR_LOG] Failed to record error log: %s", log_err)

        # ── Emit structured JSON error summary to stdout/stderr ──
        # queue_worker.py reads this to record detailed error info
        # even when process_video.py exits with code 1 or -6 (SIGABRT)
        try:
            import json as _json
            _summary = _json.dumps({
                "error_summary": True,
                "step": _current_step,
                "code": _error_code,
                "message": str(exc)[:500],
                "type": type(exc).__name__,
            }, ensure_ascii=False)
            # Print to stdout (which is captured by queue_worker's log file)
            print(_summary, flush=True)
        except Exception:
            pass

        # Still cleanup on error to prevent disk accumulation
        try:
            cleanup_video_files(video_id)
        except Exception as ce:
            logger.warning("[CLEANUP][ERROR-PATH] Cleanup also failed: %s", ce)
        raise
    finally:
        # Final safety net: always attempt cleanup regardless of success/error
        try:
            cleanup_video_files(video_id)
        except Exception as _e:
            logger.debug(f"Suppressed: {_e}")
        logger.info("[DB] Closing database connection...")
        close_db_sync()

if __name__ == "__main__":
    main()
# Health check log trigger
