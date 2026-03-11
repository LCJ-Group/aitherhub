"""
Video compression module for 1080p transcoding.

Compresses uploaded videos to 1080p H.264 using FFmpeg, then uploads
the compressed version back to Azure Blob Storage, replacing the original.
This reduces storage costs and speeds up downstream processing.

Typical compression ratio: 13GB → 1-2GB (for 2-3 hour livestream videos)
"""
import os
import sys
import shutil
import subprocess
import logging
from urllib.parse import urlparse, unquote

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("video_compressor")

# =====================
# BIN RESOLVE
# =====================
def _resolve_bin(name, win_fallback=None, linux_fallback=None):
    if sys.platform.startswith("win"):
        return shutil.which(name) or win_fallback
    return shutil.which(name) or linux_fallback


FFMPEG = _resolve_bin(
    "ffmpeg",
    win_fallback=r"C:\ffmpeg\bin\ffmpeg.exe",
    linux_fallback="/usr/bin/ffmpeg",
)

FFPROBE = _resolve_bin(
    "ffprobe",
    win_fallback=r"C:\ffmpeg\bin\ffprobe.exe",
    linux_fallback="/usr/bin/ffprobe",
)

# =====================
# AZURE CONFIG
# =====================
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
AZURE_BLOB_CONTAINER = os.getenv("AZURE_BLOB_CONTAINER", "videos")
SAS_EXP_MINUTES = int(os.getenv("AZURE_BLOB_SAS_EXP_MINUTES", "120"))


def _parse_account_from_conn_str(conn_str: str) -> dict:
    """Parse AccountName and AccountKey from Azure Storage connection string."""
    parts = conn_str.split(";")
    out = {"AccountName": None, "AccountKey": None}
    for p in parts:
        if p.startswith("AccountName="):
            out["AccountName"] = p.split("=", 1)[1]
        if p.startswith("AccountKey="):
            out["AccountKey"] = p.split("=", 1)[1]
    return out


def parse_blob_url(blob_url: str) -> dict:
    """Parse blob URL to extract container and blob path."""
    if "?" in blob_url:
        base_url, _ = blob_url.split("?", 1)
    else:
        base_url = blob_url
    parsed = urlparse(base_url)
    path_parts = parsed.path.lstrip("/").split("/", 1)
    container = path_parts[0] if path_parts else ""
    blob_path = unquote(path_parts[1]) if len(path_parts) > 1 else ""
    parent_path = "/".join(blob_path.split("/")[:-1]) if "/" in blob_path else ""
    return {
        "container": container,
        "blob_path": blob_path,
        "parent_path": parent_path,
    }


