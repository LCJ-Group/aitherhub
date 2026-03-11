"""Video splitting utilities.

This module provides a placeholder function to split a processed video
into segments. The actual splitting logic is intentionally left empty
so you can implement it later.

Usage:
    from split_video import split_video_into_segments
    split_video_into_segments(video_id="...", video_url="https://...", video_path="/tmp/.../video.mp4")
"""
from typing import Optional
import logging
import os
import subprocess
import shutil
from datetime import datetime
from urllib.parse import urlparse, unquote
from dotenv import load_dotenv

from db_ops import load_video_phases_sync

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

# (No local log files) rely on central logger instead

# Output directory for split video segments (local temp)
SPLIT_VIDEO_DIR = os.path.join(os.path.dirname(__file__), "splitvideo")

# Azure Storage config
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
AZURE_BLOB_CONTAINER = os.getenv("AZURE_BLOB_CONTAINER", "videos")
SAS_EXP_MINUTES = int(os.getenv("AZURE_BLOB_SAS_EXP_MINUTES", "60"))


def _parse_account_from_conn_str(conn_str: str) -> dict:
    """Extract AccountName and AccountKey from connection string."""
    parts = conn_str.split(";")
    out = {"AccountName": None, "AccountKey": None}
    for p in parts:
        if p.startswith("AccountName="):
            out["AccountName"] = p.split("=", 1)[1]
        if p.startswith("AccountKey="):
            out["AccountKey"] = p.split("=", 1)[1]
    return out


def parse_blob_url(blob_url: str) -> dict:
    """Parse Azure blob URL to extract account, container, and blob path.
    
    Args:
        blob_url: Full Azure blob URL with or without SAS token.
            e.g. https://account.blob.core.windows.net/container/path/to/video.mp4?sas_token
    
    Returns:
        dict with keys: account_url, container, blob_path, parent_path, sas_token
    """
    # Split URL and query string (SAS token)
    if "?" in blob_url:
        base_url, sas_token = blob_url.split("?", 1)
    else:
        base_url = blob_url
        sas_token = ""

    parsed = urlparse(base_url)
    # parsed.netloc = "account.blob.core.windows.net"
    # parsed.path = "/container/path/to/video.mp4"

    account_url = f"{parsed.scheme}://{parsed.netloc}"
    
    # Remove leading slash and split into container + blob_path
    path_parts = parsed.path.lstrip("/").split("/", 1)
    container = path_parts[0] if path_parts else ""
    blob_path = unquote(path_parts[1]) if len(path_parts) > 1 else ""
    
    # Get parent folder path (without filename)
    parent_path = "/".join(blob_path.split("/")[:-1]) if "/" in blob_path else ""

    return {
        "account_url": account_url,
        "container": container,
        "blob_path": blob_path,
        "parent_path": parent_path,
        "sas_token": sas_token,
    }


