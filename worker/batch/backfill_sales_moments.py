"""
backfill_sales_moments.py  –  既存動画のsales_momentsをDBに投入
==================================================================
Worker VM上で直接実行する。
Azure App Service上のbackfill APIはworkerコードにアクセスできないため、
このスクリプトでWorker VM上から直接DBに書き込む。

使い方:
  python backfill_sales_moments.py                    # 全動画
  python backfill_sales_moments.py --video-id abc-123 # 特定動画
  python backfill_sales_moments.py --limit 5          # 最初の5動画
"""

import argparse
import asyncio
import os
import sys
import tempfile
import traceback

import requests as http_requests

sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text

from csv_slot_filter import detect_sales_moments
from db_ops import ensure_sales_moments_table_sync, bulk_insert_sales_moments_sync

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True, echo=False)
AsyncSessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


def parse_trend_excel_safe(file_path: str):
    """Try to parse trend data from Excel file."""
    try:
        from excel_parser import parse_trend_excel
        return parse_trend_excel(file_path)
    except Exception as e:
        print(f"  [WARN] excel_parser failed: {e}")
        return None


async def backfill_all(video_id: str = None, limit: int = None):
    """Backfill sales_moments for existing videos."""

    async with AsyncSessionLocal() as session:
        # Ensure table exists
        conn = await session.connection()
        raw_conn = await conn.get_raw_connection()
        sync_conn = raw_conn.driver_connection
        ensure_sales_moments_table_sync(sync_conn)
        print("[backfill] video_sales_moments table ensured.")

        # Get videos with excel_trend_blob_url
        sql = """
            SELECT id, filename, excel_trend_blob_url, time_offset_seconds
            FROM videos
            WHERE status = 'completed'
              AND excel_trend_blob_url IS NOT NULL
              AND excel_trend_blob_url != ''
        """
        if video_id:
            sql += f" AND id = '{video_id}'"
        sql += " ORDER BY created_at DESC"
        if limit:
            sql += f" LIMIT {limit}"

        result = await session.execute(text(sql))
        videos = result.fetchall()
        print(f"[backfill] Found {len(videos)} videos with trend data.")

        success_count = 0
        skip_count = 0
        error_count = 0

        for v in videos:
            vid = str(v.id)
            filename = v.filename or "unknown"
            excel_url = v.excel_trend_blob_url
            time_offset = float(v.time_offset_seconds) if v.time_offset_seconds else 0.0

            print(f"\n[{vid[:8]}] {filename}")

            # Check if already has sales_moments
            try:
                check_sql = text("SELECT COUNT(*) FROM video_sales_moments WHERE video_id = :vid")
                check_result = await session.execute(check_sql, {"vid": vid})
                existing_count = check_result.scalar()
                if existing_count and existing_count > 0:
                    print(f"  Already has {existing_count} moments. Skipping.")
                    skip_count += 1
                    continue
            except Exception:
                pass  # Table might not exist yet, continue

            # Download Excel
            try:
                resp = http_requests.get(excel_url, timeout=30)
                if resp.status_code != 200:
                    print(f"  [ERROR] Failed to download Excel: HTTP {resp.status_code}")
                    error_count += 1
                    continue
            except Exception as e:
                print(f"  [ERROR] Download failed: {e}")
                error_count += 1
                continue

            # Save to temp file and parse
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
                tmp.write(resp.content)
                tmp_path = tmp.name

            try:
                trend_data = parse_trend_excel_safe(tmp_path)
                if trend_data is None or trend_data.empty:
                    print(f"  [SKIP] No trend data parsed.")
                    skip_count += 1
                    continue

                print(f"  Trend data: {len(trend_data)} rows")

                # Detect sales moments
                moments = detect_sales_moments(
                    trends=trend_data,
                    time_offset_seconds=time_offset,
                )

                if not moments:
                    print(f"  No moments detected.")
                    skip_count += 1
                    continue

                print(f"  Detected {len(moments)} moments")

                # Save to DB
                conn2 = await session.connection()
                raw_conn2 = await conn2.get_raw_connection()
                sync_conn2 = raw_conn2.driver_connection
                bulk_insert_sales_moments_sync(sync_conn2, vid, moments)
                print(f"  ✅ Saved {len(moments)} moments to DB")
                success_count += 1

            except Exception as e:
                print(f"  [ERROR] Processing failed: {e}")
                traceback.print_exc()
                error_count += 1
            finally:
                os.unlink(tmp_path)

        print(f"\n{'='*60}")
        print(f"[backfill] DONE: success={success_count}, skip={skip_count}, error={error_count}")
        print(f"[backfill] Total videos processed: {len(videos)}")


def main():
    parser = argparse.ArgumentParser(description="Backfill sales_moments for existing videos")
    parser.add_argument("--video-id", default=None, help="Specific video ID")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of videos")
    args = parser.parse_args()

    asyncio.run(backfill_all(video_id=args.video_id, limit=args.limit))


if __name__ == "__main__":
    main()
