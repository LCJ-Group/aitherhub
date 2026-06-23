"""
AI Video Generator — One-Click Product Video Generation Pipeline
================================================================

The killer feature: Input product info → Get a lip-synced video of a real liver
introducing your product, powered by AI.

Pipeline:
  1. Product Info (image + text) → GPT-4 Script Generation (using winning patterns)
  2. Script → ElevenLabs TTS (liver's cloned voice)
  3. Audio + Avatar → HeyGen Lip-Sync Video Generation
  4. Video → Azure Blob Storage → Delivery

Endpoints:
  POST /api/v1/ai-video-generator/generate     — Full pipeline: product → video
  GET  /api/v1/ai-video-generator/status/{job_id} — Check generation status
  GET  /api/v1/ai-video-generator/jobs          — List user's generation jobs
  GET  /api/v1/ai-video-generator/avatars       — List available livers/avatars
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import base64
import re

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.dependencies import get_current_user

logger = logging.getLogger(__name__)


def _get_openai_client():
    """Create OpenAI client using Azure OpenAI credentials (same as ai_clip_generator)."""
    import openai
    from urllib.parse import urlparse as _urlparse
    azure_key = os.getenv("AZURE_OPENAI_KEY", "")
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    if azure_key and azure_endpoint:
        _parsed = _urlparse(azure_endpoint)
        clean_endpoint = f"{_parsed.scheme}://{_parsed.netloc}/"
        return openai.AsyncAzureOpenAI(
            api_key=azure_key,
            azure_endpoint=clean_endpoint,
            api_version=os.getenv("GPT5_API_VERSION", "2025-04-01-preview"),
        )
    # Fallback to standard OpenAI (requires OPENAI_API_KEY)
    from openai import AsyncOpenAI
    return AsyncOpenAI()


def _get_openai_model() -> str:
    """Get the model/deployment name to use."""
    return os.getenv("GPT5_MODEL") or os.getenv("GPT5_DEPLOYMENT") or "gpt-4.1-mini"


router = APIRouter(prefix="/ai-video-generator", tags=["AI Video Generator"])

# ──────────────────────────────────────────────
# In-memory job store (production: use DB table)
# ──────────────────────────────────────────────
_jobs: Dict[str, Dict[str, Any]] = {}

# ──────────────────────────────────────────────
# Avatars cache (reduces response time from ~6s to <100ms)
# SAS tokens are valid for 2h, cache for 30min
# ──────────────────────────────────────────────
_avatars_cache: Optional[Dict[str, Any]] = None
_avatars_cache_time: float = 0
_AVATARS_CACHE_TTL: int = 1800  # 30 minutes


# ──────────────────────────────────────────────
# Request / Response Models
# ──────────────────────────────────────────────

class VideoGenerateRequest(BaseModel):
    """Request to generate a product introduction video."""
    # Product info (all optional - can be auto-detected from image/URL)
    product_name: Optional[str] = Field(None, max_length=200, description="商品名（省略時は画像/URLから自動取得）")
    product_description: Optional[str] = Field(None, max_length=2000, description="商品説明・特徴")
    product_image_url: Optional[str] = Field(None, description="商品画像URL")
    product_page_url: Optional[str] = Field(None, description="商品ページURL（楽天/Amazon等）")
    product_price: Optional[str] = Field(None, max_length=200, description="商品価格")
    original_price: Optional[str] = Field(None, max_length=100, description="定価")
    discounted_price: Optional[str] = Field(None, max_length=100, description="割引価格")
    benefits: Optional[str] = Field(None, max_length=1000, description="特典・限定オファー")
    target_audience: Optional[str] = Field(None, max_length=500, description="ターゲット層")

    # Liver/Avatar selection
    avatar_id: str = Field(..., description="HeyGen avatar ID (ライバーのデジタルツイン)")
    voice_id: Optional[str] = Field(None, description="ElevenLabs voice ID (省略時はデフォルト)")

    # Generation options
    tone: str = Field("energetic", description="トーン: energetic, professional_friendly, calm, sexy")
    language: str = Field("ja", description="言語: ja, en, zh, ko")
    duration_seconds: int = Field(60, ge=15, le=180, description="目標動画長さ（秒）")
    dimension_width: int = Field(720, description="動画幅")
    dimension_height: int = Field(1280, description="動画高さ（デフォルト: 縦型9:16）")

    # Optional: user-provided script (skip AI generation)
    custom_script: Optional[str] = Field(None, max_length=5000, description="カスタム台本（省略時はAI生成）")

    # Product showcase options (NEW)
    showcase_mode: Optional[str] = Field(None, description="商品展示モード: overlay, split, fullscreen")
    showcase_description: Optional[str] = Field(None, max_length=2000, description="商品展示方式の詳細説明（カメラ角度、回転等）")

    # Person photo analysis (NEW)
    person_image_url: Optional[str] = Field(None, description="人物写真URL（アップロード後のURL）")
    # Digital Twin motion control (NEW)
    motion_prompt: Optional[str] = Field(None, max_length=2000, description="Digital Twin動作指示（自然言語で手の動き・体の動きを指定）")
    use_digital_twin_v3: Optional[bool] = Field(False, description="Digital Twin v3 APIを使用（motion_prompt対応）")


class VideoGenerateResponse(BaseModel):
    """Response from video generation request."""
    success: bool
    job_id: Optional[str] = None
    status: str = "queued"
    message: Optional[str] = None
    error: Optional[str] = None


class JobStatusResponse(BaseModel):
    """Detailed job status."""
    job_id: str
    status: str  # queued, generating_script, generating_audio, generating_video, completed, failed
    progress: int = 0  # 0-100
    created_at: str
    updated_at: str
    # Results (populated on completion)
    script: Optional[str] = None
    audio_url: Optional[str] = None
    video_url: Optional[str] = None
    video_duration_sec: Optional[float] = None
    # Input echo
    product_name: Optional[str] = None
    avatar_id: Optional[str] = None
    language: Optional[str] = None
    tone: Optional[str] = None
    # Error info
    error: Optional[str] = None
    error_step: Optional[str] = None


class AvatarInfo(BaseModel):
    """Available avatar/liver info."""
    avatar_id: str
    name: str
    preview_image_url: Optional[str] = None
    thumbnail_image_url: Optional[str] = None  # Static JPG thumbnail (first frame)
    avatar_type: str = "full"  # full, half, talking_photo
    face_image_url: Optional[str] = None  # For DB livers: face thumbnail URL
    source: str = "aitherhub"  # aitherhub (DB liver) or heygen (Digital Twin)


# ──────────────────────────────────────────────
# Auth helper
# ──────────────────────────────────────────────

async def verify_admin_key(x_admin_key: str = Header(...)):
    if x_admin_key != "aither:hub":
        raise HTTPException(status_code=401, detail="Invalid admin key")
    return True


# ──────────────────────────────────────────────
# Pipeline Implementation
# ──────────────────────────────────────────────

async def _run_pipeline(job_id: str, request: VideoGenerateRequest, db_url: str):
    """
    Background task: runs the full product → video pipeline.

    Steps:
      1. Generate script (GPT-4 with winning patterns)
      2. Generate audio (ElevenLabs TTS with liver's voice)
      3. Generate video (HeyGen lip-sync with avatar)
      4. Store result
    """
    job = _jobs[job_id]

    try:
        # ═══════════════════════════════════════════
        # STEP 0: Auto-detect product info (if not provided)
        # ═══════════════════════════════════════════
        if not request.product_name and (request.product_image_url or request.product_page_url):
            job["status"] = "analyzing_product"
            job["progress"] = 5
            job["updated_at"] = datetime.now(timezone.utc).isoformat()
            try:
                product_info = await _analyze_product(request)
                if product_info:
                    if not request.product_name and product_info.get("name"):
                        request.product_name = product_info["name"]
                    if not request.product_description and product_info.get("description"):
                        request.product_description = product_info["description"]
                    if not request.original_price and product_info.get("price"):
                        request.original_price = product_info["price"]
                    logger.info(f"[AIVideoGen:{job_id}] Product auto-detected: {request.product_name}")
            except Exception as e:
                logger.warning(f"[AIVideoGen:{job_id}] Product analysis failed (continuing): {e}")

        # Ensure we have at least a product name
        if not request.product_name:
            request.product_name = "商品紹介"  # Fallback

        # ═══════════════════════════════════════════
        # STEP 1: Script Generation
        # ═══════════════════════════════════════════
        job["status"] = "generating_script"
        job["progress"] = 10
        job["updated_at"] = datetime.now(timezone.utc).isoformat()

        if request.custom_script:
            # User provided their own script
            script_text = request.custom_script
            logger.info(f"[AIVideoGen:{job_id}] Using custom script ({len(script_text)} chars)")
        else:
            # Generate script with GPT-4
            script_text = await _generate_script(request)
            logger.info(f"[AIVideoGen:{job_id}] Script generated ({len(script_text)} chars)")

        job["script"] = script_text
        job["progress"] = 30
        job["updated_at"] = datetime.now(timezone.utc).isoformat()

        # ═══════════════════════════════════════════
        # STEP 2: Audio Generation (ElevenLabs TTS)
        # ═══════════════════════════════════════════
        job["status"] = "generating_audio"
        job["progress"] = 40
        job["updated_at"] = datetime.now(timezone.utc).isoformat()

        audio_url = await _generate_audio(script_text, request)
        logger.info(f"[AIVideoGen:{job_id}] Audio generated: {audio_url[:60]}...")

        job["audio_url"] = audio_url
        job["progress"] = 60
        job["updated_at"] = datetime.now(timezone.utc).isoformat()

        # ═══════════════════════════════════════════
        # STEP 2.4: Auto-extract person image from liver clips (if needed)
        # When showcase_mode is set but no person_image_url, extract a frame
        # from the liver's clip video to use as the person reference image.
        # ═══════════════════════════════════════════
        if request.showcase_mode and request.product_image_url and not request.person_image_url:
            if request.avatar_id and request.avatar_id.startswith("aitherhub:"):
                liver_name = request.avatar_id.replace("aitherhub:", "")
                logger.info(f"[AIVideoGen:{job_id}] Showcase mode with AitherHub liver '{liver_name}' - extracting person frame...")
                try:
                    from sqlalchemy import text as sa_text
                    from app.core.db import AsyncSessionLocal
                    async with AsyncSessionLocal() as db:
                        result = await db.execute(sa_text("""
                            SELECT vc.clip_url FROM video_clips vc
                            LEFT JOIN videos v ON v.id = vc.video_id
                            WHERE vc.clip_url IS NOT NULL
                                AND vc.clip_url != ''
                                AND v.original_filename IS NOT NULL
                                AND v.original_filename LIKE :pattern
                            ORDER BY vc.created_at DESC
                            LIMIT 1
                        """), {"pattern": f"{liver_name}-%"})
                        row = result.fetchone()
                    if row and row.clip_url:
                        clip_url = _ensure_sas_url(row.clip_url)
                        # Extract a frame from the clip video
                        import httpx, tempfile, subprocess
                        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as dl_client:
                            resp = await dl_client.get(clip_url)
                            resp.raise_for_status()
                        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_vid:
                            tmp_vid.write(resp.content)
                            tmp_vid_path = tmp_vid.name
                        frame_path = tmp_vid_path.replace(".mp4", "_frame.jpg")
                        # Extract frame at 1 second (usually a good pose)
                        subprocess.run(
                            ["ffmpeg", "-y", "-ss", "1", "-i", tmp_vid_path,
                             "-vframes", "1", "-q:v", "2", frame_path],
                            capture_output=True, timeout=30
                        )
                        if os.path.exists(frame_path) and os.path.getsize(frame_path) > 0:
                            # Upload frame to blob
                            with open(frame_path, "rb") as f:
                                frame_bytes = f.read()
                            blob_service = BlobServiceClient.from_connection_string(CONNECTION_STRING)
                            blob_name = f"ai-video-gen/person-frames/{uuid.uuid4().hex}.jpg"
                            blob_client = blob_service.get_blob_client(container=CONTAINER_NAME, blob=blob_name)
                            blob_client.upload_blob(
                                frame_bytes, overwrite=True,
                                content_settings=ContentSettings(content_type="image/jpeg")
                            )
                            request.person_image_url = generate_read_sas_from_url(blob_client.url, expires_hours=24)
                            logger.info(f"[AIVideoGen:{job_id}] Person frame extracted and uploaded: {request.person_image_url[:60]}...")
                        # Cleanup
                        for p in [tmp_vid_path, frame_path]:
                            if os.path.exists(p):
                                os.unlink(p)
                except Exception as extract_err:
                    logger.warning(f"[AIVideoGen:{job_id}] Failed to extract person frame from liver clips: {extract_err}")

        # ═══════════════════════════════════════════
        # STEP 2.5: AI Showcase Image Generation (if enabled)
        # Generate composite image of person + product using AI
        # This becomes the talking photo for HeyGen video generation
        # ═══════════════════════════════════════════
        if request.showcase_mode and request.product_image_url and request.person_image_url:
            job["status"] = "compositing_showcase"
            job["progress"] = 62
            job["updated_at"] = datetime.now(timezone.utc).isoformat()
            try:
                composite_image_url = await _generate_showcase_image(
                    person_image_url=request.person_image_url,
                    product_image_url=request.product_image_url,
                    showcase_description=request.showcase_description,
                    showcase_mode=request.showcase_mode,
                )
                # Replace person_image_url with the composite image
                # This way HeyGen will use the composite as the talking photo
                request.person_image_url = composite_image_url
                logger.info(f"[AIVideoGen:{job_id}] Showcase image generated: {composite_image_url[:60]}...")
            except Exception as showcase_err:
                logger.warning(
                    f"[AIVideoGen:{job_id}] AI showcase image generation failed (using original person photo): {showcase_err}"
                )
                # Non-fatal: continue with original person photo if AI compositing fails

        # ═══════════════════════════════════════════
        # STEP 3: Video Generation (HeyGen)
        # ═══════════════════════════════════════════
        job["status"] = "generating_video"
        job["progress"] = 70
        job["updated_at"] = datetime.now(timezone.utc).isoformat()
        video_url, duration_sec = await _generate_video(audio_url, request)
        logger.info(f"[AIVideoGen:{job_id}] Video generated: {video_url[:60]}...")
        # ═══════════════════════════════════════════
        # STEP 3.5: Product Showcase FFmpeg Compositing (fallback)
        # Only runs if AI image generation was NOT used (no person_image_url)
        # or if showcase_mode is set but person photo was not provided
        # ═══════════════════════════════════════════
        if request.showcase_mode and request.product_image_url and not request.person_image_url:
            job["status"] = "compositing_showcase"
            job["progress"] = 85
            job["updated_at"] = datetime.now(timezone.utc).isoformat()
            try:
                video_url = await _composite_showcase(
                    video_url=video_url,
                    product_image_url=request.product_image_url,
                    showcase_mode=request.showcase_mode,
                    showcase_description=request.showcase_description,
                    duration_sec=duration_sec,
                )
                logger.info(f"[AIVideoGen:{job_id}] Showcase composited (FFmpeg): {video_url[:60]}...")
            except Exception as comp_err:
                logger.warning(f"[AIVideoGen:{job_id}] Showcase compositing failed (using original): {comp_err}")
                # Non-fatal: continue with original video if compositing fails

        # ═══════════════════════════════════════════
        # STEP 4: Complete
        # ═══════════════════════════════════════════
        job["status"] = "completed"
        job["progress"] = 100
        job["video_url"] = video_url
        job["video_duration_sec"] = duration_sec
        job["updated_at"] = datetime.now(timezone.utc).isoformat()

        logger.info(
            f"[AIVideoGen:{job_id}] \u2705 Pipeline complete! "
            f"Product: {request.product_name}, Duration: {duration_sec}s"
        )

        # Persist completion to DB
        await _sync_job_to_db(job_id, job)

    except Exception as e:
        logger.error(f"[AIVideoGen:{job_id}] \u274c Pipeline failed: {e}", exc_info=True)
        job["status"] = "failed"
        job["error"] = str(e)
        job["error_step"] = job.get("status", "unknown")
        job["updated_at"] = datetime.now(timezone.utc).isoformat()

        # Persist failure to DB
        await _sync_job_to_db(job_id, job)


async def _sync_job_to_db(job_id: str, job: Dict[str, Any]):
    """Sync in-memory job state to DB."""
    try:
        from app.core.db import AsyncSessionLocal
        from app.models.orm.ai_video_gen_job import AiVideoGenJob
        from sqlalchemy import update as sa_update
        async with AsyncSessionLocal() as db_session:
            await db_session.execute(
                sa_update(AiVideoGenJob).where(AiVideoGenJob.job_id == job_id).values(
                    status=job.get("status"),
                    progress=job.get("progress", 0),
                    script=job.get("script"),
                    audio_url=job.get("audio_url"),
                    video_url=job.get("video_url"),
                    video_duration_sec=job.get("video_duration_sec"),
                    error=job.get("error"),
                    error_step=job.get("error_step"),
                    completed_at=datetime.now(timezone.utc) if job.get("status") in ("completed", "failed") else None,
                )
            )
            await db_session.commit()
    except Exception as e:
        logger.warning(f"[AIVideoGen:{job_id}] DB sync failed (non-fatal): {e}")


async def _load_jobs_from_db() -> List[Dict[str, Any]]:
    """Load completed/failed jobs from DB for history display."""
    try:
        from app.core.db import AsyncSessionLocal
        from app.models.orm.ai_video_gen_job import AiVideoGenJob
        from sqlalchemy import select, desc
        async with AsyncSessionLocal() as db_session:
            result = await db_session.execute(
                select(AiVideoGenJob).order_by(desc(AiVideoGenJob.created_at)).limit(100)
            )
            rows = result.scalars().all()
            jobs = []
            for row in rows:
                jobs.append({
                    "job_id": row.job_id,
                    "status": row.status,
                    "progress": row.progress or 0,
                    "created_at": row.created_at.isoformat() if row.created_at else "",
                    "updated_at": row.updated_at.isoformat() if row.updated_at else "",
                    "product_name": row.product_name,
                    "avatar_id": row.avatar_id,
                    "language": row.language,
                    "tone": row.tone,
                    "script": row.script,
                    "audio_url": row.audio_url,
                    "video_url": row.video_url,
                    "video_duration_sec": row.video_duration_sec,
                    "error": row.error,
                    "error_step": row.error_step,
                })
            return jobs
    except Exception as e:
        logger.warning(f"[AIVideoGen] DB load failed: {e}")
        return []


async def _analyze_product(request: VideoGenerateRequest) -> Optional[Dict[str, str]]:
    """
    Analyze product from image URL or page URL using GPT-4 Vision.
    Returns dict with: name, description, price, brand, notes
    """
    import httpx

    client = _get_openai_client()

    # If product_page_url is provided, try to scrape basic info
    page_context = ""
    if request.product_page_url:
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as http:
                resp = await http.get(request.product_page_url, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; AitherHub/1.0)"
                })
                if resp.status_code == 200:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(resp.text, "html.parser")
                    title = soup.title.string if soup.title else ""
                    # Extract meta description
                    meta_desc = soup.find("meta", attrs={"name": "description"})
                    desc = meta_desc["content"] if meta_desc and meta_desc.get("content") else ""
                    # Extract OG image if no product_image_url
                    if not request.product_image_url:
                        og_img = soup.find("meta", attrs={"property": "og:image"})
                        if og_img and og_img.get("content"):
                            request.product_image_url = og_img["content"]
                    page_context = f"\n\u30da\u30fc\u30b8\u30bf\u30a4\u30c8\u30eb: {title}\n\u30da\u30fc\u30b8\u8aac\u660e: {desc[:500]}"
        except Exception as e:
            logger.warning(f"[AIVideoGen] Page scrape failed: {e}")

    # Build messages for GPT-4 Vision
    system_msg = (
        "You are a product information extraction assistant. "
        "Analyze the product photo/info and extract structured information. "
        "Return ONLY a valid JSON object with these fields:\n"
        '- "name": product name (string, required)\n'
        '- "description": product description including features, key selling points (string)\n'
        '- "price": price if visible (string, include currency symbol)\n'
        '- "brand": brand name if visible (string)\n'
        '- "notes": additional sales points or notable features (string)\n\n'
        "Detect the language of the product and respond in that language. "
        "Do NOT wrap the JSON in markdown code blocks."
    )

    user_content = []
    user_text = "\u3053\u306e\u5546\u54c1\u306e\u60c5\u5831\u3092\u89e3\u6790\u3057\u3066\u3001JSON\u5f62\u5f0f\u3067\u8fd4\u3057\u3066\u304f\u3060\u3055\u3044\u3002"
    if page_context:
        user_text += page_context
    user_content.append({"type": "text", "text": user_text})
    if request.product_image_url:
        # V2.34.6: Download image and convert to base64 (OpenAI can't always download external URLs)
        image_data_url = None
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
                img_resp = await http.get(request.product_image_url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                })
                if img_resp.status_code == 200 and len(img_resp.content) <= 20 * 1024 * 1024:
                    ct = img_resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
                    if ct not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
                        ct = "image/jpeg"
                    b64_img = base64.b64encode(img_resp.content).decode("utf-8")
                    image_data_url = f"data:{ct};base64,{b64_img}"
        except Exception as e:
            logger.warning(f"[AIVideoGen] Image download failed in pipeline: {e}")
        user_content.append({
            "type": "image_url",
            "image_url": {"url": image_data_url or request.product_image_url, "detail": "high"},
        })

    response = await client.chat.completions.create(
        model=_get_openai_model(),
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_content},
        ],
        max_completion_tokens=1000,
    )

    raw_text = response.choices[0].message.content.strip()

    # Parse JSON
    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw_text)
    if json_match:
        raw_text = json_match.group(1).strip()

    try:
        product_data = json.loads(raw_text)
    except json.JSONDecodeError:
        json_match2 = re.search(r'\{[\s\S]*\}', raw_text)
        if json_match2:
            product_data = json.loads(json_match2.group())
        else:
            return None

    return product_data


async def _generate_script(request: VideoGenerateRequest) -> str:
    """Generate a selling script using GPT-4 with winning patterns."""
    client = _get_openai_client()

    # Calculate target word count based on duration
    # Japanese speech: ~300 characters per minute
    target_chars = int(request.duration_seconds * 5)  # ~5 chars/sec for Japanese

    # Build product context
    product_context = f"商品名: {request.product_name}"
    if request.product_description:
        product_context += f"\n商品説明: {request.product_description}"
    if request.product_price:
        product_context += f"\n価格: {request.product_price}"
    if request.original_price:
        product_context += f"\n定価: {request.original_price}"
    if request.discounted_price:
        product_context += f"\n割引価格: {request.discounted_price}"
    if request.benefits:
        product_context += f"\n特典: {request.benefits}"
    if request.target_audience:
        product_context += f"\nターゲット: {request.target_audience}"

    # Tone mapping
    tone_instructions = {
        "energetic": "テンション高めで元気よく、視聴者を巻き込むような話し方。「すごい！」「見て見て！」などの感嘆詞を使う。",
        "professional_friendly": "プロフェッショナルだけど親しみやすい。信頼感を与えながらも堅すぎない。",
        "calm": "落ち着いた優しいトーン。高級感を演出し、じっくり商品の良さを伝える。",
        "sexy": "魅力的で自信に満ちたトーン。商品を使うことで得られる自分への投資感を演出。",
    }
    tone_desc = tone_instructions.get(request.tone, tone_instructions["energetic"])

    # Build showcase instructions if provided
    showcase_instruction = ""
    if request.showcase_mode:
        mode_desc = {
            "overlay": "動画中に商品画像がオーバーレイ表示されます（画面の右上に小さく表示）",
            "split": "画面が左右に分割され、左側にあなた、右側に商品画像が表示されます",
            "fullscreen": "動画の途中で商品画像が全画面表示される瞬間があります",
        }
        showcase_instruction = f"\n\n【商品展示】\n展示モード: {mode_desc.get(request.showcase_mode, request.showcase_mode)}\n"
        if request.showcase_description:
            showcase_instruction += f"展示の詳細指示: {request.showcase_description}\n"
        showcase_instruction += "台本の中で、商品画像が表示されるタイミングに合わせて『こちらをご覧ください』『画面に映っているのが〜』などの自然な誘導を入れてください。"

    system_prompt = f"""あなたはライブコマースのトップセールスライバーです。
商品を紹介する台本を生成してください。

【重要ルール】
- 台本はセリフのみ（ト書き・指示は不要）
- 視聴者に直接話しかけるように
- 自然な口語体で書く
- 目標文字数: 約{target_chars}文字（{request.duration_seconds}秒の動画用）
- 冒頭で注意を引き、中盤で商品の魅力を伝え、最後にCTA（行動喚起）で締める

【トーン】
{tone_desc}

【構成】
1. フック（最初の5秒で視聴者の注意を引く）
2. 商品紹介（何がすごいのか、どんな悩みを解決するか）
3. 使用感・効果（実際に使うとどうなるか）
4. 限定感・緊急性（今買うべき理由）
5. CTA（「リンクから購入」「コメントで質問」など）{showcase_instruction}"""

    user_prompt = f"""以下の商品の紹介台本を生成してください：

{product_context}

台本（セリフのみ）:"""

    # Use vision if product image is available
    messages = [{"role": "system", "content": system_prompt}]

    if request.product_image_url:
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": user_prompt},
                {"type": "image_url", "image_url": {"url": request.product_image_url}},
            ],
        })
    else:
        messages.append({"role": "user", "content": user_prompt})

    response = await client.chat.completions.create(
        model=_get_openai_model(),
        messages=messages,
        max_completion_tokens=2000,
    )

    script = response.choices[0].message.content.strip()

    # Clean up: remove any markdown formatting or stage directions
    import re
    script = re.sub(r'\*\*.*?\*\*', '', script)  # Remove bold markers
    script = re.sub(r'\[.*?\]', '', script)  # Remove stage directions in brackets
    script = re.sub(r'（.*?）', '', script)  # Remove stage directions in Japanese brackets
    script = re.sub(r'\n{3,}', '\n\n', script)  # Normalize line breaks
    script = script.strip()

    return script


async def _generate_audio(script_text: str, request: VideoGenerateRequest) -> str:
    """Generate TTS audio using ElevenLabs and upload to Azure Blob."""
    from app.services.elevenlabs_tts_service import ElevenLabsTTSService

    el_service = ElevenLabsTTSService()

    # Generate MP3 audio
    mp3_audio = await el_service.text_to_speech(
        text=script_text,
        voice_id=request.voice_id,
        output_format="mp3_44100_128",
        language_code=request.language,
    )

    logger.info(f"[AIVideoGen] TTS generated: {len(mp3_audio)} bytes")

    # Upload to Azure Blob
    audio_id = f"ai-video-gen-{uuid.uuid4().hex[:10]}"
    from app.api.v1.endpoints.digital_human import _upload_mp3_to_blob, _ensure_sas_url
    audio_blob_url = await _upload_mp3_to_blob(mp3_audio, audio_id)
    audio_sas_url = _ensure_sas_url(audio_blob_url)

    return audio_sas_url


async def _generate_video(audio_url: str, request: VideoGenerateRequest) -> tuple:
    """Generate lip-sync video using HeyGen and return (video_url, duration_sec).
    
    Supports three modes (checked in priority order):
      1. Custom person image (person_image_url set):
         Upload person image as talking photo → generate video
      2. AitherHub liver (avatar_id starts with 'aitherhub:'): 
         Upload face image as talking photo → generate video with talking photo
      3. HeyGen Digital Twin (regular avatar_id):
         Use pre-registered avatar directly
    """
    from app.services.heygen_service import HeyGenService, get_heygen_service
    from app.api.v1.endpoints.digital_human import _upload_video_to_blob, _ensure_sas_url

    heygen = get_heygen_service()
    if not heygen.api_key:
        raise Exception("HEYGEN_API_KEY not configured")

    dimension = {"width": request.dimension_width, "height": request.dimension_height}
    title = f"AIVideoGen-{request.product_name[:30]}-{uuid.uuid4().hex[:6]}"

    # ─── Route by avatar source (priority order) ───
    # Mode 0: Digital Twin v3 API (highest priority when explicitly enabled)
    # This mode requires a pre-created Digital Twin avatar_id
    if request.use_digital_twin_v3 or request.motion_prompt:
        dt_avatar_id = request.avatar_id
        if not dt_avatar_id or dt_avatar_id == "custom_person":
            raise Exception(
                "Digital Twin v3にはDigital Twin avatar_idが必要です。"
                "先にDigital Twinを作成してから、それをアバターとして選択してください。"
            )
        motion = request.motion_prompt or ""
        # Auto-generate motion_prompt from showcase context if not provided
        if not motion and request.showcase_mode and request.product_name:
            motion = (
                f"Hold the product ({request.product_name}) in both hands at chest height, "
                f"presenting it to the camera with natural gestures. "
                f"Occasionally look at the product then back at the camera."
            )
        # Map dimension to aspect_ratio
        aspect = "9:16"
        if request.dimension_width > request.dimension_height:
            aspect = "16:9"
        elif request.dimension_width == request.dimension_height:
            aspect = "1:1"
        logger.info(f"[AIVideoGen] Using v3 Digital Twin API: avatar={dt_avatar_id}, motion={motion[:80]}")
        video_id = await heygen.generate_video_v3(
            avatar_id=dt_avatar_id,
            script=request.custom_script or request.product_description or f"{request.product_name}の紹介",
            audio_url=audio_url,
            motion_prompt=motion,
            resolution="1080p",
            aspect_ratio=aspect,
            engine_type="avatar_iv",
            title=title,
        )
    # Mode 1: Custom person image upload
    elif request.person_image_url:
        logger.info(f"[AIVideoGen] Using custom person image: {request.person_image_url[:80]}...")
        talking_photo_id = await heygen.upload_talking_photo(request.person_image_url)
        logger.info(f"[AIVideoGen] Custom person talking photo ID: {talking_photo_id}")
        video_id = await heygen.generate_video(
            talking_photo_id=talking_photo_id,
            audio_url=audio_url,
            dimension=dimension,
            title=title,
        )
    # Mode 2: AitherHub liver
    elif request.avatar_id.startswith("aitherhub:"):
        # AitherHub liver: extract frame from clip video → talking photo → video
        liver_name = request.avatar_id.replace("aitherhub:", "")
        logger.info(f"[AIVideoGen] Using AitherHub liver: {liver_name}")

        # Get latest clip_url for this liver from DB
        clip_video_url = None
        try:
            from sqlalchemy import text as sa_text
            from app.core.db import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                result = await db.execute(sa_text("""
                    SELECT vc.clip_url FROM video_clips vc
                    LEFT JOIN videos v ON v.id = vc.video_id
                    WHERE vc.clip_url IS NOT NULL
                        AND vc.clip_url != ''
                        AND v.original_filename IS NOT NULL
                        AND v.original_filename LIKE :pattern
                    ORDER BY vc.created_at DESC
                    LIMIT 1
                """), {"pattern": f"{liver_name}-%"})
                row = result.fetchone()
                if row:
                    clip_video_url = row.clip_url
        except Exception as db_err:
            logger.warning(f"[AIVideoGen] DB lookup for liver clip failed: {db_err}")

        if not clip_video_url:
            raise Exception(f"No clip video found for liver: {liver_name}")

        # Ensure clip URL has SAS token (public access is disabled on storage account)
        clip_video_url = _ensure_sas_url(clip_video_url)
        logger.info(f"[AIVideoGen] Extracting frame from clip (with SAS): {clip_video_url[:80]}...")

        # Extract frame from video and upload as talking photo
        talking_photo_id = await heygen.upload_talking_photo_from_video(clip_video_url)
        logger.info(f"[AIVideoGen] Talking photo ID: {talking_photo_id}")

        # Generate video with talking photo
        video_id = await heygen.generate_video(
            talking_photo_id=talking_photo_id,
            audio_url=audio_url,
            dimension=dimension,
            title=title,
        )
    else:
        # HeyGen Digital Twin: use pre-registered avatar directly (v2 API, no motion_prompt)
        logger.info(f"[AIVideoGen] Using HeyGen Digital Twin (v2): {request.avatar_id}")
        video_id = await heygen.generate_video_with_avatar(
            avatar_id=request.avatar_id,
            audio_url=audio_url,
            dimension=dimension,
            title=title,
        )

    logger.info(f"[AIVideoGen] HeyGen video started: {video_id}")
    # Determine which polling method to use (v3 vs v1)
    use_v3_polling = bool(request.use_digital_twin_v3 or request.motion_prompt)

    # Poll for completion (max 5 minutes)
    max_wait = 300
    poll_interval = 5
    elapsed = 0

    while elapsed < max_wait:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

        if use_v3_polling:
            status_data = await heygen.get_video_status_v3(video_id)
        else:
            status_data = await heygen.get_video_status(video_id)
        current_status = status_data.get("status", "unknown")

        logger.info(f"[AIVideoGen] Video {video_id}: status={current_status}, elapsed={elapsed}s")

        if current_status == "completed":
            video_url = status_data.get("video_url")
            duration = status_data.get("duration")

            # Re-upload to Azure Blob for persistence
            final_url = video_url
            try:
                import httpx
                async with httpx.AsyncClient(timeout=120) as client:
                    dl_resp = await client.get(video_url)
                    dl_resp.raise_for_status()
                    video_bytes = dl_resp.content

                blob_id = f"ai-video-gen-{uuid.uuid4().hex[:10]}"
                blob_url = await _upload_video_to_blob(video_bytes, blob_id)
                final_url = _ensure_sas_url(blob_url)
                logger.info(f"[AIVideoGen] Video re-uploaded to Azure: {final_url[:60]}...")
            except Exception as dl_err:
                logger.warning(f"[AIVideoGen] Failed to re-upload: {dl_err}, using HeyGen URL")

            return final_url, duration

        elif current_status == "failed":
            error_msg = status_data.get("error", "Unknown error")
            raise Exception(f"HeyGen video generation failed: {error_msg}")

    raise Exception(f"HeyGen video generation timed out after {max_wait}s")


async def _generate_showcase_image(
    person_image_url: str,
    product_image_url: str,
    showcase_description: Optional[str],
    showcase_mode: str = "fullscreen",
) -> str:
    """
    Use AI image generation (GPT-image-1 / gpt-4o) to create a composite image
    where the person is holding/presenting the product based on the user's description.
    
    This composite image is then used as the talking photo for HeyGen,
    so the person in the video appears to be interacting with the product.
    
    Returns: URL of the generated composite image (uploaded to Azure Blob with SAS).
    """
    import httpx
    import base64
    import asyncio
    from app.services.storage_service import CONNECTION_STRING, CONTAINER_NAME
    from azure.storage.blob import BlobServiceClient, ContentSettings
    from app.services.storage_service import generate_read_sas_from_url

    logger.info(f"[AIVideoGen:Showcase] Generating AI composite image...")
    logger.info(f"[AIVideoGen:Showcase] Person: {person_image_url[:60]}...")
    logger.info(f"[AIVideoGen:Showcase] Product: {product_image_url[:60]}...")
    logger.info(f"[AIVideoGen:Showcase] Description: {showcase_description}")

    # Ensure product_image_url has SAS token if it's a blob URL
    from app.api.v1.endpoints.digital_human import _ensure_sas_url
    if "blob.core.windows.net" in product_image_url and "?" not in product_image_url:
        product_image_url = _ensure_sas_url(product_image_url)
    if "blob.core.windows.net" in person_image_url and "?" not in person_image_url:
        person_image_url = _ensure_sas_url(person_image_url)

    # Download both images and convert to base64
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        person_resp = await client.get(person_image_url)
        person_resp.raise_for_status()
        person_bytes = person_resp.content
        # Detect content type (OpenAI accepts png, webp, jpg)
        person_ct = person_resp.headers.get("content-type", "image/jpeg")
        if "png" in person_ct:
            person_ext = "png"
        elif "webp" in person_ct:
            person_ext = "webp"
        else:
            person_ext = "jpg"

        product_resp = await client.get(product_image_url)
        product_resp.raise_for_status()
        product_bytes = product_resp.content
        product_ct = product_resp.headers.get("content-type", "image/jpeg")
        if "png" in product_ct:
            product_ext = "png"
        elif "webp" in product_ct:
            product_ext = "webp"
        else:
            product_ext = "jpg"

    # Build the prompt for image generation
    # Following OpenAI's official prompting guide for identity preservation:
    # - Structure: background/scene → subject → key details → constraints
    # - Lock identity: explicitly state what must NOT change
    # - Specify pose, gaze, object interactions clearly
    # - Use "photorealistic" for realistic mode
    mode_instructions = {
        "overlay": (
            "Create a photorealistic portrait-style image. "
            "The person from image 1 is the main subject, shown from waist up, facing the camera with a natural smile. "
            "The product from image 2 is placed as a small overlay element in the upper-right corner of the frame, "
            "clearly visible but not dominating the composition."
        ),
        "split": (
            "Create a photorealistic split-screen composition. "
            "Left half: the person from image 1, shown from waist up, facing slightly right toward the product, with a natural confident expression. "
            "Right half: the product from image 2, displayed prominently on a clean surface with soft studio lighting. "
            "Both halves share consistent lighting direction."
        ),
        "fullscreen": (
            "Create a photorealistic product presentation image. "
            "The person from image 1 is holding the product from image 2 in their hands, presenting it toward the camera. "
            "The person's hands are naturally gripping or cradling the product at chest height. "
            "The person has a natural, confident smile and is looking at the camera. "
            "The product is clearly visible and recognizable, occupying a prominent area of the frame."
        ),
    }
    base_instruction = mode_instructions.get(showcase_mode, mode_instructions["fullscreen"])

    # User's custom description takes priority but we still add identity lock
    identity_lock = (
        "\n\nIDENTITY LOCK (CRITICAL - DO NOT VIOLATE):\n"
        "- The person's face MUST exactly match image 1: same facial structure, eyes, nose, lips, skin tone, hair style, and proportions.\n"
        "- Do NOT alter, idealize, or change any facial features. Preserve real texture and imperfections.\n"
        "- The product MUST exactly match image 2: same shape, color, label, packaging, and proportions.\n"
        "- Do NOT add text, watermarks, or logos that are not in the original images.\n"
    )
    style_instruction = (
        "\nSTYLE:\n"
        "- Photorealistic, natural lighting (soft diffuse studio light or natural daylight).\n"
        "- Clean, simple background (solid light gray or soft gradient).\n"
        "- Professional product photography aesthetic.\n"
        "- Vertical 9:16 aspect ratio framing.\n"
        "- No cinematic grading, no dramatic filters, no stylization.\n"
    )

    if showcase_description and showcase_description.strip():
        composition_prompt = (
            f"Using the two reference images provided:\n"
            f"- Image 1: reference person (use their exact face and appearance)\n"
            f"- Image 2: reference product (use its exact appearance)\n\n"
            f"ACTION: {showcase_description}\n\n"
            f"POSE & INTERACTION: {base_instruction}"
            f"{identity_lock}"
            f"{style_instruction}"
        )
    else:
        composition_prompt = (
            f"Using the two reference images provided:\n"
            f"- Image 1: reference person (use their exact face and appearance)\n"
            f"- Image 2: reference product (use its exact appearance)\n\n"
            f"POSE & INTERACTION: {base_instruction}"
            f"{identity_lock}"
            f"{style_instruction}"
        )

    # Use OpenAI API for image editing
    # gpt-image-2 is recommended for identity-sensitive edits and compositing
    openai_key = os.getenv("OPENAI_API_KEY", "")
    if not openai_key:
        raise Exception("OPENAI_API_KEY not configured for image generation")
    from openai import OpenAI as SyncOpenAI
    import io
    logger.info(f"[AIVideoGen:Showcase] Calling OpenAI images.edit (gpt-image-2) with reference images...")
    logger.info(f"[AIVideoGen:Showcase] Prompt: {composition_prompt[:200]}...")

    def _call_openai_image_edit():
        """Sync call wrapped for asyncio.to_thread to avoid blocking event loop."""
        client = SyncOpenAI(api_key=openai_key)
        # Prepare image files as BytesIO with .name attribute
        person_file = io.BytesIO(person_bytes)
        person_file.name = f"person.{person_ext}"
        product_file = io.BytesIO(product_bytes)
        product_file.name = f"product.{product_ext}"
        # Try gpt-image-2 first (best for identity preservation)
        # Fall back to gpt-image-1 if gpt-image-2 is not available
        try:
            return client.images.edit(
                model="gpt-image-2",
                image=[person_file, product_file],
                prompt=composition_prompt,
                quality="high",
                n=1,
                size="1024x1536",  # Vertical (close to 9:16)
            )
        except Exception as e2:
            logger.warning(f"[AIVideoGen:Showcase] gpt-image-2 failed ({e2}), falling back to gpt-image-1")
            # Reset file positions for retry
            person_file.seek(0)
            product_file.seek(0)
            return client.images.edit(
                model="gpt-image-1",
                image=[person_file, product_file],
                prompt=composition_prompt,
                n=1,
                size="1024x1536",
            )

    # Run sync OpenAI call in thread pool to avoid blocking
    response = await asyncio.to_thread(_call_openai_image_edit)

    # Get the generated image
    if not response.data:
        raise Exception("Image generation returned no data")

    image_data = response.data[0]
    if image_data.b64_json:
        generated_bytes = base64.b64decode(image_data.b64_json)
    elif image_data.url:
        # Fallback: download from URL if b64_json not available
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as dl_client:
            dl_resp = await dl_client.get(image_data.url)
            dl_resp.raise_for_status()
            generated_bytes = dl_resp.content
    else:
        raise Exception("Image generation returned neither b64_json nor url")

    logger.info(f"[AIVideoGen:Showcase] Image generated ({len(generated_bytes)} bytes)")

    # Upload to Azure Blob
    if not CONNECTION_STRING:
        raise Exception("Azure storage not configured")

    blob_service = BlobServiceClient.from_connection_string(CONNECTION_STRING)
    blob_name = f"ai-video-gen/showcase/{uuid.uuid4().hex}.png"
    blob_client = blob_service.get_blob_client(container=CONTAINER_NAME, blob=blob_name)
    blob_client.upload_blob(
        generated_bytes,
        overwrite=True,
        content_settings=ContentSettings(content_type="image/png"),
    )
    blob_url = blob_client.url
    signed_url = generate_read_sas_from_url(blob_url, expires_hours=24)

    logger.info(f"[AIVideoGen:Showcase] Composite image uploaded: {signed_url[:60]}...")
    return signed_url


async def _composite_showcase(
    video_url: str,
    product_image_url: str,
    showcase_mode: str,
    showcase_description: Optional[str],
    duration_sec: float,
) -> str:
    """
    Composite product image onto the generated video using FFmpeg.
    
    Modes:
      - overlay: Product image shown as picture-in-picture (top-right corner, ~30% width)
      - split: Left half = person video, right half = product image
      - fullscreen: Product image shown fullscreen for middle 30% of video duration
    
    Returns the new video URL (uploaded to Azure Blob).
    """
    import subprocess
    import tempfile
    import httpx
    from app.api.v1.endpoints.digital_human import _upload_video_to_blob, _ensure_sas_url

    logger.info(f"[AIVideoGen:Showcase] Mode={showcase_mode}, image={product_image_url[:60]}...")

    with tempfile.TemporaryDirectory() as tmp_dir:
        video_path = os.path.join(tmp_dir, "input.mp4")
        image_path = os.path.join(tmp_dir, "product.png")
        output_path = os.path.join(tmp_dir, "output.mp4")

        # Download video
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.get(video_url)
            resp.raise_for_status()
            with open(video_path, "wb") as f:
                f.write(resp.content)
            logger.info(f"[AIVideoGen:Showcase] Video downloaded: {len(resp.content)} bytes")

        # Download product image
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(product_image_url)
            resp.raise_for_status()
            with open(image_path, "wb") as f:
                f.write(resp.content)
            logger.info(f"[AIVideoGen:Showcase] Product image downloaded: {len(resp.content)} bytes")

        # Get video dimensions
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0",
            video_path,
        ]
        probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
        try:
            vw, vh = [int(x) for x in probe_result.stdout.strip().split(",")]
        except (ValueError, IndexError):
            vw, vh = 720, 1280  # Default vertical video dimensions
        logger.info(f"[AIVideoGen:Showcase] Video dimensions: {vw}x{vh}")

        # Build FFmpeg command based on mode
        if showcase_mode == "overlay":
            # Picture-in-picture: product image in top-right corner, ~30% of video width
            # Show from 20% to 80% of video duration
            overlay_w = int(vw * 0.30)
            margin = int(vw * 0.03)
            start_time = duration_sec * 0.15
            end_time = duration_sec * 0.85
            ffmpeg_cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-i", image_path,
                "-filter_complex",
                f"[1:v]scale={overlay_w}:-1,format=rgba,"
                f"fade=in:st={start_time}:d=0.5:alpha=1,"
                f"fade=out:st={end_time - 0.5}:d=0.5:alpha=1[ovrl];"
                f"[0:v][ovrl]overlay={vw - overlay_w - margin}:{margin}:"
                f"enable='between(t,{start_time},{end_time})'[outv]",
                "-map", "[outv]", "-map", "0:a",
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "copy",
                "-movflags", "+faststart",
                output_path,
            ]

        elif showcase_mode == "split":
            # Split screen: left = video (50%), right = product image (50%)
            # The video is resized to fill left half, image fills right half
            half_w = vw // 2
            ffmpeg_cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-i", image_path,
                "-filter_complex",
                f"[0:v]scale={half_w}:{vh}:force_original_aspect_ratio=increase,"
                f"crop={half_w}:{vh}[left];"
                f"[1:v]scale={half_w}:{vh}:force_original_aspect_ratio=increase,"
                f"crop={half_w}:{vh}[right];"
                f"[left][right]hstack=inputs=2[outv]",
                "-map", "[outv]", "-map", "0:a",
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "copy",
                "-movflags", "+faststart",
                output_path,
            ]

        elif showcase_mode == "fullscreen":
            # Fullscreen product image shown for middle 30% of video
            # Video → product image → video transition
            img_start = duration_sec * 0.35
            img_end = duration_sec * 0.65
            ffmpeg_cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-loop", "1", "-i", image_path,
                "-filter_complex",
                f"[1:v]scale={vw}:{vh}:force_original_aspect_ratio=increase,"
                f"crop={vw}:{vh},format=yuv420p,"
                f"fade=in:st=0:d=0.3,fade=out:st={img_end - img_start - 0.3}:d=0.3[img];"
                f"[0:v][img]overlay=0:0:"
                f"enable='between(t,{img_start},{img_end})'[outv]",
                "-map", "[outv]", "-map", "0:a",
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "copy",
                "-movflags", "+faststart",
                "-shortest",
                output_path,
            ]
        else:
            logger.warning(f"[AIVideoGen:Showcase] Unknown mode: {showcase_mode}, skipping")
            return video_url

        # Run FFmpeg
        logger.info(f"[AIVideoGen:Showcase] Running FFmpeg ({showcase_mode})...")
        result = subprocess.run(
            ffmpeg_cmd,
            capture_output=True,
            text=True,
            timeout=180,  # 3 min timeout for compositing
        )

        if result.returncode != 0:
            logger.error(f"[AIVideoGen:Showcase] FFmpeg failed: {result.stderr[-500:]}")
            raise Exception(f"FFmpeg compositing failed: {result.stderr[-200:]}")

        # Check output file exists and has content
        output_size = os.path.getsize(output_path)
        if output_size < 1000:
            raise Exception(f"FFmpeg output too small: {output_size} bytes")
        logger.info(f"[AIVideoGen:Showcase] Output: {output_size} bytes")

        # Upload composited video to Azure Blob
        with open(output_path, "rb") as f:
            video_bytes = f.read()

        blob_id = f"ai-video-gen-showcase-{uuid.uuid4().hex[:10]}"
        blob_url = await _upload_video_to_blob(video_bytes, blob_id)
        final_url = _ensure_sas_url(blob_url)
        logger.info(f"[AIVideoGen:Showcase] Uploaded: {final_url[:60]}...")

        return final_url


# ──────────────────────────────────────────────
# API Endpoints
# ──────────────────────────────────────────────

@router.post(
    "/generate",
    response_model=VideoGenerateResponse,
    summary="Generate product introduction video",
    description=(
        "Full pipeline: Product info → AI Script → Voice Clone TTS → "
        "Lip-sync Video. Returns job_id for status polling."
    ),
)
async def generate_video(
    body: VideoGenerateRequest,
    background_tasks: BackgroundTasks,
    _auth: bool = Depends(verify_admin_key),
):
    """Start the AI video generation pipeline."""
    job_id = f"avgen-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    _jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "created_at": now,
        "updated_at": now,
        "product_name": body.product_name,
        "avatar_id": body.avatar_id,
        "language": body.language,
        "tone": body.tone,
        "script": None,
        "audio_url": None,
        "video_url": None,
        "video_duration_sec": None,
        "error": None,
        "error_step": None,
    }

    # Persist to DB
    try:
        from app.core.db import AsyncSessionLocal
        async with AsyncSessionLocal() as db_session:
            from app.models.orm.ai_video_gen_job import AiVideoGenJob
            db_job = AiVideoGenJob(
                job_id=job_id,
                status="queued",
                progress=0,
                product_name=body.product_name,
                product_description=body.product_description,
                product_image_url=body.product_image_url,
                product_page_url=body.product_page_url,
                product_price=body.product_price,
                avatar_id=body.avatar_id,
                voice_id=body.voice_id,
                tone=body.tone,
                language=body.language,
                duration_seconds=body.duration_seconds,
                benefits=body.benefits,
                target_audience=body.target_audience,
                custom_script=body.custom_script,
                showcase_mode=body.showcase_mode,
                showcase_description=body.showcase_description,
                person_image_url=body.person_image_url,
            )
            db_session.add(db_job)
            await db_session.commit()
    except Exception as db_err:
        logger.warning(f"[AIVideoGen:{job_id}] DB persist failed (non-fatal): {db_err}")

    # Get DB URL for background task
    db_url = os.getenv("DATABASE_URL", "")

    # Run pipeline in background
    background_tasks.add_task(_run_pipeline, job_id, body, db_url)

    logger.info(
        f"[AIVideoGen] Job {job_id} queued: product={body.product_name}, "
        f"avatar={body.avatar_id}, duration={body.duration_seconds}s"
    )

    return VideoGenerateResponse(
        success=True,
        job_id=job_id,
        status="queued",
        message=f"動画生成を開始しました。ジョブID: {job_id}",
    )


@router.get(
    "/status/{job_id}",
    response_model=JobStatusResponse,
    summary="Check video generation status",
)
async def get_job_status(
    job_id: str,
    _auth: bool = Depends(verify_admin_key),
):
    """Get the current status of a video generation job."""
    job = _jobs.get(job_id)
    if not job:
        # Try loading from DB
        db_jobs = await _load_jobs_from_db()
        for dj in db_jobs:
            if dj["job_id"] == job_id:
                job = dj
                break
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return JobStatusResponse(**job)


@router.get(
    "/jobs",
    summary="List all video generation jobs",
)
async def list_jobs(
    limit: int = Query(20, ge=1, le=100),
    _auth: bool = Depends(verify_admin_key),
):
    """List recent video generation jobs (merged: in-memory + DB)."""
    # Merge in-memory (active) and DB (historical)
    db_jobs = await _load_jobs_from_db()
    merged: Dict[str, Dict[str, Any]] = {}
    for dj in db_jobs:
        merged[dj["job_id"]] = dj
    # In-memory overrides DB (fresher state)
    for jid, jdata in _jobs.items():
        merged[jid] = jdata

    jobs_list = sorted(
        merged.values(),
        key=lambda j: j.get("created_at", ""),
        reverse=True,
    )[:limit]

    return {
        "jobs": [JobStatusResponse(**j) for j in jobs_list],
        "total": len(merged),
    }


@router.get(
    "/avatars",
    summary="List available livers/avatars for video generation",
)
async def list_avatars(
    _auth: bool = Depends(verify_admin_key),
    db: AsyncSession = Depends(get_db),
):
    """
    List available livers for video generation.
    
    Priority:
      1. AitherHub DB livers — real faces from live streams (uses talking photo)
         Identified by video_filename pattern: {tiktok_username}-{date}-{time}.mp4
      2. HeyGen Digital Twins — pre-registered avatars (direct generation)
    
    AitherHub livers are shown first as they represent real company livers.
    
    Performance: Uses in-memory cache (30min TTL) to avoid repeated DB queries
    and SAS token generation on every request.
    """
    global _avatars_cache, _avatars_cache_time

    # Return cached result if still fresh
    now = time.time()
    if _avatars_cache and (now - _avatars_cache_time) < _AVATARS_CACHE_TTL:
        logger.info(f"[AIVideoGen] Returning cached avatars ({_avatars_cache['total']} items)")
        return _avatars_cache

    result = []

    # Run DB query and HeyGen API in parallel
    async def _fetch_aitherhub_livers():
        """Fetch AitherHub livers from DB with SAS-signed preview URLs + thumbnail generation."""
        livers = []
        try:
            liver_sql = text("""
                WITH liver_clips AS (
                    SELECT 
                        REGEXP_REPLACE(v.original_filename, '-[0-9]{8}-[0-9]{4}\\.mp4$', '') AS liver_name,
                        vc.clip_url,
                        vc.created_at,
                        ROW_NUMBER() OVER (
                            PARTITION BY REGEXP_REPLACE(v.original_filename, '-[0-9]{8}-[0-9]{4}\\.mp4$', '')
                            ORDER BY vc.created_at DESC
                        ) AS rn
                    FROM video_clips vc
                    LEFT JOIN videos v ON v.id = vc.video_id
                    WHERE vc.clip_url IS NOT NULL
                        AND vc.clip_url != ''
                        AND v.original_filename IS NOT NULL
                        AND v.original_filename != ''
                        AND v.original_filename ~ '^[^-]+-[0-9]{8}-[0-9]{4}\\.mp4$'
                )
                SELECT liver_name, clip_url, 
                       (SELECT COUNT(*) FROM video_clips vc2 
                        LEFT JOIN videos v2 ON v2.id = vc2.video_id
                        WHERE vc2.clip_url IS NOT NULL
                        AND v2.original_filename LIKE liver_clips.liver_name || '-%') AS clip_count
                FROM liver_clips
                WHERE rn = 1
                ORDER BY clip_count DESC
            """)
            liver_result = await db.execute(liver_sql)
            liver_rows = liver_result.fetchall()

            from app.services.storage_service import generate_read_sas_from_url

            for row in liver_rows:
                liver_name = row.liver_name.strip() if row.liver_name else ""
                if not liver_name:
                    continue
                avatar_id = f"aitherhub:{liver_name}"
                clip_url = row.clip_url
                signed_url = generate_read_sas_from_url(clip_url, expires_hours=2) if clip_url else None
                preview_url = signed_url or clip_url
                
                # Generate thumbnail URL: replace .mp4 extension with _thumb.jpg in blob path
                thumb_url = None
                if clip_url:
                    thumb_blob_url = clip_url.rsplit('.', 1)[0] + '_thumb.jpg' if '.' in clip_url else None
                    if thumb_blob_url:
                        thumb_url = generate_read_sas_from_url(thumb_blob_url, expires_hours=2)
                
                livers.append(AvatarInfo(
                    avatar_id=avatar_id,
                    name=liver_name,
                    preview_image_url=preview_url,
                    thumbnail_image_url=thumb_url,
                    avatar_type="talking_photo",
                    face_image_url=clip_url,
                    source="aitherhub",
                ))
            logger.info(f"[AIVideoGen] Found {len(livers)} aitherhub livers from DB")
        except Exception as e:
            logger.error(f"[AIVideoGen] Failed to query aitherhub livers: {e}", exc_info=True)
        return livers

    async def _fetch_heygen_twins():
        """Fetch HeyGen Digital Twin avatars."""
        twins = []
        try:
            from app.services.heygen_service import get_heygen_service
            heygen = get_heygen_service()
            if heygen.api_key:
                avatars = await heygen.list_avatars(custom_only=True)
                for av in avatars:
                    twins.append(AvatarInfo(
                        avatar_id=av.get("avatar_id", ""),
                        name=av.get("avatar_name", "Unknown"),
                        preview_image_url=av.get("preview_image_url") or av.get("thumbnail_url"),
                        avatar_type=av.get("avatar_type", "full"),
                        source="heygen",
                    ))
                logger.info(f"[AIVideoGen] Added {len(twins)} HeyGen Digital Twin avatars")
        except Exception as e:
            logger.warning(f"[AIVideoGen] HeyGen avatars unavailable: {e}")
        return twins

    # Execute both fetches concurrently
    aitherhub_livers, heygen_twins = await asyncio.gather(
        _fetch_aitherhub_livers(),
        _fetch_heygen_twins(),
    )
    result = aitherhub_livers + heygen_twins

    # Cache the result
    response = {"avatars": result, "total": len(result)}
    _avatars_cache = response
    _avatars_cache_time = time.time()
    logger.info(f"[AIVideoGen] Cached {len(result)} avatars (TTL={_AVATARS_CACHE_TTL}s)")

    return response


# ──────────────────────────────────────────────
# POST /ai-video-generator/generate-thumbnails
# ──────────────────────────────────────────────

@router.post("/generate-thumbnails")
async def generate_thumbnails(
    background_tasks: BackgroundTasks,
    _key: str = Depends(verify_admin_key),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate JPG thumbnail images from liver video clips.
    Uses ffmpeg to extract the first frame, uploads to Azure Blob as {clip_name}_thumb.jpg.
    Only generates thumbnails that don't already exist.
    """
    import subprocess
    import tempfile
    import httpx
    from app.services.storage_service import (
        generate_read_sas_from_url,
        CONNECTION_STRING,
        ACCOUNT_NAME,
        CONTAINER_NAME,
    )
    from azure.storage.blob import BlobServiceClient, ContentSettings

    # Get all liver clips
    liver_sql = text("""
        WITH liver_clips AS (
            SELECT 
                REGEXP_REPLACE(v.original_filename, '-[0-9]{8}-[0-9]{4}\\.mp4$', '') AS liver_name,
                vc.clip_url,
                ROW_NUMBER() OVER (
                    PARTITION BY REGEXP_REPLACE(v.original_filename, '-[0-9]{8}-[0-9]{4}\\.mp4$', '')
                    ORDER BY vc.created_at DESC
                ) AS rn
            FROM video_clips vc
            LEFT JOIN videos v ON v.id = vc.video_id
            WHERE vc.clip_url IS NOT NULL
                AND vc.clip_url != ''
                AND v.original_filename IS NOT NULL
                AND v.original_filename != ''
                AND v.original_filename ~ '^[^-]+-[0-9]{8}-[0-9]{4}\\.mp4$'
        )
        SELECT liver_name, clip_url FROM liver_clips WHERE rn = 1
    """)
    result = await db.execute(liver_sql)
    rows = result.fetchall()

    generated = []
    skipped = []
    errors = []

    blob_service = BlobServiceClient.from_connection_string(CONNECTION_STRING)
    container_client = blob_service.get_container_client(CONTAINER_NAME)

    for row in rows:
        liver_name = row.liver_name.strip() if row.liver_name else ""
        clip_url = row.clip_url
        if not clip_url or not liver_name:
            continue

        # Determine thumbnail blob name
        try:
            parts = clip_url.split("/")
            container_idx = parts.index(CONTAINER_NAME) if CONTAINER_NAME in parts else -1
            if container_idx < 0:
                continue
            blob_name = "/".join(parts[container_idx + 1:])
            if "?" in blob_name:
                blob_name = blob_name.split("?", 1)[0]
            from urllib.parse import unquote
            blob_name = unquote(blob_name)
            thumb_blob_name = blob_name.rsplit('.', 1)[0] + '_thumb.jpg'

            # Check if thumbnail already exists
            thumb_blob_client = container_client.get_blob_client(thumb_blob_name)
            try:
                thumb_blob_client.get_blob_properties()
                skipped.append(liver_name)
                continue  # Already exists
            except Exception:
                pass  # Doesn't exist, generate it

            # Download video to temp file, extract first frame with ffmpeg
            signed_url = generate_read_sas_from_url(clip_url, expires_hours=1)
            if not signed_url:
                errors.append(f"{liver_name}: no SAS URL")
                continue

            with tempfile.NamedTemporaryFile(suffix='.mp4', delete=True) as tmp_video:
                with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp_thumb:
                    tmp_thumb_path = tmp_thumb.name

                # Download video (first 5MB is enough for first frame)
                async with httpx.AsyncClient(timeout=30) as client:
                    async with client.stream("GET", signed_url) as resp:
                        if resp.status_code != 200:
                            errors.append(f"{liver_name}: download failed ({resp.status_code})")
                            continue
                        downloaded = 0
                        with open(tmp_video.name, 'wb') as f:
                            async for chunk in resp.aiter_bytes(chunk_size=65536):
                                f.write(chunk)
                                downloaded += len(chunk)
                                if downloaded > 5 * 1024 * 1024:  # 5MB limit
                                    break

                # Extract first frame with ffmpeg
                proc = subprocess.run(
                    ["ffmpeg", "-y", "-i", tmp_video.name, "-vframes", "1",
                     "-q:v", "2", "-vf", "scale=320:-1", tmp_thumb_path],
                    capture_output=True, timeout=15
                )
                if proc.returncode != 0:
                    errors.append(f"{liver_name}: ffmpeg failed")
                    import os as _os
                    if _os.path.exists(tmp_thumb_path):
                        _os.unlink(tmp_thumb_path)
                    continue

                # Upload thumbnail to blob
                import os as _os
                if _os.path.exists(tmp_thumb_path) and _os.path.getsize(tmp_thumb_path) > 0:
                    with open(tmp_thumb_path, 'rb') as f:
                        thumb_blob_client.upload_blob(
                            f, overwrite=True,
                            content_settings=ContentSettings(content_type="image/jpeg")
                        )
                    generated.append(liver_name)
                    _os.unlink(tmp_thumb_path)
                else:
                    errors.append(f"{liver_name}: empty thumbnail")
                    if _os.path.exists(tmp_thumb_path):
                        _os.unlink(tmp_thumb_path)

        except Exception as e:
            errors.append(f"{liver_name}: {str(e)[:100]}")

    # Invalidate avatar cache so next request picks up thumbnails
    global _avatars_cache, _avatars_cache_time
    _avatars_cache = None
    _avatars_cache_time = 0

    return {
        "status": "ok",
        "generated": generated,
        "skipped": skipped,
        "errors": errors,
        "total_processed": len(rows),
    }


# ──────────────────────────────────────────────
# POST /ai-video-generator/analyze-product
# ──────────────────────────────────────────────

@router.post("/analyze-product")
async def analyze_product_endpoint(
    image: UploadFile = File(None, description="商品写真（アップロード）"),
    image_url: str = Query(None, description="商品画像URL"),
    page_url: str = Query(None, description="商品ページURL（楽天/Amazon等）"),
    _: bool = Depends(verify_admin_key),
):
    """
    商品情報を自動解析するエンドポイント。
    
    3つの入力方法に対応:
    1. 商品写真アップロード（multipart/form-data）
    2. 商品画像URL（query parameter）
    3. 商品ページURL（query parameter）→ スクレイピング + OG画像解析
    
    Returns: { success, product: { name, description, price, brand, notes } }
    """
    import httpx
    # Determine the image source
    actual_image_url = image_url
    page_context = ""

    # If page_url provided, scrape it for context and OG image
    if page_url:
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as http:
                resp = await http.get(page_url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                })
                if resp.status_code == 200:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(resp.text, "html.parser")
                    title = soup.title.string if soup.title else ""
                    meta_desc = soup.find("meta", attrs={"name": "description"})
                    desc = meta_desc["content"] if meta_desc and meta_desc.get("content") else ""
                    # Get OG image
                    if not actual_image_url:
                        og_img = soup.find("meta", attrs={"property": "og:image"})
                        if og_img and og_img.get("content"):
                            actual_image_url = og_img["content"]
                    # Get price from common patterns
                    price_el = soup.find(class_=re.compile(r"price|Price"))
                    price_text = price_el.get_text(strip=True)[:50] if price_el else ""
                    page_context = f"\nページタイトル: {title}\nページ説明: {desc[:500]}"
                    if price_text:
                        page_context += f"\n検出価格: {price_text}"
        except Exception as e:
            logger.warning(f"[AIVideoGen] Page scrape failed: {e}")

    # Build the image content for GPT-4 Vision
    data_url = None
    _uploaded_product_blob_url = None  # Will be set if we upload to blob
    if image and image.filename:
        # Direct upload
        content = await image.read()
        if len(content) > 20 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Image too large. Max 20MB.")
        content_type = image.content_type or "image/jpeg"
        if content_type not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
            content_type = "image/jpeg"
        b64_image = base64.b64encode(content).decode("utf-8")
        data_url = f"data:{content_type};base64,{b64_image}"
        # Upload product image to blob so it can be used by showcase pipeline
        try:
            from azure.storage.blob import BlobServiceClient, ContentSettings
            from app.services.storage_service import CONNECTION_STRING, CONTAINER_NAME, generate_read_sas_from_url
            if CONNECTION_STRING:
                ext = image.filename.rsplit(".", 1)[-1].lower() if image.filename and "." in image.filename else "jpg"
                blob_name = f"ai-video-gen/products/{uuid.uuid4().hex}.{ext}"
                blob_service = BlobServiceClient.from_connection_string(CONNECTION_STRING)
                blob_client = blob_service.get_blob_client(container=CONTAINER_NAME, blob=blob_name)
                blob_client.upload_blob(
                    content, overwrite=True,
                    content_settings=ContentSettings(content_type=content_type),
                )
                _uploaded_product_blob_url = generate_read_sas_from_url(blob_client.url, expires_hours=24)
                logger.info(f"[AIVideoGen] Product image uploaded to blob: {_uploaded_product_blob_url[:60]}...")
        except Exception as upload_err:
            logger.warning(f"[AIVideoGen] Product image blob upload failed (non-fatal): {upload_err}")
    elif actual_image_url:
        # V2.34.6: Download image and convert to base64
        # OpenAI often can't download external URLs (returns invalid_image_url error)
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
                img_resp = await http.get(actual_image_url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                })
                if img_resp.status_code == 200:
                    img_content = img_resp.content
                    if len(img_content) > 20 * 1024 * 1024:
                        raise HTTPException(status_code=400, detail="Image too large. Max 20MB.")
                    ct = img_resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
                    if ct not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
                        ct = "image/jpeg"
                    b64_image = base64.b64encode(img_content).decode("utf-8")
                    data_url = f"data:{ct};base64,{b64_image}"
                    logger.info(f"[AIVideoGen] Downloaded image from URL, size={len(img_content)} bytes")
                else:
                    logger.warning(f"[AIVideoGen] Failed to download image: HTTP {img_resp.status_code}")
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"[AIVideoGen] Image download failed: {e}, will try URL directly")

    if not data_url and not actual_image_url and not page_context:
        raise HTTPException(
            status_code=400,
            detail="商品写真、画像URL、または商品ページURLのいずれかを指定してください。"
        )

    # Call GPT-4 Vision
    client = _get_openai_client()

    system_msg = (
        "You are a product information extraction assistant for live commerce. "
        "Analyze the product photo/info and extract structured information. "
        "Return ONLY a valid JSON object with these fields:\n"
        '- "name": product name (string, required)\n'
        '- "description": product description including features, key selling points, ingredients/effects (string, 2-3 sentences)\n'
        '- "price": price if visible (string, include currency symbol)\n'
        '- "brand": brand name if visible (string)\n'
        '- "notes": additional sales points, target audience, or notable features (string)\n\n'
        "Detect the language of the product and respond in that language. "
        "If the product is Japanese, respond in Japanese. "
        "Do NOT wrap the JSON in markdown code blocks."
    )

    user_content = []
    user_text = "この商品の情報を解析して、JSON形式で返してください。"
    if page_context:
        user_text += page_context
    user_content.append({"type": "text", "text": user_text})

    if data_url:
        user_content.append({
            "type": "image_url",
            "image_url": {"url": data_url, "detail": "high"},
        })
    elif actual_image_url:
        user_content.append({
            "type": "image_url",
            "image_url": {"url": actual_image_url, "detail": "high"},
        })

    try:
        response = await client.chat.completions.create(
            model=_get_openai_model(),
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_content},
            ],
            max_completion_tokens=1000,
        )

        raw_text = response.choices[0].message.content.strip()

        # Parse JSON
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw_text)
        if json_match:
            raw_text = json_match.group(1).strip()

        try:
            product_data = json.loads(raw_text)
        except json.JSONDecodeError:
            json_match2 = re.search(r'\{[\s\S]*\}', raw_text)
            if json_match2:
                product_data = json.loads(json_match2.group())
            else:
                raise HTTPException(
                    status_code=500,
                    detail="AIが商品情報を解析できませんでした。別の画像をお試しください。"
                )

        return {
            "success": True,
            "product": {
                "name": product_data.get("name", ""),
                "description": product_data.get("description", ""),
                "price": str(product_data.get("price", "")),
                "brand": product_data.get("brand", ""),
                "notes": product_data.get("notes", ""),
            },
            "image_url": actual_image_url or _uploaded_product_blob_url or None,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AIVideoGen] Product analysis failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"商品解析に失敗しました: {str(e)}")



