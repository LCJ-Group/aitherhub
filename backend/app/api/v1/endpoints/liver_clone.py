"""
Liver Clone API Endpoints
=========================

Real-time Face Swap + Voice Conversion Live Streaming control endpoints.

Endpoints:
  POST /api/v1/liver-clone/sessions              — Create new session
  POST /api/v1/liver-clone/sessions/{id}/start   — Start streaming
  POST /api/v1/liver-clone/sessions/{id}/stop    — Stop streaming
  GET  /api/v1/liver-clone/sessions/{id}         — Get session status
  GET  /api/v1/liver-clone/sessions              — List all sessions
  DELETE /api/v1/liver-clone/sessions/{id}       — Delete session
  PATCH /api/v1/liver-clone/sessions/{id}/config — Update config
  POST /api/v1/liver-clone/sessions/{id}/comment — Respond to comment
  POST /api/v1/liver-clone/sessions/{id}/speak   — Push TTS text
  POST /api/v1/liver-clone/sessions/{id}/vad     — VAD event webhook
  GET  /api/v1/liver-clone/sessions/{id}/metrics — Stream metrics
  GET  /api/v1/liver-clone/health                — Health check
"""
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import logging
import asyncio

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/liver-clone", tags=["liver-clone"])


# ============================================================
# Request Models
# ============================================================

class CreateSessionRequest(BaseModel):
    """Create a new Liver Clone session."""
    # Face swap
    source_face_url: Optional[str] = None
    source_face_base64: Optional[str] = None
    face_swap_quality: str = "high"  # fast, balanced, high, ultra

    # RTMP
    input_rtmp: str = ""        # From OBS (body double's stream)
    output_rtmp: str = ""       # To streaming platform

    # Voice
    voice_id: str = ""          # ElevenLabs voice ID
    voice_stability: float = 0.5
    voice_similarity: float = 0.75

    # Mode
    mode: str = "hybrid"        # manual, auto, hybrid
    vad_threshold: float = 0.3
    silence_timeout: float = 5.0

    # Auto-pilot persona
    persona_name: str = ""
    persona_style: str = ""
    language: str = "en"
    products: List[Dict[str, Any]] = []
    opening_script: str = ""

    # Stream settings
    resolution: str = "720p"
    fps: int = 30


class UpdateConfigRequest(BaseModel):
    """Update session configuration."""
    source_face_url: Optional[str] = None
    source_face_base64: Optional[str] = None
    face_swap_quality: Optional[str] = None
    voice_id: Optional[str] = None
    voice_stability: Optional[float] = None
    voice_similarity: Optional[float] = None
    mode: Optional[str] = None
    vad_threshold: Optional[float] = None
    silence_timeout: Optional[float] = None
    persona_name: Optional[str] = None
    persona_style: Optional[str] = None
    language: Optional[str] = None


class CommentRequest(BaseModel):
    """Respond to a viewer comment."""
    comment: str
    username: str = ""


class SpeakRequest(BaseModel):
    """Push text to be spoken via TTS."""
    text: str


class VADEventRequest(BaseModel):
    """VAD event from GPU Worker."""
    is_speaking: bool
    confidence: float = 0.0


# ============================================================
# Endpoints
# ============================================================

@router.post("/sessions")
async def create_session(req: CreateSessionRequest):
    """
    Create a new Liver Clone session with configuration.

    The session is created in CONFIGURING state.
    Call /start to begin streaming.
    """
    from app.services.liver_clone_service import get_liver_clone_service

    service = get_liver_clone_service()
    try:
        result = await service.create_session(req.model_dump())
        return result
    except Exception as e:
        logger.exception("[LiverClone API] Failed to create session")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sessions/{session_id}/start")
async def start_session(session_id: str):
    """
    Start the Liver Clone pipeline for a configured session.

    This will:
    1. Set source face on GPU Worker
    2. Start face swap stream (RTMP in → face swap → RTMP out)
    3. Start audio processing (VAD + STS/TTS)
    4. Start auto-pilot if mode is AUTO or HYBRID
    """
    from app.services.liver_clone_service import get_liver_clone_service

    service = get_liver_clone_service()
    try:
        result = await service.start_session(session_id)
        if result.get("status") == "error":
            raise HTTPException(status_code=500, detail=result.get("error"))
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[LiverClone API] Failed to start session {session_id}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sessions/{session_id}/stop")
async def stop_session(session_id: str):
    """Stop a running Liver Clone session gracefully."""
    from app.services.liver_clone_service import get_liver_clone_service

    service = get_liver_clone_service()
    try:
        result = await service.stop_session(session_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"[LiverClone API] Failed to stop session {session_id}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}")
