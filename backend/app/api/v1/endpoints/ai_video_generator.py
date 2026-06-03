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

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-video-generator", tags=["AI Video Generator"])

# ──────────────────────────────────────────────
# In-memory job store (production: use DB table)
# ──────────────────────────────────────────────
_jobs: Dict[str, Dict[str, Any]] = {}


# ──────────────────────────────────────────────
# Request / Response Models
# ──────────────────────────────────────────────

class VideoGenerateRequest(BaseModel):
    """Request to generate a product introduction video."""
    # Product info
    product_name: str = Field(..., min_length=1, max_length=200, description="商品名")
    product_description: Optional[str] = Field(None, max_length=2000, description="商品説明・特徴")
    product_image_url: Optional[str] = Field(None, description="商品画像URL")
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
    # Error info
    error: Optional[str] = None
    error_step: Optional[str] = None


class AvatarInfo(BaseModel):
    """Available avatar/liver info."""
    avatar_id: str
    name: str
    preview_image_url: Optional[str] = None
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
        # STEP 3: Video Generation (HeyGen)
        # ═══════════════════════════════════════════
        job["status"] = "generating_video"
        job["progress"] = 70
        job["updated_at"] = datetime.now(timezone.utc).isoformat()

        video_url, duration_sec = await _generate_video(audio_url, request)
        logger.info(f"[AIVideoGen:{job_id}] Video generated: {video_url[:60]}...")

        # ═══════════════════════════════════════════
        # STEP 4: Complete
        # ═══════════════════════════════════════════
        job["status"] = "completed"
        job["progress"] = 100
        job["video_url"] = video_url
        job["video_duration_sec"] = duration_sec
        job["updated_at"] = datetime.now(timezone.utc).isoformat()

        logger.info(
            f"[AIVideoGen:{job_id}] ✅ Pipeline complete! "
            f"Product: {request.product_name}, Duration: {duration_sec}s"
        )

    except Exception as e:
        logger.error(f"[AIVideoGen:{job_id}] ❌ Pipeline failed: {e}", exc_info=True)
        job["status"] = "failed"
        job["error"] = str(e)
        job["error_step"] = job.get("status", "unknown")
        job["updated_at"] = datetime.now(timezone.utc).isoformat()


