"""generate_dataset.py  –  AI学習用データセット生成ジョブ v7
=====================================================
仕様:
  ① 目的変数: y_click (click_spike窓重複), y_order (order_spike窓重複), y_strong
  ② 1行 = 1 event (phase)
  ③ event × sales_moment は window ±150s で結合、距離減衰 weight
  ④ 正例:負例 = 1:3 サンプリング
  ⑤ 情報リーク防止: GMV/注文数/クリック数は特徴量に入れない
v7 変更点 (品質特徴量統合 - Level 2):
  - frame_quality (JSONB) からフレーム品質特徴量5個を抽出:
    fq_blur_score, fq_brightness_mean, fq_brightness_std, fq_color_saturation, fq_scene_change_count
  - audio_features (JSONB) から音声品質特徴量7個を抽出:
    af_energy_mean, af_energy_max, af_pitch_mean, af_pitch_std,
    af_speech_rate, af_silence_ratio, af_energy_trend
  - 合計 76 + 12 = 88 特徴量
v6 変更点 (NGフィードバック統合)::
  - fetch_phases()にvideo_clips.is_unusable/unusable_reasonとclip_feedback.ratingをLEFT JOIN
  - NG特徴量追加:
    is_ng (is_unusable=TRUE OR feedback_rating='bad' → 1)
    ng_source ('unusable' / 'feedback' / 'both' / 'none')
    unusable_reason_* (6種 one-hot: irrelevant, too_short, too_long, no_product, audio_bad, low_quality)
    has_ng_feedback (clip_feedbackにrating='bad'があれば1)
    ng_reason_tag_count (reason_tagsの数)
  - NGフェーズ強制負例化: is_ng=1のフェーズはy_click=0, y_order=0, y_strong=0に強制上書き
  - dataset_stats.jsonにng_feedback集計追加

v5 変更点 (confidence weight):
  - sample_weightにconfidence-based重みを反映
    csv=1.0, purchase_popup=0.9, product_viewers_popup=0.75, viewer/comment_spike=0.5
  - confidence_weightフィールド追加

v4 変更点 (screen moment 統合):
  - video_sales_momentsのsourceカラムに対応 (csv / screen)
  - screen momentの教師信号をラベル計算に統合:
    purchase_popup → y_strong + y_order (最強信号)
    product_viewers_popup → y_click (興味信号)
    viewer_spike → y_click
    comment_spike → y_click
  - moment_sourceメタデータ追加 ('csv' / 'screen' / 'both' / 'none')
  - has_screen_moment, has_csv_moment フラグ追加
  - screen_purchase_popup, screen_product_viewers フラグ追加
  - dataset_stats.jsonにmoment_sources集計追加

v3 変更点:
  - human_sales_tags を行動タグ(8) + 販売心理タグ(14) の one-hot 特徴量に展開
  - user_rating (1-5) を数値特徴量として追加
  - user_comment からキーワード特徴量 + テキスト長を抽出
  - has_human_review フラグ追加（レビュー済みかどうか）
  - reviewer_name は補助メタデータとして出力（特徴量には入れない）

出力: train_click.jsonl / train_order.jsonl

特徴量 (v6):
  テキスト系: keyword flags (円/¥/割引/今だけ/残り/リンク/カート/タップ etc.)
              数字出現フラグ, text_length
  構造系:     event_type, event_duration, event_position_min
  商品系:     product_match, top_product_name_in_text
  CTA系:      cta_score, importance_score (AI生成なのでリークではない)
  人間レビュー系:
    user_rating (1-5, 未評価=0)
    has_human_review (0/1)
    human_tag_count (選択されたタグ数)
    htag_HOOK, htag_CHAT, ... (行動タグ one-hot × 8)
    htag_EMPATHY, htag_PROBLEM, ... (販売心理タグ one-hot × 14)
    comment_length (コメント文字数)
    comment_kw_price, comment_kw_cta, ... (コメントからのキーワードフラグ)
  NGフィードバック系 (v6):
    is_ng (0/1: is_unusable OR feedback_rating='bad')
    ng_source ('unusable'/'feedback'/'both'/'none')
    has_ng_feedback (0/1)
    ng_reason_tag_count (タグ数)
    unusable_reason_irrelevant, unusable_reason_too_short, unusable_reason_too_long,
    unusable_reason_no_product, unusable_reason_audio_bad, unusable_reason_low_quality (one-hot)
  品質特徴量 (v7):
    fq_blur_score, fq_brightness_mean, fq_brightness_std, fq_color_saturation, fq_scene_change_count
    af_energy_mean, af_energy_max, af_pitch_mean, af_pitch_std,
    af_speech_rate, af_silence_ratio, af_energy_trend

使い方:
  python generate_dataset.py --output-dir /tmp/datasets
  python generate_dataset.py --video-id abc-123 --output-dir /tmp/datasets
"""

