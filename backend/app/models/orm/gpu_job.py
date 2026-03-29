"""
GPU Job model for persistent job queue.

Stores all GPU processing jobs (MuseTalk, FaceFusion, IMTalker, LivePortrait)
in the database so they survive worker restarts and can be retried automatically.
"""
from datetime import datetime
from sqlalchemy import Text, DateTime, Integer, Float, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB
from app.models.orm.base import Base, UUIDMixin, TimestampMixin


class GpuJob(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "gpu_jobs"

    # Job identification
    action: Mapped[str] = mapped_column(nullable=False, index=True)
    # e.g. "musetalk", "facefusion_video", "facefusion_frame", "imtalker", "liveportrait"

    # Status tracking
    status: Mapped[str] = mapped_column(
        nullable=False, default="pending", index=True
    )
    # pending → submitted → in_progress → completed / failed / cancelled

    # Provider tracking
    provider: Mapped[str] = mapped_column(
        nullable=False, default="runpod"
    )
    # "runpod", "modal", "replicate"

    provider_job_id: Mapped[str | None] = mapped_column(
        nullable=True, index=True
    )
    # RunPod job ID, Modal call ID, etc.

    # Input/Output (JSONB for flexible schema)
    input_data: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True
    )
    output_data: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True
    )

    # Error tracking
    error_message: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    # Retry tracking
    retry_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    max_retries: Mapped[int] = mapped_column(
        Integer, default=3, nullable=False
    )

    # Timing
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Duration tracking (seconds)
    duration_seconds: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )

    # Caller context (for linking back to the feature that requested the job)
    caller_type: Mapped[str | None] = mapped_column(nullable=True)
    # e.g. "auto_video", "digital_human", "face_swap", "manual"
    caller_id: Mapped[str | None] = mapped_column(nullable=True)
    # e.g. video_id, session_id

    __table_args__ = (
        Index("ix_gpu_jobs_status_created", "status", "created_at"),
        Index("ix_gpu_jobs_provider_status", "provider", "status"),
    )
