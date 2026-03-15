"""
ElevenLabs TTS & Speech-to-Speech Service for AitherHub

This module provides a Python client for the ElevenLabs API, enabling:
  1. Text-to-Speech (TTS) with cloned voice — for digital human livestreams
  2. Speech-to-Speech (STS) voice conversion — for video face swap pipeline

Architecture:
  - TTS Flow: Text → ElevenLabs TTS (cloned voice) → Audio → Tencent Cloud (lip sync)
  - STS Flow: Staff audio → ElevenLabs STS (voice clone) → Influencer audio

Key Features:
  - Text-to-speech with cloned voice (supports Japanese)
  - Speech-to-speech voice conversion (maintain emotion & timing)
  - Streaming audio generation for low-latency scenarios
  - PCM output format compatible with Tencent Cloud audio driver
  - Audio chunking for WebSocket transmission
  - Voice management (list, get voice details)

Reference:
  https://elevenlabs.io/docs/api-reference/text-to-speech/convert
  https://elevenlabs.io/docs/api-reference/speech-to-speech/convert
"""
from __future__ import annotations

import base64
import io
import logging
import os
import struct
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_BASE_URL = os.getenv("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io")
ELEVENLABS_DEFAULT_MODEL = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
ELEVENLABS_DEFAULT_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")
ELEVENLABS_STS_MODEL = os.getenv("ELEVENLABS_STS_MODEL_ID", "eleven_multilingual_sts_v2")

# Tencent Cloud audio driver requires: PCM, 16kHz, 16bit, mono
# ElevenLabs output format that matches: pcm_16000
TENCENT_COMPATIBLE_FORMAT = "pcm_16000"

# Audio chunking parameters for Tencent Cloud WebSocket
# Each chunk = 160ms of audio at 16kHz, 16bit, mono = 5120 bytes
CHUNK_DURATION_MS = 160
CHUNK_SIZE_BYTES = 5120  # 16000 Hz * 16 bit / 8 * 0.16s = 5120 bytes
INITIAL_BURST_COUNT = 6  # First 6 chunks sent at max speed
SUBSEQUENT_INTERVAL_MS = 120  # 120ms interval for subsequent chunks


# ──────────────────────────────────────────────
# Main Service Class
# ──────────────────────────────────────────────

