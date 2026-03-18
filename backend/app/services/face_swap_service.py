"""
Face Swap Service for AitherHub — Mode B: Real Face Livestream
This module manages communication with a remote FaceFusion GPU worker
to provide real-time face swapping for livestreams. Combined with
the body double approach, it enables influencers to appear on multiple
simultaneous livestreams using their own face on a stand-in's body.

Architecture:
  AitherHub Backend ←→ FaceFusion GPU Worker (RunPod)
                          ↓
  Body Double (RTMP in) → FaceFusion → RTMP out (to TikTok/YouTube/etc.)

Quality Presets (v7 optimised):
  fast     : hyperswap_1b_256, no enhancer, 512 boost     → ~15 fps
  balanced : hyperswap_1c_256, no enhancer, 512 boost     → ~10 fps
  high     : hyperswap_1c_256, no enhancer, 1024 boost    → ~9 fps
  ultra    : hyperswap_1c_256, GPEN-BFR-2048, 1024 boost  → ~2 fps

GPU Worker URL Resolution (priority order):
  1. Manual FACE_SWAP_WORKER_URL environment variable
  2. Auto-discovery via RunPod API (RUNPOD_API_KEY required)
  3. Disabled if neither is set
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

from app.services.runpod_discovery_service import get_runpod_discovery

logger = logging.getLogger(__name__)

# ── Environment Configuration ────────────────────────────────────────────────

FACE_SWAP_WORKER_URL = os.getenv("FACE_SWAP_WORKER_URL", "")
FACE_SWAP_WORKER_API_KEY = os.getenv("FACE_SWAP_WORKER_API_KEY", "change-me-in-production")
WORKER_CONNECT_TIMEOUT = float(os.getenv("FACE_SWAP_CONNECT_TIMEOUT", "10"))
WORKER_READ_TIMEOUT = float(os.getenv("FACE_SWAP_READ_TIMEOUT", "300"))
DEFAULT_OUTPUT_RESOLUTION = os.getenv("FACE_SWAP_RESOLUTION", "720p")
DEFAULT_OUTPUT_FPS = int(os.getenv("FACE_SWAP_FPS", "30"))


# ── Enums ────────────────────────────────────────────────────────────────────

class StreamStatus(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


class FaceSwapQuality(str, Enum):
    FAST = "fast"
    BALANCED = "balanced"
    HIGH = "high"
    ULTRA = "ultra"
    STANDARD = "standard"
    PRO = "pro"
    CINEMA = "cinema"


# ── Exceptions ───────────────────────────────────────────────────────────────

class FaceSwapError(Exception):
    """Base exception for face swap operations."""
    pass


class WorkerConnectionError(FaceSwapError):
    """Cannot reach the GPU worker."""
    pass


class WorkerAPIError(FaceSwapError):
    """GPU worker returned an error response."""
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Worker API error {status_code}: {detail}")


# ── Service ──────────────────────────────────────────────────────────────────

class FaceSwapService:
    """
    Client for the FaceFusion GPU Worker API.

    Now supports automatic GPU Worker URL discovery via RunPod API.
    If FACE_SWAP_WORKER_URL is not set, the service will automatically
    find the running GPU pod and construct the proxy URL.

    Usage:
        service = FaceSwapService()
        await service.health_check()
        await service.set_source_face(image_bytes=b"...", append=False)
        await service.start_stream(
            input_rtmp="rtmp://...",
            output_rtmp="rtmp://...",
            quality=FaceSwapQuality.HIGH,
        )
        status = await service.get_stream_status()
        await service.stop_stream()
    """

    def __init__(
        self,
        worker_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self._static_worker_url = (worker_url or FACE_SWAP_WORKER_URL).rstrip("/") or None
        self.api_key = api_key or FACE_SWAP_WORKER_API_KEY
        self._discovery = get_runpod_discovery()

        if not self._static_worker_url and not self._discovery.is_configured:
            logger.warning(
                "Neither FACE_SWAP_WORKER_URL nor RUNPOD_API_KEY is set — "
                "face swap features disabled"
            )

    @property
    def is_configured(self) -> bool:
        """Check if the worker URL is configured (static or discoverable)."""
        return bool(self._static_worker_url) or self._discovery.is_configured

    async def _get_worker_url(self) -> str:
        """
        Resolve the current worker URL.
        Uses static URL if set, otherwise auto-discovers from RunPod.
        """
        if self._static_worker_url:
            return self._static_worker_url

        url = await self._discovery.get_worker_url()
        if not url:
            raise WorkerConnectionError(
                "GPU Worker URL not available. "
                "Set FACE_SWAP_WORKER_URL or RUNPOD_API_KEY."
            )
        return url

    def _headers(self) -> Dict[str, str]:
        return {"X-Api-Key": self.api_key}

    async def _request(
        self,
        method: str,
        path: str,
        _retry_on_discovery: bool = True,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Make an HTTP request to the GPU worker.

        If the request fails with a connection error or 404, and auto-discovery
        is enabled, it will invalidate the cache and retry with a fresh URL.
        """
        if not self.is_configured:
            raise WorkerConnectionError("Face swap worker not configured")

        worker_url = await self._get_worker_url()
        url = f"{worker_url}{path}"
        timeout = httpx.Timeout(
            connect=WORKER_CONNECT_TIMEOUT,
            read=WORKER_READ_TIMEOUT,
            write=30.0,
            pool=10.0,
        )

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.request(
                    method,
                    url,
                    headers=self._headers(),
                    **kwargs,
                )

                if resp.status_code >= 400:
                    detail = resp.text[:500]
                    try:
                        detail = resp.json().get("detail", detail)
                    except Exception:
                        pass

                    # If 404 and using auto-discovery, try refreshing the URL
                    if (
                        resp.status_code == 404
                        and _retry_on_discovery
                        and not self._static_worker_url
                    ):
                        logger.warning(
                            f"Worker returned 404 at {url}. "
                            f"Refreshing URL from RunPod API..."
                        )
                        await self._discovery.invalidate_cache()
                        return await self._request(
                            method, path, _retry_on_discovery=False, **kwargs
                        )

                    raise WorkerAPIError(resp.status_code, detail)

                return resp.json()

        except httpx.ConnectError as e:
            # Connection failed — try re-discovering if possible
            if _retry_on_discovery and not self._static_worker_url:
                logger.warning(
                    f"Cannot connect to worker at {url}. "
                    f"Refreshing URL from RunPod API..."
                )
                await self._discovery.invalidate_cache()
                try:
                    return await self._request(
                        method, path, _retry_on_discovery=False, **kwargs
                    )
                except Exception:
                    pass  # Fall through to original error
            raise WorkerConnectionError(f"Cannot connect to worker at {url}: {e}")
        except httpx.TimeoutException as e:
            raise WorkerConnectionError(f"Worker request timed out: {e}")

    # ── Health ───────────────────────────────────────────────────────────

    async def health_check(self) -> Dict[str, Any]:
        """
        Check GPU worker health.
        Returns GPU info, FaceFusion version, source face status, stream status.
        """
        result = await self._request("GET", "/api/health")

        # Add discovery info to health response
        if self._discovery._cached_pod_id:
            result["runpod_pod_id"] = self._discovery._cached_pod_id
        if self._discovery._cached_url:
            result["worker_url_source"] = "auto-discovery"
        elif self._static_worker_url:
            result["worker_url_source"] = "static"

        return result

    # ── Source Face ──────────────────────────────────────────────────────

    async def set_source_face(
        self,
        image_bytes: Optional[bytes] = None,
        image_url: Optional[str] = None,
        image_base64: Optional[str] = None,
        face_index: int = 0,
        append: bool = False,
    ) -> Dict[str, Any]:
        """
        Upload a source face image to the worker.

        Args:
            image_bytes: Raw image bytes (preferred for file uploads)
            image_url: URL to download the image from
            image_base64: Base64-encoded image string
            face_index: Index of face to use if multiple detected
            append: If True, add to existing source faces (multi-angle)
        """
        # GPU Worker's set-source endpoint uses FastAPI query params
        # (not form body) for image_url, image_base64, face_index.
        # Only file uploads use multipart form data.
        query = {"face_index": face_index}

        if image_bytes:
            files = {"file": ("source_face.jpg", image_bytes, "image/jpeg")}
            return await self._request(
                "POST", "/api/set-source",
                params=query, files=files,
            )
        elif image_url:
            query["image_url"] = image_url
            return await self._request(
                "POST", "/api/set-source",
                params=query,
            )
        elif image_base64:
            query["image_base64"] = image_base64
            return await self._request(
                "POST", "/api/set-source",
                params=query,
            )
        else:
            raise FaceSwapError("Provide image_bytes, image_url, or image_base64")

    # ── Stream Control ───────────────────────────────────────────────────

    async def start_stream(
        self,
        input_rtmp: str,
        output_rtmp: str,
        quality: FaceSwapQuality = FaceSwapQuality.HIGH,
        resolution: str = DEFAULT_OUTPUT_RESOLUTION,
        fps: int = DEFAULT_OUTPUT_FPS,
    ) -> Dict[str, Any]:
        """
        Start real-time face swap stream.

        Args:
            input_rtmp: RTMP URL of body double's stream
            output_rtmp: RTMP URL for output (to platform)
            quality: Quality preset (fast/balanced/high/ultra)
            resolution: Output resolution (480p/720p/1080p)
            fps: Output FPS
        """
        return await self._request("POST", "/api/start-stream", json={
            "input_rtmp": input_rtmp,
            "output_rtmp": output_rtmp,
            "quality": quality.value if hasattr(quality, 'value') else str(quality),
            "resolution": resolution,
            "fps": fps,
        })

    async def stop_stream(self) -> Dict[str, Any]:
        """Stop the running face swap stream."""
        return await self._request("POST", "/api/stop-stream")

    async def get_stream_status(self) -> Dict[str, Any]:
        """Get current stream status and metrics."""
        return await self._request("GET", "/api/stream-status")

    # ── Single Frame ─────────────────────────────────────────────────────

    async def swap_frame(
        self,
        image_base64: str,
        quality: FaceSwapQuality = FaceSwapQuality.HIGH,
    ) -> Dict[str, Any]:
        """
        Swap face on a single image (for testing/preview).
        Returns processed image as base64.
        """
        return await self._request("POST", "/api/swap-frame", json={
            "image_base64": image_base64,
            "quality": quality.value if hasattr(quality, 'value') else str(quality),
        })

    # ── Video Face Swap ──────────────────────────────────────────────────

    async def swap_video(
        self,
        job_id: str,
        video_url: str,
        quality: FaceSwapQuality = FaceSwapQuality.HIGH,
        output_video_quality: int = 95,
    ) -> Dict[str, Any]:
        """
        Start an async video face swap job.
        Returns immediately; poll video_status() for progress.
        """
        return await self._request("POST", "/api/swap-video", json={
            "job_id": job_id,
            "video_url": video_url,
            "quality": quality.value if hasattr(quality, 'value') else str(quality),
            "output_video_quality": output_video_quality,
        })

    async def video_status(self, job_id: str) -> Dict[str, Any]:
        """Get video face swap job status and progress."""
        return await self._request("GET", f"/api/video-status/{job_id}")

    async def video_download_url(self, job_id: str) -> str:
        """Get the download URL for a completed video job."""
        worker_url = await self._get_worker_url()
        return f"{worker_url}/api/video-download/{job_id}"

    async def delete_video_job(self, job_id: str) -> Dict[str, Any]:
        """Delete a video job and its output file."""
        return await self._request("DELETE", f"/api/video-job/{job_id}")

    # ── Configuration ────────────────────────────────────────────────────

    async def get_config(self) -> Dict[str, Any]:
        """Get current FaceFusion configuration and available models."""
        return await self._request("GET", "/api/config")

    async def update_config(self, **kwargs) -> Dict[str, Any]:
        """Update FaceFusion configuration. Changes take effect on next stream start."""
        return await self._request("POST", "/api/config", json=kwargs)

    async def apply_preset(self, quality: FaceSwapQuality) -> Dict[str, Any]:
        """Apply a quality preset (fast/balanced/high/ultra)."""
        q = quality.value if hasattr(quality, 'value') else str(quality)
        return await self._request("POST", f"/api/apply-preset?quality={q}")
