"""Add missing columns to videos and video_phases tables.

Fixes 500 errors on Sales Moment Clips, Hook Detection, and Moment Clips.

- videos.duration: total video duration in seconds
- video_phases.audio_text: transcribed audio text per phase
- video_phases.gmv: GMV value per phase
- video_phases.order_count: order count per phase
- video_phases.viewer_count: viewer count per phase
- video_phases.product_clicks: product click count per phase

Revision ID: 20260309_missing_cols
Revises: 20260308_clip_segments
Create Date: 2026-03-09
"""
from alembic import op
import sqlalchemy as sa

revision = "20260309_missing_cols"
down_revision = "20260308_clip_segments"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Add duration column to videos table
    op.add_column(
        "videos",
        sa.Column("duration", sa.Float(), nullable=True,
                  comment="Total video duration in seconds"),
    )

    # 2. Add audio_text column to video_phases table
    op.add_column(
        "video_phases",
        sa.Column("audio_text", sa.Text(), nullable=True,
                  comment="Transcribed audio text for this phase"),
    )

    # 3. Add sales metric columns to video_phases table
    op.add_column(
        "video_phases",
        sa.Column("gmv", sa.Float(), nullable=True, server_default="0",
                  comment="GMV value during this phase"),
    )
    op.add_column(
        "video_phases",
        sa.Column("order_count", sa.Integer(), nullable=True, server_default="0",
                  comment="Order count during this phase"),
    )
    op.add_column(
        "video_phases",
        sa.Column("viewer_count", sa.Integer(), nullable=True, server_default="0",
                  comment="Viewer count during this phase"),
    )
    op.add_column(
        "video_phases",
        sa.Column("product_clicks", sa.Integer(), nullable=True, server_default="0",
                  comment="Product click count during this phase"),
    )

    # 4. Backfill videos.duration from video_phases max(time_end)
    op.execute("""
        UPDATE videos v
        SET duration = sub.max_end
        FROM (
            SELECT video_id, MAX(time_end) as max_end
            FROM video_phases
            WHERE time_end IS NOT NULL
            GROUP BY video_id
        ) sub
        WHERE v.id = sub.video_id
          AND v.duration IS NULL
    """)


def downgrade():
    op.drop_column("video_phases", "product_clicks")
    op.drop_column("video_phases", "viewer_count")
    op.drop_column("video_phases", "order_count")
    op.drop_column("video_phases", "gmv")
    op.drop_column("video_phases", "audio_text")
    op.drop_column("videos", "duration")
