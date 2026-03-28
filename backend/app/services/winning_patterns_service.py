"""
Winning Patterns Service — Extract data-driven selling patterns from AitherHub analysis.

This module analyzes completed video data to extract:
  1. CTA phrases that triggered actual sales (from sales_moments + audio_text)
  2. Optimal product description durations (from product_exposures + sales_moments)
  3. High-performing phase structures (from phase metrics)
  4. Cross-video pattern aggregation

These patterns are injected into script generation prompts to create
scripts grounded in real performance data — not generic AI guesses.

Data sources:
  - video_sales_moments: click/order spikes with timestamps
  - video_phases + audio_text: what was said at each moment
  - video_product_exposures: when products were shown/mentioned
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 1. CTA Phrase Extraction
# ──────────────────────────────────────────────

async def extract_cta_phrases(
    db: AsyncSession,
    video_id: str,
    window_before_sec: float = 30.0,
    window_after_sec: float = 10.0,
) -> List[Dict[str, Any]]:
    """
    Extract what was said immediately before each sales moment.

    For each click_spike or order_spike in video_sales_moments,
    find the audio_text from the video_phase that covers that time window.
    """
    sql = text("""
        SELECT
            sm.time_sec   AS moment_time,
            sm.video_sec,
            sm.moment_type,
            sm.moment_type_detail,
            sm.click_value,
            sm.click_delta,
            sm.order_value,
            sm.order_delta,
            sm.gmv_value,
            sm.confidence,
            sm.reasons,
            vp.phase_index,
            vp.time_start  AS phase_start,
            vp.time_end    AS phase_end,
            vp.audio_text,
            vp.phase_description,
            vp.sales_psychology_tags
        FROM video_sales_moments sm
        JOIN video_phases vp
          ON  vp.video_id = sm.video_id
          AND vp.time_start <= (sm.video_sec + :window_after)
          AND vp.time_end   >= (sm.video_sec - :window_before)
          AND vp.audio_text IS NOT NULL
          AND CHAR_LENGTH(vp.audio_text) > 0
        WHERE sm.video_id = :vid
        ORDER BY sm.time_sec, vp.phase_index
    """)

    result = await db.execute(sql, {
        "vid": video_id,
        "window_before": window_before_sec,
        "window_after": window_after_sec,
    })
    rows = result.fetchall()

    cta_contexts = []
    seen_moments = set()

    for r in rows:
        moment_key = f"{r.moment_time}_{r.moment_type}"
        if moment_key in seen_moments:
            continue
        seen_moments.add(moment_key)

        metric_value = r.order_value if r.moment_type == "order" else r.click_value

        cta_contexts.append({
            "moment_time_sec": r.moment_time,
            "video_sec": r.moment_time,
            "moment_type": r.moment_type,
            "moment_detail": r.moment_type_detail,
            "metric_value": metric_value,
            "click_delta": r.click_delta,
            "order_delta": r.order_delta,
            "gmv_value": r.gmv_value,
            "confidence": r.confidence,
            "reasons": r.reasons,
            "pre_talk": r.audio_text or "",
            "phase_description": r.phase_description or "",
            "phase_index": r.phase_index,
            "sales_psychology_tags": r.sales_psychology_tags,
        })

    return cta_contexts


# ──────────────────────────────────────────────
# 2. Product Description Duration Analysis
# ──────────────────────────────────────────────

async def analyze_product_durations(
    db: AsyncSession,
    video_id: str,
) -> List[Dict[str, Any]]:
    """
    Analyze how long each product was described/shown and correlate with sales.
    """
    # Get product exposures
    exposure_sql = text("""
        SELECT product_name, time_start, time_end,
               (time_end - time_start) AS duration_sec
        FROM video_product_exposures
        WHERE video_id = :vid
        ORDER BY product_name, time_start
    """)
    exp_result = await db.execute(exposure_sql, {"vid": video_id})
    exposures = exp_result.fetchall()

    # Get sales moments
    moment_sql = text("""
        SELECT video_sec, moment_type, click_value, order_value, gmv_value
        FROM video_sales_moments
        WHERE video_id = :vid
        ORDER BY video_sec
    """)
    mom_result = await db.execute(moment_sql, {"vid": video_id})
    moments = mom_result.fetchall()

    # Aggregate by product
    product_stats: Dict[str, Dict] = {}
    for exp in exposures:
        pname = exp.product_name
        if pname not in product_stats:
            product_stats[pname] = {
                "product_name": pname,
                "total_exposure_sec": 0.0,
                "exposure_count": 0,
                "segments": [],
                "sales_within_60s": 0,
                "sales_within_120s": 0,
                "total_clicks_nearby": 0.0,
                "total_orders_nearby": 0.0,
                "total_gmv_nearby": 0.0,
            }
        stats = product_stats[pname]
        dur = max(0, exp.duration_sec or 0)
        stats["total_exposure_sec"] += dur
        stats["exposure_count"] += 1
        stats["segments"].append({
            "start": exp.time_start,
            "end": exp.time_end,
            "duration": dur,
        })

        # Check for sales within 60s and 120s after exposure end
        for m in moments:
            time_after = m.video_sec - exp.time_end
            if 0 <= time_after <= 60:
                stats["sales_within_60s"] += 1
                stats["total_clicks_nearby"] += (m.click_value or 0)
                stats["total_orders_nearby"] += (m.order_value or 0)
                stats["total_gmv_nearby"] += (m.gmv_value or 0)
            elif 60 < time_after <= 120:
                stats["sales_within_120s"] += 1

    # Build results
    results = []
    for pname, stats in product_stats.items():
        avg_seg = (
            stats["total_exposure_sec"] / stats["exposure_count"]
            if stats["exposure_count"] > 0 else 0
        )
        results.append({
            "product_name": pname,
            "total_exposure_sec": round(stats["total_exposure_sec"], 1),
            "exposure_count": stats["exposure_count"],
            "avg_segment_sec": round(avg_seg, 1),
            "had_sales": stats["sales_within_60s"] > 0,
            "sales_within_60s": stats["sales_within_60s"],
            "sales_within_120s": stats["sales_within_120s"],
            "total_clicks_nearby": stats["total_clicks_nearby"],
            "total_orders_nearby": stats["total_orders_nearby"],
            "total_gmv_nearby": stats["total_gmv_nearby"],
        })

    # Sort by sales impact
    results.sort(key=lambda x: (x["sales_within_60s"], x["total_gmv_nearby"]), reverse=True)
    return results


# ──────────────────────────────────────────────
# 3. High-Performing Phase Analysis
# ──────────────────────────────────────────────

async def extract_top_phases(
    db: AsyncSession,
    video_id: str,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """
    Extract the highest-performing phases by engagement and sales metrics.
    Uses only video_phases table (no JOIN to phases table needed).
    """
    sql = text("""
        SELECT
            vp.phase_index,
            vp.time_start,
            vp.time_end,
            (vp.time_end - vp.time_start) AS duration_sec,
            vp.audio_text,
            vp.phase_description,
            vp.gmv,
            vp.order_count,
            vp.viewer_count,
            vp.like_count,
            vp.cta_score,
            vp.delta_view,
            vp.delta_like,
            vp.sales_psychology_tags
        FROM video_phases vp
        WHERE vp.video_id = :vid
        ORDER BY
            COALESCE(vp.gmv, 0) * 0.4
            + COALESCE(vp.delta_view, 0) * 0.25
            + COALESCE(vp.delta_like, 0) * 0.15
            + COALESCE(vp.cta_score, 0) * 0.20
            DESC
        LIMIT :lim
    """)
    result = await db.execute(sql, {"vid": video_id, "lim": limit})
    rows = result.fetchall()

    phases = []
    for r in rows:
        score = (
            (r.gmv or 0) * 0.4
            + (r.delta_view or 0) * 0.25
            + (r.delta_like or 0) * 0.15
            + (r.cta_score or 0) * 0.20
        )
        phases.append({
            "phase_index": r.phase_index,
            "time_start": r.time_start,
            "time_end": r.time_end,
            "duration_sec": round(r.duration_sec or 0, 1),
            "audio_text": r.audio_text or "",
            "phase_description": r.phase_description or "",
            "gmv": r.gmv,
            "order_count": r.order_count,
            "viewer_count": r.viewer_count,
            "like_count": r.like_count,
            "cta_score": r.cta_score,
            "delta_view": r.delta_view,
            "delta_like": r.delta_like,
            "sales_psychology_tags": r.sales_psychology_tags,
            "composite_score": round(score, 2),
        })

    return phases


# ──────────────────────────────────────────────
# 4. Cross-Video Pattern Aggregation
# ──────────────────────────────────────────────

async def aggregate_patterns_across_videos(
    db: AsyncSession,
    user_id: Optional[int] = None,
    video_ids: Optional[List[str]] = None,
    limit_videos: int = 50,
) -> Dict[str, Any]:
    """
    Aggregate winning patterns across multiple videos.
    This is the core differentiator — patterns from real sales data
    across many livestreams, not just one video.
    """
    # Get video IDs
    if video_ids:
        vids = video_ids
    else:
        vid_sql = text("""
            SELECT id FROM videos
            WHERE status = 'DONE'
            AND (:uid IS NULL OR user_id = :uid)
            ORDER BY created_at DESC
            LIMIT :lim
        """)
        vid_result = await db.execute(vid_sql, {"uid": user_id, "lim": limit_videos})
        vids = [str(r.id) for r in vid_result.fetchall()]

    if not vids:
        return {"videos_analyzed": 0, "cta_phrases": [], "product_durations": [], "top_techniques": []}

    # Aggregate CTA contexts across videos
    all_cta = []
    all_durations = []
    for vid in vids[:limit_videos]:
        try:
            ctas = await extract_cta_phrases(db, vid)
            all_cta.extend(ctas)
            durations = await analyze_product_durations(db, vid)
            all_durations.extend(durations)
        except Exception as e:
            logger.warning(f"Pattern extraction failed for video {vid}: {e}")

    # Extract common CTA phrases
    cta_phrases = _extract_common_phrases(all_cta)

    # Aggregate product duration insights
    duration_insights = _aggregate_duration_insights(all_durations)

    # Extract top sales psychology techniques
    top_techniques = await _extract_top_techniques(db, vids)

    return {
        "videos_analyzed": len(vids),
        "cta_phrases": cta_phrases[:20],
        "product_durations": duration_insights[:20],
        "top_techniques": top_techniques[:10],
        "raw_cta_count": len(all_cta),
        "raw_duration_count": len(all_durations),
    }


def _extract_common_phrases(cta_contexts: List[Dict]) -> List[Dict]:
    """Extract and rank common CTA phrases from sales moment contexts."""
    if not cta_contexts:
        return []

    # Common CTA patterns in Japanese live commerce
    cta_patterns = [
        (r"今だけ|いまだけ", "限定感"),
        (r"残り\d+|あと\d+", "希少性"),
        (r"送料無料|送料込み", "送料無料"),
        (r"セット|まとめ買い", "セット訴求"),
        (r"クーポン|割引|OFF|オフ", "割引訴求"),
        (r"使ってみて|試して", "体験訴求"),
        (r"おすすめ|オススメ", "推薦"),
        (r"人気|売れてる|大人気", "人気訴求"),
        (r"限定|数量限定", "数量限定"),
        (r"ぜひ|是非", "行動喚起"),
        (r"ポチ|カート|購入", "購入促進"),
        (r"コメント|教えて", "エンゲージメント"),
        (r"実は|実際", "信頼構築"),
        (r"悩み|困って", "課題提起"),
        (r"効果|変わ", "効果訴求"),
    ]

    phrase_stats = []
    for pattern, label in cta_patterns:
        matches = []
        for ctx in cta_contexts:
            talk = ctx.get("pre_talk", "")
            if re.search(pattern, talk):
                matches.append(ctx)

        if matches:
            avg_confidence = sum(m.get("confidence", 0) for m in matches) / len(matches)
            order_matches = [m for m in matches if m.get("moment_type") == "order"]
            phrase_stats.append({
                "pattern": label,
                "regex": pattern,
                "occurrence_count": len(matches),
                "order_correlation": len(order_matches),
                "avg_confidence": round(avg_confidence, 2),
                "example_talks": [m["pre_talk"][:200] for m in matches[:3]],
            })

    phrase_stats.sort(key=lambda x: (x["order_correlation"], x["occurrence_count"]), reverse=True)
    return phrase_stats


def _aggregate_duration_insights(durations: List[Dict]) -> List[Dict]:
    """Aggregate product duration data across videos."""
    if not durations:
        return []

    # Group by whether they had sales
    with_sales = [d for d in durations if d.get("had_sales")]
    without_sales = [d for d in durations if not d.get("had_sales")]

    insights = []

    if with_sales:
        avg_exposure_with_sales = sum(d["total_exposure_sec"] for d in with_sales) / len(with_sales)
        avg_segments_with_sales = sum(d["exposure_count"] for d in with_sales) / len(with_sales)
        insights.append({
            "category": "売れた商品の平均説明時間",
            "value": f"{avg_exposure_with_sales:.0f}秒（約{avg_exposure_with_sales/60:.1f}分）",
            "avg_exposure_sec": round(avg_exposure_with_sales, 1),
            "avg_segments": round(avg_segments_with_sales, 1),
            "sample_size": len(with_sales),
        })

    if without_sales:
        avg_exposure_without = sum(d["total_exposure_sec"] for d in without_sales) / len(without_sales)
        insights.append({
            "category": "売れなかった商品の平均説明時間",
            "value": f"{avg_exposure_without:.0f}秒（約{avg_exposure_without/60:.1f}分）",
            "avg_exposure_sec": round(avg_exposure_without, 1),
            "sample_size": len(without_sales),
        })

    # Top products by GMV
    top_gmv = sorted(durations, key=lambda x: x.get("total_gmv_nearby", 0), reverse=True)
    for d in top_gmv[:5]:
        if d.get("total_gmv_nearby", 0) > 0:
            insights.append({
                "category": f"高GMV商品: {d['product_name'][:30]}",
                "value": f"説明{d['total_exposure_sec']:.0f}秒, GMV={d['total_gmv_nearby']:.0f}",
                "product_name": d["product_name"],
                "exposure_sec": d["total_exposure_sec"],
                "gmv": d["total_gmv_nearby"],
            })

    return insights


async def _extract_top_techniques(
    db: AsyncSession,
    video_ids: List[str],
) -> List[Dict]:
    """Extract most common sales psychology tags from high-performing phases."""
    if not video_ids:
        return []

    # Use IN clause instead of ANY (MySQL compatible)
    placeholders = ", ".join([f":vid_{i}" for i in range(len(video_ids))])
    params = {f"vid_{i}": vid for i, vid in enumerate(video_ids)}

    sql = text(f"""
        SELECT vp.sales_psychology_tags, COUNT(*) as cnt
        FROM video_phases vp
        WHERE vp.video_id IN ({placeholders})
          AND vp.sales_psychology_tags IS NOT NULL
          AND CHAR_LENGTH(vp.sales_psychology_tags) > 0
          AND (COALESCE(vp.gmv, 0) > 0 OR COALESCE(vp.cta_score, 0) > 0.5)
        GROUP BY vp.sales_psychology_tags
        ORDER BY cnt DESC
        LIMIT 20
    """)
    try:
        result = await db.execute(sql, params)
        rows = result.fetchall()

        tag_counts: Dict[str, int] = {}
        for r in rows:
            tags_str = r.sales_psychology_tags or ""
            # Handle both JSON array and comma-separated formats
            tags = []
            if tags_str.startswith("["):
                try:
                    tags = json.loads(tags_str)
                except (json.JSONDecodeError, TypeError):
                    tags = [t.strip() for t in tags_str.split(",")]
            else:
                tags = [t.strip() for t in tags_str.split(",")]

            for tag in tags:
                tag = tag.strip().strip('"').strip("'")
                if tag:
                    tag_counts[tag] = tag_counts.get(tag, 0) + r.cnt

        techniques = []
        for tag, count in sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
            techniques.append({
                "technique": tag,
                "frequency": count,
            })

        return techniques
    except Exception as e:
        logger.warning(f"Top techniques extraction failed: {e}")
        return []


# ──────────────────────────────────────────────
# 5. Generate Data-Driven Script
# ──────────────────────────────────────────────

async def generate_data_driven_script(
    db: AsyncSession,
    video_id: str,
    product_focus: Optional[str] = None,
    tone: str = "professional_friendly",
    language: str = "ja",
    duration_minutes: int = 10,
    cross_video: bool = True,
) -> Dict[str, Any]:
    """
    Generate a script grounded in real performance data.

    This is the key differentiator from generic AI script generators:
    every recommendation in the script is backed by actual sales data.
    """
    # Step 1: Extract patterns from this video
    cta_phrases = await extract_cta_phrases(db, video_id)
    product_durations = await analyze_product_durations(db, video_id)
    top_phases = await extract_top_phases(db, video_id, limit=10)

    # Step 2: Cross-video patterns (optional)
    cross_patterns = None
    if cross_video:
        try:
            cross_patterns = await aggregate_patterns_across_videos(db, limit_videos=20)
        except Exception as e:
            logger.warning(f"Cross-video aggregation failed: {e}")
            # Rollback to clear any failed transaction state
            try:
                await db.rollback()
            except Exception:
                pass

    # Step 3: Get video info
    video_sql = text("""
        SELECT id, original_filename, duration, top_products
        FROM videos WHERE id = :vid
    """)
    video_result = await db.execute(video_sql, {"vid": video_id})
    video = video_result.mappings().first()
    if not video:
        raise ValueError(f"Video {video_id} not found")

    # Step 4: Build the data-driven prompt
    prompt = _build_data_driven_prompt(
        video=dict(video),
        cta_phrases=cta_phrases,
        product_durations=product_durations,
        top_phases=top_phases,
        cross_patterns=cross_patterns,
        product_focus=product_focus,
        tone=tone,
        language=language,
        duration_minutes=duration_minutes,
    )

    # Step 5: Generate with LLM
    try:
        import os
        from openai import AsyncAzureOpenAI

        client = AsyncAzureOpenAI(
            api_key=os.getenv("AZURE_OPENAI_KEY"),
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            api_version=os.getenv("VISION_API_VERSION", "2024-06-01"),
        )
        gpt_model = os.getenv("VISION_MODEL", "gpt-4o")

        target_chars = duration_minutes * 250
        response = await client.chat.completions.create(
            model=gpt_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an elite live commerce script writer. "
                        "You write scripts based on REAL sales data, not guesses. "
                        "Every CTA, every product description timing, every engagement hook "
                        "is backed by actual performance metrics from past livestreams."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=8192,
            temperature=0.7,
        )
        script = response.choices[0].message.content.strip()

        # Post-process
        script = re.sub(r'\*\*', '', script)
        script = re.sub(r'\*', '', script)
        script = re.sub(r'【[^】]*】', '', script)
        script = re.sub(r'#{1,6}\s*', '', script)
        script = re.sub(r'\n{3,}', '\n\n', script)
        script = script.strip()

        char_count = len(script)
        estimated_duration = round(char_count / 250, 1)

        return {
            "script": script,
            "char_count": char_count,
            "estimated_duration_minutes": estimated_duration,
            "patterns_used": {
                "cta_phrases_found": len(cta_phrases),
                "products_analyzed": len(product_durations),
                "top_phases_used": len(top_phases),
                "cross_video_patterns": bool(cross_patterns),
                "videos_in_cross_analysis": cross_patterns.get("videos_analyzed", 0) if cross_patterns else 0,
            },
            "data_insights": {
                "cta_contexts": cta_phrases[:5],
                "product_duration_insights": product_durations[:5],
                "top_techniques": cross_patterns.get("top_techniques", [])[:5] if cross_patterns else [],
            },
            "model": "gpt-4.1-mini",
            "source_video": str(video["id"]),
        }

    except Exception as e:
        logger.exception(f"Data-driven script generation failed: {e}")
        raise


def _build_data_driven_prompt(
    video: Dict,
    cta_phrases: List[Dict],
    product_durations: List[Dict],
    top_phases: List[Dict],
    cross_patterns: Optional[Dict],
    product_focus: Optional[str],
    tone: str,
    language: str,
    duration_minutes: int,
) -> str:
    """Build a prompt enriched with real performance data."""

    target_chars = duration_minutes * 250
    min_chars = int(target_chars * 0.85)
    max_chars = int(target_chars * 1.15)

    # Language
    lang_map = {
        "ja": "日本語で台本を生成してください。",
        "zh": "请用中文生成直播台本。",
        "en": "Generate the script in English.",
    }
    lang_instruction = lang_map.get(language, lang_map["ja"])

    # Tone
    tone_map = {
        "professional_friendly": "プロフェッショナルだが親しみやすいトーンで。",
        "energetic": "エネルギッシュで盛り上がるトーンで。",
        "calm": "落ち着いた上品なトーンで。",
    }
    tone_instruction = tone_map.get(tone, tone_map["professional_friendly"])

    # Top products
    top_products = video.get("top_products", "")
    if isinstance(top_products, str):
        try:
            top_products = json.loads(top_products)
        except (json.JSONDecodeError, TypeError):
            pass

    # Build CTA evidence section
    cta_section = ""
    if cta_phrases:
        cta_lines = []
        for ctx in cta_phrases[:5]:
            moment_type = "注文" if ctx["moment_type"] == "order" else "クリック"
            talk_preview = ctx["pre_talk"][:150].replace("\n", " ")
            cta_lines.append(
                f"- {moment_type}発生（{ctx['moment_time_sec']:.0f}秒時点）: "
                f"「{talk_preview}」"
            )
        cta_section = f"""
