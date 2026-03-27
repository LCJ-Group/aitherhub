"""
AitherHub GPU Worker — RunPod Serverless Handler
=================================================

Unified handler for all GPU-accelerated AI tasks:
  - MuseTalk lip-sync (digital human)
  - FaceFusion face swap (video & single frame)
  - IMTalker premium digital human
  - LivePortrait 3-layer pipeline

Architecture:
  RunPod Serverless → handler.py → AI Engine → Result → S3 Upload → URL

Input format:
  {
    "input": {
      "action": "musetalk" | "facefusion_video" | "facefusion_frame" | "imtalker" | "liveportrait",
      ... action-specific parameters ...
    }
  }

Output format:
  {
    "output_url": "https://...",  // S3 URL of the result
    "status": "completed",
    "duration_sec": 30.5,
    ... action-specific metadata ...
  }

Environment variables:
  RUNPOD_VOLUME_PATH  — Network volume mount path (default: /runpod-volume)
  AWS_ACCESS_KEY_ID   — S3 credentials for result upload
  AWS_SECRET_ACCESS_KEY
  S3_BUCKET           — S3 bucket name
  S3_ENDPOINT_URL     — S3 endpoint (for non-AWS S3-compatible storage)
  AZURE_STORAGE_CONNECTION_STRING — Azure Blob Storage (alternative to S3)
  AZURE_STORAGE_CONTAINER — Azure container name
"""

import asyncio
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import runpod

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("aitherhub-serverless")

# ── Configuration ────────────────────────────────────────────────────────────

VOLUME_PATH = os.getenv("RUNPOD_VOLUME_PATH", "/runpod-volume")
WORKSPACE = os.getenv("WORKSPACE", "/workspace")
TEMP_DIR = os.getenv("TEMP_DIR", "/tmp/aitherhub")
os.makedirs(TEMP_DIR, exist_ok=True)

# Paths for AI tools (installed in Docker image or Network Volume)
FACEFUSION_DIR = os.getenv("FACEFUSION_DIR", f"{WORKSPACE}/facefusion")
MUSETALK_DIR = os.getenv("MUSETALK_DIR", f"{WORKSPACE}/MuseTalk")
IMTALKER_DIR = os.getenv("IMTALKER_DIR", f"{WORKSPACE}/IMTalker")

# Azure Blob Storage config (for result upload)
AZURE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
AZURE_CONTAINER = os.getenv("AZURE_STORAGE_CONTAINER", "aitherhub-media")

# S3 config (alternative)
S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "")

# ── Global Model State ───────────────────────────────────────────────────────
# Models are loaded once at cold start and reused across requests

_musetalk_engine = None
_liveportrait_engine = None
_models_loaded = False


def _ensure_workspace():
    """Ensure workspace directories exist, linking from network volume if available."""
    volume_workspace = os.path.join(VOLUME_PATH, "workspace")
    if os.path.isdir(volume_workspace) and not os.path.isdir(WORKSPACE):
        os.symlink(volume_workspace, WORKSPACE)
        logger.info(f"Linked {WORKSPACE} -> {volume_workspace}")

    for d in [TEMP_DIR, f"{WORKSPACE}/source_faces", f"{WORKSPACE}/tmp"]:
        os.makedirs(d, exist_ok=True)


def _load_models():
    """Pre-load models at cold start for faster inference."""
    global _models_loaded
    if _models_loaded:
        return

    _ensure_workspace()

    # Verify critical paths
    paths_to_check = {
        "FaceFusion": FACEFUSION_DIR,
        "MuseTalk": MUSETALK_DIR,
    }
    for name, path in paths_to_check.items():
        if os.path.isdir(path):
            logger.info(f"{name} found at {path}")
        else:
            logger.warning(f"{name} NOT found at {path}")

    _models_loaded = True
    logger.info("Model paths verified, ready for inference")


# Load models at import time (cold start)
_load_models()


# ── File Utilities ───────────────────────────────────────────────────────────

