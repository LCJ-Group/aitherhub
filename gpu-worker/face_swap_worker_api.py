"""
FaceFusion GPU Worker API Server
================================

A FastAPI wrapper around FaceFusion that exposes HTTP endpoints
for AitherHub to control real-time face swapping remotely.

Architecture:
  Body Double (RTMP in) → ffmpeg → virtual cam → FaceFusion → UDP → ffmpeg → RTMP out

Endpoints:
  POST /api/health          - GPU health check
  POST /api/set-source      - Upload source face image
  POST /api/start-stream    - Start real-time face swap stream
  POST /api/stop-stream     - Stop the running stream
  GET  /api/stream-status   - Get current stream metrics
  POST /api/swap-frame      - Swap face on a single image (test)
  GET  /api/config          - Get current FaceFusion configuration
  POST /api/config          - Update FaceFusion configuration
"""

import asyncio
import base64
import io
import json
import logging
import os
import signal
import subprocess
import sys

# ── Ensure CUDA libraries (cuDNN, cuBLAS) are discoverable ────────────────────
_nvidia_lib_dirs = [
    "/usr/local/lib/python3.11/dist-packages/nvidia/cudnn/lib",
    "/usr/local/lib/python3.11/dist-packages/nvidia/cublas/lib",
]
_existing = os.environ.get("LD_LIBRARY_PATH", "")
_new_paths = [p for p in _nvidia_lib_dirs if os.path.isdir(p) and p not in _existing]
if _new_paths:
    os.environ["LD_LIBRARY_PATH"] = ":".join(_new_paths) + (":" + _existing if _existing else "")
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("face-swap-worker")

# ── Configuration ────────────────────────────────────────────────────────────

WORKER_API_KEY = os.getenv("WORKER_API_KEY", "change-me-in-production")
FACEFUSION_DIR = os.getenv("FACEFUSION_DIR", "/workspace/facefusion")
SOURCE_FACE_DIR = os.getenv("SOURCE_FACE_DIR", "/workspace/source_faces")
TEMP_DIR = os.getenv("TEMP_DIR", "/workspace/tmp")
PORT = int(os.getenv("PORT", os.getenv("WORKER_PORT", "8000")))

# Ensure directories exist
Path(SOURCE_FACE_DIR).mkdir(parents=True, exist_ok=True)
Path(TEMP_DIR).mkdir(parents=True, exist_ok=True)

# ── State ────────────────────────────────────────────────────────────────────

current_session = {
    "id": None,
    "status": "idle",       # idle | starting | running | stopping | error
    "facefusion_proc": None,
    "ffmpeg_in_proc": None,
    "ffmpeg_out_proc": None,
    "start_time": None,
    "config": {},
    "error": None,
}

current_config = {
    "face_swapper_model": "inswapper_128",
    "face_swapper_pixel_boost": "512x512",
    "face_swapper_weight": 0.85,
    "face_enhancer_model": "gfpgan_1.4",
    "face_enhancer_enabled": True,
    "face_detector_model": "yolo_face",
    "face_detector_score": 0.5,
    "face_mask_types": ["box", "occlusion", "region"],
    "face_mask_blur": 0.3,
    "face_mask_padding": [0, 0, 0, 0],
    "output_image_quality": 95,
    "output_resolution": "1280x720",
    "output_fps": 30,
    "execution_providers": "cuda",
    "execution_thread_count": 4,
}

source_face_path: Optional[str] = None

# ── InsightFace In-Memory Engine ──────────────────────────────────────────────
# These are loaded once at startup and reused for every frame (50-200ms vs 14-35s)
face_analyzer = None       # insightface.app.FaceAnalysis instance
face_swapper_model = None  # INSwapper ONNX model
source_face_embedding = None  # Pre-computed source face object from FaceAnalysis
face_enhancer_net = None   # GFPGAN face restoration network for HD quality
INSWAPPER_MODEL_PATH = os.getenv(
    "INSWAPPER_MODEL_PATH",
    "/workspace/facefusion/.assets/models/inswapper_128.onnx"
)
GFPGAN_MODEL_PATH = os.getenv(
    "GFPGAN_MODEL_PATH",
    "/workspace/facefusion/.assets/models/GFPGANv1.4.pth"
)


# ── Auth ─────────────────────────────────────────────────────────────────────

async def verify_api_key(x_api_key: str = Header(...)):
    """Verify the API key from request header."""
    if x_api_key != WORKER_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return True


# ── Models ───────────────────────────────────────────────────────────────────

class SetSourceRequest(BaseModel):
    image_url: Optional[str] = None
    image_base64: Optional[str] = None
    face_index: int = Field(default=0, description="Index of face to use if multiple detected")


class StartStreamRequest(BaseModel):
    input_rtmp: str = Field(..., description="RTMP URL of incoming stream (body double)")
    output_rtmp: str = Field(..., description="RTMP URL for outgoing stream (to platform)")
    quality: str = Field(default="high", description="Quality preset: fast, balanced, high")
    resolution: str = Field(default="720p", description="Output resolution: 480p, 720p, 1080p")
    fps: int = Field(default=30, description="Output FPS")
    face_enhancer: bool = Field(default=True, description="Enable GFPGAN face enhancement")
    # Liver Clone audio settings
    voice_id: Optional[str] = Field(default=None, description="ElevenLabs voice ID for STS/TTS")
    audio_mode: str = Field(default="hybrid", description="Audio mode: manual, auto, hybrid")
    vad_threshold: float = Field(default=0.3, description="VAD energy threshold (0-1)")
    silence_timeout: float = Field(default=5.0, description="Seconds of silence before auto-pilot")
    voice_stability: float = Field(default=0.5, description="ElevenLabs voice stability")
    voice_similarity: float = Field(default=0.75, description="ElevenLabs voice similarity")
    language: str = Field(default="en", description="Language for TTS")
    liver_clone_session_id: Optional[str] = Field(default=None, description="Liver Clone session ID")


class StopStreamRequest(BaseModel):
    session_id: Optional[str] = None


class SwapFrameRequest(BaseModel):
    image_base64: str = Field(..., description="Base64-encoded input image")
    quality: str = Field(default="high", description="Quality preset")
    face_enhancer: bool = Field(default=True, description="Enable face enhancement")


class SwapVideoRequest(BaseModel):
    """Request to start a video face swap job."""
    job_id: str = Field(..., description="Unique job ID assigned by the backend")
    video_url: str = Field(..., description="URL to download the input video")
    face_enhancer: bool = Field(default=True, description="Enable face enhancement")
    quality: str = Field(default="high", description="Quality preset: fast, balanced, high, pro, cinema")
    output_video_quality: int = Field(default=90, description="Output video quality 0-100")


# ── Video Job State ─────────────────────────────────────────────────────────

video_jobs: dict = {}  # job_id -> {status, progress, output_path, error, ...}


class UpdateConfigRequest(BaseModel):
    face_swapper_model: Optional[str] = None
    face_swapper_pixel_boost: Optional[str] = None
    face_swapper_weight: Optional[float] = None
    face_enhancer_model: Optional[str] = None
    face_enhancer_enabled: Optional[bool] = None
    face_detector_model: Optional[str] = None
    face_detector_score: Optional[float] = None
    face_mask_types: Optional[list] = None
    face_mask_blur: Optional[float] = None
    face_mask_padding: Optional[list] = None
    output_image_quality: Optional[int] = None
    output_resolution: Optional[str] = None
    output_fps: Optional[int] = None
    execution_thread_count: Optional[int] = None


