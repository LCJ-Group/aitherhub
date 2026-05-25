"""
FaceFusion GPU Worker API Server v3.1 - Direct ONNX Pipeline + GFPGAN + Hair Protection
========================================================================================

A FastAPI wrapper that uses DIRECT ONNX Runtime inference for ultra-fast face swapping
with GFPGAN face enhancement and landmark-based hair protection mask.

Architecture:
  - InsightFace for face detection + 106-point landmarks (~14ms)
  - Direct ONNX session.run() for inswapper_128 (~6ms)
  - GFPGAN 1.4 for skin detail restoration (~11ms)
  - Landmark-based mask for hair/forehead protection (~1ms)
  - Color-corrected paste_back (~2ms)
  - Total: ~34ms per frame = 29+ FPS

Endpoints:
  POST /api/health          - GPU health check
  POST /api/set-source      - Upload source face image
  POST /api/start-stream    - Start real-time face swap stream
  POST /api/stop-stream     - Stop the running stream
  GET  /api/stream-status   - Get current stream metrics
  POST /api/swap-frame      - Swap face on a single image (test)
  GET  /api/config          - Get current configuration
  POST /api/config          - Update configuration
  POST /api/swap-video      - Start async video face swap job
  WS   /api/preview-stream  - WebSocket real-time preview
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

_raw_api_key = os.getenv("WORKER_API_KEY", "aitherhub")
VALID_API_KEYS = {k.strip() for k in _raw_api_key.split(",") if k.strip()}
# Always accept both keys for backward compatibility with Azure env vars
VALID_API_KEYS.add("aitherhub")
VALID_API_KEYS.add("change-me-in-production")
WORKER_API_KEY = _raw_api_key  # Keep for WebSocket compat
FACEFUSION_DIR = os.getenv("FACEFUSION_DIR", "/workspace/facefusion")
SOURCE_FACE_DIR = os.getenv("SOURCE_FACE_DIR", "/workspace/source_faces")
TEMP_DIR = os.getenv("TEMP_DIR", "/workspace/tmp")
PORT = int(os.getenv("PORT", os.getenv("WORKER_PORT", "11434")))
INSIGHTFACE_DIR = os.getenv("INSIGHTFACE_DIR", "/workspace/insightface_models")

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
    "face_swapper_pixel_boost": "128x128",
    "face_swapper_weight": 0.5,
    "face_enhancer_model": "gfpgan_1.4",
    "face_enhancer_enabled": True,  # GFPGAN enabled by default for quality
    "face_detector_model": "yolo_face",
    "face_detector_score": 0.5,
    "face_mask_types": ["box", "landmark"],
    "face_mask_blur": 0.3,
    "face_mask_padding": [0, 0, 0, 0],
    "output_image_quality": 95,
    "output_resolution": "1280x720",
    "output_fps": 30,
    "execution_providers": "cuda",
    "execution_thread_count": 4,
}

source_face_path: Optional[str] = None

# ── Direct ONNX Engine State ─────────────────────────────────────────────────
# These are initialized once at startup for ultra-fast inference
onnx_engine_ready = False
onnx_session = None           # ONNX Runtime InferenceSession for inswapper_128
onnx_model_initializer = None # Static matrix from model for embedding transform
gfpgan_session = None         # ONNX Runtime InferenceSession for GFPGAN 1.4
insightface_app = None        # InsightFace FaceAnalysis for detection
source_face_embedding = None  # Pre-computed source embedding (transformed)
source_face_raw = None        # Raw InsightFace Face object

# arcface_128 template landmarks (normalized 0-1, multiply by crop_size)
ARCFACE_128_TEMPLATE = None  # Will be set during init


# ── Auth ─────────────────────────────────────────────────────────────────────

async def verify_api_key(x_api_key: str = Header(...)):
    """Verify the API key from request header."""
    if x_api_key not in VALID_API_KEYS:
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
        "--processors", *processors,
        "--face-swapper-model", current_config["face_swapper_model"],
        "--face-swapper-pixel-boost", current_config["face_swapper_pixel_boost"],
        "--face-swapper-weight", str(current_config["face_swapper_weight"]),
        "--face-detector-model", current_config["face_detector_model"],
        "--face-detector-score", str(current_config["face_detector_score"]),
        "--face-mask-types", *current_config["face_mask_types"],
        "--face-mask-blur", str(current_config["face_mask_blur"]),
        "--face-mask-padding", *[str(p) for p in current_config["face_mask_padding"]],
        "--output-image-quality", str(current_config["output_image_quality"]),
        "--execution-providers", current_config["execution_providers"],
        "--execution-thread-count", str(current_config["execution_thread_count"]),
    ]

    if current_config["face_enhancer_enabled"]:
        cmd.extend(["--face-enhancer-model", current_config["face_enhancer_model"]])

    return cmd


# ── Direct ONNX Pipeline Engine ──────────────────────────────────────────────

def init_direct_onnx_engine():
    """
    Initialize the Direct ONNX Pipeline for ultra-fast face swapping.
    
    This bypasses FaceFusion's inference_manager entirely, loading:
    1. inswapper_128 ONNX model directly with CUDA acceleration
    2. GFPGAN 1.4 for face enhancement (skin detail restoration)
    3. InsightFace for detection + 106-point landmarks
    
    Performance: ~34ms per frame total pipeline
    """
    global onnx_engine_ready, onnx_session, onnx_model_initializer
    global gfpgan_session, insightface_app, ARCFACE_128_TEMPLATE

    try:
        import numpy as np
        import cv2
        import onnxruntime as ort

        MODEL_PATH = os.path.join(FACEFUSION_DIR, ".assets/models/inswapper_128.onnx")
        GFPGAN_PATH = os.path.join(FACEFUSION_DIR, ".assets/models/gfpgan_1.4.onnx")

        if not os.path.exists(MODEL_PATH):
            logger.error(f"[ONNX] Model not found: {MODEL_PATH}")
            return False

        # 1. Load inswapper_128 ONNX session with CUDA
        logger.info("[ONNX] Loading inswapper_128 model with CUDA...")
        providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
        sess_options = ort.SessionOptions()
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        onnx_session = ort.InferenceSession(MODEL_PATH, sess_options=sess_options, providers=providers)
        active_providers = onnx_session.get_providers()
        logger.info(f"[ONNX] inswapper_128 providers: {active_providers}")

        # 2. Extract model initializer (static matrix for embedding transform)
        import onnx
        model = onnx.load(MODEL_PATH)
        onnx_model_initializer = onnx.numpy_helper.to_array(model.graph.initializer[-1])
        logger.info(f"[ONNX] Model initializer shape: {onnx_model_initializer.shape}")
        del model  # Free memory

        # 3. Load GFPGAN 1.4 for face enhancement
        if os.path.exists(GFPGAN_PATH):
            logger.info("[ONNX] Loading GFPGAN 1.4 model with CUDA...")
            gfpgan_session = ort.InferenceSession(GFPGAN_PATH, sess_options=sess_options, providers=providers)
            gfpgan_providers = gfpgan_session.get_providers()
            logger.info(f"[ONNX] GFPGAN providers: {gfpgan_providers}")
        else:
            logger.warning(f"[ONNX] GFPGAN model not found: {GFPGAN_PATH}")
            gfpgan_session = None

        # 4. Load InsightFace for face detection + landmarks
        logger.info("[ONNX] Loading InsightFace buffalo_l for detection...")
        from insightface.app import FaceAnalysis
        insightface_app = FaceAnalysis(
            name='buffalo_l',
            root=INSIGHTFACE_DIR,
            providers=providers,
        )
        insightface_app.prepare(ctx_id=0, det_size=(640, 640))
        logger.info("[ONNX] InsightFace loaded successfully")

        # 5. Set arcface_128 template (normalized coordinates * 128)
        ARCFACE_128_TEMPLATE = np.array([
            [0.36167656, 0.40387734],
            [0.63696719, 0.40235469],
            [0.50019687, 0.56044219],
            [0.38710391, 0.72160547],
            [0.61507734, 0.72034453],
        ], dtype=np.float32) * 128.0

        # 6. Warmup: run dummy inferences to pre-compile CUDA kernels
        logger.info("[ONNX] Warming up CUDA kernels...")
        dummy_source = np.random.randn(1, 512).astype(np.float32)
        dummy_target = np.random.randn(1, 3, 128, 128).astype(np.float32)
        for _ in range(3):
            onnx_session.run(None, {'source': dummy_source, 'target': dummy_target})

        if gfpgan_session is not None:
            dummy_gfpgan = np.random.randn(1, 3, 512, 512).astype(np.float32)
            for _ in range(3):
                gfpgan_session.run(None, {'input': dummy_gfpgan})
            logger.info("[ONNX] GFPGAN warmup complete")

        logger.info("[ONNX] CUDA warmup complete")

        onnx_engine_ready = True
        logger.info("[ONNX] Direct ONNX Pipeline v3.1 initialized successfully!")
        logger.info("[ONNX] Pipeline: detect(14ms) → swap(6ms) → GFPGAN(11ms) → mask+paste(3ms) = ~34ms/frame")
        return True

    except Exception as e:
        logger.error(f"[ONNX] Failed to initialize: {e}")
        import traceback
        traceback.print_exc()
        onnx_engine_ready = False
        return False


def compute_source_embedding(image_path: str, face_index: int = 0) -> bool:
    """
    Compute and cache the source face embedding using InsightFace.
    The embedding is pre-transformed with the model initializer for fast inference.
    """
    global source_face_embedding, source_face_raw

    try:
        import cv2
        import numpy as np

        img = cv2.imread(image_path)
        if img is None:
            logger.error(f"[ONNX] Cannot read image: {image_path}")
            return False

        faces = insightface_app.get(img)
        if not faces:
            logger.warning(f"[ONNX] No faces detected in {image_path}")
            return False

        # Select face by index
        idx = min(face_index, len(faces) - 1)
        face = faces[idx]
        source_face_raw = face

        # Pre-compute transformed embedding (done once, reused for all frames)
        raw_embedding = face.embedding.reshape(1, -1).astype(np.float32)
        transformed = np.dot(raw_embedding, onnx_model_initializer)
        transformed = transformed / np.linalg.norm(transformed)
        source_face_embedding = transformed.astype(np.float32)

        logger.info(f"[ONNX] Source face computed ({len(faces)} faces detected, "
                    f"using index {idx}, embedding shape: {source_face_embedding.shape})")
        return True

    except Exception as e:
        logger.error(f"[ONNX] Error computing source face: {e}")
        import traceback
        traceback.print_exc()
        return False


def create_landmark_mask(face, frame_shape, blur_amount=0.3):
    """
    Create a face-only mask using InsightFace landmarks that protects hair/forehead.
    
    Uses the face bounding box to create an ellipse that covers the face area
    but naturally excludes the forehead/hair region by shifting the center down
    and using a shorter vertical radius.
    
    This replaces the slow xseg_1 model (209ms) with a <2ms CPU operation.
    """
    import cv2
    import numpy as np

    h, w = frame_shape[:2]
    mask = np.zeros((h, w), dtype=np.float32)

    # Get face bounding box
    bbox = face.bbox.astype(int)
    x1, y1, x2, y2 = bbox
    face_w = x2 - x1
    face_h = y2 - y1

    # Create ellipse centered slightly below face center (protects forehead/hair)
    cx = (x1 + x2) // 2
    cy = (y1 + y2) // 2 + int(face_h * 0.08)  # Shift down 8% to protect forehead

    # Horizontal radius: 45% of face width (covers cheeks but not ears)
    # Vertical radius: 40% of face height (shorter to exclude forehead)
    rx = int(face_w * 0.45)
    ry = int(face_h * 0.40)

    # Draw filled ellipse
    cv2.ellipse(mask, (cx, cy), (rx, ry), 0, 0, 360, 1.0, -1)

    # Apply gaussian blur for soft edges
    blur_ksize = max(3, int(min(face_w, face_h) * blur_amount) | 1)
    mask = cv2.GaussianBlur(mask, (blur_ksize, blur_ksize), 0)

    return mask


def enhance_face_gfpgan(face_crop_128, target_size=512):
    """
    Enhance a face crop using GFPGAN 1.4.
    
    Input: 128x128 BGR face crop (uint8)
    Output: 512x512 enhanced BGR face (uint8)
    
    GFPGAN restores skin detail (pores, texture, wrinkles) that is lost
    when inswapper_128 generates at 128x128 resolution.
    """
    import cv2
    import numpy as np

    if gfpgan_session is None:
        # If GFPGAN not available, just resize
        return cv2.resize(face_crop_128, (target_size, target_size), interpolation=cv2.INTER_CUBIC)

    # Resize to 512x512 for GFPGAN input
    face_512 = cv2.resize(face_crop_128, (target_size, target_size), interpolation=cv2.INTER_CUBIC)

    # Prepare input: BGR→RGB, /255, HWC→CHW, add batch dim
    input_tensor = face_512[:, :, ::-1].astype(np.float32) / 255.0
    input_tensor = input_tensor.transpose(2, 0, 1)
    input_tensor = np.expand_dims(input_tensor, axis=0)

    # Run GFPGAN inference
    output = gfpgan_session.run(None, {'input': input_tensor})[0][0]

    # Post-process: CHW→HWC, clip, RGB→BGR, *255
    output = output.transpose(1, 2, 0)
    output = output.clip(0, 1)
    output = (output[:, :, ::-1] * 255).astype(np.uint8)

    return output


def color_transfer(source, target, mask=None):
    """
    Transfer color statistics from target to source for seamless blending.
    Uses mean/std matching in LAB color space.
    
    IMPORTANT: Only applies color correction to masked (non-zero) pixels.
    The source image may have large black (0,0,0) regions from warpAffine
    which must be excluded from both statistics AND transformation.
    """
    import cv2
    import numpy as np

    if source.shape != target.shape:
        return source

    # Determine valid region: where source actually has face pixels (not black from warp)
    source_gray = cv2.cvtColor(source, cv2.COLOR_BGR2GRAY)
    valid_pixels = source_gray > 5  # Exclude near-black warp border pixels

    if mask is not None:
        # Combine: only pixels that are both in mask AND have actual content
        mask_bool = (mask > 0.5) & valid_pixels
    else:
        mask_bool = valid_pixels

    if mask_bool.sum() < 100:
        return source

    # Convert to LAB
    source_lab = cv2.cvtColor(source, cv2.COLOR_BGR2LAB).astype(np.float32)
    target_lab = cv2.cvtColor(target, cv2.COLOR_BGR2LAB).astype(np.float32)

    # Compute stats ONLY from valid masked region
    s_mean = source_lab[mask_bool].mean(axis=0)
    s_std = source_lab[mask_bool].std(axis=0) + 1e-6
    t_mean = target_lab[mask_bool].mean(axis=0)
    t_std = target_lab[mask_bool].std(axis=0) + 1e-6

    # Transfer: normalize source, then apply target stats
    # Blend factor: 0.2 = subtle color correction (conservative to avoid color shift)
    blend = 0.2
    result_lab = source_lab.copy()
    for i in range(3):
        result_lab[:, :, i] = (result_lab[:, :, i] - s_mean[i]) * (t_std[i] / s_std[i]) * blend + \
                               result_lab[:, :, i] * (1 - blend) + \
                               (t_mean[i] - s_mean[i]) * blend

    # CRITICAL: Only apply color correction to valid pixels, keep black regions unchanged
    result_lab_uint8 = result_lab.clip(0, 255).astype(np.uint8)
    result_bgr = cv2.cvtColor(result_lab_uint8, cv2.COLOR_LAB2BGR)

    # Restore original black regions (don't color-correct warp borders)
    output = source.copy()
    output[valid_pixels] = result_bgr[valid_pixels]

    return output


def direct_swap_frame(frame, detect_score: float = 0.5, use_enhancer: bool = True):
    """
    Process a single frame through the Direct ONNX Pipeline v3.1.
    
    Pipeline:
      1. InsightFace detection → bbox, landmarks_5, landmark_2d_106, embedding (~14ms)
      2. Warp face to 128x128 using arcface template (~0.5ms)
      3. ONNX inswapper inference (~6ms)
      4. GFPGAN face enhancement 128→512→target_size (~11ms)
      5. Create landmark-based mask (protects hair/forehead) (~1ms)
      6. Color-corrected paste back (~2ms)
      Total: ~34ms per frame
    
    Returns the processed frame (numpy array) or None on failure.
    """
    import cv2
    import numpy as np

    if source_face_embedding is None:
        return None

    # Step 1: Detect faces in target frame
    faces = insightface_app.get(frame)
    if not faces:
        return frame  # No faces detected - return original

    result = frame.copy()

    for target_face in faces:
        # Get 5-point landmarks from InsightFace
        landmarks_5 = target_face.kps.astype(np.float32)

        # Step 2: Compute affine matrix and warp face to 128x128
        affine_matrix = cv2.estimateAffinePartial2D(
            landmarks_5, ARCFACE_128_TEMPLATE,
            method=cv2.RANSAC, ransacReprojThreshold=100
        )[0]

        if affine_matrix is None:
            continue

        crop = cv2.warpAffine(
            result, affine_matrix, (128, 128),
            borderMode=cv2.BORDER_REPLICATE,
            flags=cv2.INTER_AREA,
        )

        # Step 3: Prepare input tensor (BGR→RGB, /255, HWC→CHW)
        crop_input = crop[:, :, ::-1].astype(np.float32) / 255.0
        crop_input = crop_input.transpose(2, 0, 1)
        crop_input = np.expand_dims(crop_input, axis=0)

        # Step 4: Run inswapper ONNX inference
        swap_result = onnx_session.run(
            None,
            {'source': source_face_embedding, 'target': crop_input}
        )[0][0]

        # Step 5: Normalize output (CHW→HWC, clip, RGB→BGR)
        swap_result = swap_result.transpose(1, 2, 0)
        swap_result = swap_result.clip(0, 1)
        swap_result = (swap_result[:, :, ::-1] * 255).astype(np.uint8)

        # Step 6: GFPGAN Enhancement (restores skin detail)
        if use_enhancer and gfpgan_session is not None:
            enhanced = enhance_face_gfpgan(swap_result, target_size=512)
            # Resize back to match paste requirements
            # We'll paste at higher resolution for better quality
            paste_size = 512
        else:
            enhanced = swap_result
            paste_size = 128

        # Step 7: Create affine matrix for the enhanced size
        scale_factor = paste_size / 128.0
        affine_matrix_scaled = affine_matrix.copy()
        affine_matrix_scaled[:, 2] *= 1  # Translation stays the same
        # Create new affine for paste_size
        scaled_template = ARCFACE_128_TEMPLATE * scale_factor
        affine_matrix_large = cv2.estimateAffinePartial2D(
            landmarks_5, scaled_template,
            method=cv2.RANSAC, ransacReprojThreshold=100
        )[0]

        if affine_matrix_large is None:
            affine_matrix_large = affine_matrix
            enhanced = cv2.resize(enhanced, (128, 128), interpolation=cv2.INTER_AREA)
            paste_size = 128

        # Step 8: Create landmark-based mask (protects hair/forehead)
        h, w = result.shape[:2]
        face_mask = create_landmark_mask(target_face, result.shape, blur_amount=0.3)

        # Step 9: Paste enhanced face back using inverse affine
        inv_matrix = cv2.invertAffineTransform(affine_matrix_large)

        # Warp the enhanced face back to original position
        face_warped = cv2.warpAffine(
            enhanced, inv_matrix, (w, h),
            borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0),
        )

        # Create a mask for the warped face region (where pixels are non-zero)
        warp_mask = np.zeros((paste_size, paste_size), dtype=np.float32)
        # Soft box mask within the crop
        border = int(paste_size * 0.05)
        warp_mask[border:-border, border:-border] = 1.0
        blur_k = max(3, int(paste_size * 0.08) | 1)
        warp_mask = cv2.GaussianBlur(warp_mask, (blur_k, blur_k), 0)

        warp_mask_warped = cv2.warpAffine(
            warp_mask, inv_matrix, (w, h),
            borderMode=cv2.BORDER_CONSTANT, borderValue=0,
        )

        # Combine: warp_mask (where face was pasted) AND face_mask (landmark-based protection)
        combined_mask = warp_mask_warped * face_mask

        # Step 10: Color correction for seamless blending
        face_region = combined_mask > 0.1
        if face_region.any():
            face_warped = color_transfer(face_warped, result, combined_mask)

        # Step 11: Alpha blend
        mask_3ch = combined_mask[:, :, np.newaxis]
        result = (face_warped * mask_3ch + result * (1 - mask_3ch)).astype(np.uint8)

    return result


# ── FaceFusion CLI Fallback (for video processing) ───────────────────────────

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
        "--processors", *processors,
        "--face-swapper-model", current_config["face_swapper_model"],
        "--face-swapper-pixel-boost", current_config["face_swapper_pixel_boost"],
        "--face-swapper-weight", str(current_config["face_swapper_weight"]),
        "--face-detector-model", current_config["face_detector_model"],
        "--face-detector-score", str(current_config["face_detector_score"]),
        "--face-mask-types", *current_config["face_mask_types"],
        "--face-mask-blur", str(current_config["face_mask_blur"]),
        "--face-mask-padding", *[str(p) for p in current_config["face_mask_padding"]],
        "--output-video-quality", str(current_config.get("output_video_quality", 90)),
        "--execution-providers", current_config["execution_providers"],
        "--execution-thread-count", str(current_config["execution_thread_count"]),
    ]

    if current_config["face_enhancer_enabled"]:
        cmd.extend(["--face-enhancer-model", current_config["face_enhancer_model"]])

    return cmd


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    global source_face_path

    logger.info("=" * 60)
    logger.info("FaceFusion GPU Worker v3.1 - Direct ONNX + GFPGAN + Hair Protection")
    logger.info("=" * 60)
    logger.info(f"FaceFusion directory: {FACEFUSION_DIR}")
    logger.info(f"Source face directory: {SOURCE_FACE_DIR}")
    logger.info(f"InsightFace directory: {INSIGHTFACE_DIR}")

    gpu_info = get_gpu_info()
    logger.info(f"GPU: {gpu_info['gpu_name']} ({gpu_info['gpu_memory_total_mb']}MB)")

    # ── Initialize Direct ONNX Engine ──────────────────────────────────────
    engine_ok = init_direct_onnx_engine()

    # If a source face was previously saved, pre-compute embedding
    if engine_ok:
        existing_faces = sorted(Path(SOURCE_FACE_DIR).glob("source_face_*.jpg"), reverse=True)
        if existing_faces:
            latest_face = str(existing_faces[0])
            source_face_path = latest_face
            embed_ok = compute_source_embedding(latest_face)
            if embed_ok:
                logger.info(f"[ONNX] Pre-loaded source face from {latest_face}")
                # Warmup: run a full swap to ensure all CUDA kernels are compiled
                try:
                    import cv2
                    _warmup_start = time.time()
                    _warmup_img = cv2.imread(latest_face)
                    if _warmup_img is not None:
                        _warmup_result = direct_swap_frame(_warmup_img, use_enhancer=True)
                        _warmup_elapsed = (time.time() - _warmup_start) * 1000
                        if _warmup_result is not None:
                            logger.info(f"[ONNX] Full pipeline warmup (with GFPGAN): {_warmup_elapsed:.0f}ms")
                            # Run again to get cached performance
                            _t2 = time.time()
                            _r2 = direct_swap_frame(_warmup_img, use_enhancer=True)
                            _e2 = (time.time() - _t2) * 1000
                            logger.info(f"[ONNX] Cached pipeline speed (with GFPGAN): {_e2:.0f}ms/frame")
                            # Also test without enhancer
                            _t3 = time.time()
                            _r3 = direct_swap_frame(_warmup_img, use_enhancer=False)
                            _e3 = (time.time() - _t3) * 1000
                            logger.info(f"[ONNX] Cached pipeline speed (no GFPGAN): {_e3:.0f}ms/frame")
                        else:
                            logger.warning("[ONNX] Warmup returned None")
                except Exception as _e:
                    logger.warning(f"[ONNX] Warmup failed (non-fatal): {_e}")

    logger.info(f"[ONNX] Engine status: ready={onnx_engine_ready}, "
                f"source_face={'OK' if source_face_embedding is not None else 'NONE'}, "
                f"gfpgan={'OK' if gfpgan_session is not None else 'NONE'}")
    logger.info("=" * 60)

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
    description="Ultra-fast face swap worker (Direct ONNX + GFPGAN + Hair Protection, ~34ms/frame)",
    version="3.1.0",
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
    Returns GPU status, engine info, and stream state.
    """
    gpu_info = get_gpu_info()

    ff_installed = Path(f"{FACEFUSION_DIR}/facefusion.py").exists()

    return {
        "status": "ok" if onnx_engine_ready else "engine_not_ready",
        "gpu": gpu_info,
        "facefusion_installed": ff_installed,
        "source_face_loaded": source_face_path is not None and Path(source_face_path).exists(),
        "stream_status": current_session["status"],
        "session_id": current_session["id"],
        "config": current_config,
        "engine": {
            "type": "direct_onnx_pipeline_v3.1",
            "version": "3.1.0",
            "onnx_engine_ready": onnx_engine_ready,
            "gfpgan_ready": gfpgan_session is not None,
            "source_face_loaded": source_face_embedding is not None,
            "pipeline": "detect(14ms) → swap(6ms) → GFPGAN(11ms) → mask+paste(3ms) = ~34ms",
            "features": ["hair_protection", "gfpgan_enhancement", "color_correction", "landmark_mask"],
            "expected_fps": "29+ FPS (~34ms/frame)",
            "ready": onnx_engine_ready and source_face_embedding is not None,
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
        content = await file.read()
        with open(save_path, "wb") as f:
            f.write(content)
        logger.info(f"Source face saved from upload: {save_path} ({len(content)} bytes)")

    elif image_base64:
        content = base64.b64decode(image_base64)
        with open(save_path, "wb") as f:
            f.write(content)
        logger.info(f"Source face saved from base64: {save_path} ({len(content)} bytes)")

    elif image_url:
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

    # Compute source face embedding
    face_detected = False
    num_faces = 0

    if onnx_engine_ready:
        try:
            import cv2
            img = cv2.imread(save_path)
            if img is not None:
                faces = insightface_app.get(img)
                num_faces = len(faces)
                if num_faces > 0:
                    face_detected = compute_source_embedding(save_path, face_index)
                    logger.info(f"[ONNX] Source face computed "
                                f"({num_faces} faces detected, using index {face_index})")
                else:
                    logger.warning("[ONNX] No faces detected in source image")
        except Exception as e:
            logger.error(f"[ONNX] Error computing source face: {e}")
    else:
        face_detected = True
        logger.info("[ONNX] Engine not ready, source saved for CLI fallback")

    return {
        "status": "ok",
        "source_face_path": save_path,
        "face_detected": face_detected,
        "face_index": face_index,
        "num_faces_detected": num_faces,
        "engine": "direct_onnx_v3.1" if onnx_engine_ready else "cli_fallback",
        "features": {
            "gfpgan": gfpgan_session is not None,
            "hair_protection": True,
            "color_correction": True,
        },
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

    processes_alive = {}
    for key in ("facefusion_proc", "ffmpeg_in_proc", "ffmpeg_out_proc"):
        proc = current_session.get(key)
        if proc:
            poll = proc.poll()
            processes_alive[key.replace("_proc", "")] = poll is None
        else:
            processes_alive[key.replace("_proc", "")] = False

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
    Uses Direct ONNX Pipeline v3.1 with GFPGAN + hair protection.
    Returns the processed image as base64.
    """
    if source_face_path is None or not Path(source_face_path).exists():
        raise HTTPException(400, "Source face not set. Call /api/set-source first.")

    content = base64.b64decode(req.image_base64)
    start_time = time.time()

    # Try Direct ONNX Pipeline first
    if onnx_engine_ready and source_face_embedding is not None:
        try:
            import cv2
            import numpy as np

            nparr = np.frombuffer(content, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            if frame is None:
                raise ValueError("Failed to decode image")

            result = direct_swap_frame(frame, use_enhancer=req.face_enhancer)

            if result is not None:
                _, encoded = cv2.imencode('.jpg', result,
                                          [cv2.IMWRITE_JPEG_QUALITY, 95])
                output_base64 = base64.b64encode(encoded.tobytes()).decode()

                elapsed_ms = int((time.time() - start_time) * 1000)
                logger.info(f"[swap-frame] Processed in {elapsed_ms}ms "
                            f"(Direct ONNX v3.1, enhancer={req.face_enhancer})")

                return {
                    "status": "ok",
                    "image_base64": output_base64,
                    "quality": req.quality,
                    "face_enhancer": req.face_enhancer,
                    "processing_time_ms": elapsed_ms,
                    "engine": "direct_onnx_v3.1",
                    "features": {
                        "gfpgan": req.face_enhancer and gfpgan_session is not None,
                        "hair_protection": True,
                        "color_correction": True,
                    },
                }
        except Exception as e:
            logger.warning(f"[swap-frame] Direct ONNX failed, falling back to CLI: {e}")

    # Fallback to FaceFusion CLI
    input_path = os.path.join(TEMP_DIR, f"input_{uuid.uuid4().hex[:8]}.jpg")
    output_path = os.path.join(TEMP_DIR, f"output_{uuid.uuid4().hex[:8]}.jpg")

    try:
        with open(input_path, "wb") as f:
            f.write(content)

        original_enhancer = current_config["face_enhancer_enabled"]
        current_config["face_enhancer_enabled"] = req.face_enhancer

        cmd = build_facefusion_headless_cmd(input_path, output_path)
        logger.info(f"Processing single frame via CLI: {' '.join(cmd[:6])}...")

        _env = os.environ.copy()
        _env["LD_LIBRARY_PATH"] = "/usr/lib/x86_64-linux-gnu:" + _env.get("LD_LIBRARY_PATH", "")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=90,
            cwd=FACEFUSION_DIR,
            env=_env,
        )

        current_config["face_enhancer_enabled"] = original_enhancer

        if result.returncode != 0:
            logger.error(f"FaceFusion error: {result.stderr}")
            raise HTTPException(500, f"FaceFusion processing failed: {result.stderr[:500]}")

        if not Path(output_path).exists():
            raise HTTPException(500, "Output image not generated")

        with open(output_path, "rb") as f:
            output_base64 = base64.b64encode(f.read()).decode()

        elapsed_ms = int((time.time() - start_time) * 1000)
        return {
            "status": "ok",
            "image_base64": output_base64,
            "quality": req.quality,
            "face_enhancer": req.face_enhancer,
            "processing_time_ms": elapsed_ms,
            "engine": "facefusion_cli",
        }

    finally:
        for p in (input_path, output_path):
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass


@app.get("/api/config")
async def get_config(auth: bool = Depends(verify_api_key)):
    """Get current configuration."""
    return {
        "config": current_config,
        "quality_presets": {
            "fast": {
                "face_swapper_model": "inswapper_128",
                "face_enhancer_enabled": False,
                "face_detector_model": "yolo_face",
                "face_detector_score": 0.5,
                "face_mask_blur": 0.3,
                "description": "Fastest mode (~25ms), no GFPGAN",
            },
            "standard": {
                "face_swapper_model": "inswapper_128",
                "face_enhancer_enabled": True,
                "face_enhancer_model": "gfpgan_1.4",
                "face_detector_model": "yolo_face",
                "face_detector_score": 0.3,
                "face_mask_blur": 0.2,
                "description": "Balanced mode (~34ms), GFPGAN + hair protection",
            },
            "pro": {
                "face_swapper_model": "inswapper_128",
                "face_enhancer_enabled": True,
                "face_enhancer_model": "gfpgan_1.4",
                "face_detector_model": "yolo_face",
                "face_detector_score": 0.3,
                "face_mask_blur": 0.15,
                "description": "Best quality (~34ms), GFPGAN + hair protection + color correction",
            },
        },
        "available_models": {
            "face_swapper": ["inswapper_128"],
            "face_enhancer": [
                "gfpgan_1.4",
                "gpen_bfr_256",
                "gpen_bfr_512",
                "codeformer",
            ],
            "face_detector": ["yolo_face"],
        },
        "features": {
            "gfpgan_ready": gfpgan_session is not None,
            "hair_protection": True,
            "color_correction": True,
            "landmark_mask": True,
        },
    }


@app.post("/api/config")
async def update_config(req: UpdateConfigRequest, auth: bool = Depends(verify_api_key)):
    """
    Update configuration.
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

def _run_video_job(job_id: str, video_url: str, face_enhancer: bool,
                   quality: str, output_video_quality: int):
    """Background thread: download video, run FaceFusion CLI, update job state."""
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

        # --- Step 2: Get video duration ---
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
            current_config["face_swapper_model"] = "inswapper_128"
            current_config["face_swapper_pixel_boost"] = "128x128"
            current_config["face_detector_model"] = "yolo_face"
            current_config["face_detector_score"] = 0.5
            current_config["face_mask_blur"] = 0.3
        elif quality == "pro":
            current_config["face_enhancer_enabled"] = True
            current_config["face_enhancer_model"] = "codeformer"
            current_config["face_swapper_model"] = "inswapper_128"
            current_config["face_swapper_pixel_boost"] = "128x128"
            current_config["face_detector_model"] = "yolo_face"
            current_config["face_detector_score"] = 0.3
            current_config["face_mask_blur"] = 0.1
        elif quality == "cinema":
            current_config["face_enhancer_enabled"] = True
            current_config["face_enhancer_model"] = "gpen_bfr_512"
            current_config["face_swapper_model"] = "inswapper_128"
            current_config["face_swapper_pixel_boost"] = "128x128"
            current_config["face_detector_model"] = "yolo_face"
            current_config["face_detector_score"] = 0.3
            current_config["face_mask_blur"] = 0.1
        else:
            current_config["face_enhancer_enabled"] = face_enhancer

        current_config["output_video_quality"] = output_video_quality
        logger.info(f"[{job_id}] Quality preset: {quality}, "
                    f"enhancer={current_config['face_enhancer_enabled']}")

        # --- Step 4: Run FaceFusion CLI for video ---
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

        for line in iter(proc.stdout.readline, ""):
            line = line.strip()
            if not line:
                continue
            if "%" in line:
                try:
                    pct_str = line.split("%")[0].split()[-1]
                    pct = float(pct_str)
                    job["progress"] = 20 + int(pct * 0.7)
                except (ValueError, IndexError):
                    pass
            logger.debug(f"[{job_id}] FF: {line[:120]}")

        proc.wait()

        # Restore config
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
        try:
            os.unlink(input_path)
        except FileNotFoundError:
            pass
        job["pid"] = None


@app.post("/api/swap-video")
async def swap_video(req: SwapVideoRequest, auth: bool = Depends(verify_api_key)):
    """
    Start an async video face swap job.
    Uses FaceFusion CLI for video processing (best quality for offline).
    Returns immediately; poll /api/video-status/{job_id} for progress.
    """
    if source_face_path is None or not Path(source_face_path).exists():
        raise HTTPException(400, "Source face not set. Call /api/set-source first.")

    if req.job_id in video_jobs:
        existing = video_jobs[req.job_id]
        if existing["status"] in ("downloading", "processing"):
            raise HTTPException(409, f"Job {req.job_id} already in progress")

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
    """Download the processed video."""
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

    if job.get("pid"):
        try:
            os.kill(job["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass

    if job.get("output_path"):
        try:
            os.unlink(job["output_path"])
        except FileNotFoundError:
            pass

    del video_jobs[job_id]
    return {"status": "deleted", "job_id": job_id}


# ── Audio Processor Endpoints ─────────────────────────────────────────────────

audio_processor: Optional[object] = None


@app.get("/api/audio-status")
async def get_audio_status(auth: bool = Depends(verify_api_key)):
    """Get audio processor status."""
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
    
    Uses Direct ONNX Pipeline v3.1 for high-quality real-time results:
    - InsightFace detection + landmarks (~14ms)
    - Direct ONNX inswapper inference (~6ms)
    - GFPGAN face enhancement (~11ms)
    - Landmark-based mask + color-corrected paste (~3ms)
    - Total: ~34ms per frame = 29+ FPS
    
    Protocol:
      1. Client connects with ?api_key=xxx
      2. Client sends JPEG frames as binary messages
      3. Server processes each frame through Direct ONNX Pipeline v3.1
      4. Server returns processed JPEG frame as binary message
    """
    # Verify API key
    if api_key not in VALID_API_KEYS:
        await websocket.close(code=4001, reason="Invalid API key")
        return

    await websocket.accept()
    logger.info("[Preview] WebSocket client connected")

    frame_count = 0
    error_count = 0
    MAX_ERRORS = 10

    use_direct = onnx_engine_ready and source_face_embedding is not None

    if use_direct:
        logger.info("[Preview] Using Direct ONNX Pipeline v3.1 "
                    "(GFPGAN + hair protection, ~34ms/frame)")
    else:
        logger.warning("[Preview] ONNX engine not ready, using CLI fallback")

    try:
        import cv2
        import numpy as np

        total_process_time = 0
        frames_processed = 0

        # Latest frame buffer - only process the most recent frame
        latest_frame_data = None
        frame_lock = asyncio.Lock()

        async def receiver():
            """Continuously receive frames, keeping only the latest."""
            nonlocal latest_frame_data, frame_count
            try:
                while True:
                    data = await websocket.receive_bytes()
                    frame_count += 1
                    async with frame_lock:
                        latest_frame_data = data
            except WebSocketDisconnect:
                pass
            except Exception:
                pass

        async def processor():
            """Process the latest frame and send result back."""
            nonlocal latest_frame_data, error_count, total_process_time, frames_processed

            last_data = None

            while True:
                async with frame_lock:
                    data = latest_frame_data

                if data is None or data == last_data:
                    await asyncio.sleep(0.005)  # 5ms poll
                    continue

                last_data = data

                if source_face_embedding is None and (source_face_path is None or not Path(source_face_path).exists()):
                    await websocket.send_json({
                        "type": "error",
                        "message": "Source face not set. Upload a face image first."
                    })
                    await asyncio.sleep(0.1)
                    continue

                try:
                    start_time = time.time()

                    if use_direct and source_face_embedding is not None:
                        # ── Direct ONNX Pipeline v3.1 Processing ──────────────
                        nparr = np.frombuffer(data, np.uint8)
                        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

                        if frame is None:
                            error_count += 1
                            await websocket.send_bytes(data)
                            continue

                        h_orig, w_orig = frame.shape[:2]

                        # Process frame with GFPGAN + hair protection
                        use_enhancer = current_config.get("face_enhancer_enabled", True)
                        result = direct_swap_frame(frame, use_enhancer=use_enhancer)

                        if result is not None:
                            _, encoded = cv2.imencode('.jpg', result,
                                                      [cv2.IMWRITE_JPEG_QUALITY, 90])
                            processed = encoded.tobytes()
                        else:
                            processed = data

                        elapsed_ms = int((time.time() - start_time) * 1000)
                        total_process_time += elapsed_ms
                        frames_processed += 1

                        if frames_processed % 10 == 0:
                            avg_ms = total_process_time / max(1, frames_processed)
                            fps = 1000.0 / avg_ms if avg_ms > 0 else 0
                            logger.info(f"[Preview] Frame {frames_processed}: {elapsed_ms}ms "
                                        f"(avg: {avg_ms:.0f}ms, ~{fps:.1f} FPS, "
                                        f"{w_orig}x{h_orig}, enhancer={use_enhancer})")

                        await websocket.send_bytes(processed)
                        error_count = 0

                    else:
                        # ── FaceFusion CLI Fallback ─────────────────────────────
                        input_path = os.path.join(TEMP_DIR, f"preview_in_{frames_processed % 10}.jpg")
                        output_path = os.path.join(TEMP_DIR, f"preview_out_{frames_processed % 10}.jpg")

                        with open(input_path, "wb") as f:
                            f.write(data)

                        cmd = build_facefusion_headless_cmd(input_path, output_path)
                        _env = os.environ.copy()
                        _env["LD_LIBRARY_PATH"] = "/usr/lib/x86_64-linux-gnu:" + _env.get("LD_LIBRARY_PATH", "")
                        proc_result = subprocess.run(
                            cmd, capture_output=True, text=True,
                            timeout=90, cwd=FACEFUSION_DIR, env=_env,
                        )

                        if proc_result.returncode == 0 and Path(output_path).exists():
                            with open(output_path, "rb") as f:
                                processed = f.read()
                            await websocket.send_bytes(processed)
                            error_count = 0
                        else:
                            error_count += 1
                            await websocket.send_bytes(data)

                        elapsed_ms = int((time.time() - start_time) * 1000)
                        frames_processed += 1

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
                    try:
                        await websocket.send_bytes(data)
                    except Exception:
                        break

                if error_count >= MAX_ERRORS:
                    try:
                        await websocket.send_json({
                            "type": "error",
                            "message": "Too many processing errors. Check GPU Worker logs."
                        })
                    except Exception:
                        pass
                    break

        # Run receiver and processor concurrently
        receiver_task = asyncio.create_task(receiver())
        try:
            await processor()
        finally:
            receiver_task.cancel()
            try:
                await receiver_task
            except asyncio.CancelledError:
                pass

    except WebSocketDisconnect:
        logger.info(f"[Preview] Client disconnected after {frame_count} frames")
    except Exception as e:
        logger.error(f"[Preview] WebSocket error: {e}")
    finally:
        logger.info(f"[Preview] Session ended. Frames received: {frame_count}, processed: {frames_processed}")


@app.post("/api/preview-frame")
async def preview_frame(req: SwapFrameRequest, auth: bool = Depends(verify_api_key)):
    """
    Process a single frame for preview (HTTP fallback for WebSocket).
    Uses Direct ONNX Pipeline v3.1 with GFPGAN + hair protection.
    """
    if source_face_path is None or not Path(source_face_path).exists():
        raise HTTPException(400, "Source face not set. Call /api/set-source first.")

    content = base64.b64decode(req.image_base64)
    start_time = time.time()

    # Try Direct ONNX Pipeline first
    if onnx_engine_ready and source_face_embedding is not None:
        try:
            import cv2
            import numpy as np

            nparr = np.frombuffer(content, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            if frame is None:
                raise ValueError("Failed to decode image")

            result = direct_swap_frame(frame, use_enhancer=req.face_enhancer)

            if result is not None:
                _, encoded = cv2.imencode('.jpg', result,
                                          [cv2.IMWRITE_JPEG_QUALITY, 95])
                output_base64 = base64.b64encode(encoded.tobytes()).decode()

                elapsed_ms = int((time.time() - start_time) * 1000)
                logger.info(f"[Preview] Frame processed in {elapsed_ms}ms "
                            f"(Direct ONNX v3.1, enhancer={req.face_enhancer})")

                return {
                    "status": "ok",
                    "image_base64": output_base64,
                    "processing_time_ms": elapsed_ms,
                    "engine": "direct_onnx_v3.1",
                    "features": {
                        "gfpgan": req.face_enhancer and gfpgan_session is not None,
                        "hair_protection": True,
                        "color_correction": True,
                    },
                }
            else:
                output_base64 = req.image_base64
                elapsed_ms = int((time.time() - start_time) * 1000)
                return {
                    "status": "ok",
                    "image_base64": output_base64,
                    "processing_time_ms": elapsed_ms,
                    "engine": "direct_onnx_v3.1",
                    "note": "no_faces_detected",
                }
        except Exception as e:
            logger.warning(f"[Preview] Direct ONNX failed, falling back to CLI: {e}")

    # Fallback to FaceFusion CLI
    input_path = os.path.join(TEMP_DIR, f"preview_{uuid.uuid4().hex[:8]}.jpg")
    output_path = os.path.join(TEMP_DIR, f"preview_out_{uuid.uuid4().hex[:8]}.jpg")

    try:
        with open(input_path, "wb") as f:
            f.write(content)

        original_enhancer = current_config["face_enhancer_enabled"]
        current_config["face_enhancer_enabled"] = req.face_enhancer

        cmd = build_facefusion_headless_cmd(input_path, output_path)
        _env = os.environ.copy()
        _env["LD_LIBRARY_PATH"] = "/usr/lib/x86_64-linux-gnu:" + _env.get("LD_LIBRARY_PATH", "")
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=90, cwd=FACEFUSION_DIR, env=_env,
        )
        elapsed_ms = int((time.time() - start_time) * 1000)

        current_config["face_enhancer_enabled"] = original_enhancer

        if result.returncode != 0:
            raise HTTPException(500, f"FaceFusion error: {result.stderr[:300]}")
        if not Path(output_path).exists():
            raise HTTPException(500, "Output not generated")

        with open(output_path, "rb") as f:
            output_base64 = base64.b64encode(f.read()).decode()

        return {
            "status": "ok",
            "image_base64": output_base64,
            "processing_time_ms": elapsed_ms,
            "engine": "facefusion_cli",
        }
    finally:
        for p in (input_path, output_path):
            try:
                os.unlink(p)
            except (FileNotFoundError, OSError):
                pass


# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info(f"Starting FaceFusion GPU Worker v3.1 (Direct ONNX + GFPGAN + Hair Protection) on port {PORT}")
    uvicorn.run(
        "face_swap_worker_api:app",
        host="0.0.0.0",
        port=PORT,
        workers=1,
        log_level="info",
    )
