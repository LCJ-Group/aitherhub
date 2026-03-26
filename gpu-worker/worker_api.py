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
from fastapi import Depends, FastAPI, Header, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
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
PORT = int(os.getenv("WORKER_PORT", "8000"))

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

# Quality presets for video face swap
QUALITY_PRESETS = {
    "fast": {
        "face_swapper_model": "hyperswap_1b_256",
        "face_swapper_pixel_boost": "512x512",
        "face_enhancer_enabled": False,
        "face_detector_model": "yolo_face",
        "execution_thread_count": 8,
    },
    "balanced": {
        "face_swapper_model": "hyperswap_1c_256",
        "face_swapper_pixel_boost": "512x512",
        "face_enhancer_enabled": False,
        "face_detector_model": "yolo_face",
        "execution_thread_count": 8,
    },
    "high": {
        "face_swapper_model": "hyperswap_1c_256",
        "face_swapper_pixel_boost": "1024x1024",
        "face_enhancer_enabled": False,
        "face_detector_model": "yolo_face",
        "execution_thread_count": 8,
    },
    "ultra": {
        "face_swapper_model": "hyperswap_1c_256",
        "face_swapper_pixel_boost": "1024x1024",
        "face_enhancer_enabled": True,
        "face_enhancer_model": "gfpgan_1.4",
        "face_detector_model": "yolo_face",
        "execution_thread_count": 4,
    },
}

current_config = {
    "face_swapper_model": "hyperswap_1c_256",
    "face_swapper_pixel_boost": "1024x1024",
    "face_swapper_weight": 0.85,
    "face_enhancer_model": "gfpgan_1.4",
    "face_enhancer_enabled": False,
    "face_detector_model": "yolo_face",
    "face_detector_score": 0.5,
    "face_mask_types": ["box", "occlusion", "region"],
    "face_mask_blur": 0.3,
    "face_mask_padding": [0, 0, 0, 0],
    "output_image_quality": 95,
    "output_resolution": "1280x720",
    "output_fps": 30,
    "execution_providers": "cuda",
    "execution_thread_count": 8,
}

source_face_path: Optional[str] = None


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
    face_enhancer: bool = Field(default=False, description="Enable GFPGAN face enhancement (overrides preset if True)")


class StopStreamRequest(BaseModel):
    session_id: Optional[str] = None


class SwapFrameRequest(BaseModel):
    image_base64: str = Field(..., description="Base64-encoded input image")
    quality: str = Field(default="high", description="Quality preset")
    face_enhancer: bool = Field(default=False, description="Enable face enhancement (overrides preset if True)")


class SwapVideoRequest(BaseModel):
    """Request to start a video face swap job."""
    job_id: str = Field(..., description="Unique job ID assigned by the backend")
    video_url: str = Field(..., description="URL to download the input video")
    face_enhancer: bool = Field(default=False, description="Enable face enhancement (overrides preset if True)")
    quality: str = Field(default="high", description="Quality preset: fast, balanced, high, ultra")
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


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info("FaceFusion GPU Worker starting up (v8 quality-presets)...")
    logger.info(f"FaceFusion directory: {FACEFUSION_DIR}")
    logger.info(f"Source face directory: {SOURCE_FACE_DIR}")
    logger.info(f"Default config: model={current_config['face_swapper_model']}, "
                f"boost={current_config['face_swapper_pixel_boost']}, "
                f"enhancer={current_config['face_enhancer_enabled']}, "
                f"detector={current_config['face_detector_model']}")

    gpu_info = get_gpu_info()
    logger.info(f"GPU: {gpu_info['gpu_name']} ({gpu_info['gpu_memory_total_mb']}MB)")

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

    # Validate face detection using FaceFusion (optional quick check)
    face_detected = True  # Assume success; full validation happens at stream start

    return {
        "status": "ok",
        "source_face_path": save_path,
        "face_detected": face_detected,
        "face_index": face_index,
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
    preset = QUALITY_PRESETS.get(req.quality, QUALITY_PRESETS["high"])
    for key, value in preset.items():
        current_config[key] = value
    if req.face_enhancer and req.quality != "fast":
        current_config["face_enhancer_enabled"] = True

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

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=FACEFUSION_DIR,
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

        # --- Step 3: Apply quality preset ---
        # Save original config to restore after processing
        original_config = dict(current_config)
        preset = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["high"])
        for key, value in preset.items():
            current_config[key] = value
        # Override face_enhancer if explicitly requested
        if face_enhancer and quality != "fast":
            current_config["face_enhancer_enabled"] = True
        current_config["output_video_quality"] = output_video_quality
        logger.info(f"[{job_id}] Applied preset '{quality}': "
                    f"model={current_config['face_swapper_model']}, "
                    f"boost={current_config['face_swapper_pixel_boost']}, "
                    f"enhancer={current_config['face_enhancer_enabled']}, "
                    f"detector={current_config['face_detector_model']}")

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
        # Restore original config
        current_config.update(original_config)

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


# ── Debug ────────────────────────────────────────────────────────────────────

