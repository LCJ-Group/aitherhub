"""Add frame_meta column to video_sales_moments

Revision ID: 20260308_frame_meta
Revises: 20260308_upload_event_log
Create Date: 2026-03-08
"""
from alembic import op
import sqlalchemy as sa

revision = "20260308_frame_meta"
down_revision = "20260308_upload_event_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add frame_meta JSON column to video_sales_moments
    op.add_column(
        "video_sales_moments",
        sa.Column("frame_meta", sa.Text(), nullable=True, comment="JSON: face_region, product_region, chat_messages for Auto Zoom / Chat Highlight"),
    )


def downgrade() -> None:
    op.drop_column("video_sales_moments", "frame_meta")
