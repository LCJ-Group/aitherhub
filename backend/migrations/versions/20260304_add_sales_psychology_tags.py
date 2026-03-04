"""add sales_psychology_tags to video_phases

Revision ID: 20260304_sales_tags
Revises: 20260225_live_sessions
Create Date: 2026-03-04 15:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260304_sales_tags'
down_revision = '20260225_live_sessions'
branch_labels = None
depends_on = None


def upgrade():
    # Sales Psychology Tags: JSON array of tag strings
    # e.g. ["HOOK", "DEMONSTRATION", "CTA"]
    #
    # Valid tags (15 types):
    #   HOOK, EMPATHY, PROBLEM, EDUCATION, SOLUTION,
    #   DEMONSTRATION, COMPARISON, PROOF, TRUST, SOCIAL_PROOF,
    #   OBJECTION_HANDLING, URGENCY, LIMITED_OFFER, BONUS, CTA
    #
    # Multiple tags per phase. Stored as JSON text for MySQL compatibility.
    op.add_column(
        'video_phases',
        sa.Column('sales_psychology_tags', sa.Text(), nullable=True)
    )


def downgrade():
    op.drop_column('video_phases', 'sales_psychology_tags')