async def get_session_status(session_id: str):
    """Get current session status and metrics."""
    from app.services.liver_clone_service import get_liver_clone_service

    service = get_liver_clone_service()
    try:
        return service.get_session_status(session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/sessions")
async def list_sessions():
    """List all active Liver Clone sessions."""
    from app.services.liver_clone_service import get_liver_clone_service

    service = get_liver_clone_service()
    return {"sessions": service.list_sessions()}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a session (stops it first if running)."""
    from app.services.liver_clone_service import get_liver_clone_service

    service = get_liver_clone_service()
    try:
        result = await service.delete_session(session_id)
        return result
    except Exception as e:
        logger.exception(f"[LiverClone API] Failed to delete session {session_id}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/sessions/{session_id}/config")
async def update_config(session_id: str, req: UpdateConfigRequest):
    """Update session configuration while running."""
    from app.services.liver_clone_service import get_liver_clone_service

    service = get_liver_clone_service()
    # Only include non-None fields
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    try:
        result = await service.update_config(session_id, updates)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"[LiverClone API] Failed to update config for {session_id}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sessions/{session_id}/comment")
async def respond_to_comment(session_id: str, req: CommentRequest):
    """
    Generate and speak a response to a viewer comment.
    Uses AI to generate a contextual response, then speaks it via TTS.
    """
    from app.services.liver_clone_service import get_liver_clone_service

    service = get_liver_clone_service()
    try:
        result = await service.respond_to_comment(
            session_id, req.comment, req.username
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"[LiverClone API] Comment response failed for {session_id}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sessions/{session_id}/speak")
async def push_speak_text(session_id: str, req: SpeakRequest):
    """
    Push text to be spoken via TTS.
    Used when the person is silent and you want the AI to say something specific.
    """
    from app.services.liver_clone_service import get_liver_clone_service

    service = get_liver_clone_service()
    try:
        result = await service.push_tts_text(session_id, req.text)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"[LiverClone API] TTS push failed for {session_id}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sessions/{session_id}/vad")
async def vad_event(session_id: str, req: VADEventRequest):
    """
    VAD (Voice Activity Detection) event webhook.
    Called by the GPU Worker when voice activity state changes.
    """
    from app.services.liver_clone_service import get_liver_clone_service

    service = get_liver_clone_service()
    try:
        result = await service.on_vad_event(session_id, req.is_speaking)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"[LiverClone API] VAD event failed for {session_id}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}/metrics")
async def get_stream_metrics(session_id: str):
    """Get real-time stream metrics from GPU Worker."""
    from app.services.liver_clone_service import get_liver_clone_service

    service = get_liver_clone_service()
    try:
        result = await service.get_stream_metrics(session_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"[LiverClone API] Metrics failed for {session_id}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def health_check():
    """
    Health check for Liver Clone system.
    Checks GPU Worker, ElevenLabs, and active sessions.
    """
    from app.services.liver_clone_service import get_liver_clone_service

    service = get_liver_clone_service()
    try:
        return await service.health_check()
    except Exception as e:
        logger.exception("[LiverClone API] Health check failed")
        return {
            "status": "error",
            "error": str(e),
        }


# ============================================================
# Preview Endpoints
# ============================================================

class PreviewFrameRequest(BaseModel):
    """Request to process a single preview frame."""
    image_base64: str = ""
    image_url: str = ""


@router.post("/preview/frame")
async def preview_frame(req: PreviewFrameRequest):
    """
    Process a single webcam frame through face swap and return the result.
    Used for testing face swap quality before starting a stream.
    
    Requires source face to be set first via session creation.
    """
    from app.services.liver_clone_service import get_liver_clone_service

    service = get_liver_clone_service()
    try:
        result = await service.face_swap.preview_frame(req.image_base64)
        return result
    except Exception as e:
        logger.exception("[LiverClone API] Preview frame failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/preview/ws-url")
async def get_preview_ws_url():
    """
    Get the WebSocket URL for real-time preview streaming.
    Returns the backend's own WebSocket proxy URL (avoids RunPod Proxy 403 on WS).
    """
    # Return the backend's own WebSocket proxy endpoint
    import os
    backend_base = os.getenv(
        "BACKEND_PUBLIC_URL",
        "https://aitherhubapi-cpcjcnezbgf5f7e2.japaneast-01.azurewebsites.net"
    )
    ws_base = backend_base.replace("https://", "wss://").replace("http://", "ws://")
    return {"ws_url": f"{ws_base}/api/v1/liver-clone/preview/ws"}


@router.websocket("/preview/ws")
async def preview_ws_proxy(websocket: WebSocket):
    """
    WebSocket proxy for real-time face swap preview.
    Uses HTTP POST /api/swap-frame to the GPU Worker for each frame,
    bypassing RunPod Proxy's Cloudflare 403 on WebSocket upgrades.

    Architecture: async pipeline with latest-frame-only processing.
    - Receiver task: drains client frames, keeps only the latest
    - Processor task: picks up latest frame, sends to GPU Worker, returns result
    - This ensures GPU Worker always processes the most recent frame
      and slow processing doesn't cause frame queue buildup.

    Protocol:
      Client sends: JSON text {type:"frame", data: base64} or {type:"config", data: {...}}
      Server responds: binary JPEG processed frame (or text JSON error/status)
    """
    import httpx
    import base64
    import json
    import asyncio
    from app.services.liver_clone_service import get_liver_clone_service

    await websocket.accept()
    logger.info("[Preview WS Proxy] Client connected (async-pipeline mode)")

    service = get_liver_clone_service()
    try:
        worker_url = await service.face_swap._get_worker_url()
    except Exception as e:
        logger.error(f"[Preview WS Proxy] Cannot get worker URL: {e}")
        await websocket.close(code=1011, reason="GPU Worker unavailable")
        return

    swap_url = f"{worker_url}/api/swap-frame"
    headers = {"X-Api-Key": service.face_swap.api_key}

    # Shared state between receiver and processor
    latest_frame: asyncio.Event = asyncio.Event()
    latest_frame_data: list = [None]  # mutable container for latest base64 frame
    client_disconnected = asyncio.Event()
    consecutive_errors = 0
    MAX_CONSECUTIVE_ERRORS = 5

    async def receiver_task():
        """Drain frames from client, keep only the latest."""
        nonlocal consecutive_errors
        try:
            while not client_disconnected.is_set():
                try:
                    msg = await websocket.receive()
                except WebSocketDisconnect:
                    client_disconnected.set()
                    return

                if msg["type"] == "websocket.disconnect":
                    client_disconnected.set()
                    return

                if msg["type"] != "websocket.receive":
                    continue

                # Handle text messages
                if "text" in msg and msg["text"]:
                    text_data = msg["text"]
                    try:
                        cmd = json.loads(text_data)
                        msg_type = cmd.get("type", "")

                        # Forward config commands directly (low latency)
                        if msg_type == "config":
                            try:
                                async with httpx.AsyncClient(
                                    timeout=httpx.Timeout(connect=3.0, read=10.0, write=5.0, pool=3.0)
                                ) as cfg_client:
                                    config_url = f"{worker_url}/api/config"
                                    resp = await cfg_client.post(
                                        config_url,
                                        json=cmd.get("data", {}),
                                        headers=headers,
                                    )
                                    await websocket.send_text(json.dumps({
                                        "type": "config_ack",
                                        "status": resp.status_code
                                    }))
                            except Exception as cfg_err:
                                logger.warning(f"[Preview WS Proxy] config error: {cfg_err}")
                                await websocket.send_text(json.dumps({
                                    "type": "config_ack",
                                    "status": 500
                                }))
                            continue

                        # Store latest frame (overwrite previous)
                        if msg_type == "frame" and cmd.get("data"):
                            latest_frame_data[0] = cmd["data"]
                            latest_frame.set()
                            continue

                    except json.JSONDecodeError:
                        continue
                    except Exception:
                        continue

                # Handle binary frames (fallback)
                frame_bytes = msg.get("bytes")
                if frame_bytes:
                    latest_frame_data[0] = base64.b64encode(frame_bytes).decode("ascii")
                    latest_frame.set()

        except Exception as e:
            logger.error(f"[Preview WS Proxy] Receiver error: {e}")
            client_disconnected.set()

    async def processor_task():
        """Process latest frame via GPU Worker."""
        nonlocal consecutive_errors
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=3.0, read=8.0, write=5.0, pool=3.0)
        ) as http_client:
            while not client_disconnected.is_set():
                # Wait for a new frame (with timeout to check disconnect)
                try:
                    await asyncio.wait_for(latest_frame.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                # Grab the latest frame and clear the event
                frame_b64 = latest_frame_data[0]
                latest_frame.clear()

                if not frame_b64 or client_disconnected.is_set():
                    continue

                try:
                    resp = await http_client.post(
                        swap_url,
                        json={"image_base64": frame_b64},
                        headers=headers,
                    )

                    if resp.status_code == 200:
                        result = resp.json()
                        if "image_base64" in result:
                            result_bytes = base64.b64decode(result["image_base64"])
                            if not client_disconnected.is_set():
                                await websocket.send_bytes(result_bytes)
                            consecutive_errors = 0
                        elif "error" in result:
                            consecutive_errors += 1
                            if consecutive_errors <= 3:
                                await websocket.send_text(json.dumps({
                                    "type": "error",
                                    "message": str(result["error"])[:200]
                                }))
                    else:
                        consecutive_errors += 1
                        detail = resp.text[:200]
                        try:
                            detail = resp.json().get("detail", detail)
                        except Exception:
                            pass
                        if consecutive_errors <= 3:
                            logger.warning(
                                f"[Preview WS Proxy] Worker returned {resp.status_code}: {detail}"
                            )
                            await websocket.send_text(json.dumps({
                                "type": "error",
                                "message": f"Worker error {resp.status_code}: {detail}"
                            }))

                except httpx.TimeoutException:
                    consecutive_errors += 1
                    if consecutive_errors <= 3:
                        logger.warning("[Preview WS Proxy] swap-frame timeout")
                    # Don't send error for timeouts - just skip frame
                    continue
                except httpx.ConnectError:
                    consecutive_errors += 1
                    if consecutive_errors == 1:
                        logger.error("[Preview WS Proxy] Cannot connect to GPU Worker")
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "message": "GPU Worker connection failed"
                        }))
                    # Wait before retry to avoid hammering
                    await asyncio.sleep(2.0)
                    continue
                except Exception as e:
                    consecutive_errors += 1
                    error_msg = str(e)[:200]
                    logger.error(f"[Preview WS Proxy] swap-frame error: {error_msg}")
                    if consecutive_errors <= 3:
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "message": error_msg
                        }))

                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    logger.error(
                        f"[Preview WS Proxy] {MAX_CONSECUTIVE_ERRORS} consecutive errors, closing"
                    )
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "message": "GPU Worker not responding. Please try again later."
                    }))
                    client_disconnected.set()
                    break

    # Run receiver and processor concurrently
    try:
        await asyncio.gather(
            receiver_task(),
            processor_task(),
        )
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"[Preview WS Proxy] Unexpected error: {e}")
        try:
            await websocket.close(code=1011, reason=str(e)[:120])
        except Exception:
            pass

    logger.info("[Preview WS Proxy] Session closed")


@router.post("/preview/set-source")
async def preview_set_source(req: PreviewFrameRequest):
    """
    Set the source face for preview mode.
    This uploads the face to the GPU Worker without creating a full session.
    Accepts image_base64 or image_url.
    """
    from app.services.liver_clone_service import get_liver_clone_service

    service = get_liver_clone_service()
    try:
        if req.image_base64:
            result = await service.face_swap.set_source_face(
                image_base64=req.image_base64
            )
        elif req.image_url:
            result = await service.face_swap.set_source_face(
                image_url=req.image_url
            )
        else:
            raise HTTPException(status_code=400, detail="image_base64 or image_url required")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[LiverClone API] Preview set-source failed")
        raise HTTPException(status_code=500, detail=str(e))


class PreviewSpeakRequest(BaseModel):
    """Request to generate TTS audio for preview mode."""
    text: str
    voice_id: str = ""
    voice_stability: float = 0.5
    voice_similarity: float = 0.75
    language: str = "ja"


@router.get("/preview/validate-voice")
async def validate_voice(voice_id: str):
    """
    Validate a Voice ID against the ElevenLabs API.
    Returns voice name and details if valid, or an error if not found.
    This prevents silent failures from typos like 'I' vs 'l'.
    """
    from app.services.elevenlabs_tts_service import ElevenLabsTTSService, ElevenLabsError

    if not voice_id or not voice_id.strip():
        raise HTTPException(status_code=400, detail="voice_id is required")

    try:
        tts = ElevenLabsTTSService()
        voice_info = await tts.get_voice(voice_id.strip())
        logger.info(
            f"[LiverClone API] Voice validated: id={voice_id[:8]}..., "
            f"name={voice_info.get('name', 'unknown')}"
        )
        return {
            "valid": True,
            "voice_id": voice_id.strip(),
            "name": voice_info.get("name", ""),
            "category": voice_info.get("category", ""),
            "labels": voice_info.get("labels", {}),
        }
    except ElevenLabsError as e:
        if e.status_code == 404 or "not_found" in str(e).lower():
            logger.warning(f"[LiverClone API] Voice not found: {voice_id}")
            return {
                "valid": False,
                "voice_id": voice_id.strip(),
                "error": f"Voice ID '{voice_id}' not found. Please check for typos (e.g., 'I' vs 'l').",
            }
        logger.exception(f"[LiverClone API] Voice validation failed: {voice_id}")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.exception(f"[LiverClone API] Voice validation error: {voice_id}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/preview/speak")
async def preview_speak(req: PreviewSpeakRequest):
    """
    Generate TTS audio for preview mode (no session required).
    Returns base64-encoded MP3 audio that the frontend can play directly.
    The frontend should detect volume levels and send mouth_open to GPU Worker.
    """
    import base64
    from app.services.elevenlabs_tts_service import ElevenLabsTTSService

    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Text is required")

    voice_id = req.voice_id
    if not voice_id:
        import os
        voice_id = os.getenv("ELEVENLABS_VOICE_ID", "")
    if not voice_id:
        raise HTTPException(status_code=400, detail="voice_id is required")

    try:
        tts = ElevenLabsTTSService()
        # Generate MP3 audio (easier for browser playback)
        audio_bytes = await tts.text_to_speech(
            text=req.text,
            voice_id=voice_id,
            language_code=req.language,
            output_format="mp3_44100_128",
            voice_settings={
                "stability": req.voice_stability,
                "similarity_boost": req.voice_similarity,
            },
        )

        audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")
        duration_ms = len(audio_bytes) / 128 * 8  # rough estimate for MP3

        logger.info(
            f"[LiverClone API] Preview speak: text_len={len(req.text)}, "
            f"audio_size={len(audio_bytes)}, voice={voice_id[:8]}..."
        )

        return {
            "status": "ok",
            "audio_base64": audio_base64,
            "audio_format": "mp3",
            "text": req.text,
            "audio_size": len(audio_bytes),
        }
    except Exception as e:
        logger.exception("[LiverClone API] Preview speak failed")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Preview STS (Speech-to-Speech) Endpoint
# Uses ElevenLabs streaming API for low-latency voice conversion
# ============================================================

class PreviewSTSRequest(BaseModel):
    """Request to convert voice using ElevenLabs STS in preview mode."""
    audio_base64: str  # Base64-encoded audio (webm/opus from MediaRecorder)
    voice_id: str = ""
    voice_stability: float = 0.5
    voice_similarity: float = 0.75


@router.post("/preview/sts")
async def preview_sts(req: PreviewSTSRequest):
    """
    Convert voice using ElevenLabs STS streaming API for low-latency preview.
    Uses /v1/speech-to-speech/{voice_id}/stream for faster first-byte response.
    
    Key design decision: Browser MediaRecorder outputs WebM/Opus chunks that may
    be incomplete containers (especially short 2-3s chunks). ElevenLabs rejects
    these with 400 errors. Solution: transcode to WAV via ffmpeg before sending.
    This adds ~50ms latency but guarantees compatibility with any audio format.
    
    Returns base64-encoded MP3 audio.
    """
    import base64
    import json
    import httpx
    import os
    import tempfile
    import subprocess

    if not req.audio_base64:
        raise HTTPException(status_code=400, detail="audio_base64 is required")

    voice_id = req.voice_id
    if not voice_id:
        voice_id = os.getenv("ELEVENLABS_VOICE_ID", "")
    if not voice_id:
        raise HTTPException(status_code=400, detail="voice_id is required")

    api_key = os.getenv("ELEVENLABS_API_KEY", "")
    base_url = os.getenv("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io")
    sts_model = os.getenv("ELEVENLABS_STS_MODEL_ID", "eleven_multilingual_sts_v2")

    try:
        audio_bytes = base64.b64decode(req.audio_base64)
        if len(audio_bytes) < 200:
            return {"status": "skipped", "reason": "audio_too_short"}

        logger.info(f"[STS] input={len(audio_bytes)}B voice={voice_id[:8]}...")

        # ── Transcode WebM/Opus → WAV for ElevenLabs compatibility ──
        # Browser MediaRecorder outputs WebM chunks that may be incomplete
        # containers. ffmpeg handles this gracefully and outputs clean PCM WAV.
        wav_bytes = audio_bytes  # fallback: use raw if transcode fails
        input_filename = "input_audio.webm"
        input_mime = "audio/webm"
        try:
            with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp_in:
                tmp_in.write(audio_bytes)
                tmp_in_path = tmp_in.name
            tmp_out_path = tmp_in_path.replace(".webm", ".wav")

            result = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", tmp_in_path,
                    "-ar", "16000",      # 16kHz mono = optimal for speech
                    "-ac", "1",
                    "-f", "wav",
                    tmp_out_path,
                ],
                capture_output=True, timeout=10,
            )

            if result.returncode == 0:
                with open(tmp_out_path, "rb") as f:
                    wav_bytes = f.read()
                input_filename = "input_audio.wav"
                input_mime = "audio/wav"
                logger.info(f"[STS] transcoded: {len(audio_bytes)}B webm -> {len(wav_bytes)}B wav")
            else:
                stderr_snippet = result.stderr.decode(errors='ignore')[-200:]
                logger.warning(f"[STS] ffmpeg transcode failed, using raw: {stderr_snippet}")
        except Exception as transcode_err:
            logger.warning(f"[STS] transcode error (using raw): {transcode_err}")
        finally:
            # Cleanup temp files
            for p in [tmp_in_path, tmp_out_path]:
                try:
                    os.unlink(p)
                except Exception:
                    pass

        # Skip if transcoded audio is too short (silence/noise)
        if len(wav_bytes) < 500:
            return {"status": "skipped", "reason": "audio_too_short_after_transcode"}

        # ── Send to ElevenLabs STS streaming endpoint ──
        url = f"{base_url}/v1/speech-to-speech/{voice_id}/stream"
        params = {"output_format": "mp3_44100_128"}
        voice_settings = json.dumps({
            "stability": req.voice_stability,
            "similarity_boost": req.voice_similarity,
        })

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url,
                files={"audio": (input_filename, wav_bytes, input_mime)},
                data={
                    "model_id": sts_model,
                    "voice_settings": voice_settings,
                    "remove_background_noise": "true",
                },
                headers={"xi-api-key": api_key},
                params=params,
            )

        if response.status_code != 200:
            error_text = response.text[:200] if response.text else "unknown"
            logger.error(f"[STS] ElevenLabs error: {response.status_code} {error_text}")
            raise HTTPException(
                status_code=502,
                detail=f"ElevenLabs STS error: {response.status_code}"
            )

        converted_bytes = response.content
        if len(converted_bytes) < 100:
            return {"status": "skipped", "reason": "output_too_short"}

        converted_base64 = base64.b64encode(converted_bytes).decode("utf-8")
        logger.info(f"[STS] success: {len(audio_bytes)}B -> {len(converted_bytes)}B")

        return {
            "status": "ok",
            "audio_base64": converted_base64,
            "audio_format": "mp3",
            "input_size": len(audio_bytes),
            "output_size": len(converted_bytes),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[STS] failed")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Product Introduction (AI Script Generation)
# ============================================================

class ProductIntroRequest(BaseModel):
    """Request to generate product introduction script from image."""
    image_base64: str = ""  # Base64-encoded product image
    image_url: str = ""     # Or URL to product image
    product_name: str = ""  # Optional product name
    product_info: str = ""  # Optional additional product info
    language: str = "ja"    # Language for script
    style: str = "enthusiastic"  # Script style: enthusiastic, casual, professional
    max_length: int = 150   # Max characters for script


@router.post("/preview/product-intro")
async def generate_product_intro(req: ProductIntroRequest):
    """
    Generate a product introduction script from a product image.
    Uses GPT-4 Vision to analyze the product and create a live-commerce
    style introduction script that can be read aloud via TTS.
    """
    import base64
    import os
    import httpx

    if not req.image_base64 and not req.image_url:
        raise HTTPException(status_code=400, detail="image_base64 or image_url required")

    openai_key = os.getenv("OPENAI_API_KEY", "")
    if not openai_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not configured")

    try:
        # Build image content for GPT-4 Vision
        if req.image_base64:
            image_content = {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{req.image_base64}",
                    "detail": "low",
                },
            }
        else:
            image_content = {
                "type": "image_url",
                "image_url": {"url": req.image_url, "detail": "low"},
            }

        # Special mode: identify product only (no script generation)
        if req.style == "identify":
            system_prompt_identify = (
                "商品画像を見て、以下の形式で商品情報を抽出してください。\n"
                "商品名: (商品名を推測)\n"
                "特徴: (主な特徴を簡潔に)\n"
                "日本語で回答してください。"
            )
            messages_identify = [
                {"role": "system", "content": system_prompt_identify},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "この商品を識別してください。"},
                        image_content,
                    ],
                },
            ]
            openai_base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    f"{openai_base}/chat/completions",
                    json={
                        "model": "gpt-4.1-nano",
                        "messages": messages_identify,
                        "max_tokens": 100,
                        "temperature": 0.3,
                    },
                    headers={
                        "Authorization": f"Bearer {openai_key}",
                        "Content-Type": "application/json",
                    },
                )
            if resp.status_code != 200:
                logger.error(f"[ProductIntro] Identify error: {resp.status_code}")
                raise HTTPException(status_code=502, detail="AI identification failed")
            result = resp.json()
            script = result["choices"][0]["message"]["content"].strip()
            logger.info(f"[ProductIntro] Identified: {script[:80]}")
            return {"status": "ok", "script": script, "language": req.language, "style": "identify"}

        style_prompts = {
            "enthusiastic": "テンション高く、興奮気味に商品を紹介するライブコマースのライバー",
            "casual": "親しみやすくカジュアルに商品を紹介するライバー",
            "professional": "専門的で信頼感のあるトーンで商品を紹介するライバー",
        }
        style_desc = style_prompts.get(req.style, style_prompts["enthusiastic"])

        lang_instruction = ""
        if req.language == "ja":
            lang_instruction = "\u65e5\u672c\u8a9e\u3067\u751f\u6210\u3057\u3066\u304f\u3060\u3055\u3044\u3002"
        elif req.language == "en":
            lang_instruction = "Generate in English."
        elif req.language == "zh":
            lang_instruction = "\u7528\u4e2d\u6587\u751f\u6210\u3002"

        product_context = ""
        if req.product_name:
            product_context += f"\u5546\u54c1\u540d: {req.product_name}\n"
        if req.product_info:
            product_context += f"\u5546\u54c1\u60c5\u5831: {req.product_info}\n"

        system_prompt = (
            f"\u3042\u306a\u305f\u306f{style_desc}\u3067\u3059\u3002"
            f"\u5546\u54c1\u753b\u50cf\u3092\u898b\u3066\u3001\u30e9\u30a4\u30d6\u30b3\u30de\u30fc\u30b9\u3067\u8996\u8074\u8005\u306b\u5411\u3051\u3066\u5546\u54c1\u3092\u7d39\u4ecb\u3059\u308b\u30b9\u30af\u30ea\u30d7\u30c8\u3092\u751f\u6210\u3057\u3066\u304f\u3060\u3055\u3044\u3002"
            f"\u81ea\u7136\u306b\u8a71\u3059\u3088\u3046\u306b\u3001\u77ed\u304f\u30a4\u30f3\u30d1\u30af\u30c8\u306e\u3042\u308b\u6587\u7ae0\u3067\u3002"
            f"{max(50, req.max_length)}\u6587\u5b57\u4ee5\u5185\u3067\u3002{lang_instruction}"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"{product_context}\u3053\u306e\u5546\u54c1\u3092\u7d39\u4ecb\u3057\u3066\u304f\u3060\u3055\u3044\u3002"},
                    image_content,
                ],
            },
        ]

        # Call OpenAI GPT-4 Vision
        openai_base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{openai_base}/chat/completions",
                json={
                    "model": "gpt-4.1-mini",
                    "messages": messages,
                    "max_tokens": 300,
                    "temperature": 0.8,
                },
                headers={
                    "Authorization": f"Bearer {openai_key}",
                    "Content-Type": "application/json",
                },
            )

        if resp.status_code != 200:
            logger.error(f"[ProductIntro] OpenAI error: {resp.status_code} {resp.text[:200]}")
            raise HTTPException(status_code=502, detail="AI script generation failed")

        result = resp.json()
        script = result["choices"][0]["message"]["content"].strip()

        logger.info(f"[ProductIntro] Generated script: {script[:50]}...")

        return {
            "status": "ok",
            "script": script,
            "language": req.language,
            "style": req.style,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ProductIntro] Script generation failed")
        raise HTTPException(status_code=500, detail=str(e))



# ============================================================
# User Settings Persistence (DB-backed)
# ============================================================
# Replaces localStorage/IndexedDB with server-side storage.
# Settings are tied to user_id for cross-browser/device persistence.

class SaveSettingsRequest(BaseModel):
    """Request to save user's Liver Clone settings."""
    voice_id: str = ""
    voice_stability: float = 0.5
    voice_similarity: float = 0.75
    sts_enabled: bool = False
    mode: str = "hybrid"
    quality: str = "high"
    language: str = "ja"
    resolution: str = "720p"
    fps: int = 30
    vad_threshold: float = 0.3
    silence_timeout: float = 5.0
    saved_voices: str = "[]"  # JSON string
    saved_faces: str = "[]"   # JSON string
    saved_products: str = "[]"  # JSON string
    active_face_url: str = ""


class SaveFaceImageRequest(BaseModel):
    """Request to upload a face image for persistence."""
    face_id: str  # Unique identifier for this face
    image_base64: str  # Full-resolution base64 image
    name: str = ""  # Display name


@router.get("/settings")
async def get_settings(user_id: int = 0):
    """
    Get user's Liver Clone settings from DB.
    Falls back to defaults if no settings exist yet.
    
    Note: user_id is passed as query param for now (admin-key auth).
    In future, extract from JWT token.
    """
    from sqlalchemy import text
    from app.core.db import get_db

    if not user_id:
        # Default user for single-user mode
        user_id = 1

    async for db in get_db():
        try:
            # Ensure table exists (safe for first deploy)
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS liver_clone_settings (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    voice_id VARCHAR(255),
                    voice_stability FLOAT DEFAULT 0.5 NOT NULL,
                    voice_similarity FLOAT DEFAULT 0.75 NOT NULL,
                    sts_enabled BOOLEAN DEFAULT FALSE NOT NULL,
                    mode VARCHAR(50) DEFAULT 'hybrid' NOT NULL,
                    quality VARCHAR(50) DEFAULT 'high' NOT NULL,
                    language VARCHAR(10) DEFAULT 'ja' NOT NULL,
                    resolution VARCHAR(10) DEFAULT '720p' NOT NULL,
                    fps INTEGER DEFAULT 30 NOT NULL,
                    vad_threshold FLOAT DEFAULT 0.3 NOT NULL,
                    silence_timeout FLOAT DEFAULT 5.0 NOT NULL,
                    saved_voices TEXT DEFAULT '[]' NOT NULL,
                    saved_faces TEXT DEFAULT '[]' NOT NULL,
                    saved_products TEXT DEFAULT '[]' NOT NULL,
                    active_face_url TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
                )
            """))
            await db.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS ix_liver_clone_settings_user_id 
                ON liver_clone_settings(user_id)
            """))
            await db.commit()

            result = await db.execute(
                text("SELECT * FROM liver_clone_settings WHERE user_id = :uid"),
                {"uid": user_id},
            )
            row = result.mappings().fetchone()

            if not row:
                return {
                    "status": "ok",
                    "exists": False,
                    "settings": {
                        "voice_id": "",
                        "voice_stability": 0.5,
                        "voice_similarity": 0.75,
                        "sts_enabled": False,
                        "mode": "hybrid",
                        "quality": "high",
                        "language": "ja",
                        "resolution": "720p",
                        "fps": 30,
                        "vad_threshold": 0.3,
                        "silence_timeout": 5.0,
                        "saved_voices": "[]",
                        "saved_faces": "[]",
                        "saved_products": "[]",
                        "active_face_url": "",
                    },
                }

            return {
                "status": "ok",
                "exists": True,
                "settings": {
                    "voice_id": row["voice_id"] or "",
                    "voice_stability": row["voice_stability"],
                    "voice_similarity": row["voice_similarity"],
                    "sts_enabled": row["sts_enabled"],
                    "mode": row["mode"],
                    "quality": row["quality"],
                    "language": row["language"],
                    "resolution": row["resolution"],
                    "fps": row["fps"],
                    "vad_threshold": row["vad_threshold"],
                    "silence_timeout": row["silence_timeout"],
                    "saved_voices": row["saved_voices"],
                    "saved_faces": row["saved_faces"],
                    "saved_products": row["saved_products"],
                    "active_face_url": row["active_face_url"] or "",
                },
            }
        except Exception as e:
            logger.exception("[Settings] Failed to get settings")
            raise HTTPException(status_code=500, detail=str(e))


