"""
Auto Live Engine — AI自動ライブ配信エンジン

商品データをもとにAIがセールストークを自動生成し、
speakQueueに連続pushすることでアバターがずっと喋り続ける。
コメントが来たら割り込みで応答する。

Architecture:
  [Auto Speech Loop]
  商品A紹介 → 商品B紹介 → 商品C紹介 → ... (ループ)
  
  [Comment Interrupt]
  コメント検出 → AI応答生成 → speakQueue push → 自動スピーチ再開
"""
import os
import time
import asyncio
import logging
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ============================================================
# Configuration
# ============================================================
OPENAI_MODEL = os.getenv("AUTO_LIVE_MODEL", "gpt-4.1-mini")
COMMENT_POLL_INTERVAL = 5  # seconds
SPEAK_QUEUE_CHECK_INTERVAL = 2  # seconds


@dataclass
class LiveProduct:
    """ライブ配信で紹介する商品"""
    item_id: int
    item_name: str
    description: str = ""
    price: str = ""
    images: List[str] = field(default_factory=list)
    attributes: List[Dict] = field(default_factory=list)
    brand: str = ""
    sales: int = 0
    rating: float = 0.0
    models: List[Dict] = field(default_factory=list)

    def to_context(self) -> str:
        """AIプロンプト用のコンテキスト文字列を生成"""
        parts = [f"Product: {self.item_name}"]
        if self.brand:
            parts.append(f"Brand: {self.brand}")
        if self.price:
            parts.append(f"Price: {self.price}")
        if self.description:
            # 長すぎる場合は最初の500文字
            desc = self.description[:500]
            parts.append(f"Description: {desc}")
        if self.attributes:
            attrs = ", ".join([f"{a.get('attribute_name', '')}: {a.get('attribute_value', '')}" for a in self.attributes if a.get('attribute_value')])
            if attrs:
                parts.append(f"Attributes: {attrs}")
        if self.sales:
            parts.append(f"Total Sales: {self.sales}")
        if self.rating:
            parts.append(f"Rating: {self.rating}/5")
        if self.models:
            variants = ", ".join([m.get("model_name", "") for m in self.models[:5]])
            if variants:
                parts.append(f"Variants: {variants}")
        return "\n".join(parts)


@dataclass
class AutoLiveSession:
    """自動ライブ配信セッションの状態管理"""
    session_id: str  # AitherHub internal session ID
    shopee_session_id: Optional[int] = None
    products: List[LiveProduct] = field(default_factory=list)
    current_product_index: int = 0
    is_running: bool = False
    is_paused: bool = False
    language: str = "en"  # en, ja, zh, th, etc.
    style: str = "professional"  # professional, casual, energetic
    speak_count: int = 0
    comment_count: int = 0
    last_comment_id: Optional[str] = None
    _task: Optional[asyncio.Task] = field(default=None, repr=False)
    _comment_task: Optional[asyncio.Task] = field(default=None, repr=False)


# Global sessions registry
_sessions: Dict[str, AutoLiveSession] = {}


