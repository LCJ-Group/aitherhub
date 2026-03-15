"""
Face Swap Service for AitherHub — Mode B: Real Face Livestream

This module manages communication with a remote FaceFusion GPU worker
to provide real-time face swapping for livestreams. Combined with
ElevenLabs voice cloning (already implemented), this enables a "body
double" to livestream with an influencer's face and voice.

Architecture:
  ┌──────────────┐    ┌──────────────────────┐    ┌────────────────┐
  │  Body Double  │    │  FaceFusion GPU       │    │  Streaming     │
  │  (camera +    │───▶│  Worker               │───▶│  Platform      │
  │   products)   │    │  (face swap + RTMP)   │    │  (viewers)     │
  └──────────────┘    └──────────────────────┘    └────────────────┘
                              ▲
                       ┌──────┴──────┐
                       │ Source Face  │
                       │ (influencer │
                       │  photo)     │
                       └─────────────┘

  ┌──────────────┐    ┌──────────────────────┐
  │  Script Text  │───▶│  ElevenLabs TTS      │───▶ Audio output
  │  (AitherHub)  │    │  (voice clone)       │    (cloned voice)
  └──────────────┘    └──────────────────────┘

The GPU worker runs FaceFusion in headless mode inside a Docker container
on a cloud GPU server (e.g., Vast.ai, RunPod). It exposes a simple HTTP
API for AitherHub to control the face swap pipeline.

GPU Worker API (expected endpoints on the worker):
  POST /api/health          → Health check
  POST /api/set-source      → Upload source face image
  POST /api/start-stream    → Start RTMP face-swap stream
  POST /api/stop-stream     → Stop the stream
  GET  /api/stream-status   → Get current stream status
  POST /api/swap-frame      → Swap face in a single frame (for testing)

Reference:
  - FaceFusion: https://docs.facefusion.io/
  - FaceFusion CLI headless: python facefusion.py headless-run
  - FaceFusion Docker: https://docs.facefusion.io/usage/run-with-docker
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from enum import Enum
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

# GPU Worker connection settings
FACE_SWAP_WORKER_URL = os.getenv("FACE_SWAP_WORKER_URL", "")
FACE_SWAP_WORKER_API_KEY = os.getenv("FACE_SWAP_WORKER_API_KEY", "")

# Timeouts
WORKER_CONNECT_TIMEOUT = float(os.getenv("FACE_SWAP_CONNECT_TIMEOUT", "10"))
WORKER_READ_TIMEOUT = float(os.getenv("FACE_SWAP_READ_TIMEOUT", "120"))

# Stream defaults
DEFAULT_OUTPUT_RESOLUTION = os.getenv("FACE_SWAP_RESOLUTION", "720p")
DEFAULT_OUTPUT_FPS = int(os.getenv("FACE_SWAP_FPS", "30"))


# ──────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────

class StreamStatus(str, Enum):
    """Status of the face swap stream on the GPU worker."""
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


class FaceSwapQuality(str, Enum):
    """Quality presets for face swap processing."""
    FAST = "fast"           # Lower quality, higher FPS (~60fps)
    BALANCED = "balanced"   # Good quality, good FPS (~30fps)
    HIGH = "high"           # Best quality, lower FPS (~15-20fps)


# ──────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────

class FaceSwapError(Exception):
    """Base exception for face swap operations."""
    pass


class WorkerConnectionError(FaceSwapError):
    """Cannot connect to the GPU worker."""
    pass


class WorkerAPIError(FaceSwapError):
    """GPU worker returned an error response."""
    def __init__(self, message: str, status_code: int = 0, detail: str = ""):
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)


# ──────────────────────────────────────────────
# Face Swap Service
# ──────────────────────────────────────────────

class FaceSwapService:
    """
    Manages communication with a remote FaceFusion GPU worker for
    real-time face swapping in livestreams.

    The GPU worker is a separate server running FaceFusion with a
    lightweight HTTP API wrapper. This service handles:
      - Source face management (upload/change the influencer's face)
      - Stream lifecycle (start/stop RTMP face-swap streams)
      - Health monitoring
      - Single-frame testing

    Usage:
        service = FaceSwapService(
            worker_url="http://gpu-worker:8000",
            api_key="secret",
        )

        # Set the source face (influencer's photo)
        await service.set_source_face(image_url="https://...")

        # Start face-swap stream
        await service.start_stream(
            input_rtmp="rtmp://input-server/live/body-double",
            output_rtmp="rtmp://platform/live/stream-key",
            quality="balanced",
        )

        # Check status
        status = await service.get_stream_status()

        # Stop stream
        await service.stop_stream()
    """

    def __init__(
        self,
        worker_url: Optional[str] = None,
        api_key: Optional[str] = None,
        connect_timeout: float = WORKER_CONNECT_TIMEOUT,
        read_timeout: float = WORKER_READ_TIMEOUT,
    ):
        self.worker_url = (worker_url or FACE_SWAP_WORKER_URL).rstrip("/")
        self.api_key = api_key or FACE_SWAP_WORKER_API_KEY
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout

        if not self.worker_url:
            logger.warning(
                "FACE_SWAP_WORKER_URL not configured. "
                "Face swap features will be unavailable."
            )

    # ──────────────────────────────────────────
    # Internal HTTP Client
    # ──────────────────────────────────────────

    def _build_headers(self) -> Dict[str, str]:
        """Build request headers with authentication."""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        json_data: Optional[Dict] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Send an HTTP request to the GPU worker.

        Raises:
            WorkerConnectionError: If the worker is unreachable
            WorkerAPIError: If the worker returns an error
        """
        if not self.worker_url:
            raise WorkerConnectionError(
                "GPU worker URL not configured. "
                "Set FACE_SWAP_WORKER_URL environment variable."
            )

        url = f"{self.worker_url}{path}"
        read_timeout = timeout or self.read_timeout

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=self.connect_timeout,
                    read=read_timeout,
                    write=30.0,
                    pool=10.0,
                )
            ) as client:
                response = await client.request(
                    method=method,
                    url=url,
                    json=json_data,
                    headers=self._build_headers(),
                )

                if response.status_code >= 400:
                    detail = ""
                    try:
                        detail = response.json().get("detail", response.text)
                    except Exception:
                        detail = response.text[:500]

                    raise WorkerAPIError(
                        f"GPU worker error: {response.status_code} on {path}",
                        status_code=response.status_code,
                        detail=str(detail),
                    )

                return response.json()

        except httpx.ConnectError as e:
            raise WorkerConnectionError(
                f"Cannot connect to GPU worker at {self.worker_url}: {e}"
            ) from e
        except httpx.TimeoutException as e:
            raise WorkerConnectionError(
                f"Timeout connecting to GPU worker at {self.worker_url}: {e}"
            ) from e
        except (WorkerConnectionError, WorkerAPIError):
            raise
        except Exception as e:
            raise FaceSwapError(
                f"Unexpected error communicating with GPU worker: {e}"
            ) from e

    # ──────────────────────────────────────────
    # Source Face Management
    # ──────────────────────────────────────────

    async def set_source_face(
        self,
        image_url: Optional[str] = None,
        image_base64: Optional[str] = None,
        face_index: int = 0,
    ) -> Dict[str, Any]:
        """
        Set the source face image for face swapping.

        The source face is the face that will replace the body double's
        face in the stream. This is typically the influencer's photo.

        Args:
            image_url: URL of the source face image (publicly accessible)
            image_base64: Base64-encoded image data (alternative to URL)
            face_index: If the image contains multiple faces, which one
                       to use (0-indexed, default: 0 = largest face)

        Returns:
            Dict with face detection results (landmarks, bbox, etc.)
        """
        if not image_url and not image_base64:
            raise FaceSwapError("Either image_url or image_base64 is required")

        payload = {"face_index": face_index}
        if image_url:
            payload["image_url"] = image_url
        if image_base64:
            payload["image_base64"] = image_base64

        logger.info(
            f"Setting source face: url={bool(image_url)}, "
            f"base64={bool(image_base64)}, face_index={face_index}"
        )

        result = await self._request("POST", "/api/set-source", json_data=payload)
        logger.info(f"Source face set: {result.get('status', 'unknown')}")
        return result

    # ──────────────────────────────────────────
    # Stream Lifecycle
    # ──────────────────────────────────────────

    async def start_stream(
        self,
        input_rtmp: str,
        output_rtmp: str,
        quality: str = FaceSwapQuality.BALANCED,
        resolution: str = DEFAULT_OUTPUT_RESOLUTION,
        fps: int = DEFAULT_OUTPUT_FPS,
        face_enhancer: bool = True,
        face_mask_blur: float = 0.3,
    ) -> Dict[str, Any]:
        """
        Start the real-time face swap stream.

        The GPU worker will:
        1. Pull video from input_rtmp (body double's camera feed)
        2. Detect and swap faces using FaceFusion
        3. Push the result to output_rtmp (streaming platform)

        Args:
            input_rtmp: RTMP URL of the input stream (body double's feed)
            output_rtmp: RTMP URL of the output stream (platform ingest)
            quality: Quality preset (fast/balanced/high)
            resolution: Output resolution (480p/720p/1080p)
            fps: Output frame rate
            face_enhancer: Enable GFPGAN face enhancement for realism
            face_mask_blur: Face mask blur amount (0.0-1.0)

        Returns:
            Dict with stream session info
        """
        payload = {
            "input_rtmp": input_rtmp,
            "output_rtmp": output_rtmp,
            "quality": quality,
            "resolution": resolution,
            "fps": fps,
            "face_enhancer": face_enhancer,
            "face_mask_blur": face_mask_blur,
        }

        logger.info(
            f"Starting face swap stream: quality={quality}, "
            f"resolution={resolution}, fps={fps}"
        )

        result = await self._request(
            "POST", "/api/start-stream",
            json_data=payload,
            timeout=60.0,  # Starting stream may take longer
        )

        logger.info(f"Stream started: {result.get('session_id', 'unknown')}")
        return result

    async def stop_stream(
        self,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Stop the face swap stream.

        Args:
            session_id: Specific session to stop (if None, stops current)

        Returns:
            Dict with stop confirmation
        """
        payload = {}
        if session_id:
            payload["session_id"] = session_id

        logger.info(f"Stopping face swap stream: session={session_id or 'current'}")
        result = await self._request("POST", "/api/stop-stream", json_data=payload)
        logger.info("Stream stopped")
        return result

    async def get_stream_status(
        self,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get the current status of the face swap stream.

        Returns:
            Dict with:
              - status: StreamStatus enum value
              - fps: Current processing FPS
              - latency_ms: Current processing latency
              - uptime_seconds: How long the stream has been running
              - frames_processed: Total frames processed
              - errors: List of recent errors (if any)
        """
        params = ""
        if session_id:
            params = f"?session_id={session_id}"

        return await self._request(
            "GET", f"/api/stream-status{params}",
            timeout=10.0,
        )

    # ──────────────────────────────────────────
    # Single Frame Testing
    # ──────────────────────────────────────────

    async def swap_single_frame(
        self,
        frame_base64: str,
        quality: str = FaceSwapQuality.HIGH,
        face_enhancer: bool = True,
    ) -> Dict[str, Any]:
        """
        Swap face in a single frame (for testing / preview).

        Args:
            frame_base64: Base64-encoded input frame (JPEG/PNG)
            quality: Quality preset
            face_enhancer: Enable face enhancement

        Returns:
            Dict with:
              - output_base64: Base64-encoded output frame
              - processing_ms: Processing time in milliseconds
              - faces_detected: Number of faces detected
        """
        payload = {
            "frame_base64": frame_base64,
            "quality": quality,
            "face_enhancer": face_enhancer,
        }

        logger.info("Swapping single frame for testing")
        return await self._request(
            "POST", "/api/swap-frame",
            json_data=payload,
            timeout=30.0,
        )

    # ──────────────────────────────────────────
    # Health Check
    # ──────────────────────────────────────────

    async def health_check(self) -> Dict[str, Any]:
        """
        Check the health of the GPU worker.

        Returns:
            Dict with:
              - status: "ok" or "error"
              - gpu_name: GPU model name
              - gpu_memory_used_mb: GPU memory usage
              - gpu_memory_total_mb: Total GPU memory
              - facefusion_version: FaceFusion version
              - models_loaded: List of loaded models
              - stream_status: Current stream status
        """
        if not self.worker_url:
            return {
                "status": "not_configured",
                "error": "FACE_SWAP_WORKER_URL not set",
                "worker_url": "",
            }

        try:
            result = await self._request(
                "POST", "/api/health",
                timeout=10.0,
            )
            result["status"] = result.get("status", "ok")
            result["worker_url"] = self.worker_url[:30] + "..."
            return result

        except WorkerConnectionError as e:
            return {
                "status": "unreachable",
                "error": str(e),
                "worker_url": self.worker_url[:30] + "...",
            }
        except WorkerAPIError as e:
            return {
                "status": "error",
                "error": str(e),
                "status_code": e.status_code,
                "worker_url": self.worker_url[:30] + "...",
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "worker_url": self.worker_url[:30] + "...",
            }

    # ──────────────────────────────────────────
    # Worker Configuration
    # ──────────────────────────────────────────

    async def get_worker_config(self) -> Dict[str, Any]:
        """
        Get the current configuration of the GPU worker.

        Returns:
            Dict with worker configuration including:
              - available_models: List of face swap models
              - available_enhancers: List of face enhancer models
              - execution_provider: Current execution provider (cuda/tensorrt)
              - max_resolution: Maximum supported resolution
        """
        return await self._request("GET", "/api/config", timeout=10.0)

    async def update_worker_config(
        self,
        face_swap_model: Optional[str] = None,
        face_enhancer_model: Optional[str] = None,
        execution_provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update the GPU worker's configuration.

        Args:
            face_swap_model: Face swap model to use (e.g., "inswapper_128")
            face_enhancer_model: Face enhancer model (e.g., "gfpgan_1.4")
            execution_provider: Execution provider (cuda/tensorrt/cpu)

        Returns:
            Dict with updated configuration
        """
        payload = {}
        if face_swap_model:
            payload["face_swap_model"] = face_swap_model
        if face_enhancer_model:
            payload["face_enhancer_model"] = face_enhancer_model
        if execution_provider:
            payload["execution_provider"] = execution_provider

        return await self._request(
            "POST", "/api/config",
            json_data=payload,
            timeout=30.0,
        )

    # ──────────────────────────────────────────
    # Video Face Swap
    # ──────────────────────────────────────────

    async def start_video_swap(
        self,
        job_id: str,
        video_url: str,
        quality: str = "high",
        face_enhancer: bool = True,
        output_video_quality: int = 90,
    ) -> Dict[str, Any]:
        """
        Start an asynchronous video face swap job on the GPU worker.

        The GPU worker will download the video, process all frames with
        FaceFusion, and make the result available for download.

        Args:
            job_id: Unique job identifier
            video_url: URL to download the input video
            quality: Quality preset (fast, balanced, high)
            face_enhancer: Enable GFPGAN face enhancement
            output_video_quality: Output video quality 0-100

        Returns:
            Dict with job_id and poll_url
        """
        payload = {
            "job_id": job_id,
            "video_url": video_url,
            "quality": quality,
            "face_enhancer": face_enhancer,
            "output_video_quality": output_video_quality,
        }

        logger.info(f"Starting video swap job: {job_id}")
        return await self._request(
            "POST", "/api/swap-video",
            json_data=payload,
            timeout=30.0,
        )

    async def get_video_status(self, job_id: str) -> Dict[str, Any]:
        """
        Get the status and progress of a video face swap job.

        Returns:
            Dict with status, progress (0-100), step description, etc.
        """
        return await self._request(
            "GET", f"/api/video-status/{job_id}",
            timeout=10.0,
        )

    async def get_video_download_url(self, job_id: str) -> str:
        """
        Get the download URL for a completed video face swap job.
        Returns the full URL to stream the processed video.
        """
        return f"{self.worker_url}/api/video-download/{job_id}"

    async def delete_video_job(self, job_id: str) -> Dict[str, Any]:
        """
        Delete a video job and its output file from the GPU worker.
        """
        return await self._request(
            "DELETE", f"/api/video-job/{job_id}",
            timeout=10.0,
        )
