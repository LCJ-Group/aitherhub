"""
Auto Video Pipeline API Endpoints

Provides REST API for the fully automated video generation pipeline:
  - Create auto video jobs (topic + body double video → final video)
  - Poll job status and progress
  - Download completed videos
  - List and manage jobs

All endpoints are prefixed with /auto-video.
"""
from __future__ import annotations

import os
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.services.auto_video_pipeline_service import (
    AutoVideoPipelineService,
    AutoVideoStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auto-video", tags=["Auto Video Pipeline"])

# Singleton service instance
_service: Optional[AutoVideoPipelineService] = None


def get_service() -> AutoVideoPipelineService:
    global _service
    if _service is None:
        _service = AutoVideoPipelineService()
    return _service


# ──────────────────────────────────────────────
# Request / Response Schemas
# ──────────────────────────────────────────────

class CreateAutoVideoRequest(BaseModel):
    """Request to create a new auto video generation job."""
    video_url: str = Field(
        ...,
        description="URL of the body double video to process",
    )
    topic: str = Field(
        ...,
        description="Topic or product name for script generation",
    )
    voice_id: Optional[str] = Field(
        None,
        description="ElevenLabs voice ID (uses default cloned voice if not set)",
    )
    language: str = Field(
        "ja",
        description="Script language: ja, en, zh",
    )
    tone: str = Field(
        "professional_friendly",
        description="Script tone: professional_friendly, energetic, calm",
    )
    script_text: Optional[str] = Field(
        None,
        description="Pre-written script (skips AI generation if provided)",
    )
    quality: str = Field(
        "high",
        description="Face swap quality preset: fast, balanced, high, ultra",
    )
    enable_lip_sync: bool = Field(
        True,
        description="Apply ElevenLabs lip sync after merging",
    )
    product_info: Optional[str] = Field(
        None,
        description="Additional product information for script generation",
    )
    target_duration_sec: Optional[int] = Field(
        None,
        description="Target video duration in seconds (auto-detected from video if not set)",
    )


class CreateAutoVideoResponse(BaseModel):
    """Response after creating an auto video job."""
    job_id: str
    status: str
    message: str


class AutoVideoStatusResponse(BaseModel):
    """Response for job status queries."""
    job_id: str
    status: str
    step: str
    progress: int
    error: Optional[str] = None
    elapsed_sec: float
    topic: str
    generated_script: Optional[str] = None
    tts_audio_duration_sec: Optional[float] = None
    enable_lip_sync: bool
    result_video_url: Optional[str] = None


class AutoVideoListItem(BaseModel):
    """Summary item for job listing."""
    job_id: str
    status: str
    progress: int
    topic: str
    created_at: float
    completed_at: Optional[float] = None


# ──────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────

@router.post(
    "/create",
    response_model=CreateAutoVideoResponse,
    summary="Create auto video generation job",
    description=(
        "Start a fully automated video generation pipeline: "
        "script generation (GPT) → voice generation (ElevenLabs TTS) → "
        "face swap (FaceFusion GPU) → lip sync (ElevenLabs Dubbing) → "
        "final video output."
    ),
)
async def create_auto_video(req: CreateAutoVideoRequest):
    """Create a new auto video generation job."""
    service = get_service()

    if not service.face_swap.is_configured:
        raise HTTPException(
            status_code=503,
            detail="Face swap GPU worker not configured. Set FACE_SWAP_WORKER_URL.",
        )

    try:
        job_id = await service.create_job(
            video_url=req.video_url,
            topic=req.topic,
            voice_id=req.voice_id,
            language=req.language,
            tone=req.tone,
            script_text=req.script_text,
            quality=req.quality,
            enable_lip_sync=req.enable_lip_sync,
            product_info=req.product_info,
            target_duration_sec=req.target_duration_sec,
        )

        return CreateAutoVideoResponse(
            job_id=job_id,
            status="pending",
            message=f"Auto video pipeline started. Poll /auto-video/status/{job_id} for progress.",
        )

    except Exception as e:
        logger.error(f"Failed to create auto video job: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/status/{job_id}",
    response_model=AutoVideoStatusResponse,
    summary="Get auto video job status",
    description="Poll the current status and progress of an auto video generation job.",
)
async def get_auto_video_status(job_id: str):
    """Get the current status of an auto video job."""
    service = get_service()

    try:
        status = await service.get_job_status(job_id)
        return AutoVideoStatusResponse(**status)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")


@router.get(
    "/download/{job_id}",
    summary="Download completed auto video",
    description="Download the final generated video file.",
)
async def download_auto_video(job_id: str):
    """Download the completed auto video."""
    service = get_service()

    path = await service.get_result_video_path(job_id)
    if not path or not os.path.exists(path):
        raise HTTPException(
            status_code=404,
            detail=f"Video not ready or job {job_id} not found",
        )

    return FileResponse(
        path=path,
        media_type="video/mp4",
        filename=f"auto_video_{job_id}.mp4",
    )


@router.get(
    "/script/{job_id}",
    summary="Get generated script",
    description="Get the AI-generated script for an auto video job.",
)
async def get_auto_video_script(job_id: str):
    """Get the generated script for a job."""
    service = get_service()

    try:
        status = await service.get_job_status(job_id)
        script = status.get("generated_script")
        if not script:
            raise HTTPException(
                status_code=404,
                detail="Script not yet generated for this job",
            )
        return {
            "job_id": job_id,
            "script": script,
            "topic": status["topic"],
        }
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")


@router.get(
    "/list",
    response_model=list[AutoVideoListItem],
    summary="List auto video jobs",
    description="List recent auto video generation jobs.",
)
async def list_auto_video_jobs(
    limit: int = Query(20, ge=1, le=100, description="Max number of jobs to return"),
):
    """List recent auto video jobs."""
    service = get_service()
    jobs = await service.list_jobs(limit=limit)
    return [AutoVideoListItem(**j) for j in jobs]


@router.delete(
    "/delete/{job_id}",
    summary="Delete auto video job",
    description="Delete a job and cleanup all temporary files.",
)
async def delete_auto_video(job_id: str):
    """Delete a job and cleanup."""
    service = get_service()

    try:
        result = await service.delete_job(job_id)
        return result
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")


@router.get(
    "/health",
    summary="Auto video pipeline health check",
    description="Check the health of all pipeline components.",
)
async def auto_video_health():
    """Check health of all pipeline components."""
    service = get_service()

    result = {
        "pipeline": "ok",
        "face_swap_worker": "not_configured",
        "elevenlabs_tts": "not_configured",
        "script_generator": "ok",  # GPT is always available
    }

    # Check face swap worker
    if service.face_swap.is_configured:
        try:
            health = await service.face_swap.health_check()
            result["face_swap_worker"] = health.get("status", "unknown")
            result["face_swap_gpu"] = health.get("gpu_name", "unknown")
        except Exception as e:
            result["face_swap_worker"] = f"error: {str(e)[:100]}"

    # Check ElevenLabs
    try:
        el_health = await service.tts.health_check()
        result["elevenlabs_tts"] = el_health.get("status", "unknown")
        result["elevenlabs_voices"] = el_health.get("total_voices", 0)
    except Exception as e:
        result["elevenlabs_tts"] = f"error: {str(e)[:100]}"

    return result
