# audio_features_pipeline.py
"""
STEP 6.5 – Audio Paralinguistic Feature Extraction (Filtered + Parallel)

v4: Parallel processing with ThreadPoolExecutor for 3-5x speedup.

Extracts audio features ONLY for high-CTA or high-importance phases
to avoid overloading the worker queue.

Features extracted per phase:
  - energy_mean     : Average RMS energy (voice loudness)
  - energy_max      : Peak RMS energy
  - pitch_mean      : Average fundamental frequency (F0) in Hz
  - pitch_std       : F0 standard deviation (intonation/抑揚)
  - speech_rate     : Words per second (話速)
  - silence_ratio   : Ratio of silence in the phase (間の取り方)
  - energy_trend    : "rising" / "falling" / "stable" (energy trajectory)

All features are computed from the WAV audio chunks already
extracted by audio_pipeline.py (16kHz mono).
"""

import os
import subprocess
import tempfile
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from decouple import config


def env(key, default=None):
    return os.getenv(key) or config(key, default=default)


FFMPEG_BIN = env("FFMPEG_PATH", "ffmpeg")

# Filter thresholds: only analyze phases meeting these criteria
CTA_SCORE_THRESHOLD = int(env("AUDIO_FEATURES_CTA_THRESHOLD", "3"))
IMPORTANCE_SCORE_THRESHOLD = float(env("AUDIO_FEATURES_IMPORTANCE_THRESHOLD", "0.5"))

# v4: Parallel workers for audio feature extraction
AUDIO_FEATURES_WORKERS = int(env("AUDIO_FEATURES_WORKERS", "8"))


def should_analyze_phase(phase: dict) -> bool:
    """
    Determine whether a phase warrants audio feature extraction.

    A phase is analyzed if:
      - cta_score >= CTA_SCORE_THRESHOLD (default: 3), OR
      - csv_metrics.importance_score >= IMPORTANCE_SCORE_THRESHOLD (default: 0.5)

    This filter prevents the worker from processing every phase,
    which would cause memory/time issues on long livestreams.
    """
    cta_score = phase.get("cta_score", 1)
    if cta_score >= CTA_SCORE_THRESHOLD:
        return True

    csv_metrics = phase.get("csv_metrics", {})
    if isinstance(csv_metrics, dict):
        importance = csv_metrics.get("importance_score", 0)
        try:
            if float(importance) >= IMPORTANCE_SCORE_THRESHOLD:
                return True
        except (ValueError, TypeError) as _e:
            print(f"Suppressed: {_e}")

    return False


