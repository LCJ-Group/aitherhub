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
from typing import Union
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
    product_image_urls: Optional[List[str]] = Field(None, description="Multiple product image URLs (optional, max 10)")
    product_description: Optional[str] = Field(None, max_length=2000, description="Product description")
    product_price: Optional[str] = Field(None, max_length=200, description="Product price (combined string)")
    original_price: Optional[str] = Field(None, max_length=100, description="Original/retail price (e.g. ¥5,980)")
    discounted_price: Optional[str] = Field(None, max_length=100, description="Discounted/stream-special price (e.g. ¥3,980)")
    benefits: Optional[str] = Field(None, max_length=1000, description="Special benefits/tokuten for the stream")
    target_audience: Optional[str] = Field(None, max_length=500, description="Target audience description")
    tone: str = Field("professional_friendly", description="Script tone: professional_friendly, energetic, calm")
    language: str = Field("ja", description="Output language: ja, zh, en")
    duration_minutes: int = Field(10, ge=1, le=60, description="Target script duration in minutes")
    additional_instructions: Optional[str] = Field(None, max_length=1000, description="Any extra instructions")


class ScriptRateRequest(BaseModel):
    """Request body for rating a generated script."""
    rating: int = Field(..., ge=1, le=5, description="Star rating 1-5")
    comment: Optional[str] = Field(None, max_length=2000, description="Free-form comment")
    good_tags: Optional[List[str]] = Field(None, description="Good point tags")
    bad_tags: Optional[List[str]] = Field(None, description="Bad point tags")


