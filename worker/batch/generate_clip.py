"""
Generate TikTok-style clip from a video phase.

Steps:
1. Download source video from Azure Blob
2. Cut the specified segment (+ silence removal)
3. Extract audio and transcribe with Whisper (word-level timestamps)
4. Crop/resize to 9:16 vertical format + burn TikTok-style subtitles
5. Hook intro insertion (climax at start) — ML-scored clips only
5b. Sound effect auto-insertion (price/CTA/transition) — ML-scored clips only
6. Upload to Azure Blob
7. Update DB with clip URL + captions
8. Auto-enrich clip metadata (Clip DB)

Usage:
    python generate_clip.py \
        --clip-id <uuid> \
        --video-id <uuid> \
        --blob-url <sas_url> \
        --time-start 52.0 \
        --time-end 85.0
"""

import os
import sys
import json
import re
import random
import argparse
import logging
import resource
import subprocess
import tempfile
import time
import requests
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Memory guard for FFmpeg child processes
# ---------------------------------------------------------------------------
# FFmpeg can consume unbounded memory when processing long/high-res videos.
# On a 28GB VM, a single FFmpeg process once consumed 17GB, causing OOM that
# killed the entire VM. This helper sets RLIMIT_AS (virtual address space)
# on child processes so they get killed instead of crashing the VM.
#
# Default limit: 8GB per FFmpeg process. The systemd cgroup limit is 14GB
# for the entire worker, so 8GB per child leaves headroom for Python + I/O.
# ---------------------------------------------------------------------------
_FFMPEG_MEM_LIMIT_BYTES = int(os.getenv("FFMPEG_MEM_LIMIT_GB", "8")) * 1024 * 1024 * 1024


def _limit_ffmpeg_memory():
    """preexec_fn for subprocess: set virtual memory limit on child process."""
    try:
        resource.setrlimit(resource.RLIMIT_AS, (_FFMPEG_MEM_LIMIT_BYTES, _FFMPEG_MEM_LIMIT_BYTES))
    except (ValueError, OSError):
        pass  # non-fatal: some environments don't support RLIMIT_AS

# Load environment variables
project_root = Path(__file__).parent.parent.parent
load_dotenv(project_root / ".env")
load_dotenv()

# Setup logging
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "generate_clip.log"), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("generate_clip")

# Add batch dir to path
BATCH_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BATCH_DIR)

from db_ops import init_db_sync, close_db_sync, get_event_loop, get_session
from split_video import upload_to_blob, parse_blob_url
from sqlalchemy import text

# Environment
WHISPER_ENDPOINT = os.getenv("WHISPER_ENDPOINT")
AZURE_KEY = os.getenv("AZURE_OPENAI_KEY")
FFMPEG_BIN = os.getenv("FFMPEG_PATH", "ffmpeg")

# OpenAI client for GPT-4o subtitle post-processing
try:
    from openai import OpenAI
    _openai_api_key = os.getenv("OPENAI_API_KEY") or os.getenv("AZURE_OPENAI_KEY") or ""
    if _openai_api_key and not _openai_api_key.startswith("your-"):
        _openai_client = OpenAI(api_key=_openai_api_key)
        logger.info("OpenAI client initialized for subtitle refinement")
    else:
        logger.warning("No OPENAI_API_KEY or AZURE_OPENAI_KEY found, GPT refinement disabled")
        _openai_client = None
except Exception as e:
    logger.warning(f"OpenAI client init failed: {e}")
    _openai_client = None

# Font configuration – Noto Sans CJK JP (installed via fonts-noto-cjk package)
JP_FONT_DIR = "/usr/share/fonts/opentype/noto"
JP_FONT_FILE = os.path.join(JP_FONT_DIR, "NotoSansCJK-Black.ttc")
JP_FONT_NAME = "Noto Sans CJK JP Black"  # Name as registered in fontconfig

# Japanese filler words to remove from subtitles
FILLER_WORDS = {
    "えー", "えーと", "えっと", "えーっと",
    "あー", "あのー", "あの", "あのね",
    "うー", "うーん", "うん", "んー", "ん",
    "まあ", "まぁ", "まー",
    "そのー", "その",
    "なんか", "なんかね",
    "ほら", "ほらね",
    "ねー", "ねえ",
    "こう", "こうね",
}

# TikTok subtitle styles – randomly selected per clip (large font, reference-matched sizing)
SUBTITLE_STYLES = [
    {
        "name": "bold_white",
        "fontsize": 72,
        "fontcolor": "white",
        "highlight_color": "#FFD700",  # Gold highlight for karaoke
        "borderw": 6,
        "bordercolor": "black",
        "shadowx": 2,
        "shadowy": 2,
        "shadowcolor": "black@0.5",
    },
    {
        "name": "yellow_pop",
        "fontsize": 74,
        "fontcolor": "#FFFFFF",
        "highlight_color": "#FFFF00",  # Yellow highlight
        "borderw": 6,
        "bordercolor": "black",
        "shadowx": 3,
        "shadowy": 3,
        "shadowcolor": "black@0.6",
    },
    {
        "name": "cyan_glow",
        "fontsize": 70,
        "fontcolor": "white",
        "highlight_color": "#00FFFF",  # Cyan highlight
        "borderw": 6,
        "bordercolor": "#003333",
        "shadowx": 2,
        "shadowy": 2,
        "shadowcolor": "#006666@0.5",
    },
    {
        "name": "pink_bold",
        "fontsize": 72,
        "fontcolor": "white",
        "highlight_color": "#FF69B4",  # Pink highlight
        "borderw": 6,
        "bordercolor": "black",
        "shadowx": 2,
        "shadowy": 2,
        "shadowcolor": "black@0.5",
    },
    {
        "name": "white_pink_outline",
        "fontsize": 72,
        "fontcolor": "white",
        "highlight_color": "#FF6B9D",  # Pink highlight
        "borderw": 6,
        "bordercolor": "black",
        "shadowx": 0,
        "shadowy": 0,
        "shadowcolor": "black@0.0",
    },
]


# =========================
# DB helpers
# =========================

def update_clip_progress(clip_id: str, progress_pct: int, progress_step: str, log_message: str = None, preview_url: str = None):
    """Update clip generation progress in database.
    
    If log_message is provided, it is appended to the processing_logs JSONB array
    so the frontend can display a real-time AI processing log panel.
    Falls back to basic progress update if processing_logs column doesn't exist yet.
    """
    loop = get_event_loop()
    async def _update():
        if log_message:
            # Try to append a structured log entry to processing_logs JSONB array
            try:
                async with get_session() as session:
                    sql = text("""
                        UPDATE video_clips
                        SET progress_pct = :pct,
                            progress_step = :step,
                            processing_logs = COALESCE(processing_logs, CAST('[]' AS jsonb)) || CAST(:log_entry AS jsonb),
                            updated_at = NOW()
                        WHERE id = :clip_id
                    """)
                    import json as _json
                    _entry = {
                        "ts": datetime.now().strftime("%H:%M:%S"),
                        "pct": progress_pct,
                        "step": progress_step,
                        "msg": log_message,
                    }
                    if preview_url:
                        _entry["preview_url"] = preview_url
                    log_entry = _json.dumps(_entry)
                    await session.execute(sql, {
                        "pct": progress_pct, "step": progress_step,
                        "log_entry": log_entry, "clip_id": clip_id,
                    })
            except Exception:
                # Fallback with NEW session: processing_logs column may not exist yet
                async with get_session() as session2:
                    sql = text("""
                        UPDATE video_clips
                        SET progress_pct = :pct, progress_step = :step, updated_at = NOW()
                        WHERE id = :clip_id
                    """)
                    await session2.execute(sql, {"pct": progress_pct, "step": progress_step, "clip_id": clip_id})
        else:
            async with get_session() as session:
                sql = text("""
                    UPDATE video_clips
                    SET progress_pct = :pct, progress_step = :step, updated_at = NOW()
                    WHERE id = :clip_id
                """)
                await session.execute(sql, {"pct": progress_pct, "step": progress_step, "clip_id": clip_id})
    loop.run_until_complete(_update())


def update_clip_status(clip_id: str, status: str, clip_url: str = None, error_message: str = None, captions: list = None):
    """Update clip status in database."""
    loop = get_event_loop()
    async def _update():
        async with get_session() as session:
            if clip_url:
                sql = text("""
                    UPDATE video_clips
                    SET status = :status, clip_url = :clip_url, progress_pct = 100, progress_step = 'completed', updated_at = NOW()
                    WHERE id = :clip_id
                """)
                await session.execute(sql, {"status": status, "clip_url": clip_url, "clip_id": clip_id})
            elif error_message:
                sql = text("""
                    UPDATE video_clips
                    SET status = :status, error_message = :error_message, progress_step = 'error', updated_at = NOW()
                    WHERE id = :clip_id
                """)
                await session.execute(sql, {"status": status, "error_message": error_message, "clip_id": clip_id})
            else:
                sql = text("""
                    UPDATE video_clips
                    SET status = :status, updated_at = NOW()
                    WHERE id = :clip_id
                """)
                await session.execute(sql, {"status": status, "clip_id": clip_id})
            # Save captions (subtitle data) to DB
            if captions is not None:
                import json as _json
                captions_sql = text("""
                    UPDATE video_clips
                    SET captions = CAST(:captions_json AS jsonb), updated_at = NOW()
                    WHERE id = :clip_id
                """)
                await session.execute(captions_sql, {
                    "captions_json": _json.dumps(captions, ensure_ascii=False),
                    "clip_id": clip_id,
                })

    loop.run_until_complete(_update())


# =========================
# Intermediate preview upload (for real-time monitor)
# =========================

def _upload_intermediate_preview(local_path: str, clip_id: str, step_name: str) -> str | None:
    """Upload an intermediate video file to Azure Blob for real-time preview.
    
    Returns the blob URL WITH read SAS token if successful, None otherwise.
    Non-fatal: errors are logged but do not stop the pipeline.
    """
    try:
        if not os.path.exists(local_path) or os.path.getsize(local_path) == 0:
            return None
        blob_name = f"clip-previews/{clip_id}/{step_name}.mp4"
        url = upload_to_blob(local_path, blob_name)
        if url:
            # Generate a read-only SAS token so the frontend can play the video
            try:
                from split_video import AZURE_STORAGE_CONNECTION_STRING, AZURE_BLOB_CONTAINER, _parse_account_from_conn_str
                from azure.storage.blob import generate_blob_sas, BlobSasPermissions
                from datetime import datetime, timedelta
                conn_info = _parse_account_from_conn_str(AZURE_STORAGE_CONNECTION_STRING)
                account_name = conn_info.get("AccountName")
                account_key = conn_info.get("AccountKey")
                if account_name and account_key:
                    sas = generate_blob_sas(
                        account_name=account_name,
                        container_name=AZURE_BLOB_CONTAINER,
                        blob_name=blob_name,
                        account_key=account_key,
                        permission=BlobSasPermissions(read=True),
                        expiry=datetime.utcnow() + timedelta(hours=24),
                    )
                    url = f"{url}?{sas}"
            except Exception as sas_err:
                logger.warning(f"[PREVIEW] Failed to generate SAS for {step_name} (non-fatal): {sas_err}")
            logger.info(f"[PREVIEW] Uploaded intermediate preview: {step_name} -> {url[:80]}...")
        return url
    except Exception as e:
        logger.warning(f"[PREVIEW] Failed to upload {step_name} preview (non-fatal): {e}")
        return None


# =========================
# Download
# =========================

def download_video(blob_url: str, dest_path: str):
    """Download video from Azure Blob."""
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    logger.info(f"Downloading video to {dest_path}")

    # Try azcopy first
    try:
        azcopy_path = os.getenv("AZCOPY_PATH") or "/usr/local/bin/azcopy"
        result = subprocess.run(
            [azcopy_path, "copy", blob_url, dest_path, "--overwrite=true"],
            check=True, capture_output=True, text=True, timeout=1800
        )
        logger.info("AzCopy download succeeded")
        return
    except Exception as e:
        logger.info(f"AzCopy failed, falling back to requests: {e}")

    # Fallback to requests
    with requests.get(blob_url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8 * 1024 * 1024):
                if chunk:
                    f.write(chunk)
    logger.info("Download completed via requests")


# =========================
# Cut segment
# =========================

