"""compute_phase_quality.py  –  Phase-level quality feature extraction (Level 2)
================================================================================
Batch script to compute frame quality + audio quality features for ALL phases
of DONE videos. Results are stored directly in video_phases table.

Frame Quality Features (per phase, averaged over 5 sampled frames):
  - blur_score        : Laplacian variance (higher = sharper image)
  - brightness_mean   : Mean pixel brightness (0-255)
  - brightness_std    : Brightness standard deviation (contrast proxy)
  - color_saturation  : Mean saturation in HSV color space
  - scene_change_count: Number of significant scene changes within phase

Audio Quality Features (per phase, computed from phase audio segment):
  - energy_mean       : Average RMS energy (voice loudness)
  - energy_max        : Peak RMS energy
  - pitch_mean        : Average fundamental frequency (F0) in Hz
  - pitch_std         : F0 standard deviation (intonation)
  - speech_rate       : Characters per second (for Japanese)
  - silence_ratio     : Ratio of silence in the phase
  - energy_trend      : "rising" / "falling" / "stable"

Storage:
  - audio_features column (JSON text) in video_phases — same as existing
  - New columns: frame_quality (JSON text) in video_phases

Usage:
  python compute_phase_quality.py --output-dir /tmp/quality_results
  python compute_phase_quality.py --video-id abc-123 --output-dir /tmp/quality_results
  python compute_phase_quality.py --skip-existing  # skip phases that already have quality data
"""
import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse

import cv2
import numpy as np
from dotenv import load_dotenv

load_dotenv()

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
import ssl as _ssl
from urllib.parse import parse_qs, urlencode, urlunparse

# ── DB Setup (same pattern as generate_dataset.py) ──
DATABASE_URL = os.getenv("DATABASE_URL")
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
FFMPEG_BIN = os.getenv("FFMPEG_PATH", "ffmpeg")

engine = None
AsyncSessionLocal = None

FRAMES_PER_PHASE = 5          # Number of frames to sample per phase
AUDIO_WORKERS = 4             # Parallel workers for audio extraction
FRAME_WORKERS = 4             # Parallel workers for frame extraction


def _prepare_database_url(url: str):
    """Strip sslmode from URL for asyncpg compatibility."""
    parsed = urlparse(url)
    qp = parse_qs(parsed.query)
    connect_args = {}
    if "sslmode" in qp:
        mode = qp.pop("sslmode")[0]
        if mode == "require":
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
            connect_args["ssl"] = ctx
    if "ssl" in qp:
        mode = qp.pop("ssl")[0]
        if mode == "require":
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
            connect_args["ssl"] = ctx
    new_query = urlencode(qp, doseq=True)
    cleaned = urlunparse(parsed._replace(query=new_query))
    return cleaned, connect_args


def _init_db():
    global engine, AsyncSessionLocal
    if engine is not None:
        return
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    cleaned_url, connect_args = _prepare_database_url(DATABASE_URL)
    engine = create_async_engine(cleaned_url, pool_pre_ping=True, echo=False,
                                 connect_args=connect_args)
    AsyncSessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


# ── Azure Blob helpers ──

def _regenerate_sas_url(blob_path: str) -> str:
    """Generate a fresh SAS URL for a blob path using connection string.
    Uses the same pattern as process_video.py's _regenerate_sas_url.
    blob_path format: 'user@email.com/video-id/video-id_preview.mp4'
    """
    conn_str = AZURE_STORAGE_CONNECTION_STRING
    if not conn_str:
        raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING not set")

    from azure.storage.blob import generate_blob_sas, BlobSasPermissions
    from datetime import timedelta

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

    container = "videos"
    expiry = datetime.utcnow() + timedelta(hours=24)
    sas_token = generate_blob_sas(
        account_name=account_name,
        container_name=container,
        blob_name=blob_path,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=expiry,
    )
    return f"https://{account_name}.blob.core.windows.net/{container}/{blob_path}?{sas_token}"


def _download_video(blob_url: str, dest_path: str):
    """Download video using azcopy or requests fallback."""
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    # Try azcopy first
    try:
        sas_url = _regenerate_sas_url(blob_url)
        result = subprocess.run(
            ["/usr/local/bin/azcopy", "copy", sas_url, dest_path, "--overwrite=true"],
            check=True, capture_output=True, text=True, timeout=600,
        )
        if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
            return True
    except Exception as e:
        print(f"  [WARN] azcopy failed: {e}")

    # Fallback: requests
    try:
        import requests
        sas_url = _regenerate_sas_url(blob_url)
        resp = requests.get(sas_url, stream=True, timeout=600)
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192 * 16):
                f.write(chunk)
        if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
            return True
    except Exception as e:
        print(f"  [ERROR] Download failed: {e}")

    return False


# ── Frame Quality Computation ──