import argparse
import json
import math
import os
import random
import re
import sys
import asyncio
from collections import defaultdict
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(__file__))
load_dotenv()

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
import ssl as _ssl
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

DATABASE_URL = os.getenv("DATABASE_URL")

# DB engine is lazily initialized (only when generate() is called)
engine = None
AsyncSessionLocal = None

def _prepare_database_url(url: str):
    """Strip sslmode from URL for asyncpg compatibility."""
    parsed = urlparse(url)
    qp = parse_qs(parsed.query)
    connect_args = {}
    if "sslmode" in qp:
        mode = qp.pop("sslmode")[0]
        if mode == "require":
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
            connect_args["ssl"] = ctx
    if "ssl" in qp:
        mode = qp.pop("ssl")[0]
        if mode == "require":
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
            connect_args["ssl"] = ctx
    new_query = urlencode(qp, doseq=True)
    cleaned = urlunparse(parsed._replace(query=new_query))
    return cleaned, connect_args

def _init_db():
    global engine, AsyncSessionLocal
    if engine is not None:
        return
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    cleaned_url, connect_args = _prepare_database_url(DATABASE_URL)
    engine = create_async_engine(cleaned_url, pool_pre_ping=True, echo=False,
                                 connect_args=connect_args)
    AsyncSessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

# ── Config ──
MOMENT_WINDOW_SEC = 150       # ±150s window for label matching
WEIGHT_DECAY_TAU = 60.0       # exp(-d/tau) decay constant
NEG_RATIO = 3                 # negative:positive ratio
RANDOM_SEED = 42

# ── Human Sales Tags (must match frontend/backend definitions) ──
# Behavior tags (行動タグ)
BEHAVIOR_TAGS = [
    "HOOK", "CHAT", "PREP", "PHONE_OP",
    "LONG_GREET", "COMMENT_READ", "SILENCE", "PRICE_SHOW",
]

# Sales psychology tags (販売心理タグ)
PSYCHOLOGY_TAGS = [
    "EMPATHY", "PROBLEM", "EDUCATION", "SOLUTION",
    "DEMONSTRATION", "COMPARISON", "PROOF", "TRUST", "SOCIAL_PROOF",
    "OBJECTION_HANDLING", "URGENCY", "LIMITED_OFFER", "BONUS", "CTA",
]

# Combined: all human-assignable tags
ALL_HUMAN_TAGS = BEHAVIOR_TAGS + PSYCHOLOGY_TAGS

# Feature name prefix for human tags
HUMAN_TAG_FEATURES = [f"htag_{t}" for t in ALL_HUMAN_TAGS]

# ── Keyword flags for feature extraction (phase description) ──
# 各キーワードグループ: (flag_name, [patterns])
KEYWORD_GROUPS = [
    ("kw_price",      [r"円", r"¥", r"\d+円", r"価格", r"値段", r"プライス"]),
    ("kw_discount",   [r"割引", r"割", r"OFF", r"オフ", r"セール", r"半額", r"お得", r"特別価格"]),
    ("kw_urgency",    [r"今だけ", r"限定", r"残り", r"ラスト", r"早い者勝ち", r"なくなり次第", r"本日限り"]),
    ("kw_cta",        [r"リンク", r"カート", r"タップ", r"クリック", r"押して", r"ポチ", r"購入", r"買って"]),
    ("kw_quantity",   [r"残り\d+", r"\d+個", r"\d+点", r"在庫", r"ストック"]),
    ("kw_comparison", [r"通常", r"定価", r"普通", r"比べ", r"違い", r"他と"]),
    ("kw_quality",    [r"品質", r"成分", r"効果", r"おすすめ", r"人気", r"ランキング"]),
    ("kw_number",     [r"\d{3,}"]),  # 3桁以上の数字 = 価格っぽい
]

# ── NG Unusable Reason categories (for one-hot encoding) ──
UNUSABLE_REASONS = [
    "irrelevant",
    "too_short",
    "too_long",
    "no_product",
    "audio_bad",
    "low_quality",
]


