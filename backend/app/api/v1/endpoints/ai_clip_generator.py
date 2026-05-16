"""
ai_clip_generator.py — 全自動AIクリップ生成エンドポイント
===========================================================
ClipDBからNG除外・ブランド選択済みクリップを自動選定し、
Whisper字幕生成→シーン別フォント切替→効果音自動挿入→
トランジション→最初3秒フック生成→サムネイル自動生成→Export
を全自動で行うAPIエンドポイント。

Endpoints:
  POST /ai-clip/generate          - 全自動クリップ生成ジョブ開始
  GET  /ai-clip/jobs/{job_id}     - ジョブ進捗確認
  GET  /ai-clip/jobs              - 全ジョブ一覧
  GET  /ai-clip/candidates        - 生成候補クリップ一覧（プレビュー）
  GET  /ai-clip/brands            - 利用可能ブランド一覧
  GET  /ai-clip/templates         - 編集テンプレート一覧

注意: 既存のclip_editor_v2.pyやclip_db.pyは一切変更しない。
ロジックは参考にしつつ、完全に独立した実装とする。
"""
import uuid
import json
import os
import logging
import tempfile
import asyncio
import time
import subprocess
from typing import Optional, List
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import APIRouter, HTTPException, Query, Header, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.db import AsyncSessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-clip", tags=["AI Clip Generator"])

# ─── Configuration ────────────────────────────────────────────────────────────
ADMIN_KEY = os.getenv("ADMIN_API_KEY", "aither:hub")

# ─── Job Storage (file-based, same pattern as clip_editor_v2) ─────────────────
_AI_CLIP_JOB_DIR = os.path.join(tempfile.gettempdir(), "aitherhub_ai_clip_jobs")
os.makedirs(_AI_CLIP_JOB_DIR, exist_ok=True)

# Concurrency limiter
_AI_CLIP_SEMAPHORE = asyncio.Semaphore(1)  # Max 1 concurrent AI clip generation (heavy)

# ─── ASS Subtitle Styles (copied from clip_editor_v2 for independence) ────────
_ASS_STYLES = {
    'simple': {
        'fontsize': 80, 'bold': 1, 'primary_color': '&H00FFFFFF',
        'outline_color': '&H00000000', 'outline': 5, 'shadow': 3,
        'border_style': 1, 'back_color': '&H80000000',
    },
    'box': {
        'fontsize': 80, 'bold': 1, 'primary_color': '&H00FFFFFF',
        'outline_color': '&H00000000', 'outline': 22, 'shadow': 0,
        'border_style': 3, 'back_color': '&H33000000',
    },
    'outline': {
        'fontsize': 84, 'bold': 1, 'primary_color': '&H00FFFFFF',
        'outline_color': '&H00000000', 'outline': 8, 'shadow': 0,
        'border_style': 1, 'back_color': '&H00000000',
    },
    'pop': {
        'fontsize': 90, 'bold': 1, 'primary_color': '&H0035E1FF',
        'outline_color': '&H00356BFF', 'outline': 7, 'shadow': 4,
        'border_style': 1, 'back_color': '&H70000000',
    },
    'gradient': {
        'fontsize': 80, 'bold': 1, 'primary_color': '&H00FFFFFF',
        'outline_color': '&H00000000', 'outline': 22, 'shadow': 0,
        'border_style': 3, 'back_color': '&H26C852BB',
    },
    'karaoke': {
        'fontsize': 84, 'bold': 1, 'primary_color': '&H7FFFFFFF',
        'outline_color': '&H00000000', 'outline': 22, 'shadow': 0,
        'border_style': 3, 'back_color': '&H4C000000',
        'secondary_color': '&H0035E1FF',
    },
}

# Hook style (large text for first 3 seconds)
_HOOK_STYLE = {
    'fontsize': 120, 'bold': 1, 'primary_color': '&H0035E1FF',  # Yellow
    'outline_color': '&H00356BFF', 'outline': 10, 'shadow': 5,
    'border_style': 1, 'back_color': '&H70000000',
}

# Scene-based style mapping
_SCENE_STYLE_MAP = {
    'intro': 'pop',        # 冒頭: インパクト重視
    'product': 'box',      # 商品紹介: 読みやすさ重視
    'demo': 'simple',      # デモ: シンプル
    'cta': 'gradient',     # CTA: 目立つ
    'closing': 'outline',  # 締め: クリーン
    'default': 'box',      # デフォルト
}

# Transition types for ffmpeg xfade
_TRANSITIONS = [
    'fade', 'fadeblack', 'fadewhite', 'slideleft', 'slideright',
    'slideup', 'slidedown', 'wipeleft', 'wiperight', 'wipeup', 'wipedown',
    'smoothleft', 'smoothright', 'smoothup', 'smoothdown',
]

# Sound effects (will be downloaded from Azure Blob Storage)
_SFX_TYPES = {
    'whoosh': 'sfx/whoosh.mp3',
    'pop': 'sfx/pop.mp3',
    'ding': 'sfx/ding.mp3',
    'swoosh': 'sfx/swoosh.mp3',
    'impact': 'sfx/impact.mp3',
}

# Font search paths (same as clip_editor_v2)
_FONT_SEARCH_PATHS = [
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc',
    '/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc',
    '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc',
]


# ─── DB Session Helper ────────────────────────────────────────────────────────
@asynccontextmanager
async def get_session():
    async with AsyncSessionLocal() as session:
        yield session
        await session.commit()


# ─── Auth Helper ──────────────────────────────────────────────────────────────
def verify_admin(x_admin_key: Optional[str] = Header(None)):
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ─── Job Management (file-based) ─────────────────────────────────────────────
def _save_job(job_id: str, data: dict):
    path = os.path.join(_AI_CLIP_JOB_DIR, f"{job_id}.json")
    with open(path, "w") as f:
        json.dump(data, f, default=str)