## 実績データ: 売れた瞬間の直前トーク（CTA分析）
以下は実際に注文/クリックが発生した瞬間の直前に話していた内容です。
これらのフレーズやトーク構成を台本に活用してください。

{chr(10).join(cta_lines)}
"""

    # Build product duration section
    duration_section = ""
    if product_durations:
        dur_lines = []
        for pd in product_durations[:5]:
            sales_mark = "★売上あり" if pd["had_sales"] else ""
            dur_lines.append(
                f"- {pd['product_name'][:30]}: "
                f"説明時間={pd['total_exposure_sec']:.0f}秒（{pd['exposure_count']}回）"
                f" {sales_mark}"
            )
        duration_section = f"""
## 実績データ: 商品説明の最適時間
以下は各商品の説明時間と売上の相関データです。
売上があった商品の説明時間を参考に、台本の時間配分を決めてください。

{chr(10).join(dur_lines)}
"""

    # Build top phases section
    phase_section = ""
    if top_phases:
        phase_lines = []
        for p in top_phases[:5]:
            time_range = f"{p['time_start']:.0f}s-{p['time_end']:.0f}s"
            phase_lines.append(
                f"- Phase {p['phase_index']} [{time_range}] "
                f"GMV={p.get('gmv', 0) or 0} score={p['composite_score']}\n"
                f"  話した内容: 「{p['audio_text'][:100]}」\n"
                f"  AI要約: {p['phase_description'][:100]}"
            )
        phase_section = f"""
