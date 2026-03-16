"""
Auto Video Job DB persistence layer.

Provides helpers to save / load auto video jobs from PostgreSQL.
The in-memory dict (auto_video_jobs) remains the primary store during
pipeline execution for speed; DB is synced at key checkpoints:
  - Job creation
  - Step transitions (status changes)
  - Job completion / error
  - Job deletion

On startup, completed/errored jobs are loaded from DB into memory
so list_jobs and get_job_status work across deploys.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orm.auto_video_job import AutoVideoJob

logger = logging.getLogger(__name__)


def _job_dict_to_orm(job: Dict[str, Any]) -> AutoVideoJob:
    """Convert in-memory job dict to ORM model."""
    status_val = job["status"]
    if hasattr(status_val, "value"):
        status_val = status_val.value

    created_at = job.get("created_at")
    if isinstance(created_at, (int, float)):
        created_at = datetime.fromtimestamp(created_at, tz=timezone.utc)

    completed_at = job.get("completed_at")
    if isinstance(completed_at, (int, float)):
        completed_at = datetime.fromtimestamp(completed_at, tz=timezone.utc)

    return AutoVideoJob(
        job_id=job["job_id"],
        status=status_val,
        step=job.get("step", "pending"),
        step_detail=job.get("step_detail"),
        progress=job.get("progress", 0),
        error=job.get("error"),
        video_url=job["video_url"],
        topic=job["topic"],
        voice_id=job.get("voice_id"),
        language=job.get("language", "ja"),
        tone=job.get("tone", "professional_friendly"),
        quality=job.get("quality", "high"),
        enable_lip_sync=job.get("enable_lip_sync", True),
        script_text=job.get("script_text"),
        product_info=job.get("product_info"),
        generated_script=job.get("generated_script"),
        tts_audio_duration_sec=job.get("tts_audio_duration_sec"),
        result_video_url=job.get("result_video_url"),
        result_blob_url=job.get("result_blob_url"),
        result_video_size_mb=job.get("result_video_size_mb"),
        result_video_path=job.get("result_video_path"),
        face_swap_job_id=job.get("face_swap_job_id"),
        created_at=created_at,
        completed_at=completed_at,
    )


def _orm_to_job_dict(row: AutoVideoJob) -> Dict[str, Any]:
    """Convert ORM model to in-memory job dict."""
    created_ts = row.created_at.timestamp() if row.created_at else time.time()
    completed_ts = row.completed_at.timestamp() if row.completed_at else None

    return {
        "job_id": row.job_id,
        "status": row.status,
        "step": row.step or "pending",
        "step_detail": row.step_detail or "",
        "progress": row.progress or 0,
        "error": row.error,
        "video_url": row.video_url,
        "topic": row.topic,
        "voice_id": row.voice_id,
        "language": row.language or "ja",
        "tone": row.tone or "professional_friendly",
        "script_text": row.script_text,
        "quality": row.quality or "high",
        "enable_lip_sync": row.enable_lip_sync if row.enable_lip_sync is not None else True,
        "product_info": row.product_info,
        "target_duration_sec": None,
        "created_at": created_ts,
        "completed_at": completed_ts,
        "result_video_path": row.result_video_path,
        "result_video_url": row.result_video_url,
        "result_blob_url": row.result_blob_url,
        "result_video_size_mb": row.result_video_size_mb,
        "generated_script": row.generated_script,
        "tts_audio_duration_sec": row.tts_audio_duration_sec,
        "face_swap_job_id": row.face_swap_job_id,
        "dubbing_id": None,
    }


async def save_job_to_db(job: Dict[str, Any]) -> None:
    """Save or update a job in the database (upsert)."""
    try:
        from app.core.db import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            async with session.begin():
                existing = await session.get(AutoVideoJob, job["job_id"])
                if existing:
                    # Update existing record
                    status_val = job["status"]
                    if hasattr(status_val, "value"):
                        status_val = status_val.value

                    existing.status = status_val
                    existing.step = job.get("step", "pending")
                    existing.step_detail = job.get("step_detail")
                    existing.progress = job.get("progress", 0)
                    existing.error = job.get("error")
                    existing.generated_script = job.get("generated_script")
                    existing.tts_audio_duration_sec = job.get("tts_audio_duration_sec")
                    existing.result_video_url = job.get("result_video_url")
                    existing.result_blob_url = job.get("result_blob_url")
                    existing.result_video_size_mb = job.get("result_video_size_mb")
                    existing.result_video_path = job.get("result_video_path")
                    existing.face_swap_job_id = job.get("face_swap_job_id")

                    completed_at = job.get("completed_at")
                    if isinstance(completed_at, (int, float)):
                        existing.completed_at = datetime.fromtimestamp(
                            completed_at, tz=timezone.utc
                        )
                else:
                    # Insert new record
                    orm_obj = _job_dict_to_orm(job)
                    session.add(orm_obj)

        logger.debug(f"[{job['job_id']}] Job saved to DB")
    except Exception as e:
        logger.warning(f"[{job.get('job_id', '?')}] Failed to save job to DB: {e}")


async def load_jobs_from_db(limit: int = 50) -> List[Dict[str, Any]]:
    """Load recent jobs from the database."""
    try:
        from app.core.db import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(AutoVideoJob)
                .order_by(AutoVideoJob.created_at.desc())
                .limit(limit)
            )
            rows = result.scalars().all()
            return [_orm_to_job_dict(row) for row in rows]
    except Exception as e:
        logger.warning(f"Failed to load jobs from DB: {e}")
        return []


async def load_job_from_db(job_id: str) -> Optional[Dict[str, Any]]:
    """Load a single job from the database."""
    try:
        from app.core.db import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            row = await session.get(AutoVideoJob, job_id)
            if row:
                return _orm_to_job_dict(row)
            return None
    except Exception as e:
        logger.warning(f"Failed to load job {job_id} from DB: {e}")
        return None


async def delete_job_from_db(job_id: str) -> None:
    """Delete a job from the database."""
    try:
        from app.core.db import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(
                    delete(AutoVideoJob).where(AutoVideoJob.job_id == job_id)
                )
        logger.debug(f"[{job_id}] Job deleted from DB")
    except Exception as e:
        logger.warning(f"Failed to delete job {job_id} from DB: {e}")


async def restore_jobs_to_memory(auto_video_jobs: Dict[str, Dict[str, Any]]) -> int:
    """
    Load completed/errored jobs from DB into the in-memory store.
    Called on startup to restore job history after deploy/restart.
    Returns the number of jobs restored.
    """
    try:
        jobs = await load_jobs_from_db(limit=100)
        count = 0
        for job_dict in jobs:
            job_id = job_dict["job_id"]
            if job_id not in auto_video_jobs:
                auto_video_jobs[job_id] = job_dict
                count += 1
        return count
    except Exception as e:
        logger.warning(f"Failed to restore jobs from DB: {e}")
        return 0
