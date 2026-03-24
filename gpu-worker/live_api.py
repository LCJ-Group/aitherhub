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
"""

import os
import sys
import time
import uuid
import logging
import tempfile
from typing import Optional
from contextlib import asynccontextmanager

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    global engine
    config = EngineConfig()
    engine = LiveStreamEngine(config)
    logger.info("Live Streaming Engine initialized.")
    yield
    if engine and engine.state in (EngineState.STREAMING, EngineState.SPEAKING):
        engine.stop_stream()
    logger.info("Live Streaming Engine shut down.")


app = FastAPI(
    title="AI Live Streaming Engine",
    description="Server-side hybrid AI live streaming with real-time lip-sync",
    version="1.0.0",
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


class RespondRequest(BaseModel):
    comment: str = Field(..., description="Viewer comment to respond to")
    persona: str = Field(
        "あなたはプロのライブコマース販売員です。親しみやすく、商品の魅力を伝えるのが得意です。",
        description="AI persona/system prompt"
    )
    product_info: Optional[str] = Field(None, description="Current product information")
    voice_id: Optional[str] = Field(None, description="TTS voice ID")
    language: str = Field("ja", description="Language for response")


class TestVideoRequest(BaseModel):
    audio_path: Optional[str] = Field(None, description="Local path to audio file")
    audio_url: Optional[str] = Field(None, description="URL to audio file")
    text: Optional[str] = Field(None, description="Text to speak (TTS)")


# ─── TTS Functions ───────────────────────────────────────────────────────────

async def text_to_speech_openai(text: str, output_path: str, voice: str = "alloy"):
    """Generate speech using OpenAI TTS API."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{OPENAI_API_BASE}/audio/speech",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
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


async def text_to_speech_elevenlabs(text: str, output_path: str, voice_id: str = None):
    """Generate speech using ElevenLabs TTS API."""
    vid = voice_id or ELEVENLABS_VOICE_ID
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{vid}",
            headers={
                "xi-api-key": ELEVENLABS_API_KEY,
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


async def generate_tts(text: str, voice_id: str = None) -> str:
    """Generate TTS audio and return the file path."""
    output_path = os.path.join(tempfile.gettempdir(), f"tts_{uuid.uuid4().hex[:8]}.wav")
    if TTS_PROVIDER == "elevenlabs" and ELEVENLABS_API_KEY:
        await text_to_speech_elevenlabs(text, output_path, voice_id)
    else:
        await text_to_speech_openai(text, output_path)
    return output_path


# ─── GPT Functions ───────────────────────────────────────────────────────────

async def generate_response(comment: str, persona: str, product_info: str = None) -> str:
    """Generate a conversational response using GPT."""
    messages = [{"role": "system", "content": persona}]
    if product_info:
        messages.append({
            "role": "system",
            "content": f"現在紹介中の商品情報:\n{product_info}"
        })
    messages.append({
        "role": "user",
        "content": f"視聴者のコメント: 「{comment}」\n\n自然に、親しみやすく返答してください。短く簡潔に（50文字以内）。"
    })

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{OPENAI_API_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
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


# ─── API Endpoints ───────────────────────────────────────────────────────────

@app.get("/api/v1/live/health")
async def health():
    return {"status": "ok", "engine_state": engine.state.value if engine else "not_initialized"}


@app.get("/api/v1/live/status")
async def get_status(x_api_key: str = Header(None)):
    verify_api_key(x_api_key)
    if not engine:
        raise HTTPException(status_code=500, detail="Engine not initialized")
    return engine.get_status()


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

    success = engine.prepare(req.video_path)
    if not success:
        raise HTTPException(status_code=500, detail="Avatar preparation failed")

    return {
        "status": "prepared",
        "avatar_frames": len(engine.musetalk.frame_list_cycle),
        "video_path": req.video_path,
        "frame_size": f"{engine.config.width}x{engine.config.height}"
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
        audio_path = await generate_tts(req.text)
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
        audio_path = await generate_tts(req.text, req.voice_id)
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
    response_text = await generate_response(req.comment, req.persona, req.product_info)
    gpt_time = time.time() - start
    logger.info(f"GPT response ({gpt_time:.1f}s): {response_text}")

    # Step 2: TTS
    start = time.time()
    audio_path = await generate_tts(response_text, req.voice_id)
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

    if engine.state not in (EngineState.STREAMING, EngineState.SPEAKING):
        raise HTTPException(status_code=409, detail=f"Cannot stop in state: {engine.state.value}")

    engine.stop_stream()
    return {"status": "stopped"}


# ─── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("LIVE_API_PORT", "8002"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
