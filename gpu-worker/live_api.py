"""
Live Streaming Control API
============================
FastAPI server that provides REST API endpoints to control the
Hybrid AI Live Streaming Engine.

Endpoints:
    POST /api/v1/live/prepare      - Load models and prepare avatar
    POST /api/v1/live/start        - Start RTMP live stream
    POST /api/v1/live/speak        - Generate lip-sync and speak
    POST /api/v1/live/respond      - Respond to viewer comment (GPT + TTS + lip-sync)
    POST /api/v1/live/stop         - Stop live stream
    POST /api/v1/live/test-video   - Generate test MP4 (no RTMP needed)
    GET  /api/v1/live/status       - Get engine status
    GET  /api/v1/live/health       - Health check

    # Autonomous mode
    POST /api/v1/live/autopilot/start   - Start autonomous conversation loop
    POST /api/v1/live/autopilot/stop    - Stop autonomous conversation loop
    GET  /api/v1/live/autopilot/status  - Get autopilot status

    # Product catalog
    POST /api/v1/live/products          - Set product catalog
    GET  /api/v1/live/products          - Get product catalog

    # TikTok comments
    POST /api/v1/live/tiktok/connect    - Connect to TikTok Live for comments
    POST /api/v1/live/tiktok/disconnect - Disconnect from TikTok Live
    GET  /api/v1/live/tiktok/status     - Get TikTok connection status
"""

import os
import sys
import time
import uuid
import json
import asyncio
import logging
import tempfile
import threading
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager
from collections import deque

import httpx
from fastapi import FastAPI, HTTPException, Header, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

