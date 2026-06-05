"""
RunPod GPU Worker Connection Service for AitherHub
===================================================

Provides the GPU Worker URL using a FIXED Pod ID.

SAFETY RULES (NEVER VIOLATE):
  - This service ONLY provides the worker URL. It does NOT manage pods.
  - Pod stop/start/terminate/delete/create operations are FORBIDDEN in code.
  - The Pod ID is HARDCODED and must ONLY be changed by a human developer.
  - GraphQL discovery is DISABLED to prevent connecting to wrong pods.
  - If the pod is unresponsive, a human must restart it from RunPod Console.

Environment variables:
  RUNPOD_WORKER_PORT      — Internal port for the worker (default: 8888)
  FACE_SWAP_WORKER_URL    — Manual override URL (if set, used directly)
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  HARDCODED POD ID — DO NOT CHANGE WITHOUT HUMAN APPROVAL                   ║
# ║  This is the ONLY GPU Worker pod. Never stop, delete, or replace it.       ║
# ║  To update: change this value manually after creating a new pod.           ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
HARDCODED_POD_ID = os.getenv("RUNPOD_FALLBACK_POD_ID", "1qfcanzq3epk49")

RUNPOD_WORKER_PORT = int(os.getenv("RUNPOD_WORKER_PORT", "8888"))

# Manual override URL (takes priority over everything)
FACE_SWAP_WORKER_URL = os.getenv("FACE_SWAP_WORKER_URL", "")

# Cache TTL (seconds) — how long to keep URL before logging again
CACHE_TTL = int(os.getenv("RUNPOD_CACHE_TTL", "300"))  # 5 minutes

# RunPod API key — ONLY used for read-only health checks (get_pod_info)
# NEVER used for pod mutations (stop/start/delete/create)
RUNPOD_API_KEY = os.getenv("RUNPOD_API_KEY", "")
RUNPOD_GRAPHQL_URL = "https://api.runpod.io/graphql"


class RunPodDiscoveryService:
    """
    Provides the GPU Worker URL using a FIXED Pod ID.

    NO auto-discovery. NO pod management. NO mutations.
    The pod ID is hardcoded and never changes automatically.

    Usage:
        discovery = RunPodDiscoveryService()
        url = await discovery.get_worker_url()
        # Returns: "https://<POD_ID>-8888.proxy.runpod.net"
    """

    def __init__(self):
        self.api_key = RUNPOD_API_KEY
        self._cached_url: Optional[str] = None
        self._cached_pod_id: str = HARDCODED_POD_ID
        self._cache_time: float = 0

    @property
    def is_configured(self) -> bool:
        """Check if a Pod ID is available."""
        return bool(HARDCODED_POD_ID) or bool(FACE_SWAP_WORKER_URL)

    async def get_worker_url(self, force_refresh: bool = False) -> Optional[str]:
        """
        Get the GPU Worker URL.

        Priority:
          1. FACE_SWAP_WORKER_URL environment variable (manual override)
          2. Constructed URL from HARDCODED_POD_ID

        NO GraphQL discovery. NO automatic pod selection.
        """
        # Priority 1: Manual override URL
        if FACE_SWAP_WORKER_URL:
            self._cached_url = FACE_SWAP_WORKER_URL
            self._cache_time = time.time()
            return FACE_SWAP_WORKER_URL

        # Priority 2: Fixed Pod ID URL
        if HARDCODED_POD_ID:
            url = (
                f"https://{HARDCODED_POD_ID}-{RUNPOD_WORKER_PORT}"
                f".proxy.runpod.net"
            )
            # Only log on first use or refresh
            if not self._cached_url or force_refresh:
                logger.info(
                    f"GPU Worker URL (fixed pod): {url}"
                )
            self._cached_url = url
            self._cached_pod_id = HARDCODED_POD_ID
            self._cache_time = time.time()
            return url

        logger.error(
            "NO GPU Worker configured! "
            "Set FACE_SWAP_WORKER_URL or RUNPOD_FALLBACK_POD_ID."
        )
        return None

    async def invalidate_cache(self):
        """
        Invalidate the cached URL.
        Note: With fixed Pod ID, this just forces a re-log on next call.
        The URL itself doesn't change.
        """
        logger.info("Cache invalidated (URL will be re-logged on next access)")
        self._cached_url = None
        self._cache_time = 0

    async def get_pod_info(self) -> Optional[dict]:
        """
        Get READ-ONLY info about the pod (for health checks / debugging).
        This is a QUERY ONLY — no mutations.
        """
        if not self.api_key:
            return None

        pod_id = self._cached_pod_id or HARDCODED_POD_ID
        if not pod_id:
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

    # ╔══════════════════════════════════════════════════════════════════════════╗
    # ║  SAFETY: restart_pod() has been PERMANENTLY REMOVED.                    ║
    # ║                                                                         ║
    # ║  Pod lifecycle management (stop/start/restart/delete) must NEVER be     ║
    # ║  performed by application code. Only a human operator should manage     ║
    # ║  pods via the RunPod Console.                                           ║
    # ║                                                                         ║
    # ║  History: restart_pod() previously called podStop + podResume via       ║
    # ║  GraphQL API, which caused accidental pod deletion/stoppage.            ║
    # ╚══════════════════════════════════════════════════════════════════════════╝


# Singleton instance
_discovery_instance: Optional[RunPodDiscoveryService] = None


def get_runpod_discovery() -> RunPodDiscoveryService:
    """Get or create the singleton RunPodDiscoveryService instance."""
    global _discovery_instance
    if _discovery_instance is None:
        _discovery_instance = RunPodDiscoveryService()
    return _discovery_instance
