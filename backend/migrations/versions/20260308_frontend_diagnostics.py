"""Add frontend_diagnostics table for error log collection

Revision ID: fe_diag_001
Revises: (auto)
Create Date: 2026-03-08
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "fe_diag_001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "frontend_diagnostics",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("video_id", sa.String(255), nullable=True, index=True),
        sa.Column("section_name", sa.String(100), nullable=False, index=True),
        sa.Column("endpoint", sa.String(500), nullable=True),
        sa.Column("error_type", sa.String(50), nullable=False, index=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("http_status", sa.Integer, nullable=True),
        sa.Column("request_id", sa.String(100), nullable=True, index=True),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column("page_url", sa.String(1000), nullable=True),
        sa.Column("user_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
    )


def downgrade():
    op.drop_table("frontend_diagnostics")
