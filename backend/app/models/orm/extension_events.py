# app/models/orm/extension_events.py
"""
Extension event sourcing models for Chrome extension MVP.

Tables:
- ext_sessions: Extension-specific session tracking (2-screen binding)
- raw_events: Immutable event log (event sourcing pattern)
- product_snapshots: Dashboard product table snapshots (diff-based funnel)
- trend_snapshots: 5-minute bucket time-series from dashboard trends
- ext_sales_moments: Detected commerce reaction moments (post-processing)
- moment_event_links: Links between sales_moments and raw_events
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Integer,
    String,
    Text,
    Float,
    Boolean,
    DateTime,
    JSON,
    Numeric,
    ForeignKey,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.orm.base import Base


# ── ext_sessions ───────────────────────────────────────────────────
class ExtSession(Base):
    """
    Extension-specific session for 2-screen binding.
    One session per live broadcast.
    """

    __tablename__ = "ext_sessions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # Platform: tiktok_live / shopee_live / other
    platform: Mapped[str] = mapped_column(String(50), nullable=False, default="tiktok_live")

    # Creator/account identifier
    creator_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)

    # URLs for the two screens
    live_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dashboard_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Tab IDs for tracking
    live_tab_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    dashboard_tab_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Live metadata
    live_title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    room_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Timestamps
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Time sync offset between live and dashboard screens (ms)
    sync_offset_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Status: waiting_live / waiting_dashboard / active / ended / error
    status: Mapped[str] = mapped_column(
        String(30), default="waiting_live", server_default="waiting_live"
    )

    # Link to existing live_sessions table (if applicable)
    live_session_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ── raw_events ──────────────────────────────────────────────────────
class RawEvent(Base):
    """
    Immutable event log. The most important table.
    Every DOM change, network event, OCR result, or manual marker
    is stored here as a raw event for later analysis.
    """

    __tablename__ = "raw_events"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    session_id: Mapped[str] = mapped_column(
        String(36), index=True, nullable=False
    )

    # Source of the event: live_dom / dashboard_dom / ocr / manual
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # Event type: viewer_count_snapshot, comment_added, product_switched,
    # dashboard_kpi_snapshot, product_metrics_snapshot, manual_marker_added, etc.
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # Timestamps (both client and server for sync)
    captured_at_client: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    captured_at_server: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now()
    )

    # Video timestamp (seconds from live start)
    video_sec: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Product reference
    product_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Numeric value (viewer count, GMV, sales count, etc.)
    numeric_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)

    # Text content (comment text, product name, notification text, etc.)
    text_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Full payload as JSON (for any additional structured data)
    payload_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Confidence score for OCR/detection results (0.0 - 1.0)
    confidence_score: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 4), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_raw_events_session_time", "session_id", "captured_at_client"),
        Index("ix_raw_events_session_type", "session_id", "event_type"),
    )


# ── product_snapshots ──────────────────────────────────────────────
class ProductSnapshot(Base):
    """
    Dashboard product table snapshots.
    Designed for diff-based funnel analysis:
    Exposure → Click → Cart → Sale
    """

    __tablename__ = "product_snapshots"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    session_id: Mapped[str] = mapped_column(
        String(36), index=True, nullable=False
    )

    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Product identification
    product_id: Mapped[str] = mapped_column(String(255), nullable=False)
    product_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Funnel metrics (cumulative at snapshot time)
    gmv: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    sales_count: Mapped[int] = mapped_column(Integer, default=0)
    add_to_cart_count: Mapped[int] = mapped_column(Integer, default=0)
    click_count: Mapped[int] = mapped_column(Integer, default=0)
    impression_count: Mapped[int] = mapped_column(Integer, default=0)
    ctr: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 6), nullable=True)

    # Table position metadata (for visibility tracking)
    snapshot_seq: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_visible_on_page: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    page_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rank_on_table: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_product_snapshots_session_time", "session_id", "captured_at"),
        Index("ix_product_snapshots_session_product", "session_id", "product_id"),
    )


# ── trend_snapshots ────────────────────────────────────────────────
class TrendSnapshot(Base):
    """
    5-minute bucket time-series from dashboard trend graphs.
    Not a graph image store — structured time-series buckets.
    metric_type examples: gmv, impressions, pin_event_count
    """

    __tablename__ = "trend_snapshots"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    session_id: Mapped[str] = mapped_column(
        String(36), index=True, nullable=False
    )

    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Metric identification
    metric_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # Bucket time range
    bucket_start_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    bucket_end_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Metric value for this bucket
    metric_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_trend_snapshots_session_metric", "session_id", "metric_type"),
        Index("ix_trend_snapshots_session_bucket", "session_id", "bucket_start_at"),
    )


# ── ext_sales_moments ──────────────────────────────────────────────
class ExtSalesMoment(Base):
    """
    Detected commerce reaction moments (post-processing result).
    This is a "reaction moment" table, not just sales.
    Covers the full funnel: click → cart → sales → gmv
    plus engagement signals: comment, viewer.
    """

    __tablename__ = "ext_sales_moments"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    session_id: Mapped[str] = mapped_column(
        String(36), index=True, nullable=False
    )

    # Moment time range
    moment_start_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    moment_end_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Video timestamp range (optional)
    video_sec_start: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    video_sec_end: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Primary product associated with this moment
    primary_product_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )

    # ── Funnel 4-layer deltas ──
    click_delta: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cart_delta: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    sales_delta: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    gmv_delta: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), default=0, server_default="0"
    )

    # ── Engagement deltas ──
    comment_delta: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    viewer_delta: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Composite strength score (0.0 - 1.0)
    # 0.30*click + 0.20*cart + 0.25*sales + 0.15*gmv + 0.05*comment + 0.05*viewer
    strength_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), default=0, server_default="0"
    )

    # Moment type: trigger / conversion / strong
    moment_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # Evidence level: estimated / confirmed_like
    evidence_level: Mapped[str] = mapped_column(
        String(30), default="estimated", server_default="estimated"
    )

    # Status: candidate / confirmed / rejected
    status: Mapped[str] = mapped_column(
        String(20), default="candidate", server_default="candidate"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_ext_sales_moments_session_time", "session_id", "moment_start_at"),
    )


# ── moment_event_links ─────────────────────────────────────────────
class MomentEventLink(Base):
    """
    Links between sales_moments and raw_events.
    Tracks which events contributed to or occurred around a moment.
    """

    __tablename__ = "moment_event_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    sales_moment_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("ext_sales_moments.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    raw_event_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("raw_events.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # Relation type: before / inside / after / trigger_candidate
    relation_type: Mapped[str] = mapped_column(String(30), nullable=False)

    # Time distance from moment start in milliseconds
    time_distance_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        Index(
            "ix_moment_event_links_moment_event",
            "sales_moment_id",
            "raw_event_id",
            unique=True,
        ),
    )
