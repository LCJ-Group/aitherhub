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

    # --- Strategy 1: Azure OpenAI Responses API (production) ---
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
    portrait_url: str,
    portrait_type: str = "image",
    engine: str = "imtalker",
    voice_id: Optional[str] = None,
    language: str = "ja",
    products: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Create a new livestream session."""
    session_id = _new_session_id()
    session = {
        "session_id": session_id,
        "status": "active",
        "engine": engine,
        "portrait_url": portrait_url,
        "portrait_type": portrait_type,
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
    logger.info(f"Live session created: {session_id}, engine={engine}, products={len(products or [])}")
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
- テキストのみ出力"""

    try:
        gpt_model = model_override or "gpt-4.1-mini"
        script = await _call_gpt(
            messages=[
                {"role": "system", "content": SALES_BRAIN_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
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


async def import_tiktok_product(
    product_url: str,
    language: str = "ja",
) -> Dict[str, Any]:
    """
    Import a product from TikTok Shop URL.

    Supports:
      - Short URLs: https://vt.tiktok.com/...
      - Full URLs: https://www.tiktok.com/view/product/...

    Flow:
      1. Follow redirect to get full URL with og_info (using requests, not httpx)
      2. Parse og_info for product title + image
      3. Use GPT to analyze and structure the product data (separate function)
    """
    import json as json_module
    import urllib.parse
    import re

    product_title = ""
    product_image = ""
    product_id = ""
    original_url = product_url

    try:
        # ── Step 1: Resolve short URL → full URL (using requests, not httpx) ──
        redirect_url = await _resolve_tiktok_url(product_url)
        logger.info(f"TikTok URL resolved: {redirect_url[:200]}")

        # ── Step 2: Parse og_info from URL parameters ──
        parsed = urllib.parse.urlparse(redirect_url)
        params = urllib.parse.parse_qs(parsed.query)

        # Extract product ID from path
        path_match = re.search(r"/product/(\d+)", parsed.path)
        if path_match:
            product_id = path_match.group(1)

        # Extract og_info (contains title + image)
        if "og_info" in params:
            og_info = json_module.loads(params["og_info"][0])
            product_title = og_info.get("title", "")
            product_image = og_info.get("image", "")
            logger.info(f"TikTok og_info parsed: title='{product_title[:60]}', image={'yes' if product_image else 'no'}")

        # Fallback 1: Scrape HTML page for title and meta tags
        if not product_title:
            try:
                import httpx
                async with httpx.AsyncClient(
                    follow_redirects=True,
                    timeout=15.0,
                    headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"},
                ) as client:
                    resp = await client.get(redirect_url)
                    html_text = resp.text

                    # Try og:title
                    og_title_match = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html_text)
                    if og_title_match:
                        product_title = og_title_match.group(1).strip()
                        logger.info(f"TikTok og:title from HTML: '{product_title[:80]}'")

                    # Try <title> tag
                    if not product_title:
                        title_match = re.search(r'<title[^>]*>([^<]+)</title>', html_text)
                        if title_match:
                            raw_title = title_match.group(1).strip()
                            # Remove common suffixes like " | TikTok"
                            product_title = re.sub(r'\s*[|\-]\s*TikTok.*$', '', raw_title).strip()
                            logger.info(f"TikTok <title> from HTML: '{product_title[:80]}'")

                    # Try og:image
                    if not product_image:
                        og_img_match = re.search(r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)', html_text)
                        if og_img_match:
                            product_image = og_img_match.group(1).strip()

                    # Try to find price in HTML
                    price_html_match = re.search(r'["\']price["\']\s*:\s*["\']?([\d,.]+)', html_text)
                    if not price_html_match:
                        price_html_match = re.search(r'[¥￥$]\s*([\d,.]+)', html_text)

            except Exception as html_err:
                logger.warning(f"TikTok HTML scrape fallback failed: {html_err}")

        # Fallback 2: Use product ID as name
        if not product_title:
            shop_name = params.get("unique_id", [""])[0]
            if product_id:
                product_title = f"TikTok Product #{product_id}"
                if shop_name:
                    product_title += f" by @{shop_name}"

        if not product_title:
            return {
                "success": False,
                "error": "商品情報を取得できませんでした。URLを確認してください。",
            }

        # ── Step 3: Use GPT to analyze and enrich product data ──
        product_description = product_title
        product_features = []
        product_price = ""

        # Try to extract price from title
        price_match = re.search(r'[\$¥￥]\s*[\d,.]+|[\d,.]+\s*[円元]', product_title)
        if price_match:
            product_price = price_match.group(0)

        # Use GPT to generate a rich description from the title
        try:
            import openai
            client = openai.AsyncOpenAI()

            lang_map = {"ja": "日本語", "zh": "中文", "en": "English"}
            lang_name = lang_map.get(language, "日本語")

            gpt_response = await client.chat.completions.create(
                model="gpt-4.1-nano",
                messages=[
                    {"role": "system", "content": f"あなたはTikTok Shopの商品分析AIです。商品タイトルから、{lang_name}で商品の説明文と特徴を推測して生成してください。JSON形式で出力してください。"},
                    {"role": "user", "content": f"""以下のTikTok Shop商品を分析してください。

商品タイトル: {product_title}
商品URL: {original_url}

JSON形式で出力:
{{
  "name": "商品の短い名前（ブランド名+商品カテゴリ）",
  "description": "商品の魅力的な説明文（50-100文字）",
  "features": ["特徴1", "特徴2", "特徴3"],
  "price": "価格（わかる場合）"
}}"""},
                ],
                max_tokens=512,
                temperature=0.3,
                response_format={"type": "json_object"},
            )

            gpt_text = gpt_response.choices[0].message.content.strip()
            gpt_data = json_module.loads(gpt_text)

            if gpt_data.get("name"):
                product_title = gpt_data["name"]
            if gpt_data.get("description"):
                product_description = gpt_data["description"]
            if gpt_data.get("features"):
                product_features = gpt_data["features"]
            if gpt_data.get("price") and not product_price:
                product_price = gpt_data["price"]

            logger.info(f"GPT enriched product: name='{product_title}', features={len(product_features)}")

        except Exception as gpt_err:
            logger.warning(f"GPT product analysis failed (using raw title): {gpt_err}")

        product_data = {
            "name": product_title,
            "description": product_description,
            "price": product_price,
            "features": product_features,
            "image_url": product_image,
            "original_url": original_url,
            "tiktok_product_id": product_id,
            "source": "tiktok_shop",
        }

        logger.info(f"TikTok product imported: '{product_data['name']}'")

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