# ──────────────────────────────────────────────
# GET /ai-video-generator/voices — Voice list for selection
# ──────────────────────────────────────────────

class VoiceInfo(BaseModel):
    """Voice information for selection."""
    voice_id: str
    name: str
    language: Optional[str] = None
    gender: Optional[str] = None
    preview_url: Optional[str] = None
    category: str = "premade"  # premade, cloned, professional


@router.get(
    "/voices",
    summary="List available voices for video generation",
)
async def list_voices(
    language: Optional[str] = Query(None, description="Filter by language (ja, en, zh, ko, etc.)"),
    _auth: bool = Depends(verify_admin_key),
):
    """
    List available ElevenLabs voices for video generation.
    Returns both premade and cloned voices.
    """
    try:
        from app.services.elevenlabs_tts_service import ElevenLabsTTSService
        tts = ElevenLabsTTSService()
        voices_raw = await tts.list_voices()

        voices = []
        for v in voices_raw:
            voice_lang = None
            labels = v.get("labels", {})
            if isinstance(labels, dict):
                voice_lang = labels.get("language")
                gender = labels.get("gender")
            else:
                gender = None

            # Filter by language if specified
            if language and voice_lang and language.lower() not in (voice_lang or "").lower():
                continue

            voices.append(VoiceInfo(
                voice_id=v.get("voice_id", ""),
                name=v.get("name", "Unknown"),
                language=voice_lang,
                gender=gender,
                preview_url=v.get("preview_url"),
                category=v.get("category", "premade"),
            ))

        return {
            "voices": voices,
            "total": len(voices),
        }
    except Exception as e:
        logger.error(f"[AIVideoGen] Failed to list voices: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"音声一覧の取得に失敗しました: {str(e)}")


# ──────────────────────────────────────────────
# GET /ai-video-generator/languages — Supported languages
# ──────────────────────────────────────────────

SUPPORTED_LANGUAGES = [
    {"code": "ja", "name": "日本語", "name_en": "Japanese"},
    {"code": "en", "name": "English", "name_en": "English"},
    {"code": "zh", "name": "中文", "name_en": "Chinese"},
    {"code": "ko", "name": "한국어", "name_en": "Korean"},
    {"code": "th", "name": "ภาษาไทย", "name_en": "Thai"},
    {"code": "vi", "name": "Tiếng Việt", "name_en": "Vietnamese"},
    {"code": "id", "name": "Bahasa Indonesia", "name_en": "Indonesian"},
    {"code": "ms", "name": "Bahasa Melayu", "name_en": "Malay"},
    {"code": "es", "name": "Español", "name_en": "Spanish"},
    {"code": "fr", "name": "Français", "name_en": "French"},
    {"code": "de", "name": "Deutsch", "name_en": "German"},
    {"code": "pt", "name": "Português", "name_en": "Portuguese"},
    {"code": "ar", "name": "العربية", "name_en": "Arabic"},
    {"code": "hi", "name": "हिन्दी", "name_en": "Hindi"},
]


@router.get(
    "/languages",
    summary="List supported output languages",
)
async def list_languages(
    _auth: bool = Depends(verify_admin_key),
):
    """List all supported languages for video generation."""
    return {
        "languages": SUPPORTED_LANGUAGES,
        "total": len(SUPPORTED_LANGUAGES),
    }


# ──────────────────────────────────────────────
# POST /ai-video-generator/analyze-person — Person photo analysis
# ──────────────────────────────────────────────

@router.post(
    "/analyze-person",
    summary="Analyze person photo for video generation",
)
async def analyze_person_photo(
    image: UploadFile = File(None, description="人物写真（アップロード）"),
    image_url: str = Query(None, description="人物画像URL"),
    _: bool = Depends(verify_admin_key),
):
    """
    人物写真を分析し、外見・表情・動作などの情報を抽出する。
    分析結果は動画生成時のアバター選択や台本生成に活用される。
    
    Returns: { success, analysis: { appearance, expression, style, suggestions } }
    """
    import httpx
    actual_image_url = image_url
    data_url = None
    # Handle file upload
    if image and image.filename:
        content = await image.read()
        import base64 as b64mod
        ext = image.filename.rsplit(".", 1)[-1].lower() if "." in image.filename else "jpg"
        mime = f"image/{ext}" if ext != "jpg" else "image/jpeg"
        data_url = f"data:{mime};base64,{b64mod.b64encode(content).decode()}"
        actual_image_url = data_url
    if not actual_image_url:
        raise HTTPException(status_code=400, detail="画像URLまたはファイルが必要です")
    try:
        # V2.34.6: Use shared Azure OpenAI client (same credentials as ai_clip_generator)
        client = _get_openai_client()

        messages = [
            {
                "role": "system",
                "content": (
                    "あなたは人物分析の専門家です。写真から以下の情報を日本語で分析してください:\n"
                    "1. 外見特徴（髪型、服装、体型、年齢層）\n"
                    "2. 表情・雰囲気（明るい、クール、知的等）\n"
                    "3. スタイル（カジュアル、フォーマル、スポーティ等）\n"
                    "4. 動画生成への提案（この人物に合うトーン、商品カテゴリ、台本スタイル）\n\n"
                    "JSON形式で回答してください:\n"
                    '{"appearance": "...", "expression": "...", "style": "...", "age_range": "...", '
                    '"suggestions": {"tone": "...", "product_categories": [...], "script_style": "..."}}'
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "この人物を分析してください。"},
                    {"type": "image_url", "image_url": {"url": actual_image_url}},
                ],
            },
        ]

        response = await client.chat.completions.create(
            model=_get_openai_model(),
            messages=messages,
            max_completion_tokens=1000,
        )

        analysis_text = response.choices[0].message.content.strip()
        
        # Try to parse as JSON
        try:
            # Remove markdown code block if present
            clean = analysis_text
            if "```json" in clean:
                clean = clean.split("```json")[1].split("```")[0].strip()
            elif "```" in clean:
                clean = clean.split("```")[1].split("```")[0].strip()
            analysis = json.loads(clean)
        except (json.JSONDecodeError, IndexError):
            analysis = {"raw_analysis": analysis_text}

        return {
            "success": True,
            "analysis": analysis,
            "image_url": actual_image_url[:100] + "..." if data_url else actual_image_url,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AIVideoGen] Person analysis failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"人物分析に失敗しました: {str(e)}")