# ============================================================
# AI Sales Talk Generation
# ============================================================
async def generate_sales_talk(
    product: LiveProduct,
    language: str = "en",
    style: str = "professional",
    context: str = "",
    max_length: int = 200,
) -> str:
    """AIで商品のセールストークを生成"""
    import openai
    client = openai.AsyncOpenAI()

    lang_instructions = {
        "en": "Speak in English.",
        "ja": "日本語で話してください。",
        "zh": "请用中文说话。",
        "th": "พูดภาษาไทย",
        "ms": "Bercakap dalam Bahasa Melayu.",
    }

    style_instructions = {
        "professional": "Speak like a professional beauty consultant. Be knowledgeable and trustworthy.",
        "casual": "Speak casually and friendly, like chatting with a friend.",
        "energetic": "Be energetic and exciting! Use enthusiasm to promote the product.",
    }

    system_prompt = f"""You are an AI live commerce host selling beauty/hair care products on Shopee Live.
{lang_instructions.get(language, lang_instructions['en'])}
{style_instructions.get(style, style_instructions['professional'])}

Rules:
- Keep each speech segment under {max_length} characters (this is for TTS, so keep it concise)
- Focus on ONE key selling point per segment
- Be natural and conversational, not robotic
- Include calls to action (e.g., "add to cart now", "limited stock")
- Vary your approach: features, benefits, ingredients, usage tips, comparisons, testimonials
- Do NOT repeat the same points
- Output ONLY the speech text, no labels or formatting

{context}"""

    product_context = product.to_context()

    try:
        response = await client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Generate the next sales talk segment for this product:\n\n{product_context}"},
            ],
            max_tokens=300,
            temperature=0.8,
        )
        text = response.choices[0].message.content.strip()
        logger.info(f"[AutoLive] Generated sales talk ({len(text)} chars): {text[:80]}...")
        return text
    except Exception as e:
        logger.error(f"[AutoLive] Failed to generate sales talk: {e}")
        return ""


async def generate_comment_response(
    comment_user: str,
    comment_text: str,
    product: Optional[LiveProduct],
    language: str = "en",
) -> str:
    """コメントに対するAI応答を生成"""
    import openai
    client = openai.AsyncOpenAI()

    lang_instructions = {
        "en": "Respond in English.",
        "ja": "日本語で返答してください。",
        "zh": "请用中文回复。",
        "th": "ตอบเป็นภาษาไทย",
        "ms": "Jawab dalam Bahasa Melayu.",
    }

    product_context = product.to_context() if product else "No specific product context."

    system_prompt = f"""You are an AI live commerce host responding to a viewer's comment on Shopee Live.
{lang_instructions.get(language, lang_instructions['en'])}

Rules:
- Keep response under 100 characters (for TTS)
- Be warm and grateful
- If the comment is a question about the product, answer using the product info
- If it's a greeting or compliment, respond warmly
- Always mention the commenter's name
- Output ONLY the response text

Current product being discussed:
{product_context}"""

    try:
        response = await client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"{comment_user} says: {comment_text}"},
            ],
            max_tokens=150,
            temperature=0.7,
        )
        text = response.choices[0].message.content.strip()
        logger.info(f"[AutoLive] Comment response for {comment_user}: {text[:60]}...")
        return text
    except Exception as e:
        logger.error(f"[AutoLive] Failed to generate comment response: {e}")
        return ""


# ============================================================
# Speak Queue Integration
# ============================================================
async def push_to_speak_queue(session_id: str, text: str, priority: str = "normal") -> bool:
    """speakQueueにテキストをpush（LiveAvatarが話す）
    
    liveavatar_service.push_speak_text() はグローバルキュー。
    OBSページとメインページの両方がポーリングしてLiveKit data channelで送信する。
    priorityが'high'の場合はテキストの先頭にマーカーを付ける（将来の優先度制御用）。
    """
    from app.services.liveavatar_service import get_liveavatar_service
    service = get_liveavatar_service()

    try:
        service.push_speak_text(text)
        logger.info(f"[AutoLive] Pushed to speak queue (priority={priority}): {text[:50]}...")
        return True
    except Exception as e:
        logger.error(f"[AutoLive] Failed to push to speak queue: {e}")
        return False


async def get_queue_length(session_id: str) -> int:
    """speakQueueの残りアイテム数を取得"""
    from app.services.liveavatar_service import get_liveavatar_service
    service = get_liveavatar_service()
    try:
        return len(service._speak_queue)
    except Exception:
        return 0


