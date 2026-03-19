"""
Digital Human (數智人) Livestream API Endpoints

These endpoints provide the AitherHub ↔ Tencent Cloud IVH + ElevenLabs integration:

  === Liveroom Management ===
  POST /api/v1/digital-human/liveroom/create     – Create livestream room (supports hybrid voice mode)
  GET  /api/v1/digital-human/liveroom/{id}        – Query livestream room status
  GET  /api/v1/digital-human/liverooms            – List all active livestream rooms
  POST /api/v1/digital-human/liveroom/{id}/takeover – Send real-time interjection (supports hybrid voice)
  POST /api/v1/digital-human/liveroom/{id}/close  – Close livestream room

  === Script & Audio Generation ===
  POST /api/v1/digital-human/script/generate      – Generate script from analysis (preview)
  POST /api/v1/digital-human/audio/generate       – Pre-generate audio with cloned voice

  === Voice & Health ===
  GET  /api/v1/digital-human/voices               – List available ElevenLabs voices
  GET  /api/v1/digital-human/health               – Health check (both services)

Architecture:
  Hybrid mode combines ElevenLabs TTS (voice cloning, supports Japanese)
  with Tencent Cloud Digital Human (lip-sync, visual rendering).

  ┌─────────────┐     ┌──────────────┐     ┌──────────────────┐
  │  Text Input  │────▶│ ElevenLabs   │────▶│ Tencent Cloud    │
  │  (台本/評論) │     │ TTS API      │     │ Digital Human    │
  │              │     │ (声音克隆)    │     │ (口型同步+直播)   │
  └─────────────┘     │ PCM 16kHz    │     │ Audio Driver     │
                      └──────────────┘     └──────────────────┘
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.schemas.digital_human_schema import (
    CreateLiveroomRequest,
    CreateLiveroomResponse,
    GetLiveroomResponse,
    ListLiveroomsResponse,
    TakeoverRequest,
    TakeoverResponse,
    CloseLiveroomResponse,
    GenerateScriptRequest,
    GenerateScriptResponse,
    HybridHealthResponse,
    GenerateAudioRequest,
    GenerateAudioResponse,
    VoiceListResponse,
)
from app.services.tencent_digital_human_service import (
    TencentDigitalHumanService,
    TencentAPIError,
    ScriptReq,
    VideoLayer,
    SpeechParam,
    AnchorParam,
    LIVEROOM_STATUS,
)
from app.services.elevenlabs_tts_service import (
    ElevenLabsTTSService,
    ElevenLabsError,
)
from app.services.hybrid_livestream_service import HybridLivestreamService
from app.services.script_generator_service import (
    generate_liveroom_scripts,
    generate_takeover_script,
    fetch_video_analysis,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/digital-human",
    tags=["Digital Human (數智人)"],
)

# ──────────────────────────────────────────────
# Auth dependency (PoC: admin key only)
# ──────────────────────────────────────────────

ADMIN_KEY = "aither:hub"


async def verify_admin_key(x_admin_key: str = Header(...)):
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Invalid admin key")
    return True


# ──────────────────────────────────────────────
# Service singletons
# ──────────────────────────────────────────────

_tencent_service: Optional[TencentDigitalHumanService] = None
_elevenlabs_service: Optional[ElevenLabsTTSService] = None
_hybrid_service: Optional[HybridLivestreamService] = None


def get_tencent_service() -> TencentDigitalHumanService:
    global _tencent_service
    if _tencent_service is None:
        _tencent_service = TencentDigitalHumanService()
    return _tencent_service


def get_elevenlabs_service() -> ElevenLabsTTSService:
    global _elevenlabs_service
    if _elevenlabs_service is None:
        _elevenlabs_service = ElevenLabsTTSService()
    return _elevenlabs_service


def get_hybrid_service() -> HybridLivestreamService:
    global _hybrid_service
    if _hybrid_service is None:
        _hybrid_service = HybridLivestreamService(
            elevenlabs_service=get_elevenlabs_service(),
            tencent_service=get_tencent_service(),
        )
    return _hybrid_service


# ══════════════════════════════════════════════
# LIVEROOM MANAGEMENT
# ══════════════════════════════════════════════

# ──────────────────────────────────────────────
# 1. Create Liveroom
# ──────────────────────────────────────────────

@router.post(
    "/liveroom/create",
    response_model=CreateLiveroomResponse,
    summary="Create a digital human livestream room",
    description=(
        "Create a new Tencent Cloud IVH livestream room. "
        "If video_id is provided, scripts are auto-generated from AitherHub analysis results. "
        "Set use_hybrid_voice=true to pre-generate audio with ElevenLabs voice cloning."
    ),
)
async def create_liveroom(
    req: CreateLiveroomRequest,
    db: AsyncSession = Depends(get_db),
    _auth: bool = Depends(verify_admin_key),
):
    service = get_tencent_service()

    try:
        # Generate or use provided scripts
        if req.video_id:
            logger.info(f"Generating scripts from video analysis: {req.video_id}")
            script_dicts = await generate_liveroom_scripts(
                db=db,
                video_id=req.video_id,
                product_focus=req.product_focus,
                tone=req.tone,
                language=req.language,
            )
            scripts_text = [sd["Content"] for sd in script_dicts]
        elif req.scripts:
            scripts_text = req.scripts
        else:
            raise HTTPException(
                status_code=400,
                detail="Either video_id or scripts must be provided",
            )

        # Hybrid mode: pre-generate audio with ElevenLabs
        audio_results = None
        mode = "text_only"
        if req.use_hybrid_voice:
            mode = "hybrid"
            hybrid = get_hybrid_service()
            try:
                audio_results = await hybrid.generate_script_audio(
                    scripts=scripts_text,
                    language=req.language,
                    voice_id=req.elevenlabs_voice_id,
                )
                logger.info(
                    f"Hybrid audio generated: {len(audio_results)} scripts"
                )
            except ElevenLabsError as e:
                logger.warning(f"ElevenLabs audio generation failed, continuing with text: {e}")
                audio_results = [{"status": "error", "error": str(e)}]

        # Build script objects for Tencent API
        scripts = []
        for text in scripts_text:
            bgs = []
            if req.backgrounds:
                bgs = [
                    VideoLayer(url=bg.url, x=bg.x, y=bg.y, width=bg.width, height=bg.height)
                    for bg in req.backgrounds
                ]
            scripts.append(ScriptReq(content=text, backgrounds=bgs))

        # Build optional params
        speech_param = None
        if req.speech_param:
            speech_param = SpeechParam(
                speed=req.speech_param.speed,
                timbre_key=req.speech_param.timbre_key,
                volume=req.speech_param.volume,
                pitch=req.speech_param.pitch,
            )

        anchor_param = None
        if req.anchor_param:
            anchor_param = AnchorParam(
                horizontal_position=req.anchor_param.horizontal_position,
                vertical_position=req.anchor_param.vertical_position,
                scale=req.anchor_param.scale,
            )

        # Call Tencent API
        result = await service.open_liveroom(
            scripts=scripts,
            cycle_times=req.cycle_times,
            callback_url=req.callback_url,
            virtualman_project_id=req.virtualman_project_id,
            protocol=req.protocol,
            speech_param=speech_param,
            anchor_param=anchor_param,
        )

        status_code = result.get("Status", 0)
        return CreateLiveroomResponse(
            success=True,
            liveroom_id=result.get("LiveRoomId"),
            status=status_code,
            status_label=LIVEROOM_STATUS.get(status_code, "UNKNOWN"),
            req_id=result.get("ReqId"),
            play_url=result.get("VideoStreamPlayUrl"),
            script_preview=scripts[0].content[:500] if scripts else None,
            mode=mode,
            audio_results=audio_results,
        )

    except TencentAPIError as e:
        logger.error(f"Tencent API error creating liveroom: {e}")
        return CreateLiveroomResponse(success=False, error=str(e))
    except ValueError as e:
        logger.error(f"Validation error: {e}")
        return CreateLiveroomResponse(success=False, error=str(e))
    except Exception as e:
        logger.exception(f"Unexpected error creating liveroom: {e}")
        return CreateLiveroomResponse(success=False, error=f"Internal error: {str(e)}")


# ──────────────────────────────────────────────
# 2. Get Liveroom Status
# ──────────────────────────────────────────────

@router.get(
    "/liveroom/{liveroom_id}",
    response_model=GetLiveroomResponse,
    summary="Query livestream room status",
)
async def get_liveroom(
    liveroom_id: str,
    _auth: bool = Depends(verify_admin_key),
):
    service = get_tencent_service()

    try:
        result = await service.get_liveroom(liveroom_id)
        status_code = result.get("Status", 0)
        return GetLiveroomResponse(
            success=True,
            liveroom_id=result.get("LiveRoomId"),
            status=status_code,
            status_label=LIVEROOM_STATUS.get(status_code, "UNKNOWN"),
            play_url=result.get("VideoStreamPlayUrl"),
            details=result,
        )
    except TencentAPIError as e:
        return GetLiveroomResponse(success=False, error=str(e))
    except Exception as e:
        logger.exception(f"Error getting liveroom: {e}")
        return GetLiveroomResponse(success=False, error=f"Internal error: {str(e)}")


# ──────────────────────────────────────────────
# 3. List Liverooms
# ──────────────────────────────────────────────

@router.get(
    "/liverooms",
    response_model=ListLiveroomsResponse,
    summary="List all active livestream rooms",
)
async def list_liverooms(
    page_size: int = 20,
    page_index: int = 1,
    _auth: bool = Depends(verify_admin_key),
):
    service = get_tencent_service()

    try:
        result = await service.list_liverooms(
            page_size=page_size,
            page_index=page_index,
        )
        liverooms = result.get("LiveRoomList", [])
        return ListLiveroomsResponse(success=True, liverooms=liverooms)
    except TencentAPIError as e:
        return ListLiveroomsResponse(success=False, error=str(e))
    except Exception as e:
        logger.exception(f"Error listing liverooms: {e}")
        return ListLiveroomsResponse(success=False, error=f"Internal error: {str(e)}")


# ──────────────────────────────────────────────
# 4. Takeover (Real-time Interjection)
# ──────────────────────────────────────────────

@router.post(
    "/liveroom/{liveroom_id}/takeover",
    response_model=TakeoverResponse,
    summary="Send real-time interjection to livestream",
    description=(
        "Interrupt the current script and have the digital human speak the given text immediately. "
        "If content is not provided, it will be auto-generated from event_context. "
        "Set use_hybrid_voice=true to also generate audio with cloned voice."
    ),
)
async def takeover_liveroom(
    liveroom_id: str,
    req: TakeoverRequest,
    _auth: bool = Depends(verify_admin_key),
):
    service = get_tencent_service()

    try:
        # Determine content
        if req.content:
            content = req.content
        elif req.event_context:
            content = await generate_takeover_script(
                context=req.event_context,
                event_type=req.event_type,
                language=req.language,
            )
        else:
            raise HTTPException(
                status_code=400,
                detail="Either content or event_context must be provided",
            )

        # Hybrid mode: generate audio with ElevenLabs
        audio_info = None
        mode = "text_only"
        if req.use_hybrid_voice:
            mode = "hybrid"
            hybrid = get_hybrid_service()
            try:
                result = await hybrid.takeover_with_voice(
                    liveroom_id=liveroom_id,
                    text=content,
                    language=req.language,
                    voice_id=req.elevenlabs_voice_id,
                )
                audio_info = result.get("audio_info")
                return TakeoverResponse(
                    success=True,
                    content_sent=content,
                    mode=mode,
                    audio_info=audio_info,
                )
            except Exception as e:
                logger.warning(f"Hybrid takeover failed, falling back to text: {e}")
                audio_info = {"status": "failed", "error": str(e)}

        # Standard text-based takeover
        result = await service.takeover(liveroom_id, content)
        return TakeoverResponse(
            success=True,
            content_sent=content,
            mode=mode,
            audio_info=audio_info,
        )

    except TencentAPIError as e:
        return TakeoverResponse(success=False, error=str(e))
    except Exception as e:
        logger.exception(f"Error in takeover: {e}")
        return TakeoverResponse(success=False, error=f"Internal error: {str(e)}")


# ──────────────────────────────────────────────
# 5. Close Liveroom
# ──────────────────────────────────────────────

@router.post(
    "/liveroom/{liveroom_id}/close",
    response_model=CloseLiveroomResponse,
    summary="Close a livestream room",
)
async def close_liveroom(
    liveroom_id: str,
    _auth: bool = Depends(verify_admin_key),
):
    service = get_tencent_service()

    try:
        result = await service.close_liveroom(liveroom_id)
        return CloseLiveroomResponse(success=True, liveroom_id=liveroom_id)
    except TencentAPIError as e:
        return CloseLiveroomResponse(success=False, error=str(e))
    except Exception as e:
        logger.exception(f"Error closing liveroom: {e}")
        return CloseLiveroomResponse(success=False, error=f"Internal error: {str(e)}")


# ══════════════════════════════════════════════
# SCRIPT & AUDIO GENERATION
# ══════════════════════════════════════════════

# ──────────────────────────────────────────────
# 6. Generate Script (Preview, no liveroom)
# ──────────────────────────────────────────────

@router.post(
    "/script/generate",
    response_model=GenerateScriptResponse,
    summary="Generate livestream script from video analysis",
    description=(
        "Generate a digital human livestream script from AitherHub video analysis results. "
        "This endpoint does NOT create a liveroom — it's for previewing and editing scripts."
    ),
)
async def generate_script(
    req: GenerateScriptRequest,
    db: AsyncSession = Depends(get_db),
    _auth: bool = Depends(verify_admin_key),
):
    try:
        # Fetch analysis data for metadata
        analysis_data = await fetch_video_analysis(db, req.video_id)
        phases_count = len(analysis_data.get("phases", []))

        # Generate scripts
        script_dicts = await generate_liveroom_scripts(
            db=db,
            video_id=req.video_id,
            product_focus=req.product_focus,
            tone=req.tone,
            language=req.language,
        )

        script_text = script_dicts[0]["Content"] if script_dicts else ""

        return GenerateScriptResponse(
            success=True,
            video_id=req.video_id,
            script=script_text,
            script_length=len(script_text),
            phases_used=phases_count,
        )

    except ValueError as e:
        return GenerateScriptResponse(success=False, error=str(e))
    except Exception as e:
        logger.exception(f"Error generating script: {e}")
        return GenerateScriptResponse(success=False, error=f"Internal error: {str(e)}")


# ──────────────────────────────────────────────
# 7. Generate Audio (ElevenLabs voice cloning)
# ──────────────────────────────────────────────

@router.post(
    "/audio/generate",
    response_model=GenerateAudioResponse,
    summary="Pre-generate audio with cloned voice",
    description=(
        "Generate speech audio from text using ElevenLabs voice cloning. "
        "Supports 32+ languages including Japanese. "
        "Audio is generated in PCM 16kHz format compatible with Tencent Cloud audio driver."
    ),
)
async def generate_audio(
    req: GenerateAudioRequest,
    _auth: bool = Depends(verify_admin_key),
):
    try:
        hybrid = get_hybrid_service()
        results = await hybrid.generate_script_audio(
            scripts=req.texts,
            language=req.language,
            voice_id=req.voice_id,
        )

        total_duration = sum(
            r.get("duration_ms", 0) for r in results if r.get("status") == "ok"
        )

        return GenerateAudioResponse(
            success=True,
            results=results,
            total_duration_ms=round(total_duration, 1),
        )

    except ElevenLabsError as e:
        return GenerateAudioResponse(success=False, error=str(e))
    except Exception as e:
        logger.exception(f"Error generating audio: {e}")
        return GenerateAudioResponse(success=False, error=f"Internal error: {str(e)}")


# ══════════════════════════════════════════════
# VOICE & HEALTH
# ══════════════════════════════════════════════

# ──────────────────────────────────────────────
# 8. List Voices
# ──────────────────────────────────────────────

@router.get(
    "/voices",
    response_model=VoiceListResponse,
    summary="List available ElevenLabs voices",
    description="List all voices including cloned voices available for TTS.",
)
async def list_voices(
    _auth: bool = Depends(verify_admin_key),
):
    try:
        el_service = get_elevenlabs_service()
        voices = await el_service.list_voices()

        # Simplify voice data for response
        voice_list = []
        cloned_count = 0
        for v in voices:
            is_cloned = v.get("category") == "cloned"
            if is_cloned:
                cloned_count += 1
            voice_list.append({
                "voice_id": v.get("voice_id"),
                "name": v.get("name"),
                "category": v.get("category"),
                "labels": v.get("labels", {}),
                "is_cloned": is_cloned,
            })

        return VoiceListResponse(
            success=True,
            voices=voice_list,
            cloned_count=cloned_count,
            total_count=len(voice_list),
        )

    except ElevenLabsError as e:
        return VoiceListResponse(success=False, error=str(e))
    except Exception as e:
        logger.exception(f"Error listing voices: {e}")
        return VoiceListResponse(success=False, error=f"Internal error: {str(e)}")


# ──────────────────────────────────────────────
# 9. Health Check
# ──────────────────────────────────────────────

@router.get(
    "/health",
    response_model=HybridHealthResponse,
    summary="Health check for digital human services",
    description="Check connectivity to both Tencent Cloud IVH and ElevenLabs APIs.",
)
async def health_check(
    _auth: bool = Depends(verify_admin_key),
):
    try:
        hybrid = get_hybrid_service()
        result = await hybrid.health_check()

        return HybridHealthResponse(
            success=True,
            overall_status=result.get("status"),
            elevenlabs=result.get("elevenlabs"),
            tencent=result.get("tencent"),
            capabilities=result.get("capabilities"),
        )

    except Exception as e:
        logger.exception(f"Health check error: {e}")
        return HybridHealthResponse(
            success=False,
            error=f"Health check failed: {str(e)}",
        )


# ══════════════════════════════════════════════
# MODE B: FACE SWAP LIVESTREAM (FaceFusion)
# ══════════════════════════════════════════════
#
# These endpoints control the FaceFusion GPU worker for real-time
# face swapping. A body double streams with products while the
# GPU worker replaces their face with the influencer's face.
#
# Combined with ElevenLabs voice cloning (Mode A audio), this
# creates a fully cloned livestream presence.
#
# Architecture:
#   Body Double (camera) → GPU Worker (face swap) → Platform (viewers)
#   Script Text → ElevenLabs TTS (voice clone) → Audio output
# ══════════════════════════════════════════════

from app.services.face_swap_service import (
    FaceSwapService,
    FaceSwapError,
    WorkerConnectionError,
    WorkerAPIError,
)
from app.schemas.digital_human_schema import (
    SetSourceFaceRequest,
    SetSourceFaceResponse,
    StartFaceSwapStreamRequest,
    StartFaceSwapStreamResponse,
    StopFaceSwapStreamRequest,
    StopFaceSwapStreamResponse,
    FaceSwapStreamStatusResponse,
    SwapSingleFrameRequest,
    SwapSingleFrameResponse,
    FaceSwapHealthResponse,
    FullHealthResponse,
)

# Face swap service singleton
_face_swap_service: Optional[FaceSwapService] = None


def get_face_swap_service() -> FaceSwapService:
    global _face_swap_service
    if _face_swap_service is None:
        _face_swap_service = FaceSwapService()
    return _face_swap_service


# ──────────────────────────────────────────────
# 10. Set Source Face
# ──────────────────────────────────────────────

@router.post(
    "/face-swap/set-source",
    response_model=SetSourceFaceResponse,
    summary="Set the source face for face swapping",
    description=(
        "Upload the influencer's face photo that will replace the body double's "
        "face during the livestream. The image should contain a clear, front-facing "
        "photo. If the image contains multiple faces, use face_index to select one."
    ),
)
async def set_source_face(
    req: SetSourceFaceRequest,
    _auth: bool = Depends(verify_admin_key),
):
    if not req.image_url and not req.image_base64:
        raise HTTPException(
            status_code=400,
            detail="Either image_url or image_base64 must be provided",
        )

    try:
        service = get_face_swap_service()
        result = await service.set_source_face(
            image_url=req.image_url,
            image_base64=req.image_base64,
            face_index=req.face_index,
        )

        return SetSourceFaceResponse(
            success=True,
            face_detected=result.get("face_detected", True),
            face_bbox=result.get("face_bbox"),
            face_landmarks=result.get("face_landmarks"),
        )

    except WorkerConnectionError as e:
        logger.error(f"GPU worker connection error: {e}")
        return SetSourceFaceResponse(
            success=False,
            error=f"GPU worker unreachable: {str(e)}",
        )
    except WorkerAPIError as e:
        logger.error(f"GPU worker API error: {e}")
        return SetSourceFaceResponse(
            success=False,
            error=f"GPU worker error ({e.status_code}): {e.detail}",
        )
    except FaceSwapError as e:
        return SetSourceFaceResponse(success=False, error=str(e))
    except Exception as e:
        logger.exception(f"Unexpected error setting source face: {e}")
        return SetSourceFaceResponse(
            success=False, error=f"Internal error: {str(e)}"
        )


# ──────────────────────────────────────────────
# 11. Start Face Swap Stream
# ──────────────────────────────────────────────

@router.post(
    "/face-swap/stream/start",
    response_model=StartFaceSwapStreamResponse,
    summary="Start a real-time face swap livestream",
    description=(
        "Start the face swap pipeline: the GPU worker pulls the body double's "
        "camera feed from input_rtmp, swaps the face with the source face, and "
        "pushes the result to output_rtmp (streaming platform). "
        "Make sure to call /face-swap/set-source first to set the influencer's face."
    ),
)
async def start_face_swap_stream(
    req: StartFaceSwapStreamRequest,
    _auth: bool = Depends(verify_admin_key),
):
    try:
        service = get_face_swap_service()
        result = await service.start_stream(
            input_rtmp=req.input_rtmp,
            output_rtmp=req.output_rtmp,
            quality=req.quality,
            resolution=req.resolution,
            fps=req.fps,
            face_enhancer=req.face_enhancer,
            face_mask_blur=req.face_mask_blur,
        )

        return StartFaceSwapStreamResponse(
            success=True,
            session_id=result.get("session_id"),
            status=result.get("status", "starting"),
            input_rtmp=req.input_rtmp,
            output_rtmp=req.output_rtmp,
            quality=req.quality,
            resolution=req.resolution,
            fps=req.fps,
        )

    except WorkerConnectionError as e:
        logger.error(f"GPU worker connection error: {e}")
        return StartFaceSwapStreamResponse(
            success=False,
            error=f"GPU worker unreachable: {str(e)}",
        )
    except WorkerAPIError as e:
        logger.error(f"GPU worker API error: {e}")
        return StartFaceSwapStreamResponse(
            success=False,
            error=f"GPU worker error ({e.status_code}): {e.detail}",
        )
    except FaceSwapError as e:
        return StartFaceSwapStreamResponse(success=False, error=str(e))
    except Exception as e:
        logger.exception(f"Unexpected error starting face swap stream: {e}")
        return StartFaceSwapStreamResponse(
            success=False, error=f"Internal error: {str(e)}"
        )


# ──────────────────────────────────────────────
# 12. Stop Face Swap Stream
# ──────────────────────────────────────────────

@router.post(
    "/face-swap/stream/stop",
    response_model=StopFaceSwapStreamResponse,
    summary="Stop the face swap livestream",
)
async def stop_face_swap_stream(
    req: StopFaceSwapStreamRequest,
    _auth: bool = Depends(verify_admin_key),
):
    try:
        service = get_face_swap_service()
        result = await service.stop_stream(session_id=req.session_id)

        return StopFaceSwapStreamResponse(
            success=True,
            session_id=result.get("session_id"),
            uptime_seconds=result.get("uptime_seconds"),
            frames_processed=result.get("frames_processed"),
        )

    except WorkerConnectionError as e:
        return StopFaceSwapStreamResponse(
            success=False,
            error=f"GPU worker unreachable: {str(e)}",
        )
    except (WorkerAPIError, FaceSwapError) as e:
        return StopFaceSwapStreamResponse(success=False, error=str(e))
    except Exception as e:
        logger.exception(f"Error stopping face swap stream: {e}")
        return StopFaceSwapStreamResponse(
            success=False, error=f"Internal error: {str(e)}"
        )


# ──────────────────────────────────────────────
# 13. Get Face Swap Stream Status
# ──────────────────────────────────────────────

@router.get(
    "/face-swap/stream/status",
    response_model=FaceSwapStreamStatusResponse,
    summary="Get face swap stream status",
    description="Check the current status of the face swap stream including FPS, latency, and uptime.",
)
async def get_face_swap_stream_status(
    session_id: Optional[str] = None,
    _auth: bool = Depends(verify_admin_key),
):
    try:
        service = get_face_swap_service()
        result = await service.get_stream_status(session_id=session_id)

        return FaceSwapStreamStatusResponse(
            success=True,
            status=result.get("status", "idle"),
            session_id=result.get("session_id"),
            fps=result.get("fps"),
            latency_ms=result.get("latency_ms"),
            uptime_seconds=result.get("uptime_seconds"),
            frames_processed=result.get("frames_processed"),
            errors=result.get("errors"),
        )

    except WorkerConnectionError as e:
        return FaceSwapStreamStatusResponse(
            success=False,
            status="unreachable",
            error=f"GPU worker unreachable: {str(e)}",
        )
    except (WorkerAPIError, FaceSwapError) as e:
        return FaceSwapStreamStatusResponse(
            success=False,
            status="error",
            error=str(e),
        )
    except Exception as e:
        logger.exception(f"Error getting stream status: {e}")
        return FaceSwapStreamStatusResponse(
            success=False,
            status="error",
            error=f"Internal error: {str(e)}",
        )


# ──────────────────────────────────────────────
# 14. Swap Single Frame (Testing)
# ──────────────────────────────────────────────

@router.post(
    "/face-swap/test-frame",
    response_model=SwapSingleFrameResponse,
    summary="Test face swap on a single frame",
    description=(
        "Upload a single frame and get back the face-swapped result. "
        "Useful for testing and previewing the face swap quality before "
        "starting a livestream. Requires set-source to be called first."
    ),
)
async def swap_single_frame(
    req: SwapSingleFrameRequest,
    _auth: bool = Depends(verify_admin_key),
):
    try:
        service = get_face_swap_service()
        result = await service.swap_single_frame(
            frame_base64=req.frame_base64,
            quality=req.quality,
            face_enhancer=req.face_enhancer,
        )

        return SwapSingleFrameResponse(
            success=True,
            output_base64=result.get("output_base64"),
            processing_ms=result.get("processing_ms"),
            faces_detected=result.get("faces_detected"),
        )

    except WorkerConnectionError as e:
        return SwapSingleFrameResponse(
            success=False,
            error=f"GPU worker unreachable: {str(e)}",
        )
    except (WorkerAPIError, FaceSwapError) as e:
        return SwapSingleFrameResponse(success=False, error=str(e))
    except Exception as e:
        logger.exception(f"Error swapping frame: {e}")
        return SwapSingleFrameResponse(
            success=False, error=f"Internal error: {str(e)}"
        )


# ──────────────────────────────────────────────
# 15. Face Swap Health Check
# ──────────────────────────────────────────────

@router.get(
    "/face-swap/health",
    response_model=FaceSwapHealthResponse,
    summary="Health check for Face Swap GPU worker",
    description="Check connectivity and status of the FaceFusion GPU worker.",
)
async def face_swap_health_check(
    _auth: bool = Depends(verify_admin_key),
):
    try:
        service = get_face_swap_service()
        result = await service.health_check()

        return FaceSwapHealthResponse(
            success=result.get("status") in ("ok", "not_configured"),
            status=result.get("status"),
            gpu_name=result.get("gpu_name"),
            gpu_memory_used_mb=result.get("gpu_memory_used_mb"),
            gpu_memory_total_mb=result.get("gpu_memory_total_mb"),
            facefusion_version=result.get("facefusion_version"),
            stream_status=result.get("stream_status"),
            worker_url=result.get("worker_url"),
            error=result.get("error"),
        )

    except Exception as e:
        logger.exception(f"Face swap health check error: {e}")
        return FaceSwapHealthResponse(
            success=False,
            status="error",
            error=f"Health check failed: {str(e)}",
        )


# ──────────────────────────────────────────────
# 16. Full Health Check (Mode A + Mode B)
# ──────────────────────────────────────────────

@router.get(
    "/full-health",
    response_model=FullHealthResponse,
    summary="Combined health check for all livestream services",
    description=(
        "Check health of all services: "
        "Mode A (Tencent Digital Human + ElevenLabs voice cloning) and "
        "Mode B (FaceFusion GPU worker for face swapping)."
    ),
)
async def full_health_check(
    _auth: bool = Depends(verify_admin_key),
):
    try:
        # Mode A health
        mode_a_health = {}
        try:
            hybrid = get_hybrid_service()
            mode_a_health = await hybrid.health_check()
        except Exception as e:
            mode_a_health = {"status": "error", "error": str(e)}

        # Mode B health
        mode_b_health = {}
        try:
            face_swap = get_face_swap_service()
            mode_b_health = await face_swap.health_check()
        except Exception as e:
            mode_b_health = {"status": "error", "error": str(e)}

        # Determine overall status
        a_ok = mode_a_health.get("status") == "ok"
        b_ok = mode_b_health.get("status") in ("ok", "not_configured")

        if a_ok and b_ok:
            overall = "ok"
        elif a_ok:
            overall = "mode_b_issue"
        elif b_ok:
            overall = "mode_a_issue"
        else:
            overall = "both_issues"

        return FullHealthResponse(
            success=True,
            overall_status=overall,
            mode_a=mode_a_health,
            mode_b=mode_b_health,
            capabilities={
                "mode_a_digital_human": True,
                "mode_a_voice_cloning": True,
                "mode_a_japanese_tts": True,
                "mode_b_face_swap": bool(mode_b_health.get("status") == "ok"),
                "mode_b_realtime_stream": bool(mode_b_health.get("status") == "ok"),
                "mode_b_face_enhancer": True,
            },
        )

    except Exception as e:
        logger.exception(f"Full health check error: {e}")
        return FullHealthResponse(
            success=False,
            error=f"Health check failed: {str(e)}",
        )



# ══════════════════════════════════════════════
# MODE C: MUSETALK LIP-SYNC VIDEO GENERATION
# ══════════════════════════════════════════════
#
# These endpoints proxy to the GPU Worker's MuseTalk pipeline.
# Given a portrait image and audio file, MuseTalk generates a
# lip-synced video where the portrait appears to speak the audio.
#
# This is useful for:
#   - Creating product review videos with a consistent presenter
#   - Generating multilingual versions of the same presentation
#   - Pre-producing content for social media / e-commerce
#
# Architecture:
#   Portrait + Audio → GPU Worker (MuseTalk v1.5) → H.264+AAC Video
# ══════════════════════════════════════════════

from app.services.musetalk_service import (
    MuseTalkService,
    MuseTalkError,
    MuseTalkConnectionError,
    MuseTalkAPIError,
)
from app.schemas.digital_human_schema import (
    MuseTalkGenerateRequest,
    MuseTalkGenerateResponse,
    MuseTalkStatusResponse,
    MuseTalkHealthResponse,
)
from fastapi.responses import StreamingResponse
import io

# MuseTalk service singleton
_musetalk_service: Optional[MuseTalkService] = None


def get_musetalk_service() -> MuseTalkService:
    global _musetalk_service
    if _musetalk_service is None:
        _musetalk_service = MuseTalkService()
    return _musetalk_service


# ──────────────────────────────────────────────
# 17. MuseTalk Generate
# ──────────────────────────────────────────────

@router.post(
    "/musetalk/generate",
    response_model=MuseTalkGenerateResponse,
    summary="Generate a lip-synced video using MuseTalk",
    description=(
        "Start a MuseTalk lip-sync video generation job. "
        "Provide a portrait image URL and an audio file URL. "
        "The GPU worker will generate a video where the portrait appears to speak the audio. "
        "Use the status endpoint to poll for completion, then download the result."
    ),
)
async def musetalk_generate(
    req: MuseTalkGenerateRequest,
    _auth: bool = Depends(verify_admin_key),
):
    try:
        service = get_musetalk_service()
        result = await service.generate(
            portrait_url=req.portrait_url,
            audio_url=req.audio_url,
            job_id=req.job_id,
            bbox_shift=req.bbox_shift,
            extra_margin=req.extra_margin,
            batch_size=req.batch_size,
            output_fps=req.output_fps,
        )

        return MuseTalkGenerateResponse(
            success=True,
            job_id=result.get("job_id"),
            status=result.get("status", "queued"),
        )

    except MuseTalkConnectionError as e:
        logger.error(f"MuseTalk worker connection error: {e}")
        return MuseTalkGenerateResponse(
            success=False,
            error=f"GPU worker unreachable: {str(e)}",
        )
    except MuseTalkAPIError as e:
        logger.error(f"MuseTalk worker API error: {e}")
        return MuseTalkGenerateResponse(
            success=False,
            error=f"GPU worker error ({e.status_code}): {e.detail}",
        )
    except MuseTalkError as e:
        return MuseTalkGenerateResponse(success=False, error=str(e))
    except Exception as e:
        logger.exception(f"Unexpected error in MuseTalk generate: {e}")
        return MuseTalkGenerateResponse(
            success=False, error=f"Internal error: {str(e)}"
        )


# ──────────────────────────────────────────────
# 18. MuseTalk Status
# ──────────────────────────────────────────────

@router.get(
    "/musetalk/status/{job_id}",
    response_model=MuseTalkStatusResponse,
    summary="Check MuseTalk job status",
    description=(
        "Poll the status of a MuseTalk generation job. "
        "Returns progress (0-100) and status (queued/processing/completed/error)."
    ),
)
async def musetalk_status(
    job_id: str,
    _auth: bool = Depends(verify_admin_key),
):
    try:
        service = get_musetalk_service()
        result = await service.get_status(job_id)

        return MuseTalkStatusResponse(
            success=True,
            job_id=result.get("job_id", job_id),
            status=result.get("status"),
            progress=result.get("progress"),
            error=result.get("error"),
        )

    except MuseTalkConnectionError as e:
        return MuseTalkStatusResponse(
            success=False,
            job_id=job_id,
            error=f"GPU worker unreachable: {str(e)}",
        )
    except MuseTalkAPIError as e:
        return MuseTalkStatusResponse(
            success=False,
            job_id=job_id,
            error=f"GPU worker error ({e.status_code}): {e.detail}",
        )
    except Exception as e:
        logger.exception(f"Error checking MuseTalk status: {e}")
        return MuseTalkStatusResponse(
            success=False,
            job_id=job_id,
            error=f"Internal error: {str(e)}",
        )


# ──────────────────────────────────────────────
# 19. MuseTalk Download
# ──────────────────────────────────────────────

@router.get(
    "/musetalk/download/{job_id}",
    summary="Download MuseTalk generated video",
    description=(
        "Download the generated lip-synced video (MP4). "
        "The job must be in 'completed' status."
    ),
    responses={
        200: {
            "content": {"video/mp4": {}},
            "description": "The generated video file",
        },
    },
)
async def musetalk_download(
    job_id: str,
    _auth: bool = Depends(verify_admin_key),
):
    try:
        service = get_musetalk_service()

        # First check status
        status_result = await service.get_status(job_id)
        if status_result.get("status") != "completed":
            raise HTTPException(
                status_code=400,
                detail=f"Job not completed: {status_result.get('status')}",
            )

        # Download video
        video_bytes = await service.download(job_id)

        return StreamingResponse(
            io.BytesIO(video_bytes),
            media_type="video/mp4",
            headers={
                "Content-Disposition": f'attachment; filename="musetalk_{job_id}.mp4"',
                "Content-Length": str(len(video_bytes)),
            },
        )

    except HTTPException:
        raise
    except MuseTalkConnectionError as e:
        raise HTTPException(status_code=502, detail=f"GPU worker unreachable: {str(e)}")
    except MuseTalkAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    except Exception as e:
        logger.exception(f"Error downloading MuseTalk video: {e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


# ──────────────────────────────────────────────
# 20. MuseTalk Health Check
# ──────────────────────────────────────────────

@router.get(
    "/musetalk/health",
    response_model=MuseTalkHealthResponse,
    summary="Health check for MuseTalk GPU worker",
    description="Check connectivity and status of the MuseTalk GPU worker.",
)
async def musetalk_health_check(
    _auth: bool = Depends(verify_admin_key),
):
    try:
        service = get_musetalk_service()
        result = await service.health_check()

        return MuseTalkHealthResponse(
            success=result.get("status") in ("ok", "not_configured"),
            status=result.get("status"),
            gpu_name=result.get("gpu_name"),
            gpu_memory_used_mb=result.get("gpu_memory_used_mb"),
            gpu_memory_total_mb=result.get("gpu_memory_total_mb"),
            musetalk_loaded=result.get("musetalk_loaded"),
            worker_url=result.get("worker_url"),
            error=result.get("error"),
        )

    except Exception as e:
        logger.exception(f"MuseTalk health check error: {e}")
        return MuseTalkHealthResponse(
            success=False,
            status="error",
            error=f"Health check failed: {str(e)}",
        )


# ══════════════════════════════════════════════════════════════════════════════
# Mode C+: MuseTalk + ElevenLabs TTS  (Text → Lip-Synced Video)
# ══════════════════════════════════════════════════════════════════════════════
#
# Pipeline:
#   1. Text → ElevenLabs TTS → WAV audio bytes
#   2. WAV audio → Upload to Azure Blob → public URL
#   3. Portrait URL + Audio URL → MuseTalk GPU Worker → Lip-synced MP4
#
# This endpoint combines steps 1-3 into a single API call.
# ══════════════════════════════════════════════════════════════════════════════

from app.schemas.digital_human_schema import (
    MuseTalkTextGenerateRequest,
    MuseTalkTextGenerateResponse,
)
import uuid
import struct
import wave


def _pcm_to_wav(pcm_data: bytes, sample_rate: int = 16000, channels: int = 1, sample_width: int = 2) -> bytes:
    """Convert raw PCM bytes to WAV format."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return buf.getvalue()


