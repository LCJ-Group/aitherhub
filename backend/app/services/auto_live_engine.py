"""
Auto Live Engine — AI自動ライブ配信エンジン v2

完全な直播フローを実現：
  開場挨拶 → 雑談/トレンド → 商品紹介 → 過渡トーク → 次の商品 → 雑談 → ...

商品データはShopeeから取得 OR 手動追加（画像+テキスト）。
商品がなくても雑談モードで配信可能。
コメントが来たら割り込みで応答する。

Architecture:
  [Livestream Flow Engine]
  OPENING → CHAT → PRODUCT_INTRO → PRODUCT_DEEP → TRANSITION → CHAT → PRODUCT_INTRO → ...
  
  [Comment Interrupt]
  コメント検出 → AI応答生成 → speakQueue push → フロー再開
"""
import os
import time
import asyncio
import logging
import random
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

# ============================================================
# Configuration
# ============================================================
OPENAI_MODEL = os.getenv("AUTO_LIVE_MODEL", "gpt-4.1-mini")
COMMENT_POLL_INTERVAL = 5  # seconds
SPEAK_QUEUE_CHECK_INTERVAL = 1  # seconds (reduced from 2 for less gap between speeches)


# ============================================================
# Livestream Flow Phases
# ============================================================
class FlowPhase(str, Enum):
    OPENING = "opening"           # 開場挨拶
    CHAT = "chat"                 # 雑談・トレンド・視聴者との会話
    PRODUCT_INTRO = "product_intro"  # 商品紹介（概要）
    PRODUCT_DEEP = "product_deep"    # 商品深掘り（詳細・使い方・比較）
    TRANSITION = "transition"     # 過渡トーク（次の商品への橋渡し）
    CLOSING = "closing"           # 締めの挨拶


# Flow templates: defines the sequence of phases in a livestream
# Each entry is (phase, segments_count) — how many speech segments to generate for this phase
FLOW_WITH_PRODUCTS = [
    (FlowPhase.OPENING, 1),
    (FlowPhase.CHAT, 2),
    (FlowPhase.PRODUCT_INTRO, 2),
    (FlowPhase.PRODUCT_DEEP, 3),
    (FlowPhase.CHAT, 1),
    (FlowPhase.TRANSITION, 1),
    # This block repeats for each product
]

FLOW_CHAT_ONLY = [
    (FlowPhase.OPENING, 2),
    (FlowPhase.CHAT, 3),
    # Repeats
]


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
    # Manual product fields
    image_url: str = ""  # Single image URL for manually added products
    custom_notes: str = ""  # Free-form notes from user

    def to_context(self) -> str:
        """AIプロンプト用のコンテキスト文字列を生成"""
        parts = [f"Product: {self.item_name}"]
        if self.brand:
            parts.append(f"Brand: {self.brand}")
        if self.price:
            parts.append(f"Price: {self.price}")
        if self.description:
            desc = self.description[:500]
            parts.append(f"Description: {desc}")
        if self.custom_notes:
            parts.append(f"Additional Notes: {self.custom_notes}")
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
        if self.image_url:
            parts.append(f"Product Image: {self.image_url}")
        return "\n".join(parts)


@dataclass
class AutoLiveSession:
    """自動ライブ配信セッションの状態管理"""
    session_id: str
    shopee_session_id: Optional[int] = None
    products: List[LiveProduct] = field(default_factory=list)
    current_product_index: int = 0
    is_running: bool = False
    is_paused: bool = False
    language: str = "en"
    style: str = "professional"
    speak_count: int = 0
    comment_count: int = 0
    last_comment_id: Optional[str] = None
    # Flow state
    current_phase: FlowPhase = FlowPhase.OPENING
    phase_segment_count: int = 0  # How many segments generated in current phase
    flow_index: int = 0  # Position in the flow template
    opening_done: bool = False
    # Conversation history for context continuity (FULL text, not truncated)
    conversation_history: List[str] = field(default_factory=list)
    # Exact texts generated (for dedup)
    generated_texts: List[str] = field(default_factory=list)
    # Custom host persona
    host_name: str = ""
    host_persona: str = ""
    # Chat topics for variety
    chat_topics_used: List[str] = field(default_factory=list)
    _task: Optional[asyncio.Task] = field(default=None, repr=False)
    _comment_task: Optional[asyncio.Task] = field(default=None, repr=False)


# Global sessions registry
_sessions: Dict[str, AutoLiveSession] = {}


