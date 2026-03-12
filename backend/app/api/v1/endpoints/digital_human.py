"""
Digital Human (數智人) Livestream API Endpoints

These endpoints provide the AitherHub ↔ Tencent Cloud IVH integration:

  POST /api/v1/digital-human/liveroom/create     – Create livestream room (auto-generate scripts from analysis)
  GET  /api/v1/digital-human/liveroom/{id}        – Query livestream room status
  GET  /api/v1/digital-human/liverooms            – List all active livestream rooms
  POST /api/v1/digital-human/liveroom/{id}/takeover – Send real-time interjection
  POST /api/v1/digital-human/liveroom/{id}/close  – Close livestream room
  POST /api/v1/digital-human/script/generate      – Generate script from analysis (preview, no liveroom)

Architecture:
  This is a PoC module. Authentication uses the existing AitherHub admin key
  (X-Admin-Key header) for simplicity. In production, this should be integrated
  with the full user auth system.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.schemas.digital_human_schema import (
    CreateLiveroomRequest,
    CreateLiveroomResponse,
    GetLiveroomResponse,
    ListLiveroomsResponse,
    TakeoverRequest,
    TakeoverResponse,
    CloseLiveroomRequest,
    CloseLiveroomResponse,
    GenerateScriptRequest,
    GenerateScriptResponse,
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
# Service singleton
# ──────────────────────────────────────────────

_tencent_service: Optional[TencentDigitalHumanService] = None


def get_tencent_service() -> TencentDigitalHumanService:
    global _tencent_service
    if _tencent_service is None:
        _tencent_service = TencentDigitalHumanService()
    return _tencent_service


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
        "Otherwise, provide scripts manually."
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
            scripts = []
            for sd in script_dicts:
                bgs = []
                if req.backgrounds:
                    bgs = [
                        VideoLayer(url=bg.url, x=bg.x, y=bg.y, width=bg.width, height=bg.height)
                        for bg in req.backgrounds
                    ]
                scripts.append(ScriptReq(
                    content=sd["Content"],
                    backgrounds=bgs,
                ))
        elif req.scripts:
            scripts = []
            for text in req.scripts:
                bgs = []
                if req.backgrounds:
                    bgs = [
                        VideoLayer(url=bg.url, x=bg.x, y=bg.y, width=bg.width, height=bg.height)
                        for bg in req.backgrounds
                    ]
                scripts.append(ScriptReq(content=text, backgrounds=bgs))
        else:
            raise HTTPException(
                status_code=400,
                detail="Either video_id or scripts must be provided",
            )

        # Build optional params
        speech_param = None
        if req.speech_param:
            speech_param = SpeechParam(
                speed=req.speech_param.speed,
                timbre_key=req.speech_param.timbre_key,
                volume=req.speech_param.volume,
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
    _auth: bool = Depends(verify_admin_key),
):
    service = get_tencent_service()

    try:
        result = await service.list_liverooms()
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
        "If content is not provided, it will be auto-generated from event_context."
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

        result = await service.takeover(liveroom_id, content)
        return TakeoverResponse(success=True, content_sent=content)

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