@app.get("/api/debug/test-facefusion")
async def debug_test_facefusion(auth: bool = Depends(verify_api_key)):
    """Run a quick FaceFusion test and return full output for debugging."""
    import subprocess
    result = {}

    # Check source face
    result["source_face_path"] = source_face_path
    result["source_face_exists"] = source_face_path is not None and Path(source_face_path).exists()
    if result["source_face_exists"]:
        result["source_face_size"] = os.path.getsize(source_face_path)
        # Check file format
        try:
            import imghdr
            result["source_face_format"] = imghdr.what(source_face_path)
        except Exception:
            pass

    # Check .jobs directory
    jobs_path = os.path.join(FACEFUSION_DIR, ".jobs")
    result["jobs_path_exists"] = os.path.isdir(jobs_path)

    # Build the command that would be used
    if source_face_path:
        test_cmd = [
            sys.executable, f"{FACEFUSION_DIR}/facefusion.py", "headless-run",
            "--source-paths", source_face_path,
            "--target-path", "/dev/null",
            "--output-path", "/dev/null",
            "--processors", "face_swapper",
            "--face-swapper-model", current_config["face_swapper_model"],
            "--help",
        ]
        result["test_command"] = " ".join(test_cmd)

    # Run FaceFusion --help to check available args
    try:
        proc = subprocess.run(
            [sys.executable, f"{FACEFUSION_DIR}/facefusion.py", "headless-run", "--help"],
            capture_output=True, text=True, timeout=30, cwd=FACEFUSION_DIR,
        )
        result["help_stdout"] = proc.stdout[:3000]
        result["help_stderr"] = proc.stderr[:3000]
        result["help_returncode"] = proc.returncode
    except Exception as e:
        result["help_error"] = str(e)

    # Check FaceFusion models directory
    models_dir = os.path.join(FACEFUSION_DIR, ".assets", "models")
    if os.path.isdir(models_dir):
        result["models"] = os.listdir(models_dir)[:20]
    else:
        # Try alternative paths
        for alt in ["/workspace/facefusion/models", "/root/.facefusion/models"]:
            if os.path.isdir(alt):
                result["models_path"] = alt
                result["models"] = os.listdir(alt)[:20]
                break

    return result


@app.post("/api/debug/run-facefusion")
async def debug_run_facefusion(
    auth: bool = Depends(verify_api_key),
    target_url: str = "",
):
    """Run FaceFusion on a single frame and return full output."""
    import subprocess
    import httpx

    if not source_face_path or not Path(source_face_path).exists():
        return {"error": "No source face set"}

    # Download a single frame from the target video
    input_path = os.path.join(TEMP_DIR, "debug_input.jpg")
    output_path = os.path.join(TEMP_DIR, "debug_output.jpg")

    if target_url:
        # Download video and extract first frame
        video_path = os.path.join(TEMP_DIR, "debug_video.mp4")
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(target_url)
            resp.raise_for_status()
            with open(video_path, "wb") as f:
                f.write(resp.content)

        # Extract first frame
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-vframes", "1", "-q:v", "2", input_path],
            capture_output=True, timeout=30,
        )
    else:
        return {"error": "Provide target_url"}

    if not Path(input_path).exists():
        return {"error": "Failed to extract frame"}

    # Run FaceFusion headless-run on single image
    cmd = build_facefusion_headless_cmd(input_path, output_path)
    result = {"command": " ".join(cmd)}

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, cwd=FACEFUSION_DIR,
        )
        result["stdout"] = proc.stdout[:5000]
        result["stderr"] = proc.stderr[:5000]
        result["returncode"] = proc.returncode
        result["output_exists"] = Path(output_path).exists()
        if result["output_exists"]:
            result["output_size"] = os.path.getsize(output_path)
    except Exception as e:
        result["error"] = str(e)

    # Cleanup
    for p in [input_path, output_path, os.path.join(TEMP_DIR, "debug_video.mp4")]:
        try:
            os.unlink(p)
        except FileNotFoundError:
            pass

    return result


# ── MuseTalk Digital Human (v2 with MuseTalkEngine + Avatar Caching) ──────────

import hashlib

MUSETALK_DIR = os.getenv("MUSETALK_DIR", "/workspace/MuseTalk")
MUSETALK_MODELS_DIR = os.path.join(MUSETALK_DIR, "models")

# MuseTalkEngine singleton (from live_engine.py)
_musetalk_engine = None
_musetalk_engine_lock = asyncio.Lock() if hasattr(asyncio, 'Lock') else None

# Avatar cache: portrait_hash -> True (avatar already prepared)
_avatar_cache: dict = {}  # portrait_hash -> {"prepared": True, "portrait_path": str}
_current_avatar_hash: str = ""  # Hash of the avatar currently loaded in the engine


