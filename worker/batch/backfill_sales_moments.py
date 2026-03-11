"""
backfill_sales_moments.py  –  既存動画のsales_momentsをDBに投入
==================================================================
Worker VM上で直接実行する。
ローカルの excel_data/{video_id}/trend_stats.xlsx から読み込む。

使い方:
  python backfill_sales_moments.py                    # 全動画
  python backfill_sales_moments.py --video-id abc-123 # 特定動画
  python backfill_sales_moments.py --limit 5          # 最初の5動画
"""

import argparse
import asyncio
import glob
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text as sa_text

from csv_slot_filter import detect_sales_moments
from db_ops import (
    ensure_sales_moments_table,
    bulk_insert_sales_moments,
)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True, echo=False)
AsyncSessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

# Local Excel data directory
EXCEL_DATA_DIR = os.path.join(os.path.dirname(__file__), "excel_data")


def find_local_trend_file(video_id: str) -> str | None:
    """Find local trend_stats.xlsx for a video."""
    path = os.path.join(EXCEL_DATA_DIR, video_id, "trend_stats.xlsx")
    if os.path.exists(path):
        return path
    # Also check for other naming patterns
    pattern = os.path.join(EXCEL_DATA_DIR, video_id, "*trend*.xlsx")
    matches = glob.glob(pattern)
    return matches[0] if matches else None


def parse_trend_excel_safe(file_path: str):
    """Try to parse trend data from Excel file. Returns a DataFrame."""
    import pandas as pd
    try:
        from excel_parser import parse_trend_excel
        data = parse_trend_excel(file_path)  # returns list[dict]
        if not data:
            return None
        return pd.DataFrame(data)
    except Exception as e:
        print(f"  [WARN] excel_parser failed: {e}")
        return None


async def backfill_all(video_id: str = None, limit: int = None):
    """Backfill sales_moments for existing videos."""

    # Ensure table exists
    try:
        await ensure_sales_moments_table()
        print("[backfill] video_sales_moments table ensured.")
    except Exception as e:
        print(f"[backfill] Table creation note: {e}")

    async with AsyncSessionLocal() as session:
        # Get completed videos
        sql = """
            SELECT id, original_filename, time_offset_seconds
            FROM videos
            WHERE status IN ('completed', 'DONE', 'ERROR')
        """
        if video_id:
            sql += f" AND id = '{video_id}'"
        sql += " ORDER BY created_at DESC"
        if limit:
            sql += f" LIMIT {limit}"

        result = await session.execute(sa_text(sql))
        videos = result.fetchall()
        print(f"[backfill] Found {len(videos)} completed videos.")

        # Check available local Excel files
        local_video_ids = set()
        if os.path.exists(EXCEL_DATA_DIR):
            local_video_ids = set(os.listdir(EXCEL_DATA_DIR))
        print(f"[backfill] Local excel_data dirs: {len(local_video_ids)}")

        success_count = 0
        skip_count = 0
        no_file_count = 0
        error_count = 0

        for v in videos:
            vid = str(v[0])
            filename = str(v[1]) if v[1] else "unknown"
            time_offset = float(v[2]) if v[2] else 0.0

            # Find local trend file
            trend_path = find_local_trend_file(vid)
            if not trend_path:
                no_file_count += 1
                continue

            print(f"\n[{vid[:8]}] {filename}")

            # Check if already has sales_moments
            try:
                check_sql = sa_text(
                    "SELECT COUNT(*) FROM video_sales_moments WHERE video_id = :vid"
                )
                check_result = await session.execute(check_sql, {"vid": vid})
                existing_count = check_result.scalar()
                if existing_count and existing_count > 0:
                    print(f"  Already has {existing_count} moments. Skipping.")
                    skip_count += 1
                    continue
            except Exception as _e:
                print(f"Suppressed: {_e}")

            try:
                trend_data = parse_trend_excel_safe(trend_path)
                if trend_data is None or trend_data.empty:
                    print(f"  [SKIP] No trend data parsed from {trend_path}")
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

                # Count by type
                type_counts = {}
                for m in moments:
                    t = m.get("moment_type", "?")
                    type_counts[t] = type_counts.get(t, 0) + 1
                print(f"  Detected {len(moments)} moments: {type_counts}")

                # Save to DB
                await bulk_insert_sales_moments(vid, moments)
                print(f"  Saved to DB")
                success_count += 1

            except Exception as e:
                print(f"  [ERROR] {e}")
                traceback.print_exc()
                error_count += 1

        print(f"\n{'='*60}")
        print(f"[backfill] DONE")
        print(f"  success:  {success_count}")
        print(f"  skip:     {skip_count}")
        print(f"  no_file:  {no_file_count}")
        print(f"  error:    {error_count}")
        print(f"  total:    {len(videos)}")


def main():
    parser = argparse.ArgumentParser(description="Backfill sales_moments")
    parser.add_argument("--video-id", default=None, help="Specific video ID")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of videos")
    args = parser.parse_args()

    asyncio.run(backfill_all(video_id=args.video_id, limit=args.limit))


if __name__ == "__main__":
    main()