class ScriptGenerateResponse(BaseModel):
    """Response for script generation."""
    script_id: Optional[str] = None
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

    # Step 3: Analyze product images (supports multiple)
    product_analysis = None
    images_analyzed_count = 0
    all_image_urls = []
    if body.product_image_urls:
        all_image_urls = body.product_image_urls[:10]  # max 10
    elif body.product_image_url:
        all_image_urls = [body.product_image_url]

    if all_image_urls:
        try:
            if len(all_image_urls) == 1:
                product_analysis = await _analyze_product_image(all_image_urls[0])
                images_analyzed_count = 1 if product_analysis else 0
            else:
                product_analysis = await _analyze_multiple_product_images(all_image_urls)
                images_analyzed_count = len(all_image_urls) if product_analysis else 0
        except Exception as e:
            logger.warning(f"Product image analysis failed: {e}")

    # Step 4: Build prompt
    prompt = _build_standalone_prompt(
        product_name=body.product_name,
        product_description=body.product_description,
        product_price=body.product_price,
        original_price=body.original_price,
        discounted_price=body.discounted_price,
        benefits=body.benefits,
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

    # Check char count and extend if too short
    target_chars = body.duration_minutes * 250
    min_chars = int(target_chars * 0.85)
    if len(script) < min_chars and len(script) > 200:
        logger.info(f"Script too short ({len(script)}/{min_chars} chars), requesting continuation")
        remaining = min_chars - len(script)
        extend_messages = [
            {"role": "system", "content": messages[0]["content"]},
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": script},
            {"role": "user", "content": (
                f"台本が短すぎます（{len(script)}文字）。目標は{min_chars}文字以上です。\n"
                f"あと{remaining}文字以上、台本の続きを生成してください。\n"
                "フォーマットルール（⏱🎤📋）を守って、セクションの続きまたは新しいセクションを追加してください。\n"
                "説明を加えず、台本の続きだけを出力してください。"
            )},
        ]
        try:
            if azure_key and azure_endpoint:
                client_ext = openai.AzureOpenAI(
                    api_key=azure_key,
                    azure_endpoint=azure_endpoint,
                    api_version=os.getenv("GPT5_API_VERSION", "2025-04-01-preview"),
                )
                input_ext = [{"role": m["role"], "content": m["content"]} for m in extend_messages]
                resp_ext = client_ext.responses.create(
                    model=azure_model,
                    input=input_ext,
                    max_output_tokens=4096,
                )
                ext_text = ""
                if hasattr(resp_ext, "output_text") and resp_ext.output_text:
                    ext_text = resp_ext.output_text.strip()
                elif hasattr(resp_ext, "output") and resp_ext.output:
                    for item in resp_ext.output:
                        if hasattr(item, "content"):
                            for part in item.content:
                                if hasattr(part, "text"):
                                    ext_text += part.text
                    ext_text = ext_text.strip()
                if ext_text:
                    script = script + "\n" + ext_text
                    logger.info(f"Extended script to {len(script)} chars")
            elif openai_key:
                client_ext2 = openai.AsyncOpenAI()
                resp_ext2 = await client_ext2.chat.completions.create(
                    model="gpt-4.1-mini",
                    messages=extend_messages,
                    max_tokens=4096,
                    temperature=0.7,
                )
                ext_text = resp_ext2.choices[0].message.content.strip()
                if ext_text:
                    script = script + "\n" + ext_text
                    logger.info(f"Extended script to {len(script)} chars")
        except Exception as e:
            logger.warning(f"Script extension failed: {e}")

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

    patterns_used_data = {
        "cross_video_patterns": bool(cross_patterns),
        "videos_in_cross_analysis": cross_patterns.get("videos_analyzed", 0) if cross_patterns else 0,
        "cta_patterns_found": len(cross_patterns.get("cta_phrases", [])) if cross_patterns else 0,
        "feedback_knowledge_used": bool(feedback_knowledge),
        "feedback_total_rated": feedback_stats.get("total_rated", 0),
        "feedback_winning_patterns": len(feedback_knowledge.get("winning_patterns", [])) if feedback_knowledge else 0,
        "feedback_losing_patterns": len(feedback_knowledge.get("losing_patterns", [])) if feedback_knowledge else 0,
        "product_image_analyzed": bool(product_analysis),
        "images_analyzed_count": images_analyzed_count,
    }
    data_insights_data = {
        "cta_phrases": cross_patterns.get("cta_phrases", [])[:5] if cross_patterns else [],
        "duration_insights": cross_patterns.get("product_durations", [])[:5] if cross_patterns else [],
        "top_techniques": cross_patterns.get("top_techniques", [])[:5] if cross_patterns else [],
    }

    # Save to DB for scoring/learning
    script_id = None
    try:
        import uuid
        script_id = str(uuid.uuid4())
        user_email = current_user.get("email", "anonymous")
        await db.execute(
            text("""
                INSERT INTO script_generations (
                    id, user_email, product_name, product_description,
                    original_price, discounted_price, benefits,
                    target_audience, tone, language, duration_minutes,
                    generated_script, char_count, model_used,
                    patterns_used, product_analysis, created_at
                ) VALUES (
                    :id, :user_email, :product_name, :product_description,
                    :original_price, :discounted_price, :benefits,
                    :target_audience, :tone, :language, :duration_minutes,
                    :generated_script, :char_count, :model_used,
                    :patterns_used, :product_analysis, NOW()
                )
            """),
            {
                "id": script_id,
                "user_email": user_email,
                "product_name": body.product_name,
                "product_description": body.product_description,
                "original_price": body.original_price or body.product_price,
                "discounted_price": body.discounted_price,
                "benefits": body.benefits,
                "target_audience": body.target_audience,
                "tone": body.tone,
                "language": body.language,
                "duration_minutes": body.duration_minutes,
                "generated_script": script,
                "char_count": char_count,
                "model_used": model_used,
                "patterns_used": json.dumps(patterns_used_data),
                "product_analysis": json.dumps(product_analysis) if product_analysis else None,
            },
        )
        await db.commit()
        logger.info(f"Script generation saved to DB: {script_id}")
    except Exception as e:
        logger.warning(f"Failed to save script generation to DB: {e}")
        try:
            await db.rollback()
        except Exception:
            pass
        script_id = None

    return ScriptGenerateResponse(
        script_id=script_id,
        script=script,
        char_count=char_count,
        estimated_duration_minutes=estimated_duration,
        patterns_used=patterns_used_data,
        data_insights=data_insights_data,
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
# GET /script-generator/history (user's own generations)
# ──────────────────────────────────────────────

@router.get("/history")
async def get_user_history(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """
    Get the current user's script generation history.
    Returns a list of past generations with summary info (no full script text).
    """
    user_email = current_user.get("email", "anonymous")

    try:
        # Count total for this user
        count_result = await db.execute(
            text("SELECT COUNT(*) FROM script_generations WHERE user_email = :email"),
            {"email": user_email},
        )
        total = count_result.scalar() or 0

        # Get list (summary only - no full script text)
        list_result = await db.execute(
            text("""
                SELECT id, product_name, original_price, discounted_price,
                       benefits, char_count, model_used, rating,
                       rating_good_tags, created_at, tone, language, duration_minutes
                FROM script_generations
                WHERE user_email = :email
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            {"email": user_email, "limit": limit, "offset": offset},
        )
        rows = list_result.fetchall()
        generations = []
        for r in rows:
            generations.append({
                "id": str(r[0]),
                "product_name": r[1],
                "original_price": r[2],
                "discounted_price": r[3],
                "benefits": r[4],
                "char_count": r[5],
                "model_used": r[6],
                "rating": r[7],
                "rating_good_tags": json.loads(r[8]) if isinstance(r[8], str) else r[8],
                "created_at": r[9].isoformat() if r[9] else None,
                "tone": r[10],
                "language": r[11],
                "duration_minutes": r[12],
            })

        return {
            "total": total,
            "user_email": user_email,
            "generations": generations,
        }
    except Exception as e:
        logger.exception(f"User history fetch failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history/{script_id}")
async def get_user_history_detail(
    script_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get full details of a specific script generation (user's own only).
    Includes the full script text, product analysis, and patterns used.
    """
    user_email = current_user.get("email", "anonymous")

    try:
        result = await db.execute(
            text("""
                SELECT id, user_email, product_name, product_description,
                       original_price, discounted_price, benefits,
                       target_audience, tone, language, duration_minutes,
                       generated_script, char_count, model_used,
                       patterns_used, product_analysis,
                       rating, rating_comment, rating_good_tags, rating_bad_tags,
                       rated_at, created_at
                FROM script_generations
                WHERE id = :id AND user_email = :email
            """),
            {"id": script_id, "email": user_email},
        )
        r = result.fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="Script not found")

        return {
            "id": str(r[0]),
            "user_email": r[1],
            "product_name": r[2],
            "product_description": r[3],
            "original_price": r[4],
            "discounted_price": r[5],
            "benefits": r[6],
            "target_audience": r[7],
            "tone": r[8],
            "language": r[9],
            "duration_minutes": r[10],
            "generated_script": r[11],
            "char_count": r[12],
            "model_used": r[13],
            "patterns_used": json.loads(r[14]) if isinstance(r[14], str) else r[14],
            "product_analysis": json.loads(r[15]) if isinstance(r[15], str) else r[15],
            "rating": r[16],
            "rating_comment": r[17],
            "rating_good_tags": json.loads(r[18]) if isinstance(r[18], str) else r[18],
            "rating_bad_tags": json.loads(r[19]) if isinstance(r[19], str) else r[19],
            "rated_at": r[20].isoformat() if r[20] else None,
            "created_at": r[21].isoformat() if r[21] else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"User history detail fetch failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────
# GET /script-generator/debug-patterns (admin only)
# ──────────────────────────────────────────────

@router.get("/debug-patterns")
async def debug_patterns(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Debug endpoint to test cross_patterns and feedback_knowledge extraction."""
    expected_key = f"{os.getenv('ADMIN_ID', 'aither')}:{os.getenv('ADMIN_PASS', 'hub')}"
    if x_admin_key != expected_key:
        raise HTTPException(status_code=403, detail="Forbidden")

    from app.services.winning_patterns_service import (
        aggregate_patterns_across_videos,
        extract_feedback_knowledge,
    )

    result = {"cross_patterns": None, "feedback_knowledge": None, "errors": []}

    try:
        cross_patterns = await aggregate_patterns_across_videos(db, limit_videos=50)
        result["cross_patterns"] = {
            "videos_analyzed": cross_patterns.get("videos_analyzed", 0),
            "cta_phrases_count": len(cross_patterns.get("cta_phrases", [])),
            "product_durations_count": len(cross_patterns.get("product_durations", [])),
            "top_techniques_count": len(cross_patterns.get("top_techniques", [])),
            "raw_cta_count": cross_patterns.get("raw_cta_count", 0),
            "raw_duration_count": cross_patterns.get("raw_duration_count", 0),
            "top_techniques": cross_patterns.get("top_techniques", [])[:5],
        }
    except Exception as e:
        result["errors"].append(f"cross_patterns: {str(e)}")
        try:
            await db.rollback()
        except Exception:
            pass

    try:
        feedback_knowledge = await extract_feedback_knowledge(db, top_limit=15, bottom_limit=10)
        if feedback_knowledge:
            result["feedback_knowledge"] = {
                "stats": feedback_knowledge.get("stats", {}),
                "winning_count": len(feedback_knowledge.get("winning_patterns", [])),
                "losing_count": len(feedback_knowledge.get("losing_patterns", [])),
                "winning_sample": feedback_knowledge.get("winning_patterns", [])[:2],
            }
    except Exception as e:
        result["errors"].append(f"feedback_knowledge: {str(e)}")

    return result


# ──────────────────────────────────────────────
# POST /script-generator/{script_id}/rate
# ──────────────────────────────────────────────

@router.post("/{script_id}/rate")
async def rate_script(
    script_id: str,
    body: ScriptRateRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Rate a generated script. Users can provide star rating (1-5),
    good/bad tags, and a free-form comment.
    """
    # Verify script exists
    result = await db.execute(
        text("SELECT id FROM script_generations WHERE id = :id"),
        {"id": script_id},
    )
    if not result.fetchone():
        raise HTTPException(status_code=404, detail="Script not found")

    try:
        await db.execute(
            text("""
                UPDATE script_generations
                SET rating = :rating,
                    rating_comment = :comment,
                    rating_good_tags = :good_tags,
                    rating_bad_tags = :bad_tags,
                    rated_at = NOW()
                WHERE id = :id
            """),
            {
                "id": script_id,
                "rating": body.rating,
                "comment": body.comment,
                "good_tags": json.dumps(body.good_tags) if body.good_tags else None,
                "bad_tags": json.dumps(body.bad_tags) if body.bad_tags else None,
            },
        )
        await db.commit()
        logger.info(f"Script {script_id} rated: {body.rating} stars")
        return {"status": "ok", "script_id": script_id, "rating": body.rating}
    except Exception as e:
        logger.exception(f"Failed to rate script {script_id}: {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────
# GET /script-generator/admin/generations (admin only)
# ──────────────────────────────────────────────

@router.get("/admin/generations")
async def admin_list_script_generations(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    rated_only: bool = Query(False),
):
    """Admin endpoint: list all script generations with ratings."""
    expected_key = f"{os.getenv('ADMIN_ID', 'aither')}:{os.getenv('ADMIN_PASS', 'hub')}"
    if x_admin_key != expected_key:
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        # Count total
        count_sql = "SELECT COUNT(*) FROM script_generations"
        if rated_only:
            count_sql += " WHERE rating IS NOT NULL"
        count_result = await db.execute(text(count_sql))
        total = count_result.scalar() or 0

        # Stats
        stats_result = await db.execute(text("""
            SELECT
                COUNT(*) as total_generated,
                COUNT(rating) as total_rated,
                ROUND(AVG(rating)::numeric, 1) as avg_rating,
                COUNT(CASE WHEN rating >= 4 THEN 1 END) as good_count,
                COUNT(CASE WHEN rating <= 2 THEN 1 END) as bad_count,
                ROUND(AVG(char_count)::numeric, 0) as avg_char_count
            FROM script_generations
        """))
        stats_row = stats_result.fetchone()
        stats = {
            "total_generated": stats_row[0] if stats_row else 0,
            "total_rated": stats_row[1] if stats_row else 0,
            "avg_rating": float(stats_row[2]) if stats_row and stats_row[2] else None,
            "good_count": stats_row[3] if stats_row else 0,
            "bad_count": stats_row[4] if stats_row else 0,
            "avg_char_count": int(stats_row[5]) if stats_row and stats_row[5] else 0,
        }

        # List
        where_clause = "WHERE rating IS NOT NULL" if rated_only else ""
        list_result = await db.execute(
            text(f"""
                SELECT id, user_email, product_name, original_price, discounted_price,
                       char_count, model_used, rating, rating_comment,
                       rating_good_tags, rating_bad_tags, rated_at, created_at
                FROM script_generations
                {where_clause}
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            {"limit": limit, "offset": offset},
        )
        rows = list_result.fetchall()
        generations = []
        for r in rows:
            generations.append({
                "id": str(r[0]),
                "user_email": r[1],
                "product_name": r[2],
                "original_price": r[3],
                "discounted_price": r[4],
                "char_count": r[5],
                "model_used": r[6],
                "rating": r[7],
                "rating_comment": r[8],
                "rating_good_tags": json.loads(r[9]) if isinstance(r[9], str) else r[9],
                "rating_bad_tags": json.loads(r[10]) if isinstance(r[10], str) else r[10],
                "rated_at": r[11].isoformat() if r[11] else None,
                "created_at": r[12].isoformat() if r[12] else None,
            })

        return {
            "total": total,
            "stats": stats,
            "generations": generations,
        }
    except Exception as e:
        logger.exception(f"Admin list script generations failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/admin/generations/{gen_id}")
async def admin_get_script_generation(
    gen_id: str,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Admin endpoint: get full details of a script generation."""
    expected_key = f"{os.getenv('ADMIN_ID', 'aither')}:{os.getenv('ADMIN_PASS', 'hub')}"
    if x_admin_key != expected_key:
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        result = await db.execute(
            text("""
                SELECT id, user_email, product_name, product_description,
                       original_price, discounted_price, benefits,
                       target_audience, tone, language, duration_minutes,
                       generated_script, char_count, model_used,
                       patterns_used, product_analysis,
                       rating, rating_comment, rating_good_tags, rating_bad_tags,
                       rated_at, created_at
                FROM script_generations
                WHERE id = :id
            """),
            {"id": gen_id},
        )
        r = result.fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="Script generation not found")

        return {
            "id": str(r[0]),
            "user_email": r[1],
            "product_name": r[2],
            "product_description": r[3],
            "original_price": r[4],
            "discounted_price": r[5],
            "benefits": r[6],
            "target_audience": r[7],
            "tone": r[8],
            "language": r[9],
            "duration_minutes": r[10],
            "generated_script": r[11],
            "char_count": r[12],
            "model_used": r[13],
            "patterns_used": json.loads(r[14]) if isinstance(r[14], str) else r[14],
            "product_analysis": json.loads(r[15]) if isinstance(r[15], str) else r[15],
            "rating": r[16],
            "rating_comment": r[17],
            "rating_good_tags": json.loads(r[18]) if isinstance(r[18], str) else r[18],
            "rating_bad_tags": json.loads(r[19]) if isinstance(r[19], str) else r[19],
            "rated_at": r[20].isoformat() if r[20] else None,
            "created_at": r[21].isoformat() if r[21] else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Admin get script generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────
# Internal: Product Image Analysis
# ──────────────────────────────────────────────

async def _analyze_product_image(image_url: str) -> Optional[Dict[str, Any]]:
    """
    Analyze a product image using Azure OpenAI Responses API (Vision).
    Extracts detailed product features including text/OCR, ingredients,
    usage instructions, and live commerce demo suggestions.
    """
    import openai

    azure_key = os.getenv("AZURE_OPENAI_KEY", "")
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    # Use the same model as script generation for reliability
    vision_model = os.getenv("GPT5_MODEL") or os.getenv("GPT5_DEPLOYMENT") or "gpt-4.1-mini"
    api_version = os.getenv("GPT5_API_VERSION", "2025-04-01-preview")

    if not azure_key or not azure_endpoint:
        logger.warning("Product image analysis skipped: missing AZURE_OPENAI_KEY or AZURE_OPENAI_ENDPOINT")
        return None

    prompt = """あなたはライブコマースの商品分析エキスパートです。この商品画像を徹底的に分析してください。

## 分析指示（必ず全項目を埋めること）

1. **画像内テキストの完全読み取り**: パッケージに書かれた文字、成分表、効能表示、使用方法、キャッチコピー、ブランド名を全て読み取ってください
2. **商品の物理的特徴**: 色、質感、サイズ感、容器の形状、高級感の有無
3. **成分・効能の分析**: 読み取れた成分名から期待される効果を推測
4. **ビフォーアフター**: もし比較画像なら、変化の具体的な説明
5. **使用シーン**: 画像から読み取れる使い方のヒント

以下のJSON形式で返してください：
{
  "product_type": "商品の種類（具体的に。例：ピーリング美容液、高保湿クリーム）",
  "brand_name": "ブランド名（画像から読み取れた場合）",
  "text_on_image": ["画像内に書かれている全てのテキスト（成分名、効能、キャッチコピー等）"],
  "ingredients_found": ["読み取れた成分名のリスト"],
  "claimed_effects": ["画像から読み取れる効能・効果の主張"],
  "visual_features": ["商品の視覚的特徴（色、質感、形状、高級感等）"],
  "packaging": "パッケージの詳細な特徴（素材感、デザイン、サイズ感）",
  "usage_instructions": "画像から読み取れる使い方の情報",
  "selling_points": ["ライブコマースで訴求すべきセールスポイント（5つ以上）"],
  "demo_script_suggestions": [
    "具体的なデモ提案1: 例）手の甲に1-2滴垂らして浸透速度を見せる",
    "具体的なデモ提案2: 例）テクスチャーの伸びを見せながら『サラッとしてるでしょ？』",
    "具体的なデモ提案3: 例）パッケージを近づけて成分表を読み上げる"
  ],
  "talk_points": ["配信者が話すべき具体的なトークポイント（商品の特徴を活かした会話例）"]
}
JSONのみを出力してください。"""

    try:
        client = openai.AzureOpenAI(
            api_key=azure_key,
            azure_endpoint=azure_endpoint,
            api_version=api_version,
        )
        # Use Responses API with input_image format
        response = client.responses.create(
            model=vision_model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {
                            "type": "input_image",
                            "image_url": image_url,
                        },
                    ],
                }
            ],
            max_output_tokens=2500,
        )
        # Extract text from Responses API output
        content = ""
        if hasattr(response, "output_text") and response.output_text:
            content = response.output_text.strip()
        elif hasattr(response, "output") and response.output:
            for item in response.output:
                if hasattr(item, "content"):
                    for part in item.content:
                        if hasattr(part, "text"):
                            content += part.text
            content = content.strip()

        if not content:
            logger.warning("Product image analysis returned empty content")
            return None

        json_match = re.search(r"\{[\s\S]*\}", content)
        if json_match:
            result = json.loads(json_match.group())
            logger.info(f"Product image analysis success: {len(result.get('selling_points', []))} selling points found")
            return result
        return {"raw_analysis": content}
    except Exception as e:
        logger.warning(f"Product image analysis failed: {type(e).__name__}: {e}")
        return None


async def _analyze_multiple_product_images(image_urls: List[str]) -> Optional[Dict[str, Any]]:
    """
    Analyze multiple product images using Azure OpenAI Responses API (Vision).
    Sends all images in a single request for comprehensive analysis.
    Extracts text, ingredients, usage info, and generates demo suggestions.
    """
    import openai

    azure_key = os.getenv("AZURE_OPENAI_KEY", "")
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    vision_model = os.getenv("GPT5_MODEL") or os.getenv("GPT5_DEPLOYMENT") or "gpt-4.1-mini"
    api_version = os.getenv("GPT5_API_VERSION", "2025-04-01-preview")

    if not azure_key or not azure_endpoint:
        logger.warning("Multiple image analysis skipped: missing AZURE_OPENAI_KEY or AZURE_OPENAI_ENDPOINT")
        return None

    prompt = f"""あなたはライブコマースの商品分析エキスパートです。以下の{len(image_urls)}枚の商品画像を全て徹底的に分析してください。

## 分析指示（各画像について必ず実行すること）

1. **画像内テキストの完全読み取り（OCR）**: 各画像のパッケージ、ラベル、成分表、効能表示、使用方法、キャッチコピー、注意書きを全て読み取る
2. **成分分析**: 読み取れた成分名から、その効果・効能を専門的に解説
3. **使用方法の読み取り**: 画像から読み取れる使い方、適量、頻度の情報
4. **ビフォーアフター分析**: 比較画像があれば、変化を具体的に説明
5. **パッケージデザイン分析**: 高級感、ブランドイメージ、ターゲット層の推測
6. **ライブコマース活用法**: 各画像をライブ配信でどう見せるかの具体的提案

以下のJSON形式で返してください：
{{
  "product_type": "商品の種類（具体的に。例：ピーリング美容液、高保湿クリーム）",
  "brand_name": "ブランド名（画像から読み取れた場合）",
  "text_on_images": ["全画像から読み取った全てのテキスト（成分名、効能、キャッチコピー、使用方法等）"],
  "ingredients_found": ["読み取れた成分名とその効果の説明（例：ヒアルロン酸 - 保湿・水分保持）"],
  "claimed_effects": ["画像から読み取れる効能・効果の主張"],
  "visual_features": ["商品の視覚的特徴（色、質感、テクスチャー、形状、高級感等）"],
  "packaging": "パッケージの詳細な特徴（素材感、デザイン、サイズ感、開封方法）",
  "usage_instructions": "画像から読み取れる使い方の詳細情報（適量、頻度、手順）",
  "selling_points": ["ライブコマースで訴求すべきセールスポイント（8つ以上、具体的に）"],
  "demo_script_suggestions": [
    "具体的なデモ提案1（例：手の甲に1-2滴垂らして浸透速度を実演）",
    "具体的なデモ提案2（例：テクスチャーの伸びを見せながらコメント）",
    "具体的なデモ提案3（例：パッケージを近づけて成分表を読み上げる）",
    "具体的なデモ提案4（例：ビフォーアフターの写真を見せて解説）"
  ],
  "talk_points": [
    "配信者が話すべき具体的トークポイント1（商品特徴を活かした自然な会話例）",
    "配信者が話すべき具体的トークポイント2",
    "配信者が話すべき具体的トークポイント3"
  ],
  "image_descriptions": ["各画像の詳細な説明（何が写っているか、どんな情報が読み取れるか）"]
}}
JSONのみを出力してください。"""

    try:
        client = openai.AzureOpenAI(
            api_key=azure_key,
            azure_endpoint=azure_endpoint,
            api_version=api_version,
        )

        # Build content array with text + all images (Responses API format)
        content_parts = [{"type": "input_text", "text": prompt}]
        for url in image_urls[:10]:  # max 10 images
            content_parts.append({
                "type": "input_image",
                "image_url": url,
            })

        response = client.responses.create(
            model=vision_model,
            input=[{"role": "user", "content": content_parts}],
            max_output_tokens=4000,
        )

        # Extract text from Responses API output
        result_content = ""
        if hasattr(response, "output_text") and response.output_text:
            result_content = response.output_text.strip()
        elif hasattr(response, "output") and response.output:
            for item in response.output:
                if hasattr(item, "content"):
                    for part in item.content:
                        if hasattr(part, "text"):
                            result_content += part.text
            result_content = result_content.strip()

        if not result_content:
            logger.warning("Multiple image analysis returned empty content")
            # Fallback: try single image
            return await _analyze_product_image(image_urls[0])

        json_match = re.search(r"\{[\s\S]*\}", result_content)
        if json_match:
            result = json.loads(json_match.group())
            logger.info(f"Multiple image analysis success: {len(image_urls)} images, {len(result.get('selling_points', []))} selling points")
            return result
        return {"raw_analysis": result_content}
    except Exception as e:
        logger.warning(f"Multiple product image analysis failed: {type(e).__name__}: {e}")
        # Fallback: analyze just the first image
        try:
            return await _analyze_product_image(image_urls[0])
        except Exception as e2:
            logger.warning(f"Single image fallback also failed: {e2}")
            return None


# ──────────────────────────────────────────────
# Internal: Prompt Builder (v2 with feedback knowledge + format)
# ──────────────────────────────────────────────

def _build_standalone_prompt(
    product_name: str,
    product_description: Optional[str],
    product_price: Optional[str],
    original_price: Optional[str] = None,
    discounted_price: Optional[str] = None,
    benefits: Optional[str] = None,
    target_audience: Optional[str] = None,
    product_analysis: Optional[Dict] = None,
    cross_patterns: Optional[Dict] = None,
    feedback_knowledge: Optional[Dict] = None,
    tone: str = "professional_friendly",
    language: str = "ja",
    duration_minutes: int = 10,
    additional_instructions: Optional[str] = None,
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

    # Price handling: prefer separate original/discounted, fallback to combined
    if original_price and discounted_price:
        product_section += f"- 通常販売価格: {original_price}\n"
        product_section += f"- 配信限定割引価格: {discounted_price}\n"
        product_section += "- 【重要】台本内で通常価格→割引価格の比較を効果的に演出してください。お得感を強調するCTAを入れてください。\n"
    elif discounted_price:
        product_section += f"- 配信特別価格: {discounted_price}\n"
    elif original_price:
        product_section += f"- 販売価格: {original_price}\n"
    elif product_price:
        product_section += f"- 価格: {product_price}\n"

    if product_description:
        product_section += f"- 商品説明: {product_description}\n"
    if target_audience:
        product_section += f"- ターゲット層: {target_audience}\n"

    # Benefits / Tokuten section
    if benefits:
        product_section += f"\n### 配信限定特典・キャンペーン\n{benefits}\n"
        product_section += "- 【重要】特典情報は台本のCTAセクションで必ず強調してください。視聴者限定の特別感を演出してください。\n"

    # Product image analysis section — CRITICAL for script quality
    image_section = ""
    if product_analysis:
        image_section = "\n## AI商品画像分析結果（★台本に必ず反映すること）\n"
        image_section += "※ 以下の分析結果は実際の商品画像からAIが読み取った情報です。\n"
        image_section += "※ 台本のセリフ内でこれらの情報を具体的に言及してください。\n\n"
        if isinstance(product_analysis, dict):
            if product_analysis.get("product_type"):
                image_section += f"### 商品タイプ: {product_analysis['product_type']}\n"
            if product_analysis.get("brand_name"):
                image_section += f"- ブランド名: {product_analysis['brand_name']}\n"

            # Text found on images (OCR results)
            text_on = product_analysis.get("text_on_images") or product_analysis.get("text_on_image") or []
            if text_on:
                if isinstance(text_on, list):
                    image_section += f"\n### 画像内テキスト（パッケージから読み取った情報）:\n"
                    for t in text_on:
                        image_section += f"- {t}\n"
                else:
                    image_section += f"\n### 画像内テキスト: {text_on}\n"

            # Ingredients
            ingredients = product_analysis.get("ingredients_found", [])
            if ingredients:
                if isinstance(ingredients, list):
                    image_section += f"\n### 成分情報（台本内で具体的に言及すること）:\n"
                    for ing in ingredients:
                        image_section += f"- {ing}\n"
                else:
                    image_section += f"\n### 成分情報: {ingredients}\n"

            # Claimed effects
            effects = product_analysis.get("claimed_effects", [])
            if effects:
                if isinstance(effects, list):
                    image_section += f"\n### 効能・効果（台本の商品説明セクションで必ず触れる）:\n"
                    for eff in effects:
                        image_section += f"- {eff}\n"
                else:
                    image_section += f"\n### 効能・効果: {effects}\n"

            # Visual features
            if product_analysis.get("visual_features"):
                features = product_analysis['visual_features']
                if isinstance(features, list):
                    image_section += f"\n### 視覚的特徴: {', '.join(features)}\n"
                else:
                    image_section += f"\n### 視覚的特徴: {features}\n"

            # Usage instructions
            usage = product_analysis.get("usage_instructions", "")
            if usage:
                image_section += f"\n### 使用方法（デモセクションで実演する）: {usage}\n"

            # Packaging
            if product_analysis.get("packaging"):
                image_section += f"- パッケージ: {product_analysis['packaging']}\n"

            # Selling points
            if product_analysis.get("selling_points"):
                points = product_analysis['selling_points']
                if isinstance(points, list):
                    image_section += f"\n### セールスポイント（台本全体で活用）:\n"
                    for i, sp in enumerate(points, 1):
                        image_section += f"{i}. {sp}\n"
                else:
                    image_section += f"\n### セールスポイント: {points}\n"

            # Demo suggestions
            demos = product_analysis.get("demo_script_suggestions") or []
            if demos:
                if isinstance(demos, list):
                    image_section += f"\n### デモ実演提案（台本のデモセクションに直接反映）:\n"
                    for d in demos:
                        image_section += f"- {d}\n"
                elif product_analysis.get("suggested_demo"):
                    image_section += f"- デモ提案: {product_analysis['suggested_demo']}\n"

            # Talk points
            talks = product_analysis.get("talk_points", [])
            if talks:
                if isinstance(talks, list):
                    image_section += f"\n### トークポイント（セリフに織り込む）:\n"
                    for tp in talks:
                        image_section += f"- {tp}\n"

            # Image descriptions
            if product_analysis.get("image_descriptions"):
                descs = product_analysis['image_descriptions']
                if isinstance(descs, list) and len(descs) > 1:
                    image_section += "\n### 各画像の分析:\n"
                    for i, desc in enumerate(descs, 1):
                        image_section += f"  {i}. {desc}\n"

            # Strong instruction to use image analysis in script
            image_section += "\n### ★★★ 重要指示 ★★★\n"
            image_section += "上記の商品画像分析結果を台本に必ず反映してください：\n"
            image_section += "1. 成分名をセリフ内で具体的に言及する（例：『これ、ヒアルロン酸が入ってるんですよ』）\n"
            image_section += "2. 効能を自然な会話で伝える（例：『これのいいところは、保湿力がすごいんです』）\n"
            image_section += "3. デモ提案を台本の実演セクションに織り込む\n"
            image_section += "4. パッケージの特徴をオープニングで見せる演出に活用\n"
            image_section += "5. トークポイントをセリフの自然な流れに組み込む\n"

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
