# app/models/orm/video.py
from sqlalchemy import ForeignKey, Text, Integer, String, Float, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from app.models.orm.base import Base, UUIDMixin, TimestampMixin
from typing import Optional
from datetime import datetime


class Video(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "videos"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))

    original_filename: Mapped[str | None]

    status: Mapped[str]

    # Intra-step progress (0-100) for real-time progress display
    step_progress: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=0, server_default="0")

    # Upload type: 'screen_recording' (default) or 'clean_video'
    upload_type: Mapped[str] = mapped_column(
        String(50), default="screen_recording", server_default="screen_recording"
    )

    # Excel file blob URLs (only for clean_video uploads)
    excel_product_blob_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    excel_trend_blob_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Total video duration in seconds
    duration: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Compressed preview video blob path (e.g., email/video_id/video_id_preview.mp4)
    compressed_blob_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Cached top 2 product names by GMV as JSON string
    top_products: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Time offset in seconds: where this video starts within the CSV timeline
    # Used when a long stream is split into multiple videos sharing the same CSV
    time_offset_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True, default=0)

    # --- Enqueue evidence (Improvement 1) ---
    queue_enqueued_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    queue_message_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    enqueue_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    enqueue_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- Worker claimed evidence (Improvement 2) ---
    worker_claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    worker_instance_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    dequeue_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Language for AI analysis output (ja, zh-TW, etc.)
    language: Mapped[Optional[str]] = mapped_column(String(10), nullable=True, default="ja", server_default="ja")

    # Real-time processing logs (JSONB array of log entries for live UI display)
    processing_logs: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
