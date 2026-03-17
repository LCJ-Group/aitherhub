"""
Auto Video Pipeline Service for AitherHub

Fully automated video generation pipeline:
  1. Download input video
  2. Generate script from topic/product using GPT
  3. Face swap body double video using FaceFusion GPU Worker
  4. Lip sync + TTS via Sync.so (ElevenLabs voice integrated)
  5. Upload to Azure Blob Storage & finalize

This enables creating influencer-style videos automatically:
  - User provides: topic/product + body double video
  - System produces: video with influencer face + voice speaking about the topic

Architecture:
  ┌──────────────┐   ┌────────────────────────────────────────┐   ┌──────────────┐
  │ Topic/Product│──▶│ Auto Video Pipeline                    │──▶│ Final Video  │
  │ + Body Video │   │                                        │   │ (influencer  │
  └──────────────┘   │ ┌──────────┐  ┌──────────┐            │   │  face+voice) │
                     │ │ GPT      │  │ Sync.so  │            │   └──────────────┘
                     │ │ (script) │  │(TTS+lip) │            │
                     │ └──────────┘  └──────────┘            │
                     │ ┌──────────────────────┐              │
                     │ │ FaceFusion GPU Worker │              │
                     │ │ (face swap)           │              │
                     │ └──────────────────────┘              │
                     └────────────────────────────────────────┘
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# AitherHub "Sales Brain" — Aggregate top-performing patterns
# ──────────────────────────────────────────────

async def _fetch_sales_brain_context() -> str:
    """
    Fetch aggregated top-performing patterns from ALL analyzed videos.
    This is the "sales brain" — accumulated knowledge of what sells.

    Returns a formatted string for injection into GPT prompts.
    """
    try:
        from app.core.db import AsyncSessionLocal
        from sqlalchemy import text

        async with AsyncSessionLocal() as db:
            # Top-performing phases across ALL videos (by GMV + engagement)
            top_phases = await db.execute(
                text("""
                    SELECT
                        p.phase_description,
                        p.sales_psychology_tags,
                        vp.gmv, vp.orders, vp.cta_score,
                        vp.product_names,
                        p.delta_view, p.delta_like
                    FROM phases p
                    JOIN video_phases vp ON p.id = vp.phase_id
                    WHERE p.deleted_at IS NULL
                      AND (vp.gmv > 0 OR vp.cta_score > 3 OR p.delta_view > 50)
                    ORDER BY
                        (COALESCE(vp.gmv, 0) * 0.4
                         + COALESCE(p.delta_view, 0) * 0.25
                         + COALESCE(p.delta_like, 0) * 0.15
                         + COALESCE(vp.cta_score, 0) * 0.20) DESC
                    LIMIT 15
                """)
            )
            top_rows = [dict(r._mapping) for r in top_phases]

            # Top speech patterns from high-performing phases
            top_speech = await db.execute(
                text("""
                    SELECT ss.text
                    FROM speech_segments ss
                    JOIN audio_chunks ac ON ss.audio_chunk_id = ac.id
                    JOIN phases p ON p.video_id = ac.video_id
                        AND ss.start_ms >= p.time_start * 1000
                        AND ss.end_ms <= p.time_end * 1000
                    JOIN video_phases vp ON p.id = vp.phase_id
                    WHERE p.deleted_at IS NULL AND vp.gmv > 0
                    ORDER BY vp.gmv DESC
                    LIMIT 30
                """)
            )
            speech_rows = [r._mapping["text"] for r in top_speech]

            # Aggregate psychology tags frequency
            tag_freq = {}
            for row in top_rows:
                tags = row.get("sales_psychology_tags", "")
                if tags:
                    for tag in str(tags).split(","):
                        tag = tag.strip()
                        if tag:
                            tag_freq[tag] = tag_freq.get(tag, 0) + 1
            sorted_tags = sorted(tag_freq.items(), key=lambda x: x[1], reverse=True)

            # Build context string
            parts = []
            parts.append("### AitherHub Sales Brain — 過去の分析から学んだ売れるパターン")
            parts.append("")

            if sorted_tags:
                parts.append("**効果的なセールス心理タグ（頻度順）:**")
                for tag, count in sorted_tags[:10]:
                    parts.append(f"  - {tag} (出現{count}回)")
                parts.append("")

            if top_rows:
                parts.append("**トップパフォーマンスフェーズ（売上・エンゲージメント上位）:**")
                for i, row in enumerate(top_rows[:8], 1):
                    desc = row.get("phase_description", "")
                    gmv = row.get("gmv", 0)
                    cta = row.get("cta_score", 0)
                    products = row.get("product_names", "")
                    parts.append(
                        f"  {i}. GMV={gmv} CTA={cta} 商品={products}\n"
                        f"     {desc[:200]}"
                    )
                parts.append("")

            if speech_rows:
                parts.append("**売れた時の話し方の参考（実際の発話）:**")
                for text_seg in speech_rows[:15]:
                    if text_seg and len(text_seg) > 5:
                        parts.append(f"  「{text_seg[:150]}」")
                parts.append("")

            context = "\n".join(parts)
            logger.info(
                f"Sales brain context loaded: {len(top_rows)} phases, "
                f"{len(speech_rows)} speech segments, {len(sorted_tags)} tags"
            )
            return context

    except Exception as e:
        logger.warning(f"Failed to load sales brain context: {e}")
        return "(AitherHub分析データは現在利用できません)"


# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

TEMP_DIR = os.getenv("AUTO_VIDEO_TEMP_DIR", "/tmp/auto_video_pipeline")
os.makedirs(TEMP_DIR, exist_ok=True)

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
SYNC_API_KEY = os.getenv("SYNC_API_KEY", "")


# ──────────────────────────────────────────────
# Job Status
# ──────────────────────────────────────────────

class AutoVideoStatus(str, Enum):
    PENDING = "pending"
    GENERATING_SCRIPT = "generating_script"
    GENERATING_VOICE = "generating_voice"  # kept for backward compat (DB records)
    FACE_SWAPPING = "face_swapping"
    MERGING = "merging"  # kept for backward compat (DB records)
    LIP_SYNCING = "lip_syncing"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    ERROR = "error"


# ──────────────────────────────────────────────
# In-Memory Job Store
# ──────────────────────────────────────────────

auto_video_jobs: Dict[str, Dict[str, Any]] = {}


# ──────────────────────────────────────────────
# Auto Video Pipeline Service
# ──────────────────────────────────────────────

class AutoVideoPipelineService:
    """
    Orchestrates the full automated video generation pipeline.

    Usage:
        service = AutoVideoPipelineService()
        job_id = await service.create_job(
            video_url="https://storage/body_double.mp4",
            topic="KYOGOKUカラーシャンプー",
            voice_id="elevenlabs_voice_id",
        )
        status = await service.get_job_status(job_id)
    """

    def __init__(self):
        from app.services.face_swap_service import FaceSwapService
        from app.services.elevenlabs_tts_service import ElevenLabsTTSService
        from app.services.sync_lip_sync_service import SyncLipSyncService

        self.face_swap = FaceSwapService()
        self.tts = ElevenLabsTTSService()
        self.sync_lip_sync = SyncLipSyncService()

    async def create_job(
        self,
        video_url: str,
        topic: str,
        voice_id: Optional[str] = None,
        language: str = "ja",
        tone: str = "professional_friendly",
        script_text: Optional[str] = None,
        quality: str = "high",
        enable_lip_sync: bool = True,
        product_info: Optional[str] = None,
        target_duration_sec: Optional[int] = None,
    ) -> str:
        """
        Create a new auto video generation job.

        Args:
            video_url: URL of the body double video
            topic: Topic or product name for script generation
            voice_id: ElevenLabs voice ID (uses default if not set)
            language: Script language (ja, en, zh)
            tone: Script tone (professional_friendly, energetic, calm)
            script_text: Pre-written script (skips GPT generation if provided)
            quality: Face swap quality preset (fast, balanced, high, ultra)
            enable_lip_sync: Apply Sync.so lip sync with TTS
            product_info: Additional product information for script generation
            target_duration_sec: Target video duration in seconds

        Returns:
            job_id for polling status
        """
        job_id = f"av-{uuid.uuid4().hex[:12]}"

        job = {
            "job_id": job_id,
            "status": AutoVideoStatus.PENDING,
            "step": "pending",
            "step_detail": "Job created",
            "progress": 0,
            "error": None,
            "video_url": video_url,
            "topic": topic,
            "voice_id": voice_id or self.tts.voice_id or os.getenv("ELEVENLABS_VOICE_ID", ""),
            "language": language,
            "tone": tone,
            "script_text": script_text,
            "quality": quality,
            "enable_lip_sync": enable_lip_sync,
            "product_info": product_info,
            "target_duration_sec": target_duration_sec,
            "created_at": time.time(),
            "completed_at": None,
            "result_video_path": None,
            "generated_script": None,
            "tts_audio_duration_sec": None,
            "face_swap_job_id": None,
            "sync_generation_id": None,
        }

        auto_video_jobs[job_id] = job
        logger.info(f"[{job_id}] Auto video pipeline job created: topic={topic}")

        # Persist to DB
        from app.services.auto_video_db import save_job_to_db
        asyncio.create_task(save_job_to_db(job))

        # Start processing in background
        asyncio.create_task(self._run_pipeline(job_id))

        return job_id

    async def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Get the current status of an auto video job."""
        if job_id not in auto_video_jobs:
            # Try loading from DB (survives deploys)
            from app.services.auto_video_db import load_job_from_db
            db_job = await load_job_from_db(job_id)
            if db_job:
                auto_video_jobs[job_id] = db_job
            else:
                raise ValueError(f"Job {job_id} not found")

        job = auto_video_jobs[job_id]
        elapsed = time.time() - job["created_at"]

        return {
            "job_id": job_id,
            "status": job["status"],
            "step": job["step"],
            "step_detail": job.get("step_detail", ""),
            "progress": job["progress"],
            "error": job.get("error"),
            "elapsed_sec": round(elapsed, 1),
            "topic": job["topic"],
            "generated_script": job.get("generated_script"),
            "tts_audio_duration_sec": job.get("tts_audio_duration_sec"),
            "enable_lip_sync": job.get("enable_lip_sync", True),
            "lip_sync_error": job.get("lip_sync_error"),
            "result_video_url": self._resolve_result_url(job),
        }

    @staticmethod
    def _resolve_result_url(job: Dict[str, Any]) -> Optional[str]:
        """Return a fresh SAS-signed URL for the result video.

        If the stored result_video_url already has a SAS token, return it as-is
        (it was generated with a 7-day expiry during finalize).
        If we have a raw blob URL (result_blob_url), regenerate a fresh SAS.
        """
        url = job.get("result_video_url")
        if url:
            return url
        blob_url = job.get("result_blob_url")
        if blob_url:
            try:
                from app.services.storage_service import generate_read_sas_from_url
                return generate_read_sas_from_url(blob_url, expires_hours=168)
            except Exception:
                pass
        return None

    async def list_jobs(self, limit: int = 20) -> List[Dict[str, Any]]:
        """List recent auto video jobs (memory + DB fallback)."""
        # Merge DB jobs into memory if not already present
        try:
            from app.services.auto_video_db import load_jobs_from_db
            db_jobs = await load_jobs_from_db(limit=limit)
            for dj in db_jobs:
                if dj["job_id"] not in auto_video_jobs:
                    auto_video_jobs[dj["job_id"]] = dj
        except Exception as e:
            logger.debug(f"DB fallback for list_jobs failed: {e}")

        jobs = sorted(
            auto_video_jobs.values(),
            key=lambda j: j["created_at"],
            reverse=True,
        )[:limit]
        return [
            {
                "job_id": j["job_id"],
                "status": j["status"],
                "progress": j["progress"],
                "topic": j["topic"],
                "created_at": j["created_at"],
                "completed_at": j.get("completed_at"),
            }
            for j in jobs
        ]

    async def delete_job(self, job_id: str) -> Dict[str, Any]:
        """Delete a job and cleanup temporary files."""
        if job_id not in auto_video_jobs:
            # Try loading from DB
            from app.services.auto_video_db import load_job_from_db
            db_job = await load_job_from_db(job_id)
            if db_job:
                auto_video_jobs[job_id] = db_job
            else:
                raise ValueError(f"Job {job_id} not found")

        job = auto_video_jobs[job_id]

        # Cleanup GPU worker job
        if job.get("face_swap_job_id"):
            try:
                await self.face_swap.delete_video_job(job["face_swap_job_id"])
            except Exception as e:
                logger.warning(f"[{job_id}] Failed to cleanup GPU worker job: {e}")

        # Cleanup temp files
        for suffix in [
            "_input.mp4", "_swapped.mp4",
            "_final.mp4", "_script.txt",
        ]:
            path = os.path.join(TEMP_DIR, f"{job_id}{suffix}")
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

        del auto_video_jobs[job_id]

        # Also delete from DB
        from app.services.auto_video_db import delete_job_from_db
        asyncio.create_task(delete_job_from_db(job_id))

        return {"status": "deleted", "job_id": job_id}

    async def get_result_video_path(self, job_id: str) -> Optional[str]:
        """Get the local file path of the completed video."""
        if job_id not in auto_video_jobs:
            return None
        job = auto_video_jobs[job_id]
        if job["status"] != AutoVideoStatus.COMPLETED:
            return None
        return job.get("result_video_path")

    # ──────────────────────────────────────────
    # Internal Pipeline
    # ──────────────────────────────────────────

    async def _run_pipeline(self, job_id: str):
        """
        Execute the full automated video generation pipeline.

        Steps:
          1. Download input video & get duration
          2. Generate script (GPT) or use provided script
          3. Face swap video (FaceFusion GPU Worker)
          4. Lip sync + TTS via Sync.so (ElevenLabs voice integrated)
          5. Upload to Blob & Finalize
        """
        job = auto_video_jobs[job_id]

        try:
            input_path = os.path.join(TEMP_DIR, f"{job_id}_input.mp4")
            swapped_path = os.path.join(TEMP_DIR, f"{job_id}_swapped.mp4")
            final_path = os.path.join(TEMP_DIR, f"{job_id}_final.mp4")

            # ── Step 1: Download input video ──
            job["status"] = AutoVideoStatus.PENDING
            job["step"] = "pending"
            job["step_detail"] = "Downloading body double video"
            job["progress"] = 2
            logger.info(f"[{job_id}] Step 1: Downloading video")

            # If the video URL is on our Azure Blob Storage (no SAS token),
            # generate a read SAS to avoid 409 "Public access not permitted"
            download_url = job["video_url"]
            if "aitherhub.blob.core.windows.net" in download_url and "?" not in download_url:
                from app.services.storage_service import generate_read_sas_from_url
                sas_url = generate_read_sas_from_url(download_url)
                if sas_url:
                    download_url = sas_url
                    logger.info(f"[{job_id}] Added read SAS to blob URL")
                else:
                    logger.warning(f"[{job_id}] Failed to generate read SAS, trying direct URL")

            async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
                async with client.stream("GET", download_url) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("content-length", 0))
                    downloaded = 0
                    with open(input_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=256 * 1024):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total > 0:
                                job["progress"] = min(5, int(downloaded / total * 5))

            # Get video duration
            video_duration = await self._get_video_duration(input_path)
            logger.info(
                f"[{job_id}] Downloaded: "
                f"{os.path.getsize(input_path) / (1024*1024):.1f} MB, "
                f"duration: {video_duration:.1f}s"
            )

            # ── Step 2: Generate script ──
            job["status"] = AutoVideoStatus.GENERATING_SCRIPT
            job["step"] = "generating_script"
            job["step_detail"] = "Generating script with AI"
            job["progress"] = 8
            logger.info(f"[{job_id}] Step 2: Generating script")

            if job["script_text"]:
                script = job["script_text"]
                logger.info(f"[{job_id}] Using provided script: {len(script)} chars")
            else:
                # Load AitherHub "sales brain" context
                sales_brain = await _fetch_sales_brain_context()
                script = await self._generate_script(
                    topic=job["topic"],
                    product_info=job.get("product_info"),
                    language=job["language"],
                    tone=job["tone"],
                    target_duration_sec=job.get("target_duration_sec") or video_duration,
                    sales_brain_context=sales_brain,
                )

            job["generated_script"] = script
            job["progress"] = 15

            # Save script for reference
            script_path = os.path.join(TEMP_DIR, f"{job_id}_script.txt")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(script)

            # ── Step 3: Face swap (GPU Worker) ──
            job["status"] = AutoVideoStatus.FACE_SWAPPING
            job["step"] = "face_swapping"
            job["step_detail"] = "Face swapping video (GPU processing)"
            job["progress"] = 18
            logger.info(f"[{job_id}] Step 3: Starting face swap")

            fs_job_id = f"fs-{job_id}"
            job["face_swap_job_id"] = fs_job_id

            # Use SAS URL for face swap too (GPU worker needs to download the video)
            fs_video_url = job["video_url"]
            if "aitherhub.blob.core.windows.net" in fs_video_url and "?" not in fs_video_url:
                from app.services.storage_service import generate_read_sas_from_url
                sas_url = generate_read_sas_from_url(fs_video_url)
                if sas_url:
                    fs_video_url = sas_url

            # Convert quality string to FaceSwapQuality enum
            from app.services.face_swap_service import FaceSwapQuality
            fs_quality = FaceSwapQuality(job["quality"]) if isinstance(job["quality"], str) else job["quality"]

            await self.face_swap.swap_video(
                job_id=fs_job_id,
                video_url=fs_video_url,
                quality=fs_quality,
                output_video_quality=95,
            )

            # Poll GPU worker for face swap progress
            while True:
                await asyncio.sleep(3)
                fs_status = await self.face_swap.video_status(fs_job_id)

                if fs_status.get("status") == "completed":
                    job["progress"] = 60
                    break
                elif fs_status.get("status") == "error":
                    raise RuntimeError(
                        f"Face swap failed: {fs_status.get('error', 'unknown')}"
                    )
                else:
                    gpu_progress = fs_status.get("progress", 0)
                    job["progress"] = 18 + int(gpu_progress * 0.42)  # 18-60%
                    job["step"] = "face_swapping"
                    job["step_detail"] = f"Face swapping: {fs_status.get('step', 'processing')}"

            # Download face-swapped video
            job["step_detail"] = "Downloading face-swapped video"
            download_url = await self.face_swap.video_download_url(fs_job_id)

            async with httpx.AsyncClient(
                timeout=300,
                headers={"X-Api-Key": self.face_swap.api_key},
            ) as client:
                resp = await client.get(download_url)
                resp.raise_for_status()
                with open(swapped_path, "wb") as f:
                    f.write(resp.content)

            logger.info(
                f"[{job_id}] Face swap complete: "
                f"{os.path.getsize(swapped_path) / (1024*1024):.1f} MB"
            )

            # ── Step 4: Lip sync + TTS via Sync.so ──
            if job["enable_lip_sync"]:
                job["status"] = AutoVideoStatus.LIP_SYNCING
                job["step"] = "lip_syncing"
                job["step_detail"] = "Applying lip sync + voice (Sync.so)"
                job["progress"] = 65
                logger.info(f"[{job_id}] Step 4: Lip sync + TTS via Sync.so")

                # Validate voice_id before proceeding
                effective_voice_id = job.get("voice_id") or os.getenv("ELEVENLABS_VOICE_ID", "")
                if not effective_voice_id:
                    error_msg = (
                        "No voice_id configured. Set ELEVENLABS_VOICE_ID env var "
                        "or pass voice_id when creating the job."
                    )
                    logger.error(f"[{job_id}] {error_msg}")
                    job["lip_sync_error"] = error_msg
                    raise RuntimeError(error_msg)
                job["voice_id"] = effective_voice_id
                logger.info(f"[{job_id}] Using voice_id: {effective_voice_id}")

                try:
                    # Upload face-swapped video to Azure Blob for Sync.so to access
                    from app.services.storage_service import (
                        generate_upload_sas,
                        generate_read_sas_from_url,
                    )

                    _, swap_upload_url, swap_blob_url, _ = await generate_upload_sas(
                        email="auto-video@aitherhub.com",
                        video_id=f"{job_id}-swap",
                        filename=f"{job_id}-swapped.mp4",
                    )

                    async with httpx.AsyncClient(timeout=300) as upload_client:
                        with open(swapped_path, "rb") as f:
                            swap_data = f.read()
                        resp = await upload_client.put(
                            swap_upload_url,
                            content=swap_data,
                            headers={
                                "x-ms-blob-type": "BlockBlob",
                                "Content-Type": "video/mp4",
                            },
                        )
                        resp.raise_for_status()

                    # Generate read SAS URL for Sync.so to download
                    swap_sas_url = generate_read_sas_from_url(swap_blob_url)
                    if not swap_sas_url:
                        raise RuntimeError("Failed to generate SAS URL for swapped video")

                    job["progress"] = 70
                    logger.info(f"[{job_id}] Uploaded swapped video to blob")

                    # ── Step 4a: Generate TTS audio via ElevenLabs ──
                    job["step_detail"] = "Generating voice audio (ElevenLabs TTS)"
                    logger.info(f"[{job_id}] Step 4a: Generating TTS audio with ElevenLabs")

                    tts_audio_bytes = await self.tts.text_to_speech(
                        text=script,
                        voice_id=effective_voice_id,
                        language_code=job.get("language", "ja"),
                        output_format="mp3_44100_128",
                    )

                    # Save TTS audio locally and upload to blob
                    tts_audio_path = os.path.join(work_dir, f"{job_id}-tts.mp3")
                    with open(tts_audio_path, "wb") as f:
                        f.write(tts_audio_bytes)

                    tts_duration_sec = len(tts_audio_bytes) / (44100 * 2)  # approximate
                    job["tts_audio_duration_sec"] = round(tts_duration_sec, 1)
                    logger.info(
                        f"[{job_id}] TTS audio generated: {len(tts_audio_bytes)} bytes, "
                        f"~{tts_duration_sec:.1f}s"
                    )

                    # Upload TTS audio to Azure Blob for Sync.so to access
                    _, tts_upload_url, tts_blob_url, _ = await generate_upload_sas(
                        email="auto-video@aitherhub.com",
                        video_id=f"{job_id}-tts",
                        filename=f"{job_id}-tts.mp3",
                    )

                    async with httpx.AsyncClient(timeout=120) as tts_upload_client:
                        resp = await tts_upload_client.put(
                            tts_upload_url,
                            content=tts_audio_bytes,
                            headers={
                                "x-ms-blob-type": "BlockBlob",
                                "Content-Type": "audio/mpeg",
                            },
                        )
                        resp.raise_for_status()

                    tts_sas_url = generate_read_sas_from_url(tts_blob_url)
                    if not tts_sas_url:
                        raise RuntimeError("Failed to generate SAS URL for TTS audio")

                    job["progress"] = 78

                    # ── Step 4b: Lip sync via Sync.so (video + audio mode) ──
                    job["step_detail"] = "Applying lip sync (Sync.so)"
                    logger.info(
                        f"[{job_id}] Step 4b: Lip sync via Sync.so "
                        f"(video + audio mode)"
                    )

                    sync_result = await self.sync_lip_sync.lip_sync(
                        video_url=swap_sas_url,
                        audio_url=tts_sas_url,
                        model="lipsync-2",
                        sync_mode="cut_off",
                        max_wait_sec=600,
                        poll_interval=5,
                    )

                    job["sync_generation_id"] = sync_result.get("generation_id")
                    output_url = sync_result.get("output_url")

                    if output_url:
                        # Download the lip-synced video
                        job["step_detail"] = "Downloading lip-synced video"
                        job["progress"] = 88
                        await self.sync_lip_sync.download_result(output_url, final_path)
                        logger.info(f"[{job_id}] Sync.so lip sync completed successfully")
                    else:
                        raise RuntimeError("Sync.so returned no output URL")

                except Exception as e:
                    error_detail = str(e)
                    logger.error(
                        f"[{job_id}] Sync.so lip sync FAILED: {error_detail}"
                    )
                    job["lip_sync_error"] = error_detail
                    job["step_detail"] = f"Lip sync failed: {error_detail[:100]}"
                    # Still produce a video, but record the failure clearly
                    logger.warning(f"[{job_id}] Falling back to face-swapped video WITHOUT audio")
                    shutil.copy2(swapped_path, final_path)
            else:
                # No lip sync requested — use face-swapped video as-is (no audio)
                shutil.copy2(swapped_path, final_path)

            job["progress"] = 92

            # ── Step 5: Finalize — Upload to Blob Storage ──
            job["status"] = AutoVideoStatus.FINALIZING
            job["step"] = "finalizing"
            job["step_detail"] = "Uploading result video"
            job["progress"] = 95

            final_size_mb = os.path.getsize(final_path) / (1024 * 1024)
            job["result_video_path"] = final_path
            job["result_video_size_mb"] = round(final_size_mb, 1)

            # Upload final video to Azure Blob Storage
            try:
                from app.services.storage_service import generate_upload_sas

                _, upload_url, blob_url, _ = await generate_upload_sas(
                    email="auto-video@aitherhub.com",
                    video_id=job_id,
                    filename=f"{job_id}-final.mp4",
                )

                async with httpx.AsyncClient(timeout=300) as upload_client:
                    with open(final_path, "rb") as f:
                        video_data = f.read()
                    resp = await upload_client.put(
                        upload_url,
                        content=video_data,
                        headers={
                            "x-ms-blob-type": "BlockBlob",
                            "Content-Type": "video/mp4",
                        },
                    )
                    resp.raise_for_status()

                # Generate a read SAS URL so the frontend can access the video
                # (public access is disabled on this storage account)
                from app.services.storage_service import generate_read_sas_from_url
                sas_url = generate_read_sas_from_url(blob_url, expires_hours=168)  # 7 days
                job["result_video_url"] = sas_url or blob_url
                job["result_blob_url"] = blob_url  # keep raw URL for re-generating SAS
                logger.info(
                    f"[{job_id}] Uploaded final video to blob: {blob_url}"
                )
            except Exception as upload_err:
                logger.warning(
                    f"[{job_id}] Failed to upload final video to blob: {upload_err}"
                )
                # Pipeline still completes, but result_video_url stays None

            job["progress"] = 97
            job["step_detail"] = "Preparing result"

            # ── Done ──
            job["status"] = AutoVideoStatus.COMPLETED
            job["step"] = "completed"
            job["step_detail"] = "Pipeline completed"
            job["progress"] = 100
            job["completed_at"] = time.time()
            elapsed = job["completed_at"] - job["created_at"]
            logger.info(
                f"[{job_id}] Auto video pipeline completed in {elapsed:.1f}s. "
                f"Output: {final_size_mb:.1f} MB"
            )

            # Persist completed job to DB
            from app.services.auto_video_db import save_job_to_db
            await save_job_to_db(job)

        except Exception as e:
            logger.error(f"[{job_id}] Pipeline failed: {e}", exc_info=True)
            job["status"] = AutoVideoStatus.ERROR
            job["step"] = "error"
            job["step_detail"] = str(e)
            job["error"] = str(e)

            # Persist error state to DB
            from app.services.auto_video_db import save_job_to_db
            await save_job_to_db(job)

    # ──────────────────────────────────────────
    # Script Generation (GPT)
    # ──────────────────────────────────────────

    async def _generate_script(
        self,
        topic: str,
        product_info: Optional[str],
        language: str,
        tone: str,
        target_duration_sec: float,
        sales_brain_context: str = "",
    ) -> str:
        """
        Generate a livestream/video script using GPT.

        Estimates ~3 characters per second for Japanese speech,
        ~2.5 words per second for English.
        """
        # Estimate target character count based on duration
        if language == "ja":
            chars_per_sec = 5  # Japanese: ~5 chars/sec for natural speech
            target_chars = int(target_duration_sec * chars_per_sec)
        elif language == "zh":
            chars_per_sec = 4
            target_chars = int(target_duration_sec * chars_per_sec)
        else:
            words_per_sec = 2.5
            target_chars = int(target_duration_sec * words_per_sec * 5)

        lang_map = {
            "ja": "日本語",
            "zh": "中文",
            "en": "English",
        }
        lang_name = lang_map.get(language, "日本語")

        tone_map = {
            "professional_friendly": "プロフェッショナルだが親しみやすいトーン。美容のプロとして信頼感を持ちつつ、視聴者に寄り添う話し方。",
            "energetic": "エネルギッシュで盛り上がるトーン。セール感を出しつつ、商品の魅力を熱く語る。",
            "calm": "落ち着いた上品なトーン。高級感のある商品紹介に適した、ゆったりとした話し方。",
        }
        tone_desc = tone_map.get(tone, tone_map["professional_friendly"])

        product_context = ""
        if product_info:
            product_context = f"\n\n## 商品情報\n{product_info}"

        brain_context = ""
        if sales_brain_context:
            brain_context = f"\n\n{sales_brain_context}"

        prompt = f"""あなたはライブコマース・動画コンテンツの台本作成のプロフェッショナルです。
以下のテーマについて、インフルエンサーが読み上げる動画台本を生成してください。

## テーマ
{topic}
{product_context}

## 要件
- 言語: {lang_name}
- トーン: {tone_desc}
- 目標の長さ: 約{target_chars}文字（約{target_duration_sec:.0f}秒の動画用）
- 構成: 挨拶→テーマ導入→詳細説明→使用シーン/メリット→まとめ/CTA
- 自然な話し言葉で、AIデジタルヒューマンが読み上げても違和感がないように
- 視聴者への呼びかけや質問を適度に挿入
- 句読点と改行を適切に配置（読み上げのペースを制御）

## 注意事項
- 台本テキストのみを出力してください（メタ情報やコメントは不要）
- 虚偽の効能表現、薬事法に抵触する表現は避ける
- 「こんにちは」で始めない（自然な導入を工夫する）
{brain_context}
"""

        try:
            import openai

            client = openai.AsyncOpenAI()
            response = await client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a professional script writer for influencer "
                            "video content and live commerce. Generate natural, "
                            "engaging scripts optimized for text-to-speech delivery."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=4096,
                temperature=0.7,
            )
            script = response.choices[0].message.content.strip()
            logger.info(f"GPT script generated: {len(script)} chars for topic: {topic}")
            return script

        except Exception as e:
            logger.error(f"GPT script generation failed: {e}")
            # Fallback: simple template
            return self._fallback_script(topic, language)

    @staticmethod
    def _fallback_script(topic: str, language: str) -> str:
        """Simple fallback script when GPT is unavailable."""
        if language == "ja":
            return (
                f"皆さん、今日は{topic}についてお話しします。\n\n"
                f"{topic}は、多くの方に愛されている素晴らしい商品です。\n"
                f"その特徴と魅力について、詳しくご紹介していきますね。\n\n"
                f"ぜひ最後までご覧ください。"
            )
        return (
            f"Today, let's talk about {topic}.\n\n"
            f"{topic} is a wonderful product loved by many.\n"
            f"Let me share its features and benefits with you.\n\n"
            f"Please watch until the end!"
        )

    # ──────────────────────────────────────────
    # Utilities
    # ──────────────────────────────────────────

    @staticmethod
    async def _get_video_duration(path: str) -> float:
        """Get video duration in seconds using ffprobe."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            return float(stdout.decode().strip()) if proc.returncode == 0 else 0
        except Exception:
            return 0

    @staticmethod
    async def _get_audio_duration(path: str) -> float:
        """Get audio duration in seconds using ffprobe."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            return float(stdout.decode().strip()) if proc.returncode == 0 else 0
        except Exception:
            return 0