def _load_job(job_id: str) -> dict | None:
    path = os.path.join(_AI_CLIP_JOB_DIR, f"{job_id}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _update_job(job_id: str, **kwargs):
    if "progress_pct" in kwargs:
        try:
            kwargs["progress_pct"] = max(0, min(100, int(kwargs["progress_pct"])))
        except (TypeError, ValueError):
            kwargs["progress_pct"] = 0
    data = _load_job(job_id) or {}
    data.update(kwargs)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_job(job_id, data)


def _list_jobs(limit: int = 50) -> list:
    """List recent jobs sorted by creation time."""
    jobs = []
    if not os.path.exists(_AI_CLIP_JOB_DIR):
        return jobs
    for fname in os.listdir(_AI_CLIP_JOB_DIR):
        if not fname.endswith(".json"):
            continue
        try:
            path = os.path.join(_AI_CLIP_JOB_DIR, fname)
            with open(path) as f:
                data = json.load(f)
            jobs.append(data)
        except Exception:
            continue
    jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)
    return jobs[:limit]


# ─── Utility Functions ────────────────────────────────────────────────────────
def _find_cjk_font() -> str:
    """Find a CJK-capable font."""
    import glob
    for p in _FONT_SEARCH_PATHS:
        if os.path.exists(p):
            return p
    noto_fonts = glob.glob('/usr/share/fonts/**/NotoSans*CJK*', recursive=True)
    if noto_fonts:
        return noto_fonts[0]
    all_fonts = glob.glob('/usr/share/fonts/**/*.ttf', recursive=True) + \
               glob.glob('/usr/share/fonts/**/*.ttc', recursive=True)
    return all_fonts[0] if all_fonts else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'


