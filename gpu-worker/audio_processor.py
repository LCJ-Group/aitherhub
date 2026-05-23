"""
Audio Processor Module for Liver Clone
=======================================
Handles:
  - VAD (Voice Activity Detection) on incoming RTMP audio
  - ElevenLabs STS (Speech-to-Speech) voice conversion
  - ElevenLabs TTS for auto-pilot mode
  - Audio mixing: replace original audio with converted/generated audio
  - Mode switching: manual → STS, silence → TTS auto-pilot

Architecture:
  RTMP input audio → VAD detection
    → If voice detected: extract audio → STS conversion → mix into output
    → If silence > threshold: TTS auto-pilot → generate speech → mix into output
"""
import asyncio
import io
import logging
import os
import struct
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Callable, List

import httpx
import numpy as np

logger = logging.getLogger("audio-processor")

# ── Configuration ──────────────────────────────────────────────────────────
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_BASE_URL = "https://api.elevenlabs.io/v1"
AITHERHUB_API_URL = os.getenv("AITHERHUB_API_URL", "https://aitherhubapi-production.up.railway.app")
AITHERHUB_ADMIN_KEY = os.getenv("AITHERHUB_ADMIN_KEY", "aither:hub")


class AudioMode(Enum):
    MANUAL = "manual"       # Person speaks → STS conversion only
    AUTO = "auto"           # AI generates all speech (TTS)
    HYBRID = "hybrid"       # Person speaks → STS; silence → TTS auto


@dataclass
class AudioConfig:
    voice_id: str = ""
    mode: AudioMode = AudioMode.HYBRID
    vad_threshold: float = 0.3          # Energy threshold for voice detection
    silence_timeout: float = 5.0        # Seconds of silence before auto-pilot kicks in
    voice_stability: float = 0.5
    voice_similarity: float = 0.75
    language: str = "en"
    session_id: str = ""                # Liver Clone session ID for speak queue


@dataclass
class AudioState:
    is_speaking: bool = False
    last_voice_time: float = 0.0
    auto_pilot_active: bool = False
    current_tts_text: str = ""
    speak_queue: List[str] = field(default_factory=list)
    processed_queue_ids: set = field(default_factory=set)