def download_file(url: str, dest_path: str) -> str:
    """Download a file from URL to local path."""
    import httpx

    if url.startswith("file://"):
        src = url[7:]
        shutil.copy2(src, dest_path)
        return dest_path

    with httpx.Client(timeout=300, follow_redirects=True) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=256 * 1024):
                    f.write(chunk)

    logger.info(f"Downloaded {url[:80]}... -> {dest_path} ({os.path.getsize(dest_path)} bytes)")
    return dest_path


def upload_result(local_path: str, filename: str) -> str:
    """Upload result file to cloud storage and return public URL."""
    # Try Azure Blob Storage first
    if AZURE_CONNECTION_STRING:
        return _upload_to_azure(local_path, filename)

    # Try S3
    if S3_BUCKET:
        return _upload_to_s3(local_path, filename)

    # Fallback: return local path (for testing)
    logger.warning("No cloud storage configured, returning local path")
    return f"file://{local_path}"


def _upload_to_azure(local_path: str, filename: str) -> str:
    """Upload to Azure Blob Storage."""
    from azure.storage.blob import BlobServiceClient, ContentSettings

    blob_service = BlobServiceClient.from_connection_string(AZURE_CONNECTION_STRING)
    container_client = blob_service.get_container_client(AZURE_CONTAINER)

    blob_path = f"serverless-output/{filename}"
    content_type = "video/mp4" if filename.endswith(".mp4") else "image/jpeg"

    with open(local_path, "rb") as f:
        container_client.upload_blob(
            name=blob_path,
            data=f,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )

    blob_url = f"{container_client.url}/{blob_path}"
    logger.info(f"Uploaded to Azure: {blob_url}")
    return blob_url


def _upload_to_s3(local_path: str, filename: str) -> str:
    """Upload to S3-compatible storage."""
    import boto3

    s3_kwargs = {"endpoint_url": S3_ENDPOINT_URL} if S3_ENDPOINT_URL else {}
    s3 = boto3.client("s3", **s3_kwargs)

    s3_key = f"serverless-output/{filename}"
    content_type = "video/mp4" if filename.endswith(".mp4") else "image/jpeg"

    s3.upload_file(
        local_path, S3_BUCKET, s3_key,
        ExtraArgs={"ContentType": content_type},
    )

    if S3_ENDPOINT_URL:
        url = f"{S3_ENDPOINT_URL}/{S3_BUCKET}/{s3_key}"
    else:
        url = f"https://{S3_BUCKET}.s3.amazonaws.com/{s3_key}"

    logger.info(f"Uploaded to S3: {url}")
    return url


def convert_audio_to_wav(audio_path: str, job_dir: str) -> str:
    """Convert audio to WAV 16kHz mono if needed."""
    if audio_path.endswith(".wav"):
        return audio_path

    wav_path = os.path.join(job_dir, "audio_16k.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-i", audio_path, "-ar", "16000", "-ac", "1", wav_path],
        capture_output=True, timeout=60,
    )
    if os.path.exists(wav_path):
        return wav_path
    return audio_path


# ── Action Handlers ──────────────────────────────────────────────────────────

