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

# ╔══════════════════════════════════════════════════════════════════════════╗
# ║ PROTECTED VOICE SETTINGS — DO NOT CHANGE WITHOUT TESTING LIP-SYNC      ║
# ║ These values were obtained by binding ElevenLabs voice to LiveAvatar    ║
# ║ via the Bind Third Party Voice API. Changing them will break lip-sync.  ║
# ╚══════════════════════════════════════════════════════════════════════════╝
#
# Default ElevenLabs voice connected to LiveAvatar
# Bound from ElevenLabs voice_id: RJs5YoIcR2WzF8qHIg1q (japan kyogoku ryu)
# Created via: POST /v1/voices/bind { provider_voice_id, secret_id }
DEFAULT_VOICE_ID = "14efbcf8-d01b-425c-8b82-9d6802616997"  # DO NOT CHANGE — breaks lip-sync

# ElevenLabs voice_id → LiveAvatar UUID mapping
# When frontend sends an ElevenLabs voice_id, we map it to the LiveAvatar UUID
# To add new voices: use /api/v1/digital-human/liveavatar/voices/bind endpoint
ELEVENLABS_TO_LIVEAVATAR_VOICE_MAP: Dict[str, str] = {
    "RJs5YoIcR2WzF8qHIg1q": "14efbcf8-d01b-425c-8b82-9d6802616997",  # japan kyogoku ryu
}


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
        # Active session store — allows OBS Browser Source to retrieve
        # LiveKit credentials without postMessage (direct URL access)
        self._active_session: Optional[Dict[str, Any]] = None

        # Speak-text queue — main page pushes text, OBS polls and pops
        # Each item: {"text": str, "timestamp": float, "id": str}
        self._speak_queue: List[Dict[str, Any]] = []
        self._speak_queue_counter: int = 0
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

        # Map ElevenLabs voice_id to LiveAvatar UUID if needed
        if voice_id and voice_id in ELEVENLABS_TO_LIVEAVATAR_VOICE_MAP:
            mapped_id = ELEVENLABS_TO_LIVEAVATAR_VOICE_MAP[voice_id]
            logger.info(f"[LiveAvatar] Mapped ElevenLabs voice_id '{voice_id}' → LiveAvatar UUID '{mapped_id}'")
            voice_id = mapped_id
        elif not voice_id or not _UUID_RE.match(str(voice_id)):
            logger.info(f"[LiveAvatar] voice_id '{voice_id}' is empty or not UUID, using default: {DEFAULT_VOICE_ID}")
            voice_id = DEFAULT_VOICE_ID

        try:
            # ── Step 1: Create session token ──
            logger.info(f"[LiveAvatar] Step 1: Creating session token for avatar: {avatar_id}")

            # Build session token body following official API reference:
            # https://docs.liveavatar.com/api-reference/sessions/create-session-token
            # Include voice_settings with provider for proper ElevenLabs TTS + lip-sync.
            token_body: Dict[str, Any] = {
                "mode": "FULL",
                "avatar_id": avatar_id,
                "is_sandbox": sandbox,
                "interactivity_type": "CONVERSATIONAL",
                "avatar_persona": {
                    "voice_id": voice_id,
                    "language": language,
                    "voice_settings": {
                        "provider": "elevenLabs",
                        "speed": 1.0,
                        "stability": 0.75,
                        "similarity_boost": 0.75,
                        "style": 0,
                        "use_speaker_boost": True,
                        "model": "eleven_flash_v2_5",
                    },
                },
                "video_settings": {
                    "quality": "high",
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

            result = {
                "session_id": session_id,
                "session_token": session_token,
                "livekit_url": livekit_url,
                "livekit_client_token": livekit_client_token,
                "ws_url": ws_url,
                "max_session_duration": max_session_duration,
                "sandbox": sandbox,
            }

            # Store as active session so OBS Browser Source can retrieve it
            self._active_session = {
                "session_id": session_id,
                "livekit_url": livekit_url,
                "livekit_client_token": livekit_client_token,
                "created_at": time.time(),
            }
            logger.info(f"[LiveAvatar] Stored active session for OBS: {session_id}")

            return result

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

            # Clear active session if it matches
            if self._active_session and self._active_session.get("session_id") == session_id:
                self._active_session = None
                logger.info(f"[LiveAvatar] Cleared active session: {session_id}")

            return result

        except httpx.HTTPStatusError as e:
            error_text = e.response.text[:500]
            logger.error(f"[LiveAvatar] HTTP error stopping session: {e.response.status_code} - {error_text}")
            raise LiveAvatarError(f"HTTP {e.response.status_code}: {error_text}")
        except Exception as e:
            logger.error(f"[LiveAvatar] Error stopping session: {e}")
            raise LiveAvatarError(str(e))

    # ──────────────────────────────────────────
    # Active Session (for OBS Browser Source)
    # ──────────────────────────────────────────
    def get_active_session(self) -> Optional[Dict[str, Any]]:
        """
        Get the currently active LiveAvatar session info.

        Used by OBS Browser Source to retrieve LiveKit credentials
        without needing postMessage (since OBS has no window.opener).

        Returns None if no active session, or the session info dict
        containing session_id, livekit_url, livekit_client_token.
        """
        if not self._active_session:
            return None

        # Check if session is too old (max 2 hours)
        created_at = self._active_session.get("created_at", 0)
        if time.time() - created_at > 7200:
            logger.info("[LiveAvatar] Active session expired (>2h), clearing")
            self._active_session = None
            return None

        return self._active_session

    # ──────────────────────────────────────────
    # Speak-Text Queue (for OBS Browser Source)
    # ──────────────────────────────────────────
    def push_speak_text(self, text: str) -> Dict[str, Any]:
        """
        Push a speak-text command to the queue.
        Called by the main page when it sends speakText to its own LiveKit room.
        OBS polls this queue and sends the text to its own LiveKit room.
        """
        self._speak_queue_counter += 1
        item = {
            "id": str(self._speak_queue_counter),
            "text": text,
            "timestamp": time.time(),
        }
        self._speak_queue.append(item)
        # Keep only last 100 items to prevent memory leak
        if len(self._speak_queue) > 100:
            self._speak_queue = self._speak_queue[-50:]
        logger.info(f"[LiveAvatar] Queued speak text #{item['id']}: {text[:50]}...")
        return item

    def pop_speak_texts(self, after_id: str = "0") -> List[Dict[str, Any]]:
        """
        Pop all speak-text items after the given ID.
        OBS calls this every 1-2 seconds to get new texts.
        Returns items with id > after_id.
        """
        try:
            after_num = int(after_id)
        except (ValueError, TypeError):
            after_num = 0

        results = [item for item in self._speak_queue if int(item["id"]) > after_num]

        # Clean up old items (older than 5 minutes)
        cutoff = time.time() - 300
        self._speak_queue = [item for item in self._speak_queue if item["timestamp"] > cutoff]

        return results

    # ──────────────────────────────────────────
    # Voice Management
    # ──────────────────────────────────────────
    async def list_voices(self) -> List[Dict[str, Any]]:
        """
        List all voices registered in LiveAvatar (custom + third-party bound).
        """
        if not self.api_key:
            raise LiveAvatarError("LIVEAVATAR_API_KEY not set")

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"{self.base_url}/v1/voices",
                    headers=self._api_headers,
                )
                resp.raise_for_status()
                data = resp.json()

            raw_data = data.get("data", [])
            # Handle both {"data": {"results": [...]}} and {"data": [...]}
            if isinstance(raw_data, list):
                voices = raw_data
            elif isinstance(raw_data, dict):
                voices = raw_data.get("results", [])
            else:
                voices = []
            logger.info(f"[LiveAvatar] Fetched {len(voices)} voices")
            return voices

        except httpx.HTTPStatusError as e:
            error_text = e.response.text[:500]
            logger.error(f"[LiveAvatar] HTTP error listing voices: {e.response.status_code} - {error_text}")
            raise LiveAvatarError(f"HTTP {e.response.status_code}: {error_text}")
        except Exception as e:
            logger.error(f"[LiveAvatar] Error listing voices: {e}")
            raise LiveAvatarError(str(e))

    async def list_secrets(self) -> List[Dict[str, Any]]:
        """
        List all secrets (API keys) stored in LiveAvatar.
        """
        if not self.api_key:
            raise LiveAvatarError("LIVEAVATAR_API_KEY not set")

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"{self.base_url}/v1/secrets",
                    headers=self._api_headers,
                )
                resp.raise_for_status()
                data = resp.json()

            raw_data = data.get("data", [])
            # Handle both {"data": {"results": [...]}} and {"data": [...]}
            if isinstance(raw_data, list):
                secrets = raw_data
            elif isinstance(raw_data, dict):
                secrets = raw_data.get("results", [])
            else:
                secrets = []
            logger.info(f"[LiveAvatar] Fetched {len(secrets)} secrets")
            return secrets

        except httpx.HTTPStatusError as e:
            error_text = e.response.text[:500]
            logger.error(f"[LiveAvatar] HTTP error listing secrets: {e.response.status_code} - {error_text}")
            raise LiveAvatarError(f"HTTP {e.response.status_code}: {error_text}")
        except Exception as e:
            logger.error(f"[LiveAvatar] Error listing secrets: {e}")
            raise LiveAvatarError(str(e))

    async def create_secret(
        self,
        provider: str,
        api_key_value: str,
        name: str = "",
    ) -> Dict[str, Any]:
        """
        Create a secret (store a third-party API key in LiveAvatar).
        """
        if not self.api_key:
            raise LiveAvatarError("LIVEAVATAR_API_KEY not set")

        try:
            body = {
                "provider": provider,
                "api_key": api_key_value,
            }
            if name:
                body["name"] = name

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self.base_url}/v1/secrets",
                    headers=self._api_headers,
                    json=body,
                )
                resp.raise_for_status()
                result = resp.json()

            secret_data = result.get("data", {})
            logger.info(f"[LiveAvatar] Created secret: {secret_data.get('secret_id', 'unknown')}")
            return secret_data

        except httpx.HTTPStatusError as e:
            error_text = e.response.text[:500]
            logger.error(f"[LiveAvatar] HTTP error creating secret: {e.response.status_code} - {error_text}")
            raise LiveAvatarError(f"HTTP {e.response.status_code}: {error_text}")
        except Exception as e:
            logger.error(f"[LiveAvatar] Error creating secret: {e}")
            raise LiveAvatarError(str(e))

    async def bind_third_party_voice(
        self,
        provider_voice_id: str,
        secret_id: str,
        name: str = "",
    ) -> Dict[str, Any]:
        """
        Bind a third-party voice (e.g., ElevenLabs) to LiveAvatar.

        Returns a UUID voice_id that can be used in LiveAvatar sessions.
        """
        if not self.api_key:
            raise LiveAvatarError("LIVEAVATAR_API_KEY not set")

        try:
            body: Dict[str, Any] = {
                "provider_voice_id": provider_voice_id,
                "secret_id": secret_id,
            }
            if name:
                body["name"] = name

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self.base_url}/v1/voices/third_party",
                    headers=self._api_headers,
                    json=body,
                )
                resp.raise_for_status()
                result = resp.json()

            voice_data = result.get("data", {})
            logger.info(
                f"[LiveAvatar] Bound third-party voice: "
                f"provider_voice_id={provider_voice_id} -> "
                f"liveavatar_voice_id={voice_data.get('voice_id', 'unknown')}"
            )
            return voice_data

        except httpx.HTTPStatusError as e:
            error_text = e.response.text[:500]
            logger.error(f"[LiveAvatar] HTTP error binding voice: {e.response.status_code} - {error_text}")
            raise LiveAvatarError(f"HTTP {e.response.status_code}: {error_text}")
        except Exception as e:
            logger.error(f"[LiveAvatar] Error binding voice: {e}")
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
