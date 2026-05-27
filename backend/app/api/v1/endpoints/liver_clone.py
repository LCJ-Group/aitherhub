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
    Returns base64-encoded MP3 audio (22kHz/32kbps for minimal size).
    """
    import base64
    import json
    import httpx
    import os

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
            # Too short = likely noise, skip
            return {"status": "skipped", "reason": "audio_too_short"}

        logger.info(f"[STS] input={len(audio_bytes)}B voice={voice_id[:8]}...")

        # Use streaming endpoint for lower latency
        url = f"{base_url}/v1/speech-to-speech/{voice_id}/stream"
        params = {"output_format": "mp3_22050_32"}
        voice_settings = json.dumps({
            "stability": req.voice_stability,
            "similarity_boost": req.voice_similarity,
        })

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                url,
                files={"audio": ("chunk.webm", audio_bytes, "audio/webm")},
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

        style_prompts = {
            "enthusiastic": "\u30c6\u30f3\u30b7\u30e7\u30f3\u9ad8\u304f\u3001\u8208\u596e\u6c17\u5473\u306b\u5546\u54c1\u3092\u7d39\u4ecb\u3059\u308b\u30e9\u30a4\u30d6\u30b3\u30de\u30fc\u30b9\u306e\u30e9\u30a4\u30d0\u30fc",
            "casual": "\u89aa\u3057\u307f\u3084\u3059\u304f\u30ab\u30b8\u30e5\u30a2\u30eb\u306b\u5546\u54c1\u3092\u7d39\u4ecb\u3059\u308b\u30e9\u30a4\u30d0\u30fc",
            "professional": "\u5c02\u9580\u7684\u3067\u4fe1\u983c\u611f\u306e\u3042\u308b\u30c8\u30fc\u30f3\u3067\u5546\u54c1\u3092\u7d39\u4ecb\u3059\u308b\u30e9\u30a4\u30d0\u30fc",
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
