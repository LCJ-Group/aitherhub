"""
LiveAvatar Streaming Service for AitherHub
============================================

Replaces HeyGen Streaming API (deprecated March 2026) with LiveAvatar API.
Uses FULL Mode for text-to-speech avatar streaming via LiveKit WebRTC.

Flow:
  1. Create session token via LiveAvatar API
  2. Frontend connects to LiveKit room using the token
  3. Frontend sends text via LiveKit data channel → avatar speaks
  4. Stop session when done

Reference:
  https://docs.liveavatar.com/reference/create_session_token_v1_sessions_token_post
  https://docs.liveavatar.com/docs/full-mode-events
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────
LIVEAVATAR_API_KEY = os.getenv("LIVEAVATAR_API_KEY", "")
LIVEAVATAR_BASE_URL = "https://api.liveavatar.com"


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
      - Supports Japanese and other languages
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
    def _headers(self) -> Dict[str, str]:
        return {
            "X-API-KEY": self.api_key,
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
        import time

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
                        headers=self._headers,
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
                            headers=self._headers,
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
    # Session Management (FULL Mode)
    # ──────────────────────────────────────────
    async def create_session(
        self,
        avatar_id: str,
        language: str = "ja",
        persona_prompt: str = "",
        voice_id: Optional[str] = None,
        sandbox: bool = False,
    ) -> Dict[str, Any]:
        """
        Create a LiveAvatar streaming session in FULL Mode.

        FULL Mode: Text sent via LiveKit data channel → avatar speaks.
        Returns session_id and session_token for LiveKit connection.

        Args:
            avatar_id: UUID of the avatar to use
            language: Language code (e.g., 'ja' for Japanese)
            persona_prompt: System prompt for the avatar persona
            voice_id: Optional voice ID override
            sandbox: If True, use sandbox mode (free, 1-min sessions)
        """
        if not self.api_key:
            raise LiveAvatarError("LIVEAVATAR_API_KEY not set")

        if not persona_prompt:
            persona_prompt = (
                "あなたは京極琉（きょうごくりゅう）です。"
                "美容師であり、KYOGOKUブランドの代表です。"
                "ライブコマースで商品を紹介しています。"
                "視聴者に親しみやすく、丁寧に話してください。"
                "日本語で話してください。"
            )

        try:
            logger.info(f"[LiveAvatar] Creating FULL mode session for avatar: {avatar_id}")

            body: Dict[str, Any] = {
                "mode": "FULL",
                "avatar_id": avatar_id,
                "avatar_persona": {
                    "persona_id": "default",
                    "persona_prompt": persona_prompt,
                    "language": language,
                },
            }

            # Add voice override if specified
            if voice_id:
                body["avatar_persona"]["voice_id"] = voice_id

            # Sandbox mode for testing
            if sandbox:
                body["sandbox"] = True

            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{self.base_url}/v1/sessions/token",
                    headers=self._headers,
                    json=body,
                )
                resp.raise_for_status()
                result = resp.json()

            data = result.get("data", {})
            session_id = data.get("session_id", "")
            session_token = data.get("session_token", "")

            if not session_id or not session_token:
                raise LiveAvatarError(f"Invalid response: {result}")

            logger.info(f"[LiveAvatar] Session created: {session_id}")

            return {
                "session_id": session_id,
                "session_token": session_token,
                "sandbox": sandbox,
            }

        except httpx.HTTPStatusError as e:
            error_text = e.response.text[:500]
            logger.error(f"[LiveAvatar] HTTP error creating session: {e.response.status_code} - {error_text}")
            raise LiveAvatarError(f"HTTP {e.response.status_code}: {error_text}")
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
                    headers=self._headers,
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
                    headers=self._headers,
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
