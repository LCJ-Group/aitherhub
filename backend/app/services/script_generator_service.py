"""
Script Generator Service — AitherHub Analysis → Digital Human Livestream Scripts

This module converts AitherHub video analysis results into structured scripts
for the Tencent Cloud Digital Human (數智人) livestream platform.

Data flow:
  1. Fetch video analysis data (phases, insights, CSV metrics, speech segments)
  2. Rank phases by performance (GMV, viewer engagement, CTA effectiveness)
  3. Use LLM (GPT) to rewrite top-performing phase content into polished
     livestream scripts optimized for digital human delivery
  4. Output ScriptReq objects ready for the Tencent IVH API

Architecture note:
  This is a PoC module. The LLM prompt and scoring logic will be refined
  based on real livestream performance data.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
SCRIPT_MAX_CHARS = 400_000  # Tencent IVH limit per script
TAKEOVER_MAX_CHARS = 500    # Tencent IVH takeover limit


# ──────────────────────────────────────────────
# Phase Scoring
# ──────────────────────────────────────────────

def _score_phase(phase: Dict[str, Any]) -> float:
    """
    Calculate a composite score for a phase based on its performance metrics.
    Higher score = better performing phase = should be prioritized in scripts.

    Scoring weights:
      - GMV contribution: 40%
      - Viewer delta (engagement): 25%
      - Like delta (engagement): 15%
      - CTA score: 20%
    """
    gmv = float(phase.get("gmv", 0) or 0)
    delta_view = int(phase.get("delta_view", 0) or 0)
    delta_like = int(phase.get("delta_like", 0) or 0)
    cta_score = float(phase.get("cta_score", 0) or 0)

    # Normalize each metric to 0-1 range (will be normalized across all phases later)
    score = (
        gmv * 0.40
        + delta_view * 0.25
        + delta_like * 0.15
        + cta_score * 0.20
    )
    return score


# ──────────────────────────────────────────────
# Data Fetching
# ──────────────────────────────────────────────

async def fetch_video_analysis(
    db: AsyncSession,
    video_id: str,
) -> Dict[str, Any]:
    """
    Fetch comprehensive analysis data for a video from the AitherHub database.

    Returns a dict with:
      - video: basic video info
      - phases: list of phase dicts with metrics
      - insights: list of phase insight dicts
      - speech_segments: list of speech text segments
      - reports: list of report content
    """
    # Fetch video info
    video_row = await db.execute(
        text("""
            SELECT id, original_filename, duration, status, upload_type, top_products
            FROM videos WHERE id = :vid
        """),
        {"vid": video_id},
    )
    video = video_row.mappings().first()
    if not video:
        raise ValueError(f"Video {video_id} not found")

    # Fetch phases with CSV metrics
    phases_result = await db.execute(
        text("""
            SELECT
                p.id, p.phase_index, p.phase_description,
                p.time_start, p.time_end,
                p.view_start, p.view_end,
                p.like_start, p.like_end,
                p.delta_view, p.delta_like,
                p.sales_psychology_tags,
                vp.gmv, vp.orders, vp.viewers, vp.likes,
                vp.product_names, vp.cta_score
            FROM phases p
            LEFT JOIN video_phases vp ON p.id = vp.phase_id
            WHERE p.video_id = :vid AND p.deleted_at IS NULL
            ORDER BY p.phase_index
        """),
        {"vid": video_id},
    )
    phases = [dict(r._mapping) for r in phases_result]

    # Fetch phase insights
    insights_result = await db.execute(
        text("""
            SELECT phase_index, insight
            FROM phase_insights
            WHERE video_id = :vid AND deleted_at IS NULL
            ORDER BY phase_index
        """),
        {"vid": video_id},
    )
    insights = [dict(r._mapping) for r in insights_result]

    # Fetch speech segments (transcribed audio)
    speech_result = await db.execute(
        text("""
            SELECT ss.start_ms, ss.end_ms, ss.text, ss.confidence
            FROM speech_segments ss
            JOIN audio_chunks ac ON ss.audio_chunk_id = ac.id
            WHERE ac.video_id = :vid
            ORDER BY ss.start_ms
        """),
        {"vid": video_id},
    )
    speech_segments = [dict(r._mapping) for r in speech_result]

    # Fetch reports
    reports_result = await db.execute(
        text("""
            SELECT report_content, version
            FROM reports
            WHERE video_id = :vid
            ORDER BY version DESC
            LIMIT 2
        """),
        {"vid": video_id},
    )
    reports = [dict(r._mapping) for r in reports_result]

    return {
        "video": dict(video._mapping),
        "phases": phases,
        "insights": insights,
        "speech_segments": speech_segments,
        "reports": reports,
    }


# ──────────────────────────────────────────────
# Script Generation (LLM-powered)
# ──────────────────────────────────────────────

async def generate_script_with_llm(
    analysis_data: Dict[str, Any],
    product_focus: Optional[str] = None,
    tone: str = "professional_friendly",
    language: str = "ja",
    max_length: int = SCRIPT_MAX_CHARS,
) -> str:
    """
    Use LLM to generate a polished livestream script from analysis data.

    The script is optimized for digital human delivery:
      - Natural speech patterns (not too formal, not too casual)
      - Clear product introductions with benefits
      - Engagement hooks (questions, calls to action)
      - Appropriate pacing markers

    Args:
        analysis_data: Output from fetch_video_analysis()
        product_focus: Optional product name to emphasize
        tone: Script tone (professional_friendly, energetic, calm)
        language: Output language (ja, zh, en)
        max_length: Maximum script length in characters

    Returns:
        Generated script text ready for Tencent IVH API
    """
    # Rank phases by performance
    phases = analysis_data.get("phases", [])
    scored_phases = [(p, _score_phase(p)) for p in phases]
    scored_phases.sort(key=lambda x: x[1], reverse=True)

    # Build context from top-performing phases
    top_phases = scored_phases[:10]  # Top 10 phases
    phase_summaries = []
    for p, score in top_phases:
        desc = p.get("phase_description", "")
        gmv = p.get("gmv", 0)
        products = p.get("product_names", "")
        tags = p.get("sales_psychology_tags", "")
        time_range = f"{p.get('time_start', 0):.0f}s-{p.get('time_end', 0):.0f}s"
        phase_summaries.append(
            f"Phase {p.get('phase_index')}: [{time_range}] score={score:.1f} "
            f"GMV={gmv} products={products} tags={tags}\n  {desc}"
        )

    # Build speech context
    speech_texts = [s["text"] for s in analysis_data.get("speech_segments", [])[:50]]
    speech_context = "\n".join(speech_texts) if speech_texts else "(No speech data)"

    # Build insights context
    insights = analysis_data.get("insights", [])
    insight_texts = [
        f"Phase {i.get('phase_index')}: {i.get('insight', '')}"
        for i in insights[:10]
    ]
    insights_context = "\n".join(insight_texts) if insight_texts else "(No insights)"

    # Build report context
    reports = analysis_data.get("reports", [])
    report_context = reports[0].get("report_content", "") if reports else "(No report)"

    # Top products
    video = analysis_data.get("video", {})
    top_products = video.get("top_products", "")

    # Determine language instruction
    lang_map = {
        "ja": "日本語で台本を生成してください。",
        "zh": "请用中文生成直播台本。",
        "en": "Generate the script in English.",
    }
    lang_instruction = lang_map.get(language, lang_map["ja"])

    # Determine tone instruction
    tone_map = {
        "professional_friendly": "プロフェッショナルだが親しみやすいトーンで。美容のプロとして信頼感を持ちつつ、視聴者に寄り添う話し方。",
        "energetic": "エネルギッシュで盛り上がるトーンで。セール感を出しつつ、商品の魅力を熱く語る。",
        "calm": "落ち着いた上品なトーンで。高級感のある商品紹介に適した、ゆったりとした話し方。",
    }
    tone_instruction = tone_map.get(tone, tone_map["professional_friendly"])

    product_instruction = ""
    if product_focus:
        product_instruction = f"\n特に「{product_focus}」を重点的に紹介してください。"

    prompt = f"""あなたはライブコマースの台本作成のプロフェッショナルです。
