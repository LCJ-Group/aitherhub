"""Video API — Sales & Analytics

Split from video.py for maintainability.
"""
from typing import List, Optional
import json
import uuid as uuid_module
import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, select
from loguru import logger

from app.core.dependencies import get_db, get_current_user
from app.models.orm.video import Video

router = APIRouter(
    prefix="/videos",
    tags=["videos"],
)

# =========================================================
# Sales Moments API
# =========================================================

@router.get("/{video_id}/sales-moments")
async def get_sales_moments(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    動画のsales_moments（売れた瞬間）を取得する。
    ルールA: 既存APIは触らない。完全に新規エンドポイント。
    テーブルが存在しない場合はからのリストを返す（フォールバック）。
    """
    try:
        sql = text("""
            SELECT id, video_id, time_key, time_sec, video_sec, moment_type,
                   moment_type_detail, source, frame_meta,
                   click_value, click_delta, click_sigma_score,
                   order_value, order_delta, gmv_value,
                   confidence, reasons, created_at
            FROM video_sales_moments
            WHERE video_id = :video_id
            ORDER BY video_sec ASC
        """)
        result = await db.execute(sql, {"video_id": video_id})
        rows = result.fetchall()

        moments = []
        for row in rows:
            r = dict(row._mapping)
            # reasonsはJSON文字列なのでパース
            if r.get("reasons") and isinstance(r["reasons"], str):
                try:
                    r["reasons"] = json.loads(r["reasons"])
                except Exception:
                    r["reasons"] = [r["reasons"]]
            # frame_metaはJSON文字列なのでパース
            if r.get("frame_meta") and isinstance(r["frame_meta"], str):
                try:
                    r["frame_meta"] = json.loads(r["frame_meta"])
                except Exception:
                    r["frame_meta"] = None
            # datetimeをISO文字列に変換
            if r.get("created_at"):
                r["created_at"] = r["created_at"].isoformat()
            # UUIDを文字列に変換
            if r.get("id"):
                r["id"] = str(r["id"])
            if r.get("video_id"):
                r["video_id"] = str(r["video_id"])
            moments.append(r)

        return {"sales_moments": moments, "count": len(moments)}

    except Exception as e:
        # テーブルが存在しない場合など → 空リストを返す（ルールA: フォールバック）
        logger.warning(f"[SALES_MOMENTS] Failed to fetch for {video_id}: {e}")
        return {"sales_moments": [], "count": 0}


# =========================================================
# SALES MOMENTS BACKFILL (POST)
# =========================================================

@router.post("/{video_id}/sales-moments/backfill")
async def backfill_sales_moments(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    既存動画のsales_momentsをバックフィルする。
    product-dataのtrend_statsからsales_momentsを検出してDBに保存する。
    ルールA: 既存APIは触らない。完全に新規エンドポイント。
    ルールB: 失敗しても既存機能に影響しない。
    """
    import sys
    import os
    import tempfile
    import requests as http_requests

    # workerのcsv_slot_filter, excel_parserをインポート
    # __file__ = backend/app/api/v1/endpoints/video.py
    # 5 levels up = repo root, then worker/batch
    worker_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "worker", "batch")
    sys.path.insert(0, os.path.abspath(worker_path))

    try:
        from csv_slot_filter import detect_sales_moments
        from excel_parser import parse_trend_excel

        # 1. 動画のexcel_trend_blob_urlを取得
        video_sql = text("""
            SELECT id, upload_type, excel_trend_blob_url, time_offset_seconds
            FROM videos
            WHERE id = :video_id
        """)
        video_result = await db.execute(video_sql, {"video_id": video_id})
        video_row = video_result.fetchone()
        if not video_row:
            raise HTTPException(status_code=404, detail="Video not found")

        trend_url = video_row[2]  # excel_trend_blob_url
        time_offset = video_row[3] or 0  # time_offset_seconds

        if not trend_url:
            return {"status": "skipped", "reason": "no_trend_url", "count": 0}

        # 2. Excelをダウンロードしてパース
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            resp = http_requests.get(trend_url, timeout=30)
            resp.raise_for_status()
            tmp.write(resp.content)
            tmp_path = tmp.name

        try:
            trend_data = parse_trend_excel(tmp_path)
        finally:
            os.unlink(tmp_path)

        if not trend_data:
            return {"status": "skipped", "reason": "no_trend_data", "count": 0}

        # 3. sales_momentsを検出
        moments = detect_sales_moments(
            trends=trend_data,
            time_offset_seconds=float(time_offset) if time_offset else 0,
        )

        if not moments:
            return {"status": "ok", "reason": "no_moments_detected", "count": 0}

        # 3. テーブル作成（IF NOT EXISTS）
        create_sql = text("""
            CREATE TABLE IF NOT EXISTS video_sales_moments (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                video_id UUID NOT NULL,
                time_key VARCHAR(32) NOT NULL,
                time_sec FLOAT NOT NULL,
                video_sec FLOAT NOT NULL,
                moment_type VARCHAR(16) NOT NULL,
                click_value FLOAT DEFAULT 0,
                click_delta FLOAT DEFAULT 0,
                click_sigma_score FLOAT DEFAULT 0,
                order_value FLOAT DEFAULT 0,
                order_delta FLOAT DEFAULT 0,
                gmv_value FLOAT DEFAULT 0,
                confidence FLOAT DEFAULT 0,
                reasons TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await db.execute(create_sql)

        # インデックス作成（IF NOT EXISTS）
        await db.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_vsm_video_id ON video_sales_moments(video_id)"
        ))

        # 4. 既存データを削除（冪等性）
        await db.execute(
            text("DELETE FROM video_sales_moments WHERE video_id = :video_id"),
            {"video_id": video_id},
        )

        # 5. 新データを挿入
        for m in moments:
            await db.execute(
                text("""
                    INSERT INTO video_sales_moments
                    (video_id, time_key, time_sec, video_sec, moment_type,
                     click_value, click_delta, click_sigma_score,
                     order_value, order_delta, gmv_value,
                     confidence, reasons)
                    VALUES
                    (:video_id, :time_key, :time_sec, :video_sec, :moment_type,
                     :click_value, :click_delta, :click_sigma_score,
                     :order_value, :order_delta, :gmv_value,
                     :confidence, :reasons)
                """),
                {
                    "video_id": video_id,
                    "time_key": m["time_key"],
                    "time_sec": m["time_sec"],
                    "video_sec": m["video_sec"],
                    "moment_type": m["moment_type"],
                    "click_value": m["click_value"],
                    "click_delta": m["click_delta"],
                    "click_sigma_score": m["click_sigma_score"],
                    "order_value": m["order_value"],
                    "order_delta": m["order_delta"],
                    "gmv_value": m["gmv_value"],
                    "confidence": m["confidence"],
                    "reasons": json.dumps(m["reasons"], ensure_ascii=False),
                },
            )

        await db.commit()

        logger.info(
            f"[SALES_MOMENTS] Backfilled {len(moments)} moments for video {video_id}"
        )

        return {
            "status": "ok",
            "count": len(moments),
            "moments": moments,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[SALES_MOMENTS] Backfill failed for {video_id}: {e}")
        await db.rollback()
        return {"status": "error", "reason": str(e), "count": 0}



# =========================================================
# AI Event Score Prediction
# =========================================================

@router.get("/{video_id}/event-scores")
async def get_event_scores(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    各フェーズの「売れやすさスコア」を返す。
    Click / Order / Combined の3スコア + model_version。
    
    学習済みモデルがある場合: モデルで推論
    モデルがない場合: ルールベースのヒューリスティックスコア
    """
    try:
        # Fetch phases (safe columns only - no GMV/order/click leak)
        sql = text("""
            SELECT
                vp.phase_index,
                vp.phase_description,
                vp.time_start,
                vp.time_end,
                vp.cta_score,
                vp.sales_psychology_tags,
                COALESCE(vp.importance_score, 0) as importance_score
            FROM video_phases vp
             WHERE vp.video_id = :video_id
            ORDER BY vp.phase_index
        """)
        result = await db.execute(sql, {
            "video_id": video_id,
        })
        phases = result.fetchall()

        if not phases:
            return {"model_version": None, "score_source": "none", "scores": []}

        # Fetch video duration for position normalization
        # Use MAX(time_end) from video_phases as duration (duration_seconds column may not exist)
        dur_sql = text("""
            SELECT COALESCE(MAX(time_end), 0) as max_time_end
            FROM video_phases
            WHERE video_id = :vid
        """)
        dur_result = await db.execute(dur_sql, {"vid": video_id})
        dur_row = dur_result.fetchone()
        video_duration = float(dur_row.max_time_end) if dur_row and dur_row.max_time_end else 0

        # Fetch sales moments
        moments = []
        try:
            sm_sql = text("""
                SELECT video_sec, moment_type
                FROM video_sales_moments
                WHERE video_id = :video_id
                ORDER BY video_sec
            """)
            sm_result = await db.execute(sm_sql, {"video_id": video_id})
            moments = sm_result.fetchall()
        except Exception as _e:
            logger.debug(f"Non-critical error suppressed: {_e}")

        # Fetch product stats for product name matching
        product_names = []
        try:
            ps_sql = text("""
                SELECT product_name
                FROM video_product_stats
                WHERE video_id = :video_id
                ORDER BY COALESCE(product_clicks, 0) DESC
            """)
            ps_result = await db.execute(ps_sql, {"video_id": video_id})
            product_names = [r.product_name for r in ps_result.fetchall() if r.product_name]
        except Exception as _e:
            logger.debug(f"Non-critical error suppressed: {_e}")

        # Try model-based prediction
        model_result = _predict_with_model_v4(phases, moments, product_names, video_duration)

        if model_result is not None:
            click_scores, order_scores, model_version = model_result
            score_source = "model"
        else:
            click_scores = _predict_heuristic_v4(phases, moments)
            order_scores = click_scores  # heuristic doesn't distinguish
            model_version = None
            score_source = "heuristic"

        # Build response with Click / Order / Combined scores
        result_list = []
        for i, phase in enumerate(phases):
            click_s = click_scores[i]
            order_s = order_scores[i]
            combined = round(0.7 * click_s + 0.3 * order_s, 4)

            # Feature importance explanation (rule-based, top 3 reasons)
            reasons = _explain_score(phase, moments, product_names, video_duration)

            result_list.append({
                "phase_index": phase.phase_index,
                "score_click": round(click_s, 4),
                "score_order": round(order_s, 4),
                "score_combined": combined,
                "score_source": score_source,
                "reasons": reasons,
            })

        # Add rank by combined score
        sorted_by_score = sorted(result_list, key=lambda x: x["score_combined"], reverse=True)
        for rank, item in enumerate(sorted_by_score, 1):
            item["rank"] = rank

        # Re-sort by phase_index for output
        result_list.sort(key=lambda x: x["phase_index"])

        return {
            "model_version": model_version,
            "score_source": score_source,
            "scores": result_list,
        }

    except Exception as e:
        logger.error(f"[EVENT_SCORES] Failed for {video_id}: {e}")
        return {"model_version": None, "score_source": "error", "scores": []}


# ── Keyword extraction (same as generate_dataset.py) ──
import re as _re

_KEYWORD_GROUPS = [
    ("kw_price",      [r"円", r"¥", r"\d+円", r"価格", r"値段", r"プライス"]),
    ("kw_discount",   [r"割引", r"割", r"OFF", r"オフ", r"セール", r"半額", r"お得", r"特別価格"]),
    ("kw_urgency",    [r"今だけ", r"限定", r"残り", r"ラスト", r"早い者勝ち", r"なくなり次第", r"本日限り"]),
    ("kw_cta",        [r"リンク", r"カート", r"タップ", r"クリック", r"押して", r"ポチ", r"購入", r"買って"]),
    ("kw_quantity",   [r"残り\d+", r"\d+個", r"\d+点", r"在庫", r"ストック"]),
    ("kw_comparison", [r"通常", r"定価", r"普通", r"比べ", r"違い", r"他と"]),
    ("kw_quality",    [r"品質", r"成分", r"効果", r"おすすめ", r"人気", r"ランキング"]),
    ("kw_number",     [r"\d{3,}"]),
]


def _extract_kw_flags(text_str):
    if not text_str:
        return {g[0]: 0 for g in _KEYWORD_GROUPS}
    flags = {}
    for flag_name, patterns in _KEYWORD_GROUPS:
        matched = 0
        for pat in patterns:
            if _re.search(pat, text_str, _re.IGNORECASE):
                matched = 1
                break
        flags[flag_name] = matched
    return flags


def _check_product_match(text_str, product_names):
    if not text_str or not product_names:
        return 0, 0, 0
    text_lower = text_str.lower()
    matched = 0
    matched_top3 = 0
    for i, name in enumerate(product_names):
        if not name:
            continue
        short = name[:6].lower().strip()
        if len(short) >= 2 and short in text_lower:
            matched += 1
            if i < 3:
                matched_top3 = 1
    return (1 if matched > 0 else 0), matched_top3, matched


# ── Known event types (must match train.py v4) ──
_KNOWN_EVENT_TYPES = [
    "HOOK", "GREETING", "INTRO", "DEMONSTRATION", "PRICE",
    "CTA", "OBJECTION", "SOCIAL_PROOF", "URGENCY",
    "EMPATHY", "EDUCATION", "CHAT", "TRANSITION", "CLOSING", "UNKNOWN",
]


def _build_feature_vector_v4(phase, product_names, video_duration):
    """Build feature vector matching generate_dataset.py v2 / train.py v4 schema."""
    import numpy as np

    time_start = float(phase.time_start) if phase.time_start else 0
    time_end = float(phase.time_end) if phase.time_end else 0
    duration = time_end - time_start
    desc = phase.phase_description or ""

    # Tags
    tags = []
    try:
        raw = phase.sales_psychology_tags
        if raw:
            tags = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as _e:
        logger.debug(f"Non-critical error suppressed: {_e}")
    event_type = tags[0] if tags else "UNKNOWN"

    # Keyword flags
    kw = _extract_kw_flags(desc)

    # Text features
    text_length = len(desc) if desc else 0
    has_number = 1 if _re.search(r"\d+", desc) else 0
    exclamation_count = desc.count("！") + desc.count("!") if desc else 0

    # Product match
    pm, pm_top3, pm_count = _check_product_match(desc, product_names)

    # Position
    event_position_min = round(time_start / 60.0, 1)
    event_position_pct = round(time_start / video_duration, 3) if video_duration > 0 else 0.0

    # Build feature dict in exact order matching features_used in manifest
    features = {
        "event_duration": round(duration, 1),
        "event_position_min": event_position_min,
        "event_position_pct": event_position_pct,
        "tag_count": len(tags),
        "cta_score": float(phase.cta_score) if phase.cta_score else 0,
        "importance_score": float(phase.importance_score),
        "text_length": text_length,
        "has_number": has_number,
        "exclamation_count": exclamation_count,
        **kw,
        "product_match": pm,
        "product_match_top3": pm_top3,
        "matched_product_count": pm_count,
    }

    # Event type one-hot
    for et in _KNOWN_EVENT_TYPES:
        features[f"event_{et}"] = 1 if event_type == et else 0

    return features


def _predict_with_model_v4(phases, moments, product_names, video_duration):
    """
    Predict using trained v4 model with manifest.json for feature compatibility.
    Returns (click_scores, order_scores, model_version) or None.
    """
    import os
    import pickle
    import numpy as np

    model_dir = os.environ.get(
        "AI_MODEL_DIR",
        "/var/www/aitherhub/worker/batch/models"
    )
    manifest_path = os.path.join(model_dir, "manifest.json")

    if not os.path.exists(manifest_path):
        return None

    try:
        with open(manifest_path, "r") as f:
            manifest = json.load(f)
    except Exception:
        return None

    model_version = manifest.get("model_version", "unknown")
    features_used = manifest.get("features_used", [])

    if not features_used:
        return None

    # Build feature matrix
    X = np.zeros((len(phases), len(features_used)), dtype=np.float32)

    for i, phase in enumerate(phases):
        feat_dict = _build_feature_vector_v4(phase, product_names, video_duration)
        for j, feat_name in enumerate(features_used):
            X[i, j] = float(feat_dict.get(feat_name, 0))

    # Load and predict for each target
    results = {}
    for target in ["click", "order"]:
        model_info = manifest.get("models", {}).get(target, {})
        if not model_info:
            continue

        best_type = model_info.get("best_model", "lgbm")
        files = model_info.get("files", {})
        model_file = files.get(best_type)

        if not model_file:
            continue

        model_path = os.path.join(model_dir, model_file)
        if not os.path.exists(model_path):
            continue

        try:
            with open(model_path, "rb") as f:
                obj = pickle.load(f)

            if best_type == "lgbm":
                model = obj
                probas = model.predict_proba(X)
            else:  # lr
                model = obj["model"]
                scaler = obj.get("scaler")
                X_scaled = scaler.transform(X) if scaler else X
                probas = model.predict_proba(X_scaled)

            scores = [float(p[1]) if len(p) > 1 else float(p[0]) for p in probas]
            results[target] = scores
        except Exception as e:
            logger.warning(f"[EVENT_SCORES] Model {target}/{best_type} failed: {e}")
            continue

    if not results:
        return None

    n = len(phases)
    click_scores = results.get("click", [0.5] * n)
    order_scores = results.get("order", [0.5] * n)

    return click_scores, order_scores, model_version


def _predict_heuristic_v4(phases, moments):
    """
    Heuristic scoring when no model is available.
    Uses safe signals only (no GMV/order/click leak).
    """
    STRONG_WINDOW = 150

    scores = []
    for phase in phases:
        score = 0.0
        time_start = float(phase.time_start) if phase.time_start else 0
        time_end = float(phase.time_end) if phase.time_end else 0
        phase_mid = (time_start + time_end) / 2
        desc = phase.phase_description or ""

        # 1. CTA score (0-0.30)
        cta = float(phase.cta_score) if phase.cta_score else 0
        score += (cta / 5.0) * 0.30

        # 2. Importance score (0-0.20)
        imp = float(phase.importance_score) if phase.importance_score else 0
        score += imp * 0.20

        # 3. Sales moment proximity (0-0.20)
        for m in moments:
            dist = abs(float(m.video_sec) - phase_mid)
            if dist <= STRONG_WINDOW:
                if m.moment_type == "strong":
                    score += 0.20
                    break
                elif m.moment_type in ("click_spike", "order_spike"):
                    score += 0.12
                    break

        # 4. Event type bonus (0-0.15)
        tags = []
        try:
            raw = phase.sales_psychology_tags
            if raw:
                tags = json.loads(raw) if isinstance(raw, str) else raw
        except Exception as _e:
            logger.debug(f"Non-critical error suppressed: {_e}")
        high_value_types = {"PRICE", "CTA", "URGENCY", "SOCIAL_PROOF"}
        if any(t in high_value_types for t in tags):
            score += 0.15

        # 5. Keyword bonus (0-0.15)
        kw = _extract_kw_flags(desc)
        kw_score = sum([
            kw.get("kw_price", 0) * 0.04,
            kw.get("kw_discount", 0) * 0.03,
            kw.get("kw_urgency", 0) * 0.03,
            kw.get("kw_cta", 0) * 0.03,
            kw.get("kw_quantity", 0) * 0.02,
        ])
        score += kw_score

        scores.append(min(score, 1.0))

    return scores


def _explain_score(phase, moments, product_names, video_duration):
    """
    Generate top-3 human-readable reasons for the score.
    Rule-based, no LLM needed.
    """
    reasons = []
    time_start = float(phase.time_start) if phase.time_start else 0
    time_end = float(phase.time_end) if phase.time_end else 0
    duration = time_end - time_start
    phase_mid = (time_start + time_end) / 2
    desc = phase.phase_description or ""

    # Position in stream
    pos_min = round(time_start / 60.0, 1)
    if 5 <= pos_min <= 30:
        reasons.append(f"配信中盤（{pos_min:.0f}分台）")
    elif pos_min < 5:
        reasons.append(f"配信序盤（{pos_min:.0f}分台）")
    elif pos_min > 30:
        reasons.append(f"配信終盤（{pos_min:.0f}分台）")

    # Duration
    if 20 <= duration <= 60:
        reasons.append(f"{duration:.0f}秒でテンポ良い")
    elif duration > 120:
        reasons.append(f"{duration:.0f}秒の長尺（じっくり説明）")

    # CTA
    cta = float(phase.cta_score) if phase.cta_score else 0
    if cta >= 4:
        reasons.append("CTA強め（カート誘導あり）")
    elif cta >= 3:
        reasons.append("CTA中程度")

    # Keywords
    kw = _extract_kw_flags(desc)
    if kw.get("kw_price"):
        reasons.append("価格提示あり")
    if kw.get("kw_discount"):
        reasons.append("割引・セール言及")
    if kw.get("kw_urgency"):
        reasons.append("緊急性（今だけ/限定）")
    if kw.get("kw_cta"):
        reasons.append("購入誘導ワードあり")

    # Sales moment proximity
    for m in moments:
        dist = abs(float(m.video_sec) - phase_mid)
        if dist <= 150:
            if m.moment_type == "strong":
                reasons.append("売上スパイク窓内")
            elif m.moment_type == "click_spike":
                reasons.append("クリックスパイク窓内")
            break

    # Event type
    tags = []
    try:
        raw = phase.sales_psychology_tags
        if raw:
            tags = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as _e:
        logger.debug(f"Non-critical error suppressed: {_e}")
    type_labels = {
        "PRICE": "価格提示フェーズ",
        "CTA": "CTAフェーズ",
        "URGENCY": "緊急性フェーズ",
        "SOCIAL_PROOF": "社会的証明フェーズ",
        "DEMONSTRATION": "デモフェーズ",
    }
    for t in tags:
        if t in type_labels:
            reasons.append(type_labels[t])
            break

    # Product match
    if product_names and desc:
        pm, _, _ = _check_product_match(desc, product_names)
        if pm:
            reasons.append("商品名言及あり")

    return reasons[:3]  # Top 3 only


# =========================================================
# SALES CLIP CANDIDATES (GET)
# =========================================================
@router.get("/{video_id}/sales-clip-candidates")

@router.get("/{video_id}/sales-clip-candidates")
async def get_sales_clip_candidates(
    video_id: str,
    top_n: int = 5,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    動画の各フェーズに sales_score を付与し、
    売上につながる可能性が高いクリップ候補（TOP3〜5）を返す。
    """
    from app.services.sales_clip_service import compute_sales_scores, extract_clip_candidates

    try:
        user_id = user.get("user_id") or user.get("id")

        sql_phases = text("""
            SELECT
                vp.phase_index,
                vp.time_start,
                vp.time_end,
                COALESCE(vp.gmv, 0) as gmv,
                COALESCE(vp.order_count, 0) as order_count,
                COALESCE(vp.viewer_count, 0) as viewer_count,
                COALESCE(vp.product_clicks, 0) as product_clicks,
                COALESCE(vp.cta_score, 0) as cta_score,
                vp.user_rating,
                vp.sales_psychology_tags,
                vp.human_sales_tags
            FROM video_phases vp
            WHERE vp.video_id = :video_id
            ORDER BY vp.phase_index
        """)
        phases_result = await db.execute(sql_phases, {
            "video_id": video_id,
        })
        phase_rows = phases_result.fetchall()

        if not phase_rows:
            return {
                "video_id": video_id,
                "total_phases": 0,
                "candidates": [],
                "phase_scores": [],
            }

        phases = [dict(row._mapping) for row in phase_rows]

        moments: list[dict] = []
        try:
            sql_moments = text("""
                SELECT video_sec, moment_type, click_value, order_value, gmv_value
                FROM video_sales_moments
                WHERE video_id = :video_id
                ORDER BY video_sec ASC
            """)
            moments_result = await db.execute(sql_moments, {"video_id": video_id})
            moments = [dict(row._mapping) for row in moments_result.fetchall()]
        except Exception as _e:
            logger.debug(f"Non-critical error suppressed: {_e}")

        phase_scores = compute_sales_scores(phases, moments)
        top_n_clamped = max(1, min(int(top_n), 10))
        candidates = extract_clip_candidates(phase_scores, top_n=top_n_clamped)

        return {
            "video_id": video_id,
            "total_phases": len(phases),
            "moments_count": len(moments),
            "candidates": [
                {
                    "rank": c.rank,
                    "label": c.label,
                    "phase_index": c.phase_index,
                    "phase_indices": c.phase_indices,
                    "time_start": c.time_start,
                    "time_end": c.time_end,
                    "duration": c.duration,
                    "sales_score": c.sales_score,
                    "score_breakdown": c.score_breakdown,
                    "reasons": c.reasons,
                }
                for c in candidates
            ],
            "phase_scores": [
                {
                    "phase_index": ps.phase_index,
                    "time_start": ps.time_start,
                    "time_end": ps.time_end,
                    "sales_score": ps.sales_score,
                    "score_breakdown": ps.score_breakdown,
                    "reasons": ps.reasons,
                }
                for ps in phase_scores
            ],
        }

    except Exception as exc:
        logger.exception(f"[SALES_CLIP] Failed for {video_id}: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to compute sales clip candidates: {exc}")



# =========================

@router.get("/{video_id}/sales-moment-clips")
async def get_sales_moment_clips(
    video_id: str,
    top_n: int = 5,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    売上・注文・クリック・視聴者のスパイク（急増）を検出し、
    その瞬間を中心にクリップ候補を自動生成する。

    既存の sales-clip-candidates がフェーズ単位のスコアリングであるのに対し、
    このエンドポイントは時系列データのスパイクから直接クリップ候補を生成する。
    """
    from app.services.sales_moment_clip_service import (
        detect_spikes,
        build_moment_clips,
        compute_timed_metrics_from_phases,
    )

    try:
        user_id = user.get("user_id") or user.get("id")

        # 動画の所有権チェック（videosテーブルで確認）
        ownership_sql = text("SELECT id FROM videos WHERE id = :video_id AND user_id = :user_id")
        ownership_result = await db.execute(ownership_sql, {"video_id": video_id, "user_id": user_id})
        if not ownership_result.fetchone():
            return {"video_id": video_id, "spike_count": 0, "video_duration": 0.0, "candidates": []}

        # フェーズデータ取得（video_phasesのuser_idは不整合があるためフィルタしない）
        try:
            sql_phases = text("""
                SELECT
                    vp.phase_index,
                    vp.time_start,
                    vp.time_end,
                    COALESCE(vp.gmv, 0) as gmv,
                    COALESCE(vp.order_count, 0) as order_count,
                    COALESCE(vp.viewer_count, 0) as viewer_count,
                    COALESCE(vp.product_clicks, 0) as product_clicks,
                    COALESCE(vp.cta_score, 0) as cta_score
                FROM video_phases vp
                WHERE vp.video_id = :video_id
                ORDER BY vp.phase_index ASC
            """)
            phases_result = await db.execute(sql_phases, {
                "video_id": video_id,
            })
            phase_rows = phases_result.fetchall()
            phases = [dict(row._mapping) for row in phase_rows]
        except Exception:
            # Fallback: query without sales metric columns
            await db.rollback()
            sql_phases_fallback = text("""
                SELECT
                    vp.phase_index,
                    vp.time_start,
                    vp.time_end,
                    COALESCE(vp.cta_score, 0) as cta_score
                FROM video_phases vp
                WHERE vp.video_id = :video_id
                ORDER BY vp.phase_index ASC
            """)
            phases_result = await db.execute(sql_phases_fallback, {
                "video_id": video_id,
            })
            phase_rows = phases_result.fetchall()
            # Add default values for missing columns
            phases = [
                {**dict(row._mapping), "gmv": 0, "order_count": 0, "viewer_count": 0, "product_clicks": 0}
                for row in phase_rows
            ]

        if not phases:
            return {
                "video_id": video_id,
                "spike_count": 0,
                "candidates": [],
            }

        # 動画の総秒数（duration カラムが存在しない場合は video_phases から計算）
        try:
            video_sql = text("SELECT duration FROM videos WHERE id = :video_id")
            vres = await db.execute(video_sql, {"video_id": video_id})
            video_row = vres.fetchone()
            video_duration = float(video_row.duration) if video_row and video_row.duration else 0.0
        except Exception:
            video_duration = 0.0

        # Fallback: duration が 0 の場合は phases の max(time_end) から計算
        if video_duration <= 0 and phases:
            video_duration = max((float(p.get("time_end", 0)) for p in phases), default=0.0)

        # 時系列メトリクスを構築
        timed_metrics = compute_timed_metrics_from_phases(phases)

        # スパイク検出
        spikes = detect_spikes(timed_metrics)

        # クリップ候補生成
        top_n_clamped = max(1, min(int(top_n), 10))
        candidates = build_moment_clips(
            spikes=spikes,
            phases=phases,
            video_duration=video_duration,
            top_n=top_n_clamped,
        )

        return {
            "video_id": video_id,
            "spike_count": len(spikes),
            "video_duration": video_duration,
            "candidates": [
                {
                    "rank": c.rank,
                    "label": c.label,
                    "phase_index": c.phase_index,
                    "time_start": c.time_start,
                    "time_end": c.time_end,
                    "duration": c.duration,
                    "score": c.score,
                    "primary_metric": c.primary_metric,
                    "summary": c.summary,
                    "spike_events": [
                        {
                            "video_sec": se.video_sec,
                            "metric": se.metric,
                            "value": se.value,
                            "spike_ratio": se.spike_ratio,
                        }
                        for se in c.spike_events[:5]  # 最大5件
                    ],
                }
                for c in candidates
            ],
        }

    except Exception as exc:
        logger.exception(f"[SALES_MOMENT_CLIP] Failed for {video_id}: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to compute sales moment clips: {exc}")



# =========================
# Hook Detection API
# =========================

@router.get("/{video_id}/hook-detection")
async def detect_hooks_for_video(
    video_id: str,
    max_candidates: int = 10,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    動画のトランスクリプトからフック（Hook）候補を検出する。
    TikTok / Reels 向けに「最初3秒」で視聴者を引き付ける
    フレーズをスコアリングして返す。
    """
    from app.services.hook_detection_service import detect_hooks, suggest_hook_placement

    try:
        user_id = user.get("user_id") or user.get("id")

        # トランスクリプトセグメントを取得
        # まず video_phases の audio_text から取得を試みる
        phase_rows = []
        has_audio_text = True
        try:
            sql_phases = text("""
                SELECT
                    vp.phase_index,
                    vp.time_start,
                    vp.time_end,
                    vp.audio_text
                FROM video_phases vp
                WHERE vp.video_id = :video_id
                ORDER BY vp.phase_index ASC
            """)
            phases_result = await db.execute(sql_phases, {
                "video_id": video_id,
            })
            phase_rows = phases_result.fetchall()
        except Exception:
            # audio_text column doesn't exist yet - fallback to phase_description
            has_audio_text = False
            await db.rollback()
            sql_phases_fallback = text("""
                SELECT
                    vp.phase_index,
                    vp.time_start,
                    vp.time_end,
                    vp.phase_description as audio_text
                FROM video_phases vp
                WHERE vp.video_id = :video_id
                ORDER BY vp.phase_index ASC
            """)
            phases_result = await db.execute(sql_phases_fallback, {
                "video_id": video_id,
            })
            phase_rows = phases_result.fetchall()

        # フェーズのaudio_textからセグメントを構築
        segments = []
        for row in phase_rows:
            audio_text = row.audio_text
            if not audio_text:
                continue
            t_start = float(row.time_start) if row.time_start else 0.0
            t_end = float(row.time_end) if row.time_end else t_start + 60.0

            # audio_text を文に分割
            import re as _re
            raw_text = str(audio_text).strip()
            sentences = _re.split(r'[。！？!?\n]', raw_text)
            sentences = [s.strip() for s in sentences if s.strip()]

            # 句読点がなく分割できなかった場合、スペース・読点・助詞区切りで再分割
            if len(sentences) <= 1 and len(raw_text) > 80:
                # まず読点「、」で分割を試みる
                sentences = _re.split(r'[、,]', raw_text)
                sentences = [s.strip() for s in sentences if s.strip() and len(s.strip()) > 3]

            # それでも長すぎる場合、約50文字ごとに分割
            final_sentences = []
            for sent in (sentences if sentences else [raw_text]):
                if len(sent) > 80:
                    # 50文字ごとに分割
                    for j in range(0, len(sent), 50):
                        chunk = sent[j:j+50].strip()
                        if chunk:
                            final_sentences.append(chunk)
                else:
                    final_sentences.append(sent)
            sentences = final_sentences if final_sentences else [raw_text]

            # 均等に時間を割り当て
            duration = t_end - t_start
            per_sentence = duration / len(sentences) if sentences else duration
            for i, sent in enumerate(sentences):
                seg_start = t_start + i * per_sentence
                seg_end = seg_start + per_sentence
                segments.append({
                    "start": seg_start,
                    "end": seg_end,
                    "text": sent,
                })

        if not segments:
            return {
                "video_id": video_id,
                "hook_count": 0,
                "hooks": [],
                "message": "トランスクリプトが見つかりません",
            }

        # フック検出
        max_cand = max(1, min(int(max_candidates), 20))
        hooks = detect_hooks(segments, max_candidates=max_cand)

        # 動画全体のフック配置提案（duration カラムが存在しない場合はフェーズから計算）
        try:
            video_sql = text("SELECT duration FROM videos WHERE id = :video_id")
            vres = await db.execute(video_sql, {"video_id": video_id})
            video_row = vres.fetchone()
            video_duration = float(video_row.duration) if video_row and video_row.duration else 0.0
        except Exception:
            video_duration = max((s.get("end", 0) for s in segments), default=0.0) if segments else 0.0

        placement = suggest_hook_placement(hooks, 0, video_duration) if hooks else None

        return {
            "video_id": video_id,
            "hook_count": len(hooks),
            "hooks": [
                {
                    "text": h.text,
                    "start_sec": h.start_sec,
                    "end_sec": h.end_sec,
                    "hook_score": h.hook_score,
                    "hook_reasons": h.hook_reasons,
                    "is_question": h.is_question,
                    "has_number": h.has_number,
                    "keyword_matches": h.keyword_matches,
                }
                for h in hooks
            ],
            "placement_suggestion": {
                "should_reorder": placement.get("should_reorder", False) if placement else False,
                "suggested_start": placement.get("suggested_start", 0) if placement else 0,
                "reason": placement.get("reason", "") if placement else "",
                "best_hook_text": placement["best_hook"].text if placement and placement.get("best_hook") else None,
            } if placement else None,
        }

    except Exception as exc:
        logger.exception(f"[HOOK_DETECTION] Failed for {video_id}: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to detect hooks: {exc}")



# ── Moment-based Clipping API ──────────────────────────────────────────────

MOMENT_CATEGORIES = {
    "purchase_popup": {
        "label": "Purchase Popup Clips",
        "icon": "shopping_cart",
        "description": "購入ポップアップが表示された瞬間",
        "padding_before": 20.0,
        "padding_after": 20.0,
        "priority": 1,
    },
    "comment_spike": {
        "label": "Comment Explosion Clips",
        "icon": "chat_bubble",
        "description": "コメントが爆発的に増えた瞬間",
        "padding_before": 15.0,
        "padding_after": 15.0,
        "priority": 2,
    },
    "viewer_spike": {
        "label": "Viewer Spike Clips",
        "icon": "visibility",
        "description": "視聴者数が急増した瞬間",
        "padding_before": 15.0,
        "padding_after": 15.0,
        "priority": 3,
    },
    "gift_animation": {
        "label": "Gift / Like Animation Clips",
        "icon": "card_giftcard",
        "description": "ギフト・いいねアニメーションが集中した瞬間",
        "padding_before": 10.0,
        "padding_after": 15.0,
        "priority": 4,
    },
    "product_reveal": {
        "label": "Product Reveal Clips",
        "icon": "unarchive",
        "description": "商品を見せる・開封する瞬間",
        "padding_before": 5.0,
        "padding_after": 20.0,
        "priority": 5,
    },
    "chat_purchase_highlight": {
        "label": "Chat Highlight Clips",
        "icon": "forum",
        "description": "購入関連コメントが集中した瞬間",
        "padding_before": 10.0,
        "padding_after": 15.0,
        "priority": 6,
    },
    "product_viewers_popup": {
        "label": "Product Viewers Clips",
        "icon": "people",
        "description": "商品閲覧者数ポップアップが表示された瞬間",
        "padding_before": 10.0,
        "padding_after": 15.0,
        "priority": 7,
    },
    "strong": {
        "label": "Strong Sales Moments",
        "icon": "trending_up",
        "description": "売上が大きく伸びた瞬間（CSV売上データ検出）",
        "padding_before": 20.0,
        "padding_after": 20.0,
        "priority": 0,
    },
    "weak": {
        "label": "Weak Sales Signals",
        "icon": "show_chart",
        "description": "売上の小さな動きが検出された瞬間",
        "padding_before": 15.0,
        "padding_after": 15.0,
        "priority": 8,
    },
}


@router.get("/{video_id}/moment-clips")
async def get_moment_clips(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Moment-based Clipping API
    =========================
    video_sales_moments の moment_type_detail でグループ化し、
    各カテゴリごとにクリップ候補を自動生成して返す。

    レスポンス:
    {
        "video_id": "...",
        "categories": [
            {
                "category": "purchase_popup",
                "label": "Purchase Popup Clips",
                "icon": "shopping_cart",
                "description": "...",
                "clips": [
                    {
                        "id": 1,
                        "time_start": 120.0,
                        "time_end": 160.0,
                        "duration": 40.0,
                        "video_sec": 140.0,
                        "confidence": 0.85,
                        "reasons": [...],
                        "order_value": 3,
                        "click_value": 5,
                        "frame_meta": {...},
                    }
                ],
                "count": 3,
            }
        ],
        "total_moments": 15,
        "auto_zoom_data": [...],
    }
    """
    try:
        user_id = current_user.get("user_id") or current_user.get("id")

        # 動画情報を取得（duration カラムが存在しない場合のフォールバック付き）
        try:
            video_sql = text("SELECT duration, upload_type FROM videos WHERE id = :video_id AND user_id = :user_id")
            vres = await db.execute(video_sql, {"video_id": video_id, "user_id": user_id})
            video_row = vres.fetchone()
            if not video_row:
                raise HTTPException(status_code=404, detail="Video not found")
            video_duration = float(video_row.duration) if video_row.duration else 0.0
        except HTTPException:
            raise
        except Exception:
            # Fallback: check video exists without duration column
            video_check_sql = text("SELECT id, upload_type FROM videos WHERE id = :video_id AND user_id = :user_id")
            vres = await db.execute(video_check_sql, {"video_id": video_id, "user_id": user_id})
            video_row = vres.fetchone()
            if not video_row:
                raise HTTPException(status_code=404, detail="Video not found")
            # Compute duration from video_phases
            dur_sql = text("SELECT MAX(time_end) as max_end FROM video_phases WHERE video_id = :video_id")
            dur_res = await db.execute(dur_sql, {"video_id": video_id})
            dur_row = dur_res.fetchone()
            video_duration = float(dur_row.max_end) if dur_row and dur_row.max_end else 0.0

        # sales_moments を取得（moment_type_detail 含む）
        try:
            sql = text("""
                SELECT id, video_id, time_key, time_sec, video_sec, moment_type,
                       moment_type_detail, source, frame_meta,
                       click_value, click_delta, click_sigma_score,
                       order_value, order_delta, gmv_value,
                       confidence, reasons, created_at
                FROM video_sales_moments
                WHERE video_id = :video_id
                ORDER BY video_sec ASC
            """)
            result = await db.execute(sql, {"video_id": video_id})
            rows = result.fetchall()
        except Exception:
            # Fallback: query without newer columns
            await db.rollback()
            try:
                sql_fallback = text("""
                    SELECT id, video_id, time_key, time_sec, video_sec, moment_type,
                           moment_type AS moment_type_detail,
                           'pipeline' AS source,
                           NULL AS frame_meta,
                           click_value, click_delta, click_sigma_score,
                           order_value, order_delta, gmv_value,
                           confidence, reasons, created_at
                    FROM video_sales_moments
                    WHERE video_id = :video_id
                    ORDER BY video_sec ASC
                """)
                result = await db.execute(sql_fallback, {"video_id": video_id})
                rows = result.fetchall()
            except Exception:
                rows = []

        if not rows:
            # === CTA Fallback: video_sales_moments が空の場合 ===
            # video_phases の CTA スコアから代替モーメントを自動生成
            try:
                cta_sql = text("""
                    SELECT phase_index, time_start, time_end,
                           COALESCE(cta_score, 0) as cta_score
                    FROM video_phases
                    WHERE video_id = :video_id
                    ORDER BY phase_index ASC
                """)
                cta_result = await db.execute(cta_sql, {"video_id": video_id})
                cta_rows = cta_result.fetchall()
            except Exception:
                cta_rows = []

            if not cta_rows:
                return {
                    "video_id": video_id,
                    "categories": [],
                    "total_moments": 0,
                    "auto_zoom_data": [],
                }

            # CTA >= 4 → strong, CTA >= 3 → weak
            strong_moments = []
            weak_moments = []
            for cr in cta_rows:
                r = dict(cr._mapping)
                cta = float(r.get("cta_score", 0))
                t_start = float(r.get("time_start", 0))
                t_end = float(r.get("time_end", 0))
                mid = (t_start + t_end) / 2

                moment_data = {
                    "video_sec": mid,
                    "confidence": min(cta / 5.0, 1.0),
                    "reasons": [f"CTAスコア {cta:.0f}"],
                    "order_value": 0,
                    "click_value": 0,
                    "frame_meta": None,
                }
                if cta >= 4:
                    strong_moments.append(moment_data)
                elif cta >= 3:
                    weak_moments.append(moment_data)

            # カテゴリごとにクリップ候補を生成
            fallback_categories = []
            if video_duration <= 0 and cta_rows:
                video_duration = max((float(dict(cr._mapping).get("time_end", 0)) for cr in cta_rows), default=0.0)

            if strong_moments:
                strong_clips = _build_moment_category_clips(
                    strong_moments,
                    padding_before=MOMENT_CATEGORIES["strong"]["padding_before"],
                    padding_after=MOMENT_CATEGORIES["strong"]["padding_after"],
                    video_duration=video_duration,
                    merge_gap=10.0,
                )
                if strong_clips:
                    fallback_categories.append({
                        "category": "strong",
                        "label": MOMENT_CATEGORIES["strong"]["label"],
                        "icon": MOMENT_CATEGORIES["strong"]["icon"],
                        "description": MOMENT_CATEGORIES["strong"]["description"],
                        "clips": strong_clips,
                        "count": len(strong_clips),
                    })

            if weak_moments:
                weak_clips = _build_moment_category_clips(
                    weak_moments,
                    padding_before=MOMENT_CATEGORIES["weak"]["padding_before"],
                    padding_after=MOMENT_CATEGORIES["weak"]["padding_after"],
                    video_duration=video_duration,
                    merge_gap=10.0,
                )
                if weak_clips:
                    fallback_categories.append({
                        "category": "weak",
                        "label": MOMENT_CATEGORIES["weak"]["label"],
                        "icon": MOMENT_CATEGORIES["weak"]["icon"],
                        "description": MOMENT_CATEGORIES["weak"]["description"],
                        "clips": weak_clips,
                        "count": len(weak_clips),
                    })

            total = sum(c["count"] for c in fallback_categories)
            return {
                "video_id": video_id,
                "categories": fallback_categories,
                "total_moments": len(strong_moments) + len(weak_moments),
                "auto_zoom_data": [],
                "fallback_source": "cta_score",
            }

        # moment_type_detail でグループ化
        from collections import defaultdict
        grouped = defaultdict(list)
        auto_zoom_data = []

        for row in rows:
            r = dict(row._mapping)
            # JSON パース
            if r.get("reasons") and isinstance(r["reasons"], str):
                try:
                    r["reasons"] = json.loads(r["reasons"])
                except Exception:
                    r["reasons"] = [r["reasons"]]
            if r.get("frame_meta") and isinstance(r["frame_meta"], str):
                try:
                    r["frame_meta"] = json.loads(r["frame_meta"])
                except Exception:
                    r["frame_meta"] = None
            if r.get("created_at"):
                r["created_at"] = r["created_at"].isoformat()
            if r.get("id"):
                r["id"] = str(r["id"])
            if r.get("video_id"):
                r["video_id"] = str(r["video_id"])

            detail = r.get("moment_type_detail") or r.get("moment_type", "unknown")
            grouped[detail].append(r)

            # Auto Zoom データ収集
            if r.get("frame_meta"):
                fm = r["frame_meta"]
                if fm.get("face_region") or fm.get("product_region"):
                    auto_zoom_data.append({
                        "video_sec": r["video_sec"],
                        "face_region": fm.get("face_region"),
                        "product_region": fm.get("product_region"),
                    })

        # カテゴリごとにクリップ候補を生成
        categories = []
        for detail_type, cat_config in sorted(MOMENT_CATEGORIES.items(), key=lambda x: x[1]["priority"]):
            moments_in_cat = grouped.get(detail_type, [])
            if not moments_in_cat:
                continue

            # 近接するモーメントをマージしてクリップ化
            clips = _build_moment_category_clips(
                moments_in_cat,
                padding_before=cat_config["padding_before"],
                padding_after=cat_config["padding_after"],
                video_duration=video_duration,
                merge_gap=10.0,
            )

            categories.append({
                "category": detail_type,
                "label": cat_config["label"],
                "icon": cat_config["icon"],
                "description": cat_config["description"],
                "clips": clips,
                "count": len(clips),
            })

        return {
            "video_id": video_id,
            "categories": categories,
            "total_moments": len(rows),
            "auto_zoom_data": auto_zoom_data,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"[MOMENT_CLIPS] Failed for {video_id}: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to get moment clips: {exc}")


def _build_moment_category_clips(
    moments: list,
    padding_before: float = 15.0,
    padding_after: float = 15.0,
    video_duration: float = 0.0,
    merge_gap: float = 10.0,
) -> list:
    """
    同一カテゴリのモーメントを近接マージしてクリップ候補を生成する。
    """
    if not moments:
        return []

    # video_sec でソート
    sorted_moments = sorted(moments, key=lambda m: m.get("video_sec", 0))

    clips = []
    current_clip = None

    for m in sorted_moments:
        vsec = m.get("video_sec", 0)
        t_start = max(0, vsec - padding_before)
        t_end = vsec + padding_after
        if video_duration > 0:
            t_end = min(t_end, video_duration)

        if current_clip is None:
            current_clip = {
                "time_start": t_start,
                "time_end": t_end,
                "moments": [m],
                "best_confidence": m.get("confidence", 0),
            }
        elif t_start <= current_clip["time_end"] + merge_gap:
            # マージ
            current_clip["time_end"] = max(current_clip["time_end"], t_end)
            current_clip["moments"].append(m)
            current_clip["best_confidence"] = max(
                current_clip["best_confidence"], m.get("confidence", 0)
            )
        else:
            clips.append(current_clip)
            current_clip = {
                "time_start": t_start,
                "time_end": t_end,
                "moments": [m],
                "best_confidence": m.get("confidence", 0),
            }

    if current_clip:
        clips.append(current_clip)

    # confidence 降順でソート
    clips.sort(key=lambda c: c["best_confidence"], reverse=True)

    # クリップ候補に変換
    result = []
    for i, clip in enumerate(clips, 1):
        best_moment = max(clip["moments"], key=lambda m: m.get("confidence", 0))
        all_reasons = []
        for m in clip["moments"]:
            if m.get("reasons"):
                all_reasons.extend(m["reasons"] if isinstance(m["reasons"], list) else [m["reasons"]])

        # frame_meta を集約（最初に見つかったものを使用）
        frame_meta = None
        for m in clip["moments"]:
            if m.get("frame_meta"):
                frame_meta = m["frame_meta"]
                break

        result.append({
            "id": i,
            "time_start": round(clip["time_start"], 1),
            "time_end": round(clip["time_end"], 1),
            "duration": round(clip["time_end"] - clip["time_start"], 1),
            "video_sec": round(best_moment.get("video_sec", 0), 1),
            "confidence": round(clip["best_confidence"], 2),
            "moment_count": len(clip["moments"]),
            "reasons": all_reasons[:5],
            "order_value": sum(m.get("order_value", 0) for m in clip["moments"]),
            "click_value": sum(m.get("click_value", 0) for m in clip["moments"]),
            "frame_meta": frame_meta,
        })

    return result