async def _upload_audio_to_blob(audio_wav: bytes, job_id: str) -> str:
    """Upload WAV audio bytes to Azure Blob Storage and return the public URL."""
    from app.services.storage_service import generate_upload_sas
    import httpx as _httpx

    vid, upload_url, blob_url, expiry = await generate_upload_sas(
        email="ai-live-creator@aitherhub.com",
        video_id=f"tts-audio-{job_id}",
        filename=f"tts_{job_id}.wav",
    )

    # Upload the WAV bytes directly to Azure Blob via SAS URL
    async with _httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.put(
            upload_url,
            content=audio_wav,
            headers={
                "x-ms-blob-type": "BlockBlob",
                "Content-Type": "audio/wav",
            },
        )
        if resp.status_code not in (200, 201):
            raise MuseTalkError(
                f"Failed to upload TTS audio to blob: HTTP {resp.status_code}"
            )

    logger.info(f"TTS audio uploaded to blob: {blob_url} ({len(audio_wav)} bytes)")
    return blob_url


# ──────────────────────────────────────────────
# 21. MuseTalk Generate from Text (TTS + Lip-Sync)
# ──────────────────────────────────────────────

@router.post(
    "/musetalk/generate-from-text",
    response_model=MuseTalkTextGenerateResponse,
    summary="Generate lip-synced video from text (ElevenLabs TTS + MuseTalk)",
    description=(
        "Complete pipeline: Text → ElevenLabs TTS (voice synthesis) → "
        "MuseTalk GPU Worker (lip-sync video generation). "
        "Provide a portrait image URL and text. The AI will generate speech audio "
        "from the text, then create a video where the portrait lip-syncs to the audio. "
        "Use /musetalk/status/{job_id} to poll for completion."
    ),
)
async def musetalk_generate_from_text(
    req: MuseTalkTextGenerateRequest,
    _auth: bool = Depends(verify_admin_key),
):
    job_id = req.job_id or f"tts-mt-{int(__import__('time').time())}"

    try:
        # ── Step 1: Generate TTS audio via ElevenLabs ──
        logger.info(
            f"[{job_id}] Step 1: ElevenLabs TTS — text_len={len(req.text)}, "
            f"voice={req.voice_id or 'default'}, lang={req.language_code}"
        )

        el_service = get_elevenlabs_service()

        # Generate PCM audio (16kHz, 16bit, mono)
        pcm_audio = await el_service.text_to_speech(
            text=req.text,
            voice_id=req.voice_id,
            language_code=req.language_code,
            voice_settings=req.voice_settings,
            output_format="pcm_16000",
        )

        # Convert PCM to WAV (MuseTalk needs WAV format)
        wav_audio = _pcm_to_wav(pcm_audio, sample_rate=16000)
        tts_duration_ms = len(pcm_audio) / (16000 * 2) * 1000  # PCM 16kHz 16bit

        logger.info(
            f"[{job_id}] TTS complete: {len(wav_audio)} bytes WAV, "
            f"~{tts_duration_ms:.0f}ms duration"
        )

        # ── Step 2: Upload WAV to Azure Blob ──
        logger.info(f"[{job_id}] Step 2: Uploading TTS audio to Azure Blob...")
        audio_url = await _upload_audio_to_blob(wav_audio, job_id)

        # ── Step 3: Start MuseTalk generation ──
        logger.info(
            f"[{job_id}] Step 3: Starting MuseTalk generation — "
            f"portrait={req.portrait_url[:60]}..., audio={audio_url[:60]}..."
        )

        service = get_musetalk_service()
        result = await service.generate(
            portrait_url=req.portrait_url,
            audio_url=audio_url,
            job_id=job_id,
            bbox_shift=req.bbox_shift,
            extra_margin=req.extra_margin,
            batch_size=req.batch_size,
            output_fps=req.output_fps,
        )

        logger.info(f"[{job_id}] MuseTalk job submitted: {result}")

        return MuseTalkTextGenerateResponse(
            success=True,
            job_id=result.get("job_id", job_id),
            status=result.get("status", "queued"),
            tts_duration_ms=round(tts_duration_ms, 1),
            audio_url=audio_url,
        )

    except ElevenLabsError as e:
        logger.error(f"[{job_id}] ElevenLabs TTS error: {e}")
        return MuseTalkTextGenerateResponse(
            success=False,
            job_id=job_id,
            error=f"TTS error: {str(e)}",
        )
    except MuseTalkConnectionError as e:
        logger.error(f"[{job_id}] MuseTalk worker connection error: {e}")
        return MuseTalkTextGenerateResponse(
            success=False,
            job_id=job_id,
            error=f"GPU worker unreachable: {str(e)}",
        )
    except MuseTalkAPIError as e:
        logger.error(f"[{job_id}] MuseTalk worker API error: {e}")
        return MuseTalkTextGenerateResponse(
            success=False,
            job_id=job_id,
            error=f"GPU worker error ({e.status_code}): {e.detail}",
        )
    except Exception as e:
        logger.exception(f"[{job_id}] Unexpected error in TTS+MuseTalk pipeline: {e}")
        return MuseTalkTextGenerateResponse(
            success=False,
            job_id=job_id,
            error=f"Internal error: {str(e)}",
        )


# ──────────────────────────────────────────────
# 22. List ElevenLabs Voices (for AI Live Creator voice selector)
# ──────────────────────────────────────────────

@router.get(
    "/musetalk/voices",
    summary="List available ElevenLabs voices for AI Live Creator",
    description="List all voices including cloned voices available for TTS in AI Live Creator.",
)
async def musetalk_list_voices(
    _auth: bool = Depends(verify_admin_key),
):
    try:
        el_service = get_elevenlabs_service()
        voices = await el_service.list_voices()

        voice_list = []
        for v in voices:
            is_cloned = v.get("category") == "cloned"
            voice_list.append({
                "voice_id": v.get("voice_id"),
                "name": v.get("name"),
                "category": v.get("category"),
                "is_cloned": is_cloned,
                "labels": v.get("labels", {}),
            })

        return {
            "success": True,
            "voices": voice_list,
            "total_count": len(voice_list),
        }

    except ElevenLabsError as e:
        return {"success": False, "error": str(e), "voices": []}
    except Exception as e:
        logger.exception(f"Error listing voices for AI Live Creator: {e}")
        return {"success": False, "error": f"Internal error: {str(e)}", "voices": []}
