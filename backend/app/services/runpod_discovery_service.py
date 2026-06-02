"""
RunPod Pod Discovery Service for AitherHub

Automatically discovers the GPU Worker URL from RunPod API,
eliminating the need to manually update FACE_SWAP_WORKER_URL
when Pod IDs change (e.g., after migration).

How it works:
  1. Calls RunPod GraphQL API to list RUNNING pods
  2. Finds the FaceFusion worker pod by name pattern
  3. Constructs the proxy URL from Pod ID
  4. Caches the URL and refreshes on connection errors
  5. Falls back to known Pod ID if API fails

Environment variables:
  RUNPOD_API_KEY          — RunPod API key (required for auto-discovery)
  RUNPOD_POD_NAME_PATTERN — Pod name pattern to match (default: "")
  RUNPOD_WORKER_PORT      — Internal port for the worker (default: 11434)
  FACE_SWAP_WORKER_URL    — Manual override (if set, used as primary)
  RUNPOD_FALLBACK_POD_ID  — Fallback Pod ID when API discovery fails
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

RUNPOD_API_KEY = os.getenv("RUNPOD_API_KEY", "")
RUNPOD_GRAPHQL_URL = "https://api.runpod.io/graphql"
RUNPOD_POD_NAME_PATTERN = os.getenv("RUNPOD_POD_NAME_PATTERN", "")
RUNPOD_WORKER_PORT = int(os.getenv("RUNPOD_WORKER_PORT", "11434"))
RUNPOD_FALLBACK_POD_ID = os.getenv("RUNPOD_FALLBACK_POD_ID", "oduj2lzhknvsw6")

# Cache TTL: how long to keep a discovered URL before re-checking (seconds)
CACHE_TTL = int(os.getenv("RUNPOD_CACHE_TTL", "300"))  # 5 minutes


class RunPodDiscoveryService:
    """
    Discovers and caches the GPU Worker URL from RunPod API.

    Uses GraphQL API with api_key query parameter for authentication.
    Falls back to a known Pod ID if API discovery fails.

    Usage:
        discovery = RunPodDiscoveryService()
        url = await discovery.get_worker_url()
        # Returns: "https://0b15dygj3kbr0i-11434.proxy.runpod.net"
    """

    def __init__(self):
        self.api_key = RUNPOD_API_KEY
        self._cached_url: Optional[str] = None
        self._cached_pod_id: Optional[str] = None
        self._cache_time: float = 0

    @property
    def is_configured(self) -> bool:
        """Check if RunPod API key or fallback Pod ID is available."""
        return bool(self.api_key) or bool(RUNPOD_FALLBACK_POD_ID)

    async def get_worker_url(self, force_refresh: bool = False) -> Optional[str]:
        """
        Get the GPU Worker URL via auto-discovery.

        Priority:
          1. Cached URL (if still valid)
          2. Auto-discover from RunPod GraphQL API
          3. Fallback to known Pod ID

        Args:
            force_refresh: If True, bypass cache and re-discover

        Returns:
            Worker URL string, or None if not available
        """
        # Priority 1: Cached URL (if not expired and not forced)
        if (
            not force_refresh
            and self._cached_url
            and (time.time() - self._cache_time) < CACHE_TTL
        ):
            return self._cached_url

        # Priority 2: Auto-discover via GraphQL API
        if self.api_key:
            url = await self._discover_worker_url()
            if url:
                return url

        # Priority 3: Fallback to known Pod ID
        if RUNPOD_FALLBACK_POD_ID:
            fallback_url = (
                f"https://{RUNPOD_FALLBACK_POD_ID}-{RUNPOD_WORKER_PORT}"
                f".proxy.runpod.net"
            )
            logger.info(
                f"Using fallback Pod ID: {RUNPOD_FALLBACK_POD_ID}, "
                f"url={fallback_url}"
            )
            # Cache the fallback URL with a shorter TTL
            self._cached_url = fallback_url
            self._cached_pod_id = RUNPOD_FALLBACK_POD_ID
            self._cache_time = time.time()
            return fallback_url

        logger.warning(
            "Neither RUNPOD_API_KEY nor RUNPOD_FALLBACK_POD_ID is set. "
            "GPU Worker auto-discovery is disabled."
        )
        return None

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
        Call RunPod GraphQL API to find a RUNNING pod and construct its proxy URL.
        Uses api_key as query parameter for authentication.
        """
        try:
            logger.info("Discovering GPU Worker URL from RunPod GraphQL API...")

            query = """
            query {
                myself {
                    pods {
                        id
                        name
                        desiredStatus
                        runtime {
                            ports {
                                privatePort
                                publicPort
                                type
                            }
                        }
                    }
                }
            }
            """

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{RUNPOD_GRAPHQL_URL}?api_key={self.api_key}",
                    json={"query": query},
                    headers={"Content-Type": "application/json"},
                )

                if resp.status_code != 200:
                    logger.error(
                        f"RunPod GraphQL API returned {resp.status_code}: "
                        f"{resp.text[:200]}"
                    )
                    return self._cached_url  # Return stale cache if available

                data = resp.json()

            # Check for GraphQL errors
            if "errors" in data or "error" in data:
                error_detail = data.get("errors") or data.get("error")
                logger.error(f"RunPod GraphQL error: {error_detail}")
                return self._cached_url

            # Extract pods from response
            pods = []
            try:
                all_pods = data["data"]["myself"]["pods"]
                # Filter to RUNNING pods only
                pods = [
                    p for p in all_pods
                    if (p.get("desiredStatus") or "").upper() == "RUNNING"
                ]
            except (KeyError, TypeError) as e:
                logger.error(
                    f"Unexpected RunPod API response structure: {e}. "
                    f"Response: {str(data)[:300]}"
                )
                return self._cached_url

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
                # If no name pattern match, try matching by port
                for pod in pods:
                    runtime = pod.get("runtime") or {}
                    ports = runtime.get("ports") or []
                    for port_info in ports:
                        if port_info.get("privatePort") == RUNPOD_WORKER_PORT:
                            target_pod = pod
                            break
                    if target_pod:
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
            query = """
            query getPod($podId: String!) {
                pod(input: { podId: $podId }) {
                    id
                    name
                    desiredStatus
                    runtime {
                        uptimeInSeconds
                        ports {
                            privatePort
                            publicPort
                            type
                        }
                        gpus {
                            id
                            gpuUtilPercent
                            memoryUtilPercent
                        }
                    }
                    machine {
                        gpuDisplayName
                    }
                }
            }
            """

            pod_id = self._cached_pod_id
            if not pod_id:
                await self.get_worker_url()
                pod_id = self._cached_pod_id

            if not pod_id:
                return None

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{RUNPOD_GRAPHQL_URL}?api_key={self.api_key}",
                    json={"query": query, "variables": {"podId": pod_id}},
                    headers={"Content-Type": "application/json"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if "data" in data and "pod" in data["data"]:
                        return data["data"]["pod"]

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