def get_video_resolution(video_path: str) -> tuple[int, int] | None:
    """
    Get video resolution (width, height) using ffprobe.
    Returns None if ffprobe fails.
    """
    if not FFPROBE:
        logger.warning("[COMPRESS] ffprobe not found, cannot detect resolution")
        return None

    try:
        cmd = [
            FFPROBE,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0:s=x",
            video_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            logger.warning("[COMPRESS] ffprobe failed: %s", result.stderr)
            return None

        output = result.stdout.strip()
        if "x" in output:
            w, h = output.split("x")
            return int(w), int(h)
        return None
    except Exception as e:
        logger.warning("[COMPRESS] ffprobe error: %s", e)
        return None


def get_video_duration(video_path: str) -> float | None:
    """
    Get video duration in seconds using ffprobe.
    Returns None if ffprobe fails.
    """
    if not FFPROBE:
        return None

    try:
        cmd = [
            FFPROBE,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return None
        return float(result.stdout.strip())
    except Exception:
        return None


def needs_compression(video_path: str) -> bool:
    """
    Check if video needs 1080p compression.
    Returns True if:
    - Resolution is higher than 1080p (height > 1080)
    - File size is larger than 2GB (likely uncompressed or high bitrate)
    """
    file_size = os.path.getsize(video_path)
    file_size_gb = file_size / (1024 ** 3)

    resolution = get_video_resolution(video_path)

    if resolution:
        width, height = resolution
        logger.info(
            "[COMPRESS] Video info: %dx%d, %.2f GB",
            width, height, file_size_gb,
        )
        # Compress if resolution > 1080p OR file is very large
        if height > 1080:
            logger.info("[COMPRESS] Resolution > 1080p → needs compression")
            return True
        if file_size_gb > 2.0:
            logger.info("[COMPRESS] File > 2GB at 1080p → needs compression for bitrate reduction")
            return True
        logger.info("[COMPRESS] Already ≤ 1080p and < 2GB → skip compression")
        return False
    else:
        # If we can't detect resolution, compress if file is large
        if file_size_gb > 2.0:
            logger.info("[COMPRESS] Cannot detect resolution, file > 2GB → compress")
            return True
        logger.info("[COMPRESS] Cannot detect resolution, file < 2GB → skip")
        return False


def generate_analysis_video(
    input_path: str,
    output_path: str | None = None,
    fps: int = 1,
    scale_width: int = 1280,
    crf: int = 28,
    preset: str = "veryfast",
    timeout: int | None = None,
) -> str | None:
    """
    Generate a lightweight analysis-only video for fast frame extraction.

    Reduces a raw 4 GB screen recording to ~80-200 MB by:
    - Dropping to *fps* frames per second (default 1 fps)
    - Scaling to *scale_width* px wide (default 1280)
    - Using high CRF (28) and veryfast preset
    - Stripping audio entirely (-an)

    Downstream frame extraction (``extract_frames``) runs 10x faster on
    this lightweight file compared to the original RAW video.

    Returns:
        Path to the analysis video, or None on failure.
    """
    if not FFMPEG:
        logger.error("[ANALYSIS_VIDEO] FFmpeg not found!")
        return None
    if not os.path.exists(input_path):
        logger.error("[ANALYSIS_VIDEO] Input not found: %s", input_path)
        return None
    if output_path is None:
        base, ext = os.path.splitext(input_path)
        output_path = f"{base}_analysis{ext}"

    original_size = os.path.getsize(input_path)
    logger.info(
        "[ANALYSIS_VIDEO] Generating analysis video: %s (%.2f GB) → fps=%d, w=%d, crf=%d",
        input_path, original_size / (1024 ** 3), fps, scale_width, crf,
    )

    cmd = [
        FFMPEG, "-y",
        "-i", input_path,
        "-vf", f"fps={fps},scale={scale_width}:-1",
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf),
        "-an",                  # no audio – analysis only
        "-movflags", "+faststart",
        output_path,
    ]

    try:
        logger.info("[ANALYSIS_VIDEO] FFmpeg command: %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            logger.error("[ANALYSIS_VIDEO] FFmpeg failed: %s", result.stderr[-2000:] if result.stderr else "")
            if os.path.exists(output_path):
                os.remove(output_path)
            return None
        if not os.path.exists(output_path):
            logger.error("[ANALYSIS_VIDEO] Output not created")
            return None

        analysis_size = os.path.getsize(output_path)
        ratio = (1 - analysis_size / original_size) * 100 if original_size > 0 else 0
        logger.info(
            "[ANALYSIS_VIDEO] SUCCESS: %.2f GB → %.2f MB (%.1f%% reduction)",
            original_size / (1024 ** 3),
            analysis_size / (1024 ** 2),
            ratio,
        )
        return output_path
    except subprocess.TimeoutExpired:
        logger.warning("[ANALYSIS_VIDEO] Timed out after %s seconds", timeout)
        if os.path.exists(output_path):
            os.remove(output_path)
        return None
    except Exception as e:
        logger.error("[ANALYSIS_VIDEO] Unexpected error: %s", e)
        if os.path.exists(output_path):
            os.remove(output_path)
        return None


def compress_to_1080p(
    input_path: str,
    output_path: str | None = None,
    crf: int = 23,
    preset: str = "veryfast",
) -> str | None:
    """
    Compress video to 1080p using FFmpeg.

    Uses H.264 (libx264) with:
    - Scale to 1080p height (maintain aspect ratio)
    - CRF 23 (good quality, reasonable file size)
    - veryfast preset (speed-optimised; ~3-5x faster than medium)
    - AAC audio at 128kbps

    Args:
        input_path: Path to input video file
        output_path: Path for compressed output (default: input_path with _1080p suffix)
        crf: Constant Rate Factor (18=high quality, 23=default, 28=smaller)
        preset: FFmpeg preset (ultrafast, fast, medium, slow)

    Returns:
        Path to compressed file, or None if compression failed
    """
    if not FFMPEG:
        logger.error("[COMPRESS] FFmpeg not found!")
        return None

    if not os.path.exists(input_path):
        logger.error("[COMPRESS] Input file not found: %s", input_path)
        return None

    if output_path is None:
        base, ext = os.path.splitext(input_path)
        output_path = f"{base}_1080p{ext}"

    original_size = os.path.getsize(input_path)
    logger.info(
        "[COMPRESS] Starting 1080p compression: %s (%.2f GB)",
        input_path,
        original_size / (1024 ** 3),
    )

    # Build FFmpeg command
    # -vf scale=-2:1080 → scale to 1080p height, width auto (divisible by 2)
    # If video is already ≤ 1080p, scale filter will be a no-op effectively,
    # but we still benefit from re-encoding at lower bitrate
    # Try NVENC (GPU) first, fallback to libx264 (CPU)
    use_nvenc = False
    try:
        probe = subprocess.run(
            [FFMPEG, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10,
        )
        if "h264_nvenc" in (probe.stdout or ""):
            use_nvenc = True
            logger.info("[COMPRESS] NVENC available → using GPU encoding")
    except Exception as _e:
        logger.debug(f"Suppressed: {_e}")

    if use_nvenc:
        cmd = [
            FFMPEG,
            "-y",
            "-hwaccel", "cuda",
            "-i", input_path,
            "-vf", "scale=-2:'min(1080,ih)'",
            "-c:v", "h264_nvenc",
            "-preset", "p4",          # NVENC preset (p1=fastest, p7=best quality)
            "-rc", "vbr",
            "-cq", str(crf),          # quality level (similar to CRF)
            "-b:v", "0",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            "-max_muxing_queue_size", "1024",
            output_path,
        ]
    else:
        logger.info("[COMPRESS] NVENC not available → using CPU encoding")
        cmd = [
            FFMPEG,
            "-y",
            "-i", input_path,
            "-vf", "scale=-2:'min(1080,ih)'",
            "-c:v", "libx264",
            "-preset", preset,
            "-crf", str(crf),
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            "-max_muxing_queue_size", "1024",
            output_path,
        ]

    try:
        # Dynamic timeout: scale with video duration (~1x realtime for CPU, min 1h, max 8h)
        _duration = get_video_duration(input_path) or 7200  # fallback 2h
        COMPRESS_TIMEOUT_SECONDS = max(3600, min(int(_duration * 1.0) + 1800, 8 * 3600))
        logger.info("[COMPRESS] FFmpeg command: %s", " ".join(cmd))
        logger.info("[COMPRESS] Timeout set to %d seconds (%.1f hours)", COMPRESS_TIMEOUT_SECONDS, COMPRESS_TIMEOUT_SECONDS / 3600)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=COMPRESS_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            logger.error("[COMPRESS] FFmpeg timed out after %d seconds!", COMPRESS_TIMEOUT_SECONDS)
            if os.path.exists(output_path):
                os.remove(output_path)
            return None

        if result.returncode != 0:
            logger.error("[COMPRESS] FFmpeg failed with code %d", result.returncode)
            logger.error("[COMPRESS] stderr: %s", result.stderr[-2000:] if result.stderr else "<empty>")
            if os.path.exists(output_path):
                os.remove(output_path)
            return None

        if not os.path.exists(output_path):
            logger.error("[COMPRESS] Output file not created")
            return None

        compressed_size = os.path.getsize(output_path)
        ratio = (1 - compressed_size / original_size) * 100 if original_size > 0 else 0

        logger.info(
            "[COMPRESS] SUCCESS: %.2f GB → %.2f GB (%.1f%% reduction)",
            original_size / (1024 ** 3),
            compressed_size / (1024 ** 3),
            ratio,
        )

        return output_path

    except Exception as e:
        logger.error("[COMPRESS] Unexpected error: %s", e)
        if os.path.exists(output_path):
            os.remove(output_path)
        return None


def upload_compressed_to_blob(local_path: str, blob_name: str) -> bool:
    """
    Upload compressed video to Azure Blob Storage, replacing the original.

    Uses azcopy for efficient upload of large files.

    Args:
        local_path: Path to local compressed file
        blob_name: Blob name in the container (e.g., 'user@email.com/video-id/video-id.mp4')

    Returns:
        True if upload succeeded, False otherwise
    """
    if not AZURE_STORAGE_CONNECTION_STRING:
        logger.error("[COMPRESS] AZURE_STORAGE_CONNECTION_STRING not set")
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

        file_size = os.path.getsize(local_path)
        logger.info(
            "[COMPRESS] Uploading compressed file to blob: %s (%.2f GB)",
            blob_name,
            file_size / (1024 ** 3),
        )

        # Try azcopy first (fastest for large files)
        azcopy = shutil.which("azcopy") or "/usr/local/bin/azcopy"
        try:
            subprocess.run(
                [azcopy, "copy", local_path, dest_url, "--overwrite=true"],
                check=True,
                capture_output=True,
                text=True,
            )
            logger.info("[COMPRESS] Upload via azcopy SUCCESS")
            return True
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            logger.warning("[COMPRESS] azcopy failed, falling back to SDK: %s", e)

        # Fallback: use Azure SDK for upload
        from azure.storage.blob import BlobServiceClient

        blob_service = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
        blob_client = blob_service.get_blob_client(
            container=AZURE_BLOB_CONTAINER,
            blob=blob_name,
        )

        with open(local_path, "rb") as f:
            blob_client.upload_blob(
                f,
                overwrite=True,
                max_concurrency=4,
                blob_type="BlockBlob",
            )

        logger.info("[COMPRESS] Upload via SDK SUCCESS")
        return True

    except Exception as e:
        logger.error("[COMPRESS] Upload failed: %s", e)
        return False


def compress_and_replace(
    video_path: str,
    blob_url: str | None = None,
    crf: int = 23,
    preset: str = "veryfast",
) -> str:
    """
    Main entry point: compress video to 1080p and optionally replace in Blob Storage.

    This function:
    1. Checks if compression is needed (resolution > 1080p or file > 2GB)
    2. Compresses to 1080p using FFmpeg
    3. Uploads compressed version to Blob Storage (replacing original)
    4. Replaces local file with compressed version
    5. Returns path to the (possibly compressed) video

    Args:
        video_path: Path to local video file
        blob_url: Original blob URL (to determine blob name for upload)
        crf: FFmpeg CRF value
        preset: FFmpeg preset

    Returns:
        Path to the video file to use for processing (compressed or original)
    """
    if not needs_compression(video_path):
        logger.info("[COMPRESS] Compression not needed, using original")
        return video_path

    # Compress
    compressed_path = compress_to_1080p(video_path, crf=crf, preset=preset)
    if compressed_path is None:
        logger.warning("[COMPRESS] Compression failed, falling back to original")
        return video_path

    # Upload compressed version to Blob Storage
    if blob_url:
        blob_info = parse_blob_url(blob_url)
        blob_name = blob_info.get("blob_path", "")
        if blob_name:
            upload_ok = upload_compressed_to_blob(compressed_path, blob_name)
            if upload_ok:
                logger.info("[COMPRESS] Compressed file uploaded to blob: %s", blob_name)
            else:
                logger.warning("[COMPRESS] Failed to upload compressed file to blob")

    # Replace local file with compressed version
    try:
        os.remove(video_path)
        os.rename(compressed_path, video_path)
        logger.info("[COMPRESS] Local file replaced with compressed version")
    except Exception as e:
        logger.warning("[COMPRESS] Failed to replace local file: %s", e)
        # If rename fails, try to use the compressed file directly
        if os.path.exists(compressed_path):
            return compressed_path

    return video_path