def _seconds_to_ass_time(seconds: float) -> str:
    """Convert seconds to ASS time format: H:MM:SS.cc"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _classify_scene(text_content: str, time_start: float, total_duration: float) -> str:
    """Classify scene type based on content and timing."""
    position_ratio = time_start / max(total_duration, 1)

    # First 10%: intro
    if position_ratio < 0.1:
        return 'intro'
    # Last 10%: closing
    if position_ratio > 0.9:
        return 'closing'

    text_lower = text_content.lower()
    # CTA keywords
    cta_keywords = ['買', '購入', 'リンク', 'url', 'クーポン', '割引', '限定', '今すぐ',
                    '下單', '購買', '連結', '優惠', '折扣']
    if any(kw in text_lower for kw in cta_keywords):
        return 'cta'

    # Product keywords
    product_keywords = ['商品', '製品', 'プロダクト', '成分', '効果', '使い方',
                       '產品', '成份', '效果', '用法']
    if any(kw in text_lower for kw in product_keywords):
        return 'product'

    # Demo keywords
    demo_keywords = ['見て', '実際', 'こんな感じ', 'デモ', '使って',
                    '看', '實際', '示範']
    if any(kw in text_lower for kw in demo_keywords):
        return 'demo'

    return 'default'


# ─── Pydantic Models ─────────────────────────────────────────────────────────
class GenerateRequest(BaseModel):
    """全自動AIクリップ生成リクエスト"""
    brand_id: Optional[str] = Field(None, description="ブランドID（widget_clientsのclient_id）")
    max_clips: int = Field(5, ge=1, le=20, description="最大生成クリップ数")
    subtitle_style: str = Field("auto", description="字幕スタイル (auto/simple/box/outline/pop/gradient/karaoke)")
    enable_sfx: bool = Field(True, description="効果音を自動挿入するか")
    enable_transitions: bool = Field(True, description="トランジションを追加するか")
    transition_type: str = Field("fade", description="トランジション種類")
    transition_duration: float = Field(0.5, ge=0.1, le=2.0, description="トランジション時間（秒）")
    enable_hook: bool = Field(True, description="最初3秒のフックテキストを生成するか")
    hook_text: Optional[str] = Field(None, description="カスタムフックテキスト（空の場合はAI生成）")
    enable_thumbnail: bool = Field(True, description="サムネイルを自動生成するか")
    min_duration: float = Field(10.0, ge=5.0, description="最小クリップ長（秒）")
    max_duration: float = Field(60.0, le=180.0, description="最大クリップ長（秒）")
    min_cta_score: int = Field(0, ge=0, le=5, description="最小CTAスコア")
    min_importance: float = Field(0.0, ge=0.0, description="最小重要度スコア")
    target_language: str = Field("auto", description="字幕言語 (auto/ja/zh/zh-tw)")
    position_y: float = Field(75.0, ge=0, le=100, description="字幕Y位置（%）")


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    progress_pct: int = 0
    current_step: str = ""
    clips_completed: int = 0
    clips_total: int = 0
    results: list = []
    error: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/brands")
async def list_brands(x_admin_key: Optional[str] = Header(None)):
    """利用可能なブランド（ウィジェットクライアント）一覧を取得"""
    verify_admin(x_admin_key)
    async with get_session() as session:
        result = await session.execute(text("""
            SELECT client_id, name, domain, is_active,
                   (SELECT COUNT(*) FROM widget_clip_assignments wca
                    WHERE wca.client_id = wc.client_id AND wca.is_active = TRUE) as clip_count
            FROM widget_clients wc
            WHERE wc.is_active = TRUE
            ORDER BY name
        """))
        rows = result.fetchall()
    return {
        "brands": [
            {
                "client_id": r.client_id,
                "name": r.name,
                "domain": r.domain,
                "clip_count": r.clip_count,
            }
            for r in rows
        ]
    }


@router.get("/candidates")
async def list_candidates(
    brand_id: Optional[str] = Query(None, description="ブランドID"),
    min_cta_score: int = Query(0, ge=0, le=5),
    min_importance: float = Query(0.0, ge=0.0),
    min_duration: float = Query(10.0),
    max_duration: float = Query(60.0),
    limit: int = Query(20, ge=1, le=100),
    x_admin_key: Optional[str] = Header(None),
):
    """生成候補クリップ一覧（プレビュー用）。NG除外・ブランド選択済みのみ。"""
    verify_admin(x_admin_key)

    conditions = [
        "vc.status = 'completed'",
        "vc.clip_url IS NOT NULL",
        "COALESCE(vc.is_unusable, FALSE) = FALSE",  # NG除外
    ]
    params: dict = {"limit": limit}

    # ブランド選択済みのみ（brand_id指定時）
    if brand_id:
        conditions.append("""
            vc.id::text IN (
                SELECT wca.clip_id FROM widget_clip_assignments wca
                WHERE wca.client_id = :brand_id AND wca.is_active = TRUE
            )
        """)
        params["brand_id"] = brand_id
    else:
        # ブランド未指定の場合でも、何かしらブランドが割り当てられているクリップのみ
        conditions.append("""
            vc.id::text IN (
                SELECT wca.clip_id FROM widget_clip_assignments wca
                WHERE wca.is_active = TRUE
            )
        """)

    # Duration filter
    if min_duration > 0:
        conditions.append("COALESCE(vc.duration_sec, 0) >= :min_duration")
        params["min_duration"] = min_duration
    if max_duration > 0:
        conditions.append("COALESCE(vc.duration_sec, 0) <= :max_duration")
        params["max_duration"] = max_duration

    # CTA score filter
    if min_cta_score > 0:
        conditions.append("COALESCE(vc.cta_score, 0) >= :min_cta_score")
        params["min_cta_score"] = min_cta_score

    # Importance score filter
    if min_importance > 0:
        conditions.append("COALESCE(vc.importance_score, 0) >= :min_importance")
        params["min_importance"] = min_importance

    where_clause = " AND ".join(conditions)

    async with get_session() as session:
        result = await session.execute(text(f"""
            SELECT vc.id as clip_id, vc.video_id, vc.phase_index,
                   vc.time_start, vc.time_end, vc.duration_sec,
                   vc.clip_url, vc.thumbnail_url, vc.transcript_text,
                   vc.product_name, vc.cta_score, vc.importance_score,
                   vc.liver_name, vc.stream_date, vc.captions,
                   vc.subtitle_style, vc.exported_url
            FROM video_clips vc
            WHERE {where_clause}
            ORDER BY COALESCE(vc.importance_score, 0) DESC,
                     COALESCE(vc.cta_score, 0) DESC
            LIMIT :limit
        """), params)
        rows = result.fetchall()

    return {
        "total": len(rows),
        "candidates": [
            {
                "clip_id": str(r.clip_id),
                "video_id": str(r.video_id),
                "phase_index": r.phase_index,
                "time_start": r.time_start,
                "time_end": r.time_end,
                "duration_sec": r.duration_sec,
                "clip_url": r.clip_url,
                "thumbnail_url": r.thumbnail_url,
                "transcript_text": (r.transcript_text or "")[:200],
                "product_name": r.product_name,
                "cta_score": r.cta_score,
                "importance_score": r.importance_score,
                "liver_name": r.liver_name,
                "stream_date": str(r.stream_date) if r.stream_date else None,
                "has_captions": r.captions is not None,
                "has_export": r.exported_url is not None,
                "subtitle_style": r.subtitle_style,
            }
            for r in rows
        ]
    }


@router.get("/templates")
async def list_templates(x_admin_key: Optional[str] = Header(None)):
    """編集テンプレート一覧（字幕スタイル・トランジション・効果音の組み合わせ）"""
    verify_admin(x_admin_key)
    return {
        "templates": [
            {
                "id": "sales_highlight",
                "name": "セールスハイライト",
                "description": "売上に繋がるシーンを強調。CTAスコア高いクリップ優先。",
                "subtitle_style": "pop",
                "enable_sfx": True,
                "enable_transitions": True,
                "transition_type": "fade",
                "enable_hook": True,
                "min_cta_score": 3,
            },
            {
                "id": "product_showcase",
                "name": "商品紹介",
                "description": "商品の魅力を伝えるクリーンな編集。",
                "subtitle_style": "box",
                "enable_sfx": False,
                "enable_transitions": True,
                "transition_type": "fadeblack",
                "enable_hook": True,
                "min_cta_score": 0,
            },
            {
                "id": "viral_short",
                "name": "バズ狙いショート",
                "description": "TikTok/Reels向け。派手な演出でインパクト重視。",
                "subtitle_style": "auto",
                "enable_sfx": True,
                "enable_transitions": True,
                "transition_type": "slideleft",
                "enable_hook": True,
                "min_cta_score": 0,
            },
            {
                "id": "minimal",
                "name": "ミニマル",
                "description": "字幕のみ。効果音・トランジションなし。",
                "subtitle_style": "simple",
                "enable_sfx": False,
                "enable_transitions": False,
                "transition_type": "fade",
                "enable_hook": False,
                "min_cta_score": 0,
            },
        ],
        "available_styles": list(_ASS_STYLES.keys()) + ["auto"],
        "available_transitions": _TRANSITIONS,
        "available_sfx": list(_SFX_TYPES.keys()),
    }


@router.post("/generate")
async def generate_ai_clip(
    req: GenerateRequest,
    background_tasks: BackgroundTasks,
    x_admin_key: Optional[str] = Header(None),
):
    """全自動AIクリップ生成ジョブを開始する"""
    verify_admin(x_admin_key)

    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    job_data = {
        "job_id": job_id,
        "status": "queued",
        "progress_pct": 0,
        "current_step": "候補クリップ選定中...",
        "clips_completed": 0,
        "clips_total": 0,
        "results": [],
        "error": None,
        "created_at": now,
        "updated_at": now,
        "config": req.dict(),
    }
    _save_job(job_id, job_data)

    # Start background processing
    background_tasks.add_task(_run_ai_clip_generation, job_id, req)

    return {
        "job_id": job_id,
        "status": "queued",
        "message": "全自動AIクリップ生成ジョブを開始しました",
    }


@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str, x_admin_key: Optional[str] = Header(None)):
    """ジョブの進捗を確認する"""
    verify_admin(x_admin_key)
    job = _load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/jobs")
async def list_jobs(
    limit: int = Query(20, ge=1, le=100),
    x_admin_key: Optional[str] = Header(None),
):
    """全ジョブ一覧を取得する"""
    verify_admin(x_admin_key)
    jobs = _list_jobs(limit=limit)
    return {"jobs": jobs, "total": len(jobs)}


# ─── Background Processing ───────────────────────────────────────────────────

async def _run_ai_clip_generation(job_id: str, req: GenerateRequest):
    """全自動AIクリップ生成のメインパイプライン"""
    try:
        async with _AI_CLIP_SEMAPHORE:
            await _run_ai_clip_generation_inner(job_id, req)
    except Exception as e:
        logger.error(f"[ai-clip {job_id}] Fatal error: {e}", exc_info=True)
        _update_job(job_id, status="failed", error=str(e)[:500])


async def _run_ai_clip_generation_inner(job_id: str, req: GenerateRequest):
    """Inner pipeline logic."""
    import httpx

    logger.info(f"[ai-clip {job_id}] Starting generation pipeline")
    _update_job(job_id, status="selecting", progress_pct=5, current_step="候補クリップ選定中...")

    # ── Step 1: Select candidate clips from ClipDB ──
    candidates = await _select_candidates(req)
    if not candidates:
        _update_job(job_id, status="failed", error="条件に合うクリップが見つかりませんでした")
        return

    clips_total = min(len(candidates), req.max_clips)
    _update_job(job_id, clips_total=clips_total, progress_pct=10,
                current_step=f"{clips_total}件のクリップを選定完了")
    logger.info(f"[ai-clip {job_id}] Selected {clips_total} candidates")

    results = []
    for idx, clip in enumerate(candidates[:clips_total]):
        clip_id = str(clip["clip_id"])
        step_base_pct = 10 + int((idx / clips_total) * 80)

        try:
            _update_job(job_id, status="processing", progress_pct=step_base_pct,
                        current_step=f"クリップ {idx+1}/{clips_total} 処理中...",
                        clips_completed=idx)

            result = await _process_single_clip(job_id, clip, req, idx, clips_total)
            results.append(result)

            _update_job(job_id, clips_completed=idx + 1, results=results)
            logger.info(f"[ai-clip {job_id}] Clip {idx+1}/{clips_total} done: {clip_id}")

        except Exception as e:
            logger.error(f"[ai-clip {job_id}] Clip {idx+1} failed: {e}", exc_info=True)
            results.append({
                "clip_id": clip_id,
                "status": "failed",
                "error": str(e)[:200],
            })
            _update_job(job_id, clips_completed=idx + 1, results=results)

    # ── Final ──
    success_count = sum(1 for r in results if r.get("status") == "done")
    _update_job(
        job_id,
        status="done",
        progress_pct=100,
        current_step=f"完了: {success_count}/{clips_total}件成功",
        clips_completed=clips_total,
        results=results,
    )
    logger.info(f"[ai-clip {job_id}] Pipeline complete: {success_count}/{clips_total} success")


async def _select_candidates(req: GenerateRequest) -> list:
    """ClipDBから候補クリップを選定する"""
    conditions = [
        "vc.status = 'completed'",
        "vc.clip_url IS NOT NULL",
        "COALESCE(vc.is_unusable, FALSE) = FALSE",
    ]
    params: dict = {"limit": req.max_clips * 3}  # 余裕を持って取得

    if req.brand_id:
        conditions.append("""
            vc.id::text IN (
                SELECT wca.clip_id FROM widget_clip_assignments wca
                WHERE wca.client_id = :brand_id AND wca.is_active = TRUE
            )
        """)
        params["brand_id"] = req.brand_id
    else:
        conditions.append("""
            vc.id::text IN (
                SELECT wca.clip_id FROM widget_clip_assignments wca
                WHERE wca.is_active = TRUE
            )
        """)

    if req.min_duration > 0:
        conditions.append("COALESCE(vc.duration_sec, 0) >= :min_duration")
        params["min_duration"] = req.min_duration
    if req.max_duration > 0:
        conditions.append("COALESCE(vc.duration_sec, 0) <= :max_duration")
        params["max_duration"] = req.max_duration
    if req.min_cta_score > 0:
        conditions.append("COALESCE(vc.cta_score, 0) >= :min_cta_score")
        params["min_cta_score"] = req.min_cta_score
    if req.min_importance > 0:
        conditions.append("COALESCE(vc.importance_score, 0) >= :min_importance")
        params["min_importance"] = req.min_importance

    where_clause = " AND ".join(conditions)

    async with get_session() as session:
        result = await session.execute(text(f"""
            SELECT vc.id as clip_id, vc.video_id, vc.phase_index,
                   vc.time_start, vc.time_end, vc.duration_sec,
                   vc.clip_url, vc.thumbnail_url, vc.transcript_text,
                   vc.product_name, vc.cta_score, vc.importance_score,
                   vc.captions, vc.subtitle_style, vc.liver_name
            FROM video_clips vc
            WHERE {where_clause}
            ORDER BY COALESCE(vc.importance_score, 0) DESC,
                     COALESCE(vc.cta_score, 0) DESC,
                     vc.created_at DESC
            LIMIT :limit
        """), params)
        rows = result.fetchall()

    return [
        {
            "clip_id": r.clip_id,
            "video_id": r.video_id,
            "phase_index": r.phase_index,
            "time_start": r.time_start,
            "time_end": r.time_end,
            "duration_sec": r.duration_sec,
            "clip_url": r.clip_url,
            "thumbnail_url": r.thumbnail_url,
            "transcript_text": r.transcript_text,
            "product_name": r.product_name,
            "cta_score": r.cta_score,
            "importance_score": r.importance_score,
            "captions": r.captions,
            "subtitle_style": r.subtitle_style,
            "liver_name": r.liver_name,
        }
        for r in rows
    ]


async def _process_single_clip(job_id: str, clip: dict, req: GenerateRequest,
                                idx: int, total: int) -> dict:
    """1つのクリップを全自動処理する"""
    import httpx

    clip_id = str(clip["clip_id"])
    clip_url = clip["clip_url"]
    captions = clip.get("captions")
    tmp_dir = tempfile.mkdtemp(prefix=f"ai_clip_{clip_id[:8]}_")

    try:
        # ── 1. Download clip ──
        _update_job(job_id, current_step=f"クリップ {idx+1}/{total}: ダウンロード中...")
        video_path = os.path.join(tmp_dir, "input.mp4")

        download_url = clip_url
        # Generate SAS token if needed
        if "?" not in download_url or "sig=" not in download_url:
            try:
                from app.services.storage_service import generate_read_sas_from_url
                sas_url = generate_read_sas_from_url(download_url, expires_hours=2)
                if sas_url:
                    download_url = sas_url
            except Exception as e:
                logger.warning(f"[ai-clip {job_id}] SAS generation failed: {e}")

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.get(download_url)
            resp.raise_for_status()
            with open(video_path, "wb") as f:
                f.write(resp.content)

        file_size = os.path.getsize(video_path)
        logger.info(f"[ai-clip {job_id}] Downloaded clip: {file_size} bytes")

        # ── 2. Get video info (resolution, duration) ──
        probe_cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", video_path
        ]
        probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
        probe_data = json.loads(probe_result.stdout) if probe_result.returncode == 0 else {}

        video_width = 1080
        video_height = 1920
        duration = clip.get("duration_sec") or 30.0
        for stream in probe_data.get("streams", []):
            if stream.get("codec_type") == "video":
                video_width = int(stream.get("width", 1080))
                video_height = int(stream.get("height", 1920))
                if "duration" in stream:
                    duration = float(stream["duration"])
                break
        if "format" in probe_data and "duration" in probe_data["format"]:
            duration = float(probe_data["format"]["duration"])

        # ── 3. Transcribe (if no captions exist) ──
        if not captions:
            _update_job(job_id, current_step=f"クリップ {idx+1}/{total}: 字幕生成中 (Whisper)...")
            captions = await _transcribe_clip(video_path, req.target_language)

        # Parse captions if string
        if isinstance(captions, str):
            try:
                captions = json.loads(captions)
            except Exception:
                captions = []

        if not captions:
            captions = []

        # ── 4. Hook detection & generation ──
        hook_text = None
        if req.enable_hook:
            _update_job(job_id, current_step=f"クリップ {idx+1}/{total}: フック生成中...")
            hook_text = await _generate_hook(captions, clip, req)

        # ── 5. Scene classification & style assignment ──
        _update_job(job_id, current_step=f"クリップ {idx+1}/{total}: シーン分析中...")
        styled_captions = _assign_scene_styles(captions, duration, req.subtitle_style)

        # ── 6. Generate ASS subtitle file ──
        _update_job(job_id, current_step=f"クリップ {idx+1}/{total}: 字幕ファイル生成中...")
        ass_path = os.path.join(tmp_dir, "subtitles.ass")
        _generate_multi_style_ass(
            styled_captions, hook_text, ass_path,
            video_width, video_height, duration, req.position_y
        )

        # ── 7. Build ffmpeg command ──
        _update_job(job_id, current_step=f"クリップ {idx+1}/{total}: エンコード中...")
        output_path = os.path.join(tmp_dir, "output.mp4")
        ffmpeg_cmd = _build_ffmpeg_command(
            video_path, ass_path, output_path,
            video_width, video_height, duration, req
        )

        # Execute ffmpeg
        logger.info(f"[ai-clip {job_id}] ffmpeg: {' '.join(ffmpeg_cmd[:10])}...")
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)

        if proc.returncode != 0:
            err_msg = stderr.decode()[-500:] if stderr else "Unknown error"
            raise RuntimeError(f"ffmpeg failed: {err_msg}")

        output_size = os.path.getsize(output_path)
        logger.info(f"[ai-clip {job_id}] Encoded: {output_size} bytes")

        # Get actual output duration
        actual_duration = duration
        try:
            probe_out = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", output_path],
                capture_output=True, text=True, timeout=15
            )
            if probe_out.returncode == 0:
                probe_out_data = json.loads(probe_out.stdout)
                if "format" in probe_out_data and "duration" in probe_out_data["format"]:
                    actual_duration = float(probe_out_data["format"]["duration"])
        except Exception:
            pass

        # ── 8. Generate thumbnail ──
        thumbnail_url = None
        if req.enable_thumbnail:
            _update_job(job_id, current_step=f"クリップ {idx+1}/{total}: サムネイル生成中...")
            thumbnail_url = await _generate_thumbnail(output_path, tmp_dir, clip_id)

        # ── 9. Upload to Azure Blob Storage ──
        _update_job(job_id, current_step=f"クリップ {idx+1}/{total}: アップロード中...")
        download_url, blob_url = await _upload_to_blob(output_path, clip_id, job_id)

        # ── 10. Save to DB ──
        await _save_export_record(clip_id, blob_url, thumbnail_url)

        return {
            "clip_id": clip_id,
            "status": "done",
            "download_url": download_url,
            "blob_url": blob_url,
            "thumbnail_url": thumbnail_url,
            "file_size": output_size,
            "duration_sec": actual_duration,
            "hook_text": hook_text,
            "captions_count": len(captions),
        }

    except Exception as e:
        raise
    finally:
        # Cleanup temp files
        import shutil
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


async def _transcribe_clip(video_path: str, target_language: str = "auto") -> list:
    """Whisper APIで字幕を生成する"""
    import openai

    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "https://aoai-kyogoku-service.openai.azure.com/")
    azure_key = os.getenv("AZURE_OPENAI_KEY", "")

    if not azure_key:
        logger.warning("[ai-clip] No Azure OpenAI key configured, skipping transcription")
        return []

    from urllib.parse import urlparse as _urlparse
    _parsed = _urlparse(azure_endpoint)
    clean_endpoint = f"{_parsed.scheme}://{_parsed.netloc}/"

    openai_client = openai.AsyncAzureOpenAI(
        api_key=azure_key,
        api_version="2024-06-01",
        azure_endpoint=clean_endpoint,
    )

    # Determine language
    whisper_lang_map = {"ja": "ja", "zh-tw": "zh", "zh": "zh"}
    is_auto = target_language == "auto"
    whisper_language = None if is_auto else whisper_lang_map.get(target_language, "ja")

    # Whisper prompt
    whisper_prompt = (
        "全頭ブリーチ、カラーリング、ヘアケア、ケラチン、アミノ酸、コラーゲン、"
        "頭皮、毛穴、髪質、ダメージ、補修、シャンプー、トリートメント、"
        "ヘアマスク、KYOGOKU、京極、ライブ配信、ライブコマース"
    )

    # Load user dictionary
    try:
        async with get_session() as session:
            dict_result = await session.execute(text("""
                SELECT from_text, to_text FROM subtitle_dictionary
                WHERE user_id = 'default' AND is_active = TRUE
            """))
            dict_rows = dict_result.fetchall()
        if dict_rows:
            dict_terms = []
            for dr in dict_rows:
                term = (dr.to_text.strip() if dr.to_text and dr.to_text.strip() else dr.from_text.strip())
                if term and term not in dict_terms:
                    dict_terms.append(term)
            if dict_terms:
                whisper_prompt += "、" + "、".join(dict_terms[:50])
    except Exception as e:
        logger.warning(f"[ai-clip] Dict load failed: {e}")

    # ── Extract audio to mp3 first (Whisper has 25MB file size limit) ──
    whisper_file = video_path
    tmp_audio_dir = os.path.dirname(video_path)
    audio_path = os.path.join(tmp_audio_dir, "audio_for_whisper.mp3")
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", video_path,
            "-vn",  # no video
            "-acodec", "libmp3lame",
            "-ar", "16000",  # 16kHz (optimal for Whisper)
            "-ac", "1",  # mono
            "-b:a", "64k",  # 64kbps
            audio_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_audio, stderr_audio = await asyncio.wait_for(proc.communicate(), timeout=60)
        if proc.returncode == 0 and os.path.exists(audio_path):
            audio_size = os.path.getsize(audio_path)
            video_size = os.path.getsize(video_path)
            logger.info(f"[ai-clip] Extracted audio: {audio_size/1024/1024:.1f} MB (from {video_size/1024/1024:.1f} MB video)")
            whisper_file = audio_path
        else:
            logger.warning(f"[ai-clip] Audio extraction failed (rc={proc.returncode}), using original video")
    except Exception as audio_err:
        logger.warning(f"[ai-clip] Audio extraction error: {audio_err}, using original video")

    try:
        with open(whisper_file, "rb") as f:
            whisper_kwargs = dict(
                model="whisper",
                file=f,
                response_format="verbose_json",
                timestamp_granularities=["segment", "word"],
            )
            if whisper_language:
                whisper_kwargs["language"] = whisper_language
            if whisper_prompt:
                whisper_kwargs["prompt"] = whisper_prompt

            response = await openai_client.audio.transcriptions.create(**whisper_kwargs)

        segments = []
        if hasattr(response, "segments") and response.segments:
            for seg in response.segments:
                segments.append({
                    "start": seg.get("start", seg.start) if hasattr(seg, "start") else seg.get("start", 0),
                    "end": seg.get("end", seg.end) if hasattr(seg, "end") else seg.get("end", 0),
                    "text": seg.get("text", seg.text) if hasattr(seg, "text") else seg.get("text", ""),
                })
        elif hasattr(response, "text") and response.text:
            # Fallback: single segment
            segments.append({"start": 0, "end": 30, "text": response.text})

        logger.info(f"[ai-clip] Transcribed: {len(segments)} segments")
        return segments

    except Exception as e:
        logger.error(f"[ai-clip] Whisper failed: {e}")
        return []


async def _generate_hook(captions: list, clip: dict, req: GenerateRequest) -> str:
    """フックテキストを生成する（AI or ルールベース）"""
    # If custom hook text provided, use it
    if req.hook_text:
        return req.hook_text

    # Try to detect hook from existing captions using hook_detection_service logic
    from app.services.hook_detection_service import detect_hooks, suggest_hook_placement

    if captions:
        hooks = detect_hooks(captions, max_candidates=5)
        if hooks and hooks[0].hook_score >= 50:
            return hooks[0].text[:50]  # Use detected hook

    # Fallback: Generate hook using GPT
    transcript = " ".join(c.get("text", "") for c in (captions or []))[:500]
    product_name = clip.get("product_name") or ""

    try:
        import openai
        azure_key = os.getenv("AZURE_OPENAI_KEY", "")
        azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
        azure_model = os.getenv("GPT5_MODEL") or os.getenv("GPT5_DEPLOYMENT") or "gpt-4.1-mini"

        if not azure_key or not azure_endpoint:
            # Fallback to simple hook
            return _generate_simple_hook(product_name, transcript)

        from urllib.parse import urlparse as _urlparse
        _parsed = _urlparse(azure_endpoint)
        clean_endpoint = f"{_parsed.scheme}://{_parsed.netloc}/"

        client = openai.AzureOpenAI(
            api_key=azure_key,
            azure_endpoint=clean_endpoint,
            api_version=os.getenv("GPT5_API_VERSION", "2025-04-01-preview"),
        )

        prompt = f"""以下のライブコマース動画の内容から、TikTok/Reelsの最初3秒で視聴者を引き付ける
