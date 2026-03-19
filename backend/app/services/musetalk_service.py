"""
MuseTalk Lip-Sync Service for AitherHub — Mode C: AI Lip-Sync Video

This module manages communication with the GPU Worker's MuseTalk endpoints
to generate lip-synced digital human videos from a portrait image and audio.

Architecture:
  AitherHub Backend ←→ GPU Worker (RunPod)
                          ↓
  Portrait + Audio → MuseTalk v1.5 → H.264+AAC Video (lip-synced)

GPU Worker Endpoints:
  POST /api/digital-human/generate       – Start generation job
  GET  /api/digital-human/status/{id}    – Check job progress
  GET  /api/digital-human/download/{id}  – Download output video

The service reuses the same GPU Worker URL and API key as the face swap service,
since both run on the same RunPod pod.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import httpx

from app.services.runpod_discovery_service import get_runpod_discovery

logger = logging.getLogger(__name__)

# ── Environment Configuration ────────────────────────────────────────────────

MUSETALK_WORKER_URL = os.getenv("MUSETALK_WORKER_URL", "")
MUSETALK_WORKER_API_KEY = os.getenv(
    "MUSETALK_WORKER_API_KEY",
    os.getenv("FACE_SWAP_WORKER_API_KEY", "change-me-in-production"),
)
WORKER_CONNECT_TIMEOUT = float(os.getenv("MUSETALK_CONNECT_TIMEOUT", "10"))
WORKER_READ_TIMEOUT = float(os.getenv("MUSETALK_READ_TIMEOUT", "600"))


# ── Exceptions ───────────────────────────────────────────────────────────────

class MuseTalkError(Exception):
    """Base exception for MuseTalk operations."""
    pass


class MuseTalkConnectionError(MuseTalkError):
    """Cannot reach the GPU worker."""
    pass


class MuseTalkAPIError(MuseTalkError):
    """GPU worker returned an error response."""
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"MuseTalk Worker API error {status_code}: {detail}")


# ── Service ──────────────────────────────────────────────────────────────────

class MuseTalkService:
    """
    Client for the MuseTalk GPU Worker API.

    Supports automatic GPU Worker URL discovery via RunPod API.
    Falls back to MUSETALK_WORKER_URL or FACE_SWAP_WORKER_URL env vars.

    Usage:
        service = MuseTalkService()
        result = await service.generate(portrait_url="...", audio_url="...")
        status = await service.get_status(job_id="...")
        video_bytes = await service.download(job_id="...")
    """

    def __init__(
        self,
        worker_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        # Try MUSETALK_WORKER_URL, then FACE_SWAP_WORKER_URL as fallback
        url = worker_url or MUSETALK_WORKER_URL
        if not url:
            url = os.getenv("FACE_SWAP_WORKER_URL", "")
        self._static_worker_url = url.rstrip("/") or None
        self.api_key = api_key or MUSETALK_WORKER_API_KEY
        self._discovery = get_runpod_discovery()

        if not self._static_worker_url and not self._discovery.is_configured:
            logger.warning(
                "Neither MUSETALK_WORKER_URL nor RUNPOD_API_KEY is set — "
                "MuseTalk features disabled"
            )

    @property
    def is_configured(self) -> bool:
        """Check if the worker URL is configured (static or discoverable)."""
        return bool(self._static_worker_url) or self._discovery.is_configured

    async def _get_worker_url(self) -> str:
        """Resolve the current worker URL."""
        if self._static_worker_url:
            return self._static_worker_url

        url = await self._discovery.get_worker_url()
        if not url:
            raise MuseTalkConnectionError(
                "GPU Worker URL not available. "
                "Set MUSETALK_WORKER_URL or RUNPOD_API_KEY."
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
    ) -> httpx.Response:
        """
        Make an HTTP request to the GPU worker.
        Returns the raw httpx.Response for flexibility (JSON or streaming).
        """
        if not self.is_configured:
            raise MuseTalkConnectionError("MuseTalk worker not configured")

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

                    raise MuseTalkAPIError(resp.status_code, detail)

                return resp

        except httpx.ConnectError as e:
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
                    pass
            raise MuseTalkConnectionError(
                f"Cannot connect to GPU worker at {url}: {e}"
            ) from e
        except httpx.TimeoutException as e:
            raise MuseTalkConnectionError(
                f"Timeout connecting to GPU worker at {url}: {e}"
            ) from e

    # ── Public API ───────────────────────────────────────────────────────────

    async def generate(
        self,
        portrait_url: str,
        audio_url: str,
        job_id: Optional[str] = None,
        bbox_shift: int = 0,
        extra_margin: int = 10,
        batch_size: int = 16,
        output_fps: int = 25,
    ) -> Dict[str, Any]:
        """
        Start a MuseTalk lip-sync video generation job.

        Args:
            portrait_url: URL of the portrait image (front-facing photo)
            audio_url: URL of the audio file (WAV or MP3)
            job_id: Optional custom job ID (auto-generated if not provided)
            bbox_shift: Vertical shift for face bounding box
            extra_margin: Extra margin below face for v1.5
            batch_size: Inference batch size
            output_fps: Output video FPS

        Returns:
            dict with job_id and status
        """
        import uuid
        if not job_id:
            job_id = f"mt-{uuid.uuid4().hex[:12]}"

        payload = {
            "job_id": job_id,
            "portrait_url": portrait_url,
            "audio_url": audio_url,
            "bbox_shift": bbox_shift,
            "extra_margin": extra_margin,
            "batch_size": batch_size,
            "output_fps": output_fps,
        }

        resp = await self._request("POST", "/api/digital-human/generate", json=payload)
        return resp.json()

    async def get_status(self, job_id: str) -> Dict[str, Any]:
        """
        Get the status of a MuseTalk generation job.

        Returns:
            dict with job_id, status, progress, error
        """
        resp = await self._request("GET", f"/api/digital-human/status/{job_id}")
        return resp.json()

    async def download(self, job_id: str) -> bytes:
        """
        Download the generated video file.

        Returns:
            Raw video bytes (MP4)
        """
        resp = await self._request("GET", f"/api/digital-human/download/{job_id}")
        return resp.content

    async def health_check(self) -> Dict[str, Any]:
        """
        Quick health check by calling the worker's health endpoint.
        """
        if not self.is_configured:
            return {
                "status": "not_configured",
                "error": "MUSETALK_WORKER_URL not set",
            }

        try:
            resp = await self._request("GET", "/api/health")
            data = resp.json()
            # GPU info may be nested under "gpu" key
            gpu = data.get("gpu", {})
            return {
                "status": "ok",
                "worker_url": (await self._get_worker_url())[:50] + "...",
                "gpu_name": gpu.get("gpu_name") or data.get("gpu_name"),
                "gpu_memory_used_mb": gpu.get("gpu_memory_used_mb") or data.get("gpu_memory_used_mb"),
                "gpu_memory_total_mb": gpu.get("gpu_memory_total_mb") or data.get("gpu_memory_total_mb"),
                "musetalk_loaded": data.get("musetalk_loaded", False),
            }
        except MuseTalkConnectionError as e:
            return {"status": "unreachable", "error": str(e)}
        except MuseTalkAPIError as e:
            return {"status": "error", "error": str(e)}
        except Exception as e:
            return {"status": "error", "error": f"Unexpected: {str(e)}"}