def upload_to_blob(local_path: str, blob_name: str) -> str | None:
    """Upload a local file to Azure blob storage using Azure SDK.
    
    Args:
        local_path: Path to local file.
        blob_name: Destination blob name (path within container).
    
    Returns:
        Blob URL if successful, None otherwise.
    """
    if not AZURE_STORAGE_CONNECTION_STRING:
        logger.error("AZURE_STORAGE_CONNECTION_STRING not set")
        return None

    # Try azcopy first for faster upload if available
    try:
        conn = _parse_account_from_conn_str(AZURE_STORAGE_CONNECTION_STRING)
        account_name = conn.get("AccountName")
        account_key = conn.get("AccountKey")

        if account_name and account_key:
            try:
                # generate a short-lived blob SAS for destination
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

                dest_url = f"https://{account_name}.blob.core.windows.net/{AZURE_BLOB_CONTAINER}/{blob_name}?{sas}"

                # Detect azcopy binary (allow override via AZCOPY_PATH)
                azcopy_path = os.getenv("AZCOPY_PATH") or shutil.which("azcopy") or "/usr/local/bin/azcopy"
                logger.info("Uploading with azcopy to %s (binary=%s)", dest_url, azcopy_path)

                try:
                    proc = subprocess.run(
                        [azcopy_path, "copy", local_path, dest_url, "--overwrite=true"],
                        capture_output=True,
                        text=True,
                        timeout=60*60
                    )

                    # Log azcopy output (no local files)
                    logger.debug("azcopy stdout: %s", proc.stdout)
                    logger.debug("azcopy stderr: %s", proc.stderr)
                    if proc.returncode == 0:
                        logger.info("AzCopy upload succeeded: %s", blob_name)
                        return dest_url.split("?", 1)[0]
                    else:
                        logger.warning("AzCopy failed (rc=%s) for %s", proc.returncode, blob_name)
                        # fall through to SDK fallback
                except FileNotFoundError:
                    logger.info("azcopy not found at %s, falling back to SDK upload", azcopy_path)
                except subprocess.TimeoutExpired as e:
                    logger.warning("azcopy timeout after %s seconds for %s", 60*60, blob_name)
                    # attempt to write partial output if available
                    logger.warning("azcopy timeout for %s: %s", blob_name, repr(e))
                except Exception as e:
                    logger.warning("azcopy upload attempt failed: %s, falling back to SDK", e)
            except FileNotFoundError:
                logger.info("azcopy not found, falling back to SDK upload")
            except subprocess.CalledProcessError as e:
                logger.warning("azcopy failed: %s, falling back to SDK", getattr(e, 'stderr', e))
            except Exception as e:
                logger.warning("azcopy upload attempt failed: %s, falling back to SDK", e)

    except Exception:
        logger.debug("Failed to attempt azcopy path, will use SDK upload")

    # Fallback: SDK upload (same as previous behavior)
    try:
        from azure.storage.blob import BlobServiceClient, ContentSettings

        blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
        blob_client = blob_service_client.get_blob_client(container=AZURE_BLOB_CONTAINER, blob=blob_name)

        with open(local_path, "rb") as f:
            blob_client.upload_blob(
                f,
                overwrite=True,
                content_settings=ContentSettings(content_type="video/mp4")
            )

        logger.info("Uploaded blob (SDK): %s", blob_name)
        return blob_client.url
    except Exception as e:
        logger.error("Upload failed (SDK): %s", e)
        return None


def cut_segment(input_path: str, out_path: str, start_sec: float, end_sec: float, crf: int = 23, preset: str = "fast") -> bool:
    """Cut a single segment from input video using ffmpeg.
    
    Args:
        input_path: Path to source video file.
        out_path: Path to output segment file.
        start_sec: Start time in seconds.
        end_sec: End time in seconds.
        crf: Constant Rate Factor for quality (lower = better, default 23).
        preset: Encoding preset (default "fast").
    
    Returns:
        True if successful, False otherwise.
    """
    duration = end_sec - start_sec
    if duration <= 0:
        logger.warning("Invalid duration: start=%s end=%s", start_sec, end_sec)
        return False

    tmp_path = out_path + ".tmp.mp4"

    # Try fast stream-copy (-ss before -i)
    fast_cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(start_sec),
        "-i", input_path,
        "-t", str(duration),
        "-c", "copy",
        "-movflags", "+faststart",
        tmp_path,
    ]

    # Fallback: place -ss after -i (slower but sometimes more compatible)
    slow_cmd = [
        "ffmpeg",
        "-y",
        "-i", input_path,
        "-ss", str(start_sec),
        "-t", str(duration),
        "-c", "copy",
        "-movflags", "+faststart",
        tmp_path,
    ]

    # No per-invocation ffmpeg log files; rely on logger

    try:
        proc = subprocess.run(fast_cmd, check=True, capture_output=True, text=True)
        os.replace(tmp_path, out_path)
        return True
    except FileNotFoundError:
        logger.exception("ffmpeg not found when running: %s", ' '.join(fast_cmd))
        return False
    except subprocess.CalledProcessError as e:
        logger.debug("ffmpeg fast copy stdout: %s", getattr(e, 'stdout', ''))
        logger.debug("ffmpeg fast copy stderr: %s", getattr(e, 'stderr', ''))

    try:
        proc2 = subprocess.run(slow_cmd, check=True, capture_output=True, text=True)
        os.replace(tmp_path, out_path)
        return True
    except subprocess.CalledProcessError as e2:
        logger.debug("ffmpeg slow copy stdout: %s", getattr(e2, 'stdout', ''))
        logger.debug("ffmpeg slow copy stderr: %s", getattr(e2, 'stderr', ''))
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception as _e:
                logger.debug(f"Suppressed: {_e}")
        return False


