"""Create tiktok_tracked_videos and tiktok_performance_snapshots tables

Revision ID: 20260516_tiktok_tracking
"""
from alembic import op
import sqlalchemy as sa

revision = "20260516_tiktok_tracking"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # ── tiktok_tracked_videos: URL登録・追跡管理テーブル ──
    op.create_table(
        "tiktok_tracked_videos",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("tiktok_url", sa.Text(), nullable=False),
        sa.Column("tiktok_video_id", sa.String(64), nullable=True),
        sa.Column("account_name", sa.String(255), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("cover_url", sa.Text(), nullable=True),
        sa.Column("clip_db_id", sa.Text(), nullable=True),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), server_default="active", nullable=False),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_tiktok_tracked_videos_status", "tiktok_tracked_videos", ["status"])
    op.create_index("ix_tiktok_tracked_videos_tiktok_video_id", "tiktok_tracked_videos", ["tiktok_video_id"], unique=True)

    # ── tiktok_performance_snapshots: 定期取得したパフォーマンスデータ ──
    op.create_table(
        "tiktok_performance_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("tracked_video_id", sa.Integer(), sa.ForeignKey("tiktok_tracked_videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("play_count", sa.Integer(), server_default="0"),
        sa.Column("digg_count", sa.Integer(), server_default="0"),
        sa.Column("comment_count", sa.Integer(), server_default="0"),
        sa.Column("share_count", sa.Integer(), server_default="0"),
        sa.Column("collect_count", sa.Integer(), server_default="0"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_tiktok_snapshots_tracked_video_id", "tiktok_performance_snapshots", ["tracked_video_id"])
    op.create_index("ix_tiktok_snapshots_fetched_at", "tiktok_performance_snapshots", ["fetched_at"])


def downgrade():
    op.drop_index("ix_tiktok_snapshots_fetched_at")
    op.drop_index("ix_tiktok_snapshots_tracked_video_id")
    op.drop_table("tiktok_performance_snapshots")
    op.drop_index("ix_tiktok_tracked_videos_tiktok_video_id")
    op.drop_index("ix_tiktok_tracked_videos_status")
    op.drop_table("tiktok_tracked_videos")