def extract_ng_features(is_unusable, unusable_reason, feedback_rating, feedback_reason_tags) -> dict:
    """NGフィードバック情報から特徴量を抽出.

    Args:
        is_unusable: video_clips.is_unusable (bool or None)
        unusable_reason: video_clips.unusable_reason (str or None)
        feedback_rating: clip_feedback.rating (str or None)
        feedback_reason_tags: clip_feedback.reason_tags (list/dict or None)

    Returns:
        dict with NG feature values
    """
    clip_unusable = bool(is_unusable) if is_unusable is not None else False
    has_bad_feedback = (feedback_rating == 'bad') if feedback_rating else False

    is_ng = 1 if (clip_unusable or has_bad_feedback) else 0

    # Determine NG source
    if clip_unusable and has_bad_feedback:
        ng_source = "both"
    elif clip_unusable:
        ng_source = "unusable"
    elif has_bad_feedback:
        ng_source = "feedback"
    else:
        ng_source = "none"

    # Unusable reason one-hot
    reason_lower = (unusable_reason or "").lower().strip()
    reason_features = {}
    for reason in UNUSABLE_REASONS:
        reason_features[f"unusable_reason_{reason}"] = 1 if reason in reason_lower else 0

    # Feedback reason tags count
    reason_tags = feedback_reason_tags
    if isinstance(reason_tags, str):
        try:
            reason_tags = json.loads(reason_tags)
        except (json.JSONDecodeError, TypeError):
            reason_tags = []
    if not isinstance(reason_tags, (list, dict)):
        reason_tags = []
    ng_reason_tag_count = len(reason_tags) if isinstance(reason_tags, list) else len(reason_tags.keys()) if isinstance(reason_tags, dict) else 0

    return {
        "is_ng": is_ng,
        "ng_source": ng_source,
        "has_ng_feedback": 1 if has_bad_feedback else 0,
        "ng_reason_tag_count": ng_reason_tag_count,
        **reason_features,
    }


# ── Keyword flags for user_comment extraction ──
COMMENT_KEYWORD_GROUPS = [
    ("comment_kw_price",    [r"円", r"¥", r"価格", r"値段", r"安", r"高"]),
    ("comment_kw_cta",      [r"CTA", r"購入", r"カート", r"リンク", r"誘導"]),
    ("comment_kw_positive", [r"良", r"上手", r"うまい", r"すごい", r"最高", r"神", r"完璧"]),
    ("comment_kw_negative", [r"弱", r"微妙", r"改善", r"もっと", r"足りない", r"ダメ", r"悪"]),
    ("comment_kw_emotion",  [r"感情", r"共感", r"泣", r"笑", r"熱", r"テンション"]),
    ("comment_kw_timing",   [r"タイミング", r"早", r"遅", r"長", r"短", r"間"]),
]


def extract_keyword_flags(text_str: str) -> dict:
    """テキストからキーワードフラグを抽出."""
    if not text_str:
        return {g[0]: 0 for g in KEYWORD_GROUPS}
    flags = {}
    for flag_name, patterns in KEYWORD_GROUPS:
        matched = 0
        for pat in patterns:
            if re.search(pat, text_str, re.IGNORECASE):
                matched = 1
                break
        flags[flag_name] = matched
    return flags


def extract_comment_keyword_flags(comment: str) -> dict:
    """user_commentからキーワードフラグを抽出."""
    if not comment:
        return {g[0]: 0 for g in COMMENT_KEYWORD_GROUPS}
    flags = {}
    for flag_name, patterns in COMMENT_KEYWORD_GROUPS:
        matched = 0
        for pat in patterns:
            if re.search(pat, comment, re.IGNORECASE):
                matched = 1
                break
        flags[flag_name] = matched
    return flags


def extract_text_features(text_str: str) -> dict:
    """テキスト長と数字出現フラグ."""
    if not text_str:
        return {"text_length": 0, "has_number": 0, "exclamation_count": 0}
    return {
        "text_length": len(text_str),
        "has_number": 1 if re.search(r"\d+", text_str) else 0,
        "exclamation_count": text_str.count("！") + text_str.count("!"),
    }


def extract_human_tag_features(human_tags: list) -> dict:
    """human_sales_tagsをone-hotフラグに展開."""
    tag_set = set(human_tags) if human_tags else set()
    features = {}
    for tag in ALL_HUMAN_TAGS:
        features[f"htag_{tag}"] = 1 if tag in tag_set else 0
    return features


def extract_comment_features(comment: str) -> dict:
    """user_commentからテキスト特徴量を抽出."""
    if not comment:
        return {"comment_length": 0, **{g[0]: 0 for g in COMMENT_KEYWORD_GROUPS}}
    return {
        "comment_length": len(comment),
        **extract_comment_keyword_flags(comment),
    }


# ── DB Fetch Functions ──

