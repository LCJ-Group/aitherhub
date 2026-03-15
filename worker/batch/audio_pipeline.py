# audio_pipeline.py
"""
v6: BatchedInferencePipeline for 2-4x faster transcription.

Changes from v5:
  - Uses BatchedInferencePipeline instead of chunk-by-chunk sequential transcription
  - Eliminates the need for _whisper_lock (batched pipeline handles GPU internally)
  - Falls back to sequential chunk processing if batched mode fails
  - Keeps Azure engine path unchanged
"""
import os
import time
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from decouple import config


# =========================
# ENV
# =========================

def env(key, default=None):
    return os.getenv(key) or config(key, default=default)


AUDIO_OUT_ROOT = "audio"
AUDIO_TEXT_ROOT = "audio_text"

FFMPEG_BIN = env("FFMPEG_PATH", "ffmpeg")
CHUNK_SECONDS = 1800  # v5: 30-min chunks (was 600=10min) — fewer chunks = less overhead

# Whisper engine selection: "local" (faster-whisper) or "azure" (Azure API)
WHISPER_ENGINE = env("WHISPER_ENGINE", "local")

# Azure Whisper settings (used only when WHISPER_ENGINE="azure")
WHISPER_ENDPOINT = env("WHISPER_ENDPOINT", "")
AZURE_KEY = env("AZURE_OPENAI_KEY", "")

# Local Whisper settings (used only when WHISPER_ENGINE="local")
WHISPER_MODEL_SIZE = env("WHISPER_MODEL_SIZE", "large-v3")
WHISPER_DEVICE = env("WHISPER_DEVICE", "auto")          # "auto", "cuda", "cpu"
WHISPER_COMPUTE_TYPE = env("WHISPER_COMPUTE_TYPE", "auto")  # "auto", "float16", "int8_float16", "int8"
WHISPER_BEAM_SIZE = int(env("WHISPER_BEAM_SIZE", "5"))
WHISPER_LANGUAGE = env("WHISPER_LANGUAGE", "ja")

# v6: Batch size for BatchedInferencePipeline (T4 16GB: 16 is safe)
WHISPER_BATCH_SIZE = int(env("WHISPER_BATCH_SIZE", "16"))

# v4: Parallel transcription workers (kept for fallback)
WHISPER_PARALLEL_WORKERS = int(env("WHISPER_PARALLEL_WORKERS", "2"))

MAX_RETRY = 10
SLEEP_BETWEEN_REQUESTS = 2


# =========================
# LOCAL WHISPER MODEL (lazy load)
# =========================

_whisper_model = None
_batched_pipeline = None


def _get_whisper_model():
    """
    Lazy-load the faster-whisper model.
    Called once on first transcription, then cached.
    """
    global _whisper_model
    if _whisper_model is not None:
        return _whisper_model

    from faster_whisper import WhisperModel
    import torch

    # Auto-detect device
    if WHISPER_DEVICE == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    else:
        device = WHISPER_DEVICE

    # Auto-detect compute type
    if WHISPER_COMPUTE_TYPE == "auto":
        if device == "cuda":
            compute_type = "int8_float16"   # T4: best speed/VRAM balance
        else:
            compute_type = "int8"           # CPU: int8 is fastest
    else:
        compute_type = WHISPER_COMPUTE_TYPE

    print(f"[WHISPER-LOCAL] Loading model: {WHISPER_MODEL_SIZE}")
    print(f"[WHISPER-LOCAL] Device: {device}, Compute: {compute_type}")

    _whisper_model = WhisperModel(
        WHISPER_MODEL_SIZE,
        device=device,
        compute_type=compute_type,
    )

    print(f"[WHISPER-LOCAL] Model loaded successfully")
    return _whisper_model


def _get_batched_pipeline():
    """
    Lazy-load the BatchedInferencePipeline for faster transcription.
    v6: Uses batched inference for 2-4x speedup over sequential.
    """
    global _batched_pipeline
    if _batched_pipeline is not None:
        return _batched_pipeline

    from faster_whisper import BatchedInferencePipeline

    model = _get_whisper_model()
    _batched_pipeline = BatchedInferencePipeline(model=model)
    print(f"[WHISPER-LOCAL] BatchedInferencePipeline initialized (batch_size={WHISPER_BATCH_SIZE})")
    return _batched_pipeline


