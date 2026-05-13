"""Create video_performance table for TikTok screenshot OCR data

Revision ID: 20260513_video_perf
Revises: 20260416_video_lang
Create Date: 2026-05-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260513_video_perf"
down_revision = "20260416_video_lang"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "video_performance",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False,
                  comment="References videos.id"),
        sa.Column("platform", sa.String(50), nullable=False, server_default="tiktok",
                  comment="Platform: tiktok, instagram, youtube, etc."),
        # Core metrics from OCR
        sa.Column("views", sa.BigInteger(), nullable=True),
        sa.Column("likes", sa.BigInteger(), nullable=True),
        sa.Column("comments", sa.BigInteger(), nullable=True),
        sa.Column("shares", sa.BigInteger(), nullable=True),
        sa.Column("saves", sa.BigInteger(), nullable=True),
        sa.Column("purchases", sa.BigInteger(), nullable=True),
        sa.Column("revenue", sa.Float(), nullable=True, comment="Revenue in JPY"),
        # Computed rates
        sa.Column("engagement_rate", sa.Float(), nullable=True,
                  comment="(likes+comments+shares)/views"),
        sa.Column("conversion_rate", sa.Float(), nullable=True,
                  comment="purchases/views"),
        # Retention data (from insight screenshot)
        sa.Column("avg_watch_time_seconds", sa.Float(), nullable=True),
        sa.Column("retention_curve", postgresql.JSONB(), nullable=True,
                  comment="Array of {second: N, retention_pct: X}"),
        # Matching metadata
        sa.Column("matched_by", sa.String(100), nullable=True,
                  comment="How this was matched: caption, date, manual, etc."),
        sa.Column("match_confidence", sa.Float(), nullable=True,
                  comment="0.0-1.0 confidence of auto-match"),
        sa.Column("tiktok_video_id", sa.String(100), nullable=True,
                  comment="TikTok native video ID if available"),
        sa.Column("caption", sa.Text(), nullable=True,
                  comment="Caption extracted from screenshot"),
        sa.Column("hashtags", postgresql.JSONB(), nullable=True,
                  comment="Array of hashtags"),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True,
                  comment="When the video was posted on TikTok"),
        # Screenshot source
        sa.Column("screenshot_url", sa.Text(), nullable=True,
                  comment="URL of the uploaded screenshot"),
        sa.Column("ocr_raw", postgresql.JSONB(), nullable=True,
                  comment="Raw OCR extraction result for debugging"),
        # Timestamps
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()"),
                  comment="When this performance data was recorded"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )

    # Indexes
    op.create_index("ix_video_performance_video_id", "video_performance", ["video_id"])
    op.create_index("ix_video_performance_recorded_at", "video_performance", ["recorded_at"])
    op.create_index("ix_video_performance_platform", "video_performance", ["platform"])


def downgrade():
    op.drop_index("ix_video_performance_platform")
    op.drop_index("ix_video_performance_recorded_at")
    op.drop_index("ix_video_performance_video_id")
    op.drop_table("video_performance")
