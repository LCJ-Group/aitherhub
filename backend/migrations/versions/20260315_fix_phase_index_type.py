"""Fix phase_index column type from INTEGER to TEXT in clip_feedback and sales_confirmation.

Moment clips use string IDs like 'moment_strong_test4' which cannot be cast to INTEGER.
This migration changes the column type to TEXT to support both numeric and string phase indices.

Revision ID: 20260315_fix_phase_index
Revises: 20260310_feedback_cols
Create Date: 2026-03-15
"""
from alembic import op

revision = "20260315_fix_phase_index"
down_revision = "20260310_feedback_cols"
branch_labels = None
depends_on = None


def upgrade():
    # Fix clip_feedback.phase_index: INTEGER → TEXT
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'clip_feedback'
                  AND column_name = 'phase_index'
                  AND data_type = 'integer'
            ) THEN
                ALTER TABLE clip_feedback DROP CONSTRAINT IF EXISTS uq_clip_feedback_video_phase;
                ALTER TABLE clip_feedback ALTER COLUMN phase_index TYPE TEXT USING phase_index::TEXT;
                ALTER TABLE clip_feedback ADD CONSTRAINT uq_clip_feedback_video_phase UNIQUE (video_id, phase_index);
            END IF;
        END $$;
    """)

    # Fix sales_confirmation.phase_index: INTEGER → TEXT
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'sales_confirmation'
                  AND column_name = 'phase_index'
                  AND data_type = 'integer'
            ) THEN
                ALTER TABLE sales_confirmation DROP CONSTRAINT IF EXISTS uq_sales_confirmation_video_phase;
                ALTER TABLE sales_confirmation ALTER COLUMN phase_index TYPE TEXT USING phase_index::TEXT;
                ALTER TABLE sales_confirmation ADD CONSTRAINT uq_sales_confirmation_video_phase UNIQUE (video_id, phase_index);
            END IF;
        END $$;
    """)


def downgrade():
    # Revert clip_feedback.phase_index: TEXT → INTEGER
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'clip_feedback'
                  AND column_name = 'phase_index'
                  AND data_type = 'text'
            ) THEN
                ALTER TABLE clip_feedback DROP CONSTRAINT IF EXISTS uq_clip_feedback_video_phase;
                -- This will fail if non-numeric values exist
                ALTER TABLE clip_feedback ALTER COLUMN phase_index TYPE INTEGER USING phase_index::INTEGER;
                ALTER TABLE clip_feedback ADD CONSTRAINT uq_clip_feedback_video_phase UNIQUE (video_id, phase_index);
            END IF;
        END $$;
    """)

    # Revert sales_confirmation.phase_index: TEXT → INTEGER
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'sales_confirmation'
                  AND column_name = 'phase_index'
                  AND data_type = 'text'
            ) THEN
                ALTER TABLE sales_confirmation DROP CONSTRAINT IF EXISTS uq_sales_confirmation_video_phase;
                ALTER TABLE sales_confirmation ALTER COLUMN phase_index TYPE INTEGER USING phase_index::INTEGER;
                ALTER TABLE sales_confirmation ADD CONSTRAINT uq_sales_confirmation_video_phase UNIQUE (video_id, phase_index);
            END IF;
        END $$;
    """)
