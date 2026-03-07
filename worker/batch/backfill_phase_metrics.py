"""
backfill_phase_metrics.py
=========================
既存動画の video_phases テーブルに保存されている CSV 由来の指標を、
最新ロジックで再計算して上書きする CLI ツール。

内部処理は app/services/phase_metrics_recalculator.py に委譲する。

【実行方法】
  # 全動画を再集計
  python backfill_phase_metrics.py

  # 特定動画のみ
  python backfill_phase_metrics.py --video-id <uuid>

  # ドライラン（DBを更新しない、ログのみ）
  python backfill_phase_metrics.py --dry-run

  # デバッグ（詳細ログ出力）
  python backfill_phase_metrics.py --video-id <uuid> --debug

【デプロイ先での実行】
  cd /var/www/aitherhub/worker/batch
  python backfill_phase_metrics.py --video-id <uuid> --debug
"""

import argparse
import asyncio
import json
import os
import sys
import traceback

# ── Path setup ────────────────────────────────────────────────────────────────
# Add both worker/batch and backend/app to sys.path so we can import from both.
WORKER_BATCH_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(WORKER_BATCH_DIR, "..", ".."))
BACKEND_DIR = os.path.join(PROJECT_ROOT, "backend")

sys.path.insert(0, WORKER_BATCH_DIR)
sys.path.insert(0, BACKEND_DIR)
sys.path.insert(0, os.path.join(BACKEND_DIR, "app"))

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
load_dotenv()

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text as sa_text

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

# Handle sslmode for asyncpg
import ssl
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

def _prepare_db_url(url: str):
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)
    connect_args = {}
    if "sslmode" in query_params:
        sslmode = query_params["sslmode"][0]
        del query_params["sslmode"]
        if sslmode == "require":
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            connect_args["ssl"] = ctx
    new_query = urlencode(query_params, doseq=True)
    cleaned = urlunparse((parsed.scheme, parsed.netloc, parsed.path,
                          parsed.params, new_query, parsed.fragment))
    return cleaned, connect_args

cleaned_url, connect_args = _prepare_db_url(DATABASE_URL)
engine = create_async_engine(cleaned_url, pool_pre_ping=True, echo=False,
                             connect_args=connect_args)
AsyncSessionLocal = sessionmaker(bind=engine, class_=AsyncSession,
                                 expire_on_commit=False)


# ── SQL ───────────────────────────────────────────────────────────────────────

SQL_VIDEOS = """
    SELECT v.id, v.original_filename, v.upload_type,
           v.excel_trend_blob_url, v.time_offset_seconds
    FROM videos v
    WHERE v.status IN ('completed', 'DONE')
      AND v.upload_type = 'clean_video'
      AND v.excel_trend_blob_url IS NOT NULL
      AND LENGTH(v.excel_trend_blob_url) > 5
      AND v.id IN (SELECT DISTINCT video_id FROM video_phases)
    ORDER BY v.created_at DESC
"""


# ── Main ──────────────────────────────────────────────────────────────────────

async def backfill(video_id_filter: str | None, dry_run: bool, debug: bool):
    """Run backfill using the service layer."""

    # Import the service
    try:
        sys.path.insert(0, os.path.join(BACKEND_DIR, "app"))
        from services.phase_metrics_recalculator import (
            recalculate_phase_metrics,
            PHASE_METRICS_LOGIC_VERSION,
        )
    except ImportError:
        # Fallback: try direct import
        try:
            from app.services.phase_metrics_recalculator import (
                recalculate_phase_metrics,
                PHASE_METRICS_LOGIC_VERSION,
            )
        except ImportError as e:
            print(f"[ERROR] Cannot import recalculator service: {e}")
            print("  Falling back to standalone mode...")
            await _backfill_standalone(video_id_filter, dry_run, debug)
            return

    print(f"\n[backfill_phase_metrics] Logic version: {PHASE_METRICS_LOGIC_VERSION}")

    # Ensure migration tables exist
    async with AsyncSessionLocal() as session:
        try:
            await session.execute(sa_text("""
                CREATE TABLE IF NOT EXISTS phase_metrics_recalc_log (
                    id BIGSERIAL PRIMARY KEY,
                    video_id VARCHAR(255) NOT NULL,
                    triggered_by VARCHAR(255),
                    mode VARCHAR(20) NOT NULL DEFAULT 'dry-run',
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    logic_version INTEGER NOT NULL DEFAULT 1,
                    before_json JSONB, after_json JSONB,
                    diff_json JSONB, logs_json JSONB,
                    error_message TEXT, duration_ms INTEGER,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """))
            await session.execute(sa_text(
                "ALTER TABLE video_phases ADD COLUMN IF NOT EXISTS "
                "phase_metrics_version_applied INTEGER DEFAULT NULL"
            ))
            await session.execute(sa_text(
                "ALTER TABLE videos ADD COLUMN IF NOT EXISTS "
                "phase_metrics_version_applied INTEGER DEFAULT NULL"
            ))
            await session.execute(sa_text(
                "ALTER TABLE videos ADD COLUMN IF NOT EXISTS "
                "last_recalculated_at TIMESTAMPTZ DEFAULT NULL"
            ))
            await session.commit()
        except Exception as e:
            print(f"[WARN] Migration check: {e}")
            await session.rollback()

    # Get target videos
    async with AsyncSessionLocal() as session:
        if video_id_filter:
            r = await session.execute(
                sa_text(SQL_VIDEOS + " AND v.id = :vid"),
                {"vid": video_id_filter},
            )
        else:
            r = await session.execute(sa_text(SQL_VIDEOS))
        videos = r.fetchall()

    print(f"[backfill_phase_metrics] Found {len(videos)} videos to process")
    if dry_run:
        print("[DRY RUN] No DB updates will be made\n")

    success = 0
    skipped = 0
    errors = 0

    for row in videos:
        vid = str(row[0])
        fname = str(row[1]) if row[1] else "?"

        print(f"\n{'='*60}")
        print(f"[{vid[:8]}] {fname}")

        async with AsyncSessionLocal() as session:
            result = await recalculate_phase_metrics(
                video_id=vid,
                db=session,
                dry_run=dry_run,
                triggered_by="cli:backfill",
            )

        if result["status"] == "success":
            success += 1
            diff = result.get("diff", {})
            print(f"  [OK] Phases changed: {diff.get('phases_changed', '?')}")
            print(f"       GMV: {result.get('before_summary', {}).get('total_gmv', 0):.0f} "
                  f"-> {result.get('after_summary', {}).get('total_gmv', 0):.0f}")
            print(f"       Orders: {result.get('before_summary', {}).get('total_orders', 0)} "
                  f"-> {result.get('after_summary', {}).get('total_orders', 0)}")
            if debug:
                for log_line in result.get("logs", []):
                    print(f"  {log_line}")
        else:
            error_logs = [l for l in result.get("logs", []) if "ERROR" in l]
            if error_logs:
                print(f"  [ERROR] {error_logs[0]}")
                errors += 1
            else:
                print(f"  [SKIP] {result.get('logs', ['Unknown'])[-1]}")
                skipped += 1

    print(f"\n{'='*60}")
    print(f"[backfill_phase_metrics] DONE: success={success} skipped={skipped} errors={errors}")
    if dry_run:
        print("[DRY RUN] No DB was modified")