def handle_musetalk(job_input: Dict[str, Any]) -> Dict[str, Any]:
    """
    MuseTalk lip-sync: portrait + audio → lip-synced video.

    Input:
      portrait_url, audio_url, portrait_type, bbox_shift, extra_margin,
      batch_size, output_fps
    """
    start_time = time.time()
    job_id = job_input.get("job_id", f"mt-{uuid.uuid4().hex[:12]}")
    job_dir = os.path.join(TEMP_DIR, f"dh_{job_id}")
    os.makedirs(job_dir, exist_ok=True)

    try:
        # Step 1: Download files
        portrait_url = job_input["portrait_url"]
        audio_url = job_input["audio_url"]

        # Determine portrait extension
        url_lower = portrait_url.split("?")[0].lower()
        if url_lower.endswith((".mov", ".mp4")):
            p_ext = ".mp4"
        elif url_lower.endswith(".png"):
            p_ext = ".png"
        else:
            p_ext = ".jpg"

        portrait_path = download_file(portrait_url, os.path.join(job_dir, f"portrait{p_ext}"))

        # Determine audio extension
        a_url_lower = audio_url.split("?")[0].lower()
        a_ext = ".wav" if a_url_lower.endswith(".wav") else ".mp3"
        audio_path = download_file(audio_url, os.path.join(job_dir, f"audio{a_ext}"))

        # Convert audio to WAV if needed
        audio_path = convert_audio_to_wav(audio_path, job_dir)

        # Step 2: Load MuseTalk engine
        global _musetalk_engine
        if _musetalk_engine is None:
            engine_path = os.path.join(WORKSPACE, "live_engine.py")
            if not os.path.exists(engine_path):
                engine_path = os.path.join(os.path.dirname(__file__), "..", "live_engine.py")

            import importlib.util
            spec = importlib.util.spec_from_file_location("live_engine", engine_path)
            live_engine_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(live_engine_mod)

            config = live_engine_mod.EngineConfig(
                version="v15",
                bbox_shift=job_input.get("bbox_shift", 0),
                extra_margin=job_input.get("extra_margin", 10),
                batch_size=job_input.get("batch_size", 20),
                parsing_mode="jaw",
                left_cheek_width=90,
                right_cheek_width=90,
                audio_padding_length_left=2,
                audio_padding_length_right=2,
            )
            _musetalk_engine = live_engine_mod.MuseTalkEngine(config)
            _musetalk_engine.load_models()
            logger.info("MuseTalkEngine loaded")

        engine = _musetalk_engine

        # Step 3: Prepare avatar
        portrait_hash = hashlib.md5(open(portrait_path, "rb").read()).hexdigest()
        engine.prepare_avatar(portrait_path)
        logger.info(f"Avatar prepared: {portrait_hash}")

        # Step 4: Generate lip-sync frames
        frames = engine.generate_lipsync_frames(audio_path)
        if not frames:
            raise RuntimeError("No lip-sync frames generated")

        logger.info(f"Generated {len(frames)} lip-sync frames")

        # Step 5: Encode video
        output_path = os.path.join(job_dir, f"output_{job_id}.mp4")
        temp_vid = os.path.join(job_dir, "temp_video.mp4")
        fps = job_input.get("output_fps", 25)

        h, w = frames[0].shape[:2]
        ffmpeg_proc = subprocess.Popen(
            [
                "ffmpeg", "-y", "-v", "warning",
                "-f", "rawvideo", "-pix_fmt", "bgr24",
                "-s", f"{w}x{h}", "-r", str(fps),
                "-i", "pipe:0",
                "-vcodec", "libx264", "-pix_fmt", "yuv420p",
                "-crf", "18", "-preset", "fast",
                temp_vid,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        for frame in frames:
            try:
                ffmpeg_proc.stdin.write(frame.tobytes())
            except BrokenPipeError:
                break

        ffmpeg_proc.stdin.close()
        ffmpeg_proc.wait()

        # Merge audio
        merge_cmd = f"ffmpeg -y -v warning -i {temp_vid} -i {audio_path} -c:v copy -c:a aac -shortest {output_path}"
        os.system(merge_cmd)

        if os.path.exists(temp_vid):
            os.remove(temp_vid)

        if not os.path.exists(output_path):
            raise RuntimeError("Failed to produce output video")

        # Step 6: Upload result
        output_filename = f"musetalk_{job_id}_{int(time.time())}.mp4"
        output_url = upload_result(output_path, output_filename)

        duration = time.time() - start_time
        file_size_mb = os.path.getsize(output_path) / (1024 * 1024)

        return {
            "status": "completed",
            "output_url": output_url,
            "job_id": job_id,
            "duration_sec": round(duration, 1),
            "file_size_mb": round(file_size_mb, 1),
            "frame_count": len(frames),
        }

    except Exception as e:
        logger.error(f"MuseTalk error: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "error": str(e), "job_id": job_id}

    finally:
        # Cleanup
        if os.path.isdir(job_dir):
            shutil.rmtree(job_dir, ignore_errors=True)


def handle_facefusion_video(job_input: Dict[str, Any]) -> Dict[str, Any]:
    """
    FaceFusion video face swap: source face + target video → face-swapped video.

    Input:
      source_face_url, video_url, face_enhancer, quality, output_video_quality
    """
    start_time = time.time()
    job_id = job_input.get("job_id", f"ff-{uuid.uuid4().hex[:12]}")
    job_dir = os.path.join(TEMP_DIR, f"ff_{job_id}")
    os.makedirs(job_dir, exist_ok=True)

    # FaceFusion quality presets
    QUALITY_PRESETS = {
        "fast": {
            "face_swapper_model": "inswapper_128_fp16",
            "face_swapper_pixel_boost": "128x128",
            "face_enhancer_enabled": False,
            "face_detector_model": "yolo_face",
        },
        "balanced": {
            "face_swapper_model": "inswapper_128",
            "face_swapper_pixel_boost": "256x256",
            "face_enhancer_enabled": False,
            "face_detector_model": "retinaface",
        },
        "high": {
            "face_swapper_model": "inswapper_128",
            "face_swapper_pixel_boost": "512x512",
            "face_enhancer_enabled": True,
            "face_enhancer_model": "gfpgan_1.4",
            "face_detector_model": "retinaface",
        },
    }

    try:
        # Step 1: Download files
        source_face_path = download_file(
            job_input["source_face_url"],
            os.path.join(job_dir, "source_face.jpg"),
        )
        video_path = download_file(
            job_input["video_url"],
            os.path.join(job_dir, "input_video.mp4"),
        )

        # Step 2: Apply quality preset
        quality = job_input.get("quality", "high")
        preset = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["high"])
        face_enhancer = job_input.get("face_enhancer", preset.get("face_enhancer_enabled", False))

        # Step 3: Build FaceFusion command
        output_path = os.path.join(job_dir, f"output_{job_id}.mp4")
        processors = ["face_swapper"]
        if face_enhancer and quality != "fast":
            processors.append("face_enhancer")

        cmd = [
            sys.executable, f"{FACEFUSION_DIR}/facefusion.py", "headless-run",
            "--source-paths", source_face_path,
            "--target-path", video_path,
            "--output-path", output_path,
            "--processors", *processors,
            "--face-swapper-model", preset["face_swapper_model"],
            "--face-swapper-pixel-boost", preset["face_swapper_pixel_boost"],
            "--face-detector-model", preset.get("face_detector_model", "retinaface"),
            "--face-detector-score", "0.5",
            "--face-mask-types", "box",
            "--face-mask-blur", "0.3",
            "--face-mask-padding", "0", "0", "0", "0",
            "--output-video-quality", str(job_input.get("output_video_quality", 90)),
            "--execution-providers", "cuda",
            "--execution-thread-count", "4",
        ]

        if face_enhancer and quality != "fast":
            cmd.extend(["--face-enhancer-model", preset.get("face_enhancer_model", "gfpgan_1.4")])

        logger.info(f"Running FaceFusion: {' '.join(cmd[:8])}...")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=1800,  # 30 min max
            cwd=FACEFUSION_DIR,
        )

        if result.returncode != 0:
            raise RuntimeError(f"FaceFusion failed: {result.stderr[:500]}")

        if not os.path.exists(output_path):
            raise RuntimeError("FaceFusion produced no output")

        # Step 4: Upload result
        output_filename = f"faceswap_{job_id}_{int(time.time())}.mp4"
        output_url = upload_result(output_path, output_filename)

        duration = time.time() - start_time
        file_size_mb = os.path.getsize(output_path) / (1024 * 1024)

        return {
            "status": "completed",
            "output_url": output_url,
            "job_id": job_id,
            "duration_sec": round(duration, 1),
            "file_size_mb": round(file_size_mb, 1),
            "quality": quality,
        }

    except Exception as e:
        logger.error(f"FaceFusion video error: {e}")
        return {"status": "error", "error": str(e), "job_id": job_id}

    finally:
        if os.path.isdir(job_dir):
            shutil.rmtree(job_dir, ignore_errors=True)


def handle_facefusion_frame(job_input: Dict[str, Any]) -> Dict[str, Any]:
    """
    FaceFusion single frame face swap: source face + target image → face-swapped image.

    Input:
      source_face_url, image_base64 or image_url, quality, face_enhancer
    """
    import base64
    start_time = time.time()
    job_id = job_input.get("job_id", f"ffs-{uuid.uuid4().hex[:12]}")
    job_dir = os.path.join(TEMP_DIR, f"ffs_{job_id}")
    os.makedirs(job_dir, exist_ok=True)

    try:
        # Download source face
        source_face_path = download_file(
            job_input["source_face_url"],
            os.path.join(job_dir, "source_face.jpg"),
        )

        # Get target image
        input_path = os.path.join(job_dir, "input.jpg")
        if job_input.get("image_base64"):
            content = base64.b64decode(job_input["image_base64"])
            with open(input_path, "wb") as f:
                f.write(content)
        elif job_input.get("image_url"):
            download_file(job_input["image_url"], input_path)
        else:
            raise ValueError("Provide image_base64 or image_url")

        # Build command
        output_path = os.path.join(job_dir, "output.jpg")
        quality = job_input.get("quality", "high")
        face_enhancer = job_input.get("face_enhancer", False)

        processors = ["face_swapper"]
        if face_enhancer:
            processors.append("face_enhancer")

        cmd = [
            sys.executable, f"{FACEFUSION_DIR}/facefusion.py", "headless-run",
            "--source-paths", source_face_path,
            "--target-path", input_path,
            "--output-path", output_path,
            "--processors", *processors,
            "--face-swapper-model", "inswapper_128",
            "--face-swapper-pixel-boost", "512x512",
            "--face-detector-model", "retinaface",
            "--face-detector-score", "0.5",
            "--face-mask-types", "box",
            "--face-mask-blur", "0.3",
            "--face-mask-padding", "0", "0", "0", "0",
            "--output-image-quality", "95",
            "--execution-providers", "cuda",
            "--execution-thread-count", "4",
        ]

        if face_enhancer:
            cmd.extend(["--face-enhancer-model", "gfpgan_1.4"])

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, cwd=FACEFUSION_DIR,
        )

        if result.returncode != 0 or not os.path.exists(output_path):
            raise RuntimeError(f"FaceFusion frame failed: {result.stderr[:500]}")

        # Read and encode output
        with open(output_path, "rb") as f:
            output_base64 = base64.b64encode(f.read()).decode()

        duration = time.time() - start_time

        return {
            "status": "completed",
            "image_base64": output_base64,
            "job_id": job_id,
            "duration_sec": round(duration, 1),
        }

    except Exception as e:
        logger.error(f"FaceFusion frame error: {e}")
        return {"status": "error", "error": str(e), "job_id": job_id}

    finally:
        if os.path.isdir(job_dir):
            shutil.rmtree(job_dir, ignore_errors=True)


