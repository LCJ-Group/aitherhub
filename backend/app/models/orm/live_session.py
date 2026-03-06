# app/models/orm/live_session.py
"""
Persistent live session model.
Stores both worker-based live_capture sessions and Chrome extension sessions.
In-memory caches (metrics, SSE events) remain in live_event_service for
real-time performance; this table provides persistence across restarts.

Note: Extension-specific 2-screen session tracking has been moved to
ext_sessions table (see extension_events.py).
"""
from sqlalchemy import Integer, String, Text, Boolean, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from app.models.orm.base import Base, TimestampMixin
from typing import Optional
from datetime import datetime


class LiveSession(Base, TimestampMixin):
    __tablename__ = "live_sessions"

    # Primary key: the video_id used throughout the system
    # For worker sessions: UUID (e.g., "31e81697-04a5-...")
    # For extension sessions: "ext_{user_id}_{room_id}"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    video_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)

    user_id: Mapped[int] = mapped_column(Integer, index=True)

    # Session type: 'live_capture' (worker-based) or 'extension' (Chrome ext)
    session_type: Mapped[str] = mapped_column(String(50))

    # Whether the session is currently active
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    # TikTok account username (e.g., "kyogokuprofessional")
    account: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Live URL (for worker-based sessions)
    live_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Source: 'streamer' or 'workbench' (for extension sessions)
    source: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # TikTok room ID
    room_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Region
    region: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Extension session UUID (for extension sessions only)
    ext_session_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Stream info as JSON (stream_url, username, etc.)
    stream_info: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Latest metrics snapshot as JSON
    latest_metrics: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Session start time
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now()
    )

    # Session end time
    ended_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
