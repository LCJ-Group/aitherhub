"""
RunPod Pod Discovery Service for AitherHub

Automatically discovers the GPU Worker URL from RunPod API,
eliminating the need to manually update FACE_SWAP_WORKER_URL
when Pod IDs change (e.g., after migration).

How it works:
  1. Calls RunPod REST API to list RUNNING pods
  2. Finds the FaceFusion worker pod by name pattern
  3. Constructs the proxy URL from Pod ID
  4. Caches the URL and refreshes on connection errors

Environment variables:
  RUNPOD_API_KEY          — RunPod API key (required for auto-discovery)
  RUNPOD_POD_NAME_PATTERN — Pod name pattern to match (default: "")
  RUNPOD_WORKER_PORT      — Internal port for the worker (default: 8000)
  FACE_SWAP_WORKER_URL    — Manual override (if set, skips auto-discovery)
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

RUNPOD_API_KEY = os.getenv("RUNPOD_API_KEY", "")
RUNPOD_API_BASE = "https://rest.runpod.io/v1"
RUNPOD_POD_NAME_PATTERN = os.getenv("RUNPOD_POD_NAME_PATTERN", "")
RUNPOD_WORKER_PORT = int(os.getenv("RUNPOD_WORKER_PORT", "8000"))

# Cache TTL: how long to keep a discovered URL before re-checking (seconds)
CACHE_TTL = int(os.getenv("RUNPOD_CACHE_TTL", "300"))  # 5 minutes


class RunPodDiscoveryService:
    """
    Discovers and caches the GPU Worker URL from RunPod API.

    Usage:
        discovery = RunPodDiscoveryService()
        url = await discovery.get_worker_url()
        # Returns: "https://fn8766r4jhzv3c-8000.proxy.runpod.net"
    """

    def __init__(self):
        self.api_key = RUNPOD_API_KEY
        self._cached_url: Optional[str] = None
        self._cached_pod_id: Optional[str] = None
        self._cache_time: float = 0
        self._manual_url = os.getenv("FACE_SWAP_WORKER_URL", "").rstrip("/")

    @property
    def is_configured(self) -> bool:
        """Check if either manual URL or RunPod API key is available."""
        return bool(self._manual_url) or bool(self.api_key)

    async def get_worker_url(self, force_refresh: bool = False) -> Optional[str]:
        """
        Get the GPU Worker URL.

        Priority:
          1. Manual FACE_SWAP_WORKER_URL (if set, always use it)
          2. Cached URL (if still valid)
          3. Auto-discover from RunPod API

        Args:
            force_refresh: If True, bypass cache and re-discover

        Returns:
            Worker URL string, or None if not available
        """
        # Priority 1: Manual override
        if self._manual_url:
            return self._manual_url

        # Priority 2: Cached URL (if not expired and not forced)
        if (
            not force_refresh
            and self._cached_url
            and (time.time() - self._cache_time) < CACHE_TTL
        ):
            return self._cached_url

        # Priority 3: Auto-discover
        if not self.api_key:
            logger.warning(
                "Neither FACE_SWAP_WORKER_URL nor RUNPOD_API_KEY is set. "
                "GPU Worker auto-discovery is disabled."
            )
            return None

        return await self._discover_worker_url()

    async def invalidate_cache(self):
        """
        Invalidate the cached URL.
        Call this when a connection error occurs to trigger re-discovery.
        """
        logger.info("Invalidating RunPod worker URL cache")
        self._cached_url = None
        self._cached_pod_id = None
        self._cache_time = 0

    async def _discover_worker_url(self) -> Optional[str]:
        """
        Call RunPod API to find a RUNNING pod and construct its proxy URL.
        """
        try:
            logger.info("Discovering GPU Worker URL from RunPod API...")

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{RUNPOD_API_BASE}/pods",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    params={"desiredStatus": "RUNNING"},
                )

                if resp.status_code != 200:
                    logger.error(
                        f"RunPod API returned {resp.status_code}: {resp.text[:200]}"
                    )
                    return self._cached_url  # Return stale cache if available

                pods = resp.json()

            if not pods:
                logger.warning("No RUNNING pods found on RunPod")
                return None

            # Find the FaceFusion worker pod
            target_pod = None

            if RUNPOD_POD_NAME_PATTERN:
                # Match by name pattern
                pattern = RUNPOD_POD_NAME_PATTERN.lower()
                for pod in pods:
                    pod_name = (pod.get("name") or "").lower()
                    if pattern in pod_name:
                        target_pod = pod
                        break

            if not target_pod:
                # If no name pattern match, use the first GPU pod with port 8000
                for pod in pods:
                    ports = pod.get("ports") or []
                    port_strs = [str(p) for p in ports]
                    if any(f"{RUNPOD_WORKER_PORT}" in p for p in port_strs):
                        target_pod = pod
                        break

            if not target_pod:
                # Last resort: use the first running pod
                target_pod = pods[0]
                logger.warning(
                    f"No pod matched name pattern or port {RUNPOD_WORKER_PORT}. "
                    f"Using first running pod: {target_pod.get('id')}"
                )

            pod_id = target_pod["id"]
            pod_name = target_pod.get("name", "unknown")
            worker_url = f"https://{pod_id}-{RUNPOD_WORKER_PORT}.proxy.runpod.net"

            # Update cache
            self._cached_url = worker_url
            self._cached_pod_id = pod_id
            self._cache_time = time.time()

            logger.info(
                f"Discovered GPU Worker: pod_id={pod_id}, "
                f"name={pod_name}, url={worker_url}"
            )
            return worker_url

        except httpx.ConnectError as e:
            logger.error(f"Cannot connect to RunPod API: {e}")
            return self._cached_url
        except httpx.TimeoutException as e:
            logger.error(f"RunPod API request timed out: {e}")
            return self._cached_url
        except Exception as e:
            logger.error(f"RunPod discovery failed: {type(e).__name__}: {e}")
            return self._cached_url

    async def get_pod_info(self) -> Optional[dict]:
        """
        Get detailed info about the discovered pod.
        Useful for health checks and debugging.
        """
        if not self.api_key:
            return None

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                if self._cached_pod_id:
                    resp = await client.get(
                        f"{RUNPOD_API_BASE}/pods/{self._cached_pod_id}",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                    )
                    if resp.status_code == 200:
                        return resp.json()

                # If no cached pod ID, discover first
                await self._discover_worker_url()
                if self._cached_pod_id:
                    resp = await client.get(
                        f"{RUNPOD_API_BASE}/pods/{self._cached_pod_id}",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                    )
                    if resp.status_code == 200:
                        return resp.json()

        except Exception as e:
            logger.error(f"Failed to get pod info: {e}")

        return None


# Singleton instance
_discovery_instance: Optional[RunPodDiscoveryService] = None


def get_runpod_discovery() -> RunPodDiscoveryService:
    """Get or create the singleton RunPodDiscoveryService instance."""
    global _discovery_instance
    if _discovery_instance is None:
        _discovery_instance = RunPodDiscoveryService()
    return _discovery_instance
