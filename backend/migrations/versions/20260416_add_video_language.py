"""Add language column to videos table

Revision ID: 20260416_video_lang
Revises: 20260315_fix_phase_index
Create Date: 2026-04-16
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260416_video_lang"
down_revision = "20260315_fix_phase_index"
branch_labels = None
depends_on = None


def upgrade():
    # Add language column with default 'ja' for existing rows
    op.add_column(
        "videos",
        sa.Column("language", sa.String(10), nullable=True, server_default="ja"),
    )


def downgrade():
    op.drop_column("videos", "language")