def _get_video_duration_sec(path: str) -> float | None:
    """Return the duration (seconds) of a video file via ffprobe, or None on failure."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        val = result.stdout.strip()
        return float(val) if val else None
    except Exception:
        return None


def cut_segment(input_path: str, output_path: str, start_sec: float, end_sec: float) -> bool:
    """Cut a segment from the video with audio.

    Strategy (2026-03 v2 revision):
    - First try ``-c copy`` (stream copy) for near-instant cutting without
      re-encoding.  This preserves original quality (1080p) and finishes in
      seconds instead of minutes.
    - If stream-copy produces a duration deviation > 3s (due to keyframe
      alignment), fall back to re-encode with ``-preset veryfast``.
    - Use ``-ss BEFORE -i`` for fast seeking in all modes.
    """
    duration = end_sec - start_sec
    if duration <= 0:
        logger.error(
            f"[CUT_SEGMENT] Invalid range: start={start_sec:.2f}s end={end_sec:.2f}s "
            f"(duration={duration:.2f}s)"
        )
        return False

    logger.info(
        f"[CUT_SEGMENT] Cutting {start_sec:.2f}s - {end_sec:.2f}s "
        f"(requested duration={duration:.2f}s) from {input_path}"
    )

    success = False

    # ---- Phase 1: Stream copy (near-instant, 1080p preserved) ----
    cmd_copy = [
        FFMPEG_BIN, "-y",
        "-ss", f"{start_sec:.3f}",
        "-i", input_path,
        "-t", f"{duration:.3f}",
        "-c", "copy",
        "-movflags", "+faststart",
        "-avoid_negative_ts", "make_zero",
        output_path,
    ]
    try:
        subprocess.run(cmd_copy, check=True, capture_output=True, text=True, timeout=120)
        # Verify duration
        copy_dur = _get_video_duration_sec(output_path)
        if copy_dur is not None and abs(copy_dur - duration) <= 3.0:
            success = True
            logger.info(
                f"[CUT_SEGMENT] Stream-copy cut succeeded "
                f"(actual={copy_dur:.2f}s, deviation={abs(copy_dur - duration):.2f}s)"
            )
        elif copy_dur is not None:
            logger.warning(
                f"[CUT_SEGMENT] Stream-copy duration deviation too large: "
                f"requested={duration:.2f}s actual={copy_dur:.2f}s, will re-encode"
            )
        else:
            logger.warning("[CUT_SEGMENT] Stream-copy: could not verify duration, will re-encode")
    except subprocess.CalledProcessError as e:
        logger.warning(
            f"[CUT_SEGMENT] Stream-copy failed: {e.stderr[-300:] if e.stderr else e}"
        )
    except subprocess.TimeoutExpired:
        logger.warning(f"[CUT_SEGMENT] Stream-copy timed out")

    # ---- Phase 2: Re-encode with veryfast preset (accurate) ----
    if not success:
        logger.info("[CUT_SEGMENT] Falling back to re-encode (veryfast)")
        cmd = [
            FFMPEG_BIN, "-y",
            "-ss", f"{start_sec:.3f}",
            "-accurate_seek",
            "-i", input_path,
            "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            output_path,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=1800)
            success = True
            logger.info(f"[CUT_SEGMENT] Re-encode cut succeeded")
        except subprocess.CalledProcessError as e:
            logger.error(
                f"[CUT_SEGMENT] Re-encode cut failed (start={start_sec:.2f}s): "
                f"{e.stderr[-500:] if e.stderr else e}"
            )
        except subprocess.TimeoutExpired:
            logger.error(f"[CUT_SEGMENT] Re-encode cut timed out after 600s (start={start_sec:.2f}s)")

    if not success:
        # Final fallback: keyframe seek without accurate_seek
        logger.warning(f"[CUT_SEGMENT] Trying final fallback (keyframe seek, veryfast)")
        cmd_fallback = [
            FFMPEG_BIN, "-y",
            "-ss", f"{start_sec:.3f}",
            "-i", input_path,
            "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            output_path,
        ]
        try:
            subprocess.run(cmd_fallback, check=True, capture_output=True, text=True, timeout=1800)
            success = True
            logger.info(f"[CUT_SEGMENT] Final fallback cut succeeded")
        except Exception as e2:
            logger.error(f"[CUT_SEGMENT] Final fallback cut also failed: {e2}")
            return False

    # Post-cut verification
    actual_dur = _get_video_duration_sec(output_path)
    if actual_dur is not None:
        deviation = abs(actual_dur - duration)
        if deviation > 2.0:
            logger.warning(
                f"[CUT_SEGMENT] Duration mismatch! requested={duration:.2f}s "
                f"actual={actual_dur:.2f}s deviation={deviation:.2f}s "
                f"(start={start_sec:.2f}s end={end_sec:.2f}s)"
            )
        else:
            logger.info(
                f"[CUT_SEGMENT] Duration OK: requested={duration:.2f}s actual={actual_dur:.2f}s"
            )
    else:
        logger.warning(f"[CUT_SEGMENT] Could not verify output duration")

    return success


# =========================
# Speech-Aware Cut
# =========================

# Japanese sentence-ending patterns for boundary detection
_JP_SENTENCE_END_RE = re.compile(
    r'[。！？!?…]$'
    r'|ます$|です$|ました$|でした$|ません$|ください$'
    r'|よね$|だよ$|だね$|かな$|よ$|ね$|な$|わ$|さ$'
    r'|って$|けど$|から$|ので$|のに$|ても$|ても$'
)

# Pause threshold (seconds) – a gap this long between words suggests a natural break
_PAUSE_THRESHOLD = 0.4


def _find_speech_boundary(words: list, target_sec: float, search_window: float = 3.0, prefer: str = "after") -> float:
    """
    Find the best speech boundary near *target_sec* within ±search_window.

    Strategy (priority order):
      1. Sentence-ending word whose *end* is closest to target_sec
      2. Long pause (>= _PAUSE_THRESHOLD) between consecutive words
      3. Any word boundary closest to target_sec
      4. Original target_sec (no adjustment)

    Parameters
    ----------
    words : list[dict]
        Word-level timestamps [{"word": str, "start": float, "end": float}, ...]
    target_sec : float
        The original cut point (in clip-local seconds).
    search_window : float
        How many seconds before/after target_sec to search.
    prefer : str
        "after" = prefer boundaries >= target_sec (for end cuts)
        "before" = prefer boundaries <= target_sec (for start cuts)
    """
    if not words:
        return target_sec

    lo = target_sec - search_window
    hi = target_sec + search_window

    # Collect candidate boundaries in the window
    sentence_ends: list[float] = []
    pause_points: list[float] = []
    word_ends: list[float] = []

    for i, w in enumerate(words):
        w_end = w.get("end", 0)
        if not (lo <= w_end <= hi):
            continue

        word_ends.append(w_end)

        # Check sentence-ending pattern
        text = w.get("word", "").strip()
        if text and _JP_SENTENCE_END_RE.search(text):
            sentence_ends.append(w_end)

        # Check pause after this word
        if i + 1 < len(words):
            next_start = words[i + 1].get("start", 0)
            gap = next_start - w_end
            if gap >= _PAUSE_THRESHOLD:
                pause_points.append(w_end)

    def _best(candidates: list[float]) -> float | None:
        if not candidates:
            return None
        if prefer == "after":
            after = [c for c in candidates if c >= target_sec]
            if after:
                return min(after, key=lambda c: abs(c - target_sec))
        elif prefer == "before":
            before = [c for c in candidates if c <= target_sec]
            if before:
                return min(before, key=lambda c: abs(c - target_sec))
        return min(candidates, key=lambda c: abs(c - target_sec))

    # Priority 1: sentence end
    best = _best(sentence_ends)
    if best is not None:
        return best

    # Priority 2: pause
    best = _best(pause_points)
    if best is not None:
        return best

    # Priority 3: any word boundary
    best = _best(word_ends)
    if best is not None:
        return best

    return target_sec


def adjust_cut_to_speech_boundary(
    source_path: str,
    original_start: float,
    original_end: float,
    search_window: float = 3.0,
) -> tuple[float, float]:
    """
    Pre-transcribe a slightly wider region around the requested clip and
    snap start/end to natural speech boundaries.

    Returns (adjusted_start, adjusted_end).
    """
    # Widen the region by search_window on each side for analysis
    analysis_start = max(0.0, original_start - search_window)
    analysis_end = original_end + search_window

    # Extract audio for the wider region
    work_dir = tempfile.mkdtemp(prefix="speech_boundary_")
    try:
        # Cut wider segment
        wider_path = os.path.join(work_dir, "wider.mp4")
        duration = analysis_end - analysis_start
        cmd = [
            FFMPEG_BIN, "-y",
            "-ss", f"{analysis_start:.3f}",
            "-accurate_seek",
            "-i", source_path,
            "-t", f"{duration:.3f}",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "64k",
            wider_path,
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        # Extract audio
        audio_path = os.path.join(work_dir, "boundary_audio.wav")
        if not extract_audio(wider_path, audio_path):
            logger.warning("[SPEECH_CUT] Audio extraction failed, using original boundaries")
            return original_start, original_end

        # Transcribe to get word-level timestamps
        segments = transcribe_audio(audio_path)
        if not segments:
            logger.warning("[SPEECH_CUT] No transcript, using original boundaries")
            return original_start, original_end

        # Collect all words (timestamps are relative to analysis_start)
        all_words = []
        for seg in segments:
            for w in seg.get("words", []):
                all_words.append({
                    "word": w["word"],
                    "start": w["start"] + analysis_start,  # Convert to full-video time
                    "end": w["end"] + analysis_start,
                })

        if not all_words:
            # Fallback: use segment-level boundaries
            for seg in segments:
                all_words.append({
                    "word": seg.get("text", ""),
                    "start": seg["start"] + analysis_start,
                    "end": seg["end"] + analysis_start,
                })

        if not all_words:
            return original_start, original_end

        # Adjust start: find a boundary BEFORE or AT original_start
        adj_start = _find_speech_boundary(
            all_words, original_start, search_window=search_window, prefer="before"
        )
        # Adjust end: find a boundary AFTER or AT original_end
        adj_end = _find_speech_boundary(
            all_words, original_end, search_window=search_window, prefer="after"
        )

        # Sanity checks
        if adj_end <= adj_start:
            logger.warning("[SPEECH_CUT] Adjusted end <= start, using originals")
            return original_start, original_end

        # Don't let the clip grow by more than 2× search_window total
        max_growth = search_window * 2
        original_dur = original_end - original_start
        adjusted_dur = adj_end - adj_start
        if adjusted_dur > original_dur + max_growth:
            logger.warning(f"[SPEECH_CUT] Clip grew too much ({adjusted_dur:.1f}s vs {original_dur:.1f}s), clamping")
            adj_end = adj_start + original_dur + max_growth

        logger.info(
            f"[SPEECH_CUT] Adjusted: {original_start:.2f}-{original_end:.2f} → "
            f"{adj_start:.2f}-{adj_end:.2f} (delta_start={adj_start - original_start:+.2f}s, "
            f"delta_end={adj_end - original_end:+.2f}s)"
        )
        return adj_start, adj_end

    except Exception as e:
        logger.warning(f"[SPEECH_CUT] Failed: {e}, using original boundaries")
        return original_start, original_end
    finally:
        import shutil
        shutil.rmtree(work_dir, ignore_errors=True)


# =========================
# Transcribe with Whisper
# =========================

def extract_audio(video_path: str, audio_path: str) -> bool:
    """Extract audio from video as WAV."""
    cmd = [
        FFMPEG_BIN, "-y",
        "-i", video_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        audio_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120)
        return True
    except Exception as e:
        logger.error(f"Failed to extract audio: {e}")
        return False


def transcribe_audio(audio_path: str, subtitle_language: str = "ja") -> list:
    """Transcribe audio using Azure Whisper API.
    
    Args:
        audio_path: Path to audio file
        subtitle_language: Target subtitle language ('ja', 'zh-TW', 'auto')
            - 'ja': Force Japanese recognition
            - 'zh-TW': Force Chinese recognition (will be converted to Traditional Chinese later)
            - 'auto': Let Whisper auto-detect the language
    
    Returns list of segments with word-level timestamps for karaoke effect.
    Each segment has: start, end, text, words (list of {word, start, end})
    """
    if not WHISPER_ENDPOINT or not AZURE_KEY:
        logger.warning("Whisper endpoint not configured, skipping transcription")
        return []

    with open(audio_path, "rb") as f:
        audio_data = f.read()

    # Language-specific Whisper prompts and settings
    whisper_lang_map = {
        'ja': 'ja',
        'zh-TW': 'zh',
        'auto': None,  # Let Whisper auto-detect
    }
    whisper_lang = whisper_lang_map.get(subtitle_language, 'ja')
    
    # Japanese prompt to improve Whisper recognition accuracy
    whisper_prompt_ja = (
        "ライブ配信、ライブコマース、商品紹介、視聴者、コメント、"
        "購入、カート、セット、限定、在庫、価格、お得、割引、"
        "ありがとうございます、よろしくお願いします、"
        "こんにちは、こんばんは、お疲れ様です、"
        "京極琉、KYOGOKU、シャンプー、トリートメント、カラー、"
        "ブリーチ、ヘアケア、美容、サロン、髪、頭皮、"
        "NMN、RENOVATIO、レノバティオ、"
        "コラーゲン、ヒアルロン酸、美容液、クリーム、"
        "送料無料、ポイント、クーポン、タイムセール、"
        "めっちゃ、すごい、やばい、マジで、本当に、"
        "円、個、本、セット、パック"
    )
    # Chinese prompt for beauty/cosmetics context
    whisper_prompt_zh = (
        "直播、直播帶貨、商品介紹、觀眾、留言、"
        "購買、購物車、套裝、限定、庫存、價格、優惠、折扣、"
        "謝謝、請多多關照、"
        "你好、晚上好、辛苦了、"
        "京極琉、KYOGOKU、洗髮精、護髮素、染髮、"
        "漂髮、護髮、美容、沙龍、頭髮、頭皮、"
        "NMN、RENOVATIO、"
        "膠原蛋白、玻尿酸、精華液、乳霜、"
        "免運費、點數、優惠券、限時特賣"
    )
    
    if subtitle_language == 'zh-TW':
        whisper_prompt = whisper_prompt_zh
    elif subtitle_language == 'auto':
        # For auto-detect, provide minimal prompt to avoid biasing language detection
        whisper_prompt = "KYOGOKU, RENOVATIO, NMN"
    else:
        whisper_prompt = whisper_prompt_ja
    
    # Enhance Whisper prompt with dictionary terms (improves recognition accuracy)
    try:
        dict_entries = get_subtitle_dictionary()
        if dict_entries:
            dict_terms = []
            for entry in dict_entries:
                # Use the correct form (to_text) as Whisper hint
                term = (entry.get("to") or entry["from"]).strip()
                if term and term not in dict_terms:
                    dict_terms.append(term)
            if dict_terms:
                dict_prompt = "、".join(dict_terms[:30])  # Limit to 30 terms to avoid prompt overflow
                whisper_prompt = f"{whisper_prompt}、{dict_prompt}"
                logger.info(f"[TRANSCRIBE] Added {len(dict_terms[:30])} dictionary terms to Whisper prompt")
    except Exception as e:
        logger.warning(f"[TRANSCRIBE] Failed to add dictionary terms to Whisper prompt: {e}")

    logger.info(f"[TRANSCRIBE] subtitle_language={subtitle_language}, whisper_lang={whisper_lang}")

    for attempt in range(3):
        try:
            # Build files dict — omit language param for auto-detect
            files_dict = {
                "file": ("audio.wav", audio_data, "audio/wav"),
                "response_format": (None, "verbose_json"),
                "timestamp_granularities[]": (None, "word"),
                "temperature": (None, "0"),
                "task": (None, "transcribe"),
                "prompt": (None, whisper_prompt),
            }
            if whisper_lang is not None:
                files_dict["language"] = (None, whisper_lang)
            
            response = requests.post(
                WHISPER_ENDPOINT,
                headers={"api-key": AZURE_KEY},
                files=files_dict,
                timeout=120,
            )

            if response.status_code == 200:
                data = response.json()
                segments = []

                # Use word-level timestamps for karaoke-style subtitles
                words = data.get("words", [])
                if words:
                    # Group words into subtitle lines (max ~10 chars per line)
                    # Keep word-level timestamps for karaoke highlight effect
                    current_words = []
                    current_start = None
                    char_count = 0

                    for word in words:
                        w_text = word.get("word", "").strip()
                        if not w_text:
                            continue

                        # Skip filler words
                        if w_text in FILLER_WORDS:
                            logger.debug(f"Skipping filler word: {w_text}")
                            continue

                        w_start = word.get("start", 0)
                        w_end = word.get("end", 0)

                        if current_start is None:
                            current_start = w_start

                        current_words.append({
                            "word": w_text,
                            "start": w_start,
                            "end": w_end,
                        })
                        char_count += len(w_text)

                        # Break line at ~10 characters for readability
                        if char_count >= 10:
                            segments.append({
                                "start": current_start,
                                "end": w_end,
                                "text": "".join(w["word"] for w in current_words),
                                "words": current_words,
                            })
                            current_words = []
                            current_start = None
                            char_count = 0

                    # Remaining words
                    if current_words:
                        segments.append({
                            "start": current_start,
                            "end": current_words[-1]["end"],
                            "text": "".join(w["word"] for w in current_words),
                            "words": current_words,
                        })
                else:
                    # Fallback to segment-level timestamps (no word-level data)
                    for seg in data.get("segments", []):
                        segments.append({
                            "start": seg.get("start", 0),
                            "end": seg.get("end", 0),
                            "text": seg.get("text", "").strip(),
                            "words": [],
                        })

                logger.info(f"Transcribed {len(segments)} subtitle segments ({len(words)} words total)")
                return segments

            elif response.status_code == 429:
                wait_time = 5 * (attempt + 1)
                logger.warning(f"Rate limited, waiting {wait_time}s")
                time.sleep(wait_time)
            else:
                logger.error(f"Whisper API error: {response.status_code} {response.text[:200]}")

        except Exception as e:
            logger.error(f"Transcription attempt {attempt + 1} failed: {e}")
            time.sleep(3)

    return []


# =========================
# GPT-4o subtitle post-processing
# =========================

# ============================================================================
# PROTECTED ZONE — Subtitle-Audio Sync Engine
# DO NOT modify the functions below without thorough testing.
# These functions ensure subtitle timestamps stay synchronized with audio.
# Any changes to timestamp mapping logic MUST be verified with real clips.
# Protected functions:
#   - refine_subtitles_with_gpt()
#   - _find_best_match() (inner function)
#   - orig_char_timeline construction
#   - Subsequence matching & timestamp restoration logic
# Last verified: 2026-04-18 (commit 6db6d3b)
# ============================================================================
def refine_subtitles_with_gpt(segments: list, phase_context: str = "", product_names: list = None, subtitle_language: str = "ja", dict_entries: list = None) -> list:
    """
    Use GPT-4.1-mini to refine Whisper transcription for subtitles (multi-language).
    
    Improvements:
    - Fix misrecognized Japanese words using context + product name dictionary
    - Merge fragmented segments into natural sentence units
    - Remove filler words contextually
    - Add appropriate punctuation
    - Reconstruct word-level timestamps for karaoke effect
    
    PROTECTED: Timestamp restoration uses character-level subsequence matching
    against original Whisper word timestamps. Do not replace with position-ratio
    based mapping — that causes audio-subtitle desync.
    
    Returns refined segments with word-level timestamps preserved.
    """
    if not _openai_client or not segments:
        logger.info("GPT refinement skipped (no client or no segments)")
        return segments

    # Combine all segment texts with timestamps for context
    raw_lines = []
    for i, seg in enumerate(segments):
        raw_lines.append(f"[{seg['start']:.2f}-{seg['end']:.2f}] {seg['text']}")
    raw_text = "\n".join(raw_lines)

    # Build context sections (language-aware)
    if subtitle_language == 'zh-TW':
        context_section = ""
        if phase_context:
            context_section = f"""\n## 此階段的內容（參考資訊 - 用於修正商品名和專有名詞）
{phase_context}\n"""
        product_section = ""
        if product_names:
            product_section = f"""\n## 商品名辭典（此影片中出現的商品名 - 必須用於修正誤識別）
{', '.join(product_names)}
※ 如果Whisper誤識別，請修正為上述商品名\n"""
        # Build dictionary section for GPT prompt
        dict_section = ""
        if dict_entries:
            replacement_lines = []
            no_break_words = []
            for entry in dict_entries:
                if entry.get("to") and entry["to"] != entry["from"]:
                    replacement_lines.append(f"  - 「{entry['from']}」→「{entry['to']}」")
                if entry.get("no_break"):
                    word = entry.get("to") or entry["from"]
                    no_break_words.append(word)
            parts = []
            if replacement_lines:
                parts.append("置換規則（必須遵守 - Whisper誤識別時自動修正）:\n" + "\n".join(replacement_lines))
            if no_break_words:
                parts.append(f"分割禁止詞（這些詞不能在中間換行）: {', '.join(no_break_words)}")
            if parts:
                dict_section = "\n## 字幕自訂辭典（最優先 - 必須遵守）\n" + "\n".join(parts) + "\n"
        prompt = f"""你是繁體中文直播帶貨影片的TikTok/Reels病毒式字幕製作專家。
請將Whisper自動生成的字幕文字轉換為在社群媒體影片中最能引起關注的格式。
所有輸出必須使用繁體中文。
{context_section}{product_section}{dict_section}
## 修正規則（按優先順序）
1. **重複・片段文字的合併（最重要）**: 將重複的片段整合為一個
   - 理解前後文脈，合併為通順自然的一句話
   - 合併後，時間戳從第一個片段的start到最後一個片段的end
2. **誤識別修正**: 修正不自然的詞語和句子
   - 商品名・品牌名的誤識別（從上下文・商品名辭典推測）
   - 數字・金額的錯誤
   - 詞語中間斷開的情況要合併
3. **填充詞去除**: 去除「嗯」「那個」「就是」「然後」等填充詞
4. **病毒式分段**: 分割為TikTok字幕最佳長度
   - 每行4〜10個字為理想（傳達意義的最小單位）
   - 避免過短的分割（3字以下的獨立片段要與前後合併）
   - 在意義的分界・換氣處換行
5. **重要詞標記**: 以下詞語標記 emphasis: true
   - 商品名・品牌名
   - 金額（例: 1000元、半價）
   - 感嘆表達（超棒、太厲害、真的假的）
   - 限量表達（限定、剩餘不多、最後）
   - CTA表達（快來、趕快、買起來）