# ============================================================
# Auto Speech Loop
# ============================================================
async def _auto_speech_loop(session: AutoLiveSession):
    """自動スピーチのメインループ"""
    logger.info(f"[AutoLive] Starting auto speech loop for session {session.session_id}")

    # 各商品について複数のアプローチでトークを生成
    approaches = [
        "Introduce the product and its key benefit",
        "Explain the main ingredients/materials and why they're special",
        "Share usage tips and how to get the best results",
        "Compare with competitors (without naming them) and highlight advantages",
        "Mention customer reviews and satisfaction",
        "Create urgency: limited stock, special price, today only",
    ]

    approach_index = 0

    while session.is_running:
        if session.is_paused:
            await asyncio.sleep(1)
            continue

        # キューが溜まりすぎないように待つ
        queue_len = await get_queue_length(session.session_id)
        if queue_len >= 3:
            await asyncio.sleep(SPEAK_QUEUE_CHECK_INTERVAL)
            continue

        # 現在の商品を取得
        if not session.products:
            logger.warning("[AutoLive] No products to present")
            await asyncio.sleep(5)
            continue

        product = session.products[session.current_product_index % len(session.products)]
        approach = approaches[approach_index % len(approaches)]

        # セールストーク生成
        context = f"Approach for this segment: {approach}\nThis is segment #{session.speak_count + 1} of the live stream."
        text = await generate_sales_talk(
            product=product,
            language=session.language,
            style=session.style,
            context=context,
        )

        if text:
            success = await push_to_speak_queue(session.session_id, text)
            if success:
                session.speak_count += 1
                approach_index += 1

                # 6つのアプローチを一巡したら次の商品へ
                if approach_index % len(approaches) == 0:
                    session.current_product_index += 1
                    if session.current_product_index >= len(session.products):
                        session.current_product_index = 0
                    logger.info(f"[AutoLive] Moving to next product: {session.products[session.current_product_index % len(session.products)].item_name}")

        # 次のトーク生成まで少し待つ（キューが空になるのを待つ）
        await asyncio.sleep(SPEAK_QUEUE_CHECK_INTERVAL)

    logger.info(f"[AutoLive] Auto speech loop ended for session {session.session_id}")


# ============================================================
# Comment Monitor Loop
# ============================================================
async def _comment_monitor_loop(session: AutoLiveSession):
    """コメント監視ループ — Shopeeライブのコメントを取得して応答"""
    logger.info(f"[AutoLive] Starting comment monitor for session {session.session_id}")

    if not session.shopee_session_id:
        logger.warning("[AutoLive] No Shopee session ID, comment monitoring disabled")
        return

    from app.services.shopee_live_service import get_latest_comments

    seen_comments = set()

    while session.is_running:
        try:
            comments = await get_latest_comments(session.shopee_session_id)
            if comments:
                for comment in comments:
                    comment_id = comment.get("comment_id") or f"{comment.get('user_name')}_{comment.get('create_time')}"
                    if comment_id in seen_comments:
                        continue
                    seen_comments.add(comment_id)

                    user_name = comment.get("user_name", "viewer")
                    content = comment.get("content", "")

                    if not content:
                        continue

                    logger.info(f"[AutoLive] New comment from {user_name}: {content}")

                    # 現在の商品コンテキストで応答生成
                    current_product = None
                    if session.products:
                        current_product = session.products[session.current_product_index % len(session.products)]

                    response = await generate_comment_response(
                        comment_user=user_name,
                        comment_text=content,
                        product=current_product,
                        language=session.language,
                    )

                    if response:
                        # 優先度高でキューにpush（自動スピーチより先に話す）
                        await push_to_speak_queue(session.session_id, response, priority="high")
                        session.comment_count += 1

                        # Shopeeにもテキスト返信
                        try:
                            from app.services.shopee_live_service import post_comment
                            await post_comment(session.shopee_session_id, response)
                        except Exception as e:
                            logger.warning(f"[AutoLive] Failed to post comment reply: {e}")

        except Exception as e:
            logger.error(f"[AutoLive] Comment monitor error: {e}")

        await asyncio.sleep(COMMENT_POLL_INTERVAL)

    logger.info(f"[AutoLive] Comment monitor ended for session {session.session_id}")


