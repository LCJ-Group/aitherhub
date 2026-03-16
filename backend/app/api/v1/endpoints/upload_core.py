"""
Upload Core – isolated upload endpoints.

This module contains ONLY the upload-related endpoints.
It is intentionally kept separate from video.py so that new feature
development never accidentally breaks the upload pipeline.

╔══════════════════════════════════════════════════════════════════╗
║  FROZEN API CONTRACT – DO NOT CHANGE ROUTES OR RESPONSE SCHEMAS ║
║                                                                  ║
║  Routes defined here:                                            ║
║    POST /api/v1/videos/generate-upload-url                       ║
║    POST /api/v1/videos/generate-download-url                     ║
║    POST /api/v1/videos/upload-complete                           ║
║    POST /api/v1/videos/batch-upload-complete                     ║
║    POST /api/v1/videos/generate-excel-upload-url                 ║
║    GET  /api/v1/videos/uploads/check/{user_id}                   ║
║    DELETE /api/v1/videos/uploads/clear/{user_id}                 ║
║                                                                  ║
║  Pipeline order (enforced by UploadPipelineService):             ║
║    Step 1 – Validate inputs                                      ║
║    Step 2 – Create DB record  (status = "uploaded")              ║
║    Step 3 – Generate download SAS URL                            ║
║    Step 4 – Build queue payload                                  ║
║    Step 5 – Enqueue worker job                                   ║
║    Step 6 – Persist enqueue evidence                             ║
║    Step 7 – Clean up upload session                              ║
║                                                                  ║
║  Rules:                                                          ║
║    - No feature-specific logic (clips, phases, reports) here.    ║
║    - Any change MUST pass backend/tests/test_upload_pipeline.py  ║
║    - Worker failures MUST NOT break upload success.              ║
╚══════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import uuid as uuid_module
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, text
from loguru import logger

from app.schema.video_schema import (
    GenerateUploadURLRequest,
    GenerateUploadURLResponse,
    GenerateDownloadURLRequest,
    GenerateDownloadURLResponse,
    UploadCompleteRequest,
    UploadCompleteResponse,
    GenerateExcelUploadURLRequest,
    GenerateExcelUploadURLResponse,
    BatchUploadCompleteRequest,
    BatchUploadCompleteResponse,
)
from app.services.video_service import VideoService
from app.services.upload_pipeline import UploadPipelineService
from app.repository.video_repository import VideoRepository
from app.core.dependencies import get_db, get_current_user
from app.models.orm.upload import Upload
from app.models.orm.video import Video

router = APIRouter(
    prefix="/videos",
    tags=["upload-core"],
)

# Singleton service (stateless – no repo needed for URL generation)
_video_service = VideoService()


# ──────────────────────────────────────────────
# 1. Generate Upload URL (SAS)
# ──────────────────────────────────────────────
@router.post("/generate-upload-url", response_model=GenerateUploadURLResponse)
async def generate_upload_url(
    payload: GenerateUploadURLRequest,
    db: AsyncSession = Depends(get_db),
):
    """Generate a write-only SAS URL for direct upload to Azure Blob Storage."""
    try:
        result = await _video_service.generate_upload_url(
            email=payload.email,
            db=db,
            video_id=payload.video_id,
            filename=payload.filename,
        )
        return GenerateUploadURLResponse(**result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to generate upload URL: {exc}")


# ──────────────────────────────────────────────
# 2. Generate Download URL (SAS)
# ──────────────────────────────────────────────
@router.post("/generate-download-url", response_model=GenerateDownloadURLResponse)
async def generate_download_url(payload: GenerateDownloadURLRequest):
    """Generate a read-only SAS URL for downloading a blob."""
    try:
        result = await _video_service.generate_download_url(
            email=payload.email,
            video_id=payload.video_id,
            filename=payload.filename,
            expires_in_minutes=payload.expires_in_minutes,
        )
        return GenerateDownloadURLResponse(**result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to generate download URL: {exc}")


# ──────────────────────────────────────────────
# 3. Upload Complete (single video)
# ──────────────────────────────────────────────
@router.post("/upload-complete", response_model=UploadCompleteResponse)
async def upload_complete(
    payload: UploadCompleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Handle upload completion.

    Pipeline order (guaranteed by UploadPipelineService):
      1. Validate inputs
      2. Create DB record  (status = "uploaded")
      3. Generate download SAS URL
      4. Enqueue worker job
      5. Persist enqueue evidence
      6. Clean up upload session

    Worker failures do NOT break upload success.
    """
    try:
        if current_user["email"] != payload.email:
            raise HTTPException(status_code=403, detail="Email does not match current user")

        video_repo = VideoRepository(lambda: db)
        pipeline = UploadPipelineService(video_repository=video_repo)

        result = await pipeline.complete_upload(
            user_id=current_user["id"],
            email=payload.email,
            video_id=payload.video_id,
            original_filename=payload.filename,
            db=db,
            upload_id=payload.upload_id,
            upload_type=payload.upload_type or "screen_recording",
            excel_product_blob_url=payload.excel_product_blob_url,
            excel_trend_blob_url=payload.excel_trend_blob_url,
            time_offset_seconds=payload.time_offset_seconds or 0,
        )
        return UploadCompleteResponse(**result.to_dict())
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception(f"[upload_complete] Unexpected error: {exc}")
        # Record error log to video_error_logs so UI can display it
        try:
            import traceback as _tb
            from app.core.db import AsyncSessionLocal
            async with AsyncSessionLocal() as err_db:
                await err_db.execute(
                    text("""
                        INSERT INTO video_error_logs
                            (video_id, error_code, error_step, error_message, error_detail, source)
                        VALUES
                            (:vid, :code, :step, :msg, :detail, 'api')
                    """),
                    {
                        "vid": payload.video_id,
                        "code": "UPLOAD_COMPLETE_FAIL",
                        "step": "UPLOAD_COMPLETE",
                        "msg": str(exc)[:2000],
                        "detail": _tb.format_exc()[:10000],
                    },
                )
                await err_db.commit()
        except Exception as log_err:
            logger.warning(f"[upload_complete] Failed to record error log: {log_err}")
        raise HTTPException(status_code=500, detail=f"Failed to complete upload: {exc}")