async def fetch_phases(session, video_id=None, user_id=None):
    """Fetch video_phases with safe columns only (no GMV/order/click)."""
    conditions = []
    params = {}
    if video_id:
        conditions.append("vp.video_id = :video_id")
        params["video_id"] = video_id
    if user_id:
        conditions.append("vp.user_id = :user_id")
        params["user_id"] = user_id
    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    sql = text(f"""
        SELECT
            vp.video_id,
            vp.user_id,
            vp.phase_index,
            vp.phase_description,
            vp.time_start,
            vp.time_end,
            vp.cta_score,
            vp.sales_psychology_tags,
            vp.human_sales_tags,
            vp.user_rating,
            vp.user_comment,
            vp.reviewer_name,
            COALESCE(vp.importance_score, 0) as importance_score,
            vp.group_id,
            -- NG feedback columns (v6)
            COALESCE(vc.is_unusable, FALSE) as is_unusable,
            vc.unusable_reason,
            cf.rating as feedback_rating,
            cf.reason_tags as feedback_reason_tags,
            -- Quality features (v7)
            vp.frame_quality,
            vp.audio_features
        FROM video_phases vp
        LEFT JOIN video_clips vc
            ON vc.video_id = vp.video_id
            AND vc.phase_index = vp.phase_index::text
        LEFT JOIN clip_feedback cf
            ON cf.video_id = vp.video_id
            AND cf.phase_index = vp.phase_index::text
        {where}
        ORDER BY vp.video_id, vp.phase_index
    """)
    result = await session.execute(sql, params)
    return result.fetchall()


async def fetch_sales_moments(session, video_id=None):
    """Fetch all sales moments (both csv and screen sources)."""
    params = {}
    where = ""
    if video_id:
        where = "WHERE video_id = :video_id"
        params["video_id"] = video_id

    sql = text(f"""
        SELECT video_id, video_sec, moment_type,
               COALESCE(moment_type_detail, moment_type) as moment_type_detail,
               COALESCE(source, 'csv') as source,
               confidence
        FROM video_sales_moments
        {where}
        ORDER BY video_id, video_sec
    """)
    result = await session.execute(sql, params)
    return result.fetchall()


async def fetch_product_stats(session, video_id=None):
    """Fetch product stats for product name matching."""
    params = {}
    where = ""
    if video_id:
        where = "WHERE video_id = :video_id"
        params["video_id"] = video_id

    sql = text(f"""
        SELECT video_id, product_name, product_clicks, gmv
        FROM video_product_stats
        {where}
        ORDER BY video_id, COALESCE(product_clicks, 0) DESC
    """)
    try:
        result = await session.execute(sql, params)
        return result.fetchall()
    except Exception:
        return []


async def fetch_video_durations(session, video_ids: list):
    """Fetch video durations for event_position normalization."""
    if not video_ids:
        return {}
    sql = text("""
        SELECT video_id, duration_seconds
        FROM videos
        WHERE video_id = ANY(:ids)
    """)
    try:
        result = await session.execute(sql, {"ids": video_ids})
        return {str(r.video_id): float(r.duration_seconds or 0) for r in result.fetchall()}
    except Exception:
        return {}


# ── Index Builders ──

def build_moments_index(moments_rows):
    """Build {video_id: [moment_dict, ...]}.

    Each moment dict now includes source and moment_type_detail
    for csv/screen distinction in output.
    """
    idx = defaultdict(list)
    for r in moments_rows:
        idx[str(r.video_id)].append({
            "video_sec": float(r.video_sec),
            "moment_type": r.moment_type,
            "moment_type_detail": getattr(r, 'moment_type_detail', r.moment_type),
            "source": getattr(r, 'source', 'csv'),
            "confidence": r.confidence,
        })
    return dict(idx)


def build_product_names_index(product_rows):
    """Build {video_id: [product_name, ...]} (top products first)."""
    idx = defaultdict(list)
    for r in product_rows:
        name = r.product_name
        if name:
            idx[str(r.video_id)].append(name)
    return dict(idx)


# ── Label Computation ──

