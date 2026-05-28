"""
editing_style.py — 編集スタイル学習機能
=========================================
お手本完成動画をアップロードし、AIが編集スタイルを分析して
「編集プロファイル」として保存する。次回のAIクリップ生成時に参照される。

Phase 1: 完成動画のみ → スタイル分析
Phase 2: 完成動画 + 元の長尺動画のペア → 差分比較学習

Endpoints:
  POST /editing-style/profiles                    - プロファイル作成（名前のみ）
  GET  /editing-style/profiles                    - プロファイル一覧
  GET  /editing-style/profiles/{profile_id}       - プロファイル詳細
  DELETE /editing-style/profiles/{profile_id}     - プロファイル削除
  POST /editing-style/get-upload-url              - SAS URL取得（フロントエンド直接アップロード用）
  POST /editing-style/register-sample             - アップロード完了通知（DB登録）
  POST /editing-style/upload-sample               - サーバー経由アップロード（レガシー互換）
  POST /editing-style/analyze                     - Phase 1: 完成動画からスタイル分析
  POST /editing-style/analyze-pair                - Phase 2: 完成動画+元動画ペアで差分学習
  GET  /editing-style/profiles/{profile_id}/samples - サンプル動画一覧
"""
import uuid
import json
import os
import re
import logging
import tempfile
import asyncio
import time
import subprocess
from typing import Optional, List
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Header, BackgroundTasks, UploadFile, File, Form
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.db import AsyncSessionLocal, engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/editing-style", tags=["Editing Style Learning"])

# ─── Configuration ────────────────────────────────────────────────────────────
ADMIN_KEY = os.getenv("ADMIN_API_KEY", "aither:hub")

_DB_TABLES_ENSURED = False


def verify_admin(x_admin_key: Optional[str] = Header(None)):
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