@router.put("/settings")
async def save_settings(req: SaveSettingsRequest, user_id: int = 0):
    """
    Save (upsert) user's Liver Clone settings to DB.
    Uses INSERT ... ON CONFLICT for atomic upsert.
    """
    from sqlalchemy import text
    from app.core.db import get_db

    if not user_id:
        user_id = 1

    async for db in get_db():
        try:
            # Ensure table exists
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS liver_clone_settings (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    voice_id VARCHAR(255),
                    voice_stability FLOAT DEFAULT 0.5 NOT NULL,
                    voice_similarity FLOAT DEFAULT 0.75 NOT NULL,
                    sts_enabled BOOLEAN DEFAULT FALSE NOT NULL,
                    mode VARCHAR(50) DEFAULT 'hybrid' NOT NULL,
                    quality VARCHAR(50) DEFAULT 'high' NOT NULL,
                    language VARCHAR(10) DEFAULT 'ja' NOT NULL,
                    resolution VARCHAR(10) DEFAULT '720p' NOT NULL,
                    fps INTEGER DEFAULT 30 NOT NULL,
                    vad_threshold FLOAT DEFAULT 0.3 NOT NULL,
                    silence_timeout FLOAT DEFAULT 5.0 NOT NULL,
                    saved_voices TEXT DEFAULT '[]' NOT NULL,
                    saved_faces TEXT DEFAULT '[]' NOT NULL,
                    saved_products TEXT DEFAULT '[]' NOT NULL,
                    active_face_url TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
                )
            """))
            await db.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS ix_liver_clone_settings_user_id 
                ON liver_clone_settings(user_id)
            """))
            await db.commit()

            # Upsert using ON CONFLICT
            await db.execute(
                text("""
                    INSERT INTO liver_clone_settings (
                        user_id, voice_id, voice_stability, voice_similarity,
                        sts_enabled, mode, quality, language, resolution, fps,
                        vad_threshold, silence_timeout, saved_voices, saved_faces,
                        saved_products, active_face_url, updated_at
                    ) VALUES (
                        :user_id, :voice_id, :voice_stability, :voice_similarity,
                        :sts_enabled, :mode, :quality, :language, :resolution, :fps,
                        :vad_threshold, :silence_timeout, :saved_voices, :saved_faces,
                        :saved_products, :active_face_url, NOW()
                    )
                    ON CONFLICT (user_id) DO UPDATE SET
                        voice_id = EXCLUDED.voice_id,
                        voice_stability = EXCLUDED.voice_stability,
                        voice_similarity = EXCLUDED.voice_similarity,
                        sts_enabled = EXCLUDED.sts_enabled,
                        mode = EXCLUDED.mode,
                        quality = EXCLUDED.quality,
                        language = EXCLUDED.language,
                        resolution = EXCLUDED.resolution,
                        fps = EXCLUDED.fps,
                        vad_threshold = EXCLUDED.vad_threshold,
                        silence_timeout = EXCLUDED.silence_timeout,
                        saved_voices = EXCLUDED.saved_voices,
                        saved_faces = EXCLUDED.saved_faces,
                        saved_products = EXCLUDED.saved_products,
                        active_face_url = EXCLUDED.active_face_url,
                        updated_at = NOW()
                """),
                {
                    "user_id": user_id,
                    "voice_id": req.voice_id,
                    "voice_stability": req.voice_stability,
                    "voice_similarity": req.voice_similarity,
                    "sts_enabled": req.sts_enabled,
                    "mode": req.mode,
                    "quality": req.quality,
                    "language": req.language,
                    "resolution": req.resolution,
                    "fps": req.fps,
                    "vad_threshold": req.vad_threshold,
                    "silence_timeout": req.silence_timeout,
                    "saved_voices": req.saved_voices,
                    "saved_faces": req.saved_faces,
                    "saved_products": req.saved_products,
                    "active_face_url": req.active_face_url,
                },
            )
            await db.commit()

            logger.info(f"[Settings] Saved settings for user_id={user_id}")
            return {"status": "ok", "user_id": user_id}
        except Exception as e:
            logger.exception("[Settings] Failed to save settings")
            raise HTTPException(status_code=500, detail=str(e))