6. **標點符號**: 不使用「、」和「。」。自然的分隔處使用半形空格代替

## 輸入（Whisper原始文字 + 時間戳）
{raw_text}

## 時間戳規則（最重要 - 必須遵守）
- **絕對不要改變原始Whisper時間戳**（只允許±0.3秒以內的微調）
- 合併重複片段時: 使用第一個片段的start到最後一個片段的end
- 將一個原始片段分割為多個時: 在原始start〜end範圍內按字數比例分配
- 去除填充詞時: 不改變包含填充詞的片段的start/end（只修正文字）
- 片段之間有間隔時保持原樣（不要強行填補）
- 音訊與字幕的同步精度最優先

## 輸出格式
以下JSON陣列格式輸出。每個元素為:
{{{{
  "start": float,
  "end": float,
  "text": "修正後文字（繁體中文）",
  "emphasis": true/false,
  "highlight_words": [{{"word": "關鍵詞", "type": "product|price|emotion|cta"}}]
}}}}
- emphasis: true 的行會在字幕中大字強調顯示
- highlight_words: 文字中的重要關鍵詞 用於色彩標記（可省略）
  - product: 商品名・品牌名 → 黃色
  - price: 金額・數字 → 紅色
  - emotion: 感嘆表達 → 橘色
  - cta: CTA・限量表達 → 綠色
- 只有填充詞的片段要去除
- 不要製作3字以下的獨立片段（要與前後合併）

只輸出JSON陣列（不需要說明）:"""
        system_msg = "你是繁體中文直播帶貨字幕的修正專家。只輸出JSON陣列。"
    elif subtitle_language == 'auto':
        # For auto-detected language, use a generic prompt
        context_section = ""
        if phase_context:
            context_section = f"""\n## Phase context (reference for fixing product names and proper nouns)
{phase_context}\n"""
        product_section = ""
        if product_names:
            product_section = f"""\n## Product name dictionary (products in this video - must use for fixing misrecognitions)
{', '.join(product_names)}
※ If Whisper misrecognized, correct to the above product names\n"""
        # Build dictionary section for GPT prompt
        dict_section = ""
        if dict_entries:
            replacement_lines = []
            no_break_words = []
            for entry in dict_entries:
                if entry.get("to") and entry["to"] != entry["from"]:
                    replacement_lines.append(f"  - \"{entry['from']}\" → \"{entry['to']}\"")
                if entry.get("no_break"):
                    word = entry.get("to") or entry["from"]
                    no_break_words.append(word)
            parts = []
            if replacement_lines:
                parts.append("Replacement rules (MUST follow - auto-correct Whisper misrecognitions):\n" + "\n".join(replacement_lines))
            if no_break_words:
                parts.append(f"No-break words (NEVER split these words across lines): {', '.join(no_break_words)}")
            if parts:
                dict_section = "\n## Custom subtitle dictionary (HIGHEST PRIORITY - MUST follow)\n" + "\n".join(parts) + "\n"
        prompt = f"""You are an expert at creating viral TikTok/Reels subtitles for live commerce videos.
Convert the Whisper-generated subtitle text into the most engaging format for social media.
Keep the subtitles in the SAME LANGUAGE as the original audio (do not translate).
{context_section}{product_section}{dict_section}
## Correction rules (by priority)
1. **Merge duplicate/fragmented text (most important)**: Consolidate repeated segments
   - Understand context and merge into natural, coherent sentences
   - After merging, timestamps span from first segment's start to last segment's end
2. **Fix misrecognitions**: Correct unnatural words and sentences
   - Product/brand name misrecognitions (infer from context/dictionary)
   - Number/price errors
   - Words split in the middle should be joined
3. **Remove filler words**: Remove filler words and hesitations
4. **Viral segmentation**: Split into optimal TikTok subtitle lengths
   - 4-15 characters per line (minimum meaningful unit)
   - Avoid too-short splits (merge segments under 3 chars with neighbors)
   - Break at meaning boundaries and breath pauses
5. **Important word marking**: Mark these with emphasis: true
   - Product/brand names
   - Prices and amounts
   - Exclamations and strong expressions
   - Limited quantity expressions
   - CTA expressions
6. **Punctuation**: Do NOT use commas or periods. Use half-width spaces for natural pauses instead.

## Input (Whisper raw text + timestamps)
{raw_text}

