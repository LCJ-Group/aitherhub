"""
phase_metrics_recalculator.py
=============================
既存動画の Derived Data（phase metrics）を最新ロジックで安全に再計算する。

【データ保護ルール】
- Raw Data（video, csv_metrics, transcript, phase_boundaries）: 変更禁止
- Human Data（user_rating, user_comment, human_sales_tags, reviewer_name）: 絶対保護
- Derived Data（gmv, order_count, viewer_count, ...）: 再計算対象

【更新対象カラム（video_phases）】
  gmv, order_count, viewer_count, like_count, comment_count,
  share_count, new_followers, product_clicks, conversion_rate,
  gpm, importance_score, phase_metrics_version_applied

【絶対に更新しないカラム（video_phases）】
  phase_index, time_start, time_end, phase_description,
  sales_psychology_tags, human_sales_tags, reviewer_name,
  user_rating, user_comment, rated_at, view_start, view_end,
  like_start, like_end, delta_view, delta_like
"""

import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ── Logic Version ─────────────────────────────────────────────────────────────
# Increment this when the recalculation algorithm changes.
# v1: Original (PR #114 前)
# v2: PR #114 — csv_first_sec + start_sec (time_offset_seconds 欠落)
# v3: PR #115 — csv_first_sec + time_offset_seconds + start_sec (正しい変換)
PHASE_METRICS_LOGIC_VERSION = 3


# ── Helper functions ──────────────────────────────────────────────────────────

def _safe_float(v) -> float:
    if v is None:
        return 0.0
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def _parse_time_to_seconds(val) -> Optional[float]:
    """時刻文字列を秒数に変換"""
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
            return int(parts[0]) * 3600 + int(parts[1]) * 60
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except (ValueError, TypeError):
        pass
    return None


def _fmt_sec(sec: float) -> str:
    """秒数を HH:MM:SS 形式に変換"""
    try:
        sec = int(sec)
        return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"
    except Exception:
        return str(sec)


def _detect_time_key(trends: list[dict]) -> Optional[str]:
    """CSV行から時刻カラムを検出する"""
    if not trends:
        return None
    sample = trends[0]
    for k in sample.keys():
        kl = k.lower()
        if any(w in kl for w in ["時間", "time", "timestamp", "秒", "sec"]):
            return k
    return None


def _detect_column_keys(sample: dict) -> dict:
    """CSVのカラム名からKPIキーを検出する"""
    # worker/batch/column_normalizer を使えない場合のフォールバック
    KPI_PATTERNS = {
        "gmv":            ["gmv", "売上", "revenue", "sales", "金額"],
        "order_count":    ["order", "注文", "成約"],
        "viewer_count":   ["viewer", "視聴", "観客", "人数"],
        "like_count":     ["like", "いいね", "♡"],
        "comment_count":  ["comment", "コメント"],
        "share_count":    ["share", "シェア", "共有"],
        "new_followers":  ["follower", "フォロワー", "新規"],
        "product_clicks": ["click", "クリック", "商品"],
        "ctor":           ["ctor", "cvr", "転換", "conversion"],
        "gpm":            ["gpm", "千人あたり"],
    }
    detected = {}
    for kpi_name, patterns in KPI_PATTERNS.items():
        for col_name in sample.keys():
            col_lower = col_name.lower()
            if any(p in col_lower for p in patterns):
                detected[kpi_name] = col_name
                break
    return detected


# ── Core recalculation ────────────────────────────────────────────────────────

