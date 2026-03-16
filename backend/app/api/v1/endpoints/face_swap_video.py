"""
Face Swap Video API Endpoints

Provides REST API for the video face swap + voice conversion pipeline:
  - Upload video for processing
  - Start face swap + voice conversion pipeline
  - Poll job status and progress
  - Download completed video
  - List and manage jobs

All endpoints require admin authentication via X-Admin-Key header.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.services.video_face_swap_service import (
    VideoFaceSwapService,
    VideoJobStatus,
    video_pipeline_jobs,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/face-swap",
    tags=["Face Swap Video"],
)

# Admin key for authentication
ADMIN_KEY = os.getenv("ADMIN_API_KEY", "aither:hub")


# ──────────────────────────────────────────────
# Auth
# ──────────────────────────────────────────────

async def verify_admin(x_admin_key: str = Header(...)):
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Invalid admin key")
    return True


# ──────────────────────────────────────────────
# Request/Response Models
# ──────────────────────────────────────────────

class StartVideoJobRequest(BaseModel):
    """Request to start a video face swap pipeline job."""
    video_url: str = Field(..., description="URL of the input video (Azure Blob SAS URL)")
    voice_id: Optional[str] = Field(None, description="ElevenLabs voice ID for voice conversion")
    quality: str = Field(default="high", description="Face swap quality: fast, balanced, high")
    face_enhancer: bool = Field(default=True, description="Enable GFPGAN face enhancement")
    enable_voice_conversion: bool = Field(default=True, description="Enable ElevenLabs voice conversion")
    remove_background_noise: bool = Field(default=False, description="Remove background noise from audio")


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    step: str
    progress: int
    error: Optional[str] = None
    elapsed_sec: float = 0
    duration_sec: float = 0
    result_video_url: Optional[str] = None
    enable_voice_conversion: bool = True


# ──────────────────────────────────────────────
# Service Instance
# ──────────────────────────────────────────────

_service: Optional[VideoFaceSwapService] = None


def get_service() -> VideoFaceSwapService:
    global _service
    if _service is None:
        _service = VideoFaceSwapService()
    return _service


# ──────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────

@router.post("/start-job")
async def start_video_job(
    req: StartVideoJobRequest,
    auth: bool = Header(None, alias="X-Admin-Key"),
):
    """
    Start a new video face swap + voice conversion pipeline job.

    The pipeline:
      1. Downloads the video from the provided URL
      2. Extracts audio track
      3. Sends video to GPU Worker for face swap (FaceFusion)
      4. Sends audio to ElevenLabs for voice conversion (STS)
      5. Merges face-swapped video + converted audio
      6. Makes the result available for download

    Returns immediately with a job_id. Poll /face-swap/status/{job_id}
    for progress updates.
    """
    if auth != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Invalid admin key")

    service = get_service()

    try:
        job_id = await service.create_job(
            video_url=req.video_url,
            voice_id=req.voice_id,
            quality=req.quality,
            face_enhancer=req.face_enhancer,
            enable_voice_conversion=req.enable_voice_conversion,
            remove_background_noise=req.remove_background_noise,
        )

        return {
            "status": "accepted",
            "job_id": job_id,
            "message": "Video pipeline job started",
            "poll_url": f"/api/v1/face-swap/status/{job_id}",
        }

    except Exception as e:
        logger.error(f"Failed to start video job: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{job_id}")
async def get_job_status(
    job_id: str,
    auth: str = Header(None, alias="X-Admin-Key"),
):
    """
    Get the status and progress of a video pipeline job.

    Progress ranges from 0-100:
      - 0-10: Downloading input video
      - 10-15: Extracting audio
      - 15-70: Face swapping (GPU processing)
      - 70-85: Voice conversion (ElevenLabs)
      - 85-95: Merging video + audio
      - 95-100: Uploading result
    """
    if auth != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Invalid admin key")

    service = get_service()

    try:
        status = await service.get_job_status(job_id)

        # Add download URL if completed
        if status["status"] == VideoJobStatus.COMPLETED:
            status["download_url"] = f"/api/v1/face-swap/download/{job_id}"

        return status

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to get job status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/download/{job_id}")
async def download_result(
    job_id: str,
    auth: str = Header(None, alias="X-Admin-Key"),
):
    """
    Download the completed face-swapped video.

    Returns the video file as a streaming response (MP4).
    """
    if auth != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Invalid admin key")

    service = get_service()
    video_path = await service.get_result_video_path(job_id)

    if not video_path:
        raise HTTPException(
            status_code=404,
            detail=f"Job {job_id} not found or not completed",
        )

    if not os.path.exists(video_path):
        raise HTTPException(
            status_code=404,
            detail="Result video file not found on disk",
        )

    return FileResponse(
        path=video_path,
        media_type="video/mp4",
        filename=f"face_swap_{job_id}.mp4",
    )


@router.get("/jobs")
async def list_jobs(
    limit: int = 20,
    auth: str = Header(None, alias="X-Admin-Key"),
):
    """List recent video pipeline jobs."""
    if auth != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Invalid admin key")

    service = get_service()
    jobs = await service.list_jobs(limit=limit)
    return {"jobs": jobs, "total": len(video_pipeline_jobs)}


@router.delete("/job/{job_id}")
async def delete_job(
    job_id: str,
    auth: str = Header(None, alias="X-Admin-Key"),
):
    """Delete a video pipeline job and cleanup files."""
    if auth != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Invalid admin key")

    service = get_service()

    try:
        result = await service.delete_job(job_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to delete job: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/voices")
async def list_voices(
    auth: str = Header(None, alias="X-Admin-Key"),
):
    """
    List available ElevenLabs voices for voice conversion.
    Returns all voices including cloned voices.
    """
    if auth != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Invalid admin key")

    service = get_service()

    try:
        voices = await service.tts.list_voices()
        return {
            "voices": [
                {
                    "voice_id": v.get("voice_id"),
                    "name": v.get("name"),
                    "category": v.get("category"),
                    "labels": v.get("labels", {}),
                    "preview_url": v.get("preview_url"),
                }
                for v in voices
            ],
            "total": len(voices),
        }
    except Exception as e:
        logger.error(f"Failed to list voices: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def health_check(
    auth: str = Header(None, alias="X-Admin-Key"),
):
    """
    Health check for the video face swap pipeline.
    Checks GPU Worker and ElevenLabs connectivity.
    """
    if auth != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Invalid admin key")

    service = get_service()

    gpu_health = await service.face_swap.health_check()
    tts_health = await service.tts.health_check()

    return {
        "gpu_worker": gpu_health,
        "elevenlabs": tts_health,
        "active_jobs": sum(
            1 for j in video_pipeline_jobs.values()
            if j["status"] not in (VideoJobStatus.COMPLETED, VideoJobStatus.ERROR)
        ),
        "total_jobs": len(video_pipeline_jobs),
    }