以下の動画分析データを基に、數智人（AIデジタルヒューマン）が読み上げるライブ配信の台本を生成してください。

{lang_instruction}
{tone_instruction}
{product_instruction}

## 分析データ

### トップパフォーマンスフェーズ（売上・エンゲージメント順）
{chr(10).join(phase_summaries)}

### 分析インサイト
{insights_context}

### 元の配信者の話し方（参考）
{speech_context[:3000]}

### 分析レポート（要約）
{report_context[:2000]}

### 注力商品
{top_products}

## 台本生成ルール

1. **構成**: 挨拶→商品紹介→特徴説明→使用シーン→限定オファー→CTA の流れで構成
2. **自然な話し方**: 數智人が自然に読み上げられるよう、句読点と改行を適切に配置
3. **エンゲージメント**: 視聴者への質問や呼びかけを適度に挿入（「〜ですよね？」「コメントで教えてください」等）
4. **商品情報**: 分析データから得られた商品名・特徴を正確に反映
5. **長さ**: {max_length // 1000}K文字以内
6. **禁止事項**: 虚偽の効能表現、薬事法に抵触する表現は避ける

台本のみを出力してください。メタ情報やコメントは不要です。
"""

    try:
        import openai

        client = openai.AsyncOpenAI()
        response = await client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "You are a professional livestream script writer for e-commerce."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=4096,
            temperature=0.7,
        )
        script = response.choices[0].message.content.strip()
        logger.info(f"LLM script generated: {len(script)} chars")
        return script[:max_length]

    except Exception as e:
        logger.error(f"LLM script generation failed: {e}")
        # Fallback: build a simple script from phase descriptions
        return _build_fallback_script(analysis_data, language)


def _build_fallback_script(
    analysis_data: Dict[str, Any],
    language: str = "ja",
) -> str:
    """
    Build a simple script without LLM, using phase descriptions and speech segments.
    Used as a fallback when LLM is unavailable.
    """
    parts = []

    if language == "ja":
        parts.append("皆さん、こんにちは！本日のライブ配信をご覧いただきありがとうございます。")
        parts.append("今日は素晴らしい商品をご紹介していきますので、最後までお付き合いください。")
    elif language == "zh":
        parts.append("大家好！感谢观看今天的直播。")
        parts.append("今天为大家带来了非常棒的商品，请一定看到最后哦。")
    else:
        parts.append("Hello everyone! Thank you for watching today's livestream.")
        parts.append("We have some amazing products to show you today.")

    # Add content from top-performing phases
    phases = analysis_data.get("phases", [])
    scored = [(p, _score_phase(p)) for p in phases]
    scored.sort(key=lambda x: x[1], reverse=True)

    for p, score in scored[:5]:
        desc = p.get("phase_description", "")
        if desc:
            parts.append(desc)

    # Add speech segments as reference
    speech = analysis_data.get("speech_segments", [])
    for seg in speech[:20]:
        if seg.get("text") and len(seg["text"]) > 10:
            parts.append(seg["text"])

    if language == "ja":
        parts.append("本日のライブ配信をご覧いただき、ありがとうございました。またお会いしましょう！")
    elif language == "zh":
        parts.append("感谢大家观看今天的直播，我们下次再见！")
    else:
        parts.append("Thank you for watching today's livestream. See you next time!")

    return "\n\n".join(parts)


# ──────────────────────────────────────────────
# Takeover Script Generation
# ──────────────────────────────────────────────

async def generate_takeover_script(
    context: str,
    event_type: str = "product_highlight",
    language: str = "ja",
) -> str:
    """
    Generate a short interjection script (max 500 chars) for real-time takeover.

    This is used when AitherHub detects a sales moment or engagement spike
    during a live stream, and wants the digital human to react immediately.

    Args:
        context: Description of the event (e.g., "Product X just sold 50 units")
        event_type: Type of event (product_highlight, engagement_spike, flash_sale, viewer_question)
        language: Output language

    Returns:
        Short script text (max 500 chars)
    """
    lang_map = {
        "ja": "日本語",
        "zh": "中文",
        "en": "English",
    }
    lang_name = lang_map.get(language, "日本語")

    event_prompts = {
        "product_highlight": f"商品のハイライトを{lang_name}で短く紹介（500文字以内）",
        "engagement_spike": f"視聴者のエンゲージメントが急上昇。盛り上げる一言を{lang_name}で（500文字以内）",
        "flash_sale": f"フラッシュセールの告知を{lang_name}で緊急感を持って（500文字以内）",
        "viewer_question": f"視聴者の質問に{lang_name}で親しみやすく回答（500文字以内）",
    }
    event_instruction = event_prompts.get(event_type, event_prompts["product_highlight"])

    prompt = f"""{event_instruction}

コンテキスト: {context}

ルール:
- 500文字以内で簡潔に
- 數智人が自然に読み上げられる文体
- 視聴者への呼びかけを含む
- テキストのみ出力（メタ情報不要）
"""

    try:
        import openai

        client = openai.AsyncOpenAI()
        response = await client.chat.completions.create(
            model="gpt-4.1-nano",  # Use faster model for real-time takeover
            messages=[
                {"role": "system", "content": "You are a live commerce host. Be concise and engaging."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=500,
            temperature=0.8,
        )
        script = response.choices[0].message.content.strip()
        return script[:TAKEOVER_MAX_CHARS]

    except Exception as e:
        logger.error(f"Takeover script generation failed: {e}")
        # Simple fallback
        fallbacks = {
            "ja": f"皆さん、注目です！{context[:200]}。お見逃しなく！",
            "zh": f"大家注意！{context[:200]}。千万不要错过！",
            "en": f"Everyone, pay attention! {context[:200]}. Don't miss out!",
        }
        return fallbacks.get(language, fallbacks["ja"])[:TAKEOVER_MAX_CHARS]


# ──────────────────────────────────────────────
# Full Pipeline: Analysis → Script → Liveroom
# ──────────────────────────────────────────────

async def generate_liveroom_scripts(
    db: AsyncSession,
    video_id: str,
    product_focus: Optional[str] = None,
    tone: str = "professional_friendly",
    language: str = "ja",
) -> List[Dict[str, Any]]:
    """
    Full pipeline: fetch analysis data → generate script → return ScriptReq-compatible dicts.

    Returns:
        List of dicts compatible with ScriptReq.to_dict() format:
        [{"Content": "...", "Backgrounds": [...]}]
    """
    # Step 1: Fetch analysis data
    analysis_data = await fetch_video_analysis(db, video_id)

    # Step 2: Generate script with LLM
    script_text = await generate_script_with_llm(
        analysis_data,
        product_focus=product_focus,
        tone=tone,
        language=language,
    )

    # Step 3: Package as ScriptReq format
    scripts = [
        {
            "Content": script_text,
            # Backgrounds can be added later via the API
        }
    ]

    logger.info(
        f"Generated {len(scripts)} script(s) for video {video_id}: "
        f"{len(script_text)} chars"
    )
    return scripts