def compute_labels_v2(phase_start: float, phase_end: float, moments: list):
    """
    Compute labels with distance-weighted scoring.

    Supports both CSV and screen sources:
      - CSV moments: click_spike, order_spike, strong
      - Screen moments: purchase_popup, product_viewers_popup, viewer_spike, comment_spike

    Returns:
      y_click: 1 if click-like moment within ±MOMENT_WINDOW_SEC
      y_order: 1 if order-like moment within ±MOMENT_WINDOW_SEC
      y_strong: 1 if strong/purchase_popup within ±MOMENT_WINDOW_SEC
      weight_click: max distance-decay weight for click moments
      weight_order: max distance-decay weight for order moments
      nearest_click_sec: distance to nearest click-like moment
      nearest_order_sec: distance to nearest order-like moment
      moment_source: 'csv', 'screen', 'both', or 'none'
      has_screen_moment: 1 if any screen moment matched
      has_csv_moment: 1 if any csv moment matched
      screen_purchase_popup: 1 if purchase_popup detected
      screen_product_viewers: 1 if product_viewers_popup detected
    """
    phase_mid = (phase_start + phase_end) / 2

    y_click = 0
    y_order = 0
    y_strong = 0
    weight_click = 0.0
    weight_order = 0.0
    nearest_click = None
    nearest_order = None
    has_csv = 0
    has_screen = 0
    screen_purchase = 0
    screen_viewers = 0

    # Moment type mapping:
    # CSV:    click_spike, order_spike, strong → click/order/strong
    # Screen: purchase_popup → strong (strongest signal)
    #         product_viewers_popup → click (interest signal)
    #         viewer_spike → click
    #         comment_spike → click
    CLICK_TYPES = {"click_spike", "click", "product_viewers_popup", "viewer_spike", "comment_spike"}
    ORDER_TYPES = {"order_spike", "purchase_popup"}
    STRONG_TYPES = {"strong", "purchase_popup"}

    for m in moments:
        sec = m["video_sec"]
        dist = abs(sec - phase_mid)

        if dist > MOMENT_WINDOW_SEC:
            continue

        w = math.exp(-dist / WEIGHT_DECAY_TAU)
        mtype = m["moment_type"]
        detail = m.get("moment_type_detail", mtype)
        source = m.get("source", "csv")

        # Track source presence
        if source == "csv":
            has_csv = 1
        elif source == "screen":
            has_screen = 1
            if detail == "purchase_popup":
                screen_purchase = 1
            elif detail == "product_viewers_popup":
                screen_viewers = 1

        # Use detail for finer classification when available
        effective_type = detail if detail else mtype

        if effective_type in CLICK_TYPES or effective_type in STRONG_TYPES:
            y_click = 1
            weight_click = max(weight_click, w)
            if nearest_click is None or dist < nearest_click:
                nearest_click = dist

        if effective_type in ORDER_TYPES or effective_type in STRONG_TYPES:
            y_order = 1
            weight_order = max(weight_order, w)
            if nearest_order is None or dist < nearest_order:
                nearest_order = dist

        if effective_type in STRONG_TYPES:
            y_strong = 1

    # Determine combined source
    if has_csv and has_screen:
        moment_source = "both"
    elif has_csv:
        moment_source = "csv"
    elif has_screen:
        moment_source = "screen"
    else:
        moment_source = "none"

    return {
        "y_click": y_click,
        "y_order": y_order,
        "y_strong": y_strong,
        "weight_click": round(weight_click, 4),
        "weight_order": round(weight_order, 4),
        "nearest_click_sec": round(nearest_click, 1) if nearest_click is not None else None,
        "nearest_order_sec": round(nearest_order, 1) if nearest_order is not None else None,
        "moment_source": moment_source,
        "has_screen_moment": has_screen,
        "has_csv_moment": has_csv,
        "screen_purchase_popup": screen_purchase,
        "screen_product_viewers": screen_viewers,
    }


def check_product_in_text(text_str: str, product_names: list) -> dict:
    """Check if top product names appear in phase text (partial match)."""
    if not text_str or not product_names:
        return {"product_match": 0, "product_match_top3": 0, "matched_product_count": 0}

    text_lower = text_str.lower()
    matched = 0
    matched_top3 = 0

    for i, name in enumerate(product_names):
        if not name:
            continue
        # Use first 6 chars for partial match (product names can be long)
        short_name = name[:6].lower().strip()
        if len(short_name) >= 2 and short_name in text_lower:
            matched += 1
            if i < 3:
                matched_top3 = 1

    return {
        "product_match": 1 if matched > 0 else 0,
        "product_match_top3": matched_top3,
        "matched_product_count": matched,
    }


def parse_json_field(raw):
    """Safely parse a JSON text field."""
    if not raw:
        return []
    try:
        if isinstance(raw, str):
            return json.loads(raw)
        return raw
    except (json.JSONDecodeError, TypeError):
        return []


# ── Quality Feature Extraction (v7) ──

FRAME_QUALITY_KEYS = ["blur_score", "brightness_mean", "brightness_std", "color_saturation", "scene_change_count"]
AUDIO_FEATURE_KEYS = ["energy_mean", "energy_max", "pitch_mean", "pitch_std", "speech_rate", "silence_ratio", "energy_trend"]