## Timestamp rules (most important - must follow)
- **Never change original Whisper timestamps** (only ±0.3s fine-tuning allowed)
- When merging: use first segment's start to last segment's end
- When splitting: distribute within original start~end by character ratio
- When removing fillers: don't change segment start/end (only modify text)
- Keep gaps between segments as-is (don't force-fill)
- Audio-subtitle sync accuracy is top priority

## Output format
JSON array format. Each element:
{{{{
  "start": float,
  "end": float,
  "text": "corrected text (same language as original)",
  "emphasis": true/false,
  "highlight_words": [{{"word": "keyword", "type": "product|price|emotion|cta"}}]
}}}}
- emphasis: true lines will be displayed prominently
- highlight_words: key words in text for color highlighting (optional)
  - product: product/brand names → yellow
  - price: prices/amounts → red
  - emotion: exclamations/strong expressions → orange
  - cta: CTA/limited expressions → green
- Remove segments that are only filler words
- Don't create standalone segments under 3 characters

Output JSON array only (no explanation):"""
        system_msg = "You are a live commerce subtitle correction expert. Output JSON array only."
    else:
        # Default: Japanese
        context_section = ""
        if phase_context:
            context_section = f"""\n## このフェーズの内容（参考情報 - 商品名や固有名詞の修正に活用）
{phase_context}\n"""
        product_section = ""
        if product_names:
            product_section = f"""\n## 商品名辞書（この動画に登場する商品名 - 誤認識修正に必ず活用）
{', '.join(product_names)}
※ Whisperが誤認識した場合、上記の商品名に修正してください\n"""
        # Build dictionary section for GPT prompt
        dict_section = ""
        if dict_entries:
            replacement_lines = []
            no_break_words = []
            for entry in dict_entries:
                if entry.get("to") and entry["to"] != entry["from"]:
                    replacement_lines.append(f"  - 「{entry['from']}」→「{entry['to']}」")
                if entry.get("no_break"):
                    word = entry.get("to") or entry["from"]
                    no_break_words.append(word)
            parts = []
            if replacement_lines:
                parts.append("置換ルール（必ず遵守 - Whisper誤認識時に自動修正）:\n" + "\n".join(replacement_lines))
            if no_break_words:
                parts.append(f"分割禁止ワード（これらの単語は途中で改行しない）: {', '.join(no_break_words)}")
            if parts:
                dict_section = "\n## 字幕カスタム辞書（最優先 - 必ず遵守）\n" + "\n".join(parts) + "\n"
        prompt = f"""あなたは日本語ライブコマース動画のTikTok/Reels向けバイラル字幕を作成する専門家です。
Whisperで自動生成された字幕テキストを、SNS動画で最大限バズる形式に変換してください。
{context_section}{product_section}{dict_section}
## 修正ルール（優先度順）
1. **重複・断片テキストの結合（最重要）**: 同じ内容が繰り返されているセグメントを統合する
   - 例: 「あとは合わせんがん」+「合 わせがんに混ぜて」→「あとは合わせがんに混ぜて」
   - 例: 「コラーゲンパックみたいにする」+「パックみたいにするっていうのも」→「コラーゲンパックみたいにするっていうのも」
   - 前後の文脈を理解して、意味が通る自然な1文にまとめる
   - 結合した場合、タイムスタンプは最初のセグメントのstartから最後のセグメントのendまでとする
2. **誤認識の修正**: 日本語として不自然な単語や文を正しく修正する
   - 商品名・ブランド名の誤認識（コンテキスト・商品名辞書から推測）
   - 数字・金額の誤り（例: 「センエン」→「1000円」）
   - 単語の途中で切れている場合は結合（例: 「合 わせがん」→「合わせがん」）
3. **フィラーワード除去**: 「えー」「あのー」「うーん」「なんか」「まあ」等を除去
4. **バイラル文節分割**: TikTok字幕として最適な長さに分割する
   - 1行は8〜15文字が理想（意味が伝わる最小単位）
   - 短すぎる分割は避ける（3文字以下の単独セグメントは前後と結合）
   - 意味の区切り・息継ぎで改行
   - 重要ワード（商品名、金額、感嘆表現）は強調表示で目立たせる
5. **重要ワードマーキング**: 以下のワードは emphasis: true を付ける
   - 商品名・ブランド名
   - 金額（例: 1000円、半額）
   - 感嘆表現（すごい、やばい、めっちゃ、マジで）
   - 数量限定表現（限定、残りわずか、ラスト）
   - CTA表現（今すぐ、ポチって、買って）
6. **句読点**: 「、」「。」は使わない。自然な区切りには半角スペースを使う

## 入力（Whisper生テキスト + タイムスタンプ）
{raw_text}

## タイムスタンプルール（最重要 - 厳守）
- **元のWhisperタイムスタンプを絶対に変更しない**（±0.3秒以内の微調整のみ許可）
- 重複セグメントを結合した場合: 最初のセグメントのstartから最後のセグメントのendまでを使用
- 1つの元セグメントを複数に分割する場合: 元のstart〜endの範囲内で文字数比率で分配
- フィラーワード除去時: フィラーを含むセグメントのstart/endは変更しない（テキストのみ修正）
  - 例: [2.50-4.00] 「えーと、髪の毛を」→ [2.50-4.00] 「髪の毛を」（タイムスタンプ維持）
- セグメント間にギャップがある場合はそのまま維持（無理に埋めない）
- 音声と字幕の同期精度が最優先。テキスト修正のためにタイムスタンプを犠牲にしない

## 出力形式
以下のJSON配列形式で出力。各要素は:
{{{{
  "start": float,
  "end": float,
  "text": "修正後テキスト",
  "emphasis": true/false,
  "highlight_words": [{{"word": "キーワード", "type": "product|price|emotion|cta"}}]
}}}}
- emphasis: true の行は字幕で大きく強調表示される
- highlight_words: テキスト内の重要キーワードを抽出し色分け表示する（省略可）
  - product: 商品名・ブランド名（例: KYOGOKU iCell）→ 黄色
  - price: 金額・数字（例: 3980円 半額 50%OFF）→ 赤色
  - emotion: 感嘆表現（例: すごい やばい めっちゃ マジで）→ オレンジ
  - cta: CTA・限定表現（例: 今すぐ 限定 残りわずか ポチって）→ 緑色
- フィラーワードのみのセグメントは除去
- 3文字以下の単独セグメントは作らない（前後と結合すること）

JSON配列のみ出力（説明不要）:"""
        system_msg = "あなたは日本語ライブコマース字幕の修正専門家です。JSON配列のみを出力してください。"

    try:
        response = _openai_client.responses.create(
            model="gpt-4.1-mini",
            input=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt},
            ],
            max_output_tokens=4096,
        )

        result_text = response.output_text.strip()
        
        # Extract JSON from response (handle markdown code blocks)
        if result_text.startswith("```"):
            lines = result_text.split("\n")
            json_lines = []
            in_block = False
            for line in lines:
                if line.startswith("```"):
                    in_block = not in_block
                    continue
                if in_block:
                    json_lines.append(line)
            result_text = "\n".join(json_lines)

        refined = json.loads(result_text)

        if not isinstance(refined, list) or len(refined) == 0:
            logger.warning("GPT returned invalid format, using original segments")
            return segments

        # Compute the valid time range from original Whisper segments
        orig_min_start = min(s["start"] for s in segments)
        orig_max_end = max(s["end"] for s in segments)
        logger.info(f"Original Whisper time range: {orig_min_start:.2f} - {orig_max_end:.2f}")

        # Build a flat list of original Whisper word timestamps for matching
        # Each entry: {"word": str, "start": float, "end": float}
        orig_words_flat = []
        for seg in segments:
            for w in seg.get("words", []):
                orig_words_flat.append(w)
        logger.info(f"Original Whisper words for matching: {len(orig_words_flat)}")

        # Build a character-level timeline from ALL original Whisper words
        # This is the ground truth for audio-text alignment
        orig_char_timeline = []  # [(char, start, end), ...]
        for ow in orig_words_flat:
            ow_text = ow.get("word", "")
            ow_start = ow.get("start", 0)
            ow_end = ow.get("end", 0)
            ow_dur = max(0.01, ow_end - ow_start)
            ow_chars = list(ow_text)
            for ci, ch in enumerate(ow_chars):
                ch_s = ow_start + (ow_dur * ci / max(1, len(ow_chars)))
                ch_e = ow_start + (ow_dur * (ci + 1) / max(1, len(ow_chars)))
                orig_char_timeline.append((ch, round(ch_s, 3), round(ch_e, 3)))

        # Build original full text for subsequence matching
        orig_full_text = "".join(ch for ch, _, _ in orig_char_timeline)
        logger.info(f"Original char timeline: {len(orig_char_timeline)} chars, text='{orig_full_text[:80]}...'")

        # Helper: find best matching position of a substring in original text
        # Uses sliding window with character similarity score
        def _find_best_match(query_text, search_start_idx=0):
            """Find the best matching position of query_text in orig_full_text.
            Returns (start_idx, end_idx, score) in orig_char_timeline.
            Uses character-level matching to handle GPT text modifications."""
            if not query_text or not orig_full_text:
                return None
            query_chars = list(query_text)
            qlen = len(query_chars)
            best_score = -1
            best_start = search_start_idx
            # Search window: from search_start_idx, scan forward
            # Allow some backward tolerance for overlapping segments
            scan_start = max(0, search_start_idx - min(20, qlen))
            scan_end = min(len(orig_full_text), search_start_idx + qlen * 3 + 50)
            for i in range(scan_start, scan_end):
                # Score: count matching characters in a window of qlen
                matches = 0
                window_end = min(i + qlen + 5, len(orig_full_text))  # slight extra
                qi = 0
                oi = i
                while qi < qlen and oi < window_end:
                    if query_chars[qi] == orig_full_text[oi]:
                        matches += 1
                        qi += 1
                        oi += 1
                    else:
                        # Try skipping one char in original (deletion in GPT)
                        if oi + 1 < window_end and qi < qlen and query_chars[qi] == orig_full_text[oi + 1]:
                            oi += 1
                        # Try skipping one char in query (insertion by GPT)
                        elif qi + 1 < qlen and oi < window_end and query_chars[qi + 1] == orig_full_text[oi]:
                            qi += 1
                        else:
                            qi += 1
                            oi += 1
                score = matches / max(1, qlen)
                if score > best_score:
                    best_score = score
                    best_start = i
                if score >= 0.95:  # Good enough match
                    break
            # Determine end index: advance through orig matching query chars
            end_idx = best_start
            qi = 0
            while qi < qlen and end_idx < len(orig_char_timeline):
                if qi < qlen and end_idx < len(orig_full_text) and query_chars[qi] == orig_full_text[end_idx]:
                    qi += 1
                end_idx += 1
                if qi >= qlen:
                    break
            return (best_start, min(end_idx, len(orig_char_timeline)), best_score)

        # Validate, clean, clamp timestamps, and reconstruct word-level timestamps
        valid_segments = []
        orig_search_cursor = 0  # Track position in original text for sequential matching

        for seg in refined:
            if isinstance(seg, dict) and "start" in seg and "end" in seg and "text" in seg:
                text_val = seg["text"].strip()
                if text_val:
                    s_start = float(seg["start"])
                    s_end = float(seg["end"])

                    # Clamp timestamps to original Whisper range to prevent GPT drift
                    s_start = max(orig_min_start, min(s_start, orig_max_end))
                    s_end = max(s_start + 0.1, min(s_end, orig_max_end))  # Ensure min 100ms duration

                    # Ensure segments don't overlap with previous segment
                    if valid_segments and s_start < valid_segments[-1]["end"]:
                        s_start = valid_segments[-1]["end"]
                        if s_start >= s_end:
                            continue  # Skip if no room left

                    chars = list(text_val)
                    total_chars = len(chars)
                    duration = s_end - s_start
                    words = []

                    # Strategy: match GPT text to original Whisper char timeline
                    # using subsequence matching, then use original timestamps
                    match_result = _find_best_match(text_val, orig_search_cursor) if orig_char_timeline else None

                    if match_result and match_result[2] >= 0.5:  # At least 50% character match
                        match_start, match_end, match_score = match_result
                        # Use original Whisper timestamps from the matched region
                        matched_chars = orig_char_timeline[match_start:match_end]

                        if matched_chars:
                            # Override GPT timestamps with Whisper timestamps
                            whisper_start = matched_chars[0][1]
                            whisper_end = matched_chars[-1][2]

                            # Use Whisper timestamps if they're reasonable
                            # (within 2s of GPT timestamps to catch gross errors)
                            if abs(whisper_start - s_start) <= 2.0:
                                s_start = whisper_start
                            if abs(whisper_end - s_end) <= 2.0:
                                s_end = whisper_end
                            # Ensure min duration
                            s_end = max(s_start + 0.1, s_end)

                            # Re-check overlap after timestamp correction
                            if valid_segments and s_start < valid_segments[-1]["end"]:
                                s_start = valid_segments[-1]["end"]
                                if s_start >= s_end:
                                    continue

                            # Map each GPT character to original timestamps via subsequence
                            mi = 0  # index into matched_chars
                            for ci, ch in enumerate(chars):
                                # Try to find this character in matched region
                                found = False
                                search_limit = min(mi + 5, len(matched_chars))
                                for si in range(mi, search_limit):
                                    if matched_chars[si][0] == ch:
                                        _, ch_start, ch_end = matched_chars[si]
                                        words.append({"word": ch, "start": ch_start, "end": ch_end})
                                        mi = si + 1
                                        found = True
                                        break
                                if not found:
                                    # Character not in original (GPT added punctuation etc)
                                    # Interpolate from surrounding matched characters
                                    if words:
                                        prev_end = words[-1]["end"]
                                    else:
                                        prev_end = s_start
                                    # Look ahead for next matched char
                                    next_start = s_end
                                    for fi in range(ci + 1, min(ci + 5, total_chars)):
                                        if fi < total_chars:
                                            for si in range(mi, min(mi + 5, len(matched_chars))):
                                                if matched_chars[si][0] == chars[fi]:
                                                    next_start = matched_chars[si][1]
                                                    break
                                            if next_start != s_end:
                                                break
                                    ch_start = prev_end
                                    ch_end = min(prev_end + 0.05, next_start)  # 50ms for inserted chars
                                    words.append({"word": ch, "start": round(ch_start, 3), "end": round(ch_end, 3)})

                            # Advance search cursor past this match
                            orig_search_cursor = match_end
                            logger.debug(f"Matched '{text_val[:20]}' score={match_score:.2f} range={match_start}-{match_end}")
                        else:
                            # Matched region empty, fallback to even distribution
                            for ci, ch in enumerate(chars):
                                ch_start = s_start + (duration * ci / total_chars)
                                ch_end = s_start + (duration * (ci + 1) / total_chars)
                                words.append({"word": ch, "start": round(ch_start, 3), "end": round(ch_end, 3)})
                    else:
                        # No good match found: use GPT timestamps with even distribution
                        # But still try to find matching original words in time range
                        matching_orig_words = []
                        for ow in orig_words_flat:
                            ow_start = ow.get("start", 0)
                            ow_end = ow.get("end", 0)
                            if ow_end >= s_start - 0.3 and ow_start <= s_end + 0.3:
                                matching_orig_words.append(ow)

                        if matching_orig_words:
                            # Build char timeline from time-range matched words
                            range_char_ts = []
                            for ow in matching_orig_words:
                                ow_text = ow.get("word", "")
                                ow_s = max(s_start, ow.get("start", s_start))
                                ow_e = min(s_end, ow.get("end", s_end))
                                ow_d = max(0.01, ow_e - ow_s)
                                for ci2, ch2 in enumerate(list(ow_text)):
                                    ch_s2 = ow_s + (ow_d * ci2 / max(1, len(ow_text)))
                                    ch_e2 = ow_s + (ow_d * (ci2 + 1) / max(1, len(ow_text)))
                                    range_char_ts.append((ch2, round(ch_s2, 3), round(ch_e2, 3)))

                            if range_char_ts:
                                # Map by subsequence matching within this range
                                ri = 0
                                for ci, ch in enumerate(chars):
                                    found = False
                                    for si in range(ri, min(ri + 5, len(range_char_ts))):
                                        if range_char_ts[si][0] == ch:
                                            _, ch_start, ch_end = range_char_ts[si]
                                            words.append({"word": ch, "start": ch_start, "end": ch_end})
                                            ri = si + 1
                                            found = True
                                            break
                                    if not found:
                                        if words:
                                            prev_end = words[-1]["end"]
                                        else:
                                            prev_end = s_start
                                        ch_start = prev_end
                                        ch_end = min(prev_end + 0.05, s_end)
                                        words.append({"word": ch, "start": round(ch_start, 3), "end": round(ch_end, 3)})
                            else:
                                for ci, ch in enumerate(chars):
                                    ch_start = s_start + (duration * ci / total_chars)
                                    ch_end = s_start + (duration * (ci + 1) / total_chars)
                                    words.append({"word": ch, "start": round(ch_start, 3), "end": round(ch_end, 3)})
                        else:
                            # No matching original words found: even distribution fallback
                            for ci, ch in enumerate(chars):
                                ch_start = s_start + (duration * ci / total_chars)
                                ch_end = s_start + (duration * (ci + 1) / total_chars)
                                words.append({"word": ch, "start": round(ch_start, 3), "end": round(ch_end, 3)})

                    valid_segments.append({
                        "start": round(s_start, 3),
                        "end": round(s_end, 3),
                        "text": text_val,
                        "words": words,
                        "emphasis": bool(seg.get("emphasis", False)),
                        "highlight_words": seg.get("highlight_words", []),
                    })

        if not valid_segments:
            logger.warning("GPT refinement produced no valid segments, using original")
            return segments

        logger.info(f"GPT refined {len(segments)} segments → {len(valid_segments)} segments")
        return valid_segments

    except json.JSONDecodeError as e:
        logger.warning(f"GPT response JSON parse failed: {e}")
        return segments
    except Exception as e:
        logger.error(f"GPT subtitle refinement failed: {e}")
        return segments


def get_phase_context(video_id: str, phase_index: int) -> str:
    """
    Fetch phase description and insight from DB to provide context for subtitle refinement.
    """
    loop = get_event_loop()

    async def _fetch():
        async with get_session() as session:
            sql = text("""
                SELECT vp.phase_description, pi.insight
                FROM video_phases vp
                LEFT JOIN phase_insights pi
                    ON pi.video_id = vp.video_id AND pi.phase_index = vp.phase_index
                WHERE vp.video_id = :video_id AND vp.phase_index = :phase_index
                LIMIT 1
            """)
            result = await session.execute(sql, {"video_id": video_id, "phase_index": phase_index})
            row = result.fetchone()
            if row:
                parts = []
                if row.phase_description:
                    parts.append(f"概要: {row.phase_description}")
                if row.insight:
                    parts.append(f"分析: {row.insight}")
                return "\n".join(parts)
            return ""

    try:
        return loop.run_until_complete(_fetch())
    except Exception as e:
        logger.warning(f"Failed to fetch phase context: {e}")
        return ""


def get_product_names(video_id: str) -> list:
    """
    Fetch product names from video_product_exposures and video_phases tables
    to provide domain-specific vocabulary for subtitle refinement.
    """
    loop = get_event_loop()

    async def _fetch():
        async with get_session() as session:
            # Get product names from product exposures
            sql = text("""
                SELECT DISTINCT product_name
                FROM video_product_exposures
                WHERE video_id = :video_id AND product_name IS NOT NULL
            """)
            result = await session.execute(sql, {"video_id": video_id})
            names = [row.product_name for row in result.fetchall() if row.product_name]

            # Also get product names from video_phases
            sql2 = text("""
                SELECT DISTINCT product_names
                FROM video_phases
                WHERE video_id = :video_id AND product_names IS NOT NULL
            """)
            result2 = await session.execute(sql2, {"video_id": video_id})
            for row in result2.fetchall():
                if row.product_names:
                    try:
                        pn = json.loads(row.product_names) if isinstance(row.product_names, str) else row.product_names
                        if isinstance(pn, list):
                            names.extend(pn)
                    except (json.JSONDecodeError, TypeError):
                        pass

            return list(set(n for n in names if n and len(n) > 1))

    try:
        return loop.run_until_complete(_fetch())
    except Exception as e:
        logger.warning(f"Failed to fetch product names: {e}")
        return []


def get_subtitle_dictionary() -> list:
    """
    Fetch all active subtitle dictionary entries for GPT subtitle refinement.
    Returns list of dicts: [{"from": str, "to": str, "no_break": bool}, ...]
    """
    loop = get_event_loop()

    async def _fetch():
        async with get_session() as session:
            sql = text("""
                SELECT from_text, to_text, no_break, category
                FROM subtitle_dictionary
                WHERE user_id = 'default' AND is_active = TRUE
                ORDER BY LENGTH(from_text) DESC
            """)
            result = await session.execute(sql)
            rows = result.fetchall()
            return [
                {
                    "from": row.from_text,
                    "to": row.to_text or "",
                    "no_break": row.no_break,
                    "category": row.category or "other"
                }
                for row in rows if row.from_text
            ]

    try:
        return loop.run_until_complete(_fetch())
    except Exception as e:
        logger.warning(f"Failed to fetch subtitle dictionary: {e}")
        return []


# =========================
# Person detection + scene filtering
# =========================

YOLO_MODEL_PATH = os.getenv("YOLO_MODEL_PATH", "/home/azureuser/yolov8n.pt")


def detect_person_intervals(video_path: str, sample_fps: float = 2.0, confidence: float = 0.4) -> list:
    """
    Detect time intervals where a person is visible using YOLOv8.
    Samples frames at `sample_fps` rate and returns merged intervals.
    Returns list of (start_sec, end_sec) tuples.
    """
    try:
        import cv2
        from ultralytics import YOLO
    except ImportError as e:
        logger.warning(f"Person detection dependencies not available: {e}")
        return None  # Return None to signal detection is unavailable

    if not os.path.exists(YOLO_MODEL_PATH):
        logger.warning(f"YOLO model not found at {YOLO_MODEL_PATH}")
        return None

    logger.info(f"Running person detection on {video_path} (sample_fps={sample_fps})")
    model = YOLO(YOLO_MODEL_PATH)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.error("Failed to open video for person detection")
        return None

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / fps
    frame_interval = max(1, int(fps / sample_fps))  # Sample every N frames

    person_frames = []  # List of timestamps where person is detected
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % frame_interval == 0:
            timestamp = frame_idx / fps
            results = model(frame, verbose=False, classes=[0])  # class 0 = person
            if results and len(results[0].boxes) > 0:
                # Check if any detection has sufficient confidence
                for box in results[0].boxes:
                    if box.conf[0] >= confidence:
                        person_frames.append(timestamp)
                        break

        frame_idx += 1

    cap.release()

    if not person_frames:
        logger.warning("No person detected in any frame")
        return []

    logger.info(f"Person detected in {len(person_frames)} sampled frames out of {frame_idx // frame_interval} total")

    # Merge nearby timestamps into continuous intervals
    # Allow gap of up to 1.5 seconds between person detections
    max_gap = 1.5 / sample_fps * sample_fps + 0.5  # ~2 seconds tolerance
    intervals = []
    interval_start = person_frames[0]
    prev_time = person_frames[0]

    for t in person_frames[1:]:
        if t - prev_time > max_gap:
            # Close current interval with small padding
            intervals.append((max(0, interval_start - 0.3), min(duration, prev_time + 0.5)))
            interval_start = t
        prev_time = t

    # Close last interval
    intervals.append((max(0, interval_start - 0.3), min(duration, prev_time + 0.5)))

    # Merge overlapping intervals
    merged = [intervals[0]]
    for start, end in intervals[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    logger.info(f"Person visible in {len(merged)} intervals: {merged}")
    return merged


def concatenate_intervals(video_path: str, intervals: list, output_path: str) -> bool:
    """
    Concatenate only the specified time intervals from the video.

    Uses FFmpeg filter_complex with accurate seeking (re-encode) to avoid
    duplicate frames caused by keyframe-aligned stream-copy cuts.
    When there are many intervals (>10), falls back to per-segment re-encode
    + concat demuxer to avoid overly complex filter graphs.
    """
    if not intervals:
        return False

    # Filter out very short intervals
    intervals = [(s, e) for s, e in intervals if e - s >= 0.5]
    if not intervals:
        return False

    work_dir = os.path.dirname(output_path)
    n = len(intervals)
    logger.info(f"Concatenating {n} intervals from {video_path}")

    # ---- Strategy A: filter_complex for small number of intervals ----
    if n <= 10:
        try:
            return _concat_via_filter_complex(video_path, intervals, output_path)
        except Exception as e:
            logger.warning(f"filter_complex concat failed, falling back to per-segment: {e}")

    # ---- Strategy B: per-segment re-encode + concat demuxer ----
    return _concat_via_segments(video_path, intervals, output_path, work_dir)


def _concat_via_filter_complex(video_path: str, intervals: list, output_path: str) -> bool:
    """
    Use a single FFmpeg command with filter_complex to trim and concatenate
    intervals accurately (re-encode, no keyframe issues).
    """
    n = len(intervals)
    # Build filter_complex string
    filter_parts = []
    concat_inputs = ""
    for i, (start, end) in enumerate(intervals):
        duration = end - start
        filter_parts.append(
            f"[0:v]trim=start={start:.3f}:duration={duration:.3f},setpts=PTS-STARTPTS[v{i}];"
        )
        filter_parts.append(
            f"[0:a]atrim=start={start:.3f}:duration={duration:.3f},asetpts=PTS-STARTPTS[a{i}];"
        )
        concat_inputs += f"[v{i}][a{i}]"

    filter_parts.append(
        f"{concat_inputs}concat=n={n}:v=1:a=1[outv][outa]"
    )
    filter_str = "".join(filter_parts)

    cmd = [
        FFMPEG_BIN, "-y",
        "-i", video_path,
        "-filter_complex", filter_str,
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        logger.error(f"filter_complex concat stderr: {result.stderr[-500:]}")
        raise RuntimeError(f"filter_complex concat failed (rc={result.returncode})")

    logger.info(f"Concatenated {n} intervals via filter_complex")
    return True


def _concat_via_segments(video_path: str, intervals: list, output_path: str, work_dir: str) -> bool:
    """
    Cut each interval with re-encode (accurate seek) then concatenate
    via concat demuxer.  Used when there are too many intervals for
    filter_complex.
    """
    segment_files = []

    for i, (start, end) in enumerate(intervals):
        seg_path = os.path.join(work_dir, f"person_seg_{i}.mp4")
        duration = end - start
        if duration < 0.5:
            continue

        # Always re-encode for accurate cutting (avoids keyframe duplication)
        cmd = [
            FFMPEG_BIN, "-y",
            "-ss", f"{start:.3f}",
            "-accurate_seek",
            "-i", video_path,
            "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            seg_path,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=300)
            segment_files.append(seg_path)
        except Exception as e:
            logger.error(f"Failed to cut interval {i} ({start:.1f}-{end:.1f}): {e}")

    if not segment_files:
        return False

    if len(segment_files) == 1:
        os.rename(segment_files[0], output_path)
        return True

    # Create concat file list
    concat_list_path = os.path.join(work_dir, "person_concat.txt")
    with open(concat_list_path, "w") as f:
        for seg_path in segment_files:
            f.write(f"file '{seg_path}'\n")

    # Concatenate using concat demuxer (stream copy is safe here because
    # all segments were re-encoded with identical codec settings)
    cmd = [
        FFMPEG_BIN, "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_list_path,
        "-c", "copy",
        "-movflags", "+faststart",
        output_path,
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=300)
        logger.info(f"Concatenated {len(segment_files)} re-encoded segments")
        return True
    except Exception as e:
        logger.error(f"Failed to concatenate segments: {e}")
        return False
    finally:
        # Cleanup temp segments
        for seg_path in segment_files:
            if os.path.exists(seg_path):
                os.remove(seg_path)
        if os.path.exists(concat_list_path):
            os.remove(concat_list_path)


# =========================
# Video processing (crop + subtitles)
# =========================

def get_video_dimensions(video_path: str) -> tuple:
    """Get video width and height."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json",
        video_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)
        stream = data["streams"][0]
        return int(stream["width"]), int(stream["height"])
    except Exception as e:
        logger.error(f"Failed to get video dimensions: {e}")
        return 1920, 1080  # Default assumption