async def _backfill_standalone(video_id_filter, dry_run, debug):
    """Standalone fallback when service import fails (legacy mode)."""
    print("[WARN] Running in standalone mode (service import failed)")
    print("[WARN] Please ensure backend/app is accessible for full functionality")

    from excel_parser import load_excel_data

    # Import compute function from the service if possible, else use local
    try:
        from services.phase_metrics_recalculator import compute_phase_metrics
    except ImportError:
        print("[ERROR] Cannot import compute_phase_metrics. Aborting.")
        return

    async with AsyncSessionLocal() as session:
        if video_id_filter:
            r = await session.execute(
                sa_text(SQL_VIDEOS + " AND v.id = :vid"),
                {"vid": video_id_filter},
            )
        else:
            r = await session.execute(sa_text(SQL_VIDEOS))
        videos = r.fetchall()

    print(f"Found {len(videos)} videos")

    for row in videos:
        vid = str(row[0])
        fname = str(row[1]) if row[1] else "?"
        trend_url = str(row[3]) if row[3] else None
        time_offset = float(row[4]) if row[4] else 0.0

        print(f"\n[{vid[:8]}] {fname}")

        if not trend_url:
            print("  [SKIP] No trend URL")
            continue

        try:
            excel_data = load_excel_data(vid, {
                "excel_trend_blob_url": trend_url,
                "excel_product_blob_url": None,
                "upload_type": "clean_video",
                "time_offset_seconds": time_offset,
            })
            if not excel_data or not excel_data.get("has_trend_data"):
                print("  [SKIP] No trend data")
                continue

            async with AsyncSessionLocal() as session:
                r = await session.execute(
                    sa_text("SELECT phase_index, time_start, time_end "
                            "FROM video_phases WHERE video_id = :vid ORDER BY phase_index"),
                    {"vid": vid},
                )
                phase_rows = r.fetchall()

            phases = [{"phase_index": p[0], "time_start": p[1], "time_end": p[2]}
                      for p in phase_rows]

            metrics, logs = compute_phase_metrics(
                trends=excel_data["trends"],
                phases=phases,
                time_offset_seconds=time_offset,
            )

            if debug:
                for line in logs:
                    print(f"  {line}")

            if not dry_run and metrics:
                async with AsyncSessionLocal() as session:
                    for m in metrics:
                        await session.execute(sa_text("""
                            UPDATE video_phases SET
                                gmv=:gmv, order_count=:order_count,
                                viewer_count=:viewer_count, like_count=:like_count,
                                comment_count=:comment_count, share_count=:share_count,
                                new_followers=:new_followers, product_clicks=:product_clicks,
                                conversion_rate=:conversion_rate, gpm=:gpm,
                                importance_score=:importance_score, updated_at=now()
                            WHERE video_id=:vid AND phase_index=:pi
                        """), {"vid": vid, "pi": m["phase_index"], **m})
                    await session.commit()
                print(f"  [OK] Updated {len(metrics)} phases")
            else:
                print(f"  [DRY RUN] Would update {len(metrics)} phases")

        except Exception as e:
            print(f"  [ERROR] {e}")
            traceback.print_exc()


def main():
    parser = argparse.ArgumentParser(
        description="Backfill phase CSV metrics for existing videos"
    )
    parser.add_argument("--video-id", help="Process only this video ID")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be updated without modifying DB")
    parser.add_argument("--debug", action="store_true",
                        help="Print detailed logs for each video")
    args = parser.parse_args()

    asyncio.run(backfill(
        video_id_filter=args.video_id,
        dry_run=args.dry_run,
        debug=args.debug,
    ))


if __name__ == "__main__":
    main()
