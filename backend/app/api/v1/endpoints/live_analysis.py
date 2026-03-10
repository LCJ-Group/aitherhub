"""
Live Analysis endpoints for the LiveBoost Companion App.

These endpoints handle the lifecycle of live-stream analysis jobs:
  - Start analysis after chunk upload completion
  - Poll analysis status
  - Generate per-chunk signed upload URLs

╔══════════════════════════════════════════════════════════════════╗
║  Routes:                                                        ║
║    POST /api/v1/live-analysis/start                              ║
║    GET  /api/v1/live-analysis/status/{video_id}                  ║
║    POST /api/v1/live-analysis/generate-chunk-upload-url          ║
╚══════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import uuid as uuid_module
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from loguru import logger

from app.core.db import get_db
from app.core.dependencies import get_current_user
from app.models.orm.live_analysis_job import LiveAnalysisJob
from app.schemas.live_analysis_schema import (
    LiveAnalysisStartRequest,
    LiveAnalysisStartResponse,
    LiveAnalysisStatusResponse,
    AnalysisResults,
    GenerateChunkUploadURLRequest,
    GenerateChunkUploadURLResponse,
)
from app.services.storage_service import generate_upload_sas
from app.services.queue_service import enqueue_job


router = APIRouter(
    prefix="/live-analysis",
    tags=["live-analysis"],
)


# ──────────────────────────────────────────────
# 0. Migrate (one-time table creation)
# ──────────────────────────────────────────────
@router.post("/migrate")
async def migrate_tables():
    """
    One-time endpoint to create missing tables.
    Safe to call multiple times (CREATE IF NOT EXISTS).
    """
    try:
        from app.core.db import engine
        from app.models.orm.base import Base
        import app.models.orm  # noqa: F401

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return {"status": "ok", "message": "Tables verified/created successfully"}
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        raise HTTPException(status_code=500, detail=f"Migration failed: {str(e)}")


# ──────────────────────────────────────────────
# 1. Start Analysis
# ──────────────────────────────────────────────
@router.post("/start", response_model=LiveAnalysisStartResponse)
async def start_live_analysis(
    payload: LiveAnalysisStartRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Trigger the analysis pipeline after all chunks have been uploaded.

    This endpoint:
      1. Creates a LiveAnalysisJob record (status=pending)
      2. Enqueues a worker job for the analysis pipeline
      3. Returns the job ID for status polling

    Called by the LiveBoost iOS app after ChunkUploadService
    confirms all chunks are uploaded.
    """
    try:
        user_id = current_user["id"]
        video_id = payload.video_id

        # Check for duplicate: prevent re-triggering for same video_id
        existing = await db.execute(
            select(LiveAnalysisJob).where(
                LiveAnalysisJob.video_id == video_id,
                LiveAnalysisJob.user_id == user_id,
            )
        )
        existing_job = existing.scalar_one_or_none()
        if existing_job:
            # If already exists and not failed, return existing job
            if existing_job.status not in ("failed",):
                return LiveAnalysisStartResponse(
                    job_id=str(existing_job.id),
                    video_id=video_id,
                    status=existing_job.status,
                    message="Analysis job already exists",
                )
            else:
                # Reset failed job for retry
                existing_job.status = "pending"
                existing_job.current_step = None
                existing_job.progress = 0
                existing_job.error_message = None
                existing_job.started_at = None
                existing_job.completed_at = None
                existing_job.results = None
                await db.commit()
                await db.refresh(existing_job)
                job = existing_job
        else:
            # Create new job
            job = LiveAnalysisJob(
                id=uuid_module.uuid4(),
                video_id=video_id,
                user_id=user_id,
                stream_source=payload.stream_source,
                status="pending",
                total_chunks=payload.total_chunks,
                progress=0,
            )
            db.add(job)
            await db.commit()
            await db.refresh(job)

        # Enqueue worker job
        queue_payload = {
            "job_type": "live_analysis",
            "job_id": str(job.id),
            "video_id": video_id,
            "user_id": user_id,
            "stream_source": payload.stream_source,
            "total_chunks": payload.total_chunks,
            "email": current_user.get("email", ""),
        }

        enqueue_result = await enqueue_job(queue_payload)

        # Persist enqueue evidence
        try:
            if enqueue_result.success:
                await db.execute(
                    update(LiveAnalysisJob)
                    .where(LiveAnalysisJob.id == job.id)
                    .values(
                        queue_message_id=enqueue_result.message_id,
                        queue_enqueued_at=enqueue_result.enqueued_at,
                        started_at=datetime.now(timezone.utc),
                    )
                )
                logger.info(
                    f"[live-analysis] Enqueued OK job={job.id} video={video_id} "
                    f"msg_id={enqueue_result.message_id}"
                )
            else:
                await db.execute(
                    update(LiveAnalysisJob)
                    .where(LiveAnalysisJob.id == job.id)
                    .values(
                        status="failed",
                        error_message=f"Failed to enqueue: {enqueue_result.error}",
                    )
                )
                logger.error(
                    f"[live-analysis] Enqueue FAILED job={job.id} error={enqueue_result.error}"
                )
            await db.commit()
        except Exception as db_err:
            logger.error(f"[live-analysis] Failed to save enqueue evidence: {db_err}")
            try:
                await db.rollback()
            except Exception:
                pass

        return LiveAnalysisStartResponse(
            job_id=str(job.id),
            video_id=video_id,
            status="pending" if enqueue_result.success else "failed",
            message=(
                "Analysis pipeline started"
                if enqueue_result.success
                else f"Failed to start analysis: {enqueue_result.error}"
            ),
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"[live-analysis/start] Unexpected error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start analysis: {exc}",
        )


