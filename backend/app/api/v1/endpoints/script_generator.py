"""
Script Generator Tool API Endpoints

Standalone "売れる台本" (Winning Script) tool — generates live commerce scripts
based on real performance data from AitherHub's analysis database.

Unlike the video-specific script generator, this tool does NOT require a video ID.
Users provide product info (name, image, price, etc.) and the system generates
a script grounded in cross-video winning patterns from all analyzed livestreams.

v2: Added feedback knowledge integration + script format with clear
    separation of dialogue (セリフ) and stage directions (ト書き).

Endpoints:
  POST /script-generator/generate   — Generate a script from product info
  GET  /script-generator/patterns    — Get aggregated winning patterns (preview)
  POST /script-generator/upload-image — Get SAS URL for product image upload
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/script-generator", tags=["Script Generator Tool"])


# ──────────────────────────────────────────────
# Request / Response Models
# ──────────────────────────────────────────────

class ScriptGenerateRequest(BaseModel):
    """Request body for standalone script generation."""
    product_name: str = Field(..., min_length=1, max_length=200, description="Product name (required)")
    product_image_url: Optional[str] = Field(None, description="Product image URL (optional, for AI analysis)")
    product_description: Optional[str] = Field(None, max_length=2000, description="Product description")
    product_price: Optional[str] = Field(None, max_length=100, description="Product price (e.g. ¥3,980)")
    target_audience: Optional[str] = Field(None, max_length=500, description="Target audience description")
    tone: str = Field("professional_friendly", description="Script tone: professional_friendly, energetic, calm")
    language: str = Field("ja", description="Output language: ja, zh, en")
    duration_minutes: int = Field(10, ge=1, le=60, description="Target script duration in minutes")
    additional_instructions: Optional[str] = Field(None, max_length=1000, description="Any extra instructions")


class ScriptGenerateResponse(BaseModel):
    """Response for script generation."""
    script: str
    char_count: int
    estimated_duration_minutes: float
    patterns_used: Dict[str, Any]
    data_insights: Dict[str, Any]
    product_analysis: Optional[Dict[str, Any]] = None
    model: str


# ──────────────────────────────────────────────
# POST /script-generator/generate
# ──────────────────────────────────────────────

@router.post("/generate", response_model=ScriptGenerateResponse)
async def generate_standalone_script(
    body: ScriptGenerateRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate a live commerce script from product info + real performance data.

    This is the standalone "売れる台本" tool. It:
    1. Aggregates winning patterns from ALL analyzed livestreams
    2. Extracts feedback knowledge (star-rated phase evaluations)
    3. Optionally analyzes the product image with Vision AI
    4. Generates a script with clear dialogue/stage-direction separation
    """
    import openai

    # Step 1: Aggregate cross-video winning patterns
    from app.services.winning_patterns_service import (
        aggregate_patterns_across_videos,
        extract_feedback_knowledge,
    )
    cross_patterns = None
    try:
        cross_patterns = await aggregate_patterns_across_videos(db, limit_videos=50)
        logger.info(f"Cross-video patterns: {cross_patterns.get('videos_analyzed', 0)} videos, "
                     f"{len(cross_patterns.get('cta_phrases', []))} CTA patterns")
    except Exception as e:
        logger.warning(f"Cross-video aggregation failed: {e}", exc_info=True)
        try:
            await db.rollback()
        except Exception:
            pass

    # Step 2: Extract feedback knowledge (star ratings)
    feedback_knowledge = None
    try:
        feedback_knowledge = await extract_feedback_knowledge(db, top_limit=15, bottom_limit=10)
        if feedback_knowledge:
            stats = feedback_knowledge.get('stats', {})
            logger.info(f"Feedback knowledge: {stats.get('total_rated', 0)} rated, "
                         f"{len(feedback_knowledge.get('winning_patterns', []))} winning, "
                         f"{len(feedback_knowledge.get('losing_patterns', []))} losing")
    except Exception as e:
        logger.warning(f"Feedback knowledge extraction failed: {e}", exc_info=True)
        try:
            await db.rollback()
        except Exception:
            pass

    # Step 3: Analyze product image (if provided)
    product_analysis = None
    if body.product_image_url:
        try:
            product_analysis = await _analyze_product_image(body.product_image_url)
        except Exception as e:
            logger.warning(f"Product image analysis failed: {e}")

    # Step 4: Build prompt
    prompt = _build_standalone_prompt(
        product_name=body.product_name,
        product_description=body.product_description,
        product_price=body.product_price,
        target_audience=body.target_audience,
        product_analysis=product_analysis,
        cross_patterns=cross_patterns,
        feedback_knowledge=feedback_knowledge,
        tone=body.tone,
        language=body.language,
        duration_minutes=body.duration_minutes,
        additional_instructions=body.additional_instructions,
    )

    # Step 5: Generate with LLM (same pattern as live_session_service)
    messages = [
        {
            "role": "system",
            "content": (
                "You are an elite live commerce script writer with deep expertise in "
                "Japanese live commerce (ライブコマース). "
                "You write scripts based on REAL sales data from actual livestreams, not guesses. "
                "Every CTA, every product description timing, every engagement hook "
                "is backed by actual performance metrics.\n\n"
                "## MANDATORY OUTPUT FORMAT (MUST FOLLOW EXACTLY)\n\n"
                "Every single line of your output MUST start with one of these 3 markers:\n\n"
                "⏱ — Section header with time range. Example:\n"
                "⏱ オープニング [0:00 - 1:30]\n\n"
                "🎤 — Dialogue line (what the liver says out loud). Example:\n"
                '🎤「こんばんは！今日も来てくれてありがとうございます！」\n\n'
                "📋 — Stage direction (action/timing cue). Example:\n"
                "📋（カメラに向かって笑顔で手を振る）\n\n"
                "RULES:\n"
                "1. EVERY line must begin with ⏱, 🎤, or 📋. No exceptions. No plain text lines.\n"
                "2. NEVER mix dialogue and stage directions on the same line.\n"
                "3. Dialogue (🎤) must sound natural and conversational.\n"
                "4. Stage directions (📋) must be concise actions in （）parentheses.\n"
                "5. Generate the FULL requested length. Do NOT cut short.\n"
                "6. Do NOT use markdown headers (#), bold (**), or any other formatting.\n"
                "7. Output ONLY the script. No explanations, no meta-commentary."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    script = None
    errors = []
    model_used = "unknown"

    # Strategy 1: Azure OpenAI Responses API
    azure_key = os.getenv("AZURE_OPENAI_KEY", "")
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    azure_model = os.getenv("GPT5_MODEL") or os.getenv("GPT5_DEPLOYMENT") or "gpt-4.1-mini"
    if azure_key and azure_endpoint:
        try:
            client = openai.AzureOpenAI(
                api_key=azure_key,
                azure_endpoint=azure_endpoint,
                api_version=os.getenv("GPT5_API_VERSION", "2025-04-01-preview"),
            )
            input_payload = [{"role": m["role"], "content": m["content"]} for m in messages]
            response = client.responses.create(
                model=azure_model,
                input=input_payload,
                max_output_tokens=8192,
            )
            result = ""
            if hasattr(response, "output_text") and response.output_text:
                result = response.output_text.strip()
            elif hasattr(response, "output") and response.output:
                for item in response.output:
                    if hasattr(item, "content"):
                        for part in item.content:
                            if hasattr(part, "text"):
                                result += part.text
                result = result.strip()

            if result:
                script = result
                model_used = azure_model
                logger.info(f"Standalone script: Azure OpenAI success ({len(script)} chars)")
            else:
                errors.append("Azure OpenAI: empty response")
        except Exception as e:
            errors.append(f"Azure OpenAI: {str(e)[:200]}")
            logger.warning(f"Standalone script Azure OpenAI failed: {e}")

    # Strategy 2: OpenAI fallback
    if script is None:
        openai_key = os.getenv("OPENAI_API_KEY", "")
        if openai_key:
            try:
                client2 = openai.AsyncOpenAI()
                response = await client2.chat.completions.create(
                    model="gpt-4.1-mini",
                    messages=messages,
                    max_tokens=8192,
                    temperature=0.7,
                )
                script = response.choices[0].message.content.strip()
                model_used = "gpt-4.1-mini"
                logger.info(f"Standalone script: OpenAI fallback success ({len(script)} chars)")
            except Exception as e:
                errors.append(f"OpenAI: {str(e)[:200]}")
                logger.warning(f"Standalone script OpenAI fallback failed: {e}")

    if script is None:
        raise HTTPException(status_code=500, detail=f"All LLM strategies failed: {'; '.join(errors)}")

    # Post-process: clean up markdown but preserve our format markers (🎤📋⏱)
    script = re.sub(r'\*\*', '', script)
    script = re.sub(r'\*', '', script)
    # Remove markdown headers but NOT lines starting with our markers
    script = re.sub(r'^#{1,6}\s*', '', script, flags=re.MULTILINE)
    script = re.sub(r'\n{3,}', '\n\n', script)
    # Remove code block markers if LLM wrapped output in ```
    script = re.sub(r'^```[a-z]*\s*$', '', script, flags=re.MULTILINE)
    script = script.strip()

    # If LLM ignored format markers, add a note in logs
    has_markers = '🎤' in script or '📋' in script or '⏱' in script
    if not has_markers:
        logger.warning("LLM output missing format markers (🎤📋⏱) - model may have ignored format instructions")

    char_count = len(script)
    estimated_duration = round(char_count / 250, 1)

    # Build patterns_used info
    feedback_stats = {}
    if feedback_knowledge:
        feedback_stats = feedback_knowledge.get("stats", {})

    return ScriptGenerateResponse(
        script=script,
        char_count=char_count,
        estimated_duration_minutes=estimated_duration,
        patterns_used={
            "cross_video_patterns": bool(cross_patterns),
            "videos_in_cross_analysis": cross_patterns.get("videos_analyzed", 0) if cross_patterns else 0,
            "cta_patterns_found": len(cross_patterns.get("cta_phrases", [])) if cross_patterns else 0,
            "feedback_knowledge_used": bool(feedback_knowledge),
            "feedback_total_rated": feedback_stats.get("total_rated", 0),
            "feedback_winning_patterns": len(feedback_knowledge.get("winning_patterns", [])) if feedback_knowledge else 0,
            "feedback_losing_patterns": len(feedback_knowledge.get("losing_patterns", [])) if feedback_knowledge else 0,
            "product_image_analyzed": bool(product_analysis),
        },
        data_insights={
            "cta_phrases": cross_patterns.get("cta_phrases", [])[:5] if cross_patterns else [],
            "duration_insights": cross_patterns.get("product_durations", [])[:5] if cross_patterns else [],
            "top_techniques": cross_patterns.get("top_techniques", [])[:5] if cross_patterns else [],
        },
        product_analysis=product_analysis,
        model=model_used,
    )


# ──────────────────────────────────────────────
# GET /script-generator/patterns
# ──────────────────────────────────────────────

@router.get("/patterns")
async def get_winning_patterns_preview(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit_videos: int = Query(50, ge=1, le=200),
):
    """
    Get aggregated winning patterns from all analyzed livestreams.
    This is a preview endpoint so the UI can show patterns before generation.
    """
    from app.services.winning_patterns_service import (
        aggregate_patterns_across_videos,
        extract_feedback_knowledge,
    )

    try:
        patterns = await aggregate_patterns_across_videos(db, limit_videos=limit_videos)
        feedback = await extract_feedback_knowledge(db, top_limit=10, bottom_limit=5)
        return {
            "videos_analyzed": patterns.get("videos_analyzed", 0),
            "cta_phrases": patterns.get("cta_phrases", [])[:10],
            "duration_insights": patterns.get("product_durations", [])[:10],
            "top_techniques": patterns.get("top_techniques", [])[:10],
            "feedback_stats": feedback.get("stats", {}),
            "winning_patterns_preview": [
                p["description"][:150] for p in feedback.get("winning_patterns", [])[:5]
            ],
        }
    except Exception as e:
        logger.exception(f"Winning patterns preview failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────
# POST /script-generator/upload-image
# ──────────────────────────────────────────────

@router.post("/upload-image")
async def get_image_upload_url(
    current_user: dict = Depends(get_current_user),
):
    """
    Generate a SAS URL for uploading a product image to Azure Blob Storage.
    The uploaded image can then be passed to /generate as product_image_url.
    """
    from app.services.storage_service import generate_upload_sas

    email = current_user.get("email", "script-tool")
    try:
        vid, upload_url, blob_url, expiry = await generate_upload_sas(
            email=email,
            video_id=None,
            filename="product-image.jpg",
        )
        return {
            "upload_url": upload_url,
            "blob_url": blob_url,
            "expiry": expiry.isoformat(),
        }
    except Exception as e:
        logger.exception(f"Image upload SAS generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────
# Internal: Product Image Analysis
# ──────────────────────────────────────────────

async def _analyze_product_image(image_url: str) -> Optional[Dict[str, Any]]:
    """
    Analyze a product image using Azure OpenAI Vision API.
    Extracts product features, colors, packaging, and selling points.
    """
    import openai

    azure_key = os.getenv("AZURE_OPENAI_KEY", "")
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    vision_model = os.getenv("VISION_MODEL", "gpt-4o")
    vision_api_version = os.getenv("VISION_API_VERSION", "2024-06-01")

    if not azure_key or not azure_endpoint:
        return None

    prompt = """この商品画像を分析して、以下の情報をJSON形式で返してください：
{
  "product_type": "商品の種類（例：シャンプー、美容液、サプリメント）",
  "visual_features": ["目立つ視覚的特徴のリスト"],
  "colors": ["主要な色"],
  "packaging": "パッケージの特徴",
  "selling_points": ["画像から読み取れるセールスポイント"],
  "suggested_demo": "ライブコマースでのデモ方法の提案"
}
JSONのみを出力してください。"""

    try:
        client = openai.AzureOpenAI(
            api_key=azure_key,
            azure_endpoint=azure_endpoint,
            api_version=vision_api_version,
        )
        response = client.chat.completions.create(
            model=vision_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": image_url, "detail": "high"},
                        },
                    ],
                }
            ],
            max_tokens=1000,
            temperature=0.3,
        )
        content = response.choices[0].message.content
        json_match = re.search(r"\{[\s\S]*\}", content)
        if json_match:
            return json.loads(json_match.group())
        return {"raw_analysis": content}
    except Exception as e:
        logger.warning(f"Product image analysis failed: {e}")
        return None


# ──────────────────────────────────────────────
# Internal: Prompt Builder (v2 with feedback knowledge + format)
# ──────────────────────────────────────────────

def _build_standalone_prompt(
    product_name: str,
    product_description: Optional[str],
    product_price: Optional[str],
    target_audience: Optional[str],
    product_analysis: Optional[Dict],
    cross_patterns: Optional[Dict],
    feedback_knowledge: Optional[Dict],
    tone: str,
    language: str,
    duration_minutes: int,
    additional_instructions: Optional[str],
) -> str:
    """Build a prompt for standalone script generation with real data + feedback knowledge."""

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

    # Product info section
    product_section = f"## 商品情報\n- 商品名: {product_name}\n"
    if product_price:
        product_section += f"- 価格: {product_price}\n"
    if product_description:
        product_section += f"- 商品説明: {product_description}\n"
    if target_audience:
        product_section += f"- ターゲット層: {target_audience}\n"

    # Product image analysis section
    image_section = ""
    if product_analysis:
        image_section = "\n## AI商品画像分析結果\n"
        if isinstance(product_analysis, dict):
            if product_analysis.get("product_type"):
                image_section += f"- 商品タイプ: {product_analysis['product_type']}\n"
            if product_analysis.get("visual_features"):
                image_section += f"- 視覚的特徴: {', '.join(product_analysis['visual_features'])}\n"
            if product_analysis.get("selling_points"):
                image_section += f"- セールスポイント: {', '.join(product_analysis['selling_points'])}\n"
            if product_analysis.get("suggested_demo"):
                image_section += f"- デモ提案: {product_analysis['suggested_demo']}\n"

    # ── Feedback Knowledge Section (NEW - the key differentiator) ──
    feedback_section = ""
    if feedback_knowledge:
        winning = feedback_knowledge.get("winning_patterns", [])
        losing = feedback_knowledge.get("losing_patterns", [])
        stats = feedback_knowledge.get("stats", {})

        if winning or losing:
            total_rated = stats.get('total_rated', 0)
            feedback_section = f"\n## 実績フィードバックデータ（{total_rated}件の評価済みフェーズから抽出）\n"

            if winning:
                feedback_section += "\n### 高評価パターン（星4-5: 売れる配信の特徴）\n"
                feedback_section += "以下は実際のライブコマース配信で高評価を受けたフェーズの分析です。これらのパターンを台本に積極的に反映してください。\n"
                for i, w in enumerate(winning[:10], 1):
                    feedback_section += f"\n{i}. [星{w['rating']}] {w['description']}\n"
                    if w.get('tags'):
                        feedback_section += f"   タグ: {w['tags']}\n"

            if losing:
                feedback_section += "\n### 低評価パターン（星1-2: 避けるべき配信の特徴）\n"
                feedback_section += "以下のパターンは視聴者の離脱や売上低下を招くため、台本では避けてください。\n"
                for i, l in enumerate(losing[:7], 1):
                    feedback_section += f"\n{i}. [星{l['rating']}] {l['description']}\n"

    # Cross-video patterns section
    patterns_section = ""
    if cross_patterns:
        patterns_section = "\n## 過去の配信実績データ\n"
        patterns_section += f"分析済み動画数: {cross_patterns.get('videos_analyzed', 0)}本\n"

        cta_phrases = cross_patterns.get("cta_phrases", [])
        if cta_phrases:
            patterns_section += "\n### 売れたCTAパターン:\n"
            for c in cta_phrases[:8]:
                patterns_section += f"- {c['pattern']}: {c['occurrence_count']}回出現, 注文相関{c['order_correlation']}回\n"
                if c.get("example_talks"):
                    example = c['example_talks'][0][:80]
                    patterns_section += f"  例: {example}\n"

        durations = cross_patterns.get("product_durations", [])
        if durations:
            patterns_section += "\n### 商品説明の最適時間:\n"
            for d in durations[:5]:
                patterns_section += f"- {d['category']}: {d['value']}\n"

        techniques = cross_patterns.get("top_techniques", [])
        if techniques:
            patterns_section += "\n### 売れる販売心理テクニック:\n"
            for t in techniques[:7]:
                patterns_section += f"- {t['technique']} (出現{t['frequency']}回)\n"

    # Build final prompt
    prompt = f"""# ライブコマース台本生成リクエスト

{lang_instruction}
{tone_instruction}

{product_section}
{image_section}
{feedback_section}
{patterns_section}

## 台本フォーマット指示（必ず守ること）

台本は以下のフォーマットで生成してください。ライバーが配信中に見ながら使える実用的な台本です。

### フォーマットルール:
1. 各セクションは「⏱ セクション名 [MM:SS - MM:SS]」で始める
2. ライバーが実際に声に出して話すセリフは「🎤」で始める
3. 演出指示・アクション・タイミング指示は「📋」で始め、（）で囲む
4. セリフとト書きは絶対に混ぜない。別の行にする
5. セリフは自然な口語で、実際にライブで話すように書く
6. ト書きは簡潔な指示文で書く

### 台本の構成（必須セクション）:
1. オープニング（挨拶・雰囲気作り）
2. 商品紹介（特徴・メリット・使い方）
3. デモ・実演（実際に見せる）
4. 視聴者との交流（コメント対応・質問回答）
5. CTA（購入促進・限定感）
6. クロージング（まとめ・次回予告）

### フォーマット例:
```
⏱ オープニング [0:00 - 1:00]
📋（カメラに向かって笑顔で手を振る。商品はまだ見せない）
🎤「こんばんは！今日も来てくれてありがとうございます！」
🎤「今日は、私が本当にハマってる商品を紹介しますよ」
📋（コメント欄を見て、視聴者の名前を呼ぶ）
🎤「あ、〇〇さん、いつもありがとう！」
```

## 生成条件（厳守）
- 目標文字数: {min_chars}〜{max_chars}文字（約{duration_minutes}分の配信）。この文字数に必ず到達すること。短すぎる台本は不可。
- すべてのセリフ行は🎤で始め、「」で囲む
- すべての演出指示行は📋で始め、（）で囲む
- すべてのセクション見出し行は⏱で始める
- 各セクションに目安時間を入れる
- 実績データのパターンを自然に反映する（「データによると」などとは言わない）
- フィードバックの高評価パターンを積極的に取り入れ、低評価パターンは避ける
- プレーンテキスト行（マーカーなし）は絶対に出力しない
"""

    if additional_instructions:
        prompt += f"\n## 追加指示\n{additional_instructions}\n"

    prompt += ("\n重要: 上記のフォーマットルールに従って台本を生成してください。"
              "\n全ての行は必ず⏱または🎤または📋のいずれかで始めてください。"
              f"\n文字数は{min_chars}文字以上必須です。")

    return prompt
