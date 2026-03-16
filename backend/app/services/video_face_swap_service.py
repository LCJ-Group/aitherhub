"""
Video Face Swap Pipeline Service for AitherHub

Orchestrates the full video transformation pipeline:
  1. Upload video to Azure Blob Storage
  2. Extract audio from video (ffmpeg)
  3. Send video to GPU Worker for face swap (FaceFusion)
  4. Send audio to ElevenLabs for voice conversion (Speech-to-Speech)
  5. Merge face-swapped video + converted audio (ffmpeg)
  6. Upload final video to Azure Blob Storage

This enables staff members to record videos that are then transformed
to look and sound like an influencer.

Architecture:
  ┌─────────────┐     ┌──────────────────┐     ┌──────────────┐
  │ Staff Video  │────▶│ Video Pipeline   │────▶│ Final Video  │
  │ (face+voice) │     │ Service          │     │ (influencer  │
  └─────────────┘     │                  │     │  face+voice) │
                      │ ┌──────────────┐ │     └──────────────┘
                      │ │ FaceFusion   │ │
                      │ │ (GPU Worker) │ │
                      │ └──────────────┘ │
                      │ ┌──────────────┐ │
                      │ │ ElevenLabs   │ │
                      │ │ (STS API)    │ │
                      │ └──────────────┘ │
                      └──────────────────┘
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import tempfile
import time
import uuid
from enum import Enum
from typing import Any, Dict, Optional

import httpx

from app.services.face_swap_service import FaceSwapService, FaceSwapError
from app.services.elevenlabs_tts_service import ElevenLabsTTSService, ElevenLabsError

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

TEMP_DIR = os.getenv("VIDEO_PIPELINE_TEMP_DIR", "/tmp/video_pipeline")
os.makedirs(TEMP_DIR, exist_ok=True)

# GPU Worker settings (reuse from face_swap_service)
FACE_SWAP_WORKER_URL = os.getenv("FACE_SWAP_WORKER_URL", "")
FACE_SWAP_WORKER_API_KEY = os.getenv("FACE_SWAP_WORKER_API_KEY", "")


# ──────────────────────────────────────────────
# Job Status Enum
# ──────────────────────────────────────────────

class VideoJobStatus(str, Enum):
    PENDING = "pending"
    UPLOADING = "uploading"
    EXTRACTING_AUDIO = "extracting_audio"
    FACE_SWAPPING = "face_swapping"
    VOICE_CONVERTING = "voice_converting"
    MERGING = "merging"
    UPLOADING_RESULT = "uploading_result"
    COMPLETED = "completed"
    ERROR = "error"


# ──────────────────────────────────────────────
# In-Memory Job Store
# ──────────────────────────────────────────────

# In production, this should be backed by a database
video_pipeline_jobs: Dict[str, Dict[str, Any]] = {}


# ──────────────────────────────────────────────
# Video Pipeline Service
# ──────────────────────────────────────────────

class VideoFaceSwapService:
    """
    Orchestrates the full video face swap + voice conversion pipeline.

    Usage:
        service = VideoFaceSwapService()
        job_id = await service.create_job(
            video_url="https://blob.storage/input.mp4",
            voice_id="ElevenLabs_voice_id",
        )
        # Poll for status
        status = await service.get_job_status(job_id)
    """

    def __init__(self):
        self.face_swap = FaceSwapService()
        self.tts = ElevenLabsTTSService()

    async def create_job(
        self,
        video_url: str,
        voice_id: Optional[str] = None,
        quality: str = "high",
        face_enhancer: bool = True,
        enable_voice_conversion: bool = True,
        remove_background_noise: bool = False,
    ) -> str:
        """
        Create a new video face swap job and start processing.

        Args:
            video_url: URL of the input video (Azure Blob SAS URL)
            voice_id: ElevenLabs voice ID for voice conversion
            quality: Face swap quality preset (fast, balanced, high)
            face_enhancer: Enable GFPGAN face enhancement
            enable_voice_conversion: Whether to convert voice via ElevenLabs
            remove_background_noise: Remove background noise from audio

        Returns:
            job_id: Unique job identifier for polling status
        """
        job_id = f"vfs-{uuid.uuid4().hex[:12]}"

        job = {
            "job_id": job_id,
            "status": VideoJobStatus.PENDING,
            "step": "Job created",
            "progress": 0,
            "error": None,
            "video_url": video_url,
            "voice_id": voice_id or self.tts.voice_id,
            "quality": quality,
            "face_enhancer": face_enhancer,
            "enable_voice_conversion": enable_voice_conversion,
            "remove_background_noise": remove_background_noise,
            "created_at": time.time(),
            "completed_at": None,
            "result_video_url": None,
            "face_swap_job_id": None,
            "duration_sec": 0,
        }

        video_pipeline_jobs[job_id] = job
        logger.info(f"[{job_id}] Video pipeline job created")

        # Start processing in background
        asyncio.create_task(self._run_pipeline(job_id))

        return job_id

    async def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Get the current status of a video pipeline job."""
        if job_id not in video_pipeline_jobs:
            raise ValueError(f"Job {job_id} not found")

        job = video_pipeline_jobs[job_id]
        elapsed = time.time() - job["created_at"]

        return {
            "job_id": job_id,
            "status": job["status"],
            "step": job["step"],
            "progress": job["progress"],
            "error": job.get("error"),
            "elapsed_sec": round(elapsed, 1),
            "duration_sec": job.get("duration_sec", 0),
            "result_video_url": job.get("result_video_url"),
            "enable_voice_conversion": job.get("enable_voice_conversion", True),
        }

    async def list_jobs(self, limit: int = 20) -> list:
        """List recent video pipeline jobs."""
        jobs = sorted(
            video_pipeline_jobs.values(),
            key=lambda j: j["created_at"],
            reverse=True,
        )[:limit]
        return [
            {
                "job_id": j["job_id"],
                "status": j["status"],
                "progress": j["progress"],
                "created_at": j["created_at"],
                "completed_at": j.get("completed_at"),
            }
            for j in jobs
        ]

    async def delete_job(self, job_id: str) -> Dict[str, Any]:
        """Delete a job and cleanup temporary files."""
        if job_id not in video_pipeline_jobs:
            raise ValueError(f"Job {job_id} not found")

        job = video_pipeline_jobs[job_id]

        # Cleanup GPU worker job
        if job.get("face_swap_job_id"):
            try:
                await self.face_swap.delete_video_job(job["face_swap_job_id"])
            except Exception as e:
                logger.warning(f"[{job_id}] Failed to cleanup GPU worker job: {e}")

        # Cleanup temp files
        for suffix in ["_input.mp4", "_audio.mp3", "_swapped.mp4",
                       "_voice.mp3", "_final.mp4"]:
            path = os.path.join(TEMP_DIR, f"{job_id}{suffix}")
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

        del video_pipeline_jobs[job_id]
        return {"status": "deleted", "job_id": job_id}

    # ──────────────────────────────────────────
    # Internal Pipeline
    # ──────────────────────────────────────────

    async def _run_pipeline(self, job_id: str):
        """
        Execute the full video transformation pipeline.

        Steps:
          1. Download input video
          2. Extract audio track
          3. Send video to GPU Worker for face swap
          4. Send audio to ElevenLabs for voice conversion
          5. Merge face-swapped video + converted audio
          6. Upload result
        """
        job = video_pipeline_jobs[job_id]

        try:
            input_path = os.path.join(TEMP_DIR, f"{job_id}_input.mp4")
            audio_path = os.path.join(TEMP_DIR, f"{job_id}_audio.mp3")
            swapped_path = os.path.join(TEMP_DIR, f"{job_id}_swapped.mp4")
            voice_path = os.path.join(TEMP_DIR, f"{job_id}_voice.mp3")
            final_path = os.path.join(TEMP_DIR, f"{job_id}_final.mp4")

            # ── Step 1: Download input video ──
            job["status"] = VideoJobStatus.UPLOADING
            job["step"] = "Downloading input video"
            job["progress"] = 5
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
                                job["progress"] = min(10, int(downloaded / total * 10))

            file_size_mb = os.path.getsize(input_path) / (1024 * 1024)
            logger.info(f"[{job_id}] Downloaded: {file_size_mb:.1f} MB")

            # Get video duration
            try:
                probe = await asyncio.create_subprocess_exec(
                    "ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", input_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await probe.communicate()
                job["duration_sec"] = float(stdout.decode().strip()) if probe.returncode == 0 else 0
            except Exception:
                job["duration_sec"] = 0

            # ── Step 2: Extract audio ──
            job["status"] = VideoJobStatus.EXTRACTING_AUDIO
            job["step"] = "Extracting audio track"
            job["progress"] = 12
            logger.info(f"[{job_id}] Step 2: Extracting audio")

            extract_proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", input_path,
                "-vn", "-acodec", "libmp3lame", "-q:a", "2",
                audio_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await extract_proc.communicate()
            if extract_proc.returncode != 0:
                logger.warning(
                    f"[{job_id}] Audio extraction failed (may have no audio): "
                    f"{stderr.decode()[:200]}"
                )
                # Video might not have audio — continue without voice conversion
                job["enable_voice_conversion"] = False

            # ── Step 3: Face swap (GPU Worker) ──
            job["status"] = VideoJobStatus.FACE_SWAPPING
            job["step"] = "Face swapping video (GPU processing)"
            job["progress"] = 15
            logger.info(f"[{job_id}] Step 3: Starting face swap on GPU worker")

            # Upload video to a temporary accessible URL for the GPU worker
            # We use the input video URL directly since it's already accessible
            fs_job_id = f"fs-{job_id}"
            job["face_swap_job_id"] = fs_job_id

            await self.face_swap.start_video_swap(
                job_id=fs_job_id,
                video_url=job["video_url"],
                quality=job["quality"],
                face_enhancer=job["face_enhancer"],
            )

            # Poll GPU worker for face swap progress
            while True:
                await asyncio.sleep(3)
                fs_status = await self.face_swap.get_video_status(fs_job_id)

                if fs_status["status"] == "completed":
                    job["progress"] = 70
                    break
                elif fs_status["status"] == "error":
                    raise FaceSwapError(
                        f"Face swap failed: {fs_status.get('error', 'unknown')}"
                    )
                else:
                    # Map GPU worker progress (0-100) to pipeline progress (15-70)
                    gpu_progress = fs_status.get("progress", 0)
                    job["progress"] = 15 + int(gpu_progress * 0.55)
                    job["step"] = f"Face swapping: {fs_status.get('step', 'processing')}"

            # Download face-swapped video from GPU worker
            job["step"] = "Downloading face-swapped video"
            download_url = await self.face_swap.get_video_download_url(fs_job_id)

            async with httpx.AsyncClient(
                timeout=300,
                headers={"X-Api-Key": FACE_SWAP_WORKER_API_KEY},
            ) as client:
                resp = await client.get(download_url)
                resp.raise_for_status()
                with open(swapped_path, "wb") as f:
                    f.write(resp.content)

            logger.info(
                f"[{job_id}] Face swap complete: "
                f"{os.path.getsize(swapped_path) / (1024*1024):.1f} MB"
            )

            # ── Step 4: Voice conversion (ElevenLabs STS) ──
            if job["enable_voice_conversion"] and os.path.exists(audio_path):
                job["status"] = VideoJobStatus.VOICE_CONVERTING
                job["step"] = "Converting voice (ElevenLabs)"
                job["progress"] = 75
                logger.info(f"[{job_id}] Step 4: Voice conversion")

                with open(audio_path, "rb") as f:
                    audio_data = f.read()

                audio_size_mb = len(audio_data) / (1024 * 1024)
                logger.info(f"[{job_id}] Audio size: {audio_size_mb:.1f} MB")

                # ElevenLabs has a file size limit; for long videos,
                # we may need to chunk the audio
                MAX_AUDIO_SIZE_MB = 10  # ElevenLabs limit
                if audio_size_mb > MAX_AUDIO_SIZE_MB:
                    logger.warning(
                        f"[{job_id}] Audio too large for STS ({audio_size_mb:.1f}MB). "
                        f"Splitting into chunks..."
                    )
                    converted_audio = await self._convert_audio_chunked(
                        job_id, audio_path, job["voice_id"],
                        job["remove_background_noise"],
                    )
                else:
                    converted_audio = await self.tts.speech_to_speech(
                        audio_data=audio_data,
                        voice_id=job["voice_id"],
                        output_format="mp3_44100_128",
                        remove_background_noise=job["remove_background_noise"],
                    )

                with open(voice_path, "wb") as f:
                    f.write(converted_audio)

                logger.info(
                    f"[{job_id}] Voice conversion complete: "
                    f"{len(converted_audio) / (1024*1024):.2f} MB"
                )
                job["progress"] = 85
            else:
                voice_path = audio_path  # Use original audio
                job["progress"] = 85

            # ── Step 5: Merge video + audio ──
            job["status"] = VideoJobStatus.MERGING
            job["step"] = "Merging video and audio"
            job["progress"] = 88
            logger.info(f"[{job_id}] Step 5: Merging")

            if os.path.exists(voice_path) and os.path.getsize(voice_path) > 0:
                # Merge face-swapped video with converted/original audio
                merge_proc = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-y",
                    "-i", swapped_path,
                    "-i", voice_path,
                    "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k",
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-shortest",
                    final_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await merge_proc.communicate()
                if merge_proc.returncode != 0:
                    logger.error(f"[{job_id}] Merge failed: {stderr.decode()[:300]}")
                    # Fallback: use face-swapped video without audio
                    import shutil
                    shutil.copy2(swapped_path, final_path)
            else:
                # No audio — use face-swapped video as-is
                import shutil
                shutil.copy2(swapped_path, final_path)

            final_size_mb = os.path.getsize(final_path) / (1024 * 1024)
            logger.info(f"[{job_id}] Final video: {final_size_mb:.1f} MB")

            # ── Step 6: Store result ──
            job["status"] = VideoJobStatus.UPLOADING_RESULT
            job["step"] = "Preparing result for download"
            job["progress"] = 95

            # Store the final video path for download
            # In production, upload to Azure Blob Storage
            job["result_video_path"] = final_path
            job["result_video_size_mb"] = round(final_size_mb, 1)

            # ── Done ──
            job["status"] = VideoJobStatus.COMPLETED
            job["step"] = "Pipeline completed"
            job["progress"] = 100
            job["completed_at"] = time.time()
            elapsed = job["completed_at"] - job["created_at"]
            logger.info(
                f"[{job_id}] Pipeline completed in {elapsed:.1f}s. "
                f"Output: {final_size_mb:.1f} MB"
            )

        except Exception as e:
            logger.error(f"[{job_id}] Pipeline failed: {e}", exc_info=True)
            job["status"] = VideoJobStatus.ERROR
            job["step"] = "Error"
            job["error"] = str(e)

    async def _convert_audio_chunked(
        self,
        job_id: str,
        audio_path: str,
        voice_id: str,
        remove_background_noise: bool,
    ) -> bytes:
        """
        Convert audio in chunks for files exceeding ElevenLabs size limit.

        Splits audio into ~5 minute segments, converts each, then concatenates.
        """
        chunk_duration = 300  # 5 minutes per chunk
        chunk_dir = os.path.join(TEMP_DIR, f"{job_id}_chunks")
        os.makedirs(chunk_dir, exist_ok=True)

        try:
            # Split audio into chunks
            split_proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", audio_path,
                "-f", "segment", "-segment_time", str(chunk_duration),
                "-c", "copy",
                os.path.join(chunk_dir, "chunk_%03d.mp3"),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await split_proc.communicate()

            # Get chunk files
            chunk_files = sorted([
                os.path.join(chunk_dir, f)
                for f in os.listdir(chunk_dir)
                if f.startswith("chunk_") and f.endswith(".mp3")
            ])

            if not chunk_files:
                raise RuntimeError("Audio splitting produced no chunks")

            logger.info(f"[{job_id}] Split audio into {len(chunk_files)} chunks")

            # Convert each chunk
            converted_chunks = []
            for i, chunk_file in enumerate(chunk_files):
                logger.info(f"[{job_id}] Converting chunk {i+1}/{len(chunk_files)}")
                with open(chunk_file, "rb") as f:
                    chunk_data = f.read()

                converted = await self.tts.speech_to_speech(
                    audio_data=chunk_data,
                    voice_id=voice_id,
                    output_format="mp3_44100_128",
                    remove_background_noise=remove_background_noise,
                )

                converted_path = os.path.join(chunk_dir, f"converted_{i:03d}.mp3")
                with open(converted_path, "wb") as f:
                    f.write(converted)
                converted_chunks.append(converted_path)

            # Concatenate converted chunks
            concat_list = os.path.join(chunk_dir, "concat.txt")
            with open(concat_list, "w") as f:
                for path in converted_chunks:
                    f.write(f"file '{path}'\n")

            output_path = os.path.join(chunk_dir, "merged.mp3")
            concat_proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", concat_list, "-c", "copy", output_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await concat_proc.communicate()

            with open(output_path, "rb") as f:
                return f.read()

        finally:
            # Cleanup chunk directory
            import shutil
            shutil.rmtree(chunk_dir, ignore_errors=True)

    async def get_result_video_path(self, job_id: str) -> Optional[str]:
        """Get the local file path of the completed video."""
        if job_id not in video_pipeline_jobs:
            return None
        job = video_pipeline_jobs[job_id]
        if job["status"] != VideoJobStatus.COMPLETED:
            return None
        return job.get("result_video_path")
