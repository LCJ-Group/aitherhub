"""
Upload Core – isolated upload endpoints.

This module contains ONLY the upload-related endpoints.
It is intentionally kept separate from video.py so that new feature
development never accidentally breaks the upload pipeline.

Rules:
  - No feature-specific logic (clips, phases, reports, etc.) here.
  - Any change to this file MUST pass the upload regression tests.
  - The API contract (request/response schemas) MUST remain backward-compatible.
"""

from __future__ import annotations

import uuid as uuid_module
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
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
    """Handle upload completion – save video info to database and enqueue for processing."""
    try:
        if current_user["email"] != payload.email:
            raise HTTPException(status_code=403, detail="Email does not match current user")

        video_repo = VideoRepository(lambda: db)
        service = VideoService(video_repository=video_repo)

        result = await service.handle_upload_complete(
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
        return UploadCompleteResponse(**result)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to complete upload: {exc}")


# ──────────────────────────────────────────────
# 4. Batch Upload Complete (multiple videos)
# ──────────────────────────────────────────────
@router.post("/batch-upload-complete", response_model=BatchUploadCompleteResponse)
async def batch_upload_complete(
    payload: BatchUploadCompleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Handle batch upload completion – multiple videos sharing the same Excel files."""
    try:
        if current_user["email"] != payload.email:
            raise HTTPException(status_code=403, detail="Email does not match current user")

        video_repo = VideoRepository(lambda: db)
        service = VideoService(video_repository=video_repo)

        video_ids = []
        for v in payload.videos:
            result = await service.handle_upload_complete(
                user_id=current_user["id"],
                email=payload.email,
                video_id=v.video_id,
                original_filename=v.filename,
                db=db,
                upload_id=v.upload_id,
                upload_type="clean_video",
                excel_product_blob_url=payload.excel_product_blob_url,
                excel_trend_blob_url=payload.excel_trend_blob_url,
                time_offset_seconds=v.time_offset_seconds or 0,
            )
            video_ids.append(result["video_id"])

        return BatchUploadCompleteResponse(
            video_ids=video_ids,
            status="uploaded",
            message=f"{len(video_ids)} videos queued for analysis",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to complete batch upload: {exc}")


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
