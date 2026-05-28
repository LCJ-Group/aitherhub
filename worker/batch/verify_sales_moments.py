"""
Sales Moments 検証スクリプト
1. video_sales_moments テーブルの存在確認
2. 既存動画でtrend_statsデータがある動画を特定
3. detect_sales_moments() を手動実行して結果を検証
4. DBに保存して API レスポンスを確認
"""
import asyncio
import os
import sys
import json
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("verify")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from db_ops import (
    AsyncSessionLocal, engine,
    ensure_sales_moments_table_sync,
    bulk_insert_sales_moments_sync,
    get_sales_moments_sync,
    run_sync,
)
from sqlalchemy import text


async def check_table_exists():
    """video_sales_moments テーブルの存在確認"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'video_sales_moments'
            )
        """))
        exists = result.scalar()
        logger.info(f"[TABLE] video_sales_moments exists: {exists}")
        return exists


async def get_videos_with_csv_data():
    """CSVデータ（trend_stats）がある動画を取得"""
    async with AsyncSessionLocal() as session:
        # video_phases テーブルから csv_metrics が NULL でない動画を探す
        result = await session.execute(text("""
            SELECT DISTINCT v.id, v.original_filename, v.status, v.created_at
            FROM videos v
            WHERE v.status = 'done'
            ORDER BY v.created_at DESC
            LIMIT 20
        """))
        rows = result.fetchall()
        videos = []
        for r in rows:
            videos.append({
                "id": str(r[0]),
                "filename": r[1],
                "status": r[2],
                "created_at": str(r[3]),
            })
        return videos


async def get_video_excel_urls(video_id: str):
    """動画のExcel/CSV URLを取得"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("""
            SELECT excel_url, excel_url_2
            FROM videos
            WHERE id = :video_id
        """), {"video_id": video_id})
        row = result.fetchone()
        if row:
            return {"excel_url": row[0], "excel_url_2": row[1]}
        return None


async def get_existing_sales_moments():
    """既にDBに保存されているsales_momentsを取得"""
    async with AsyncSessionLocal() as session:
        try:
            result = await session.execute(text("""
                SELECT video_id, COUNT(*) as cnt,
                       SUM(CASE WHEN moment_type = 'strong' THEN 1 ELSE 0 END) as strong_cnt,
                       SUM(CASE WHEN moment_type = 'click' THEN 1 ELSE 0 END) as click_cnt,
                       SUM(CASE WHEN moment_type = 'order' THEN 1 ELSE 0 END) as order_cnt
                FROM video_sales_moments
                GROUP BY video_id
                ORDER BY cnt DESC
            """))
            rows = result.fetchall()
            return [dict(r._mapping) for r in rows]
        except Exception as e:
            logger.warning(f"[QUERY] Failed: {e}")
            return []


async def get_video_csv_metrics(video_id: str):
    """動画のphaseに紐づくCSVメトリクスを取得"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("""
            SELECT phase_index, time_start, time_end, csv_metrics
            FROM video_phases
            WHERE video_id = :video_id
            ORDER BY phase_index
        """), {"video_id": video_id})
        rows = result.fetchall()
        return [dict(r._mapping) for r in rows]


async def get_sales_moments_detail(video_id: str):
    """特定動画のsales_momentsを詳細取得"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("""
            SELECT time_key, time_sec, video_sec, moment_type,
                   click_value, click_delta, click_sigma_score,
                   order_value, order_delta, gmv_value,
                   confidence, reasons
            FROM video_sales_moments
            WHERE video_id = :video_id
            ORDER BY video_sec ASC
        """), {"video_id": video_id})
        rows = result.fetchall()
        return [dict(r._mapping) for r in rows]


async def main():
    print("=" * 60)
    print("Sales Moments 検証")
    print("=" * 60)

    # 1. テーブル存在確認
    print("\n--- 1. テーブル存在確認 ---")
    exists = await check_table_exists()
    if not exists:
        print("テーブルが存在しません。作成します...")
        ensure_sales_moments_table_sync()
        exists = await check_table_exists()
        print(f"作成後: {exists}")

    # 2. 既存のsales_momentsデータ確認
    print("\n--- 2. 既存sales_momentsデータ ---")
    existing = await get_existing_sales_moments()
    if existing:
        print(f"  {len(existing)} 動画にsales_momentsデータあり:")
        for e in existing:
            print(f"    video_id={str(e['video_id'])[:8]}... "
                  f"total={e['cnt']} (strong={e['strong_cnt']}, "
                  f"click={e['click_cnt']}, order={e['order_cnt']})")
    else:
        print("  まだsales_momentsデータなし（新規動画処理時に自動生成されます）")

    # 3. CSVデータがある動画を確認
    print("\n--- 3. 完了済み動画一覧 ---")
    videos = await get_videos_with_csv_data()
    print(f"  {len(videos)} 動画が完了済み:")
    for v in videos[:10]:
        excel = await get_video_excel_urls(v["id"])
        has_csv = "✅" if (excel and (excel.get("excel_url") or excel.get("excel_url_2"))) else "❌"
        print(f"    {v['id'][:8]}... {v['filename'][:40]:40s} CSV={has_csv}")

    # 4. CSVデータがある動画で手動検証
    print("\n--- 4. 手動検証（CSVデータがある動画） ---")
    for v in videos[:10]:
        excel = await get_video_excel_urls(v["id"])
        if excel and (excel.get("excel_url") or excel.get("excel_url_2")):
            video_id = v["id"]
            print(f"\n  動画: {v['filename']}")
            print(f"  video_id: {video_id}")

            # sales_momentsがあるか確認
            moments = await get_sales_moments_detail(video_id)
            if moments:
                print(f"  sales_moments: {len(moments)} 件")
                for m in moments:
                    print(f"    {m['time_key']:>10s} | video_sec={m['video_sec']:>7.0f}s | "
                          f"type={m['moment_type']:>6s} | "
                          f"click={m['click_value']:>5.0f} (Δ{m['click_delta']:>+5.0f}, σ={m['click_sigma_score']:>5.1f}) | "
                          f"order={m['order_value']:>5.0f} (Δ{m['order_delta']:>+5.0f}) | "
                          f"conf={m['confidence']:.2f} | {m['reasons']}")
            else:
                print("  sales_moments: なし（まだ処理されていない）")

            # CSVメトリクスも確認
            csv_metrics = await get_video_csv_metrics(video_id)
            if csv_metrics:
                print(f"  phases with csv_metrics: {sum(1 for c in csv_metrics if c.get('csv_metrics'))} / {len(csv_metrics)}")

            break  # 最初の1動画だけ詳細表示

    # 5. サマリー
    print("\n" + "=" * 60)
    print("検証サマリー")
    print("=" * 60)
    if existing:
        total_moments = sum(e["cnt"] for e in existing)
        total_strong = sum(e["strong_cnt"] for e in existing)
        print(f"  動画数: {len(existing)}")
        print(f"  総moments: {total_moments}")
        print(f"  strong: {total_strong}")
        print(f"  → 教師データとして利用可能")
    else:
        print("  sales_momentsデータはまだありません。")
        print("  次に動画を処理（またはre-analyze）すると自動的に検出されます。")
        print("  手動テスト: 既存動画のtrend_statsを使って検出を試みます...")

    await engine.dispose()


if __name__ == "__main__":

    run_sync(main())
