import asyncio, os, sys
sys.path.insert(0, "/var/www/aitherhub/worker/batch")
from dotenv import load_dotenv
load_dotenv("/var/www/aitherhub/.env")

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text as sa_text

from csv_slot_filter import detect_sales_moments
from db_ops import bulk_insert_sales_moments, ensure_sales_moments_table
from excel_parser import load_excel_data

engine = create_async_engine(os.getenv("DATABASE_URL"), pool_pre_ping=True)
AsyncSessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

SQL_QUERY = (
    "SELECT v.id, v.original_filename, v.upload_type, "
    "v.excel_trend_blob_url, v.time_offset_seconds "
    "FROM videos v "
    "WHERE v.status IN (:s1, :s2) "
    "AND v.id IN (SELECT DISTINCT video_id FROM video_phases) "
    "AND v.id NOT IN (SELECT DISTINCT video_id FROM video_sales_moments) "
    "AND v.excel_trend_blob_url IS NOT NULL "
    "AND LENGTH(v.excel_trend_blob_url) > 5 "
    "ORDER BY v.created_at DESC"
)

async def backfill():
    try:
        await ensure_sales_moments_table()
    except Exception as _e:
        print(f"Suppressed: {_e}")

    async with AsyncSessionLocal() as session:
        r = await session.execute(
            sa_text(SQL_QUERY),
            {"s1": "completed", "s2": "DONE"}
        )
        rows = r.fetchall()
        print(f"[backfill_v2] Found {len(rows)} videos with trend blob URLs")

        success = 0
        no_moments = 0
        errors = 0

        for row in rows:
            vid = str(row[0])
            fname = str(row[1]) if row[1] else "?"
            trend_url = str(row[3]) if row[3] else None
            time_offset = float(row[4]) if row[4] else 0.0

            if not trend_url:
                continue

            print(f"\n[{vid[:8]}] {fname}")

            try:
                excel_urls = {
                    "excel_trend_blob_url": trend_url,
                    "excel_product_blob_url": None,
                    "upload_type": "clean_video",
                    "time_offset_seconds": time_offset,
                }
                excel_data = load_excel_data(vid, excel_urls)

                if not excel_data or not excel_data.get("has_trend_data"):
                    print("  No trend data loaded")
                    no_moments += 1
                    continue

                trends = excel_data["trends"]
                print(f"  Trend data: {len(trends)} rows")

                moments = detect_sales_moments(
                    trends=trends,
                    time_offset_seconds=time_offset,
                )

                if not moments:
                    print("  No moments detected")
                    no_moments += 1
                    continue

                type_counts = {}
                for m in moments:
                    t = m.get("moment_type", "?")
                    type_counts[t] = type_counts.get(t, 0) + 1
                print(f"  Detected {len(moments)} moments: {type_counts}")

                await bulk_insert_sales_moments(vid, moments)
                print("  Saved to DB")
                success += 1

            except Exception as e:
                print(f"  [ERROR] {e}")
                errors += 1

        print("\n" + "=" * 60)
        print(f"[backfill_v2] DONE: success={success} no_moments={no_moments} errors={errors}")

    await engine.dispose()

asyncio.run(backfill())