# ============================================================
# Chat Topics Pool (for natural conversation variety)
# ============================================================
CHAT_TOPICS = {
    "en": [
        "Ask viewers where they're watching from and what time it is there",
        "Share a fun beauty tip or life hack",
        "Talk about current beauty trends on social media",
        "Ask viewers about their hair/skin care routine",
        "Share a personal story about discovering a great product",
        "Discuss seasonal beauty tips (weather-appropriate care)",
        "Talk about common beauty mistakes people make",
        "Ask viewers what products they want to see next",
        "Share behind-the-scenes of how products are made",
        "Discuss self-care and wellness tips",
    ],
    "ja": [
        "視聴者にどこから見ているか聞く",
        "美容の豆知識やライフハックを共有する",
        "SNSで話題の美容トレンドについて話す",
        "視聴者のヘアケア・スキンケアルーティンを聞く",
        "良い商品を見つけた時のエピソードを共有する",
        "季節に合った美容ケアのアドバイスをする",
        "よくある美容の間違いについて話す",
        "次に見たい商品を視聴者に聞く",
        "商品の製造裏話を共有する",
        "セルフケアとウェルネスのコツを話す",
    ],
    "zh": [
        "问观众从哪里看的直播，那边几点了",
        "分享一个有趣的美容小技巧",
        "聊聊社交媒体上的美容趋势",
        "问观众平时的护肤护发习惯",
        "分享发现好产品的小故事",
        "聊聊换季护肤护发的注意事项",
        "说说常见的美容误区",
        "问观众想看什么产品",
        "分享产品背后的故事",
        "聊聊自我护理和健康生活",
    ],
    "th": [
        "ถามผู้ชมว่าดูจากที่ไหน",
        "แชร์เคล็ดลับความงาม",
        "พูดคุยเรื่องเทรนด์ความงามบนโซเชียล",
        "ถามผู้ชมเรื่องรูทีนดูแลผิวและผม",
        "แชร์เรื่องราวการค้นพบผลิตภัณฑ์ดีๆ",
        "พูดคุยเรื่องการดูแลตัวเองตามฤดูกาล",
        "พูดถึงข้อผิดพลาดด้านความงามที่พบบ่อย",
        "ถามผู้ชมว่าอยากดูสินค้าอะไรต่อ",
        "แชร์เบื้องหลังการผลิตสินค้า",
        "พูดคุยเรื่องการดูแลตัวเอง",
    ],
}


