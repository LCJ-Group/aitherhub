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
import time
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
        # Ensure URLs have SAS tokens for GPU Worker access
        portrait_url = _ensure_sas_url(req.portrait_url)
        audio_url = _ensure_sas_url(req.audio_url)

        service = get_musetalk_service()
        result = await service.generate(
            portrait_url=portrait_url,
            audio_url=audio_url,
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


def _ensure_sas_url(url: str) -> str:
    """If the URL points to our Azure Blob and has no SAS token, add a read SAS."""
    if not url:
        return url
    # Only process our own blob URLs
    if "blob.core.windows.net" not in url and "aitherhub" not in url:
        return url
    # Already has SAS token
    if "sig=" in url or "sv=" in url:
        return url
    try:
        from app.services.storage_service import generate_read_sas_from_url
        sas_url = generate_read_sas_from_url(url, expires_hours=24)
        if sas_url:
            logger.info(f"Added read SAS to blob URL: {url[:60]}...")
            return sas_url
    except Exception as exc:
        logger.warning(f"Failed to add SAS to URL {url[:60]}: {exc}")
    return url


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

        # ── Step 2.5: Add SAS tokens for GPU Worker access ──
        audio_url = _ensure_sas_url(audio_url)
        portrait_url = _ensure_sas_url(req.portrait_url)

        # ── Step 3: Start MuseTalk generation ──
        logger.info(
            f"[{job_id}] Step 3: Starting MuseTalk generation — "
            f"portrait={portrait_url[:60]}..., audio={audio_url[:60]}..."
        )

        service = get_musetalk_service()
        result = await service.generate(
            portrait_url=portrait_url,
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


# ══════════════════════════════════════════════
# Mode D: IMTalker Premium Digital Human
# ══════════════════════════════════════════════
#
# IMTalker produces full facial animation:
#   - Head movement
#   - Facial expressions
#   - Eye blinks & gaze
#   - Lip-sync
#
# Uses the same GPU Worker and job tracking as MuseTalk.
# Status/download endpoints are shared (same digital_human_jobs dict on worker).

from app.schemas.digital_human_schema import (
    IMTalkerGenerateRequest,
    IMTalkerGenerateResponse,
    IMTalkerTextGenerateRequest,
    IMTalkerTextGenerateResponse,
)


# ──────────────────────────────────────────────
# 23. IMTalker Generate (Audio Mode)
# ──────────────────────────────────────────────

@router.post(
    "/imtalker/generate",
    response_model=IMTalkerGenerateResponse,
    summary="Generate premium digital human video (IMTalker)",
    description=(
        "Start a premium digital human video generation using IMTalker. "
        "Produces full facial animation (head movement, expressions, eye blinks) "
        "in addition to lip-sync. Requires a portrait image and audio file."
    ),
)
async def imtalker_generate(
    req: IMTalkerGenerateRequest,
    _auth: bool = Depends(verify_admin_key),
):
    import uuid
    job_id = req.job_id or f"imt-{uuid.uuid4().hex[:12]}"

    try:
        service = get_musetalk_service()  # Same GPU worker

        # Ensure SAS URLs for Azure Blob access
        portrait_url = _ensure_sas_url(req.portrait_url)
        audio_url = _ensure_sas_url(req.audio_url)

        payload = {
            "job_id": job_id,
            "portrait_url": portrait_url,
            "audio_url": audio_url,
            "a_cfg_scale": req.a_cfg_scale,
            "nfe": req.nfe,
            "crop": req.crop,
            "output_fps": req.output_fps,
        }

        resp = await service._request(
            "POST", "/api/digital-human/imtalker/generate", json=payload
        )
        result = resp.json()

        return IMTalkerGenerateResponse(
            success=True,
            job_id=result.get("job_id", job_id),
            status=result.get("status", "queued"),
            engine="imtalker",
        )

    except MuseTalkConnectionError as e:
        return IMTalkerGenerateResponse(
            success=False, job_id=job_id, error=f"GPU Worker offline: {e}", engine="imtalker"
        )
    except MuseTalkAPIError as e:
        return IMTalkerGenerateResponse(
            success=False, job_id=job_id, error=f"Worker error: {e.detail}", engine="imtalker"
        )
    except Exception as e:
        logger.exception(f"IMTalker generate error: {e}")
        return IMTalkerGenerateResponse(
            success=False, job_id=job_id, error=str(e), engine="imtalker"
        )


# ──────────────────────────────────────────────
# 24. IMTalker Generate from Text (TTS + Premium Animation)
# ──────────────────────────────────────────────

@router.post(
    "/imtalker/generate-from-text",
    response_model=IMTalkerTextGenerateResponse,
    summary="Generate premium video from text (ElevenLabs TTS + IMTalker)",
    description=(
        "Full pipeline: Text → ElevenLabs TTS (voice cloning) → IMTalker (premium animation). "
        "Produces a video with full facial animation driven by AI-generated speech."
    ),
)
async def imtalker_generate_from_text(
    req: IMTalkerTextGenerateRequest,
    _auth: bool = Depends(verify_admin_key),
):
    job_id = req.job_id or f"tts-imt-{int(__import__('time').time())}"

    try:
        # ── Step 1: Generate TTS audio via ElevenLabs ──
        logger.info(f"[{job_id}] Step 1: Generating TTS audio via ElevenLabs...")
        el_service = get_elevenlabs_service()

        voice_settings = req.voice_settings or {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True,
        }

        # Generate PCM audio (16kHz, 16bit, mono — same as MuseTalk)
        pcm_audio = await el_service.text_to_speech(
            text=req.text,
            voice_id=req.voice_id,
            voice_settings=voice_settings,
            output_format="pcm_16000",
            language_code=req.language_code,
        )

        if not pcm_audio:
            raise ValueError("ElevenLabs returned empty audio data")

        # Convert PCM to WAV using shared helper
        wav_data = _pcm_to_wav(pcm_audio, sample_rate=16000)
        tts_duration_ms = len(pcm_audio) / (16000 * 2) * 1000
        logger.info(f"[{job_id}] TTS audio generated: {tts_duration_ms:.0f}ms, {len(pcm_audio)} bytes PCM")

        # ── Step 2: Upload WAV to Azure Blob ──
        logger.info(f"[{job_id}] Step 2: Uploading TTS audio to Azure Blob...")
        audio_url = await _upload_audio_to_blob(wav_data, job_id)

        # ── Step 2.5: Add SAS tokens for GPU Worker access ──
        audio_url = _ensure_sas_url(audio_url)
        portrait_url = _ensure_sas_url(req.portrait_url)

        logger.info(
            f"[{job_id}] Step 3: Starting IMTalker generation — "
            f"portrait={portrait_url[:60]}... audio={audio_url[:60]}..."
        )

        service = get_musetalk_service()  # Same GPU worker
        payload = {
            "job_id": job_id,
            "portrait_url": portrait_url,
            "audio_url": audio_url,
            "a_cfg_scale": req.a_cfg_scale,
            "nfe": req.nfe,
            "crop": req.crop,
            "output_fps": req.output_fps,
        }

        resp = await service._request(
            "POST", "/api/digital-human/imtalker/generate", json=payload
        )
        result = resp.json()
        logger.info(f"[{job_id}] IMTalker job submitted: {result}")

        return IMTalkerTextGenerateResponse(
            success=True,
            job_id=result.get("job_id", job_id),
            status=result.get("status", "queued"),
            tts_duration_ms=tts_duration_ms,
            audio_url=audio_url,
            engine="imtalker",
        )

    except ElevenLabsError as e:
        return IMTalkerTextGenerateResponse(
            success=False, job_id=job_id, error=f"TTS error: {e}", engine="imtalker"
        )
    except MuseTalkConnectionError as e:
        logger.error(f"[{job_id}] GPU Worker connection error: {e}")
        return IMTalkerTextGenerateResponse(
            success=False, job_id=job_id, error=f"GPU Worker offline: {e}", engine="imtalker"
        )
    except MuseTalkAPIError as e:
        logger.error(f"[{job_id}] GPU Worker API error: {e}")
        return IMTalkerTextGenerateResponse(
            success=False, job_id=job_id, error=f"Worker error: {e.detail}", engine="imtalker"
        )
    except Exception as e:
        logger.exception(f"[{job_id}] Unexpected error in TTS+IMTalker pipeline: {e}")
        return IMTalkerTextGenerateResponse(
            success=False, job_id=job_id, error=str(e), engine="imtalker"
        )


# ──────────────────────────────────────────────
# 25. IMTalker Status (reuses MuseTalk status endpoint on worker)
# ──────────────────────────────────────────────

@router.get(
    "/imtalker/status/{job_id}",
    response_model=MuseTalkStatusResponse,  # Same response format
    summary="Check IMTalker job status",
)
async def imtalker_status(
    job_id: str,
    _auth: bool = Depends(verify_admin_key),
):
    """IMTalker shares the same job tracking system as MuseTalk on the GPU Worker."""
    try:
        service = get_musetalk_service()
        result = await service.get_status(job_id)
        return MuseTalkStatusResponse(
            success=True,
            job_id=job_id,
            status=result.get("status"),
            progress=result.get("progress"),
            error=result.get("error"),
        )
    except (MuseTalkConnectionError, MuseTalkAPIError) as e:
        return MuseTalkStatusResponse(
            success=False, job_id=job_id, status="error", error=str(e)
        )
    except Exception as e:
        return MuseTalkStatusResponse(
            success=False, job_id=job_id, status="error", error=str(e)
        )


# ──────────────────────────────────────────────
# 26. IMTalker Download (reuses MuseTalk download endpoint on worker)
# ──────────────────────────────────────────────

@router.get(
    "/imtalker/download/{job_id}",
    summary="Download IMTalker generated video",
)
async def imtalker_download(
    job_id: str,
    _auth: bool = Depends(verify_admin_key),
):
    """IMTalker shares the same download system as MuseTalk on the GPU Worker."""
    try:
        service = get_musetalk_service()
        video_bytes = await service.download(job_id)

        return StreamingResponse(
            io.BytesIO(video_bytes),
            media_type="video/mp4",
            headers={
                "Content-Disposition": f'attachment; filename="imtalker_{job_id}.mp4"',
                "Content-Length": str(len(video_bytes)),
            },
        )
    except MuseTalkConnectionError as e:
        raise HTTPException(status_code=503, detail=f"GPU Worker offline: {e}")
    except MuseTalkAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    except Exception as e:
        logger.exception(f"Error downloading IMTalker video: {e}")
        raise HTTPException(status_code=500, detail=str(e))



# ══════════════════════════════════════════════
# Mode E: AI Live Creator — Livestream Brain
# ══════════════════════════════════════════════
#
# Live Session management with:
#   - Sales Brain (帯貨大脳): Product → GPT script → TTS → Digital Human video
#   - Comment Response: Comment → GPT reply → TTS → Digital Human video
#   - Video Queue: Pre-generated segments for livestream playback
#
# Architecture:
#   Product Info ─┐
#                 ├──▶ GPT Script ──▶ ElevenLabs TTS ──▶ IMTalker/MuseTalk ──▶ Video Queue
#   Comment ──────┘
# ══════════════════════════════════════════════

from app.schemas.digital_human_schema import (
    CreateLiveSessionRequest,
    CreateLiveSessionResponse,
    LiveSessionStatusResponse,
    ListLiveSessionsResponse,
    GenerateProductScriptRequest,
    GenerateProductScriptResponse,
    CommentResponseRequest,
    CommentResponseResponse,
    GenerateAndQueueRequest,
    GenerateAndQueueResponse,
)
from app.services.live_session_service import (
    create_session,
    get_session,
    list_sessions,
    close_session,
    add_to_queue,
    update_queue_item,
    generate_product_script,
    generate_comment_response,
    generate_session_scripts,
)


# ──────────────────────────────────────────────
# 27. Create Live Session
# ──────────────────────────────────────────────

@router.post(
    "/live-session/create",
    response_model=CreateLiveSessionResponse,
    summary="Create a new AI Live Creator session",
    description=(
        "Create a livestream session with portrait, engine, voice, and product list. "
        "The session manages video generation queue and Sales Brain scripts."
    ),
)
async def create_live_session(
    req: CreateLiveSessionRequest,
    _auth: bool = Depends(verify_admin_key),
):
    try:
        products = [p.model_dump() for p in req.products] if req.products else []
        portrait_url = _ensure_sas_url(req.portrait_url)

        session = create_session(
            portrait_url=portrait_url,
            portrait_type=getattr(req, 'portrait_type', 'image'),
            engine=req.engine,
            voice_id=req.voice_id,
            language=req.language,
            products=products,
        )

        return CreateLiveSessionResponse(
            success=True,
            session_id=session["session_id"],
            status=session["status"],
            engine=session["engine"],
            products_count=len(products),
        )

    except Exception as e:
        logger.exception(f"Error creating live session: {e}")
        return CreateLiveSessionResponse(
            success=False, error=f"Internal error: {str(e)}"
        )


# ──────────────────────────────────────────────
# 28. Get Live Session Status
# ──────────────────────────────────────────────

@router.get(
    "/live-session/{session_id}",
    response_model=LiveSessionStatusResponse,
    summary="Get live session status and queue",
)
async def get_live_session(
    session_id: str,
    _auth: bool = Depends(verify_admin_key),
):
    session = get_session(session_id)
    if not session:
        return LiveSessionStatusResponse(
            success=False, error=f"Session {session_id} not found"
        )
    return LiveSessionStatusResponse(success=True, session=session)


# ──────────────────────────────────────────────
# 29. List Live Sessions
# ──────────────────────────────────────────────

@router.get(
    "/live-sessions",
    response_model=ListLiveSessionsResponse,
    summary="List all active live sessions",
)
async def list_live_sessions(
    _auth: bool = Depends(verify_admin_key),
):
    sessions = list_sessions()
    return ListLiveSessionsResponse(success=True, sessions=sessions)


# ──────────────────────────────────────────────
# 30. Close Live Session
# ──────────────────────────────────────────────

@router.post(
    "/live-session/{session_id}/close",
    summary="Close a live session",
)
async def close_live_session_endpoint(
    session_id: str,
    _auth: bool = Depends(verify_admin_key),
):
    success = close_session(session_id)
    if not success:
        return {"success": False, "error": f"Session {session_id} not found"}
    return {"success": True, "session_id": session_id, "status": "closed"}


# ──────────────────────────────────────────────
# 31. Sales Brain — Generate Product Script (帯貨大脳)
# ──────────────────────────────────────────────

@router.post(
    "/sales-brain/generate-script",
    response_model=GenerateProductScriptResponse,
    summary="Generate a livestream script for a product (Sales Brain / 帯貨大脳)",
    description=(
        "The Sales Brain analyzes product information and generates an optimized "
        "livestream script for the digital human to read. Supports multiple tones "
        "and script types (introduction, highlight, promotion, closing)."
    ),
)
async def sales_brain_generate_script(
    req: GenerateProductScriptRequest,
    _auth: bool = Depends(verify_admin_key),
):
    try:
        script = await generate_product_script(
            product_name=req.product_name,
            product_description=req.product_description,
            product_price=req.product_price,
            product_features=req.product_features,
            tone=req.tone,
            language=req.language,
            script_type=req.script_type,
        )

        return GenerateProductScriptResponse(
            success=True,
            product_name=req.product_name,
            script_type=req.script_type,
            script_text=script,
            script_length=len(script),
        )

    except Exception as e:
        logger.exception(f"Sales Brain script generation error: {e}")
        return GenerateProductScriptResponse(
            success=False, error=f"Script generation failed: {str(e)}"
        )


# ──────────────────────────────────────────────
# 32. Sales Brain — Generate All Session Scripts
# ──────────────────────────────────────────────

@router.post(
    "/live-session/{session_id}/generate-all-scripts",
    summary="Generate scripts for all products in a session",
)
async def generate_all_session_scripts(
    session_id: str,
    _auth: bool = Depends(verify_admin_key),
):
    try:
        results = await generate_session_scripts(session_id)
        return {
            "success": True,
            "session_id": session_id,
            "scripts": results,
            "total": len(results),
        }
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.exception(f"Error generating session scripts: {e}")
        return {"success": False, "error": f"Internal error: {str(e)}"}


# ──────────────────────────────────────────────
# 33. Comment Response — Generate Reply
# ──────────────────────────────────────────────

@router.post(
    "/comment-response/generate",
    response_model=CommentResponseResponse,
    summary="Generate a response to a viewer comment",
    description=(
        "The AI generates a natural response to a viewer's comment. "
        "Optionally auto-generates a digital human video with the response."
    ),
)
async def comment_response_generate(
    req: CommentResponseRequest,
    _auth: bool = Depends(verify_admin_key),
):
    try:
        current_product = None
        if req.current_product:
            current_product = req.current_product.model_dump()

        reply = await generate_comment_response(
            comment_text=req.comment_text,
            commenter_name=req.commenter_name,
            current_product=current_product,
            language=req.language,
        )

        video_job_id = None

        # Auto-generate video if requested
        if req.auto_generate_video and req.portrait_url:
            try:
                portrait_url = _ensure_sas_url(req.portrait_url)
                # Get portrait_type from session or default to image
                portrait_type = "image"
                if req.session_id:
                    sess = get_session(req.session_id)
                    if sess:
                        portrait_type = sess.get("portrait_type", "image")
                el_service = get_elevenlabs_service()

                # Generate TTS
                pcm_audio = await el_service.text_to_speech(
                    text=reply,
                    voice_id=req.voice_id,
                    output_format="pcm_16000",
                    language_code=req.language,
                )
                wav_data = _pcm_to_wav(pcm_audio, sample_rate=16000)
                job_id = f"cr-{int(time.time())}"
                audio_url = await _upload_audio_to_blob(wav_data, job_id)
                audio_url = _ensure_sas_url(audio_url)

                service = get_musetalk_service()

                if req.engine == "imtalker":
                    payload = {
                        "job_id": job_id,
                        "portrait_url": portrait_url,
                        "portrait_type": portrait_type,
                        "audio_url": audio_url,
                        "a_cfg_scale": 2.0,
                        "nfe": 10,
                        "crop": True if portrait_type == "image" else False,
                        "output_fps": 25,
                    }
                    resp = await service._request(
                        "POST", "/api/digital-human/imtalker/generate", json=payload
                    )
                else:
                    result = await service.generate(
                        portrait_url=portrait_url,
                        portrait_type=portrait_type,
                        audio_url=audio_url,
                        job_id=job_id,
                    )

                video_job_id = job_id
                logger.info(f"Comment response video queued: {job_id}")

                # Add to session queue if session_id provided
                if req.session_id:
                    add_to_queue(req.session_id, {
                        "job_id": job_id,
                        "type": "comment_reply",
                        "status": "processing",
                        "text_preview": reply[:100],
                        "comment": req.comment_text,
                        "commenter": req.commenter_name,
                        "timestamp": time.time(),
                    })

            except Exception as ve:
                logger.error(f"Comment response video generation failed: {ve}")
                # Still return the text reply even if video fails

        # Record in session history
        if req.session_id:
            session = get_session(req.session_id)
            if session:
                session["comment_history"].append({
                    "comment": req.comment_text,
                    "commenter": req.commenter_name,
                    "reply": reply,
                    "job_id": video_job_id,
                    "timestamp": time.time(),
                })

        return CommentResponseResponse(
            success=True,
            comment_text=req.comment_text,
            reply_text=reply,
            reply_length=len(reply),
            video_job_id=video_job_id,
        )

    except Exception as e:
        logger.exception(f"Comment response error: {e}")
        return CommentResponseResponse(
            success=False, error=f"Failed: {str(e)}"
        )


# ──────────────────────────────────────────────
# 34. Generate Video and Add to Queue
# ──────────────────────────────────────────────

@router.post(
    "/live-session/{session_id}/generate-video",
    response_model=GenerateAndQueueResponse,
    summary="Generate a digital human video and add to session queue",
    description=(
        "Generate a video from text using the session's portrait and engine, "
        "then add it to the video queue for livestream playback."
    ),
)
async def generate_and_queue_video(
    session_id: str,
    req: GenerateAndQueueRequest,
    _auth: bool = Depends(verify_admin_key),
):
    session = get_session(session_id)
    if not session:
        return GenerateAndQueueResponse(
            success=False, error=f"Session {session_id} not found"
        )

    try:
        portrait_url = _ensure_sas_url(session["portrait_url"])
        portrait_type = session.get("portrait_type", "image")
        el_service = get_elevenlabs_service()

        # Step 1: TTS
        pcm_audio = await el_service.text_to_speech(
            text=req.text,
            voice_id=session.get("voice_id"),
            output_format="pcm_16000",
            language_code=session.get("language", "ja"),
        )
        wav_data = _pcm_to_wav(pcm_audio, sample_rate=16000)
        job_id = f"lq-{int(time.time())}"
        audio_url = await _upload_audio_to_blob(wav_data, job_id)
        audio_url = _ensure_sas_url(audio_url)

        # Step 2: Start video generation
        service = get_musetalk_service()

        if session["engine"] == "imtalker":
            payload = {
                "job_id": job_id,
                "portrait_url": portrait_url,
                "portrait_type": portrait_type,
                "audio_url": audio_url,
                "a_cfg_scale": 2.0,
                "nfe": 10,
                "crop": True if portrait_type == "image" else False,
                "output_fps": 25,
            }
            resp = await service._request(
                "POST", "/api/digital-human/imtalker/generate", json=payload
            )
        else:
            await service.generate(
                portrait_url=portrait_url,
                portrait_type=portrait_type,
                audio_url=audio_url,
                job_id=job_id,
            )

        # Step 3: Add to queue
        queue_item = {
            "job_id": job_id,
            "type": req.queue_type,
            "status": "processing",
            "text_preview": req.text[:100],
            "product_name": req.product_name,
            "timestamp": time.time(),
        }
        add_to_queue(session_id, queue_item)

        return GenerateAndQueueResponse(
            success=True,
            job_id=job_id,
            queue_position=len(session["video_queue"]),
            status="processing",
        )

    except ElevenLabsError as e:
        return GenerateAndQueueResponse(
            success=False, error=f"TTS error: {str(e)}"
        )
    except (MuseTalkConnectionError, MuseTalkAPIError) as e:
        return GenerateAndQueueResponse(
            success=False, error=f"GPU Worker error: {str(e)}"
        )
    except Exception as e:
        logger.exception(f"Error in generate-and-queue: {e}")
        return GenerateAndQueueResponse(
            success=False, error=f"Internal error: {str(e)}"
        )


# ──────────────────────────────────────────────
# 35. Get Session Video Queue Status
# ──────────────────────────────────────────────

@router.get(
    "/live-session/{session_id}/queue",
    summary="Get the video queue for a live session",
)
async def get_session_queue(
    session_id: str,
    _auth: bool = Depends(verify_admin_key),
):
    session = get_session(session_id)
    if not session:
        return {"success": False, "error": f"Session {session_id} not found"}

    # Update queue item statuses from GPU Worker
    service = get_musetalk_service()
    for item in session["video_queue"]:
        if item.get("status") == "processing":
            try:
                status_result = await service.get_status(item["job_id"])
                item["status"] = status_result.get("status", item["status"])
                item["progress"] = status_result.get("progress", 0)
            except Exception:
                pass  # Keep current status

    return {
        "success": True,
        "session_id": session_id,
        "queue": session["video_queue"],
        "total": len(session["video_queue"]),
        "completed": sum(1 for i in session["video_queue"] if i.get("status") == "completed"),
        "processing": sum(1 for i in session["video_queue"] if i.get("status") == "processing"),
    }



# ──────────────────────────────────────────────
# 36. TikTok Shop Product Import
# ──────────────────────────────────────────────

from app.schemas.digital_human_schema import (
    TikTokProductImportRequest,
    TikTokProductImportResponse,
)
from app.services.live_session_service import (
    import_tiktok_product,
    add_product_to_session,
)


@router.post(
    "/tiktok-product/import",
    response_model=TikTokProductImportResponse,
    summary="Import a product from TikTok Shop URL",
    description=(
        "Paste a TikTok Shop product URL (short or full). "
        "The system resolves the URL, extracts product info from og_info, "
        "and uses GPT to analyze and structure the product data. "
        "Optionally adds the product to a live session."
    ),
)
async def tiktok_product_import(
    req: TikTokProductImportRequest,
    _auth: bool = Depends(verify_admin_key),
):
    try:
        result = await import_tiktok_product(
            product_url=req.product_url,
            language=req.language,
        )

        if not result.get("success"):
            return TikTokProductImportResponse(
                success=False,
                error=result.get("error", "Import failed"),
            )

        product = result["product"]
        added_to_session = None

        # Auto-add to session if session_id provided
        if req.session_id:
            added_to_session = add_product_to_session(req.session_id, product)

        return TikTokProductImportResponse(
            success=True,
            product=product,
            added_to_session=added_to_session,
        )

    except Exception as e:
        logger.exception(f"TikTok product import endpoint error: {e}")
        return TikTokProductImportResponse(
            success=False,
            error=f"Internal error: {str(e)}",
        )



# ══════════════════════════════════════════════
# Real-time TTS Speak (Video Loop + Audio Overlay)
# ══════════════════════════════════════════════
from app.schemas.digital_human_schema import (
    TTSSpeakRequest,
    TTSSpeakResponse,
    AutoPilotStartRequest,
    AutoPilotStartResponse,
    AutoPilotNextRequest,
    AutoPilotNextResponse,
)


async def _upload_mp3_to_blob(mp3_data: bytes, audio_id: str) -> str:
    """Upload MP3 audio bytes to Azure Blob Storage and return the public URL."""
    from app.services.storage_service import generate_upload_sas
    import httpx as _httpx

    vid, upload_url, blob_url, expiry = await generate_upload_sas(
        email="ai-live-creator@aitherhub.com",
        video_id=f"tts-mp3-{audio_id}",
        filename=f"speak_{audio_id}.mp3",
    )

    async with _httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.put(
            upload_url,
            content=mp3_data,
            headers={
                "x-ms-blob-type": "BlockBlob",
                "Content-Type": "audio/mpeg",
            },
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"Failed to upload MP3 audio to blob: HTTP {resp.status_code}"
            )

    logger.info(f"TTS MP3 uploaded to blob: {blob_url} ({len(mp3_data)} bytes)")
    return blob_url


async def _upload_video_to_blob(video_bytes: bytes, job_id: str) -> str:
    """Upload MP4 video bytes to Azure Blob Storage and return the public URL."""
    from app.services.storage_service import generate_upload_sas
    import httpx as _httpx

    vid, upload_url, blob_url, expiry = await generate_upload_sas(
        email="ai-live-creator@aitherhub.com",
        video_id=f"lipsync-video-{job_id}",
        filename=f"lipsync_{job_id}.mp4",
    )

    async with _httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.put(
            upload_url,
            content=video_bytes,
            headers={
                "x-ms-blob-type": "BlockBlob",
                "Content-Type": "video/mp4",
            },
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"Failed to upload lip-sync video to blob: HTTP {resp.status_code}"
            )

    logger.info(f"Lip-sync video uploaded to blob: {blob_url} ({len(video_bytes)} bytes)")
    return blob_url


async def _generate_lipsync_video(
    portrait_url: str,
    audio_url: str,
    engine: str = "musetalk",
    max_wait_sec: int = 120,
) -> Optional[str]:
    """
    Generate a lip-synced video using MuseTalk or IMTalker GPU Worker.
    Submits the job, polls for completion, downloads the video,
    uploads to Azure Blob, and returns the SAS URL.

    Returns None if GPU Worker is unavailable or generation fails.
    """
    import uuid
    job_id = f"ap-{uuid.uuid4().hex[:10]}"

    try:
        service = get_musetalk_service()

        # Ensure SAS tokens for GPU Worker access
        portrait_sas = _ensure_sas_url(portrait_url)
        audio_sas = _ensure_sas_url(audio_url)

        if engine == "imtalker":
            # IMTalker: full facial animation + lip-sync
            payload = {
                "job_id": job_id,
                "portrait_url": portrait_sas,
                "audio_url": audio_sas,
                "a_cfg_scale": 2.0,
                "nfe": 32,
                "crop": True,
                "output_fps": 25,
            }
            resp = await service._request(
                "POST", "/api/digital-human/imtalker/generate", json=payload
            )
        else:
            # MuseTalk: standard lip-sync (faster)
            payload = {
                "job_id": job_id,
                "portrait_url": portrait_sas,
                "portrait_type": "video",
                "audio_url": audio_sas,
                "bbox_shift": 0,
                "extra_margin": 10,
                "batch_size": 16,
                "output_fps": 25,
            }
            resp = await service._request(
                "POST", "/api/digital-human/generate", json=payload
            )

        result = resp.json()
        actual_job_id = result.get("job_id", job_id)
        logger.info(f"[AutoPilot LipSync] Job submitted: {actual_job_id} (engine={engine})")

        # Poll for completion
        import asyncio
        poll_interval = 3  # seconds
        elapsed = 0
        while elapsed < max_wait_sec:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            status_resp = await service.get_status(actual_job_id)
            status = status_resp.get("status", "unknown")
            progress = status_resp.get("progress", 0)

            logger.info(
                f"[AutoPilot LipSync] Job {actual_job_id}: status={status}, "
                f"progress={progress}%, elapsed={elapsed}s"
            )

            if status == "completed":
                # Download the video
                video_bytes = await service.download(actual_job_id)
                if not video_bytes:
                    logger.error(f"[AutoPilot LipSync] Empty video download for {actual_job_id}")
                    return None

                # Upload to Azure Blob
                blob_url = await _upload_video_to_blob(video_bytes, actual_job_id)
                video_url = _ensure_sas_url(blob_url)
                logger.info(
                    f"[AutoPilot LipSync] Video ready: {video_url[:80]}... "
                    f"({len(video_bytes)} bytes, {elapsed}s)"
                )
                return video_url

            elif status in ("error", "failed"):
                error_msg = status_resp.get("error", "Unknown error")
                logger.error(f"[AutoPilot LipSync] Job {actual_job_id} failed: {error_msg}")
                return None

        logger.warning(f"[AutoPilot LipSync] Job {actual_job_id} timed out after {max_wait_sec}s")
        return None

    except Exception as e:
        logger.error(f"[AutoPilot LipSync] Error generating lip-sync video: {e}")
        return None


# ──────────────────────────────────────────────
# 37. TTS Speak — Generate audio for real-time playback
# ───────────────────────────────────────────────
@router.post(
    "/live-session/{session_id}/speak",
    response_model=TTSSpeakResponse,
    summary="Generate TTS audio for real-time playback over looping video",
    description=(
        "Generate speech audio (MP3) from text using ElevenLabs TTS. "
        "The audio is uploaded to Azure Blob and a SAS URL is returned. "
        "The frontend plays this audio over the continuously looping portrait video. "
        "No GPU video generation needed — instant response."
    ),
)
async def tts_speak(
    session_id: str,
    req: TTSSpeakRequest,
    _auth: bool = Depends(verify_admin_key),
):
    session = get_session(session_id)
    if not session:
        return TTSSpeakResponse(
            success=False, error=f"Session {session_id} not found"
        )

    try:
        el_service = get_elevenlabs_service()
        voice_id = req.voice_id or session.get("voice_id")
        language = req.language or session.get("language", "zh")

        # Generate MP3 audio (browser-compatible format)
        mp3_audio = await el_service.text_to_speech(
            text=req.text,
            voice_id=voice_id,
            output_format="mp3_44100_128",
            language_code=language,
        )

        # Calculate duration from MP3 size (approximate: 128kbps = 16KB/s)
        duration_ms = len(mp3_audio) / 16.0  # 128kbps / 8 = 16 bytes/ms

        # Upload to Azure Blob
        audio_id = f"speak-{int(time.time())}-{session_id[:8]}"
        blob_url = await _upload_mp3_to_blob(mp3_audio, audio_id)
        audio_url = _ensure_sas_url(blob_url)

        # Track in session
        speak_item = {
            "audio_id": audio_id,
            "type": req.speak_type,
            "text": req.text[:200],
            "product_name": req.product_name,
            "audio_url": audio_url,
            "duration_ms": duration_ms,
            "timestamp": time.time(),
        }
        if "speak_history" not in session:
            session["speak_history"] = []
        session["speak_history"].append(speak_item)

        return TTSSpeakResponse(
            success=True,
            audio_url=audio_url,
            audio_duration_ms=round(duration_ms, 1),
            text=req.text,
            speak_type=req.speak_type,
        )

    except ElevenLabsError as e:
        return TTSSpeakResponse(
            success=False, error=f"TTS error: {str(e)}"
        )
    except Exception as e:
        logger.exception(f"Error in TTS speak: {e}")
        return TTSSpeakResponse(
            success=False, error=f"Internal error: {str(e)}"
        )


# ──────────────────────────────────────────────
# 38. AutoPilot Start — Initialize the livestream brain
# ──────────────────────────────────────────────
@router.post(
    "/live-session/{session_id}/autopilot/start",
    response_model=AutoPilotStartResponse,
    summary="Start the auto-pilot livestream brain",
    description=(
        "Initialize the auto-pilot state machine for a live session. "
        "The brain will automatically cycle through greeting → product intro → "
        "comment response → sales pitch → next product."
    ),
)
async def autopilot_start(
    session_id: str,
    req: AutoPilotStartRequest,
    _auth: bool = Depends(verify_admin_key),
):
    session = get_session(session_id)
    if not session:
        return AutoPilotStartResponse(
            success=False, error=f"Session {session_id} not found"
        )

    try:
        # Update session with autopilot config
        if req.products:
            session["products"] = req.products
        if req.voice_id:
            session["voice_id"] = req.voice_id
        session["language"] = req.language
        session["autopilot"] = {
            "active": True,
            "state": "idle",
            "product_index": 0,
            "script_type": "introduction",
            "cycle_duration_sec": req.cycle_duration_sec,
            "started_at": time.time(),
            "total_speaks": 0,
            "previous_script": "",
            "persona": req.persona or {},
            "persona_id": req.persona_id,
        }

        return AutoPilotStartResponse(
            success=True,
            status="autopilot_started",
        )

    except Exception as e:
        logger.exception(f"Error starting autopilot: {e}")
        return AutoPilotStartResponse(
            success=False, error=f"Internal error: {str(e)}"
        )


# ──────────────────────────────────────────────
# 39. AutoPilot Next — Get next speech segment
# ──────────────────────────────────────────────
@router.post(
    "/live-session/{session_id}/autopilot/next",
    response_model=AutoPilotNextResponse,
    summary="Get the next speech segment from the auto-pilot brain",
    description=(
        "The frontend calls this after each audio finishes playing. "
        "The brain decides what to say next based on state, products, "
        "and pending comments. Returns generated script text + TTS audio URL."
    ),
)
async def autopilot_next(
    session_id: str,
    req: AutoPilotNextRequest,
    _auth: bool = Depends(verify_admin_key),
):
    session = get_session(session_id)
    if not session:
        return AutoPilotNextResponse(
            success=False, error=f"Session {session_id} not found"
        )

    products = session.get("products", [])
    autopilot = session.get("autopilot", {})

    try:
        # Retrieve autopilot context
        previous_script = autopilot.get("previous_script", "")
        persona = autopilot.get("persona", {})
        has_comments = bool(req.pending_comments and len(req.pending_comments) > 0)

        # No products — generate a generic greeting/filler
        if not products:
            filler_text = _generate_filler_script(req.language)
            el_service = get_elevenlabs_service()
            voice_id = req.voice_id or session.get("voice_id")
            mp3_audio = await el_service.text_to_speech(
                text=filler_text,
                voice_id=voice_id,
                output_format="mp3_44100_128",
                language_code=req.language,
            )
            duration_ms = len(mp3_audio) / 16.0
            audio_id = f"filler-{int(time.time())}"
            blob_url = await _upload_mp3_to_blob(mp3_audio, audio_id)
            audio_url = _ensure_sas_url(blob_url)
            autopilot["previous_script"] = filler_text

            return AutoPilotNextResponse(
                success=True,
                action="speak_script",
                audio_url=audio_url,
                audio_duration_ms=round(duration_ms, 1),
                text=filler_text,
                script_type="greeting",
                next_state="idle",
            )

        # Determine next script based on state (comments influence the cycle)
        product_index = req.current_product_index
        script_type = req.current_script_type
        next_script_type, next_product_index, next_state = _advance_script_state(
            script_type, product_index, len(products),
            has_comments=has_comments,
        )

        product = products[next_product_index]
        product_name = product.get("name", product.get("product_name", "商品"))
        product_desc = product.get("description", product.get("product_description", ""))
        product_price = product.get("price", product.get("product_price", ""))
        product_features = product.get("features", product.get("product_features", []))

        # Resolve fine-tuned model if persona_id is set
        finetune_model_id = None
        persona_id = autopilot.get("persona_id")
        if persona_id:
            try:
                from app.models.orm.persona import Persona
                from app.core.db import AsyncSessionLocal
                from sqlalchemy import select
                async with AsyncSessionLocal() as db_session:
                    result = await db_session.execute(
                        select(Persona).where(Persona.id == persona_id)
                    )
                    persona_obj = result.scalar_one_or_none()
                    if persona_obj and persona_obj.finetune_model_id:
                        finetune_model_id = persona_obj.finetune_model_id
                        logger.info(f"Using fine-tuned model: {finetune_model_id} for persona {persona_id}")
            except Exception as e:
                logger.warning(f"Failed to load persona model: {e}")

        # Generate script using Sales Brain (with context, comments, persona)
        from app.services.live_session_service import generate_product_script
        script_text = await generate_product_script(
            product_name=product_name,
            product_description=product_desc,
            product_price=product_price,
            product_features=product_features,
            tone="energetic",
            language=req.language,
            script_type=next_script_type,
            previous_script=previous_script,
            pending_comments=req.pending_comments if has_comments else None,
            persona=persona if persona else None,
            model_override=finetune_model_id,
        )
        if not script_text:
            script_text = f"この{product_name}は本当に素晴らしい商品です！"

        # Generate TTS (WAV for GPU Worker lip-sync)
        el_service = get_elevenlabs_service()
        voice_id = req.voice_id or session.get("voice_id")
        pcm_audio = await el_service.text_to_speech(
            text=script_text,
            voice_id=voice_id,
            output_format="pcm_16000",
            language_code=req.language,
        )
        wav_audio = _pcm_to_wav(pcm_audio, sample_rate=16000)
        tts_duration_ms = len(pcm_audio) / (16000 * 2) * 1000

        # Upload WAV audio to blob (needed for GPU Worker)
        audio_id = f"script-{int(time.time())}"
        wav_blob_url = await _upload_audio_to_blob(wav_audio, audio_id)
        wav_audio_url = _ensure_sas_url(wav_blob_url)

        # Also generate MP3 for fallback audio playback
        mp3_audio = await el_service.text_to_speech(
            text=script_text,
            voice_id=voice_id,
            output_format="mp3_44100_128",
            language_code=req.language,
        )
        mp3_blob_url = await _upload_mp3_to_blob(mp3_audio, audio_id)
        mp3_audio_url = _ensure_sas_url(mp3_blob_url)

        # Generate lip-synced video via GPU Worker
        portrait_url = session.get("portrait_url", "")
        lipsync_engine = session.get("engine", "musetalk")
        video_url = None
        video_job_id = None

        if portrait_url:
            logger.info(
                f"[AutoPilot] Generating lip-sync video: "
                f"engine={lipsync_engine}, portrait={portrait_url[:60]}..."
            )
            video_url = await _generate_lipsync_video(
                portrait_url=portrait_url,
                audio_url=wav_audio_url,
                engine=lipsync_engine,
                max_wait_sec=180,
            )
            if video_url:
                logger.info(f"[AutoPilot] Lip-sync video ready: {video_url[:80]}...")
            else:
                logger.warning("[AutoPilot] Lip-sync video generation failed, falling back to audio-only")

        # Update autopilot state
        autopilot["state"] = next_state
        autopilot["product_index"] = next_product_index
        autopilot["script_type"] = next_script_type
        autopilot["total_speaks"] = autopilot.get("total_speaks", 0) + 1
        autopilot["previous_script"] = script_text  # Keep context for next call

        action = "switch_product" if next_product_index != product_index else "speak_script"
        # If comments were woven in, mark as interaction
        if has_comments and next_script_type == "interaction":
            action = "speak_script"  # Not a separate reply_comment action

        return AutoPilotNextResponse(
            success=True,
            action=action,
            audio_url=mp3_audio_url,
            audio_duration_ms=round(tts_duration_ms, 1),
            text=script_text,
            script_type=next_script_type,
            product_name=product_name,
            product_index=next_product_index,
            next_state=next_state,
            video_url=video_url,
        )

    except ElevenLabsError as e:
        return AutoPilotNextResponse(
            success=False, error=f"TTS error: {str(e)}"
        )
    except Exception as e:
        logger.exception(f"Error in autopilot next: {e}")
        return AutoPilotNextResponse(
            success=False, error=f"Internal error: {str(e)}"
        )


def _advance_script_state(
    current_type: str, current_index: int, total_products: int,
    has_comments: bool = False,
) -> tuple:
    """
    State machine for script cycling (enhanced for continuous livestream flow):

    Base cycle per product:
      introduction → highlight → interaction/filler → promotion → closing → (next product)

    - After highlight, if there are pending comments, insert "interaction" state
      (comments woven into product talk). Otherwise insert "filler" (engagement talk).
    - This ensures the AI keeps talking continuously, not just responding to comments.

    Returns: (next_script_type, next_product_index, next_state)
    """
    # Extended cycle: introduction → highlight → interaction/filler → promotion → closing
    script_cycle = ["introduction", "highlight", "_interact_or_filler", "promotion", "closing"]
    
    # Map current_type to position
    type_to_pos = {
        "introduction": 0,
        "highlight": 1,
        "interaction": 2,
        "filler": 2,
        "_interact_or_filler": 2,
        "promotion": 3,
        "closing": 4,
    }
    current_pos = type_to_pos.get(current_type, -1)

    next_pos = current_pos + 1
    if next_pos >= len(script_cycle):
        # Completed cycle for this product → move to next product
        next_index = (current_index + 1) % total_products
        return "introduction", next_index, "product_intro"
    else:
        next_type = script_cycle[next_pos]
        if next_type == "_interact_or_filler":
            next_type = "interaction" if has_comments else "filler"
        return next_type, current_index, "product_intro"


def _generate_filler_script(language: str) -> str:
    """Generate a filler script when no products are available."""
    fillers = {
        "zh": "大家好！欢迎来到我们的直播间！今天给大家带来了很多好东西，大家可以在评论区留言，我会一一回复大家的问题。",
        "ja": "皆さん、こんにちは！ライブ配信にようこそ！今日は素敵な商品をたくさんご紹介します。コメントでご質問をどうぞ！",
        "en": "Hello everyone! Welcome to our livestream! We have some amazing products to show you today. Drop your questions in the comments!",
    }
    return fillers.get(language, fillers["zh"])


# ──────────────────────────────────────────────
# DEBUG: GPT Provider Test (temporary)
# ──────────────────────────────────────────────

@router.get("/debug/gpt-test", summary="Test GPT providers (debug)")
async def debug_gpt_test(_auth: bool = Depends(verify_admin_key)):
    """Temporary debug endpoint to test GPT provider availability."""
    import os
    results = {
        "azure_openai_key_set": bool(os.getenv("AZURE_OPENAI_KEY")),
        "azure_openai_endpoint_set": bool(os.getenv("AZURE_OPENAI_ENDPOINT")),
        "openai_api_key_set": bool(os.getenv("OPENAI_API_KEY")),
        "openai_base_url_set": bool(os.getenv("OPENAI_BASE_URL")),
        "gpt5_model": os.getenv("GPT5_MODEL", "not set"),
        "gpt5_deployment": os.getenv("GPT5_DEPLOYMENT", "not set"),
        "gpt5_api_version": os.getenv("GPT5_API_VERSION", "not set"),
    }

    # Test Azure OpenAI with Responses API (matching chat.py / live_ai.py)
    azure_key = os.getenv("AZURE_OPENAI_KEY", "")
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    azure_model = os.getenv("GPT5_MODEL") or os.getenv("GPT5_DEPLOYMENT") or "gpt-4.1-mini"
    if azure_key and azure_endpoint:
        try:
            import openai
            client = openai.AzureOpenAI(
                api_key=azure_key,
                azure_endpoint=azure_endpoint,
                api_version=os.getenv("GPT5_API_VERSION", "2025-04-01-preview"),
            )
            response = client.responses.create(
                model=azure_model,
                input=[{"role": "user", "content": "Say hello in Japanese in one sentence"}],
                max_output_tokens=50,
            )
            text = response.output_text if hasattr(response, 'output_text') else str(response)
            results["azure_responses_api_test"] = "SUCCESS"
            results["azure_responses_api_response"] = text.strip()[:100]
        except Exception as e:
            results["azure_responses_api_test"] = f"FAILED: {str(e)[:200]}"
    else:
        results["azure_responses_api_test"] = "SKIPPED (no credentials)"

    # Test _call_gpt (the actual function used by Sales Brain)
    try:
        from app.services.live_session_service import _call_gpt
        gpt_result = await _call_gpt(
            messages=[{"role": "user", "content": "Say hello in Japanese in one sentence"}],
            max_tokens=50,
        )
        results["_call_gpt_test"] = "SUCCESS"
        results["_call_gpt_response"] = gpt_result[:100]
    except Exception as e:
        results["_call_gpt_test"] = f"FAILED: {str(e)[:300]}"

    return results
