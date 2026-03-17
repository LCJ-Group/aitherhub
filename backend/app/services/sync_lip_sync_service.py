"""
Sync.so Lip Sync Service for AitherHub

Provides high-quality lip sync using Sync.so API.
Supports two modes:
  1. Video + Audio URL → Lip-synced video
  2. Video + ElevenLabs TTS (voice_id + script) → Lip-synced video with generated voice

Sync.so models:
  - lipsync-2: General purpose, good quality (default)
  - lipsync-2-pro: Premium quality, enhanced facial detail
  - lipsync-1.9.0-beta: Maximum speed
  - react-1: Expressive lip sync with emotions

Docs: https://docs.sync.so
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

SYNC_API_KEY = os.getenv("SYNC_API_KEY", "")
SYNC_API_BASE = "https://api.sync.so"


class SyncLipSyncService:
    """
    Lip sync service using Sync.so API.

    Usage:
        service = SyncLipSyncService()

        # Mode 1: Video + Audio URLs
        result = await service.lip_sync(
            video_url="https://...",
            audio_url="https://...",
        )

        # Mode 2: Video + ElevenLabs TTS (integrated)
        result = await service.lip_sync_with_tts(
            video_url="https://...",
            voice_id="elevenlabs_voice_id",
            script="台本テキスト",
        )
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or SYNC_API_KEY
        if not self.api_key:
            logger.warning("SYNC_API_KEY not set — lip sync will be unavailable")

    # ──────────────────────────────────────────
    # Mode 1: Video + Audio → Lip Sync
    # ──────────────────────────────────────────

    async def lip_sync(
        self,
        video_url: str,
        audio_url: str,
        model: str = "lipsync-2",
        sync_mode: str = "cut_off",
        model_mode: str = "lips",
        max_wait_sec: int = 600,
        poll_interval: int = 5,
    ) -> dict:
        """
        Apply lip sync to a video using a separate audio track.

        Args:
            video_url: Public URL of the video
            audio_url: Public URL of the audio
            model: Sync.so model (lipsync-2, lipsync-2-pro, etc.)
            sync_mode: How to handle length mismatch (cut_off, bounce, loop, silence, remap)
            model_mode: What to animate (lips, face, head)
            max_wait_sec: Maximum wait time for processing
            poll_interval: Seconds between status checks

        Returns:
            dict with keys: output_url, duration, generation_id
        """
        if not self.api_key:
            raise RuntimeError("SYNC_API_KEY not configured")

        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
        }

        payload = {
            "model": model,
            "input": [
                {"type": "video", "url": video_url},
                {"type": "audio", "url": audio_url},
            ],
            "options": {
                "sync_mode": sync_mode,
                "model_mode": model_mode,
            },
        }

        return await self._create_and_poll(headers, payload, max_wait_sec, poll_interval)

    # ──────────────────────────────────────────
    # Mode 2: Video + ElevenLabs TTS → Lip Sync
    # ──────────────────────────────────────────

    async def lip_sync_with_tts(
        self,
        video_url: str,
        voice_id: str,
        script: str,
        model: str = "lipsync-2",
        sync_mode: str = "cut_off",
        model_mode: str = "lips",
        stability: float = 0.5,
        similarity_boost: float = 0.75,
        max_wait_sec: int = 600,
        poll_interval: int = 5,
    ) -> dict:
        """
        Apply lip sync with integrated ElevenLabs TTS.
        Sync.so generates the voice AND syncs the lips in one step.

        Args:
            video_url: Public URL of the video
            voice_id: ElevenLabs voice ID
            script: Text to speak
            model: Sync.so model
            sync_mode: How to handle length mismatch
            model_mode: What to animate (lips, face, head)
            stability: ElevenLabs voice stability (0-1)
            similarity_boost: ElevenLabs voice similarity boost (0-1)
            max_wait_sec: Maximum wait time
            poll_interval: Seconds between status checks

        Returns:
            dict with keys: output_url, duration, generation_id
        """
        if not self.api_key:
            raise RuntimeError("SYNC_API_KEY not configured")

        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
        }

        # Get ElevenLabs API key for the TTS integration
        elevenlabs_api_key = os.getenv("ELEVENLABS_API_KEY", "")

        payload = {
            "model": model,
            "input": [
                {"type": "video", "url": video_url},
                {
                    "type": "text",
                    "provider": {
                        "name": "elevenlabs",
                        "voiceId": voice_id,
                        "script": script,
                        "stability": stability,
                        "similarity_boost": similarity_boost,
                    },
                },
            ],
            "options": {
                "sync_mode": sync_mode,
                "model_mode": model_mode,
            },
        }

        # Sync.so needs the ElevenLabs API key passed via header
        if elevenlabs_api_key:
            headers["x-elevenlabs-api-key"] = elevenlabs_api_key

        return await self._create_and_poll(headers, payload, max_wait_sec, poll_interval)

    # ──────────────────────────────────────────
    # Internal: Create generation and poll
    # ──────────────────────────────────────────

    async def _create_and_poll(
        self,
        headers: dict,
        payload: dict,
        max_wait_sec: int,
        poll_interval: int,
    ) -> dict:
        """Create a Sync.so generation and poll until completion."""

        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, pool=None)) as client:
            # Step 1: Create generation
            logger.info(f"[sync.so] Creating generation: model={payload['model']}")
            resp = await client.post(
                f"{SYNC_API_BASE}/v2/generate",
                headers=headers,
                json=payload,
                timeout=60.0,
            )

            if resp.status_code not in (200, 201):
                error_text = resp.text[:500]
                logger.error(f"[sync.so] Create failed: {resp.status_code} {error_text}")
                raise RuntimeError(f"Sync.so create failed: {resp.status_code} — {error_text}")

            gen = resp.json()
            gen_id = gen.get("id", "unknown")
            status = gen.get("status", "PENDING")
            logger.info(f"[sync.so] Generation created: id={gen_id}, status={status}")

            # Step 2: Poll for completion
            start_time = time.time()
            poll_headers = {"x-api-key": self.api_key}

            while time.time() - start_time < max_wait_sec:
                if status in ("COMPLETED",):
                    break
                if status in ("FAILED", "REJECTED"):
                    error = gen.get("error", "unknown")
                    raise RuntimeError(f"Sync.so generation failed: {error}")

                await asyncio.sleep(poll_interval)

                poll_resp = await client.get(
                    f"{SYNC_API_BASE}/v2/generate/{gen_id}",
                    headers=poll_headers,
                    timeout=30.0,
                )

                if poll_resp.status_code != 200:
                    logger.warning(f"[sync.so] Poll failed: {poll_resp.status_code}")
                    continue

                gen = poll_resp.json()
                status = gen.get("status", "PENDING")
                logger.debug(f"[sync.so] Poll: id={gen_id}, status={status}")

            if status != "COMPLETED":
                elapsed = time.time() - start_time
                raise RuntimeError(
                    f"Sync.so generation timed out after {elapsed:.0f}s "
                    f"(status={status})"
                )

            output_url = gen.get("output_url") or gen.get("outputUrl")
            duration = gen.get("output_duration") or gen.get("outputDuration")

            logger.info(
                f"[sync.so] Generation completed: id={gen_id}, "
                f"duration={duration}s, output_url={output_url}"
            )

            return {
                "generation_id": gen_id,
                "output_url": output_url,
                "duration": duration,
                "status": status,
            }

    # ──────────────────────────────────────────
    # Utility: Download result
    # ──────────────────────────────────────────

    async def download_result(self, output_url: str, save_path: str) -> str:
        """Download the lip-synced video from Sync.so output URL."""
        async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
            resp = await client.get(output_url)
            resp.raise_for_status()
            with open(save_path, "wb") as f:
                f.write(resp.content)
            size_mb = len(resp.content) / (1024 * 1024)
            logger.info(f"[sync.so] Downloaded result: {size_mb:.1f} MB → {save_path}")
            return save_path

    # ──────────────────────────────────────────
    # Health check
    # ──────────────────────────────────────────

    async def health_check(self) -> dict:
        """Check if Sync.so API is accessible.
        
        Uses a minimal POST to /v2/generate with an incomplete payload.
        A 422 response means the API key is valid (auth passed, but input missing).
        """
        if not self.api_key:
            return {"status": "unconfigured", "error": "SYNC_API_KEY not set"}

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{SYNC_API_BASE}/v2/generate",
                    headers={
                        "x-api-key": self.api_key,
                        "Content-Type": "application/json",
                    },
                    json={"model": "lipsync-2"},
                )
                # 422 = auth passed, input validation failed (expected)
                # 401/403 = auth failed
                if resp.status_code == 422:
                    return {"status": "ok"}
                if resp.status_code in (401, 403):
                    return {"status": "auth_error", "http_code": resp.status_code}
                return {"status": "ok" if resp.status_code in (200, 201) else "error", "http_code": resp.status_code}
        except Exception as e:
            return {"status": "error", "error": str(e)}
