"""
Liver Clone Service — Real-time Face Swap + Voice Conversion Live Streaming
=============================================================================

Integrates:
  - FaceFusion GPU Worker (real-time face swap via RTMP)
  - ElevenLabs STS (Speech-to-Speech voice conversion)
  - ElevenLabs TTS (Text-to-Speech for auto-pilot mode)
  - Auto Live Engine (script generation + comment responses)
  - VAD (Voice Activity Detection) for automatic mode switching

Architecture:
  ┌─────────────────────────────────────────────────────────────┐
  │                    Liver Clone System                         │
  │                                                              │
  │  [Body Double + Camera + Mic]                                │
  │       ↓                                                      │
  │  OBS → RTMP input → [RunPod GPU Worker]                     │
  │                          ├── FaceFusion (face swap)          │
  │                          ├── VAD (voice activity detection)  │
  │                          ├── STS (voice conversion)          │
  │                          └── TTS (auto-pilot speech)         │
  │       ↓                                                      │
  │  RTMP output → Shopee Live / TikTok Live / YouTube Live     │
  └─────────────────────────────────────────────────────────────┘

Modes:
  - MANUAL: Person speaks → face swap + voice conversion only
  - AUTO: Person silent → AI generates script + TTS plays
  - HYBRID: Auto-switches between MANUAL and AUTO based on VAD

Session Lifecycle:
  1. configure() — set face, voice, RTMP URLs, persona
  2. start() — start face swap stream + voice processing
  3. [running] — VAD monitors audio, switches modes automatically
  4. stop() — graceful shutdown
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from app.services.face_swap_service import (
    FaceSwapService,
    FaceSwapQuality,
    FaceSwapError,
    WorkerConnectionError,
)
from app.services.elevenlabs_tts_service import ElevenLabsTTSService
from app.services.runpod_discovery_service import get_runpod_discovery

logger = logging.getLogger(__name__)

# ── Environment Configuration ────────────────────────────────────────────────
LIVER_CLONE_WORKER_URL = os.getenv("LIVER_CLONE_WORKER_URL", "")
LIVER_CLONE_WORKER_API_KEY = os.getenv("LIVER_CLONE_WORKER_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
DEFAULT_VAD_THRESHOLD = float(os.getenv("LIVER_CLONE_VAD_THRESHOLD", "0.3"))
DEFAULT_SILENCE_TIMEOUT = float(os.getenv("LIVER_CLONE_SILENCE_TIMEOUT", "5.0"))


# ── Enums ────────────────────────────────────────────────────────────────────

class LiverCloneMode(str, Enum):
    """Operating mode for Liver Clone."""
    MANUAL = "manual"       # Person speaks, only face+voice conversion
    AUTO = "auto"           # Fully automated (AI script + TTS)
    HYBRID = "hybrid"       # Auto-switch based on VAD


class LiverCloneStatus(str, Enum):
    """Session status."""
    IDLE = "idle"
    CONFIGURING = "configuring"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


class AudioMode(str, Enum):
    """Current audio processing mode."""
    STS = "sts"             # Speech-to-Speech (person is talking)
    TTS = "tts"             # Text-to-Speech (AI is talking)
    SILENT = "silent"       # Transitioning / waiting


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class LiverCloneConfig:
    """Configuration for a Liver Clone session."""
    # Face swap
    source_face_url: Optional[str] = None
    source_face_base64: Optional[str] = None
    face_swap_quality: str = "high"

    # RTMP
    input_rtmp: str = ""        # Body double's stream (from OBS)
    output_rtmp: str = ""       # To streaming platform

    # Voice
    voice_id: str = ""          # ElevenLabs voice ID for STS/TTS
    voice_stability: float = 0.5
    voice_similarity: float = 0.75

    # Mode
    mode: str = "hybrid"        # manual / auto / hybrid
    vad_threshold: float = DEFAULT_VAD_THRESHOLD
    silence_timeout: float = DEFAULT_SILENCE_TIMEOUT

    # Auto-pilot (for AUTO/HYBRID modes)
    persona_name: str = ""
    persona_style: str = ""
    language: str = "en"
    products: List[Dict[str, Any]] = field(default_factory=list)
    opening_script: str = ""

    # Stream settings
    resolution: str = "720p"
    fps: int = 30


@dataclass
class LiverCloneSession:
    """Active Liver Clone session state."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    config: LiverCloneConfig = field(default_factory=LiverCloneConfig)
    status: LiverCloneStatus = LiverCloneStatus.IDLE
    audio_mode: AudioMode = AudioMode.SILENT
    mode: LiverCloneMode = LiverCloneMode.HYBRID

    # Metrics
    start_time: Optional[float] = None
    total_sts_seconds: float = 0.0
    total_tts_seconds: float = 0.0
    speak_count: int = 0
    comment_count: int = 0
    mode_switches: int = 0

    # Auto-pilot state
    auto_live_session_id: Optional[str] = None
    current_script_index: int = 0
    last_speech_time: Optional[float] = None
    is_speaking: bool = False

    # Error tracking
    error: Optional[str] = None
    last_error_time: Optional[float] = None