def handle_imtalker(job_input: Dict[str, Any]) -> Dict[str, Any]:
    """
    IMTalker premium digital human: portrait + audio → full facial animation video.

    Input:
      portrait_url, audio_url, portrait_type, a_cfg_scale, nfe, crop, output_fps
    """
    start_time = time.time()
    job_id = job_input.get("job_id", f"imt-{uuid.uuid4().hex[:12]}")
    job_dir = os.path.join(TEMP_DIR, f"imt_{job_id}")
    os.makedirs(job_dir, exist_ok=True)

    try:
        # Step 1: Download files
        portrait_url = job_input["portrait_url"]
        audio_url = job_input["audio_url"]

        url_lower = portrait_url.split("?")[0].lower()
        if url_lower.endswith((".mov", ".mp4")):
            p_ext = ".mp4"
        elif url_lower.endswith(".png"):
            p_ext = ".png"
        else:
            p_ext = ".jpg"

        portrait_path = download_file(portrait_url, os.path.join(job_dir, f"portrait{p_ext}"))

        a_url_lower = audio_url.split("?")[0].lower()
        a_ext = ".wav" if a_url_lower.endswith(".wav") else ".mp3"
        audio_path = download_file(audio_url, os.path.join(job_dir, f"audio{a_ext}"))

        # Convert audio
        audio_path = convert_audio_to_wav(audio_path, job_dir)

        # Step 2: Run IMTalker
        output_dir = os.path.join(job_dir, "results")
        os.makedirs(output_dir, exist_ok=True)

        cmd = [
            sys.executable,
            os.path.join(IMTALKER_DIR, "generator", "generate.py"),
            "--ref_path", portrait_path,
            "--aud_path", audio_path,
            "--res_dir", output_dir,
            "--generator_path", os.path.join(IMTALKER_DIR, "checkpoints", "generator.ckpt"),
            "--renderer_path", os.path.join(IMTALKER_DIR, "checkpoints", "renderer.ckpt"),
            "--wav2vec_model_path", os.path.join(IMTALKER_DIR, "checkpoints", "wav2vec2-base-960h"),
            "--a_cfg_scale", str(job_input.get("a_cfg_scale", 2.0)),
            "--nfe", str(job_input.get("nfe", 48)),
            "--fps", str(job_input.get("output_fps", 25)),
        ]
        if job_input.get("crop", True):
            cmd.append("--crop")

        env = os.environ.copy()
        env["PYTHONPATH"] = f"{IMTALKER_DIR}:{env.get('PYTHONPATH', '')}"

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=1800,
            cwd=IMTALKER_DIR, env=env,
        )

        if result.returncode != 0:
            raise RuntimeError(f"IMTalker failed: {result.stderr[:500]}")

        # Find output video
        output_files = [f for f in os.listdir(output_dir) if f.endswith(".mp4")]
        if not output_files:
            raise RuntimeError("IMTalker produced no output")

        src_video = os.path.join(output_dir, output_files[0])
        final_output = os.path.join(job_dir, f"output_{job_id}.mp4")
        shutil.move(src_video, final_output)

        # Upload result
        output_filename = f"imtalker_{job_id}_{int(time.time())}.mp4"
        output_url = upload_result(final_output, output_filename)

        duration = time.time() - start_time
        file_size_mb = os.path.getsize(final_output) / (1024 * 1024)

        return {
            "status": "completed",
            "output_url": output_url,
            "job_id": job_id,
            "duration_sec": round(duration, 1),
            "file_size_mb": round(file_size_mb, 1),
            "engine": "imtalker",
        }

    except Exception as e:
        logger.error(f"IMTalker error: {e}")
        return {"status": "error", "error": str(e), "job_id": job_id}

    finally:
        if os.path.isdir(job_dir):
            shutil.rmtree(job_dir, ignore_errors=True)


