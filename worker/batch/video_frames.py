# video_frames.py
import os
import cv2
import numpy as np
import subprocess
import logging
from decouple import config

logger = logging.getLogger("video_frames")


def env(key, default=None):
    return os.getenv(key) or config(key, default=default)

FFMPEG_BIN = env("FFMPEG_PATH", "ffmpeg")

# v13: Limit ffmpeg thread count to prevent CPU saturation when multiple jobs run concurrently.
# With MAX_WORKERS=2 (heavy) + MAX_CLIP_WORKERS=6 (clip), unlimited threads (-threads 0)
# causes load averages >24 on a 4-core VM, leading to stall false-positives.
# 4 threads per ffmpeg instance is optimal for 4-core VM with concurrent workloads.
FFMPEG_THREADS = env("FFMPEG_THREADS", "4")

# v4: Sampling interval for phase detection scoring
# Instead of comparing every frame (3600 for 1h video),
# compare every Nth frame (720 for 5s interval at fps=1)
SCORE_SAMPLE_INTERVAL = int(env("SCORE_SAMPLE_INTERVAL", "3"))

# v6: Frame extraction resolution and quality
# 320px is sufficient for phase detection (histogram/absdiff) and product detection.
# GPT Vision works well at 320px. Reduces disk usage by ~87% vs original (120KB→15KB per frame).
# Also reduces GPU VRAM usage during NVDEC decode and speeds up extraction.
FRAME_SCALE_WIDTH = int(env("FRAME_SCALE_WIDTH", "320"))
FRAME_JPEG_QUALITY = int(env("FRAME_JPEG_QUALITY", "8"))  # 2=best, 31=worst
# Estimated bytes per frame at current settings (for disk space pre-check)
FRAME_ESTIMATED_BYTES = int(env("FRAME_ESTIMATED_BYTES", "15000"))  # ~15KB at 320px q8

# ======================================================
# STEP 0 – EXTRACT FRAMES
# ======================================================


def _get_video_duration(video_path: str) -> float:
    """Get video duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=30,
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def _detect_video_codec(video_path: str) -> str:
    """Detect the video codec using ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=30,
        )
        return result.stdout.strip().lower()
    except Exception:
        return "unknown"


