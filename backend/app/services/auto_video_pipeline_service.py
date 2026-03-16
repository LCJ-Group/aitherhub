"""
Auto Video Pipeline Service for AitherHub

Fully automated video generation pipeline:
  1. Generate script from topic/product using GPT
  2. Generate voice audio from script using ElevenLabs TTS
  3. Face swap body double video using FaceFusion GPU Worker
  4. Merge face-swapped video + TTS audio
  5. Apply lip sync using ElevenLabs Dubbing API
  6. Output final video

This enables creating influencer-style videos automatically:
  - User provides: topic/product + body double video
  - System produces: video with influencer face + voice speaking about the topic

Architecture:
  ┌──────────────┐   ┌────────────────────────────────────────┐   ┌──────────────┐
  │ Topic/Product│──▶│ Auto Video Pipeline                    │──▶│ Final Video  │
  │ + Body Video │   │                                        │   │ (influencer  │
  └──────────────┘   │ ┌──────────┐  ┌──────────┐            │   │  face+voice) │
                     │ │ GPT      │  │ ElevenLabs│            │   └──────────────┘
                     │ │ (script) │  │ (TTS+dub) │            │
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
ELEVENLABS_BASE_URL = os.getenv("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io")


# ──────────────────────────────────────────────
# Job Status
# ──────────────────────────────────────────────

class AutoVideoStatus(str, Enum):
    PENDING = "pending"
    GENERATING_SCRIPT = "generating_script"
    GENERATING_VOICE = "generating_voice"
    FACE_SWAPPING = "face_swapping"
    MERGING = "merging"
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

        self.face_swap = FaceSwapService()
        self.tts = ElevenLabsTTSService()

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
            enable_lip_sync: Apply ElevenLabs lip sync after merging
            product_info: Additional product information for script generation
            target_duration_sec: Target video duration in seconds

        Returns:
            job_id for polling status
        """
        job_id = f"av-{uuid.uuid4().hex[:12]}"

        job = {
            "job_id": job_id,
            "status": AutoVideoStatus.PENDING,
            "step": "Job created",
            "progress": 0,
            "error": None,
            "video_url": video_url,
            "topic": topic,
            "voice_id": voice_id or self.tts.voice_id,
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
            "dubbing_id": None,
        }

        auto_video_jobs[job_id] = job
        logger.info(f"[{job_id}] Auto video pipeline job created: topic={topic}")

        # Start processing in background
        asyncio.create_task(self._run_pipeline(job_id))

        return job_id

    async def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Get the current status of an auto video job."""
        if job_id not in auto_video_jobs:
            raise ValueError(f"Job {job_id} not found")

        job = auto_video_jobs[job_id]
        elapsed = time.time() - job["created_at"]

        return {
            "job_id": job_id,
            "status": job["status"],
            "step": job["step"],
            "progress": job["progress"],
            "error": job.get("error"),
            "elapsed_sec": round(elapsed, 1),
            "topic": job["topic"],
            "generated_script": job.get("generated_script"),
            "tts_audio_duration_sec": job.get("tts_audio_duration_sec"),
            "enable_lip_sync": job.get("enable_lip_sync", True),
            "result_video_url": job.get("result_video_url"),
        }

    async def list_jobs(self, limit: int = 20) -> List[Dict[str, Any]]:
        """List recent auto video jobs."""
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
            "_input.mp4", "_tts_audio.mp3", "_swapped.mp4",
            "_merged.mp4", "_final.mp4", "_script.txt",
        ]:
            path = os.path.join(TEMP_DIR, f"{job_id}{suffix}")
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

        del auto_video_jobs[job_id]
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
          3. Generate TTS audio (ElevenLabs)
          4. Face swap video (FaceFusion GPU Worker)
          5. Merge face-swapped video + TTS audio
          6. Apply lip sync (ElevenLabs Dubbing)
          7. Finalize
        """
        job = auto_video_jobs[job_id]

        try:
            input_path = os.path.join(TEMP_DIR, f"{job_id}_input.mp4")
            tts_audio_path = os.path.join(TEMP_DIR, f"{job_id}_tts_audio.mp3")
            swapped_path = os.path.join(TEMP_DIR, f"{job_id}_swapped.mp4")
            merged_path = os.path.join(TEMP_DIR, f"{job_id}_merged.mp4")
            final_path = os.path.join(TEMP_DIR, f"{job_id}_final.mp4")

            # ── Step 1: Download input video ──
            job["status"] = AutoVideoStatus.PENDING
            job["step"] = "Downloading body double video"
            job["progress"] = 2
            logger.info(f"[{job_id}] Step 1: Downloading video")

            async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
                async with client.stream("GET", job["video_url"]) as resp:
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
            job["step"] = "Generating script with AI"
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

            # ── Step 3: Generate TTS audio ──
            job["status"] = AutoVideoStatus.GENERATING_VOICE
            job["step"] = "Generating voice audio (ElevenLabs TTS)"
            job["progress"] = 18
            logger.info(f"[{job_id}] Step 3: Generating TTS audio")

            tts_audio = await self.tts.text_to_speech(
                text=script,
                voice_id=job["voice_id"],
                output_format="mp3_44100_128",
                language_code=job["language"],
            )

            with open(tts_audio_path, "wb") as f:
                f.write(tts_audio)

            tts_duration = await self._get_audio_duration(tts_audio_path)
            job["tts_audio_duration_sec"] = round(tts_duration, 1)
            job["progress"] = 25
            logger.info(
                f"[{job_id}] TTS audio: {len(tts_audio) / (1024*1024):.2f} MB, "
                f"duration: {tts_duration:.1f}s"
            )

            # ── Step 4: Face swap (GPU Worker) ──
            job["status"] = AutoVideoStatus.FACE_SWAPPING
            job["step"] = "Face swapping video (GPU processing)"
            job["progress"] = 28
            logger.info(f"[{job_id}] Step 4: Starting face swap")

            fs_job_id = f"fs-{job_id}"
            job["face_swap_job_id"] = fs_job_id

            await self.face_swap.swap_video(
                job_id=fs_job_id,
                video_url=job["video_url"],
                quality=job["quality"],
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
                    job["progress"] = 28 + int(gpu_progress * 0.32)
                    job["step"] = f"Face swapping: {fs_status.get('step', 'processing')}"

            # Download face-swapped video
            job["step"] = "Downloading face-swapped video"
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

            # ── Step 5: Merge face-swapped video + TTS audio ──
            job["status"] = AutoVideoStatus.MERGING
            job["step"] = "Merging video and generated voice"
            job["progress"] = 65
            logger.info(f"[{job_id}] Step 5: Merging video + TTS audio")

            merge_proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y",
                "-i", swapped_path,
                "-i", tts_audio_path,
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k",
                "-map", "0:v:0", "-map", "1:a:0",
                "-shortest",
                merged_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await merge_proc.communicate()
            if merge_proc.returncode != 0:
                logger.error(f"[{job_id}] Merge failed: {stderr.decode()[:300]}")
                # Fallback: use face-swapped video without audio
                shutil.copy2(swapped_path, merged_path)

            job["progress"] = 72

            # ── Step 6: Lip sync (ElevenLabs Dubbing) ──
            if job["enable_lip_sync"]:
                job["status"] = AutoVideoStatus.LIP_SYNCING
                job["step"] = "Applying lip sync (ElevenLabs Dubbing)"
                job["progress"] = 75
                logger.info(f"[{job_id}] Step 6: Lip sync via ElevenLabs Dubbing")

                try:
                    lip_synced_path = await self._apply_lip_sync(
                        job_id=job_id,
                        video_path=merged_path,
                        language=job["language"],
                    )
                    if lip_synced_path and os.path.exists(lip_synced_path):
                        shutil.copy2(lip_synced_path, final_path)
                        logger.info(f"[{job_id}] Lip sync applied successfully")
                    else:
                        logger.warning(f"[{job_id}] Lip sync returned no result, using merged video")
                        shutil.copy2(merged_path, final_path)
                except Exception as e:
                    logger.warning(
                        f"[{job_id}] Lip sync failed (using merged video): {e}"
                    )
                    shutil.copy2(merged_path, final_path)
            else:
                shutil.copy2(merged_path, final_path)

            job["progress"] = 95

            # ── Step 7: Finalize ──
            job["status"] = AutoVideoStatus.FINALIZING
            job["step"] = "Preparing result"
            job["progress"] = 97

            final_size_mb = os.path.getsize(final_path) / (1024 * 1024)
            job["result_video_path"] = final_path
            job["result_video_size_mb"] = round(final_size_mb, 1)

            # ── Done ──
            job["status"] = AutoVideoStatus.COMPLETED
            job["step"] = "Pipeline completed"
            job["progress"] = 100
            job["completed_at"] = time.time()
            elapsed = job["completed_at"] - job["created_at"]
            logger.info(
                f"[{job_id}] Auto video pipeline completed in {elapsed:.1f}s. "
                f"Output: {final_size_mb:.1f} MB"
            )

        except Exception as e:
            logger.error(f"[{job_id}] Pipeline failed: {e}", exc_info=True)
            job["status"] = AutoVideoStatus.ERROR
            job["step"] = "Error"
            job["error"] = str(e)

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
    # Lip Sync (ElevenLabs Dubbing API)
    # ──────────────────────────────────────────

    async def _apply_lip_sync(
        self,
        job_id: str,
        video_path: str,
        language: str,
    ) -> Optional[str]:
        """
        Apply lip sync to a video using ElevenLabs Dubbing API.

        Uses same-language dubbing (source_lang == target_lang) to
        synchronize lip movements with the audio track.

        Returns path to the lip-synced video file, or None on failure.
        """
        if not ELEVENLABS_API_KEY:
            logger.warning(f"[{job_id}] ElevenLabs API key not set, skipping lip sync")
            return None

        headers = {"xi-api-key": ELEVENLABS_API_KEY}

        # Step 1: Create dubbing job
        logger.info(f"[{job_id}] Creating ElevenLabs dubbing job")

        with open(video_path, "rb") as f:
            video_data = f.read()

        async with httpx.AsyncClient(timeout=600) as client:
            # Create dubbing
            files = {
                "file": ("video.mp4", video_data, "video/mp4"),
            }
            data = {
                "name": f"aitherhub-{job_id}",
                "source_lang": language,
                "target_lang": language,  # Same language = lip sync only
                "num_speakers": "1",
                "highest_resolution": "true",
                "drop_background_audio": "false",
            }

            resp = await client.post(
                f"{ELEVENLABS_BASE_URL}/v1/dubbing",
                headers=headers,
                files=files,
                data=data,
            )

            if resp.status_code != 200:
                logger.error(
                    f"[{job_id}] Dubbing create failed: "
                    f"{resp.status_code} {resp.text[:300]}"
                )
                return None

            dub_result = resp.json()
            dubbing_id = dub_result["dubbing_id"]
            expected_duration = dub_result.get("expected_duration_sec", 0)
            auto_video_jobs[job_id]["dubbing_id"] = dubbing_id
            logger.info(
                f"[{job_id}] Dubbing job created: {dubbing_id}, "
                f"expected: {expected_duration}s"
            )

            # Step 2: Poll for completion
            max_wait = max(600, expected_duration * 3)
            start_time = time.time()

            while time.time() - start_time < max_wait:
                await asyncio.sleep(5)

                status_resp = await client.get(
                    f"{ELEVENLABS_BASE_URL}/v1/dubbing/{dubbing_id}",
                    headers=headers,
                )

                if status_resp.status_code != 200:
                    logger.warning(
                        f"[{job_id}] Dubbing status check failed: "
                        f"{status_resp.status_code}"
                    )
                    continue

                dub_status = status_resp.json()
                status = dub_status.get("status", "")

                if status == "dubbed":
                    logger.info(f"[{job_id}] Dubbing completed")
                    break
                elif status in ("failed", "error"):
                    error = dub_status.get("error", "unknown")
                    logger.error(f"[{job_id}] Dubbing failed: {error}")
                    return None
                else:
                    logger.debug(f"[{job_id}] Dubbing status: {status}")
            else:
                logger.error(f"[{job_id}] Dubbing timed out after {max_wait}s")
                return None

            # Step 3: Download dubbed video
            output_path = os.path.join(TEMP_DIR, f"{job_id}_dubbed.mp4")

            download_resp = await client.get(
                f"{ELEVENLABS_BASE_URL}/v1/dubbing/{dubbing_id}/audio/{language}",
                headers=headers,
            )

            if download_resp.status_code == 200:
                # This returns audio only; we need the video
                # Try the resource endpoint for video
                video_resp = await client.get(
                    f"{ELEVENLABS_BASE_URL}/v1/dubbing/{dubbing_id}/resource/video",
                    headers=headers,
                )
                if video_resp.status_code == 200:
                    with open(output_path, "wb") as f:
                        f.write(video_resp.content)
                    logger.info(
                        f"[{job_id}] Dubbed video downloaded: "
                        f"{len(video_resp.content) / (1024*1024):.1f} MB"
                    )
                    return output_path
                else:
                    # Fallback: merge original video with dubbed audio
                    dubbed_audio_path = os.path.join(
                        TEMP_DIR, f"{job_id}_dubbed_audio.mp3"
                    )
                    with open(dubbed_audio_path, "wb") as f:
                        f.write(download_resp.content)

                    merge_proc = await asyncio.create_subprocess_exec(
                        "ffmpeg", "-y",
                        "-i", video_path,
                        "-i", dubbed_audio_path,
                        "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "192k",
                        "-map", "0:v:0", "-map", "1:a:0",
                        "-shortest",
                        output_path,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await merge_proc.communicate()

                    if os.path.exists(output_path):
                        return output_path

            logger.error(
                f"[{job_id}] Failed to download dubbed content: "
                f"{download_resp.status_code}"
            )
            return None

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