def handle_liveportrait(job_input: Dict[str, Any]) -> Dict[str, Any]:
    """
    LivePortrait 3-layer pipeline: portrait + audio → animated video.

    Input:
      portrait_url, audio_url, output_fps, enable_smoothing, enable_angle_policy,
      enable_idle, smoothing_alpha_exp, smoothing_alpha_pose, flicker_threshold
    """
    start_time = time.time()
    job_id = job_input.get("job_id", f"lp-{uuid.uuid4().hex[:12]}")
    job_dir = os.path.join(TEMP_DIR, f"lp_{job_id}")
    os.makedirs(job_dir, exist_ok=True)

    try:
        # Step 1: Download files
        portrait_url = job_input["portrait_url"]
        audio_url = job_input["audio_url"]

        url_lower = portrait_url.split("?")[0].lower()
        p_ext = ".png" if url_lower.endswith(".png") else ".jpg"
        portrait_path = download_file(portrait_url, os.path.join(job_dir, f"portrait{p_ext}"))

        a_url_lower = audio_url.split("?")[0].lower()
        a_ext = ".wav" if a_url_lower.endswith(".wav") else ".mp3"
        audio_path = download_file(audio_url, os.path.join(job_dir, f"audio{a_ext}"))

        audio_path = convert_audio_to_wav(audio_path, job_dir)

        # Step 2: Load LivePortrait engine
        global _liveportrait_engine
        if _liveportrait_engine is None:
            from liveportrait_engine import LivePortraitEngine
            _liveportrait_engine = LivePortraitEngine(gpu_id=0)
            logger.info("LivePortrait engine loaded")

        engine = _liveportrait_engine

        # Configure smoother
        from liveportrait_engine import TemporalSmoother
        engine.smoother = TemporalSmoother(
            alpha_exp=job_input.get("smoothing_alpha_exp", 0.3),
            alpha_pose=job_input.get("smoothing_alpha_pose", 0.2),
            flicker_threshold=job_input.get("flicker_threshold", 8.0),
        )

        # Step 3: Generate
        output_path = os.path.join(job_dir, f"output_{job_id}.mp4")
        success = engine.generate_from_audio(
            audio_path=audio_path,
            output_path=output_path,
            source_path=portrait_path,
            fps=job_input.get("output_fps", 25),
            enable_smoothing=job_input.get("enable_smoothing", True),
            enable_angle_policy=job_input.get("enable_angle_policy", True),
            enable_idle=job_input.get("enable_idle", False),
        )

        if not success or not os.path.exists(output_path):
            raise RuntimeError("LivePortrait pipeline failed")

        # Upload result
        output_filename = f"liveportrait_{job_id}_{int(time.time())}.mp4"
        output_url = upload_result(output_path, output_filename)

        duration = time.time() - start_time
        file_size_mb = os.path.getsize(output_path) / (1024 * 1024)

        return {
            "status": "completed",
            "output_url": output_url,
            "job_id": job_id,
            "duration_sec": round(duration, 1),
            "file_size_mb": round(file_size_mb, 1),
            "engine": "liveportrait",
        }

    except Exception as e:
        logger.error(f"LivePortrait error: {e}")
        return {"status": "error", "error": str(e), "job_id": job_id}

    finally:
        if os.path.isdir(job_dir):
            shutil.rmtree(job_dir, ignore_errors=True)