async def _ensure_tables():
    """Create editing_profiles and editing_style_samples tables if not exist"""
    global _DB_TABLES_ENSURED
    if _DB_TABLES_ENSURED:
        return
    try:
        async with engine.begin() as conn:
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS editing_profiles (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    style_params JSONB DEFAULT '{}',
                    sample_count INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'draft',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS editing_style_samples (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL REFERENCES editing_profiles(id) ON DELETE CASCADE,
                    sample_type TEXT NOT NULL DEFAULT 'finished',
                    video_url TEXT NOT NULL,
                    original_video_url TEXT,
                    analysis_result JSONB DEFAULT '{}',
                    analysis_status TEXT DEFAULT 'pending',
                    filename TEXT DEFAULT '',
                    duration_sec FLOAT DEFAULT 0,
                    error_message TEXT DEFAULT '',
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_ess_profile ON editing_style_samples(profile_id)
            """))
            # Add error_message column if missing (migration)
            await conn.execute(text("""
                DO $$ BEGIN
                    ALTER TABLE editing_style_samples ADD COLUMN IF NOT EXISTS error_message TEXT DEFAULT '';
                EXCEPTION WHEN others THEN NULL;
                END $$;
            """))
        _DB_TABLES_ENSURED = True
        logger.info("[editing-style] Tables ensured")
    except Exception as e:
        logger.error(f"[editing-style] Table creation failed: {e}")


from contextlib import asynccontextmanager

@asynccontextmanager
async def get_session():
    async with AsyncSessionLocal() as session:
        yield session


# ─── Pydantic Models ──────────────────────────────────────────────────────────

class CreateProfileRequest(BaseModel):
    name: str = Field(..., description="プロファイル名（例: 黄松松スタイル）")
    description: str = Field("", description="説明")


class GetUploadUrlRequest(BaseModel):
    profile_id: str = Field(..., description="対象プロファイルID")
    filename: str = Field(..., description="ファイル名")
    sample_type: str = Field("finished", description="finished or original")


class RegisterSampleRequest(BaseModel):
    profile_id: str = Field(..., description="対象プロファイルID")
    video_url: str = Field(..., description="アップロード済みのBlob URL")
    filename: str = Field(..., description="ファイル名")
    sample_type: str = Field("finished", description="finished or original")
    file_size: int = Field(0, description="ファイルサイズ（バイト）")


class AnalyzeRequest(BaseModel):
    profile_id: str = Field(..., description="対象プロファイルID")
    sample_id: str = Field(..., description="分析対象のサンプルID")


class AnalyzePairRequest(BaseModel):
    profile_id: str = Field(..., description="対象プロファイルID")
    finished_sample_id: str = Field(..., description="完成動画のサンプルID")
    original_sample_id: str = Field(..., description="元の長尺動画のサンプルID")


# ─── Profile CRUD ─────────────────────────────────────────────────────────────

@router.post("/profiles")
async def create_profile(
    req: CreateProfileRequest,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """編集プロファイルを新規作成"""
    verify_admin(x_admin_key)
    await _ensure_tables()

    profile_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    async with get_session() as session:
        await session.execute(text("""
            INSERT INTO editing_profiles (id, name, description, status, created_at, updated_at)
            VALUES (:id, :name, :description, 'draft', :now, :now)
        """), {"id": profile_id, "name": req.name, "description": req.description, "now": now})
        await session.commit()

    return {"success": True, "id": profile_id, "profile_id": profile_id, "name": req.name}


@router.get("/profiles")
async def list_profiles(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """編集プロファイル一覧を取得"""
    verify_admin(x_admin_key)
    await _ensure_tables()

    async with get_session() as session:
        result = await session.execute(text("""
            SELECT id, name, description, style_params, sample_count, status, created_at, updated_at
            FROM editing_profiles
            ORDER BY updated_at DESC
        """))
        rows = result.fetchall()

    profiles = []
    for r in rows:
        profiles.append({
            "id": r.id,
            "name": r.name,
            "description": r.description,
            "style_params": r.style_params if r.style_params else {},
            "sample_count": r.sample_count or 0,
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        })

    return {"profiles": profiles, "total": len(profiles)}


@router.get("/profiles/{profile_id}")
async def get_profile(
    profile_id: str,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """編集プロファイル詳細を取得"""
    verify_admin(x_admin_key)
    await _ensure_tables()

    async with get_session() as session:
        result = await session.execute(text("""
            SELECT id, name, description, style_params, sample_count, status, created_at, updated_at
            FROM editing_profiles WHERE id = :id
        """), {"id": profile_id})
        row = result.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="プロファイルが見つかりません")

    # Get samples
    async with get_session() as session:
        samples_result = await session.execute(text("""
            SELECT id, sample_type, video_url, original_video_url, analysis_result,
                   analysis_status, filename, duration_sec, created_at
            FROM editing_style_samples
            WHERE profile_id = :profile_id
            ORDER BY created_at ASC
        """), {"profile_id": profile_id})
        sample_rows = samples_result.fetchall()

    samples = []
    for s in sample_rows:
        samples.append({
            "id": s.id,
            "sample_type": s.sample_type,
            "video_url": s.video_url,
            "original_video_url": s.original_video_url,
            "analysis_result": s.analysis_result if s.analysis_result else {},
            "analysis_status": s.analysis_status,
            "filename": s.filename,
            "duration_sec": s.duration_sec,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        })

    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "style_params": row.style_params if row.style_params else {},
        "sample_count": row.sample_count or 0,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "samples": samples,
    }


@router.delete("/profiles/{profile_id}")
async def delete_profile(
    profile_id: str,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """編集プロファイルを削除（サンプルもカスケード削除）"""
    verify_admin(x_admin_key)
    await _ensure_tables()

    async with get_session() as session:
        result = await session.execute(text("""
            DELETE FROM editing_profiles WHERE id = :id RETURNING id
        """), {"id": profile_id})
        deleted = result.fetchone()
        await session.commit()

    if not deleted:
        raise HTTPException(status_code=404, detail="プロファイルが見つかりません")

    return {"success": True, "deleted_id": profile_id}


# ─── Direct Upload (Frontend → Azure Blob) ──────────────────────────────────

@router.post("/get-upload-url")
async def get_upload_url(
    req: GetUploadUrlRequest,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """フロントエンドから直接Azure Blobにアップロードするための書き込みSAS URLを取得"""
    verify_admin(x_admin_key)
    await _ensure_tables()

    # Validate profile exists
    async with get_session() as session:
        result = await session.execute(text(
            "SELECT id FROM editing_profiles WHERE id = :id"
        ), {"id": req.profile_id})
        if not result.fetchone():
            raise HTTPException(status_code=404, detail="プロファイルが見つかりません")

    from app.services.storage_service import generate_upload_sas

    file_id = f"editing-style-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    filename = req.filename or f"sample_{int(time.time())}.mp4"

    vid, upload_url, blob_url, expiry = await generate_upload_sas(
        email="editing-style@aitherhub.com",
        video_id=file_id,
        filename=filename,
    )

    return {
        "upload_url": upload_url,
        "blob_url": blob_url,
        "expiry": expiry.isoformat(),
        "file_id": file_id,
    }


@router.post("/register-sample")
async def register_sample(
    req: RegisterSampleRequest,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """フロントエンドからの直接アップロード完了後、DBにサンプルを登録する"""
    verify_admin(x_admin_key)
    await _ensure_tables()

    # Validate profile exists
    async with get_session() as session:
        result = await session.execute(text(
            "SELECT id FROM editing_profiles WHERE id = :id"
        ), {"id": req.profile_id})
        if not result.fetchone():
            raise HTTPException(status_code=404, detail="プロファイルが見つかりません")

    # Generate read SAS URL
    from app.services.storage_service import generate_read_sas_from_url
    read_url = generate_read_sas_from_url(req.video_url)
    if not read_url:
        read_url = req.video_url

    # Save sample record
    sample_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    async with get_session() as session:
        await session.execute(text("""
            INSERT INTO editing_style_samples
                (id, profile_id, sample_type, video_url, filename, analysis_status, created_at)
            VALUES (:id, :profile_id, :sample_type, :video_url, :filename, 'pending', :now)
        """), {
            "id": sample_id,
            "profile_id": req.profile_id,
            "sample_type": req.sample_type,
            "video_url": read_url,
            "filename": req.filename,
            "now": now,
        })
        # Update sample count
        await session.execute(text("""
            UPDATE editing_profiles
            SET sample_count = (SELECT COUNT(*) FROM editing_style_samples WHERE profile_id = :pid),
                updated_at = :now
            WHERE id = :pid
        """), {"pid": req.profile_id, "now": now})
        await session.commit()

    logger.info(f"[editing-style] Sample registered: {req.filename} ({req.file_size} bytes) -> profile {req.profile_id}")
    return {
        "success": True,
        "sample_id": sample_id,
        "video_url": read_url,
        "filename": req.filename,
        "sample_type": req.sample_type,
    }


# ─── Legacy Server-side Upload (fallback) ────────────────────────────────────

@router.post("/upload-sample")
async def upload_sample(
    file: UploadFile = File(...),
    profile_id: str = Form(...),
    sample_type: str = Form("finished"),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """お手本動画をアップロードする（サーバー経由 - レガシー互換）
    sample_type: 'finished' = 完成動画, 'original' = 元の長尺動画
    """
    verify_admin(x_admin_key)
    await _ensure_tables()

    # Validate profile exists
    async with get_session() as session:
        result = await session.execute(text(
            "SELECT id FROM editing_profiles WHERE id = :id"
        ), {"id": profile_id})
        if not result.fetchone():
            raise HTTPException(status_code=404, detail="プロファイルが見つかりません")

    # Upload to Azure Blob
    from app.services.storage_service import generate_upload_sas, generate_read_sas_from_url
    import httpx

    try:
        content = await file.read()
        max_size = 500 * 1024 * 1024  # 500MB for videos
        if len(content) > max_size:
            raise HTTPException(status_code=400, detail="ファイルが大きすぎます（最大500MB）")

        file_id = f"editing-style-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        filename = file.filename or f"sample_{int(time.time())}.mp4"

        vid, upload_url, blob_url, expiry = await generate_upload_sas(
            email="editing-style@aitherhub.com",
            video_id=file_id,
            filename=filename,
        )

        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.put(
                upload_url,
                content=content,
                headers={
                    "x-ms-blob-type": "BlockBlob",
                    "Content-Type": file.content_type or "video/mp4",
                },
            )
            resp.raise_for_status()

        # Generate read SAS URL
        read_url = generate_read_sas_from_url(blob_url)
        if not read_url:
            read_url = blob_url

        # Save sample record
        sample_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        async with get_session() as session:
            await session.execute(text("""
                INSERT INTO editing_style_samples
                    (id, profile_id, sample_type, video_url, filename, analysis_status, created_at)
                VALUES (:id, :profile_id, :sample_type, :video_url, :filename, 'pending', :now)
            """), {
                "id": sample_id,
                "profile_id": profile_id,
                "sample_type": sample_type,
                "video_url": read_url,
                "filename": filename,
                "now": now,
            })
            # Update sample count
            await session.execute(text("""
                UPDATE editing_profiles
                SET sample_count = (SELECT COUNT(*) FROM editing_style_samples WHERE profile_id = :pid),
                    updated_at = :now
                WHERE id = :pid
            """), {"pid": profile_id, "now": now})
            await session.commit()

        logger.info(f"[editing-style] Sample uploaded: {filename} ({len(content)} bytes) -> profile {profile_id}")
        return {
            "success": True,
            "sample_id": sample_id,
            "video_url": read_url,
            "filename": filename,
            "file_size": len(content),
            "sample_type": sample_type,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[editing-style] Upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"アップロードに失敗しました: {str(e)}")


# ─── Phase 1: Single Video Analysis ──────────────────────────────────────────

@router.post("/analyze")
async def analyze_single(
    req: AnalyzeRequest,
    background_tasks: BackgroundTasks,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """Phase 1: 完成動画からスタイルを分析する（非同期）"""
    verify_admin(x_admin_key)
    await _ensure_tables()

    # Validate
    async with get_session() as session:
        result = await session.execute(text("""
            SELECT id, video_url, profile_id, sample_type FROM editing_style_samples
            WHERE id = :id AND profile_id = :pid
        """), {"id": req.sample_id, "pid": req.profile_id})
        sample = result.fetchone()

    if not sample:
        raise HTTPException(status_code=404, detail="サンプルが見つかりません")

    # If this is an original sample, try to find its paired finished sample and run pair analysis
    if sample.sample_type == 'original':
        async with get_session() as session:
            # Find a finished sample in the same profile that was paired with this original
            pair_result = await session.execute(text("""
                SELECT id, video_url FROM editing_style_samples
                WHERE profile_id = :pid AND sample_type = 'finished'
                  AND original_video_url = :orig_url
                ORDER BY created_at DESC LIMIT 1
            """), {"pid": req.profile_id, "orig_url": sample.video_url})
            pair_row = pair_result.fetchone()

        if pair_row:
            # Run as pair analysis (longer timeout, proper diff analysis)
            async with get_session() as session:
                await session.execute(text("""
                    UPDATE editing_style_samples SET analysis_status = 'analyzing'
                    WHERE id IN (:fid, :oid)
                """), {"fid": pair_row.id, "oid": req.sample_id})
                await session.commit()
            asyncio.create_task(_run_pair_analysis_safe(
                req.profile_id, pair_row.id, req.sample_id,
                pair_row.video_url, sample.video_url,
            ))
            return {"success": True, "message": "ペア分析（再試行）を開始しました", "sample_id": req.sample_id}

    # Mark as analyzing
    async with get_session() as session:
        await session.execute(text("""
            UPDATE editing_style_samples SET analysis_status = 'analyzing' WHERE id = :id
        """), {"id": req.sample_id})
        await session.commit()

    # Determine timeout based on sample type
    is_original = sample.sample_type == 'original'
    asyncio.create_task(_run_single_analysis_safe(req.profile_id, req.sample_id, sample.video_url, is_original=is_original))

    return {"success": True, "message": "分析を開始しました", "sample_id": req.sample_id}


async def _run_single_analysis_safe(profile_id: str, sample_id: str, video_url: str, is_original: bool = False):
    """Wrapper with timeout and guaranteed status update"""
    timeout_sec = 900 if is_original else 300  # 15 min for originals, 5 min for finished
    try:
        await asyncio.wait_for(
            _run_single_analysis(profile_id, sample_id, video_url),
            timeout=timeout_sec
        )
    except asyncio.TimeoutError:
        logger.error(f"[editing-style] Analysis timed out for {sample_id}")
        try:
            async with get_session() as session:
                await session.execute(text("""
                    UPDATE editing_style_samples
                    SET analysis_status = 'error',
                        analysis_result = :err,
                        error_message = 'タイムアウト（5分超過）'
                    WHERE id = :id
                """), {"id": sample_id, "err": json.dumps({"error": "timeout_5min"})})
                await session.commit()
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[editing-style] Analysis crashed for {sample_id}: {e}")
        try:
            async with get_session() as session:
                await session.execute(text("""
                    UPDATE editing_style_samples
                    SET analysis_status = 'error',
                        analysis_result = :err,
                        error_message = :msg
                    WHERE id = :id
                """), {"id": sample_id, "err": json.dumps({"error": str(e)}), "msg": str(e)[:500]})
                await session.commit()
        except Exception:
            pass


async def _run_single_analysis(profile_id: str, sample_id: str, video_url: str):
    """Phase 1: 完成動画のスタイル分析（バックグラウンド処理）"""
    import httpx

    tmp_dir = tempfile.mkdtemp(prefix="editing_style_")
    video_path = os.path.join(tmp_dir, "sample.mp4")

    try:
        # 1. Download video
        logger.info(f"[editing-style] Downloading sample {sample_id}...")
        async with httpx.AsyncClient(timeout=180) as client:
            async with client.stream("GET", video_url) as resp:
                resp.raise_for_status()
                with open(video_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        f.write(chunk)

        # 2. Get video duration
        duration = _get_video_duration(video_path)
        logger.info(f"[editing-style] Video duration: {duration:.1f}s")

        # 3. Scene change detection (using ffmpeg)
        logger.info(f"[editing-style] Detecting scene changes for {sample_id}...")
        scene_times = await _detect_scene_changes(video_path)

        # Calculate cut intervals
        cut_intervals = _calc_cut_intervals(scene_times, duration)
        avg_cut_interval = sum(cut_intervals) / len(cut_intervals) if cut_intervals else duration

        # 4. Transcribe with Whisper
        logger.info(f"[editing-style] Transcribing sample {sample_id}...")
        captions = await _transcribe_for_style(video_path)

        # 5. GPT analysis of editing style
        logger.info(f"[editing-style] GPT analyzing style for {sample_id}...")
        style_analysis = await _gpt_analyze_style(
            captions=captions,
            duration=duration,
            scene_count=len(scene_times),
            avg_cut_interval=avg_cut_interval,
            cut_intervals=cut_intervals[:50],
        )

        # 6. Build analysis result
        analysis_result = {
            "duration_sec": round(duration, 2),
            "scene_count": len(scene_times),
            "avg_cut_interval": round(avg_cut_interval, 2),
            "min_cut_interval": round(min(cut_intervals), 2) if cut_intervals else 0,
            "max_cut_interval": round(max(cut_intervals), 2) if cut_intervals else 0,
            "transcript_length": len(captions),
            "style_analysis": style_analysis,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        }

        # 7. Save analysis result
        async with get_session() as session:
            await session.execute(text("""
                UPDATE editing_style_samples
                SET analysis_result = :result, analysis_status = 'done', duration_sec = :dur
                WHERE id = :id
            """), {"result": json.dumps(analysis_result), "id": sample_id, "dur": duration})
            await session.commit()

        # 8. Aggregate profile style_params from all analyzed samples
        await _aggregate_profile_style(profile_id)

        logger.info(f"[editing-style] ✅ Analysis complete for {sample_id}: "
                    f"duration={duration:.1f}s, scenes={len(scene_times)}, avg_cut={avg_cut_interval:.2f}s")

    except Exception as e:
        logger.error(f"[editing-style] Analysis failed for {sample_id}: {e}", exc_info=True)
        async with get_session() as session:
            await session.execute(text("""
                UPDATE editing_style_samples
                SET analysis_status = 'error',
                    analysis_result = :err,
                    error_message = :msg
                WHERE id = :id
            """), {"id": sample_id, "err": json.dumps({"error": str(e)}), "msg": str(e)[:500]})
            await session.commit()
    finally:
        import shutil
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


# ─── Phase 2: Pair Analysis (Finished + Original) ────────────────────────────

@router.post("/analyze-pair")
async def analyze_pair(
    req: AnalyzePairRequest,
    background_tasks: BackgroundTasks,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """Phase 2: 完成動画 + 元動画のペアで差分比較学習（非同期）"""
    verify_admin(x_admin_key)
    await _ensure_tables()

    # Validate both samples exist
    async with get_session() as session:
        finished = await session.execute(text("""
            SELECT id, video_url FROM editing_style_samples
            WHERE id = :id AND profile_id = :pid
        """), {"id": req.finished_sample_id, "pid": req.profile_id})
        finished_row = finished.fetchone()

        original = await session.execute(text("""
            SELECT id, video_url FROM editing_style_samples
            WHERE id = :id AND profile_id = :pid
        """), {"id": req.original_sample_id, "pid": req.profile_id})
        original_row = original.fetchone()

    if not finished_row:
        raise HTTPException(status_code=404, detail="完成動画サンプルが見つかりません")
    if not original_row:
        raise HTTPException(status_code=404, detail="元動画サンプルが見つかりません")

    # Mark both as analyzing
    async with get_session() as session:
        await session.execute(text("""
            UPDATE editing_style_samples SET analysis_status = 'analyzing'
            WHERE id = :fid
        """), {"fid": req.finished_sample_id})
        await session.execute(text("""
            UPDATE editing_style_samples SET analysis_status = 'analyzing'
            WHERE id = :oid
        """), {"oid": req.original_sample_id})
        # Link the original to the finished sample
        await session.execute(text("""
            UPDATE editing_style_samples SET original_video_url = :orig_url
            WHERE id = :fid
        """), {"fid": req.finished_sample_id, "orig_url": original_row.video_url})
        await session.commit()

    # Run pair analysis with timeout wrapper
    asyncio.create_task(_run_pair_analysis_safe(
        req.profile_id, req.finished_sample_id, req.original_sample_id,
        finished_row.video_url, original_row.video_url,
    ))

    return {"success": True, "message": "ペア分析を開始しました"}


async def _run_pair_analysis_safe(
    profile_id: str,
    finished_sample_id: str,
    original_sample_id: str,
    finished_url: str,
    original_url: str,
):
    """Wrapper with timeout and guaranteed status update"""
    try:
        await asyncio.wait_for(
            _run_pair_analysis(profile_id, finished_sample_id, original_sample_id, finished_url, original_url),
            timeout=600  # 10 minutes max for pair analysis
        )
    except asyncio.TimeoutError:
        logger.error(f"[editing-style] Pair analysis timed out")
        try:
            async with get_session() as session:
                await session.execute(text("""
                    UPDATE editing_style_samples
                    SET analysis_status = 'error', analysis_result = :err, error_message = 'タイムアウト（10分超過）'
                    WHERE id = :fid
                """), {"fid": finished_sample_id, "err": json.dumps({"error": "timeout_10min"})})
                await session.execute(text("""
                    UPDATE editing_style_samples
                    SET analysis_status = 'error', error_message = 'タイムアウト（10分超過）'
                    WHERE id = :oid
                """), {"oid": original_sample_id})
                await session.commit()
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[editing-style] Pair analysis crashed: {e}")
        try:
            async with get_session() as session:
                await session.execute(text("""
                    UPDATE editing_style_samples
                    SET analysis_status = 'error', analysis_result = :err, error_message = :msg
                    WHERE id = :fid
                """), {"fid": finished_sample_id, "err": json.dumps({"error": str(e)}), "msg": str(e)[:500]})
                await session.execute(text("""
                    UPDATE editing_style_samples
                    SET analysis_status = 'error', error_message = :msg
                    WHERE id = :oid
                """), {"oid": original_sample_id, "msg": str(e)[:500]})
                await session.commit()
        except Exception:
            pass


async def _run_pair_analysis(
    profile_id: str,
    finished_sample_id: str,
    original_sample_id: str,
    finished_url: str,
    original_url: str,
):
    """Phase 2: 完成動画と元動画の差分を比較して編集パターンを学習"""
    import httpx

    tmp_dir = tempfile.mkdtemp(prefix="editing_style_pair_")
    finished_path = os.path.join(tmp_dir, "finished.mp4")
    original_path = os.path.join(tmp_dir, "original.mp4")

    try:
        # 1. Download both videos
        logger.info(f"[editing-style] Downloading pair for analysis...")
        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream("GET", finished_url) as resp:
                resp.raise_for_status()
                with open(finished_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
            async with client.stream("GET", original_url) as resp:
                resp.raise_for_status()
                with open(original_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        f.write(chunk)

        # 2. Get durations
        finished_duration = _get_video_duration(finished_path)
        original_duration = _get_video_duration(original_path)
        cut_ratio = 1.0 - (finished_duration / original_duration) if original_duration > 0 else 0
        logger.info(f"[editing-style] Pair: original={original_duration:.1f}s, "
                    f"finished={finished_duration:.1f}s, cut_ratio={cut_ratio:.1%}")

        # 3. Transcribe both
        logger.info(f"[editing-style] Transcribing pair...")
        finished_captions = await _transcribe_for_style(finished_path)
        original_captions = await _transcribe_for_style(original_path)

        # 4. Scene detection on both
        finished_scenes = await _detect_scene_changes(finished_path)
        original_scenes = await _detect_scene_changes(original_path, timeout=600)  # longer timeout for long original videos

        # 5. Diff analysis with GPT
        logger.info(f"[editing-style] GPT diff analysis...")
        diff_analysis = await _gpt_analyze_pair_diff(
            original_captions=original_captions,
            finished_captions=finished_captions,
            original_duration=original_duration,
            finished_duration=finished_duration,
            original_scene_count=len(original_scenes),
            finished_scene_count=len(finished_scenes),
            cut_ratio=cut_ratio,
        )

        # 6. Calculate detailed metrics
        finished_cut_intervals = _calc_cut_intervals(finished_scenes, finished_duration)
        avg_cut_interval = (sum(finished_cut_intervals) / len(finished_cut_intervals)
                           if finished_cut_intervals else finished_duration)

        # 7. Build analysis result
        analysis_result = {
            "type": "pair_analysis",
            "original_duration_sec": round(original_duration, 2),
            "finished_duration_sec": round(finished_duration, 2),
            "duration_sec": round(finished_duration, 2),
            "cut_ratio": round(cut_ratio, 4),
            "original_scene_count": len(original_scenes),
            "finished_scene_count": len(finished_scenes),
            "scene_count": len(finished_scenes),
            "avg_cut_interval": round(avg_cut_interval, 2),
            "original_transcript_segments": len(original_captions),
            "finished_transcript_segments": len(finished_captions),
            "diff_analysis": diff_analysis,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        }

        # 8. Save to both samples
        async with get_session() as session:
            await session.execute(text("""
                UPDATE editing_style_samples
                SET analysis_result = :result, analysis_status = 'done', duration_sec = :dur
                WHERE id = :id
            """), {"result": json.dumps(analysis_result), "id": finished_sample_id, "dur": finished_duration})
            await session.execute(text("""
                UPDATE editing_style_samples
                SET analysis_status = 'done', duration_sec = :dur
                WHERE id = :id
            """), {"id": original_sample_id, "dur": original_duration})
            await session.commit()

        # 9. Aggregate profile
        await _aggregate_profile_style(profile_id)

        logger.info(f"[editing-style] ✅ Pair analysis complete: cut_ratio={cut_ratio:.1%}")

    except Exception as e:
        logger.error(f"[editing-style] Pair analysis failed: {e}", exc_info=True)
        async with get_session() as session:
            await session.execute(text("""
                UPDATE editing_style_samples
                SET analysis_status = 'error', analysis_result = :err, error_message = :msg
                WHERE id = :fid
            """), {"fid": finished_sample_id, "err": json.dumps({"error": str(e)}), "msg": str(e)[:500]})
            await session.execute(text("""
                UPDATE editing_style_samples
                SET analysis_status = 'error', error_message = :msg
                WHERE id = :oid
            """), {"oid": original_sample_id, "msg": str(e)[:500]})
            await session.commit()
    finally:
        import shutil
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


# ─── Helper Functions ─────────────────────────────────────────────────────────

def _get_video_duration(video_path: str) -> float:
    """Get video duration using ffprobe"""
    probe_cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", video_path
    ]
    result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
    if result.returncode == 0:
        data = json.loads(result.stdout)
        return float(data.get("format", {}).get("duration", 0))
    return 0.0


async def _detect_scene_changes(video_path: str, threshold: float = 0.3, timeout: int = 300) -> list:
    """Detect scene changes using ffmpeg (async, non-blocking).

    For long videos (>10 min), we limit analysis to the first 30 minutes
    to avoid excessive processing time on Azure App Service.
    """
    duration = _get_video_duration(video_path)
    # For very long videos, limit to first 30 min to avoid timeout
    time_limit_args = []
    if duration > 600:  # > 10 minutes
        time_limit_args = ["-t", "1800"]  # analyze first 30 min max
        logger.info(f"[editing-style] Long video ({duration:.0f}s), limiting scene detection to first 30 min")

    cmd = [
        "ffmpeg", *time_limit_args, "-i", video_path,
        "-vf", f"select='gt(scene,{threshold})',showinfo",
        "-vsync", "vfr", "-f", "null", "-"
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
    except asyncio.TimeoutError:
        logger.warning(f"[editing-style] Scene detection timed out after {timeout}s for {video_path}")
        try:
            proc.kill()
        except Exception:
            pass
        return []
    except Exception as e:
        logger.warning(f"[editing-style] Scene detection failed: {e}")
        return []

    scene_times = []
    for line in stderr_text.split("\n"):
        if "pts_time:" in line:
            match = re.search(r"pts_time:(\d+\.?\d*)", line)
            if match:
                scene_times.append(float(match.group(1)))
    return scene_times


def _calc_cut_intervals(scene_times: list, duration: float) -> list:
    """Calculate intervals between cuts"""
    cut_points = [0.0] + scene_times + [duration]
    intervals = []
    for i in range(len(cut_points) - 1):
        interval = cut_points[i + 1] - cut_points[i]
        if interval > 0.1:
            intervals.append(interval)
    return intervals


async def _transcribe_for_style(video_path: str) -> list:
    """Whisperで音声認識（スタイル分析用）"""
    import openai

    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    azure_key = os.getenv("AZURE_OPENAI_KEY", "")

    if not azure_key or not azure_endpoint:
        logger.warning("[editing-style] No Azure OpenAI key, skipping transcription")
        return []

    from urllib.parse import urlparse as _urlparse
    _parsed = _urlparse(azure_endpoint)
    clean_endpoint = f"{_parsed.scheme}://{_parsed.netloc}/"

    openai_client = openai.AsyncAzureOpenAI(
        api_key=azure_key,
        api_version="2024-06-01",
        azure_endpoint=clean_endpoint,
    )

    # Extract audio (limit to first 15 min for long videos to avoid timeout)
    audio_path = video_path.replace(".mp4", "_audio.mp3")
    duration = 0
    try:
        duration = _get_video_duration(video_path)
    except Exception:
        pass
    ffmpeg_args = ["ffmpeg", "-y", "-i", video_path]
    if duration > 600:  # > 10 min: only transcribe first 15 min
        ffmpeg_args.extend(["-t", "900"])
        logger.info(f"[editing-style] Long video ({duration:.0f}s), limiting audio to first 15 min")
    ffmpeg_args.extend(["-vn", "-acodec", "libmp3lame", "-ar", "16000", "-ac", "1", "-b:a", "64k", audio_path])
    audio_timeout = 300 if duration > 600 else 120  # longer timeout for long videos
    try:
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=audio_timeout)
    except Exception as e:
        logger.warning(f"[editing-style] Audio extraction failed: {e}")
        return []

    if not os.path.exists(audio_path):
        return []

    # Check file size (Whisper 25MB limit)
    file_size = os.path.getsize(audio_path)
    if file_size > 25 * 1024 * 1024:
        audio_path_small = audio_path.replace(".mp3", "_small.mp3")
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", audio_path,
                "-acodec", "libmp3lame", "-ar", "16000", "-ac", "1", "-b:a", "32k",
                audio_path_small,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=120)
            audio_path = audio_path_small
        except Exception:
            pass

    try:
        with open(audio_path, "rb") as audio_file:
            response = await openai_client.audio.transcriptions.create(
                model="whisper",
                file=audio_file,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )

        captions = []
        if hasattr(response, "segments") and response.segments:
            for seg in response.segments:
                captions.append({
                    "start": getattr(seg, "start", 0),
                    "end": getattr(seg, "end", 0),
                    "text": getattr(seg, "text", ""),
                })
        return captions
    except Exception as e:
        logger.warning(f"[editing-style] Whisper transcription failed: {e}")
        return []


async def _gpt_analyze_style(
    captions: list,
    duration: float,
    scene_count: int,
    avg_cut_interval: float,
    cut_intervals: list,
) -> dict:
    """GPTで編集スタイルを分析する"""
    import openai

    azure_key = os.getenv("AZURE_OPENAI_KEY", "")
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    azure_model = os.getenv("GPT5_MODEL") or os.getenv("GPT5_DEPLOYMENT") or "gpt-4.1-mini"

    if not azure_key or not azure_endpoint:
        return {
            "hook_style": "unknown",
            "pacing": "medium",
            "silence_tolerance_sec": 0.5,
            "content_density": "medium",
        }

    from urllib.parse import urlparse as _urlparse
    _parsed = _urlparse(azure_endpoint)
    clean_endpoint = f"{_parsed.scheme}://{_parsed.netloc}/"

    client = openai.AsyncAzureOpenAI(
        api_key=azure_key,
        azure_endpoint=clean_endpoint,
        api_version=os.getenv("GPT5_API_VERSION", "2025-04-01-preview"),
    )

    # Build transcript excerpt
    transcript_lines = []
    for cap in captions[:30]:
        s = float(cap.get("start", 0))
        e = float(cap.get("end", 0))
        t = cap.get("text", "").strip()
        if t:
            transcript_lines.append(f"[{s:.1f}-{e:.1f}] {t}")

    transcript_text = "\n".join(transcript_lines) if transcript_lines else "(字幕なし)"

    short_cuts = len([x for x in cut_intervals if x < 2.0])
    medium_cuts = len([x for x in cut_intervals if 2.0 <= x < 5.0])
    long_cuts = len([x for x in cut_intervals if x >= 5.0])

    prompt = f"""以下はライブコマースの編集済み動画の分析データです。この動画の編集スタイルを分析してください。

【動画情報】
- 総尺: {duration:.1f}秒
- シーンカット数: {scene_count}回
- 平均カット間隔: {avg_cut_interval:.2f}秒
- カット間隔分布: 短(2秒未満)={short_cuts}回, 中(2-5秒)={medium_cuts}回, 長(5秒以上)={long_cuts}回

【字幕テキスト（冒頭30セグメント）】
{transcript_text}

【分析タスク】
以下の項目をJSON形式で出力してください：

1. hook_style: フック（冒頭）のスタイル ("question"/"command"/"shock"/"story"/"direct")
2. pacing: 編集のテンポ ("fast"/"medium"/"slow")
3. silence_tolerance_sec: 無音をどの程度許容するか（秒）
4. content_density: 情報密度 ("high"/"medium"/"low")
5. cut_aggressiveness: カットの積極性 (0.0-1.0)
6. preferred_clip_duration_sec: 好みのクリップ長（秒）
7. hook_duration_sec: フック部分の長さ（秒）
8. subtitle_style_preference: 字幕スタイル ("pop"/"simple"/"box"/"gradient")
9. transition_style: トランジション ("hard_cut"/"fade"/"mixed")
10. energy_level: エネルギーレベル ("high"/"medium"/"low")

JSON形式のみで出力してください。"""

    try:
        response = await client.chat.completions.create(
            model=azure_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.2,
        )
        content = response.choices[0].message.content.strip()
        json_match = re.search(r'\{[^{}]*\}', content, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return json.loads(content)
    except Exception as e:
        logger.warning(f"[editing-style] GPT style analysis failed: {e}")
        return {
            "hook_style": "unknown",
            "pacing": "fast" if avg_cut_interval < 2 else ("medium" if avg_cut_interval < 5 else "slow"),
            "silence_tolerance_sec": 0.5,
            "content_density": "medium",
            "cut_aggressiveness": min(1.0, scene_count / (duration / 2)) if duration > 0 else 0.5,
        }


async def _gpt_analyze_pair_diff(
    original_captions: list,
    finished_captions: list,
    original_duration: float,
    finished_duration: float,
    original_scene_count: int,
    finished_scene_count: int,
    cut_ratio: float,
) -> dict:
    """GPTで完成動画と元動画の差分を分析する"""
    import openai

    azure_key = os.getenv("AZURE_OPENAI_KEY", "")
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    azure_model = os.getenv("GPT5_MODEL") or os.getenv("GPT5_DEPLOYMENT") or "gpt-4.1-mini"

    if not azure_key or not azure_endpoint:
        return {
            "cut_ratio": cut_ratio,
            "editing_philosophy": "unknown",
            "silence_handling": "moderate",
        }

    from urllib.parse import urlparse as _urlparse
    _parsed = _urlparse(azure_endpoint)
    clean_endpoint = f"{_parsed.scheme}://{_parsed.netloc}/"

    client = openai.AsyncAzureOpenAI(
        api_key=azure_key,
        azure_endpoint=clean_endpoint,
        api_version=os.getenv("GPT5_API_VERSION", "2025-04-01-preview"),
    )

    orig_lines = []
    for cap in original_captions[:40]:
        s = float(cap.get("start", 0))
        e = float(cap.get("end", 0))
        t = cap.get("text", "").strip()
        if t:
            orig_lines.append(f"[{s:.1f}-{e:.1f}] {t}")

    fin_lines = []
    for cap in finished_captions[:40]:
        s = float(cap.get("start", 0))
        e = float(cap.get("end", 0))
        t = cap.get("text", "").strip()
        if t:
            fin_lines.append(f"[{s:.1f}-{e:.1f}] {t}")

    prompt = f"""以下は同じライブ配信の「元の長尺動画」と「編集済み完成動画」のデータです。
編集者がどのような基準でカットしたかを分析してください。

【元動画】
- 総尺: {original_duration:.1f}秒
- シーンカット数: {original_scene_count}回
- 字幕セグメント数: {len(original_captions)}

字幕（冒頭40セグメント）:
{chr(10).join(orig_lines[:40]) if orig_lines else "(なし)"}

【完成動画】
- 総尺: {finished_duration:.1f}秒（カット率: {cut_ratio:.1%}）
- シーンカット数: {finished_scene_count}回
- 字幕セグメント数: {len(finished_captions)}

字幕（冒頭40セグメント）:
{chr(10).join(fin_lines[:40]) if fin_lines else "(なし)"}

【分析タスク】
編集者のカット基準をJSON形式で出力してください：

1. cut_ratio: 実際のカット率 (0.0-1.0)
2. editing_philosophy: 編集方針 ("aggressive"/"moderate"/"conservative")
3. silence_handling: 無音の扱い ("strict"/"moderate"/"lenient")
4. silence_threshold_sec: 無音カットの閾値（秒）
5. filler_handling: フィラーワードの扱い ("always_cut"/"sometimes_cut"/"keep")
6. content_filter: コンテンツフィルタ基準 ("strict"/"moderate"/"lenient")
7. preferred_segment_duration: 好みのセグメント長（秒）
8. keeps_greetings: 挨拶を残すか (true/false)
9. keeps_reactions: リアクション・感嘆を残すか (true/false)
10. transition_preference: カット間のつなぎ ("hard"/"fade"/"mixed")
11. hook_creation: フック作成方法 ("extract"/"create"/"none")
12. max_single_segment_sec: 1つのセグメントの最大長（秒）

JSON形式のみで出力してください。"""

    try:
        response = await client.chat.completions.create(
            model=azure_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600,
            temperature=0.2,
        )
        content = response.choices[0].message.content.strip()
        json_match = re.search(r'\{[^{}]*\}', content, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return json.loads(content)
    except Exception as e:
        logger.warning(f"[editing-style] GPT pair diff analysis failed: {e}")
        return {
            "cut_ratio": cut_ratio,
            "editing_philosophy": "moderate",
            "silence_handling": "moderate",
            "silence_threshold_sec": 0.5,
        }


async def _aggregate_profile_style(profile_id: str):
    """全サンプルの分析結果を集約してプロファイルのstyle_paramsを更新する"""
    async with get_session() as session:
        result = await session.execute(text("""
            SELECT analysis_result FROM editing_style_samples
            WHERE profile_id = :pid AND analysis_status = 'done'
              AND analysis_result IS NOT NULL
        """), {"pid": profile_id})
        rows = result.fetchall()

    if not rows:
        return

    # Collect all style analyses
    all_analyses = []
    pair_analyses = []
    for r in rows:
        ar = r.analysis_result if isinstance(r.analysis_result, dict) else {}
        if ar.get("type") == "pair_analysis":
            if ar.get("diff_analysis"):
                pair_analyses.append(ar["diff_analysis"])
        elif ar.get("style_analysis"):
            all_analyses.append(ar["style_analysis"])

    # Aggregate: prefer pair analysis (more accurate), fallback to single
    aggregated = {}

    if pair_analyses:
        numeric_keys = ["silence_threshold_sec", "preferred_segment_duration", "max_single_segment_sec", "cut_ratio"]
        for key in numeric_keys:
            values = [p.get(key) for p in pair_analyses if p.get(key) is not None]
            if values:
                aggregated[key] = round(sum(values) / len(values), 2)

        cat_keys = ["editing_philosophy", "silence_handling", "filler_handling",
                    "content_filter", "transition_preference", "hook_creation"]
        for key in cat_keys:
            values = [p.get(key) for p in pair_analyses if p.get(key)]
            if values:
                aggregated[key] = max(set(values), key=values.count)

        bool_keys = ["keeps_greetings", "keeps_reactions"]
        for key in bool_keys:
            values = [p.get(key) for p in pair_analyses if p.get(key) is not None]
            if values:
                aggregated[key] = sum(1 for v in values if v) > len(values) / 2

    if all_analyses:
        numeric_keys_single = ["silence_tolerance_sec", "cut_aggressiveness",
                               "preferred_clip_duration_sec", "hook_duration_sec"]
        for key in numeric_keys_single:
            if key not in aggregated:
                values = [a.get(key) for a in all_analyses if a.get(key) is not None]
                if values:
                    aggregated[key] = round(sum(values) / len(values), 2)

        cat_keys_single = ["hook_style", "pacing", "content_density",
                           "subtitle_style_preference", "transition_style", "energy_level"]
        for key in cat_keys_single:
            if key not in aggregated:
                values = [a.get(key) for a in all_analyses if a.get(key)]
                if values:
                    aggregated[key] = max(set(values), key=values.count)

    # Save aggregated style_params
    if aggregated:
        async with get_session() as session:
            await session.execute(text("""
                UPDATE editing_profiles
                SET style_params = :params, status = 'active', updated_at = NOW()
                WHERE id = :pid
            """), {"pid": profile_id, "params": json.dumps(aggregated)})
            await session.commit()
        logger.info(f"[editing-style] Profile {profile_id} style_params updated: {list(aggregated.keys())}")


# ─── Get Samples ──────────────────────────────────────────────────────────────

@router.get("/profiles/{profile_id}/samples")
async def list_samples(
    profile_id: str,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """プロファイルのサンプル動画一覧"""
    verify_admin(x_admin_key)
    await _ensure_tables()

    async with get_session() as session:
        result = await session.execute(text("""
            SELECT id, sample_type, video_url, original_video_url, analysis_result,
                   analysis_status, filename, duration_sec, created_at
            FROM editing_style_samples
            WHERE profile_id = :pid
            ORDER BY created_at ASC
        """), {"pid": profile_id})
        rows = result.fetchall()

    samples = []
    for s in rows:
        samples.append({
            "id": s.id,
            "sample_type": s.sample_type,
            "video_url": s.video_url,
            "original_video_url": s.original_video_url,
            "analysis_result": s.analysis_result if s.analysis_result else {},
            "analysis_status": s.analysis_status,
            "filename": s.filename,
            "duration_sec": s.duration_sec,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        })

    return {"samples": samples, "total": len(samples)}


# ─── Video Preview URL (fresh SAS token) ─────────────────────────────────────

class GetPreviewUrlRequest(BaseModel):
    video_url: str = Field(..., description="保存済みのBlob URL（SAS期限切れの可能性あり）")


@router.post("/get-preview-url")
async def get_preview_url(
    req: GetPreviewUrlRequest,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """動画プレビュー用のフレッシュなSAS URL を生成する"""
    verify_admin(x_admin_key)
    from app.services.storage_service import generate_read_sas_from_url
    # Strip existing SAS query params to get base blob URL
    base_url = req.video_url.split("?")[0] if "?" in req.video_url else req.video_url
    fresh_url = generate_read_sas_from_url(base_url, expires_hours=4)
    if not fresh_url:
        # Fallback: return the original URL as-is
        fresh_url = req.video_url
    return {"preview_url": fresh_url}
