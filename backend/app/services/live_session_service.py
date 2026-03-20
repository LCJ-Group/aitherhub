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
7. テキストのみ出力（メタ情報やマークダウン記法は不要）"""


async def generate_product_script(
    product_name: str,
    product_description: str = "",
    product_price: str = "",
    product_features: Optional[List[str]] = None,
    tone: str = "professional_friendly",
    language: str = "ja",
    script_type: str = "introduction",
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
        script_type: "introduction" | "highlight" | "promotion" | "closing"
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
    }
    type_desc = type_map.get(script_type, type_map["introduction"])

    features_text = ""
    if product_features:
        features_text = "\n".join(f"- {f}" for f in product_features)

    prompt = f"""以下の商品情報から、ライブ配信用の台本を{lang_name}で生成してください。

## 商品情報
- 商品名: {product_name}
- 説明: {product_description or '(なし)'}
- 価格: {product_price or '(未設定)'}
- 特徴:
{features_text or '(なし)'}

## 台本タイプ
{type_desc}

## トーン
{tone_desc}

## ルール
- 200〜500文字で生成
- デジタルヒューマンが自然に読み上げられる文体
- 視聴者への呼びかけを含める
- テキストのみ出力"""

    try:
        import openai
        client = openai.AsyncOpenAI()
        response = await client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": SALES_BRAIN_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1024,
            temperature=0.7,
        )
        script = response.choices[0].message.content.strip()
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
6. テキストのみ出力"""


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
        import openai
        client = openai.AsyncOpenAI()
        response = await client.chat.completions.create(
            model="gpt-4.1-nano",  # Fast model for real-time responses
            messages=[
                {"role": "system", "content": COMMENT_RESPONSE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=256,
            temperature=0.8,
        )
        reply = response.choices[0].message.content.strip()
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

TIKTOK_PRODUCT_ANALYSIS_PROMPT = """あなたはECサイトの商品データ解析AIです。
TikTok Shopの商品タイトルと画像URLから、ライブコマース配信に必要な商品情報を構造化してください。

出力はJSON形式で、以下のフィールドを含めてください:
{
  "name": "商品名（簡潔に）",
  "description": "商品の説明文（50〜150文字）",
  "price": "価格（わかる場合のみ）",
  "features": ["特徴1", "特徴2", "特徴3"],
  "category": "カテゴリ（例: 美容, ファッション, 食品, 電子機器, etc.）",
  "target_audience": "ターゲット層（例: 20〜30代女性）",
  "selling_points": ["セールスポイント1", "セールスポイント2"]
}

ルール:
1. 商品タイトルから情報を最大限抽出する
2. 不明な項目は推測で埋めるが、価格が不明な場合は空文字にする
3. JSONのみ出力（説明文やマークダウンは不要）"""


async def _resolve_tiktok_url(product_url: str) -> str:
    """Resolve TikTok short URL to full URL by following redirects (using requests in thread)."""
    import asyncio
    import requests as sync_requests

    def _follow_redirects(url: str) -> str:
        try:
            resp = sync_requests.head(
                url,
                allow_redirects=True,
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0 (compatible; AitherHub/1.0)"}
            )
            return resp.url
        except Exception:
            # Fallback: try GET
            try:
                resp = sync_requests.get(
                    url,
                    allow_redirects=True,
                    timeout=15,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; AitherHub/1.0)"},
                    stream=True,
                )
                final_url = resp.url
                resp.close()
                return final_url
            except Exception:
                return url

    loop = asyncio.get_event_loop()
    resolved = await loop.run_in_executor(None, _follow_redirects, product_url)
    return str(resolved)


async def _analyze_product_with_gpt(
    product_title: str,
    product_id: str,
    product_image: str,
    language: str,
) -> str:
    """Use GPT to analyze product title and return structured JSON."""
    import openai

    lang_map = {"ja": "日本語", "zh": "中文", "en": "English"}
    lang_name = lang_map.get(language, "日本語")

    prompt = f"""以下のTikTok Shop商品のタイトルから、商品情報を{lang_name}で構造化してください。

商品タイトル: {product_title}
商品ID: {product_id}
商品画像URL: {product_image or '(なし)'}

JSONのみ出力してください。"""

    client = openai.AsyncOpenAI()
    response = await client.chat.completions.create(
        model="gpt-4.1-nano",
        messages=[
            {"role": "system", "content": TIKTOK_PRODUCT_ANALYSIS_PROMPT},
            {"role": "user", "content": prompt},
        ],
        max_tokens=512,
        temperature=0.3,
    )
    return response.choices[0].message.content.strip()


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

        # Fallback: try to extract from encode_params or other fields
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

        # ── Step 3: Build product data from og_info (no GPT needed) ──
        # GPT analysis is done later via sales-brain/generate-script
        product_data = {
            "name": product_title,
            "description": product_title,  # Use title as description initially
            "price": "",
            "features": [],
            "image_url": product_image,
            "original_url": original_url,
            "tiktok_product_id": product_id,
            "source": "tiktok_shop",
        }

        # Try to extract price from title (common patterns: $XX, ¥XX, XX円)
        price_match = re.search(r'[\$¥￥]\s*[\d,.]+|[\d,.]+\s*[円元]', product_title)
        if price_match:
            product_data["price"] = price_match.group(0)

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