# ============================================================
# Session Management
# ============================================================
async def start_auto_live(
    session_id: str,
    products: List[Dict],
    language: str = "en",
    style: str = "professional",
    shopee_session_id: Optional[int] = None,
) -> Dict:
    """自動ライブ配信を開始"""
    # 既存セッションがあれば停止
    if session_id in _sessions:
        await stop_auto_live(session_id)

    # 商品データをLiveProductに変換
    live_products = []
    for p in products:
        live_products.append(LiveProduct(
            item_id=p.get("itemId") or p.get("item_id", 0),
            item_name=p.get("itemName") or p.get("item_name", "Unknown"),
            description=p.get("description", ""),
            price=str(p.get("price", "")),
            images=p.get("images", []),
            attributes=p.get("attributes", []),
            brand=p.get("brand", ""),
            sales=p.get("sales", 0),
            rating=p.get("rating", 0.0),
            models=p.get("models", []),
        ))

    session = AutoLiveSession(
        session_id=session_id,
        shopee_session_id=shopee_session_id,
        products=live_products,
        language=language,
        style=style,
        is_running=True,
    )

    # 自動スピーチループを開始
    session._task = asyncio.create_task(_auto_speech_loop(session))

    # コメント監視ループを開始（Shopeeセッションがある場合）
    if shopee_session_id:
        session._comment_task = asyncio.create_task(_comment_monitor_loop(session))

    _sessions[session_id] = session

    logger.info(f"[AutoLive] Started auto live for session {session_id} with {len(live_products)} products, lang={language}")

    return {
        "status": "started",
        "session_id": session_id,
        "product_count": len(live_products),
        "language": language,
        "style": style,
        "shopee_session_id": shopee_session_id,
    }


async def stop_auto_live(session_id: str) -> Dict:
    """自動ライブ配信を停止"""
    session = _sessions.get(session_id)
    if not session:
        return {"status": "not_found", "session_id": session_id}

    session.is_running = False

    # タスクのキャンセル
    if session._task and not session._task.done():
        session._task.cancel()
        try:
            await session._task
        except asyncio.CancelledError:
            pass

    if session._comment_task and not session._comment_task.done():
        session._comment_task.cancel()
        try:
            await session._comment_task
        except asyncio.CancelledError:
            pass

    stats = {
        "status": "stopped",
        "session_id": session_id,
        "speak_count": session.speak_count,
        "comment_count": session.comment_count,
    }

    del _sessions[session_id]
    logger.info(f"[AutoLive] Stopped auto live for session {session_id}: {stats}")
    return stats


async def pause_auto_live(session_id: str) -> Dict:
    """自動ライブ配信を一時停止"""
    session = _sessions.get(session_id)
    if not session:
        return {"status": "not_found"}
    session.is_paused = True
    return {"status": "paused", "session_id": session_id}


async def resume_auto_live(session_id: str) -> Dict:
    """自動ライブ配信を再開"""
    session = _sessions.get(session_id)
    if not session:
        return {"status": "not_found"}
    session.is_paused = False
    return {"status": "resumed", "session_id": session_id}


def get_auto_live_status(session_id: str) -> Dict:
    """自動ライブ配信のステータスを取得"""
    session = _sessions.get(session_id)
    if not session:
        return {"status": "not_running", "session_id": session_id}

    current_product = None
    if session.products:
        p = session.products[session.current_product_index % len(session.products)]
        current_product = {"itemId": p.item_id, "itemName": p.item_name}

    return {
        "status": "paused" if session.is_paused else "running",
        "session_id": session_id,
        "speak_count": session.speak_count,
        "comment_count": session.comment_count,
        "current_product": current_product,
        "product_count": len(session.products),
        "language": session.language,
        "style": session.style,
    }


def list_active_sessions() -> List[Dict]:
    """アクティブな自動ライブセッション一覧"""
    return [get_auto_live_status(sid) for sid in _sessions]