def _extract_frame_at_time(video_path: str, time_sec: float) -> np.ndarray:
    """Extract a single frame at a given timestamp using ffmpeg."""
    cmd = [
        FFMPEG_BIN, "-y",
        "-ss", str(time_sec),
        "-i", video_path,
        "-vframes", "1",
        "-f", "image2pipe",
        "-pix_fmt", "bgr24",
        "-vcodec", "rawvideo",
        "-"
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode != 0 or len(result.stdout) == 0:
            return None
        # Get video dimensions from stderr
        stderr = result.stderr.decode("utf-8", errors="ignore")
        # Try to parse dimensions
        match = re.search(r'(\d{2,4})x(\d{2,4})', stderr)
        if match:
            w, h = int(match.group(1)), int(match.group(2))
            expected = w * h * 3
            if len(result.stdout) >= expected:
                frame = np.frombuffer(result.stdout[:expected], dtype=np.uint8).reshape(h, w, 3)
                return frame
        return None
    except Exception:
        return None


def _extract_frames_opencv(video_path: str, time_sec: float) -> np.ndarray:
    """Extract a single frame using OpenCV (more reliable)."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        fps = 30
    frame_num = int(time_sec * fps)
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
    ret, frame = cap.read()
    cap.release()
    if ret:
        return frame
    return None


def compute_frame_quality(video_path: str, time_start: float, time_end: float) -> dict:
    """
    Compute frame quality metrics for a phase by sampling frames.

    Returns dict with:
      blur_score, brightness_mean, brightness_std, color_saturation, scene_change_count
    """
    duration = time_end - time_start
    if duration <= 0:
        return _empty_frame_quality()

    # Sample FRAMES_PER_PHASE evenly spaced frames
    n = min(FRAMES_PER_PHASE, max(1, int(duration)))
    timestamps = [time_start + (i + 0.5) * duration / n for i in range(n)]

    frames = []
    for ts in timestamps:
        frame = _extract_frames_opencv(video_path, ts)
        if frame is not None:
            frames.append(frame)

    if not frames:
        return _empty_frame_quality()

    # Compute per-frame metrics
    blur_scores = []
    brightness_means = []
    brightness_stds = []
    saturations = []

    for frame in frames:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # Blur score (Laplacian variance)
        laplacian = cv2.Laplacian(gray, cv2.CV_64F)
        blur_scores.append(float(laplacian.var()))

        # Brightness
        brightness_means.append(float(np.mean(gray)))
        brightness_stds.append(float(np.std(gray)))

        # Color saturation
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        saturations.append(float(np.mean(hsv[:, :, 1])))

    # Scene change detection (compare consecutive frames)
    scene_changes = 0
    if len(frames) >= 2:
        for i in range(1, len(frames)):
            gray1 = cv2.cvtColor(frames[i - 1], cv2.COLOR_BGR2GRAY)
            gray2 = cv2.cvtColor(frames[i], cv2.COLOR_BGR2GRAY)
            # Resize to same dimensions if needed
            if gray1.shape != gray2.shape:
                h = min(gray1.shape[0], gray2.shape[0])
                w = min(gray1.shape[1], gray2.shape[1])
                gray1 = cv2.resize(gray1, (w, h))
                gray2 = cv2.resize(gray2, (w, h))
            diff = cv2.absdiff(gray1, gray2)
            mean_diff = float(np.mean(diff))
            if mean_diff > 30:  # Threshold for scene change
                scene_changes += 1

    return {
        "blur_score": round(float(np.mean(blur_scores)), 2),
        "brightness_mean": round(float(np.mean(brightness_means)), 2),
        "brightness_std": round(float(np.mean(brightness_stds)), 2),
        "color_saturation": round(float(np.mean(saturations)), 2),
        "scene_change_count": scene_changes,
    }


def _empty_frame_quality() -> dict:
    return {
        "blur_score": 0.0,
        "brightness_mean": 0.0,
        "brightness_std": 0.0,
        "color_saturation": 0.0,
        "scene_change_count": 0,
    }


# ── Audio Quality Computation ──

def _extract_phase_audio(video_path: str, start_sec: float, end_sec: float) -> str:
    """Extract phase audio as temporary WAV file."""
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
            "-vn", "-ac", "1", "-ar", "16000",
            tmp.name,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if os.path.exists(tmp.name) and os.path.getsize(tmp.name) > 100:
        return tmp.name
    try:
        os.unlink(tmp.name)
    except OSError:
        pass
    return None


def compute_audio_quality(video_path: str, time_start: float, time_end: float,
                          speech_text: str = "") -> dict:
    """
    Compute audio quality metrics for a phase.
    Same features as audio_features_pipeline.py but for ALL phases.
    """
    import librosa

    wav_path = _extract_phase_audio(video_path, time_start, time_end)
    if wav_path is None:
        return _empty_audio_quality()

    try:
        y, sr = librosa.load(wav_path, sr=16000, mono=True)
        if len(y) == 0:
            return _empty_audio_quality()

        # 1. Energy (RMS)
        rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
        energy_mean = float(np.mean(rms))
        energy_max = float(np.max(rms))

        # Energy trend
        mid = len(rms) // 2
        if mid > 0:
            first_half = float(np.mean(rms[:mid]))
            second_half = float(np.mean(rms[mid:]))
            ratio = second_half / (first_half + 1e-8)
            if ratio > 1.15:
                energy_trend = "rising"
            elif ratio < 0.85:
                energy_trend = "falling"
            else:
                energy_trend = "stable"
        else:
            energy_trend = "stable"

        # 2. Pitch (F0)
        f0, voiced_flag, voiced_probs = librosa.pyin(
            y, fmin=50, fmax=600, sr=sr,
            frame_length=2048, hop_length=512,
        )
        f0_voiced = f0[~np.isnan(f0)] if f0 is not None else np.array([])
        pitch_mean = float(np.mean(f0_voiced)) if len(f0_voiced) > 0 else 0.0
        pitch_std = float(np.std(f0_voiced)) if len(f0_voiced) > 0 else 0.0

        # 3. Speech rate (characters per second for Japanese)
        duration = time_end - time_start
        if speech_text:
            cleaned = re.sub(r'[\s。、！？!?,.\-\n\r]+', '', speech_text)
            word_count = len(cleaned)
        else:
            word_count = 0
        speech_rate = round(word_count / duration, 2) if duration > 0 and word_count > 0 else 0.0

        # 4. Silence ratio
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

    except Exception as e:
        print(f"  [AUDIO-ERROR] {e}")
        return _empty_audio_quality()
    finally:
        try:
            os.unlink(wav_path)
        except OSError:
            pass


def _empty_audio_quality() -> dict:
    return {
        "energy_mean": 0.0,
        "energy_max": 0.0,
        "pitch_mean": 0.0,
        "pitch_std": 0.0,
        "speech_rate": 0.0,
        "silence_ratio": 0.0,
        "energy_trend": "stable",
    }


# ── DB Operations ──

async def fetch_done_videos(session):
    """Fetch all DONE videos with their blob URLs."""
    sql = text("""
        SELECT id, compressed_blob_url, original_filename, duration
        FROM videos
        WHERE status = 'DONE'
        ORDER BY created_at DESC
    """)
    result = await session.execute(sql)
    return result.fetchall()


async def fetch_phases_for_video(session, video_id: str):
    """Fetch all phases for a video."""
    sql = text("""
        SELECT phase_index, time_start, time_end, audio_features, audio_text,
               cta_score, importance_score
        FROM video_phases
        WHERE video_id = :video_id
        ORDER BY phase_index
    """)
    result = await session.execute(sql, {"video_id": video_id})
    return result.fetchall()


async def ensure_frame_quality_column(session):
    """Add frame_quality column to video_phases if it doesn't exist."""
    check_sql = text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'video_phases' AND column_name = 'frame_quality'
    """)
    result = await session.execute(check_sql)
    if result.fetchone() is None:
        print("[DB] Adding frame_quality column to video_phases...")
        await session.execute(text(
            "ALTER TABLE video_phases ADD COLUMN frame_quality TEXT"
        ))
        await session.commit()
        print("[DB] frame_quality column added.")
    else:
        print("[DB] frame_quality column already exists.")


async def update_phase_quality(session, video_id: str, phase_index: int,
                                frame_quality: dict, audio_features: dict):
    """Update both frame_quality and audio_features for a phase."""
    sql = text("""
        UPDATE video_phases
        SET frame_quality = :frame_quality,
            audio_features = :audio_features,
            updated_at = now()
        WHERE video_id = :video_id
          AND phase_index = :phase_index
    """)
    await session.execute(sql, {
        "video_id": video_id,
        "phase_index": phase_index,
        "frame_quality": json.dumps(frame_quality),
        "audio_features": json.dumps(audio_features),
    })


# ── Main Pipeline ──

async def process_video(video_id: str, blob_url: str, output_dir: str,
                         skip_existing: bool = False):
    """Process a single video: compute quality features for all phases."""
    _init_db()

    print(f"\n{'='*60}")
    print(f"Processing video: {video_id}")
    print(f"{'='*60}")

    # 1. Download video
    local_dir = os.path.join(output_dir, "videos")
    local_path = os.path.join(local_dir, f"{video_id}.mp4")

    if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
        print(f"  [DL] Using cached: {local_path}")
    else:
        if not blob_url:
            print(f"  [SKIP] No blob_url for video {video_id}")
            return {"video_id": video_id, "status": "no_blob_url", "phases": 0}

        print(f"  [DL] Downloading video...")
        if not _download_video(blob_url, local_path):
            print(f"  [ERROR] Download failed for {video_id}")
            return {"video_id": video_id, "status": "download_failed", "phases": 0}
        print(f"  [DL] Downloaded: {os.path.getsize(local_path) / (1024**2):.1f} MB")

    # 2. Fetch phases
    async with AsyncSessionLocal() as sess:
        phases = await fetch_phases_for_video(sess, video_id)

    if not phases:
        print(f"  [SKIP] No phases for video {video_id}")
        return {"video_id": video_id, "status": "no_phases", "phases": 0}

    print(f"  [PHASES] {len(phases)} phases to process")

    # 3. Process each phase
    results = []
    updated = 0

    for phase in phases:
        pi = phase.phase_index
        ts = float(phase.time_start or 0)
        te = float(phase.time_end or 0)
        existing_af = phase.audio_features
        existing_at = phase.audio_text or ""

        # Skip if already has both quality scores
        if skip_existing and existing_af and phase.frame_quality:
            continue

        # Compute frame quality
        fq = compute_frame_quality(local_path, ts, te)

        # Compute audio quality (for ALL phases, not just high-CTA)
        aq = compute_audio_quality(local_path, ts, te, existing_at)

        results.append({
            "phase_index": pi,
            "frame_quality": fq,
            "audio_features": aq,
        })

    # 4. Batch update DB
    if results:
        async with AsyncSessionLocal() as sess:
            for r in results:
                await update_phase_quality(
                    sess, video_id, r["phase_index"],
                    r["frame_quality"], r["audio_features"],
                )
                updated += 1
            await sess.commit()

    print(f"  [DONE] Updated {updated}/{len(phases)} phases")

    # 5. Cleanup video file to save disk space
    try:
        os.remove(local_path)
        print(f"  [CLEANUP] Removed {local_path}")
    except OSError:
        pass

    return {"video_id": video_id, "status": "ok", "phases": updated}


async def run(args):
    """Main entry point."""
    _init_db()

    output_dir = args.output_dir
    os.makedirs(output_dir, exist_ok=True)

    # Ensure frame_quality column exists
    async with AsyncSessionLocal() as sess:
        await ensure_frame_quality_column(sess)

    # Fetch videos to process
    if args.video_id:
        async with AsyncSessionLocal() as sess:
            sql = text("SELECT id, compressed_blob_url, original_filename, duration "
                       "FROM videos WHERE id = :vid")
            result = await sess.execute(sql, {"vid": args.video_id})
            videos = result.fetchall()
    else:
        async with AsyncSessionLocal() as sess:
            videos = await fetch_done_videos(sess)

    print(f"\n{'#'*60}")
    print(f"# Phase Quality Computation - Level 2")
    print(f"# Videos to process: {len(videos)}")
    print(f"# Output dir: {output_dir}")
    print(f"# Skip existing: {args.skip_existing}")
    print(f"{'#'*60}\n")

    # Process videos sequentially (each video is large)
    all_results = []
    start_time = time.time()

    for i, video in enumerate(videos):
        vid = str(video.id)
        blob = video.compressed_blob_url
        fname = video.original_filename or "unknown"

        print(f"\n[{i+1}/{len(videos)}] {fname} ({vid})")

        try:
            result = await process_video(vid, blob, output_dir, args.skip_existing)
            all_results.append(result)
        except Exception as e:
            print(f"  [ERROR] {e}")
            all_results.append({"video_id": vid, "status": f"error: {e}", "phases": 0})

    # Summary
    elapsed = time.time() - start_time
    ok_count = sum(1 for r in all_results if r["status"] == "ok")
    total_phases = sum(r["phases"] for r in all_results)

    print(f"\n{'#'*60}")
    print(f"# COMPLETE")
    print(f"# Videos processed: {ok_count}/{len(videos)}")
    print(f"# Total phases updated: {total_phases}")
    print(f"# Elapsed: {elapsed/60:.1f} min")
    print(f"{'#'*60}")

    # Save summary
    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "videos_total": len(videos),
        "videos_ok": ok_count,
        "total_phases_updated": total_phases,
        "elapsed_seconds": round(elapsed, 1),
        "results": all_results,
    }
    summary_path = os.path.join(output_dir, "quality_summary.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"\nSummary saved: {summary_path}")


def main():
    parser = argparse.ArgumentParser(description="Compute phase quality features")
    parser.add_argument("--video-id", type=str, default=None,
                        help="Process a single video (default: all DONE videos)")
    parser.add_argument("--output-dir", type=str, default="/tmp/quality_results",
                        help="Output directory for results")
    parser.add_argument("--skip-existing", action="store_true",
                        help="Skip phases that already have quality data")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