# Add paths
MUSETALK_DIR = "/workspace/MuseTalk"
if MUSETALK_DIR not in sys.path:
    sys.path.insert(0, MUSETALK_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from live_engine import LiveStreamEngine, EngineConfig, EngineState

logger = logging.getLogger("live_api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

# ─── Configuration ───────────────────────────────────────────────────────────

API_KEY = os.environ.get("LIVE_API_KEY", "change-me-in-production")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
TTS_PROVIDER = os.environ.get("TTS_PROVIDER", "openai")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "")

# ─── Global Engine Instance ──────────────────────────────────────────────────

engine: Optional[LiveStreamEngine] = None

# ─── Autopilot State ─────────────────────────────────────────────────────────

autopilot_running = False
autopilot_thread: Optional[threading.Thread] = None
autopilot_stop_event = threading.Event()

# Product catalog
product_catalog: List[Dict[str, Any]] = []
current_product_index = 0

# TikTok comment queue
tiktok_comments: deque = deque(maxlen=100)
tiktok_connected = False
tiktok_thread: Optional[threading.Thread] = None
tiktok_stop_event = threading.Event()
tiktok_username = ""

# Conversation history for context
conversation_history: List[Dict[str, str]] = []
MAX_HISTORY = 20


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    global engine
    config = EngineConfig()
    engine = LiveStreamEngine(config)
    logger.info("Live Streaming Engine initialized.")
    yield
    # Cleanup
    global autopilot_running, tiktok_connected
    autopilot_running = False
    autopilot_stop_event.set()
    tiktok_connected = False
    tiktok_stop_event.set()
    if engine and engine.state in (EngineState.STREAMING, EngineState.SPEAKING):
        engine.stop_stream()
    logger.info("Live Streaming Engine shut down.")


app = FastAPI(
    title="AI Live Streaming Engine",
    description="Server-side hybrid AI live streaming with real-time lip-sync, autonomous conversation, and TikTok integration",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Auth ────────────────────────────────────────────────────────────────────

def verify_api_key(x_api_key: str = Header(None)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ─── Request/Response Models ─────────────────────────────────────────────────

class PrepareRequest(BaseModel):
    video_path: str = Field(..., description="Path to base portrait video on GPU Worker")
    fps: int = Field(25, description="Output frame rate")
    version: str = Field("v15", description="MuseTalk version (v1 or v15)")
    bbox_shift: int = Field(0, description="Bounding box shift value")
    extra_margin: int = Field(10, description="Extra margin for face cropping")
    parsing_mode: str = Field("jaw", description="Face parsing mode: raw, jaw, jaw_safe, neck")


class StartRequest(BaseModel):
    rtmp_url: str = Field(..., description="RTMP destination URL")
    video_bitrate: str = Field("4000k", description="Video bitrate")
    preset: str = Field("ultrafast", description="FFmpeg encoding preset")


class SpeakRequest(BaseModel):
    text: Optional[str] = Field(None, description="Text to speak (TTS)")
    audio_url: Optional[str] = Field(None, description="URL to pre-generated audio")
    audio_path: Optional[str] = Field(None, description="Local path to audio file")
    voice_id: Optional[str] = Field(None, description="TTS voice ID")
    language: str = Field("ja", description="Language for TTS")
    openai_api_key: Optional[str] = Field(None, description="OpenAI API key (overrides env)")
    elevenlabs_api_key: Optional[str] = Field(None, description="ElevenLabs API key (overrides env)")
    tts_provider: Optional[str] = Field(None, description="TTS provider: openai or elevenlabs")


class RespondRequest(BaseModel):
    comment: str = Field(..., description="Viewer comment to respond to")
    persona: str = Field(
        "あなたはプロのライブコマース販売員です。親しみやすく、商品の魅力を伝えるのが得意です。",
        description="AI persona/system prompt"
    )
    product_info: Optional[str] = Field(None, description="Current product information")
    voice_id: Optional[str] = Field(None, description="TTS voice ID")
    language: str = Field("ja", description="Language for response")
    openai_api_key: Optional[str] = Field(None, description="OpenAI API key (overrides env)")
    elevenlabs_api_key: Optional[str] = Field(None, description="ElevenLabs API key (overrides env)")
    tts_provider: Optional[str] = Field(None, description="TTS provider: openai or elevenlabs")


class TestVideoRequest(BaseModel):
    audio_path: Optional[str] = Field(None, description="Local path to audio file")
    audio_url: Optional[str] = Field(None, description="URL to audio file")
    text: Optional[str] = Field(None, description="Text to speak (TTS)")
    openai_api_key: Optional[str] = Field(None, description="OpenAI API key (overrides env)")
    elevenlabs_api_key: Optional[str] = Field(None, description="ElevenLabs API key (overrides env)")
    tts_provider: Optional[str] = Field(None, description="TTS provider: openai or elevenlabs")


class ProductItem(BaseModel):
    name: str = Field(..., description="Product name")
    price: str = Field("", description="Product price")
    description: str = Field("", description="Product description")
    features: List[str] = Field(default_factory=list, description="Key features")
    promotion: str = Field("", description="Current promotion/discount")


class ProductCatalogRequest(BaseModel):
    products: List[ProductItem] = Field(..., description="List of products to introduce")


class AutopilotStartRequest(BaseModel):
    persona: str = Field(
        "あなたはプロのライブコマース販売員「あいちゃん」です。明るく元気で、視聴者に親しみやすい話し方をします。商品の魅力を分かりやすく伝え、視聴者の質問にも丁寧に答えます。",
        description="AI persona/system prompt"
    )
    product_intro_interval: int = Field(30, description="Seconds between product introductions when no comments")
    comment_priority: bool = Field(True, description="Prioritize responding to comments over product intros")
    voice_id: Optional[str] = Field(None, description="TTS voice ID")
    language: str = Field("ja", description="Language")
    openai_api_key: Optional[str] = Field(None, description="OpenAI API key")
    elevenlabs_api_key: Optional[str] = Field(None, description="ElevenLabs API key")
    tts_provider: Optional[str] = Field(None, description="TTS provider")
    idle_phrases: List[str] = Field(
        default_factory=lambda: [
            "皆さん、こんにちは！今日も素敵な商品をご紹介しますよ！",
            "コメントお待ちしてます！気になることがあったら何でも聞いてくださいね！",
            "いらっしゃいませ！今日のおすすめ商品、ぜひチェックしてくださいね！",
            "ご覧いただきありがとうございます！何かご質問はありますか？",
        ],
        description="Phrases to say when idle (no comments, between product intros)"
    )


class TikTokConnectRequest(BaseModel):
    username: str = Field(..., description="TikTok username to connect to (without @)")


# ─── TTS Functions ───────────────────────────────────────────────────────────

async def text_to_speech_openai(text: str, output_path: str, voice: str = "alloy", api_key: str = None):
    """Generate speech using OpenAI TTS API."""
    key = api_key or OPENAI_API_KEY
    if not key:
        raise ValueError("OpenAI API key not configured")
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{OPENAI_API_BASE}/audio/speech",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "tts-1",
                "input": text,
                "voice": voice,
                "response_format": "wav"
            },
            timeout=30.0
        )
        response.raise_for_status()
        with open(output_path, "wb") as f:
            f.write(response.content)
    return output_path


async def text_to_speech_elevenlabs(text: str, output_path: str, voice_id: str = None, api_key: str = None):
    """Generate speech using ElevenLabs TTS API."""
    vid = voice_id or ELEVENLABS_VOICE_ID
    key = api_key or ELEVENLABS_API_KEY
    if not key:
        raise ValueError("ElevenLabs API key not configured")
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{vid}",
            headers={
                "xi-api-key": key,
                "Content-Type": "application/json"
            },
            json={
                "text": text,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75
                }
            },
            timeout=30.0
        )
        response.raise_for_status()
        with open(output_path, "wb") as f:
            f.write(response.content)
    return output_path


