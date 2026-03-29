"""
HeyGen Video Generation Service for AitherHub
==============================================

Replaces MuseTalk GPU Worker for lip-sync video generation.
Uses HeyGen's Studio Video API to generate full-body animated videos
from a photo avatar + audio input.

Flow:
  1. Upload portrait image to HeyGen (or use existing talking_photo_id)
  2. Submit video generation job with audio URL
  3. Poll for completion
  4. Return video URL

Reference:
  https://docs.heygen.com/reference/create-an-avatar-video-v2
  https://docs.heygen.com/docs/using-audio-source-as-voice
  https://docs.heygen.com/docs/photo-avatars-api
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────
HEYGEN_API_KEY = os.getenv("HEYGEN_API_KEY", "")
HEYGEN_BASE_URL = "https://api.heygen.com"

# Default engine: avatar IV is higher quality
HEYGEN_DEFAULT_ENGINE = os.getenv("HEYGEN_ENGINE", "avatar_iv")


class HeyGenError(Exception):
    """Custom exception for HeyGen API errors."""
    pass


class HeyGenService:
    """
    HeyGen Video Generation Service.

    Generates lip-synced avatar videos using HeyGen's Studio Video API.
    Supports:
      - Photo Avatars (static image → animated video)
      - Audio source as voice (ElevenLabs TTS output)
      - Polling for video completion
    """

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or HEYGEN_API_KEY
        self.base_url = HEYGEN_BASE_URL
        self._avatar_cache: Dict[str, str] = {}  # portrait_url → talking_photo_id
        if not self.api_key:
            logger.warning(
                "HEYGEN_API_KEY not set — HeyGen video generation will not work. "
                "Set the HEYGEN_API_KEY environment variable."
            )

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            "X-Api-Key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ──────────────────────────────────────────
    # Asset Upload
    # ──────────────────────────────────────────
    async def upload_asset(
        self,
        file_url: str,
        asset_type: str = "image",
    ) -> str:
        """
        Upload an asset (image/audio) to HeyGen and return the asset_id.
        Uses URL-based upload.
        """
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.base_url}/v1/asset",
                headers=self._headers,
                json={"url": file_url, "type": asset_type},
            )
            resp.raise_for_status()
            data = resp.json()

        if data.get("error"):
            raise HeyGenError(f"Asset upload failed: {data['error']}")

        asset_id = data.get("data", {}).get("asset_id", "")
        if not asset_id:
            raise HeyGenError(f"No asset_id in response: {data}")

        logger.info(f"[HeyGen] Asset uploaded: {asset_id} (type={asset_type})")
        return asset_id

    # ──────────────────────────────────────────
    # Photo Avatar Management
    # ──────────────────────────────────────────
    async def create_photo_avatar(
        self,
        image_url: str,
        name: str = "AitherHub Avatar",
    ) -> str:
        """
        Create a Photo Avatar from an image URL.
        Returns the talking_photo_id for video generation.

        Steps:
          1. Upload image as asset
          2. Create photo avatar
          3. Return the avatar ID
        """
        # Check cache first
        if image_url in self._avatar_cache:
            cached_id = self._avatar_cache[image_url]
            logger.info(f"[HeyGen] Using cached avatar: {cached_id}")
            return cached_id

        try:
            # Step 1: Upload image asset
            asset_id = await self.upload_asset(image_url, asset_type="image")

            # Step 2: Create photo avatar directly
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{self.base_url}/v2/photo_avatar",
                    headers=self._headers,
                    json={
                        "name": name,
                        "image_asset_id": asset_id,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            if data.get("error"):
                raise HeyGenError(f"Photo avatar creation failed: {data['error']}")

            avatar_data = data.get("data", {})
            talking_photo_id = avatar_data.get("talking_photo_id") or avatar_data.get("id", "")

            if not talking_photo_id:
                # Try listing avatars to find the one we just created
                talking_photo_id = await self._find_avatar_by_name(name)

            if talking_photo_id:
                self._avatar_cache[image_url] = talking_photo_id
                logger.info(f"[HeyGen] Photo avatar created: {talking_photo_id}")
            else:
                raise HeyGenError(f"Could not get talking_photo_id from response: {data}")

            return talking_photo_id

        except httpx.HTTPStatusError as e:
            raise HeyGenError(f"HTTP error creating photo avatar: {e.response.status_code} - {e.response.text}")

    async def _find_avatar_by_name(self, name: str) -> Optional[str]:
        """Find a photo avatar by name from the avatar list."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"{self.base_url}/v2/avatars",
                    headers=self._headers,
                )
                resp.raise_for_status()
                data = resp.json()

            avatars = data.get("data", {}).get("avatars", [])
            for avatar in avatars:
                if avatar.get("avatar_name") == name:
                    return avatar.get("avatar_id")
        except Exception as e:
            logger.warning(f"[HeyGen] Error finding avatar by name: {e}")
        return None

    async def list_avatars(self) -> list:
        """List all available avatars."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.base_url}/v2/avatars",
                headers=self._headers,
            )
            resp.raise_for_status()
            data = resp.json()
        return data.get("data", {}).get("avatars", [])

    # ──────────────────────────────────────────
    # Video Generation
    # ──────────────────────────────────────────
    async def generate_video(
        self,
        talking_photo_id: str,
        audio_url: str,
        dimension: Optional[Dict[str, int]] = None,
        title: str = "AutoPilot Video",
        engine: str = "",
    ) -> str:
        """
        Generate a video using a photo avatar and audio source.

        Args:
            talking_photo_id: The photo avatar ID
            audio_url: URL to the audio file (MP3/WAV from ElevenLabs)
            dimension: Video dimensions (default: 1080x1920 portrait)
            title: Video title
            engine: Avatar engine (avatar_iii or avatar_iv)

        Returns:
            video_id for status polling
        """
        if not dimension:
            dimension = {"width": 1080, "height": 1920}

        engine = engine or HEYGEN_DEFAULT_ENGINE

        payload = {
            "title": title,
            "video_inputs": [
                {
                    "character": {
                        "type": "talking_photo",
                        "talking_photo_id": talking_photo_id,
                    },
                    "voice": {
                        "type": "audio",
                        "audio_url": audio_url,
                    },
                }
            ],
            "dimension": dimension,
        }

        logger.info(
            f"[HeyGen] Generating video: avatar={talking_photo_id}, "
            f"audio={audio_url[:60]}..., engine={engine}"
        )

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.base_url}/v2/video/generate",
                headers=self._headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        if data.get("error"):
            raise HeyGenError(f"Video generation failed: {data['error']}")

        video_id = data.get("data", {}).get("video_id", "")
        if not video_id:
            raise HeyGenError(f"No video_id in response: {data}")

        logger.info(f"[HeyGen] Video generation started: {video_id}")
        return video_id

    async def get_video_status(self, video_id: str) -> Dict[str, Any]:
        """
        Check the status of a video generation job.

        Returns dict with:
          - status: pending | waiting | processing | completed | failed
          - video_url: URL to download the video (when completed)
          - error: error message (when failed)
        """
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.base_url}/v1/video_status.get",
                headers=self._headers,
                params={"video_id": video_id},
            )
            resp.raise_for_status()
            data = resp.json()

        video_data = data.get("data", {})
        return {
            "status": video_data.get("status", "unknown"),
            "video_url": video_data.get("video_url"),
            "duration": video_data.get("duration"),
            "error": video_data.get("error"),
        }

    # ──────────────────────────────────────────
    # High-Level: Generate and Wait
    # ──────────────────────────────────────────
    async def generate_and_wait(
        self,
        talking_photo_id: str,
        audio_url: str,
        dimension: Optional[Dict[str, int]] = None,
        title: str = "AutoPilot Video",
        max_wait_sec: int = 300,
        poll_interval: int = 5,
    ) -> Optional[str]:
        """
        Generate a video and wait for completion.

        Returns the video URL or None on failure/timeout.
        """
        try:
            video_id = await self.generate_video(
                talking_photo_id=talking_photo_id,
                audio_url=audio_url,
                dimension=dimension,
                title=title,
            )

            elapsed = 0
            while elapsed < max_wait_sec:
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

                status = await self.get_video_status(video_id)
                current_status = status.get("status", "unknown")

                logger.info(
                    f"[HeyGen] Video {video_id}: status={current_status}, "
                    f"elapsed={elapsed}s"
                )

                if current_status == "completed":
                    video_url = status.get("video_url")
                    if video_url:
                        logger.info(
                            f"[HeyGen] Video ready: {video_url[:80]}... "
                            f"(duration={status.get('duration')}s, waited={elapsed}s)"
                        )
                        return video_url
                    else:
                        logger.error(f"[HeyGen] Completed but no video_url: {status}")
                        return None

                elif current_status == "failed":
                    error = status.get("error", "Unknown error")
                    logger.error(f"[HeyGen] Video {video_id} failed: {error}")
                    return None

            logger.warning(
                f"[HeyGen] Video {video_id} timed out after {max_wait_sec}s"
            )
            return None

        except HeyGenError as e:
            logger.error(f"[HeyGen] Error: {e}")
            return None
        except Exception as e:
            logger.error(f"[HeyGen] Unexpected error: {e}")
            return None

    # ──────────────────────────────────────────
    # Health Check
    # ──────────────────────────────────────────
    async def health_check(self) -> Dict[str, Any]:
        """Check HeyGen API connectivity and quota."""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{self.base_url}/v1/user/remaining_quota",
                    headers=self._headers,
                )
                resp.raise_for_status()
                data = resp.json()

            quota = data.get("data", {})
            return {
                "status": "ok",
                "api_key_set": bool(self.api_key),
                "remaining_credits": quota.get("remaining_credits"),
                "remaining_quota": quota,
            }
        except Exception as e:
            return {
                "status": "error",
                "api_key_set": bool(self.api_key),
                "error": str(e),
            }


# ──────────────────────────────────────────────
# Singleton
# ──────────────────────────────────────────────
_heygen_service: Optional[HeyGenService] = None


def get_heygen_service() -> HeyGenService:
    global _heygen_service
    if _heygen_service is None:
        _heygen_service = HeyGenService()
    return _heygen_service