# =========================
# STEP 3.1 – EXTRACT AUDIO (full file, no chunking for batched mode)
# =========================

def _get_video_duration_seconds(video_path: str) -> float:
    """Get video duration in seconds using ffprobe. Returns 0 on failure."""
    try:
        result = subprocess.run(
            [FFMPEG_BIN.replace("ffmpeg", "ffprobe"),
             "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=30
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def extract_audio_full(video_path: str, out_dir: str) -> str:
    """
    Extract full audio as a single WAV file (for BatchedInferencePipeline).
    WAV, mono, 16kHz – safe for Whisper.
    Returns the path to the full audio file.
    Timeout scales with video duration (duration * 0.5 + 600s, max 3h).
    """
    os.makedirs(out_dir, exist_ok=True)
    full_audio_path = os.path.join(out_dir, "full_audio.wav")

    # Dynamic timeout based on video duration
    duration = _get_video_duration_seconds(video_path)
    timeout = min(int(duration * 0.5) + 600, 21600)  # max 6 hours (for 9h+ videos)
    timeout = max(timeout, 600)  # min 10 minutes
    print(f"[AUDIO] extract_audio_full: duration={duration:.0f}s, timeout={timeout}s")

    try:
        subprocess.run(
            [
                FFMPEG_BIN, "-y",
                "-i", video_path,
                "-map", "0:a:0",
                "-vn",
                "-ac", "1",
                "-ar", "16000",
                full_audio_path
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout
        )
    except subprocess.TimeoutExpired:
        print(f"[AUDIO][WARN] extract_audio_full timed out after {timeout}s")
        # Clean up partial file
        if os.path.exists(full_audio_path):
            try:
                os.remove(full_audio_path)
            except Exception as _e:
                print(f"Suppressed: {_e}")
        return None

    if os.path.exists(full_audio_path) and os.path.getsize(full_audio_path) > 100:
        return full_audio_path
    return None


def extract_audio_chunks(video_path: str, out_dir: str) -> str:
    """
    Extract audio chunks into out_dir.
    WAV, mono, 16kHz – safe for Whisper.
    v4: Uses GPU decode if available for faster extraction.
    Timeout scales with video duration (duration * 0.5 + 600s, max 3h).
    """
    os.makedirs(out_dir, exist_ok=True)

    chunk_pattern = os.path.join(out_dir, "chunk_%03d.wav")

    # Dynamic timeout based on video duration
    duration = _get_video_duration_seconds(video_path)
    timeout = min(int(duration * 0.5) + 600, 21600)  # max 6 hours (for 9h+ videos)
    timeout = max(timeout, 600)  # min 10 minutes
    print(f"[AUDIO] extract_audio_chunks: duration={duration:.0f}s, timeout={timeout}s")

    try:
        subprocess.run(
            [
                FFMPEG_BIN, "-y",
                "-i", video_path,
                "-map", "0:a:0",
                "-vn",
                "-f", "segment",
                "-segment_time", str(CHUNK_SECONDS),
                "-reset_timestamps", "1",
                "-ac", "1",
                "-ar", "16000",
                chunk_pattern
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout
        )
    except subprocess.TimeoutExpired:
        print(f"[AUDIO][WARN] extract_audio_chunks timed out after {timeout}s")
        # Don't delete partial chunks - they may still be usable

    return out_dir


# =========================
# STEP 3.2 – TRANSCRIBE (LOCAL) – v6 BATCHED
# =========================

def _transcribe_batched_local(audio_path: str, text_dir: str, on_progress=None):
    """
    v6: Transcribe a full audio file using BatchedInferencePipeline.
    This is 2-4x faster than sequential chunk processing because:
    - VAD splits audio into segments internally
    - Multiple segments are batched together for GPU inference
    - No chunk boundary artifacts

    Output: writes chunk-compatible .txt files to text_dir for backward compatibility.
    """
    pipeline = _get_batched_pipeline()

    t0 = time.time()
    print(f"[WHISPER-BATCHED] Starting batched transcription (batch_size={WHISPER_BATCH_SIZE})")

    segments_iter, info = pipeline.transcribe(
        audio_path,
        batch_size=WHISPER_BATCH_SIZE,
        language=WHISPER_LANGUAGE,
        word_timestamps=True,
    )

    # Collect all segments
    all_segments = []
    full_text_parts = []
    for seg in segments_iter:
        all_segments.append({
            "start": seg.start,
            "end": seg.end,
            "text": seg.text.strip(),
        })
        full_text_parts.append(seg.text.strip())

        # Progress callback based on segment timestamps
        if on_progress and info.duration and info.duration > 0:
            pct = min(int(seg.end / info.duration * 100), 100)
            on_progress(pct)

    elapsed = time.time() - t0
    print(
        f"[WHISPER-BATCHED] Done in {elapsed:.1f}s "
        f"(lang={info.language}, prob={info.language_probability:.2f}, "
        f"segments={len(all_segments)}, duration={info.duration:.0f}s)"
    )

    # Write output in chunk-compatible format for backward compatibility
    # Split segments into virtual chunks based on CHUNK_SECONDS
    os.makedirs(text_dir, exist_ok=True)

    if not all_segments:
        # Write a single empty chunk file
        txt_path = os.path.join(text_dir, "chunk_000.txt")
        _write_transcription(txt_path, {"text": "", "segments": []})
        return

    # Determine total duration and number of virtual chunks
    max_end = max(s["end"] for s in all_segments)
    num_chunks = max(1, int(max_end // CHUNK_SECONDS) + 1)

    for chunk_idx in range(num_chunks):
        chunk_start = chunk_idx * CHUNK_SECONDS
        chunk_end = (chunk_idx + 1) * CHUNK_SECONDS

        # Filter segments that belong to this chunk
        chunk_segments = [
            s for s in all_segments
            if s["start"] >= chunk_start and s["start"] < chunk_end
        ]

        chunk_text = " ".join(s["text"] for s in chunk_segments)

        txt_path = os.path.join(text_dir, f"chunk_{chunk_idx:03d}.txt")
        _write_transcription(txt_path, {
            "text": chunk_text,
            "segments": chunk_segments,
        })
        print(f"[OK] Saved → {txt_path} ({len(chunk_segments)} segments)")

    if on_progress:
        on_progress(100)


# =========================
# STEP 3.2 – TRANSCRIBE (LOCAL) – FALLBACK SEQUENTIAL
# =========================

import threading

# Lock for thread-safe access to faster-whisper model
# faster-whisper with CTranslate2 is NOT thread-safe for concurrent inference
_whisper_lock = threading.Lock()


def _transcribe_chunk_local(audio_path: str, chunk_index: int) -> dict:
    """
    Transcribe a single audio chunk using local faster-whisper.
    Thread-safe: uses lock to serialize GPU access.

    Returns dict with:
      - "text": full transcription text
      - "segments": list of {start, end, text} dicts (absolute time with offset)
    """
    model = _get_whisper_model()
    offset = chunk_index * CHUNK_SECONDS

    t0 = time.time()
    print(f"[WHISPER-LOCAL] Transcribing chunk_{chunk_index:03d}.wav ...")

    # Lock for thread-safe model access
    with _whisper_lock:
        segments_iter, info = model.transcribe(
            audio_path,
            beam_size=WHISPER_BEAM_SIZE,
            language=WHISPER_LANGUAGE,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
            word_timestamps=True,
        )

        # Consume the generator inside the lock (faster-whisper uses lazy evaluation)
        segments = []
        full_text_parts = []
        for seg in segments_iter:
            segments.append({
                "start": seg.start + offset,
                "end": seg.end + offset,
                "text": seg.text.strip(),
            })
            full_text_parts.append(seg.text.strip())

    elapsed = time.time() - t0
    print(
        f"[WHISPER-LOCAL] Done chunk_{chunk_index:03d}.wav "
        f"in {elapsed:.1f}s "
        f"(lang={info.language}, prob={info.language_probability:.2f}, "
        f"segments={len(segments)})"
    )

    return {
        "text": " ".join(full_text_parts),
        "segments": segments,
    }


# =========================
# STEP 3.2 – TRANSCRIBE (AZURE API) – fallback
# =========================

def _transcribe_chunk_azure(audio_path: str, chunk_index: int, filename: str) -> dict:
    """
    Transcribe a single audio chunk using Azure Whisper API.
    Kept as fallback when WHISPER_ENGINE="azure".

    Returns dict with:
      - "text": full transcription text
      - "segments": list of {start, end, text} dicts (absolute time with offset)
    """
    import requests as req
    import random

    offset = chunk_index * CHUNK_SECONDS

    for attempt in range(1, MAX_RETRY + 1):
        with open(audio_path, "rb") as audio_file:
            audio_data = audio_file.read()

        print(f"[WHISPER-AZURE] Sending {filename}, attempt {attempt}/{MAX_RETRY}")
        t0 = time.time()

        try:
            response = req.post(
                WHISPER_ENDPOINT,
                headers={"api-key": AZURE_KEY},
                files={
                    "file": (filename, audio_data, "audio/wav"),
                    "response_format": (None, "verbose_json"),
                    "timestamp_granularities[]": (None, "word"),
                    "timestamp_granularities[]": (None, "segment"),
                    "temperature": (None, "0"),
                    "task": (None, "transcribe"),
                    "language": (None, "ja"),
                },
                timeout=120,
            )
            print(f"[WHISPER-AZURE] Done {filename} in {time.time() - t0:.1f}s status={response.status_code}")

        except Exception as e:
            print(f"[WHISPER-AZURE][ERROR] {filename} failed after {time.time() - t0:.1f}s: {e}")
            time.sleep(5)
            continue

        # SUCCESS
        if response.status_code == 200:
            data = response.json()
            segments = []
            for seg in data.get("segments", []):
                segments.append({
                    "start": seg["start"] + offset,
                    "end": seg["end"] + offset,
                    "text": seg["text"].strip(),
                })
            return {
                "text": data.get("text", ""),
                "segments": segments,
            }

        # RATE LIMIT
        if response.status_code == 429:
            wait_time = 5 * attempt + random.uniform(1, 3)
            print(f"[WAIT] 429 rate limit → retry #{attempt} after {wait_time:.1f}s")
            time.sleep(wait_time)
            continue

        # OTHER ERROR
        print("ERROR:", response.text)
        break

    # All retries failed
    return {"text": "", "segments": []}


# =========================
# STEP 3.2 – TRANSCRIBE (UNIFIED ENTRY POINT)
# =========================

def transcribe_audio_chunks(audio_dir: str, text_dir: str, on_progress=None):
    """
    Transcribe all audio in audio_dir, write results to text_dir.

    v6: Uses BatchedInferencePipeline for local engine (2-4x faster).
    Falls back to sequential chunk processing if batched mode fails.

    Output format is identical for all engines:
      [TEXT]
      <full transcription>

      [TIMELINE]
      0.00s → 3.50s : こんにちは
      3.50s → 7.20s : 今日は...

    on_progress(percent): optional callback for real-time progress (0-100)
    """
    os.makedirs(text_dir, exist_ok=True)

    engine = WHISPER_ENGINE.lower()

    if engine == "local":
        # v6: Try batched transcription first (uses full_audio.wav if available)
        full_audio_path = os.path.join(audio_dir, "full_audio.wav")

        if os.path.exists(full_audio_path) and os.path.getsize(full_audio_path) > 100:
            print(f"[TRANSCRIBE] v6: Using BatchedInferencePipeline on full audio")
            try:
                _transcribe_batched_local(full_audio_path, text_dir, on_progress)
                return
            except Exception as e:
                print(f"[TRANSCRIBE][WARN] Batched transcription failed, falling back to chunks: {e}")

        # Fallback: chunk-based transcription
        # v7: If chunks don't exist yet (skipped in process_video), extract them now
        files = sorted([
            f for f in os.listdir(audio_dir)
            if f.startswith("chunk_") and f.endswith(".wav")
        ])
        if not files:
            print(f"[TRANSCRIBE] No chunks found, extracting from full audio or video...")
            # Try to find the original video path from the audio dir structure
            # audio_dir = output/<video_id>/audio
            # We need to extract chunks from full_audio.wav if it exists
            if os.path.exists(full_audio_path) and os.path.getsize(full_audio_path) > 100:
                # Split full_audio.wav into chunks using ffmpeg
                chunk_pattern = os.path.join(audio_dir, "chunk_%03d.wav")
                try:
                    import subprocess as _sp
                    _sp.run(
                        ["ffmpeg", "-y", "-i", full_audio_path,
                         "-f", "segment", "-segment_time", str(CHUNK_SECONDS),
                         "-reset_timestamps", "1", "-ac", "1", "-ar", "16000",
                         chunk_pattern],
                        stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, timeout=3600,
                    )
                except Exception as chunk_err:
                    print(f"[TRANSCRIBE][WARN] Failed to split full audio into chunks: {chunk_err}")
                files = sorted([
                    f for f in os.listdir(audio_dir)
                    if f.startswith("chunk_") and f.endswith(".wav")
                ])
        total_chunks = len(files)
        print(f"[TRANSCRIBE] Engine: {engine}, Chunks: {total_chunks}, Workers: {WHISPER_PARALLEL_WORKERS}")

        if total_chunks > 1:
            # ---- PARALLEL LOCAL TRANSCRIPTION ----
            _get_whisper_model()
            completed = [0]
            results_map = {}

            def _process_chunk(f):
                audio_path = os.path.join(audio_dir, f)
                chunk_index = int(f.split("_")[1].split(".")[0])
                result = _transcribe_chunk_local(audio_path, chunk_index)
                results_map[chunk_index] = (f, result)
                completed[0] += 1
                if on_progress and total_chunks > 0:
                    pct = min(int(completed[0] / total_chunks * 100), 100)
                    on_progress(pct)
                return chunk_index

            with ThreadPoolExecutor(max_workers=WHISPER_PARALLEL_WORKERS) as pool:
                futures = [pool.submit(_process_chunk, f) for f in files]
                for fut in as_completed(futures):
                    try:
                        fut.result()
                    except Exception as e:
                        print(f"[TRANSCRIBE][ERROR] Chunk failed: {e}")

            for f in files:
                chunk_index = int(f.split("_")[1].split(".")[0])
                if chunk_index not in results_map:
                    continue
                _, result = results_map[chunk_index]
                txt_path = os.path.join(text_dir, f.replace(".wav", ".txt"))
                _write_transcription(txt_path, result)
                print(f"[OK] Saved → {txt_path}")
        else:
            for idx, f in enumerate(files):
                audio_path = os.path.join(audio_dir, f)
                txt_path = os.path.join(text_dir, f.replace(".wav", ".txt"))
                chunk_index = int(f.split("_")[1].split(".")[0])
                result = _transcribe_chunk_local(audio_path, chunk_index)
                _write_transcription(txt_path, result)
                print(f"[OK] Saved → {txt_path}")
                if on_progress and total_chunks > 0:
                    pct = min(int((idx + 1) / total_chunks * 100), 100)
                    on_progress(pct)

    else:
        # ---- SEQUENTIAL AZURE ----
        files = sorted([
            f for f in os.listdir(audio_dir)
            if f.startswith("chunk_") and f.endswith(".wav")
        ])
        total_chunks = len(files)
        print(f"[TRANSCRIBE] Engine: {engine}, Chunks: {total_chunks}")

        for idx, f in enumerate(files):
            audio_path = os.path.join(audio_dir, f)
            txt_path = os.path.join(text_dir, f.replace(".wav", ".txt"))
            chunk_index = int(f.split("_")[1].split(".")[0])
            result = _transcribe_chunk_azure(audio_path, chunk_index, f)
            _write_transcription(txt_path, result)
            print(f"[OK] Saved → {txt_path}")
            if on_progress and total_chunks > 0:
                pct = min(int((idx + 1) / total_chunks * 100), 100)
                on_progress(pct)
            print(f"[SLEEP] {SLEEP_BETWEEN_REQUESTS}s to avoid quota")
            time.sleep(SLEEP_BETWEEN_REQUESTS)


def _write_transcription(txt_path: str, result: dict):
    """Write transcription result to text file in standard format."""
    with open(txt_path, "w", encoding="utf-8") as out:
        out.write("[TEXT]\n")
        out.write(result.get("text", ""))

        out.write("\n\n[TIMELINE]\n")
        for seg in result.get("segments", []):
            out.write(
                f"{seg['start']:.2f}s → {seg['end']:.2f}s : {seg['text']}\n"
            )