def split_video_into_segments(video_id: str, video_url: str, video_path: str | None = None) -> Optional[list]:
    """Split the given video into segments based on analysis/report.

    Args:
        video_id: Internal video identifier.
        video_url: URL to the video (may be a blob URL).
        video_path: Local filesystem path to the downloaded video (if available).

    Returns:
        Optional[list]: A list of segment metadata (start/end timestamps, paths),
        or None. Implementation is left as a TODO.

    Note: The function body is intentionally left unimplemented. Replace the
    body with your splitting logic (ffmpeg calls, upload artifacts, etc.).
    """
    logger.info(
        "split_video_into_segments called for video_id=%s url=%s path=%s",
        video_id,
        video_url,
        video_path,
    )

    # Create output directory for this video's segments
    out_dir = os.path.join(SPLIT_VIDEO_DIR, video_id)
    os.makedirs(out_dir, exist_ok=True)
    logger.info("Created output directory: %s", out_dir)

    # Load all video_phases rows for this video_id from the DB.
    try:
        phases = load_video_phases_sync(video_id)
        
        logger.info("Loaded %d video_phases for video_id=%s", len(phases), video_id)
    except Exception as e:
        logger.exception("Failed to load video_phases for %s: %s", video_id, e)
        return None

    # Check if we have a local video file to work with
    if not video_path or not os.path.exists(video_path):
        logger.error("No valid video_path provided or file not found: %s", video_path)
        return None

    # Parse blob URL to get the parent path for uploading segments
    blob_info = parse_blob_url(video_url)
    logger.info("Parsed blob URL: container=%s, parent_path=%s", blob_info["container"], blob_info["parent_path"])

    # Cut each phase into a separate video segment
    segments = []
    for phase in phases:
        time_start = phase.get("time_start")
        time_end = phase.get("time_end")

        if time_start is None or time_end is None:
            logger.warning("Skipping phase %s: missing time_start or time_end", phase.get("phase_index"))
            continue

        # Convert to float (seconds)
        start_sec = float(time_start)
        end_sec = float(time_end)

        # Build output filename: {time_start}_{time_end}.mp4
        out_filename = f"{time_start}_{time_end}.mp4"
        out_path = os.path.join(out_dir, out_filename)

        logger.info("Cutting segment: %s -> %s (%.2fs - %.2fs)", video_path, out_path, start_sec, end_sec)

        success = cut_segment(video_path, out_path, start_sec, end_sec)
        if success:
            # Build blob destination path: {parent_path}/reportvideo/{filename}
            if blob_info["parent_path"]:
                dest_blob_name = f"{blob_info['parent_path']}/reportvideo/{out_filename}"
            else:
                dest_blob_name = f"reportvideo/{out_filename}"

            logger.info("Uploading segment to blob: %s", dest_blob_name)
            blob_url = upload_to_blob(out_path, dest_blob_name)

            segments.append({
                "phase_index": phase.get("phase_index"),
                "time_start": time_start,
                "time_end": time_end,
                "output_path": out_path,
                "blob_url": blob_url,
                "uploaded": blob_url is not None,
            })

            if blob_url:
                logger.info("Segment uploaded: %s", dest_blob_name)
            else:
                logger.warning("Failed to upload segment: %s", out_filename)
        else:
            logger.warning("Failed to cut segment for phase %s", phase.get("phase_index"))

    logger.info("Split complete: %d/%d segments created", len(segments), len(phases))

    # Cleanup: remove splitvideo/{video_id} folder after upload
    try:
        import shutil
        shutil.rmtree(out_dir)
        logger.info("Cleaned up splitvideo folder: %s", out_dir)
    except Exception as e:
        logger.warning("Failed to cleanup splitvideo folder %s: %s", out_dir, e)

    return segments