def build_ass_subtitle(segments: list, style: dict, video_width: int = 1080, video_height: int = 1920) -> str:
    """Build ASS subtitle file with TikTok-style karaoke highlight effect.
    
    Uses word-level timestamps to create a karaoke effect where each character
    is highlighted as it is spoken, similar to popular TikTok/Reels subtitles.
    Supports emphasis segments with larger font size and accent color.
    """
    fontsize = style["fontsize"]
    emphasis_fontsize = int(fontsize * 1.5)  # 150% for emphasis words
    fontcolor_ass = _hex_to_ass_color(style["fontcolor"])
    highlight_color_ass = _hex_to_ass_color(style.get("highlight_color", "#FFD700"))
    emphasis_color_ass = _hex_to_ass_color(style.get("highlight_color", "#FFD700"))
    bordercolor_ass = _hex_to_ass_color(style.get("bordercolor", "black"))
    outline = style.get("borderw", 4)
    emphasis_outline = outline + 2

    # ASS header with Default + Emphasis styles
    ass_content = f"""[Script Info]
Title: TikTok Clip Subtitles - Karaoke
ScriptType: v4.00+
PlayResX: {video_width}
PlayResY: {video_height}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{JP_FONT_NAME},{fontsize},{fontcolor_ass},{highlight_color_ass},{bordercolor_ass},&H00000000,-1,0,0,0,100,100,2,0,1,{outline},0,2,40,40,320,1
Style: Emphasis,{JP_FONT_NAME},{emphasis_fontsize},{emphasis_color_ass},{highlight_color_ass},{bordercolor_ass},&H00000000,-1,0,0,0,100,100,2,0,1,{emphasis_outline},0,2,40,40,300,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    # Color mapping for highlight_words types (ASS BGR format)
    HIGHLIGHT_COLORS = {
        "product": _hex_to_ass_color("#FFD700"),   # yellow
        "price": _hex_to_ass_color("#FF4444"),      # red
        "emotion": _hex_to_ass_color("#FF8C00"),    # orange
        "cta": _hex_to_ass_color("#00FF7F"),        # green
    }

    for seg in segments:
        start_time = _seconds_to_ass_time(seg["start"])
        end_time = _seconds_to_ass_time(seg["end"])
        words = seg.get("words", [])
        is_emphasis = seg.get("emphasis", False)
        style_name = "Emphasis" if is_emphasis else "Default"
        highlight_words = seg.get("highlight_words", [])

        # Build a lookup: keyword text -> ASS color tag
        hw_map = {}
        for hw in highlight_words:
            hw_word = hw.get("word", "")
            hw_type = hw.get("type", "")
            if hw_word and hw_type in HIGHLIGHT_COLORS:
                hw_map[hw_word] = HIGHLIGHT_COLORS[hw_type]

        if words and len(words) > 1:
            # Build karaoke effect using \kf (smooth fill) tags
            karaoke_text = ""
            for w in words:
                w_duration_cs = max(1, int((w["end"] - w["start"]) * 100))
                char = w["word"].replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
                # Check if this word matches any highlight keyword
                color_tag = ""
                if hw_map:
                    w_text = w["word"].strip()
                    for kw, kw_color in hw_map.items():
                        if kw in w_text or w_text in kw:
                            color_tag = f"\\c{kw_color}"
                            break
                if color_tag:
                    karaoke_text += f"{{\\kf{w_duration_cs}{color_tag}}}{char}"
                else:
                    karaoke_text += f"{{\\kf{w_duration_cs}}}{char}"
            ass_content += f"Dialogue: 0,{start_time},{end_time},{style_name},,0,0,0,,{karaoke_text}\n"
        else:
            text = seg["text"]
            # Apply inline color tags for highlight_words
            if hw_map:
                for kw, kw_color in hw_map.items():
                    if kw in text:
                        escaped_kw = kw.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
                        text = text.replace(kw, f"{{\\c{kw_color}}}{escaped_kw}{{\\c}}")
            text = text.replace("\n", "\\N")
            ass_content += f"Dialogue: 0,{start_time},{end_time},{style_name},,0,0,0,,{text}\n"

    return ass_content


def _hex_to_ass_color(color: str) -> str:
    """Convert hex color or named color to ASS color format (&HAABBGGRR)."""
    color_map = {
        "white": "&H00FFFFFF",
        "black": "&H00000000",
        "yellow": "&H0000FFFF",
        "red": "&H000000FF",
    }
    if color.lower() in color_map:
        return color_map[color.lower()]

    # Handle hex colors like #FF69B4
    color = color.lstrip("#")
    if "@" in color:
        color = color.split("@")[0].lstrip("#")

    if len(color) == 6:
        r, g, b = color[0:2], color[2:4], color[4:6]
        return f"&H00{b}{g}{r}"

    return "&H00FFFFFF"


def _seconds_to_ass_time(seconds: float) -> str:
    """Convert seconds to ASS time format (H:MM:SS.CC)."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def create_vertical_clip(
    input_path: str,
    output_path: str,
    segments: list,
    style: dict,
    speed_factor: float = 1.0,
) -> bool:
    """Create 9:16 vertical clip with burned-in karaoke subtitles and optional speed adjustment.
    
    Args:
        speed_factor: Playback speed multiplier (1.0 = normal, 1.2 = 20% faster, etc.)
                      Subtitles are pre-adjusted for the speed change.
    """
    width, height = get_video_dimensions(input_path)
    logger.info(f"Source video: {width}x{height}, speed_factor={speed_factor}")

    # Target: 1080x1920 (9:16)
    target_w, target_h = 1080, 1920

    # Calculate crop dimensions to get 9:16 from source
    source_ratio = width / height
    target_ratio = target_w / target_h  # 0.5625

    if source_ratio > target_ratio:
        crop_h = height
        crop_w = int(height * target_ratio)
        crop_x = (width - crop_w) // 2
        crop_y = 0
    else:
        crop_w = width
        crop_h = int(width / target_ratio)
        crop_x = 0
        crop_y = (height - crop_h) // 2

    # NOTE: Subtitle timestamps MUST be adjusted for speed change.
    # When setpts=PTS/speed_factor is applied, the video plays speed_factor times
    # faster. The ASS filter uses the frame's PTS to decide when to show subtitles.
    # Since PTS is scaled by 1/speed_factor, subtitles at original timestamp T
    # will appear at wall-clock time T/speed_factor. Therefore we must divide
    # subtitle timestamps by speed_factor so they align with the sped-up video.
    subtitle_segments = segments
    if speed_factor != 1.0 and speed_factor > 0:
        logger.info(
            f"Speed factor {speed_factor}x applied; adjusting subtitle timestamps by 1/{speed_factor:.3f}"
        )
        subtitle_segments = [
            {
                **seg,
                "start": seg["start"] / speed_factor,
                "end": seg["end"] / speed_factor,
                "words": [
                    {**w, "start": w["start"] / speed_factor, "end": w["end"] / speed_factor}
                    for w in seg.get("words", [])
                ] if seg.get("words") else seg.get("words"),
            }
            for seg in segments
        ]

    # Build ASS subtitle file
    ass_path = input_path + ".ass"
    ass_content = build_ass_subtitle(subtitle_segments, style, target_w, target_h)
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_content)

    logger.info(f"Created ASS subtitle file: {ass_path}")

    # FFmpeg command: crop → scale → burn subtitles (+ optional speed adjustment)
    ass_path_escaped = ass_path.replace("'", "'\\''")
    
    # Video filter: crop → scale → subtitles
    vf_parts = [
        f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}",
        f"scale={target_w}:{target_h}:flags=lanczos",
        f"ass='{ass_path_escaped}':fontsdir='{JP_FONT_DIR}'",
    ]
    
    # Add speed adjustment to video filter
    if speed_factor != 1.0 and speed_factor > 0:
        # setpts=PTS/speed_factor speeds up the video
        vf_parts.append(f"setpts=PTS/{speed_factor}")
    
    filter_complex = ",".join(vf_parts)
    
    cmd = [
        FFMPEG_BIN, "-y",
        "-i", input_path,
        "-vf", filter_complex,
    ]
    
    # Audio filter for speed adjustment
    if speed_factor != 1.0 and speed_factor > 0:
        # atempo supports 0.5-2.0 range; chain multiple for larger ranges
        atempo_filters = []
        remaining = speed_factor
        while remaining > 2.0:
            atempo_filters.append("atempo=2.0")
            remaining /= 2.0
        while remaining < 0.5:
            atempo_filters.append("atempo=0.5")
            remaining /= 0.5
        atempo_filters.append(f"atempo={remaining:.4f}")
        cmd.extend(["-af", ",".join(atempo_filters)])
    
    cmd.extend([
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "22",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-movflags", "+faststart",
        "-r", "30",
        output_path,
    ])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if result.returncode != 0:
            logger.error(f"FFmpeg stderr: {result.stderr[-500:]}")
            return create_vertical_clip_drawtext(input_path, output_path, segments, style,
                                                  crop_w, crop_h, crop_x, crop_y, target_w, target_h)
        logger.info(f"Vertical clip created successfully (speed={speed_factor}x, karaoke subtitles)")
        return True
    except Exception as e:
        logger.error(f"FFmpeg failed: {e}")
        return create_vertical_clip_drawtext(input_path, output_path, segments, style,
                                              crop_w, crop_h, crop_x, crop_y, target_w, target_h)
    finally:
        if os.path.exists(ass_path):
            os.remove(ass_path)