# ──────────────────────────────────────────────
# POST /ai-video-generator/upload-product-photo — Upload product photo to blob
# ──────────────────────────────────────────────
@router.post(
    "/upload-product-photo",
    summary="Upload product photo to blob for showcase pipeline",
)
async def upload_product_photo(
    image: UploadFile = File(..., description="商品写真"),
    _: bool = Depends(verify_admin_key),
):
    """
    商品写真をAzure Blobにアップロードし、URLを返す。
    このURLはgenerate APIのproduct_image_urlフィールドに使用可能。
    """
    try:
        content = await image.read()
        if len(content) > 20 * 1024 * 1024:  # 20MB limit
            raise HTTPException(status_code=400, detail="ファイルサイズは20MB以下にしてください")
        ext = image.filename.rsplit(".", 1)[-1].lower() if image.filename and "." in image.filename else "jpg"
        blob_name = f"ai-video-gen/products/{uuid.uuid4().hex}.{ext}"
        from azure.storage.blob import BlobServiceClient, ContentSettings
        from app.services.storage_service import CONNECTION_STRING, CONTAINER_NAME
        if not CONNECTION_STRING:
            raise HTTPException(status_code=500, detail="Storage not configured")
        blob_service = BlobServiceClient.from_connection_string(CONNECTION_STRING)
        blob_client = blob_service.get_blob_client(container=CONTAINER_NAME, blob=blob_name)
        content_type = image.content_type or f"image/{ext}"
        blob_client.upload_blob(
            content, overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )
        blob_url = blob_client.url
        from app.services.storage_service import generate_read_sas_from_url
        signed_url = generate_read_sas_from_url(blob_url, expires_hours=24)
        return {
            "success": True,
            "url": signed_url,
            "blob_url": blob_url,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AIVideoGen] Product photo upload failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"アップロードに失敗しました: {str(e)}")


