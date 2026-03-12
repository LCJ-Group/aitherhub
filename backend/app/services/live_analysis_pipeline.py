"""
LiveBoost Analysis Pipeline – Worker-side processing service.

This module implements the full analysis pipeline for live-stream videos
captured by the LiveBoost Companion App.

Pipeline steps:
  1. assembling     – Concatenate uploaded chunks into a single video
  2. audio_extraction – Extract audio track from assembled video
  3. speech_to_text – Transcribe audio using STT (Whisper / Azure STT)
  4. ocr_processing – Run OCR on video frames (sales pop, comments)
  5. sales_detection – Detect sales moments from combined signals
  6. clip_generation – Generate clip candidates from detected moments

This service is designed to be called by the Azure Queue worker.

Architecture note:
  Live Boost App は将来的に Live Commerce Data OS のデータ収集基盤になるため、
  拡張可能な構造で設計しています。
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update, text

from app.models.orm.live_analysis_job import LiveAnalysisJob

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Pipeline Step Definitions
# ──────────────────────────────────────────────

PIPELINE_STEPS = [
    {"name": "assembling", "label": "Assembling video chunks", "weight": 0.10},
    {"name": "audio_extraction", "label": "Extracting audio track", "weight": 0.10},
    {"name": "speech_to_text", "label": "Transcribing speech", "weight": 0.25},
    {"name": "ocr_processing", "label": "Processing OCR (sales pop / comments)", "weight": 0.25},
    {"name": "sales_detection", "label": "Detecting sales moments", "weight": 0.15},
    {"name": "clip_generation", "label": "Generating clip candidates", "weight": 0.15},
]


class LiveAnalysisPipeline:
    """
    Orchestrates the full analysis pipeline for a single live-stream video.

    Usage (from worker):
        pipeline = LiveAnalysisPipeline(db_session)
        await pipeline.run(job_id="...", video_id="...", email="...", total_chunks=42)
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    # ──────────────────────────────────────────
    # Main entry point
    # ──────────────────────────────────────────

    async def run(
        self,
        job_id: str,
        video_id: str,
        email: str,
        total_chunks: Optional[int] = None,
        stream_source: str = "tiktok_live",
    ) -> Dict[str, Any]:
        """
        Execute the full analysis pipeline.

        Returns the analysis results dict on success.
        Raises on unrecoverable failure (after updating job status to 'failed').
        """
        job_uuid = uuid.UUID(job_id)
        logger.info(f"[pipeline] Starting analysis job={job_id} video={video_id}")

        try:
            # Step 1: Assemble chunks
            await self._update_step(job_uuid, "assembling", 0.0, video_id=video_id)
            assembled_path = await self._assemble_chunks(
                video_id=video_id,
                email=email,
                total_chunks=total_chunks,
            )
            await self._update_step(job_uuid, "assembling", 0.10, video_id=video_id)

            # Step 2: Extract audio
            await self._update_step(job_uuid, "audio_extraction", 0.10, video_id=video_id)
            audio_path = await self._extract_audio(assembled_path)
            await self._update_step(job_uuid, "audio_extraction", 0.20, video_id=video_id)

            # Step 3: Speech to Text
            await self._update_step(job_uuid, "speech_to_text", 0.20, video_id=video_id)
            transcript = await self._speech_to_text(audio_path)
            await self._update_step(job_uuid, "speech_to_text", 0.45, video_id=video_id)

            # Step 4: OCR Processing
            await self._update_step(job_uuid, "ocr_processing", 0.45, video_id=video_id)
            ocr_results = await self._ocr_processing(assembled_path)
            await self._update_step(job_uuid, "ocr_processing", 0.70, video_id=video_id)

            # Step 5: Sales Moment Detection
            await self._update_step(job_uuid, "sales_detection", 0.70, video_id=video_id)
            sales_moments = await self._detect_sales_moments(
                transcript=transcript,
                ocr_results=ocr_results,
                stream_source=stream_source,
            )
            await self._update_step(job_uuid, "sales_detection", 0.85, video_id=video_id)

            # Step 6: Clip Generation
            await self._update_step(job_uuid, "clip_generation", 0.85, video_id=video_id)
            clips = await self._generate_clips(
                assembled_path=assembled_path,
                sales_moments=sales_moments,
                video_id=video_id,
                email=email,
            )
            await self._update_step(job_uuid, "clip_generation", 1.0, video_id=video_id)

            # Build final results
            results = {
                "top_sales_moments": sales_moments,
                "hook_candidates": self._extract_hooks(transcript, sales_moments),
                "clip_candidates": clips,
                "total_duration_seconds": await self._get_duration(assembled_path),
                "total_sales_detected": len(sales_moments),
            }

            # Mark completed
            await self.db.execute(
                update(LiveAnalysisJob)
                .where(LiveAnalysisJob.id == job_uuid)
                .values(
                    status="completed",
                    current_step="Analysis complete",
                    progress=1.0,
                    completed_at=datetime.now(timezone.utc),
                    results=results,
                )
            )

            # BUILD 28: Mark videos table as DONE
            try:
                duration = results.get("total_duration_seconds")
                await self.db.execute(
                    text("""
                        UPDATE videos
                        SET status = 'DONE',
                            step_progress = 100,
                            duration = :duration,
                            updated_at = now()
                        WHERE id = :video_id
                    """),
                    {"video_id": video_id, "duration": duration},
                )
            except Exception as e:
                logger.debug(f"[pipeline] Non-critical: video DONE sync failed: {e}")

            await self.db.commit()

            logger.info(
                f"[pipeline] Completed job={job_id} "
                f"sales_moments={len(sales_moments)} clips={len(clips)}"
            )

            # Cleanup temp files
            await self._cleanup(assembled_path, audio_path)

            return results

        except Exception as exc:
            logger.exception(f"[pipeline] Failed job={job_id}: {exc}")
            try:
                await self.db.execute(
                    update(LiveAnalysisJob)
                    .where(LiveAnalysisJob.id == job_uuid)
                    .values(
                        status="failed",
                        error_message=str(exc)[:2000],
                    )
                )
                # BUILD 28: Mark videos table as ERROR
                try:
                    await self.db.execute(
                        text("""
                            UPDATE videos
                            SET status = 'ERROR', updated_at = now()
                            WHERE id = :video_id
                        """),
                        {"video_id": video_id},
                    )
                except Exception:
                    pass
                await self.db.commit()
            except Exception as _e:
                logger.debug(f"Suppressed: {_e}")
            raise

    # ──────────────────────────────────────────
    # Step update helper
    # ──────────────────────────────────────────

    # BUILD 28: Map LiveBoost pipeline steps to AitherHub video status values
    # so that the videos table status stays compatible with the existing
    # progress/status display system.
    _STEP_TO_VIDEO_STATUS = {
        "assembling":       "STEP_COMPRESS_1080P",
        "audio_extraction": "STEP_0_EXTRACT_FRAMES",
        "speech_to_text":   "STEP_3_TRANSCRIBE_AUDIO",
        "ocr_processing":   "STEP_4_IMAGE_CAPTION",
        "sales_detection":  "STEP_5_BUILD_PHASE_UNITS",
        "clip_generation":  "STEP_13_BUILD_REPORTS",
    }

    async def _sync_video_status(
        self,
        video_id: str,
        step_name: str,
        progress: float,
    ) -> None:
        """BUILD 28: Sync the videos table status with pipeline progress.

        Maps LiveBoost pipeline steps to existing AitherHub STEP_* status
        values so the History UI shows correct progress indicators.
        """
        video_status = self._STEP_TO_VIDEO_STATUS.get(step_name, "processing")
        # Convert 0.0-1.0 progress to 0-100 step_progress
        step_progress = min(int(progress * 100), 100)
        try:
            await self.db.execute(
                text("""
                    UPDATE videos
                    SET status = :status,
                        step_progress = :step_progress,
                        updated_at = now()
                    WHERE id = :video_id
                """),
                {
                    "video_id": video_id,
                    "status": video_status,
                    "step_progress": step_progress,
                },
            )
        except Exception as e:
            logger.debug(f"[pipeline] Non-critical: video status sync failed: {e}")

    async def _update_step(
        self,
        job_id: uuid.UUID,
        step_name: str,
        progress: float,
        video_id: str | None = None,
    ) -> None:
        """Update the job's current step and progress in the database."""
        step_info = next(
            (s for s in PIPELINE_STEPS if s["name"] == step_name),
            None,
        )
        label = step_info["label"] if step_info else step_name

        await self.db.execute(
            update(LiveAnalysisJob)
            .where(LiveAnalysisJob.id == job_id)
            .values(
                status=step_name,
                current_step=label,
                progress=round(progress, 3),
            )
        )

        # BUILD 28: Also sync to videos table
        if video_id:
            await self._sync_video_status(video_id, step_name, progress)

        await self.db.commit()
        logger.info(f"[pipeline] step={step_name} progress={progress:.1%}")

    # ──────────────────────────────────────────
    # Step 1: Assemble Chunks
    # ──────────────────────────────────────────

    async def _assemble_chunks(
        self,
        video_id: str,
        email: str,
        total_chunks: Optional[int] = None,
    ) -> str:
        """
        Download all chunks from blob storage and concatenate into a single video.

        Uses ffmpeg concat demuxer for lossless concatenation of H.264 chunks.

        Returns the local path to the assembled video file.
        """
        from app.services.storage_service import generate_download_sas

        work_dir = tempfile.mkdtemp(prefix=f"liveboost_{video_id}_")
        chunk_dir = os.path.join(work_dir, "chunks")
        os.makedirs(chunk_dir, exist_ok=True)

        # Determine chunk count
        if total_chunks is None:
            # Try to discover chunks by probing blob storage
            total_chunks = await self._discover_chunk_count(email, video_id)

        if total_chunks == 0:
            raise ValueError(
                f"BUILD 33: No chunks found in blob storage for video_id={video_id} "
                f"email={email}. This usually means the iOS app failed to upload "
                f"chunks before calling /start. Check ChunkUploadService logs on device."
            )

        # Download each chunk
        import aiohttp

        chunk_paths = []
        async with aiohttp.ClientSession() as session:
            for i in range(total_chunks):
                chunk_filename = f"chunks/chunk_{i:04d}.mp4"
                try:
                    download_url, _ = await generate_download_sas(
                        email=email,
                        video_id=video_id,
                        filename=chunk_filename,
                        expires_in_minutes=60,
                    )

                    local_path = os.path.join(chunk_dir, f"chunk_{i:04d}.mp4")
                    async with session.get(download_url) as resp:
                        if resp.status == 200:
                            with open(local_path, "wb") as f:
                                async for data in resp.content.iter_chunked(1024 * 1024):
                                    f.write(data)
                            chunk_paths.append(local_path)
                            logger.info(f"[assemble] Downloaded chunk {i}/{total_chunks}")
                        else:
                            logger.warning(
                                f"[assemble] Chunk {i} download failed: HTTP {resp.status}"
                            )
                except Exception as e:
                    logger.warning(f"[assemble] Failed to download chunk {i}: {e}")

        if not chunk_paths:
            raise ValueError(
                f"BUILD 33: No chunks could be downloaded for video_id={video_id}. "
                f"Expected {total_chunks} chunks but downloaded 0. "
                f"Blob path: {email}/{video_id}/chunks/chunk_XXXX.mp4"
            )

        # Create ffmpeg concat list
        concat_list_path = os.path.join(work_dir, "concat_list.txt")
        with open(concat_list_path, "w") as f:
            for path in sorted(chunk_paths):
                f.write(f"file '{path}'\n")

        # Concatenate using ffmpeg
        output_path = os.path.join(work_dir, f"{video_id}_assembled.mp4")
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_list_path,
            "-c", "copy",
            output_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(
                f"ffmpeg concat failed (rc={proc.returncode}): {stderr.decode()[:500]}"
            )

        logger.info(
            f"[assemble] Assembled {len(chunk_paths)} chunks → {output_path}"
        )
        return output_path

    async def _discover_chunk_count(self, email: str, video_id: str) -> int:
        """Probe blob storage to discover how many chunks exist."""
        from app.services.storage_service import generate_download_sas
        import aiohttp

        count = 0
        async with aiohttp.ClientSession() as session:
            for i in range(10000):  # Safety limit
                chunk_filename = f"chunks/chunk_{i:04d}.mp4"
                try:
                    download_url, _ = await generate_download_sas(
                        email=email,
                        video_id=video_id,
                        filename=chunk_filename,
                        expires_in_minutes=5,
                    )
                    async with session.head(download_url) as resp:
                        if resp.status == 200:
                            count += 1
                        else:
                            break
                except Exception:
                    break
        logger.info(f"[assemble] Discovered {count} chunks for video={video_id}")
        return count

    # ──────────────────────────────────────────
    # Step 2: Audio Extraction
    # ──────────────────────────────────────────

    async def _extract_audio(self, video_path: str) -> str:
        """
        Extract audio track from video using ffmpeg.

        Returns path to the extracted WAV file (16kHz mono for STT).
        """
        audio_path = video_path.replace(".mp4", "_audio.wav")

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y",
            "-i", video_path,
            "-vn",                    # No video
            "-acodec", "pcm_s16le",   # PCM 16-bit
            "-ar", "16000",           # 16kHz for Whisper
            "-ac", "1",               # Mono
            audio_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(
                f"Audio extraction failed (rc={proc.returncode}): {stderr.decode()[:500]}"
            )

        logger.info(f"[audio] Extracted audio → {audio_path}")
        return audio_path

    # ──────────────────────────────────────────
    # Step 3: Speech to Text
    # ──────────────────────────────────────────

    async def _speech_to_text(self, audio_path: str) -> List[Dict[str, Any]]:
        """
        Transcribe audio using OpenAI Whisper API or local Whisper model.

        Returns a list of transcript segments:
          [{"start": 0.0, "end": 5.2, "text": "..."}]
        """
        try:
            # Try OpenAI Whisper API first
            return await self._stt_openai_whisper(audio_path)
        except Exception as e:
            logger.warning(f"[stt] OpenAI Whisper failed, trying local: {e}")
            try:
                return await self._stt_local_whisper(audio_path)
            except Exception as e2:
                logger.error(f"[stt] Local Whisper also failed: {e2}")
                return []

    async def _stt_openai_whisper(self, audio_path: str) -> List[Dict[str, Any]]:
        """Transcribe using OpenAI Whisper API."""
        import openai

        client = openai.AsyncOpenAI()

        # Split audio into 25MB chunks if needed (Whisper API limit)
        file_size = os.path.getsize(audio_path)
        max_size = 25 * 1024 * 1024  # 25MB

        if file_size <= max_size:
            with open(audio_path, "rb") as f:
                response = await client.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                    response_format="verbose_json",
                    language="ja",
                    timestamp_granularities=["segment"],
                )
            segments = []
            if hasattr(response, "segments"):
                for seg in response.segments:
                    segments.append({
                        "start": seg.get("start", seg.start if hasattr(seg, "start") else 0),
                        "end": seg.get("end", seg.end if hasattr(seg, "end") else 0),
                        "text": seg.get("text", seg.text if hasattr(seg, "text") else ""),
                    })
            return segments
        else:
            # Split and transcribe in parts
            return await self._stt_chunked_whisper(audio_path, max_size)

    async def _stt_chunked_whisper(
        self, audio_path: str, max_size: int
    ) -> List[Dict[str, Any]]:
        """Split large audio and transcribe each part."""
        import openai

        client = openai.AsyncOpenAI()
        duration = await self._get_audio_duration(audio_path)
        chunk_duration = 600  # 10 minutes per chunk
        segments = []
        offset = 0.0

        while offset < duration:
            chunk_path = audio_path.replace(".wav", f"_part_{int(offset)}.wav")
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y",
                "-i", audio_path,
                "-ss", str(offset),
                "-t", str(chunk_duration),
                "-acodec", "pcm_s16le",
                "-ar", "16000",
                "-ac", "1",
                chunk_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()

            if os.path.exists(chunk_path) and os.path.getsize(chunk_path) > 0:
                try:
                    with open(chunk_path, "rb") as f:
                        response = await client.audio.transcriptions.create(
                            model="whisper-1",
                            file=f,
                            response_format="verbose_json",
                            language="ja",
                            timestamp_granularities=["segment"],
                        )
                    if hasattr(response, "segments"):
                        for seg in response.segments:
                            start = seg.get("start", getattr(seg, "start", 0))
                            end = seg.get("end", getattr(seg, "end", 0))
                            text = seg.get("text", getattr(seg, "text", ""))
                            segments.append({
                                "start": start + offset,
                                "end": end + offset,
                                "text": text,
                            })
                except Exception as e:
                    logger.warning(f"[stt] Chunk at {offset}s failed: {e}")

                # Cleanup chunk
                try:
                    os.remove(chunk_path)
                except Exception as _e:
                    logger.debug(f"Suppressed: {_e}")

            offset += chunk_duration

        return segments

    async def _stt_local_whisper(self, audio_path: str) -> List[Dict[str, Any]]:
        """Transcribe using local Whisper model (fallback)."""
        try:
            import whisper

            model = whisper.load_model("base")
            result = model.transcribe(audio_path, language="ja")
            return [
                {
                    "start": seg["start"],
                    "end": seg["end"],
                    "text": seg["text"],
                }
                for seg in result.get("segments", [])
            ]
        except ImportError:
            logger.warning("[stt] Local whisper not installed")
            return []

    async def _get_audio_duration(self, audio_path: str) -> float:
        """Get audio duration in seconds using ffprobe."""
        proc = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        try:
            return float(stdout.decode().strip())
        except (ValueError, AttributeError):
            return 0.0

    # ──────────────────────────────────────────
    # Step 4: OCR Processing
    # ──────────────────────────────────────────

    async def _ocr_processing(self, video_path: str) -> List[Dict[str, Any]]:
        """
        Extract frames at intervals and run OCR to detect:
          - Sales pop notifications
          - Comment text overlays
          - Product names / prices

        Returns a list of OCR results with timestamps.
        """
        work_dir = os.path.dirname(video_path)
        frames_dir = os.path.join(work_dir, "frames")
        os.makedirs(frames_dir, exist_ok=True)

        # Extract frames every 5 seconds
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", "fps=1/5",  # 1 frame every 5 seconds
            "-q:v", "2",
            os.path.join(frames_dir, "frame_%06d.jpg"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        # Process frames with OCR
        ocr_results = []
        frame_files = sorted(
            f for f in os.listdir(frames_dir) if f.endswith(".jpg")
        )

        for i, frame_file in enumerate(frame_files):
            frame_path = os.path.join(frames_dir, frame_file)
            timestamp = i * 5.0  # 5-second intervals

            try:
                detections = await self._ocr_single_frame(frame_path)
                if detections:
                    ocr_results.append({
                        "timestamp": timestamp,
                        "frame": frame_file,
                        "detections": detections,
                    })
            except Exception as e:
                logger.warning(f"[ocr] Frame {frame_file} failed: {e}")

        logger.info(f"[ocr] Processed {len(frame_files)} frames, {len(ocr_results)} with detections")
        return ocr_results

    async def _ocr_single_frame(self, frame_path: str) -> List[Dict[str, Any]]:
        """
        Run OCR on a single frame using OpenAI Vision API.

        Detects:
          - Sales pop notifications (e.g., "○○さんが購入しました")
          - Product names and prices
          - Comment overlays
        """
        import openai
        import base64

        client = openai.AsyncOpenAI()

        with open(frame_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

        try:
            response = await client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an OCR assistant specialized in TikTok live commerce streams. "
                            "Analyze the frame and extract:\n"
                            "1. Sales pop notifications (e.g., 'XXさんが購入しました')\n"
                            "2. Product names and prices visible on screen\n"
                            "3. Comment text overlays\n"
                            "Return JSON array of detections. Each detection has: "
                            "type (sales_pop|product|comment), text, confidence (0-1).\n"
                            "If nothing detected, return empty array []."
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image_data}",
                                },
                            },
                        ],
                    },
                ],
                max_tokens=500,
                temperature=0.1,
            )

            import json
            content = response.choices[0].message.content.strip()
            # Try to parse JSON from response
            if content.startswith("["):
                return json.loads(content)
            elif "```json" in content:
                json_str = content.split("```json")[1].split("```")[0].strip()
                return json.loads(json_str)
            elif "```" in content:
                json_str = content.split("```")[1].split("```")[0].strip()
                return json.loads(json_str)
            else:
                return []

        except Exception as e:
            logger.warning(f"[ocr] Vision API failed for {frame_path}: {e}")
            return []

    # ──────────────────────────────────────────
    # Step 5: Sales Moment Detection
    # ──────────────────────────────────────────

    async def _detect_sales_moments(
        self,
        transcript: List[Dict[str, Any]],
        ocr_results: List[Dict[str, Any]],
        stream_source: str = "tiktok_live",
    ) -> List[Dict[str, Any]]:
        """
        Combine transcript and OCR signals to detect sales moments.

        Detection signals:
          1. Sales pop OCR detections (highest confidence)
          2. Verbal CTAs in transcript ("今すぐ購入", "カートに入れて", etc.)
          3. Comment surges (multiple comments in short window)
          4. Price mentions in transcript

        Returns sorted list of sales moments by confidence.
        """
        moments = []

        # Signal 1: Sales pop from OCR
        for ocr in ocr_results:
            for det in ocr.get("detections", []):
                if det.get("type") == "sales_pop":
                    moments.append({
                        "timestamp_start": ocr["timestamp"],
                        "timestamp_end": ocr["timestamp"] + 10.0,
                        "product_name": self._extract_product_from_sales_pop(det.get("text", "")),
                        "confidence": min(det.get("confidence", 0.8), 1.0),
                        "trigger_type": "sales_pop",
                        "transcript_snippet": self._get_transcript_at(
                            transcript, ocr["timestamp"]
                        ),
                    })

        # Signal 2: Verbal CTAs in transcript
        cta_keywords = [
            "購入", "カートに入れ", "今すぐ", "買って", "ポチ",
            "セール", "限定", "残り", "ラスト", "売り切れ",
            "お得", "値下げ", "割引", "クーポン",
        ]
        for seg in transcript:
            text = seg.get("text", "")
            for kw in cta_keywords:
                if kw in text:
                    moments.append({
                        "timestamp_start": seg["start"],
                        "timestamp_end": seg["end"],
                        "product_name": None,
                        "confidence": 0.6,
                        "trigger_type": "verbal_cta",
                        "transcript_snippet": text,
                    })
                    break

        # Signal 3: Product mentions from OCR
        for ocr in ocr_results:
            for det in ocr.get("detections", []):
                if det.get("type") == "product":
                    moments.append({
                        "timestamp_start": ocr["timestamp"],
                        "timestamp_end": ocr["timestamp"] + 15.0,
                        "product_name": det.get("text", ""),
                        "confidence": min(det.get("confidence", 0.5), 1.0),
                        "trigger_type": "product_display",
                        "transcript_snippet": self._get_transcript_at(
                            transcript, ocr["timestamp"]
                        ),
                    })

        # Deduplicate and merge overlapping moments
        moments = self._merge_overlapping_moments(moments)

        # Sort by confidence descending
        moments.sort(key=lambda m: m["confidence"], reverse=True)

        # Limit to top 50 moments
        return moments[:50]

    def _extract_product_from_sales_pop(self, text: str) -> Optional[str]:
        """Extract product name from sales pop text like 'XXさんがYYを購入しました'."""
        if "を購入" in text:
            parts = text.split("を購入")
            if parts:
                name_part = parts[0]
                if "が" in name_part:
                    return name_part.split("が")[-1].strip()
        return None

    def _get_transcript_at(
        self, transcript: List[Dict[str, Any]], timestamp: float
    ) -> Optional[str]:
        """Get transcript text near a given timestamp."""
        window = 10.0  # seconds
        snippets = []
        for seg in transcript:
            if abs(seg["start"] - timestamp) < window:
                snippets.append(seg["text"])
        return " ".join(snippets) if snippets else None

    def _merge_overlapping_moments(
        self, moments: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Merge moments that overlap within 15 seconds."""
        if not moments:
            return []

        moments.sort(key=lambda m: m["timestamp_start"])
        merged = [moments[0]]

        for m in moments[1:]:
            last = merged[-1]
            if m["timestamp_start"] <= last["timestamp_end"] + 15.0:
                # Merge: extend end time, keep higher confidence
                last["timestamp_end"] = max(last["timestamp_end"], m["timestamp_end"])
                if m["confidence"] > last["confidence"]:
                    last["confidence"] = m["confidence"]
                    last["trigger_type"] = m["trigger_type"]
                if m.get("product_name") and not last.get("product_name"):
                    last["product_name"] = m["product_name"]
                if m.get("transcript_snippet"):
                    existing = last.get("transcript_snippet") or ""
                    last["transcript_snippet"] = (
                        existing + " " + m["transcript_snippet"]
                    ).strip()
            else:
                merged.append(m)

        return merged

    # ──────────────────────────────────────────
    # Step 6: Clip Generation
    # ──────────────────────────────────────────

    async def _generate_clips(
        self,
        assembled_path: str,
        sales_moments: List[Dict[str, Any]],
        video_id: str,
        email: str,
    ) -> List[Dict[str, Any]]:
        """
        Generate clip candidates from detected sales moments.

        Each clip is a short segment (15-60s) centered on a sales moment,
        suitable for short-form content (TikTok, Reels, Shorts).
        """
        from app.services.storage_service import generate_upload_sas

        clips = []
        work_dir = os.path.dirname(assembled_path)
        clips_dir = os.path.join(work_dir, "clips")
        os.makedirs(clips_dir, exist_ok=True)

        # Generate clips for top 10 moments
        top_moments = sales_moments[:10]

        for i, moment in enumerate(top_moments):
            try:
                # Calculate clip boundaries (30s before, 30s after center)
                center = (moment["timestamp_start"] + moment["timestamp_end"]) / 2
                clip_start = max(0, center - 30)
                clip_duration = 60.0  # 60-second clips

                clip_filename = f"clip_{i:03d}.mp4"
                clip_path = os.path.join(clips_dir, clip_filename)

                # Extract clip using ffmpeg
                proc = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-y",
                    "-i", assembled_path,
                    "-ss", str(clip_start),
                    "-t", str(clip_duration),
                    "-c:v", "libx264",
                    "-preset", "fast",
                    "-crf", "23",
                    "-c:a", "aac",
                    "-b:a", "128k",
                    clip_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()

                if proc.returncode == 0 and os.path.exists(clip_path):
                    # Upload clip to blob storage
                    clip_blob_name = f"clips/{clip_filename}"
                    try:
                        _, upload_url, blob_url, _ = await generate_upload_sas(
                            email=email,
                            video_id=video_id,
                            filename=clip_blob_name,
                        )

                        # Upload the clip
                        import aiohttp
                        async with aiohttp.ClientSession() as session:
                            with open(clip_path, "rb") as f:
                                clip_data = f.read()
                            async with session.put(
                                upload_url,
                                data=clip_data,
                                headers={
                                    "x-ms-blob-type": "BlockBlob",
                                    "Content-Type": "video/mp4",
                                },
                            ) as resp:
                                if resp.status in (200, 201):
                                    clips.append({
                                        "timestamp_start": clip_start,
                                        "timestamp_end": clip_start + clip_duration,
                                        "title": moment.get("product_name") or f"Sales Moment #{i+1}",
                                        "score": moment["confidence"],
                                        "clip_url": blob_url,
                                    })
                                    logger.info(f"[clips] Uploaded clip {i}")
                    except Exception as e:
                        logger.warning(f"[clips] Failed to upload clip {i}: {e}")
                        clips.append({
                            "timestamp_start": clip_start,
                            "timestamp_end": clip_start + clip_duration,
                            "title": moment.get("product_name") or f"Sales Moment #{i+1}",
                            "score": moment["confidence"],
                            "clip_url": None,
                        })

            except Exception as e:
                logger.warning(f"[clips] Failed to generate clip {i}: {e}")

        return clips

    # ──────────────────────────────────────────
    # Hook Extraction
    # ──────────────────────────────────────────

    def _extract_hooks(
        self,
        transcript: List[Dict[str, Any]],
        sales_moments: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Extract hook candidates – attention-grabbing moments
        that could serve as video openers.
        """
        hooks = []

        # Hook from high-confidence sales moments
        for moment in sales_moments[:5]:
            hooks.append({
                "timestamp": moment["timestamp_start"],
                "hook_text": moment.get("transcript_snippet", "")[:100],
                "score": moment["confidence"],
            })

        # Hook from energetic transcript segments
        energy_keywords = [
            "すごい", "やばい", "最高", "大人気", "完売",
            "ありがとう", "嬉しい", "みんな",
        ]
        for seg in transcript:
            text = seg.get("text", "")
            for kw in energy_keywords:
                if kw in text:
                    hooks.append({
                        "timestamp": seg["start"],
                        "hook_text": text[:100],
                        "score": 0.5,
                    })
                    break

        # Deduplicate and sort
        seen_timestamps = set()
        unique_hooks = []
        for h in hooks:
            t_key = round(h["timestamp"] / 10) * 10
            if t_key not in seen_timestamps:
                seen_timestamps.add(t_key)
                unique_hooks.append(h)

        unique_hooks.sort(key=lambda h: h["score"], reverse=True)
        return unique_hooks[:20]

    # ──────────────────────────────────────────
    # Utility
    # ──────────────────────────────────────────

    async def _get_duration(self, video_path: str) -> Optional[float]:
        """Get video duration in seconds."""
        proc = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        try:
            return float(stdout.decode().strip())
        except (ValueError, AttributeError):
            return None

    async def _cleanup(self, *paths: str) -> None:
        """Remove temporary files and directories."""
        import shutil

        for path in paths:
            if not path:
                continue
            try:
                parent_dir = os.path.dirname(path)
                if parent_dir and "liveboost_" in parent_dir:
                    shutil.rmtree(parent_dir, ignore_errors=True)
                    logger.info(f"[cleanup] Removed {parent_dir}")
                elif os.path.isfile(path):
                    os.remove(path)
            except Exception as e:
                logger.warning(f"[cleanup] Failed to remove {path}: {e}")
