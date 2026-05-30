"""Create liver_clone_settings table for persistent user settings

This replaces localStorage/IndexedDB-based storage with server-side persistence.
Settings are tied to user_id so they persist across browsers/devices.

Revision ID: 20260530_liver_clone_settings
"""
from alembic import op
import sqlalchemy as sa

revision = "20260530_liver_clone_settings"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # ── liver_clone_settings: per-user persistent settings ──
    op.create_table(
        "liver_clone_settings",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        # Voice settings
        sa.Column("voice_id", sa.String(255), nullable=True),
        sa.Column("voice_stability", sa.Float(), server_default="0.5", nullable=False),
        sa.Column("voice_similarity", sa.Float(), server_default="0.75", nullable=False),
        sa.Column("sts_enabled", sa.Boolean(), server_default="false", nullable=False),
        # Mode & quality settings
        sa.Column("mode", sa.String(50), server_default="hybrid", nullable=False),
        sa.Column("quality", sa.String(50), server_default="high", nullable=False),
        sa.Column("language", sa.String(10), server_default="ja", nullable=False),
        sa.Column("resolution", sa.String(10), server_default="720p", nullable=False),
        sa.Column("fps", sa.Integer(), server_default="30", nullable=False),
        # VAD settings
        sa.Column("vad_threshold", sa.Float(), server_default="0.3", nullable=False),
        sa.Column("silence_timeout", sa.Float(), server_default="5.0", nullable=False),
        # Saved voices (JSON array of {id, name} objects)
        sa.Column("saved_voices", sa.Text(), server_default="[]", nullable=False),
        # Saved faces (JSON array of {id, name, image_url} objects)
        sa.Column("saved_faces", sa.Text(), server_default="[]", nullable=False),
        # Saved products (JSON array of {id, name, script, image_url} objects)
        sa.Column("saved_products", sa.Text(), server_default="[]", nullable=False),
        # Active face (currently selected face URL for face swap)
        sa.Column("active_face_url", sa.Text(), nullable=True),
        # Timestamps
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    # One settings row per user
    op.create_index("ix_liver_clone_settings_user_id", "liver_clone_settings", ["user_id"], unique=True)


def downgrade():
    op.drop_index("ix_liver_clone_settings_user_id", table_name="liver_clone_settings")
    op.drop_table("liver_clone_settings")
