"""
Live Analysis endpoints for the LiveBoost Companion App.

These endpoints handle the lifecycle of live-stream analysis jobs:
  - Start analysis after chunk upload completion
  - Poll analysis status
  - Generate per-chunk signed upload URLs

BUILD 30: Self-healing status API
  - Status API auto-creates missing jobs when video record exists
  - Eliminates "waiting forever" — if job is missing, create + enqueue it
  - Retry endpoint improved with total_chunks from video metadata

╔══════════════════════════════════════════════════════════════════╗
║  Routes:                                                        ║
║    POST /api/v1/live-analysis/start                              ║
║    GET  /api/v1/live-analysis/status/{video_id}                  ║
║    POST /api/v1/live-analysis/generate-chunk-upload-url          ║
║    POST /api/v1/live-analysis/retry/{video_id}                   ║
╚══════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import uuid as uuid_module
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, text
from loguru import logger

from app.core.db import get_db
from app.core.dependencies import get_current_user
from app.models.orm.live_analysis_job import LiveAnalysisJob
from app.models.orm.video import Video
from app.schemas.live_analysis_schema import (
    LiveAnalysisStartRequest,
    LiveAnalysisStartResponse,
    LiveAnalysisStatusResponse,
    AnalysisResults,
    GenerateChunkUploadURLRequest,
    GenerateChunkUploadURLResponse,
)
from app.services.storage_service import generate_upload_sas, check_blob_exists
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
    One-time endpoint to create the live_analysis_jobs table.
    Safe to call multiple times (CREATE IF NOT EXISTS).
    """
    try:
        from app.core.db import engine

        async with engine.begin() as conn:
            await conn.run_sync(
                LiveAnalysisJob.__table__.create,
                checkfirst=True,
            )
        return {"status": "ok", "message": "live_analysis_jobs table verified/created successfully"}
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        raise HTTPException(status_code=500, detail=f"Migration failed: {str(e)}")


# ──────────────────────────────────────────────
# Helper: Ensure videos table record exists
# ──────────────────────────────────────────────
async def _ensure_video_record(
    db: AsyncSession,
    video_id: str,
    user_id: int,
    status_value: str = "uploaded",
) -> None:
    """
    Ensure a corresponding record exists in the `videos` table
    so that LiveBoost sessions appear in AitherHub's History view.
    """
    try:
        result = await db.execute(
            select(Video).where(Video.id == uuid_module.UUID(video_id))
        )
        existing = result.scalar_one_or_none()

        if existing:
            if existing.status in ("ERROR", "failed") and status_value in ("uploaded", "pending"):
                existing.status = "uploaded"
                existing.step_progress = 0
                await db.flush()
                logger.info(
                    f"[live-analysis] Reset videos record for retry: "
                    f"video={video_id} status=uploaded"
                )
        else:
            video = Video(
                id=uuid_module.UUID(video_id),
                user_id=user_id,
                original_filename=f"LiveBoost_{datetime.now(timezone.utc).strftime('%m%d_%H%M')}",
                status=status_value,
                upload_type="live_boost",
                step_progress=0,
            )
            db.add(video)
            await db.flush()
            logger.info(
                f"[live-analysis] Created videos record: "
                f"video={video_id} user={user_id} upload_type=live_boost"
            )
    except Exception as e:
        logger.warning(f"[live-analysis] Failed to ensure video record: {e}")


