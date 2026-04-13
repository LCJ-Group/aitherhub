"""
LiveAvatar Streaming Service for AitherHub
============================================

Replaces HeyGen Streaming API (deprecated March 2026) with LiveAvatar API.
Uses FULL Mode for text-to-speech avatar streaming via LiveKit WebRTC.

Two-step session flow:
  1. POST /v1/sessions/token  → session_id + session_token
  2. POST /v1/sessions/start  → livekit_url + livekit_client_token

Frontend connects to LiveKit room using livekit_url + livekit_client_token,
then sends text via LiveKit data channel → avatar speaks immediately.

Reference:
  https://docs.liveavatar.com/reference/create_session_token_v1_sessions_token_post
  https://docs.liveavatar.com/reference/start_session_v1_sessions_start_post
  https://docs.liveavatar.com/docs/full-mode-events
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

import httpx

# UUID v4 regex pattern
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────
LIVEAVATAR_API_KEY = os.getenv("LIVEAVATAR_API_KEY", "")
LIVEAVATAR_BASE_URL = "https://api.liveavatar.com"

# Default avatar: kyogokuryu custom avatar (kokumin1010)
DEFAULT_AVATAR_ID = "d55f3fc1-372f-426e-8fcf-75f0da82a04a"

# Default ElevenLabs voice connected to LiveAvatar
DEFAULT_VOICE_ID = "de5574fc-009e-4a01-a881-9919ef8f5a0c"


class LiveAvatarError(Exception):
    """Custom exception for LiveAvatar API errors."""
    pass


class LiveAvatarService:
    """
    LiveAvatar Streaming Service.

    Provides real-time avatar streaming using LiveAvatar's FULL Mode.
    The avatar speaks text sent via LiveKit data channel from the frontend.

    FULL Mode:
      - Text input → Avatar speaks immediately (TTS managed by LiveAvatar)
      - No need for separate TTS integration
      - Supports Japanese and other languages via ElevenLabs voices
    """

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or LIVEAVATAR_API_KEY
        self.base_url = LIVEAVATAR_BASE_URL
        self._avatars_cache: Optional[List[Dict]] = None
        self._avatars_cache_time: float = 0
        self._AVATARS_CACHE_TTL: int = 1800  # 30 minutes
        if not self.api_key:
            logger.warning(
                "LIVEAVATAR_API_KEY not set — LiveAvatar streaming will not work. "
                "Set the LIVEAVATAR_API_KEY environment variable."
            )

    @property
    def _api_headers(self) -> Dict[str, str]:
        """Headers for API-key-authenticated endpoints (e.g., /v1/sessions/token)."""
        return {
            "X-Api-Key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _token_headers(self, session_token: str) -> Dict[str, str]:
        """Headers for session-token-authenticated endpoints (e.g., /v1/sessions/start)."""
        return {
            "Authorization": f"Bearer {session_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ──────────────────────────────────────────
    # Avatar Management
    # ──────────────────────────────────────────
    async def list_avatars(self, include_public: bool = True) -> List[Dict[str, Any]]:
        """
        List available avatars (user's custom + optionally public).

        Returns combined list of user avatars and public avatars.
        Results are cached for 30 minutes.
        """
        now = time.time()
        if self._avatars_cache and (now - self._avatars_cache_time) < self._AVATARS_CACHE_TTL:
            return self._avatars_cache

        if not self.api_key:
            raise LiveAvatarError("LIVEAVATAR_API_KEY not set")

        all_avatars: List[Dict] = []

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                # Fetch user's custom avatars
                page = 1
                while True:
                    resp = await client.get(
                        f"{self.base_url}/v1/avatars",
                        headers=self._api_headers,
                        params={"page": page, "page_size": 100},
                    )
                    resp.raise_for_status()
                    data = resp.json().get("data", {})
                    results = data.get("results", [])
                    for a in results:
                        a["_source"] = "custom"
                    all_avatars.extend(results)
                    if not data.get("next"):
                        break
                    page += 1

                # Fetch public avatars
                if include_public:
                    page = 1
                    while True:
                        resp = await client.get(
                            f"{self.base_url}/v1/avatars/public",
                            headers=self._api_headers,
                            params={"page": page, "page_size": 100},
                        )
                        resp.raise_for_status()
                        data = resp.json().get("data", {})
                        results = data.get("results", [])
                        for a in results:
                            a["_source"] = "public"
                        all_avatars.extend(results)
                        if not data.get("next"):
                            break
                        page += 1

            self._avatars_cache = all_avatars
            self._avatars_cache_time = now
            logger.info(f"[LiveAvatar] Fetched {len(all_avatars)} avatars")
            return all_avatars

        except httpx.HTTPStatusError as e:
            error_text = e.response.text[:500]
            logger.error(f"[LiveAvatar] HTTP error listing avatars: {e.response.status_code} - {error_text}")
            raise LiveAvatarError(f"HTTP {e.response.status_code}: {error_text}")
        except Exception as e:
            logger.error(f"[LiveAvatar] Error listing avatars: {e}")
            raise LiveAvatarError(str(e))

    # ──────────────────────────────────────────
    # Session Management (2-step FULL Mode)
    # ──────────────────────────────────────────
    async def create_session(
        self,
        avatar_id: str = "",
        language: str = "ja",
        persona_prompt: str = "",
        voice_id: Optional[str] = None,
        sandbox: bool = False,
    ) -> Dict[str, Any]:
        """
        Create and start a LiveAvatar streaming session in FULL Mode.

        Two-step flow:
          Step 1: POST /v1/sessions/token → session_id + session_token
          Step 2: POST /v1/sessions/start → livekit_url + livekit_client_token

        Args:
            avatar_id: UUID of the avatar to use (defaults to Ann Therapist)
            language: Language code (e.g., 'ja' for Japanese)
            persona_prompt: System prompt for the avatar persona
            voice_id: Optional voice ID override (ElevenLabs voice)
            sandbox: If True, use sandbox mode (free, 1-min sessions)

        Returns:
            Dict with session_id, livekit_url, livekit_client_token, max_session_duration
        """
        if not self.api_key:
            raise LiveAvatarError("LIVEAVATAR_API_KEY not set")

        # Use defaults if not specified or if not valid UUID format
        if not avatar_id or not _UUID_RE.match(str(avatar_id)):
            logger.info(f"[LiveAvatar] avatar_id '{avatar_id}' is empty or not UUID, using default: {DEFAULT_AVATAR_ID}")
            avatar_id = DEFAULT_AVATAR_ID
        if not voice_id or not _UUID_RE.match(str(voice_id)):
            logger.info(f"[LiveAvatar] voice_id '{voice_id}' is empty or not UUID, using default: {DEFAULT_VOICE_ID}")
            voice_id = DEFAULT_VOICE_ID

        try:
            # ── Step 1: Create session token ──
            logger.info(f"[LiveAvatar] Step 1: Creating session token for avatar: {avatar_id}")

            token_body: Dict[str, Any] = {
                "mode": "FULL",
                "avatar_id": avatar_id,
                "is_sandbox": sandbox,
                "avatar_persona": {
                    "voice_id": voice_id,
                    "language": language,
                },
                "video_settings": {
                    "quality": "medium",
                    "encoding": "H264",
                },
            }

            # Add persona prompt if specified
            if persona_prompt:
                token_body["avatar_persona"]["persona_prompt"] = persona_prompt

            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{self.base_url}/v1/sessions/token",
                    headers=self._api_headers,
                    json=token_body,
                )
                resp.raise_for_status()
                token_result = resp.json()

            token_data = token_result.get("data", {})
            session_id = token_data.get("session_id", "")
            session_token = token_data.get("session_token", "")

            if not session_id or not session_token:
                raise LiveAvatarError(f"Step 1 failed - invalid response: {token_result}")

            logger.info(f"[LiveAvatar] Step 1 complete: session_id={session_id}")

            # ── Step 2: Start session (get LiveKit URL + token) ──
            logger.info(f"[LiveAvatar] Step 2: Starting session {session_id}")

            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{self.base_url}/v1/sessions/start",
                    headers=self._token_headers(session_token),
                    json={},
                )
                resp.raise_for_status()
                start_result = resp.json()

            start_data = start_result.get("data", {})
            livekit_url = start_data.get("livekit_url", "")
            livekit_client_token = start_data.get("livekit_client_token", "")
            ws_url = start_data.get("ws_url", "")
            max_session_duration = start_data.get("max_session_duration", 1200)

            if not livekit_url or not livekit_client_token:
                raise LiveAvatarError(f"Step 2 failed - no LiveKit credentials: {start_result}")

            logger.info(
                f"[LiveAvatar] Step 2 complete: livekit_url={livekit_url[:50]}..., "
                f"ws_url={'yes' if ws_url else 'no'}, "
                f"max_duration={max_session_duration}s"
            )

            return {
                "session_id": session_id,
                "session_token": session_token,
                "livekit_url": livekit_url,
                "livekit_client_token": livekit_client_token,
                "ws_url": ws_url,
                "max_session_duration": max_session_duration,
                "sandbox": sandbox,
            }

        except httpx.HTTPStatusError as e:
            error_text = e.response.text[:500]
            logger.error(f"[LiveAvatar] HTTP error creating session: {e.response.status_code} - {error_text}")
            raise LiveAvatarError(f"HTTP {e.response.status_code}: {error_text}")
        except LiveAvatarError:
            raise
        except Exception as e:
            logger.error(f"[LiveAvatar] Error creating session: {e}")
            raise LiveAvatarError(str(e))

    async def stop_session(
        self,
        session_id: str,
    ) -> Dict[str, Any]:
        """
        Stop a LiveAvatar streaming session.
        """
        if not self.api_key:
            raise LiveAvatarError("LIVEAVATAR_API_KEY not set")

        try:
            logger.info(f"[LiveAvatar] Stopping session: {session_id}")
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self.base_url}/v1/sessions/stop",
                    headers=self._api_headers,
                    json={"session_id": session_id},
                )
                resp.raise_for_status()
                result = resp.json()

            logger.info(f"[LiveAvatar] Session stopped: {session_id}")
            return result

        except httpx.HTTPStatusError as e:
            error_text = e.response.text[:500]
            logger.error(f"[LiveAvatar] HTTP error stopping session: {e.response.status_code} - {error_text}")
            raise LiveAvatarError(f"HTTP {e.response.status_code}: {error_text}")
        except Exception as e:
            logger.error(f"[LiveAvatar] Error stopping session: {e}")
            raise LiveAvatarError(str(e))

    # ──────────────────────────────────────────
    # Health Check
    # ──────────────────────────────────────────
    async def health_check(self) -> Dict[str, Any]:
        """
        Check LiveAvatar API connectivity and key validity.
        """
        if not self.api_key:
            return {
                "status": "error",
                "api_key_set": False,
                "error": "LIVEAVATAR_API_KEY not set",
            }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{self.base_url}/v1/avatars",
                    headers=self._api_headers,
                    params={"page_size": 1},
                )
                resp.raise_for_status()
                data = resp.json()

            return {
                "status": "ok",
                "api_key_set": True,
                "custom_avatars": data.get("data", {}).get("count", 0),
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
_liveavatar_service: Optional[LiveAvatarService] = None


def get_liveavatar_service() -> LiveAvatarService:
    global _liveavatar_service
    if _liveavatar_service is None:
        _liveavatar_service = LiveAvatarService()
    return _liveavatar_service