async def generate_tts(text: str, voice_id: str = None, openai_key: str = None, elevenlabs_key: str = None, provider: str = None) -> str:
    """Generate TTS audio and return the file path."""
    output_path = os.path.join(tempfile.gettempdir(), f"tts_{uuid.uuid4().hex[:8]}.wav")
    use_provider = provider or TTS_PROVIDER
    el_key = elevenlabs_key or ELEVENLABS_API_KEY
    if use_provider == "elevenlabs" and el_key:
        await text_to_speech_elevenlabs(text, output_path, voice_id, el_key)
    else:
        await text_to_speech_openai(text, output_path, api_key=openai_key)
    return output_path


def generate_tts_sync(text: str, voice_id: str = None, openai_key: str = None, elevenlabs_key: str = None, provider: str = None) -> str:
    """Synchronous wrapper for TTS generation (for use in threads)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(generate_tts(text, voice_id, openai_key, elevenlabs_key, provider))
    finally:
        loop.close()


# ─── GPT Functions ───────────────────────────────────────────────────────────

async def generate_response(comment: str, persona: str, product_info: str = None, api_key: str = None) -> str:
    """Generate a conversational response using GPT."""
    key = api_key or OPENAI_API_KEY
    if not key:
        raise ValueError("OpenAI API key not configured")
    messages = [{"role": "system", "content": persona}]
    if product_info:
        messages.append({
            "role": "system",
            "content": f"現在紹介中の商品情報:\n{product_info}"
        })

    # Add conversation history for context
    for h in conversation_history[-10:]:
        messages.append(h)

    messages.append({
        "role": "user",
        "content": f"視聴者のコメント: 「{comment}」\n\n自然に、親しみやすく返答してください。短く簡潔に（50文字以内）。"
    })

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{OPENAI_API_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "gpt-4o-mini",
                "messages": messages,
                "max_tokens": 100,
                "temperature": 0.8
            },
            timeout=15.0
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]


def generate_response_sync(comment: str, persona: str, product_info: str = None, api_key: str = None) -> str:
    """Synchronous wrapper for GPT response (for use in threads)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(generate_response(comment, persona, product_info, api_key))
    finally:
        loop.close()


async def generate_product_intro(product: Dict[str, Any], persona: str, api_key: str = None) -> str:
    """Generate a product introduction script using GPT."""
    key = api_key or OPENAI_API_KEY
    if not key:
        raise ValueError("OpenAI API key not configured")

    product_text = f"商品名: {product.get('name', '')}\n"
    if product.get('price'):
        product_text += f"価格: {product['price']}\n"
    if product.get('description'):
        product_text += f"説明: {product['description']}\n"
    if product.get('features'):
        product_text += f"特徴: {', '.join(product['features'])}\n"
    if product.get('promotion'):
        product_text += f"プロモーション: {product['promotion']}\n"

    messages = [
        {"role": "system", "content": persona},
        {"role": "user", "content": (
            f"以下の商品をライブコマースで紹介してください。\n\n{product_text}\n\n"
            "自然で親しみやすい口調で、視聴者の購買意欲を高めるように紹介してください。"
            "100文字以内で簡潔に。"
        )}
    ]

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{OPENAI_API_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "gpt-4o-mini",
                "messages": messages,
                "max_tokens": 200,
                "temperature": 0.8
            },
            timeout=15.0
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]