# ──────────────────────────────────────────────
# POST /ai-video-generator/upload-person-photo — Upload person photo to blob
# ──────────────────────────────────────────────
@router.post(
    "/upload-person-photo",
    summary="Upload person photo for avatar creation",
)
async def upload_person_photo(
    image: UploadFile = File(..., description="人物写真"),
    _: bool = Depends(verify_admin_key),
):
    """
    人物写真をAzure Blobにアップロードし、URLを返す。
    このURLはgenerate APIのperson_image_urlフィールドに使用可能。
    """
    try:
        content = await image.read()
        if len(content) > 10 * 1024 * 1024:  # 10MB limit
            raise HTTPException(status_code=400, detail="ファイルサイズは10MB以下にしてください")

        ext = image.filename.rsplit(".", 1)[-1].lower() if image.filename and "." in image.filename else "jpg"
        blob_name = f"ai-video-gen/persons/{uuid.uuid4().hex}.{ext}"

        # Use Azure Blob SDK directly to upload (use same container as storage_service)
        from azure.storage.blob import BlobServiceClient, ContentSettings
        from app.services.storage_service import CONNECTION_STRING, CONTAINER_NAME
        if not CONNECTION_STRING:
            raise HTTPException(status_code=500, detail="Storage not configured")

        blob_service = BlobServiceClient.from_connection_string(CONNECTION_STRING)
        blob_client = blob_service.get_blob_client(container=CONTAINER_NAME, blob=blob_name)
        blob_client.upload_blob(
            content,
            overwrite=True,
            content_settings=ContentSettings(content_type=f"image/{ext}"),
        )
        blob_url = blob_client.url

        from app.services.storage_service import generate_read_sas_from_url
        signed_url = generate_read_sas_from_url(blob_url, expires_hours=24)

        return {
            "success": True,
            "url": signed_url,
            "blob_url": blob_url,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AIVideoGen] Person photo upload failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"アップロードに失敗しました: {str(e)}")