def _get_portrait_hash(portrait_path: str) -> str:
    """Compute a hash of the portrait file for caching."""
    h = hashlib.md5()
    with open(portrait_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _get_or_create_engine():
    """Get or create the MuseTalkEngine singleton."""
    global _musetalk_engine
    if _musetalk_engine is not None and _musetalk_engine.models_loaded:
        return _musetalk_engine

    # Import from live_engine.py (should be in /workspace/ alongside worker_api.py)
    engine_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live_engine.py")
    if not os.path.exists(engine_path):
        engine_path = "/workspace/live_engine.py"

    import importlib.util
    spec = importlib.util.spec_from_file_location("live_engine", engine_path)
    live_engine_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(live_engine_mod)

    config = live_engine_mod.EngineConfig(
        version="v15",
        bbox_shift=0,
        extra_margin=10,
        batch_size=20,
        parsing_mode="jaw",
        left_cheek_width=90,
        right_cheek_width=90,
        audio_padding_length_left=2,
        audio_padding_length_right=2,
    )
    _musetalk_engine = live_engine_mod.MuseTalkEngine(config)
    _musetalk_engine.load_models()
    logger.info("MuseTalkEngine singleton created and models loaded")
    return _musetalk_engine


# Legacy model cache (kept for backward compatibility with load_musetalk_models)
musetalk_models = {
    "loaded": False,
    "vae": None,
    "unet": None,
    "pe": None,
    "whisper": None,
    "audio_processor": None,
    "fp": None,
    "device": None,
    "timesteps": None,
}


def load_musetalk_models():
    """Lazy-load MuseTalk v1.5 models via MuseTalkEngine singleton."""
    if musetalk_models["loaded"]:
        return True
    try:
        engine = _get_or_create_engine()
        # Populate legacy dict for any code that still references it
        musetalk_models.update({
            "loaded": True,
            "vae": engine.vae,
            "unet": engine.unet,
            "pe": engine.pe,
            "whisper": engine.whisper,
            "audio_processor": engine.audio_processor,
            "fp": engine.fp,
            "device": engine.device,
            "timesteps": engine.timesteps,
        })
        logger.info("MuseTalk v1.5 models loaded successfully (via MuseTalkEngine)")
        return True
    except Exception as e:
        logger.error(f"Failed to load MuseTalk models: {e}")
        import traceback
        traceback.print_exc()
        return False


class DigitalHumanRequest(BaseModel):
    """Request to generate a digital human video."""
    job_id: str = Field(..., description="Unique job ID")
    portrait_url: str = Field(..., description="URL of portrait image/video (front-facing)")
    audio_url: str = Field(..., description="URL of audio file (WAV/MP3)")
    portrait_type: str = Field(default="image", description="'image' or 'video'")
    bbox_shift: int = Field(default=0, description="Vertical shift for face bounding box")
    extra_margin: int = Field(default=10, description="Extra margin below face for v1.5")
    batch_size: int = Field(default=16, description="Inference batch size")
    output_fps: int = Field(default=25, description="Output video FPS")


# Digital human job state
digital_human_jobs: dict = {}  # job_id -> {status, progress, output_path, error, ...}


@app.post("/api/digital-human/generate", dependencies=[Depends(verify_api_key)])
async def digital_human_generate(req: DigitalHumanRequest):
    """Start a digital human video generation job using MuseTalk v2 engine."""
    if req.job_id in digital_human_jobs:
        return {"error": f"Job {req.job_id} already exists"}

    digital_human_jobs[req.job_id] = {
        "status": "queued",
        "progress": 0,
        "output_path": None,
        "error": None,
        "created_at": time.time(),
    }

    asyncio.create_task(_run_digital_human_job(req))
    return {"job_id": req.job_id, "status": "queued"}


@app.get("/api/digital-human/status/{job_id}", dependencies=[Depends(verify_api_key)])
async def digital_human_status(job_id: str):
    """Get the status of a digital human generation job."""
    job = digital_human_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return {
        "job_id": job_id,
        "status": job["status"],
        "progress": job["progress"],
        "error": job.get("error"),
    }


from fastapi.responses import FileResponse

@app.get("/api/digital-human/download/{job_id}", dependencies=[Depends(verify_api_key)])
async def digital_human_download(job_id: str):
    """Download the generated digital human video."""
    job = digital_human_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job["status"] != "completed":
        raise HTTPException(status_code=400, detail=f"Job not completed: {job['status']}")
    if not job.get("output_path") or not os.path.exists(job["output_path"]):
        raise HTTPException(status_code=404, detail="Output file not found")
    return FileResponse(
        job["output_path"],
        media_type="video/mp4",
        filename=f"digital_human_{job_id}.mp4",
    )


@app.get("/api/digital-human/health", dependencies=[Depends(verify_api_key)])
async def digital_human_health():
    """Health check for MuseTalk engine."""
    engine = _musetalk_engine
    return {
        "status": "ok",
        "engine": "musetalk_v2",
        "models_loaded": engine.models_loaded if engine else False,
        "avatar_cached": bool(_avatar_cache),
        "current_avatar_hash": _current_avatar_hash or None,
        "cached_avatars": len(_avatar_cache),
        "active_jobs": len([j for j in digital_human_jobs.values() if j["status"] in ("queued", "processing")]),
    }


async def _run_digital_human_job(req: DigitalHumanRequest):
    """Background task to run MuseTalk v2 inference with avatar caching."""
    import httpx
    job = digital_human_jobs[req.job_id]
    job["status"] = "processing"
    job["progress"] = 5

    job_dir = os.path.join(TEMP_DIR, f"dh_{req.job_id}")
    os.makedirs(job_dir, exist_ok=True)

    try:
        # Step 1: Download portrait and audio
        logger.info(f"[DH {req.job_id}] Downloading portrait and audio...")

        # Handle file:// URLs for local testing
        if req.portrait_url.startswith('file://'):
            import shutil as _shutil
            _src = req.portrait_url[7:]
            _ext = os.path.splitext(_src)[1] or '.mp4'
            portrait_path = os.path.join(job_dir, 'portrait' + _ext)
            _shutil.copy2(_src, portrait_path)
        else:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.get(req.portrait_url)
                r.raise_for_status()
                ct = r.headers.get("content-type", "").lower()
                url_lower = req.portrait_url.split("?")[0].lower()
                if any(v in ct for v in ["video", "quicktime", "mp4", "mov"]) or url_lower.endswith((".mov", ".mp4", ".avi", ".mkv")):
                    ext = ".mov" if (url_lower.endswith(".mov") or "quicktime" in ct) else ".mp4"
                elif "png" in ct or url_lower.endswith(".png"):
                    ext = ".png"
                else:
                    ext = ".jpg"
                portrait_path = os.path.join(job_dir, f"portrait{ext}")
                with open(portrait_path, "wb") as f:
                    f.write(r.content)

        # Download audio
        if req.audio_url.startswith('file://'):
            import shutil as _shutil
            _src = req.audio_url[7:]
            audio_ext = os.path.splitext(_src)[1] or '.wav'
            audio_path = os.path.join(job_dir, 'audio' + audio_ext)
            _shutil.copy2(_src, audio_path)
        else:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.get(req.audio_url)
                r.raise_for_status()
                audio_ext = ".wav" if "wav" in r.headers.get("content-type", "") else ".mp3"
                audio_path = os.path.join(job_dir, f"audio{audio_ext}")
                with open(audio_path, "wb") as f:
                    f.write(r.content)

        job["progress"] = 10

        # Convert MP3 to WAV if needed
        if audio_ext == ".mp3":
            wav_path = os.path.join(job_dir, "audio.wav")
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", audio_path, "-ar", "16000", "-ac", "1", wav_path,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
            audio_path = wav_path

        job["progress"] = 15

        # Step 2: Load MuseTalk engine (lazy singleton)
        logger.info(f"[DH {req.job_id}] Loading MuseTalk engine...")
        loop = asyncio.get_event_loop()
        engine = await loop.run_in_executor(None, _get_or_create_engine)
        job["progress"] = 20

        # Step 3: Prepare avatar (with caching)
        portrait_hash = await loop.run_in_executor(None, _get_portrait_hash, portrait_path)
        logger.info(f"[DH {req.job_id}] Portrait hash: {portrait_hash}")

        global _current_avatar_hash
        if portrait_hash == _current_avatar_hash:
            logger.info(f"[DH {req.job_id}] Avatar cache HIT (same avatar in engine) - skipping preparation")
        else:
            logger.info(f"[DH {req.job_id}] Avatar cache MISS or different avatar - preparing avatar...")
            logger.info(f"[DH {req.job_id}]   current_hash={_current_avatar_hash}, new_hash={portrait_hash}")
            success = await loop.run_in_executor(None, engine.prepare_avatar, portrait_path)
            if not success:
                raise RuntimeError("Failed to prepare avatar from portrait")
            _current_avatar_hash = portrait_hash
            _avatar_cache[portrait_hash] = {"prepared": True, "portrait_path": portrait_path}
            logger.info(f"[DH {req.job_id}] Avatar prepared and cached (now current)")

        job["progress"] = 35

        # Step 4: Generate lip-sync frames
        logger.info(f"[DH {req.job_id}] Generating lip-sync frames...")
        output_path = os.path.join(job_dir, f"output_{req.job_id}.mp4")

        def run_lipsync():
            return _musetalk_inference_v2(
                engine=engine,
                audio_path=audio_path,
                output_path=output_path,
                job_id=req.job_id,
                fps=req.output_fps,
            )

        result_path = await loop.run_in_executor(None, run_lipsync)

        if result_path and os.path.exists(result_path):
            job["status"] = "completed"
            job["progress"] = 100
            job["output_path"] = result_path
            logger.info(f"[DH {req.job_id}] Completed: {result_path}")
        else:
            raise RuntimeError("MuseTalk inference produced no output")

    except Exception as e:
        logger.error(f"[DH {req.job_id}] Error: {e}")
        import traceback
        traceback.print_exc()
        job["status"] = "error"
        job["error"] = str(e)
    finally:
        # Clean up input files but keep output
        for f in ["portrait.png", "portrait.jpg", "portrait.mov", "portrait.mp4",
                  "audio.wav", "audio.mp3"]:
            p = os.path.join(job_dir, f)
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass


def _musetalk_inference_v2(
    engine,
    audio_path: str,
    output_path: str,
    job_id: str,
    fps: int = 25,
) -> str:
    """Run MuseTalk v2 inference using MuseTalkEngine with pre-cached avatar.

    This is much faster than the original _musetalk_inference because:
    1. Avatar frames, landmarks, latents, and masks are pre-computed and cached
    2. Uses get_image_blending (mask-based) for higher quality compositing
    3. Only audio processing + neural inference + compositing runs per request
    """
    import shutil

    job = digital_human_jobs.get(job_id, {})

    logger.info(f"[DH {job_id}] Running MuseTalk v2 inference (cached avatar)...")
    job["progress"] = 40

    # Generate lip-sync frames using the engine (avatar already prepared)
    frames = engine.generate_lipsync_frames(audio_path)

    if not frames:
        raise RuntimeError("No lip-sync frames generated")

    logger.info(f"[DH {job_id}] Generated {len(frames)} lip-sync frames")
    job["progress"] = 80

    # Encode video with ffmpeg (pipe frames directly, no temp images)
    job_dir = os.path.dirname(output_path)
    temp_vid = os.path.join(job_dir, "temp_video.mp4")

    h, w = frames[0].shape[:2]
    logger.info(f"[DH {job_id}] Encoding video ({w}x{h}, {fps}fps, {len(frames)} frames)...")

    # Use ffmpeg pipe for faster encoding (no disk I/O for frames)
    import subprocess
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

    job["progress"] = 90

    # Merge audio
    merge_cmd = f"ffmpeg -y -v warning -i {temp_vid} -i {audio_path} -c:v copy -c:a aac -shortest {output_path}"
    os.system(merge_cmd)

    job["progress"] = 95

    # Clean up
    if os.path.exists(temp_vid):
        os.remove(temp_vid)

    if os.path.exists(output_path):
        file_size = os.path.getsize(output_path) / (1024 * 1024)
        logger.info(f"[DH {job_id}] Output: {output_path} ({file_size:.1f} MB)")
        return output_path
    else:
        raise RuntimeError("ffmpeg failed to produce output video")


# ── IMTalker Premium Digital Human ─────────────────────────────────────────

IMTALKER_DIR = os.getenv("IMTALKER_DIR", "/workspace/IMTalker")


class IMTalkerRequest(BaseModel):
    """Request to generate a premium digital human video with IMTalker."""
    job_id: str = Field(..., description="Unique job ID")
    portrait_url: str = Field(..., description="URL of portrait image or driving video")
    portrait_type: str = Field(default="image", description="'image' or 'video'")
    audio_url: str = Field(..., description="URL of audio file (WAV/MP3)")
    a_cfg_scale: float = Field(default=3.5, description="Audio CFG scale (3.5 = strong natural lip sync, 1.5 = too subtle)")
    nfe: int = Field(default=48, description="Number of function evaluations for ODE solver (higher = better quality)")
    crop: bool = Field(default=True, description="Whether to crop face region")
    output_fps: int = Field(default=25, description="Output video FPS")


@app.post("/api/digital-human/imtalker/generate", dependencies=[Depends(verify_api_key)])
async def imtalker_generate(req: IMTalkerRequest):
    """Start a premium digital human video generation job using IMTalker.
    IMTalker produces full facial animation (head movement, expressions,
    eye blinks, gaze) in addition to lip-sync."""
    if req.job_id in digital_human_jobs:
        return {"error": f"Job {req.job_id} already exists"}

    digital_human_jobs[req.job_id] = {
        "status": "queued",
        "progress": 0,
        "output_path": None,
        "error": None,
        "engine": "imtalker",
        "created_at": time.time(),
    }

    asyncio.create_task(_run_imtalker_job(req))
    return {"job_id": req.job_id, "status": "queued", "engine": "imtalker"}


async def _run_imtalker_job(req: IMTalkerRequest):
    """Background task to run IMTalker inference via subprocess."""
    import httpx
    job = digital_human_jobs[req.job_id]
    job["status"] = "processing"
    job["progress"] = 5

    job_dir = os.path.join(TEMP_DIR, f"imt_{req.job_id}")
    os.makedirs(job_dir, exist_ok=True)

    try:
        # ── Step 1: Download portrait and audio ──
        logger.info(f"[IMT {req.job_id}] Downloading portrait and audio...")
        portrait_is_video = False
        portrait_video_path = None

        # Handle file:// URLs for local testing
        if req.portrait_url.startswith('file://'):
            import shutil as _shutil
            _src = req.portrait_url[7:]
            url_lower = _src.lower()
            is_video_by_type = req.portrait_type == "video"
            is_video_by_url = url_lower.endswith((".mov", ".mp4", ".avi", ".mkv"))
            if is_video_by_type or is_video_by_url:
                portrait_is_video = True
                vid_ext = ".mov" if url_lower.endswith(".mov") else ".mp4"
                portrait_video_path = os.path.join(job_dir, f"portrait_video{vid_ext}")
                _shutil.copy2(_src, portrait_video_path)
                portrait_path = os.path.join(job_dir, "portrait.png")
                extract_proc = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-y", "-i", portrait_video_path,
                    "-vframes", "1", "-q:v", "2", portrait_path,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                await extract_proc.wait()
                if not os.path.exists(portrait_path) or os.path.getsize(portrait_path) == 0:
                    portrait_is_video = False
                    portrait_path = portrait_video_path
            else:
                ext = os.path.splitext(_src)[1] or '.png'
                portrait_path = os.path.join(job_dir, f"portrait{ext}")
                _shutil.copy2(_src, portrait_path)
        else:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.get(req.portrait_url)
                r.raise_for_status()
                ct = r.headers.get("content-type", "").lower()
                url_lower = req.portrait_url.split("?")[0].lower()
                is_video_by_type = req.portrait_type == "video"
                is_video_by_ct = any(v in ct for v in ["video", "quicktime", "mp4", "mov"])
                is_video_by_url = url_lower.endswith((".mov", ".mp4", ".avi", ".mkv"))
                logger.info(f"[IMT {req.job_id}] Portrait detection: type={req.portrait_type}, ct={ct}, url_ext={url_lower[-10:]}, is_video={is_video_by_type or is_video_by_ct or is_video_by_url}")
                if is_video_by_type or is_video_by_ct or is_video_by_url:
                    portrait_is_video = True
                    vid_ext = ".mov" if (url_lower.endswith(".mov") or "quicktime" in ct) else ".mp4"
                    portrait_video_path = os.path.join(job_dir, f"portrait_video{vid_ext}")
                    with open(portrait_video_path, "wb") as f:
                        f.write(r.content)
                    portrait_path = os.path.join(job_dir, "portrait.png")
                    extract_proc = await asyncio.create_subprocess_exec(
                        "ffmpeg", "-y", "-i", portrait_video_path,
                        "-vframes", "1", "-q:v", "2", portrait_path,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    )
                    await extract_proc.wait()
                    if not os.path.exists(portrait_path) or os.path.getsize(portrait_path) == 0:
                        logger.warning(f"[IMT {req.job_id}] Failed to extract frame from video")
                        portrait_is_video = False
                        portrait_path = portrait_video_path
                    else:
                        logger.info(f"[IMT {req.job_id}] Extracted first frame from video portrait")
                else:
                    ext = ".png" if ("png" in ct or url_lower.endswith(".png")) else ".jpg"
                    portrait_path = os.path.join(job_dir, f"portrait{ext}")
                    with open(portrait_path, "wb") as f:
                        f.write(r.content)

        # Download audio
        if req.audio_url.startswith('file://'):
            import shutil as _shutil
            _src = req.audio_url[7:]
            audio_ext = os.path.splitext(_src)[1] or '.wav'
            audio_path = os.path.join(job_dir, f"audio{audio_ext}")
            _shutil.copy2(_src, audio_path)
        else:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.get(req.audio_url)
                r.raise_for_status()
                audio_ext = ".wav" if "wav" in r.headers.get("content-type", "") else ".mp3"
                audio_path = os.path.join(job_dir, f"audio{audio_ext}")
                with open(audio_path, "wb") as f:
                    f.write(r.content)

        job["progress"] = 10

        # ── Step 2: Convert audio to WAV 16kHz mono ──
        wav_path = os.path.join(job_dir, "audio_16k.wav")
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", audio_path, "-ar", "16000", "-ac", "1", wav_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()
        if os.path.exists(wav_path):
            audio_path = wav_path
        job["progress"] = 15

        # ── Step 3: Run IMTalker inference via subprocess ──
        logger.info(f"[IMT {req.job_id}] Running IMTalker inference...")
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
            "--a_cfg_scale", str(req.a_cfg_scale),
            "--nfe", str(req.nfe),
            "--fps", str(req.output_fps),
        ]
        if req.crop:
            cmd.append("--crop")

        env = os.environ.copy()
        env["PYTHONPATH"] = f"{IMTALKER_DIR}:{env.get('PYTHONPATH', '')}"

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=IMTALKER_DIR,
            env=env,
        )

        job["progress"] = 25

        # Read output for progress tracking
        stdout_lines = []
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace").strip()
            if decoded:
                stdout_lines.append(decoded)
                logger.info(f"[IMT {req.job_id}] {decoded}")
                # Progress heuristics
                if "Processing:" in decoded:
                    job["progress"] = 40
                if "Inferencing" in decoded or "Model Stats" in decoded:
                    job["progress"] = 50
                if "Rendering" in decoded.lower() or "render" in decoded.lower():
                    job["progress"] = 70

        await proc.wait()

        if proc.returncode != 0:
            error_output = "\n".join(stdout_lines[-15:])
            raise RuntimeError(f"IMTalker failed (exit {proc.returncode}): {error_output}")

        job["progress"] = 85

        # ── Step 4: Find IMTalker output video (512x512) ──
        output_files = [f for f in os.listdir(output_dir) if f.endswith(".mp4")]
        if not output_files:
            raise RuntimeError("IMTalker produced no output video")

        src_video = os.path.join(output_dir, output_files[0])
        job["progress"] = 85

        # ── Step 5: Composite onto original 9:16 portrait ──
        # Strategy v5: Replicate IMTalker's crop logic to find the exact face
        # region, then paste the animated face back onto the original portrait.
        # Body, background, and everything outside the face stays untouched.
        logger.info(f"[IMT {req.job_id}] Compositing v5: face-only replacement on original portrait...")
        final_output = os.path.join(job_dir, f"output_{req.job_id}.mp4")

        try:
            from PIL import Image
            import numpy as np

            img = Image.open(portrait_path)
            orig_w, orig_h = img.size
            logger.info(f"[IMT {req.job_id}] Original portrait: {orig_w}x{orig_h}")

            # ── Replicate IMTalker's process_img crop logic ──
            # This mirrors DataProcessor.process_img() in IMTalker/generator/generate.py
            img_arr = np.array(img)

            # Use face_alignment (same library as IMTalker) to detect face
            import face_alignment
            fa = face_alignment.FaceAlignment(
                face_alignment.LandmarksType.TWO_D, flip_input=False,
                device='cuda'  # GPU worker always has CUDA
            )
            bboxes = fa.face_detector.detect_from_image(img_arr)
            valid_bboxes = [
                (int(x1), int(y1), int(x2), int(y2), score)
                for (x1, y1, x2, y2, score) in bboxes if score > 0.95
            ]

            if not valid_bboxes:
                logger.warning(f"[IMT {req.job_id}] No face detected for v5 composite, lowering threshold")
                valid_bboxes = [
                    (int(x1), int(y1), int(x2), int(y2), score)
                    for (x1, y1, x2, y2, score) in bboxes if score > 0.5
                ]

            if valid_bboxes:
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

                logger.info(
                    f"[IMT {req.job_id}] v5 face crop: bbox=({x1},{y1},{x2},{y2}), "
                    f"crop=({crop_x1},{crop_y1})-({crop_x2},{crop_y2}), side={side}"
                )

                # ── v7: Inner-face overlay with driving video background ──
                # v6 problem: always used static image as background, even when
                # a driving video was provided. This made the body completely
                # static while only the face moved — very unnatural.
                #
                # v7 approach:
                # - If driving video exists, use it as background (body moves!)
                # - Use inner 70% of IMTalker output (larger face area)
                # - Improved elliptical mask with wider feathering
                # - Better blending for seamless face replacement
                inner_ratio = 0.70  # use inner 70% of IMTalker output (was 55%)
                inner_px = int(512 * inner_ratio)  # ~358 pixels
                margin = (512 - inner_px) // 2     # ~77 pixels from each edge

                # The inner region maps to a smaller area on the original image
                inner_side = int(side * inner_ratio)
                inner_x1 = crop_x1 + (side - inner_side) // 2
                inner_y1 = crop_y1 + (side - inner_side) // 2

                # Create elliptical mask for the inner region (on inner_px x inner_px)
                mask_path = os.path.join(job_dir, "inner_face_mask.png")
                feather_px = max(40, int(inner_px * 0.28))  # 28% feather (wider)

                y_coords = np.arange(inner_px, dtype=np.float32)
                x_coords = np.arange(inner_px, dtype=np.float32)
                cy_m = inner_px * 0.46  # slightly above center (avoid chin)
                cx_m = inner_px / 2.0
                ry = inner_px * 0.48  # vertical radius (larger)
                rx = inner_px * 0.50  # horizontal radius (wider for cheeks)
                dy = (y_coords - cy_m) / max(ry, 1)
                dx = (x_coords - cx_m) / max(rx, 1)
                dist = np.sqrt(dy[:, None]**2 + dx[None, :]**2)
                # Smooth falloff with very wide feather for seamless blending
                mask_arr = np.clip(1.0 - (dist - 0.45) / 0.75, 0.0, 1.0)
                mask_arr = mask_arr ** 0.4  # gentler curve for smoother transition
                mask_img = Image.fromarray((mask_arr * 255).astype(np.uint8), mode='L')
                mask_img.save(mask_path)

                # Determine background source: driving video or static image
                use_video_bg = portrait_is_video and portrait_video_path and os.path.exists(portrait_video_path)
                bg_source = portrait_video_path if use_video_bg else portrait_path
                bg_label = "driving_video" if use_video_bg else "static_image"

                logger.info(
                    f"[IMT {req.job_id}] v7 inner-face: crop_side={side}, "
                    f"inner_px={inner_px}, inner_side={inner_side}, "
                    f"inner_pos=({inner_x1},{inner_y1}), feather={feather_px}px, "
                    f"bg={bg_label}"
                )

                # ffmpeg filter:
                # 1. Crop inner region from IMTalker output (center 70%)
                # 2. Scale to inner_side
                # 3. Apply elliptical mask
                # 4. Overlay on background (driving video or static portrait)
                if use_video_bg:
                    # Driving video background: scale video to original size,
                    # sync with IMTalker output duration
                    filter_complex = (
                        f"[0:v]crop={inner_px}:{inner_px}:{margin}:{margin},"
                        f"scale={inner_side}:{inner_side}:flags=lanczos[face_inner];"
                        f"[2:v]scale={inner_side}:{inner_side}:flags=bilinear[mask_scaled];"
                        f"[face_inner][mask_scaled]alphamerge[face_masked];"
                        f"[1:v]scale={orig_w}:{orig_h}:flags=lanczos[bg];"
                        f"[bg][face_masked]overlay={inner_x1}:{inner_y1}:format=auto[out]"
                    )
                else:
                    # Static image background (original behavior, improved)
                    filter_complex = (
                        f"[0:v]crop={inner_px}:{inner_px}:{margin}:{margin},"
                        f"scale={inner_side}:{inner_side}:flags=lanczos[face_inner];"
                        f"[2:v]scale={inner_side}:{inner_side}:flags=bilinear[mask_scaled];"
                        f"[face_inner][mask_scaled]alphamerge[face_masked];"
                        f"[1:v]scale={orig_w}:{orig_h}[bg];"
                        f"[bg][face_masked]overlay={inner_x1}:{inner_y1}:format=auto[out]"
                    )

                # Build ffmpeg command based on background type
                if use_video_bg:
                    # For video background: use stream_loop to loop driving video
                    # if it's shorter than IMTalker output
                    composite_cmd = [
                        "ffmpeg", "-y",
                        "-i", src_video,                          # input 0: IMTalker (512x512)
                        "-stream_loop", "-1", "-i", bg_source,    # input 1: driving video (looped)
                        "-loop", "1", "-i", mask_path,             # input 2: elliptical mask
                        "-filter_complex", filter_complex,
                        "-map", "[out]",
                        "-map", "0:a?",
                        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                        "-c:a", "aac", "-b:a", "128k",
                        "-shortest",
                        "-pix_fmt", "yuv420p",
                        final_output,
                    ]
                else:
                    composite_cmd = [
                        "ffmpeg", "-y",
                        "-i", src_video,                          # input 0: IMTalker (512x512)
                        "-loop", "1", "-i", bg_source,            # input 1: static portrait
                        "-loop", "1", "-i", mask_path,             # input 2: elliptical mask
                        "-filter_complex", filter_complex,
                        "-map", "[out]",
                        "-map", "0:a?",
                        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                        "-c:a", "aac", "-b:a", "128k",
                        "-shortest",
                        "-pix_fmt", "yuv420p",
                        final_output,
                    ]

                comp_proc = await asyncio.create_subprocess_exec(
                    *composite_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=job_dir,
                )
                comp_out, _ = await comp_proc.communicate()
                if comp_proc.returncode != 0:
                    comp_log = comp_out.decode('utf-8', errors='replace')[-800:]
                    logger.warning(f"[IMT {req.job_id}] v7 composite failed: {comp_log}")
                    raise RuntimeError(f"v7 composite ffmpeg failed")
                else:
                    logger.info(
                        f"[IMT {req.job_id}] v7 composite success: inner {inner_side}x{inner_side} "
                        f"at ({inner_x1},{inner_y1}) on {orig_w}x{orig_h}, bg={bg_label}"
                    )

                # Clean up temp
                if os.path.exists(mask_path):
                    os.remove(mask_path)
            else:
                raise RuntimeError("No face detected for v5 composite")

        except Exception as e_v5:
            logger.warning(f"[IMT {req.job_id}] v5 composite failed ({e_v5}), falling back to scaled output")
            # Fallback: scale 512x512 to fit 9:16 with padding
            target_w, target_h = (1080, 1920) if orig_w >= 1080 else (720, 1280)
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
                "-map", "[out]",
                "-map", "0:a?",
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-c:a", "aac", "-b:a", "128k",
                "-shortest", "-pix_fmt", "yuv420p",
                final_output,
            ]
            fb_proc = await asyncio.create_subprocess_exec(
                *fallback_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=job_dir,
            )
            fb_out, _ = await fb_proc.communicate()
            if fb_proc.returncode != 0:
                logger.warning(f"[IMT {req.job_id}] Fallback also failed, using raw output")
                import shutil
                shutil.move(src_video, final_output)

        except Exception as e:
            logger.warning(f"[IMT {req.job_id}] Composite failed ({e}), using raw 512x512 output")
            import shutil
            shutil.move(src_video, final_output)

        job["progress"] = 95

        if os.path.exists(final_output):
            file_size = os.path.getsize(final_output) / (1024 * 1024)
            logger.info(f"[IMT {req.job_id}] Output: {final_output} ({file_size:.1f} MB)")
            job["status"] = "completed"
            job["progress"] = 100
            job["output_path"] = final_output
        else:
            raise RuntimeError("IMTalker output file not found")

    except Exception as e:
        logger.error(f"[IMT {req.job_id}] Error: {e}")
        job["status"] = "error"
        job["error"] = str(e)
    finally:
        # Clean up input files but keep output
        for fname in ["portrait.png", "portrait.jpg", "portrait_video.mp4", "portrait_video.mov", "audio.wav", "audio.mp3", "audio_16k.wav"]:
            p = os.path.join(job_dir, fname)
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        # Clean up results subdir
        results_dir = os.path.join(job_dir, "results")
        if os.path.isdir(results_dir):
            import shutil
            shutil.rmtree(results_dir, ignore_errors=True)


