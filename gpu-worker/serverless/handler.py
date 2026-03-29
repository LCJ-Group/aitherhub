"""
AitherHub GPU Worker — RunPod Serverless Handler
=================================================

Unified handler for all GPU-accelerated AI tasks:
  - MuseTalk lip-sync (digital human)
  - FaceFusion face swap (video & single frame)
  - IMTalker premium digital human (with v8 Poisson compositing)
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

# ── Compatibility patch: torch 2.1.x + diffusers 0.30.x ──────────────────────
# diffusers 0.30.2 uses torch.utils._pytree.register_pytree_node which was
# added in torch 2.3. For torch 2.1.x we alias the private _register_pytree_node.
import torch.utils._pytree
if not hasattr(torch.utils._pytree, 'register_pytree_node'):
    torch.utils._pytree.register_pytree_node = torch.utils._pytree._register_pytree_node
# ──────────────────────────────────────────────────────────────────────────────

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
APP_DIR = os.getenv("APP_DIR", "/app")
TEMP_DIR = os.getenv("TEMP_DIR", "/tmp/aitherhub")
os.makedirs(TEMP_DIR, exist_ok=True)

# Paths for AI tools (baked into Docker image at /app/)
# Network Volume paths are checked as fallback by setup.sh
FACEFUSION_DIR = os.getenv("FACEFUSION_DIR", f"{APP_DIR}/facefusion")
MUSETALK_DIR = os.getenv("MUSETALK_DIR", f"{APP_DIR}/MuseTalk")
IMTALKER_DIR = os.getenv("IMTALKER_DIR", f"{APP_DIR}/IMTalker")

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
    """Ensure workspace directories exist. Docker image has tools at /app/."""
    # Create workspace dir if it doesn't exist
    os.makedirs(WORKSPACE, exist_ok=True)

    # If network volume has workspace data, link it (optional)
    volume_workspace = os.path.join(VOLUME_PATH, "workspace")
    if os.path.isdir(volume_workspace):
        logger.info(f"Network volume workspace found at {volume_workspace}")

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
        "IMTalker": IMTALKER_DIR,
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

        # Step 1b: Pre-process video portrait (BUILD 50)
        # Convert high-fps/high-res videos to 25fps and max 720p for MuseTalk compatibility
        if p_ext == ".mp4":
            portrait_path = _preprocess_portrait_video(portrait_path, job_dir)

        # Determine audio extension
        a_url_lower = audio_url.split("?")[0].lower()
        a_ext = ".wav" if a_url_lower.endswith(".wav") else ".mp3"
        audio_path = download_file(audio_url, os.path.join(job_dir, f"audio{a_ext}"))

        # Convert audio to WAV if needed
        audio_path = convert_audio_to_wav(audio_path, job_dir)

        # Step 2: Load MuseTalk engine
        global _musetalk_engine
        if _musetalk_engine is None:
            # Search for live_engine.py in multiple locations
            engine_path = None
            for candidate in [
                os.path.join(APP_DIR, "live_engine.py"),
                os.path.join(WORKSPACE, "live_engine.py"),
                os.path.join(os.path.dirname(__file__), "live_engine.py"),
                os.path.join(os.path.dirname(__file__), "..", "live_engine.py"),
            ]:
                if os.path.exists(candidate):
                    engine_path = candidate
                    break
            if engine_path is None:
                raise FileNotFoundError("live_engine.py not found in any search path")

            import importlib.util
            spec = importlib.util.spec_from_file_location("live_engine", engine_path)
            live_engine_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(live_engine_mod)

            config = live_engine_mod.EngineConfig(
                version="v15",
                bbox_shift=job_input.get("bbox_shift", 0),
                extra_margin=job_input.get("extra_margin", 10),
                batch_size=job_input.get("batch_size", 16),
            )
            _musetalk_engine = live_engine_mod.MuseTalkEngine(config)
            logger.info("MuseTalk engine loaded")

        engine = _musetalk_engine

        # Step 3: Prepare avatar from portrait image/video
        if not engine.prepare_avatar(portrait_path):
            raise RuntimeError("Avatar preparation failed — could not detect face in portrait")

        # Step 4: Generate lip-sync video
        output_path = os.path.join(job_dir, f"output_{job_id}.mp4")
        success = engine.generate_test_video(
            audio_path=audio_path,
            output_path=output_path,
        )

        if not success or not os.path.exists(output_path):
            raise RuntimeError("MuseTalk pipeline failed")

        # Upload result
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
            "engine": "musetalk",
        }

    except Exception as e:
        logger.error(f"MuseTalk error: {e}", exc_info=True)
        return {"status": "error", "error": str(e), "job_id": job_id}

    finally:
        if os.path.isdir(job_dir):
            shutil.rmtree(job_dir, ignore_errors=True)


def _preprocess_portrait_video(video_path: str, job_dir: str) -> str:
    """
    BUILD 50: Pre-process portrait video for MuseTalk.
    - Limit to 25fps (MuseTalk target fps)
    - Limit resolution to 720p max dimension
    - Re-encode to H.264 for compatibility
    """
    import subprocess as sp

    try:
        # Probe video info
        probe_cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", video_path
        ]
        probe_result = sp.run(probe_cmd, capture_output=True, text=True, timeout=30)
        import json as _json
        probe_data = _json.loads(probe_result.stdout)

        width = height = fps = 0
        for stream in probe_data.get("streams", []):
            if stream.get("codec_type") == "video":
                width = int(stream.get("width", 0))
                height = int(stream.get("height", 0))
                fps_str = stream.get("r_frame_rate", "25/1")
                num, den = fps_str.split("/")
                fps = int(num) / max(int(den), 1)
                break

        needs_process = False
        vf_filters = []

        # Check if resize needed (max 720p)
        MAX_DIM = 720
        if max(width, height) > MAX_DIM:
            if width > height:
                vf_filters.append(f"scale={MAX_DIM}:-2")
            else:
                vf_filters.append(f"scale=-2:{MAX_DIM}")
            needs_process = True
            logger.info(f"[preprocess] Resize: {width}x{height} -> max {MAX_DIM}p")

        # Check if fps reduction needed
        if fps > 30:
            vf_filters.append("fps=25")
            needs_process = True
            logger.info(f"[preprocess] FPS: {fps} -> 25")

        if not needs_process:
            logger.info(f"[preprocess] Video OK: {width}x{height} @ {fps}fps, no preprocessing needed")
            return video_path

        output_path = os.path.join(job_dir, "portrait_processed.mp4")
        vf_str = ",".join(vf_filters)
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", vf_str,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "copy",
            "-movflags", "+faststart",
            output_path
        ]
        logger.info(f"[preprocess] Running: {' '.join(cmd)}")
        result = sp.run(cmd, capture_output=True, text=True, timeout=120)

        if result.returncode != 0:
            logger.warning(f"[preprocess] ffmpeg failed: {result.stderr[:300]}")
            return video_path  # fallback to original

        new_size = os.path.getsize(output_path)
        logger.info(f"[preprocess] Done: {output_path} ({new_size / 1024 / 1024:.1f} MB)")
        return output_path

    except Exception as e:
        logger.warning(f"[preprocess] Error: {e}, using original video")
        return video_path


def handle_facefusion_video(job_input: Dict[str, Any]) -> Dict[str, Any]:
    """
    FaceFusion video face swap: source face + target video → face-swapped video.

    Input:
      source_face_url, target_video_url, quality, face_enhancer, trim_start, trim_end
    """
    start_time = time.time()
    job_id = job_input.get("job_id", f"ffv-{uuid.uuid4().hex[:12]}")
    job_dir = os.path.join(TEMP_DIR, f"ffv_{job_id}")
    os.makedirs(job_dir, exist_ok=True)

    try:
        # Download files
        source_face_path = download_file(
            job_input["source_face_url"],
            os.path.join(job_dir, "source_face.jpg"),
        )
        target_video_path = download_file(
            job_input["target_video_url"],
            os.path.join(job_dir, "target.mp4"),
        )

        # Build command
        output_path = os.path.join(job_dir, "output.mp4")
        quality = job_input.get("quality", "high")
        face_enhancer = job_input.get("face_enhancer", False)

        processors = ["face_swapper"]
        if face_enhancer:
            processors.append("face_enhancer")

        cmd = [
            sys.executable, f"{FACEFUSION_DIR}/facefusion.py", "headless-run",
            "--source-paths", source_face_path,
            "--target-path", target_video_path,
            "--output-path", output_path,
            "--processors", *processors,
            "--face-swapper-model", "inswapper_128",
            "--face-swapper-pixel-boost", "512x512",
            "--face-detector-model", "retinaface",
            "--face-detector-score", "0.5",
            "--face-mask-types", "box",
            "--face-mask-blur", "0.3",
            "--face-mask-padding", "0", "0", "0", "0",
            "--output-video-quality", "80",
            "--output-video-encoder", "libx264",
            "--execution-providers", "cuda",
            "--execution-thread-count", "4",
        ]

        if face_enhancer:
            cmd.extend(["--face-enhancer-model", "gfpgan_1.4"])

        trim_start = job_input.get("trim_start")
        trim_end = job_input.get("trim_end")
        if trim_start is not None:
            cmd.extend(["--trim-frame-start", str(trim_start)])
        if trim_end is not None:
            cmd.extend(["--trim-frame-end", str(trim_end)])

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=1800,
            cwd=FACEFUSION_DIR,
        )

        if result.returncode != 0 or not os.path.exists(output_path):
            raise RuntimeError(f"FaceFusion failed: {result.stderr[:500]}")

        # Upload result
        output_filename = f"facefusion_{job_id}_{int(time.time())}.mp4"
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


# ── IMTalker v8 Compositing Helpers ──────────────────────────────────────────

def _extract_first_frame(video_path: str, output_path: str) -> bool:
    """Extract the first frame from a video using ffmpeg."""
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-vframes", "1", "-q:v", "2", output_path],
        capture_output=True, timeout=30,
    )
    return os.path.exists(output_path) and os.path.getsize(output_path) > 0


def _imtalker_v8_composite(
    src_video: str,
    portrait_path: str,
    portrait_video_path: Optional[str],
    portrait_is_video: bool,
    final_output: str,
    job_dir: str,
    job_id: str,
) -> bool:
    """
    v8 Composite: OpenCV frame-by-frame with Poisson blending.

    Reads crop coordinates from JSON saved by patched generate.py,
    uses inner 90% of IMTalker 512x512 output, applies seamlessClone
    (Poisson blending) with the driving video/image as background.
    Falls back to Gaussian-blurred elliptical mask if seamlessClone fails.
    """
    import cv2
    import numpy as np
    from PIL import Image

    img = Image.open(portrait_path)
    orig_w, orig_h = img.size
    logger.info(f"[IMT {job_id}] v8: Original portrait: {orig_w}x{orig_h}")

    # ── Read crop coordinates from JSON (saved by patched generate.py) ──
    output_dir = os.path.dirname(src_video)
    crop_json_path = None
    for f in os.listdir(output_dir):
        if f.endswith("_crop_coords.json"):
            crop_json_path = os.path.join(output_dir, f)
            break

    if crop_json_path and os.path.exists(crop_json_path):
        with open(crop_json_path, 'r') as f:
            crop_coords = json.load(f)
        crop_x1 = crop_coords["x1"]
        crop_y1 = crop_coords["y1"]
        crop_x2 = crop_coords["x2"]
        crop_y2 = crop_coords["y2"]
        side = crop_coords["side"]
        logger.info(f"[IMT {job_id}] v8: Crop coords from JSON: ({crop_x1},{crop_y1})-({crop_x2},{crop_y2}), side={side}")
    else:
        # Fallback: replicate IMTalker's crop logic using face_alignment
        logger.info(f"[IMT {job_id}] v8: No crop JSON, replicating crop logic...")
        import face_alignment
        fa = face_alignment.FaceAlignment(
            face_alignment.LandmarksType.TWO_D, flip_input=False, device='cuda'
        )
        img_arr = np.array(img)
        bboxes = fa.face_detector.detect_from_image(img_arr)
        valid_bboxes = [
            (int(x1), int(y1), int(x2), int(y2), score)
            for (x1, y1, x2, y2, score) in bboxes if score > 0.5
        ]
        if not valid_bboxes:
            raise RuntimeError("No face detected for v8 composite")
        x1, y1, x2, y2, _ = valid_bboxes[0]
        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2
        half_w = int((x2 - x1) * 0.8)
        half_h = int((y2 - y1) * 0.8)
        half = max(half_w, half_h)
        crop_x1 = max(cx - half, 0)
        crop_x2 = min(cx + half, orig_w)
        crop_y1 = max(cy - half, 0)
        crop_y2 = min(cy + half, orig_h)
        side = min(crop_x2 - crop_x1, crop_y2 - crop_y1)
        crop_x2 = crop_x1 + side
        crop_y2 = crop_y1 + side
        del fa  # free GPU memory

    # ── v8 parameters ──
    INNER_RATIO = 0.90
    IMT_SIZE = 512
    inner_px = int(IMT_SIZE * INNER_RATIO)
    margin = (IMT_SIZE - inner_px) // 2
    inner_side = int(side * INNER_RATIO)
    inner_x1 = crop_x1 + (side - inner_side) // 2
    inner_y1 = crop_y1 + (side - inner_side) // 2

    logger.info(
        f"[IMT {job_id}] v8 params: inner_ratio={INNER_RATIO}, "
        f"inner_px={inner_px}, margin={margin}, inner_side={inner_side}, "
        f"inner_pos=({inner_x1},{inner_y1})"
    )

    # ── Create elliptical mask for blending ──
    mask_2d = np.zeros((inner_px, inner_px), dtype=np.float32)
    cy_m = inner_px * 0.47
    cx_m = inner_px / 2.0
    ry = inner_px * 0.47
    rx = inner_px * 0.46
    y_coords = np.arange(inner_px, dtype=np.float32)
    x_coords = np.arange(inner_px, dtype=np.float32)
    dy = (y_coords - cy_m) / max(ry, 1)
    dx = (x_coords - cx_m) / max(rx, 1)
    dist = np.sqrt(dy[:, None]**2 + dx[None, :]**2)
    mask_2d = np.clip(1.0 - (dist - 0.6) / 0.5, 0.0, 1.0)
    blur_k = max(31, int(inner_px * 0.15) | 1)
    mask_2d = cv2.GaussianBlur(mask_2d, (blur_k, blur_k), 0)
    poisson_mask = (mask_2d > 0.5).astype(np.uint8) * 255

    # ── Read IMTalker output video ──
    imt_cap = cv2.VideoCapture(src_video)
    imt_fps = imt_cap.get(cv2.CAP_PROP_FPS) or 25
    imt_frame_count = int(imt_cap.get(cv2.CAP_PROP_FRAME_COUNT))
    logger.info(f"[IMT {job_id}] IMTalker output: {imt_frame_count} frames at {imt_fps} fps")

    # ── Read background source ──
    use_video_bg = portrait_is_video and portrait_video_path and os.path.exists(portrait_video_path)
    bg_label = "driving_video" if use_video_bg else "static_image"
    logger.info(f"[IMT {job_id}] v8 background: {bg_label}")

    if use_video_bg:
        bg_cap = cv2.VideoCapture(portrait_video_path)
        bg_frame_count = int(bg_cap.get(cv2.CAP_PROP_FRAME_COUNT))
    else:
        bg_img = cv2.imread(portrait_path)
        bg_cap = None
        bg_frame_count = 0

    # ── Setup output video writer ──
    temp_video_no_audio = os.path.join(job_dir, "v8_composite_noaudio.mp4")
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out_writer = cv2.VideoWriter(
        temp_video_no_audio, fourcc, imt_fps, (orig_w, orig_h)
    )

    # ── Frame-by-frame compositing ──
    frame_idx = 0
    poisson_fail_count = 0

    while True:
        ret, imt_frame = imt_cap.read()
        if not ret:
            break

        # Get background frame
        if use_video_bg and bg_cap is not None:
            bg_ret, bg_frame = bg_cap.read()
            if not bg_ret:
                bg_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                bg_ret, bg_frame = bg_cap.read()
                if not bg_ret:
                    bg_frame = bg_img.copy() if bg_img is not None else np.zeros((orig_h, orig_w, 3), dtype=np.uint8)
            bg_frame = cv2.resize(bg_frame, (orig_w, orig_h))
        else:
            bg_frame = bg_img.copy()

        # Extract inner region from IMTalker output
        face_crop = imt_frame[margin:margin+inner_px, margin:margin+inner_px]
        face_resized = cv2.resize(face_crop, (inner_side, inner_side))

        # Resize mask
        alpha_resized = cv2.resize(mask_2d, (inner_side, inner_side))
        mask_resized = cv2.resize(poisson_mask, (inner_side, inner_side))

        # Ensure coordinates are within bounds
        fx1 = max(0, inner_x1)
        fy1 = max(0, inner_y1)
        fx2 = min(orig_w, inner_x1 + inner_side)
        fy2 = min(orig_h, inner_y1 + inner_side)
        sx1 = fx1 - inner_x1
        sy1 = fy1 - inner_y1
        sx2 = sx1 + (fx2 - fx1)
        sy2 = sy1 + (fy2 - fy1)

        # Try Poisson blending (seamlessClone)
        try:
            center_x = (fx1 + fx2) // 2
            center_y = (fy1 + fy2) // 2
            center = (int(center_x), int(center_y))
            result_frame = cv2.seamlessClone(
                face_resized, bg_frame, mask_resized, center, cv2.NORMAL_CLONE
            )
            out_writer.write(result_frame)
            frame_idx += 1
            continue
        except Exception as e_poisson:
            if poisson_fail_count == 0:
                logger.warning(f"[IMT {job_id}] Poisson blend failed on frame {frame_idx}: {e_poisson}")
            poisson_fail_count += 1

        # Fallback: alpha blending with soft mask
        alpha_3ch = np.stack([alpha_resized]*3, axis=-1)
        roi = bg_frame[fy1:fy2, fx1:fx2]
        face_region = face_resized[sy1:sy2, sx1:sx2]
        alpha_region = alpha_3ch[sy1:sy2, sx1:sx2]
        blended = (face_region.astype(np.float32) * alpha_region +
                   roi.astype(np.float32) * (1.0 - alpha_region))
        bg_frame[fy1:fy2, fx1:fx2] = blended.astype(np.uint8)
        out_writer.write(bg_frame)
        frame_idx += 1

    out_writer.release()
    imt_cap.release()
    if bg_cap is not None:
        bg_cap.release()

    logger.info(
        f"[IMT {job_id}] v8 composite: {frame_idx} frames, "
        f"poisson_fails={poisson_fail_count}"
    )

    # ── Add audio back ──
    add_audio_cmd = [
        "ffmpeg", "-y",
        "-i", temp_video_no_audio,
        "-i", src_video,
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-map", "0:v:0", "-map", "1:a:0?",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        "-pix_fmt", "yuv420p",
        final_output,
    ]
    audio_result = subprocess.run(
        add_audio_cmd, capture_output=True, timeout=120, cwd=job_dir,
    )
    if audio_result.returncode != 0:
        logger.warning(f"[IMT {job_id}] Audio mux failed, using video without audio")
        shutil.move(temp_video_no_audio, final_output)
    else:
        if os.path.exists(temp_video_no_audio):
            os.remove(temp_video_no_audio)

    logger.info(f"[IMT {job_id}] v8 composite complete: {final_output}")
    return True


def handle_imtalker(job_input: Dict[str, Any]) -> Dict[str, Any]:
    """
    IMTalker premium digital human: portrait + audio → full facial animation video.
    Includes v8 Poisson compositing for natural full-resolution output.

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
        portrait_type = job_input.get("portrait_type", "image")

        url_lower = portrait_url.split("?")[0].lower()
        is_video_by_type = portrait_type == "video"
        is_video_by_url = url_lower.endswith((".mov", ".mp4", ".avi", ".mkv"))
        portrait_is_video = is_video_by_type or is_video_by_url

        if portrait_is_video:
            p_ext = ".mp4"
        elif url_lower.endswith(".png"):
            p_ext = ".png"
        else:
            p_ext = ".jpg"

        portrait_download_path = download_file(portrait_url, os.path.join(job_dir, f"portrait{p_ext}"))

        # If video portrait, extract first frame for IMTalker (needs still image)
        portrait_video_path = None
        if portrait_is_video:
            portrait_video_path = portrait_download_path
            portrait_path = os.path.join(job_dir, "portrait_frame.png")
            if not _extract_first_frame(portrait_video_path, portrait_path):
                logger.warning(f"[IMT {job_id}] Frame extraction failed, using video directly")
                portrait_is_video = False
                portrait_path = portrait_download_path
        else:
            portrait_path = portrait_download_path

        a_url_lower = audio_url.split("?")[0].lower()
        a_ext = ".wav" if a_url_lower.endswith(".wav") else ".mp3"
        audio_path = download_file(audio_url, os.path.join(job_dir, f"audio{a_ext}"))

        # Convert audio
        audio_path = convert_audio_to_wav(audio_path, job_dir)

        # Step 2: Run IMTalker inference (produces 512x512 output)
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
        env["PYTHONPATH"] = f"{IMTALKER_DIR}:{IMTALKER_DIR}/generator:{env.get('PYTHONPATH', '')}"

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

        # Step 3: v8 Composite — full-resolution Poisson blending
        try:
            _imtalker_v8_composite(
                src_video=src_video,
                portrait_path=portrait_path,
                portrait_video_path=portrait_video_path,
                portrait_is_video=portrait_is_video,
                final_output=final_output,
                job_dir=job_dir,
                job_id=job_id,
            )
        except Exception as e_v8:
            logger.warning(f"[IMT {job_id}] v8 composite failed ({e_v8}), falling back to scaled output")
            # Fallback: scale 512x512 to fit 9:16 with padding
            from PIL import Image as PILImage
            try:
                img_fb = PILImage.open(portrait_path)
                orig_w_fb, orig_h_fb = img_fb.size
            except Exception:
                orig_w_fb, orig_h_fb = 1080, 1920
            target_w, target_h = (1080, 1920) if orig_w_fb >= 1080 else (720, 1280)
            filter_simple = (
                f"[0:v]scale={target_w}:{target_h}:"
                f"force_original_aspect_ratio=decrease,"
                f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:"
                f"color=0xE6DCD2[out]"
            )
            fallback_cmd = [
                "ffmpeg", "-y",
                "-i", src_video,
                "-filter_complex", filter_simple,
                "-map", "[out]", "-map", "0:a?",
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-c:a", "aac", "-b:a", "128k",
                "-shortest", "-pix_fmt", "yuv420p",
                final_output,
            ]
            subprocess.run(fallback_cmd, capture_output=True, timeout=120)
            if not os.path.exists(final_output):
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
        props = torch.cuda.get_device_properties(0)
        total_mem = getattr(props, 'total_memory', getattr(props, 'total_mem', 0))
        gpu_info = {
            "gpu_name": torch.cuda.get_device_name(0),
            "gpu_memory_total_mb": round(total_mem / 1024 / 1024),
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
        "app_dir": APP_DIR,
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