def create_vertical_clip_drawtext(
    input_path: str,
    output_path: str,
    segments: list,
    style: dict,
    crop_w: int, crop_h: int, crop_x: int, crop_y: int,
    target_w: int, target_h: int,
) -> bool:
    """Fallback: create clip using drawtext filter instead of ASS."""
    logger.info("Falling back to drawtext subtitles")

    fontsize = style["fontsize"]
    fontcolor = style["fontcolor"]
    borderw = style.get("borderw", 4)

    # Build drawtext filter chain
    vf_parts = [
        f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}",
        f"scale={target_w}:{target_h}:flags=lanczos",
    ]

    # Use the Noto Sans CJK JP font file for drawtext
    font_file = JP_FONT_FILE
    if not os.path.exists(font_file):
        logger.warning(f"Font file not found: {font_file}, trying fc-match")
        try:
            result = subprocess.run(["fc-match", "Noto Sans CJK JP:style=Black", "-f", "%{file}"],
                                   capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and result.stdout.strip():
                font_file = result.stdout.strip()
        except Exception as _e:
            logger.debug(f"Suppressed: {_e}")

    for seg in segments:
        text = seg["text"].replace("'", "'\\''").replace(":", "\\:")
        start = seg["start"]
        end = seg["end"]
        vf_parts.append(
            f"drawtext=text='{text}'"
            f":fontfile='{font_file}'"
            f":fontsize={fontsize}"
            f":fontcolor={fontcolor}"
            f":borderw={borderw}"
            f":bordercolor={style.get('bordercolor', '#FF6B9D')}"
            f":x=(w-text_w)/2"
            f":y=h*0.68"
            f":enable='between(t,{start},{end})'"
        )

    vf = ",".join(vf_parts)

    cmd = [
        FFMPEG_BIN, "-y",
        "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "22",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-movflags", "+faststart",
        "-r", "30",
        output_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if result.returncode != 0:
            logger.error(f"drawtext FFmpeg stderr: {result.stderr[-500:]}")
            # Last resort: just crop without subtitles
            return create_vertical_clip_nosub(input_path, output_path,
                                               crop_w, crop_h, crop_x, crop_y, target_w, target_h)
        logger.info("Vertical clip created with drawtext subtitles")
        return True
    except Exception as e:
        logger.error(f"drawtext FFmpeg failed: {e}")
        return create_vertical_clip_nosub(input_path, output_path,
                                           crop_w, crop_h, crop_x, crop_y, target_w, target_h)


def create_vertical_clip_nosub(
    input_path: str, output_path: str,
    crop_w: int, crop_h: int, crop_x: int, crop_y: int,
    target_w: int, target_h: int,
) -> bool:
    """Last resort: create vertical clip without subtitles."""
    logger.info("Creating vertical clip without subtitles")

    cmd = [
        FFMPEG_BIN, "-y",
        "-i", input_path,
        "-vf", f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale={target_w}:{target_h}:flags=lanczos",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "22",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=1800)
        logger.info("Vertical clip created (no subtitles)")
        return True
    except Exception as e:
        logger.error(f"Final FFmpeg attempt failed: {e}")
        return False


# =========================
# Silence detection + removal
# =========================

def detect_silence_intervals(video_path: str, noise_threshold: str = "-35dB", min_silence_duration: float = 0.8) -> list:
    """
    Detect silent intervals in a video using ffmpeg silencedetect filter.
    Returns list of (start_sec, end_sec) tuples representing silent intervals.
    
    Args:
        video_path: Path to the video file
        noise_threshold: Noise level threshold (dB). Audio below this is considered silence.
        min_silence_duration: Minimum duration (seconds) of silence to detect.
    """
    cmd = [
        FFMPEG_BIN, "-y",
        "-i", video_path,
        "-af", f"silencedetect=noise={noise_threshold}:d={min_silence_duration}",
        "-f", "null", "-",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        stderr = result.stderr
    except Exception as e:
        logger.error(f"Silence detection failed: {e}")
        return []

    # Parse silencedetect output from stderr
    # Format: [silencedetect @ ...] silence_start: 1.234
    #         [silencedetect @ ...] silence_end: 5.678 | silence_duration: 4.444
    silence_starts = re.findall(r"silence_start:\s*([\d.]+)", stderr)
    silence_ends = re.findall(r"silence_end:\s*([\d.]+)", stderr)

    intervals = []
    for i in range(min(len(silence_starts), len(silence_ends))):
        start = float(silence_starts[i])
        end = float(silence_ends[i])
        if end - start >= min_silence_duration:
            intervals.append((start, end))

    # Handle case where silence extends to end of file (no silence_end)
    if len(silence_starts) > len(silence_ends):
        # Get video duration
        try:
            probe_cmd = [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path,
            ]
            probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
            video_duration = float(probe_result.stdout.strip())
            start = float(silence_starts[-1])
            if video_duration - start >= min_silence_duration:
                intervals.append((start, video_duration))
        except Exception as _e:
            logger.debug(f"Suppressed: {_e}")

    logger.info(f"Detected {len(intervals)} silent intervals: {intervals}")
    return intervals


def remove_silence_from_video(video_path: str, output_path: str, silence_intervals: list, min_keep: float = 0.3) -> bool:
    """
    Remove silent intervals from video by keeping only non-silent parts.
    Keeps a small buffer (min_keep seconds) at silence boundaries for natural transitions.
    
    Args:
        video_path: Input video path
        output_path: Output video path
        silence_intervals: List of (start, end) tuples of silent intervals
        min_keep: Buffer in seconds to keep at silence boundaries
    """
    if not silence_intervals:
        return False

    # Get video duration
    try:
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path,
        ]
        probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
        video_duration = float(probe_result.stdout.strip())
    except Exception as e:
        logger.error(f"Failed to get video duration: {e}")
        return False

    # Build non-silent intervals (inverse of silence intervals with buffer)
    non_silent = []
    prev_end = 0.0

    for s_start, s_end in sorted(silence_intervals):
        # Add buffer: keep min_keep seconds into the silence
        keep_end = s_start + min_keep
        keep_start = s_end - min_keep

        if keep_end > prev_end:
            non_silent.append((prev_end, keep_end))
        prev_end = max(prev_end, keep_start)

    # Add remaining part after last silence
    if prev_end < video_duration:
        non_silent.append((prev_end, video_duration))

    # Filter out very short intervals
    non_silent = [(s, e) for s, e in non_silent if e - s >= 0.3]

    if not non_silent:
        logger.warning("No non-silent intervals found, keeping original")
        return False

    logger.info(f"Keeping {len(non_silent)} non-silent intervals (total: {sum(e-s for s,e in non_silent):.1f}s)")

    # Use concatenate_intervals to join non-silent parts
    return concatenate_intervals(video_path, non_silent, output_path)


# =========================
# Hook intro insertion (climax at start)
# =========================

def _check_is_ml_clip(clip_id: str) -> bool:
    """
    Check if this clip was generated via ML scoring (has ml_model_version set).
    Hook intro is only applied to ML-scored clips.
    """
    loop = get_event_loop()

    async def _check():
        async with get_session() as session:
            sql = text("SELECT ml_model_version FROM video_clips WHERE id = :cid")
            result = await session.execute(sql, {"cid": clip_id})
            row = result.fetchone()
            if row and row.ml_model_version:
                return True
            return False

    try:
        return loop.run_until_complete(_check())
    except Exception as e:
        logger.warning(f"[HOOK] Failed to check ml_model_version: {e}")
        return False


def detect_hook_moment(clip_path: str, segments: list, hook_duration: float = 2.5) -> tuple:
    """
    Detect the best "climax" moment in the clip for hook intro insertion.

    Strategy (priority order):
      1. Segment with emphasis=True AND highlight_words containing 'price' or 'cta'
      2. Segment with emphasis=True (loudest/most important)
      3. Audio peak detection (highest RMS energy window)

    Returns:
        (hook_start_sec, hook_end_sec) or (None, None) if no suitable moment found.
        Times are relative to the clip (0-based).
    """
    clip_duration = _get_video_duration_sec(clip_path)
    if not clip_duration or clip_duration < 8.0:
        # Too short for hook insertion (need at least 8s: 2.5s hook + 5.5s content)
        logger.info(f"[HOOK] Clip too short ({clip_duration}s) for hook insertion, skipping")
        return (None, None)

    best_start = None
    best_score = -1

    # Strategy 1 & 2: Use emphasis segments and highlight_words
    if segments:
        for seg in segments:
            seg_start = seg.get("start", 0)
            seg_end = seg.get("end", 0)
            seg_mid = (seg_start + seg_end) / 2

            # Skip segments in the first 3 seconds (would be redundant as hook)
            if seg_mid < 3.0:
                continue
            # Skip if segment is too close to the end
            if seg_start + hook_duration > clip_duration:
                continue

            score = 0
            is_emphasis = seg.get("emphasis", False)
            hw_list = seg.get("highlight_words", [])

            if is_emphasis:
                score += 10

            # Bonus for price/cta highlight words
            for hw in hw_list:
                hw_type = hw.get("type", "")
                if hw_type == "price":
                    score += 8  # Price reveal is very hook-worthy
                elif hw_type == "cta":
                    score += 6  # CTA is also compelling
                elif hw_type == "emotion":
                    score += 4  # Emotional moments are engaging
                elif hw_type == "product":
                    score += 2  # Product mentions are mildly interesting

            # Prefer moments later in the clip (more likely to be climax)
            position_bonus = min(seg_mid / clip_duration * 3, 3.0)
            score += position_bonus

            if score > best_score:
                best_score = score
                # Center the hook window around the segment
                best_start = max(0, seg_start - 0.3)  # Start slightly before

    # Strategy 3: Audio peak detection (fallback)
    if best_start is None or best_score < 5:
        logger.info("[HOOK] No strong emphasis segment found, trying audio peak detection...")
        try:
            audio_peak_start = _detect_audio_peak_window(clip_path, hook_duration, skip_first_sec=3.0)
            if audio_peak_start is not None:
                if best_start is None or best_score < 3:
                    best_start = audio_peak_start
                    best_score = 3
                    logger.info(f"[HOOK] Audio peak detected at {audio_peak_start:.1f}s")
        except Exception as e:
            logger.warning(f"[HOOK] Audio peak detection failed: {e}")

    if best_start is None:
        logger.info("[HOOK] No suitable hook moment found")
        return (None, None)

    hook_end = min(best_start + hook_duration, clip_duration)
    logger.info(f"[HOOK] Best hook moment: {best_start:.1f}s - {hook_end:.1f}s (score={best_score:.1f})")
    return (best_start, hook_end)


def _detect_audio_peak_window(video_path: str, window_sec: float = 2.5, skip_first_sec: float = 3.0) -> float:
    """
    Find the time window with highest average audio energy using FFmpeg volumedetect.
    Uses astats filter to get per-frame RMS values.

    Returns start time of the loudest window, or None.
    """
    # Extract audio levels using FFmpeg astats
    cmd = [
        FFMPEG_BIN, "-y",
        "-i", video_path,
        "-af", "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        output = result.stderr + result.stdout
    except Exception as e:
        logger.warning(f"[HOOK] astats failed: {e}")
        return None

    # Parse RMS levels with timestamps
    # Format: frame:N    pts:N    pts_time:N.NNN
    #         lavfi.astats.Overall.RMS_level=-N.N
    rms_data = []  # [(time_sec, rms_db)]
    lines = output.split("\n")
    current_time = None
    for line in lines:
        if "pts_time:" in line:
            try:
                t = float(line.split("pts_time:")[1].strip().split()[0])
                current_time = t
            except (ValueError, IndexError):
                pass
        elif "RMS_level=" in line and current_time is not None:
            try:
                rms_str = line.split("RMS_level=")[1].strip()
                if rms_str != "-inf":
                    rms_db = float(rms_str)
                    rms_data.append((current_time, rms_db))
            except (ValueError, IndexError):
                pass
            current_time = None

    if len(rms_data) < 5:
        logger.info(f"[HOOK] Not enough audio data points ({len(rms_data)})")
        return None

    # Find the window with highest average RMS (skip first N seconds)
    best_avg = -999
    best_time = None
    for i in range(len(rms_data)):
        t, _ = rms_data[i]
        if t < skip_first_sec:
            continue
        # Collect RMS values in the window
        window_vals = []
        for j in range(i, len(rms_data)):
            tj, rms_j = rms_data[j]
            if tj - t > window_sec:
                break
            window_vals.append(rms_j)
        if len(window_vals) >= 2:
            avg = sum(window_vals) / len(window_vals)
            if avg > best_avg:
                best_avg = avg
                best_time = t

    return best_time


def insert_hook_intro(clip_path: str, hook_start: float, hook_end: float,
                      output_path: str, crossfade_sec: float = 0.3) -> bool:
    """
    Insert a hook intro at the beginning of the clip.

    The hook is a short excerpt from the clip's climax moment, placed at the
    very beginning with a brief flash/crossfade transition, then the full
    clip plays from the start.

    Flow: [Hook 2-3s] → [Flash transition] → [Full clip from start]

    Uses FFmpeg filter_complex with xfade for smooth transition.
    """
    hook_duration = hook_end - hook_start
    if hook_duration < 1.0:
        logger.warning(f"[HOOK] Hook too short ({hook_duration:.1f}s), skipping")
        return False

    clip_duration = _get_video_duration_sec(clip_path)
    if not clip_duration:
        return False

    logger.info(f"[HOOK] Inserting hook intro: {hook_start:.1f}-{hook_end:.1f}s "
                f"({hook_duration:.1f}s) at start of {clip_duration:.1f}s clip")

    # Strategy: Use filter_complex to:
    # 1. Extract hook segment (trim)
    # 2. Keep full original clip
    # 3. Concatenate with xfade transition
    xfade_dur = min(crossfade_sec, hook_duration * 0.3, 0.5)

    # Video filter: trim hook, then xfade into full clip
    filter_complex = (
        f"[0:v]trim=start={hook_start:.3f}:end={hook_end:.3f},setpts=PTS-STARTPTS[hookv];"
        f"[0:v]setpts=PTS-STARTPTS[mainv];"
        f"[hookv][mainv]xfade=transition=fadewhite:duration={xfade_dur:.2f}:"
        f"offset={hook_duration - xfade_dur:.3f}[outv];"
        # Audio: trim hook, then acrossfade into full clip
        f"[0:a]atrim=start={hook_start:.3f}:end={hook_end:.3f},asetpts=PTS-STARTPTS[hooka];"
        f"[0:a]asetpts=PTS-STARTPTS[maina];"
        f"[hooka][maina]acrossfade=d={xfade_dur:.2f}:c1=tri:c2=tri[outa]"
    )

    cmd = [
        FFMPEG_BIN, "-y",
        "-i", clip_path,
        "-filter_complex", filter_complex,
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-movflags", "+faststart", "-r", "30",
        output_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                                preexec_fn=_limit_ffmpeg_memory)
        if result.returncode != 0:
            logger.error(f"[HOOK] FFmpeg xfade failed: {result.stderr[-500:]}")
            # Fallback: simple concat without crossfade
            return _insert_hook_simple_concat(clip_path, hook_start, hook_end, output_path)
        logger.info(f"[HOOK] Hook intro inserted successfully (xfade transition)")
        return True
    except subprocess.TimeoutExpired:
        logger.error("[HOOK] FFmpeg hook insertion timed out")
        return False
    except Exception as e:
        logger.error(f"[HOOK] Hook insertion failed: {e}")
        return _insert_hook_simple_concat(clip_path, hook_start, hook_end, output_path)


def _insert_hook_simple_concat(clip_path: str, hook_start: float, hook_end: float,
                                output_path: str) -> bool:
    """
    Fallback: Simple concat without crossfade transition.
    Extracts hook segment, then concatenates with full clip via concat demuxer.
    """
    work_dir = os.path.dirname(output_path)
    hook_path = os.path.join(work_dir, "hook_segment.mp4")

    # 1. Extract hook segment
    hook_duration = hook_end - hook_start
    cmd_hook = [
        FFMPEG_BIN, "-y",
        "-ss", f"{hook_start:.3f}",
        "-i", clip_path,
        "-t", f"{hook_duration:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-movflags", "+faststart", "-r", "30",
        hook_path,
    ]
    try:
        subprocess.run(cmd_hook, check=True, capture_output=True, text=True, timeout=120)
    except Exception as e:
        logger.error(f"[HOOK] Failed to extract hook segment: {e}")
        return False

    # 2. Create concat list
    concat_list = os.path.join(work_dir, "hook_concat.txt")
    with open(concat_list, "w") as f:
        f.write(f"file '{hook_path}'\n")
        f.write(f"file '{clip_path}'\n")

    # 3. Concatenate (stream copy since both are same codec settings)
    cmd_concat = [
        FFMPEG_BIN, "-y",
        "-f", "concat", "-safe", "0",
        "-i", concat_list,
        "-c", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    try:
        subprocess.run(cmd_concat, check=True, capture_output=True, text=True, timeout=120)
        logger.info("[HOOK] Hook intro inserted (simple concat fallback)")
        return True
    except Exception as e:
        logger.error(f"[HOOK] Simple concat failed: {e}")
        return False
    finally:
        for f_path in [hook_path, concat_list]:
            if os.path.exists(f_path):
                try:
                    os.remove(f_path)
                except OSError:
                    pass


def _adjust_captions_for_hook(captions_or_segments: list, hook_duration: float,
                               crossfade_sec: float = 0.3) -> list:
    """
    Shift all caption/segment timestamps forward by hook_duration to account
    for the hook intro that was prepended to the clip.

    The hook intro adds time at the beginning, so all original timestamps
    need to be offset by the hook duration (minus crossfade overlap).
    """
    offset = hook_duration - crossfade_sec
    if offset <= 0:
        return captions_or_segments

    adjusted = []
    for item in captions_or_segments:
        new_item = dict(item)
        new_item["start"] = round(item.get("start", 0) + offset, 3)
        new_item["end"] = round(item.get("end", 0) + offset, 3)
        # Adjust word-level timestamps too
        if "words" in new_item and new_item["words"]:
            new_words = []
            for w in new_item["words"]:
                new_w = dict(w)
                new_w["start"] = round(w.get("start", 0) + offset, 3)
                new_w["end"] = round(w.get("end", 0) + offset, 3)
                new_words.append(new_w)
            new_item["words"] = new_words
        adjusted.append(new_item)
    return adjusted


# =========================
# Sound effect (SE) auto-insertion
# =========================

# SE types and their default local paths (fallback; prefer Azure Blob)
_SE_TYPES = {
    "hook_transition": "whoosh_transition.mp3",
    "price_reveal": "price_reveal.mp3",
    "cta_attention": "cta_attention.mp3",
}

# Azure Blob path for SE files (shared across all clips)
_SE_BLOB_PREFIX = "shared/sound_effects"


def _get_se_file(se_type: str, work_dir: str) -> str | None:
    """
    Get a sound effect file for the given type.
    First tries to download from Azure Blob, then falls back to local bundled files.

    Returns local file path or None if not available.
    """
    filename = _SE_TYPES.get(se_type)
    if not filename:
        return None

    local_path = os.path.join(work_dir, filename)
    if os.path.exists(local_path):
        return local_path

    # Try Azure Blob download
    try:
        from split_video import AZURE_STORAGE_CONNECTION_STRING, AZURE_BLOB_CONTAINER
        if AZURE_STORAGE_CONNECTION_STRING:
            from azure.storage.blob import BlobServiceClient
            blob_name = f"{_SE_BLOB_PREFIX}/{filename}"
            bsc = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
            blob_client = bsc.get_blob_client(container=AZURE_BLOB_CONTAINER, blob=blob_name)
            with open(local_path, "wb") as f:
                data = blob_client.download_blob().readall()
                f.write(data)
            if os.path.getsize(local_path) > 100:
                logger.info(f"[SE] Downloaded {se_type} from blob: {blob_name}")
                return local_path
            else:
                os.remove(local_path)
    except Exception as e:
        logger.debug(f"[SE] Blob download failed for {se_type}: {e}")

    # Fallback: bundled SE files in worker/batch/sound_effects/
    bundled_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sound_effects")
    bundled_path = os.path.join(bundled_dir, filename)
    if os.path.exists(bundled_path) and os.path.getsize(bundled_path) > 100:
        import shutil
        shutil.copy2(bundled_path, local_path)
        logger.info(f"[SE] Using bundled SE file: {bundled_path}")
        return local_path

    # Fallback: generate synthetic SE using FFmpeg
    return _generate_synthetic_se(se_type, local_path)


def _generate_synthetic_se(se_type: str, output_path: str) -> str | None:
    """
    Generate a synthetic sound effect using FFmpeg sine wave synthesis.
    Used as last-resort fallback when no pre-made SE files are available.
    """
    try:
        if se_type == "hook_transition":
            # Whoosh: frequency sweep 800Hz, short fade
            cmd = [
                FFMPEG_BIN, "-y",
                "-f", "lavfi", "-i", "sine=frequency=800:duration=0.5",
                "-af", "afade=t=in:d=0.1,afade=t=out:d=0.3,asetrate=44100*1.5,aresample=44100,volume=0.6",
                "-c:a", "aac", "-b:a", "128k",
                output_path,
            ]
        elif se_type == "price_reveal":
            # Ascending dual tone
            cmd = [
                FFMPEG_BIN, "-y",
                "-f", "lavfi", "-i", "sine=frequency=1200:duration=0.3",
                "-f", "lavfi", "-i", "sine=frequency=1800:duration=0.3",
                "-filter_complex", "[0][1]amix=inputs=2:duration=shortest,afade=t=in:d=0.05,afade=t=out:d=0.15,volume=0.5",
                "-c:a", "aac", "-b:a", "128k",
                output_path,
            ]
        elif se_type == "cta_attention":
            # Double beep
            cmd = [
                FFMPEG_BIN, "-y",
                "-f", "lavfi", "-i", "sine=frequency=1000:duration=0.15",
                "-f", "lavfi", "-i", "sine=frequency=1500:duration=0.15",
                "-filter_complex", "[0]apad=pad_dur=0.1[a0];[a0][1]concat=n=2:v=0:a=1,afade=t=out:d=0.1,volume=0.5",
                "-c:a", "aac", "-b:a", "128k",
                output_path,
            ]
        else:
            return None

        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 50:
            logger.info(f"[SE] Generated synthetic SE: {se_type}")
            return output_path
    except Exception as e:
        logger.warning(f"[SE] Failed to generate synthetic SE {se_type}: {e}")
    return None


def detect_se_insertion_points(segments: list, hook_applied: bool = False,
                               hook_duration: float = 0.0) -> list:
    """
    Detect where to insert sound effects based on segment metadata.

    Returns list of dicts: [{"time": float, "se_type": str, "volume": float}]
    """
    points = []

    # 1. Hook transition SE (at the crossfade point)
    if hook_applied and hook_duration > 0:
        points.append({
            "time": max(0, hook_duration - 0.3),  # Just before crossfade
            "se_type": "hook_transition",
            "volume": 0.4,  # Subtle
        })

    # 2. Scan segments for price/cta highlight_words
    for seg in segments:
        hw_list = seg.get("highlight_words", [])
        if not hw_list:
            continue

        seg_start = seg.get("start", 0)

        for hw in hw_list:
            hw_type = hw.get("type", "")
            hw_word = hw.get("word", "")

            # Find the exact timestamp of the highlight word in the segment's words
            hw_time = seg_start  # Default to segment start
            for w in seg.get("words", []):
                if hw_word and hw_word in w.get("word", ""):
                    hw_time = w.get("start", seg_start)
                    break

            if hw_type == "price":
                points.append({
                    "time": max(0, hw_time - 0.1),  # Slightly before price reveal
                    "se_type": "price_reveal",
                    "volume": 0.35,
                })
            elif hw_type == "cta":
                points.append({
                    "time": max(0, hw_time - 0.05),
                    "se_type": "cta_attention",
                    "volume": 0.3,
                })

    # Deduplicate: remove SE points that are too close together (< 1.5s)
    if len(points) > 1:
        points.sort(key=lambda p: p["time"])
        deduped = [points[0]]
        for p in points[1:]:
            if p["time"] - deduped[-1]["time"] >= 1.5:
                deduped.append(p)
        points = deduped

    # Cap at 5 SE insertions per clip to avoid over-saturation
    if len(points) > 5:
        # Keep hook_transition + top 4 by priority (price > cta)
        hook_points = [p for p in points if p["se_type"] == "hook_transition"]
        price_points = [p for p in points if p["se_type"] == "price_reveal"]
        cta_points = [p for p in points if p["se_type"] == "cta_attention"]
        points = hook_points + price_points[:3] + cta_points[:1]
        points.sort(key=lambda p: p["time"])

    se_summary = [(p['se_type'], round(p['time'], 1)) for p in points]
    logger.info(f"[SE] Detected {len(points)} SE insertion points: {se_summary}")
    return points


def insert_sound_effects(clip_path: str, se_points: list, output_path: str,
                          work_dir: str) -> bool:
    """
    Insert sound effects into the clip at specified time points.
    Uses FFmpeg amix filter to overlay SE audio onto the clip's audio track.

    Args:
        clip_path: Path to the input clip
        se_points: List of {"time": float, "se_type": str, "volume": float}
        output_path: Path for the output clip with SE
        work_dir: Working directory for temp files

    Returns True if successful.
    """
    if not se_points:
        return False

    # Download/prepare SE files
    se_files = {}  # se_type -> local_path
    for p in se_points:
        st = p["se_type"]
        if st not in se_files:
            se_path = _get_se_file(st, work_dir)
            if se_path:
                se_files[st] = se_path

    # Filter out points without available SE files
    valid_points = [p for p in se_points if p["se_type"] in se_files]
    if not valid_points:
        logger.warning("[SE] No valid SE files available, skipping")
        return False

    logger.info(f"[SE] Inserting {len(valid_points)} sound effects")

    # Build FFmpeg command with adelay + amix
    # Input 0: original clip
    # Input 1..N: SE files
    inputs = ["-i", clip_path]
    filter_parts = []
    mix_inputs = ["[0:a]"]

    for i, p in enumerate(valid_points):
        se_path = se_files[p["se_type"]]
        inputs.extend(["-i", se_path])

        delay_ms = int(p["time"] * 1000)
        vol = p.get("volume", 0.3)
        idx = i + 1

        # Delay the SE to the correct time position and adjust volume
        filter_parts.append(
            f"[{idx}:a]adelay={delay_ms}|{delay_ms},volume={vol:.2f}[se{i}]"
        )
        mix_inputs.append(f"[se{i}]")

    # Mix all audio streams
    n_inputs = len(mix_inputs)
    mix_str = "".join(mix_inputs)
    filter_parts.append(
        f"{mix_str}amix=inputs={n_inputs}:duration=first:dropout_transition=0[outa]"
    )

    filter_complex = ";".join(filter_parts)

    cmd = [
        FFMPEG_BIN, "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "0:v",  # Keep original video
        "-map", "[outa]",  # Use mixed audio
        "-c:v", "copy",  # No re-encode for video
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                                preexec_fn=_limit_ffmpeg_memory)
        if result.returncode != 0:
            logger.error(f"[SE] FFmpeg amix failed: {result.stderr[-500:]}")
            return False
        logger.info(f"[SE] Sound effects inserted successfully ({len(valid_points)} SEs)")
        return True
    except subprocess.TimeoutExpired:
        logger.error("[SE] FFmpeg SE insertion timed out")
        return False
    except Exception as e:
        logger.error(f"[SE] SE insertion failed: {e}")
        return False


# =========================
# Main pipeline
# =========================

def _ensure_fresh_sas_url(blob_url: str) -> str:
    """Ensure the blob_url has a valid (non-expired) SAS token.
    If the SAS token is expired or will expire within 30 minutes,
    regenerate a fresh one using AZURE_STORAGE_CONNECTION_STRING.
    Returns the original URL if no SAS token is present or regeneration fails."""
    if "?" not in blob_url or "sig=" not in blob_url:
        return blob_url  # No SAS token, return as-is

    try:
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(blob_url)
        params = parse_qs(parsed.query)
        se_values = params.get("se", [])
        if se_values:
            expiry_str = se_values[0]
            # Parse expiry datetime (format: 2026-04-17T12:00:00Z)
            expiry_dt = datetime.strptime(expiry_str, "%Y-%m-%dT%H:%M:%SZ")
            now = datetime.utcnow()
            remaining = (expiry_dt - now).total_seconds()
            if remaining > 1800:  # More than 30 min remaining
                logger.info(f"[SAS] Token still valid ({remaining/60:.0f} min remaining)")
                return blob_url
            logger.warning(f"[SAS] Token expired or expiring soon ({remaining/60:.0f} min remaining), regenerating...")
        else:
            logger.warning("[SAS] No 'se' param found in SAS URL, attempting regeneration")
    except Exception as e:
        logger.warning(f"[SAS] Could not parse SAS expiry: {e}, attempting regeneration")

    # Regenerate SAS URL
    try:
        from process_video import _regenerate_sas_url
        new_url = _regenerate_sas_url(blob_url)
        logger.info("[SAS] Successfully regenerated fresh SAS URL")
        return new_url
    except Exception as e:
        logger.error(f"[SAS] Failed to regenerate SAS URL: {e}")
        return blob_url  # Return original as fallback


def generate_clip(clip_id: str, video_id: str, blob_url: str, time_start: float, time_end: float, phase_index = -1, speed_factor: float = 1.0, subtitle_language: str = "ja", skip_person_detection: bool = False, skip_silence_removal: bool = False, force_reencode_cut: bool = False):
    """Main clip generation pipeline."""
    logger.info(f"=== Starting clip generation ===")
    logger.info(f"clip_id={clip_id}, video_id={video_id}, speed={speed_factor}x, subtitle_language={subtitle_language}")
    logger.info(f"time_range={time_start:.1f}s - {time_end:.1f}s")

    # Initialize DB
    init_db_sync()

    # Ensure blob_url has a fresh SAS token (expired tokens cause download failures)
    blob_url = _ensure_fresh_sas_url(blob_url)

    # Update status to processing + clear previous logs
    update_clip_status(clip_id, "processing")
    # Reset processing_logs for fresh run (graceful if column doesn't exist yet)
    _reset_loop = get_event_loop()
    async def _reset_logs():
        try:
            async with get_session() as _s:
                await _s.execute(text("UPDATE video_clips SET processing_logs = CAST('[]' AS jsonb) WHERE id = :cid"), {"cid": clip_id})
        except Exception:
            logger.debug("processing_logs column not available yet, skipping reset")
    _reset_loop.run_until_complete(_reset_logs())
    # Add metadata to processing_logs for AI Editor Monitor UI
    _clip_meta_msg = (f"\U0001f4cb Clip metadata: source={time_start:.1f}s-{time_end:.1f}s "
                      f"(duration={time_end - time_start:.1f}s), phase_index={phase_index}")
    update_clip_progress(clip_id, 2, "initializing", log_message=_clip_meta_msg)
    update_clip_progress(clip_id, 5, "downloading", log_message="\u2b07\ufe0f Starting source video download...")

    work_dir = tempfile.mkdtemp(prefix=f"clip_{clip_id}_")
    logger.info(f"Work directory: {work_dir}")

    # Pre-calculate safe_start/safe_end before try block (used in progress messages)
    margin = 5.0  # seconds extra on each side for speech boundary adjustment
    safe_start = max(0.0, time_start - margin)
    safe_end = time_end + margin
    safe_duration = safe_end - safe_start

    try:
        # 1. Download ONLY the needed segment (not the entire video)
        # Use ffmpeg -ss with URL to avoid downloading multi-GB files
        # This is critical for 2h+ videos where full download exceeds timeout
        update_clip_progress(clip_id, 10, "downloading", log_message=f"\u2b07\ufe0f Downloading segment ({safe_start:.0f}s-{safe_end:.0f}s) from source video")

        segment_path = os.path.join(work_dir, "segment.mp4")
        source_path = None  # May be set if full download fallback is needed

        # Try direct URL cut first (downloads only the needed portion)
        direct_cut_ok = False

        logger.info(f"[DIRECT_CUT] Attempting ffmpeg cut from URL (range={safe_start:.1f}-{safe_end:.1f}s)")
        wider_segment_path = os.path.join(work_dir, "wider_segment.mp4")
        cmd_direct = [
            FFMPEG_BIN, "-y",
            "-ss", f"{safe_start:.3f}",
            "-i", blob_url,
            "-t", f"{safe_duration:.3f}",
            *(["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-c:a", "copy"] if force_reencode_cut else ["-c", "copy"]),
            "-movflags", "+faststart",
            "-avoid_negative_ts", "make_zero",
            wider_segment_path,
        ]
        try:
            result = subprocess.run(cmd_direct, capture_output=True, text=True, timeout=300)
            if result.returncode == 0 and os.path.exists(wider_segment_path) and os.path.getsize(wider_segment_path) > 0:
                actual_dur = _get_video_duration_sec(wider_segment_path)
                if actual_dur and actual_dur > 1.0:
                    direct_cut_ok = True
                    source_path = wider_segment_path
                    # Adjust time references: now relative to wider_segment start
                    time_start_local = time_start - safe_start
                    time_end_local = time_end - safe_start
                    logger.info(f"[DIRECT_CUT] Success! Downloaded {os.path.getsize(wider_segment_path) / 1024 / 1024:.1f}MB "
                                f"(duration={actual_dur:.1f}s) instead of full video")
                else:
                    logger.warning(f"[DIRECT_CUT] Output too short ({actual_dur}s), falling back to full download")
            else:
                logger.warning(f"[DIRECT_CUT] Failed (rc={result.returncode}), falling back to full download")
                if result.stderr:
                    logger.warning(f"[DIRECT_CUT] stderr: {result.stderr[-300:]}")
        except subprocess.TimeoutExpired:
            logger.warning("[DIRECT_CUT] Timed out after 300s, falling back to full download")
        except Exception as e:
            logger.warning(f"[DIRECT_CUT] Error: {e}, falling back to full download")

        if not direct_cut_ok:
            # Fallback: download entire video (original behavior)
            logger.info("[FALLBACK] Downloading full video...")
            source_path = os.path.join(work_dir, "source.mp4")
            download_video(blob_url, source_path)
            if not os.path.exists(source_path) or os.path.getsize(source_path) == 0:
                raise RuntimeError("Failed to download source video")
            time_start_local = time_start
            time_end_local = time_end

        update_clip_progress(clip_id, 15, "speech_boundary", log_message="\U0001f50a Detecting speech boundaries to avoid mid-sentence cuts")

        # 1.5. Speech-Aware Cut: adjust boundaries to avoid mid-sentence cuts
        logger.info("[SPEECH_CUT] Adjusting clip boundaries to speech boundaries...")
        adj_start, adj_end = adjust_cut_to_speech_boundary(
            source_path, time_start_local, time_end_local, search_window=3.0
        )
        if (adj_start, adj_end) != (time_start_local, time_end_local):
            logger.info(
                f"[SPEECH_CUT] Boundaries adjusted: {time_start_local:.2f}-{time_end_local:.2f} "
                f"→ {adj_start:.2f}-{adj_end:.2f}"
            )
            time_start_local, time_end_local = adj_start, adj_end

        update_clip_progress(clip_id, 20, "cutting", log_message=f"\u2702\ufe0f Cutting segment: {time_start_local:.1f}s \u2192 {time_end_local:.1f}s ({time_end_local - time_start_local:.1f}s)")

        # 2. Cut exact segment from source (or wider segment)
        if direct_cut_ok:
            # Re-cut from wider segment to get exact boundaries after speech adjustment
            logger.info("Cutting exact segment from wider segment...")
            if not cut_segment(source_path, segment_path, time_start_local, time_end_local):
                raise RuntimeError("Failed to cut segment from wider segment")
        else:
            logger.info("Cutting segment from full source...")
            if not cut_segment(source_path, segment_path, time_start_local, time_end_local):
                raise RuntimeError("Failed to cut segment")

        # --- Intermediate preview: raw cut ---
        _prev_url = _upload_intermediate_preview(segment_path, clip_id, "01_raw_cut")
        if _prev_url:
            update_clip_progress(clip_id, 25, "cutting", log_message="\U0001f3ac Raw cut preview ready — tap to watch", preview_url=_prev_url)

        update_clip_progress(clip_id, 30, "person_detection", log_message="\U0001f9d1 Detecting person presence in video frames")

        # 2.5. Person detection: remove scenes without people
        if skip_person_detection:
            logger.info("[SKIP] Person detection skipped (--skip-person-detection)")
            person_intervals = None
        else:
            person_intervals = detect_person_intervals(segment_path)
        if person_intervals is not None:  # None means detection unavailable
            if len(person_intervals) == 0:
                logger.warning("No person detected in entire segment, using original")
                # Keep original segment as-is
            else:
                filtered_path = os.path.join(work_dir, "segment_filtered.mp4")
                if concatenate_intervals(segment_path, person_intervals, filtered_path):
                    logger.info(f"Filtered segment: kept {len(person_intervals)} person intervals")
                    update_clip_progress(clip_id, 35, "person_detection", log_message=f"\u2705 Person detected: kept {len(person_intervals)} intervals with people")
                    segment_path = filtered_path  # Use filtered version
                else:
                    logger.warning("Failed to filter person intervals, using original segment")
        else:
            logger.info("Person detection not available, using original segment")

        update_clip_progress(clip_id, 45, "silence_removal", log_message="\U0001f507 Scanning for silence intervals to remove dead air")

        # 2.7. Silence detection: remove silent intervals (coughing, dead air, etc.)
        if skip_silence_removal:
            logger.info("[SKIP] Silence removal skipped (--skip-silence-removal)")
            silence_intervals = []
        else:
            logger.info("Running silence detection...")
            silence_intervals = detect_silence_intervals(segment_path, noise_threshold="-35dB", min_silence_duration=0.8)
        if silence_intervals:
            desilenced_path = os.path.join(work_dir, "segment_desilenced.mp4")
            if remove_silence_from_video(segment_path, desilenced_path, silence_intervals):
                removed_duration = sum(e - s for s, e in silence_intervals)
                logger.info(f"Removed {removed_duration:.1f}s of silence from segment")
                update_clip_progress(clip_id, 50, "silence_removal", log_message=f"\u2705 Removed {removed_duration:.1f}s of silence ({len(silence_intervals)} intervals)")
                segment_path = desilenced_path  # Use desilenced version
            else:
                logger.warning("Failed to remove silence, using segment as-is")
        else:
            logger.info("No significant silence detected")

        # --- Intermediate preview: after silence removal ---
        _prev_url = _upload_intermediate_preview(segment_path, clip_id, "02_cleaned")
        if _prev_url:
            update_clip_progress(clip_id, 52, "silence_removal", log_message="\U0001f9f9 Cleaned segment preview ready — silence removed", preview_url=_prev_url)

        update_clip_progress(clip_id, 55, "transcribing", log_message="\U0001f3a4 Running Whisper AI speech-to-text transcription")

        # 3. Extract audio and transcribe
        audio_path = os.path.join(work_dir, "audio.wav")
        segments = []
        if extract_audio(segment_path, audio_path):
            segments = transcribe_audio(audio_path, subtitle_language=subtitle_language)
            logger.info(f"Got {len(segments)} raw subtitle segments from Whisper (lang={subtitle_language})")
            update_clip_progress(clip_id, 60, "transcribing", log_message=f"\u2705 Whisper transcription complete: {len(segments)} segments detected (lang={subtitle_language})")
        else:
            logger.warning("Audio extraction failed, proceeding without subtitles")

        update_clip_progress(clip_id, 65, "refining_subtitles", log_message="\u2728 Refining subtitles with GPT-4.1-mini (emphasis + highlight detection)")

        # 3.5. GPT subtitle refinement (merge fragments, fix errors, add emphasis)
        if segments:
            phase_context = ""
            # phase_index can be int or string (e.g. "moment_strong_1")
            _use_phase_context = False
            try:
                _use_phase_context = int(phase_index) >= 0
            except (ValueError, TypeError):
                # String phase_index like "moment_strong_1" — skip phase context lookup
                pass
            if _use_phase_context:
                try:
                    phase_context = get_phase_context(video_id, phase_index)
                    if phase_context:
                        logger.info(f"Got phase context ({len(phase_context)} chars) for subtitle refinement")
                except Exception as e:
                    logger.warning(f"Failed to get phase context: {e}")

            # Fetch product names for domain-specific vocabulary
            product_names = []
            try:
                product_names = get_product_names(video_id)
                if product_names:
                    logger.info(f"Got {len(product_names)} product names for subtitle refinement: {product_names[:5]}")
            except Exception as e:
                logger.warning(f"Failed to get product names: {e}")

            # Fetch subtitle dictionary entries for GPT prompt
            dict_entries = []
            try:
                dict_entries = get_subtitle_dictionary()
                if dict_entries:
                    logger.info(f"Got {len(dict_entries)} dictionary entries for subtitle refinement")
            except Exception as e:
                logger.warning(f"Failed to get subtitle dictionary: {e}")

            logger.info("Refining subtitles with GPT-4.1-mini...")
            segments = refine_subtitles_with_gpt(segments, phase_context, product_names=product_names, subtitle_language=subtitle_language, dict_entries=dict_entries)
            logger.info(f"After GPT refinement: {len(segments)} subtitle segments")
            # Count emphasis and highlight_words for log
            _emph_count = sum(1 for s in segments if s.get('emphasis'))
            _hw_count = sum(len(s.get('highlight_words', [])) for s in segments)
            update_clip_progress(clip_id, 72, "refining_subtitles", log_message=f"\u2705 GPT refinement done: {len(segments)} segments, {_emph_count} emphasis, {_hw_count} highlight words")
            # Log subtitle preview for AI Editor Monitor (first 3 segments)
            for _si, _seg in enumerate(segments[:3]):
                _seg_text = _seg.get('text', '')[:50]
                update_clip_progress(clip_id, 73, "subtitle_preview",
                                    log_message=f"\U0001f4ac [{_seg.get('start', 0):.1f}s] {_seg_text}")

            # Post-GPT dictionary replacement fallback (ensures dictionary is always applied)
            if dict_entries:
                replacement_entries = [e for e in dict_entries if e.get("to") and e["to"] != e["from"]]
                if replacement_entries:
                    replacement_count = 0
                    for seg in segments:
                        original_text = seg.get("text", "")
                        new_text = original_text
                        for entry in replacement_entries:
                            if entry["from"] in new_text:
                                new_text = new_text.replace(entry["from"], entry["to"])
                        if new_text != original_text:
                            seg["text"] = new_text
                            replacement_count += 1
                    if replacement_count > 0:
                        logger.info(f"Post-GPT dictionary replacement applied to {replacement_count} segments")

            # Post-GPT: replace 「、」 with half-width space in all segments
            comma_fix_count = 0
            for seg in segments:
                txt = seg.get("text", "")
                if "、" in txt or "。" in txt:
                    seg["text"] = txt.replace("、", " ").replace("。", "")
                    comma_fix_count += 1
            if comma_fix_count > 0:
                logger.info(f"Replaced punctuation (、/。) with spaces in {comma_fix_count} segments")

        update_clip_progress(clip_id, 75, "creating_clip", log_message="\U0001f3ac Creating 1080x1920 vertical clip with FFmpeg")

        # 4. Create vertical clip WITHOUT burned-in subtitles
        # Subtitles are rendered as overlay in the frontend (ClipEditorV2)
        # and burned in only during "Export MP4" via the export API.
        # This avoids double-subtitle display.
        clip_path = os.path.join(work_dir, "clip_final.mp4")
        logger.info("Creating vertical clip (no burned-in subtitles)...")
        width, height = get_video_dimensions(segment_path)
        target_w, target_h = 1080, 1920
        source_ratio = width / height
        target_ratio = target_w / target_h
        if source_ratio > target_ratio:
            crop_h = height
            crop_w = int(height * target_ratio)
            crop_x = (width - crop_w) // 2
            crop_y = 0
        else:
            crop_w = width
            crop_h = int(width / target_ratio)
            crop_x = 0
            crop_y = (height - crop_h) // 2

        # Build ffmpeg command: crop → scale → speed adjustment (no subtitles)
        vf_parts = [
            f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}",
            f"scale={target_w}:{target_h}:flags=lanczos",
        ]
        if speed_factor != 1.0 and speed_factor > 0:
            vf_parts.append(f"setpts=PTS/{speed_factor}")

        cmd = [
            FFMPEG_BIN, "-y",
            "-i", segment_path,
            "-vf", ",".join(vf_parts),
        ]
        if speed_factor != 1.0 and speed_factor > 0:
            atempo_filters = []
            remaining = speed_factor
            while remaining > 2.0:
                atempo_filters.append("atempo=2.0")
                remaining /= 2.0
            while remaining < 0.5:
                atempo_filters.append("atempo=0.5")
                remaining /= 0.5
            atempo_filters.append(f"atempo={remaining:.4f}")
            cmd.extend(["-af", ",".join(atempo_filters)])
        cmd.extend([
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
            "-movflags", "+faststart", "-r", "30",
            clip_path,
        ])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            if result.returncode != 0:
                logger.error(f"FFmpeg nosub stderr: {result.stderr[-500:]}")
                # Fallback to create_vertical_clip_nosub helper
                if not create_vertical_clip_nosub(segment_path, clip_path,
                                                   crop_w, crop_h, crop_x, crop_y, target_w, target_h):
                    raise RuntimeError("Failed to create vertical clip")
            else:
                logger.info(f"Vertical clip created (no subtitles, speed={speed_factor}x)")
        except subprocess.TimeoutExpired:
            raise RuntimeError("FFmpeg timed out creating vertical clip")
        except Exception as e:
            logger.error(f"FFmpeg nosub failed: {e}")
            if not create_vertical_clip_nosub(segment_path, clip_path,
                                               crop_w, crop_h, crop_x, crop_y, target_w, target_h):
                raise RuntimeError("Failed to create vertical clip")

        if not os.path.exists(clip_path) or os.path.getsize(clip_path) == 0:
            raise RuntimeError("Output clip file is empty")

        clip_size_mb = os.path.getsize(clip_path) / (1024 * 1024)
        logger.info(f"Clip created: {os.path.getsize(clip_path)} bytes")
        # --- Intermediate preview: vertical clip (no subtitles) ---
        _prev_url = _upload_intermediate_preview(clip_path, clip_id, "03_vertical")
        if _prev_url:
            update_clip_progress(clip_id, 78, "creating_clip", log_message=f"\U0001f4f1 Vertical clip preview ready ({clip_size_mb:.1f}MB)", preview_url=_prev_url)

        update_clip_progress(clip_id, 80, "creating_clip", log_message=f"\u2705 Vertical clip created: {clip_size_mb:.1f}MB")

        # 5. Hook intro insertion (climax at start) — ML-scored clips only
        hook_applied = False
        hook_duration_actual = 0.0
        try:
            is_ml_clip = _check_is_ml_clip(clip_id)
            if is_ml_clip:
                update_clip_progress(clip_id, 82, "hook_detection", log_message="\U0001f3af ML clip detected! Scanning for climax moment to use as hook intro")
                logger.info("[HOOK] ML-scored clip detected, attempting hook intro insertion...")
                hook_start, hook_end = detect_hook_moment(clip_path, segments)
                if hook_start is not None and hook_end is not None:
                    update_clip_progress(clip_id, 85, "hook_insertion", log_message=f"\U0001f525 Inserting hook intro: {hook_start:.1f}s-{hook_end:.1f}s (best climax) with fadewhite transition")
                    hooked_path = os.path.join(work_dir, "clip_hooked.mp4")
                    if insert_hook_intro(clip_path, hook_start, hook_end, hooked_path):
                        # Verify hooked clip is valid
                        hooked_dur = _get_video_duration_sec(hooked_path)
                        if hooked_dur and hooked_dur > _get_video_duration_sec(clip_path):
                            hook_duration_actual = hook_end - hook_start
                            # Replace clip_path with hooked version
                            os.replace(hooked_path, clip_path)
                            hook_applied = True
                            # Adjust segment timestamps to account for prepended hook
                            segments = _adjust_captions_for_hook(segments, hook_duration_actual)
                            logger.info(f"[HOOK] Hook intro applied! Duration added: {hook_duration_actual:.1f}s")
                            # --- Intermediate preview: with hook ---
                            _prev_url = _upload_intermediate_preview(clip_path, clip_id, "04_hooked")
                            if _prev_url:
                                update_clip_progress(clip_id, 86, "hook_insertion", log_message=f"\U0001f525 Hook intro preview ready (+{hook_duration_actual:.1f}s climax)", preview_url=_prev_url)
                        else:
                            logger.warning(f"[HOOK] Hooked clip invalid (dur={hooked_dur}), keeping original")
                            if os.path.exists(hooked_path):
                                os.remove(hooked_path)
                    else:
                        logger.info("[HOOK] Hook insertion failed, keeping original clip")
                else:
                    logger.info("[HOOK] No suitable hook moment found, keeping original clip")
            else:
                logger.info("[HOOK] Not an ML-scored clip, skipping hook insertion")
        except Exception as hook_err:
            logger.warning(f"[HOOK] Hook insertion error (non-fatal): {hook_err}")
            # Continue with original clip — hook is a nice-to-have, not critical

        # 5b. Sound effect auto-insertion — ML-scored clips only
        try:
            if _check_is_ml_clip(clip_id):
                se_points = detect_se_insertion_points(segments, hook_applied=hook_applied,
                                                       hook_duration=hook_duration_actual)
                if se_points:
                    update_clip_progress(clip_id, 87, "sound_effects", log_message=f"\U0001f50a Inserting {len(se_points)} sound effects (transition/price/CTA)")
                    se_output_path = os.path.join(work_dir, "clip_with_se.mp4")
                    if insert_sound_effects(clip_path, se_points, se_output_path, work_dir):
                        # Verify SE clip is valid
                        se_dur = _get_video_duration_sec(se_output_path)
                        orig_dur = _get_video_duration_sec(clip_path)
                        if se_dur and orig_dur and abs(se_dur - orig_dur) < 1.0:
                            os.replace(se_output_path, clip_path)
                            logger.info(f"[SE] Sound effects applied ({len(se_points)} SEs)")
                            # --- Intermediate preview: with sound effects ---
                            _prev_url = _upload_intermediate_preview(clip_path, clip_id, "05_with_se")
                            if _prev_url:
                                update_clip_progress(clip_id, 89, "sound_effects", log_message=f"\U0001f3b5 Sound effects preview ready ({len(se_points)} SEs)", preview_url=_prev_url)
                        else:
                            logger.warning(f"[SE] SE clip duration mismatch (se={se_dur}, orig={orig_dur}), keeping original")
                            if os.path.exists(se_output_path):
                                os.remove(se_output_path)
                    else:
                        logger.info("[SE] SE insertion failed, keeping original clip")
                else:
                    logger.info("[SE] No SE insertion points detected")
        except Exception as se_err:
            logger.warning(f"[SE] SE insertion error (non-fatal): {se_err}")

        update_clip_progress(clip_id, 90, "uploading", log_message="\u2601\ufe0f Uploading finished clip to Azure Blob Storage")

        # 6. Upload to Azure Blob
        blob_info = parse_blob_url(blob_url)
        ts_str = f"{time_start:.0f}"
        te_str = f"{time_end:.0f}"
        clip_blob_name = f"{blob_info['parent_path']}/clips/clip_{ts_str}_{te_str}.mp4" if blob_info['parent_path'] else f"clips/clip_{ts_str}_{te_str}.mp4"

        logger.info(f"Uploading clip to blob: {clip_blob_name}")
        uploaded_url = upload_to_blob(clip_path, clip_blob_name)

        if not uploaded_url:
            raise RuntimeError("Failed to upload clip to blob storage")

        logger.info(f"Clip uploaded: {uploaded_url}")
        update_clip_progress(clip_id, 95, "uploading", log_message="\u2705 Upload complete! Saving metadata to database...")

        # 7. Update DB with completed status + save captions
        # Convert segments to captions format for frontend
        captions_data = []
        for seg in segments:
            cap_entry = {
                "start": round(seg.get("start", 0), 3),
                "end": round(seg.get("end", 0), 3),
                "text": seg.get("text", ""),
                "words": [
                    {"word": w.get("word", ""), "start": round(w.get("start", 0), 3), "end": round(w.get("end", 0), 3)}
                    for w in seg.get("words", [])
                ] if seg.get("words") else [],
                "source": "whisper",
                "language": subtitle_language,
            }
            # Preserve highlight_words and emphasis from GPT refinement
            if seg.get("highlight_words"):
                cap_entry["highlight_words"] = seg["highlight_words"]
            if seg.get("emphasis"):
                cap_entry["emphasis"] = True
            captions_data.append(cap_entry)

        update_clip_status(clip_id, "completed", clip_url=uploaded_url, captions=captions_data if captions_data else None)
        hook_info = f", hook={hook_duration_actual:.1f}s" if hook_applied else ""
        logger.info(f"=== Clip generation completed successfully ({len(captions_data)} captions saved{hook_info}) ===")
        update_clip_progress(clip_id, 100, "completed", log_message=f"\U0001f389 Clip generation complete! {len(captions_data)} subtitles saved{hook_info}")

        # 8. Auto-enrich clip metadata (Clip DB)
        try:
            _enrich_clip_after_generation(clip_id, video_id, phase_index, captions_data, segments)
            logger.info(f"[ClipDB] Auto-enriched clip {clip_id}")
        except Exception as enrich_err:
            logger.warning(f"[ClipDB] Auto-enrich failed (non-fatal): {enrich_err}")

    except Exception as e:
        logger.exception(f"Clip generation failed: {e}")
        update_clip_status(clip_id, "failed", error_message=str(e)[:500])

    finally:
        # Cleanup work directory
        try:
            import shutil
            shutil.rmtree(work_dir, ignore_errors=True)
            logger.info(f"Cleaned up work directory: {work_dir}")
        except Exception as _e:
            logger.debug(f"Suppressed: {_e}")

        close_db_sync()


# =========================
# Language detection (inline, no external deps)
# =========================
import unicodedata as _ud_worker

def _detect_language_from_text(text_str: str) -> str:
    """Detect language from transcript text using Unicode character analysis."""
    if not text_str or len(text_str.strip()) < 5:
        return "unknown"
    text_str = text_str.strip()
    hiragana = katakana = cjk = hangul = thai = latin = 0
    trad_only = set("這個們對會說請問還從點裡買賣價錢東關學與對應當經過區體發現問題認為開關實際點選單項導對話視窗確認選擇設計資訊連結頁面內容標題圖片檔案資料庫")
    simp_only = set("这个们对会说请问还从点里买卖价钱东关学与对应当经过区体发现问题认为开关实际点选单项导对话视窗确认选择设计资讯连结页面内容标题图片档案资料库")
    trad_count = simp_count = 0
    for ch in text_str:
        cp = ord(ch)
        if 0x3040 <= cp <= 0x309F:
            hiragana += 1
        elif 0x30A0 <= cp <= 0x30FF:
            katakana += 1
        elif 0xAC00 <= cp <= 0xD7AF or 0x1100 <= cp <= 0x11FF:
            hangul += 1
        elif 0x0E00 <= cp <= 0x0E7F:
            thai += 1
        elif 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF:
            cjk += 1
            if ch in trad_only:
                trad_count += 1
            if ch in simp_only:
                simp_count += 1
        elif ch.isalpha() and cp < 0x0250:
            latin += 1
    total = hiragana + katakana + cjk + hangul + thai + latin
    if total < 3:
        return "unknown"
    if hiragana + katakana > 2:
        return "ja"
    if hangul > 3:
        return "ko"
    if thai > 3:
        return "th"
    if cjk > 5:
        if trad_count > simp_count:
            return "zh-TW"
        elif simp_count > trad_count:
            return "zh-CN"
        return "zh-TW"
    if latin > total * 0.5:
        return "en"
    return "unknown"


# =========================
# Clip DB auto-enrichment
# =========================
def _enrich_clip_after_generation(clip_id: str, video_id: str, phase_index, captions_data: list, segments: list):
    """
    Auto-enrich clip metadata after generation completes.
    Copies relevant data from video_phases into video_clips columns
    so clips become searchable in the Clip DB.
    """
    loop = get_event_loop()

    async def _do_enrich():
        async with get_session() as session:
            # 1. Build transcript from captions
            transcript = ""
            if captions_data:
                transcript = " ".join(c.get("text", "") for c in captions_data if c.get("text"))
            elif segments:
                transcript = " ".join(s.get("text", "") for s in segments if s.get("text"))

            # 2. Get phase metadata (only for numeric phase_index)
            phase_idx_str = str(phase_index)
            # Auto-detect language from transcript
            detected_lang = _detect_language_from_text(transcript) if transcript else "unknown"

            updates = {
                "transcript_text": transcript[:10000] if transcript else None,
                "detected_language": detected_lang,
                "enriched_at": "NOW()",
            }
            params = {"clip_id": clip_id}

            if phase_idx_str.isdigit():
                phase_sql = text("""
                    SELECT vp.phase_description, vp.gmv, vp.order_count, vp.viewer_count,
                           vp.product_names, vp.importance_score, vp.cta_score,
                           vp.sales_psychology_tags, vp.conversion_rate
                    FROM video_phases vp
                    WHERE vp.video_id = :vid AND vp.phase_index = :pidx
                """)
                p_result = await session.execute(phase_sql, {"vid": video_id, "pidx": int(phase_idx_str)})
                phase = p_result.fetchone()

                if phase:
                    updates["phase_description"] = phase.phase_description
                    updates["gmv"] = phase.gmv or 0
                    updates["viewer_count"] = phase.viewer_count or 0
                    updates["product_name"] = phase.product_names
                    updates["cta_score"] = phase.cta_score
                    updates["importance_score"] = phase.importance_score
                    updates["is_sold"] = (phase.gmv or 0) > 0 or (phase.order_count or 0) > 0

                    # Parse and save tags
                    raw_tags = phase.sales_psychology_tags
                    if raw_tags:
                        import json as _json
                        try:
                            parsed = _json.loads(raw_tags) if isinstance(raw_tags, str) else raw_tags
                            if isinstance(parsed, list):
                                updates["tags"] = _json.dumps(parsed, ensure_ascii=False)
                        except Exception:
                            pass

            # 3. Get video metadata for stream_date and liver_name
            video_sql = text("""
                SELECT v.created_at, v.original_filename
                FROM videos v WHERE v.id = :vid
            """)
            v_result = await session.execute(video_sql, {"vid": video_id})
            video = v_result.fetchone()
            if video and video.created_at:
                updates["stream_date"] = video.created_at.date() if hasattr(video.created_at, 'date') else None

            # 4. Calculate duration
            clip_sql = text("SELECT time_start, time_end FROM video_clips WHERE id = :clip_id")
            c_result = await session.execute(clip_sql, {"clip_id": clip_id})
            clip_row = c_result.fetchone()
            if clip_row and clip_row.time_start is not None and clip_row.time_end is not None:
                updates["duration_sec"] = round(clip_row.time_end - clip_row.time_start, 2)

            # 5. Build and execute UPDATE
            set_parts = []
            final_params = {"clip_id": clip_id}
            for key, val in updates.items():
                if val is not None and key != "enriched_at":
                    set_parts.append(f"{key} = :{key}")
                    final_params[key] = val
            set_parts.append("enriched_at = NOW()")

            if set_parts:
                update_sql = text(f"UPDATE video_clips SET {', '.join(set_parts)} WHERE id = :clip_id")
                await session.execute(update_sql, final_params)

            logger.info(f"[ClipDB] Enriched clip {clip_id} with {len(set_parts)} fields")

    loop.run_until_complete(_do_enrich())


# =========================
# CLI entry point
# =========================

def main():
    # Apply process-level memory limit to prevent OOM crashes on the VM.
    # This limits the entire generate_clip.py process (Python + FFmpeg children)
    # because child processes inherit RLIMIT_AS from the parent.
    _limit_ffmpeg_memory()
    logger.info(f"Memory limit set: {_FFMPEG_MEM_LIMIT_BYTES / (1024**3):.0f}GB per process")

    parser = argparse.ArgumentParser(description="Generate TikTok-style clip")
    parser.add_argument("--clip-id", required=True, help="Clip record UUID")
    parser.add_argument("--video-id", required=True, help="Source video UUID")
    parser.add_argument("--blob-url", required=True, help="Source video blob URL (with SAS)")
    parser.add_argument("--time-start", type=float, required=True, help="Start time in seconds")
    parser.add_argument("--time-end", type=float, required=True, help="End time in seconds")
    parser.add_argument("--phase-index", default="-1", help="Phase index for context-aware subtitles (int or string identifier)")
    parser.add_argument("--speed-factor", type=float, default=1.0, help="Playback speed (1.0=normal, 1.2=20%% faster)")
    parser.add_argument("--subtitle-language", default="ja", help="Subtitle language: ja, zh-TW, or auto")
    parser.add_argument("--skip-person-detection", action="store_true", help="Skip YOLOv8 person detection (batch mode)")
    parser.add_argument("--skip-silence-removal", action="store_true", help="Skip silence removal (batch mode)")
    parser.add_argument("--force-reencode-cut", action="store_true", help="Use re-encode instead of stream copy for DIRECT_CUT")

    args = parser.parse_args()

    generate_clip(
        clip_id=args.clip_id,
        video_id=args.video_id,
        blob_url=args.blob_url,
        time_start=args.time_start,
        time_end=args.time_end,
        phase_index=args.phase_index,
        speed_factor=args.speed_factor,
        subtitle_language=args.subtitle_language,
        skip_person_detection=args.skip_person_detection,
        skip_silence_removal=args.skip_silence_removal,
        force_reencode_cut=args.force_reencode_cut,
    )


if __name__ == "__main__":
    main()