class ElevenLabsTTSService:
    """
    Client for the ElevenLabs TTS & Speech-to-Speech API.

    This service generates speech audio from text or converts speech
    from one voice to another using a cloned voice.

    Usage (TTS):
        service = ElevenLabsTTSService()
        audio_bytes = await service.text_to_speech("こんにちは、皆さん！")

    Usage (STS - Voice Conversion):
        service = ElevenLabsTTSService()
        converted = await service.speech_to_speech(audio_bytes)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        voice_id: Optional[str] = None,
        model_id: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.api_key = api_key or ELEVENLABS_API_KEY
        self.voice_id = voice_id or ELEVENLABS_DEFAULT_VOICE_ID
        self.model_id = model_id or ELEVENLABS_DEFAULT_MODEL
        self.base_url = base_url or ELEVENLABS_BASE_URL

        if not self.api_key:
            logger.warning(
                "ElevenLabsTTSService: ELEVENLABS_API_KEY not configured. "
                "API calls will fail."
            )

    def _headers(self) -> Dict[str, str]:
        return {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json",
        }

    def _headers_multipart(self) -> Dict[str, str]:
        """Headers for multipart/form-data requests (no Content-Type)."""
        return {
            "xi-api-key": self.api_key,
        }

    # ──────────────────────────────────────────
    # Core TTS: Text → PCM Audio
    # ──────────────────────────────────────────

    async def text_to_speech(
        self,
        text: str,
        voice_id: Optional[str] = None,
        model_id: Optional[str] = None,
        language_code: Optional[str] = None,
        voice_settings: Optional[Dict[str, Any]] = None,
        output_format: str = TENCENT_COMPATIBLE_FORMAT,
    ) -> bytes:
        """
        Convert text to speech audio using ElevenLabs API.

        Args:
            text: The text to convert to speech
            voice_id: Override voice ID (uses cloned voice by default)
            model_id: Override model ID (default: eleven_multilingual_v2)
            language_code: Language code (e.g., "ja" for Japanese)
            voice_settings: Override voice settings (stability, similarity_boost, etc.)
            output_format: Audio format (default: pcm_16000 for Tencent compatibility)

        Returns:
            Raw audio bytes in the specified format (PCM 16kHz 16bit mono by default)
        """
        vid = voice_id or self.voice_id
        mid = model_id or self.model_id

        if not vid:
            raise ElevenLabsError("No voice_id configured. Set ELEVENLABS_VOICE_ID or pass voice_id.")

        url = f"{self.base_url}/v1/text-to-speech/{vid}"
        params = {"output_format": output_format}

        body: Dict[str, Any] = {
            "text": text,
            "model_id": mid,
        }
        if language_code:
            body["language_code"] = language_code
        if voice_settings:
            body["voice_settings"] = voice_settings

        logger.info(
            f"ElevenLabs TTS: text_len={len(text)}, voice={vid[:8]}..., "
            f"model={mid}, lang={language_code or 'auto'}, format={output_format}"
        )

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                url,
                json=body,
                headers=self._headers(),
                params=params,
            )

        if response.status_code != 200:
            error_detail = response.text[:500]
            logger.error(
                f"ElevenLabs TTS error: status={response.status_code}, "
                f"detail={error_detail}"
            )
            raise ElevenLabsError(
                f"HTTP {response.status_code}: {error_detail}",
                status_code=response.status_code,
            )

        audio_bytes = response.content
        duration_ms = len(audio_bytes) / (16000 * 2) * 1000  # PCM 16kHz 16bit
        logger.info(
            f"ElevenLabs TTS success: {len(audio_bytes)} bytes, "
            f"~{duration_ms:.0f}ms audio"
        )
        return audio_bytes

    # ──────────────────────────────────────────
    # Streaming TTS: Text → PCM Audio Stream
    # ──────────────────────────────────────────

    async def text_to_speech_stream(
        self,
        text: str,
        voice_id: Optional[str] = None,
        model_id: Optional[str] = None,
        language_code: Optional[str] = None,
        output_format: str = TENCENT_COMPATIBLE_FORMAT,
    ) -> AsyncIterator[bytes]:
        """
        Stream text-to-speech audio from ElevenLabs API.
        Returns an async iterator of audio chunks.
        """
        vid = voice_id or self.voice_id
        mid = model_id or self.model_id

        if not vid:
            raise ElevenLabsError("No voice_id configured.")

        url = f"{self.base_url}/v1/text-to-speech/{vid}/stream"
        params = {"output_format": output_format}

        body: Dict[str, Any] = {
            "text": text,
            "model_id": mid,
        }
        if language_code:
            body["language_code"] = language_code

        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                url,
                json=body,
                headers=self._headers(),
                params=params,
            ) as response:
                if response.status_code != 200:
                    error_body = await response.aread()
                    raise ElevenLabsError(
                        f"HTTP {response.status_code}: {error_body.decode()[:500]}",
                        status_code=response.status_code,
                    )
                async for chunk in response.aiter_bytes(chunk_size=CHUNK_SIZE_BYTES):
                    yield chunk

    # ──────────────────────────────────────────
    # Speech-to-Speech: Voice Conversion
    # ──────────────────────────────────────────

    async def speech_to_speech(
        self,
        audio_data: bytes,
        voice_id: Optional[str] = None,
        model_id: Optional[str] = None,
        output_format: str = "mp3_44100_128",
        remove_background_noise: bool = False,
        voice_settings: Optional[Dict[str, Any]] = None,
    ) -> bytes:
        """
        Convert speech from one voice to another using ElevenLabs STS API.

        This takes audio of someone speaking and converts it to sound like
        the target voice (e.g., influencer's cloned voice), while preserving
        the original emotion, timing, and delivery.

        Args:
            audio_data: Raw audio bytes (MP3, WAV, or other supported format)
            voice_id: Target voice ID (the voice to convert to)
            model_id: STS model ID (default: eleven_multilingual_sts_v2)
            output_format: Output audio format (default: mp3_44100_128)
            remove_background_noise: Remove background noise from input
            voice_settings: Override voice settings as JSON string

        Returns:
            Converted audio bytes in the specified format
        """
        vid = voice_id or self.voice_id
        mid = model_id or ELEVENLABS_STS_MODEL

        if not vid:
            raise ElevenLabsError(
                "No voice_id configured. Set ELEVENLABS_VOICE_ID or pass voice_id."
            )

        url = f"{self.base_url}/v1/speech-to-speech/{vid}"
        params = {"output_format": output_format}

        # Build multipart form data
        files = {
            "audio": ("input_audio.mp3", io.BytesIO(audio_data), "audio/mpeg"),
        }
        data: Dict[str, str] = {
            "model_id": mid,
            "remove_background_noise": str(remove_background_noise).lower(),
        }
        if voice_settings:
            import json
            data["voice_settings"] = json.dumps(voice_settings)

        audio_size_mb = len(audio_data) / (1024 * 1024)
        logger.info(
            f"ElevenLabs STS: audio_size={audio_size_mb:.1f}MB, "
            f"voice={vid[:8]}..., model={mid}, format={output_format}"
        )

        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                url,
                files=files,
                data=data,
                headers=self._headers_multipart(),
                params=params,
            )

        if response.status_code != 200:
            error_detail = response.text[:500]
            logger.error(
                f"ElevenLabs STS error: status={response.status_code}, "
                f"detail={error_detail}"
            )
            raise ElevenLabsError(
                f"HTTP {response.status_code}: {error_detail}",
                status_code=response.status_code,
            )

        output_bytes = response.content
        output_size_mb = len(output_bytes) / (1024 * 1024)
        logger.info(
            f"ElevenLabs STS success: {output_size_mb:.2f}MB output audio"
        )
        return output_bytes

    async def speech_to_speech_from_file(
        self,
        file_path: str,
        voice_id: Optional[str] = None,
        model_id: Optional[str] = None,
        output_format: str = "mp3_44100_128",
        remove_background_noise: bool = False,
    ) -> bytes:
        """
        Convert speech from a file path.
        Convenience wrapper around speech_to_speech().
        """
        with open(file_path, "rb") as f:
            audio_data = f.read()
        return await self.speech_to_speech(
            audio_data=audio_data,
            voice_id=voice_id,
            model_id=model_id,
            output_format=output_format,
            remove_background_noise=remove_background_noise,
        )

    # ──────────────────────────────────────────
    # Audio Chunking for Tencent Cloud
    # ──────────────────────────────────────────

    @staticmethod
    def chunk_audio_for_tencent(
        audio_bytes: bytes,
    ) -> List[Dict[str, Any]]:
        """
        Split PCM audio into chunks formatted for Tencent Cloud WebSocket
        audio driver (SEND_AUDIO command).

        Each chunk is 160ms (5120 bytes) of PCM 16kHz 16bit mono audio.
        Returns a list of dicts ready for WebSocket transmission:
          [{"Audio": base64_str, "Seq": 1, "IsFinal": False}, ...]

        The final entry always has IsFinal=True and empty Audio (as required
        by Tencent Cloud to signal end of audio stream).
        """
        chunks = []
        total_len = len(audio_bytes)
        offset = 0
        seq = 1

        while offset < total_len:
            end = min(offset + CHUNK_SIZE_BYTES, total_len)
            chunk_data = audio_bytes[offset:end]
            chunks.append({
                "Audio": base64.b64encode(chunk_data).decode("utf-8"),
                "Seq": seq,
                "IsFinal": False,
            })
            offset = end
            seq += 1

        # Tencent Cloud requires a final empty packet with IsFinal=True
        chunks.append({
            "Audio": "",
            "Seq": seq,
            "IsFinal": True,
        })

        logger.info(
            f"Audio chunked for Tencent: {len(chunks)} chunks "
            f"({total_len} bytes, ~{total_len / (16000 * 2) * 1000:.0f}ms)"
        )
        return chunks

    @staticmethod
    def estimate_audio_duration_ms(audio_bytes: bytes) -> float:
        """Estimate duration of PCM 16kHz 16bit mono audio in milliseconds."""
        return len(audio_bytes) / (16000 * 2) * 1000

    # ──────────────────────────────────────────
    # Voice Management
    # ──────────────────────────────────────────

    async def list_voices(self) -> List[Dict[str, Any]]:
        """List all available voices in the ElevenLabs account."""
        url = f"{self.base_url}/v1/voices"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=self._headers())

        if response.status_code != 200:
            raise ElevenLabsError(
                f"HTTP {response.status_code}: {response.text[:500]}",
                status_code=response.status_code,
            )

        data = response.json()
        voices = data.get("voices", [])
        logger.info(f"ElevenLabs voices: {len(voices)} available")
        return voices

    async def get_voice(self, voice_id: str) -> Dict[str, Any]:
        """Get details of a specific voice."""
        url = f"{self.base_url}/v1/voices/{voice_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=self._headers())

        if response.status_code != 200:
            raise ElevenLabsError(
                f"HTTP {response.status_code}: {response.text[:500]}",
                status_code=response.status_code,
            )

        return response.json()

    # ──────────────────────────────────────────
    # Health Check
    # ──────────────────────────────────────────

    async def health_check(self) -> Dict[str, Any]:
        """Quick health check by listing voices."""
        try:
            voices = await self.list_voices()
            cloned = [v for v in voices if v.get("category") == "cloned"]
            return {
                "status": "ok",
                "total_voices": len(voices),
                "cloned_voices": len(cloned),
                "default_voice_id": self.voice_id[:8] + "..." if self.voice_id else "NOT_SET",
                "model": self.model_id,
            }
        except ElevenLabsError as e:
            return {
                "status": "error",
                "error": str(e),
            }


# ──────────────────────────────────────────────
# Custom Exception
# ──────────────────────────────────────────────

class ElevenLabsError(Exception):
    """Exception raised when an ElevenLabs API call fails."""

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code