# ──────────────────────────────────────────────
# 2. Get Analysis Status
# ──────────────────────────────────────────────
@router.get("/status/{video_id}", response_model=LiveAnalysisStatusResponse)
async def get_analysis_status(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Poll the current status of a live analysis job.

    Returns the job status, current processing step, progress percentage,
    and results (when completed).
    """
    try:
        user_id = current_user["id"]

        result = await db.execute(
            select(LiveAnalysisJob).where(
                LiveAnalysisJob.video_id == video_id,
                LiveAnalysisJob.user_id == user_id,
            ).order_by(LiveAnalysisJob.created_at.desc())
        )
        job = result.scalar_one_or_none()

        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No analysis job found for video_id={video_id}",
            )

        # Parse results if available
        analysis_results = None
        if job.results:
            try:
                analysis_results = AnalysisResults(**job.results)
            except Exception:
                analysis_results = AnalysisResults(
                    top_sales_moments=[],
                    hook_candidates=[],
                    clip_candidates=[],
                )

        return LiveAnalysisStatusResponse(
            job_id=str(job.id),
            video_id=job.video_id,
            status=job.status,
            current_step=job.current_step,
            progress=job.progress,
            started_at=job.started_at,
            completed_at=job.completed_at,
            results=analysis_results,
            error_message=job.error_message,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"[live-analysis/status] Unexpected error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get analysis status: {exc}",
        )


# ──────────────────────────────────────────────
# 3. Generate Chunk Upload URL
# ──────────────────────────────────────────────
@router.post(
    "/generate-chunk-upload-url",
    response_model=GenerateChunkUploadURLResponse,
)
async def generate_chunk_upload_url(
    payload: GenerateChunkUploadURLRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Generate a signed upload URL for a single video chunk.

    The LiveBoost iOS app calls this for each 10MB chunk during
    recording. Chunks are stored under:
      {email}/{video_id}/chunks/chunk_{XXXX}.mp4

    After all chunks are uploaded, the iOS app calls /start to
    trigger the assembly + analysis pipeline.
    """
    try:
        email = current_user.get("email", "")
        video_id = payload.video_id
        chunk_index = payload.chunk_index

        # Generate chunk filename
        chunk_filename = f"chunks/chunk_{chunk_index:04d}.mp4"

        # Use existing storage service to generate SAS URL
        vid, upload_url, blob_url, expiry = await generate_upload_sas(
            email=email,
            video_id=video_id,
            filename=chunk_filename,
        )

        return GenerateChunkUploadURLResponse(
            video_id=video_id,
            chunk_index=chunk_index,
            upload_url=upload_url,
            blob_url=blob_url,
            expires_at=expiry,
        )

    except Exception as exc:
        logger.exception(f"[live-analysis/generate-chunk-upload-url] Error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate chunk upload URL: {exc}",
        )
