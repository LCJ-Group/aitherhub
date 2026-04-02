#!/usr/bin/env python3
"""
AitherHub GPU Encoding API
===========================
FastAPI service running on the GPU VM (NC4as_T4_v3) that handles
video encoding jobs offloaded from the App Service (B1).

Uses NVIDIA NVENC (h264_nvenc) for hardware-accelerated encoding,
which is 10-50x faster than CPU-based libx264 on B1.

Start:
    python -m worker.encoding_api
    # or: uvicorn worker.encoding_api:app --host 0.0.0.0 --port 8765

Endpoints:
    POST /encode          — Submit an encoding job (subtitle burn-in)
    GET  /encode/{job_id} — Poll job status
    GET  /health          — Health check
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("encoding_api")

# ── Config ───────────────────────────────────────────────────────────────────
FFMPEG_BIN = os.getenv("FFMPEG_PATH", "ffmpeg")
FFPROBE_BIN = os.getenv("FFPROBE_PATH", "ffprobe")
NVENC_ENCODER = "h264_nvenc"
CPU_ENCODER = "libx264"

# Azure Blob Storage
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
AZURE_BLOB_CONTAINER = os.getenv("AZURE_BLOB_CONTAINER", "videos")

# Timeouts
ENCODE_TIMEOUT = int(os.getenv("ENCODE_TIMEOUT", "1800"))  # 30 min max
DOWNLOAD_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT", "300"))  # 5 min

# Temp directory
TEMP_BASE = os.getenv("ENCODE_TEMP_DIR", "/tmp/encoding_jobs")
os.makedirs(TEMP_BASE, exist_ok=True)

# ── Job Store (in-memory) ────────────────────────────────────────────────────
_jobs: dict = {}
_jobs_lock = Lock()

# ── FastAPI App ──────────────────────────────────────────────────────────────
app = FastAPI(title="AitherHub GPU Encoding API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Models ───────────────────────────────────────────────────────────────────
class CaptionItem(BaseModel):
    text: str
    start: float
    end: float
    style: Optional[str] = None


class EncodeRequest(BaseModel):
    job_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    video_id: str
    clip_url: str  # SAS URL to download the source video
    captions: list[CaptionItem] = []
    style: str = "default"
    position_x: float = 50.0
    position_y: float = 85.0
    split_segments: Optional[list[dict]] = None
    # Caller can request specific encoder
    force_cpu: bool = False


class EncodeResponse(BaseModel):
    job_id: str
    status: str
    message: str


class JobStatus(BaseModel):
    job_id: str
    status: str  # queued, downloading, encoding, uploading, done, failed
    progress_pct: int = 0
    download_url: Optional[str] = None
    file_size: Optional[int] = None
    error: Optional[str] = None
    encoder: Optional[str] = None
    encode_time_sec: Optional[float] = None


# ── Helper: Check NVENC availability ─────────────────────────────────────────
_nvenc_available: Optional[bool] = None


def check_nvenc() -> bool:
    """Check if NVENC encoder is available."""
    global _nvenc_available
    if _nvenc_available is not None:
        return _nvenc_available
    try:
        result = subprocess.run(
            [FFMPEG_BIN, "-encoders"],
            capture_output=True, text=True, timeout=10,
        )
        _nvenc_available = NVENC_ENCODER in result.stdout
        logger.info(f"NVENC available: {_nvenc_available}")
    except Exception as e:
        logger.warning(f"NVENC check failed: {e}")
        _nvenc_available = False
    return _nvenc_available


# ── Helper: Job management ───────────────────────────────────────────────────
def _update_job(job_id: str, **kwargs):
    with _jobs_lock:
        if job_id not in _jobs:
            _jobs[job_id] = {"job_id": job_id, "status": "queued", "progress_pct": 0}
        _jobs[job_id].update(kwargs)


def _get_job(job_id: str) -> Optional[dict]:
    with _jobs_lock:
        return _jobs.get(job_id, {}).copy() if job_id in _jobs else None


# ── Helper: Generate ASS subtitle content ────────────────────────────────────
def generate_ass_content(
    captions: list[CaptionItem],
    style: str,
    video_w: int,
    video_h: int,
    pos_x: float = 50.0,
    pos_y: float = 85.0,
) -> str:
    """Generate ASS subtitle file content from captions."""
    # Calculate font size based on video resolution
    base_font_size = max(int(video_h * 0.055), 28)

    # Style presets
    style_configs = {
        "default": {
            "font": "Noto Sans CJK JP",
            "bold": 1,
            "outline": 3,
            "shadow": 1,
            "primary_color": "&H00FFFFFF",
            "outline_color": "&H00000000",
        },
        "yellow": {
            "font": "Noto Sans CJK JP",
            "bold": 1,
            "outline": 3,
            "shadow": 1,
            "primary_color": "&H0000FFFF",
            "outline_color": "&H00000000",
        },
        "neon": {
            "font": "Noto Sans CJK JP",
            "bold": 1,
            "outline": 4,
            "shadow": 2,
            "primary_color": "&H0000FF00",
            "outline_color": "&H00FF00FF",
        },
    }
    cfg = style_configs.get(style, style_configs["default"])

    # Calculate alignment position
    margin_v = int(video_h * (100 - pos_y) / 100)

    header = f"""[Script Info]