# ── Helper Functions ─────────────────────────────────────────────────────────

def get_gpu_info() -> dict:
    """Get GPU information via nvidia-smi."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,temperature.gpu,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(", ")
            return {
                "gpu_name": parts[0],
                "gpu_memory_used_mb": float(parts[1]),
                "gpu_memory_total_mb": float(parts[2]),
                "gpu_temperature_c": float(parts[3]),
                "gpu_utilization_pct": float(parts[4]),
            }
    except Exception as e:
        logger.warning(f"Failed to get GPU info: {e}")
    return {
        "gpu_name": "unknown",
        "gpu_memory_used_mb": 0,
        "gpu_memory_total_mb": 0,
        "gpu_temperature_c": 0,
        "gpu_utilization_pct": 0,
    }


def kill_process_tree(proc):
    """Kill a process and all its children."""
    if proc is None:
        return
    try:
        pid = proc.pid
        # Kill process group
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        proc.wait(timeout=5)
    except (ProcessLookupError, ChildProcessError, subprocess.TimeoutExpired):
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"Error killing process: {e}")


def build_facefusion_webcam_cmd() -> list:
    """Build the FaceFusion command for webcam mode with UDP output."""
    cmd = [
        sys.executable, f"{FACEFUSION_DIR}/facefusion.py", "run",
        "--source-paths", source_face_path,
        "--processors", "face_swapper",
        "--face-swapper-model", current_config["face_swapper_model"],
        "--face-detector-model", current_config["face_detector_model"],
        "--face-detector-score", str(current_config["face_detector_score"]),
        "--execution-providers", current_config["execution_providers"],
        "--execution-thread-count", str(current_config["execution_thread_count"]),
        "--webcam-mode", "udp",
        "--webcam-resolution", current_config["output_resolution"],
        "--webcam-fps", str(current_config["output_fps"]),
    ]

    if current_config["face_enhancer_enabled"]:
        cmd[cmd.index("face_swapper")] = "face_swapper face_enhancer"
        # Actually need to split properly
        idx = cmd.index("face_swapper face_enhancer")
        cmd[idx:idx+1] = ["face_swapper", "face_enhancer"]
        cmd.extend(["--face-enhancer-model", current_config["face_enhancer_model"]])

    return cmd


def build_facefusion_headless_cmd(input_path: str, output_path: str) -> list:
    """Build the FaceFusion command for headless single-image processing."""
    processors = ["face_swapper"]
    if current_config["face_enhancer_enabled"]:
        processors.append("face_enhancer")

    cmd = [
        sys.executable, f"{FACEFUSION_DIR}/facefusion.py", "headless-run",
        "--source-paths", source_face_path,
        "--target-path", input_path,
        "--output-path", output_path,
        # Processors
        "--processors", *processors,
        # Face swapper settings
        "--face-swapper-model", current_config["face_swapper_model"],
        "--face-swapper-pixel-boost", current_config["face_swapper_pixel_boost"],
        "--face-swapper-weight", str(current_config["face_swapper_weight"]),
        # Face detector settings
        "--face-detector-model", current_config["face_detector_model"],
        "--face-detector-score", str(current_config["face_detector_score"]),
        # Face mask settings
        "--face-mask-types", *current_config["face_mask_types"],
        "--face-mask-blur", str(current_config["face_mask_blur"]),
        "--face-mask-padding", *[str(p) for p in current_config["face_mask_padding"]],
        # Output settings
        "--output-image-quality", str(current_config["output_image_quality"]),
        # Execution settings
        "--execution-providers", current_config["execution_providers"],
        "--execution-thread-count", str(current_config["execution_thread_count"]),
    ]

    if current_config["face_enhancer_enabled"]:
        cmd.extend(["--face-enhancer-model", current_config["face_enhancer_model"]])

    return cmd


# ── HD Enhancement Helper Functions ───────────────────────────────────────────

def _color_correct_face(original, swapped, faces):
    """
    Color-correct the swapped face region to match the original frame's skin tone.
    Uses histogram matching in LAB color space for natural color blending.
    """
    try:
        import cv2
        import numpy as np
        
        result = swapped.copy()
        for face in faces:
            bbox = face.bbox.astype(int)
            x1, y1, x2, y2 = bbox
            # Clamp to image bounds
            h, w = original.shape[:2]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            if x2 <= x1 or y2 <= y1:
                continue
            
            # Extract face regions
            orig_face = original[y1:y2, x1:x2]
            swap_face = result[y1:y2, x1:x2]
            
            if orig_face.size == 0 or swap_face.size == 0:
                continue
            
            # Convert to LAB for perceptual color matching
            orig_lab = cv2.cvtColor(orig_face, cv2.COLOR_BGR2LAB).astype(np.float32)
            swap_lab = cv2.cvtColor(swap_face, cv2.COLOR_BGR2LAB).astype(np.float32)
            
            # Match mean and std of each channel
            for ch in range(3):
                orig_mean = orig_lab[:, :, ch].mean()
                orig_std = orig_lab[:, :, ch].std() + 1e-6
                swap_mean = swap_lab[:, :, ch].mean()
                swap_std = swap_lab[:, :, ch].std() + 1e-6
                
                # Normalize and re-scale
                swap_lab[:, :, ch] = (
                    (swap_lab[:, :, ch] - swap_mean) * (orig_std / swap_std) + orig_mean
                )
            
            # Clip and convert back
            swap_lab = np.clip(swap_lab, 0, 255).astype(np.uint8)
            corrected = cv2.cvtColor(swap_lab, cv2.COLOR_LAB2BGR)
            result[y1:y2, x1:x2] = corrected
        
        return result
    except Exception:
        return swapped


def _seamless_blend(original, swapped, faces):
    """
    Apply Poisson seamless cloning at face boundaries for natural blending.
    Falls back to Gaussian blur blending if seamlessClone fails.
    """
    try:
        import cv2
        import numpy as np
        
        result = swapped.copy()
        h, w = original.shape[:2]
        
        for face in faces:
            bbox = face.bbox.astype(int)
            x1, y1, x2, y2 = bbox
            # Expand bbox slightly for better blending
            pad = int((x2 - x1) * 0.1)
            x1, y1 = max(0, x1 - pad), max(0, y1 - pad)
            x2, y2 = min(w, x2 + pad), min(h, y2 + pad)
            if x2 <= x1 or y2 <= y1:
                continue
            
            # Create elliptical mask for face region
            face_w = x2 - x1
            face_h = y2 - y1
            center_x = (x1 + x2) // 2
            center_y = (y1 + y2) // 2
            
            mask = np.zeros((h, w), dtype=np.uint8)
            cv2.ellipse(
                mask,
                (center_x, center_y),
                (face_w // 2, face_h // 2),
                0, 0, 360, 255, -1
            )
            
            # Gaussian blur the mask edges for smooth transition
            blur_size = max(3, face_w // 8) | 1  # Must be odd
            mask_blurred = cv2.GaussianBlur(mask, (blur_size, blur_size), 0)
            
            # Alpha blend using the blurred mask
            alpha = mask_blurred.astype(np.float32) / 255.0
            alpha = alpha[:, :, np.newaxis]
            
            result = (swapped * alpha + original * (1 - alpha)).astype(np.uint8)
        
        return result
    except Exception:
        return swapped


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    global face_analyzer, face_swapper_model, source_face_embedding, source_face_path, face_enhancer_net

    logger.info("FaceFusion GPU Worker starting up...")
    logger.info(f"FaceFusion directory: {FACEFUSION_DIR}")
    logger.info(f"Source face directory: {SOURCE_FACE_DIR}")

    gpu_info = get_gpu_info()
    logger.info(f"GPU: {gpu_info['gpu_name']} ({gpu_info['gpu_memory_total_mb']}MB)")

    # ── Load InsightFace models for in-memory face swap ──────────────────────
    try:
        import insightface
        from insightface.app import FaceAnalysis
        import onnxruntime

        logger.info("[InsightFace] Loading face analyzer (buffalo_l)...")
        face_analyzer = FaceAnalysis(
            name='buffalo_l',
            providers=['CUDAExecutionProvider', 'CPUExecutionProvider']
        )
        face_analyzer.prepare(ctx_id=0, det_size=(640, 640))
        logger.info("[InsightFace] Face analyzer loaded successfully")

        if os.path.exists(INSWAPPER_MODEL_PATH):
            logger.info(f"[InsightFace] Loading swapper model: {INSWAPPER_MODEL_PATH}")
            face_swapper_model = insightface.model_zoo.get_model(
                INSWAPPER_MODEL_PATH,
                download=False,
                providers=['CUDAExecutionProvider', 'CPUExecutionProvider']
            )
            logger.info("[InsightFace] Swapper model loaded successfully")
        else:
            logger.warning(f"[InsightFace] inswapper_128.onnx not found at {INSWAPPER_MODEL_PATH}")
            logger.warning("[InsightFace] Will fall back to FaceFusion CLI for face swap")

        # If a source face was previously saved, pre-compute embedding
        existing_faces = sorted(Path(SOURCE_FACE_DIR).glob("source_face_*.jpg"), reverse=True)
        if existing_faces and face_analyzer:
            import cv2
            latest_face = str(existing_faces[0])
            source_face_path = latest_face  # Also set the global path
            img = cv2.imread(latest_face)
            if img is not None:
                faces = face_analyzer.get(img)
                if faces:
                    source_face_embedding = faces[0]
                    logger.info(f"[InsightFace] Pre-loaded source face from {latest_face}")

    except ImportError as e:
        logger.warning(f"[InsightFace] insightface not available: {e}")
        logger.warning("[InsightFace] Will use FaceFusion CLI fallback")
    except Exception as e:
        logger.error(f"[InsightFace] Failed to load models: {e}")
        logger.warning("[InsightFace] Will use FaceFusion CLI fallback")

    # ── Load GFPGAN face enhancer for HD quality ────────────────────────────
    try:
        if os.path.exists(GFPGAN_MODEL_PATH):
            from gfpgan import GFPGANer
            face_enhancer_net = GFPGANer(
                model_path=GFPGAN_MODEL_PATH,
                upscale=1,
                arch='clean',
                channel_multiplier=2,
                bg_upsampler=None,
            )
            logger.info(f"[GFPGAN] Face enhancer loaded: {GFPGAN_MODEL_PATH}")
        else:
            logger.info(f"[GFPGAN] Model not found at {GFPGAN_MODEL_PATH}, HD enhancement disabled")
    except ImportError:
        logger.info("[GFPGAN] gfpgan package not installed, HD enhancement disabled")
    except Exception as e:
        logger.warning(f"[GFPGAN] Failed to load: {e}")

    logger.info(f"[InsightFace] Engine status: analyzer={'OK' if face_analyzer else 'NONE'}, "
                f"swapper={'OK' if face_swapper_model else 'NONE'}, "
                f"source_face={'OK' if source_face_embedding else 'NONE'}, "
                f"enhancer={'OK' if face_enhancer_net else 'NONE'}")

    yield

    # Cleanup on shutdown
    logger.info("Shutting down, cleaning up processes...")
    if current_session["facefusion_proc"]:
        kill_process_tree(current_session["facefusion_proc"])
    if current_session["ffmpeg_in_proc"]:
        kill_process_tree(current_session["ffmpeg_in_proc"])
    if current_session["ffmpeg_out_proc"]:
        kill_process_tree(current_session["ffmpeg_out_proc"])


# ── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="FaceFusion GPU Worker",
    description="Real-time face swap worker for AitherHub",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health_check(auth: bool = Depends(verify_api_key)):
    """
    GPU worker health check.
    Returns GPU status, FaceFusion version, and stream state.
    """
    gpu_info = get_gpu_info()

    # Check FaceFusion installation
    ff_installed = Path(f"{FACEFUSION_DIR}/facefusion.py").exists()
    ff_version = "unknown"
    if ff_installed:
        try:
            result = subprocess.run(
                [sys.executable, f"{FACEFUSION_DIR}/facefusion.py", "--version"],
                capture_output=True, text=True, timeout=10,
            )
            ff_version = result.stdout.strip() or "3.5.x"
        except Exception:
            ff_version = "3.5.x (assumed)"

    return {
        "status": "ok" if ff_installed else "facefusion_not_found",
        "gpu": gpu_info,
        "facefusion_installed": ff_installed,
        "facefusion_version": ff_version,
        "source_face_loaded": source_face_path is not None and Path(source_face_path).exists(),
        "stream_status": current_session["status"],
        "session_id": current_session["id"],
        "config": current_config,
        "insightface_engine": {
            "face_analyzer": face_analyzer is not None,
            "face_swapper_model": face_swapper_model is not None,
            "source_face_embedding": source_face_embedding is not None,
            "face_enhancer_hd": face_enhancer_net is not None,
            "hd_pipeline": "color_correction + gfpgan + seamless_blend",
            "ready": all([
                face_analyzer is not None,
                face_swapper_model is not None,
                source_face_embedding is not None,
            ]),
        },
    }


@app.post("/api/set-source")
async def set_source(
    auth: bool = Depends(verify_api_key),
    image_url: Optional[str] = None,
    image_base64: Optional[str] = None,
    file: Optional[UploadFile] = File(None),
    face_index: int = 0,
):
    """
    Set the source face image (the influencer's face).
    Accepts: file upload, base64 string, or URL.
    """
    global source_face_path

    save_path = os.path.join(SOURCE_FACE_DIR, f"source_face_{int(time.time())}.jpg")

    if file is not None:
        # File upload
        content = await file.read()
        with open(save_path, "wb") as f:
            f.write(content)
        logger.info(f"Source face saved from upload: {save_path} ({len(content)} bytes)")

    elif image_base64:
        # Base64
        content = base64.b64decode(image_base64)
        with open(save_path, "wb") as f:
            f.write(content)
        logger.info(f"Source face saved from base64: {save_path} ({len(content)} bytes)")

    elif image_url:
        # URL download
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(image_url)
            resp.raise_for_status()
            with open(save_path, "wb") as f:
                f.write(resp.content)
        logger.info(f"Source face downloaded from URL: {save_path} ({len(resp.content)} bytes)")

    else:
        raise HTTPException(400, "Provide file, image_base64, or image_url")

    source_face_path = save_path

    # Compute source face embedding using InsightFace in-memory engine
    global source_face_embedding
    face_detected = False
    num_faces = 0

    if face_analyzer is not None:
        try:
            import cv2
            img = cv2.imread(save_path)
            if img is not None:
                faces = face_analyzer.get(img)
                num_faces = len(faces)
                if faces:
                    # Use specified face_index (default 0 = largest/most prominent)
                    idx = min(face_index, len(faces) - 1)
                    source_face_embedding = faces[idx]
                    face_detected = True
                    logger.info(f"[InsightFace] Source face embedding computed "
                                f"({num_faces} faces detected, using index {idx})")
                else:
                    logger.warning("[InsightFace] No faces detected in source image")
        except Exception as e:
            logger.error(f"[InsightFace] Error computing source face embedding: {e}")
    else:
        # Fallback: assume face is valid, will use FaceFusion CLI
        face_detected = True
        logger.info("[InsightFace] Engine not loaded, skipping embedding computation")

    return {
        "status": "ok",
        "source_face_path": save_path,
        "face_detected": face_detected,
        "face_index": face_index,
        "num_faces_detected": num_faces,
        "insightface_engine": face_analyzer is not None,
    }


@app.post("/api/start-stream")
async def start_stream(req: StartStreamRequest, auth: bool = Depends(verify_api_key)):
    """
    Start real-time face swap stream.

    Pipeline:
      1. ffmpeg pulls RTMP input → creates virtual webcam (/dev/video10)
      2. FaceFusion reads webcam → face swap → UDP output (udp://localhost:27000)
      3. ffmpeg reads UDP → pushes to RTMP output

    Requires: source face already set via /api/set-source
    """
    global current_session

    if current_session["status"] in ("running", "starting"):
        raise HTTPException(409, f"Stream already {current_session['status']}")

    if source_face_path is None or not Path(source_face_path).exists():
        raise HTTPException(400, "Source face not set. Call /api/set-source first.")

    session_id = f"sess-{uuid.uuid4().hex[:12]}"
    current_session["status"] = "starting"
    current_session["id"] = session_id
    current_session["error"] = None

    # Apply quality preset
    if req.quality == "fast":
        current_config["face_enhancer_enabled"] = False
    elif req.quality == "balanced":
        current_config["face_enhancer_enabled"] = True
        current_config["face_enhancer_model"] = "gfpgan_1.4"
    elif req.quality == "high":
        current_config["face_enhancer_enabled"] = True
        current_config["face_enhancer_model"] = "gfpgan_1.4"

    # Resolution mapping
    res_map = {"480p": "640x480", "720p": "1280x720", "1080p": "1920x1080"}
    current_config["output_resolution"] = res_map.get(req.resolution, "1280x720")
    current_config["output_fps"] = req.fps

    try:
        # Step 1: ffmpeg RTMP input → v4l2 virtual webcam
        # (Requires v4l2loopback kernel module loaded)
        ffmpeg_in_cmd = [
            "ffmpeg", "-y",
            "-i", req.input_rtmp,
            "-f", "v4l2",
            "-pix_fmt", "yuv420p",
            "-s", current_config["output_resolution"],
            "-r", str(req.fps),
            "/dev/video10",
        ]
        logger.info(f"Starting ffmpeg input: {' '.join(ffmpeg_in_cmd)}")
        ffmpeg_in_proc = subprocess.Popen(
            ffmpeg_in_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=os.setsid,
        )

        # Wait a moment for ffmpeg to start
        await asyncio.sleep(2)

        # Step 2: FaceFusion webcam mode → UDP output
        ff_cmd = build_facefusion_webcam_cmd()
        logger.info(f"Starting FaceFusion: {' '.join(ff_cmd)}")
        ff_proc = subprocess.Popen(
            ff_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=FACEFUSION_DIR,
            preexec_fn=os.setsid,
        )

        # Wait for FaceFusion to initialize
        await asyncio.sleep(5)

        # Step 3: ffmpeg UDP input → RTMP output
        ffmpeg_out_cmd = [
            "ffmpeg", "-y",
            "-f", "mpegts",
            "-i", "udp://localhost:27000",
            "-c:v", "libx264",
            "-preset", "ultrafast" if req.quality == "fast" else "fast",
            "-tune", "zerolatency",
            "-b:v", "4000k" if req.resolution == "1080p" else "2500k",
            "-maxrate", "4500k" if req.resolution == "1080p" else "3000k",
            "-bufsize", "9000k" if req.resolution == "1080p" else "6000k",
            "-g", str(req.fps * 2),
            "-f", "flv",
            req.output_rtmp,
        ]
        logger.info(f"Starting ffmpeg output: {' '.join(ffmpeg_out_cmd)}")
        ffmpeg_out_proc = subprocess.Popen(
            ffmpeg_out_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=os.setsid,
        )

        current_session.update({
            "id": session_id,
            "status": "running",
            "facefusion_proc": ff_proc,
            "ffmpeg_in_proc": ffmpeg_in_proc,
            "ffmpeg_out_proc": ffmpeg_out_proc,
            "start_time": time.time(),
            "config": dict(current_config),
            "error": None,
        })

        logger.info(f"Stream started: session={session_id}")

        # Start audio processor for Liver Clone if voice_id is provided
        audio_status = None
        if req.voice_id:
            try:
                from audio_processor import AudioProcessor, AudioConfig, AudioMode
                mode_map = {"manual": AudioMode.MANUAL, "auto": AudioMode.AUTO, "hybrid": AudioMode.HYBRID}
                audio_config = AudioConfig(
                    voice_id=req.voice_id,
                    mode=mode_map.get(req.audio_mode, AudioMode.HYBRID),
                    vad_threshold=req.vad_threshold,
                    silence_timeout=req.silence_timeout,
                    voice_stability=req.voice_stability,
                    voice_similarity=req.voice_similarity,
                    language=req.language,
                    session_id=req.liver_clone_session_id or session_id,
                )
                global audio_processor
                audio_processor = AudioProcessor(audio_config)
                await audio_processor.start(req.input_rtmp, "")
                audio_status = audio_processor.get_status()
                logger.info(f"Audio processor started: mode={req.audio_mode}")
            except Exception as ae:
                logger.error(f"Audio processor failed to start (non-fatal): {ae}")
                audio_status = {"error": str(ae)}

        return {
            "session_id": session_id,
            "status": "running",
            "config": current_config,
            "pipeline": {
                "input": req.input_rtmp,
                "output": req.output_rtmp,
                "quality": req.quality,
                "resolution": req.resolution,
                "fps": req.fps,
                "face_enhancer": req.face_enhancer,
            },
            "audio": audio_status,
        }

    except Exception as e:
        current_session["status"] = "error"
        current_session["error"] = str(e)
        logger.error(f"Failed to start stream: {e}")
        # Cleanup any started processes
        for key in ("ffmpeg_in_proc", "facefusion_proc", "ffmpeg_out_proc"):
            if current_session.get(key):
                kill_process_tree(current_session[key])
                current_session[key] = None
        raise HTTPException(500, f"Failed to start stream: {e}")


@app.post("/api/stop-stream")
async def stop_stream(auth: bool = Depends(verify_api_key)):
    """Stop the running face swap stream."""
    global current_session

    if current_session["status"] not in ("running", "starting", "error"):
        return {"status": "already_stopped", "session_id": None}

    session_id = current_session["id"]
    uptime = 0
    if current_session["start_time"]:
        uptime = time.time() - current_session["start_time"]

    current_session["status"] = "stopping"
    logger.info(f"Stopping stream: session={session_id}")

    # Stop audio processor if running
    try:
        if 'audio_processor' in globals() and audio_processor:
            await audio_processor.stop()
            logger.info("Audio processor stopped")
    except Exception as ae:
        logger.warning(f"Error stopping audio processor: {ae}")

    # Kill all processes in reverse order
    for key in ("ffmpeg_out_proc", "facefusion_proc", "ffmpeg_in_proc"):
        if current_session.get(key):
            kill_process_tree(current_session[key])
            current_session[key] = None

    result = {
        "session_id": session_id,
        "status": "stopped",
        "uptime_seconds": round(uptime, 1),
    }

    # Reset state
    current_session.update({
        "id": None,
        "status": "idle",
        "facefusion_proc": None,
        "ffmpeg_in_proc": None,
        "ffmpeg_out_proc": None,
        "start_time": None,
        "config": {},
        "error": None,
    })

    logger.info(f"Stream stopped: session={session_id}, uptime={uptime:.1f}s")
    return result


@app.get("/api/stream-status")
async def stream_status(auth: bool = Depends(verify_api_key)):
    """Get current stream status and metrics."""
    uptime = 0
    if current_session["start_time"]:
        uptime = time.time() - current_session["start_time"]

    # Check if processes are still alive
    processes_alive = {}
    for key in ("facefusion_proc", "ffmpeg_in_proc", "ffmpeg_out_proc"):
        proc = current_session.get(key)
        if proc:
            poll = proc.poll()
            processes_alive[key.replace("_proc", "")] = poll is None
        else:
            processes_alive[key.replace("_proc", "")] = False

    # If stream should be running but processes died
    if current_session["status"] == "running" and not all(processes_alive.values()):
        dead = [k for k, v in processes_alive.items() if not v]
        current_session["status"] = "error"
        current_session["error"] = f"Process(es) died: {', '.join(dead)}"

    gpu_info = get_gpu_info()

    return {
        "session_id": current_session["id"],
        "status": current_session["status"],
        "uptime_seconds": round(uptime, 1),
        "processes": processes_alive,
        "gpu": gpu_info,
        "config": current_session.get("config", {}),
        "error": current_session.get("error"),
    }


@app.post("/api/swap-frame")
async def swap_frame(req: SwapFrameRequest, auth: bool = Depends(verify_api_key)):
    """
    Swap face on a single image (for testing/preview).
    Returns the processed image as base64.
    """
    if source_face_path is None or not Path(source_face_path).exists():
        raise HTTPException(400, "Source face not set. Call /api/set-source first.")

    # Save input image
    input_path = os.path.join(TEMP_DIR, f"input_{uuid.uuid4().hex[:8]}.jpg")
    output_path = os.path.join(TEMP_DIR, f"output_{uuid.uuid4().hex[:8]}.jpg")

    try:
        content = base64.b64decode(req.image_base64)
        with open(input_path, "wb") as f:
            f.write(content)

        # Apply quality settings temporarily
        original_enhancer = current_config["face_enhancer_enabled"]
        current_config["face_enhancer_enabled"] = req.face_enhancer

        # Run FaceFusion headless
        cmd = build_facefusion_headless_cmd(input_path, output_path)
        logger.info(f"Processing single frame: {' '.join(cmd)}")

        import os as _os
        _env = _os.environ.copy()
        _env["LD_LIBRARY_PATH"] = "/usr/lib/x86_64-linux-gnu:" + _env.get("LD_LIBRARY_PATH", "")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=90,
            cwd=FACEFUSION_DIR,
            env=_env,
        )

        # Restore config
        current_config["face_enhancer_enabled"] = original_enhancer

        if result.returncode != 0:
            logger.error(f"FaceFusion error: {result.stderr}")
            raise HTTPException(500, f"FaceFusion processing failed: {result.stderr[:500]}")

        if not Path(output_path).exists():
            raise HTTPException(500, "Output image not generated")

        # Read and encode output
        with open(output_path, "rb") as f:
            output_base64 = base64.b64encode(f.read()).decode()

        return {
            "status": "ok",
            "image_base64": output_base64,
            "quality": req.quality,
            "face_enhancer": req.face_enhancer,
        }

    finally:
        # Cleanup temp files
        for p in (input_path, output_path):
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass


@app.get("/api/config")
async def get_config(auth: bool = Depends(verify_api_key)):
    """Get current FaceFusion configuration."""
    return {
        "config": current_config,
        "quality_presets": {
            "fast": {
                "face_swapper_model": "hyperswap_1b_256",
                "face_swapper_pixel_boost": "512x512",
                "face_enhancer_enabled": False,
                "face_enhancer_model": None,
                "face_detector_model": "yolo_face",
                "face_detector_score": 0.5,
                "face_mask_blur": 0.3,
                "expression_restorer_enabled": False,
            },
            "standard": {
                "face_swapper_model": "hyperswap_1c_256",
                "face_swapper_pixel_boost": "1024x1024",
                "face_enhancer_enabled": False,
                "face_enhancer_model": None,
                "face_detector_model": "retinaface",
                "face_detector_score": 0.3,
                "face_mask_blur": 0.1,
                "expression_restorer_enabled": True,
            },
            "pro": {
                "face_swapper_model": "hyperswap_1c_256",
                "face_swapper_pixel_boost": "1024x1024",
                "face_enhancer_enabled": True,
                "face_enhancer_model": "codeformer",
                "face_detector_model": "retinaface",
                "face_detector_score": 0.3,
                "face_mask_blur": 0.1,
                "expression_restorer_enabled": True,
            },
            "cinema": {
                "face_swapper_model": "hyperswap_1c_256",
                "face_swapper_pixel_boost": "1024x1024",
                "face_enhancer_enabled": True,
                "face_enhancer_model": "gpen_bfr_512",
                "face_detector_model": "retinaface",
                "face_detector_score": 0.3,
                "face_mask_blur": 0.1,
                "expression_restorer_enabled": True,
            },
        },
        "available_models": {
            "face_swapper": [
                "inswapper_128",
                "inswapper_128_fp16",
                "hyperswap_1a_256",
                "hyperswap_1b_256",
                "hyperswap_1c_256",
                "simswap_256",
                "blendswap_256",
                "uniface_256",
            ],
            "face_swapper_pixel_boost": [
                "128x128", "256x256", "384x384",
                "512x512", "768x768", "1024x1024",
            ],
            "face_enhancer": [
                "gfpgan_1.4",
                "gpen_bfr_256",
                "gpen_bfr_512",
                "gpen_bfr_1024",
                "gpen_bfr_2048",
                "codeformer",
                "restoreformer_plus_plus",
            ],
            "face_detector": [
                "many",
                "retinaface",
                "scrfd",
                "yolo_face",
                "yunet",
            ],
        },
    }


@app.post("/api/config")
async def update_config(req: UpdateConfigRequest, auth: bool = Depends(verify_api_key)):
    """
    Update FaceFusion configuration.
    Changes take effect on next stream start.
    """
    updated = {}
    for field, value in req.model_dump(exclude_none=True).items():
        if field in current_config:
            current_config[field] = value
            updated[field] = value

    return {
        "status": "ok",
        "updated": updated,
        "config": current_config,
        "note": "Changes take effect on next stream start" if current_session["status"] == "running" else None,
    }


# ── Video Face Swap ─────────────────────────────────────────────────────────

def build_facefusion_video_cmd(input_path: str, output_path: str) -> list:
    """Build FaceFusion command for video face swap (headless-run)."""
    processors = ["face_swapper"]
    if current_config["face_enhancer_enabled"]:
        processors.append("face_enhancer")

    cmd = [
        sys.executable, f"{FACEFUSION_DIR}/facefusion.py", "headless-run",
        "--source-paths", source_face_path,
        "--target-path", input_path,
        "--output-path", output_path,
        # Processors
        "--processors", *processors,
        # Face swapper settings
        "--face-swapper-model", current_config["face_swapper_model"],
        "--face-swapper-pixel-boost", current_config["face_swapper_pixel_boost"],
        "--face-swapper-weight", str(current_config["face_swapper_weight"]),
        # Face detector settings
        "--face-detector-model", current_config["face_detector_model"],
        "--face-detector-score", str(current_config["face_detector_score"]),
        # Face mask settings
        "--face-mask-types", *current_config["face_mask_types"],
        "--face-mask-blur", str(current_config["face_mask_blur"]),
        "--face-mask-padding", *[str(p) for p in current_config["face_mask_padding"]],
        # Output settings
        "--output-video-quality", str(current_config.get("output_video_quality", 90)),
        # Execution settings
        "--execution-providers", current_config["execution_providers"],
        "--execution-thread-count", str(current_config["execution_thread_count"]),
    ]

    if current_config["face_enhancer_enabled"]:
        cmd.extend(["--face-enhancer-model", current_config["face_enhancer_model"]])

    return cmd


def _run_video_job(job_id: str, video_url: str, face_enhancer: bool,
                   quality: str, output_video_quality: int):
    """Background thread: download video, run FaceFusion, update job state."""
    import threading
    import httpx as _httpx

    job = video_jobs[job_id]
    input_path = os.path.join(TEMP_DIR, f"vid_in_{job_id}.mp4")
    output_path = os.path.join(TEMP_DIR, f"vid_out_{job_id}.mp4")

    try:
        # --- Step 1: Download video ---
        job["status"] = "downloading"
        job["step"] = "Downloading input video"
        logger.info(f"[{job_id}] Downloading video from {video_url[:80]}...")

        with _httpx.Client(timeout=300, follow_redirects=True) as client:
            with client.stream("GET", video_url) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0))
                downloaded = 0
                with open(input_path, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=1024 * 256):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            job["progress"] = min(20, int(downloaded / total * 20))

        file_size_mb = os.path.getsize(input_path) / (1024 * 1024)
        logger.info(f"[{job_id}] Downloaded: {file_size_mb:.1f} MB")

        # --- Step 2: Get video duration for progress estimation ---
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", input_path],
                capture_output=True, text=True, timeout=30,
            )
            duration_sec = float(probe.stdout.strip()) if probe.returncode == 0 else 0
        except Exception:
            duration_sec = 0
        job["duration_sec"] = duration_sec

        # --- Step 3: Apply quality settings ---
        original_enhancer = current_config["face_enhancer_enabled"]
        original_enhancer_model = current_config.get("face_enhancer_model", "gfpgan_1.4")
        original_pixel_boost = current_config["face_swapper_pixel_boost"]
        original_swapper_model = current_config["face_swapper_model"]
        original_detector = current_config["face_detector_model"]
        original_detector_score = current_config["face_detector_score"]
        original_mask_blur = current_config["face_mask_blur"]

        if quality == "fast":
            current_config["face_enhancer_enabled"] = False
            current_config["face_swapper_model"] = "hyperswap_1b_256"
            current_config["face_swapper_pixel_boost"] = "512x512"
            current_config["face_detector_model"] = "yolo_face"
            current_config["face_detector_score"] = 0.5
            current_config["face_mask_blur"] = 0.3
        elif quality == "pro":
            current_config["face_enhancer_enabled"] = True
            current_config["face_enhancer_model"] = "codeformer"
            current_config["face_swapper_model"] = "hyperswap_1c_256"
            current_config["face_swapper_pixel_boost"] = "1024x1024"
            current_config["face_detector_model"] = "retinaface"
            current_config["face_detector_score"] = 0.3
            current_config["face_mask_blur"] = 0.1
        elif quality == "cinema":
            current_config["face_enhancer_enabled"] = True
            current_config["face_enhancer_model"] = "gpen_bfr_512"
            current_config["face_swapper_model"] = "hyperswap_1c_256"
            current_config["face_swapper_pixel_boost"] = "1024x1024"
            current_config["face_detector_model"] = "retinaface"
            current_config["face_detector_score"] = 0.3
            current_config["face_mask_blur"] = 0.1
        else:
            # standard / high / balanced — use face_enhancer param as-is
            current_config["face_enhancer_enabled"] = face_enhancer

        current_config["output_video_quality"] = output_video_quality
        logger.info(f"[{job_id}] Quality preset: {quality}, "
                    f"enhancer={current_config['face_enhancer_enabled']}, "
                    f"enhancer_model={current_config.get('face_enhancer_model')}")

        # --- Step 4: Run FaceFusion ---
        job["status"] = "processing"
        job["step"] = "Face swapping video frames"
        job["progress"] = 20

        cmd = build_facefusion_video_cmd(input_path, output_path)
        logger.info(f"[{job_id}] Running FaceFusion: {' '.join(cmd[:6])}...")

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=FACEFUSION_DIR,
        )
        job["pid"] = proc.pid

        # Parse FaceFusion output for progress
        for line in iter(proc.stdout.readline, ""):
            line = line.strip()
            if not line:
                continue
            # FaceFusion prints progress like: "Processing: 50%" or frame counts
            if "%" in line:
                try:
                    pct_str = line.split("%")[0].split()[-1]
                    pct = float(pct_str)
                    # Map 0-100% of FaceFusion to 20-90% of overall progress
                    job["progress"] = 20 + int(pct * 0.7)
                except (ValueError, IndexError):
                    pass
            logger.debug(f"[{job_id}] FF: {line[:120]}")

        proc.wait()
        # Restore all config to original values
        current_config["face_enhancer_enabled"] = original_enhancer
        current_config["face_enhancer_model"] = original_enhancer_model
        current_config["face_swapper_pixel_boost"] = original_pixel_boost
        current_config["face_swapper_model"] = original_swapper_model
        current_config["face_detector_model"] = original_detector
        current_config["face_detector_score"] = original_detector_score
        current_config["face_mask_blur"] = original_mask_blur

        if proc.returncode != 0:
            raise RuntimeError(f"FaceFusion exited with code {proc.returncode}")

        if not Path(output_path).exists():
            raise RuntimeError("FaceFusion did not produce output video")

        output_size_mb = os.path.getsize(output_path) / (1024 * 1024)
        logger.info(f"[{job_id}] Face swap complete: {output_size_mb:.1f} MB")

        # --- Step 5: Done ---
        job.update({
            "status": "completed",
            "step": "Face swap completed",
            "progress": 100,
            "output_path": output_path,
            "output_size_mb": round(output_size_mb, 1),
            "completed_at": time.time(),
        })

    except Exception as e:
        logger.error(f"[{job_id}] Video job failed: {e}")
        job.update({
            "status": "error",
            "step": "Error",
            "error": str(e),
        })
    finally:
        # Cleanup input file (keep output for download)
        try:
            os.unlink(input_path)
        except FileNotFoundError:
            pass
        job["pid"] = None


@app.post("/api/swap-video")
async def swap_video(req: SwapVideoRequest, auth: bool = Depends(verify_api_key)):
    """
    Start an async video face swap job.

    The video is downloaded from the provided URL, processed frame-by-frame
    with FaceFusion, and the result is made available for download.

    Returns immediately with job_id; poll /api/video-status/{job_id} for progress.
    """
    if source_face_path is None or not Path(source_face_path).exists():
        raise HTTPException(400, "Source face not set. Call /api/set-source first.")

    if req.job_id in video_jobs:
        existing = video_jobs[req.job_id]
        if existing["status"] in ("downloading", "processing"):
            raise HTTPException(409, f"Job {req.job_id} already in progress")

    # Initialize job
    video_jobs[req.job_id] = {
        "status": "queued",
        "step": "Queued",
        "progress": 0,
        "error": None,
        "output_path": None,
        "output_size_mb": 0,
        "duration_sec": 0,
        "pid": None,
        "created_at": time.time(),
        "completed_at": None,
    }

    # Start background thread
    import threading
    t = threading.Thread(
        target=_run_video_job,
        args=(req.job_id, req.video_url, req.face_enhancer,
              req.quality, req.output_video_quality),
        daemon=True,
    )
    t.start()

    return {
        "status": "accepted",
        "job_id": req.job_id,
        "poll_url": f"/api/video-status/{req.job_id}",
    }


@app.get("/api/video-status/{job_id}")
async def video_status(job_id: str, auth: bool = Depends(verify_api_key)):
    """Get the status and progress of a video face swap job."""
    if job_id not in video_jobs:
        raise HTTPException(404, f"Job {job_id} not found")

    job = video_jobs[job_id]
    elapsed = 0
    if job.get("created_at"):
        elapsed = time.time() - job["created_at"]

    return {
        "job_id": job_id,
        "status": job["status"],
        "step": job["step"],
        "progress": job["progress"],
        "duration_sec": job.get("duration_sec", 0),
        "elapsed_sec": round(elapsed, 1),
        "output_size_mb": job.get("output_size_mb", 0),
        "error": job.get("error"),
    }


@app.get("/api/video-download/{job_id}")
async def video_download(job_id: str, auth: bool = Depends(verify_api_key)):
    """
    Download the processed video.
    Returns the video file as a streaming response.
    """
    from fastapi.responses import FileResponse

    if job_id not in video_jobs:
        raise HTTPException(404, f"Job {job_id} not found")

    job = video_jobs[job_id]
    if job["status"] != "completed":
        raise HTTPException(400, f"Job not completed (status: {job['status']})")

    output_path = job.get("output_path")
    if not output_path or not Path(output_path).exists():
        raise HTTPException(404, "Output file not found")

    return FileResponse(
        path=output_path,
        media_type="video/mp4",
        filename=f"face_swap_{job_id}.mp4",
    )


@app.delete("/api/video-job/{job_id}")
async def delete_video_job(job_id: str, auth: bool = Depends(verify_api_key)):
    """Delete a video job and its output file."""
    if job_id not in video_jobs:
        raise HTTPException(404, f"Job {job_id} not found")

    job = video_jobs[job_id]

    # Kill running process if any
    if job.get("pid"):
        try:
            os.kill(job["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass

    # Delete output file
    if job.get("output_path"):
        try:
            os.unlink(job["output_path"])
        except FileNotFoundError:
            pass

    del video_jobs[job_id]
    return {"status": "deleted", "job_id": job_id}


# ── Audio Processor Endpoints ─────────────────────────────────────────────────

audio_processor: Optional[object] = None  # Will hold AudioProcessor instance


@app.get("/api/audio-status")
async def get_audio_status(auth: bool = Depends(verify_api_key)):
    """Get audio processor status (VAD, auto-pilot, etc.)."""
    if audio_processor is None:
        return {"status": "not_running", "message": "Audio processor not initialized"}
    return audio_processor.get_status()


@app.post("/api/audio-inject")
async def inject_audio_text(text: str, auth: bool = Depends(verify_api_key)):
    """Manually inject TTS text into the audio stream."""
    if audio_processor is None:
        raise HTTPException(400, "Audio processor not running")
    await audio_processor._generate_and_inject_tts(text)
    return {"status": "injected", "text": text}


# ── WebSocket Preview Endpoint ────────────────────────────────────────────────

@app.websocket("/api/preview-stream")
async def preview_stream(websocket: WebSocket, api_key: str = Query(...)):
    """
    WebSocket endpoint for real-time face swap preview.
    
    Protocol:
      1. Client connects with ?api_key=xxx
      2. Client sends JPEG frames as binary messages
      3. Server processes each frame through InsightFace in-memory engine
      4. Server returns processed JPEG frame as binary message
    
    Uses InsightFace in-memory engine (50-200ms/frame) instead of
    FaceFusion CLI subprocess (14-35s/frame).
    """
    # Verify API key
    if api_key != WORKER_API_KEY:
        await websocket.close(code=4001, reason="Invalid API key")
        return
    
    await websocket.accept()
    logger.info("[Preview] WebSocket client connected")
    
    frame_count = 0
    error_count = 0
    MAX_ERRORS = 10
    
    # Check if InsightFace engine is available
    use_insightface = (face_analyzer is not None and 
                       face_swapper_model is not None and 
                       source_face_embedding is not None)
    
    if use_insightface:
        logger.info("[Preview] Using InsightFace in-memory engine (fast mode)")
    else:
        logger.warning("[Preview] InsightFace engine not ready, using FaceFusion CLI fallback")
    
    try:
        import cv2
        import numpy as np
        
        while True:
            # Receive frame from client (binary JPEG)
            data = await websocket.receive_bytes()
            frame_count += 1
            
            if source_face_embedding is None and (source_face_path is None or not Path(source_face_path).exists()):
                # No source face set - return original frame with warning
                await websocket.send_json({
                    "type": "error",
                    "message": "Source face not set. Upload a face image first."
                })
                continue
            
            try:
                start_time = time.time()
                
                if use_insightface and source_face_embedding is not None:
                    # ── InsightFace In-Memory Processing (50-200ms) ──────────
                    # Decode JPEG to numpy array
                    nparr = np.frombuffer(data, np.uint8)
                    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    
                    if frame is None:
                        error_count += 1
                        await websocket.send_bytes(data)
                        continue
                    
                    # Detect faces in target frame
                    target_faces = face_analyzer.get(frame)
                    
                    if target_faces:
                        # Swap all detected faces with source face
                        result = frame.copy()
                        for target_face in target_faces:
                            result = face_swapper_model.get(
                                result, target_face, source_face_embedding, paste_back=True
                            )
                        
                        # ── HD Enhancement Pipeline ──────────────────────
                        # 1. Color correction: match swapped face color to target skin
                        result = _color_correct_face(frame, result, target_faces)
                        
                        # 2. GFPGAN face restoration (if available)
                        if face_enhancer_net is not None:
                            try:
                                _, _, enhanced = face_enhancer_net.enhance(
                                    result, has_aligned=False,
                                    only_center_face=False, paste_back=True
                                )
                                if enhanced is not None:
                                    result = enhanced
                            except Exception:
                                pass  # Skip enhancement on error
                        
                        # 3. Seamless blend at face boundary
                        result = _seamless_blend(frame, result, target_faces)
                        
                        # Encode result back to JPEG (higher quality for HD)
                        _, encoded = cv2.imencode('.jpg', result, 
                                                  [cv2.IMWRITE_JPEG_QUALITY, 92])
                        processed = encoded.tobytes()
                    else:
                        # No faces detected in frame - return original
                        processed = data
                    
                    elapsed_ms = int((time.time() - start_time) * 1000)
                    if frame_count % 30 == 0:  # Log every 30 frames
                        logger.info(f"[Preview] Frame {frame_count}: {elapsed_ms}ms "
                                    f"({len(target_faces)} faces)")
                    
                    await websocket.send_bytes(processed)
                    error_count = 0
                    
                else:
                    # ── FaceFusion CLI Fallback (14-35s) ─────────────────────
                    input_path = os.path.join(TEMP_DIR, f"preview_in_{frame_count % 10}.jpg")
                    output_path = os.path.join(TEMP_DIR, f"preview_out_{frame_count % 10}.jpg")
                    
                    with open(input_path, "wb") as f:
                        f.write(data)
                    
                    cmd = build_facefusion_headless_cmd(input_path, output_path)
                    _env = os.environ.copy()
                    _env["LD_LIBRARY_PATH"] = "/usr/lib/x86_64-linux-gnu:" + _env.get("LD_LIBRARY_PATH", "")
                    result = subprocess.run(
                        cmd, capture_output=True, text=True,
                        timeout=90, cwd=FACEFUSION_DIR, env=_env,
                    )
                    
                    if result.returncode == 0 and Path(output_path).exists():
                        with open(output_path, "rb") as f:
                            processed = f.read()
                        await websocket.send_bytes(processed)
                        error_count = 0
                    else:
                        error_count += 1
                        await websocket.send_bytes(data)
                    
                    # Cleanup temp files
                    for p in (input_path, output_path):
                        try:
                            os.unlink(p)
                        except (FileNotFoundError, OSError):
                            pass
                
            except subprocess.TimeoutExpired:
                error_count += 1
                await websocket.send_bytes(data)
            except Exception as e:
                error_count += 1
                logger.error(f"[Preview] Frame processing error: {e}")
                await websocket.send_bytes(data)
            
            if error_count >= MAX_ERRORS:
                await websocket.send_json({
                    "type": "error",
                    "message": "Too many processing errors. Check GPU Worker logs."
                })
                break
    
    except WebSocketDisconnect:
        logger.info(f"[Preview] Client disconnected after {frame_count} frames")
    except Exception as e:
        logger.error(f"[Preview] WebSocket error: {e}")
    finally:
        logger.info(f"[Preview] Session ended. Frames processed: {frame_count}")


@app.post("/api/preview-frame")
async def preview_frame(req: SwapFrameRequest, auth: bool = Depends(verify_api_key)):
    """
    Process a single frame for preview (HTTP fallback for WebSocket).
    Uses InsightFace in-memory engine when available (50-200ms),
    falls back to FaceFusion CLI (14-35s) otherwise.
    Returns processed image as base64.
    """
    if source_face_path is None or not Path(source_face_path).exists():
        raise HTTPException(400, "Source face not set. Call /api/set-source first.")
    
    content = base64.b64decode(req.image_base64)
    start_time = time.time()
    engine_used = "unknown"
    
    # Try InsightFace in-memory engine first
    if (face_analyzer is not None and face_swapper_model is not None 
            and source_face_embedding is not None):
        try:
            import cv2
            import numpy as np
            
            nparr = np.frombuffer(content, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            if frame is None:
                raise ValueError("Failed to decode image")
            
            target_faces = face_analyzer.get(frame)
            
            if target_faces:
                result = frame.copy()
                for target_face in target_faces:
                    result = face_swapper_model.get(
                        result, target_face, source_face_embedding, paste_back=True
                    )
                
                # HD Enhancement Pipeline
                result = _color_correct_face(frame, result, target_faces)
                if face_enhancer_net is not None:
                    try:
                        _, _, enhanced = face_enhancer_net.enhance(
                            result, has_aligned=False,
                            only_center_face=False, paste_back=True
                        )
                        if enhanced is not None:
                            result = enhanced
                    except Exception:
                        pass
                result = _seamless_blend(frame, result, target_faces)
                
                _, encoded = cv2.imencode('.jpg', result,
                                          [cv2.IMWRITE_JPEG_QUALITY, 95])
                output_base64 = base64.b64encode(encoded.tobytes()).decode()
            else:
                # No faces detected - return original
                output_base64 = req.image_base64
            
            elapsed_ms = int((time.time() - start_time) * 1000)
            engine_used = "insightface"
            logger.info(f"[Preview] Frame processed in {elapsed_ms}ms "
                        f"(InsightFace, {len(target_faces)} faces)")
            
            return {
                "status": "ok",
                "image_base64": output_base64,
                "processing_time_ms": elapsed_ms,
                "engine": engine_used,
                "faces_detected": len(target_faces),
            }
        except Exception as e:
            logger.warning(f"[Preview] InsightFace failed, falling back to CLI: {e}")
    
    # Fallback to FaceFusion CLI
    input_path = os.path.join(TEMP_DIR, f"preview_{uuid.uuid4().hex[:8]}.jpg")
    output_path = os.path.join(TEMP_DIR, f"preview_out_{uuid.uuid4().hex[:8]}.jpg")
    
    try:
        with open(input_path, "wb") as f:
            f.write(content)
        
        original_enhancer = current_config["face_enhancer_enabled"]
        current_config["face_enhancer_enabled"] = req.face_enhancer
        
        cmd = build_facefusion_headless_cmd(input_path, output_path)
        logger.info(f"[Preview] Processing frame via CLI: {' '.join(cmd[:6])}...")
        
        _env = os.environ.copy()
        _env["LD_LIBRARY_PATH"] = "/usr/lib/x86_64-linux-gnu:" + _env.get("LD_LIBRARY_PATH", "")
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=90, cwd=FACEFUSION_DIR, env=_env,
        )
        elapsed_ms = int((time.time() - start_time) * 1000)
        
        current_config["face_enhancer_enabled"] = original_enhancer
        
        if result.returncode != 0:
            logger.error(f"[Preview] FaceFusion error: {result.stderr[:300]}")
            raise HTTPException(500, f"FaceFusion error: {result.stderr[:300]}")
        if not Path(output_path).exists():
            raise HTTPException(500, "Output not generated")
        
        with open(output_path, "rb") as f:
            output_base64 = base64.b64encode(f.read()).decode()
        
        engine_used = "facefusion_cli"
        logger.info(f"[Preview] Frame processed in {elapsed_ms}ms (FaceFusion CLI)")
        return {
            "status": "ok",
            "image_base64": output_base64,
            "processing_time_ms": elapsed_ms,
            "engine": engine_used,
        }
    finally:
        for p in (input_path, output_path):
            try:
                os.unlink(p)
            except (FileNotFoundError, OSError):
                pass


# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info(f"Starting FaceFusion GPU Worker on port {PORT}")
    uvicorn.run(
        "face_swap_worker_api:app",
        host="0.0.0.0",
        port=PORT,
        workers=1,
        log_level="info",
    )