@router.post("/settings/upload-face")
async def upload_face_image(req: SaveFaceImageRequest, user_id: int = 0):
    """
    Upload a face image to Azure Blob Storage for persistent storage.
    Returns the public URL that can be stored in saved_faces JSON.
    
    This replaces IndexedDB storage with cloud storage so faces persist
    across browsers and devices.
    """
    import base64
    import os
    import uuid

    if not user_id:
        user_id = 1

    try:
        from azure.storage.blob import BlobServiceClient, ContentSettings

        conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
        account_name = os.getenv("AZURE_STORAGE_ACCOUNT_NAME", "")
        container_name = "liver-clone-faces"

        if not conn_str:
            raise HTTPException(status_code=500, detail="Storage not configured")

        # Decode base64 image
        image_bytes = base64.b64decode(req.image_base64)
        
        # Detect format from header bytes
        content_type = "image/jpeg"
        ext = "jpg"
        if image_bytes[:8] == b'\x89PNG\r\n\x1a\n':
            content_type = "image/png"
            ext = "png"
        elif image_bytes[:4] == b'RIFF' and image_bytes[8:12] == b'WEBP':
            content_type = "image/webp"
            ext = "webp"

        # Generate unique blob name
        blob_name = f"user_{user_id}/{req.face_id}_{uuid.uuid4().hex[:8]}.{ext}"

        # Upload to Azure Blob Storage
        service_client = BlobServiceClient.from_connection_string(conn_str)
        container_client = service_client.get_container_client(container_name)

        # Create container if not exists
        try:
            container_client.create_container(public_access="blob")
        except Exception:
            pass  # Already exists

        blob_client = container_client.get_blob_client(blob_name)
        blob_client.upload_blob(
            image_bytes,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )

        # Construct public URL
        image_url = f"https://{account_name}.blob.core.windows.net/{container_name}/{blob_name}"

        logger.info(f"[Settings] Face image uploaded: {blob_name} ({len(image_bytes)}B)")
        return {
            "status": "ok",
            "face_id": req.face_id,
            "image_url": image_url,
            "size": len(image_bytes),
        }
    except HTTPException:
        raise
    except ImportError:
        # Azure SDK not available - store as data URL fallback
        logger.warning("[Settings] Azure SDK not available, using data URL fallback")
        data_url = f"data:image/jpeg;base64,{req.image_base64[:100]}..."
        return {
            "status": "ok",
            "face_id": req.face_id,
            "image_url": "",
            "size": 0,
            "warning": "Azure storage not available, face stored locally only",
        }
    except Exception as e:
        logger.exception("[Settings] Face upload failed")
        raise HTTPException(status_code=500, detail=str(e))
