"""Add source and moment_type_detail columns to video_sales_moments.

Enables dual-source sales moment tracking:
  - source='csv'    -> from TikTok LIVE Analytics Excel (existing)
  - source='screen' -> from screen recording OCR/Vision (new)

Also adds moment_type_detail for finer-grained classification.

Revision ID: 20260306_source_moments
Revises: 20260305_reviewer_name
Create Date: 2026-03-06
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260306_source_moments"
down_revision = "20260305_reviewer_name"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Add source column (default='csv' for backward compat)
    op.execute("""
        ALTER TABLE video_sales_moments
        ADD COLUMN IF NOT EXISTS source VARCHAR(20) DEFAULT 'csv' NOT NULL
    """)

    # 2. Add moment_type_detail for finer classification
    op.execute("""
        ALTER TABLE video_sales_moments
        ADD COLUMN IF NOT EXISTS moment_type_detail VARCHAR(50)
    """)

    # 3. Index on source for filtered queries
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_vsm_source
        ON video_sales_moments (source)
    """)

    # 4. Composite index for dataset generation queries
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_vsm_video_source
        ON video_sales_moments (video_id, source)
    """)

    # 5. Backfill: set moment_type_detail = moment_type for existing rows
    op.execute("""
        UPDATE video_sales_moments
        SET moment_type_detail = moment_type
        WHERE moment_type_detail IS NULL
    """)


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_vsm_video_source")
    op.execute("DROP INDEX IF EXISTS ix_vsm_source")
    op.execute("ALTER TABLE video_sales_moments DROP COLUMN IF EXISTS moment_type_detail")
    op.execute("ALTER TABLE video_sales_moments DROP COLUMN IF EXISTS source")