def extract_quality_features(frame_quality_raw, audio_features_raw) -> dict:
    """Extract quality features from JSONB columns.

    frame_quality: {"blur_score": ..., "brightness_mean": ..., ...}
    audio_features: {"energy_mean": ..., "energy_max": ..., ...}

    Returns dict with fq_* and af_* prefixed feature names.
    Missing values default to 0.0.
    """
    fq = {}
    if frame_quality_raw:
        if isinstance(frame_quality_raw, str):
            try:
                frame_quality_raw = json.loads(frame_quality_raw)
            except (json.JSONDecodeError, TypeError):
                frame_quality_raw = {}
        for key in FRAME_QUALITY_KEYS:
            val = frame_quality_raw.get(key)
            fq[f"fq_{key}"] = float(val) if val is not None else 0.0
    else:
        for key in FRAME_QUALITY_KEYS:
            fq[f"fq_{key}"] = 0.0

    af = {}
    if audio_features_raw:
        if isinstance(audio_features_raw, str):
            try:
                audio_features_raw = json.loads(audio_features_raw)
            except (json.JSONDecodeError, TypeError):
                audio_features_raw = {}
        for key in AUDIO_FEATURE_KEYS:
            val = audio_features_raw.get(key)
            if val is None:
                af[f"af_{key}"] = 0.0
            elif isinstance(val, (int, float)):
                af[f"af_{key}"] = float(val)
            elif isinstance(val, str):
                # energy_trend can be 'rising', 'falling', 'stable' etc.
                trend_map = {'rising': 1.0, 'stable': 0.0, 'falling': -1.0}
                af[f"af_{key}"] = trend_map.get(val.lower(), 0.0)
            else:
                af[f"af_{key}"] = 0.0
    else:
        for key in AUDIO_FEATURE_KEYS:
            af[f"af_{key}"] = 0.0

    return {**fq, **af}


# ── Main Generation ──

