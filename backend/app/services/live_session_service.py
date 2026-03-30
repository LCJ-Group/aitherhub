"""
Live Session Service — AI Live Creator Livestream Brain

Manages livestream sessions with:
  - Sales Brain (帯貨大脳): Product info → GPT → Livestream script → TTS → Digital Human video
  - Comment Response: Viewer comment → GPT reply → TTS → Digital Human video
  - Video Queue: Pre-generated and on-demand video segments for livestream playback

Architecture:
  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
  │ Product Info  │────▶│ GPT Script   │────▶│ ElevenLabs   │────▶│ IMTalker/    │
  │ or Comment   │     │ Generation   │     │ TTS          │     │ MuseTalk     │
  └──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
                                                                        │
                                                                        ▼
                                                                  ┌──────────────┐
                                                                  │ Video Queue  │
                                                                  │ (ready for   │
                                                                  │  RTMP later) │
                                                                  └──────────────┘
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════
# GPT Client — Azure OpenAI Responses API (production)
# ══════════════════════════════════════════════

async def _call_gpt(
    messages: List[Dict[str, str]],
    model: str = "gpt-4.1-mini",
    max_tokens: int = 1024,
    temperature: float = 0.7,
) -> str:
    """
    Call GPT using the best available provider.
    Priority: Azure OpenAI (Responses API) → OpenAI Chat Completions → raise error.

    Azure OpenAI with gpt-5.2-chat uses the Responses API:
      client.responses.create(model=..., input=...) instead of
      client.chat.completions.create(model=..., messages=...)
    This matches the pattern used in chat.py and live_ai.py.
    """
    import openai
    errors = []

    # If model is a fine-tuned model (ft:...), use OpenAI API directly
    # Fine-tuned models are hosted on OpenAI, not Azure OpenAI
    is_finetune_model = model.startswith("ft:")

    # --- Strategy 1: Azure OpenAI Responses API (production) ---
    # Skip Azure for fine-tuned models — they only work via OpenAI API
    azure_key = os.getenv("AZURE_OPENAI_KEY", "")
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    azure_model = os.getenv("GPT5_MODEL") or os.getenv("GPT5_DEPLOYMENT") or "gpt-4.1-mini"
    if azure_key and azure_endpoint and not is_finetune_model:
        try:
            client = openai.AzureOpenAI(
                api_key=azure_key,
                azure_endpoint=azure_endpoint,
                api_version=os.getenv("GPT5_API_VERSION", "2025-04-01-preview"),
            )
            # Convert messages list to Responses API input format
            input_payload = []
            for m in messages:
                role = m.get("role", "user")
                content = m.get("content", "")
                input_payload.append({"role": role, "content": content})

            response = client.responses.create(
                model=azure_model,
                input=input_payload,
                max_output_tokens=max_tokens,
            )
            # Extract text from Responses API response
            result = ""
            if hasattr(response, "output_text") and response.output_text:
                result = response.output_text.strip()
            elif hasattr(response, "output") and response.output:
                # Fallback: iterate output items
                for item in response.output:
                    if hasattr(item, "content"):
                        for part in item.content:
                            if hasattr(part, "text"):
                                result += part.text
                result = result.strip()

            if result:
                logger.info(f"_call_gpt: Azure OpenAI Responses API success ({len(result)} chars, model={azure_model})")
                return result
            else:
                errors.append("Azure OpenAI: empty response")
                logger.warning("_call_gpt Azure OpenAI returned empty response")
        except Exception as e:
            errors.append(f"Azure OpenAI: {str(e)[:200]}")
            logger.warning(f"_call_gpt Azure OpenAI failed: {e}")

    # --- Strategy 2: OpenAI Chat Completions (sandbox / fallback) ---
    openai_key = os.getenv("OPENAI_API_KEY", "")
    if openai_key:
        try:
            client = openai.AsyncOpenAI()
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            result = response.choices[0].message.content.strip()
            logger.info(f"_call_gpt: OpenAI success ({len(result)} chars, model={model})")
            return result
        except Exception as e:
            errors.append(f"OpenAI: {str(e)[:200]}")
            logger.warning(f"_call_gpt OpenAI failed: {e}")

    raise RuntimeError(f"All GPT providers failed: {'; '.join(errors)}")


# ══════════════════════════════════════════════
# In-Memory Session Store
# ══════════════════════════════════════════════

# session_id → session data
_sessions: Dict[str, Dict[str, Any]] = {}


def _new_session_id() -> str:
    return f"ls-{uuid.uuid4().hex[:12]}"


# ══════════════════════════════════════════════
# Session Management
# ══════════════════════════════════════════════

def create_session(
    portrait_url: str = "",
    portrait_type: str = "image",
    engine: str = "imtalker",
    voice_id: Optional[str] = None,
    language: str = "ja",
    products: Optional[List[Dict[str, str]]] = None,
    avatar_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new livestream session."""
    session_id = _new_session_id()
    session = {
        "session_id": session_id,
        "status": "active",
        "engine": engine,
        "portrait_url": portrait_url,
        "portrait_type": portrait_type,
        "avatar_id": avatar_id,  # HeyGen Digital Twin avatar ID
        "voice_id": voice_id,
        "language": language,
        "products": products or [],
        "current_product_index": 0,
        "video_queue": [],          # list of {job_id, type, status, text_preview}
        "comment_history": [],      # list of {comment, reply, job_id, timestamp}
        "scripts_generated": [],    # list of {product_name, script, job_id}
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    _sessions[session_id] = session
    logger.info(f"Live session created: {session_id}, engine={engine}, avatar_id={avatar_id}, products={len(products or [])}")
    return session


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Get session by ID."""
    return _sessions.get(session_id)


def list_sessions() -> List[Dict[str, Any]]:
    """List all active sessions."""
    return [
        {
            "session_id": s["session_id"],
            "status": s["status"],
            "engine": s["engine"],
            "products_count": len(s["products"]),
            "queue_count": len(s["video_queue"]),
            "created_at": s["created_at"],
        }
        for s in _sessions.values()
        if s["status"] == "active"
    ]


def close_session(session_id: str) -> bool:
    """Close a session."""
    session = _sessions.get(session_id)
    if session:
        session["status"] = "closed"
        session["updated_at"] = time.time()
        return True
    return False


def add_to_queue(session_id: str, item: Dict[str, Any]) -> bool:
    """Add a video item to the session queue."""
    session = _sessions.get(session_id)
    if session:
        session["video_queue"].append(item)
        session["updated_at"] = time.time()
        return True
    return False


def update_queue_item(session_id: str, job_id: str, updates: Dict[str, Any]) -> bool:
    """Update a queue item's status."""
    session = _sessions.get(session_id)
    if session:
        for item in session["video_queue"]:
            if item.get("job_id") == job_id:
                item.update(updates)
                session["updated_at"] = time.time()
                return True
    return False


# ══════════════════════════════════════════════
# Sales Brain (帯貨大脳) — Product Script Generation
# ══════════════════════════════════════════════

SALES_BRAIN_SYSTEM_PROMPT = """あなたはライブコマースの帯貨大脳（Sales Brain）AIです。
商品情報を受け取り、デジタルヒューマンが自然に読み上げるライブ配信用の台本を生成します。

台本生成ルール:
1. 自然な話し言葉で書く（書き言葉ではなく、実際に声に出して読む台本）
2. 商品の魅力を具体的に伝える
3. 視聴者への呼びかけを含める（「皆さん」「ぜひ」「コメントで教えてください」等）
4. 適度な間（ポーズ）を意識した句読点配置
5. 1つの台本は200〜500文字程度（30秒〜1分の読み上げ時間）
6. 虚偽の効能表現、薬事法に抵触する表現は避ける
7. テキストのみ出力（メタ情報やマークダウン記法は不要）
8. ライブ配信の「流れ」を意識する — 前回の台本からの自然な繋がりを保つ
9. コメントがある場合は、商品紹介の中に自然に織り込む（「あ、○○さんからコメントいただきました！」のように）
10. 一方的な説明ではなく、視聴者との対話感を出す"""


async def generate_product_script(
    product_name: str,
    product_description: str = "",
    product_price: str = "",
    product_features: Optional[List[str]] = None,
    tone: str = "professional_friendly",
    language: str = "ja",
    script_type: str = "introduction",
    previous_script: str = "",
    pending_comments: Optional[List[Dict[str, str]]] = None,
    persona: Optional[Dict[str, str]] = None,
    model_override: Optional[str] = None,
    # ── Enhanced TikTok product data ──
    selling_points: Optional[List[str]] = None,
    achievements: Optional[List[str]] = None,
    reviews_summary: str = "",
    sold_info: str = "",
    target_audience: str = "",
    talk_hooks: Optional[List[str]] = None,
    variants: Optional[List[str]] = None,
) -> str:
    """
    Generate a livestream script for a product using GPT (Sales Brain).

    Args:
        product_name: Name of the product
        product_description: Product description
        product_price: Price information
        product_features: List of key features
        tone: Script tone
        language: Output language
        script_type: "introduction" | "highlight" | "promotion" | "closing" | "interaction" | "filler"
        previous_script: The previous script text for continuity
        pending_comments: List of viewer comments to weave into the script
        persona: Livestreamer persona settings (speaking_style, catchphrases, etc.)
    """
    lang_map = {
        "ja": "日本語",
        "zh": "中文",
        "en": "English",
    }
    lang_name = lang_map.get(language, "日本語")

    tone_map = {
        "professional_friendly": "プロフェッショナルだが親しみやすいトーン",
        "energetic": "エネルギッシュで盛り上がるトーン",
        "calm": "落ち着いた上品なトーン",
        "casual": "カジュアルで親しみやすいトーン",
    }
    tone_desc = tone_map.get(tone, tone_map["professional_friendly"])

    type_map = {
        "introduction": "商品の初回紹介（特徴・メリットを中心に）",
        "highlight": "商品のハイライト（特に注目すべきポイントを強調）",
        "promotion": "セール・キャンペーン告知（緊急感・限定感を演出）",
        "closing": "商品紹介の締め（購入を促すCTA）",
        "interaction": "視聴者との交流・コメントへの返答を商品紹介に織り込む",
        "filler": "つなぎトーク（視聴者への呼びかけ、雑談、盛り上げ）",
    }
    type_desc = type_map.get(script_type, type_map["introduction"])

    features_text = ""
    if product_features:
        features_text = "\n".join(f"- {f}" for f in product_features)

    # Build enhanced TikTok product context
    enhanced_context = ""
    enhanced_parts = []
    if selling_points:
        enhanced_parts.append("\n## セールスポイント（必ず台本に織り込んでください）")
        for sp in selling_points:
            enhanced_parts.append(f"- {sp}")
    if achievements:
        enhanced_parts.append("\n## 実績・受賞歴（信頼性の証拠として使ってください）")
        for a in achievements:
            enhanced_parts.append(f"- {a}")
    if reviews_summary:
        enhanced_parts.append(f"\n## レビュー・評価\n{reviews_summary}")
    if sold_info:
        enhanced_parts.append(f"\n## 販売実績\n{sold_info}")
    if target_audience:
        enhanced_parts.append(f"\n## ターゲット層\n{target_audience}")
    if variants:
        enhanced_parts.append("\n## バリエーション")
        for v in variants:
            enhanced_parts.append(f"- {v}")
    if talk_hooks:
        enhanced_parts.append("\n## トークフック（視聴者の興味を引くフレーズ）")
        for h in talk_hooks:
            enhanced_parts.append(f"- 「{h}」")
    if enhanced_parts:
        enhanced_context = "\n".join(enhanced_parts)

    # Build persona section
    persona_text = ""
    if persona:
        persona_parts = []
        if persona.get("speaking_style"):
            persona_parts.append(f"喜び方: {persona['speaking_style']}")
        if persona.get("catchphrases"):
            persona_parts.append(f"口癖・決まり文句: {persona['catchphrases']}")
        if persona.get("personality"):
            persona_parts.append(f"性格: {persona['personality']}")
        if persona.get("expertise"):
            persona_parts.append(f"専門分野: {persona['expertise']}")
        if persona_parts:
            persona_text = "\n## ライバーのペルソナ\n" + "\n".join(f"- {p}" for p in persona_parts)
            persona_text += "\n※このライバーの喜び方・口癖を反映した台本を生成してください"

    # Build previous script context
    prev_context = ""
    if previous_script:
        prev_context = f"\n## 前回の台本（流れを続けてください）\n{previous_script[:300]}"

    # Build comments section
    comments_text = ""
    if pending_comments:
        comments_items = []
        for c in pending_comments[:3]:
            name = c.get("name", c.get("commenter", ""))
            text = c.get("text", c.get("comment", ""))
            comments_items.append(f"- {name}さん: 「{text}」")
        comments_text = "\n## 視聴者コメント（商品紹介の中に自然に織り込んでください）\n" + "\n".join(comments_items)
        comments_text += "\n※コメントへの返答を商品紹介の流れの中に自然に織り込んでください。コメント返答だけで終わらず、商品の話に戻してください。"

    prompt = f"""以下の商品情報から、ライブ配信用の台本を{lang_name}で生成してください。

## 商品情報
- 商品名: {product_name}
- 説明: {product_description or '(なし)'}
- 価格: {product_price or '(未設定)'}
- 特徴:
{features_text or '(なし)'}
{enhanced_context}
{persona_text}
{prev_context}
{comments_text}

## 台本タイプ
{type_desc}

## トーン
{tone_desc}

## ルール
- 200〜500文字で生成
- デジタルヒューマンが自然に読み上げられる文体
- 視聴者への呼びかけを含める
- 前回の台本から自然に繋がるようにする
- コメントがある場合は商品紹介の中に自然に織り込む
- セールスポイント・実績・レビュー情報がある場合は必ず具体的な数字を含めて台本に織り込む
- 「累計○万本」「ランキング1位」などの実績は視聴者の信頼を得るために積極的に使う
- テキストのみ出力"""

    try:
        gpt_model = model_override or "gpt-4.1-mini"
        # For fine-tuned persona models, use the persona's system prompt
        # (matching the training data format) instead of generic Sales Brain prompt
        if gpt_model.startswith("ft:"):
            system_content = SALES_BRAIN_SYSTEM_PROMPT
            if persona:
                # Build persona-aware system prompt matching fine-tuning format
                persona_name = persona.get("name", "ライバー")
                persona_desc = persona.get("description", "")
                persona_style = persona.get("speaking_style", "")
                system_content = f"""あなたは「{persona_name}」というライブコマース配信者です。
視聴者とリアルタイムで会話しながら商品を紹介するライブ配信を行っています。
{f'プロフィール: {persona_desc}' if persona_desc else ''}
{f'話し方の特徴: {persona_style}' if persona_style else ''}
以下のルールに従ってください：
- 自然な日本語で話す（書き言葉ではなく話し言葉）
- 視聴者に親しみやすい口調で話す
- 商品の魅力を具体的に伝える
- 視聴者のコメントに自然に反応する
- ライブ配信のテンポ感を大切にする
- あなた自身の言葉で話す（第三者視点の描写はしない）""".strip()
            messages_for_gpt = [
                {"role": "system", "content": system_content},
                {"role": "user", "content": prompt},
            ]
            logger.info(f"Using fine-tuned persona model: {gpt_model}")
        else:
            messages_for_gpt = [
                {"role": "system", "content": SALES_BRAIN_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
        script = await _call_gpt(
            messages=messages_for_gpt,
            model=gpt_model,
            max_tokens=1024,
            temperature=0.7,
        )
        logger.info(f"Sales Brain script generated: {len(script)} chars for '{product_name}'")
        return script

    except Exception as e:
        logger.error(f"Sales Brain script generation failed: {e}")
        # Fallback
        if language == "zh":
            return f"大家好！今天为大家推荐{product_name}。{product_description or '这是一款非常棒的商品'}。{f'现在只需{product_price}！' if product_price else ''}赶快下单吧！"
        elif language == "en":
            return f"Hello everyone! Today I'm introducing {product_name}. {product_description or 'This is an amazing product'}. {f'Now only {product_price}!' if product_price else ''} Don't miss out!"
        else:
            return f"皆さん、こんにちは！今日は{product_name}をご紹介します。{product_description or '素晴らしい商品です'}。{f'今なら{product_price}です！' if product_price else ''}ぜひチェックしてみてください！"


# ══════════════════════════════════════════════
# Comment Response — Viewer Interaction
# ══════════════════════════════════════════════

COMMENT_RESPONSE_SYSTEM_PROMPT = """あなたはライブコマースのAIホストです。
視聴者のコメントに対して、自然で親しみやすい返答を生成します。

返答ルール:
1. 短く簡潔に（50〜150文字、10〜20秒の読み上げ時間）
2. 視聴者の名前があれば呼びかける
3. 質問には具体的に回答する
4. 商品に関する質問は正確に答える（わからない場合は正直に）
5. ポジティブで明るいトーン
6. テキストのみ出力
7. コメント返答だけで終わらず、商品の話題に自然に戻す
8. ライブ配信の流れを止めないようにする"""


async def generate_comment_response(
    comment_text: str,
    commenter_name: str = "",
    current_product: Optional[Dict[str, str]] = None,
    language: str = "ja",
) -> str:
    """
    Generate a response to a viewer comment using GPT.

    Args:
        comment_text: The viewer's comment
        commenter_name: Viewer's display name
        current_product: Currently featured product info
        language: Response language
    """
    lang_map = {"ja": "日本語", "zh": "中文", "en": "English"}
    lang_name = lang_map.get(language, "日本語")

    product_context = ""
    if current_product:
        product_context = (
            f"\n現在紹介中の商品: {current_product.get('name', '')}"
            f"\n商品説明: {current_product.get('description', '')}"
            f"\n価格: {current_product.get('price', '')}"
        )

    prompt = f"""視聴者のコメントに{lang_name}で返答してください。

コメント: {f'{commenter_name}さん: ' if commenter_name else ''}{comment_text}
{product_context}

50〜150文字で、デジタルヒューマンが自然に読み上げられる返答を生成してください。
テキストのみ出力してください。"""

    try:
        reply = await _call_gpt(
            messages=[
                {"role": "system", "content": COMMENT_RESPONSE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            model="gpt-4.1-nano",
            max_tokens=256,
            temperature=0.8,
        )
        logger.info(f"Comment response generated: {len(reply)} chars for '{comment_text[:50]}'")
        return reply

    except Exception as e:
        logger.error(f"Comment response generation failed: {e}")
        if commenter_name:
            return f"{commenter_name}さん、コメントありがとうございます！"
        return "コメントありがとうございます！"


# ══════════════════════════════════════════════
# Batch Script Generation (Multiple Products)
# ══════════════════════════════════════════════

async def generate_session_scripts(
    session_id: str,
    script_types: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Generate scripts for all products in a session.

    Returns list of {product_name, script_type, script_text}
    """
    session = get_session(session_id)
    if not session:
        raise ValueError(f"Session {session_id} not found")

    if not script_types:
        script_types = ["introduction"]

    results = []
    for product in session["products"]:
        for stype in script_types:
            script = await generate_product_script(
                product_name=product.get("name", ""),
                product_description=product.get("description", ""),
                product_price=product.get("price", ""),
                product_features=product.get("features", []),
                tone=product.get("tone", "professional_friendly"),
                language=session["language"],
                script_type=stype,
                # Enhanced TikTok product data
                selling_points=product.get("selling_points"),
                achievements=product.get("achievements"),
                reviews_summary=product.get("reviews_summary", ""),
                sold_info=product.get("sold_info", ""),
                target_audience=product.get("target_audience", ""),
                talk_hooks=product.get("talk_hooks"),
                variants=product.get("variants"),
            )
            results.append({
                "product_name": product.get("name", ""),
                "script_type": stype,
                "script_text": script,
            })

    session["scripts_generated"] = results
    session["updated_at"] = time.time()
    return results


# ══════════════════════════════════════════════
# TikTok Shop Product Import — URL → AI Analysis
# ══════════════════════════════════════════════

async def _resolve_tiktok_url(product_url: str) -> str:
    """Resolve TikTok short URL to full URL by following redirects (using httpx)."""
    import httpx

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=15.0,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AitherHub/1.0)"},
        ) as client:
            resp = await client.head(product_url)
            return str(resp.url)
    except Exception:
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=15.0,
                headers={"User-Agent": "Mozilla/5.0 (compatible; AitherHub/1.0)"},
            ) as client:
                resp = await client.get(product_url)
                return str(resp.url)
        except Exception:
            return product_url


async def _analyze_product_image_vision(image_url: str, product_title: str, language: str = "ja") -> Dict[str, Any]:
    """
    Analyze a TikTok product image using GPT Vision.
    Tries: Azure OpenAI Responses API (with image_url) → OpenAI Chat Completions (with image_url) → fallback empty.
    Extracts: achievements, features, variants, catchphrase, target audience,
    reviews/ratings info, sales numbers — anything visible in the image.
    """
    import openai
    import json as _json
    import re as _re

    lang_map = {"ja": "日本語", "zh": "中文", "en": "English"}
    lang_name = lang_map.get(language, "日本語")

    # Try higher resolution image
    high_res_url = _re.sub(r'resize-webp:\d+:\d+', 'resize-webp:800:800', image_url)
    if high_res_url == image_url:
        high_res_url = _re.sub(r'resize-jpeg:\d+:\d+', 'resize-jpeg:800:800', image_url)

    system_prompt = (
        f"あなたはTikTok Shop商品画像の分析AIです。"
        f"商品画像に写っている情報を{lang_name}で徹底的に抽出してください。"
        f"テキスト、数字、ランキング、実績、キャッチコピー、カラーバリエーション、"
        f"モデルの特徴、パッケージデザインなど、見えるもの全てを報告してください。"
        f"必ずJSON形式で出力してください。"
    )

    user_text = (
        f"この商品画像を分析してください。\n"
        f"商品タイトル: {product_title}\n\n"
        f"以下のJSON形式で出力:\n"
        f'{{\n'
        f'  "product_name": "画像から読み取れる正式な商品名",\n'
        f'  "brand": "ブランド名",\n'
        f'  "catchphrase": "キャッチコピー・メインメッセージ",\n'
        f'  "achievements": ["ランキング1位", "累計50万本突破" など画像内の実績テキスト],\n'
        f'  "variants": ["カラーや種類のバリエーション"],\n'
        f'  "visible_features": ["画像から読み取れる商品特徴"],\n'
        f'  "target_audience": "ターゲット層の推測",\n'
        f'  "price_info": "価格情報（見える場合）",\n'
        f'  "reviews_visible": "レビュー・評価情報（見える場合）",\n'
        f'  "sales_info": "販売数・売上情報（見える場合）",\n'
        f'  "package_description": "パッケージの見た目・デザインの説明",\n'
        f'  "overall_impression": "商品の全体的な印象（高級感、ナチュラル、ポップなど）",\n'
        f'  "selling_points": ["ライブ配信で使える強力なセールスポイント3-5個"]\n'
        f'}}'
    )

    errors = []

    # --- Strategy 1: Azure OpenAI Responses API with image ---
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
            response = client.responses.create(
                model=azure_model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": [
                        {"type": "input_text", "text": user_text},
                        {"type": "input_image", "image_url": high_res_url},
                    ]},
                ],
                max_output_tokens=1500,
            )
            result_text = ""
            if hasattr(response, "output_text") and response.output_text:
                result_text = response.output_text.strip()
            elif hasattr(response, "output") and response.output:
                for item in response.output:
                    if hasattr(item, "content"):
                        for part in item.content:
                            if hasattr(part, "text"):
                                result_text += part.text
                result_text = result_text.strip()

            if result_text:
                # Extract JSON from response (may have markdown code block)
                json_match = _re.search(r'\{[\s\S]*\}', result_text)
                if json_match:
                    result = _json.loads(json_match.group())
                    logger.info(f"Vision analysis (Azure) complete: {len(result.get('selling_points', []))} selling points")
                    return result
            errors.append("Azure Vision: empty response")
        except Exception as e:
            errors.append(f"Azure Vision: {str(e)[:200]}")
            logger.warning(f"Azure OpenAI Vision failed: {e}")

    # --- Strategy 2: OpenAI Chat Completions with image_url ---
    openai_key = os.getenv("OPENAI_API_KEY", "")
    if openai_key:
        try:
            client = openai.AsyncOpenAI()
            response = await client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": [
                        {"type": "text", "text": user_text},
                        {"type": "image_url", "image_url": {"url": high_res_url, "detail": "high"}},
                    ]},
                ],
                max_tokens=1500,
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            result = _json.loads(response.choices[0].message.content.strip())
            logger.info(f"Vision analysis (OpenAI) complete: {len(result.get('selling_points', []))} selling points")
            return result
        except Exception as e:
            errors.append(f"OpenAI Vision: {str(e)[:200]}")
            logger.warning(f"OpenAI Vision failed: {e}")

    logger.warning(f"All Vision providers failed: {'; '.join(errors)}")
    return {}


async def import_tiktok_product(
    product_url: str,
    language: str = "ja",
) -> Dict[str, Any]:
    """
    Import a product from TikTok Shop URL with rich analysis.

    Supports:
      - Short URLs: https://vt.tiktok.com/...
      - Full URLs: https://www.tiktok.com/view/product/...

    Flow:
      1. Follow redirect to get full URL with og_info
      2. Parse og_info for product title + image
      3. Analyze product image with GPT Vision (extract achievements, reviews, features)
      4. Enrich with GPT text analysis
      5. Return comprehensive product profile for livestream scripts
    """
    import json as json_module
    import urllib.parse
    import re

    product_title = ""
    product_image = ""
    product_id = ""
    seller_username = ""
    original_url = product_url

    try:
        # ── Step 1: Resolve short URL → full URL ──
        redirect_url = await _resolve_tiktok_url(product_url)
        logger.info(f"TikTok URL resolved: {redirect_url[:200]}")

        # ── Step 2: Parse ALL info from URL parameters ──
        parsed = urllib.parse.urlparse(redirect_url)
        params = urllib.parse.parse_qs(parsed.query)

        # Extract product ID from path
        path_match = re.search(r"/product/(\d+)", parsed.path)
        if path_match:
            product_id = path_match.group(1)

        # Extract seller username
        seller_username = params.get("unique_id", [""])[0]

        # Extract og_info (contains title + image)
        if "og_info" in params:
            og_info = json_module.loads(params["og_info"][0])
            product_title = og_info.get("title", "")
            product_image = og_info.get("image", "")
            logger.info(f"TikTok og_info parsed: title='{product_title[:60]}', image={'yes' if product_image else 'no'}")

        # Fallback: Scrape HTML page for title and meta tags
        if not product_title:
            try:
                import httpx
                async with httpx.AsyncClient(
                    follow_redirects=True,
                    timeout=15.0,
                    headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
                ) as client:
                    resp = await client.get(redirect_url)
                    html_text = resp.text
                    og_title_match = re.search(r'<meta[^>]*property=["\x27]og:title["\x27][^>]*content=["\x27]([^"\x27]+)', html_text)
                    if og_title_match:
                        product_title = og_title_match.group(1).strip()
                    if not product_title:
                        title_match = re.search(r'<title[^>]*>([^<]+)</title>', html_text)
                        if title_match:
                            product_title = re.sub(r'\s*[|\-]\s*TikTok.*$', '', title_match.group(1).strip()).strip()
                    if not product_image:
                        og_img_match = re.search(r'<meta[^>]*property=["\x27]og:image["\x27][^>]*content=["\x27]([^"\x27]+)', html_text)
                        if og_img_match:
                            product_image = og_img_match.group(1).strip()
            except Exception as html_err:
                logger.warning(f"TikTok HTML scrape fallback failed: {html_err}")

        # Fallback: Use product ID as name
        if not product_title:
            if product_id:
                product_title = f"TikTok Product #{product_id}"
                if seller_username:
                    product_title += f" by @{seller_username}"

        if not product_title:
            return {
                "success": False,
                "error": "商品情報を取得できませんでした。URLを確認してください。",
            }

        # ── Step 3: Analyze product image with GPT Vision ──
        vision_data = {}
        if product_image:
            vision_data = await _analyze_product_image_vision(
                product_image, product_title, language
            )
            logger.info(f"Vision analysis: {list(vision_data.keys())}")

        # ── Step 4: Enrich with GPT text analysis (combine title + vision data) ──
        product_description = product_title
        product_features = []
        product_price = ""
        selling_points = []
        target_audience = ""
        achievements = []
        reviews_summary = ""
        sold_info = ""
        category = ""

        # Extract from vision data
        if vision_data:
            if vision_data.get("catchphrase"):
                product_description = vision_data["catchphrase"]
            if vision_data.get("visible_features"):
                product_features = vision_data["visible_features"]
            if vision_data.get("selling_points"):
                selling_points = vision_data["selling_points"]
            if vision_data.get("target_audience"):
                target_audience = vision_data["target_audience"]
            if vision_data.get("achievements"):
                achievements = vision_data["achievements"]
            if vision_data.get("reviews_visible"):
                reviews_summary = vision_data["reviews_visible"]
            if vision_data.get("sales_info"):
                sold_info = vision_data["sales_info"]
            if vision_data.get("price_info"):
                product_price = vision_data["price_info"]

        # Try to extract price from title if not from vision
        if not product_price:
            price_match = re.search(r'[\$¥￥]\s*[\d,.]+|[\d,.]+\s*[円元]', product_title)
            if price_match:
                product_price = price_match.group(0)

        # GPT text enrichment — combine og_info title + vision analysis for comprehensive profile
        try:
            lang_map = {"ja": "日本語", "zh": "中文", "en": "English"}
            lang_name = lang_map.get(language, "日本語")

            # Build context from vision analysis
            vision_context = ""
            if vision_data:
                vision_parts = []
                if vision_data.get("brand"):
                    vision_parts.append(f"ブランド: {vision_data['brand']}")
                if vision_data.get("catchphrase"):
                    vision_parts.append(f"キャッチコピー: {vision_data['catchphrase']}")
                if vision_data.get("achievements"):
                    vision_parts.append(f"実績: {', '.join(vision_data['achievements'])}")
                if vision_data.get("variants"):
                    vision_parts.append(f"バリエーション: {', '.join(vision_data['variants'])}")
                if vision_data.get("visible_features"):
                    vision_parts.append(f"特徴: {', '.join(vision_data['visible_features'])}")
                if vision_data.get("package_description"):
                    vision_parts.append(f"パッケージ: {vision_data['package_description']}")
                if vision_data.get("overall_impression"):
                    vision_parts.append(f"印象: {vision_data['overall_impression']}")
                if vision_data.get("reviews_visible"):
                    vision_parts.append(f"レビュー情報: {vision_data['reviews_visible']}")
                if vision_data.get("sales_info"):
                    vision_parts.append(f"販売情報: {vision_data['sales_info']}")
                vision_context = "\n".join(vision_parts)

            # Build vision section text
            vision_section = "画像分析結果:\n" + vision_context if vision_context else "（画像分析なし）"

            system_msg = (
                f"あなたはTikTok Shopの商品分析AIです。"
                f"商品タイトルと画像分析結果を組み合わせて、{lang_name}でライブ配信用の"
                f"包括的な商品プロファイルを作成してください。"
                f"ライブ配信者が自然に話せるような情報を整理してください。"
                f"必ずJSON形式で出力してください。"
            )

            user_msg = (
                f"以下のTikTok Shop商品の包括的プロファイルを作成してください。\n\n"
                f"商品タイトル: {product_title}\n"
                f"販売者: @{seller_username}\n"
                f"商品URL: {original_url}\n\n"
                f"{vision_section}\n\n"
                f"以下のJSON形式で出力:\n"
                f"{{\n"
                f'  "name": "商品の正式名（ブランド名+商品名）",\n'
                f'  "short_name": "会話で使う短い呼び名",\n'
                f'  "description": "商品の魅力的な説明文（100-200文字、ライブで話せる内容）",\n'
                f'  "category": "商品カテゴリ",\n'
                f'  "features": ["特徴1", "特徴2", "特徴3", "特徴4", "特徴5"],\n'
                f'  "selling_points": ["ライブで使える強力なセールスポイント（実績・数字入り）"],\n'
                f'  "target_audience": "ターゲット層の説明",\n'
                f'  "achievements": ["ランキング実績", "累計販売数" など],\n'
                f'  "reviews_summary": "レビュー・評価の要約（わかる場合）",\n'
                f'  "sold_info": "販売実績（わかる場合）",\n'
                f'  "price": "価格（わかる場合）",\n'
                f'  "variants": ["バリエーション一覧"],\n'
                f'  "talk_hooks": ["ライブ配信で視聴者の興味を引くフレーズ3つ"]\n'
                f"}}\n"
            )

            # Use _call_gpt which handles Azure OpenAI / OpenAI fallback
            gpt_text = await _call_gpt(
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                model="gpt-4.1-mini",
                max_tokens=1500,
                temperature=0.3,
            )

            # Parse JSON from response (may have markdown code block)
            json_match = re.search(r'\{[\s\S]*\}', gpt_text)
            if json_match:
                gpt_data = json_module.loads(json_match.group())
            else:
                gpt_data = json_module.loads(gpt_text)

            # Merge GPT enrichment with vision data
            if gpt_data.get("name"):
                product_title = gpt_data["name"]
            if gpt_data.get("description"):
                product_description = gpt_data["description"]
            if gpt_data.get("features"):
                product_features = gpt_data["features"]
            if gpt_data.get("selling_points"):
                selling_points = gpt_data["selling_points"]
            if gpt_data.get("target_audience"):
                target_audience = gpt_data["target_audience"]
            if gpt_data.get("achievements"):
                achievements = gpt_data["achievements"]
            if gpt_data.get("reviews_summary"):
                reviews_summary = gpt_data["reviews_summary"]
            if gpt_data.get("sold_info"):
                sold_info = gpt_data["sold_info"]
            if gpt_data.get("price") and not product_price:
                product_price = gpt_data["price"]
            if gpt_data.get("category"):
                category = gpt_data["category"]

            logger.info(f"GPT enriched product: name='{product_title}', features={len(product_features)}, selling_points={len(selling_points)}")

        except Exception as gpt_err:
            logger.warning(f"GPT product analysis failed (using vision/raw data): {gpt_err}")

        # ── Step 5: Build comprehensive product data ──
        product_data = {
            "name": product_title,
            "short_name": gpt_data.get("short_name", product_title) if 'gpt_data' in locals() else product_title,
            "description": product_description,
            "price": product_price,
            "features": product_features,
            "selling_points": selling_points,
            "achievements": achievements,
            "reviews_summary": reviews_summary,
            "sold_info": sold_info,
            "target_audience": target_audience,
            "category": category,
            "variants": gpt_data.get("variants", vision_data.get("variants", [])) if 'gpt_data' in locals() else vision_data.get("variants", []),
            "talk_hooks": gpt_data.get("talk_hooks", []) if 'gpt_data' in locals() else [],
            "image_url": product_image,
            "image_analysis": vision_data,
            "original_url": original_url,
            "tiktok_product_id": product_id,
            "seller_username": seller_username,
            "source": "tiktok_shop",
        }

        logger.info(f"TikTok product imported (enriched): '{product_data['name']}' with {len(selling_points)} selling points, {len(achievements)} achievements")

        return {
            "success": True,
            "product": product_data,
        }

    except Exception as e:
        logger.exception(f"TikTok product import error: {e}")
        return {
            "success": False,
            "error": f"商品情報の取得に失敗しました: {str(e)}",
        }


def add_product_to_session(session_id: str, product: Dict[str, Any]) -> bool:
    """Add an imported product to a live session."""
    session = _sessions.get(session_id)
    if session:
        session["products"].append(product)
        session["updated_at"] = time.time()
        logger.info(f"Product added to session {session_id}: {product.get('name', 'unknown')}")
        return True
    return False
