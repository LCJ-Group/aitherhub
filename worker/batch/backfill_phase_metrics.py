"""
backfill_phase_metrics.py
=========================
既存動画の video_phases テーブルに保存されている CSV 由来の指標
（gmv, order_count, viewer_count, like_count, comment_count,
 share_count, new_followers, product_clicks, conversion_rate, gpm）
を、修正後のロジックで再計算して上書きする。

【なぜ必要か】
PR #114 以前に処理された動画は、古い phase_abs_start 計算
（start_sec + video_start_sec）で保存されたデータが DB に残っている。
UIは video_phases の集計済みカラムを直接表示するため、
コード修正だけでは既存データは変わらない。

【実行方法】
  # 全動画を再集計
  python backfill_phase_metrics.py

  # 特定動画のみ
  python backfill_phase_metrics.py --video-id <uuid>

  # ドライラン（DBを更新しない、ログのみ）
  python backfill_phase_metrics.py --dry-run

  # デバッグ（最初の5件のCSVイベントを手計算と突合）
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
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
load_dotenv()

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text as sa_text

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True, echo=False)
AsyncSessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

# ─────────────────────────────────────────────────────────────
# Helpers (copied from process_video.py to keep this standalone)
# ─────────────────────────────────────────────────────────────

def _safe_float(v):
    if v is None:
        return 0.0
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def _parse_time_to_seconds(val) -> float | None:
    """時刻文字列を秒数に変換（csv_slot_filter と同一ロジック）"""
    if val is None:
        return None
    if hasattr(val, "hour") and hasattr(val, "minute"):
        return val.hour * 3600 + val.minute * 60 + getattr(val, "second", 0)
    val_str = str(val).strip()
    try:
        return float(val_str)
    except (ValueError, TypeError):
        pass
    parts = val_str.split(":")
    try:
        if len(parts) == 2:
            h, m = int(parts[0]), int(parts[1])
            return h * 3600 + m * 60
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except (ValueError, TypeError):
        pass
    return None


# ─────────────────────────────────────────────────────────────
# DB queries
# ─────────────────────────────────────────────────────────────

SQL_VIDEOS = """
    SELECT
        v.id,
        v.original_filename,
        v.upload_type,
        v.excel_trend_blob_url,
        v.time_offset_seconds
    FROM videos v
    WHERE v.status IN ('completed', 'DONE')
      AND v.upload_type = 'clean_video'
      AND v.excel_trend_blob_url IS NOT NULL
      AND LENGTH(v.excel_trend_blob_url) > 5
      AND v.id IN (SELECT DISTINCT video_id FROM video_phases)
    ORDER BY v.created_at DESC
"""

SQL_PHASES = """
    SELECT
        phase_index,
        time_start,
        time_end
    FROM video_phases
    WHERE video_id = :video_id
    ORDER BY phase_index
"""

SQL_UPDATE_PHASE = """
    UPDATE video_phases
    SET
        gmv              = :gmv,
        order_count      = :order_count,
        viewer_count     = :viewer_count,
        like_count       = :like_count,
        comment_count    = :comment_count,
        share_count      = :share_count,
        new_followers    = :new_followers,
        product_clicks   = :product_clicks,
        conversion_rate  = :conversion_rate,
        gpm              = :gpm,
        importance_score = :importance_score,
        updated_at       = now()
    WHERE video_id = :video_id
      AND phase_index = :phase_index
