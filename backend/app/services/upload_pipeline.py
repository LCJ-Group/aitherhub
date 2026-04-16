"""
Upload Pipeline Service
=======================
Encapsulates the **only** correct order for completing a video upload:

    Step 1 – Validate inputs
    Step 2 – Create video DB record  (status = "uploaded")
    Step 3 – Generate download SAS URL for worker
    Step 4 – Build queue payload  (+Excel URLs for clean_video)
    Step 5 – Enqueue worker job + persist evidence
    Step 6 – Clean up upload session record

Rules
-----
- This service is the single source of truth for the upload pipeline order.
- It MUST NOT import anything from video.py or other feature modules.
- Any change to the pipeline order MUST be reflected in the integration tests
  (backend/tests/test_upload_pipeline.py).
- Worker failures MUST NOT affect upload success.  The upload is considered
  successful as soon as the DB record is created (Step 2).  Enqueue failure
  is recorded in the DB but does NOT raise an exception to the caller.
"""
from __future__ import annotations

import json
import logging
import time
import uuid as uuid_module
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import delete, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orm.upload import Upload
from app.models.orm.video import Video
from app.repository.video_repository import VideoRepository
from app.services.queue_service import EnqueueResult, enqueue_job
from app.services.storage_service import generate_download_sas

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Upload Stage Constants
# ---------------------------------------------------------------------------

class UploadStage:
    """Pipeline stage identifiers for observability."""
    VALIDATE = "validate"
    DB_RECORD = "db_record"
    SAS_GENERATE = "sas_generate"
    QUEUE_BUILD = "queue_build"
    ENQUEUE = "enqueue"
    PERSIST_EVIDENCE = "persist_evidence"
    CLEANUP = "cleanup"
    BLOB_VERIFY = "blob_verify"

    ALL_STAGES = [
        VALIDATE, DB_RECORD, SAS_GENERATE,
        QUEUE_BUILD, ENQUEUE, PERSIST_EVIDENCE, CLEANUP,
    ]


# ---------------------------------------------------------------------------
# Stage Event dataclass
# ---------------------------------------------------------------------------