# ============================================================
# AI Generation Functions
# ============================================================
async def _generate_speech(
    session: AutoLiveSession,
    phase: FlowPhase,
    product: Optional[LiveProduct] = None,
    extra_context: str = "",
    max_length: int = 200,
) -> str:
    """AIでフェーズに応じたスピーチを生成"""
    import openai
    client = openai.AsyncOpenAI()

    lang = session.language
    lang_instructions = {
        "en": "Speak in English.",
        "ja": "日本語で話してください。",
        "zh": "请用中文说话。",
        "th": "พูดภาษาไทย",
        "ms": "Bercakap dalam Bahasa Melayu.",
    }

    style_instructions = {
        "professional": "Speak like a professional beauty consultant. Be knowledgeable and trustworthy.",
        "casual": "Speak casually and friendly, like chatting with a friend. Use natural filler words.",
        "energetic": "Be energetic and exciting! Use enthusiasm and excitement.",
    }

    # Phase-specific instructions
    phase_instructions = {
        FlowPhase.OPENING: """This is the OPENING of the livestream.
- Greet viewers warmly and enthusiastically
- Welcome everyone to the stream
- Briefly mention what you'll be showing today
- Create excitement and anticipation
- Ask viewers to like and follow""",

        FlowPhase.CHAT: f"""This is a CASUAL CHAT segment between product presentations.
- Be natural and conversational, like talking to friends
- Topic hint: {extra_context}
- Engage with the audience, ask questions
- Share personal opinions or experiences
- Keep it light and fun
- This is NOT a product pitch — just genuine conversation
- You can mention upcoming products to build anticipation""",

        FlowPhase.PRODUCT_INTRO: """This is a PRODUCT INTRODUCTION segment.
- Introduce the product naturally, as if you just picked it up
- Mention the product name and what it does
- Share your first impression or personal experience
- Create curiosity — make viewers want to know more
- Keep it brief and exciting""",

        FlowPhase.PRODUCT_DEEP: """This is a PRODUCT DEEP DIVE segment.
- Go deeper into ONE specific aspect of the product
- Could be: ingredients, how to use, before/after results, comparison, price value
- Be specific and informative
- Include a call to action (add to cart, limited stock, etc.)
- Share tips for getting the best results""",

        FlowPhase.TRANSITION: """This is a TRANSITION between products.
- Smoothly wrap up the current product discussion
- Create a natural bridge to the next topic
- Maybe ask viewers if they have questions
- Build anticipation for what's coming next
- Keep it brief and natural""",

        FlowPhase.CLOSING: """This is the CLOSING of the livestream.
- Thank all viewers for watching
- Recap the best deals shown today
- Remind about limited offers
- Ask viewers to follow for next stream
- Say goodbye warmly""",
    }

    # Build conversation context — include ALL recent messages for dedup
    recent_history = ""
    if session.conversation_history:
        recent = session.conversation_history[-15:]  # Last 15 messages
        recent_history = "\n\n=== CONVERSATION HISTORY (you MUST NOT repeat or paraphrase ANY of these) ===\n" + "\n".join([f"{i+1}. {h}" for i, h in enumerate(recent)]) + "\n=== END HISTORY ==="

    # Product context
    product_context = ""
    if product:
        product_context = f"\nCurrent product:\n{product.to_context()}"

    # Host persona
    persona = ""
    if session.host_persona:
        persona = f"\nYour persona: {session.host_persona}"
    if session.host_name:
        persona += f"\nYour name: {session.host_name}"

    system_prompt = f"""You are an AI live commerce host on a live shopping stream.
{lang_instructions.get(lang, lang_instructions['en'])}
{style_instructions.get(session.style, style_instructions['professional'])}
{persona}

{phase_instructions.get(phase, '')}

CRITICAL RULES:
- Keep each speech segment between 50-{max_length} characters (for TTS, be concise but not too short)
- Be NATURAL and conversational, never robotic or scripted-sounding
- Vary your tone and energy based on the phase
- ABSOLUTELY DO NOT repeat or paraphrase anything from the conversation history below
- Each segment must be COMPLETELY DIFFERENT from all previous ones
- Do NOT start with the same greeting twice (e.g. if you already said "大家好", use a different opener)
- Output ONLY the speech text, no labels, no formatting, no quotes
- Do NOT use quotation marks around the text
{recent_history}
{product_context}

This is speech segment #{session.speak_count + 1} of the livestream. You MUST say something NEW and DIFFERENT."""

    try:
        response = await client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Generate the next {phase.value} speech segment."},
            ],
            max_tokens=300,
            temperature=0.75,
        )
        text = response.choices[0].message.content.strip()
        # Remove quotes if GPT wraps in them
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        logger.info(f"[AutoLive] [{phase.value}] Generated ({len(text)} chars): {text[:80]}...")
        return text
    except Exception as e:
        logger.error(f"[AutoLive] Failed to generate {phase.value} speech: {e}")
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

    system_prompt = f"""You are an AI live commerce host responding to a viewer's comment.
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
    """speakQueueにテキストをpush"""
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
# Livestream Flow Engine (v2)
# ============================================================
def _get_next_chat_topic(session: AutoLiveSession) -> str:
    """Get a random unused chat topic"""
    lang = session.language
    topics = CHAT_TOPICS.get(lang, CHAT_TOPICS["en"])
    unused = [t for t in topics if t not in session.chat_topics_used]
    if not unused:
        # Reset if all used
        session.chat_topics_used = []
        unused = topics
    topic = random.choice(unused)
    session.chat_topics_used.append(topic)
    return topic


async def _flow_speech_loop(session: AutoLiveSession):
    """直播フローに基づく自動スピーチループ v2"""
    logger.info(f"[AutoLive v2] Starting flow speech loop for session {session.session_id}")
    logger.info(f"[AutoLive v2] Products: {len(session.products)}, Language: {session.language}, Style: {session.style}")

    has_products = len(session.products) > 0

    # Define the flow based on whether we have products
    if has_products:
        # Full flow with products
        flow_template = list(FLOW_WITH_PRODUCTS)  # copy
    else:
        # Chat-only mode
        flow_template = list(FLOW_CHAT_ONLY)

    flow_index = 0
    phase_segments_done = 0

    while session.is_running:
        if session.is_paused:
            await asyncio.sleep(1)
            continue

        # Check queue capacity — allow up to 5 items buffered for seamless playback
        queue_len = await get_queue_length(session.session_id)
        if queue_len >= 5:
            await asyncio.sleep(0.5)  # Short sleep, check again quickly
            continue

        # Get current phase from flow template
        if flow_index >= len(flow_template):
            # Loop back (skip opening on repeat)
            if has_products:
                # After first cycle, skip opening, start from CHAT
                flow_template = [
                    (FlowPhase.CHAT, 1),
                    (FlowPhase.PRODUCT_INTRO, 2),
                    (FlowPhase.PRODUCT_DEEP, 3),
                    (FlowPhase.CHAT, 1),
                    (FlowPhase.TRANSITION, 1),
                ]
            else:
                flow_template = [(FlowPhase.CHAT, 3)]
            flow_index = 0
            phase_segments_done = 0

        current_phase, target_segments = flow_template[flow_index]
        session.current_phase = current_phase

        # Get current product for product-related phases
        current_product = None
        if has_products and current_phase in (FlowPhase.PRODUCT_INTRO, FlowPhase.PRODUCT_DEEP, FlowPhase.TRANSITION):
            current_product = session.products[session.current_product_index % len(session.products)]

        # Extra context for chat phases
        extra_context = ""
        if current_phase == FlowPhase.CHAT:
            extra_context = _get_next_chat_topic(session)

        # Generate speech
        text = await _generate_speech(
            session=session,
            phase=current_phase,
            product=current_product,
            extra_context=extra_context,
        )

        if text:
            success = await push_to_speak_queue(session.session_id, text)
            if success:
                session.speak_count += 1
                phase_segments_done += 1

                # Add to conversation history (FULL text for dedup)
                session.conversation_history.append(f"[{current_phase.value}] {text}")
                if len(session.conversation_history) > 20:
                    session.conversation_history = session.conversation_history[-20:]
                
                # Track exact texts for duplicate detection
                session.generated_texts.append(text)
                if len(session.generated_texts) > 50:
                    session.generated_texts = session.generated_texts[-50:]

                # Check if we should move to next phase
                if phase_segments_done >= target_segments:
                    flow_index += 1
                    phase_segments_done = 0

                    # Move to next product after TRANSITION
                    if current_phase == FlowPhase.TRANSITION and has_products:
                        session.current_product_index += 1
                        if session.current_product_index >= len(session.products):
                            session.current_product_index = 0
                        next_product = session.products[session.current_product_index % len(session.products)]
                        logger.info(f"[AutoLive v2] Moving to next product: {next_product.item_name}")

        # Only sleep briefly if queue has items, otherwise generate immediately
        if await get_queue_length(session.session_id) >= 3:
            await asyncio.sleep(0.5)  # Queue has buffer, short pause
        else:
            await asyncio.sleep(0.1)  # Queue is low, generate ASAP

    logger.info(f"[AutoLive v2] Flow speech loop ended for session {session.session_id}")


# ============================================================
# Comment Monitor Loop
# ============================================================
async def _comment_monitor_loop(session: AutoLiveSession):
    """コメント監視ループ"""
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
                        await push_to_speak_queue(session.session_id, response, priority="high")
                        session.comment_count += 1

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
    host_name: str = "",
    host_persona: str = "",
) -> Dict:
    """自動ライブ配信を開始（商品なしでもOK）"""
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
            image_url=p.get("image_url", ""),
            custom_notes=p.get("custom_notes", ""),
        ))

    session = AutoLiveSession(
        session_id=session_id,
        shopee_session_id=shopee_session_id,
        products=live_products,
        language=language,
        style=style,
        is_running=True,
        host_name=host_name,
        host_persona=host_persona,
    )

    # v2フローエンジンを開始
    session._task = asyncio.create_task(_flow_speech_loop(session))

    # コメント監視ループを開始（Shopeeセッションがある場合）
    if shopee_session_id:
        session._comment_task = asyncio.create_task(_comment_monitor_loop(session))

    _sessions[session_id] = session

    mode = "products" if live_products else "chat-only"
    logger.info(f"[AutoLive v2] Started auto live for session {session_id}: mode={mode}, products={len(live_products)}, lang={language}")

    return {
        "status": "started",
        "session_id": session_id,
        "product_count": len(live_products),
        "mode": mode,
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
    logger.info(f"[AutoLive v2] Stopped auto live for session {session_id}: {stats}")
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


async def add_product_to_session(session_id: str, product_data: Dict) -> Dict:
    """実行中のセッションに商品を追加"""
    session = _sessions.get(session_id)
    if not session:
        return {"status": "not_found"}

    product = LiveProduct(
        item_id=product_data.get("item_id", int(time.time())),
        item_name=product_data.get("item_name", "Unknown Product"),
        description=product_data.get("description", ""),
        price=str(product_data.get("price", "")),
        brand=product_data.get("brand", ""),
        image_url=product_data.get("image_url", ""),
        custom_notes=product_data.get("custom_notes", ""),
    )

    session.products.append(product)
    logger.info(f"[AutoLive v2] Added product to session {session_id}: {product.item_name}")

    return {
        "status": "product_added",
        "session_id": session_id,
        "product_name": product.item_name,
        "total_products": len(session.products),
    }


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
        "current_phase": session.current_phase.value,
        "mode": "products" if session.products else "chat-only",
    }


def list_active_sessions() -> List[Dict]:
    """アクティブな自動ライブセッション一覧"""
    return [get_auto_live_status(sid) for sid in _sessions]