フックテキスト（キャッチコピー）を1つ生成してください。

条件:
- 15文字以内
- 視聴者の興味を引く疑問文、衝撃的な事実、または数字を含む
- 商品名: {product_name}
- 動画内容: {transcript[:300]}

フックテキストのみを出力してください（説明不要）:"""

        response = client.responses.create(
            model=azure_model,
            input=[{"role": "user", "content": prompt}],
            max_output_tokens=100,
        )

        result = ""
        if hasattr(response, "output_text") and response.output_text:
            result = response.output_text.strip()
        elif hasattr(response, "output") and response.output:
            for item in response.output:
                if hasattr(item, "content"):
                    for part in item.content:
                        if hasattr(part, "text"):
                            result += part.text
            result = result.strip()

        if result:
            # Clean up: remove quotes, limit length
            result = result.strip('"\'「」『』').strip()
            return result[:30]

    except Exception as e:
        logger.warning(f"[ai-clip] Hook generation via GPT failed: {e}")

    return _generate_simple_hook(product_name, transcript)


def _generate_simple_hook(product_name: str, transcript: str) -> str:
    """シンプルなフックテキストを生成（GPTが使えない場合のフォールバック）"""
    if product_name:
        hooks = [
            f"知らないと損！{product_name}",
            f"プロが選ぶ{product_name}",
            f"衝撃の{product_name}",
        ]
        import random
        return random.choice(hooks)[:25]
    return "プロが教える美髪の秘密"


def _assign_scene_styles(captions: list, total_duration: float, base_style: str) -> list:
    """各字幕セグメントにシーンに応じたスタイルを割り当てる"""
    styled = []
    for cap in captions:
        cap_start = float(cap.get("start", 0))
        cap_text = cap.get("text", "")

        if base_style != "auto":
            # Fixed style
            style = base_style
        else:
            # Auto: classify scene and assign style
            scene = _classify_scene(cap_text, cap_start, total_duration)
            style = _SCENE_STYLE_MAP.get(scene, 'box')

        styled.append({
            **cap,
            "style": style,
        })
    return styled


def _generate_multi_style_ass(styled_captions: list, hook_text: Optional[str],
                               ass_path: str, video_width: int, video_height: int,
                               duration: float, position_y: float):
    """複数スタイルのASS字幕ファイルを生成する"""
    # Calculate alignment and margin
    if position_y < 33:
        alignment = 8  # top-center
        margin_v = max(20, int(video_height * position_y / 100))
    elif position_y < 66:
        alignment = 5  # middle-center
        margin_v = 30
    else:
        alignment = 2  # bottom-center
        margin_v = max(20, int(video_height * (100 - position_y) / 100))

    base_width = 1080
    scale_factor = min(video_width, video_height) / base_width

    ass = "[Script Info]\n"
    ass += "ScriptType: v4.00+\n"
    ass += f"PlayResX: {video_width}\n"
    ass += f"PlayResY: {video_height}\n"
    ass += "WrapStyle: 0\n\n"

    # Define all styles
    ass += "[V4+ Styles]\n"
    ass += "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"

    for style_name, s in _ASS_STYLES.items():
        fontsize = max(40, int(s['fontsize'] * scale_factor))
        outline_val = max(2, int(s['outline'] * scale_factor))
        shadow_val = max(0, int(s.get('shadow', 0) * scale_factor))
        secondary = s.get('secondary_color', '&H0000FFFF')
        ass += (f"Style: {style_name},Noto Sans CJK JP,{fontsize},{s['primary_color']},{secondary},"
                f"{s['outline_color']},{s['back_color']},{s['bold']},0,0,0,100,100,2,0,"
                f"{s['border_style']},{outline_val},{shadow_val},{alignment},"
                f"40,40,{margin_v},1\n")

    # Hook style (large, top-center)
    hook_fontsize = max(60, int(_HOOK_STYLE['fontsize'] * scale_factor))
    hook_outline = max(3, int(_HOOK_STYLE['outline'] * scale_factor))
    hook_shadow = max(0, int(_HOOK_STYLE['shadow'] * scale_factor))
    ass += (f"Style: hook,Noto Sans CJK JP,{hook_fontsize},{_HOOK_STYLE['primary_color']},&H0000FFFF,"
            f"{_HOOK_STYLE['outline_color']},{_HOOK_STYLE['back_color']},{_HOOK_STYLE['bold']},0,0,0,100,100,2,0,"
            f"{_HOOK_STYLE['border_style']},{hook_outline},{hook_shadow},8,"
            f"40,40,100,1\n")

    ass += "\n[Events]\n"
    ass += "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"

    # Add hook text (first 3 seconds)
    if hook_text:
        hook_start = _seconds_to_ass_time(0)
        hook_end = _seconds_to_ass_time(3.0)
        # Escape ASS special chars
        safe_hook = hook_text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
        ass += f"Dialogue: 1,{hook_start},{hook_end},hook,,0,0,0,,{safe_hook}\n"

    # Add styled captions
    MIN_DISPLAY = 2.5
    for i, cap in enumerate(styled_captions):
        cap_start = float(cap.get("start", 0))
        cap_end = float(cap.get("end", 0))
        cap_text = cap.get("text", "").strip()
        style = cap.get("style", "box")

        if not cap_text:
            continue
        if cap_end <= cap_start:
            cap_end = cap_start + MIN_DISPLAY

        # Extend short captions
        if cap_end - cap_start < MIN_DISPLAY:
            cap_end = cap_start + MIN_DISPLAY
            # Cap at next caption start
            if i + 1 < len(styled_captions):
                next_start = float(styled_captions[i + 1].get("start", 0))
                if next_start > cap_start:
                    cap_end = min(cap_end, next_start)

        start_ts = _seconds_to_ass_time(cap_start)
        end_ts = _seconds_to_ass_time(cap_end)
        safe_text = cap_text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
        ass += f"Dialogue: 0,{start_ts},{end_ts},{style},,0,0,0,,{safe_text}\n"

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass)

    logger.info(f"[ai-clip] Generated ASS: {len(styled_captions)} captions, hook={'yes' if hook_text else 'no'}")


def _build_ffmpeg_command(video_path: str, ass_path: str, output_path: str,
                          video_width: int, video_height: int,
                          duration: float, req: GenerateRequest) -> list:
    """ffmpegコマンドを構築する"""
    # Base command with ASS subtitles
    filter_complex_parts = []
    input_files = ["-i", video_path]

    # Video filter: burn ASS subtitles
    # Use ass filter (requires libass, available in our Docker image)
    vf = f"ass='{ass_path}'"
    filter_complex_parts.append(vf)

    # Build the full filter
    video_filter = ",".join(filter_complex_parts)

    cmd = [
        "ffmpeg", "-y",
        *input_files,
        "-vf", video_filter,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        "-t", str(min(duration, req.max_duration)),
        output_path,
    ]

    return cmd


async def _generate_thumbnail(video_path: str, tmp_dir: str, clip_id: str) -> Optional[str]:
    """動画からサムネイルを生成してBlobにアップロード"""
    thumbnail_path = os.path.join(tmp_dir, "thumbnail.jpg")

    # Extract frame at 1 second (or first frame if shorter)
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-ss", "1", "-vframes", "1",
        "-vf", "scale=720:-1",
        "-q:v", "2",
        thumbnail_path,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    await asyncio.wait_for(proc.communicate(), timeout=30)

    if proc.returncode != 0 or not os.path.exists(thumbnail_path):
        logger.warning(f"[ai-clip] Thumbnail generation failed for {clip_id}")
        return None

    # Upload thumbnail to blob
    try:
        from azure.storage.blob import BlobServiceClient, ContentSettings
        from app.services.storage_service import (
            CONNECTION_STRING, ACCOUNT_NAME, CONTAINER_NAME,
            generate_read_sas_from_url,
        )

        if not CONNECTION_STRING:
            return None

        blob_name = f"ai-clips/thumbnails/{clip_id}_{uuid.uuid4().hex[:8]}.jpg"
        svc = BlobServiceClient.from_connection_string(CONNECTION_STRING)
        bc = svc.get_blob_client(container=CONTAINER_NAME, blob=blob_name)

        with open(thumbnail_path, "rb") as data:
            bc.upload_blob(
                data, overwrite=True,
                content_settings=ContentSettings(content_type="image/jpeg")
            )

        blob_url = f"https://{ACCOUNT_NAME}.blob.core.windows.net/{CONTAINER_NAME}/{blob_name}"

        # Generate SAS URL for thumbnail (CDN may 409 for new blobs)
        try:
            sas_url = generate_read_sas_from_url(blob_url, expires_hours=720)  # 30 days
            if sas_url:
                return sas_url
        except Exception as sas_err:
            logger.warning(f"[ai-clip] Thumbnail SAS generation failed: {sas_err}")

        # Fallback: try CDN URL
        cdn_host = os.getenv("CDN_HOST", "https://cdn.aitherhub.com")
        blob_host = f"https://{ACCOUNT_NAME}.blob.core.windows.net"
        if cdn_host and blob_host in blob_url:
            return blob_url.replace(blob_host, cdn_host)
        return blob_url

    except Exception as e:
        logger.warning(f"[ai-clip] Thumbnail upload failed: {e}")
        return None


async def _upload_to_blob(output_path: str, clip_id: str, job_id: str) -> tuple:
    """完成動画をAzure Blob Storageにアップロード"""
    from azure.storage.blob import BlobServiceClient, ContentSettings
    from app.services.storage_service import (
        CONNECTION_STRING, ACCOUNT_NAME, CONTAINER_NAME,
        generate_read_sas_from_url,
    )

    if not CONNECTION_STRING:
        raise RuntimeError("Azure Storage not configured")

    blob_name = f"ai-clips/exports/{clip_id}_{uuid.uuid4().hex[:8]}.mp4"
    svc = BlobServiceClient.from_connection_string(CONNECTION_STRING)
    bc = svc.get_blob_client(container=CONTAINER_NAME, blob=blob_name)

    def _upload():
        with open(output_path, "rb") as data:
            bc.upload_blob(
                data, overwrite=True,
                content_settings=ContentSettings(content_type="video/mp4")
            )

    await asyncio.get_event_loop().run_in_executor(None, _upload)
    logger.info(f"[ai-clip {job_id}] Uploaded: {blob_name}")

    blob_url = f"https://{ACCOUNT_NAME}.blob.core.windows.net/{CONTAINER_NAME}/{blob_name}"

    # Generate download URL with SAS token
    try:
        disposition = f'attachment; filename="ai_clip_{clip_id[:8]}.mp4"'
        download_url = generate_read_sas_from_url(blob_url, expires_hours=72, content_disposition=disposition)
        if not download_url:
            download_url = blob_url
    except Exception:
        download_url = blob_url

    # CDN URL
    cdn_host = os.getenv("CDN_HOST", "https://cdn.aitherhub.com")
    blob_host = f"https://{ACCOUNT_NAME}.blob.core.windows.net"
    if cdn_host and blob_host in blob_url:
        blob_url = blob_url.replace(blob_host, cdn_host)

    return download_url, blob_url


async def _save_export_record(clip_id: str, blob_url: str, thumbnail_url: Optional[str]):
    """エクスポート結果をDBに保存"""
    try:
        async with get_session() as session:
            await session.execute(text("""
                UPDATE video_clips
                SET exported_url = :exported_url,
                    exported_at = NOW()
                WHERE id = :clip_id::uuid
            """), {"clip_id": clip_id, "exported_url": blob_url})
            logger.info(f"[ai-clip] Saved export record for clip {clip_id}")
    except Exception as e:
        logger.warning(f"[ai-clip] Failed to save export record: {e}")
