"""
Script Generator Tool API Endpoints

Standalone "売れる台本" (Winning Script) tool — generates live commerce scripts
based on real performance data from AitherHub's analysis database.

Unlike the video-specific script generator, this tool does NOT require a video ID.
Users provide product info (name, image, price, etc.) and the system generates
a script grounded in cross-video winning patterns from all analyzed livestreams.

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
    2. Optionally analyzes the product image with Vision AI
    3. Generates a script grounded in real sales data
    """
    import openai

    # Step 1: Aggregate cross-video winning patterns
    from app.services.winning_patterns_service import aggregate_patterns_across_videos
    cross_patterns = None
    try:
        cross_patterns = await aggregate_patterns_across_videos(db, limit_videos=50)
    except Exception as e:
        logger.warning(f"Cross-video aggregation failed: {e}")
        try:
            await db.rollback()
        except Exception:
            pass

    # Step 2: Analyze product image (if provided)
    product_analysis = None
    if body.product_image_url:
        try:
            product_analysis = await _analyze_product_image(body.product_image_url)
        except Exception as e:
            logger.warning(f"Product image analysis failed: {e}")

    # Step 3: Build prompt
    prompt = _build_standalone_prompt(
        product_name=body.product_name,
        product_description=body.product_description,
        product_price=body.product_price,
        target_audience=body.target_audience,
        product_analysis=product_analysis,
        cross_patterns=cross_patterns,
        tone=body.tone,
        language=body.language,
        duration_minutes=body.duration_minutes,
        additional_instructions=body.additional_instructions,
    )

    # Step 4: Generate with LLM (same pattern as live_session_service)
    messages = [
        {
            "role": "system",
            "content": (
                "You are an elite live commerce script writer. "
                "You write scripts based on REAL sales data from actual livestreams, not guesses. "
                "Every CTA, every product description timing, every engagement hook "
                "is backed by actual performance metrics."
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

    # Post-process: remove markdown formatting
    script = re.sub(r'\*\*', '', script)
    script = re.sub(r'\*', '', script)
    script = re.sub(r'【[^】]*】', '', script)
    script = re.sub(r'#{1,6}\s*', '', script)
    script = re.sub(r'\n{3,}', '\n\n', script)
    script = script.strip()

    char_count = len(script)
    estimated_duration = round(char_count / 250, 1)

    return ScriptGenerateResponse(
        script=script,
        char_count=char_count,
        estimated_duration_minutes=estimated_duration,
        patterns_used={
            "cross_video_patterns": bool(cross_patterns),
            "videos_in_cross_analysis": cross_patterns.get("videos_analyzed", 0) if cross_patterns else 0,
            "cta_patterns_found": len(cross_patterns.get("cta_phrases", [])) if cross_patterns else 0,
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
    from app.services.winning_patterns_service import aggregate_patterns_across_videos

    try:
        patterns = await aggregate_patterns_across_videos(db, limit_videos=limit_videos)
        return {
            "videos_analyzed": patterns.get("videos_analyzed", 0),
            "cta_phrases": patterns.get("cta_phrases", [])[:10],
            "duration_insights": patterns.get("product_durations", [])[:10],
            "top_techniques": patterns.get("top_techniques", [])[:10],
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
# Internal: Prompt Builder
# ──────────────────────────────────────────────

def _build_standalone_prompt(
    product_name: str,
    product_description: Optional[str],
    product_price: Optional[str],
    target_audience: Optional[str],
    product_analysis: Optional[Dict],
    cross_patterns: Optional[Dict],
    tone: str,
    language: str,
    duration_minutes: int,
    additional_instructions: Optional[str],
) -> str:
    """Build a prompt for standalone script generation with real data."""

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

    # Cross-video patterns section (the key differentiator)
    cross_section = ""
    if cross_patterns and cross_patterns.get("videos_analyzed", 0) > 0:
        cross_section = f"\n## 実績データ: {cross_patterns['videos_analyzed']}本の配信から抽出した勝ちパターン\n"
        cross_section += "以下は実際のライブコマース配信データから抽出された、売上に効果的なパターンです。\n\n"

        # CTA patterns
        cta_phrases = cross_patterns.get("cta_phrases", [])
        if cta_phrases:
            cross_section += "### 売れたCTAパターン（実績データ）\n"
            for cp in cta_phrases[:7]:
                cross_section += (
                    f"- {cp['pattern']}: {cp['occurrence_count']}回出現, "
                    f"注文相関={cp['order_correlation']}回\n"
                )

        # Duration insights
        duration_insights = cross_patterns.get("product_durations", [])
        if duration_insights:
            cross_section += "\n### 商品説明の最適時間（実績データ）\n"
            for di in duration_insights[:5]:
                cross_section += f"- {di.get('category', '')}: {di.get('value', '')}\n"

        # Top techniques
        techniques = cross_patterns.get("top_techniques", [])
        if techniques:
            cross_section += "\n### 効果的な販売テクニック（実績データ）\n"
            for tech in techniques[:5]:
                cross_section += f"- {tech['technique']}: {tech['frequency']}回使用\n"

    # Additional instructions
    extra = ""
    if additional_instructions:
        extra = f"\n## 追加指示\n{additional_instructions}\n"

    prompt = f"""{lang_instruction}
{tone_instruction}

あなたはライブコマースの台本作成のプロフェッショナルです。
以下の商品情報と【実際の配信実績データ】を基に、売れる台本を生成してください。

重要: この台本は一般的なAIの推測ではなく、実際のライブコマース配信データに基づいています。
データが示す「売れたCTAパターン」「効果的だった商品説明時間」「売上に繋がった販売テクニック」を忠実に反映してください。

{product_section}
{image_section}
{cross_section}
{extra}
## 台本の構成ガイド
- 目標時間: 約{duration_minutes}分（{min_chars}〜{max_chars}文字）
- オープニング（挨拶・今日の目玉商品の予告）
- 商品紹介（特徴・使い方・ビフォーアフター）
- 実績データで効果が確認されたCTAパターンを自然に組み込む
- 視聴者とのインタラクション（コメント促進・質問への対応）
- クロージング（限定感・購入促進・次回予告）

## 台本生成ルール
1. 実績データに基づく構成にすること（データがない部分は一般的なベストプラクティスで補完）
2. CTAフレーズは実績データで効果が確認されたものを優先的に使用
3. 商品説明時間は実績データの「売れた商品」の説明時間を参考に配分
4. 自然な話し言葉で、そのまま読み上げられるテキストのみ出力
5. 【タグ】や**太字**等の記号は使わない
6. 台本以外の説明文やメモは出力しない

台本のみを出力してください。"""

    return prompt
