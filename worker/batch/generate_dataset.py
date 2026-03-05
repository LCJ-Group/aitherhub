"""
generate_dataset.py  –  AI学習用データセット生成ジョブ
=====================================================
video_phases × video_sales_moments × video_product_exposures を結合し、
train.jsonl を生成する。

出力フォーマット (1行 = 1 event/phase):
{
  "video_id": "...",
  "phase_index": 0,
  "event_type": "PRICE",          # sales_psychology_tags の先頭 or "UNKNOWN"
  "tags": ["PRICE","CTA"],        # 全タグ
  "cta_score": 4,
  "phase_description": "...",
  "time_start": 120.0,
  "time_end": 148.0,
  "duration": 28.0,

  # CSV metrics (特徴量)
  "gmv": 1200,
  "order_count": 3,
  "viewer_count": 450,
  "like_count": 12,
  "comment_count": 5,
  "product_clicks": 18,
  "conversion_rate": 0.03,
  "gpm": 2666.7,
  "importance_score": 0.72,

  # 商品マッチ
  "product_match": true,
  "product_names": ["商品A"],

  # 正解ラベル
  "has_click_spike": 1,
  "has_order_spike": 1,
  "has_strong": 1,
  "label_strong_window": 1,       # ±150s窓にstrongがあるか
  "nearest_strong_sec": 135.0,    # 最も近いstrongまでの秒数

  # 音声特徴量 (あれば)
  "audio_energy_mean": 0.45,
  "audio_tempo": 3.2,
  "audio_pitch_mean": 220.0,

  # メタ
  "user_id": 1
}

使い方:
  # 全ユーザー・全動画
  python generate_dataset.py --output /tmp/train.jsonl

  # 特定ユーザー
  python generate_dataset.py --user-id 5 --output /tmp/train.jsonl

  # 特定動画
  python generate_dataset.py --video-id abc-123 --output /tmp/train.jsonl
"""

import argparse
import json
import os
import sys
import asyncio
from dotenv import load_dotenv

# Add parent paths
sys.path.insert(0, os.path.dirname(__file__))

load_dotenv()

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True, echo=False)
AsyncSessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


# ── Window size for label matching ──
STRONG_WINDOW_SEC = 150  # ±150s


