"""
HeyGen Video Generation Service for AitherHub
==============================================

Replaces MuseTalk GPU Worker for lip-sync video generation.
Uses HeyGen's Studio Video API to generate full-body animated videos
from a photo avatar + audio input.

Flow:
  1. Upload portrait image to HeyGen as talking photo
  2. Submit video generation job with audio URL
  3. Poll for completion
  4. Return video URL

Reference:
  https://docs.heygen.com/reference/create-an-avatar-video-v2
  https://docs.heygen.com/docs/using-audio-source-as-voice
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
HEYGEN_UPLOAD_URL = "https://upload.heygen.com"


class HeyGenError(Exception):
    """Custom exception for HeyGen API errors."""
    pass


class HeyGenService:
    """
    HeyGen Video Generation Service.

    Generates lip-synced avatar videos using HeyGen's Studio Video API.
    Supports:
      - Talking Photos (upload image → get talking_photo_id)
      - Audio source as voice (ElevenLabs TTS output)
      - Polling for video completion
    """

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or HEYGEN_API_KEY
        self.base_url = HEYGEN_BASE_URL
        self.upload_url = HEYGEN_UPLOAD_URL
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
    # Talking Photo Upload (v1 — proven to work)
    # ──────────────────────────────────────────
    async def upload_talking_photo(
        self,
        image_url: str,
    ) -> str:
        """
        Upload an image as a talking photo.

        Uses the v1 upload endpoint (upload.heygen.com/v1/talking_photo)
        which accepts raw image bytes with Content-Type header.

        Returns the talking_photo_id.
        """
        # Check cache first
        if image_url in self._avatar_cache:
            cached_id = self._avatar_cache[image_url]
            logger.info(f"[HeyGen] Using cached talking photo: {cached_id}")
            return cached_id

        try:
            # Step 1: Download the image
            logger.info(f"[HeyGen] Downloading portrait: {image_url[:80]}...")
            async with httpx.AsyncClient(timeout=60) as client:
                img_resp = await client.get(image_url)
                img_resp.raise_for_status()
                image_bytes = img_resp.content

            # Detect content type
            content_type = "image/jpeg"
            if image_url.lower().endswith(".png"):
                content_type = "image/png"
            elif image_url.lower().endswith(".webp"):
                content_type = "image/webp"

            # Step 2: Upload to HeyGen
            logger.info(f"[HeyGen] Uploading talking photo ({len(image_bytes)} bytes)...")
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{self.upload_url}/v1/talking_photo",
                    headers={
                        "X-Api-Key": self.api_key,
                        "Content-Type": content_type,
                    },
                    content=image_bytes,
                )
                resp.raise_for_status()
                data = resp.json()

            # Extract talking_photo_id
            tp_data = data.get("data", data)
            talking_photo_id = tp_data.get("talking_photo_id", "")

            if not talking_photo_id:
                raise HeyGenError(f"No talking_photo_id in response: {data}")

            self._avatar_cache[image_url] = talking_photo_id
            logger.info(f"[HeyGen] Talking photo uploaded: {talking_photo_id}")
            return talking_photo_id

        except httpx.HTTPStatusError as e:
            raise HeyGenError(
                f"HTTP error uploading talking photo: {e.response.status_code} - "
                f"{e.response.text[:200]}"
            )

    async def upload_talking_photo_from_video(
        self,
        video_url: str,
    ) -> str:
        """
        Extract first frame from a video and upload as talking photo.

        For portrait videos, we extract a frame and upload it as an image.
        """
        import subprocess
        import tempfile

        try:
            # Download video
            logger.info(f"[HeyGen] Downloading video for frame extraction: {video_url[:80]}...")
            async with httpx.AsyncClient(timeout=120) as client:
                vid_resp = await client.get(video_url)
                vid_resp.raise_for_status()

            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as vf:
                vf.write(vid_resp.content)
                video_path = vf.name

            # Extract first frame
            frame_path = video_path.replace(".mp4", "_frame.jpg")
            proc = subprocess.run(
                ["ffmpeg", "-y", "-i", video_path, "-ss", "0.5", "-vframes", "1", frame_path],
                capture_output=True, timeout=30,
            )

            if proc.returncode != 0 or not os.path.exists(frame_path):
                raise HeyGenError(f"Failed to extract frame from video: {proc.stderr.decode()[:200]}")

            # Upload frame as talking photo
            with open(frame_path, "rb") as f:
                image_bytes = f.read()

            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{self.upload_url}/v1/talking_photo",
                    headers={
                        "X-Api-Key": self.api_key,
                        "Content-Type": "image/jpeg",
                    },
                    content=image_bytes,
                )
                resp.raise_for_status()
                data = resp.json()

            tp_data = data.get("data", data)
            talking_photo_id = tp_data.get("talking_photo_id", "")

            if not talking_photo_id:
                raise HeyGenError(f"No talking_photo_id in response: {data}")

            self._avatar_cache[video_url] = talking_photo_id
            logger.info(f"[HeyGen] Talking photo from video: {talking_photo_id}")

            # Cleanup
            for p in [video_path, frame_path]:
                try:
                    os.unlink(p)
                except Exception:
                    pass

            return talking_photo_id

        except httpx.HTTPStatusError as e:
            raise HeyGenError(
                f"HTTP error processing video: {e.response.status_code} - "
                f"{e.response.text[:200]}"
            )

    # ──────────────────────────────────────────
    # Talking Photo List
    # ──────────────────────────────────────────
    async def list_talking_photos(self) -> list:
        """List all available talking photos."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.base_url}/v1/talking_photo.list",
                headers=self._headers,
            )
            resp.raise_for_status()
            data = resp.json()
        # data["data"] is a list directly (not {"talking_photos": [...]})
        result = data.get("data", [])
        if isinstance(result, list):
            return result
        return result.get("talking_photos", [])

    # ──────────────────────────────────────────
    # Video Generation
    # ──────────────────────────────────────────
    async def generate_video(
        self,
        talking_photo_id: str,
        audio_url: str,
        dimension: Optional[Dict[str, int]] = None,
        title: str = "AutoPilot Video",
    ) -> str:
        """
        Generate a video using a talking photo and audio source.

        Args:
            talking_photo_id: The talking photo ID
            audio_url: URL to the audio file (MP3 from ElevenLabs)
            dimension: Video dimensions (default: 720x1280 portrait)
            title: Video title

        Returns:
            video_id for status polling
        """
        if not dimension:
            dimension = {"width": 720, "height": 1280}

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
            f"audio={audio_url[:60]}..."
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