async def _generate_script(request: VideoGenerateRequest) -> str:
    """Generate a selling script using GPT-4 with winning patterns."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI()

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
5. CTA（「リンクから購入」「コメントで質問」など）"""

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
        model="gpt-4.1-mini",
        messages=messages,
        max_tokens=2000,
        temperature=0.8,
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
    
    Supports two modes:
      - AitherHub liver (avatar_id starts with 'aitherhub:'): 
        Upload face image as talking photo → generate video with talking photo
      - HeyGen Digital Twin (regular avatar_id):
        Use pre-registered avatar directly
    """
    from app.services.heygen_service import HeyGenService, get_heygen_service
    from app.api.v1.endpoints.digital_human import _upload_video_to_blob, _ensure_sas_url

    heygen = get_heygen_service()
    if not heygen.api_key:
        raise Exception("HEYGEN_API_KEY not configured")

    dimension = {"width": request.dimension_width, "height": request.dimension_height}
    title = f"AIVideoGen-{request.product_name[:30]}-{uuid.uuid4().hex[:6]}"

    # ─── Route by avatar source ───
    if request.avatar_id.startswith("aitherhub:"):
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
                    SELECT clip_url FROM video_clips
                    WHERE status = 'completed'
                        AND clip_url IS NOT NULL
                        AND clip_url != ''
                        AND video_filename IS NOT NULL
                        AND video_filename LIKE :pattern
                    ORDER BY created_at DESC
                    LIMIT 1
                """), {"pattern": f"{liver_name}-%"})
                row = result.fetchone()
                if row:
                    clip_video_url = row.clip_url
        except Exception as db_err:
            logger.warning(f"[AIVideoGen] DB lookup for liver clip failed: {db_err}")

        if not clip_video_url:
            raise Exception(f"No clip video found for liver: {liver_name}")

        logger.info(f"[AIVideoGen] Extracting frame from clip: {clip_video_url[:80]}...")

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
        # HeyGen Digital Twin: use pre-registered avatar directly
        logger.info(f"[AIVideoGen] Using HeyGen Digital Twin: {request.avatar_id}")
        video_id = await heygen.generate_video_with_avatar(
            avatar_id=request.avatar_id,
            audio_url=audio_url,
            dimension=dimension,
            title=title,
        )

    logger.info(f"[AIVideoGen] HeyGen video started: {video_id}")

    # Poll for completion (max 5 minutes)
    max_wait = 300
    poll_interval = 5
    elapsed = 0

    while elapsed < max_wait:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

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
        "script": None,
        "audio_url": None,
        "video_url": None,
        "video_duration_sec": None,
        "error": None,
        "error_step": None,
    }

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
    """List recent video generation jobs."""
    jobs_list = sorted(
        _jobs.values(),
        key=lambda j: j.get("created_at", ""),
        reverse=True,
    )[:limit]

    return {
        "jobs": [JobStatusResponse(**j) for j in jobs_list],
        "total": len(_jobs),
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
    """
    result = []

    # ─── Source 1: AitherHub DB livers (PRIORITY) ───
    # Extract unique livers from video_filename and get their latest clip_url as preview
    try:
        liver_sql = text("""
            WITH liver_clips AS (
                SELECT 
                    REGEXP_REPLACE(vc.video_filename, '-[0-9]{8}-[0-9]{4}\\.mp4$', '') AS liver_name,
                    vc.clip_url,
                    vc.created_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY REGEXP_REPLACE(vc.video_filename, '-[0-9]{8}-[0-9]{4}\\.mp4$', '')
                        ORDER BY vc.created_at DESC
                    ) AS rn
                FROM video_clips vc
                WHERE vc.status = 'completed' 
                    AND vc.clip_url IS NOT NULL
                    AND vc.clip_url != ''
                    AND vc.video_filename IS NOT NULL
                    AND vc.video_filename != ''
                    AND vc.video_filename ~ '^[^-]+-[0-9]{8}-[0-9]{4}\\.mp4$'
            )
            SELECT liver_name, clip_url, 
                   (SELECT COUNT(*) FROM video_clips vc2 
                    WHERE vc2.status = 'completed' 
                    AND vc2.clip_url IS NOT NULL
                    AND vc2.video_filename LIKE liver_clips.liver_name || '-%') AS clip_count
            FROM liver_clips
            WHERE rn = 1
            ORDER BY clip_count DESC
        """)
        liver_result = await db.execute(liver_sql)
        liver_rows = liver_result.fetchall()

        for row in liver_rows:
            liver_name = row.liver_name.strip() if row.liver_name else ""
            if not liver_name:
                continue
            avatar_id = f"aitherhub:{liver_name}"
            # Use clip_url as preview (frontend will render as <video>)
            result.append(AvatarInfo(
                avatar_id=avatar_id,
                name=liver_name,
                preview_image_url=row.clip_url,  # clip video URL for preview
                avatar_type="talking_photo",
                face_image_url=row.clip_url,  # will extract frame at generation time
                source="aitherhub",
            ))

        logger.info(f"[AIVideoGen] Found {len(result)} aitherhub livers from DB")

    except Exception as e:
        logger.error(f"[AIVideoGen] Failed to query aitherhub livers: {e}")

    # ─── Source 2: HeyGen Digital Twins (supplement) ───
    try:
        from app.services.heygen_service import get_heygen_service
        heygen = get_heygen_service()
        if heygen.api_key:
            avatars = await heygen.list_avatars(custom_only=True)
            for av in avatars:
                result.append(AvatarInfo(
                    avatar_id=av.get("avatar_id", ""),
                    name=av.get("avatar_name", "Unknown"),
                    preview_image_url=av.get("preview_image_url") or av.get("thumbnail_url"),
                    avatar_type=av.get("avatar_type", "full"),
                    source="heygen",
                ))
            logger.info(f"[AIVideoGen] Added {len(avatars)} HeyGen Digital Twin avatars")
    except Exception as e:
        logger.warning(f"[AIVideoGen] HeyGen avatars unavailable: {e}")

    return {"avatars": result, "total": len(result)}