async def fetch_phases(session, video_id=None, user_id=None):
    """Fetch all video_phases with their metrics."""
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
            vp.audio_features,
            COALESCE(vp.gmv, 0) as gmv,
            COALESCE(vp.order_count, 0) as order_count,
            COALESCE(vp.viewer_count, 0) as viewer_count,
            COALESCE(vp.like_count, 0) as like_count,
            COALESCE(vp.comment_count, 0) as comment_count,
            COALESCE(vp.share_count, 0) as share_count,
            COALESCE(vp.new_followers, 0) as new_followers,
            COALESCE(vp.product_clicks, 0) as product_clicks,
            COALESCE(vp.conversion_rate, 0) as conversion_rate,
            COALESCE(vp.gpm, 0) as gpm,
            COALESCE(vp.importance_score, 0) as importance_score,
            vp.group_id
        FROM video_phases vp
        {where}
        ORDER BY vp.video_id, vp.phase_index
    """)

    result = await session.execute(sql, params)
    return result.fetchall()


async def fetch_sales_moments(session, video_id=None):
    """Fetch all sales moments, optionally filtered by video_id."""
    if video_id:
        sql = text("""
            SELECT video_id, video_sec, moment_type,
                   click_value, click_delta, click_sigma_score,
                   order_value, order_delta, gmv_value,
                   confidence
            FROM video_sales_moments
            WHERE video_id = :video_id
            ORDER BY video_id, video_sec
        """)
        result = await session.execute(sql, {"video_id": video_id})
    else:
        sql = text("""
            SELECT video_id, video_sec, moment_type,
                   click_value, click_delta, click_sigma_score,
                   order_value, order_delta, gmv_value,
                   confidence
            FROM video_sales_moments
            ORDER BY video_id, video_sec
        """)
        result = await session.execute(sql)
    return result.fetchall()


async def fetch_product_exposures(session, video_id=None):
    """Fetch product exposure timeline."""
    if video_id:
        sql = text("""
            SELECT video_id, product_name, time_start, time_end
            FROM video_product_exposures
            WHERE video_id = :video_id
            ORDER BY video_id, time_start
        """)
        result = await session.execute(sql, {"video_id": video_id})
    else:
        sql = text("""
            SELECT video_id, product_name, time_start, time_end
            FROM video_product_exposures
            ORDER BY video_id, time_start
        """)
        result = await session.execute(sql)
    return result.fetchall()


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


def build_moments_index(moments_rows):
    """Build {video_id: [moment_dict, ...]} index."""
    idx = {}
    for r in moments_rows:
        vid = str(r.video_id)
        if vid not in idx:
            idx[vid] = []
        idx[vid].append({
            "video_sec": float(r.video_sec),
            "moment_type": r.moment_type,
            "click_value": r.click_value,
            "order_value": r.order_value,
            "gmv_value": r.gmv_value,
            "confidence": r.confidence,
        })
    return idx


def build_products_index(product_rows):
    """Build {video_id: [(product_name, time_start, time_end), ...]} index."""
    idx = {}
    for r in product_rows:
        vid = str(r.video_id)
        if vid not in idx:
            idx[vid] = []
        idx[vid].append({
            "product_name": r.product_name,
            "time_start": float(r.time_start),
            "time_end": float(r.time_end),
        })
    return idx


def compute_labels(phase_start, phase_end, moments):
    """
    Compute labels for a phase based on sales moments.

    Returns dict with:
      has_click_spike, has_order_spike, has_strong,
      label_strong_window, nearest_strong_sec
    """
    has_click = 0
    has_order = 0
    has_strong = 0
    nearest_strong = None

    # Direct overlap: moment falls within phase time range
    for m in moments:
        sec = m["video_sec"]
        if phase_start <= sec <= phase_end:
            if m["moment_type"] == "click_spike":
                has_click = 1
            elif m["moment_type"] == "order_spike":
                has_order = 1
            elif m["moment_type"] == "strong":
                has_strong = 1
                has_click = 1
                has_order = 1

    # Window-based: ±STRONG_WINDOW_SEC for strong moments
    phase_mid = (phase_start + phase_end) / 2
    label_strong_window = 0
    for m in moments:
        if m["moment_type"] == "strong":
            dist = abs(m["video_sec"] - phase_mid)
            if dist <= STRONG_WINDOW_SEC:
                label_strong_window = 1
            if nearest_strong is None or dist < nearest_strong:
                nearest_strong = dist

    return {
        "has_click_spike": has_click,
        "has_order_spike": has_order,
        "has_strong": has_strong,
        "label_strong_window": label_strong_window,
        "nearest_strong_sec": round(nearest_strong, 1) if nearest_strong is not None else None,
    }


def compute_product_match(phase_start, phase_end, products):
    """Check if any product exposure overlaps with the phase."""
    matched = []
    for p in products:
        # Overlap check
        if p["time_start"] <= phase_end and p["time_end"] >= phase_start:
            matched.append(p["product_name"])
    return matched


def parse_audio_features(raw):
    """Extract audio features from JSON."""
    data = parse_json_field(raw)
    if isinstance(data, dict):
        return {
            "audio_energy_mean": data.get("energy_mean"),
            "audio_tempo": data.get("tempo"),
            "audio_pitch_mean": data.get("pitch_mean"),
            "audio_speech_rate": data.get("speech_rate"),
        }
    return {}


async def generate(output_path, video_id=None, user_id=None):
    """Main dataset generation logic."""
    async with AsyncSessionLocal() as session:
        print("[dataset] Fetching phases...")
        phases = await fetch_phases(session, video_id=video_id, user_id=user_id)
        print(f"[dataset] Found {len(phases)} phases")

        if not phases:
            print("[dataset] No phases found. Exiting.")
            return 0

        # Collect all unique video_ids from phases
        video_ids = list(set(str(r.video_id) for r in phases))
        print(f"[dataset] Spanning {len(video_ids)} videos")

        print("[dataset] Fetching sales moments...")
        try:
            moments_rows = await fetch_sales_moments(session, video_id=video_id)
            print(f"[dataset] Found {len(moments_rows)} sales moments")
        except Exception as e:
            print(f"[dataset] Warning: Could not fetch sales moments: {e}")
            moments_rows = []

        print("[dataset] Fetching product exposures...")
        try:
            product_rows = await fetch_product_exposures(session, video_id=video_id)
            print(f"[dataset] Found {len(product_rows)} product exposures")
        except Exception as e:
            print(f"[dataset] Warning: Could not fetch product exposures: {e}")
            product_rows = []

    # Build indexes
    moments_idx = build_moments_index(moments_rows)
    products_idx = build_products_index(product_rows)

    # Generate dataset
    count = 0
    count_with_label = 0

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        for r in phases:
            vid = str(r.video_id)
            phase_start = float(r.time_start) if r.time_start is not None else 0.0
            phase_end = float(r.time_end) if r.time_end is not None else 0.0
            duration = phase_end - phase_start

            # Skip invalid phases
            if duration <= 0:
                continue

            # Parse tags
            tags = parse_json_field(r.sales_psychology_tags)
            human_tags = parse_json_field(r.human_sales_tags)
            event_type = tags[0] if tags else "UNKNOWN"

            # Compute labels
            video_moments = moments_idx.get(vid, [])
            labels = compute_labels(phase_start, phase_end, video_moments)

            # Product match
            video_products = products_idx.get(vid, [])
            matched_products = compute_product_match(phase_start, phase_end, video_products)

            # Audio features
            audio = parse_audio_features(r.audio_features)

            # Build record
            record = {
                "video_id": vid,
                "user_id": r.user_id,
                "phase_index": r.phase_index,
                "event_type": event_type,
                "tags": tags,
                "human_tags": human_tags,
                "cta_score": r.cta_score,
                "phase_description": r.phase_description,
                "time_start": phase_start,
                "time_end": phase_end,
                "duration": round(duration, 1),
                "group_id": r.group_id,

                # CSV metrics
                "gmv": float(r.gmv),
                "order_count": float(r.order_count),
                "viewer_count": float(r.viewer_count),
                "like_count": float(r.like_count),
                "comment_count": float(r.comment_count),
                "share_count": float(r.share_count),
                "new_followers": float(r.new_followers),
                "product_clicks": float(r.product_clicks),
                "conversion_rate": float(r.conversion_rate),
                "gpm": float(r.gpm),
                "importance_score": float(r.importance_score),

                # Product
                "product_match": len(matched_products) > 0,
                "product_names": matched_products,

                # Labels
                **labels,

                # Audio
                **audio,
            }

            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
            if labels["has_strong"] or labels["label_strong_window"]:
                count_with_label += 1

    await engine.dispose()

    print(f"\n[dataset] Generated {count} records → {output_path}")
    print(f"[dataset] Records with strong label: {count_with_label} ({count_with_label/max(count,1)*100:.1f}%)")
    return count


def main():
    parser = argparse.ArgumentParser(description="Generate AI training dataset")
    parser.add_argument("--output", "-o", default="/tmp/train.jsonl",
                        help="Output JSONL file path")
    parser.add_argument("--video-id", default=None,
                        help="Filter by specific video ID")
    parser.add_argument("--user-id", type=int, default=None,
                        help="Filter by specific user ID")
    args = parser.parse_args()

    count = asyncio.run(generate(args.output, video_id=args.video_id, user_id=args.user_id))
    if count == 0:
        print("[dataset] WARNING: No records generated. Check if data exists in DB.")
        sys.exit(1)


if __name__ == "__main__":
    main()