Title: AitherHub Export
ScriptType: v4.00+
PlayResX: {video_w}
PlayResY: {video_h}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{cfg['font']},{base_font_size},{cfg['primary_color']},&H000000FF,{cfg['outline_color']},&H80000000,{cfg['bold']},0,0,0,100,100,0,0,1,{cfg['outline']},{cfg['shadow']},2,10,10,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for cap in captions:
        start_h = int(cap.start // 3600)
        start_m = int((cap.start % 3600) // 60)
        start_s = cap.start % 60
        end_h = int(cap.end // 3600)
        end_m = int((cap.end % 3600) // 60)
        end_s = cap.end % 60
        start_ts = f"{start_h}:{start_m:02d}:{start_s:05.2f}"
        end_ts = f"{end_h}:{end_m:02d}:{end_s:05.2f}"
        # Escape special characters
        text = cap.text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
        events.append(f"Dialogue: 0,{start_ts},{end_ts},Default,,0,0,0,,{text}")

    return header + "\n".join(events) + "\n"


# ── Core: Encoding pipeline ─────────────────────────────────────────────────
async def _run_encode_job(req: EncodeRequest):
    """Run the full encoding pipeline: download → encode → upload."""
    job_id = req.job_id
    tmp_dir = tempfile.mkdtemp(prefix=f"enc_{job_id}_", dir=TEMP_BASE)
    encode_start = None

    try:
        # ── Step 1: Download source video ──
        _update_job(job_id, status="downloading", progress_pct=5)
        video_path = os.path.join(tmp_dir, "source.mp4")

        logger.info(f"[{job_id}] Downloading from: {req.clip_url[:100]}...")
        import httpx

        def _download():
            with httpx.Client(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
                with client.stream("GET", req.clip_url) as resp:
                    resp.raise_for_status()
                    with open(video_path, "wb") as f:
                        for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                            f.write(chunk)

        await asyncio.get_event_loop().run_in_executor(None, _download)
        file_size = os.path.getsize(video_path)
        logger.info(f"[{job_id}] Downloaded: {file_size / 1024 / 1024:.1f} MB")

        # ── Step 1b: Handle split segments ──
        if req.split_segments:
            enabled_segs = [s for s in req.split_segments if s.get("enabled", True)]
            if enabled_segs and len(enabled_segs) < len(req.split_segments):
                logger.info(f"[{job_id}] Split: {len(enabled_segs)}/{len(req.split_segments)} segments")
                concat_path = os.path.join(tmp_dir, "concat_input.mp4")
                seg_files = []
                for i, seg in enumerate(enabled_segs):
                    seg_path = os.path.join(tmp_dir, f"seg_{i}.mp4")
                    seg_cmd = [
                        FFMPEG_BIN, "-y", "-hide_banner",
                        "-ss", str(seg.get("start", 0)),
                        "-to", str(seg.get("end", 0)),
                        "-i", video_path,
                        "-c", "copy", "-avoid_negative_ts", "make_zero",
                        seg_path,
                    ]
                    seg_proc = await asyncio.create_subprocess_exec(
                        *seg_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await asyncio.wait_for(seg_proc.wait(), timeout=60)
                    if os.path.exists(seg_path) and os.path.getsize(seg_path) > 0:
                        seg_files.append(seg_path)

                if seg_files:
                    list_path = os.path.join(tmp_dir, "concat_list.txt")
                    with open(list_path, "w") as f:
                        for sf in seg_files:
                            f.write(f"file '{sf}'\n")
                    concat_cmd = [
                        FFMPEG_BIN, "-y", "-hide_banner",
                        "-f", "concat", "-safe", "0", "-i", list_path,
                        "-c", "copy", concat_path,
                    ]
                    concat_proc = await asyncio.create_subprocess_exec(
                        *concat_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await asyncio.wait_for(concat_proc.wait(), timeout=120)
                    if os.path.exists(concat_path) and os.path.getsize(concat_path) > 0:
                        os.replace(concat_path, video_path)
                        logger.info(f"[{job_id}] Split segments concatenated")

        # ── Step 2: Probe video dimensions & duration ──
        _update_job(job_id, status="encoding", progress_pct=10)
        probe_cmd = [
            FFPROBE_BIN, "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-show_entries", "format=duration",
            "-of", "json", video_path,
        ]
        probe_proc = await asyncio.create_subprocess_exec(
            *probe_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        probe_out, _ = await asyncio.wait_for(probe_proc.communicate(), timeout=15)
        probe_data = json.loads(probe_out.decode())

        video_w = probe_data.get("streams", [{}])[0].get("width", 1080)
        video_h = probe_data.get("streams", [{}])[0].get("height", 1920)
        duration_str = probe_data.get("format", {}).get("duration", "0")
        source_duration = float(duration_str) if duration_str else 0

        logger.info(f"[{job_id}] Video: {video_w}x{video_h}, duration={source_duration:.1f}s")

        # ── Step 3: Generate ASS subtitle file ──
        output_path = os.path.join(tmp_dir, "output.mp4")

        if req.captions:
            ass_path = os.path.join(tmp_dir, "subtitles.ass")
            ass_content = generate_ass_content(
                captions=req.captions,
                style=req.style,
                video_w=video_w,
                video_h=video_h,
                pos_x=req.position_x,
                pos_y=req.position_y,
            )
            with open(ass_path, "w", encoding="utf-8") as f:
                f.write(ass_content)

            # Build video filter
            ass_escaped = ass_path.replace(":", "\\:").replace("'", "'\\''")
            font_dir = "/usr/share/fonts/opentype/noto"
            if not os.path.isdir(font_dir):
                font_dir = "/usr/share/fonts"
            vf_filter = f"ass='{ass_escaped}':fontsdir='{font_dir}'"

            # Scale down high-res videos
            if video_w > 1920 or video_h > 1920:
                scale = "scale=1920:-2" if video_w > video_h else "scale=-2:1920"
                vf_filter = f"{scale},{vf_filter}"
                logger.info(f"[{job_id}] Adding scale filter for {video_w}x{video_h}")
        else:
            vf_filter = None

        # ── Step 4: Encode with NVENC or CPU fallback ──
        use_nvenc = check_nvenc() and not req.force_cpu
        encoder = NVENC_ENCODER if use_nvenc else CPU_ENCODER

        cmd = [FFMPEG_BIN, "-y", "-hide_banner", "-progress", "pipe:1", "-i", video_path]

        if vf_filter:
            cmd.extend(["-vf", vf_filter])

        if use_nvenc:
            # NVENC settings: high quality, fast
            cmd.extend([
                "-c:v", NVENC_ENCODER,
                "-preset", "p4",       # balanced speed/quality
                "-rc", "vbr",          # variable bitrate
                "-cq", "23",           # quality level
                "-b:v", "0",           # let CQ control quality
                "-gpu", "0",
            ])
        else:
            cmd.extend([
                "-c:v", CPU_ENCODER,
                "-preset", "ultrafast",
                "-crf", "23",
                "-threads", "0",
            ])

        cmd.extend(["-c:a", "copy", "-movflags", "+faststart", output_path])

        logger.info(f"[{job_id}] Encoding with {encoder}: {' '.join(cmd)}")
        _update_job(job_id, status="encoding", progress_pct=15, encoder=encoder)

        encode_start = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Progress monitoring
        source_duration_us = int(source_duration * 1_000_000) if source_duration > 0 else 0

        async def _read_progress():
            try:
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    decoded = line.decode(errors="replace").strip()
                    if decoded.startswith("out_time_us=") and source_duration_us > 0:
                        try:
                            current_us = int(decoded.split("=")[1])
                            ratio = min(current_us / source_duration_us, 1.0)
                            pct = int(15 + ratio * 65)  # 15% to 80%
                            _update_job(job_id, progress_pct=pct)
                        except (ValueError, ZeroDivisionError):
                            pass
            except Exception:
                pass

        progress_task = asyncio.create_task(_read_progress())

        # Dynamic timeout
        timeout = min(ENCODE_TIMEOUT, max(600, int(600 + source_duration * 2)))

        try:
            stderr_task = asyncio.create_task(proc.stderr.read())
            await asyncio.wait_for(proc.wait(), timeout=timeout)
            progress_task.cancel()
            stderr_data = await stderr_task
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            _update_job(
                job_id, status="failed",
                error=f"Encoding timed out ({timeout}s) with {encoder}",
            )
            logger.error(f"[{job_id}] Encoding timed out after {timeout}s")
            return

        encode_time = time.monotonic() - encode_start
        ffmpeg_stderr = stderr_data.decode(errors="replace")

        if proc.returncode != 0:
            stderr_lines = [l for l in ffmpeg_stderr.strip().split("\n") if l.strip()][-10:]
            err_msg = "\n".join(stderr_lines)
            logger.error(f"[{job_id}] ffmpeg failed (rc={proc.returncode}): {err_msg}")

            # If NVENC failed, retry with CPU
            if use_nvenc and not req.force_cpu:
                logger.info(f"[{job_id}] NVENC failed, retrying with CPU encoder")
                req_cpu = req.model_copy()
                req_cpu.force_cpu = True
                await _run_encode_job(req_cpu)
                return

            _update_job(job_id, status="failed", error=f"ffmpeg error: {err_msg[-500:]}")
            return

        output_size = os.path.getsize(output_path)
        logger.info(
            f"[{job_id}] Encoded: {output_size / 1024 / 1024:.1f} MB "
            f"in {encode_time:.1f}s with {encoder}"
        )

        # ── Step 5: Upload to Azure Blob ──
        _update_job(job_id, status="uploading", progress_pct=85)

        if not AZURE_STORAGE_CONNECTION_STRING:
            _update_job(job_id, status="failed", error="Azure storage not configured")
            return

        upload_blob_name = f"exports/{req.video_id}/subtitled_{uuid.uuid4().hex[:8]}.mp4"

        def _upload():
            from azure.storage.blob import BlobServiceClient, ContentSettings
            svc = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
            bc = svc.get_blob_client(container=AZURE_BLOB_CONTAINER, blob=upload_blob_name)
            with open(output_path, "rb") as data:
                bc.upload_blob(
                    data, overwrite=True,
                    content_settings=ContentSettings(content_type="video/mp4"),
                )

        await asyncio.get_event_loop().run_in_executor(None, _upload)
        logger.info(f"[{job_id}] Uploaded: {upload_blob_name}")

        # Generate download URL with SAS
        from azure.storage.blob import generate_blob_sas, BlobSasPermissions

        conn_parts = dict(
            item.split("=", 1)
            for item in AZURE_STORAGE_CONNECTION_STRING.split(";")
            if "=" in item
        )
        account_name = conn_parts.get("AccountName", "")
        account_key = conn_parts.get("AccountKey", "")

        blob_url = f"https://{account_name}.blob.core.windows.net/{AZURE_BLOB_CONTAINER}/{upload_blob_name}"

        try:
            sas = generate_blob_sas(
                account_name=account_name,
                container_name=AZURE_BLOB_CONTAINER,
                blob_name=upload_blob_name,
                account_key=account_key,
                permission=BlobSasPermissions(read=True),
                expiry=datetime.now(timezone.utc) + timedelta(hours=72),
            )
            download_url = f"{blob_url}?{sas}"
        except Exception as sas_err:
            logger.warning(f"[{job_id}] SAS generation failed: {sas_err}")
            download_url = blob_url

        # Apply CDN host if configured
        cdn_host = os.getenv("CDN_HOST", "")
        blob_host = f"https://{account_name}.blob.core.windows.net"
        if cdn_host and blob_host in download_url:
            download_url = download_url.replace(blob_host, cdn_host)

        _update_job(
            job_id,
            status="done",
            download_url=download_url,
            file_size=output_size,
            progress_pct=100,
            encode_time_sec=round(encode_time, 1),
            encoder=encoder,
        )
        logger.info(f"[{job_id}] Complete! {encoder} in {encode_time:.1f}s, URL: {download_url[:80]}...")

    except Exception as e:
        logger.error(f"[{job_id}] Failed: {e}", exc_info=True)
        _update_job(job_id, status="failed", error=str(e)[:300])
    finally:
        # Cleanup temp files
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


# ── API Endpoints ────────────────────────────────────────────────────────────
@app.post("/encode", response_model=EncodeResponse)
async def submit_encode_job(req: EncodeRequest):
    """Submit a new encoding job."""
    _update_job(req.job_id, status="queued", video_id=req.video_id, progress_pct=0)
    asyncio.create_task(_run_encode_job(req))
    return EncodeResponse(
        job_id=req.job_id,
        status="queued",
        message="Encoding job submitted",
    )


@app.get("/encode/{job_id}", response_model=JobStatus)
async def get_encode_status(job_id: str):
    """Get the status of an encoding job."""
    job = _get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatus(**{k: v for k, v in job.items() if k in JobStatus.model_fields})


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    nvenc = check_nvenc()
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        gpu_info = result.stdout.strip()
    except Exception:
        gpu_info = "unavailable"

    return {
        "status": "ok",
        "nvenc_available": nvenc,
        "gpu": gpu_info,
        "encoder": NVENC_ENCODER if nvenc else CPU_ENCODER,
        "active_jobs": len([j for j in _jobs.values() if j.get("status") in ("queued", "downloading", "encoding", "uploading")]),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765, log_level="info")