# ──────────────────────────────────────────────
# DELETE /ai-video-generator/jobs/{job_id} — Delete a job from history
# ──────────────────────────────────────────────

@router.delete(
    "/jobs/{job_id}",
    summary="Delete a video generation job from history",
)
async def delete_job(
    job_id: str,
    _auth: bool = Depends(verify_admin_key),
):
    """Delete a job from both in-memory and DB."""
    # Remove from in-memory
    _jobs.pop(job_id, None)

    # Remove from DB
    try:
        from app.core.db import AsyncSessionLocal
        from app.models.orm.ai_video_gen_job import AiVideoGenJob
        from sqlalchemy import delete as sa_delete
        async with AsyncSessionLocal() as db_session:
            await db_session.execute(
                sa_delete(AiVideoGenJob).where(AiVideoGenJob.job_id == job_id)
            )
            await db_session.commit()
    except Exception as e:
        logger.warning(f"[AIVideoGen] DB delete failed: {e}")

    return {"success": True, "message": f"Job {job_id} deleted"}


# ──────────────────────────────────────────────
# Custom Persons (我的人物) - Save/List/Delete
# ──────────────────────────────────────────────

@router.get(
    "/custom-persons",
    summary="List all saved custom persons",
)
async def list_custom_persons(
    _: bool = Depends(verify_admin_key),
):
    """Return all saved custom persons ordered by creation date (newest first)."""
    try:
        from app.core.db import AsyncSessionLocal
        from sqlalchemy import text as _text
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                _text("SELECT id, name, image_url, blob_url, thumbnail_url, analysis, created_at FROM custom_persons ORDER BY created_at DESC")
            )
            rows = result.fetchall()
            persons = []
            for row in rows:
                persons.append({
                    "id": row[0],
                    "name": row[1],
                    "image_url": row[2],
                    "blob_url": row[3],
                    "thumbnail_url": row[4],
                    "analysis": row[5],
                    "created_at": row[6].isoformat() if row[6] else None,
                })
            return {"success": True, "persons": persons}
    except Exception as e:
        logger.error(f"[AIVideoGen] List custom persons failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/custom-persons",
    summary="Save a custom person",
)
async def save_custom_person(
    name: str = Query(..., description="人物の名前"),
    image_url: str = Query(..., description="人物画像URL (SAS付き)"),
    blob_url: str = Query(None, description="Blob URL (SASなし)"),
    analysis: str = Query(None, description="分析結果JSON文字列"),
    _: bool = Depends(verify_admin_key),
):
    """Save a custom person to the database for reuse in video generation."""
    try:
        import json as _json
        person_id = f"person-{uuid.uuid4().hex[:12]}"
        analysis_json = None
        if analysis:
            try:
                analysis_json = _json.loads(analysis)
            except (ValueError, TypeError):
                analysis_json = {"raw": analysis}

        from app.core.db import AsyncSessionLocal
        from sqlalchemy import text as _text
        async with AsyncSessionLocal() as db:
            await db.execute(
                _text("""
                    INSERT INTO custom_persons (id, name, image_url, blob_url, analysis, created_at, updated_at)
                    VALUES (:id, :name, :image_url, :blob_url, :analysis, NOW(), NOW())
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name,
                        image_url = EXCLUDED.image_url,
                        blob_url = EXCLUDED.blob_url,
                        analysis = EXCLUDED.analysis,
                        updated_at = NOW()
                """),
                {
                    "id": person_id,
                    "name": name,
                    "image_url": image_url,
                    "blob_url": blob_url or "",
                    "analysis": _json.dumps(analysis_json) if analysis_json else None,
                },
            )
            await db.commit()

        return {
            "success": True,
            "person": {
                "id": person_id,
                "name": name,
                "image_url": image_url,
                "blob_url": blob_url,
                "analysis": analysis_json,
            },
        }
    except Exception as e:
        logger.error(f"[AIVideoGen] Save custom person failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete(
    "/custom-persons/{person_id}",
    summary="Delete a custom person",
)
async def delete_custom_person(
    person_id: str,
    _: bool = Depends(verify_admin_key),
):
    """Delete a saved custom person."""
    try:
        from app.core.db import AsyncSessionLocal
        from sqlalchemy import text as _text
        async with AsyncSessionLocal() as db:
            await db.execute(
                _text("DELETE FROM custom_persons WHERE id = :id"),
                {"id": person_id},
            )
            await db.commit()
        return {"success": True, "message": f"Person {person_id} deleted"}
    except Exception as e:
        logger.error(f"[AIVideoGen] Delete custom person failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────
# DEBUG: Showcase Pipeline Test
# ──────────────────────────────────────────────
@router.get("/debug/showcase-status", summary="Check showcase pipeline status for recent jobs")
async def debug_showcase_status(
    _auth: bool = Depends(verify_admin_key),
):
    """Check if showcase_mode was set and whether the composite image was generated for recent jobs."""
    try:
        from app.core.db import AsyncSessionLocal
        from sqlalchemy import text as _text
        async with AsyncSessionLocal() as db:
            result = await db.execute(_text("""
                SELECT job_id, status, avatar_id, showcase_mode, person_image_url, 
                       product_image_url, error, error_step, created_at
                FROM ai_video_gen_jobs 
                ORDER BY created_at DESC 
                LIMIT 10
            """))
            rows = result.fetchall()
        jobs_info = []
        for row in rows:
            jobs_info.append({
                "job_id": row.job_id,
                "status": row.status,
                "avatar_id": row.avatar_id,
                "showcase_mode": row.showcase_mode,
                "has_person_image": bool(row.person_image_url),
                "person_image_url_preview": (row.person_image_url or "")[:80],
                "has_product_image": bool(row.product_image_url),
                "product_image_url_preview": (row.product_image_url or "")[:80],
                "error": row.error,
                "created_at": str(row.created_at)[:19],
            })
        return {
            "success": True,
            "openai_api_key_set": bool(os.getenv("OPENAI_API_KEY")),
            "recent_jobs": jobs_info,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/debug/test-showcase-image", summary="Test GPT-image-1 showcase composite generation")
async def debug_test_showcase_image(
    person_image_url: str = Query(..., description="Person image URL"),
    product_image_url: str = Query(..., description="Product image URL"),
    showcase_mode: str = Query("fullscreen", description="Showcase mode"),
    _auth: bool = Depends(verify_admin_key),
):
    """Test the showcase image generation pipeline directly."""
    try:
        result_url = await _generate_showcase_image(
            person_image_url=person_image_url,
            product_image_url=product_image_url,
            showcase_description="人物が商品を手に持って笑顔で紹介している",
            showcase_mode=showcase_mode,
        )
        return {
            "success": True,
            "composite_image_url": result_url,
            "message": "Showcase image generated successfully",
        }
    except Exception as e:
        import traceback
        return {
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc(),
        }


# ──────────────────────────────────────────────
# Digital Twin Management Endpoints
# ──────────────────────────────────────────────

class DigitalTwinCreateRequest(BaseModel):
    """Request to create a Digital Twin from training video."""
    name: str = Field(..., max_length=100, description="Digital Twin名前")
    video_url: str = Field(..., description="トレーニング動画URL（2-5分、正面、良い照明）")


class DigitalTwinCreateResponse(BaseModel):
    """Response from Digital Twin creation."""
    success: bool
    look_id: Optional[str] = None
    group_id: Optional[str] = None
    name: Optional[str] = None
    consent_url: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None


@router.post(
    "/digital-twin/create",
    response_model=DigitalTwinCreateResponse,
    summary="Create a Digital Twin from training video",
)
async def create_digital_twin(
    request: DigitalTwinCreateRequest,
    _auth: bool = Depends(verify_admin_key),
):
    """
    Create a Digital Twin avatar from a training video.
    
    Requirements for training video:
    - 2-5 minutes of the person speaking to camera
    - Good lighting, simple background
    - Clear face visibility (no hats/sunglasses)
    - 1080p or higher resolution
    - MP4 format
    
    After creation, a consent URL will be returned that the person must visit.
    """
    try:
        from app.services.heygen_service import get_heygen_service
        heygen = get_heygen_service()
        
        # Step 1: Create the Digital Twin
        result = await heygen.create_digital_twin(
            name=request.name,
            video_url=request.video_url,
        )
        
        avatar_item = result.get("avatar_item", {})
        avatar_group = result.get("avatar_group", {})
        look_id = avatar_item.get("id", "")
        group_id = avatar_group.get("id", "")
        
        # Step 2: Submit consent
        consent_url = None
        if group_id:
            try:
                consent_result = await heygen.submit_consent(group_id)
                consent_url = consent_result.get("url")
            except Exception as consent_err:
                logger.warning(f"[DigitalTwin] Consent submission failed: {consent_err}")
        
        return DigitalTwinCreateResponse(
            success=True,
            look_id=look_id,
            group_id=group_id,
            name=request.name,
            consent_url=consent_url,
            message=f"Digital Twin '{request.name}' created. "
                    f"{'Please complete consent at the URL.' if consent_url else 'Consent may be required.'}",
        )
    except Exception as e:
        logger.error(f"[DigitalTwin] Creation failed: {e}", exc_info=True)
        return DigitalTwinCreateResponse(
            success=False,
            error=str(e),
            message="Digital Twin creation failed",
        )


@router.get(
    "/digital-twin/list",
    summary="List all Digital Twin avatars",
)
async def list_digital_twins(
    _auth: bool = Depends(verify_admin_key),
):
    """List all available Digital Twin looks."""
    try:
        from app.services.heygen_service import get_heygen_service
        heygen = get_heygen_service()
        looks = await heygen.list_digital_twins()
        return {
            "success": True,
            "count": len(looks),
            "digital_twins": looks,
        }
    except Exception as e:
        logger.error(f"[DigitalTwin] List failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "digital_twins": [],
        }


@router.post(
    "/digital-twin/consent/{group_id}",
    summary="Submit consent for a Digital Twin",
)
async def submit_digital_twin_consent(
    group_id: str,
    _auth: bool = Depends(verify_admin_key),
):
    """Submit consent for a Digital Twin avatar group. Returns consent URL."""
    try:
        from app.services.heygen_service import get_heygen_service
        heygen = get_heygen_service()
        result = await heygen.submit_consent(group_id)
        return {
            "success": True,
            "consent_url": result.get("url"),
            "consent_status": result.get("avatar_group", {}).get("consent_status"),
        }
    except Exception as e:
        logger.error(f"[DigitalTwin] Consent failed: {e}")
        return {"success": False, "error": str(e)}