async def generate(output_dir: str, video_id=None, user_id=None):
    """Main dataset generation."""
    _init_db()
    random.seed(RANDOM_SEED)

    async with AsyncSessionLocal() as session:
        print("[dataset] Fetching phases...")
        phases = await fetch_phases(session, video_id=video_id, user_id=user_id)
        print(f"[dataset] Found {len(phases)} phases")

        if not phases:
            print("[dataset] No phases found. Exiting.")
            return 0

        video_ids = list(set(str(r.video_id) for r in phases))
        print(f"[dataset] Spanning {len(video_ids)} videos")
        print(f"[dataset] Fetching sales moments...")
        try:
            moments_rows = await fetch_sales_moments(session, video_id=video_id)
            n_csv = sum(1 for r in moments_rows if getattr(r, 'source', 'csv') == 'csv')
            n_screen = sum(1 for r in moments_rows if getattr(r, 'source', 'csv') == 'screen')
            print(f"[dataset] Found {len(moments_rows)} sales moments "
                  f"(csv={n_csv}, screen={n_screen})")
        except Exception as e:
            print(f"[dataset] Warning: Could not fetch sales moments: {e}")
            moments_rows = []
            n_csv = n_screen = 0

        print("[dataset] Fetching product stats...")
        try:
            product_rows = await fetch_product_stats(session, video_id=video_id)
            print(f"[dataset] Found {len(product_rows)} product stats")
        except Exception as e:
            print(f"[dataset] Warning: Could not fetch product stats: {e}")
            product_rows = []

        print("[dataset] Fetching video durations...")
        durations = await fetch_video_durations(session, video_ids)

    await engine.dispose()

    # Build indexes
    moments_idx = build_moments_index(moments_rows)
    products_idx = build_product_names_index(product_rows)

    # ── Build all records ──
    all_records = []
    n_reviewed = 0
    n_ng_phases = 0
    n_ng_forced_negative = 0

    for r in phases:
        vid = str(r.video_id)
        phase_start = float(r.time_start) if r.time_start is not None else 0.0
        phase_end = float(r.time_end) if r.time_end is not None else 0.0
        duration = phase_end - phase_start

        if duration <= 0:
            continue

        # AI tags
        tags = parse_json_field(r.sales_psychology_tags)
        event_type = tags[0] if tags else "UNKNOWN"

        # Human review data
        human_tags = parse_json_field(r.human_sales_tags)
        user_rating = int(r.user_rating) if r.user_rating is not None else 0
        user_comment = r.user_comment or ""
        reviewer_name = r.reviewer_name or ""
        has_human_review = 1 if (user_rating > 0 or len(human_tags) > 0 or len(user_comment) > 0) else 0

        if has_human_review:
            n_reviewed += 1

        # NG feedback features (v6)
        ng_feats = extract_ng_features(
            is_unusable=getattr(r, 'is_unusable', None),
            unusable_reason=getattr(r, 'unusable_reason', None),
            feedback_rating=getattr(r, 'feedback_rating', None),
            feedback_reason_tags=getattr(r, 'feedback_reason_tags', None),
        )
        if ng_feats["is_ng"]:
            n_ng_phases += 1

        # Description text for feature extraction
        desc = r.phase_description or ""

        # Labels
        video_moments = moments_idx.get(vid, [])
        labels = compute_labels_v2(phase_start, phase_end, video_moments)

        # ★ CRITICAL: NGフェーズは強制的に負例にする (v6)
        # is_unusable=TRUE or feedback_rating='bad' → 全ラベルを0に上書き
        if ng_feats["is_ng"]:
            was_positive = labels["y_click"] == 1 or labels["y_order"] == 1
            labels["y_click"] = 0
            labels["y_order"] = 0
            labels["y_strong"] = 0
            labels["weight_click"] = 0.0
            labels["weight_order"] = 0.0
            if was_positive:
                n_ng_forced_negative += 1

        # Product match
        video_products = products_idx.get(vid, [])
        product_features = check_product_in_text(desc, video_products)

        # Keyword flags (from phase description)
        kw_flags = extract_keyword_flags(desc)

        # Text features (from phase description)
        text_feats = extract_text_features(desc)

        # Human tag one-hot features
        htag_features = extract_human_tag_features(human_tags)

        # Comment features
        comment_feats = extract_comment_features(user_comment)

        # Position in stream (minutes from start)
        video_duration = durations.get(vid, 0)
        event_position_min = round(phase_start / 60.0, 1)
        event_position_pct = round(phase_start / video_duration, 3) if video_duration > 0 else 0.0

        record = {
            # Identity (not features)
            "video_id": vid,
            "user_id": r.user_id,
            "phase_index": r.phase_index,

            # ── FEATURES (safe, no information leak) ──

            # Structure
            "event_type": event_type,
            "event_duration": round(duration, 1),
            "event_position_min": event_position_min,
            "event_position_pct": round(event_position_pct, 3),
            "tag_count": len(tags),

            # CTA / importance (AI-generated, not leaked)
            "cta_score": r.cta_score or 0,
            "importance_score": float(r.importance_score),

            # Text (from phase description)
            **text_feats,

            # Keywords (from phase description)
            **kw_flags,

            # Product
            **product_features,

            # ── HUMAN REVIEW FEATURES (v3) ──
            "user_rating": user_rating,
            "has_human_review": has_human_review,
            "human_tag_count": len(human_tags),

            # Human tag one-hot (行動タグ 8 + 販売心理タグ 14 = 22 features)
            **htag_features,

            # Comment features
            **comment_feats,

            # ── NG FEEDBACK FEATURES (v6) ──
            **ng_feats,

            # ── QUALITY FEATURES (v7) ──
            **extract_quality_features(
                getattr(r, 'frame_quality', None),
                getattr(r, 'audio_features', None),
            ),

            # ── METADATA (not used as features in training) ──
            "tags": tags,
            "human_tags": human_tags,
            "reviewer_name": reviewer_name,
            "text": desc[:200],  # truncated for reference
            "comment_text": user_comment[:200] if user_comment else "",

            # ── LABELS ──
            **labels,
        }

        all_records.append(record)

    print(f"[dataset] Human-reviewed phases: {n_reviewed} / {len(all_records)}"
          f" ({n_reviewed / max(len(all_records), 1) * 100:.1f}%)")
    print(f"[dataset] NG phases: {n_ng_phases} / {len(all_records)}"
          f" (forced negative override: {n_ng_forced_negative})")
    if n_ng_phases > 0:
        print(f"[dataset] NG breakdown: "
              f"{sum(1 for r in all_records if r.get('ng_source') == 'unusable')} unusable, "
              f"{sum(1 for r in all_records if r.get('ng_source') == 'feedback')} feedback, "
              f"{sum(1 for r in all_records if r.get('ng_source') == 'both')} both")

    # ── Split into positive/negative and sample ──
    os.makedirs(output_dir, exist_ok=True)

    stats = {}
    for target in ["click", "order"]:
        y_key = f"y_{target}"
        w_key = f"weight_{target}"

        positives = [r for r in all_records if r[y_key] == 1]
        negatives = [r for r in all_records if r[y_key] == 0]

        n_pos = len(positives)
        n_neg = len(negatives)

        # Sample negatives to maintain ratio
        max_neg = n_pos * NEG_RATIO
        if n_neg > max_neg and max_neg > 0:
            negatives_sampled = random.sample(negatives, max_neg)
        else:
            negatives_sampled = negatives

        dataset = positives + negatives_sampled
        random.shuffle(dataset)

        # Write JSONL
        output_path = os.path.join(output_dir, f"train_{target}.jsonl")
        with open(output_path, "w", encoding="utf-8") as f:
            for rec in dataset:
                # Add sample_weight for training
                # Confidence-based weight: csv=1.0, screen varies by type
                # purchase_popup=0.9, product_viewers_popup=0.75, viewer/comment_spike=0.5
                if rec[y_key] == 1:
                    base_w = rec[w_key]  # distance-decay weight
                    source = rec.get("moment_source", "none")
                    if source == "screen":
                        # Screen-only: apply confidence discount
                        if rec.get("screen_purchase_popup"):
                            confidence_w = 0.9
                        elif rec.get("screen_product_viewers"):
                            confidence_w = 0.75
                        else:
                            confidence_w = 0.5  # viewer_spike / comment_spike
                        rec["sample_weight"] = base_w * confidence_w
                        rec["confidence_weight"] = confidence_w
                    elif source == "both":
                        # Both sources: csv dominates, slight boost
                        rec["sample_weight"] = base_w * 1.0
                        rec["confidence_weight"] = 1.0
                    else:
                        # CSV only: full weight
                        rec["sample_weight"] = base_w
                        rec["confidence_weight"] = 1.0
                else:
                    rec["sample_weight"] = 1.0
                    rec["confidence_weight"] = 1.0
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        stats[target] = {
            "total": len(dataset),
            "positive": n_pos,
            "negative_sampled": len(negatives_sampled),
            "negative_total": n_neg,
            "path": output_path,
        }

        print(f"\n[dataset] {target}: {len(dataset)} records → {output_path}")
        print(f"  positive: {n_pos}, negative: {len(negatives_sampled)} (from {n_neg})")
        if n_pos > 0:
            print(f"  positive rate: {n_pos / len(dataset) * 100:.1f}%")

    # Source distribution stats
    source_counts = defaultdict(int)
    for r in all_records:
        source_counts[r.get("moment_source", "none")] += 1

    # Also write combined stats
    stats["human_review"] = {
        "reviewed_phases": n_reviewed,
        "total_phases": len(all_records),
        "review_rate": round(n_reviewed / max(len(all_records), 1), 4),
        "human_tag_features": HUMAN_TAG_FEATURES,
        "comment_keyword_features": [g[0] for g in COMMENT_KEYWORD_GROUPS],
    }
    stats["moment_sources"] = {
        "csv_moments": n_csv,
        "screen_moments": n_screen,
        "total_moments": n_csv + n_screen,
        "phases_by_source": dict(source_counts),
    }
    # NG feedback stats (v6)
    ng_source_counts = defaultdict(int)
    for r in all_records:
        ng_source_counts[r.get("ng_source", "none")] += 1
    stats["ng_feedback"] = {
        "total_ng_phases": n_ng_phases,
        "forced_negative_overrides": n_ng_forced_negative,
        "ng_by_source": dict(ng_source_counts),
        "unusable_reason_features": [f"unusable_reason_{r}" for r in UNUSABLE_REASONS],
    }
    # Quality features stats (v7)
    n_with_fq = sum(1 for r in all_records if r.get("fq_blur_score", 0) > 0)
    n_with_af = sum(1 for r in all_records if r.get("af_energy_mean", 0) > 0)
    stats["quality_features"] = {
        "phases_with_frame_quality": n_with_fq,
        "phases_with_audio_features": n_with_af,
        "total_phases": len(all_records),
        "frame_quality_coverage": round(n_with_fq / max(len(all_records), 1), 4),
        "audio_features_coverage": round(n_with_af / max(len(all_records), 1), 4),
        "frame_quality_features": [f"fq_{k}" for k in FRAME_QUALITY_KEYS],
        "audio_feature_names": [f"af_{k}" for k in AUDIO_FEATURE_KEYS],
    }
    print(f"[dataset] Quality features: frame_quality={n_with_fq}/{len(all_records)} "
          f"({n_with_fq / max(len(all_records), 1) * 100:.1f}%), "
          f"audio_features={n_with_af}/{len(all_records)} "
          f"({n_with_af / max(len(all_records), 1) * 100:.1f}%)")
    stats_path = os.path.join(output_dir, "dataset_stats.json")
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    print(f"\n[dataset] Stats → {stats_path}")

    return len(all_records)


def main():
    parser = argparse.ArgumentParser(description="Generate AI training dataset v7")
    parser.add_argument("--output-dir", "-o", default="/tmp/datasets",
                        help="Output directory for JSONL files")
    parser.add_argument("--video-id", default=None,
                        help="Filter by specific video ID")
    parser.add_argument("--user-id", type=int, default=None,
                        help="Filter by specific user ID")
    args = parser.parse_args()

    count = asyncio.run(generate(args.output_dir, video_id=args.video_id, user_id=args.user_id))
    if count == 0:
        print("[dataset] WARNING: No records generated.")
        sys.exit(1)


if __name__ == "__main__":
    main()
