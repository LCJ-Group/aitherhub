"""
MuseTalk Lip-Sync Service for AitherHub — Mode C: AI Lip-Sync Video

This module manages communication with the GPU Worker's MuseTalk endpoints
to generate lip-synced digital human videos from a portrait image and audio.

Architecture (Serverless mode — default):
  AitherHub Backend → RunPod Serverless API → GPU Worker (auto-scaled)
  - No Pod management needed
  - GPU spins up on demand, scales to zero when idle
  - Cost: pay per second of GPU usage only

Architecture (Legacy Pod mode — fallback):
  AitherHub Backend ←→ GPU Worker (RunPod Pod)
  - Requires manually running Pod
  - Used when FACE_SWAP_WORKER_URL is explicitly set

GPU Worker Actions (Serverless):
  action="musetalk" → MuseTalk lip-sync generation

GPU Worker Endpoints (Legacy Pod):
  POST /api/digital-human/generate       – Start generation job
  GET  /api/digital-human/status/{id}    – Check job progress
  GET  /api/digital-human/download/{id}  – Download output video

Environment variables:
  RUNPOD_API_KEY            — RunPod API key (for Serverless mode)
  RUNPOD_ENDPOINT_ID        — Serverless endpoint ID
  MUSETALK_WORKER_URL       — Legacy: direct Pod URL (overrides Serverless)
  FACE_SWAP_WORKER_URL      — Legacy: fallback Pod URL
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# ── Environment Configuration ────────────────────────────────────────────────

MUSETALK_WORKER_URL = os.getenv("MUSETALK_WORKER_URL", "")
MUSETALK_WORKER_API_KEY = os.getenv(
    "MUSETALK_WORKER_API_KEY",
    os.getenv("FACE_SWAP_WORKER_API_KEY", "change-me-in-production"),
)
WORKER_CONNECT_TIMEOUT = float(os.getenv("MUSETALK_CONNECT_TIMEOUT", "10"))
WORKER_READ_TIMEOUT = float(os.getenv("MUSETALK_READ_TIMEOUT", "600"))

# Serverless mode detection — use the resolved endpoint ID from runpod_serverless_service
from app.services.runpod_serverless_service import RUNPOD_ENDPOINT_ID as _RESOLVED_ENDPOINT_ID
USE_SERVERLESS = bool(_RESOLVED_ENDPOINT_ID)


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

    Automatically uses RunPod Serverless when RUNPOD_ENDPOINT_ID is set.
    Falls back to legacy Pod mode when MUSETALK_WORKER_URL or FACE_SWAP_WORKER_URL is set.

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
        # Determine mode: Serverless or Legacy Pod
        self._use_serverless = USE_SERVERLESS and not worker_url and not MUSETALK_WORKER_URL
        self._serverless = None
        self._discovery = None

        if self._use_serverless:
            # Serverless mode
            from app.services.runpod_serverless_service import get_runpod_serverless
            self._serverless = get_runpod_serverless()
            logger.info("MuseTalkService: Using RunPod Serverless mode")
        else:
            # Legacy Pod mode
            from app.services.runpod_discovery_service import get_runpod_discovery
            url = worker_url or MUSETALK_WORKER_URL
            if not url:
                url = os.getenv("FACE_SWAP_WORKER_URL", "")
            self._static_worker_url = url.rstrip("/") or None
            self.api_key = api_key or MUSETALK_WORKER_API_KEY
            self._discovery = get_runpod_discovery()
            logger.info("MuseTalkService: Using Legacy Pod mode")

            if not self._static_worker_url and not self._discovery.is_configured:
                logger.warning(
                    "Neither MUSETALK_WORKER_URL nor RUNPOD_API_KEY is set — "
                    "MuseTalk features disabled"
                )

    @property
    def is_configured(self) -> bool:
        """Check if the worker is configured (Serverless or Pod)."""
        if self._use_serverless:
            return self._serverless.is_configured
        return bool(self._static_worker_url) or (
            self._discovery is not None and self._discovery.is_configured
        )

    # ── Serverless Mode Methods ──────────────────────────────────────────────

    async def generate(
        self,
        portrait_url: str,
        audio_url: str,
        job_id: Optional[str] = None,
        portrait_type: str = "image",
        bbox_shift: int = 0,
        extra_margin: int = 10,
        batch_size: int = 16,
        output_fps: int = 25,
    ) -> Dict[str, Any]:
        """
        Start a MuseTalk lip-sync video generation job.

        In Serverless mode: submits job to RunPod and returns immediately.
        In Pod mode: sends request to the Pod's API.
        """
        import uuid
        if not job_id:
            job_id = f"mt-{uuid.uuid4().hex[:12]}"

        if self._use_serverless:
            # Serverless mode: submit async job
            result = await self._serverless.submit_job({
                "action": "musetalk",
                "job_id": job_id,
                "portrait_url": portrait_url,
                "portrait_type": portrait_type,
                "audio_url": audio_url,
                "bbox_shift": bbox_shift,
                "extra_margin": extra_margin,
                "batch_size": batch_size,
                "output_fps": output_fps,
            })
            return {
                "job_id": job_id,
                "runpod_job_id": result.get("id"),
                "status": "processing",
                "mode": "serverless",
            }
        else:
            # Legacy Pod mode
            payload = {
                "job_id": job_id,
                "portrait_url": portrait_url,
                "portrait_type": portrait_type,
                "audio_url": audio_url,
                "bbox_shift": bbox_shift,
                "extra_margin": extra_margin,
                "batch_size": batch_size,
                "output_fps": output_fps,
            }
            resp = await self._request("POST", "/api/digital-human/generate", json=payload)
            return resp.json()

    async def generate_and_wait(
        self,
        portrait_url: str,
        audio_url: str,
        job_id: Optional[str] = None,
        portrait_type: str = "image",
        bbox_shift: int = 0,
        extra_margin: int = 10,
        batch_size: int = 16,
        output_fps: int = 25,
        timeout: int = 600,
    ) -> Dict[str, Any]:
        """
        Generate a lip-sync video and wait for completion.
        Returns the final result with output_url.
        """
        if self._use_serverless:
            result = await self._serverless.run_musetalk(
                portrait_url=portrait_url,
                audio_url=audio_url,
                job_id=job_id,
                portrait_type=portrait_type,
                bbox_shift=bbox_shift,
                extra_margin=extra_margin,
                batch_size=batch_size,
                output_fps=output_fps,
                timeout=timeout,
            )
            return result
        else:
            # Legacy: generate + poll
            gen_result = await self.generate(
                portrait_url=portrait_url,
                audio_url=audio_url,
                job_id=job_id,
                portrait_type=portrait_type,
                bbox_shift=bbox_shift,
                extra_margin=extra_margin,
                batch_size=batch_size,
                output_fps=output_fps,
            )
            return gen_result

    async def get_status(self, job_id: str, runpod_job_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Get the status of a MuseTalk generation job.
        """
        if self._use_serverless:
            if not runpod_job_id:
                # Serverless mode but no runpod_job_id — cannot check status
                logger.warning(f"Serverless mode but no runpod_job_id for job {job_id}")
                return {
                    "job_id": job_id,
                    "status": "error",
                    "error": "No runpod_job_id available for serverless status check",
                    "mode": "serverless",
                }
            result = await self._serverless.get_status(runpod_job_id)
            status = result.get("status", "UNKNOWN")

            # Map RunPod status to our status format
            status_map = {
                "IN_QUEUE": "queued",
                "IN_PROGRESS": "processing",
                "COMPLETED": "completed",
                "FAILED": "failed",
            }

            output = result.get("output", {})
            return {
                "job_id": job_id,
                "runpod_job_id": runpod_job_id,
                "status": status_map.get(status, status.lower()),
                "output_url": output.get("output_url") if isinstance(output, dict) else None,
                "mode": "serverless",
            }
        else:
            # Legacy Pod mode
            resp = await self._request("GET", f"/api/digital-human/status/{job_id}")
            return resp.json()

    async def download(self, job_id: str) -> bytes:
        """
        Download the generated video file.
        In Serverless mode, the output_url is returned in get_status().
        """
        if self._use_serverless:
            raise MuseTalkError(
                "In Serverless mode, use get_status() to get output_url "
                "instead of downloading directly."
            )
        resp = await self._request("GET", f"/api/digital-human/download/{job_id}")
        return resp.content

    # ── IMTalker (Serverless + Legacy Pod) ──────────────────────────────────

    async def imtalker_generate(
        self,
        portrait_url: str,
        audio_url: str,
        job_id: Optional[str] = None,
        a_cfg_scale: float = 2.0,
        nfe: int = 48,
        crop: bool = True,
        output_fps: int = 25,
    ) -> Dict[str, Any]:
        """
        Start an IMTalker premium digital human generation job.
        In Serverless mode: submits to RunPod Serverless.
        In Pod mode: sends request to the Pod's API.
        """
        import uuid
        if not job_id:
            job_id = f"imt-{uuid.uuid4().hex[:12]}"

        if self._use_serverless:
            result = await self._serverless.submit_job({
                "action": "imtalker",
                "job_id": job_id,
                "portrait_url": portrait_url,
                "audio_url": audio_url,
                "a_cfg_scale": a_cfg_scale,
                "nfe": nfe,
                "crop": crop,
                "output_fps": output_fps,
            })
            return {
                "job_id": job_id,
                "runpod_job_id": result.get("id"),
                "status": "processing",
                "mode": "serverless",
                "engine": "imtalker",
            }
        else:
            payload = {
                "job_id": job_id,
                "portrait_url": portrait_url,
                "audio_url": audio_url,
                "a_cfg_scale": a_cfg_scale,
                "nfe": nfe,
                "crop": crop,
                "output_fps": output_fps,
            }
            resp = await self._request(
                "POST", "/api/digital-human/imtalker/generate", json=payload
            )
            return resp.json()

    async def imtalker_status(self, job_id: str, runpod_job_id: Optional[str] = None) -> Dict[str, Any]:
        """Get IMTalker job status."""
        if self._use_serverless:
            if not runpod_job_id:
                logger.warning(f"Serverless mode but no runpod_job_id for IMTalker job {job_id}")
                return {
                    "job_id": job_id,
                    "status": "error",
                    "error": "No runpod_job_id available for serverless status check",
                    "mode": "serverless",
                    "engine": "imtalker",
                }
            result = await self._serverless.get_status(runpod_job_id)
            status = result.get("status", "UNKNOWN")
            status_map = {
                "IN_QUEUE": "queued",
                "IN_PROGRESS": "processing",
                "COMPLETED": "completed",
                "FAILED": "failed",
            }
            output = result.get("output", {})
            return {
                "job_id": job_id,
                "runpod_job_id": runpod_job_id,
                "status": status_map.get(status, status.lower()),
                "output_url": output.get("output_url") if isinstance(output, dict) else None,
                "mode": "serverless",
                "engine": "imtalker",
            }
        else:
            resp = await self._request("GET", f"/api/digital-human/imtalker/status/{job_id}")
            return resp.json()

    # ── LivePortrait (Serverless + Legacy Pod) ───────────────────────────────

    async def liveportrait_generate(
        self,
        portrait_url: str,
        audio_url: str,
        job_id: Optional[str] = None,
        output_fps: int = 25,
        enable_smoothing: bool = True,
        enable_angle_policy: bool = True,
        enable_idle: bool = False,
    ) -> Dict[str, Any]:
        """
        Start a LivePortrait 3-layer pipeline job.
        In Serverless mode: submits to RunPod Serverless.
        In Pod mode: sends request to the Pod's API.
        """
        import uuid
        if not job_id:
            job_id = f"lp-{uuid.uuid4().hex[:12]}"

        if self._use_serverless:
            result = await self._serverless.submit_job({
                "action": "liveportrait",
                "job_id": job_id,
                "portrait_url": portrait_url,
                "audio_url": audio_url,
                "output_fps": output_fps,
                "enable_smoothing": enable_smoothing,
                "enable_angle_policy": enable_angle_policy,
                "enable_idle": enable_idle,
            })
            return {
                "job_id": job_id,
                "runpod_job_id": result.get("id"),
                "status": "processing",
                "mode": "serverless",
                "engine": "liveportrait",
            }
        else:
            payload = {
                "job_id": job_id,
                "portrait_url": portrait_url,
                "audio_url": audio_url,
                "output_fps": output_fps,
                "enable_smoothing": enable_smoothing,
                "enable_angle_policy": enable_angle_policy,
                "enable_idle": enable_idle,
            }
            resp = await self._request(
                "POST", "/api/digital-human/liveportrait/generate", json=payload
            )
            return resp.json()

    async def liveportrait_status(self, job_id: str, runpod_job_id: Optional[str] = None) -> Dict[str, Any]:
        """Get LivePortrait job status."""
        if self._use_serverless and not runpod_job_id:
            logger.warning(f"Serverless mode but no runpod_job_id for LivePortrait job {job_id}")
            return {
                "job_id": job_id,
                "status": "error",
                "error": "No runpod_job_id available for serverless status check",
                "mode": "serverless",
                "engine": "liveportrait",
            }
        if self._use_serverless and runpod_job_id:
            result = await self._serverless.get_status(runpod_job_id)
            status = result.get("status", "UNKNOWN")
            status_map = {
                "IN_QUEUE": "queued",
                "IN_PROGRESS": "processing",
                "COMPLETED": "completed",
                "FAILED": "failed",
            }
            output = result.get("output", {})
            return {
                "job_id": job_id,
                "runpod_job_id": runpod_job_id,
                "status": status_map.get(status, status.lower()),
                "output_url": output.get("output_url") if isinstance(output, dict) else None,
                "mode": "serverless",
                "engine": "liveportrait",
            }
        else:
            resp = await self._request("GET", f"/api/digital-human/liveportrait/status/{job_id}")
            return resp.json()

    async def health_check(self) -> Dict[str, Any]:
        """Quick health check."""
        if self._use_serverless:
            return await self._serverless.health_check()

        if not self.is_configured:
            return {
                "status": "not_configured",
                "error": "MUSETALK_WORKER_URL not set",
            }

        try:
            resp = await self._request("GET", "/api/health")
            data = resp.json()
            gpu = data.get("gpu", {})
            return {
                "status": "ok",
                "mode": "pod",
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

    # ── Legacy Pod Mode Internal Methods ─────────────────────────────────────

    async def _get_worker_url(self) -> str:
        """Resolve the current worker URL (Legacy Pod mode only)."""
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
        """Make an HTTP request to the GPU worker (Legacy Pod mode only)."""
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
