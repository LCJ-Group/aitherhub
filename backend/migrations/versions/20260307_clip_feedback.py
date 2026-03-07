"""create clip_feedback table for AI learning (adopt/reject feedback)

Revision ID: 20260307_clip_feedback
Revises: 20260307_recreate_reports
Create Date: 2026-03-07 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260307_clip_feedback"
down_revision = "20260307_recreate_reports"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "clip_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # Reference to video_clips (nullable: feedback can be on AI candidates before clip is generated)
        sa.Column("clip_id", postgresql.UUID(as_uuid=True), nullable=True),
        # Reference to video
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Phase index (matches video_phases.phase_index)
        sa.Column("phase_index", sa.Integer(), nullable=False),
        # Time range of the candidate clip
        sa.Column("time_start", sa.Float(), nullable=False),
        sa.Column("time_end", sa.Float(), nullable=False),
        # Feedback: 'adopted' | 'rejected'
        sa.Column("feedback", sa.String(20), nullable=False),
        # Optional: where was it posted (tiktok, reels, youtube_shorts, etc.)
        sa.Column("posted_platform", sa.String(50), nullable=True),
        # Optional: actual performance after posting
        sa.Column("actual_views", sa.BigInteger(), nullable=True),
        sa.Column("actual_sales", sa.Float(), nullable=True),
        # Reviewer info
        sa.Column("reviewer_name", sa.String(100), nullable=True),
        # AI score at the time of feedback (for learning)
        sa.Column("ai_score_at_feedback", sa.Float(), nullable=True),
        # Score breakdown JSON (for learning)
        sa.Column("score_breakdown", postgresql.JSONB(), nullable=True),
        # Reasons JSON (for learning)
        sa.Column("ai_reasons_at_feedback", postgresql.JSONB(), nullable=True),
        # Timestamps
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_index("ix_clip_feedback_video_id", "clip_feedback", ["video_id"])
    op.create_index("ix_clip_feedback_clip_id", "clip_feedback", ["clip_id"])
    op.create_index("ix_clip_feedback_feedback", "clip_feedback", ["feedback"])
    op.create_index(
        "ix_clip_feedback_video_phase",
        "clip_feedback",
        ["video_id", "phase_index"],
    )


def downgrade():
    op.drop_index("ix_clip_feedback_video_phase", table_name="clip_feedback")
    op.drop_index("ix_clip_feedback_feedback", table_name="clip_feedback")
    op.drop_index("ix_clip_feedback_clip_id", table_name="clip_feedback")
    op.drop_index("ix_clip_feedback_video_id", table_name="clip_feedback")
    op.drop_table("clip_feedback")
