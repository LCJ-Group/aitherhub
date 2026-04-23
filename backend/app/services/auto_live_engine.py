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
    (FlowPhase.OPENING, 2),
    (FlowPhase.CHAT, 3),
    (FlowPhase.PRODUCT_INTRO, 3),
    (FlowPhase.PRODUCT_DEEP, 4),
    (FlowPhase.CHAT, 2),
    (FlowPhase.TRANSITION, 1),
    # This block repeats for each product
]

FLOW_CHAT_ONLY = [
    (FlowPhase.OPENING, 2),
    (FlowPhase.CHAT, 5),
    # Repeats
]

# v3: Flow presets for different livestream durations
FLOW_PRESETS = {
    "short": {  # ~30 min: quick intro, fewer chat segments
        "with_products": [
            (FlowPhase.OPENING, 1),
            (FlowPhase.PRODUCT_INTRO, 2),
            (FlowPhase.PRODUCT_DEEP, 2),
            (FlowPhase.TRANSITION, 1),
        ],
        "repeat": [
            (FlowPhase.PRODUCT_INTRO, 2),
            (FlowPhase.PRODUCT_DEEP, 2),
            (FlowPhase.TRANSITION, 1),
        ],
        "chat_only": [
            (FlowPhase.OPENING, 1),
            (FlowPhase.CHAT, 3),
        ],
    },
    "standard": {  # ~1 hour: balanced chat and products
        "with_products": FLOW_WITH_PRODUCTS,
        "repeat": [
            (FlowPhase.CHAT, 1),
            (FlowPhase.PRODUCT_INTRO, 3),
            (FlowPhase.PRODUCT_DEEP, 4),
            (FlowPhase.CHAT, 2),
            (FlowPhase.TRANSITION, 1),
        ],
        "chat_only": FLOW_CHAT_ONLY,
    },
    "long": {  # ~2 hours: more chat, deeper product dives, storytelling
        "with_products": [
            (FlowPhase.OPENING, 3),
            (FlowPhase.CHAT, 4),
            (FlowPhase.PRODUCT_INTRO, 3),
            (FlowPhase.PRODUCT_DEEP, 5),
            (FlowPhase.CHAT, 3),
            (FlowPhase.TRANSITION, 2),
        ],
        "repeat": [
            (FlowPhase.CHAT, 3),
            (FlowPhase.PRODUCT_INTRO, 3),
            (FlowPhase.PRODUCT_DEEP, 5),
            (FlowPhase.CHAT, 2),
            (FlowPhase.TRANSITION, 2),
        ],
        "chat_only": [
            (FlowPhase.OPENING, 3),
            (FlowPhase.CHAT, 6),
        ],
    },
}


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
    # Custom host persona (basic)
    host_name: str = ""
    host_persona: str = ""
    # v3: Enhanced persona
    catchphrases: List[str] = field(default_factory=list)
    speaking_style: str = ""
    expertise: str = ""
    brand_story: str = ""
    self_introduction: str = ""
    # v3: Flow customization
    flow_preset: str = "standard"
    custom_flow: Optional[List[Dict[str, Any]]] = None
    # Chat topics for variety
    chat_topics_used: List[str] = field(default_factory=list)
    # Track push count for queue length estimation
    _push_count: int = 0
    _consumed_count: int = 0  # Updated by frontend polling
    _last_push_time: float = 0.0  # Timestamp of last push for stuck detection
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
def _build_persona_prompt(session: 'AutoLiveSession') -> str:
    """v3: ライバーの個性を反映した詳細なペルソナプロンプトを構築"""
    parts = []

    # 基本情報
    if session.host_name:
        parts.append(f"Your name is {session.host_name}.")
    if session.host_persona:
        parts.append(f"Your character: {session.host_persona}")

    # 専門分野
    if session.expertise:
        parts.append(f"Your expertise and background: {session.expertise}. Use this knowledge naturally in your speech — reference your experience when discussing products or giving tips.")

    # 話し方の特徴
    if session.speaking_style:
        parts.append(f"Your speaking style: {session.speaking_style}. ALWAYS maintain this speaking style throughout the entire livestream. This is how you naturally talk.")

    # 口癖（最も重要）
    if session.catchphrases:
        phrases = ', '.join([f'"{p}"' for p in session.catchphrases])
        parts.append(
            f"Your catchphrases / favorite expressions: {phrases}. "
            f"You MUST naturally weave these into your speech. Use at least one catchphrase per speech segment. "
            f"Don't force them — use them where they fit naturally, like a real person who has these verbal habits."
        )

    # ブランドストーリー
    if session.brand_story:
        parts.append(
            f"Brand story you can reference: {session.brand_story}. "
            f"Weave this into conversations naturally — especially during product introductions and chat segments. "
            f"Don't repeat the full story every time, but reference parts of it to build brand connection."
        )

    if not parts:
        return ""

    return "\n\n=== YOUR PERSONA (stay in character at ALL times) ===\n" + "\n".join(parts) + "\n=== END PERSONA ==="


