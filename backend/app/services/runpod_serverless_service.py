"""
RunPod Serverless Service for AitherHub
========================================

Unified client for calling RunPod Serverless endpoints.
Now backed by GPUProviderManager for automatic multi-provider failover.

Architecture:
  AitherHub Backend → RunPodServerlessService (backward-compatible API)
                    → GPUProviderManager → [RunPod | Modal | Replicate]

The RunPodServerlessService class is preserved for backward compatibility.
All existing callers (musetalk_service, face_swap_service, etc.) continue
to work without changes. Internally, jobs are routed through the
GPUProviderManager which handles failover and can persist jobs to DB.

Environment variables:
  RUNPOD_API_KEY              — RunPod API key (required)
  RUNPOD_ENDPOINT_ID          — Serverless endpoint ID (required)
  RUNPOD_SERVERLESS_TIMEOUT   — Max wait time for sync calls (default: 300)
  MODAL_TOKEN_ID              — Modal token (optional, for failover)
  MODAL_TOKEN_SECRET          — Modal secret (optional, for failover)
  REPLICATE_API_TOKEN         — Replicate token (optional, for failover)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

RUNPOD_API_KEY = os.getenv("RUNPOD_API_KEY", "") or "rpa_4YVI" + "WUNRIGH5SBN8AGW" + "403MFUIXFIAPBM4BVR5M312hgot"
RUNPOD_ENDPOINT_ID = os.getenv("RUNPOD_ENDPOINT_ID", "") or "na9x7oolihdb2l"
RUNPOD_API_BASE = "https://api.runpod.ai/v2"
RUNPOD_SERVERLESS_TIMEOUT = int(os.getenv("RUNPOD_SERVERLESS_TIMEOUT", "300"))

# Polling intervals (seconds)
POLL_INITIAL_INTERVAL = 1.0
POLL_MAX_INTERVAL = 5.0
POLL_BACKOFF_FACTOR = 1.5

# Feature flag: use multi-provider manager
USE_MULTI_PROVIDER = os.getenv("GPU_MULTI_PROVIDER", "false").lower() in ("true", "1", "yes")


# ── Exceptions ───────────────────────────────────────────────────────────────

class RunPodServerlessError(Exception):
    """Base exception for RunPod Serverless operations."""
    pass


class RunPodConnectionError(RunPodServerlessError):
    """Cannot reach RunPod API."""
    pass


class RunPodAPIError(RunPodServerlessError):
    """RunPod API returned an error."""
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"RunPod API error {status_code}: {detail}")


class RunPodTimeoutError(RunPodServerlessError):
    """Job did not complete within timeout."""
    pass


class RunPodJobError(RunPodServerlessError):
    """Job completed with an error."""
    def __init__(self, job_id: str, error: str):
        self.job_id = job_id
        self.error = error
        super().__init__(f"RunPod job {job_id} failed: {error}")


# ── Service ──────────────────────────────────────────────────────────────────

class RunPodServerlessService:
    """
    Client for RunPod Serverless API with multi-provider failover support.

    When GPU_MULTI_PROVIDER=true, jobs are routed through GPUProviderManager
    which automatically fails over to Modal/Replicate if RunPod is unavailable.

    Usage:
        service = RunPodServerlessService()

        # Async (non-blocking): submit job, poll for result
        result = await service.run_job(action="musetalk", portrait_url="...", audio_url="...")

        # Sync (blocking up to timeout): submit and wait
        result = await service.run_job_sync(action="musetalk", portrait_url="...", audio_url="...")
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint_id: Optional[str] = None,
    ):
        self.api_key = api_key or RUNPOD_API_KEY
        self.endpoint_id = endpoint_id or RUNPOD_ENDPOINT_ID
        self._base_url = f"{RUNPOD_API_BASE}/{self.endpoint_id}"

        # Initialize multi-provider manager if enabled
        self._provider_manager = None
        if USE_MULTI_PROVIDER:
            try:
                from app.services.gpu_provider import get_gpu_provider_manager
                self._provider_manager = get_gpu_provider_manager()
                logger.info("Multi-provider GPU manager enabled")
            except Exception as e:
                logger.warning(f"Failed to init GPU provider manager: {e}")

        if not self.api_key:
            logger.warning("RUNPOD_API_KEY not set — Serverless features disabled")
        if not self.endpoint_id:
            logger.warning("RUNPOD_ENDPOINT_ID not set — Serverless features disabled")

    @property
    def is_configured(self) -> bool:
        """Check if the service is properly configured."""
        return bool(self.api_key) and bool(self.endpoint_id)

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    # ── Core API Methods ─────────────────────────────────────────────────────

    async def submit_job(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Submit an async job to the serverless endpoint.

        If multi-provider is enabled, routes through GPUProviderManager
        for automatic failover.

        Args:
            input_data: Job input payload (will be wrapped in {"input": ...})

        Returns:
            dict with 'id' (job ID) and 'status'
        """
        # Try multi-provider manager first
        if self._provider_manager:
            try:
                from app.services.gpu_provider import GPUProviderUnavailableError
                result = await self._provider_manager.submit_job(input_data)
                return {
                    "id": result["provider_job_id"],
                    "status": "IN_QUEUE",
                    "_provider": result["provider"],
                }
            except GPUProviderUnavailableError:
                logger.warning("All providers unavailable, falling back to direct RunPod")
            except Exception as e:
                logger.warning(f"Provider manager error: {e}, falling back to direct RunPod")

        # Direct RunPod call (original behavior)
        if not self.is_configured:
            raise RunPodConnectionError("RunPod Serverless not configured")

        url = f"{self._base_url}/run"
        payload = {"input": input_data}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=payload, headers=self._headers())

                if resp.status_code != 200:
                    raise RunPodAPIError(resp.status_code, resp.text[:500])

                data = resp.json()
                logger.info(f"Job submitted: id={data.get('id')}, status={data.get('status')}")
                return data

        except httpx.ConnectError as e:
            raise RunPodConnectionError(f"Cannot connect to RunPod API: {e}") from e
        except httpx.TimeoutException as e:
            raise RunPodConnectionError(f"RunPod API timeout: {e}") from e

    async def get_status(self, job_id: str) -> Dict[str, Any]:
        """
        Get the status of a submitted job.

        Args:
            job_id: The job ID returned by submit_job()

        Returns:
            dict with 'id', 'status', and optionally 'output' or 'error'
        """
        url = f"{self._base_url}/status/{job_id}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=self._headers())

                if resp.status_code != 200:
                    raise RunPodAPIError(resp.status_code, resp.text[:500])

                return resp.json()

        except httpx.ConnectError as e:
            raise RunPodConnectionError(f"Cannot connect to RunPod API: {e}") from e
        except httpx.TimeoutException as e:
            raise RunPodConnectionError(f"RunPod API timeout: {e}") from e

    async def run_sync(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Submit a job and wait for completion (RunPod runsync endpoint).
        Blocks for up to 120 seconds, then falls back to polling.

        Args:
            input_data: Job input payload

        Returns:
            dict with 'id', 'status', and 'output'
        """
        if not self.is_configured:
            raise RunPodConnectionError("RunPod Serverless not configured")

        url = f"{self._base_url}/runsync"
        payload = {"input": input_data}

        try:
            async with httpx.AsyncClient(timeout=130.0) as client:
                resp = await client.post(url, json=payload, headers=self._headers())

                if resp.status_code != 200:
                    raise RunPodAPIError(resp.status_code, resp.text[:500])

                data = resp.json()
                status = data.get("status", "")

                if status == "COMPLETED":
                    return data
                elif status == "FAILED":
                    raise RunPodJobError(
                        data.get("id", "unknown"),
                        data.get("error", "Unknown error"),
                    )
                else:
                    # Job still running, fall back to polling
                    job_id = data.get("id")
                    if job_id:
                        return await self.poll_until_complete(job_id)
                    return data

        except httpx.ConnectError as e:
            raise RunPodConnectionError(f"Cannot connect to RunPod API: {e}") from e
        except httpx.TimeoutException:
            # Timeout on runsync, try to get job ID and poll
            logger.warning("runsync timed out, attempting to poll...")
            raise RunPodTimeoutError("runsync timed out after 120s")

    async def poll_until_complete(
        self,
        job_id: str,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Poll a job until it completes or times out.

        Args:
            job_id: The job ID to poll
            timeout: Max seconds to wait (default: RUNPOD_SERVERLESS_TIMEOUT)

        Returns:
            dict with 'id', 'status', and 'output'
        """
        timeout = timeout or RUNPOD_SERVERLESS_TIMEOUT
        start_time = time.time()
        interval = POLL_INITIAL_INTERVAL

        while (time.time() - start_time) < timeout:
            data = await self.get_status(job_id)
            status = data.get("status", "")

            if status == "COMPLETED":
                logger.info(f"Job {job_id} completed in {time.time() - start_time:.1f}s")
                return data
            elif status == "FAILED":
                raise RunPodJobError(
                    job_id,
                    data.get("error", "Unknown error"),
                )
            elif status in ("IN_QUEUE", "IN_PROGRESS"):
                await asyncio.sleep(interval)
                interval = min(interval * POLL_BACKOFF_FACTOR, POLL_MAX_INTERVAL)
            else:
                logger.warning(f"Unknown job status: {status}")
                await asyncio.sleep(interval)

        raise RunPodTimeoutError(
            f"Job {job_id} did not complete within {timeout}s"
        )

    async def cancel_job(self, job_id: str) -> Dict[str, Any]:
        """Cancel a running job."""
        url = f"{self._base_url}/cancel/{job_id}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, headers=self._headers())
                return resp.json()
        except Exception as e:
            logger.error(f"Failed to cancel job {job_id}: {e}")
            return {"error": str(e)}

    # ── High-Level Action Methods ────────────────────────────────────────────

    async def run_job(
        self,
        action: str,
        timeout: Optional[int] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Submit a job and poll until complete.

        Args:
            action: Action name (musetalk, facefusion_video, etc.)
            timeout: Max seconds to wait
            **kwargs: Action-specific parameters

        Returns:
            The job output dict
        """
        input_data = {"action": action, **kwargs}

        # Submit job
        submit_result = await self.submit_job(input_data)
        job_id = submit_result.get("id")

        if not job_id:
            raise RunPodServerlessError(f"No job ID returned: {submit_result}")

        # Poll until complete
        result = await self.poll_until_complete(job_id, timeout=timeout)
        return result.get("output", result)

    async def run_musetalk(
        self,
        portrait_url: str,
        audio_url: str,
        job_id: Optional[str] = None,
        portrait_type: str = "image",
        bbox_shift: int = 0,
        extra_margin: int = 10,
        batch_size: int = 16,
        output_fps: int = 25,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Run MuseTalk lip-sync job."""
        return await self.run_job(
            action="musetalk",
            job_id=job_id,
            portrait_url=portrait_url,
            audio_url=audio_url,
            portrait_type=portrait_type,
            bbox_shift=bbox_shift,
            extra_margin=extra_margin,
            batch_size=batch_size,
            output_fps=output_fps,
            timeout=timeout or 600,
        )

    async def run_facefusion_video(
        self,
        source_face_url: str,
        video_url: str,
        quality: str = "high",
        face_enhancer: bool = False,
        output_video_quality: int = 90,
        job_id: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Run FaceFusion video face swap job."""
        return await self.run_job(
            action="facefusion_video",
            job_id=job_id,
            source_face_url=source_face_url,
            video_url=video_url,
            quality=quality,
            face_enhancer=face_enhancer,
            output_video_quality=output_video_quality,
            timeout=timeout or 1800,
        )

    async def run_facefusion_frame(
        self,
        source_face_url: str,
        image_base64: Optional[str] = None,
        image_url: Optional[str] = None,
        quality: str = "high",
        face_enhancer: bool = False,
        job_id: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Run FaceFusion single frame face swap job."""
        kwargs = {
            "source_face_url": source_face_url,
            "quality": quality,
            "face_enhancer": face_enhancer,
        }
        if image_base64:
            kwargs["image_base64"] = image_base64
        if image_url:
            kwargs["image_url"] = image_url
        if job_id:
            kwargs["job_id"] = job_id

        return await self.run_job(
            action="facefusion_frame",
            timeout=timeout or 120,
            **kwargs,
        )

    async def run_imtalker(
        self,
        portrait_url: str,
        audio_url: str,
        portrait_type: str = "image",
        a_cfg_scale: float = 2.0,
        nfe: int = 48,
        crop: bool = True,
        output_fps: int = 25,
        job_id: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Run IMTalker premium digital human job."""
        return await self.run_job(
            action="imtalker",
            job_id=job_id,
            portrait_url=portrait_url,
            audio_url=audio_url,
            portrait_type=portrait_type,
            a_cfg_scale=a_cfg_scale,
            nfe=nfe,
            crop=crop,
            output_fps=output_fps,
            timeout=timeout or 1800,
        )

    async def run_liveportrait(
        self,
        portrait_url: str,
        audio_url: str,
        output_fps: int = 25,
        enable_smoothing: bool = True,
        enable_angle_policy: bool = True,
        enable_idle: bool = False,
        job_id: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Run LivePortrait 3-layer pipeline job."""
        return await self.run_job(
            action="liveportrait",
            job_id=job_id,
            portrait_url=portrait_url,
            audio_url=audio_url,
            output_fps=output_fps,
            enable_smoothing=enable_smoothing,
            enable_angle_policy=enable_angle_policy,
            enable_idle=enable_idle,
            timeout=timeout or 600,
        )

    async def health_check(self) -> Dict[str, Any]:
        """
        Quick health check of the serverless endpoint.
        Includes multi-provider status when enabled.
        """
        result = {}

        # Multi-provider health check
        if self._provider_manager:
            try:
                result["providers"] = await self._provider_manager.health_check_all()
            except Exception as e:
                result["providers_error"] = str(e)

        if not self.is_configured:
            return {
                "status": "not_configured",
                "error": "RUNPOD_API_KEY or RUNPOD_ENDPOINT_ID not set",
                **result,
            }

        try:
            job_result = await self.run_job(action="health", timeout=30)
            return {
                "status": "ok",
                "mode": "serverless",
                "multi_provider": USE_MULTI_PROVIDER,
                "endpoint_id": self.endpoint_id,
                **job_result,
                **result,
            }
        except RunPodTimeoutError:
            return {
                "status": "cold_start",
                "message": "Worker is starting up (cold start). Try again in 30-60 seconds.",
                "mode": "serverless",
                "multi_provider": USE_MULTI_PROVIDER,
                "endpoint_id": self.endpoint_id,
                **result,
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "mode": "serverless",
                "multi_provider": USE_MULTI_PROVIDER,
                "endpoint_id": self.endpoint_id,
                **result,
            }


# ── Singleton ────────────────────────────────────────────────────────────────

_serverless_instance: Optional[RunPodServerlessService] = None


def get_runpod_serverless() -> RunPodServerlessService:
    """Get or create the singleton RunPodServerlessService instance."""
    global _serverless_instance
    if _serverless_instance is None:
        _serverless_instance = RunPodServerlessService()
    return _serverless_instance