## 実績データ: 最も効果的だったフェーズ
以下は視聴者エンゲージメントと売上が最も高かったフェーズです。
これらの構成・話し方を台本に反映してください。

{chr(10).join(phase_lines)}
"""

    # Cross-video patterns
    cross_section = ""
    if cross_patterns and cross_patterns.get("videos_analyzed", 0) > 1:
        cross_lines = []
        for cp in cross_patterns.get("cta_phrases", [])[:5]:
            cross_lines.append(
                f"- {cp['pattern']}: {cp['occurrence_count']}回出現, "
                f"注文相関={cp['order_correlation']}回"
            )
        for tech in cross_patterns.get("top_techniques", [])[:3]:
            cross_lines.append(f"- 心理テクニック「{tech['technique']}」: {tech['frequency']}回使用")

        if cross_lines:
            cross_section = f"""
## 横断分析: {cross_patterns['videos_analyzed']}本の配信から抽出した勝ちパターン
以下は複数の配信を横断して発見された、売上に効果的なパターンです。

{chr(10).join(cross_lines)}
"""

    # Product focus
    product_instruction = ""
    if product_focus:
        product_instruction = f"\n特に「{product_focus}」を重点的に紹介してください。"

    prompt = f"""{lang_instruction}
{tone_instruction}
{product_instruction}

あなたはライブコマースの台本作成のプロフェッショナルです。
以下の【実際の配信実績データ】を基に、売れる台本を生成してください。

重要: この台本は一般的なAIの推測ではなく、実際の配信データに基づいています。
データが示す「売れた瞬間」「効果的だった話し方」を忠実に反映してください。

## 基本情報
- 紹介商品: {json.dumps(top_products, ensure_ascii=False) if isinstance(top_products, list) else top_products}
- 目標時間: 約{duration_minutes}分（{min_chars}〜{max_chars}文字）
{cta_section}
{duration_section}
{phase_section}
{cross_section}

## 台本生成ルール

1. 上記の実績データに基づく構成にすること（データがない部分は一般的なベストプラクティスで補完）
2. CTAフレーズは実績データで効果が確認されたものを優先的に使用
3. 商品説明時間は実績データの「売れた商品」の説明時間を参考に配分
4. 自然な話し言葉で、そのまま読み上げられるテキストのみ出力
5. 【タグ】や**太字**等の記号は使わない
6. 台本以外の説明文やメモは出力しない

台本のみを出力してください。"""

    return prompt