def _get_flow_template(session: 'AutoLiveSession', has_products: bool) -> list:
    """v3: フロープリセットまたはカスタムフローからテンプレートを取得"""
    # カスタムフローが指定されている場合はそれを使用
    if session.custom_flow:
        try:
            return [
                (FlowPhase(item["phase"]), item.get("segments", 2))
                for item in session.custom_flow
            ]
        except (KeyError, ValueError) as e:
            logger.warning(f"[AutoLive v3] Invalid custom_flow, falling back to preset: {e}")

    # プリセットから取得
    preset = FLOW_PRESETS.get(session.flow_preset, FLOW_PRESETS["standard"])
    if has_products:
        return list(preset["with_products"])
    else:
        return list(preset["chat_only"])


def _get_repeat_template(session: 'AutoLiveSession', has_products: bool) -> list:
    """v3: リピート用フローテンプレートを取得"""
    if session.custom_flow:
        try:
            return [
                (FlowPhase(item["phase"]), item.get("segments", 2))
                for item in session.custom_flow
                if item["phase"] != "opening"  # リピート時はopeningをスキップ
            ]
        except (KeyError, ValueError):
            pass

    preset = FLOW_PRESETS.get(session.flow_preset, FLOW_PRESETS["standard"])
    if has_products:
        return list(preset.get("repeat", preset["with_products"]))
    else:
        return list(preset["chat_only"])