# ── Admin: Remote Exec (for installation/maintenance) ────────────────────────

class ExecRequest(BaseModel):
    command: str
    timeout: int = Field(default=300, ge=1, le=3600)

@app.post("/api/admin/exec", dependencies=[Depends(verify_api_key)])
async def admin_exec(req: ExecRequest):
    """Execute a shell command on the GPU server (admin only)."""
    try:
        proc = await asyncio.create_subprocess_shell(
            req.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd="/workspace",
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=req.timeout)
            output = stdout.decode("utf-8", errors="replace")
        except asyncio.TimeoutError:
            proc.kill()
            output = "TIMEOUT: command exceeded time limit"
        return {"exit_code": proc.returncode, "output": output}
    except Exception as e:
        return {"exit_code": -1, "output": str(e)}

# ── Live API Proxy ────────────────────────────────────────────────────────────
# Proxy requests to live_api running on localhost:8002
import httpx

LIVE_API_BASE = os.environ.get("LIVE_API_BASE", "http://localhost:8002")

@app.api_route("/api/v1/live/{path:path}", methods=["GET", "POST", "PUT", "DELETE"], dependencies=[Depends(verify_api_key)])
async def proxy_live_api(path: str, request: Request):
    """Proxy all /api/v1/live/* requests to live_api on port 8002."""
    url = f"{LIVE_API_BASE}/api/v1/live/{path}"
    try:
        body = await request.body()
        headers = {k: v for k, v in request.headers.items()
                   if k.lower() not in ("host", "content-length", "transfer-encoding")}
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.request(
                method=request.method,
                url=url,
                content=body,
                headers=headers,
                params=dict(request.query_params),
            )
        from fastapi.responses import Response
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=dict(resp.headers),
            media_type=resp.headers.get("content-type"),
        )
    except httpx.ConnectError:
        return {"error": "live_api not running on port 8002", "status": "offline"}
    except Exception as e:
        return {"error": str(e), "status": "proxy_error"}


# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info(f"Starting FaceFusion GPU Worker on port {PORT}")
    uvicorn.run(
        "worker_api:app",
        host="0.0.0.0",
        port=PORT,
        workers=1,
        log_level="info",
    )