@dataclass
class StageEvent:
    """Records one pipeline stage execution."""
    stage: str
    status: str          # "ok" | "error" | "skipped"
    duration_ms: int = 0
    error_message: Optional[str] = None
    error_type: Optional[str] = None
    metadata: Optional[dict] = None


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class UploadPipelineResult:
    """Structured result returned to the API endpoint."""
    video_id: str
    status: str
    enqueue_status: str          # "OK" | "FAILED" | "SKIPPED"
    message: str
    enqueue_error: Optional[str] = None
    failed_stage: Optional[str] = None
    stage_events: List[StageEvent] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            "video_id": self.video_id,
            "status": self.status,
            "enqueue_status": self.enqueue_status,
            "message": self.message,
        }
        if self.failed_stage:
            d["failed_stage"] = self.failed_stage
        if self.stage_events:
            d["stages"] = [
                {
                    "stage": e.stage,
                    "status": e.status,
                    "duration_ms": e.duration_ms,
                    **({"error": e.error_message} if e.error_message else {}),
                }
                for e in self.stage_events
            ]
        return d


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class UploadPipelineService:
    """
    Executes the upload completion pipeline in a guaranteed order.

    The caller (upload_core.py) passes all required data; this service
    owns the sequencing and error handling.
    """

    def __init__(self, video_repository: VideoRepository) -> None:
        if video_repository is None:
            raise ValueError("VideoRepository is required")
        self._repo = video_repository

    # ------------------------------------------------------------------
    # Stage event logging helper
    # ------------------------------------------------------------------

    @staticmethod
    async def _log_stage_event(
        db: AsyncSession,
        video_id: str,
        upload_id: Optional[str],
        user_id: Optional[int],
        event: StageEvent,
    ) -> None:
        """Persist a stage event to upload_event_log table (best-effort)."""
        try:
            await db.execute(
                text("""
                    INSERT INTO upload_event_log
                        (video_id, upload_id, user_id, stage, status,
                         duration_ms, error_message, error_type, metadata_json)
                    VALUES
                        (:video_id, :upload_id, :user_id, :stage, :status,
                         :duration_ms, :error_message, :error_type, :metadata_json)
                """),
                {
                    "video_id": video_id,
                    "upload_id": upload_id,
                    "user_id": user_id,
                    "stage": event.stage,
                    "status": event.status,
                    "duration_ms": event.duration_ms,
                    "error_message": event.error_message,
                    "error_type": event.error_type,
                    "metadata_json": json.dumps(event.metadata) if event.metadata else None,
                },
            )
            await db.commit()
        except Exception as exc:
            _logger.debug(f"[upload_pipeline] Could not log stage event: {exc}")
            try:
                await db.rollback()
            except Exception as _e:
                logger.debug(f"Suppressed: {_e}")

    @staticmethod
    async def _update_video_stage(
        db: AsyncSession,
        video_id: str,
        last_stage: str,
        error_stage: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Update the video record with the latest pipeline stage info (best-effort)."""
        try:
            vid_uuid = uuid_module.UUID(video_id)
            values: Dict = {"upload_last_stage": last_stage}
            if error_stage:
                values["upload_error_stage"] = error_stage
                values["upload_error_message"] = (error_message or "")[:2000]
            await db.execute(
                update(Video).where(Video.id == vid_uuid).values(**values)
            )
            await db.commit()
        except Exception as exc:
            _logger.debug(f"[upload_pipeline] Could not update video stage: {exc}")
            try:
                await db.rollback()
            except Exception as _e:
                logger.debug(f"Suppressed: {_e}")

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def complete_upload(
        self,
        *,
        user_id: int,
        email: str,
        video_id: str,
        original_filename: str,
        db: AsyncSession,
        upload_id: Optional[str] = None,
        upload_type: str = "screen_recording",
        excel_product_blob_url: Optional[str] = None,
        excel_trend_blob_url: Optional[str] = None,
        time_offset_seconds: float = 0.0,
        language: str = "ja",
    ) -> UploadPipelineResult:
        """
        Execute the upload completion pipeline.

        Steps
        -----
        1. Validate inputs
        2. Create DB record  (status = "uploaded")
        3. Generate download SAS URL
        4. Build queue payload
        5. Enqueue worker job  (failure is recorded, NOT raised)
        6. Persist enqueue evidence
        7. Clean up upload session
        """
        pipeline_start = time.monotonic()
        stage_events: List[StageEvent] = []
        failed_stage: Optional[str] = None

        _logger.info(
            f"[upload_pipeline] START video_id={video_id} "
            f"user_id={user_id} upload_type={upload_type} "
            f"filename={original_filename}"
        )

        # ── Step 1: Validate inputs ───────────────────────────────────────
        t0 = time.monotonic()
        try:
            self._validate_inputs(video_id=video_id, email=email, filename=original_filename)
            evt = StageEvent(
                stage=UploadStage.VALIDATE, status="ok",
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
            stage_events.append(evt)
            _logger.info(f"[upload_pipeline] Step 1 OK: validate ({evt.duration_ms}ms)")
        except Exception as exc:
            evt = StageEvent(
                stage=UploadStage.VALIDATE, status="error",
                duration_ms=int((time.monotonic() - t0) * 1000),
                error_message=str(exc), error_type=type(exc).__name__,
            )
            stage_events.append(evt)
            failed_stage = UploadStage.VALIDATE
            _logger.error(f"[upload_pipeline] Step 1 FAILED: {exc}")
            # Try to log event (may fail if video_id is invalid)
            try:
                await self._log_stage_event(db, video_id, upload_id, user_id, evt)
            except Exception as _e:
                logger.debug(f"Suppressed: {_e}")
            raise

        # ── Step 2: Create DB record ──────────────────────────────────────
        t0 = time.monotonic()
        try:
            video = await self._create_db_record(
                user_id=user_id,
                video_id=video_id,
                original_filename=original_filename,
                upload_type=upload_type,
                excel_product_blob_url=excel_product_blob_url,
                excel_trend_blob_url=excel_trend_blob_url,
                time_offset_seconds=time_offset_seconds,
                language=language,
            )
            evt = StageEvent(
                stage=UploadStage.DB_RECORD, status="ok",
                duration_ms=int((time.monotonic() - t0) * 1000),
                metadata={"upload_type": upload_type},
            )
            stage_events.append(evt)
            _logger.info(
                f"[upload_pipeline] Step 2 OK: DB record created "
                f"video_id={video.id} ({evt.duration_ms}ms)"
            )
            await self._log_stage_event(db, video_id, upload_id, user_id, evt)
        except Exception as exc:
            evt = StageEvent(
                stage=UploadStage.DB_RECORD, status="error",
                duration_ms=int((time.monotonic() - t0) * 1000),
                error_message=str(exc), error_type=type(exc).__name__,
            )
            stage_events.append(evt)
            failed_stage = UploadStage.DB_RECORD
            _logger.error(f"[upload_pipeline] Step 2 FAILED: {exc}")
            try:
                await self._log_stage_event(db, video_id, upload_id, user_id, evt)
            except Exception as _e:
                logger.debug(f"Suppressed: {_e}")
            raise

        # ── Step 3: Generate download SAS URL ─────────────────────────────
        t0 = time.monotonic()
        try:
            download_url = await self._generate_download_url(
                email=email,
                video_id=str(video.id),
                filename=original_filename,
            )
            evt = StageEvent(
                stage=UploadStage.SAS_GENERATE, status="ok",
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
            stage_events.append(evt)
            _logger.info(f"[upload_pipeline] Step 3 OK: SAS URL generated ({evt.duration_ms}ms)")
            await self._log_stage_event(db, video_id, upload_id, user_id, evt)
        except Exception as exc:
            evt = StageEvent(
                stage=UploadStage.SAS_GENERATE, status="error",
                duration_ms=int((time.monotonic() - t0) * 1000),
                error_message=str(exc), error_type=type(exc).__name__,
            )
            stage_events.append(evt)
            failed_stage = UploadStage.SAS_GENERATE
            _logger.error(f"[upload_pipeline] Step 3 FAILED: {exc}")
            await self._log_stage_event(db, video_id, upload_id, user_id, evt)
            await self._update_video_stage(db, video_id, UploadStage.DB_RECORD, UploadStage.SAS_GENERATE, str(exc))
            # Mark enqueue_status as FAILED so stuck_video_monitor can detect
            # and retry this video (Part 2: never-enqueued detection)
            try:
                vid_uuid = uuid_module.UUID(video_id)
                await db.execute(
                    update(Video).where(Video.id == vid_uuid).values(
                        enqueue_status="FAILED",
                        enqueue_error=f"SAS URL generation failed: {exc}"[:2000],
                        last_error_code="SAS_GENERATION_FAILED",
                        last_error_message=f"SAS URL generation failed after retries: {exc}"[:2000],
                    )
                )
                await db.commit()
            except Exception as db_err:
                _logger.debug(f"[upload_pipeline] Could not mark enqueue_status: {db_err}")
                try:
                    await db.rollback()
                except Exception:
                    pass
            raise

        # ── Step 4: Build queue payload ───────────────────────────────────
        t0 = time.monotonic()
        try:
            queue_payload = self._build_queue_payload(
                video=video,
                download_url=download_url,
                original_filename=original_filename,
                user_id=user_id,
                upload_type=upload_type,
                time_offset_seconds=time_offset_seconds,
                language=language,
            )

            # Add Excel download URLs for clean_video uploads
            if upload_type == "clean_video":
                queue_payload = await self._add_excel_urls(
                    queue_payload=queue_payload,
                    email=email,
                    video_id=str(video.id),
                    excel_product_blob_url=excel_product_blob_url,
                    excel_trend_blob_url=excel_trend_blob_url,
                )

            evt = StageEvent(
                stage=UploadStage.QUEUE_BUILD, status="ok",
                duration_ms=int((time.monotonic() - t0) * 1000),
                metadata={"has_excel": upload_type == "clean_video"},
            )
            stage_events.append(evt)
            _logger.info(f"[upload_pipeline] Step 4 OK: queue payload built ({evt.duration_ms}ms)")
            await self._log_stage_event(db, video_id, upload_id, user_id, evt)
        except Exception as exc:
            evt = StageEvent(
                stage=UploadStage.QUEUE_BUILD, status="error",
                duration_ms=int((time.monotonic() - t0) * 1000),
                error_message=str(exc), error_type=type(exc).__name__,
            )
            stage_events.append(evt)
            failed_stage = UploadStage.QUEUE_BUILD
            _logger.error(f"[upload_pipeline] Step 4 FAILED: {exc}")
            await self._log_stage_event(db, video_id, upload_id, user_id, evt)
            await self._update_video_stage(db, video_id, UploadStage.SAS_GENERATE, UploadStage.QUEUE_BUILD, str(exc))
            # Mark enqueue_status as FAILED so stuck_video_monitor can detect
            try:
                vid_uuid = uuid_module.UUID(video_id)
                await db.execute(
                    update(Video).where(Video.id == vid_uuid).values(
                        enqueue_status="FAILED",
                        enqueue_error=f"Queue payload build failed: {exc}"[:2000],
                        last_error_code="QUEUE_BUILD_FAILED",
                        last_error_message=f"Queue payload build failed: {exc}"[:2000],
                    )
                )
                await db.commit()
            except Exception as db_err:
                _logger.debug(f"[upload_pipeline] Could not mark enqueue_status: {db_err}")
                try:
                    await db.rollback()
                except Exception:
                    pass
            raise

        # ── Step 5: Enqueue + persist evidence ─────────────────────────────
        t0 = time.monotonic()
        enqueue_result = await self._enqueue_and_persist(
            db=db,
            video=video,
            queue_payload=queue_payload,
        )
        enqueue_ms = int((time.monotonic() - t0) * 1000)

        if enqueue_result.success:
            evt = StageEvent(
                stage=UploadStage.ENQUEUE, status="ok",
                duration_ms=enqueue_ms,
                metadata={"message_id": enqueue_result.message_id},
            )
            stage_events.append(evt)
            _logger.info(
                f"[upload_pipeline] Step 5 OK: enqueued "
                f"video={video.id} msg_id={enqueue_result.message_id} ({enqueue_ms}ms)"
            )
            await self._update_video_stage(db, video_id, UploadStage.ENQUEUE)
        else:
            evt = StageEvent(
                stage=UploadStage.ENQUEUE, status="error",
                duration_ms=enqueue_ms,
                error_message=enqueue_result.error,
                error_type="EnqueueError",
            )
            stage_events.append(evt)
            # Enqueue failure is non-fatal
            _logger.error(
                f"[upload_pipeline] Step 5 FAILED (non-fatal): "
                f"video={video.id} error={enqueue_result.error} ({enqueue_ms}ms)"
            )
            await self._update_video_stage(
                db, video_id, UploadStage.ENQUEUE,
                UploadStage.ENQUEUE, enqueue_result.error,
            )
        await self._log_stage_event(db, video_id, upload_id, user_id, evt)

        # ── Step 7: Clean up upload session ──────────────────────────────
        t0 = time.monotonic()
        try:
            await self._cleanup_upload_session(
                db=db,
                upload_id=upload_id,
                user_id=user_id,
            )
            evt = StageEvent(
                stage=UploadStage.CLEANUP, status="ok",
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
            stage_events.append(evt)
            await self._log_stage_event(db, video_id, upload_id, user_id, evt)
        except Exception as exc:
            evt = StageEvent(
                stage=UploadStage.CLEANUP, status="error",
                duration_ms=int((time.monotonic() - t0) * 1000),
                error_message=str(exc), error_type=type(exc).__name__,
            )
            stage_events.append(evt)
            # Cleanup failure is non-fatal
            _logger.warning(f"[upload_pipeline] Step 7 FAILED (non-fatal): {exc}")
            await self._log_stage_event(db, video_id, upload_id, user_id, evt)

        # ── Build result ──────────────────────────────────────────────────
        total_ms = int((time.monotonic() - pipeline_start) * 1000)

        if enqueue_result.success:
            message = "Video upload completed; queued for analysis"
            enqueue_status = "OK"
        else:
            message = (
                f"Video saved but enqueue failed: {enqueue_result.error}. "
                "The video will be retried by the worker."
            )
            enqueue_status = "FAILED"

        _logger.info(
            f"[upload_pipeline] DONE video_id={video.id} "
            f"enqueue={enqueue_status} total={total_ms}ms "
            f"stages={len(stage_events)} "
            f"failed_stage={failed_stage or 'none'}"
        )

        return UploadPipelineResult(
            video_id=str(video.id),
            status=video.status,
            enqueue_status=enqueue_status,
            message=message,
            enqueue_error=enqueue_result.error,
            failed_stage=failed_stage,
            stage_events=stage_events,
        )

    # ------------------------------------------------------------------
    # Private helpers – each maps to one pipeline step
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_inputs(*, video_id: str, email: str, filename: str) -> None:
        """Step 1: Raise ValueError for obviously invalid inputs."""
        if not video_id:
            raise ValueError("video_id is required")
        if not email:
            raise ValueError("email is required")
        if not filename:
            raise ValueError("filename is required")
        # Validate video_id is a valid UUID
        try:
            uuid_module.UUID(video_id)
        except ValueError:
            raise ValueError(f"video_id must be a valid UUID, got: {video_id!r}")

    async def _create_db_record(
        self,
        *,
        user_id: int,
        video_id: str,
        original_filename: str,
        upload_type: str,
        excel_product_blob_url: Optional[str],
        excel_trend_blob_url: Optional[str],
        time_offset_seconds: float,
        language: str = "ja",
    ) -> Video:
        """Step 2: Persist video record to DB (status = 'uploaded')."""
        return await self._repo.create_video(
            user_id=user_id,
            video_id=video_id,
            original_filename=original_filename,
            status="uploaded",
            upload_type=upload_type,
            excel_product_blob_url=excel_product_blob_url,
            excel_trend_blob_url=excel_trend_blob_url,
            time_offset_seconds=time_offset_seconds,
            language=language,
        )

    @staticmethod
    async def _generate_download_url(
        *,
        email: str,
        video_id: str,
        filename: str,
        max_retries: int = 3,
        retry_delay: float = 3.0,
    ) -> str:
        """Step 3: Generate a 24-hour read SAS URL for the worker.

        Includes retry logic to handle transient Azure Blob Storage
        connection errors that previously caused videos to get stuck
        in 'uploaded' status without ever being enqueued.
        """
        import asyncio as _asyncio

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                download_url, _ = await generate_download_sas(
                    email=email,
                    video_id=video_id,
                    filename=filename,
                    expires_in_minutes=1440,  # 24 hours
                )
                if attempt > 1:
                    _logger.info(
                        f"[upload_pipeline] SAS URL generated on attempt "
                        f"{attempt}/{max_retries} for video {video_id}"
                    )
                return download_url
            except Exception as e:
                last_error = e
                _logger.warning(
                    f"[upload_pipeline] SAS URL attempt {attempt}/{max_retries} "
                    f"failed for video {video_id}: {e}"
                )
                if attempt < max_retries:
                    await _asyncio.sleep(retry_delay)

        raise last_error

    @staticmethod
    def _build_queue_payload(
        *,
        video: Video,
        download_url: str,
        original_filename: str,
        user_id: int,
        upload_type: str,
        time_offset_seconds: float,
        language: str = "ja",
    ) -> dict:
        """Step 4: Build the worker queue message payload."""
        return {
            "video_id": str(video.id),
            "blob_url": download_url,
            "original_filename": original_filename,
            "user_id": user_id,
            "upload_type": upload_type,
            "time_offset_seconds": time_offset_seconds,
            "language": language,
        }

    @staticmethod
    async def _add_excel_urls(
        *,
        queue_payload: dict,
        email: str,
        video_id: str,
        excel_product_blob_url: Optional[str],
        excel_trend_blob_url: Optional[str],
    ) -> dict:
        """Step 4b: Append Excel SAS URLs for clean_video uploads."""
        if excel_product_blob_url:
            try:
                product_download_url, _ = await generate_download_sas(
                    email=email,
                    video_id=video_id,
                    filename=f"excel/{excel_product_blob_url.split('/')[-1].split('?')[0]}",
                    expires_in_minutes=1440,
                )
                queue_payload["excel_product_url"] = product_download_url
            except Exception as exc:
                _logger.warning(f"[upload_pipeline] Excel product URL failed: {exc}")

        if excel_trend_blob_url:
            try:
                trend_download_url, _ = await generate_download_sas(
                    email=email,
                    video_id=video_id,
                    filename=f"excel/{excel_trend_blob_url.split('/')[-1].split('?')[0]}",
                    expires_in_minutes=1440,
                )
                queue_payload["excel_trend_url"] = trend_download_url
            except Exception as exc:
                _logger.warning(f"[upload_pipeline] Excel trend URL failed: {exc}")

        return queue_payload

    @staticmethod
    async def _enqueue_and_persist(
        *,
        db: AsyncSession,
        video: Video,
        queue_payload: dict,
    ) -> EnqueueResult:
        """
        Step 5: Enqueue the job and persist the result to DB.

        This method NEVER raises — enqueue failure is recorded in DB
        and returned to the caller as a non-fatal result.
        """
        try:
            enqueue_result = await enqueue_job(queue_payload)
        except Exception as unexpected_exc:
            _logger.error(
                f"[upload_pipeline] Step 5: enqueue_job raised unexpectedly: {unexpected_exc}"
            )
            enqueue_result = EnqueueResult(
                success=False,
                error=f"Unexpected enqueue error: {unexpected_exc}",
            )

        try:
            vid_uuid = uuid_module.UUID(str(video.id))
            if enqueue_result.success:
                await db.execute(
                    update(Video)
                    .where(Video.id == vid_uuid)
                    .values(
                        enqueue_status="OK",
                        queue_message_id=enqueue_result.message_id,
                        queue_enqueued_at=enqueue_result.enqueued_at,
                        enqueue_error=None,
                    )
                )
            else:
                await db.execute(
                    update(Video)
                    .where(Video.id == vid_uuid)
                    .values(
                        enqueue_status="FAILED",
                        queue_message_id=None,
                        queue_enqueued_at=None,
                        enqueue_error=enqueue_result.error,
                    )
                )
            await db.commit()
        except Exception as db_err:
            _logger.error(
                f"[upload_pipeline] Step 5: failed to persist enqueue evidence: {db_err}"
            )
            try:
                await db.rollback()
            except Exception as _e:
                logger.debug(f"Suppressed: {_e}")

        return enqueue_result

    @staticmethod
    async def _cleanup_upload_session(
        *,
        db: AsyncSession,
        upload_id: Optional[str],
        user_id: int,
    ) -> None:
        """
        Step 7: Remove the upload session record.

        Failure here is non-fatal — the upload has already succeeded.
        """
        # Remove specific upload session
        if upload_id:
            try:
                upload_uuid = uuid_module.UUID(upload_id)
                await db.execute(delete(Upload).where(Upload.id == upload_uuid))
                await db.commit()
            except Exception as exc:
                _logger.warning(f"[upload_pipeline] Step 7: cleanup upload_id failed: {exc}")
                try:
                    await db.rollback()
                except Exception as _e:
                    logger.debug(f"Suppressed: {_e}")

        # Remove stale upload records for this user (older than 24 hours)
        try:
            stale_cutoff = datetime.utcnow() - timedelta(hours=24)
            await db.execute(
                delete(Upload).where(
                    Upload.user_id == user_id,
                    Upload.created_at < stale_cutoff,
                )
            )
            await db.commit()
        except Exception as exc:
            _logger.warning(f"[upload_pipeline] Step 7: stale cleanup failed: {exc}")
            try:
                await db.rollback()
            except Exception as _e:
                logger.debug(f"Suppressed: {_e}")