def generate_product_intro_sync(product: Dict[str, Any], persona: str, api_key: str = None) -> str:
    """Synchronous wrapper for product intro generation."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(generate_product_intro(product, persona, api_key))
    finally:
        loop.close()


# ─── Helper: Download audio ─────────────────────────────────────────────────

async def download_audio(url: str) -> str:
    """Download audio from URL to a temp file."""
    output_path = os.path.join(tempfile.gettempdir(), f"audio_{uuid.uuid4().hex[:8]}.wav")
    async with httpx.AsyncClient() as client:
        response = await client.get(url, timeout=30.0, follow_redirects=True)
        response.raise_for_status()
        with open(output_path, "wb") as f:
            f.write(response.content)
    return output_path


# ─── Autopilot Loop ─────────────────────────────────────────────────────────

def autopilot_loop(config: Dict[str, Any]):
    """
    Autonomous conversation loop that runs in a background thread.
    Cycles through: comment response → product introduction → idle phrases.
    """
    global autopilot_running, current_product_index, conversation_history

    persona = config.get("persona", "")
    product_intro_interval = config.get("product_intro_interval", 30)
    comment_priority = config.get("comment_priority", True)
    voice_id = config.get("voice_id")
    openai_key = config.get("openai_api_key")
    elevenlabs_key = config.get("elevenlabs_api_key")
    tts_provider = config.get("tts_provider")
    idle_phrases = config.get("idle_phrases", [])

    last_product_intro_time = time.time()
    idle_phrase_idx = 0

    logger.info("Autopilot loop started.")

    while not autopilot_stop_event.is_set() and autopilot_running:
        try:
            # Wait for engine to be ready (not currently speaking)
            if engine and engine.state == EngineState.SPEAKING:
                time.sleep(0.5)
                continue

            if engine and engine.state not in (EngineState.STREAMING, EngineState.SPEAKING):
                time.sleep(1.0)
                continue

            # Priority 1: Respond to TikTok comments
            if comment_priority and tiktok_comments:
                comment_data = tiktok_comments.popleft()
                comment_text = comment_data.get("text", "")
                comment_user = comment_data.get("user", "unknown")

                logger.info(f"Autopilot responding to comment from @{comment_user}: {comment_text}")

                # Get current product info
                product_info = None
                if product_catalog and current_product_index < len(product_catalog):
                    p = product_catalog[current_product_index]
                    product_info = f"{p.get('name', '')}: {p.get('description', '')}"

                try:
                    response_text = generate_response_sync(
                        comment_text, persona, product_info, openai_key
                    )
                    logger.info(f"GPT response: {response_text}")

                    # Add to conversation history
                    conversation_history.append({"role": "user", "content": f"@{comment_user}: {comment_text}"})
                    conversation_history.append({"role": "assistant", "content": response_text})
                    if len(conversation_history) > MAX_HISTORY * 2:
                        conversation_history = conversation_history[-MAX_HISTORY * 2:]

                    # TTS
                    audio_path = generate_tts_sync(
                        response_text, voice_id, openai_key, elevenlabs_key, tts_provider
                    )

                    # Speak
                    engine.speak(audio_path)

                    # Wait for speaking to finish
                    while engine.state == EngineState.SPEAKING and not autopilot_stop_event.is_set():
                        time.sleep(0.3)

                    time.sleep(1.0)  # Brief pause between responses
                    continue

                except Exception as e:
                    logger.error(f"Autopilot comment response failed: {e}")
                    time.sleep(2.0)
                    continue

            # Priority 2: Product introduction (on timer)
            elapsed_since_intro = time.time() - last_product_intro_time
            if product_catalog and elapsed_since_intro >= product_intro_interval:
                product = product_catalog[current_product_index % len(product_catalog)]
                logger.info(f"Autopilot introducing product: {product.get('name', 'unknown')}")

                try:
                    intro_text = generate_product_intro_sync(product, persona, openai_key)
                    logger.info(f"Product intro: {intro_text}")

                    conversation_history.append({"role": "assistant", "content": intro_text})

                    audio_path = generate_tts_sync(
                        intro_text, voice_id, openai_key, elevenlabs_key, tts_provider
                    )
                    engine.speak(audio_path)

                    # Wait for speaking to finish
                    while engine.state == EngineState.SPEAKING and not autopilot_stop_event.is_set():
                        time.sleep(0.3)

                    current_product_index = (current_product_index + 1) % len(product_catalog)
                    last_product_intro_time = time.time()
                    time.sleep(2.0)
                    continue

                except Exception as e:
                    logger.error(f"Autopilot product intro failed: {e}")
                    last_product_intro_time = time.time()
                    time.sleep(5.0)
                    continue

            # Priority 3: Idle phrases
            if idle_phrases and elapsed_since_intro >= product_intro_interval * 0.6:
                phrase = idle_phrases[idle_phrase_idx % len(idle_phrases)]
                idle_phrase_idx += 1

                try:
                    audio_path = generate_tts_sync(
                        phrase, voice_id, openai_key, elevenlabs_key, tts_provider
                    )
                    engine.speak(audio_path)

                    while engine.state == EngineState.SPEAKING and not autopilot_stop_event.is_set():
                        time.sleep(0.3)

                    time.sleep(3.0)
                    continue

                except Exception as e:
                    logger.error(f"Autopilot idle phrase failed: {e}")
                    time.sleep(5.0)
                    continue

            # Nothing to do, wait
            time.sleep(1.0)

        except Exception as e:
            logger.error(f"Autopilot loop error: {e}", exc_info=True)
            time.sleep(5.0)

    autopilot_running = False
    logger.info("Autopilot loop stopped.")


# ─── TikTok Comment Fetcher ─────────────────────────────────────────────────

def tiktok_comment_fetcher(username: str):
    """
    Background thread that fetches TikTok Live comments.
    Uses TikTokLive library if available, otherwise provides a polling fallback.
    """
    global tiktok_connected

    logger.info(f"Starting TikTok comment fetcher for @{username}")

    try:
        from TikTokLive import TikTokLiveClient
        from TikTokLive.events import CommentEvent, ConnectEvent, DisconnectEvent

        client = TikTokLiveClient(unique_id=username)

        @client.on(ConnectEvent)
        async def on_connect(event: ConnectEvent):
            global tiktok_connected
            tiktok_connected = True
            logger.info(f"Connected to TikTok Live: @{username}")

        @client.on(DisconnectEvent)
        async def on_disconnect(event: DisconnectEvent):
            global tiktok_connected
            tiktok_connected = False
            logger.info(f"Disconnected from TikTok Live: @{username}")

        @client.on(CommentEvent)
        async def on_comment(event: CommentEvent):
            comment_data = {
                "user": event.user.nickname if hasattr(event.user, 'nickname') else str(event.user.unique_id),
                "text": event.comment,
                "timestamp": time.time()
            }
            tiktok_comments.append(comment_data)
            logger.info(f"TikTok comment from @{comment_data['user']}: {comment_data['text']}")

        # Run the client
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            loop.run_until_complete(client.start())
        except Exception as e:
            logger.error(f"TikTok client error: {e}")
        finally:
            loop.close()

    except ImportError:
        logger.warning("TikTokLive library not installed. Using manual comment injection mode.")
        logger.info("Install with: pip install TikTokLive")
        # In manual mode, comments are injected via the /api/v1/live/tiktok/comment endpoint
        tiktok_connected = True  # Mark as "connected" in manual mode
        while not tiktok_stop_event.is_set():
            time.sleep(1.0)
        tiktok_connected = False

    except Exception as e:
        logger.error(f"TikTok comment fetcher error: {e}", exc_info=True)
        tiktok_connected = False


# ─── API Endpoints ───────────────────────────────────────────────────────────

@app.get("/api/v1/live/health")
async def health():
    return {"status": "ok", "engine_state": engine.state.value if engine else "not_initialized"}


@app.get("/api/v1/live/status")
async def get_status(x_api_key: str = Header(None)):
    verify_api_key(x_api_key)
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    status = engine.get_status()
    status["autopilot_running"] = autopilot_running
    status["tiktok_connected"] = tiktok_connected
    status["tiktok_username"] = tiktok_username
    status["pending_comments"] = len(tiktok_comments)
    status["product_count"] = len(product_catalog)
    status["current_product_index"] = current_product_index
    return status


@app.post("/api/v1/live/prepare")
async def prepare_avatar(req: PrepareRequest, x_api_key: str = Header(None)):
    """Load MuseTalk models and prepare avatar from base video."""
    verify_api_key(x_api_key)
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")

    if engine.state not in (EngineState.IDLE, EngineState.ERROR):
        raise HTTPException(status_code=409, detail=f"Cannot prepare in state: {engine.state.value}")

    engine.config.fps = req.fps
    engine.config.version = req.version
    engine.config.bbox_shift = req.bbox_shift
    engine.config.extra_margin = req.extra_margin
    engine.config.parsing_mode = req.parsing_mode

    success = engine.prepare(req.video_path)
    if not success:
        raise HTTPException(status_code=500, detail="Avatar preparation failed")

    return {
        "status": "prepared",
        "avatar_frames": len(engine.musetalk.frame_list_cycle),
        "video_path": req.video_path,
        "frame_size": f"{engine.config.width}x{engine.config.height}",
        "parsing_mode": req.parsing_mode
    }


@app.post("/api/v1/live/test-video")
async def test_video(req: TestVideoRequest, x_api_key: str = Header(None)):
    """Generate a test MP4 with lip-sync. No RTMP needed. For quality verification."""
    verify_api_key(x_api_key)
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")

    if not engine.musetalk.models_loaded:
        raise HTTPException(status_code=400, detail="Models not loaded. Call /prepare first.")

    # Determine audio source
    audio_path = None
    if req.audio_path:
        audio_path = req.audio_path
    elif req.audio_url:
        audio_path = await download_audio(req.audio_url)
    elif req.text:
        audio_path = await generate_tts(req.text, openai_key=req.openai_api_key, elevenlabs_key=req.elevenlabs_api_key, provider=req.tts_provider)
    else:
        raise HTTPException(status_code=400, detail="Provide audio_path, audio_url, or text")

    output_path = os.path.join(tempfile.gettempdir(), f"test_{uuid.uuid4().hex[:8]}.mp4")

    start = time.time()
    success = engine.generate_test_video(audio_path, output_path)
    elapsed = time.time() - start

    if not success:
        raise HTTPException(status_code=500, detail="Test video generation failed")

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    return {
        "status": "success",
        "output_path": output_path,
        "size_mb": round(size_mb, 2),
        "generation_time_seconds": round(elapsed, 2),
        "audio_path": audio_path
    }


@app.get("/api/v1/live/test-video/download")
async def download_test_video(path: str, x_api_key: str = Header(None)):
    """Download a generated test video."""
    verify_api_key(x_api_key)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, media_type="video/mp4", filename="test_lipsync.mp4")


@app.post("/api/v1/live/start")
async def start_stream(req: StartRequest, x_api_key: str = Header(None)):
    """Start the RTMP live stream."""
    verify_api_key(x_api_key)
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")

    if not engine.musetalk.models_loaded:
        raise HTTPException(status_code=400, detail="Models not loaded. Call /prepare first.")

    engine.config.video_bitrate = req.video_bitrate
    engine.config.preset = req.preset

    success = engine.start_stream(req.rtmp_url)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to start stream")

    return {"status": "streaming", "rtmp_url": req.rtmp_url}


@app.post("/api/v1/live/speak")
async def speak(req: SpeakRequest, x_api_key: str = Header(None)):
    """Make the AI avatar speak."""
    verify_api_key(x_api_key)
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")

    if engine.state not in (EngineState.STREAMING, EngineState.SPEAKING):
        raise HTTPException(status_code=409, detail=f"Cannot speak in state: {engine.state.value}")

    audio_path = None
    if req.audio_path:
        audio_path = req.audio_path
    elif req.audio_url:
        audio_path = await download_audio(req.audio_url)
    elif req.text:
        audio_path = await generate_tts(req.text, req.voice_id, req.openai_api_key, req.elevenlabs_api_key, req.tts_provider)
    else:
        raise HTTPException(status_code=400, detail="Provide text, audio_url, or audio_path")

    start = time.time()
    success = engine.speak(audio_path)
    elapsed = time.time() - start

    if not success:
        raise HTTPException(status_code=500, detail="Lip-sync generation failed")

    return {
        "status": "speaking",
        "audio_path": audio_path,
        "lipsync_frames": len(engine._current_lipsync_frames),
        "generation_time_seconds": round(elapsed, 2)
    }


@app.post("/api/v1/live/respond")
async def respond_to_comment(req: RespondRequest, x_api_key: str = Header(None)):
    """Respond to a viewer comment: GPT → TTS → Lip-sync → Stream."""
    verify_api_key(x_api_key)
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")

    if engine.state not in (EngineState.STREAMING, EngineState.SPEAKING):
        raise HTTPException(status_code=409, detail=f"Cannot respond in state: {engine.state.value}")

    # Step 1: GPT response
    start = time.time()
    response_text = await generate_response(req.comment, req.persona, req.product_info, req.openai_api_key)
    gpt_time = time.time() - start
    logger.info(f"GPT response ({gpt_time:.1f}s): {response_text}")

    # Step 2: TTS
    start = time.time()
    audio_path = await generate_tts(response_text, req.voice_id, req.openai_api_key, req.elevenlabs_api_key, req.tts_provider)
    tts_time = time.time() - start
    logger.info(f"TTS generation ({tts_time:.1f}s): {audio_path}")

    # Step 3: Lip-sync
    start = time.time()
    success = engine.speak(audio_path)
    lipsync_time = time.time() - start

    if not success:
        raise HTTPException(status_code=500, detail="Lip-sync generation failed")

    total_time = gpt_time + tts_time + lipsync_time
    return {
        "status": "responding",
        "comment": req.comment,
        "response_text": response_text,
        "timing": {
            "gpt_seconds": round(gpt_time, 2),
            "tts_seconds": round(tts_time, 2),
            "lipsync_seconds": round(lipsync_time, 2),
            "total_seconds": round(total_time, 2)
        },
        "lipsync_frames": len(engine._current_lipsync_frames)
    }


@app.post("/api/v1/live/stop")
async def stop_stream(x_api_key: str = Header(None)):
    """Stop the live stream."""
    verify_api_key(x_api_key)
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")

    # Stop autopilot first
    global autopilot_running
    if autopilot_running:
        autopilot_running = False
        autopilot_stop_event.set()

    if engine.state not in (EngineState.STREAMING, EngineState.SPEAKING):
        raise HTTPException(status_code=409, detail=f"Cannot stop in state: {engine.state.value}")

    engine.stop_stream()
    return {"status": "stopped"}


# ─── Product Catalog Endpoints ───────────────────────────────────────────────

@app.post("/api/v1/live/products")
async def set_products(req: ProductCatalogRequest, x_api_key: str = Header(None)):
    """Set the product catalog for autonomous introductions."""
    verify_api_key(x_api_key)
    global product_catalog, current_product_index
    product_catalog = [p.dict() for p in req.products]
    current_product_index = 0
    return {
        "status": "updated",
        "product_count": len(product_catalog),
        "products": [p.get("name") for p in product_catalog]
    }


@app.get("/api/v1/live/products")
async def get_products(x_api_key: str = Header(None)):
    """Get the current product catalog."""
    verify_api_key(x_api_key)
    return {
        "products": product_catalog,
        "current_index": current_product_index
    }


# ─── Autopilot Endpoints ────────────────────────────────────────────────────

@app.post("/api/v1/live/autopilot/start")
async def start_autopilot(req: AutopilotStartRequest, x_api_key: str = Header(None)):
    """Start the autonomous conversation loop."""
    verify_api_key(x_api_key)
    global autopilot_running, autopilot_thread

    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")

    if engine.state not in (EngineState.STREAMING, EngineState.SPEAKING):
        raise HTTPException(status_code=409, detail=f"Cannot start autopilot in state: {engine.state.value}")

    if autopilot_running:
        raise HTTPException(status_code=409, detail="Autopilot already running")

    autopilot_stop_event.clear()
    autopilot_running = True

    config = {
        "persona": req.persona,
        "product_intro_interval": req.product_intro_interval,
        "comment_priority": req.comment_priority,
        "voice_id": req.voice_id,
        "openai_api_key": req.openai_api_key,
        "elevenlabs_api_key": req.elevenlabs_api_key,
        "tts_provider": req.tts_provider,
        "idle_phrases": req.idle_phrases,
    }

    autopilot_thread = threading.Thread(target=autopilot_loop, args=(config,), daemon=True)
    autopilot_thread.start()

    return {
        "status": "started",
        "persona": req.persona[:50] + "...",
        "product_intro_interval": req.product_intro_interval,
        "product_count": len(product_catalog),
        "tiktok_connected": tiktok_connected
    }


@app.post("/api/v1/live/autopilot/stop")
async def stop_autopilot(x_api_key: str = Header(None)):
    """Stop the autonomous conversation loop."""
    verify_api_key(x_api_key)
    global autopilot_running

    if not autopilot_running:
        raise HTTPException(status_code=409, detail="Autopilot not running")

    autopilot_running = False
    autopilot_stop_event.set()

    return {"status": "stopped"}


@app.get("/api/v1/live/autopilot/status")
async def get_autopilot_status(x_api_key: str = Header(None)):
    """Get autopilot status."""
    verify_api_key(x_api_key)
    return {
        "running": autopilot_running,
        "tiktok_connected": tiktok_connected,
        "tiktok_username": tiktok_username,
        "pending_comments": len(tiktok_comments),
        "product_count": len(product_catalog),
        "current_product_index": current_product_index,
        "conversation_history_length": len(conversation_history)
    }


# ─── TikTok Integration Endpoints ───────────────────────────────────────────

@app.post("/api/v1/live/tiktok/connect")
async def tiktok_connect(req: TikTokConnectRequest, x_api_key: str = Header(None)):
    """Connect to TikTok Live to fetch comments."""
    verify_api_key(x_api_key)
    global tiktok_connected, tiktok_thread, tiktok_username

    if tiktok_connected:
        raise HTTPException(status_code=409, detail="Already connected to TikTok")

    tiktok_username = req.username
    tiktok_stop_event.clear()
    tiktok_thread = threading.Thread(target=tiktok_comment_fetcher, args=(req.username,), daemon=True)
    tiktok_thread.start()

    # Wait a moment for connection
    time.sleep(2.0)

    return {
        "status": "connecting" if not tiktok_connected else "connected",
        "username": req.username
    }


@app.post("/api/v1/live/tiktok/disconnect")
async def tiktok_disconnect(x_api_key: str = Header(None)):
    """Disconnect from TikTok Live."""
    verify_api_key(x_api_key)
    global tiktok_connected

    tiktok_stop_event.set()
    tiktok_connected = False

    return {"status": "disconnected"}


@app.get("/api/v1/live/tiktok/status")
async def tiktok_status(x_api_key: str = Header(None)):
    """Get TikTok connection status."""
    verify_api_key(x_api_key)
    return {
        "connected": tiktok_connected,
        "username": tiktok_username,
        "pending_comments": len(tiktok_comments),
        "recent_comments": list(tiktok_comments)[-5:]
    }


@app.post("/api/v1/live/tiktok/comment")
async def inject_comment(comment: Dict[str, str], x_api_key: str = Header(None)):
    """Manually inject a comment (for testing or when TikTokLive library is not available)."""
    verify_api_key(x_api_key)
    comment_data = {
        "user": comment.get("user", "viewer"),
        "text": comment.get("text", ""),
        "timestamp": time.time()
    }
    tiktok_comments.append(comment_data)
    return {"status": "injected", "comment": comment_data}


# ─── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("LIVE_API_PORT", "8002"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