# ──────────────────────────────────────────────
# Helper: Auto-create and enqueue a missing job
# ──────────────────────────────────────────────
async def _auto_create_and_enqueue_job(
    db: AsyncSession,
    video_id: str,
    user_id: int,
    email: str = "",
    total_chunks: int | None = None,
    stream_source: str = "tiktok_live",
) -> LiveAnalysisJob | None:
    """
    BUILD 30: Self-healing — create a LiveAnalysisJob and enqueue it.
    Called when status API detects a video record exists but no job.
    Returns the created job, or None on failure.
    """
    try:
        job = LiveAnalysisJob(
            id=uuid_module.uuid4(),
            video_id=video_id,
            user_id=user_id,
            stream_source=stream_source,
            status="pending",
            total_chunks=total_chunks,
            progress=0,
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        logger.info(f"[live-analysis/auto-heal] Created job: {job.id} video={video_id}")

        # Enqueue
        queue_payload = {
            "job_type": "live_analysis",
            "job_id": str(job.id),
            "video_id": video_id,
            "user_id": user_id,
            "stream_source": stream_source,
            "total_chunks": total_chunks,
            "email": email,
        }
        enqueue_result = await enqueue_job(queue_payload)

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
            await db.commit()
            logger.info(
                f"[live-analysis/auto-heal] Enqueued OK job={job.id} "
                f"msg_id={enqueue_result.message_id}"
            )
        else:
            job.status = "failed"
            job.error_message = f"Auto-heal enqueue failed: {enqueue_result.error}"
            await db.commit()
            logger.error(
                f"[live-analysis/auto-heal] Enqueue FAILED job={job.id} "
                f"error={enqueue_result.error}"
            )

        return job

    except Exception as e:
        logger.error(f"[live-analysis/auto-heal] Failed to create job: {e}")
        try:
            await db.rollback()
        except Exception:
            pass
        return None


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
    """
    try:
        user_id = current_user["id"]
        video_id = payload.video_id

        logger.info(
            f"[live-analysis/start] Received: video={video_id} user={user_id} "
            f"chunks={payload.total_chunks} source={payload.stream_source}"
        )

        # BUILD 33: Verify at least chunk_0000 exists in blob storage
        # This prevents creating jobs when iOS failed to upload chunks
        email = current_user.get("email", "")
        if payload.total_chunks and payload.total_chunks > 0 and email:
            chunk_exists = await check_blob_exists(
                email=email,
                video_id=video_id,
                filename="chunks/chunk_0000.mp4",
            )
            if not chunk_exists:
                logger.error(
                    f"[live-analysis/start] BUILD 33: chunk_0000.mp4 NOT FOUND in blob storage "
                    f"for video={video_id} email={email}. Rejecting start request."
                )
                return LiveAnalysisStartResponse(
                    job_id="",
                    video_id=video_id,
                    status="failed",
                    message="No chunks found in storage. Please re-record and upload.",
                )
            logger.info(f"[live-analysis/start] BUILD 33: chunk_0000.mp4 verified in blob storage")

        # Check for duplicate
        existing = await db.execute(
            select(LiveAnalysisJob).where(
                LiveAnalysisJob.video_id == video_id,
                LiveAnalysisJob.user_id == user_id,
            )
        )
        existing_job = existing.scalar_one_or_none()

        if existing_job:
            if existing_job.status not in ("failed",):
                logger.info(
                    f"[live-analysis/start] Job already exists: {existing_job.id} "
                    f"status={existing_job.status}"
                )
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
                # Update total_chunks if provided
                if payload.total_chunks:
                    existing_job.total_chunks = payload.total_chunks
                job = existing_job

                await _ensure_video_record(db, video_id, user_id, "uploaded")
                await db.commit()
                await db.refresh(job)
                logger.info(f"[live-analysis/start] Reset failed job: {job.id}")
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
            await _ensure_video_record(db, video_id, user_id, "uploaded")
            await db.commit()
            await db.refresh(job)
            logger.info(f"[live-analysis/start] Created new job: {job.id}")

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
                    f"[live-analysis/start] Enqueued OK job={job.id} video={video_id} "
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
                try:
                    await db.execute(
                        text("""
                            UPDATE videos
                            SET status = 'ERROR', updated_at = now()
                            WHERE id = :video_id
                        """),
                        {"video_id": video_id},
                    )
                except Exception:
                    pass
                logger.error(
                    f"[live-analysis/start] Enqueue FAILED job={job.id} "
                    f"error={enqueue_result.error}"
                )
            await db.commit()
        except Exception as db_err:
            logger.error(f"[live-analysis/start] Failed to save enqueue evidence: {db_err}")
            try:
                await db.rollback()
            except Exception as _e:
                logger.debug(f"Non-critical error suppressed: {_e}")

        # Return actual status so iOS can detect failures
        final_status = "pending" if enqueue_result.success else "failed"
        return LiveAnalysisStartResponse(
            job_id=str(job.id),
            video_id=video_id,
            status=final_status,
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
# 2. Get Analysis Status (SELF-HEALING)
# ──────────────────────────────────────────────
@router.get("/status/{video_id}", response_model=LiveAnalysisStatusResponse)
async def get_analysis_status(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Poll the current status of a live analysis job.

    BUILD 30: SELF-HEALING — if no job exists but a video record does,
    automatically create the job and enqueue it. This eliminates the
    "waiting forever" state that occurs when /start fails during deploy.
    """
    try:
        user_id = current_user["id"]
        email = current_user.get("email", "")

        result = await db.execute(
            select(LiveAnalysisJob).where(
                LiveAnalysisJob.video_id == video_id,
                LiveAnalysisJob.user_id == user_id,
            ).order_by(LiveAnalysisJob.created_at.desc())
        )
        job = result.scalar_one_or_none()

        if not job:
            # Check if a video record exists
            video_result = await db.execute(
                select(Video).where(
                    Video.id == uuid_module.UUID(video_id),
                    Video.user_id == user_id,
                )
            )
            video = video_result.scalar_one_or_none()

            if video and video.upload_type == "live_boost":
                # BUILD 30: SELF-HEALING — auto-create the missing job
                logger.warning(
                    f"[live-analysis/status] SELF-HEAL: No job but video exists. "
                    f"Auto-creating job for video={video_id}"
                )
                job = await _auto_create_and_enqueue_job(
                    db=db,
                    video_id=video_id,
                    user_id=user_id,
                    email=email,
                    stream_source="tiktok_live",
                )

                if job:
                    return LiveAnalysisStatusResponse(
                        job_id=str(job.id),
                        video_id=video_id,
                        status=job.status,
                        current_step="解析ジョブを自動作成しました",
                        progress=0.0,
                        started_at=job.started_at,
                        completed_at=None,
                        results=None,
                        error_message=job.error_message,
                    )
                else:
                    # Auto-create failed — return waiting so iOS can retry
                    return LiveAnalysisStatusResponse(
                        job_id="",
                        video_id=video_id,
                        status="waiting",
                        current_step="解析ジョブの作成に失敗しました。リトライ中...",
                        progress=0.0,
                        started_at=None,
                        completed_at=None,
                        results=None,
                        error_message=None,
                    )

            # Neither job nor video exists
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

        # BUILD 31: Timeout detection — if job is in a processing state
        # and hasn't been updated for >2 minutes, mark as timed out.
        TIMEOUT_SECONDS = 120  # 2 minutes
        timeout_detected = False
        stale_seconds = None
        terminal_statuses = ("completed", "failed", "dead")

        if job.status not in terminal_statuses:
            # Use updated_at from the job record
            last_update = job.updated_at
            if last_update:
                now_utc = datetime.now(timezone.utc)
                # Ensure last_update is timezone-aware
                if last_update.tzinfo is None:
                    from datetime import timezone as _tz
                    last_update = last_update.replace(tzinfo=_tz.utc)
                delta = (now_utc - last_update).total_seconds()
                stale_seconds = int(delta)
                if delta > TIMEOUT_SECONDS:
                    timeout_detected = True
                    logger.warning(
                        f"[live-analysis/status] TIMEOUT: job={job.id} "
                        f"status={job.status} stale={stale_seconds}s"
                    )
                    # Auto-mark as failed if stale for >5 minutes
                    if delta > 300 and job.status not in terminal_statuses:
                        job.status = "failed"
                        job.error_message = (
                            f"タイムアウト: {stale_seconds}秒間進行なし。"
                            f"最終ステップ: {job.current_step or job.status}"
                        )
                        await db.commit()
                        logger.warning(
                            f"[live-analysis/status] Auto-failed job={job.id} "
                            f"after {stale_seconds}s stale"
                        )
                        # Also mark video as ERROR
                        try:
                            await db.execute(
                                text("""
                                    UPDATE videos
                                    SET status = 'ERROR', updated_at = now()
                                    WHERE id = :video_id
                                """),
                                {"video_id": video_id},
                            )
                            await db.commit()
                        except Exception:
                            pass

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
            timeout_detected=timeout_detected,
            stale_seconds=stale_seconds,
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
    """
    try:
        email = current_user.get("email", "")
        video_id = payload.video_id
        chunk_index = payload.chunk_index

        chunk_filename = f"chunks/chunk_{chunk_index:04d}.mp4"

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


# ──────────────────────────────────────────────
# 4. Retry Analysis
# ──────────────────────────────────────────────
@router.post("/retry/{video_id}", response_model=LiveAnalysisStartResponse)
async def retry_analysis(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Retry a failed or stuck analysis job.
    Can also create a job if one doesn't exist (self-healing).
    """
    try:
        user_id = current_user["id"]
        email = current_user.get("email", "")

        # Find existing job
        result = await db.execute(
            select(LiveAnalysisJob).where(
                LiveAnalysisJob.video_id == video_id,
                LiveAnalysisJob.user_id == user_id,
            )
        )
        job = result.scalar_one_or_none()

        if job:
            # Reset the job
            job.status = "pending"
            job.current_step = None
            job.progress = 0
            job.error_message = None
            job.started_at = None
            job.completed_at = None
            job.results = None
            total_chunks = job.total_chunks
            stream_source = job.stream_source or "tiktok_live"
        else:
            # No job exists — create one
            total_chunks = None
            stream_source = "tiktok_live"
            job = LiveAnalysisJob(
                id=uuid_module.uuid4(),
                video_id=video_id,
                user_id=user_id,
                stream_source=stream_source,
                status="pending",
                progress=0,
            )
            db.add(job)

        # Reset video record too
        await _ensure_video_record(db, video_id, user_id, "uploaded")
        await db.commit()
        await db.refresh(job)

        # Re-enqueue
        queue_payload = {
            "job_type": "live_analysis",
            "job_id": str(job.id),
            "video_id": video_id,
            "user_id": user_id,
            "stream_source": stream_source,
            "total_chunks": total_chunks,
            "email": email,
        }

        enqueue_result = await enqueue_job(queue_payload)

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
            await db.commit()
            logger.info(f"[live-analysis/retry] Re-enqueued job={job.id} video={video_id}")
        else:
            await db.execute(
                update(LiveAnalysisJob)
                .where(LiveAnalysisJob.id == job.id)
                .values(
                    status="failed",
                    error_message=f"Retry enqueue failed: {enqueue_result.error}",
                )
            )
            await db.commit()
            logger.error(f"[live-analysis/retry] Enqueue failed: {enqueue_result.error}")

        final_status = "pending" if enqueue_result.success else "failed"
        return LiveAnalysisStartResponse(
            job_id=str(job.id),
            video_id=video_id,
            status=final_status,
            message=(
                "Analysis retry started"
                if enqueue_result.success
                else f"Retry failed: {enqueue_result.error}"
            ),
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"[live-analysis/retry] Unexpected error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retry analysis: {exc}",
        )