# ── Active Sessions Store ────────────────────────────────────────────────────
_active_sessions: Dict[str, LiverCloneSession] = {}


# ── Service Class ────────────────────────────────────────────────────────────

class LiverCloneService:
    """
    Orchestrates the Liver Clone pipeline:
    Face Swap + Voice Conversion + Auto-pilot.
    """

    def __init__(self):
        self.face_swap = FaceSwapService(
            worker_url=LIVER_CLONE_WORKER_URL or None,
            api_key=LIVER_CLONE_WORKER_API_KEY or None,
        )
        self.tts = ElevenLabsTTSService()
        self._discovery = get_runpod_discovery()

    @property
    def is_configured(self) -> bool:
        """Check if the service has minimum configuration to operate."""
        return self.face_swap.is_configured

    # ── Session Management ───────────────────────────────────────────────

    async def create_session(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create and configure a new Liver Clone session.

        Args:
            config: Session configuration dict matching LiverCloneConfig fields

        Returns:
            Session info dict with id, status, config
        """
        session = LiverCloneSession()
        session.config = LiverCloneConfig(
            source_face_url=config.get("source_face_url"),
            source_face_base64=config.get("source_face_base64"),
            face_swap_quality=config.get("face_swap_quality", "high"),
            input_rtmp=config.get("input_rtmp", ""),
            output_rtmp=config.get("output_rtmp", ""),
            voice_id=config.get("voice_id", ""),
            voice_stability=config.get("voice_stability", 0.5),
            voice_similarity=config.get("voice_similarity", 0.75),
            mode=config.get("mode", "hybrid"),
            vad_threshold=config.get("vad_threshold", DEFAULT_VAD_THRESHOLD),
            silence_timeout=config.get("silence_timeout", DEFAULT_SILENCE_TIMEOUT),
            persona_name=config.get("persona_name", ""),
            persona_style=config.get("persona_style", ""),
            language=config.get("language", "en"),
            products=config.get("products", []),
            opening_script=config.get("opening_script", ""),
            resolution=config.get("resolution", "720p"),
            fps=config.get("fps", 30),
        )
        session.mode = LiverCloneMode(session.config.mode)
        session.status = LiverCloneStatus.CONFIGURING

        _active_sessions[session.id] = session
        logger.info(f"[LiverClone] Session created: {session.id}, mode={session.mode.value}")

        return self._session_to_dict(session)

    async def start_session(self, session_id: str) -> Dict[str, Any]:
        """
        Start the Liver Clone pipeline for a configured session.

        Steps:
          1. Set source face on GPU Worker
          2. Start face swap stream (RTMP in → face swap → RTMP out)
          3. Start audio processing (VAD + STS/TTS)
          4. Start auto-pilot if mode is AUTO or HYBRID
        """
        session = self._get_session(session_id)
        if session.status == LiverCloneStatus.RUNNING:
            return self._session_to_dict(session)

        session.status = LiverCloneStatus.STARTING
        session.error = None

        try:
            # Step 1: Set source face
            if session.config.source_face_url or session.config.source_face_base64:
                logger.info(f"[LiverClone] Setting source face for session {session_id}")
                await self.face_swap.set_source_face(
                    image_url=session.config.source_face_url,
                    image_base64=session.config.source_face_base64,
                )

            # Step 2: Start face swap stream
            if session.config.input_rtmp and session.config.output_rtmp:
                logger.info(
                    f"[LiverClone] Starting face swap stream: "
                    f"{session.config.input_rtmp} → {session.config.output_rtmp}"
                )
                await self.face_swap.start_stream(
                    input_rtmp=session.config.input_rtmp,
                    output_rtmp=session.config.output_rtmp,
                    quality=FaceSwapQuality(session.config.face_swap_quality),
                    resolution=session.config.resolution,
                    fps=session.config.fps,
                )

            # Step 3: Start audio processing on GPU Worker
            await self._start_audio_processing(session)

            # Step 4: Start auto-pilot if needed
            if session.mode in (LiverCloneMode.AUTO, LiverCloneMode.HYBRID):
                await self._start_auto_pilot(session)

            session.status = LiverCloneStatus.RUNNING
            session.start_time = time.time()
            logger.info(f"[LiverClone] Session {session_id} is now RUNNING")

            return self._session_to_dict(session)

        except WorkerConnectionError as e:
            session.status = LiverCloneStatus.ERROR
            session.error = f"GPU Worker connection failed: {str(e)}"
            logger.error(f"[LiverClone] {session.error}")
            return self._session_to_dict(session)
        except FaceSwapError as e:
            session.status = LiverCloneStatus.ERROR
            session.error = f"Face swap error: {str(e)}"
            logger.error(f"[LiverClone] {session.error}")
            return self._session_to_dict(session)
        except Exception as e:
            session.status = LiverCloneStatus.ERROR
            session.error = f"Unexpected error: {str(e)}"
            logger.exception(f"[LiverClone] Failed to start session {session_id}")
            return self._session_to_dict(session)

    async def stop_session(self, session_id: str) -> Dict[str, Any]:
        """Stop a running Liver Clone session gracefully."""
        session = self._get_session(session_id)
        if session.status == LiverCloneStatus.IDLE:
            return self._session_to_dict(session)

        session.status = LiverCloneStatus.STOPPING
        logger.info(f"[LiverClone] Stopping session {session_id}")

        try:
            # Stop face swap stream
            try:
                await self.face_swap.stop_stream()
            except Exception as e:
                logger.warning(f"[LiverClone] Error stopping face swap: {e}")

            # Stop audio processing
            await self._stop_audio_processing(session)

            # Stop auto-pilot
            if session.auto_live_session_id:
                try:
                    from app.services.auto_live_engine import stop_auto_live
                    await stop_auto_live(session.auto_live_session_id)
                except Exception as e:
                    logger.warning(f"[LiverClone] Error stopping auto-live: {e}")

            session.status = LiverCloneStatus.IDLE
            logger.info(f"[LiverClone] Session {session_id} stopped successfully")

        except Exception as e:
            session.status = LiverCloneStatus.ERROR
            session.error = f"Error during stop: {str(e)}"
            logger.exception(f"[LiverClone] Error stopping session {session_id}")

        return self._session_to_dict(session)

    async def delete_session(self, session_id: str) -> Dict[str, Any]:
        """Delete a session (stops it first if running)."""
        if session_id in _active_sessions:
            session = _active_sessions[session_id]
            if session.status == LiverCloneStatus.RUNNING:
                await self.stop_session(session_id)
            del _active_sessions[session_id]
            logger.info(f"[LiverClone] Session {session_id} deleted")
            return {"status": "deleted", "session_id": session_id}
        return {"status": "not_found", "session_id": session_id}

    def get_session_status(self, session_id: str) -> Dict[str, Any]:
        """Get current session status and metrics."""
        session = self._get_session(session_id)
        return self._session_to_dict(session)

    def list_sessions(self) -> List[Dict[str, Any]]:
        """List all active sessions."""
        return [self._session_to_dict(s) for s in _active_sessions.values()]

    # ── Audio Processing ─────────────────────────────────────────────────

    async def _start_audio_processing(self, session: LiverCloneSession):
        """
        Start audio processing on the GPU Worker.
        Sends configuration for VAD + STS + TTS pipeline.
        """
        try:
            worker_url = await self.face_swap._get_worker_url()
            import httpx
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{worker_url}/api/audio/start",
                    json={
                        "session_id": session.id,
                        "voice_id": session.config.voice_id,
                        "mode": session.mode.value,
                        "vad_threshold": session.config.vad_threshold,
                        "silence_timeout": session.config.silence_timeout,
                        "voice_stability": session.config.voice_stability,
                        "voice_similarity": session.config.voice_similarity,
                        "language": session.config.language,
                    },
                    headers={"X-Api-Key": self.face_swap.api_key},
                )
                if response.status_code == 200:
                    logger.info(f"[LiverClone] Audio processing started for {session.id}")
                else:
                    logger.warning(
                        f"[LiverClone] Audio start returned {response.status_code}: "
                        f"{response.text[:200]}"
                    )
        except Exception as e:
            logger.warning(f"[LiverClone] Could not start audio processing: {e}")
            # Non-fatal: face swap can still work without audio processing

    async def _stop_audio_processing(self, session: LiverCloneSession):
        """Stop audio processing on the GPU Worker."""
        try:
            worker_url = await self.face_swap._get_worker_url()
            import httpx
            async with httpx.AsyncClient(timeout=15) as client:
                await client.post(
                    f"{worker_url}/api/audio/stop",
                    json={"session_id": session.id},
                    headers={"X-Api-Key": self.face_swap.api_key},
                )
        except Exception as e:
            logger.warning(f"[LiverClone] Error stopping audio: {e}")

    # ── Auto-Pilot ───────────────────────────────────────────────────────

    async def _start_auto_pilot(self, session: LiverCloneSession):
        """
        Start the auto-pilot mode using the Auto Live Engine.
        When the person is silent, the AI will generate and speak scripts.
        """
        try:
            from app.services.auto_live_engine import start_auto_live

            auto_live_config = {
                "session_id": session.id,
                "language": session.config.language,
                "persona_name": session.config.persona_name,
                "persona_style": session.config.persona_style,
                "voice_id": session.config.voice_id,
                "products": session.config.products,
                "opening_script": session.config.opening_script,
                "mode": "liver_clone",  # Special mode flag
            }

            result = await start_auto_live(**auto_live_config)
            session.auto_live_session_id = result.get("session_id")
            logger.info(
                f"[LiverClone] Auto-pilot started: "
                f"auto_live_session={session.auto_live_session_id}"
            )
        except Exception as e:
            logger.warning(f"[LiverClone] Could not start auto-pilot: {e}")
            # Non-fatal: face swap + STS can still work

    # ── TTS Push (called by GPU Worker when person is silent) ────────────

    async def push_tts_text(self, session_id: str, text: str) -> Dict[str, Any]:
        """
        Push text to be spoken via TTS when person is silent.
        Called by the auto-pilot or comment response system.

        Returns TTS audio bytes to be played through the stream.
        """
        session = self._get_session(session_id)
        if session.status != LiverCloneStatus.RUNNING:
            return {"status": "error", "message": "Session not running"}

        try:
            audio_bytes = await self.tts.text_to_speech(
                text=text,
                voice_id=session.config.voice_id,
                stability=session.config.voice_stability,
                similarity_boost=session.config.voice_similarity,
            )

            session.speak_count += 1
            session.last_speech_time = time.time()

            # Send audio to GPU Worker for mixing into stream
            await self._send_tts_audio_to_worker(session, audio_bytes, text)

            return {
                "status": "ok",
                "text": text,
                "audio_size": len(audio_bytes),
            }
        except Exception as e:
            logger.error(f"[LiverClone] TTS error: {e}")
            return {"status": "error", "message": str(e)}

    async def _send_tts_audio_to_worker(
        self, session: LiverCloneSession, audio_bytes: bytes, text: str
    ):
        """Send TTS audio to GPU Worker for stream injection."""
        try:
            import base64
            worker_url = await self.face_swap._get_worker_url()
            import httpx
            async with httpx.AsyncClient(timeout=30) as client:
                await client.post(
                    f"{worker_url}/api/audio/inject-tts",
                    json={
                        "session_id": session.id,
                        "audio_base64": base64.b64encode(audio_bytes).decode(),
                        "text": text,
                    },
                    headers={"X-Api-Key": self.face_swap.api_key},
                )
        except Exception as e:
            logger.warning(f"[LiverClone] Failed to send TTS audio to worker: {e}")

    # ── Comment Response ─────────────────────────────────────────────────

    async def respond_to_comment(
        self, session_id: str, comment: str, username: str = ""
    ) -> Dict[str, Any]:
        """
        Generate and speak a response to a viewer comment.
        Uses the Auto Live Engine's comment response generation.
        """
        session = self._get_session(session_id)
        if session.status != LiverCloneStatus.RUNNING:
            return {"status": "error", "message": "Session not running"}

        try:
            from app.services.auto_live_engine import generate_comment_response

            response_text = await generate_comment_response(
                session_id=session.auto_live_session_id or session.id,
                comment=comment,
                username=username,
            )

            if response_text:
                session.comment_count += 1
                result = await self.push_tts_text(session_id, response_text)
                result["response_text"] = response_text
                result["comment"] = comment
                result["username"] = username
                return result

            return {"status": "no_response", "comment": comment}

        except Exception as e:
            logger.error(f"[LiverClone] Comment response error: {e}")
            return {"status": "error", "message": str(e)}

    # ── Health Check ─────────────────────────────────────────────────────

    async def health_check(self) -> Dict[str, Any]:
        """Check health of all Liver Clone components."""
        result = {
            "status": "ok",
            "face_swap_worker": "not_configured",
            "elevenlabs": "not_configured",
            "active_sessions": len(_active_sessions),
        }

        # Check face swap worker
        if self.face_swap.is_configured:
            try:
                health = await self.face_swap.health_check()
                result["face_swap_worker"] = health.get("status", "unknown")
                result["gpu_info"] = health.get("gpu", {})
            except Exception as e:
                result["face_swap_worker"] = f"error: {str(e)[:100]}"
                result["status"] = "degraded"

        # Check ElevenLabs
        if ELEVENLABS_API_KEY:
            try:
                el_health = await self.tts.health_check()
                result["elevenlabs"] = el_health.get("status", "unknown")
            except Exception as e:
                result["elevenlabs"] = f"error: {str(e)[:100]}"

        return result

    # ── Stream Status (from GPU Worker) ──────────────────────────────────

    async def get_stream_metrics(self, session_id: str) -> Dict[str, Any]:
        """Get real-time stream metrics from GPU Worker."""
        session = self._get_session(session_id)
        try:
            stream_status = await self.face_swap.get_stream_status()
            return {
                "session_id": session_id,
                "stream": stream_status,
                "audio_mode": session.audio_mode.value,
                "uptime": time.time() - session.start_time if session.start_time else 0,
                "speak_count": session.speak_count,
                "comment_count": session.comment_count,
                "mode_switches": session.mode_switches,
            }
        except Exception as e:
            return {
                "session_id": session_id,
                "error": str(e),
                "audio_mode": session.audio_mode.value,
            }

    # ── Mode Switching (called by GPU Worker VAD) ────────────────────────

    async def on_vad_event(self, session_id: str, is_speaking: bool) -> Dict[str, Any]:
        """
        Handle VAD event from GPU Worker.
        Switches between STS and TTS modes based on voice activity.

        Called by GPU Worker webhook when voice activity changes.
        """
        session = self._get_session(session_id)
        old_mode = session.audio_mode

        if is_speaking:
            session.audio_mode = AudioMode.STS
            session.is_speaking = True
            session.last_speech_time = time.time()
        else:
            session.audio_mode = AudioMode.TTS
            session.is_speaking = False

        if old_mode != session.audio_mode:
            session.mode_switches += 1
            logger.info(
                f"[LiverClone] Mode switch: {old_mode.value} → "
                f"{session.audio_mode.value} (session={session_id})"
            )

        return {
            "session_id": session_id,
            "audio_mode": session.audio_mode.value,
            "is_speaking": session.is_speaking,
        }

    # ── Update Config ────────────────────────────────────────────────────

    async def update_config(
        self, session_id: str, updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Update session configuration while running."""
        session = self._get_session(session_id)

        # Update config fields
        for key, value in updates.items():
            if hasattr(session.config, key):
                setattr(session.config, key, value)

        # If voice_id changed, update on GPU Worker
        if "voice_id" in updates and session.status == LiverCloneStatus.RUNNING:
            await self._start_audio_processing(session)

        # If face changed, update on GPU Worker
        if ("source_face_url" in updates or "source_face_base64" in updates):
            if session.status == LiverCloneStatus.RUNNING:
                await self.face_swap.set_source_face(
                    image_url=session.config.source_face_url,
                    image_base64=session.config.source_face_base64,
                )

        logger.info(f"[LiverClone] Config updated for session {session_id}: {list(updates.keys())}")
        return self._session_to_dict(session)

    # ── Helpers ──────────────────────────────────────────────────────────

    def _get_session(self, session_id: str) -> LiverCloneSession:
        """Get session by ID or raise error."""
        session = _active_sessions.get(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")
        return session

    def _session_to_dict(self, session: LiverCloneSession) -> Dict[str, Any]:
        """Convert session to API response dict."""
        uptime = 0
        if session.start_time and session.status == LiverCloneStatus.RUNNING:
            uptime = time.time() - session.start_time

        return {
            "session_id": session.id,
            "status": session.status.value,
            "mode": session.mode.value,
            "audio_mode": session.audio_mode.value,
            "config": {
                "input_rtmp": session.config.input_rtmp,
                "output_rtmp": session.config.output_rtmp,
                "voice_id": session.config.voice_id,
                "face_swap_quality": session.config.face_swap_quality,
                "resolution": session.config.resolution,
                "fps": session.config.fps,
                "language": session.config.language,
                "persona_name": session.config.persona_name,
                "vad_threshold": session.config.vad_threshold,
                "silence_timeout": session.config.silence_timeout,
            },
            "metrics": {
                "uptime_seconds": round(uptime, 1),
                "speak_count": session.speak_count,
                "comment_count": session.comment_count,
                "mode_switches": session.mode_switches,
                "total_sts_seconds": round(session.total_sts_seconds, 1),
                "total_tts_seconds": round(session.total_tts_seconds, 1),
            },
            "error": session.error,
            "auto_live_session_id": session.auto_live_session_id,
        }


# ── Singleton ────────────────────────────────────────────────────────────────
_liver_clone_service: Optional[LiverCloneService] = None


def get_liver_clone_service() -> LiverCloneService:
    """Get or create the Liver Clone service singleton."""
    global _liver_clone_service
    if _liver_clone_service is None:
        _liver_clone_service = LiverCloneService()
    return _liver_clone_service