class AudioProcessor:
    """
    Manages audio processing pipeline for Liver Clone.
    Runs alongside the face swap stream, processing audio in parallel.
    """

    def __init__(self, config: AudioConfig):
        self.config = config
        self.state = AudioState()
        self._running = False
        self._ffmpeg_audio_proc: Optional[subprocess.Popen] = None
        self._vad_task: Optional[asyncio.Task] = None
        self._autopilot_task: Optional[asyncio.Task] = None
        self._http_client: Optional[httpx.AsyncClient] = None

    async def start(self, input_rtmp: str, output_pipe_path: str):
        """
        Start audio processing pipeline.
        
        Args:
            input_rtmp: RTMP URL to extract audio from
            output_pipe_path: Named pipe path where processed audio is written
        """
        self._running = True
        self._http_client = httpx.AsyncClient(timeout=30.0)
        self.state.last_voice_time = time.time()

        logger.info(f"[AudioProcessor] Starting - mode={self.config.mode.value}, "
                    f"voice_id={self.config.voice_id}, silence_timeout={self.config.silence_timeout}s")

        # Start VAD monitoring
        if self.config.mode in (AudioMode.MANUAL, AudioMode.HYBRID):
            self._vad_task = asyncio.create_task(self._vad_monitor_loop(input_rtmp))

        # Start auto-pilot if hybrid or auto mode
        if self.config.mode in (AudioMode.AUTO, AudioMode.HYBRID):
            self._autopilot_task = asyncio.create_task(self._autopilot_loop())

    async def stop(self):
        """Stop all audio processing."""
        self._running = False
        if self._vad_task:
            self._vad_task.cancel()
        if self._autopilot_task:
            self._autopilot_task.cancel()
        if self._ffmpeg_audio_proc:
            self._ffmpeg_audio_proc.terminate()
        if self._http_client:
            await self._http_client.aclose()
        logger.info("[AudioProcessor] Stopped")

    async def _vad_monitor_loop(self, input_rtmp: str):
        """
        Monitor incoming RTMP audio for voice activity.
        Uses FFmpeg to extract raw PCM audio and compute energy levels.
        """
        try:
            # Extract audio as raw PCM s16le mono 16kHz
            cmd = [
                "ffmpeg", "-y",
                "-i", input_rtmp,
                "-vn",                      # No video
                "-acodec", "pcm_s16le",
                "-ar", "16000",             # 16kHz sample rate
                "-ac", "1",                 # Mono
                "-f", "s16le",
                "pipe:1"
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            self._ffmpeg_audio_proc = proc

            # Read audio in 100ms chunks (1600 samples at 16kHz)
            chunk_size = 3200  # 1600 samples × 2 bytes per sample
            while self._running:
                data = await proc.stdout.read(chunk_size)
                if not data:
                    await asyncio.sleep(0.1)
                    continue

                # Compute RMS energy
                samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                rms = np.sqrt(np.mean(samples ** 2)) / 32768.0  # Normalize to 0-1

                is_voice = rms > self.config.vad_threshold
                now = time.time()

                if is_voice:
                    if not self.state.is_speaking:
                        logger.info(f"[AudioProcessor] Voice detected (RMS={rms:.4f})")
                        self.state.is_speaking = True
                        self.state.auto_pilot_active = False
                    self.state.last_voice_time = now
                else:
                    if self.state.is_speaking:
                        # Check if silence duration exceeds threshold
                        silence_duration = now - self.state.last_voice_time
                        if silence_duration > 1.0:  # 1 second of silence = stopped speaking
                            logger.info(f"[AudioProcessor] Voice stopped (silence={silence_duration:.1f}s)")
                            self.state.is_speaking = False

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[AudioProcessor] VAD monitor error: {e}")
        finally:
            if self._ffmpeg_audio_proc:
                self._ffmpeg_audio_proc.terminate()

    async def _autopilot_loop(self):
        """
        Auto-pilot loop: when silence exceeds threshold, fetch text from
        speak queue and generate TTS audio.
        """
        try:
            while self._running:
                await asyncio.sleep(1.0)

                if self.state.is_speaking:
                    continue

                now = time.time()
                silence_duration = now - self.state.last_voice_time

                if silence_duration < self.config.silence_timeout:
                    continue

                if not self.state.auto_pilot_active:
                    logger.info(f"[AudioProcessor] Auto-pilot activated "
                                f"(silence={silence_duration:.1f}s > threshold={self.config.silence_timeout}s)")
                    self.state.auto_pilot_active = True

                # Fetch next text from speak queue
                text = await self._fetch_next_speak_text()
                if not text:
                    await asyncio.sleep(2.0)
                    continue

                # Generate TTS and play
                logger.info(f"[AudioProcessor] Auto-pilot speaking: {text[:50]}...")
                await self._generate_and_inject_tts(text)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[AudioProcessor] Auto-pilot error: {e}")

    async def _fetch_next_speak_text(self) -> Optional[str]:
        """Fetch next text from AitherHub speak queue."""
        if not self.config.session_id:
            return None
        try:
            url = f"{AITHERHUB_API_URL}/api/v1/liver-clone/{self.config.session_id}/speak-queue"
            resp = await self._http_client.get(
                url,
                headers={"X-Admin-Key": AITHERHUB_ADMIN_KEY}
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                for item in items:
                    item_id = item.get("id", "")
                    if item_id not in self.state.processed_queue_ids:
                        self.state.processed_queue_ids.add(item_id)
                        return item.get("text", "")
            return None
        except Exception as e:
            logger.error(f"[AudioProcessor] Failed to fetch speak queue: {e}")
            return None

    async def _generate_and_inject_tts(self, text: str):
        """Generate TTS audio using ElevenLabs and inject into output stream."""
        if not self.config.voice_id or not ELEVENLABS_API_KEY:
            logger.warning("[AudioProcessor] No voice_id or API key for TTS")
            return

        try:
            url = f"{ELEVENLABS_BASE_URL}/text-to-speech/{self.config.voice_id}/stream"
            payload = {
                "text": text,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {
                    "stability": self.config.voice_stability,
                    "similarity_boost": self.config.voice_similarity,
                }
            }
            headers = {
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            }

            resp = await self._http_client.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
                # Save audio to temp file
                audio_path = Path(tempfile.mktemp(suffix=".mp3", dir="/workspace/tmp"))
                audio_path.write_bytes(resp.content)
                logger.info(f"[AudioProcessor] TTS audio generated: {audio_path} ({len(resp.content)} bytes)")

                # Inject audio into output stream via named pipe or direct ffmpeg
                await self._inject_audio_to_stream(str(audio_path))

                # Cleanup
                audio_path.unlink(missing_ok=True)
            else:
                logger.error(f"[AudioProcessor] TTS failed: {resp.status_code} {resp.text[:200]}")

        except Exception as e:
            logger.error(f"[AudioProcessor] TTS generation error: {e}")

    async def _inject_audio_to_stream(self, audio_path: str):
        """
        Inject generated audio into the output RTMP stream.
        This replaces the silent audio track with the TTS audio.
        """
        # For now, we signal the main stream process to mix this audio
        # The actual mixing is handled by the stream manager
        if self._on_audio_ready:
            await self._on_audio_ready(audio_path)

    def set_audio_ready_callback(self, callback: Callable):
        """Set callback for when audio is ready to be injected."""
        self._on_audio_ready = callback

    async def convert_voice_sts(self, audio_data: bytes) -> Optional[bytes]:
        """
        Convert voice using ElevenLabs Speech-to-Speech (STS).
        Used in manual/hybrid mode when person is speaking.
        """
        if not self.config.voice_id or not ELEVENLABS_API_KEY:
            return None

        try:
            url = f"{ELEVENLABS_BASE_URL}/speech-to-speech/{self.config.voice_id}/stream"
            headers = {
                "xi-api-key": ELEVENLABS_API_KEY,
                "Accept": "audio/mpeg",
            }
            # Send as multipart form
            files = {
                "audio": ("input.wav", io.BytesIO(audio_data), "audio/wav"),
            }
            data = {
                "model_id": "eleven_english_sts_v2",
                "voice_settings": json.dumps({
                    "stability": self.config.voice_stability,
                    "similarity_boost": self.config.voice_similarity,
                }),
            }

            resp = await self._http_client.post(url, headers=headers, files=files, data=data)
            if resp.status_code == 200:
                return resp.content
            else:
                logger.error(f"[AudioProcessor] STS failed: {resp.status_code}")
                return None

        except Exception as e:
            logger.error(f"[AudioProcessor] STS conversion error: {e}")
            return None

    def get_status(self) -> dict:
        """Get current audio processor status."""
        return {
            "mode": self.config.mode.value,
            "is_speaking": self.state.is_speaking,
            "auto_pilot_active": self.state.auto_pilot_active,
            "silence_duration": time.time() - self.state.last_voice_time if self.state.last_voice_time else 0,
            "silence_timeout": self.config.silence_timeout,
            "voice_id": self.config.voice_id,
            "processed_count": len(self.state.processed_queue_ids),
        }


# ── Import json for STS voice_settings serialization ──
import json