# ──────────────────────────────────────────────
# 4. Batch Upload Complete (multiple videos)
# ──────────────────────────────────────────────
@router.post("/batch-upload-complete", response_model=BatchUploadCompleteResponse)
async def batch_upload_complete(
    payload: BatchUploadCompleteRequest,
    current_user=Depends(get_current_user),
):
    """
    Handle batch upload completion – multiple videos sharing the same Excel files.

    Each video gets its own independent DB session to prevent cascade failures:
    if one video's session encounters an error (e.g. commit/rollback), it does
    NOT affect the remaining videos in the batch.
    """
    from app.core.db import AsyncSessionLocal

    if current_user["email"] != payload.email:
        raise HTTPException(status_code=403, detail="Email does not match current user")

    video_ids = []
    failed = []
    for v in payload.videos:
        try:
            # Each video gets its own DB session to isolate failures
            async with AsyncSessionLocal() as video_db:
                video_repo = VideoRepository(lambda _db=video_db: _db)
                pipeline = UploadPipelineService(video_repository=video_repo)
                result = await pipeline.complete_upload(
                    user_id=current_user["id"],
                    email=payload.email,
                    video_id=v.video_id,
                    original_filename=v.filename,
                    db=video_db,
                    upload_id=v.upload_id,
                    upload_type="clean_video",
                    excel_product_blob_url=payload.excel_product_blob_url,
                    excel_trend_blob_url=payload.excel_trend_blob_url,
                    time_offset_seconds=v.time_offset_seconds or 0,
                )
                video_ids.append(result.video_id)
        except Exception as exc:
            logger.exception(
                f"[batch_upload_complete] Failed for video {v.video_id}: {exc}"
            )
            failed.append({"video_id": v.video_id, "error": str(exc)})
            # Record error log to video_error_logs so UI can display it
            try:
                import traceback as _tb
                async with AsyncSessionLocal() as err_db:
                    await err_db.execute(
                        text("""
                            INSERT INTO video_error_logs
                                (video_id, error_code, error_step, error_message, error_detail, source)
                            VALUES
                                (:vid, :code, :step, :msg, :detail, 'api')
                        """),
                        {
                            "vid": v.video_id,
                            "code": "BATCH_UPLOAD_FAIL",
                            "step": "UPLOAD_COMPLETE",
                            "msg": str(exc)[:2000],
                            "detail": _tb.format_exc()[:10000],
                        },
                    )
                    await err_db.commit()
            except Exception as log_err:
                logger.warning(f"[batch_upload_complete] Failed to record error log: {log_err}")

    if not video_ids and failed:
        raise HTTPException(
            status_code=500,
            detail=f"All {len(failed)} videos failed: {failed[0]['error']}",
        )

    msg = f"{len(video_ids)} videos queued for analysis"
    if failed:
        msg += f" ({len(failed)} failed)"
        logger.warning(
            f"[batch_upload_complete] {len(failed)}/{len(payload.videos)} videos failed: "
            f"{[f['video_id'] for f in failed]}"
        )

    return BatchUploadCompleteResponse(
        video_ids=video_ids,
        status="uploaded",
        message=msg,
        failed=failed,
    )


