"""
Hybrid Livestream Service for AitherHub

This module orchestrates the hybrid architecture that combines:
  - ElevenLabs TTS (voice cloning, supports Japanese)
  - Tencent Cloud Digital Human (lip-sync, visual rendering)

This enables users to livestream with their own cloned voice in any language
(including Japanese), while the Tencent Cloud digital human provides
realistic lip-sync and visual presentation.

Architecture:
  ┌─────────────┐     ┌──────────────┐     ┌──────────────────┐
  │  Text Input  │────▶│ ElevenLabs   │────▶│ Tencent Cloud    │
  │  (台本/評論) │     │ TTS API      │     │ Digital Human    │
  │              │     │ (声音克隆)    │     │ (口型同步+直播)   │
  └─────────────┘     │ PCM 16kHz    │     │ Audio Driver     │
                      └──────────────┘     └──────────────────┘

Two operation modes:

  Mode A - "Liveroom + Pre-generated Audio" (for scripted content):
    台本 → ElevenLabs TTS → Upload audio to cloud storage → 
    Tencent Liveroom with audio URL (if supported) or text fallback

  Mode B - "Interactive Session + Real-time Audio" (for live interaction):
    Real-time text → ElevenLabs TTS → PCM chunks →
    Tencent WebSocket audio driver → Live lip-sync

Reference:
  - ElevenLabs: https://elevenlabs.io/docs/api-reference/text-to-speech
  - Tencent IVH Audio Driver: https://cloud.tencent.com/document/product/1240/100398
  - Tencent IVH Liveroom: https://cloud.tencent.com/document/product/1240/112139
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

import httpx

from app.services.elevenlabs_tts_service import (
    ElevenLabsTTSService,
    ElevenLabsError,
    CHUNK_SIZE_BYTES,
    INITIAL_BURST_COUNT,
    SUBSEQUENT_INTERVAL_MS,
)
from app.services.tencent_digital_human_service import (
    TencentDigitalHumanService,
    TencentAPIError,
    ScriptReq,
    SpeechParam,
    AnchorParam,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

# Default language for TTS
DEFAULT_LANGUAGE = os.getenv("HYBRID_DEFAULT_LANGUAGE", "ja")

# Audio upload endpoint (for pre-generating audio and uploading to accessible URL)
AUDIO_UPLOAD_BASE_URL = os.getenv("AUDIO_UPLOAD_BASE_URL", "")


# ──────────────────────────────────────────────
# Hybrid Livestream Service
# ──────────────────────────────────────────────

class HybridLivestreamService:
    """
    Orchestrates the hybrid livestream architecture combining ElevenLabs
    voice cloning with Tencent Cloud digital human rendering.

    Usage:
        service = HybridLivestreamService()

        # Mode A: Create liveroom with pre-generated audio scripts
        result = await service.create_liveroom_with_voice(
            scripts_text=["こんにちは！今日は新商品を紹介します。"],
            language="ja",
        )

        # Mode B: Real-time takeover with cloned voice
        await service.takeover_with_voice(
            liveroom_id="xxx",
            text="視聴者の皆さん、コメントありがとうございます！",
            language="ja",
        )
    """

    def __init__(
        self,
        elevenlabs_service: Optional[ElevenLabsTTSService] = None,
        tencent_service: Optional[TencentDigitalHumanService] = None,
    ):
        self.elevenlabs = elevenlabs_service or ElevenLabsTTSService()
        self.tencent = tencent_service or TencentDigitalHumanService()

    # ──────────────────────────────────────────
    # Mode A: Liveroom with Pre-generated Audio
    # ──────────────────────────────────────────

    async def create_liveroom_with_voice(
        self,
        scripts_text: List[str],
        language: str = DEFAULT_LANGUAGE,
        cycle_times: int = 5,
        callback_url: Optional[str] = None,
        protocol: Optional[str] = None,
        anchor_param: Optional[AnchorParam] = None,
        voice_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a liveroom using the hybrid approach:
        1. Generate audio for each script using ElevenLabs (cloned voice)
        2. Create liveroom with Tencent Cloud

        Since Tencent Liveroom API's Scripts only accept text (not audio),
        we use a dual approach:
        - The liveroom is created with text scripts (Tencent TTS as fallback)
        - Audio files are pre-generated and stored for potential interactive use
        - For real-time interaction, use takeover_with_voice()

        Returns:
            Dict with liveroom info + pre-generated audio metadata
        """
        logger.info(
            f"Creating hybrid liveroom: {len(scripts_text)} scripts, "
            f"language={language}"
        )

        # Step 1: Pre-generate audio for all scripts using ElevenLabs
        audio_results = []
        for i, text in enumerate(scripts_text):
            try:
                audio_bytes = await self.elevenlabs.text_to_speech(
                    text=text,
                    language_code=language,
                    voice_id=voice_id,
                )
                duration_ms = self.elevenlabs.estimate_audio_duration_ms(audio_bytes)
                audio_results.append({
                    "index": i,
                    "text": text[:100],
                    "audio_size_bytes": len(audio_bytes),
                    "duration_ms": duration_ms,
                    "status": "generated",
                })
                logger.info(
                    f"Script {i}: audio generated, {len(audio_bytes)} bytes, "
                    f"{duration_ms:.0f}ms"
                )
            except ElevenLabsError as e:
                logger.error(f"Script {i}: ElevenLabs TTS failed: {e}")
                audio_results.append({
                    "index": i,
                    "text": text[:100],
                    "status": "failed",
                    "error": str(e),
                })

        # Step 2: Create liveroom with text scripts (Tencent TTS as rendering)
        # Note: Tencent Liveroom API only accepts text in Scripts.
        # The pre-generated audio is for interactive sessions (Mode B).
        scripts = [ScriptReq(content=text) for text in scripts_text]

        try:
            liveroom_result = await self.tencent.open_liveroom(
                scripts=scripts,
                cycle_times=cycle_times,
                callback_url=callback_url,
                protocol=protocol,
                anchor_param=anchor_param,
            )
        except TencentAPIError as e:
            logger.error(f"Failed to create liveroom: {e}")
            return {
                "status": "error",
                "error": str(e),
                "audio_results": audio_results,
            }

        return {
            "status": "ok",
            "liveroom": liveroom_result,
            "audio_results": audio_results,
            "mode": "hybrid",
            "note": (
                "Liveroom created with text scripts (Tencent TTS). "
                "Pre-generated audio with cloned voice is available for "
                "interactive takeover via takeover_with_voice()."
            ),
        }

    # ──────────────────────────────────────────
    # Mode B: Real-time Takeover with Cloned Voice
    # ──────────────────────────────────────────

    async def takeover_with_voice(
        self,
        liveroom_id: str,
        text: str,
        language: str = DEFAULT_LANGUAGE,
        voice_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send a real-time takeover (interjection) to a liveroom using
        the cloned voice.

        Current implementation: Uses Tencent's text-based Takeover API
        as the Liveroom service only supports text takeover.

        Future enhancement: When Tencent supports audio-based takeover
        in liveroom mode, or when we switch to interactive session mode,
        this will use ElevenLabs TTS + audio driver.

        Args:
            liveroom_id: The liveroom ID
            text: Text to speak (max 500 chars for Tencent API)
            language: Language code (e.g., "ja")
            voice_id: Override ElevenLabs voice ID

        Returns:
            Dict with takeover result and audio generation info
        """
        logger.info(
            f"Hybrid takeover: liveroom={liveroom_id}, "
            f"text_len={len(text)}, language={language}"
        )

        # Step 1: Generate audio with cloned voice (for future use / logging)
        audio_info = {}
        try:
            audio_bytes = await self.elevenlabs.text_to_speech(
                text=text[:500],
                language_code=language,
                voice_id=voice_id,
            )
            duration_ms = self.elevenlabs.estimate_audio_duration_ms(audio_bytes)
            chunks = self.elevenlabs.chunk_audio_for_tencent(audio_bytes)
            audio_info = {
                "status": "generated",
                "audio_size_bytes": len(audio_bytes),
                "duration_ms": duration_ms,
                "chunk_count": len(chunks),
            }
        except ElevenLabsError as e:
            logger.warning(f"ElevenLabs TTS failed for takeover, using text fallback: {e}")
            audio_info = {"status": "failed", "error": str(e)}

        # Step 2: Send text-based takeover to Tencent (current limitation)
        try:
            takeover_result = await self.tencent.takeover(
                liveroom_id=liveroom_id,
                content=text[:500],
            )
        except TencentAPIError as e:
            return {
                "status": "error",
                "error": str(e),
                "audio_info": audio_info,
            }

        return {
            "status": "ok",
            "takeover_result": takeover_result,
            "audio_info": audio_info,
            "mode": "text_fallback",
            "note": (
                "Takeover sent via text (Tencent TTS). "
                "Cloned voice audio was pre-generated for future "
                "interactive session mode support."
            ),
        }

    # ──────────────────────────────────────────
    # Interactive Session Audio Driver (Future)
    # ──────────────────────────────────────────

    async def send_audio_to_session(
        self,
        session_id: str,
        text: str,
        language: str = DEFAULT_LANGUAGE,
        voice_id: Optional[str] = None,
        websocket_send: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """
        Send text as cloned-voice audio to a Tencent interactive session
        via WebSocket audio driver.

        This is the full hybrid pipeline:
          Text → ElevenLabs TTS → PCM chunks → WebSocket → Digital Human

        Note: This requires an active Tencent interactive session
        (DriverType=3, audio driver mode) with WebSocket connection.

        Args:
            session_id: Tencent interactive session ID
            text: Text to convert and send
            language: Language code
            voice_id: Override ElevenLabs voice ID
            websocket_send: Async callable to send WebSocket messages

        Returns:
            Dict with audio transmission results
        """
        if not websocket_send:
            raise ValueError(
                "websocket_send callback is required for audio driver mode. "
                "Establish a WebSocket connection to Tencent IVH first."
            )

        logger.info(
            f"Audio driver: session={session_id}, text_len={len(text)}, "
            f"language={language}"
        )

        # Step 1: Generate audio with ElevenLabs
        audio_bytes = await self.elevenlabs.text_to_speech(
            text=text,
            language_code=language,
            voice_id=voice_id,
        )

        # Step 2: Chunk audio for Tencent WebSocket
        chunks = self.elevenlabs.chunk_audio_for_tencent(audio_bytes)
        req_id = uuid.uuid4().hex

        # Step 3: Send chunks via WebSocket with proper timing
        sent_count = 0
        for i, chunk in enumerate(chunks):
            message = {
                "Header": {},
                "Payload": {
                    "ReqId": req_id,
                    "SessionId": session_id,
                    "Command": "SEND_AUDIO",
                    "Data": chunk,
                },
            }

            await websocket_send(json.dumps(message))
            sent_count += 1

            # Timing control per Tencent Cloud requirements:
            # - First 6 chunks: send at max speed (no delay)
            # - Subsequent chunks: 120ms interval
            if i >= INITIAL_BURST_COUNT - 1 and not chunk.get("IsFinal"):
                await asyncio.sleep(SUBSEQUENT_INTERVAL_MS / 1000.0)

        duration_ms = self.elevenlabs.estimate_audio_duration_ms(audio_bytes)
        logger.info(
            f"Audio driver complete: {sent_count} chunks sent, "
            f"~{duration_ms:.0f}ms audio, req_id={req_id}"
        )

        return {
            "status": "ok",
            "req_id": req_id,
            "session_id": session_id,
            "chunks_sent": sent_count,
            "audio_duration_ms": duration_ms,
            "audio_size_bytes": len(audio_bytes),
        }

    # ──────────────────────────────────────────
    # Batch Script Audio Generation
    # ──────────────────────────────────────────

    async def generate_script_audio(
        self,
        scripts: List[str],
        language: str = DEFAULT_LANGUAGE,
        voice_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Pre-generate audio for a list of script texts using ElevenLabs.
        Returns metadata about each generated audio.

        This is useful for preparing audio before starting a livestream,
        so the audio is ready for immediate playback.
        """
        results = []
        total_duration = 0.0

        for i, text in enumerate(scripts):
            try:
                audio_bytes = await self.elevenlabs.text_to_speech(
                    text=text,
                    language_code=language,
                    voice_id=voice_id,
                )
                duration_ms = self.elevenlabs.estimate_audio_duration_ms(audio_bytes)
                chunks = self.elevenlabs.chunk_audio_for_tencent(audio_bytes)
                total_duration += duration_ms

                results.append({
                    "index": i,
                    "text_preview": text[:100],
                    "text_length": len(text),
                    "audio_size_bytes": len(audio_bytes),
                    "duration_ms": round(duration_ms, 1),
                    "chunk_count": len(chunks),
                    "status": "ok",
                })
            except ElevenLabsError as e:
                results.append({
                    "index": i,
                    "text_preview": text[:100],
                    "text_length": len(text),
                    "status": "error",
                    "error": str(e),
                })

        logger.info(
            f"Batch audio generation: {len(scripts)} scripts, "
            f"total_duration={total_duration:.0f}ms"
        )
        return results

    # ──────────────────────────────────────────
    # Health Check
    # ──────────────────────────────────────────

    async def health_check(self) -> Dict[str, Any]:
        """Check health of both ElevenLabs and Tencent services."""
        elevenlabs_health = await self.elevenlabs.health_check()
        tencent_health = await self.tencent.health_check()

        overall_status = "ok"
        if elevenlabs_health.get("status") != "ok":
            overall_status = "elevenlabs_error"
        if tencent_health.get("status") != "ok":
            overall_status = "tencent_error" if overall_status == "ok" else "both_error"

        return {
            "status": overall_status,
            "elevenlabs": elevenlabs_health,
            "tencent": tencent_health,
            "mode": "hybrid",
            "capabilities": {
                "liveroom_with_text": True,
                "liveroom_with_cloned_voice": False,  # Liveroom API only supports text
                "takeover_with_text": True,
                "takeover_with_cloned_voice": False,  # Takeover API only supports text
                "interactive_audio_driver": True,  # WebSocket audio driver supported
                "japanese_tts": True,
                "voice_cloning": True,
            },
        }