def _extract_phase_audio(video_path: str, start_sec: float, end_sec: float) -> str:
    """
    Extract a specific time range from the video as a temporary WAV file.
    Returns the path to the temporary file.
    """
    duration = end_sec - start_sec
    if duration <= 0:
        return None

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()

    subprocess.run(
        [
            FFMPEG_BIN, "-y",
            "-i", video_path,
            "-ss", str(start_sec),
            "-t", str(duration),
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            tmp.name,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Verify the file was created and has content
    if os.path.exists(tmp.name) and os.path.getsize(tmp.name) > 100:
        return tmp.name
    else:
        try:
            os.unlink(tmp.name)
        except OSError as _e:
            print(f"Suppressed: {_e}")
        return None


def _analyze_audio_features(wav_path: str, word_count: int, duration_sec: float) -> dict:
    """
    Extract paralinguistic features from a WAV file using librosa.

    Args:
        wav_path: Path to the WAV file (16kHz mono)
        word_count: Number of words in the phase's speech_text
        duration_sec: Duration of the phase in seconds

    Returns:
        dict with audio feature values
    """
    import librosa

    # Load audio (librosa resamples to sr by default)
    y, sr = librosa.load(wav_path, sr=16000, mono=True)

    if len(y) == 0:
        return _empty_features()

    # ---- 1. Energy (RMS) ----
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
    energy_mean = float(np.mean(rms))
    energy_max = float(np.max(rms))

    # Energy trend: compare first half vs second half
    mid = len(rms) // 2
    if mid > 0:
        first_half_energy = float(np.mean(rms[:mid]))
        second_half_energy = float(np.mean(rms[mid:]))
        ratio = second_half_energy / (first_half_energy + 1e-8)
        if ratio > 1.15:
            energy_trend = "rising"
        elif ratio < 0.85:
            energy_trend = "falling"
        else:
            energy_trend = "stable"
    else:
        energy_trend = "stable"

    # ---- 2. Pitch (F0) ----
    f0, voiced_flag, voiced_probs = librosa.pyin(
        y, fmin=50, fmax=600, sr=sr,
        frame_length=2048, hop_length=512,
    )
    # Filter out unvoiced frames (NaN)
    f0_voiced = f0[~np.isnan(f0)] if f0 is not None else np.array([])

    if len(f0_voiced) > 0:
        pitch_mean = float(np.mean(f0_voiced))
        pitch_std = float(np.std(f0_voiced))
    else:
        pitch_mean = 0.0
        pitch_std = 0.0

    # ---- 3. Speech rate ----
    if duration_sec > 0 and word_count > 0:
        speech_rate = round(word_count / duration_sec, 2)
    else:
        speech_rate = 0.0

    # ---- 4. Silence ratio ----
    # Frames where RMS < 10% of mean RMS are considered silence
    silence_threshold = energy_mean * 0.1
    if len(rms) > 0 and silence_threshold > 0:
        silence_frames = np.sum(rms < silence_threshold)
        silence_ratio = round(float(silence_frames / len(rms)), 3)
    else:
        silence_ratio = 0.0

    return {
        "energy_mean": round(energy_mean, 6),
        "energy_max": round(energy_max, 6),
        "pitch_mean": round(pitch_mean, 2),
        "pitch_std": round(pitch_std, 2),
        "speech_rate": speech_rate,
        "silence_ratio": silence_ratio,
        "energy_trend": energy_trend,
    }


def _empty_features() -> dict:
    """Return a feature dict with all zeros (used for fallback)."""
    return {
        "energy_mean": 0.0,
        "energy_max": 0.0,
        "pitch_mean": 0.0,
        "pitch_std": 0.0,
        "speech_rate": 0.0,
        "silence_ratio": 0.0,
        "energy_trend": "stable",
    }


def _count_words(text: str) -> int:
    """
    Count words in Japanese/mixed text.
    For Japanese, count characters (excluding spaces/punctuation) as a proxy.
    """
    if not text:
        return 0
    # Remove common punctuation
    import re
    cleaned = re.sub(r'[\s。、！？!?,.\-\n\r]+', '', text)
    return len(cleaned)


def _process_single_phase(phase: dict, video_path: str) -> dict:
    """
    Process a single phase's audio features.
    Thread-safe: each call creates its own temp file.

    Returns:
        dict with phase_index and features (or None)
    """
    phase_index = phase.get("phase_index")
    time_range = phase.get("time_range", {})
    start_sec = time_range.get("start_sec", 0)
    end_sec = time_range.get("end_sec", 0)
    duration = end_sec - start_sec

    if duration <= 0:
        return {"phase_index": phase_index, "features": None}

    # Extract the phase's audio segment
    wav_path = _extract_phase_audio(video_path, start_sec, end_sec)

    if wav_path is None:
        return {"phase_index": phase_index, "features": None}

    try:
        word_count = _count_words(phase.get("speech_text", ""))
        features = _analyze_audio_features(wav_path, word_count, duration)

        print(
            f"[AUDIO-FEATURES] Phase {phase_index}: "
            f"cta={phase.get('cta_score', '?')}, "
            f"energy={features['energy_mean']:.4f}, "
            f"pitch={features['pitch_mean']:.1f}Hz, "
            f"rate={features['speech_rate']}w/s, "
            f"trend={features['energy_trend']}"
        )

        return {"phase_index": phase_index, "features": features}

    except Exception as e:
        print(f"[AUDIO-FEATURES][ERROR] Phase {phase_index}: {e}")
        return {"phase_index": phase_index, "features": None}

    finally:
        # Clean up temp file
        try:
            os.unlink(wav_path)
        except OSError as _e:
            print(f"Suppressed: {_e}")


def analyze_phase_audio_features(
    phase_units: list,
    video_path: str,
) -> list:
    """
    Main entry point: extract audio features for filtered phases.

    v4: Parallel processing with ThreadPoolExecutor.

    Modifies phase_units in-place by adding 'audio_features' dict
    to each qualifying phase.

    Args:
        phase_units: List of phase dicts (must have cta_score, time_range, speech_text)
        video_path: Path to the original video file

    Returns:
        phase_units (modified in-place)
    """
    total = len(phase_units)

    # Separate phases into analyze vs skip
    phases_to_analyze = []
    for phase in phase_units:
        if should_analyze_phase(phase):
            phases_to_analyze.append(phase)
        else:
            phase["audio_features"] = None

    skipped = total - len(phases_to_analyze)
    print(f"[AUDIO-FEATURES] Will analyze {len(phases_to_analyze)}/{total} phases "
          f"({skipped} skipped, workers={AUDIO_FEATURES_WORKERS})")

    if not phases_to_analyze:
        return phase_units

    # Process in parallel
    results_map = {}  # {phase_index: features}

    with ThreadPoolExecutor(max_workers=AUDIO_FEATURES_WORKERS) as pool:
        futures = {
            pool.submit(_process_single_phase, phase, video_path): phase
            for phase in phases_to_analyze
        }

        for fut in as_completed(futures):
            try:
                result = fut.result()
                results_map[result["phase_index"]] = result["features"]
            except Exception as e:
                phase = futures[fut]
                print(f"[AUDIO-FEATURES][ERROR] Phase {phase.get('phase_index')}: {e}")
                results_map[phase.get("phase_index")] = None

    # Apply results back to phase_units
    analyzed = 0
    for phase in phase_units:
        pi = phase.get("phase_index")
        if pi in results_map:
            phase["audio_features"] = results_map[pi]
            if results_map[pi] is not None:
                analyzed += 1

    print(
        f"[AUDIO-FEATURES] Complete: {analyzed}/{total} phases analyzed, "
        f"{total - analyzed} skipped or failed"
    )

    return phase_units