"""


# ─────────────────────────────────────────────────────────────
# Core recalculation logic
# ─────────────────────────────────────────────────────────────

def recalculate_phase_metrics(
    trends: list[dict],
    phases: list[dict],
    time_offset_seconds: float,
    debug: bool = False,
) -> list[dict]:
    """
    CSV トレンドデータからフェーズごとの指標を再計算する。

    Parameters
    ----------
    trends : list[dict]
        CSV の全行（load_excel_data の "trends" キー）
    phases : list[dict]
        DB から取得したフェーズ一覧。各要素に phase_index, time_start, time_end を含む。
    time_offset_seconds : float
        動画がCSVタイムライン内のどこから始まるか（秒）
    debug : bool
        True の場合、最初の5件のCSVイベントのマッピングを詳細出力する

    Returns
    -------
    list[dict]
        phase_index をキーとした指標辞書のリスト
    """
    try:
        from csv_slot_filter import (
            _find_key, _safe_float as _sf, _parse_time_to_seconds as _pts,
            _detect_time_key, compute_slot_scores, KPI_ALIASES,
        )
    except ImportError:
        _find_key = None
        KPI_ALIASES = None
        _sf = _safe_float
        _pts = _parse_time_to_seconds
        _detect_time_key = None

    if not trends:
        return []

    sample = trends[0]

    # ── カラム検出 ──────────────────────────────────────────────
    try:
        from column_normalizer import detect_all_columns
        detection_result = detect_all_columns(sample)
        detected = detection_result["detected"]
        gmv_key     = detected.get("gmv")
        order_key   = detected.get("order_count")
        viewer_key  = detected.get("viewer_count")
        like_key    = detected.get("like_count")
        comment_key = detected.get("comment_count")
        share_key   = detected.get("share_count")
        follower_key= detected.get("new_followers")
        click_key   = detected.get("product_clicks")
        conv_key    = detected.get("ctor")
        gpm_key     = detected.get("gpm")
    except ImportError:
        if _find_key and KPI_ALIASES:
            gmv_key     = _find_key(sample, KPI_ALIASES["gmv"])
            order_key   = _find_key(sample, KPI_ALIASES["order_count"])
            viewer_key  = _find_key(sample, KPI_ALIASES["viewer_count"])
            like_key    = _find_key(sample, KPI_ALIASES["like_count"])
            comment_key = _find_key(sample, KPI_ALIASES["comment_count"])
            share_key   = _find_key(sample, KPI_ALIASES["share_count"])
            follower_key= _find_key(sample, KPI_ALIASES["new_followers"])
            click_key   = _find_key(sample, KPI_ALIASES["product_clicks"])
            conv_key    = _find_key(sample, KPI_ALIASES["ctor"])
            gpm_key     = _find_key(sample, KPI_ALIASES["gpm"])
        else:
            gmv_key = order_key = viewer_key = like_key = None
            comment_key = share_key = follower_key = click_key = None
            conv_key = gpm_key = None

    print(f"  [KEYS] gmv={gmv_key} order={order_key} viewer={viewer_key} "
          f"like={like_key} click={click_key}")

    # ── 時刻キー検出 ────────────────────────────────────────────
    time_key = _detect_time_key(trends) if _detect_time_key else None
    if not time_key:
        # フォールバック
        for k in sample.keys():
            kl = k.lower()
            if any(w in kl for w in ["時間", "time", "timestamp", "秒", "sec"]):
                time_key = k
                break
    print(f"  [KEYS] time_key={time_key}")

    # ── CSVエントリを時刻順にソート ─────────────────────────────
    timed_entries = []
    if time_key:
        for entry in trends:
            t_sec = _pts(entry.get(time_key))
            if t_sec is not None:
                timed_entries.append({"time_sec": t_sec, "entry": entry})
        timed_entries.sort(key=lambda x: x["time_sec"])

    if not timed_entries:
        print("  [WARN] No timed entries found – cannot recalculate")
        return []

    csv_first_sec = timed_entries[0]["time_sec"]
    print(f"  [TIME] csv_first_sec={csv_first_sec:.1f}s ({_fmt_sec(csv_first_sec)}), "
          f"time_offset_seconds={time_offset_seconds:.1f}s")

    # ── デバッグ: 最初の5件のCSVイベントを表示 ─────────────────
    if debug:
        print("\n  [DEBUG] 最初の5件のCSVイベント:")
        print(f"  {'CSV時刻':<12} {'絶対秒':>10} {'動画内秒':>10} {'フェーズ':>8} {'GMV':>10} {'注文':>6}")
        print("  " + "-" * 60)
        for te in timed_entries[:5]:
            t = te["time_sec"]
            video_rel = t - csv_first_sec  # 動画内の相対秒
            # どのフェーズに属するか
            matched_phase = "?"
            for ph in phases:
                ps = float(ph["time_start"] or 0)
                pe = float(ph["time_end"] or 0)
                phase_abs_s = csv_first_sec + time_offset_seconds + ps
                phase_abs_e = csv_first_sec + time_offset_seconds + pe
                if phase_abs_s <= t <= phase_abs_e:
                    matched_phase = str(ph["phase_index"])
                    break
            gmv_val = _sf(te["entry"].get(gmv_key)) if gmv_key else 0
            order_val = int(_sf(te["entry"].get(order_key)) or 0) if order_key else 0
            print(f"  {_fmt_sec(t):<12} {t:>10.1f} {video_rel:>10.1f} {matched_phase:>8} "
                  f"{gmv_val:>10.0f} {order_val:>6}")
        print()

    # ── スコアマップ ────────────────────────────────────────────
    score_map = {}
    try:
        scored_slots = compute_slot_scores(trends)
        score_map = {s["time_sec"]: s["score"] for s in scored_slots}
    except Exception:
        pass

    # ── フェーズごとに集計 ──────────────────────────────────────
    results = []
    for ph in phases:
        phase_index = ph["phase_index"]
        start_sec = float(ph["time_start"] or 0)
        end_sec   = float(ph["time_end"] or 0)

        # 正しい変換: csv_first_sec + time_offset_seconds + phase_relative_sec
        # csv_first_sec: CSVの最初の行の絶対時刻（秒）例: 14:30:00 = 52200
        # time_offset_seconds: この動画がCSVタイムライン内のどこから始まるか
        # start_sec: 動画内のフェーズ開始秒（0始まり）
        phase_abs_start = csv_first_sec + time_offset_seconds + start_sec
        phase_abs_end   = csv_first_sec + time_offset_seconds + end_sec

        phase_gmv = 0.0
        phase_orders = 0
        phase_viewers = 0
        phase_likes = 0
        phase_comments = 0
        phase_shares = 0
        phase_followers = 0
        phase_clicks = 0
        phase_conv = 0.0
        phase_gpm = 0.0
        phase_score = 0.0
        match_count = 0

        for te in timed_entries:
            t = te["time_sec"]
            e = te["entry"]
            if phase_abs_start <= t <= phase_abs_end:
                match_count += 1
                if gmv_key:     phase_gmv     += _sf(e.get(gmv_key)) or 0
                if order_key:   phase_orders  += int(_sf(e.get(order_key)) or 0)
                if viewer_key:  phase_viewers  = max(phase_viewers, int(_sf(e.get(viewer_key)) or 0))
                if like_key:    phase_likes    = max(phase_likes, int(_sf(e.get(like_key)) or 0))
                if comment_key: phase_comments += int(_sf(e.get(comment_key)) or 0)
                if share_key:   phase_shares   += int(_sf(e.get(share_key)) or 0)
                if follower_key:phase_followers += int(_sf(e.get(follower_key)) or 0)
                if click_key:   phase_clicks   += int(_sf(e.get(click_key)) or 0)
                if conv_key:    phase_conv      = max(phase_conv, _sf(e.get(conv_key)) or 0)
                if gpm_key:     phase_gpm       = max(phase_gpm, _sf(e.get(gpm_key)) or 0)
                phase_score = max(phase_score, score_map.get(t, 0))

        print(f"  [PHASE {phase_index:2d}] {_fmt_sec(start_sec)}-{_fmt_sec(end_sec)} "
              f"abs={_fmt_sec(phase_abs_start)}-{_fmt_sec(phase_abs_end)} "
              f"matches={match_count} gmv={phase_gmv:.0f} orders={phase_orders} clicks={phase_clicks}")

        results.append({
            "phase_index":    phase_index,
            "gmv":            round(phase_gmv, 2),
            "order_count":    phase_orders,
            "viewer_count":   phase_viewers,
            "like_count":     phase_likes,
            "comment_count":  phase_comments,
            "share_count":    phase_shares,
            "new_followers":  phase_followers,
            "product_clicks": phase_clicks,
            "conversion_rate":round(phase_conv, 4),
            "gpm":            round(phase_gpm, 2),
            "importance_score":round(phase_score, 4),
        })

    return results


def _fmt_sec(sec: float) -> str:
    """秒数を HH:MM:SS 形式に変換"""
    try:
        sec = int(sec)
        h = sec // 3600
        m = (sec % 3600) // 60
        s = sec % 60
        return f"{h:02d}:{m:02d}:{s:02d}"
    except Exception:
        return str(sec)


# ─────────────────────────────────────────────────────────────
# Main backfill
# ─────────────────────────────────────────────────────────────

async def backfill(video_id_filter: str | None, dry_run: bool, debug: bool):
    from excel_parser import load_excel_data

    async with AsyncSessionLocal() as session:
        # 対象動画を取得
        if video_id_filter:
            r = await session.execute(
                sa_text(SQL_VIDEOS + " AND v.id = :vid"),
                {"vid": video_id_filter},
            )
        else:
            r = await session.execute(sa_text(SQL_VIDEOS))
        videos = r.fetchall()

    print(f"\n[backfill_phase_metrics] Found {len(videos)} videos to process")
    if dry_run:
        print("[DRY RUN] No DB updates will be made\n")

    success = 0
    skipped = 0
    errors = 0

    for row in videos:
        vid         = str(row[0])
        fname       = str(row[1]) if row[1] else "?"
        trend_url   = str(row[3]) if row[3] else None
        time_offset = float(row[4]) if row[4] else 0.0

        print(f"\n{'='*60}")
        print(f"[{vid[:8]}] {fname}")
        print(f"  time_offset_seconds={time_offset:.1f}s")

        if not trend_url:
            print("  [SKIP] No trend URL")
            skipped += 1
            continue

        try:
            # Excel/CSVを読み込む
            excel_urls = {
                "excel_trend_blob_url":   trend_url,
                "excel_product_blob_url": None,
                "upload_type":            "clean_video",
                "time_offset_seconds":    time_offset,
            }
            excel_data = load_excel_data(vid, excel_urls)
            if not excel_data or not excel_data.get("has_trend_data"):
                print("  [SKIP] No trend data loaded")
                skipped += 1
                continue

            trends = excel_data["trends"]
            print(f"  Trend rows: {len(trends)}")

            # フェーズ一覧を取得
            async with AsyncSessionLocal() as session:
                r = await session.execute(sa_text(SQL_PHASES), {"video_id": vid})
                phase_rows = r.fetchall()

            phases = [
                {"phase_index": row[0], "time_start": row[1], "time_end": row[2]}
                for row in phase_rows
            ]
            print(f"  Phases: {len(phases)}")

            if not phases:
                print("  [SKIP] No phases found")
                skipped += 1
                continue

            # 再計算
            metrics_list = recalculate_phase_metrics(
                trends=trends,
                phases=phases,
                time_offset_seconds=time_offset,
                debug=debug,
            )

            if not metrics_list:
                print("  [SKIP] No metrics calculated")
                skipped += 1
                continue

            # DB更新
            if not dry_run:
                async with AsyncSessionLocal() as session:
                    for m in metrics_list:
                        await session.execute(
                            sa_text(SQL_UPDATE_PHASE),
                            {"video_id": vid, **m},
                        )
                    await session.commit()
                print(f"  [OK] Updated {len(metrics_list)} phases in DB")
            else:
                print(f"  [DRY RUN] Would update {len(metrics_list)} phases")

            success += 1

        except Exception as e:
            print(f"  [ERROR] {e}")
            traceback.print_exc()
            errors += 1

    print(f"\n{'='*60}")
    print(f"[backfill_phase_metrics] DONE: success={success} skipped={skipped} errors={errors}")
    if dry_run:
        print("[DRY RUN] No DB was modified")


def main():
    parser = argparse.ArgumentParser(
        description="Backfill phase CSV metrics for existing videos"
    )
    parser.add_argument("--video-id", help="Process only this video ID")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be updated without modifying DB")
    parser.add_argument("--debug", action="store_true",
                        help="Print first 5 CSV events with phase mapping for verification")
    args = parser.parse_args()

    asyncio.run(backfill(
        video_id_filter=args.video_id,
        dry_run=args.dry_run,
        debug=args.debug,
    ))


if __name__ == "__main__":
    main()
