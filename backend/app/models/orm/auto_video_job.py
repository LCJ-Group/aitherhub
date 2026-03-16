# app/models/orm/auto_video_job.py
"""
ORM model for auto_video_jobs table.
Persists Auto Video pipeline jobs to the database so they survive
deployments and App Service restarts.
"""
from sqlalchemy import String, Text, Float, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from app.models.orm.base import Base
from typing import Optional
from datetime import datetime


class AutoVideoJob(Base):
    __tablename__ = "auto_video_jobs"

    # Primary key: the job_id (e.g., "av-xxxxxxxxxxxx")
    job_id: Mapped[str] = mapped_column(String(50), primary_key=True)

    # Pipeline state
    status: Mapped[str] = mapped_column(String(50), default="pending")
    step: Mapped[str] = mapped_column(String(50), default="pending")
    step_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Input parameters
    video_url: Mapped[str] = mapped_column(Text)
    topic: Mapped[str] = mapped_column(Text)
    voice_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    language: Mapped[str] = mapped_column(String(10), default="ja")
    tone: Mapped[str] = mapped_column(String(50), default="professional_friendly")
    quality: Mapped[str] = mapped_column(String(20), default="high")
    enable_lip_sync: Mapped[bool] = mapped_column(default=True)
    script_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    product_info: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Generated data
    generated_script: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tts_audio_duration_sec: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Result
    result_video_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_blob_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_video_size_mb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Temp file path (only valid on the same instance)
    result_video_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # GPU worker reference
    face_swap_job_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
