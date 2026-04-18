"""
Generate TikTok-style clip from a video phase.

Steps:
1. Download source video from Azure Blob
2. Cut the specified segment
3. Extract audio and transcribe with Whisper (word-level timestamps)
4. Crop/resize to 9:16 vertical format
5. Burn TikTok-style subtitles (random style)
6. Upload to Azure Blob
7. Update DB with clip URL

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

def update_clip_progress(clip_id: str, progress_pct: int, progress_step: str):
    """Update clip generation progress in database."""
    loop = get_event_loop()
    async def _update():
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


def transcribe_audio(audio_path: str) -> list:
    """Transcribe audio using Azure Whisper API.
    
    Returns list of segments with word-level timestamps for karaoke effect.
    Each segment has: start, end, text, words (list of {word, start, end})
    """
    if not WHISPER_ENDPOINT or not AZURE_KEY:
        logger.warning("Whisper endpoint not configured, skipping transcription")
        return []

    with open(audio_path, "rb") as f:
        audio_data = f.read()

    # Japanese prompt to improve Whisper recognition accuracy
    # Including common terms helps Whisper recognize domain-specific vocabulary
    # Also includes beauty/cosmetics vocabulary for Kyogoku Ryu brand context
    whisper_prompt = (
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

    for attempt in range(3):
        try:
            response = requests.post(
                WHISPER_ENDPOINT,
                headers={"api-key": AZURE_KEY},
                files={
                    "file": ("audio.wav", audio_data, "audio/wav"),
                    "response_format": (None, "verbose_json"),
                    "timestamp_granularities[]": (None, "word"),
                    "temperature": (None, "0"),
                    "task": (None, "transcribe"),
                    "language": (None, "ja"),
                    "prompt": (None, whisper_prompt),
                },
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

def refine_subtitles_with_gpt(segments: list, phase_context: str = "", product_names: list = None) -> list:
    """
    Use GPT-4.1-mini to refine Whisper transcription for Japanese subtitles.
    
    Improvements:
    - Fix misrecognized Japanese words using context + product name dictionary
    - Merge fragmented segments into natural sentence units
    - Remove filler words contextually
    - Add appropriate punctuation
    - Reconstruct word-level timestamps for karaoke effect
    
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

    # Build context sections
    context_section = ""
    if phase_context:
        context_section = f"""\n## このフェーズの内容（参考情報 - 商品名や固有名詞の修正に活用）
{phase_context}\n"""

    # Build product name dictionary section
    product_section = ""
    if product_names:
        product_section = f"""\n## 商品名辞書（この動画に登場する商品名 - 誤認識修正に必ず活用）
{', '.join(product_names)}
※ Whisperが誤認識した場合、上記の商品名に修正してください\n"""

    prompt = f"""あなたは日本語ライブコマース動画のTikTok/Reels向けバイラル字幕を作成する専門家です。
Whisperで自動生成された字幕テキストを、SNS動画で最大限バズる形式に変換してください。
{context_section}{product_section}
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
6. **句読点**: 自然な位置に「、」を追加。字幕なので「。」は最小限

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
  "emphasis": true/false
}}}}
- emphasis: true の行は字幕で大きく強調表示される
- フィラーワードのみのセグメントは除去
- 3文字以下の単独セグメントは作らない（前後と結合すること）

JSON配列のみ出力（説明不要）:"""

    try:
        response = _openai_client.responses.create(
            model="gpt-4.1-mini",
            input=[
                {"role": "system", "content": "あなたは日本語ライブコマース字幕の修正専門家です。JSON配列のみを出力してください。"},
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

    for seg in segments:
        start_time = _seconds_to_ass_time(seg["start"])
        end_time = _seconds_to_ass_time(seg["end"])
        words = seg.get("words", [])
        is_emphasis = seg.get("emphasis", False)
        style_name = "Emphasis" if is_emphasis else "Default"

        if words and len(words) > 1:
            # Build karaoke effect using \kf (smooth fill) tags
            karaoke_text = ""
            for w in words:
                w_duration_cs = max(1, int((w["end"] - w["start"]) * 100))
                char = w["word"].replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
                karaoke_text += f"{{\\kf{w_duration_cs}}}{char}"
            ass_content += f"Dialogue: 0,{start_time},{end_time},{style_name},,0,0,0,,{karaoke_text}\n"
        else:
            text = seg["text"].replace("\n", "\\N")
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


def generate_clip(clip_id: str, video_id: str, blob_url: str, time_start: float, time_end: float, phase_index = -1, speed_factor: float = 1.0):
    """Main clip generation pipeline."""
    logger.info(f"=== Starting clip generation ===")
    logger.info(f"clip_id={clip_id}, video_id={video_id}, speed={speed_factor}x")
    logger.info(f"time_range={time_start:.1f}s - {time_end:.1f}s")

    # Initialize DB
    init_db_sync()

    # Ensure blob_url has a fresh SAS token (expired tokens cause download failures)
    blob_url = _ensure_fresh_sas_url(blob_url)

    # Update status to processing
    update_clip_status(clip_id, "processing")
    update_clip_progress(clip_id, 5, "downloading")

    work_dir = tempfile.mkdtemp(prefix=f"clip_{clip_id}_")
    logger.info(f"Work directory: {work_dir}")

    try:
        # 1. Download ONLY the needed segment (not the entire video)
        # Use ffmpeg -ss with URL to avoid downloading multi-GB files
        # This is critical for 2h+ videos where full download exceeds timeout
        update_clip_progress(clip_id, 10, "downloading")

        segment_path = os.path.join(work_dir, "segment.mp4")
        source_path = None  # May be set if full download fallback is needed

        # Try direct URL cut first (downloads only the needed portion)
        direct_cut_ok = False
        # Add margin for speech boundary adjustment
        margin = 5.0  # seconds extra on each side
        safe_start = max(0.0, time_start - margin)
        safe_end = time_end + margin
        safe_duration = safe_end - safe_start

        logger.info(f"[DIRECT_CUT] Attempting ffmpeg cut from URL (range={safe_start:.1f}-{safe_end:.1f}s)")
        wider_segment_path = os.path.join(work_dir, "wider_segment.mp4")
        cmd_direct = [
            FFMPEG_BIN, "-y",
            "-ss", f"{safe_start:.3f}",
            "-i", blob_url,
            "-t", f"{safe_duration:.3f}",
            "-c", "copy",
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

        update_clip_progress(clip_id, 15, "speech_boundary")

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

        update_clip_progress(clip_id, 20, "cutting")

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

        update_clip_progress(clip_id, 30, "person_detection")

        # 2.5. Person detection: remove scenes without people
        person_intervals = detect_person_intervals(segment_path)
        if person_intervals is not None:  # None means detection unavailable
            if len(person_intervals) == 0:
                logger.warning("No person detected in entire segment, using original")
                # Keep original segment as-is
            else:
                filtered_path = os.path.join(work_dir, "segment_filtered.mp4")
                if concatenate_intervals(segment_path, person_intervals, filtered_path):
                    logger.info(f"Filtered segment: kept {len(person_intervals)} person intervals")
                    segment_path = filtered_path  # Use filtered version
                else:
                    logger.warning("Failed to filter person intervals, using original segment")
        else:
            logger.info("Person detection not available, using original segment")

        update_clip_progress(clip_id, 45, "silence_removal")

        # 2.7. Silence detection: remove silent intervals (coughing, dead air, etc.)
        logger.info("Running silence detection...")
        silence_intervals = detect_silence_intervals(segment_path, noise_threshold="-35dB", min_silence_duration=0.8)
        if silence_intervals:
            desilenced_path = os.path.join(work_dir, "segment_desilenced.mp4")
            if remove_silence_from_video(segment_path, desilenced_path, silence_intervals):
                removed_duration = sum(e - s for s, e in silence_intervals)
                logger.info(f"Removed {removed_duration:.1f}s of silence from segment")
                segment_path = desilenced_path  # Use desilenced version
            else:
                logger.warning("Failed to remove silence, using segment as-is")
        else:
            logger.info("No significant silence detected")

        update_clip_progress(clip_id, 55, "transcribing")

        # 3. Extract audio and transcribe
        audio_path = os.path.join(work_dir, "audio.wav")
        segments = []
        if extract_audio(segment_path, audio_path):
            segments = transcribe_audio(audio_path)
            logger.info(f"Got {len(segments)} raw subtitle segments from Whisper")
        else:
            logger.warning("Audio extraction failed, proceeding without subtitles")

        update_clip_progress(clip_id, 65, "refining_subtitles")

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

            logger.info("Refining subtitles with GPT-4.1-mini...")
            segments = refine_subtitles_with_gpt(segments, phase_context, product_names=product_names)
            logger.info(f"After GPT refinement: {len(segments)} subtitle segments")

        update_clip_progress(clip_id, 75, "creating_clip")

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

        logger.info(f"Clip created: {os.path.getsize(clip_path)} bytes")

        update_clip_progress(clip_id, 90, "uploading")

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

        # 7. Update DB with completed status + save captions
        # Convert segments to captions format for frontend
        captions_data = []
        for seg in segments:
            captions_data.append({
                "start": round(seg.get("start", 0), 3),
                "end": round(seg.get("end", 0), 3),
                "text": seg.get("text", ""),
                "words": [
                    {"word": w.get("word", ""), "start": round(w.get("start", 0), 3), "end": round(w.get("end", 0), 3)}
                    for w in seg.get("words", [])
                ] if seg.get("words") else [],
                "source": "whisper",
                "language": "ja",
            })
        update_clip_status(clip_id, "completed", clip_url=uploaded_url, captions=captions_data if captions_data else None)
        logger.info(f"=== Clip generation completed successfully ({len(captions_data)} captions saved) ===")

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
            updates = {
                "transcript_text": transcript[:10000] if transcript else None,
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

    args = parser.parse_args()

    generate_clip(
        clip_id=args.clip_id,
        video_id=args.video_id,
        blob_url=args.blob_url,
        time_start=args.time_start,
        time_end=args.time_end,
        phase_index=args.phase_index,
        speed_factor=args.speed_factor,
    )


if __name__ == "__main__":
    main()
