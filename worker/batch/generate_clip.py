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
import subprocess
import tempfile
import time
import requests
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

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
    _openai_client = OpenAI()
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
            check=True, capture_output=True, text=True, timeout=600
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
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
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
            subprocess.run(cmd_fallback, check=True, capture_output=True, text=True, timeout=600)
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

def refine_subtitles_with_gpt(segments: list, phase_context: str = "") -> list:
    """
    Use GPT-4o to refine Whisper transcription for Japanese subtitles.
    
    Improvements:
    - Fix misrecognized Japanese words using context
    - Split text into natural Japanese phrases (bunsetsu)
    - Remove filler words contextually
    - Add appropriate punctuation
    - Reconstruct word-level timestamps for karaoke effect
    
    Returns refined segments with word-level timestamps preserved.
    """
    if not _openai_client or not segments:
        logger.info("GPT-4o refinement skipped (no client or no segments)")
        return segments

    # Combine all segment texts with timestamps for context
    raw_lines = []
    for i, seg in enumerate(segments):
        raw_lines.append(f"[{seg['start']:.2f}-{seg['end']:.2f}] {seg['text']}")
    raw_text = "\n".join(raw_lines)

    # Build prompt
    context_section = ""
    if phase_context:
        context_section = f"""\n## このフェーズの内容（参考情報 - 商品名や固有名詞の修正に活用）
{phase_context}\n"""

    prompt = f"""あなたは日本語ライブコマース動画のTikTok/Reels向けバイラル字幕を作成する専門家です。
Whisperで自動生成された字幕テキストを、SNS動画で最大限バズる形式に変換してください。
{context_section}
## 修正ルール（優先度順）
1. **誤認識の修正**: 日本語として不自然な単語や文を正しく修正する
   - 商品名・ブランド名の誤認識（コンテキストから推測）
   - 数字・金額の誤り（例: 「センエン」→「1000円」）
   - 敬語・丁寧語の崩れ修正
2. **フィラーワード除去**: 「えー」「あのー」「うーん」「なんか」「まあ」等を除去
3. **バイラル文節分割（最重要）**: TikTok字幕として最適な改行で分割する
   - 1行は5〜10文字が理想（短いほど読みやすい）
   - 意味の区切り・息継ぎで改行
   - 重要ワード（商品名、金額、感嘆表現）は単独行にする
   - 例: 「ブリーチ毛って色がすぐ抜けるじゃないですか」→
     「ブリーチ毛って」「色がすぐ」「抜けるじゃないですか」
4. **重要ワードマーキング**: 以下のワードは emphasis: true を付ける
   - 商品名・ブランド名
   - 金額（例: 1000円、半額）
   - 感嘆表現（すごい、やばい、めっちゃ、マジで）
   - 数量限定表現（限定、残りわずか、ラスト）
   - CTA表現（今すぐ、ポチって、買って）
5. **句読点**: 自然な位置に「、」を追加。字幕なので「。」は最小限

## 入力（Whisper生テキスト + タイムスタンプ）
{raw_text}

## タイムスタンプルール（厳守）
- **元のWhisperタイムスタンプを絶対に大幅に変更しない**
- 各セグメントのstart/endは元のタイムスタンプ範囲内に収める
- 1つの元セグメントを複数に分割する場合、元のstart〜endの範囲内で文字数比率で分配
- セグメント間にギャップがある場合はそのまま維持（無理に埋めない）
- 音声と字幕のズレを防ぐため、タイムスタンプの精度を最優先する

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
            logger.warning("GPT-4o returned invalid format, using original segments")
            return segments

        # Compute the valid time range from original Whisper segments
        orig_min_start = min(s["start"] for s in segments)
        orig_max_end = max(s["end"] for s in segments)
        logger.info(f"Original Whisper time range: {orig_min_start:.2f} - {orig_max_end:.2f}")

        # Validate, clean, clamp timestamps, and reconstruct word-level timestamps
        valid_segments = []
        for seg in refined:
            if isinstance(seg, dict) and "start" in seg and "end" in seg and "text" in seg:
                text_val = seg["text"].strip()
                if text_val:
                    s_start = float(seg["start"])
                    s_end = float(seg["end"])

                    # Clamp timestamps to original Whisper range to prevent GPT-4o drift
                    s_start = max(orig_min_start, min(s_start, orig_max_end))
                    s_end = max(s_start + 0.1, min(s_end, orig_max_end))  # Ensure min 100ms duration

                    # Ensure segments don't overlap with previous segment
                    if valid_segments and s_start < valid_segments[-1]["end"]:
                        s_start = valid_segments[-1]["end"]
                        if s_start >= s_end:
                            continue  # Skip if no room left

                    # Reconstruct word-level timestamps by distributing time evenly per character
                    chars = list(text_val)
                    total_chars = len(chars)
                    duration = s_end - s_start
                    words = []
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
            logger.warning("GPT-4o refinement produced no valid segments, using original")
            return segments

        logger.info(f"GPT-4o refined {len(segments)} segments → {len(valid_segments)} segments")
        return valid_segments

    except json.JSONDecodeError as e:
        logger.warning(f"GPT-4o response JSON parse failed: {e}")
        return segments
    except Exception as e:
        logger.error(f"GPT-4o subtitle refinement failed: {e}")
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
    Uses FFmpeg concat demuxer for seamless joining.
    """
    if not intervals:
        return False

    work_dir = os.path.dirname(output_path)
    segment_files = []

    # Cut each interval into a separate file
    for i, (start, end) in enumerate(intervals):
        seg_path = os.path.join(work_dir, f"person_seg_{i}.mp4")
        duration = end - start
        if duration < 0.5:  # Skip very short segments
            continue

        # Use stream copy for near-instant cutting (2026-03 optimization)
        cmd = [
            FFMPEG_BIN, "-y",
            "-ss", f"{start:.3f}",
            "-i", video_path,
            "-t", f"{duration:.3f}",
            "-c", "copy",
            "-movflags", "+faststart",
            "-avoid_negative_ts", "make_zero",
            seg_path,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
            segment_files.append(seg_path)
        except Exception as e:
            logger.warning(f"Stream-copy cut failed for interval {i}, trying re-encode: {e}")
            # Fallback to re-encode
            cmd_fallback = [
                FFMPEG_BIN, "-y",
                "-ss", f"{start:.3f}",
                "-i", video_path,
                "-t", f"{duration:.3f}",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                seg_path,
            ]
            try:
                subprocess.run(cmd_fallback, check=True, capture_output=True, text=True, timeout=300)
                segment_files.append(seg_path)
            except Exception as e2:
                logger.error(f"Failed to cut person interval {i}: {e2}")

    if not segment_files:
        return False

    if len(segment_files) == 1:
        # Only one segment, just rename
        os.rename(segment_files[0], output_path)
        return True

    # Create concat file list
    concat_list_path = os.path.join(work_dir, "person_concat.txt")
    with open(concat_list_path, "w") as f:
        for seg_path in segment_files:
            f.write(f"file '{seg_path}'\n")

    # Concatenate using FFmpeg concat demuxer
    # Use stream copy first (fast), fallback to re-encode if needed
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
        logger.info(f"Concatenated {len(segment_files)} person segments")
        return True
    except Exception as e:
        logger.error(f"Failed to concatenate person segments: {e}")
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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
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
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
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

def generate_clip(clip_id: str, video_id: str, blob_url: str, time_start: float, time_end: float, phase_index = -1, speed_factor: float = 1.0):
    """Main clip generation pipeline."""
    logger.info(f"=== Starting clip generation ===")
    logger.info(f"clip_id={clip_id}, video_id={video_id}, speed={speed_factor}x")
    logger.info(f"time_range={time_start:.1f}s - {time_end:.1f}s")

    # Initialize DB
    init_db_sync()

    # Update status to processing
    update_clip_status(clip_id, "processing")
    update_clip_progress(clip_id, 5, "downloading")

    work_dir = tempfile.mkdtemp(prefix=f"clip_{clip_id}_")
    logger.info(f"Work directory: {work_dir}")

    try:
        # 1. Download source video
        source_path = os.path.join(work_dir, "source.mp4")
        download_video(blob_url, source_path)

        if not os.path.exists(source_path) or os.path.getsize(source_path) == 0:
            raise RuntimeError("Failed to download source video")

        update_clip_progress(clip_id, 15, "speech_boundary")

        # 1.5. Speech-Aware Cut: adjust boundaries to avoid mid-sentence cuts
        logger.info("[SPEECH_CUT] Adjusting clip boundaries to speech boundaries...")
        adj_start, adj_end = adjust_cut_to_speech_boundary(
            source_path, time_start, time_end, search_window=3.0
        )
        if (adj_start, adj_end) != (time_start, time_end):
            logger.info(
                f"[SPEECH_CUT] Boundaries adjusted: {time_start:.2f}-{time_end:.2f} "
                f"→ {adj_start:.2f}-{adj_end:.2f}"
            )
            time_start, time_end = adj_start, adj_end

        update_clip_progress(clip_id, 20, "cutting")

        # 2. Cut segment
        segment_path = os.path.join(work_dir, "segment.mp4")
        logger.info("Cutting segment...")
        if not cut_segment(source_path, segment_path, time_start, time_end):
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

        # 3.5. GPT-4o subtitle refinement
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

            logger.info("Refining subtitles with GPT-4o...")
            segments = refine_subtitles_with_gpt(segments, phase_context)
            logger.info(f"After GPT-4o refinement: {len(segments)} subtitle segments")

        update_clip_progress(clip_id, 75, "creating_clip")

        # 4. Choose random TikTok style
        style = random.choice(SUBTITLE_STYLES)
        logger.info(f"Selected subtitle style: {style['name']}")

        # 5. Create vertical clip with subtitles + speed adjustment
        clip_path = os.path.join(work_dir, "clip_final.mp4")
        if not create_vertical_clip(segment_path, clip_path, segments, style, speed_factor=speed_factor):
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
            })
        update_clip_status(clip_id, "completed", clip_url=uploaded_url, captions=captions_data if captions_data else None)
        logger.info(f"=== Clip generation completed successfully ({len(captions_data)} captions saved) ===")

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
# CLI entry point
# =========================

def main():
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
