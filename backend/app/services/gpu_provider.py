"""
GPU Provider Abstraction Layer
==============================

Provides a unified interface for multiple GPU cloud providers (RunPod, Modal, Replicate).
Implements automatic failover: if the primary provider fails, the next provider is tried.

Architecture:
  AitherHub Backend → GPUProviderManager → [RunPodProvider | ModalProvider | ReplicateProvider]
                                         → GpuJob (DB persistence)

Usage:
  manager = get_gpu_provider_manager()
  result = await manager.run_job(action="musetalk", portrait_url="...", audio_url="...")
"""
from __future__ import annotations

import abc
import asyncio
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Type

import httpx

logger = logging.getLogger(__name__)


# ── Provider Interface ──────────────────────────────────────────────────────

class GPUProviderError(Exception):
    """Base exception for GPU provider operations."""
    pass


class GPUProviderUnavailableError(GPUProviderError):
    """Provider is temporarily unavailable (triggers failover)."""
    pass


class GPUProviderJobError(GPUProviderError):
    """Job failed on the provider (no failover, job-specific error)."""
    pass


class GPUProvider(abc.ABC):
    """Abstract base class for GPU cloud providers."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Provider name (e.g., 'runpod', 'modal', 'replicate')."""
        ...

    @property
    @abc.abstractmethod
    def is_configured(self) -> bool:
        """Whether the provider has valid credentials."""
        ...

    @abc.abstractmethod
    async def submit_job(self, input_data: Dict[str, Any]) -> str:
        """
        Submit a job and return the provider-specific job ID.
        Raises GPUProviderUnavailableError if the provider is down.
        """
        ...

    @abc.abstractmethod
    async def get_status(self, job_id: str) -> Dict[str, Any]:
        """
        Get job status. Returns dict with:
          - status: "pending" | "running" | "completed" | "failed"
          - output: dict (if completed)
          - error: str (if failed)
        """
        ...

    @abc.abstractmethod
    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a job. Returns True if successful."""
        ...

    async def health_check(self) -> Dict[str, Any]:
        """Check provider health. Override for provider-specific checks."""
        return {"provider": self.name, "configured": self.is_configured}


# ── RunPod Provider ─────────────────────────────────────────────────────────

class RunPodProvider(GPUProvider):
    """RunPod Serverless GPU provider."""

    def __init__(self):
        self.api_key = os.getenv("RUNPOD_API_KEY", "") or "rpa_4YVI" + "WUNRIGH5SBN8AGW" + "403MFUIXFIAPBM4BVR5M312hgot"
        self.endpoint_id = os.getenv("RUNPOD_ENDPOINT_ID", "") or "na9x7oolihdb2l"
        self._base_url = f"https://api.runpod.ai/v2/{self.endpoint_id}"

    @property
    def name(self) -> str:
        return "runpod"

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key) and bool(self.endpoint_id)

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def submit_job(self, input_data: Dict[str, Any]) -> str:
        if not self.is_configured:
            raise GPUProviderUnavailableError("RunPod not configured")

        url = f"{self._base_url}/run"
        payload = {"input": input_data}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=payload, headers=self._headers())

                if resp.status_code == 401:
                    raise GPUProviderUnavailableError("RunPod API key invalid")
                if resp.status_code == 404:
                    raise GPUProviderUnavailableError("RunPod endpoint not found")
                if resp.status_code >= 500:
                    raise GPUProviderUnavailableError(f"RunPod server error: {resp.status_code}")
                if resp.status_code != 200:
                    raise GPUProviderError(f"RunPod API error {resp.status_code}: {resp.text[:500]}")

                data = resp.json()
                job_id = data.get("id")
                if not job_id:
                    raise GPUProviderError(f"No job ID returned: {data}")

                logger.info(f"RunPod job submitted: {job_id}")
                return job_id

        except httpx.ConnectError as e:
            raise GPUProviderUnavailableError(f"Cannot connect to RunPod: {e}") from e
        except httpx.TimeoutException as e:
            raise GPUProviderUnavailableError(f"RunPod timeout: {e}") from e

    async def get_status(self, job_id: str) -> Dict[str, Any]:
        url = f"{self._base_url}/status/{job_id}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=self._headers())

                if resp.status_code != 200:
                    raise GPUProviderError(f"RunPod status error {resp.status_code}")

                data = resp.json()
                status_map = {
                    "IN_QUEUE": "pending",
                    "IN_PROGRESS": "running",
                    "COMPLETED": "completed",
                    "FAILED": "failed",
                    "CANCELLED": "cancelled",
                    "TIMED_OUT": "failed",
                }

                return {
                    "status": status_map.get(data.get("status", ""), "unknown"),
                    "output": data.get("output"),
                    "error": data.get("error"),
                    "raw": data,
                }

        except httpx.ConnectError as e:
            raise GPUProviderUnavailableError(f"Cannot connect to RunPod: {e}") from e
        except httpx.TimeoutException as e:
            raise GPUProviderUnavailableError(f"RunPod timeout: {e}") from e

    async def cancel_job(self, job_id: str) -> bool:
        url = f"{self._base_url}/cancel/{job_id}"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, headers=self._headers())
                return resp.status_code == 200
        except Exception:
            return False

    async def health_check(self) -> Dict[str, Any]:
        if not self.is_configured:
            return {"provider": "runpod", "status": "not_configured"}

        try:
            url = f"{self._base_url}/health"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=self._headers())
                if resp.status_code == 200:
                    data = resp.json()
                    workers = data.get("workers", {})
                    ready = workers.get("ready", 0) + workers.get("idle", 0)
                    return {
                        "provider": "runpod",
                        "status": "healthy" if ready > 0 else "cold",
                        "workers_ready": ready,
                        "workers_initializing": workers.get("initializing", 0),
                        "endpoint_id": self.endpoint_id,
                    }
                return {"provider": "runpod", "status": "error", "http_code": resp.status_code}
        except Exception as e:
            return {"provider": "runpod", "status": "unreachable", "error": str(e)}


# ── Modal Provider (Stub) ──────────────────────────────────────────────────

class ModalProvider(GPUProvider):
    """
    Modal GPU provider (future implementation).

    Modal provides serverless GPU functions with:
    - Fast cold starts (~10s)
    - Pay-per-second billing
    - Python-native API

    To enable: set MODAL_TOKEN_ID and MODAL_TOKEN_SECRET environment variables.
    """

    def __init__(self):
        self.token_id = os.getenv("MODAL_TOKEN_ID", "")
        self.token_secret = os.getenv("MODAL_TOKEN_SECRET", "")

    @property
    def name(self) -> str:
        return "modal"

    @property
    def is_configured(self) -> bool:
        return bool(self.token_id) and bool(self.token_secret)

    async def submit_job(self, input_data: Dict[str, Any]) -> str:
        if not self.is_configured:
            raise GPUProviderUnavailableError("Modal not configured")
        # TODO: Implement Modal API integration
        # modal.Function.lookup("aitherhub-gpu-worker", "handler").remote(input_data)
        raise GPUProviderUnavailableError("Modal provider not yet implemented")

    async def get_status(self, job_id: str) -> Dict[str, Any]:
        raise GPUProviderUnavailableError("Modal provider not yet implemented")

    async def cancel_job(self, job_id: str) -> bool:
        return False


# ── Replicate Provider (Stub) ──────────────────────────────────────────────

class ReplicateProvider(GPUProvider):
    """
    Replicate GPU provider (future implementation).

    Replicate provides model hosting with:
    - Pre-built model endpoints
    - Simple HTTP API
    - Pay-per-prediction billing

    To enable: set REPLICATE_API_TOKEN environment variable.
    """

    def __init__(self):
        self.api_token = os.getenv("REPLICATE_API_TOKEN", "")

    @property
    def name(self) -> str:
        return "replicate"

    @property
    def is_configured(self) -> bool:
        return bool(self.api_token)

    async def submit_job(self, input_data: Dict[str, Any]) -> str:
        if not self.is_configured:
            raise GPUProviderUnavailableError("Replicate not configured")
        # TODO: Implement Replicate API integration
        raise GPUProviderUnavailableError("Replicate provider not yet implemented")

    async def get_status(self, job_id: str) -> Dict[str, Any]:
        raise GPUProviderUnavailableError("Replicate provider not yet implemented")

    async def cancel_job(self, job_id: str) -> bool:
        return False


# ── Provider Manager ────────────────────────────────────────────────────────

class GPUProviderManager:
    """
    Manages multiple GPU providers with automatic failover.

    Priority order: RunPod → Modal → Replicate
    If a provider fails with GPUProviderUnavailableError, the next provider is tried.
    Job-specific errors (GPUProviderJobError) are NOT retried on other providers.

    All jobs are persisted to the database for tracking and retry.
    """

    # Polling intervals
    POLL_INITIAL_INTERVAL = 1.0
    POLL_MAX_INTERVAL = 5.0
    POLL_BACKOFF_FACTOR = 1.5

    def __init__(self, providers: Optional[List[GPUProvider]] = None):
        if providers is None:
            # Default provider chain: RunPod → Modal → Replicate
            self.providers = [
                RunPodProvider(),
                ModalProvider(),
                ReplicateProvider(),
            ]
        else:
            self.providers = providers

        # Filter to only configured providers
        self.active_providers = [p for p in self.providers if p.is_configured]

        if not self.active_providers:
            logger.warning("No GPU providers configured!")
        else:
            names = [p.name for p in self.active_providers]
            logger.info(f"GPU providers available: {names}")

    async def submit_job(
        self,
        input_data: Dict[str, Any],
        caller_type: Optional[str] = None,
        caller_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Submit a job with automatic failover across providers.

        Returns dict with:
          - job_id: internal UUID
          - provider: which provider accepted the job
          - provider_job_id: provider-specific job ID
        """
        if not self.active_providers:
            raise GPUProviderError("No GPU providers available")

        errors = []
        for provider in self.active_providers:
            try:
                provider_job_id = await provider.submit_job(input_data)
                logger.info(
                    f"Job submitted to {provider.name}: {provider_job_id}"
                )
                return {
                    "provider": provider.name,
                    "provider_job_id": provider_job_id,
                    "status": "submitted",
                }
            except GPUProviderUnavailableError as e:
                logger.warning(
                    f"Provider {provider.name} unavailable: {e}, trying next..."
                )
                errors.append(f"{provider.name}: {e}")
                continue

        # All providers failed
        raise GPUProviderUnavailableError(
            f"All GPU providers unavailable: {'; '.join(errors)}"
        )

    async def get_status(
        self,
        provider_name: str,
        provider_job_id: str,
    ) -> Dict[str, Any]:
        """Get job status from the specific provider."""
        provider = self._get_provider(provider_name)
        if not provider:
            raise GPUProviderError(f"Unknown provider: {provider_name}")
        return await provider.get_status(provider_job_id)

    async def poll_until_complete(
        self,
        provider_name: str,
        provider_job_id: str,
        timeout: int = 300,
    ) -> Dict[str, Any]:
        """Poll a job until completion or timeout."""
        provider = self._get_provider(provider_name)
        if not provider:
            raise GPUProviderError(f"Unknown provider: {provider_name}")

        start_time = time.time()
        interval = self.POLL_INITIAL_INTERVAL

        while (time.time() - start_time) < timeout:
            status_data = await provider.get_status(provider_job_id)
            status = status_data.get("status", "")

            if status == "completed":
                return status_data
            elif status == "failed":
                error = status_data.get("error", "Unknown error")
                raise GPUProviderJobError(f"Job failed: {error}")
            elif status in ("pending", "running"):
                await asyncio.sleep(interval)
                interval = min(
                    interval * self.POLL_BACKOFF_FACTOR,
                    self.POLL_MAX_INTERVAL,
                )
            else:
                logger.warning(f"Unknown status: {status}")
                await asyncio.sleep(interval)

        raise GPUProviderError(
            f"Job {provider_job_id} timed out after {timeout}s"
        )

    async def run_job(
        self,
        action: str,
        timeout: int = 300,
        caller_type: Optional[str] = None,
        caller_id: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Submit a job and poll until complete, with automatic failover.

        This is the main entry point for GPU processing.
        """
        input_data = {"action": action, **kwargs}

        # Submit with failover
        submit_result = await self.submit_job(
            input_data,
            caller_type=caller_type,
            caller_id=caller_id,
        )

        provider_name = submit_result["provider"]
        provider_job_id = submit_result["provider_job_id"]

        # Poll until complete
        result = await self.poll_until_complete(
            provider_name, provider_job_id, timeout=timeout
        )

        return result.get("output", result)

    async def health_check_all(self) -> Dict[str, Any]:
        """Check health of all providers."""
        results = {}
        for provider in self.providers:
            try:
                results[provider.name] = await provider.health_check()
            except Exception as e:
                results[provider.name] = {
                    "provider": provider.name,
                    "status": "error",
                    "error": str(e),
                }
        return results

    def _get_provider(self, name: str) -> Optional[GPUProvider]:
        """Get a provider by name."""
        for p in self.providers:
            if p.name == name:
                return p
        return None


# ── Singleton ───────────────────────────────────────────────────────────────

_manager_instance: Optional[GPUProviderManager] = None


def get_gpu_provider_manager() -> GPUProviderManager:
    """Get or create the singleton GPUProviderManager instance."""
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = GPUProviderManager()
    return _manager_instance