async def _generate_speech(
    session: AutoLiveSession,
    phase: FlowPhase,
    product: Optional[LiveProduct] = None,
    extra_context: str = "",
    max_length: int = 400,
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
- Talk about what exciting things you'll be showing today
- Share your excitement and set the mood
- Mention that you have great products and stories to share
- Do NOT ask questions — just welcome and set the scene""",

        FlowPhase.CHAT: f"""This is a CASUAL CHAT segment between product presentations.
- Topic hint: {extra_context}
- Talk at length about this topic — share your own experience, opinions, and stories
- Be natural and conversational, like talking to friends
- Share personal anecdotes, tips, or interesting facts
- This is NOT a product pitch — just genuine, engaging conversation
- Do NOT ask viewers questions — instead, TELL them interesting things
- You can mention upcoming products to build anticipation
- Keep talking — fill the air with interesting content""",

        FlowPhase.PRODUCT_INTRO: """This is a PRODUCT INTRODUCTION segment.
- Introduce the product with enthusiasm, as if you just picked it up
- Explain what the product is, what it does, and who it's for
- Share your first impression and personal experience using it
- Describe the packaging, texture, scent, or feel in detail
- Tell a story about how you discovered this product or why you love it
- Do NOT ask questions — just present and describe passionately""",

        FlowPhase.PRODUCT_DEEP: """This is a PRODUCT DEEP DIVE segment.
- Go deep into ONE specific aspect: ingredients, how to use, results, or value
- Be specific and informative — share expert knowledge
- Compare with similar products or explain what makes this one special
- Share tips for getting the best results with this product
- Mention the price and why it's a great deal
- Include a soft call to action (this is selling well, limited stock, etc.)
- Do NOT ask questions — just educate and convince""",

        FlowPhase.TRANSITION: """This is a TRANSITION between products.
- Smoothly wrap up the current product with a final positive comment
- Create a natural bridge to the next topic
- Build anticipation for what's coming next
- Share a quick related tip or fun fact
- Do NOT ask questions — just smoothly move on""",

        FlowPhase.CLOSING: """This is the CLOSING of the livestream.
- Thank all viewers for watching and being part of the stream
- Recap the highlights and best deals shown today
- Remind about limited offers and where to buy
- Mention when the next stream will be
- Say goodbye warmly and leave a lasting impression""",
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

    # ── v3: Enhanced persona prompt ──
    persona_block = _build_persona_prompt(session)

    # Self-introduction override for OPENING phase
    opening_override = ""
    if phase == FlowPhase.OPENING and session.self_introduction and session.speak_count == 0:
        opening_override = f"""\n\nIMPORTANT: For this opening, use the following self-introduction as a base and expand on it naturally:
\"{session.self_introduction}\"
"""

    system_prompt = f"""You are an AI live commerce host on a live shopping stream.
{lang_instructions.get(lang, lang_instructions['en'])}
{style_instructions.get(session.style, style_instructions['professional'])}
{persona_block}

{phase_instructions.get(phase, '')}
{opening_override}

CRITICAL RULES:
- Generate a speech segment of {max_length}-{max_length + 150} characters (this will be read by TTS, keep it concise)
- Be informative and engaging — explain, describe, share tips
- Do NOT end with a question unless the phase specifically calls for it
- Do NOT ask viewers to comment, reply, or answer — just TALK and PRESENT
- Be NATURAL and conversational, like a real livestream host
- ABSOLUTELY DO NOT repeat or paraphrase anything from the conversation history below
- Each segment must be COMPLETELY DIFFERENT from all previous ones
- Do NOT start with the same greeting twice
- Output ONLY the speech text, no labels, no formatting, no quotes
- Do NOT use quotation marks around the text
- AUDIO STABILITY: Use moderate punctuation. Avoid excessive exclamation marks (!!!), ALL CAPS, or dramatic emphasis. Keep energy through word choice, not punctuation. Use at most one exclamation mark per sentence.
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
            max_tokens=400,
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
        # Track push count in session for accurate queue length
        session = _sessions.get(session_id)
        if session:
            session._push_count += 1
            session._last_push_time = time.time()
        logger.info(f"[AutoLive] Pushed to speak queue (priority={priority}): {text[:50]}...")
        return True
    except Exception as e:
        logger.error(f"[AutoLive] Failed to push to speak queue: {e}")
        return False


def mark_consumed(session_id: str, count: int = 1):
    """フロントエンドがqueueからアイテムを消費した時に呼ばれる"""
    session = _sessions.get(session_id)
    if session:
        session._consumed_count += count
        pending = session._push_count - session._consumed_count
        logger.info(f"[AutoLive] Marked {count} consumed. push={session._push_count}, consumed={session._consumed_count}, pending={pending}")
    else:
        logger.warning(f"[AutoLive] mark_consumed: session '{session_id}' NOT FOUND. Active sessions: {list(_sessions.keys())}")



async def get_queue_length(session_id: str) -> int:
    """未消費アイテム数を取得 — push_count - consumed_count ベース
    
    liveavatar_serviceの_speak_queueは「OBS用のキュー」であり、
    フロントエンドがpollしてもアイテムは削除されない（5分後に自動クリーンアップ）。
    そのため、len(_speak_queue)は常に増え続け、正確な「未消費数」にならない。
    
    代わりに、push_count - consumed_count を使い、
    mark_consumed APIの失敗に備えて安全策を追加する：
    - pending が負の値にならないようにクランプ
    - 長時間stuck防止のため、最後のpushから60秒経過したらpendingを0にリセット
    """
    session = _sessions.get(session_id)
    if not session:
        return 0
    
    pending = session._push_count - session._consumed_count
    logger.debug(f"[AutoLive] get_queue_length: push={session._push_count}, consumed={session._consumed_count}, pending={pending}")
    
    # Safety: clamp to non-negative
    if pending < 0:
        pending = 0
        session._consumed_count = session._push_count
    
    # Safety: if pending seems stuck (>= buffer limit for more than 45s),
    # auto-reset to allow generation to continue
    if pending >= 6 and hasattr(session, '_last_push_time'):
        elapsed = time.time() - session._last_push_time
        if elapsed > 45:
            logger.warning(f"[AutoLive] Queue seems stuck (pending={pending}, last push {elapsed:.0f}s ago). Resetting.")
            session._consumed_count = session._push_count
            pending = 0
    
    return pending


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


def _normalize_speech_text(text: str) -> str:
    """音声安定化のためテキストを正規化する
    
    - 連続する感嘆符/疑問符を1つに（音量の急激な変化を防ぐ）
    - 過度な句読点を整理
    - 空白の正規化
    """
    import re
    # 連続する感嘆符を1つに
    text = re.sub(r'！{2,}', '！', text)
    text = re.sub(r'!{2,}', '!', text)
    # 連続する疑問符を1つに
    text = re.sub(r'？{2,}', '？', text)
    text = re.sub(r'\?{2,}', '?', text)
    # 感嘆符+疑問符の組み合わせを整理
    text = re.sub(r'[！!][？?]', '！', text)
    text = re.sub(r'[？?][！!]', '？', text)
    # 連続する「。」を1つに
    text = re.sub(r'。{2,}', '。', text)
    # 連続する「…」を1つに
    text = re.sub(r'…{2,}', '…', text)
    text = re.sub(r'\.{3,}', '…', text)
    # 空白の正規化
    text = re.sub(r'\s+', ' ', text).strip()
    return text


async def _flow_speech_loop(session: AutoLiveSession):
    """直播フローに基づく自動スピーチループ v3
    
    v3 improvements:
    - Dynamic product detection: switches from chat-only to product flow
      when products are added mid-session
    - Shorter text segments (200-350 chars) for TTS stability
    - Text normalization for consistent audio volume
    - Larger queue buffer (6) for seamless transitions
    """
    logger.info(f"[AutoLive v3] Starting flow speech loop for session {session.session_id}")
    logger.info(f"[AutoLive v3] Products: {len(session.products)}, Language: {session.language}, Style: {session.style}")

    # Track whether we had products at last check (for dynamic switching)
    had_products = len(session.products) > 0

    # v3: Use preset/custom flow templates
    flow_template = _get_flow_template(session, had_products)
    logger.info(f"[AutoLive v3] Flow preset={session.flow_preset}, template phases={[p.value for p, _ in flow_template]}")

    flow_index = 0
    phase_segments_done = 0
    opening_done = False

    while session.is_running:
        if session.is_paused:
            await asyncio.sleep(1)
            continue

        # ── Dynamic product detection ──
        # Check if products were added since last iteration
        has_products_now = len(session.products) > 0
        if has_products_now and not had_products:
            # Products were just added! Switch from chat-only to product flow
            logger.info(f"[AutoLive v3] Products detected mid-session! Switching to product flow. Products: {[p.item_name for p in session.products]}")
            had_products = True
            # Insert a transition to product intro at current position
            flow_template = [
                (FlowPhase.TRANSITION, 1),
                (FlowPhase.PRODUCT_INTRO, 2),
                (FlowPhase.PRODUCT_DEEP, 3),
                (FlowPhase.CHAT, 1),
                (FlowPhase.TRANSITION, 1),
            ]
            flow_index = 0
            phase_segments_done = 0

        # ── Queue capacity check ──
        # Keep buffer at 6 items to prevent gaps between speeches
        # Higher buffer compensates for AI generation latency (~1-3s per segment)
        queue_len = await get_queue_length(session.session_id)
        if queue_len >= 6:
            await asyncio.sleep(0.3)  # Short wait, check again quickly
            continue

        # ── Flow template cycling (v3: use repeat template) ──
        if flow_index >= len(flow_template):
            flow_template = _get_repeat_template(session, has_products_now)
            flow_index = 0
            phase_segments_done = 0

        current_phase, target_segments = flow_template[flow_index]
        session.current_phase = current_phase

        # ── Current product for product-related phases ──
        current_product = None
        if has_products_now and current_phase in (FlowPhase.PRODUCT_INTRO, FlowPhase.PRODUCT_DEEP, FlowPhase.TRANSITION):
            current_product = session.products[session.current_product_index % len(session.products)]
            logger.info(f"[AutoLive v3] Phase={current_phase.value}, Product={current_product.item_name}")

        # ── Extra context for chat phases ──
        extra_context = ""
        if current_phase == FlowPhase.CHAT:
            extra_context = _get_next_chat_topic(session)
            # If we have products, mention them in chat context too
            if has_products_now:
                product_names = [p.item_name for p in session.products]
                extra_context += f"\nProducts available: {', '.join(product_names)}"

        # ── Generate speech (shorter segments for TTS stability) ──
        text = await _generate_speech(
            session=session,
            phase=current_phase,
            product=current_product,
            extra_context=extra_context,
            max_length=200,  # Reduced from 400 for TTS stability
        )

        if text:
            # Normalize text for audio stability
            text = _normalize_speech_text(text)
            
            success = await push_to_speak_queue(session.session_id, text)
            if success:
                session.speak_count += 1
                phase_segments_done += 1

                # Add to conversation history
                session.conversation_history.append(f"[{current_phase.value}] {text}")
                if len(session.conversation_history) > 20:
                    session.conversation_history = session.conversation_history[-20:]
                
                session.generated_texts.append(text)
                if len(session.generated_texts) > 50:
                    session.generated_texts = session.generated_texts[-50:]

                # Check if we should move to next phase
                if phase_segments_done >= target_segments:
                    flow_index += 1
                    phase_segments_done = 0

                    # Move to next product after TRANSITION
                    if current_phase == FlowPhase.TRANSITION and has_products_now:
                        session.current_product_index += 1
                        if session.current_product_index >= len(session.products):
                            session.current_product_index = 0
                        next_product = session.products[session.current_product_index % len(session.products)]
                        logger.info(f"[AutoLive v3] Moving to next product: {next_product.item_name}")

        # Minimal pause between generations to keep buffer full
        await asyncio.sleep(0.1)

    logger.info(f"[AutoLive v3] Flow speech loop ended for session {session.session_id}")


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
    # v3: Enhanced persona
    catchphrases: Optional[List[str]] = None,
    speaking_style: str = "",
    expertise: str = "",
    brand_story: str = "",
    self_introduction: str = "",
    # v3: Flow customization
    flow_preset: str = "standard",
    custom_flow: Optional[List[Dict[str, Any]]] = None,
) -> Dict:
    """自動ライブ配信を開始（商品なしでもOK）v3: ペルソナ・フロー設定対応"""
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
        # v3: Enhanced persona
        catchphrases=catchphrases or [],
        speaking_style=speaking_style,
        expertise=expertise,
        brand_story=brand_story,
        self_introduction=self_introduction,
        # v3: Flow customization
        flow_preset=flow_preset,
        custom_flow=custom_flow,
    )

    # v2フローエンジンを開始
    session._task = asyncio.create_task(_flow_speech_loop(session))

    # コメント監視ループを開始（Shopeeセッションがある場合）
    if shopee_session_id:
        session._comment_task = asyncio.create_task(_comment_monitor_loop(session))

    _sessions[session_id] = session

    mode = "products" if live_products else "chat-only"
    persona_info = f"name={host_name}, catchphrases={len(catchphrases or [])}, style={speaking_style[:30]}" if (host_name or catchphrases) else "none"
    logger.info(f"[AutoLive v3] Started: session={session_id}, mode={mode}, products={len(live_products)}, lang={language}, flow={flow_preset}, persona=[{persona_info}]")

    return {
        "status": "started",
        "session_id": session_id,
        "product_count": len(live_products),
        "mode": mode,
        "language": language,
        "style": style,
        "shopee_session_id": shopee_session_id,
        "flow_preset": flow_preset,
        "persona": {
            "host_name": host_name,
            "catchphrases_count": len(catchphrases or []),
            "has_speaking_style": bool(speaking_style),
            "has_expertise": bool(expertise),
            "has_brand_story": bool(brand_story),
        },
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
        "comments_responded": session.comment_count,
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


async def update_session_id(old_session_id: str, new_session_id: str) -> Dict:
    """
    セッションIDを更新（自動再接続時に使用）
    
    古いセッションの商品・設定・フロー状態を新しいセッションIDに引き継ぐ。
    Auto Liveのスピーチ生成ループは新しいセッションIDでキューにpushし続ける。
    """
    session = _sessions.get(old_session_id)
    if not session:
        logger.warning(f"[AutoLive] update_session_id: old session {old_session_id} not found")
        return {"success": False, "error": "Old session not found"}

    logger.info(f"[AutoLive] 🔄 Updating session ID: {old_session_id} → {new_session_id}")

    # Update session ID
    session.session_id = new_session_id

    # Move session in registry
    _sessions[new_session_id] = session
    del _sessions[old_session_id]

    # Update speak queue to use new session ID
    from app.services.liveavatar_service import rename_speak_queue_session
    try:
        rename_speak_queue_session(old_session_id, new_session_id)
    except Exception as e:
        logger.warning(f"[AutoLive] Failed to rename speak queue: {e}")

    # Reset consumed/push counts for clean queue tracking
    session._consumed_count = 0
    session._push_count = 0

    logger.info(
        f"[AutoLive] ✅ Session updated: {new_session_id}, "
        f"products={len(session.products)}, phase={session.current_phase}, "
        f"running={session.is_running}"
    )

    return {
        "success": True,
        "old_session_id": old_session_id,
        "new_session_id": new_session_id,
        "products_count": len(session.products),
        "phase": session.current_phase,
        "is_running": session.is_running,
    }