def _check_gpu_available() -> bool:
    """Check if NVIDIA GPU is available for hardware decoding."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0 and len(result.stdout.strip()) > 0
    except Exception:
        return False


def _detect_vfr(video_path: str) -> bool:
    """
    v14: Detect if video has Variable Frame Rate (VFR).
    Common in iPhone ScreenRecording, OBS, and some webcam recordings.
    VFR videos cause ffmpeg to stall or produce fewer frames than expected
    when using -vsync 0.
    """
    try:
        # Method 1: Check avg_frame_rate vs r_frame_rate
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=avg_frame_rate,r_frame_rate",
             "-of", "default=noprint_wrappers=1", video_path],
            capture_output=True, text=True, timeout=30,
        )
        lines = result.stdout.strip().split('\n')
        rates = {}
        for line in lines:
            if '=' in line:
                key, val = line.split('=', 1)
                rates[key.strip()] = val.strip()

        avg_fr = rates.get('avg_frame_rate', '0/1')
        r_fr = rates.get('r_frame_rate', '0/1')

        def _parse_rate(r):
            if '/' in r:
                num, den = r.split('/')
                return float(num) / float(den) if float(den) > 0 else 0
            return float(r) if r else 0

        avg = _parse_rate(avg_fr)
        r = _parse_rate(r_fr)

        # If avg_frame_rate differs significantly from r_frame_rate, it's likely VFR
        if avg > 0 and r > 0:
            ratio = avg / r if r > avg else r / avg
            if ratio < 0.85:  # >15% difference indicates VFR
                logger.info("[VFR] Detected VFR: avg_frame_rate=%s (%.2f), r_frame_rate=%s (%.2f), ratio=%.2f",
                            avg_fr, avg, r_fr, r, ratio)
                return True

        # Method 2: Check filename patterns common for VFR sources
        basename = os.path.basename(video_path).lower()
        vfr_indicators = ['screenrecording', 'screen_recording', 'obs_', 'capture_']
        if any(ind in basename for ind in vfr_indicators):
            logger.info("[VFR] Detected VFR by filename pattern: %s", basename)
            return True

        return False
    except Exception as e:
        logger.debug("[VFR] Detection failed, assuming CFR: %s", e)
        return False


# Map video codecs to NVDEC cuvid decoder names
_CUVID_DECODERS = {
    "h264": "h264_cuvid",
    "hevc": "hevc_cuvid",
    "h265": "hevc_cuvid",
    "vp9": "vp9_cuvid",
    "vp8": "vp8_cuvid",
    "av1": "av1_cuvid",
    "mpeg4": "mpeg4_cuvid",
    "mpeg2video": "mpeg2_cuvid",
    "mpeg1video": "mpeg1_cuvid",
}


def extract_frames(
    video_path: str,
    fps: int = 1,
    frames_root: str = "frames",
    on_progress=None,
) -> str:
    """
    STEP 0 – Extract frames from video

    v14: VFR (Variable Frame Rate) resilience + partial success tolerance
    - GPU-accelerated (NVDEC) with CPU fallback + disk-optimized
    - VFR-safe: uses -vsync cfr to handle iPhone ScreenRecording etc.
    - Partial success: if >=40% frames extracted before stall, accepts result
    - Stall timeout: adaptive based on actual extraction speed (not just duration)
    - on_progress(percent): optional callback for real-time progress (0-100)
    """
    out_dir = os.path.join(frames_root, "frames")
    os.makedirs(out_dir, exist_ok=True)

    # Get expected total frames for progress tracking
    duration = _get_video_duration(video_path)
    expected_frames = int(duration * fps) if duration > 0 else 0

    # --- Resume check: if frames already extracted, skip ---
    existing_frames = len([f for f in os.listdir(out_dir) if f.endswith('.jpg')]) if os.path.exists(out_dir) else 0
    if existing_frames > 0 and expected_frames > 0:
        completeness = existing_frames / expected_frames
        if completeness >= 0.95:  # 95%+ frames already extracted → skip
            logger.info(
                "[FRAMES][RESUME] Found %d/%d frames (%.1f%%) already extracted. Skipping.",
                existing_frames, expected_frames, completeness * 100,
            )
            if on_progress:
                on_progress(100)
            return out_dir
        else:
            # Partial extraction → clean up and re-extract
            logger.info(
                "[FRAMES][RESUME] Found %d/%d frames (%.1f%%) — incomplete. Re-extracting.",
                existing_frames, expected_frames, completeness * 100,
            )
            for f in os.listdir(out_dir):
                if f.endswith('.jpg'):
                    os.remove(os.path.join(out_dir, f))

    # Detect codec and GPU availability
    codec = _detect_video_codec(video_path)
    has_gpu = _check_gpu_available()
    cuvid_decoder = _CUVID_DECODERS.get(codec)

    use_gpu = has_gpu and cuvid_decoder is not None

    _sw = FRAME_SCALE_WIDTH
    _jq = FRAME_JPEG_QUALITY

    # v14: Detect VFR (variable frame rate) videos — common in iPhone ScreenRecording
    _is_vfr = _detect_vfr(video_path)
    # v14: Use -vsync cfr for VFR videos to ensure consistent frame output
    _vsync_mode = "cfr" if _is_vfr else "0"
    if _is_vfr:
        logger.info("[FRAMES] VFR video detected — using -vsync cfr for reliable extraction")

    if use_gpu:
        # GPU path: NVDEC hardware decode + GPU resize + CPU JPG encode
        # v13: Added -threads limit for CPU-side JPG encoding to prevent saturation
        # v15: Added error tolerance flags for corrupt/VFR videos (iPhone ScreenRecording)
        #      -err_detect ignore_err: skip corrupt frames instead of stopping
        #      +discardcorrupt: discard corrupt packets
        #      +genpts: regenerate PTS for consistent timing
        cmd = [
            FFMPEG_BIN, "-y",
            "-err_detect", "ignore_err",
            "-fflags", "+discardcorrupt+genpts",
            "-hwaccel", "cuda",
            "-hwaccel_output_format", "cuda",
            "-c:v", cuvid_decoder,
            "-threads", FFMPEG_THREADS,
            "-i", video_path,
            "-vf", f"fps={fps},scale_cuda={_sw}:-1,hwdownload,format=nv12",
            "-q:v", str(_jq),
            "-vsync", _vsync_mode,
            os.path.join(out_dir, "frame_%08d.jpg"),
        ]
        logger.info("[FRAMES] Using GPU decode: %s (codec=%s, scale=%dpx, q=%d, vsync=%s)",
                    cuvid_decoder, codec, _sw, _jq, _vsync_mode)
    else:
        # CPU path: software decode + scale + JPG
        # v13: Limited threads from "0" (unlimited) to FFMPEG_THREADS to prevent CPU saturation
        # v15: Added error tolerance flags for corrupt/VFR videos (iPhone ScreenRecording)
        cmd = [
            FFMPEG_BIN, "-y",
            "-err_detect", "ignore_err",
            "-fflags", "+discardcorrupt+genpts",
            "-threads", FFMPEG_THREADS,
            "-i", video_path,
            "-vf", f"fps={fps},scale={_sw}:-1",
            "-q:v", str(_jq),
            "-vsync", _vsync_mode,
            os.path.join(out_dir, "frame_%08d.jpg"),
        ]
        logger.info("[FRAMES] Using CPU decode (gpu=%s, codec=%s, cuvid=%s, scale=%dpx, q=%d, vsync=%s)",
                    has_gpu, codec, cuvid_decoder, _sw, _jq, _vsync_mode)

    import threading, time as _time, shutil

    # --- Disk space pre-check (v3: uses FRAME_ESTIMATED_BYTES) ---
    disk = shutil.disk_usage(out_dir)
    free_gb = disk.free / (1024 ** 3)
    estimated_gb = (expected_frames * FRAME_ESTIMATED_BYTES) / (1024 ** 3) if expected_frames > 0 else 2.0
    logger.info("[FRAMES] Disk free: %.1f GB, estimated need: %.1f GB (scale=%dpx, q=%d, ~%dKB/frame)",
                free_gb, estimated_gb, _sw, _jq, FRAME_ESTIMATED_BYTES // 1024)
    if free_gb < max(estimated_gb * 1.2, 1.0):
        # Try cleanup before giving up
        try:
            from disk_guard import ensure_disk_space
            ensure_disk_space(min_free_gb=max(estimated_gb * 1.2, 1.0))
            # Re-check after cleanup
            disk = shutil.disk_usage(out_dir)
            free_gb = disk.free / (1024 ** 3)
            logger.info("[FRAMES] After cleanup: %.1f GB free", free_gb)
        except Exception as cleanup_err:
            logger.warning("[FRAMES] Cleanup failed: %s", cleanup_err)
        if free_gb < max(estimated_gb * 1.2, 1.0):
            raise RuntimeError(
                f"[FRAMES] Insufficient disk space: {free_gb:.1f} GB free, "
                f"need ~{estimated_gb:.1f} GB for {expected_frames} frames "
                f"(scale={_sw}px, q={_jq}). Clean up old files first."
            )

    # Run ffmpeg in background so we can monitor progress
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    # --- Stall detection: v14 adaptive timeout based on actual extraction speed ---
    # v14: Instead of a fixed formula, use an adaptive approach:
    # - Initial grace period: 600s (10 min) to allow ffmpeg to start producing frames
    # - After first frame: timeout = max(300, time_per_frame * 100)
    #   (allows 100x slowdown from average speed before declaring stall)
    # - Absolute max: 1800s (30 min) to prevent infinite waits
    # - PARTIAL_SUCCESS_THRESHOLD: if >=40% frames extracted, accept as success
    INITIAL_GRACE_PERIOD = 600  # 10 min for ffmpeg to start
    STALL_TIMEOUT = max(300, min(int(duration / 4), 1800))  # 300s-1800s
    PARTIAL_SUCCESS_THRESHOLD = 0.40  # Accept if >=40% frames extracted
    logger.info("[FRAMES] STALL_TIMEOUT=%ds, PARTIAL_THRESHOLD=%.0f%% (duration=%.0fs, expected=%d)",
                STALL_TIMEOUT, PARTIAL_SUCCESS_THRESHOLD * 100, duration, expected_frames)
    _stall_detected = {"value": False}
    _partial_success = {"value": False}

    if on_progress and expected_frames > 0:
        def _monitor():
            last_pct = -1
            last_count = 0
            stall_start = _time.time()
            while proc.poll() is None:
                try:
                    count = len([f for f in os.listdir(out_dir) if f.endswith('.jpg')])
                    pct = min(int(count / expected_frames * 100), 99)
                    if pct != last_pct:
                        on_progress(pct)
                        last_pct = pct
                    # Stall detection
                    if count > last_count:
                        last_count = count
                        stall_start = _time.time()
                    elif count == last_count and count > 0:
                        stall_sec = _time.time() - stall_start
                        if stall_sec > STALL_TIMEOUT:
                            # Check disk space
                            disk_now = shutil.disk_usage(out_dir)
                            free_now = disk_now.free / (1024 ** 3)
                            # v14: Check if partial success threshold met
                            completeness = count / expected_frames if expected_frames > 0 else 0
                            if completeness >= PARTIAL_SUCCESS_THRESHOLD:
                                logger.warning(
                                    "[FRAMES] STALL but PARTIAL SUCCESS: %d/%d frames (%.0f%%) extracted. "
                                    "Accepting partial result (threshold=%.0f%%).",
                                    count, expected_frames, completeness * 100,
                                    PARTIAL_SUCCESS_THRESHOLD * 100,
                                )
                                _partial_success["value"] = True
                                _stall_detected["value"] = True
                                proc.kill()
                                return
                            logger.error(
                                "[FRAMES] STALL DETECTED: no new frames for %ds "
                                "(count=%d/%d=%.0f%%, disk_free=%.1f GB). Killing ffmpeg.",
                                int(stall_sec), count, expected_frames, completeness * 100, free_now,
                            )
                            _stall_detected["value"] = True
                            proc.kill()
                            return
                except Exception as _e:
                    logger.debug(f"Suppressed: {_e}")
                _time.sleep(2)
            if not _stall_detected["value"]:
                on_progress(100)

        t = threading.Thread(target=_monitor, daemon=True)
        t.start()

    proc.wait()

    # GPU stall → treat as GPU failure and fall through to CPU fallback
    # v14: Unless partial success was achieved, in which case accept the result
    if _stall_detected["value"] and _partial_success["value"]:
        # Partial success: enough frames extracted, accept result
        _actual_count = len([f for f in os.listdir(out_dir) if f.endswith('.jpg')])
        logger.info(
            "[FRAMES] PARTIAL SUCCESS accepted: %d/%d frames (%.0f%%). Continuing pipeline.",
            _actual_count, expected_frames, _actual_count / expected_frames * 100 if expected_frames > 0 else 0,
        )
        if on_progress:
            on_progress(100)
        return out_dir
    elif _stall_detected["value"] and use_gpu:
        logger.warning(
            "[FRAMES] GPU decode stalled after %d/%d frames. Falling back to CPU.",
            len([f for f in os.listdir(out_dir) if f.endswith('.jpg')]),
            expected_frames,
        )
        # Clean partial GPU output before CPU retry
        for f in os.listdir(out_dir):
            if f.endswith('.jpg'):
                os.remove(os.path.join(out_dir, f))
    elif _stall_detected["value"] and not use_gpu:
        # CPU path stalled — v16: try segment-based extraction before failing
        _actual_count = len([f for f in os.listdir(out_dir) if f.endswith('.jpg')])
        logger.warning(
            "[FRAMES] CPU stalled at %d/%d frames (%.0f%%). Trying segment-based extraction.",
            _actual_count, expected_frames, _actual_count / expected_frames * 100 if expected_frames > 0 else 0,
        )
        # Clean partial output before segment extraction
        for f in os.listdir(out_dir):
            if f.endswith('.jpg'):
                os.remove(os.path.join(out_dir, f))
        # v16: Segment-based extraction as final fallback
        return _extract_with_segments(
            video_path, fps, out_dir, duration, expected_frames,
            _sw, _jq, _is_vfr, on_progress,
        )

    # If GPU failed (error or stall), fallback to CPU
    if (proc.returncode != 0 or _stall_detected["value"]) and use_gpu:
        stderr_out = proc.stderr.read().decode(errors='replace') if proc.stderr else ''
        logger.warning("[FRAMES] GPU decode failed (rc=%d), falling back to CPU. stderr: %s",
                       proc.returncode, stderr_out[:500])
        # Clean partial output
        for f in os.listdir(out_dir):
            if f.endswith('.jpg'):
                os.remove(os.path.join(out_dir, f))

        # v15: CPU fallback also gets error tolerance flags
        cmd_cpu = [
            FFMPEG_BIN, "-y",
            "-err_detect", "ignore_err",
            "-fflags", "+discardcorrupt+genpts",
            "-threads", FFMPEG_THREADS,
            "-i", video_path,
            "-vf", f"fps={fps},scale={_sw}:-1",
            "-q:v", str(_jq),
            "-vsync", _vsync_mode,  # v14: VFR-safe
            os.path.join(out_dir, "frame_%08d.jpg"),
        ]
        proc2 = subprocess.Popen(
            cmd_cpu,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        _stall_detected2 = {"value": False}

        if on_progress and expected_frames > 0:
            def _monitor2():
                last_pct = -1
                last_count = 0
                stall_start = _time.time()
                while proc2.poll() is None:
                    try:
                        count = len([f for f in os.listdir(out_dir) if f.endswith('.jpg')])
                        pct = min(int(count / expected_frames * 100), 99)
                        if pct != last_pct:
                            on_progress(pct)
                            last_pct = pct
                        if count > last_count:
                            last_count = count
                            stall_start = _time.time()
                        elif count == last_count and count > 0:
                            stall_sec = _time.time() - stall_start
                            if stall_sec > STALL_TIMEOUT:
                                disk_now = shutil.disk_usage(out_dir)
                                free_now = disk_now.free / (1024 ** 3)
                                # v14: Log completeness for CPU fallback stall
                                _completeness2 = count / expected_frames if expected_frames > 0 else 0
                                logger.error(
                                    "[FRAMES] CPU STALL DETECTED: no new frames for %ds "
                                    "(count=%d/%d=%.0f%%, disk_free=%.1f GB). Killing ffmpeg.",
                                    int(stall_sec), count, expected_frames, _completeness2 * 100, free_now,
                                )
                                _stall_detected2["value"] = True
                                proc2.kill()
                                return
                    except Exception as _e:
                        logger.debug(f"Suppressed: {_e}")
                    _time.sleep(2)
                if not _stall_detected2["value"]:
                    on_progress(100)

            t2 = threading.Thread(target=_monitor2, daemon=True)
            t2.start()

        proc2.wait()

        if _stall_detected2["value"]:
            # v16: CPU fallback stalled → try segment-based extraction
            _cpu_count = len([f for f in os.listdir(out_dir) if f.endswith('.jpg')])
            _cpu_completeness = _cpu_count / expected_frames if expected_frames > 0 else 0
            if _cpu_completeness >= PARTIAL_SUCCESS_THRESHOLD:
                logger.warning(
                    "[FRAMES] CPU fallback stalled but PARTIAL SUCCESS: %d/%d frames (%.0f%%). Accepting.",
                    _cpu_count, expected_frames, _cpu_completeness * 100,
                )
                if on_progress:
                    on_progress(100)
                return out_dir
            # v16: Instead of raising immediately, try segment-based extraction
            logger.warning(
                "[FRAMES] CPU fallback stalled at %d/%d (%.0f%%). Trying segment-based extraction.",
                _cpu_count, expected_frames, _cpu_completeness * 100,
            )
            for f in os.listdir(out_dir):
                if f.endswith('.jpg'):
                    os.remove(os.path.join(out_dir, f))
            return _extract_with_segments(
                video_path, fps, out_dir, duration, expected_frames,
                _sw, _jq, _is_vfr, on_progress,
            )

    if on_progress:
        on_progress(100)

    frame_count = len([f for f in os.listdir(out_dir) if f.endswith('.jpg')])
    logger.info("[OK][STEP 0][FFMPEG] %d frames extracted → %s (gpu=%s)",
                frame_count, out_dir, use_gpu)
    return out_dir


def _extract_with_segments(
    video_path: str,
    fps: int,
    out_dir: str,
    duration: float,
    expected_frames: int,
    scale_width: int,
    jpeg_quality: int,
    is_vfr: bool,
    on_progress=None,
) -> str:
    """
    v16: Segment-based frame extraction fallback.
    When normal extraction stalls (due to VFR PTS gaps, corrupt sections, etc.),
    this function extracts frames in time-based segments, skipping over problematic
    sections that cause ffmpeg to hang.

    Strategy:
    - Split video into segments (default 120s each)
    - Extract each segment with a short stall timeout (90s)
    - If a segment stalls, skip ahead to the next segment
    - Merge all extracted frames with sequential numbering
    - Accept result if >= 15% of expected frames extracted
    """
    import tempfile
    import time as _time
    import threading
    import shutil
    import glob as _glob

    SEGMENT_DURATION = 120  # seconds per segment
    SEGMENT_STALL_TIMEOUT = 90  # seconds before declaring segment stall
    SEGMENT_MIN_THRESHOLD = 0.15  # Accept if >= 15% frames extracted

    num_segments = max(1, int(duration / SEGMENT_DURATION) + 1)
    logger.info(
        "[FRAMES][SEGMENT] Starting segment-based extraction: %d segments of %ds each "
        "(duration=%.0fs, expected=%d frames)",
        num_segments, SEGMENT_DURATION, duration, expected_frames,
    )

    total_extracted = 0
    segment_results = []  # list of (segment_idx, tmp_dir, frame_count)
    skipped_segments = []

    for seg_idx in range(num_segments):
        seg_start = seg_idx * SEGMENT_DURATION
        seg_end = min((seg_idx + 1) * SEGMENT_DURATION, duration)
        seg_expected = int((seg_end - seg_start) * fps)

        if seg_expected <= 0:
            continue

        # Create temp directory for this segment
        seg_tmp = tempfile.mkdtemp(prefix=f"seg{seg_idx:03d}_")

        # Build ffmpeg command with -ss (seek) and -t (duration)
        _vsync_mode = "cfr" if is_vfr else "0"
        cmd_seg = [
            FFMPEG_BIN, "-y",
            "-err_detect", "ignore_err",
            "-fflags", "+discardcorrupt+genpts",
            "-ss", str(seg_start),
            "-t", str(SEGMENT_DURATION),
            "-threads", FFMPEG_THREADS,
            "-i", video_path,
            "-vf", f"fps={fps},scale={scale_width}:-1",
            "-q:v", str(jpeg_quality),
            "-vsync", _vsync_mode,
            os.path.join(seg_tmp, "frame_%08d.jpg"),
        ]

        proc_seg = subprocess.Popen(
            cmd_seg,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Monitor this segment with short stall timeout
        _seg_stalled = {"value": False}

        def _seg_monitor(p, tmp, stalled_flag, expected_seg):
            last_count = 0
            stall_start = _time.time()
            while p.poll() is None:
                try:
                    count = len([f for f in os.listdir(tmp) if f.endswith('.jpg')])
                    if count > last_count:
                        last_count = count
                        stall_start = _time.time()
                    elif count == last_count:
                        stall_sec = _time.time() - stall_start
                        if stall_sec > SEGMENT_STALL_TIMEOUT:
                            stalled_flag["value"] = True
                            p.kill()
                            return
                except Exception:
                    pass
                _time.sleep(2)

        t_seg = threading.Thread(
            target=_seg_monitor,
            args=(proc_seg, seg_tmp, _seg_stalled, seg_expected),
            daemon=True,
        )
        t_seg.start()
        proc_seg.wait()
        t_seg.join(timeout=5)

        seg_frames = len([f for f in os.listdir(seg_tmp) if f.endswith('.jpg')])

        if _seg_stalled["value"]:
            logger.warning(
                "[FRAMES][SEGMENT] Segment %d/%d STALLED at %ds-%ds (got %d/%d frames). Skipping.",
                seg_idx + 1, num_segments, seg_start, seg_end, seg_frames, seg_expected,
            )
            skipped_segments.append(seg_idx)
            # Keep whatever frames were extracted from this segment
            if seg_frames > 0:
                segment_results.append((seg_idx, seg_tmp, seg_frames))
                total_extracted += seg_frames
            else:
                shutil.rmtree(seg_tmp, ignore_errors=True)
        else:
            if seg_frames > 0:
                segment_results.append((seg_idx, seg_tmp, seg_frames))
                total_extracted += seg_frames
                logger.info(
                    "[FRAMES][SEGMENT] Segment %d/%d OK: %d frames (%ds-%ds)",
                    seg_idx + 1, num_segments, seg_frames, seg_start, seg_end,
                )
            else:
                shutil.rmtree(seg_tmp, ignore_errors=True)

        # Update progress
        if on_progress and expected_frames > 0:
            pct = min(int(total_extracted / expected_frames * 100), 99)
            on_progress(pct)

    # Merge all segment frames into out_dir with sequential numbering
    logger.info(
        "[FRAMES][SEGMENT] Merging %d segments: %d total frames (skipped %d segments)",
        len(segment_results), total_extracted, len(skipped_segments),
    )

    frame_num = 1
    for seg_idx, seg_tmp, seg_count in sorted(segment_results, key=lambda x: x[0]):
        seg_files = sorted([f for f in os.listdir(seg_tmp) if f.endswith('.jpg')])
        for fname in seg_files:
            src = os.path.join(seg_tmp, fname)
            dst = os.path.join(out_dir, f"frame_{frame_num:08d}.jpg")
            os.rename(src, dst)
            frame_num += 1
        shutil.rmtree(seg_tmp, ignore_errors=True)

    final_count = frame_num - 1
    completeness = final_count / expected_frames if expected_frames > 0 else 0

    if completeness >= SEGMENT_MIN_THRESHOLD:
        logger.info(
            "[FRAMES][SEGMENT] SUCCESS: %d/%d frames (%.0f%%) extracted via segments. "
            "Skipped %d problematic segments.",
            final_count, expected_frames, completeness * 100, len(skipped_segments),
        )
        if on_progress:
            on_progress(100)
        return out_dir
    else:
        raise RuntimeError(
            f"[FRAMES][SEGMENT] Segment extraction failed: {final_count}/{expected_frames} "
            f"({completeness*100:.0f}% < {SEGMENT_MIN_THRESHOLD*100:.0f}% threshold). "
            f"Skipped segments: {skipped_segments}"
        )


# ======================================================
# STEP 1 – PHASE DETECTION
# ======================================================

# ---------- 1.1 SCORE FUNCTIONS ----------

def hist_diff_score(img1, img2):
    img1_hsv = cv2.cvtColor(img1, cv2.COLOR_BGR2HSV)
    img2_hsv = cv2.cvtColor(img2, cv2.COLOR_BGR2HSV)

    hist1 = cv2.calcHist([img1_hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])
    hist2 = cv2.calcHist([img2_hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])

    cv2.normalize(hist1, hist1)
    cv2.normalize(hist2, hist2)

    return cv2.compareHist(hist1, hist2, cv2.HISTCMP_CORREL)


def absdiff_score(img1, img2):
    img1_small = cv2.resize(img1, (256, 256))
    img2_small = cv2.resize(img2, (256, 256))

    diff = cv2.absdiff(img1_small, img2_small)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)

    _, thresh = cv2.threshold(gray, 25, 255, cv2.THRESH_BINARY)
    return np.sum(thresh > 0)


# ---------- UTILS ----------

def normalize(arr):
    arr = np.array(arr, dtype=np.float32)
    if np.max(arr) - np.min(arr) == 0:
        return np.zeros_like(arr)
    return (arr - np.min(arr)) / (np.max(arr) - np.min(arr))


def moving_average(arr, k=5):
    arr = np.array(arr, dtype=np.float32)
    if len(arr) < k:
        return arr
    return np.convolve(arr, np.ones(k) / k, mode="same")


def peak_detect(arr, th):
    peaks = []
    for i in range(1, len(arr) - 1):
        if arr[i] > arr[i - 1] and arr[i] > arr[i + 1] and arr[i] > th:
            peaks.append(i)
    return peaks


# ---------- 1.1 RAW SCORE (v4: sampled for speed) ----------

def compute_raw_scores(frame_dir, on_progress=None):
    """
    Compute histogram and absdiff scores between consecutive frames.

    v4: Samples every SCORE_SAMPLE_INTERVAL frames instead of every frame.
    For a 1-hour video (3600 frames) with interval=3:
      - Before: 3600 comparisons → 5-10 min
      - After:  1200 comparisons → 1-3 min
    Scores for skipped frames are interpolated.
    """
    files = sorted(os.listdir(frame_dir))
    total = len(files)
    interval = max(1, SCORE_SAMPLE_INTERVAL)

    # Build sampled indices
    sampled_indices = list(range(0, total, interval))
    if sampled_indices[-1] != total - 1:
        sampled_indices.append(total - 1)

    logger.info("[SCORES] Total frames: %d, Sample interval: %d, Sampled: %d",
                total, interval, len(sampled_indices))

    # Compute scores at sampled points
    sampled_hist = []
    sampled_absdiff = []
    sampled_positions = []  # frame indices where scores are computed

    prev = None
    prev_idx = None
    for progress_i, idx in enumerate(sampled_indices):
        img = cv2.imread(os.path.join(frame_dir, files[idx]))
        if img is None:
            continue

        if prev is not None:
            h = hist_diff_score(prev, img)
            a = absdiff_score(prev, img)
            sampled_hist.append(h)
            sampled_absdiff.append(a)
            sampled_positions.append(idx)

        prev = img
        prev_idx = idx

        # Report progress
        if on_progress and len(sampled_indices) > 0:
            pct = min(int((progress_i + 1) / len(sampled_indices) * 100), 99)
            if progress_i % max(1, len(sampled_indices) // 50) == 0:
                on_progress(pct)

    if on_progress:
        on_progress(100)

    # Interpolate scores to full frame count
    if interval == 1 or len(sampled_positions) < 2:
        return sampled_hist, sampled_absdiff

    # Create full-length score arrays via linear interpolation
    hist_scores = np.interp(
        range(1, total),  # target positions (score[i] = diff between frame i-1 and i)
        sampled_positions,
        sampled_hist,
    ).tolist()

    absdiff_scores = np.interp(
        range(1, total),
        sampled_positions,
        sampled_absdiff,
    ).tolist()

    return hist_scores, absdiff_scores


# ---------- 1.2 CANDIDATE DETECTION ----------

def detect_candidates(hist_scores, absdiff_scores):
    hist_norm = normalize(hist_scores)
    absdiff_norm = normalize(absdiff_scores)

    hist_inv = 1 - hist_norm
    mix = (hist_inv + absdiff_norm) / 2

    smooth = moving_average(mix, k=5)

    mean_val = np.mean(smooth)
    std_val = np.std(smooth)
    th = mean_val + std_val * 1.0

    peaks = peak_detect(smooth, th)
    return peaks


# ---------- 1.3 YOLO CONFIRM ----------

def yolo_compare(model, frame1, frame2):
    r1 = model(frame1)[0]
    r2 = model(frame2)[0]

    c1 = [model.names[int(b.cls)] for b in r1.boxes]
    c2 = [model.names[int(b.cls)] for b in r2.boxes]

    if set(c1) != set(c2):
        return True
    if len(c1) != len(c2):
        return True

    def total_area(r):
        area = 0
        for b in r.boxes.xyxy:
            x1, y1, x2, y2 = b
            area += (x2 - x1) * (y2 - y1)
        return area

    a1 = total_area(r1)
    a2 = total_area(r2)

    ratio = abs(a1 - a2) / (a1 + 1e-5)
    return ratio > 0.20


def confirm_boundaries(peaks, frame_dir, model):
    confirmed = []
    files = sorted(os.listdir(frame_dir))

    for p in peaks:
        if p <= 0 or p >= len(files):
            continue

        f0 = cv2.imread(os.path.join(frame_dir, files[p - 1]))
        f1 = cv2.imread(os.path.join(frame_dir, files[p]))

        if yolo_compare(model, f0, f1):
            confirmed.append(p)

    return confirmed


# ---------- 1.4 PHASE POST-PROCESS ----------

def merge_close_boundaries(indices, min_gap=3):
    if not indices:
        return []

    merged = [indices[0]]
    for x in indices[1:]:
        if x - merged[-1] >= min_gap:
            merged.append(x)
    return merged


def filter_min_phase(indices, total_frames, min_len=25):
    result = []
    extended = [0] + indices + [total_frames - 1]
    phase_len = np.diff(extended)

    for i in range(1, len(extended) - 1):
        if extended[i] < min_len:
            continue

        if phase_len[i] >= min_len:
            result.append(extended[i])

    return result



def apply_max_phase(indices, total_frames, max_len=150):
    result = []
    extended = [0] + indices + [total_frames - 1]

    for i in range(1, len(extended) - 1):
        start = extended[i - 1]
        end = extended[i]
        length = end - start

        if length > max_len:
            mid = (start + end) // 2
            result.append(mid)

        result.append(end)

    return sorted(list(set(result)))

def pick_representative_frames(model, phases, total_frames, frame_dir, max_samples_per_phase=5):
    """
    Pick representative frames for each phase.
    OPTIMIZED: Instead of scanning ALL frames in each phase,
    sample up to max_samples_per_phase evenly spaced frames.
    This reduces YOLO inference calls from thousands to ~5 per phase.
    """
    files = sorted(os.listdir(frame_dir))
    reps = []

    extended = [0] + phases + [total_frames - 1]

    for i in range(1, len(extended)):
        start = extended[i - 1]
        end = extended[i]
        phase_len = end - start

        if phase_len <= 0:
            reps.append(start)
            continue

        # Sample evenly spaced frames instead of scanning all
        if phase_len <= max_samples_per_phase:
            sample_indices = list(range(start, end))
        else:
            step = phase_len / max_samples_per_phase
            sample_indices = [start + int(step * j) for j in range(max_samples_per_phase)]

        best_frame = start
        best_score = 0

        for f in sample_indices:
            if f >= len(files):
                continue
            img_path = os.path.join(frame_dir, files[f])
            img = cv2.imread(img_path)
            if img is None:
                continue

            result = model(img)[0]

            score = 0
            for box in result.boxes:
                conf = float(box.conf)
                x1, y1, x2, y2 = box.xyxy[0]
                area = (x2 - x1) * (y2 - y1)
                score += conf * area

            if score > best_score:
                best_score = score
                best_frame = f

        reps.append(best_frame)

    return reps


# ---------- MAIN STEP 1 ENTRY ----------

def detect_phases(frame_dir: str, model, on_progress=None):
    files = sorted(os.listdir(frame_dir))
    total_frames = len(files)

    if total_frames == 0:
        raise ValueError(
            f"No frames found in {frame_dir}. "
            f"The video file may be empty or corrupted."
        )
    if total_frames < 2:
        # Need at least 2 frames to compute differences
        logger.warning(f"Only {total_frames} frame(s) in {frame_dir}, returning single phase")
        return [0], [0], total_frames

    # compute_raw_scores is the heavy part (~80% of detect_phases time)
    def _score_progress(pct):
        if on_progress:
            on_progress(min(int(pct * 0.8), 80))  # 0-80% for scoring

    hist_scores, absdiff_scores = compute_raw_scores(frame_dir, on_progress=_score_progress)

    if on_progress:
        on_progress(85)  # Candidate detection

    peaks = detect_candidates(hist_scores, absdiff_scores)
    confirmed = confirm_boundaries(peaks, frame_dir, model)

    if on_progress:
        on_progress(90)  # Boundary merging

    merged = merge_close_boundaries(confirmed, min_gap=3)
    filtered = filter_min_phase(merged, total_frames, min_len=25)
    filtered = apply_max_phase(filtered, total_frames, max_len=150)

    # Cap total phases to avoid excessive API calls on very long videos
    MAX_PHASES = 150
    if len(filtered) > MAX_PHASES:
        # Increase max_len to reduce phase count
        _new_max_len = max(150, total_frames // MAX_PHASES)
        logger.info(
            "[PHASES] Too many phases (%d > %d). Increasing max_len %d -> %d",
            len(filtered), MAX_PHASES, 150, _new_max_len,
        )
        filtered = filter_min_phase(merged, total_frames, min_len=25)
        filtered = apply_max_phase(filtered, total_frames, max_len=_new_max_len)
        logger.info("[PHASES] After re-filter: %d phases", len(filtered))

    keyframes = filtered.copy()
    rep_frames = pick_representative_frames(model, keyframes, total_frames, frame_dir)

    if on_progress:
        on_progress(100)

    return keyframes, rep_frames, total_frames