def compute_phase_metrics(
    trends: list[dict],
    phases: list[dict],
    time_offset_seconds: float,
) -> tuple[list[dict], list[str]]:
    """
    CSV トレンドデータからフェーズごとの指標を再計算する（純粋関数）。

    Returns
    -------
    tuple[list[dict], list[str]]
        (phase_metrics_list, logs)
    """
    logs: list[str] = []

    if not trends:
        logs.append("ERROR: No trend data provided")
        return [], logs

    if not phases:
        logs.append("ERROR: No phases provided")
        return [], logs

    sample = trends[0]

    # ── カラム検出 ──
    try:
        worker_batch = os.path.join(
            os.path.dirname(__file__), "..", "..", "worker", "batch"
        )
        abs_worker = os.path.abspath(worker_batch)
        if abs_worker not in sys.path:
            sys.path.insert(0, abs_worker)
        from column_normalizer import detect_all_columns
        detection_result = detect_all_columns(sample)
        keys = detection_result["detected"]
        logs.append(f"Column detection: column_normalizer (detected {len(keys)} keys)")
    except ImportError:
        keys = _detect_column_keys(sample)
        logs.append(f"Column detection: fallback patterns (detected {len(keys)} keys)")

    gmv_key      = keys.get("gmv")
    order_key    = keys.get("order_count")
    viewer_key   = keys.get("viewer_count")
    like_key     = keys.get("like_count")
    comment_key  = keys.get("comment_count")
    share_key    = keys.get("share_count")
    follower_key = keys.get("new_followers")
    click_key    = keys.get("product_clicks")
    conv_key     = keys.get("ctor")
    gpm_key      = keys.get("gpm")

    logs.append(f"Keys: gmv={gmv_key} order={order_key} viewer={viewer_key} "
                f"like={like_key} click={click_key}")

    # ── 時刻キー検出 ──
    time_key = _detect_time_key(trends)
    if not time_key:
        logs.append("ERROR: No time key found in CSV")
        return [], logs
    logs.append(f"Time key: {time_key}")

    # ── CSVエントリを時刻順にソート ──
    timed_entries = []
    for entry in trends:
        t_sec = _parse_time_to_seconds(entry.get(time_key))
        if t_sec is not None:
            timed_entries.append({"time_sec": t_sec, "entry": entry})
    timed_entries.sort(key=lambda x: x["time_sec"])

    if not timed_entries:
        logs.append("ERROR: No timed entries found in CSV")
        return [], logs

    csv_first_sec = timed_entries[0]["time_sec"]
    csv_last_sec = timed_entries[-1]["time_sec"]
    logs.append(f"CSV range: {_fmt_sec(csv_first_sec)} - {_fmt_sec(csv_last_sec)} "
                f"({len(timed_entries)} entries)")
    logs.append(f"Time offset: {time_offset_seconds}s")

    # ── スコアマップ ──
    score_map = {}
    try:
        from csv_slot_filter import compute_slot_scores
        scored_slots = compute_slot_scores(trends)
        score_map = {s["time_sec"]: s["score"] for s in scored_slots}
    except Exception:
        logs.append("WARN: compute_slot_scores unavailable, scores will be 0")

    # ── フェーズごとに集計 ──
    results = []
    total_matches = 0

    for ph in phases:
        phase_index = ph["phase_index"]
        start_sec = float(ph["time_start"] or 0)
        end_sec   = float(ph["time_end"] or 0)

        # 正しい変換: csv_first_sec + time_offset_seconds + phase_relative_sec
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
                if gmv_key:      phase_gmv      += _safe_float(e.get(gmv_key))
                if order_key:    phase_orders   += int(_safe_float(e.get(order_key)))
                if viewer_key:   phase_viewers   = max(phase_viewers, int(_safe_float(e.get(viewer_key))))
                if like_key:     phase_likes     = max(phase_likes, int(_safe_float(e.get(like_key))))
                if comment_key:  phase_comments += int(_safe_float(e.get(comment_key)))
                if share_key:    phase_shares   += int(_safe_float(e.get(share_key)))
                if follower_key: phase_followers += int(_safe_float(e.get(follower_key)))
                if click_key:    phase_clicks   += int(_safe_float(e.get(click_key)))
                if conv_key:     phase_conv      = max(phase_conv, _safe_float(e.get(conv_key)))
                if gpm_key:      phase_gpm       = max(phase_gpm, _safe_float(e.get(gpm_key)))
                phase_score = max(phase_score, score_map.get(t, 0))

        total_matches += match_count

        logs.append(
            f"Phase {phase_index}: {_fmt_sec(start_sec)}-{_fmt_sec(end_sec)} "
            f"abs={_fmt_sec(phase_abs_start)}-{_fmt_sec(phase_abs_end)} "
            f"matches={match_count} gmv={phase_gmv:.0f} orders={phase_orders} clicks={phase_clicks}"
        )

        results.append({
            "phase_index":      phase_index,
            "gmv":              round(phase_gmv, 2),
            "order_count":      phase_orders,
            "viewer_count":     phase_viewers,
            "like_count":       phase_likes,
            "comment_count":    phase_comments,
            "share_count":      phase_shares,
            "new_followers":    phase_followers,
            "product_clicks":   phase_clicks,
            "conversion_rate":  round(phase_conv, 4),
            "gpm":              round(phase_gpm, 2),
            "importance_score": round(phase_score, 4),
        })

    logs.append(f"Total CSV matches across all phases: {total_matches}/{len(timed_entries)}")

    return results, logs