# ──────────────────────────────────────────────
# 5. Generate Excel Upload URLs
# ──────────────────────────────────────────────
@router.post("/generate-excel-upload-url", response_model=GenerateExcelUploadURLResponse)
async def generate_excel_upload_url(
    payload: GenerateExcelUploadURLRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Generate SAS upload URLs for Excel files (product + trend_stats)."""
    try:
        service = VideoService()
        result = await service.generate_excel_upload_urls(
            email=payload.email,
            video_id=payload.video_id,
            product_filename=payload.product_filename,
            trend_filename=payload.trend_filename,
        )
        return GenerateExcelUploadURLResponse(**result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to generate Excel upload URLs: {exc}")


# ──────────────────────────────────────────────
# 6. Check Resumable Upload
# ──────────────────────────────────────────────
@router.get("/uploads/check/{user_id}")
async def check_upload_resume(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Check if user has an in-progress upload to resume.

    Returns upload_resume=True only when:
    - An Upload record exists for this user, AND
    - The record is less than 24 hours old, AND
    - No corresponding Video record exists that was created after the upload
      (which would indicate the upload already completed successfully)
    """
    try:
        if current_user and current_user.get("id") != user_id:
            raise HTTPException(status_code=403, detail="Forbidden")

        result = await db.execute(
            select(Upload)
            .where(Upload.user_id == user_id)
            .order_by(Upload.created_at.desc(), Upload.id.desc())
            .limit(1)
        )
        upload = result.scalar_one_or_none()

        if not upload:
            return {"upload_resume": False}

        # Check 1: stale (>24h)
        now = datetime.now(timezone.utc)
        upload_created = (
            upload.created_at.replace(tzinfo=timezone.utc)
            if upload.created_at.tzinfo is None
            else upload.created_at
        )
        upload_age = now - upload_created
        if upload_age > timedelta(hours=24):
            logger.info(
                f"Stale upload record {upload.id} for user {user_id} "
                f"(age: {upload_age}). Deleting."
            )
            await db.delete(upload)
            await db.commit()
            return {"upload_resume": False}

        # Check 2: video already created around the same time
        video_result = await db.execute(
            select(Video)
            .where(Video.user_id == user_id, Video.status != "NEW")
            .order_by(Video.created_at.desc())
            .limit(1)
        )
        latest_video = video_result.scalar_one_or_none()

        if latest_video and latest_video.created_at:
            video_created = (
                latest_video.created_at.replace(tzinfo=timezone.utc)
                if latest_video.created_at.tzinfo is None
                else latest_video.created_at
            )
            if video_created >= upload_created - timedelta(minutes=5):
                logger.info(
                    f"Upload {upload.id} already completed "
                    f"(video {latest_video.id} status={latest_video.status}). "
                    f"Cleaning up stale upload record."
                )
                await db.delete(upload)
                await db.commit()
                return {"upload_resume": False}

        # Check 3: video currently processing
        processing_result = await db.execute(
            select(Video)
            .where(
                Video.user_id == user_id,
                Video.status.notin_(["NEW", "DONE", "ERROR", "uploaded"]),
            )
            .limit(1)
        )
        processing_video = processing_result.scalar_one_or_none()
        if processing_video:
            logger.info(
                f"Upload {upload.id} has a video in processing "
                f"(video {processing_video.id} status={processing_video.status}). "
                f"Cleaning up upload record."
            )
            await db.delete(upload)
            await db.commit()
            return {"upload_resume": False}

        return {"upload_resume": True, "upload_id": str(upload.id)}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Failed to check upload resume for user {user_id}: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to check upload resume: {exc}")


# ──────────────────────────────────────────────
# 7. Clear User Uploads
# ──────────────────────────────────────────────
@router.delete("/uploads/clear/{user_id}")
async def clear_user_uploads(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Clear all in-progress uploads for a user."""
    try:
        if current_user and current_user.get("id") != user_id:
            raise HTTPException(status_code=403, detail="Forbidden")

        result = await db.execute(
            select(Upload).where(Upload.user_id == user_id)
        )
        uploads = result.scalars().all()
        deleted_count = len(uploads)

        for upload in uploads:
            await db.delete(upload)

        await db.commit()

        return {
            "status": "success",
            "message": f"Deleted {deleted_count} upload record(s) for user {user_id}",
            "deleted_count": deleted_count,
        }
    except HTTPException:
        raise
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to clear uploads: {exc}")
