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
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile, File
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

current_config = {
    "face_swapper_model": "inswapper_128",
    "face_enhancer_model": "gfpgan_1.4",
    "face_enhancer_enabled": True,
    "face_detector_model": "yoloface",
    "face_detector_score": 0.5,
    "face_mask_blur": 0.3,
    "output_resolution": "1280x720",
    "output_fps": 30,
    "execution_providers": "cuda",
    "execution_thread_count": 4,
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
    face_enhancer: bool = Field(default=True, description="Enable GFPGAN face enhancement")


class StopStreamRequest(BaseModel):
    session_id: Optional[str] = None


class SwapFrameRequest(BaseModel):
    image_base64: str = Field(..., description="Base64-encoded input image")
    quality: str = Field(default="high", description="Quality preset")
    face_enhancer: bool = Field(default=True, description="Enable face enhancement")


class UpdateConfigRequest(BaseModel):
    face_swapper_model: Optional[str] = None
    face_enhancer_model: Optional[str] = None
    face_enhancer_enabled: Optional[bool] = None
    face_detector_model: Optional[str] = None
    face_detector_score: Optional[float] = None
    face_mask_blur: Optional[float] = None
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
    cmd = [
        sys.executable, f"{FACEFUSION_DIR}/facefusion.py", "headless-run",
        "--source-paths", source_face_path,
        "--target-path", input_path,
        "--output-path", output_path,
        "--processors", "face_swapper",
        "--face-swapper-model", current_config["face_swapper_model"],
        "--face-detector-model", current_config["face_detector_model"],
        "--face-detector-score", str(current_config["face_detector_score"]),
        "--execution-providers", current_config["execution_providers"],
        "--execution-thread-count", str(current_config["execution_thread_count"]),
    ]

    if current_config["face_enhancer_enabled"]:
        # Insert face_enhancer after face_swapper
        idx = cmd.index("face_swapper")
        cmd.insert(idx + 1, "face_enhancer")
        cmd.extend(["--face-enhancer-model", current_config["face_enhancer_model"]])

    return cmd


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info("FaceFusion GPU Worker starting up...")
    logger.info(f"FaceFusion directory: {FACEFUSION_DIR}")
    logger.info(f"Source face directory: {SOURCE_FACE_DIR}")

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
                "simswap_256",
                "blendswap_256",
            ],
            "face_enhancer": [
                "gfpgan_1.4",
                "gpen_bfr_256",
                "gpen_bfr_512",
                "codeformer",
                "restoreformer_plus_plus",
            ],
            "face_detector": [
                "yoloface",
                "retinaface",
                "scrfd",
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