# ── Main service function ─────────────────────────────────────────────────────

async def recalculate_phase_metrics(
    video_id: str,
    db: AsyncSession,
    dry_run: bool = True,
    triggered_by: Optional[str] = None,
) -> dict[str, Any]:
    """
    既存動画の phase metrics を最新ロジックで再計算する。

    Parameters
    ----------
    video_id : str
        対象動画の UUID
    db : AsyncSession
        データベースセッション
    dry_run : bool
        True の場合、計算結果を返すが DB は更新しない
    triggered_by : str | None
        誰が実行したか（'admin:user@email', 'cli:backfill', 'system:auto'）

    Returns
    -------
    dict with keys:
        video_id, status, before_summary, after_summary, diff, logs,
        logic_version, phases_updated
    """
    start_time = time.time()
    logs: list[str] = []
    result = {
        "video_id": video_id,
        "status": "error",
        "before_summary": {},
        "after_summary": {},
        "diff": {},
        "logs": [],
        "logic_version": PHASE_METRICS_LOGIC_VERSION,
        "phases_updated": 0,
    }

    try:
        # ── 1. Video 取得 ──
        video_sql = text("""
            SELECT id, original_filename, upload_type,
                   excel_trend_blob_url, time_offset_seconds
            FROM videos
            WHERE id = :vid
        """)
        r = await db.execute(video_sql, {"vid": video_id})
        video_row = r.fetchone()
        if not video_row:
            logs.append(f"ERROR: Video {video_id} not found")
            result["logs"] = logs
            return result

        fname = str(video_row[1] or "?")
        upload_type = str(video_row[2] or "")
        trend_url = str(video_row[3] or "")
        time_offset = float(video_row[4] or 0)

        logs.append(f"Video: {fname} (type={upload_type}, offset={time_offset}s)")

        if upload_type != "clean_video":
            logs.append(f"ERROR: upload_type={upload_type!r} is not 'clean_video'")
            result["logs"] = logs
            return result

        if not trend_url or len(trend_url) < 5:
            logs.append("ERROR: No excel_trend_blob_url found")
            result["logs"] = logs
            return result

        # ── 2. Phase 取得 ──
        phase_sql = text("""
            SELECT phase_index, time_start, time_end,
                   gmv, order_count, viewer_count, like_count,
                   comment_count, share_count, new_followers,
                   product_clicks, conversion_rate, gpm, importance_score
            FROM video_phases
            WHERE video_id = :vid
            ORDER BY phase_index
        """)
        r2 = await db.execute(phase_sql, {"vid": video_id})
        phase_rows = r2.fetchall()

        if not phase_rows:
            logs.append("ERROR: No phases found for this video")
            result["logs"] = logs
            return result

        phases = []
        before_phases = []
        for pr in phase_rows:
            phases.append({
                "phase_index": pr[0],
                "time_start": pr[1],
                "time_end": pr[2],
            })
            before_phases.append({
                "phase_index":      pr[0],
                "gmv":              float(pr[3] or 0),
                "order_count":      int(pr[4] or 0),
                "viewer_count":     int(pr[5] or 0),
                "like_count":       int(pr[6] or 0),
                "comment_count":    int(pr[7] or 0),
                "share_count":      int(pr[8] or 0),
                "new_followers":    int(pr[9] or 0),
                "product_clicks":   int(pr[10] or 0),
                "conversion_rate":  float(pr[11] or 0),
                "gpm":              float(pr[12] or 0),
                "importance_score": float(pr[13] or 0),
            })

        logs.append(f"Phases: {len(phases)}")

        # ── 3. CSV 読み込み ──
        try:
            worker_batch = os.path.join(
                os.path.dirname(__file__), "..", "..", "worker", "batch"
            )
            abs_worker = os.path.abspath(worker_batch)
            if abs_worker not in sys.path:
                sys.path.insert(0, abs_worker)
            from excel_parser import load_excel_data
        except ImportError as e:
            logs.append(f"ERROR: Cannot import excel_parser: {e}")
            result["logs"] = logs
            return result

        excel_urls = {
            "excel_trend_blob_url": trend_url,
            "excel_product_blob_url": None,
            "upload_type": "clean_video",
            "time_offset_seconds": time_offset,
        }
        excel_data = load_excel_data(video_id, excel_urls)
        if not excel_data or not excel_data.get("has_trend_data"):
            logs.append("ERROR: No trend data loaded from Excel/CSV")
            result["logs"] = logs
            return result

        trends = excel_data["trends"]
        logs.append(f"Trend rows loaded: {len(trends)}")

        # ── 4-6. 再計算 ──
        after_phases, calc_logs = compute_phase_metrics(
            trends=trends,
            phases=phases,
            time_offset_seconds=time_offset,
        )
        logs.extend(calc_logs)

        if not after_phases:
            logs.append("ERROR: Recalculation produced no results")
            result["logs"] = logs
            return result

        # ── Before/After summary ──
        before_summary = {
            "total_gmv": sum(p["gmv"] for p in before_phases),
            "total_orders": sum(p["order_count"] for p in before_phases),
            "total_clicks": sum(p["product_clicks"] for p in before_phases),
            "max_viewers": max((p["viewer_count"] for p in before_phases), default=0),
            "phases_with_gmv": sum(1 for p in before_phases if p["gmv"] > 0),
            "phases": before_phases,
        }
        after_summary = {
            "total_gmv": sum(p["gmv"] for p in after_phases),
            "total_orders": sum(p["order_count"] for p in after_phases),
            "total_clicks": sum(p["product_clicks"] for p in after_phases),
            "max_viewers": max((p["viewer_count"] for p in after_phases), default=0),
            "phases_with_gmv": sum(1 for p in after_phases if p["gmv"] > 0),
            "phases": after_phases,
        }

        # ── Diff 計算 ──
        diff = {
            "gmv_delta": round(after_summary["total_gmv"] - before_summary["total_gmv"], 2),
            "orders_delta": after_summary["total_orders"] - before_summary["total_orders"],
            "clicks_delta": after_summary["total_clicks"] - before_summary["total_clicks"],
            "phases_with_gmv_before": before_summary["phases_with_gmv"],
            "phases_with_gmv_after": after_summary["phases_with_gmv"],
            "phases_changed": 0,
            "phase_diffs": [],
        }

        for bp, ap in zip(before_phases, after_phases):
            changed = False
            pd = {"phase_index": bp["phase_index"]}
            for key in ["gmv", "order_count", "viewer_count", "like_count",
                        "comment_count", "share_count", "new_followers",
                        "product_clicks", "conversion_rate", "gpm", "importance_score"]:
                bv = bp.get(key, 0)
                av = ap.get(key, 0)
                if abs(float(bv) - float(av)) > 0.001:
                    changed = True
                    pd[key] = {"before": bv, "after": av}
            if changed:
                diff["phases_changed"] += 1
                diff["phase_diffs"].append(pd)

        logs.append(f"Phases changed: {diff['phases_changed']}/{len(phases)}")

        # ── 7. DB 更新 ──
        if not dry_run:
            update_sql = text("""
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
                    phase_metrics_version_applied = :version,
                    updated_at       = now()
                WHERE video_id = :video_id
                  AND phase_index = :phase_index
            """)
            for ap in after_phases:
                await db.execute(update_sql, {
                    "video_id":         video_id,
                    "phase_index":      ap["phase_index"],
                    "gmv":              ap["gmv"],
                    "order_count":      ap["order_count"],
                    "viewer_count":     ap["viewer_count"],
                    "like_count":       ap["like_count"],
                    "comment_count":    ap["comment_count"],
                    "share_count":      ap["share_count"],
                    "new_followers":    ap["new_followers"],
                    "product_clicks":   ap["product_clicks"],
                    "conversion_rate":  ap["conversion_rate"],
                    "gpm":              ap["gpm"],
                    "importance_score": ap["importance_score"],
                    "version":          PHASE_METRICS_LOGIC_VERSION,
                })

            # Update videos table
            await db.execute(text("""
                UPDATE videos
                SET phase_metrics_version_applied = :version,
                    last_recalculated_at = now()
                WHERE id = :vid
            """), {"version": PHASE_METRICS_LOGIC_VERSION, "vid": video_id})

            logs.append(f"DB updated: {len(after_phases)} phases + videos table")
        else:
            logs.append("DRY RUN: No DB changes made")

        # ── Recalc log 保存 ──
        duration_ms = int((time.time() - start_time) * 1000)
        try:
            await db.execute(text("""
                INSERT INTO phase_metrics_recalc_log
                    (video_id, triggered_by, mode, status, logic_version,
                     before_json, after_json, diff_json, logs_json, duration_ms)
                VALUES
                    (:video_id, :triggered_by, :mode, :status, :logic_version,
                     :before_json, :after_json, :diff_json, :logs_json, :duration_ms)
            """), {
                "video_id":      video_id,
                "triggered_by":  triggered_by or "unknown",
                "mode":          "dry-run" if dry_run else "execute",
                "status":        "success",
                "logic_version": PHASE_METRICS_LOGIC_VERSION,
                "before_json":   json.dumps(before_summary, ensure_ascii=False),
                "after_json":    json.dumps(after_summary, ensure_ascii=False),
                "diff_json":     json.dumps(diff, ensure_ascii=False),
                "logs_json":     json.dumps(logs, ensure_ascii=False),
                "duration_ms":   duration_ms,
            })
        except Exception as log_err:
            logs.append(f"WARN: Failed to save recalc log: {log_err}")

        if not dry_run:
            await db.commit()

        result.update({
            "status": "success",
            "before_summary": before_summary,
            "after_summary": after_summary,
            "diff": diff,
            "logs": logs,
            "phases_updated": len(after_phases) if not dry_run else 0,
            "duration_ms": duration_ms,
        })

    except Exception as e:
        logger.exception(f"Recalculation failed for {video_id}: {e}")
        logs.append(f"EXCEPTION: {e}")
        logs.append(traceback.format_exc())
        result["logs"] = logs

        # Log error
        try:
            await db.execute(text("""
                INSERT INTO phase_metrics_recalc_log
                    (video_id, triggered_by, mode, status, logic_version,
                     error_message, logs_json, duration_ms)
                VALUES
                    (:video_id, :triggered_by, :mode, 'error', :logic_version,
                     :error_message, :logs_json, :duration_ms)
            """), {
                "video_id":      video_id,
                "triggered_by":  triggered_by or "unknown",
                "mode":          "dry-run" if dry_run else "execute",
                "logic_version": PHASE_METRICS_LOGIC_VERSION,
                "error_message": str(e),
                "logs_json":     json.dumps(logs, ensure_ascii=False),
                "duration_ms":   int((time.time() - start_time) * 1000),
            })
            await db.commit()
        except Exception:
            pass

    return result
