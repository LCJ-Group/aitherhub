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
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import logging

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
    The frontend connects directly to the GPU Worker via this URL.
    """
    from app.services.liver_clone_service import get_liver_clone_service

    service = get_liver_clone_service()
    try:
        ws_url = await service.face_swap.get_preview_ws_url()
        return {"ws_url": ws_url}
    except Exception as e:
        logger.exception("[LiverClone API] Failed to get preview WS URL")
        raise HTTPException(status_code=500, detail=str(e))


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
    Convert voice using ElevenLabs Speech-to-Speech (STS) in preview mode.
    Accepts base64-encoded audio from the browser's MediaRecorder (webm/opus),
    converts it using the specified voice, and returns base64-encoded MP3.
    The frontend captures microphone audio in chunks and sends them here
    for real-time voice conversion during preview.
    """
    import base64
    from app.services.elevenlabs_tts_service import ElevenLabsTTSService

    if not req.audio_base64:
        raise HTTPException(status_code=400, detail="audio_base64 is required")

    voice_id = req.voice_id
    if not voice_id:
        import os
        voice_id = os.getenv("ELEVENLABS_VOICE_ID", "")
    if not voice_id:
        raise HTTPException(status_code=400, detail="voice_id is required")

    try:
        # Decode input audio
        audio_bytes = base64.b64decode(req.audio_base64)
        if len(audio_bytes) < 100:
            raise HTTPException(status_code=400, detail="Audio data too short")

        logger.info(
            f"[LiverClone API] Preview STS: input_size={len(audio_bytes)}, "
            f"voice={voice_id[:8]}..."
        )

        tts = ElevenLabsTTSService()

        # Use STS to convert voice
        converted_bytes = await tts.speech_to_speech(
            audio_data=audio_bytes,
            voice_id=voice_id,
            output_format="mp3_44100_128",
            voice_settings={
                "stability": req.voice_stability,
                "similarity_boost": req.voice_similarity,
            },
        )

        converted_base64 = base64.b64encode(converted_bytes).decode("utf-8")

        logger.info(
            f"[LiverClone API] Preview STS success: "
            f"input={len(audio_bytes)}B → output={len(converted_bytes)}B"
        )

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
        logger.exception("[LiverClone API] Preview STS failed")
        raise HTTPException(status_code=500, detail=str(e))
