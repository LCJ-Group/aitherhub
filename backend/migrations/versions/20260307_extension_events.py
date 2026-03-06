"""Add extension Commerce Reaction DB tables

Creates 6 new tables for Chrome extension MVP:
- ext_sessions: Extension session tracking (2-screen binding)
- raw_events: Immutable event log
- product_snapshots: Dashboard product table snapshots (funnel)
- trend_snapshots: 5-minute bucket time-series
- ext_sales_moments: Commerce reaction moments
- moment_event_links: Links between moments and events

Revision ID: 20260307_ext_events
Revises: 20260306_source_moments
Create Date: 2026-03-07 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260307_ext_events"
down_revision = "20260306_source_moments"
branch_labels = None
depends_on = None


def upgrade():
    # ── ext_sessions ──
    op.create_table(
        "ext_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("platform", sa.String(length=50), nullable=False, server_default="tiktok_live"),
        sa.Column("creator_id", sa.String(length=255), nullable=True),
        sa.Column("live_url", sa.Text(), nullable=True),
        sa.Column("dashboard_url", sa.Text(), nullable=True),
        sa.Column("live_tab_id", sa.Integer(), nullable=True),
        sa.Column("dashboard_tab_id", sa.Integer(), nullable=True),
        sa.Column("live_title", sa.String(length=500), nullable=True),
        sa.Column("room_id", sa.String(length=255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_offset_ms", sa.Integer(), server_default="0"),
        sa.Column("status", sa.String(length=30), server_default="waiting_live"),
        sa.Column("live_session_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ext_sessions_creator_id", "ext_sessions", ["creator_id"])
    op.create_index("ix_ext_sessions_status", "ext_sessions", ["status"])

    # ── raw_events ──
    op.create_table(
        "raw_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("captured_at_client", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "captured_at_server",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column("video_sec", sa.Integer(), nullable=True),
        sa.Column("product_id", sa.String(length=255), nullable=True),
        sa.Column("numeric_value", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("text_value", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("confidence_score", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_raw_events_session_id", "raw_events", ["session_id"])
    op.create_index("ix_raw_events_event_type", "raw_events", ["event_type"])
    op.create_index(
        "ix_raw_events_session_time", "raw_events", ["session_id", "captured_at_client"]
    )
    op.create_index(
        "ix_raw_events_session_type", "raw_events", ["session_id", "event_type"]
    )

    # ── product_snapshots ──
    op.create_table(
        "product_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("product_id", sa.String(length=255), nullable=False),
        sa.Column("product_name", sa.Text(), nullable=True),
        sa.Column("gmv", sa.Numeric(precision=18, scale=2), server_default="0"),
        sa.Column("sales_count", sa.Integer(), server_default="0"),
        sa.Column("add_to_cart_count", sa.Integer(), server_default="0"),
        sa.Column("click_count", sa.Integer(), server_default="0"),
        sa.Column("impression_count", sa.Integer(), server_default="0"),
        sa.Column("ctr", sa.Numeric(precision=8, scale=6), nullable=True),
        sa.Column("snapshot_seq", sa.Integer(), nullable=True),
        sa.Column("is_visible_on_page", sa.Boolean(), nullable=True),
        sa.Column("page_index", sa.Integer(), nullable=True),
        sa.Column("rank_on_table", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_product_snapshots_session_id", "product_snapshots", ["session_id"])
    op.create_index(
        "ix_product_snapshots_session_time",
        "product_snapshots",
        ["session_id", "captured_at"],
    )
    op.create_index(
        "ix_product_snapshots_session_product",
        "product_snapshots",
        ["session_id", "product_id"],
    )

    # ── trend_snapshots ──
    op.create_table(
        "trend_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metric_type", sa.String(length=50), nullable=False),
        sa.Column("bucket_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bucket_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metric_value", sa.Numeric(precision=18, scale=2), server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trend_snapshots_session_id", "trend_snapshots", ["session_id"])
    op.create_index(
        "ix_trend_snapshots_session_metric",
        "trend_snapshots",
        ["session_id", "metric_type"],
    )
    op.create_index(
        "ix_trend_snapshots_session_bucket",
        "trend_snapshots",
        ["session_id", "bucket_start_at"],
    )

    # ── ext_sales_moments ──
    op.create_table(
        "ext_sales_moments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("moment_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("moment_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("video_sec_start", sa.Integer(), nullable=True),
        sa.Column("video_sec_end", sa.Integer(), nullable=True),
        sa.Column("primary_product_id", sa.String(length=255), nullable=True),
        # Funnel 4-layer deltas
        sa.Column("click_delta", sa.Integer(), server_default="0"),
        sa.Column("cart_delta", sa.Integer(), server_default="0"),
        sa.Column("sales_delta", sa.Integer(), server_default="0"),
        sa.Column("gmv_delta", sa.Numeric(precision=18, scale=2), server_default="0"),
        # Engagement deltas
        sa.Column("comment_delta", sa.Integer(), server_default="0"),
        sa.Column("viewer_delta", sa.Integer(), server_default="0"),
        # Score and classification
        sa.Column("strength_score", sa.Numeric(precision=5, scale=4), server_default="0"),
        sa.Column("moment_type", sa.String(length=50), nullable=False),
        sa.Column("evidence_level", sa.String(length=30), server_default="estimated"),
        sa.Column("status", sa.String(length=20), server_default="candidate"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ext_sales_moments_session_id", "ext_sales_moments", ["session_id"]
    )
    op.create_index(
        "ix_ext_sales_moments_session_time",
        "ext_sales_moments",
        ["session_id", "moment_start_at"],
    )

    # ── moment_event_links ──
    op.create_table(
        "moment_event_links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("sales_moment_id", sa.String(length=36), nullable=False),
        sa.Column("raw_event_id", sa.String(length=36), nullable=False),
        sa.Column("relation_type", sa.String(length=30), nullable=False),
        sa.Column("time_distance_ms", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["sales_moment_id"], ["ext_sales_moments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["raw_event_id"], ["raw_events.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_moment_event_links_sales_moment_id",
        "moment_event_links",
        ["sales_moment_id"],
    )
    op.create_index(
        "ix_moment_event_links_raw_event_id",
        "moment_event_links",
        ["raw_event_id"],
    )
    op.create_index(
        "ix_moment_event_links_moment_event",
        "moment_event_links",
        ["sales_moment_id", "raw_event_id"],
        unique=True,
    )


def downgrade():
    op.drop_table("moment_event_links")
    op.drop_table("ext_sales_moments")
    op.drop_table("trend_snapshots")
    op.drop_table("product_snapshots")
    op.drop_table("raw_events")
    op.drop_table("ext_sessions")