def handle_health(job_input: Dict[str, Any]) -> Dict[str, Any]:
    """Health check: return GPU info and model status."""
    import torch

    gpu_info = {}
    if torch.cuda.is_available():
        gpu_info = {
            "gpu_name": torch.cuda.get_device_name(0),
            "gpu_memory_total_mb": round(torch.cuda.get_device_properties(0).total_mem / 1024 / 1024),
            "gpu_memory_used_mb": round(torch.cuda.memory_allocated(0) / 1024 / 1024),
        }

    return {
        "status": "ok",
        "gpu": gpu_info,
        "models_loaded": _models_loaded,
        "musetalk_engine": _musetalk_engine is not None,
        "liveportrait_engine": _liveportrait_engine is not None,
        "facefusion_dir": os.path.isdir(FACEFUSION_DIR),
        "musetalk_dir": os.path.isdir(MUSETALK_DIR),
        "imtalker_dir": os.path.isdir(IMTALKER_DIR),
        "workspace": WORKSPACE,
        "volume_path": VOLUME_PATH,
    }


# ── Main Handler ─────────────────────────────────────────────────────────────

ACTION_HANDLERS = {
    "musetalk": handle_musetalk,
    "facefusion_video": handle_facefusion_video,
    "facefusion_frame": handle_facefusion_frame,
    "imtalker": handle_imtalker,
    "liveportrait": handle_liveportrait,
    "health": handle_health,
}


def handler(job):
    """
    Main RunPod Serverless handler.

    Routes requests to the appropriate action handler based on the 'action' field.
    """
    job_input = job.get("input", {})
    action = job_input.get("action", "")

    if action not in ACTION_HANDLERS:
        return {
            "status": "error",
            "error": f"Unknown action: '{action}'. Available: {list(ACTION_HANDLERS.keys())}",
        }

    logger.info(f"Processing action: {action}")
    result = ACTION_HANDLERS[action](job_input)
    logger.info(f"Action {action} completed: status={result.get('status')}")

    return result


# ── Start Serverless Worker ──────────────────────────────────────────────────

runpod.serverless.start({"handler": handler})
