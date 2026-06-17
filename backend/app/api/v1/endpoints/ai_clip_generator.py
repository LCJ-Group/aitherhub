"""
ai_clip_generator.py — 全自動AIクリップ生成エンドポイント V2
===========================================================
ClipDBからNG除外・ブランド選択済みクリップを自動選定し、
Whisper字幕生成→シーン別フォント切替→ズームパルス→無音カット→
進行バー→ループ感→CTA最適化→サムネイル強化→Export
を全自動で行うAPIエンドポイント。

V2 新機能:
  - 無音/フィラーカット（Whisperタイムスタンプで無音区間を検出→カット）
  - ズームパルス（音量ピーク検出→zoompan 1.05x）
  - 画面揺れ（シェイク）（強調ポイントでcrop offset微振動）
  - 進行バー（画面下部にプログレスバー描画）
  - ループ感演出（最後1秒フェードアウト→最初フレームオーバーレイ）
  - 字幕出現アニメーション（ASS \\fad タグ）
  - キーワードハイライト（商品名・CTA語を別色）
  - 最初0.5秒の視覚インパクト（コントラスト強調フラッシュ）
  - CTA字幕最適化（最後3秒にGPT生成CTA）
  - サムネイル強化（テキスト入り＋背景ぼかし）
  - シーン別字幕スタイル自動切替（autoモードデフォルト化）

Endpoints:
  POST /ai-clip/generate          - 全自動クリップ生成ジョブ開始
  GET  /ai-clip/jobs/{job_id}     - ジョブ進捗確認
  GET  /ai-clip/jobs              - 全ジョブ一覧
  GET  /ai-clip/candidates        - 生成候補クリップ一覧（プレビュー）
  GET  /ai-clip/brands            - 利用可能ブランド一覧
  GET  /ai-clip/templates         - 編集テンプレート一覧
  GET  /ai-clip/diagnostics       - 環境診断

注意: 既存のclip_editor_v2.pyやclip_db.pyは一切変更しない。
ロジックは参考にしつつ、完全に独立した実装とする。
"""
import uuid
import json
import os
import re
import logging
import tempfile
import asyncio
import time
import math
import random
import subprocess
from typing import Optional, List
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import APIRouter, HTTPException, Query, Header, BackgroundTasks, UploadFile, File, Form, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AsyncSessionLocal, engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-clip", tags=["AI Clip Generator"])

# ─── Configuration ────────────────────────────────────────────────────────────
ADMIN_KEY = os.getenv("ADMIN_API_KEY", "aither:hub")

# ─── Job Storage (DB-persistent) ─────────────────────────────────────────────
# Legacy file-based dir kept for backward compat during transition
_AI_CLIP_JOB_DIR = os.path.join(tempfile.gettempdir(), "aitherhub_ai_clip_jobs")
os.makedirs(_AI_CLIP_JOB_DIR, exist_ok=True)

_DB_TABLE_ENSURED = False
_LAST_DB_SAVE_ERROR = None

async def _ensure_jobs_table():
    """Create ai_clip_jobs table if not exists (idempotent)"""
    global _DB_TABLE_ENSURED
    if _DB_TABLE_ENSURED:
        return
    try:
        async with engine.begin() as conn:
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS ai_clip_jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'queued',
                    progress_pct INTEGER NOT NULL DEFAULT 0,
                    current_step TEXT,
                    clips_completed INTEGER NOT NULL DEFAULT 0,
                    clips_total INTEGER NOT NULL DEFAULT 0,
                    results JSONB DEFAULT '[]'::jsonb,
                    error TEXT,
                    config JSONB DEFAULT '{}'::jsonb,
                    source_clip JSONB,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """))
        _DB_TABLE_ENSURED = True
        logger.info("[ai-clip] ai_clip_jobs table ensured")
    except Exception as e:
        logger.warning(f"[ai-clip] Failed to ensure jobs table: {e}")

# ─── Product Master Table ─────────────────────────────────────────────────────
_PRODUCT_MASTER_TABLE_ENSURED = False

async def _ensure_product_master_table():
    """Create product_master table if not exists (idempotent)"""
    global _PRODUCT_MASTER_TABLE_ENSURED
    if _PRODUCT_MASTER_TABLE_ENSURED:
        return
    try:
        async with engine.begin() as conn:
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS product_master (
                    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
                    product_name TEXT NOT NULL,
                    brand_name TEXT,
                    product_image_urls JSONB DEFAULT '[]'::jsonb,
                    keywords TEXT[],
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """))
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_product_master_name
                ON product_master (product_name)
            """))
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_product_master_active
                ON product_master (is_active) WHERE is_active = TRUE
            """))
        _PRODUCT_MASTER_TABLE_ENSURED = True
        logger.info("[ai-clip] product_master table ensured")
    except Exception as e:
        logger.warning(f"[ai-clip] Failed to ensure product_master table: {e}")



def _refresh_product_image_urls(urls: list) -> list:
    """Refresh SAS tokens for product image URLs.
    
    Product images are stored in Azure Blob with SAS tokens that expire.
    This function strips old SAS tokens and generates fresh ones.
    """
    if not urls:
        return []
    from app.services.storage_service import generate_read_sas_from_url
    refreshed = []
    for url in urls:
        if not url:
            continue
        # Strip existing SAS query params to get base blob URL
        base_url = url.split("?", 1)[0] if "?" in url else url
        # Generate fresh SAS (24h expiry)
        fresh_url = generate_read_sas_from_url(base_url)
        refreshed.append(fresh_url if fresh_url else base_url)
    return refreshed

# ─── AI Clip Deletion Feedback Table ─────────────────────────────────────────
# NOTE: 'clip_feedback' table is used by clip_feedback.py for adopt/reject workflow.
# We use a separate 'ai_clip_deletion_feedback' table to avoid schema conflicts.
_FEEDBACK_TABLE_ENSURED = False

async def _ensure_feedback_table():
    """Create ai_clip_deletion_feedback table for AI learning from deletions (idempotent)"""
    global _FEEDBACK_TABLE_ENSURED
    if _FEEDBACK_TABLE_ENSURED:
        return
    try:
        async with engine.begin() as conn:
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS ai_clip_deletion_feedback (
                    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    clip_id TEXT,
                    action TEXT NOT NULL DEFAULT 'delete',
                    reason TEXT,
                    reason_category TEXT,
                    clip_metadata JSONB DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """))
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_ai_clip_del_fb_job
                ON ai_clip_deletion_feedback (job_id)
            """))
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_ai_clip_del_fb_category
                ON ai_clip_deletion_feedback (reason_category)
            """))
        _FEEDBACK_TABLE_ENSURED = True
        logger.info("[ai-clip] ai_clip_deletion_feedback table ensured")
    except Exception as e:
        logger.warning(f"[ai-clip] Failed to ensure ai_clip_deletion_feedback table: {e}")

# Concurrency limiter
# V14.2: Increased from 2→3 to reduce queue wait times
_AI_CLIP_SEMAPHORE = asyncio.Semaphore(int(os.getenv("AI_CLIP_CONCURRENCY", "3")))

# ─── ASS Subtitle Styles ────────────────────────────────────────────────────
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

# Hook style variants (large text for first 3 seconds) — randomly selected per clip
_HOOK_STYLES = [
    {  # Yellow-Orange pop
        'fontsize': 120, 'bold': 1, 'primary_color': '&H0035E1FF',
        'outline_color': '&H00356BFF', 'outline': 10, 'shadow': 5,
        'border_style': 1, 'back_color': '&H70000000',
    },
    {  # White with strong black outline
        'fontsize': 115, 'bold': 1, 'primary_color': '&H00FFFFFF',
        'outline_color': '&H00000000', 'outline': 12, 'shadow': 4,
        'border_style': 1, 'back_color': '&H60000000',
    },
    {  # Neon green
        'fontsize': 118, 'bold': 1, 'primary_color': '&H0000FF88',
        'outline_color': '&H00003300', 'outline': 10, 'shadow': 5,
        'border_style': 1, 'back_color': '&H60000000',
    },
    {  # Hot pink
        'fontsize': 120, 'bold': 1, 'primary_color': '&H008080FF',
        'outline_color': '&H00000066', 'outline': 10, 'shadow': 4,
        'border_style': 1, 'back_color': '&H60000000',
    },
    {  # Cyan/aqua
        'fontsize': 116, 'bold': 1, 'primary_color': '&H00FFFF00',
        'outline_color': '&H00663300', 'outline': 10, 'shadow': 5,
        'border_style': 1, 'back_color': '&H60000000',
    },
    {  # Red with white outline (high contrast)
        'fontsize': 122, 'bold': 1, 'primary_color': '&H000000FF',
        'outline_color': '&H00FFFFFF', 'outline': 11, 'shadow': 3,
        'border_style': 1, 'back_color': '&H60000000',
    },
    {  # Gold with dark outline
        'fontsize': 118, 'bold': 1, 'primary_color': '&H0000D4FF',
        'outline_color': '&H00003366', 'outline': 10, 'shadow': 5,
        'border_style': 1, 'back_color': '&H60000000',
    },
    {  # White boxed (clean style)
        'fontsize': 110, 'bold': 1, 'primary_color': '&H00FFFFFF',
        'outline_color': '&H00000000', 'outline': 22, 'shadow': 0,
        'border_style': 3, 'back_color': '&H80000000',
    },
]

def _pick_hook_style() -> dict:
    """ランダムにフックスタイルを選択"""
    return random.choice(_HOOK_STYLES)

# CTA style (end of video)
_CTA_STYLE = {
    'fontsize': 100, 'bold': 1, 'primary_color': '&H0000FF00',  # Green
    'outline_color': '&H00000000', 'outline': 8, 'shadow': 4,
    'border_style': 1, 'back_color': '&H70000000',
}

# Keyword highlight color (ASS color format: &HBBGGRR)
_HIGHLIGHT_COLOR = '&H0000FFFF'  # Bright yellow
_HIGHLIGHT_CTA_COLOR = '&H0000FF00'  # Green for CTA words

# Scene-based style mapping
_SCENE_STYLE_MAP = {
    'intro': 'box',  # Was 'pop' - changed because pop's colored text looked blurry
    'product': 'box',
    'demo': 'simple',
    'cta': 'gradient',
    'closing': 'outline',
    'default': 'box',
}

# Font search paths
# Extracted individual OTF files are preferred over TTC for libass compatibility.
# libass 0.15.0 on Azure App Service cannot reliably read CJK glyphs from TTC.
_EXTRACTED_FONTS_DIR = "/tmp/aitherhub_fonts"
_FONT_SEARCH_PATHS = [
    f'{_EXTRACTED_FONTS_DIR}/NotoSansCJK-JP-Bold.otf',     # Extracted from TTC (preferred)
    f'{_EXTRACTED_FONTS_DIR}/NotoSansCJK-JP-Regular.otf',  # Extracted from TTC (preferred)
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc',
    '/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc',
    '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc',
]

# CTA keywords for highlight detection
_CTA_KEYWORDS = [
    '買', '購入', 'リンク', 'url', 'クーポン', '割引', '限定', '今すぐ',
    '下單', '購買', '連結', '優惠', '折扣', 'セール', '特別', '無料',
    'プレゼント', 'お得', '半額', 'キャンペーン', '期間限定',
    '送料無料', '初回限定', '定期便', 'ポイント', '返金保証',
    '即購入', '今だけ', '数量限定', '先着', 'ラスト',
    # 中国語追加
    '免费', '秒杀', '抢购', '包邮', '专属',
]
# Product/emphasis keywords
_EMPHASIS_KEYWORDS = [
    '商品', '製品', '成分', '効果', '使い方', 'すごい', 'やばい',
    '最高', 'おすすめ', '人気', '話題', 'プロ', '美容師', 'サロン',
    'KYOGOKU', '京極', 'ケラチン', 'コラーゲン', 'アミノ酸',
    'ヒアルロン酸', 'トリートメント', 'ダメージケア',
    'ツヤツヤ', 'サラサラ', 'ふわふわ', 'しっとり',
    'ビフォーアフター', '変わる', '感動', '神',
    'リピート', 'リピ', 'リアル', 'ガチ', '毎日', '継続',
    # 中国語追加
    '好用', '推荐', '回购', '效果', '成分', '天然',
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


# ─── Job Management (DB-persistent) ──────────────────────────────────────────

async def _save_job(job_id: str, data: dict):
    """Save job to file + DB (async, awaits DB save)"""
    # Also save to file as fallback
    try:
        path = os.path.join(_AI_CLIP_JOB_DIR, f"{job_id}.json")
        with open(path, "w") as f:
            json.dump(data, f, default=str)
    except Exception:
        pass
    # Save to DB directly (await ensures completion)
    await _save_job_db(job_id, data)


async def _save_job_db(job_id: str, data: dict):
    """Save job to DB (async) - uses engine.begin() for guaranteed commit"""
    try:
        await _ensure_jobs_table()
        # Parse timestamps - asyncpg requires actual datetime objects, not strings
        def _parse_ts(val):
            if isinstance(val, datetime):
                return val
            if isinstance(val, str):
                try:
                    return datetime.fromisoformat(val)
                except (ValueError, TypeError):
                    pass
            return datetime.now(timezone.utc)

        params = {
            "job_id": data.get("job_id", job_id),
            "status": data.get("status", "queued"),
            "progress_pct": max(0, min(100, int(data.get("progress_pct", 0)))),
            "current_step": data.get("current_step"),
            "clips_completed": int(data.get("clips_completed", 0)),
            "clips_total": int(data.get("clips_total", 0)),
            "results": json.dumps(data.get("results", []), default=str),
            "error": data.get("error"),
            "config": json.dumps(data.get("config", {}), default=str),
            "source_clip": json.dumps(data.get("source_clip"), default=str) if data.get("source_clip") else None,
            "created_at": _parse_ts(data.get("created_at")),
            "updated_at": _parse_ts(data.get("updated_at")),
        }
        async with engine.begin() as conn:
            await conn.execute(text("""
                INSERT INTO ai_clip_jobs (job_id, status, progress_pct, current_step,
                    clips_completed, clips_total, results, error, config, source_clip, created_at, updated_at)
                VALUES (:job_id, :status, :progress_pct, :current_step,
                    :clips_completed, :clips_total, CAST(:results AS jsonb), :error, CAST(:config AS jsonb), CAST(:source_clip AS jsonb),
                    :created_at, :updated_at)
                ON CONFLICT (job_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    progress_pct = EXCLUDED.progress_pct,
                    current_step = EXCLUDED.current_step,
                    clips_completed = EXCLUDED.clips_completed,
                    clips_total = EXCLUDED.clips_total,
                    results = EXCLUDED.results,
                    error = EXCLUDED.error,
                    config = EXCLUDED.config,
                    source_clip = EXCLUDED.source_clip,
                    updated_at = EXCLUDED.updated_at
            """), params)
        logger.info(f"[ai-clip] DB save OK for {job_id} (status={params['status']})")
    except Exception as e:
        import traceback
        logger.error(f"[ai-clip] DB save failed for {job_id}: {e}\n{traceback.format_exc()}")
        # Store last error for diagnostics
        global _LAST_DB_SAVE_ERROR
        _LAST_DB_SAVE_ERROR = f"{type(e).__name__}: {str(e)[:300]}"


def _load_job(job_id: str) -> dict | None:
    """Load job - try file first (for in-progress jobs), then DB"""
    # Try file first (faster for active jobs)
    path = os.path.join(_AI_CLIP_JOB_DIR, f"{job_id}.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    # Fallback: try DB (sync wrapper)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            return None  # Can't block in async context, caller should use _load_job_db
        return loop.run_until_complete(_load_job_db(job_id))
    except Exception:
        return None


async def _load_job_db(job_id: str) -> dict | None:
    """Load job from DB (async)"""
    try:
        await _ensure_jobs_table()
        async with engine.connect() as conn:
            result = await conn.execute(text("""
                SELECT job_id, status, progress_pct, current_step,
                       clips_completed, clips_total, results, error,
                       config, source_clip, created_at, updated_at
                FROM ai_clip_jobs WHERE job_id = :job_id
            """), {"job_id": job_id})
            row = result.fetchone()
            if not row:
                return None
            return {
                "job_id": row.job_id,
                "status": row.status,
                "progress_pct": row.progress_pct,
                "current_step": row.current_step,
                "clips_completed": row.clips_completed,
                "clips_total": row.clips_total,
                "results": row.results if isinstance(row.results, list) else json.loads(row.results or "[]"),
                "error": row.error,
                "config": row.config if isinstance(row.config, dict) else json.loads(row.config or "{}"),
                "source_clip": row.source_clip if isinstance(row.source_clip, (dict, type(None))) else json.loads(row.source_clip or "null"),
                "created_at": str(row.created_at) if row.created_at else None,
                "updated_at": str(row.updated_at) if row.updated_at else None,
            }
    except Exception as e:
        logger.warning(f"[ai-clip] DB load failed for {job_id}: {e}")
        return None


async def _update_job(job_id: str, **kwargs):
    """Update job - updates both file and DB (async, awaits DB save)"""
    if "progress_pct" in kwargs:
        try:
            kwargs["progress_pct"] = max(0, min(100, int(kwargs["progress_pct"])))
        except (TypeError, ValueError):
            kwargs["progress_pct"] = 0
    # Update file (for fast reads during processing)
    data = None
    path = os.path.join(_AI_CLIP_JOB_DIR, f"{job_id}.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception:
            pass
    if data is None:
        data = {"job_id": job_id}
    data.update(kwargs)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        with open(path, "w") as f:
            json.dump(data, f, default=str)
    except Exception:
        pass
    # Save to DB directly (await ensures completion)
    await _save_job_db(job_id, data)


async def _list_jobs_db(limit: int = 50) -> list:
    """List jobs from DB (async)"""
    try:
        await _ensure_jobs_table()
        async with engine.connect() as conn:
            result = await conn.execute(text("""
                SELECT job_id, status, progress_pct, current_step,
                       clips_completed, clips_total, results, error,
                       config, source_clip, created_at, updated_at
                FROM ai_clip_jobs
                ORDER BY created_at DESC
                LIMIT :limit
            """), {"limit": limit})
            rows = result.fetchall()
            jobs = []
            for row in rows:
                jobs.append({
                    "job_id": row.job_id,
                    "status": row.status,
                    "progress_pct": row.progress_pct,
                    "current_step": row.current_step,
                    "clips_completed": row.clips_completed,
                    "clips_total": row.clips_total,
                    "results": row.results if isinstance(row.results, list) else json.loads(row.results or "[]"),
                    "error": row.error,
                    "config": row.config if isinstance(row.config, dict) else json.loads(row.config or "{}"),
                    "source_clip": row.source_clip if isinstance(row.source_clip, (dict, type(None))) else json.loads(row.source_clip or "null"),
                    "created_at": str(row.created_at) if row.created_at else None,
                    "updated_at": str(row.updated_at) if row.updated_at else None,
                })
            return jobs
    except Exception as e:
        logger.warning(f"[ai-clip] DB list jobs failed: {e}")
        return []


def _list_jobs(limit: int = 50) -> list:
    """List jobs from file (sync fallback)"""
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


def _detect_cjk_font_name() -> str:
    """fontconfigを使って実際に利用可能なCJKフォント名を検出する。
    ASSファイルのFontname欄にはfontconfigが認識するフォント名を使う必要がある。
    """
    try:
        result = subprocess.run(
            ['fc-list', ':lang=ja', 'family'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            families = result.stdout.strip().split('\n')
            # Prefer Noto Sans CJK JP
            for fam in families:
                names = [n.strip() for n in fam.split(',')]
                for name in names:
                    if 'Noto Sans CJK JP' in name:
                        logger.info(f"[ai-clip] Detected CJK font via fc-list: {name}")
                        return name
            # Fallback: any Noto Sans CJK
            for fam in families:
                names = [n.strip() for n in fam.split(',')]
                for name in names:
                    if 'Noto Sans CJK' in name:
                        logger.info(f"[ai-clip] Detected CJK font via fc-list: {name}")
                        return name
            # Fallback: any Japanese font
            for fam in families:
                first_name = fam.split(',')[0].strip()
                if first_name:
                    logger.info(f"[ai-clip] Using first Japanese font: {first_name}")
                    return first_name
    except Exception as e:
        logger.warning(f"[ai-clip] fc-list failed: {e}")

    # Last resort: use the font file path directly in the ASS
    # Some libass versions accept file paths as font names
    font_path = _find_cjk_font()
    logger.warning(f"[ai-clip] fc-list unavailable, using font path: {font_path}")
    return 'Noto Sans CJK JP'


def _seconds_to_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

# Emoji regex pattern - matches most Unicode emoji ranges
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002702-\U000027B0"  # dingbats
    "\U000024C2-\U000024FF"  # enclosed alphanumerics (safe: stops before CJK)
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA00-\U0001FA6F"  # chess symbols
    "\U0001FA70-\U0001FAFF"  # symbols extended-A
    "\U0001F000-\U0001F02F"  # mahjong tiles
    "\U0001F0A0-\U0001F0FF"  # playing cards
    "\U0001F100-\U0001F1FF"  # enclosed alphanumeric supplement
    "\U0001F200-\U0001F251"  # enclosed ideographic supplement
    "\U00002600-\U000026FF"  # misc symbols
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0000200D"             # zero width joiner
    "\U00002B50-\U00002B55"  # stars
    "\U0000231A-\U0000231B"  # watch/hourglass
    "\U000023E9-\U000023F3"  # media controls
    "\U000023F8-\U000023FA"  # more media
    "\U000025AA-\U000025AB"  # squares
    "\U000025B6"             # play button
    "\U000025C0"             # reverse button
    "\U000025FB-\U000025FE"  # squares
    "\U00002934-\U00002935"  # arrows
    "\U00002B05-\U00002B07"  # arrows
    "\U00003030"             # wavy dash
    "\U0000303D"             # part alternation mark
    "\U00003297"             # circled ideograph congratulate
    "\U00003299"             # circled ideograph secret
    "]+", flags=re.UNICODE
)

def _strip_emoji(text: str) -> str:
    """ASS字幕用にテキストから絵文字を除去する（フォントが対応していないため）"""
    return _EMOJI_PATTERN.sub("", text).strip()


def _classify_scene(text_content: str, time_start: float, total_duration: float) -> str:
    position_ratio = time_start / max(total_duration, 1)
    if position_ratio < 0.1:
        return 'intro'
    if position_ratio > 0.9:
        return 'closing'
    text_lower = text_content.lower()
    if any(kw in text_lower for kw in _CTA_KEYWORDS):
        return 'cta'
    product_keywords = ['商品', '製品', 'プロダクト', '成分', '効果', '使い方',
                       '產品', '成份', '效果', '用法']
    if any(kw in text_lower for kw in product_keywords):
        return 'product'
    demo_keywords = ['見て', '実際', 'こんな感じ', 'デモ', '使って', '看', '實際', '示範']
    if any(kw in text_lower for kw in demo_keywords):
        return 'demo'
    return 'default'


# ─── V2: Audio Analysis (volume peaks for zoom/shake) ────────────────────────
def _detect_volume_peaks(video_path: str, threshold_db: float = -15.0) -> list:
    """ffmpegのvolumedetectで音量ピークを検出する。
    Returns list of (time_sec, volume_db) tuples for peaks above threshold.
    """
    try:
        # Use astats to get per-frame RMS levels
        cmd = [
            "ffmpeg", "-i", video_path, "-af",
            "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
            "-f", "null", "-"
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            return []

        peaks = []
        current_time = 0.0
        frame_duration = 1.0 / 25.0  # Approximate

        for line in proc.stderr.split('\n'):
            if 'time=' in line:
                # Parse time from ffmpeg progress
                try:
                    time_str = line.split('time=')[1].split(' ')[0]
                    parts = time_str.split(':')
                    if len(parts) == 3:
                        current_time = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
                except Exception:
                    pass

        # Simpler approach: use silencedetect to find NON-silent (loud) moments
        cmd2 = [
            "ffmpeg", "-i", video_path, "-af",
            f"silencedetect=noise={threshold_db}dB:d=0.3",
            "-f", "null", "-"
        ]
        proc2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=60)

        # Parse silence periods to find loud periods
        silence_starts = []
        silence_ends = []
        for line in proc2.stderr.split('\n'):
            if 'silence_start:' in line:
                try:
                    t = float(line.split('silence_start:')[1].strip().split(' ')[0])
                    silence_starts.append(t)
                except Exception:
                    pass
            elif 'silence_end:' in line:
                try:
                    t = float(line.split('silence_end:')[1].strip().split(' ')[0].split('|')[0])
                    silence_ends.append(t)
                except Exception:
                    pass

        # The transitions from silence to non-silence are "peaks" (sudden volume increase)
        for end_t in silence_ends:
            peaks.append(end_t)

        logger.info(f"[ai-clip] Detected {len(peaks)} volume peaks, {len(silence_starts)} silence periods")
        return peaks

    except Exception as e:
        logger.warning(f"[ai-clip] Volume peak detection failed: {e}")
        return []


def _detect_silence_periods(video_path: str, noise_db: float = -30.0,
                             min_duration: float = 0.5) -> list:
    """無音区間を検出する。Returns list of (start, end) tuples."""
    try:
        cmd = [
            "ffmpeg", "-i", video_path, "-af",
            f"silencedetect=noise={noise_db}dB:d={min_duration}",
            "-f", "null", "-"
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

        silence_periods = []
        current_start = None

        for line in proc.stderr.split('\n'):
            if 'silence_start:' in line:
                try:
                    current_start = float(line.split('silence_start:')[1].strip().split(' ')[0])
                except Exception:
                    pass
            elif 'silence_end:' in line and current_start is not None:
                try:
                    end_t = float(line.split('silence_end:')[1].strip().split(' ')[0].split('|')[0])
                    silence_periods.append((current_start, end_t))
                    current_start = None
                except Exception:
                    pass

        logger.info(f"[ai-clip] Detected {len(silence_periods)} silence periods")
        return silence_periods

    except Exception as e:
        logger.warning(f"[ai-clip] Silence detection failed: {e}")
        return []



# ─── V2.25: Scene Change Detection (ジャンプカット検出) ───────────────────────────────────────
async def _detect_scene_changes(video_path: str, duration: float,
                                 threshold: float = 0.3) -> list:
    """ffmpegのscene detectionを使ってジャンプカットを検出する。
    Returns: list of timestamps (float) where scene changes occur.
    フィルタ: 最初0.5秒と最後1秒は除外（フック/CTAの領域）
    """
    try:
        cmd = [
            "ffmpeg", "-i", video_path,
            "-vf", f"select='gt(scene,{threshold})',showinfo",
            "-vsync", "vfr",
            "-f", "null", "-"
        ]
        proc = await asyncio.subprocess.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=30)
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        # Parse showinfo output for pts_time
        import re
        scene_times = []
        for line in stderr_text.split("\n"):
            if "pts_time:" in line:
                match = re.search(r"pts_time:([\d.]+)", line)
                if match:
                    t = float(match.group(1))
                    # 最初0.5秒と最後1秒は除外
                    if 0.5 < t < duration - 1.0:
                        scene_times.append(t)
        # 近接するシーンチェンジをマージ（0.5秒以内）
        filtered = []
        for t in scene_times:
            if not filtered or t - filtered[-1] > 0.5:
                filtered.append(t)
        logger.info(f"[ai-clip] V2.25: Scene detection found {len(filtered)} jump cuts (threshold={threshold})")
        return filtered
    except asyncio.TimeoutError:
        logger.warning("[ai-clip] V2.25: Scene detection timed out")
        return []
    except Exception as e:
        logger.warning(f"[ai-clip] V2.25: Scene detection failed: {e}")
        return []

# ─── V11: Content Relevance Analysis (GPT) ───────────────────────────────────────────────

async def _analyze_content_relevance(captions: list, product_name: str, duration: float, editing_profile_params: dict = None) -> list:
    """字幕セグメントをGPTで分析し、商品・セールスと無関係な区間を検出する。
    editing_profile_params: 編集スタイル学習から得られたルール（content_editing_rules等）
    Returns: list of (start, end) tuples for irrelevant segments to cut.
    """
    import openai

    if not captions or len(captions) < 3:
        return []

    azure_key = os.getenv("AZURE_OPENAI_KEY", "")
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    azure_model = os.getenv("GPT5_MODEL") or os.getenv("GPT5_DEPLOYMENT") or "gpt-4.1-mini"

    if not azure_key or not azure_endpoint:
        # GPTが使えない場合はフィラーワード検出のみ
        return _detect_filler_segments_local(captions)

    from urllib.parse import urlparse as _urlparse
    _parsed = _urlparse(azure_endpoint)
    clean_endpoint = f"{_parsed.scheme}://{_parsed.netloc}/"

    client = openai.AsyncAzureOpenAI(
        api_key=azure_key,
        azure_endpoint=clean_endpoint,
        api_version=os.getenv("GPT5_API_VERSION", "2025-04-01-preview"),
    )

    # 字幕をタイムスタンプ付きでフォーマット
    caption_lines = []
    for i, cap in enumerate(captions):
        s = float(cap.get("start", 0))
        e = float(cap.get("end", 0))
        text = (cap.get("text") or "").strip()
        if text:
            caption_lines.append(f"[{s:.1f}-{e:.1f}] {text}")

    if not caption_lines:
        return []

    captions_text = "\n".join(caption_lines[:80])  # V13: 50→80セグメントに拡大（より多くのコンテキストをGPTに渡す）

    prompt = f"""以下はライブコマース動画の字幕データです。各行は[開始秒-終了秒] テキストの形式です。

商品名: {product_name or '（不明）'}

{captions_text}

【タスク】
この動画をTikTok短動画（帯貨/商品紹介）用に編集します。
成片の80%以上が商品に関連する有効な帯貨内容でなければなりません。
以下に該当する区間を積極的にカットしてください（優先度順）：

★★★ 1. 繰り返し・重複内容の検出（最重要！必ず最初に確認）★★★
   ライブ配信では主播が同じセールストークを何度も繰り返します。
   以下のルールで重複を検出し、カットしてください：
   - 同じ意味・同じフレーズを2回以上言っている場合、最も明確で流暢な1回だけを残す
   - 完全一致でなくても「意味的に同じ」ならカット対象（例：「ビが変わる」と「ビーが変わるんですよ」は同じ）
   - 同じCTA（行動喚起）の繰り返し（例：「左下からゲット」を3回言う→1回だけ残す）
   - 同じ商品特徴の説明を繰り返す場合も、最も詳しい1回だけ残す
   - 判定基準：文の核心部分（主語+述語+目的語）が同じなら「繰り返し」
   - 注意：異なる商品の説明は繰り返しではない。同一商品の同一ポイントの繰り返しのみカット
   ※まず全体を読み、同じ意味の発言が複数回ないか確認してからカット区間を決定すること

2. 弾幕互動・コメント読み上げ：
   - 「ありがとうXXさん」「XXさんいらっしゃい」「XXさんこんにちは」
   - 「谢谢XX」「欢迎XX」「XX来了」「感谢XX的关注」
   - 視聴者の名前を呼ぶ全ての区間
   - 「コメントありがとう」「みんなありがとう」等の視聴者への感謝
   - 視聴者への謝罪・返答（「XXさんごめん」「XXさんすみません」「XXさんお待たせ」等）
   - 弾幕の質問に答える区間（商品に関する質問への回答は除く）

3. 閑聊・雑談（商品と無関係な話題）：
   - 天気、日常、個人的な話、他の配信者の話
   - 「今日は暑いね」「疲れた」等の独り言
   - 配信環境の説明（「音聞こえる？」「画面見える？」等）

4. フィラーワード・言い淀み：
   - 「えーっと」「あの」「なんか」「えー」「うーん」
   - 「那个」「就是」「然后」「嗯」「这个」
   - 「um」「uh」「like」「you know」

5. つなぎ言葉だけの区間（「えーと」「それで」「じゃあ」「那我们」）

6. 無音・沈黙の区間（0.5秒以上）—— 特に発話終了後の無音は积極的にカット（余韻として0.8秒だけ残す）

7. 配信プラットフォーム違反リスクのある内容：
   - 他プラットフォームへの誘導
   - 過度な煽り表現

【絶対にカットしてはいけない内容】
- 商品の説明・特徴・成分・効果（ただし同一ポイントの繰り返しは1回だけ残す）
- 価格・割引・セール情報（ただし同一情報の繰り返しは1回だけ残す）
- 使用方法・使用感のレビュー
- 商品の比較・推薦理由
- 購入を促すセールストーク（ただし同一CTAの繰り返しは1回だけ残す）

【ルール】
- ★繰り返し検出を最優先で行う★ — まず全体を読み、同じ意味の発言が複数回ないか確認する
- 帯貨内容（商品関連）を最優先で残すが、同じ内容の繰り返しは1回だけ
- 弾幕互動は商品に関する質問への回答以外、全てカット
- 目標: 成片の80%以上が有効な帯貨内容になるようカット（重複なし）
- 積極的にカットする（TikTokはテンポが命、同じ話を2回聞かせない）

【出力形式】
JSON配列で出力。カットすべき区間がない場合は空配列[]。
例: [[1.2, 3.5], [8.0, 10.2]]
※数値のみ、説明不要"""

    # ── Inject editing profile rules if available ──
    if editing_profile_params:
        extra_rules = []
        # Content editing rules from pair analysis
        if editing_profile_params.get('content_editing_rules'):
            rules = editing_profile_params['content_editing_rules']
            if isinstance(rules, list):
                rules_text = '\n'.join(f'   - {r}' for r in rules)
            else:
                rules_text = f'   - {rules}'
            extra_rules.append(f"\n【編集スタイル学習からの追加ルール】\n{rules_text}")
        # Repetition handling rules
        if editing_profile_params.get('repetition_handling'):
            rep_rule = editing_profile_params['repetition_handling']
            extra_rules.append(f"\n【繰り返し処理ルール（学習済み）】\n   {rep_rule}")
        if editing_profile_params.get('max_repetition_allowed') is not None:
            max_rep = editing_profile_params['max_repetition_allowed']
            extra_rules.append(f"   → 同一フレーズの最大許容回数: {max_rep}回")
        if editing_profile_params.get('repetition_keep_strategy'):
            strategy = editing_profile_params['repetition_keep_strategy']
            extra_rules.append(f"   → 保持戦略: {strategy}")
        # Cut/keep content types
        if editing_profile_params.get('cut_content_types'):
            cut_types = editing_profile_params['cut_content_types']
            if isinstance(cut_types, list):
                cut_text = ', '.join(cut_types)
            else:
                cut_text = str(cut_types)
            extra_rules.append(f"\n【学習済み: カットすべきコンテンツタイプ】\n   {cut_text}")
        if editing_profile_params.get('keep_content_types'):
            keep_types = editing_profile_params['keep_content_types']
            if isinstance(keep_types, list):
                keep_text = ', '.join(keep_types)
            else:
                keep_text = str(keep_types)
            extra_rules.append(f"\n【学習済み: 絶対に残すべきコンテンツタイプ】\n   {keep_text}")
        if extra_rules:
            prompt += '\n'.join(extra_rules)
            logger.info(f"[ai-clip] Editing profile rules injected into content analysis prompt")

    try:
        response = await client.chat.completions.create(
            model=azure_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000,
            temperature=0.1,
        )

        result_text = response.choices[0].message.content.strip()
        # JSONパース
        # ```json ... ``` を除去
        if result_text.startswith("```"):
            result_text = result_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        segments = json.loads(result_text)
        cut_segments = []
        for seg in segments:
            if isinstance(seg, (list, tuple)) and len(seg) == 2:
                s, e = float(seg[0]), float(seg[1])
                if 0 <= s < e <= duration and (e - s) >= 0.5:
                    cut_segments.append((s, e))

        logger.info(f"[ai-clip] GPT content analysis: {len(cut_segments)} irrelevant segments found")
        return cut_segments

    except Exception as e:
        logger.warning(f"[ai-clip] GPT content relevance analysis failed: {e}")
        # フォールバック: ローカルフィラー検出のみ
        return _detect_filler_segments_local(captions)


def _detect_filler_segments_local(captions: list) -> list:
    """ローカルフィラーワード検出 + 弾幕互動検出（GPT不要）。
    字幕テキストがフィラーワードのみまたは弾幕互動パターンに合致する区間を返す。
    """
    import re as _re
    _FILLER_WORDS = {
        "えー", "えっと", "あの", "あのー", "うーん", "うん", "まあ", "その",
        "えーっと", "ええと", "ねー", "なんか", "ちょっと", "えー",
        "那个", "就是", "然后", "就是说", "嘛", "嗯", "这个",
        "um", "uh", "er", "ah", "like", "you know", "so",
    }
    # 弾幕互動パターン（視聴者への挨拶・感謝・コメント読み上げ）
    # V15: パターン大幅強化 - 視聴者名呼び・挨拶返し・弾幕反応を網羅
    _DANMAKU_PATTERNS = [
        _re.compile(r'ありがとう.*さん'),  # ありがとうXXさん
        _re.compile(r'.*さんいらっしゃい'),  # XXさんいらっしゃい
        _re.compile(r'.*さんこんにちは'),  # XXさんこんにちは
        _re.compile(r'.*さんこんばんは'),  # XXさんこんばんは
        _re.compile(r'こんにちは.*さん'),  # こんにちはXXさん
        _re.compile(r'ありがとうございます'),
        _re.compile(r'みんなありがとう'),
        _re.compile(r'コメントありがとう'),
        # V15追加: 視聴者への挨拶返し・弾幕反応パターン
        _re.compile(r'.*ちゃん.*おかえり'),  # XXちゃんおかえり
        _re.compile(r'おかえり.*'),  # おかえりXX
        _re.compile(r'.*おかえりなさい'),  # XXおかえりなさい
        _re.compile(r'やっほー'),  # やっほー（視聴者への挨拶）
        _re.compile(r'お疲れ様'),  # お疲れ様（視聴者への挨拶）
        _re.compile(r'おつかれ'),  # おつかれ
        _re.compile(r'.*さんお疲れ'),  # XXさんお疲れ
        _re.compile(r'.*さんやっほー'),  # XXさんやっほー
        _re.compile(r'.*さんおかえり'),  # XXさんおかえり
        _re.compile(r'.*さんありがとう'),  # XXさんありがとう
        _re.compile(r'.*さん来てくれて'),  # XXさん来てくれて
        _re.compile(r'わかった.*やっほー'),  # わかったーやっほー
        _re.compile(r'.*ちゃんこんにちは'),  # XXちゃんこんにちは
        _re.compile(r'.*ちゃんこんばんは'),  # XXちゃんこんばんは
        _re.compile(r'.*ちゃんありがとう'),  # XXちゃんありがとう
        _re.compile(r'初見.*さん'),  # 初見さん
        _re.compile(r'初めまして'),  # 初めまして（視聴者への挨拶）
        _re.compile(r'いらっしゃいませ'),  # いらっしゃいませ
        _re.compile(r'来てくれてありがとう'),  # 来てくれてありがとう
        # 中国語パターン
        _re.compile(r'谢谢.*的'),  # 谢谢XX的关注
        _re.compile(r'欢迎.*来'),  # 欢迎XX来
        _re.compile(r'感谢.*关注'),  # 感谢XX的关注
        _re.compile(r'谢谢.*来了'),  # 谢谢XX来了
        _re.compile(r'欢迎来到'),  # 欢迎来到直播间
        _re.compile(r'大家好'),  # 大家好
        _re.compile(r'谢谢大家'),  # 谢谢大家
        _re.compile(r'.*宝贝.*来了'),  # XX宝贝来了
        _re.compile(r'欢迎新来的'),  # 欢迎新来的朋友
        _re.compile(r'谢谢.*点赞'),  # 谢谢XX点赞
        _re.compile(r'谢谢.*礼物'),  # 谢谢XX的礼物
        _re.compile(r'感谢.*送的'),  # 感谢XX送的
        # V2.31: 追加 — 視聴者への返答・謝罪・呼びかけ
        _re.compile(r'.*さんごめん'),  # XXさんごめん
        _re.compile(r'.*さんすみません'),  # XXさんすみません
        _re.compile(r'.*さんお待たせ'),  # XXさんお待たせ
        _re.compile(r'.*さんそうだね'),  # XXさんそうだね
        _re.compile(r'.*さんそうなんです'),  # XXさんそうなんです
        _re.compile(r'.*さんね'),  # XXさんね（呼びかけ）
        _re.compile(r'.*ちゃんごめん'),  # XXちゃんごめん
        _re.compile(r'.*ちゃんすみません'),  # XXちゃんすみません
        _re.compile(r'.*ちゃんお待たせ'),  # XXちゃんお待たせ
        _re.compile(r'ごめんね.*さん'),  # ごめんねXXさん
        _re.compile(r'.*先生.*ありがとう'),  # XX先生ありがとう
        _re.compile(r'.*先生.*こんにちは'),  # XX先生こんにちは
        _re.compile(r'.*先生.*こんばんは'),  # XX先生こんばんは
    ]

    filler_segments = []
    for cap in (captions or []):
        s = float(cap.get("start", 0))
        e = float(cap.get("end", 0))
        text = (cap.get("text") or "").strip().lower()
        if e <= s:
            continue
        # フィラーワード検出
        if text in _FILLER_WORDS or len(text) <= 2:
            filler_segments.append((s, e))
            continue
        # 弾幕互動パターン検出
        for pattern in _DANMAKU_PATTERNS:
            if pattern.search(text):
                filler_segments.append((s, e))
                break

    # 連続するフィラーをマージ
    if not filler_segments:
        return []

    merged = [filler_segments[0]]
    for s, e in filler_segments[1:]:
        if s <= merged[-1][1] + 0.3:  # 0.3秒以内のギャップはマージ
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))

    return merged


# ─── V2: Smart Silence Trimming ────────────────────────────────────────────────────────────────────────────────
def _build_silence_trim_segments(duration: float, silence_periods: list,
                                  captions: list, keep_margin: float = 0.12,
                                  filler_cut_segments: list = None) -> list:
    """V2.19改善版: 無音区間 + フィラー区間 + 商品無関係区間をカット。
    - 無音区間: 1.0秒以上は積極的にカット（0.2秒の自然なポーズ残す）
    - 0.5〜1.0秒の無音は部分カット（50%除去）
    - フィラー区間（「えーっと」「あの」等）: 字幕があってもカット対象
    - 商品無関係区間（GPT判定）: カット対象
    - V2.19: 末尾無音トリム——最後の発話終了後0.8秒だけ余韻を残して残りをカット
    - 最大カット量は元の動画の70%まで（短動画向けに積極カット）
    Returns list of (start, end) tuples representing segments to KEEP.
    """
    if not silence_periods and not filler_cut_segments:
        # V2.19: 無音リストがなくても、字幕データから冒頭・末尾無音を検出してトリム
        if captions and duration > 0:
            # V15: 冒頭無音トリム —— 最初の字幕開始前の無音をカット
            first_speech_start = duration
            for cap in (captions or []):
                cap_start = float(cap.get("start", 0) if isinstance(cap, dict) else 0)
                if cap_start < first_speech_start and cap_start > 0:
                    first_speech_start = cap_start
            last_speech_end = 0.0
            for cap in (captions or []):
                cap_end = float(cap.get("end", 0) if isinstance(cap, dict) else 0)
                if cap_end > last_speech_end:
                    last_speech_end = cap_end
            trim_start = 0.0
            # 冒頭に1秒以上の無音があればトリム（0.15秒の余裕を残す）
            if first_speech_start > 1.0:
                trim_start = max(0, first_speech_start - 0.15)
                logger.info(f"[ai-clip] V15 leading silence trim: first_speech={first_speech_start:.1f}s, "
                            f"trim_from={trim_start:.1f}s")
            # 最後の発話終了後に2秒以上の無音があればトリム（0.8秒余韻残し）
            trailing_silence = duration - last_speech_end
            trim_end = duration
            if last_speech_end > 0 and trailing_silence > 2.0:
                trim_end = last_speech_end + 0.8  # 余韻0.8秒
                logger.info(f"[ai-clip] V2.19 trailing silence trim: last_speech={last_speech_end:.1f}s, "
                            f"trim_at={trim_end:.1f}s, removed={duration - trim_end:.1f}s")
            if trim_start > 0 or trim_end < duration:
                return [(trim_start, trim_end)]
        return [(0, duration)]

    # Build "protected" intervals from captions (don't cut these)
    # V11: フィラーワードのみの字幕はprotectedから除外
    _FILLER_WORDS = {
        "えー", "えっと", "あの", "あのー", "うーん", "うん", "まあ", "その",
        "えーっと", "ええと", "ねー", "なんか", "ちょっと", "えー",
        "那个", "就是", "然后", "就是说", "嘛", "嗯", "这个",
        "um", "uh", "er", "ah", "like", "you know", "so",
    }

    protected = []
    for cap in (captions or []):
        s = float(cap.get("start", 0))
        e = float(cap.get("end", 0))
        cap_text = (cap.get("text") or "").strip()
        if e <= s:
            continue
        # V11: フィラーワードのみの字幕はprotectedにしない
        cap_text_lower = cap_text.lower().strip()
        is_filler_only = cap_text_lower in _FILLER_WORDS or len(cap_text) <= 2
        if not is_filler_only:
            protected.append((max(0, s - 0.1), min(duration, e + 0.1)))

    # Merge overlapping protected intervals
    protected.sort()
    merged_protected = []
    for s, e in protected:
        if merged_protected and s <= merged_protected[-1][1]:
            merged_protected[-1] = (merged_protected[-1][0], max(merged_protected[-1][1], e))
        else:
            merged_protected.append((s, e))

    def _is_protected(start, end):
        for ps, pe in merged_protected:
            if start < pe and end > ps:
                return True
        return False

    # Determine which silence periods to actually cut
    # V2.27: 気口/停顿を「彻底削除」— 部分カットではなく完全除去し前後を直接拼接
    # 以前の問題: 部分カット（50%〜70%のみ除去）だと無効留白が残り、硬切痕跡が見える
    cuttable = []
    total_cut_time = 0.0
    max_cut_ratio = 0.70  # 最大カット量は元の動画の70%まで
    max_cut_time = duration * max_cut_ratio

    # --- Phase 1: Silence cuts (V2.27: 完全削除方式) ---
    # ソート: 時間順（前から順に処理して正確な切点管理）
    sorted_silences = sorted(silence_periods or [], key=lambda x: x[0])
    
    for s_start, s_end in sorted_silences:
        silence_len = s_end - s_start
        # Don't cut first 0.15s or last 0.15s of video
        if s_start < 0.15 or s_end > duration - 0.15:
            continue
        # Don't cut if overlaps with meaningful caption
        if _is_protected(s_start, s_end):
            continue
        # V2.27: 0.3秒以上の無音は全て完全削除対象（0.2s未満は自然な間として残す）
        if silence_len < 0.3:
            continue
        # Check max cut limit
        if total_cut_time >= max_cut_time:
            break

        # V2.27: 完全削除 — 前後に極小マージン(0.05s)だけ残して全区間カット
        # これにより「無効留白」が一切残らず、前後の有効素材が直接拼接される
        margin = 0.05  # 音声の自然な繋がりのための極小マージン
        cut_start = s_start + margin
        cut_end = s_end - margin

        if cut_end > cut_start:
            actual_cut = cut_end - cut_start
            if total_cut_time + actual_cut > max_cut_time:
                cut_end = cut_start + (max_cut_time - total_cut_time)
                if cut_end <= cut_start:
                    break
            cuttable.append((cut_start, cut_end))
            total_cut_time += (cut_end - cut_start)

    # --- Phase 2: Filler/irrelevant content cuts (from GPT analysis) ---
    # V2.30: 切点をsilence境界に対齐 — GPTが返す字幕ベースの切点は動作の途中に
    # 当たることがあり、jump cutが発生する。silence（無音）の位置は動作の自然な
    # 停止点と一致するため、切点をsilence境界に微調整する。
    silence_boundaries = set()
    for s_s, s_e in sorted(silence_periods or [], key=lambda x: x[0]):
        silence_boundaries.add(('start', s_s))
        silence_boundaries.add(('end', s_e))
    silence_starts = sorted([t for _, t in silence_boundaries if _ == 'start'])
    silence_ends = sorted([t for _, t in silence_boundaries if _ == 'end'])
    
    def _snap_to_silence(t, search_range=0.5):
        """V2.30: 指定時刻を最寄りのsilence境界に対齐する。
        search_range秒以内にsilence境界があればそこにスナップ。
        なければ元の時刻を返す。"""
        best_t = t
        best_dist = search_range
        # silence_startに近い = その時点で音声が止まる直前
        # silence_endに近い = その時点で音声が再開する直後
        # cut_startはsilence_endに合わせる（音声再開地点から残す）
        # cut_endはsilence_startに合わせる（音声停止地点でカット）
        for s_t in silence_starts + silence_ends:
            dist = abs(s_t - t)
            if dist < best_dist:
                best_dist = dist
                best_t = s_t
        return best_t
    
    if filler_cut_segments:
        for seg_start, seg_end in sorted(filler_cut_segments, key=lambda x: x[1]-x[0], reverse=True):
            if total_cut_time >= max_cut_time:
                break
            seg_len = seg_end - seg_start
            if seg_len < 0.3:
                continue
            # Don't cut first/last 0.2s
            if seg_start < 0.2 or seg_end > duration - 0.2:
                continue
            
            # V2.30: 切点をsilence境界にスナップ（動作の自然な停止点に合わせる）
            snapped_start = _snap_to_silence(seg_start, search_range=0.5)
            snapped_end = _snap_to_silence(seg_end, search_range=0.5)
            # スナップ後も有効な区間であることを確認
            if snapped_end > snapped_start + 0.2:
                seg_start = snapped_start
                seg_end = snapped_end
            
            # Content cuts can override caption protection (GPT decided it's irrelevant)
            actual_cut = seg_end - seg_start
            if total_cut_time + actual_cut > max_cut_time:
                seg_end = seg_start + (max_cut_time - total_cut_time)
                actual_cut = seg_end - seg_start
                if actual_cut <= 0.1:
                    break
            cuttable.append((seg_start, seg_end))
            total_cut_time += actual_cut

    if not cuttable:
        return [(0, duration)]

    # Build keep segments (inverse of cuttable, sorted by time)
    cuttable.sort(key=lambda x: x[0])
    # Merge overlapping cuts
    merged_cuts = []
    for cs, ce in cuttable:
        if merged_cuts and cs <= merged_cuts[-1][1]:
            merged_cuts[-1] = (merged_cuts[-1][0], max(merged_cuts[-1][1], ce))
        else:
            merged_cuts.append((cs, ce))
    cuttable = merged_cuts

    keep_segments = []
    prev_end = 0
    for cut_start, cut_end in cuttable:
        if cut_start > prev_end:
            keep_segments.append((prev_end, cut_start))
        prev_end = cut_end
    if prev_end < duration:
        keep_segments.append((prev_end, duration))

    # V2.20: 末尾無音トリム改善 —— keep_segments範囲内の字幕のみを考慮し、
    # 文が完了する位置（句読点で終わる字幕）でカットする。
    # 問題: 以前は全字幕の最後のendを使っていたが、クリップ選定で一部のセグメントが
    # 除外されている場合、実際の最後の発話が途中で切れる可能性があった。
    if captions:
        last_speech_end = 0.0
        last_complete_sentence_end = 0.0  # 文末句読点で終わる最後の字幕
        
        # keep_segmentsの最後のセグメントの範囲を取得
        if keep_segments:
            last_seg_start, last_seg_end = keep_segments[-1]
            
            import re as _re_trim
            for cap in (captions or []):
                cap_start = float(cap.get("start", 0) if isinstance(cap, dict) else 0)
                cap_end = float(cap.get("end", 0) if isinstance(cap, dict) else 0)
                cap_text = str(cap.get("text", "") if isinstance(cap, dict) else "")
                
                # keep_segmentsの最後のセグメント範囲内の字幕のみ考慮
                if cap_start >= last_seg_start and cap_end <= last_seg_end + 0.5:
                    if cap_end > last_speech_end:
                        last_speech_end = cap_end
                    # 文末句読点（。！？）で終わる字幕を追跡
                    if _re_trim.search(r'[。！？!?]\s*$', cap_text):
                        if cap_end > last_complete_sentence_end:
                            last_complete_sentence_end = cap_end
            
            # トリムポイントの決定:
            # 1. 文が完了する位置があればそこを使う
            # 2. なければ最後の発話終了時刻を使う
            effective_end = last_speech_end
            if last_complete_sentence_end > 0 and last_complete_sentence_end >= last_speech_end - 2.0:
                # 文末句読点が最後の発話の2秒以内なら、そこでカット
                effective_end = last_complete_sentence_end
            
            trailing_margin = 1.0  # 余韻秒数（少し増やして自然な終わりに）
            trim_point = effective_end + trailing_margin
            if trim_point < last_seg_end and (last_seg_end - trim_point) > 1.0:
                # 末尾セグメントをトリム
                keep_segments[-1] = (last_seg_start, trim_point)
                total_cut_time += (last_seg_end - trim_point)
                logger.info(f"[ai-clip] V2.20 trailing trim: last_speech={last_speech_end:.1f}s, "
                            f"last_sentence_end={last_complete_sentence_end:.1f}s, "
                            f"effective_end={effective_end:.1f}s, "
                            f"trim_at={trim_point:.1f}s, extra_cut={last_seg_end - trim_point:.1f}s")

    # V15: 冒頭無音トリム —— 最初の字幕開始前の無音をカット
    if captions and keep_segments:
        first_speech_start = duration
        for cap in (captions or []):
            cap_start = float(cap.get("start", 0) if isinstance(cap, dict) else 0)
            if cap_start < first_speech_start and cap_start > 0:
                first_speech_start = cap_start
        if first_speech_start > 1.0 and keep_segments[0][0] < first_speech_start - 0.15:
            # 冒頭の無音部分をトリム
            old_start = keep_segments[0][0]
            new_start = max(0, first_speech_start - 0.15)
            if new_start < keep_segments[0][1]:
                keep_segments[0] = (new_start, keep_segments[0][1])
                total_cut_time += (new_start - old_start)
                logger.info(f"[ai-clip] V15 leading trim: first_speech={first_speech_start:.1f}s, "
                            f"new_start={new_start:.1f}s, trimmed={new_start - old_start:.1f}s")

    # V2.27: 高頻度停顿ゾーン検出 — 5秒以内に≥2回かつⅥ1sの停顿がある窓を検出し、
    # その窓全体を舎棄する（頻繁な硬切による画面フラッシュ/ジャンプカットを回避）
    # ロジック: keep_segmentsの中で、短いセグメントが密集しているゾーンを検出し、
    # そのゾーン内のセグメントをまとめて削除する
    DENSE_CUT_WINDOW = 5.0   # 検出窓（秒）
    DENSE_CUT_MIN_COUNT = 2  # 窓内の最小カット数
    DENSE_CUT_MIN_PAUSE = 1.0  # 各カットの最小停顿時長
    
    if len(keep_segments) > 2:
        # 各セグメント間のギャップ（カットされた区間）を取得
        gaps = []
        for i in range(len(keep_segments) - 1):
            gap_start = keep_segments[i][1]
            gap_end = keep_segments[i + 1][0]
            gap_len = gap_end - gap_start
            gaps.append((i, gap_start, gap_end, gap_len))
        
        # スライディングウィンドウで高頻度停顿ゾーンを検出
        dense_zones = []  # (start_seg_idx, end_seg_idx) — 舎棄するセグメント範囲
        i = 0
        while i < len(gaps):
            # このギャップから始まる5秒窓をチェック
            window_start_time = gaps[i][1]  # gap_start
            long_pauses_in_window = []
            j = i
            while j < len(gaps) and gaps[j][1] < window_start_time + DENSE_CUT_WINDOW:
                if gaps[j][3] >= DENSE_CUT_MIN_PAUSE:  # gap_len >= 1.0s
                    long_pauses_in_window.append(j)
                j += 1
            
            if len(long_pauses_in_window) >= DENSE_CUT_MIN_COUNT:
                # 高頻度停顿ゾーン検出！
                # このゾーン内のセグメントを舎棄対象としてマーク
                zone_first_gap = long_pauses_in_window[0]
                zone_last_gap = long_pauses_in_window[-1]
                # 舎棄範囲: 最初の長い停顿の後のセグメントから最後の長い停顿の後のセグメントまで
                discard_start_idx = gaps[zone_first_gap][0] + 1  # 最初の長いgapの後のセグメント
                discard_end_idx = gaps[zone_last_gap][0] + 1    # 最後の長いgapの後のセグメント
                dense_zones.append((discard_start_idx, discard_end_idx))
                logger.info(f"[ai-clip] V2.27 dense-pause zone detected: "
                            f"window={window_start_time:.1f}-{window_start_time+DENSE_CUT_WINDOW:.1f}s, "
                            f"long_pauses={len(long_pauses_in_window)}, "
                            f"discard segments [{discard_start_idx}..{discard_end_idx}]")
                # ゾーンの最後のギャップの次から続行
                i = zone_last_gap + 1
            else:
                i += 1
        
        # 舎棄ゾーン内のセグメントを除外
        if dense_zones:
            discard_indices = set()
            for dz_start, dz_end in dense_zones:
                for idx in range(dz_start, dz_end + 1):
                    discard_indices.add(idx)
            
            filtered_segments = []
            for idx, seg in enumerate(keep_segments):
                if idx not in discard_indices:
                    filtered_segments.append(seg)
                else:
                    logger.info(f"[ai-clip] V2.27 discarding segment {idx}: "
                                f"({seg[0]:.1f}, {seg[1]:.1f}) - in dense-pause zone")
            
            if filtered_segments:
                logger.info(f"[ai-clip] V2.27 dense-pause discard: {len(keep_segments)} -> {len(filtered_segments)} segments "
                            f"(discarded {len(keep_segments) - len(filtered_segments)} segments in dense-pause zones)")
                keep_segments = filtered_segments
    
    # V2.27: 隣接セグメントのマージ（ギャップが極小の場合は結合してジャンプカットを減らす）
    if len(keep_segments) > 1:
        merged_keep = [keep_segments[0]]
        for i in range(1, len(keep_segments)):
            gap = keep_segments[i][0] - merged_keep[-1][1]
            # ギャップが0.15秒未満ならマージ（カットする意味がない）
            if gap < 0.15:
                merged_keep[-1] = (merged_keep[-1][0], keep_segments[i][1])
            else:
                merged_keep.append(keep_segments[i])
        if len(merged_keep) < len(keep_segments):
            logger.info(f"[ai-clip] V2.27 merged tiny-gap segments: {len(keep_segments)} -> {len(merged_keep)}")
            keep_segments = merged_keep

    total_kept = sum(e - s for s, e in keep_segments)
    logger.info(f"[ai-clip] V2.27 Smart trim: {len(cuttable)} cuts, "
                f"{total_cut_time:.1f}s removed ({total_cut_time/duration*100:.0f}%), "
                f"{total_kept:.1f}s kept, {len(keep_segments)} segments")
    return keep_segments


# ─── V2: Zoom Pulse Keyframes ────────────────────────────────────────────────
def _generate_zoom_keyframes(duration: float, volume_peaks: list,
                              captions: list, max_zoom: float = 1.08) -> list:
    """V2.34.5: zoom pulse完全廃止。画面安定性を最優先とし、一切のzoomを行わない。
    Returns empty list always.
    """
    logger.info(f"[ai-clip] V2.34.5: Zoom pulse DISABLED (visual stability priority)")
    return []


# ─── V2: drawtext-based subtitle rendering (replaces ASS for CJK compat) ──────
# ─── V2.10: Pillow + overlay 方式 ──────────────────────────────────────────
# drawtext / ASS (libass) はAzure App Serviceで日本語レンダリングに失敗するため、
# Pillowで透過PNG画像を生成し、ffmpegのoverlayフィルタで合成する方式に変更。
# overlayフィルタはffmpegの基本機能なので環境依存なく確実に動作する。

def _render_text_overlay_image(
    text: str,
    video_width: int,
    video_height: int,
    font_path: str,
    font_size: int = 72,
    text_color: tuple = (255, 255, 255, 255),
    outline_color: tuple = (0, 0, 0, 255),
    outline_width: int = 4,
    bg_color: tuple = None,
    bg_padding: int = 10,
    position: str = 'center_bottom',
    position_y_pct: float = 75.0,
    max_chars_per_line: int = 18,
) -> 'Image':
    """Pillowで透過PNG画像にテキストを描画する。
    
    Args:
        text: 描画するテキスト
        video_width: 動画の幅
        video_height: 動画の高さ
        font_path: フォントファイルのパス
        font_size: フォントサイズ
        text_color: テキスト色 (R, G, B, A)
        outline_color: アウトライン色 (R, G, B, A)
        outline_width: アウトライン幅
        bg_color: 背景ボックス色 (R, G, B, A) or None
        bg_padding: 背景ボックスのパディング
        position: テキスト位置 ('center_bottom', 'center_top', 'center')
        position_y_pct: Y位置（パーセント）
        max_chars_per_line: 1行あたりの最大文字数（自動改行用）
    
    Returns:
        PIL.Image.Image (RGBA)
    """
    from PIL import Image, ImageDraw, ImageFont
    
    # Clean text
    text = _strip_emoji(text).strip()
    if not text:
        return Image.new('RGBA', (video_width, video_height), (0, 0, 0, 0))
    
    img = Image.new('RGBA', (video_width, video_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Load font
    font = None
    try:
        font = ImageFont.truetype(font_path, font_size)
    except Exception as e:
        logger.warning(f"[ai-clip] Failed to load font {font_path}: {e}")
        # Try fallback fonts
        for fp in _FONT_SEARCH_PATHS:
            try:
                if os.path.exists(fp):
                    font = ImageFont.truetype(fp, font_size)
                    break
            except Exception:
                continue
    if font is None:
        try:
            font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', font_size)
        except Exception:
            font = ImageFont.load_default()
    
    # Auto-wrap text into multiple lines
    lines = []
    current_line = ""
    for char in text:
        test_line = current_line + char
        bbox = draw.textbbox((0, 0), test_line, font=font)
        line_width = bbox[2] - bbox[0]
        # Allow up to 90% of video width
        if line_width > video_width * 0.88:
            if current_line:
                lines.append(current_line)
                current_line = char
            else:
                lines.append(char)
                current_line = ""
        else:
            current_line = test_line
    if current_line:
        lines.append(current_line)
    
    # Calculate total text block dimensions
    line_heights = []
    line_widths = []
    line_spacing = int(font_size * 0.25)
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_widths.append(bbox[2] - bbox[0])
        line_heights.append(bbox[3] - bbox[1])
    
    total_text_h = sum(line_heights) + line_spacing * max(0, len(lines) - 1)
    max_text_w = max(line_widths) if line_widths else 0
    
    # Calculate Y position
    if position == 'center_top':
        block_y = int(video_height * 0.10)
    elif position == 'center':
        block_y = (video_height - total_text_h) // 2
    else:  # center_bottom
        block_y = int(video_height * position_y_pct / 100) - total_text_h
    
    # Draw background box if specified
    if bg_color:
        box_x1 = (video_width - max_text_w) // 2 - bg_padding
        box_y1 = block_y - bg_padding
        box_x2 = (video_width + max_text_w) // 2 + bg_padding
        box_y2 = block_y + total_text_h + bg_padding
        # Rounded rectangle for modern look
        radius = min(bg_padding * 2, 20)
        draw.rounded_rectangle(
            [box_x1, box_y1, box_x2, box_y2],
            radius=radius,
            fill=bg_color,
        )
    
    # Draw each line
    current_y = block_y
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        lw = bbox[2] - bbox[0]
        lh = bbox[3] - bbox[1]
        x = (video_width - lw) // 2
        
        # Draw outline (stroke)
        if outline_width > 0:
            # Use Pillow's built-in stroke for efficiency
            draw.text(
                (x, current_y), line, font=font,
                fill=text_color,
                stroke_width=outline_width,
                stroke_fill=outline_color,
            )
        else:
            draw.text((x, current_y), line, font=font, fill=text_color)
        
        current_y += lh + line_spacing
    
    return img


def _render_text_overlay_image_v2(
    text: str,
    video_width: int,
    video_height: int,
    font_path: str,
    font_size: int = 72,
    text_color: tuple = (255, 255, 255, 255),
    outline_color: tuple = (0, 0, 0, 255),
    outline_width: int = 4,
    bg_color: tuple = None,
    bg_padding: int = 10,
    position: str = 'center_bottom',
    position_y_pct: float = 75.0,
    keyword_spans: list = None,
) -> 'Image':
    """V2.17: キーワード強調付きテキストオーバーレイ画像を生成。
    
    改善点:
    - キーワード部分を別色で描画（商品名=金, CTA=緑, 数字=オレンジ）
    - 長文は自然な位置で改行（句読点・助詞の後）
    - フォントサイズは呼び出し元で調整済み
    """
    from PIL import Image, ImageDraw, ImageFont
    
    keyword_spans = keyword_spans or []
    
    # Clean text
    text = _strip_emoji(text).strip()
    if not text:
        return Image.new('RGBA', (video_width, video_height), (0, 0, 0, 0))
    
    img = Image.new('RGBA', (video_width, video_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Load font (normal + bold)
    font = None
    font_bold = None
    try:
        font = ImageFont.truetype(font_path, font_size)
        # Try to load bold variant
        bold_path = font_path.replace('Regular', 'Bold').replace('regular', 'bold')
        if os.path.exists(bold_path):
            font_bold = ImageFont.truetype(bold_path, font_size)
        else:
            font_bold = font  # Fallback: use same font
    except Exception as e:
        logger.warning(f"[ai-clip] Failed to load font {font_path}: {e}")
        for fp in _FONT_SEARCH_PATHS:
            try:
                if os.path.exists(fp):
                    font = ImageFont.truetype(fp, font_size)
                    font_bold = font
                    break
            except Exception:
                continue
    if font is None:
        try:
            font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', font_size)
            font_bold = font
        except Exception:
            font = ImageFont.load_default()
            font_bold = font
    
    # ── Smart line breaking (自然な位置で改行) ──
    # Break points: after particles, punctuation, before conjunctions
    BREAK_AFTER = set('、。、。，．、。、。、。、。、。')
    BREAK_AFTER_PARTICLES = ['は', 'が', 'を', 'に', 'で', 'も', 'と', 'の', 'へ', 'よ', 'ね', 'よ', 'って', 'から', 'まで', 'けど']
    
    lines = []
    current_line = ""
    max_width = video_width * 0.88
    
    i = 0
    while i < len(text):
        char = text[i]
        test_line = current_line + char
        bbox = draw.textbbox((0, 0), test_line, font=font)
        line_width = bbox[2] - bbox[0]
        
        if line_width > max_width:
            if current_line:
                # Try to find a natural break point in the last few characters
                best_break = -1
                search_range = min(8, len(current_line))
                for j in range(len(current_line) - 1, max(0, len(current_line) - search_range) - 1, -1):
                    if current_line[j] in BREAK_AFTER or current_line[j] in '、。、。，':
                        best_break = j + 1
                        break
                    # Check 2-char particles
                    if j > 0:
                        two_char = current_line[j-1:j+1]
                        if two_char in BREAK_AFTER_PARTICLES:
                            best_break = j + 1
                            break
                    # Single-char particles
                    if current_line[j] in 'はがをにでもとのへよね':
                        best_break = j + 1
                        break
                
                if best_break > 0 and best_break < len(current_line):
                    lines.append(current_line[:best_break])
                    current_line = current_line[best_break:] + char
                else:
                    lines.append(current_line)
                    current_line = char
            else:
                lines.append(char)
                current_line = ""
        else:
            current_line = test_line
        i += 1
    if current_line:
        lines.append(current_line)
    
    # ── Calculate text block dimensions ──
    line_heights = []
    line_widths = []
    line_spacing = int(font_size * 0.25)
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_widths.append(bbox[2] - bbox[0])
        line_heights.append(bbox[3] - bbox[1])
    
    total_text_h = sum(line_heights) + line_spacing * max(0, len(lines) - 1)
    max_text_w = max(line_widths) if line_widths else 0
    
    # Calculate Y position
    if position == 'center_top':
        block_y = int(video_height * 0.10)
    elif position == 'center':
        block_y = (video_height - total_text_h) // 2
    else:  # center_bottom
        block_y = int(video_height * position_y_pct / 100) - total_text_h
    
    # Draw background box
    if bg_color:
        box_x1 = (video_width - max_text_w) // 2 - bg_padding
        box_y1 = block_y - bg_padding
        box_x2 = (video_width + max_text_w) // 2 + bg_padding
        box_y2 = block_y + total_text_h + bg_padding
        radius = min(bg_padding * 2, 20)
        draw.rounded_rectangle(
            [box_x1, box_y1, box_x2, box_y2],
            radius=radius,
            fill=bg_color,
        )
    
    # ── Draw each line with keyword highlighting ──
    # Build a mapping from original text position to (line_idx, char_offset)
    char_pos_map = []  # For each char in original text, which line and offset
    pos = 0
    for line_idx, line in enumerate(lines):
        for char_offset in range(len(line)):
            char_pos_map.append((line_idx, char_offset))
            pos += 1
    
    current_y = block_y
    text_pos_offset = 0  # Track position in original text
    
    for line_idx, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        lw = bbox[2] - bbox[0]
        lh = bbox[3] - bbox[1]
        x_start = (video_width - lw) // 2
        
        if not keyword_spans:
            # No keywords - draw normally
            if outline_width > 0:
                draw.text(
                    (x_start, current_y), line, font=font,
                    fill=text_color,
                    stroke_width=outline_width,
                    stroke_fill=outline_color,
                )
            else:
                draw.text((x_start, current_y), line, font=font, fill=text_color)
        else:
            # Draw character by character with keyword highlighting
            char_x = x_start
            for ci, char in enumerate(line):
                abs_pos = text_pos_offset + ci
                # Check if this position is within a keyword span
                char_color = text_color
                use_bold = False
                for span_start, span_end, span_color in keyword_spans:
                    if span_start <= abs_pos < span_end:
                        char_color = span_color
                        use_bold = True
                        break
                
                active_font = font_bold if use_bold else font
                if outline_width > 0:
                    draw.text(
                        (char_x, current_y), char, font=active_font,
                        fill=char_color,
                        stroke_width=outline_width + (1 if use_bold else 0),
                        stroke_fill=outline_color,
                    )
                else:
                    draw.text((char_x, current_y), char, font=active_font, fill=char_color)
                
                # Advance x position
                char_bbox = draw.textbbox((0, 0), char, font=active_font)
                char_x += char_bbox[2] - char_bbox[0]
        
        text_pos_offset += len(line)
        current_y += lh + line_spacing
    
    return img


def _render_selling_point_image(
    text: str,
    video_width: int,
    video_height: int,
    font_path: str,
    font_size: int = 80,
    y_position: int = 0,
) -> 'Image':
    """V2.33: 卖点キーワード専用のオーバーレイ画像を生成。

    デザイン原則:
    - 絶対に背景色ブロックを付けない
    - 太字・高コントラスト色（オレンジまたは亮黄色）
    - 強制単行表示
    - 基礎字幕の上方に固定位置
    """
    from PIL import Image, ImageDraw, ImageFont

    text = _strip_emoji(text).strip()
    if not text:
        return Image.new('RGBA', (video_width, video_height), (0, 0, 0, 0))

    img = Image.new('RGBA', (video_width, video_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # フォント読み込み（Bold優先）
    try:
        font = ImageFont.truetype(font_path, font_size)
    except Exception:
        font = ImageFont.load_default()

    # テキストサイズ計算
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    # 中央揃え、指定のY位置に配置
    x = (video_width - text_w) // 2
    y = y_position - text_h // 2  # 指定位置を中心に

    # 色定義: オレンジ系（高視認性、広告感）
    # メインカラー: 明るいオレンジ (255, 165, 0)
    # アウトライン: 濃い赤褐色（コントラスト確保）
    text_color = (255, 170, 0, 255)  # オレンジ（視認性最大）
    outline_color = (80, 30, 0, 255)  # 濃い褐色アウトライン
    outline_width = max(4, int(font_size * 0.07))  # フォントサイズ比例のアウトライン

    # 描画: アウトライン付き太字（背景なし）
    draw.text(
        (x, y), text, font=font,
        fill=text_color,
        stroke_width=outline_width,
        stroke_fill=outline_color,
    )

    return img


def _generate_overlay_images(
    styled_captions: list,
    hook_text: Optional[str],
    cta_text: Optional[str],
    video_width: int,
    video_height: int,
    duration: float,
    font_path: str,
    tmp_dir: str,
    position_y: float = 75.0,
    clip_duration: float = 0,
    product_name: str = "",
    selling_points: list = None,
) -> list:
    """Pillowで全overlay画像（フック・字幕・CTA・卖点）を生成する。
    
    clip_duration: 実際の出力クリップの長さ（秒）。0の場合はdurationを使用。
    字幕のタイムスタンプがclip_durationを超える場合、自動的にオフセット補正を行う。
    
    Returns:
        list of (png_path, start_time, end_time) tuples
    """
    from PIL import Image
    
    overlays = []
    base_width = 1080
    scale = min(video_width, video_height) / base_width
    
    # ── Determine effective clip duration and caption time offset ──
    effective_duration = clip_duration if clip_duration > 0 else duration
    
    # Auto-detect if captions need time offset correction
    # If the first caption starts after the clip duration, captions are in absolute time
    caption_offset = 0.0
    if styled_captions:
        first_start = min(float(c.get('start', 0)) for c in styled_captions if c.get('text', '').strip())
        last_end = max(float(c.get('end', 0)) for c in styled_captions if c.get('text', '').strip())
        # If captions span beyond clip duration, they need offset correction
        if first_start > effective_duration * 0.5 or last_end > effective_duration * 1.5:
            caption_offset = first_start
            logger.info(f"[ai-clip] Caption time offset detected: {caption_offset:.1f}s "
                        f"(first_start={first_start:.1f}, last_end={last_end:.1f}, "
                        f"clip_duration={effective_duration:.1f})")
    
    logger.info(f"[ai-clip] Overlay generation: duration={duration:.1f}, "
                f"clip_duration={effective_duration:.1f}, caption_offset={caption_offset:.1f}")
    
    # ── Hook text (first 3 seconds) ──
    if hook_text:
        hook_style = _pick_hook_style()
        # Convert ASS color (&HBBGGRR) to RGB
        # ASS primary_color format: &H00BBGGRR
        hook_fontsize = max(60, int(90 * scale))
        hook_img = _render_text_overlay_image(
            text=hook_text,
            video_width=video_width,
            video_height=video_height,
            font_path=font_path,
            font_size=hook_fontsize,
            text_color=(255, 255, 255, 255),
            outline_color=(0, 0, 0, 255),
            outline_width=max(4, int(6 * scale)),
            bg_color=(0, 0, 0, 140),
            bg_padding=15,
            position='center_top',
        )
        hook_path = os.path.join(tmp_dir, "overlay_hook.png")
        hook_img.save(hook_path, 'PNG')
        overlays.append((hook_path, 0.0, 3.0))
        logger.info(f"[ai-clip] Generated hook overlay: {hook_text[:30]}")
    
    # ── Subtitle captions ── (V2.17: 重畳防止 + 長文対応 + キーワード強調)
    sub_fontsize = max(44, int(72 * scale))
    MIN_DISPLAY = 2.0
    SUBTITLE_GAP = 0.1  # 字幕間ギャップ（重畳防止）
    MAX_SINGLE_LINE_CHARS = 16  # これ以上は自動でフォント縮小
    
    # キーワード強調用の色定義
    KEYWORD_HIGHLIGHT_COLOR = (255, 255, 100, 255)  # Bright yellow for keywords (clear against dark outline)
    KEYWORD_CTA_COLOR = (100, 255, 180, 255)  # Light green for CTA (readable)
    KEYWORD_NUMBER_COLOR = (255, 255, 255, 255)  # White for numbers/prices (was orange - too blurry)
    
    # Build keyword set for highlighting
    highlight_keywords = set()
    if product_name:
        for word in product_name.split():
            if len(word) >= 2:
                highlight_keywords.add(word)
    for kw in _CTA_KEYWORDS:
        highlight_keywords.add(kw)
    for kw in _EMPHASIS_KEYWORDS:
        highlight_keywords.add(kw)
    
    # Pre-process captions: calculate timing with STRICT non-overlap guarantee
    # Strategy: Each subtitle occupies a non-overlapping time slot.
    # We use the original start/end from Whisper but GUARANTEE that:
    #   caption[i].end <= caption[i+1].start (strictly sequential)
    raw_captions = []
    for i, cap in enumerate(styled_captions or []):
        cap_start = float(cap.get('start', 0)) - caption_offset
        cap_end = float(cap.get('end', 0)) - caption_offset
        cap_text = cap.get('text', '').strip()
        if not cap_text:
            continue
        # Skip captions outside clip duration
        if cap_start >= effective_duration or cap_end <= 0:
            continue
        # Clamp to clip boundaries
        cap_start = max(0.0, cap_start)
        cap_end = min(effective_duration, cap_end)
        if cap_end <= cap_start:
            cap_end = cap_start + 0.5
        raw_captions.append({
            'start': cap_start,
            'end': cap_end,
            'text': cap_text,
            'style': cap.get('style', 'box'),
            'orig_idx': i,
        })
    
    # Sort by start time to handle any out-of-order segments
    raw_captions.sort(key=lambda x: x['start'])
    
    # STRICT non-overlap enforcement: each caption ends before next begins
    # This is the ONLY place timing is finalized - no MIN_DISPLAY expansion that could cause overlap
    # V12 FIX: フックと字幕は表示位置が異なる（フック=上部、字幕=下部）ので共存可能
    # 以前はhook_end_time=3.0で冒頭字幕をスキップしていたが、これが「冒頭2-3秒に字幕がない」問題の原因
    # hook_end_time = 3.0 if hook_text else 0.0  # REMOVED: 字幕とフックは位置が異なるので共存OK
    
    processed_captions = []
    prev_end = -1.0
    for cap in raw_captions:
        # Ensure this caption starts after previous one ended (with gap)
        # V12: フック表示中でも字幕は表示する（位置が異なるため重ならない）
        actual_start = max(cap['start'], prev_end + SUBTITLE_GAP)
        actual_end = cap['end']
        
        # If start was pushed forward, also push end forward proportionally
        if actual_start > cap['start']:
            shift = actual_start - cap['start']
            actual_end = actual_end + shift
        
        # Clamp end to clip duration
        actual_end = min(actual_end, effective_duration)
        
        # Ensure minimum display time (but NEVER extend past next caption's original start)
        if actual_end - actual_start < 0.5:
            actual_end = actual_start + 0.5
        
        # Skip if this caption would be entirely outside the clip
        if actual_start >= effective_duration:
            continue
        
        processed_captions.append({
            'start': actual_start,
            'end': actual_end,
            'text': cap['text'],
            'style': cap['style'],
            'orig_idx': cap['orig_idx'],
        })
        prev_end = actual_end
    
    # FINAL PASS: Absolutely guarantee no two captions overlap
    # This handles any edge cases from the above logic
    for i in range(len(processed_captions) - 1):
        curr = processed_captions[i]
        nxt = processed_captions[i + 1]
        if curr['end'] >= nxt['start']:
            # Force current to end before next starts
            curr['end'] = nxt['start'] - SUBTITLE_GAP
            # Ensure at least 0.3s display (absolute minimum)
            if curr['end'] - curr['start'] < 0.3:
                curr['end'] = curr['start'] + 0.3
                # If this STILL overlaps, push next forward
                if curr['end'] >= nxt['start']:
                    nxt['start'] = curr['end'] + SUBTITLE_GAP
    
    for cap_info in processed_captions:
        cap_text = cap_info['text']
        cap_start = cap_info['start']
        cap_end = cap_info['end']
        style = cap_info['style']
        orig_idx = cap_info['orig_idx']
        
        # ── 長文対応: フォントサイズ動的調整 ──
        text_len = len(cap_text)
        adjusted_fontsize = sub_fontsize
        if text_len > MAX_SINGLE_LINE_CHARS * 2:  # Very long (32+ chars)
            adjusted_fontsize = max(36, int(sub_fontsize * 0.65))
        elif text_len > MAX_SINGLE_LINE_CHARS:  # Long (16-32 chars)
            adjusted_fontsize = max(40, int(sub_fontsize * 0.80))
        
        # Determine style-based colors
        if style == 'pop':
            text_color = (255, 255, 255, 255)  # White (was yellow - looked blurry)
            outline_clr = (220, 50, 50, 255)  # Red outline (was orange - low contrast)
            bg_clr = (0, 0, 0, 140)  # Slightly darker bg for readability
        elif style == 'gradient':
            text_color = (255, 255, 255, 255)
            outline_clr = (0, 0, 0, 255)
            bg_clr = (100, 40, 130, 100)  # Purple tint
        elif style == 'outline':
            text_color = (255, 255, 255, 255)
            outline_clr = (0, 0, 0, 255)
            bg_clr = None
        elif style == 'karaoke':
            text_color = (255, 255, 255, 200)
            outline_clr = (0, 0, 0, 255)
            bg_clr = (0, 0, 0, 80)
        else:  # box, simple, default
            text_color = (255, 255, 255, 255)
            outline_clr = (0, 0, 0, 255)
            bg_clr = (0, 0, 0, 100)
        
        # ── キーワード強調: 商品名・CTA・数字を検出 ──
        # Find keywords in text for highlighting
        keyword_spans = []  # (start_pos, end_pos, color)
        for kw in highlight_keywords:
            idx = cap_text.find(kw)
            while idx >= 0:
                kw_color = KEYWORD_CTA_COLOR if kw in _CTA_KEYWORDS else KEYWORD_HIGHLIGHT_COLOR
                keyword_spans.append((idx, idx + len(kw), kw_color))
                idx = cap_text.find(kw, idx + len(kw))
        # Also highlight numbers/prices (e.g., 1980円, 50%OFF)
        import re as _re
        for m in _re.finditer(r'\d+[%％円万]?(?:OFF|off|オフ)?', cap_text):
            keyword_spans.append((m.start(), m.end(), KEYWORD_NUMBER_COLOR))
        
        # Generate subtitle image with keyword highlighting
        sub_img = _render_text_overlay_image_v2(
            text=cap_text,
            video_width=video_width,
            video_height=video_height,
            font_path=font_path,
            font_size=adjusted_fontsize,
            text_color=text_color,
            outline_color=outline_clr,
            outline_width=max(3, int(5 * scale)),
            bg_color=bg_clr,
            bg_padding=8,
            position='center_bottom',
            position_y_pct=position_y,
            keyword_spans=keyword_spans,
        )
        sub_path = os.path.join(tmp_dir, f"overlay_sub_{orig_idx:03d}.png")
        sub_img.save(sub_path, 'PNG')
        overlays.append((sub_path, cap_start, cap_end))
    
    # ── V2.33: 卖点キーワードオーバーレイ（基礎字幕の上方・中部に表示）──
    # ルール: 無背景、太字、オレンジ/亮黄色、強制単行、基礎字幕と空間的に完全解偶
    if selling_points:
        # 卖点フォントサイズ: 基礎字幕より大きく、視覚的に目立つ
        sp_fontsize = max(52, int(80 * scale))
        # 卖点表示位置: 画面中部（基礎字幕の上方）
        # 基礎字幕はposition_y（デフォルト75%）にあるので、卖点はその上方に配置
        # 卖点Y位置 = 基礎字幕Y位置 - 15%（十分な間隔を確保）
        sp_position_y_pct = max(30.0, position_y - 18.0)  # 最低30%（画面上部すぎない）
        sp_y_pixel = int(video_height * sp_position_y_pct / 100)

        for sp_idx, sp_item in enumerate(selling_points):
            if isinstance(sp_item, dict):
                sp_text = sp_item.get("text", "")
                sp_start = float(sp_item.get("start", 0))
                sp_end = float(sp_item.get("end", 0))
            else:
                continue

            if not sp_text or sp_end <= sp_start:
                continue

            # 強制単行: 改行を禁止（スペースに置換）
            sp_text = sp_text.replace("\n", " ").replace("\\N", " ").strip()[:15]

            # 卖点オーバーレイ画像を生成
            # ルール: 無背景、太字、オレンジ色、高コントラスト
            sp_img = _render_selling_point_image(
                text=sp_text,
                video_width=video_width,
                video_height=video_height,
                font_path=font_path,
                font_size=sp_fontsize,
                y_position=sp_y_pixel,
            )
            sp_path = os.path.join(tmp_dir, f"overlay_sp_{sp_idx:02d}.png")
            sp_img.save(sp_path, 'PNG')
            overlays.append((sp_path, sp_start, sp_end))

        logger.info(f"[ai-clip] V2.33 Generated {len(selling_points)} selling point overlays "
                    f"(y_pos={sp_position_y_pct:.0f}%, fontsize={sp_fontsize})")

    # ── CTA text (last 3.5 seconds) ──
    if cta_text and effective_duration > 5:
        cta_fontsize = max(50, int(80 * scale))
        cta_start = max(0, effective_duration - 3.5)
        cta_end = effective_duration - 0.3
        cta_img = _render_text_overlay_image(
            text=cta_text,
            video_width=video_width,
            video_height=video_height,
            font_path=font_path,
            font_size=cta_fontsize,
            text_color=(255, 215, 0, 255),  # Gold
            outline_color=(255, 69, 0, 255),  # OrangeRed
            outline_width=max(3, int(5 * scale)),
            bg_color=(0, 0, 0, 150),
            bg_padding=12,
            position='center_top',
        )
        cta_path = os.path.join(tmp_dir, "overlay_cta.png")
        cta_img.save(cta_path, 'PNG')
        overlays.append((cta_path, cta_start, cta_end))
        logger.info(f"[ai-clip] Generated CTA overlay: {cta_text[:30]}")
    
    # ── V2.20: 結尾矢印アニメーション（最後5秒に左下角への矢印を表示） ──
    # V2.22: 動的指示矢印（高コントラスト、警示性強、バウンスアニメーション）
    # 位置: 字幕の上方（左下方向を指す）→ 元の位置から上移
    # デザイン: 赤・黄の警告色 + バウンスアニメーション（複数フレームPNGで実現）
    if effective_duration > 5:
        try:
            from PIL import Image, ImageDraw, ImageFilter
            
            arrow_duration = min(5.0, effective_duration - 1.0)
            arrow_start = max(0, effective_duration - arrow_duration)
            arrow_end = effective_duration - 0.2
            
            # V2.22: 矢印位置を字幕の上方に上移
            # 元: video_height * 0.82 → 新: video_height * 0.58（字幕は75%付近なのでその上）
            arrow_center_x = int(video_width * 0.13)
            arrow_center_y = int(video_height * 0.58)  # 上移: 0.82 → 0.58
            arrow_size = int(min(video_width, video_height) * 0.10)  # サイズ拡大: 0.08 → 0.10
            
            # バウンスアニメーション: 4フレームの矢印を生成し、各フレームで少しずつ位置を変える
            # ffmpegのoverlay enableで時間分割してバウンス感を出す
            bounce_offsets = [0, -12, -20, -12]  # Yオフセット（ピクセル）
            frame_duration = 0.25  # 各フレーム 0.25秒
            total_cycle = frame_duration * len(bounce_offsets)  # 1サイクル = 1秒
            
            for frame_idx, y_offset in enumerate(bounce_offsets):
                arrow_img = Image.new('RGBA', (video_width, video_height), (0, 0, 0, 0))
                draw = ImageDraw.Draw(arrow_img)
                
                # バウンス位置計算
                cur_y = arrow_center_y + y_offset
                
                # V2.22: 高コントラスト赤・黄の警告色矢印
                # 外側の光彩（赤）
                glow_radius = int(arrow_size * 1.3)
                for glow_i in range(3):
                    glow_alpha = 60 - glow_i * 15
                    glow_r = glow_radius + glow_i * 4
                    draw.ellipse(
                        [(arrow_center_x - glow_r, cur_y - glow_r),
                         (arrow_center_x + glow_r, cur_y + glow_r)],
                        fill=None,
                        outline=(255, 50, 50, glow_alpha),
                        width=max(4, int(arrow_size * 0.06))
                    )
                
                # 矢印本体（真下方向を指す矢印 → 商品リンクボタンへ誘導）
                # V2.26: 方向修正 — 斜め↙ → ほぼ真下↓（商品リンクは矢印の真下にある）
                shaft_start_x = arrow_center_x + int(arrow_size * 0.08)  # ほぼ中央（わずかに右）
                shaft_start_y = cur_y - int(arrow_size * 0.7)            # 上端
                shaft_end_x = arrow_center_x - int(arrow_size * 0.08)   # ほぼ中央（わずかに左）
                shaft_end_y = cur_y + int(arrow_size * 0.7)             # 下端
                shaft_width = max(8, int(arrow_size * 0.18))
                
                # 矢印軸の赤いアウトライン（先に描画）
                draw.line(
                    [(shaft_start_x, shaft_start_y), (shaft_end_x, shaft_end_y)],
                    fill=(255, 30, 30, 200),
                    width=shaft_width + 6
                )
                # 矢印軸（黄色の太線・上書き）
                draw.line(
                    [(shaft_start_x, shaft_start_y), (shaft_end_x, shaft_end_y)],
                    fill=(255, 220, 0, 255),  # 黄色（高視認性）
                    width=shaft_width
                )
                
                # 矢印の先端（下向き三角形 → 商品リンクを指す）
                head_size = int(arrow_size * 0.55)
                # 真下方向の三角形
                tip_x = shaft_end_x  # 真下
                tip_y = shaft_end_y + int(head_size * 0.5)  # 先端は軸の下
                head_points = [
                    (tip_x, tip_y),  # 先端（真下）
                    (shaft_end_x + int(head_size * 0.38), shaft_end_y - int(head_size * 0.05)),  # 右上
                    (shaft_end_x - int(head_size * 0.38), shaft_end_y - int(head_size * 0.05)),  # 左上
                ]
                # 赤いアウトライン
                draw.polygon(head_points, fill=(255, 30, 30, 240))
                # 黄色の内側（少し小さく）
                inner_scale = 0.75
                cx = sum(p[0] for p in head_points) / 3
                cy = sum(p[1] for p in head_points) / 3
                inner_points = [
                    (int(cx + (p[0] - cx) * inner_scale), int(cy + (p[1] - cy) * inner_scale))
                    for p in head_points
                ]
                draw.polygon(inner_points, fill=(255, 220, 0, 255))
                
                # フレーム保存
                frame_path = os.path.join(tmp_dir, f"overlay_arrow_cta_f{frame_idx}.png")
                arrow_img.save(frame_path, 'PNG')
                
                # 各フレームの表示時間を計算（ループ再生）
                # arrow_startからarrow_endまで、frame_durationごとにフレームを切り替え
                t = arrow_start
                while t < arrow_end:
                    frame_start = t + frame_idx * frame_duration
                    frame_end = min(frame_start + frame_duration, arrow_end)
                    if frame_start >= arrow_end:
                        break
                    overlays.append((frame_path, frame_start, frame_end))
                    t += total_cycle
            
            logger.info(f"[ai-clip] V2.26 Generated ANIMATED arrow CTA overlay (direction=DOWN): "
                        f"pos=({arrow_center_x},{arrow_center_y}), "
                        f"frames={len(bounce_offsets)}, "
                        f"time={arrow_start:.1f}-{arrow_end:.1f}s")
        except Exception as arrow_err:
            logger.warning(f"[ai-clip] Arrow overlay generation failed: {arrow_err}")
    
    logger.info(f"[ai-clip] Generated {len(overlays)} overlay images "
                f"(hook={'yes' if hook_text else 'no'}, "
                f"captions={len(styled_captions or [])}, "
                f"cta={'yes' if cta_text else 'no'}, "
                f"arrow={'yes' if effective_duration > 5 else 'no'})")
    return overlays


# ─── V2.1: Sound Effect (SE) Insertion ────────────────────────────────────────
_SE_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'assets', 'sound_effects')

# SE categories and their files
_SE_HOOK_FILES = ['hook_impact.mp3', 'hook_shine.mp3', 'hook_pop.mp3']
_SE_TRANSITION_FILES = ['whoosh_transition.mp3']
_SE_CTA_FILES = ['cta_attention.mp3']
_SE_PRICE_FILES = ['price_reveal.mp3']


async def _apply_sound_effects(video_path: str, output_path: str, duration: float,
                               hook_time: float = 0.0, cta_time: float = None,
                               enable_hook_se: bool = True, enable_cta_se: bool = True) -> bool:
    """効果音（SE）を動画にミックスする。
    - フック表示時（0秒付近）: インパクト音
    - CTA表示時（最後3秒）: アテンション音
    音量は元動画の声を邪魔しないように低めに設定。
    Returns True if SE was applied, False if skipped.
    """
    se_dir = os.path.normpath(_SE_ASSETS_DIR)
    if not os.path.isdir(se_dir):
        logger.warning(f"[ai-clip] SE assets dir not found: {se_dir}")
        return False

    # Select SE files
    se_inputs = []  # (file_path, start_time, volume)

    if enable_hook_se:
        hook_se_file = random.choice(_SE_HOOK_FILES)
        hook_se_path = os.path.join(se_dir, hook_se_file)
        if os.path.exists(hook_se_path):
            se_inputs.append((hook_se_path, hook_time, 0.4))  # 40% volume

    if enable_cta_se and cta_time is not None:
        cta_se_file = random.choice(_SE_CTA_FILES)
        cta_se_path = os.path.join(se_dir, cta_se_file)
        if os.path.exists(cta_se_path):
            se_inputs.append((cta_se_path, cta_time, 0.35))  # 35% volume

    if not se_inputs:
        return False

    # Build ffmpeg command to mix SE with video
    input_args = ["-i", video_path]
    filter_parts = []
    amix_inputs = ["[0:a]"]

    for i, (se_path, start_t, vol) in enumerate(se_inputs):
        input_idx = i + 1
        input_args.extend(["-i", se_path])
        # Delay SE to start_time and adjust volume
        delay_ms = int(start_t * 1000)
        filter_parts.append(
            f"[{input_idx}:a]adelay={delay_ms}|{delay_ms},volume={vol}[se{i}]"
        )
        amix_inputs.append(f"[se{i}]")

    # Mix all audio streams
    n_inputs = len(amix_inputs)
    mix_expr = "".join(amix_inputs)
    filter_parts.append(f"{mix_expr}amix=inputs={n_inputs}:duration=first:dropout_transition=2[aout]")

    filter_str = ";".join(filter_parts)

    cmd = [
        "ffmpeg", "-y",
        *input_args,
        "-filter_complex", filter_str,
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",  # Don't re-encode video
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        if proc.returncode != 0:
            logger.warning(f"[ai-clip] SE mix failed: {stderr.decode(errors='replace')[-300:]}")
            return False
        logger.info(f"[ai-clip] SE applied: {len(se_inputs)} effects mixed")
        return True
    except (asyncio.TimeoutError, Exception) as e:
        logger.warning(f"[ai-clip] SE mix error: {e}")
        return False


# ─── V2.29: Pre-splice segments (concat-based silence trim) ─────────────────────────
async def _pre_splice_segments(
    video_path: str, keep_segments: list, tmp_dir: str, video_fps: int = 30
) -> tuple:
    """V2.29: select filterの代わりにconcat demuxerで無音カットを実行する。
    
    問題: select+setptsによる無音カットでは、ffmpegのデコーダ内部状態やVFR入力の
    浮動小数点精度問題により、切点で1フレーム回溯（重複）が発生する。
    
    解決: 各keep_segmentを独立した一時ファイルとして抽出し、concat demuxerで
    結合することで、切点を完全にクリーンなファイル境界にする。
    
    Args:
        video_path: 入力動画パス
        keep_segments: [(start, end), ...] 保持するセグメント
        tmp_dir: 一時ファイル保存先
        video_fps: 動画のフレームレート
    
    Returns:
        (spliced_path, new_duration) - 結合済み動画パスと新しいduration
        失敗時は (None, 0) を返す
    """
    if len(keep_segments) <= 1:
        return (None, 0)
    
    splice_dir = os.path.join(tmp_dir, "splice_segments")
    os.makedirs(splice_dir, exist_ok=True)
    
    segment_paths = []
    for i, (seg_start, seg_end) in enumerate(keep_segments):
        seg_path = os.path.join(splice_dir, f"seg_{i:03d}.mp4")
        # Re-encode each segment to ensure clean frame boundaries
        # Using -ss before -i for fast seek, then -t for duration
        seg_duration = seg_end - seg_start
        extract_cmd = [
            "ffmpeg", "-y", "-hide_banner",
            "-ss", f"{seg_start:.4f}",
            "-i", video_path,
            "-t", f"{seg_duration:.4f}",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
            "-r", str(video_fps),  # Force CFR output
            "-c:a", "aac", "-b:a", "128k",
            "-avoid_negative_ts", "make_zero",
            "-video_track_timescale", str(video_fps * 1000),
            seg_path
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *extract_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            if proc.returncode != 0 or not os.path.exists(seg_path):
                logger.warning(f"[ai-clip] V2.29: Segment {i} extraction failed: {stderr.decode(errors='replace')[-200:]}")
                return (None, 0)
            # Verify segment has content
            if os.path.getsize(seg_path) < 500:
                logger.warning(f"[ai-clip] V2.29: Segment {i} too small ({os.path.getsize(seg_path)} bytes)")
                return (None, 0)
            segment_paths.append(seg_path)
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"[ai-clip] V2.29: Segment {i} extraction error: {e}")
            return (None, 0)
    
    # V2.30: Concat with crossfade between segments to smooth cut points
    # xfade filter adds a short crossfade (0.1s) at each cut point to mask jump cuts
    spliced_path = os.path.join(tmp_dir, "spliced_nosil.mp4")
    XFADE_DURATION = 0.1  # 0.1秒の短いcrossfade（自然な過渡、不自然にならない）
    
    if len(segment_paths) == 2:
        # 2セグメント: 単純なxfade
        # Get duration of first segment
        probe_cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", segment_paths[0]]
        probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=15)
        seg0_dur = keep_segments[0][1] - keep_segments[0][0]  # fallback
        try:
            seg0_dur = float(json.loads(probe_result.stdout)["format"]["duration"])
        except Exception:
            pass
        offset = max(0, seg0_dur - XFADE_DURATION)
        concat_cmd = [
            "ffmpeg", "-y", "-hide_banner",
            "-i", segment_paths[0], "-i", segment_paths[1],
            "-filter_complex",
            f"[0:v][1:v]xfade=transition=fade:duration={XFADE_DURATION}:offset={offset:.3f}[v];"
            f"[0:a][1:a]acrossfade=d={XFADE_DURATION}[a]",
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            spliced_path
        ]
    elif len(segment_paths) <= 6:
        # 3-6セグメント: xfadeチェーン
        # Get durations of each segment
        seg_durations = []
        for i, sp in enumerate(segment_paths):
            probe_cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", sp]
            probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=15)
            fallback_dur = keep_segments[i][1] - keep_segments[i][0]
            try:
                seg_durations.append(float(json.loads(probe_result.stdout)["format"]["duration"]))
            except Exception:
                seg_durations.append(fallback_dur)
        
        # Build xfade filter chain
        inputs = "".join(f"-i {sp} " for sp in segment_paths)
        n = len(segment_paths)
        vfilter_parts = []
        afilter_parts = []
        cumulative_offset = 0.0
        
        for i in range(n - 1):
            if i == 0:
                vin = "[0:v]"
            else:
                vin = f"[v{i}]"
            next_vin = f"[{i+1}:v]"
            out_label = f"[v{i+1}]" if i < n - 2 else "[v]"
            
            cumulative_offset += seg_durations[i] - XFADE_DURATION
            offset = max(0, cumulative_offset)
            vfilter_parts.append(
                f"{vin}{next_vin}xfade=transition=fade:duration={XFADE_DURATION}:offset={offset:.3f}{out_label}"
            )
            
            if i == 0:
                ain = "[0:a]"
            else:
                ain = f"[a{i}]"
            next_ain = f"[{i+1}:a]"
            aout_label = f"[a{i+1}]" if i < n - 2 else "[a]"
            afilter_parts.append(
                f"{ain}{next_ain}acrossfade=d={XFADE_DURATION}{aout_label}"
            )
        
        filter_complex = ";".join(vfilter_parts + afilter_parts)
        concat_cmd = ["ffmpeg", "-y", "-hide_banner"]
        for sp in segment_paths:
            concat_cmd.extend(["-i", sp])
        concat_cmd.extend([
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            spliced_path
        ])
    else:
        # 7+セグメント: xfadeが複雑すぎるので、単純concatにフォールバック
        # （zoom burst + flashで切点を過隠する）
        concat_list_path = os.path.join(splice_dir, "concat_list.txt")
        with open(concat_list_path, 'w') as f:
            for p in segment_paths:
                f.write(f"file '{p}'\n")
        concat_cmd = [
            "ffmpeg", "-y", "-hide_banner",
            "-f", "concat", "-safe", "0",
            "-i", concat_list_path,
            "-c", "copy",
            "-movflags", "+faststart",
            spliced_path
        ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *concat_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
        if proc.returncode != 0 or not os.path.exists(spliced_path):
            # V2.30: xfadeが失敗した場合、単純concatにフォールバック
            logger.warning(f"[ai-clip] V2.30: Crossfade concat failed, falling back to simple concat: {stderr.decode(errors='replace')[-200:]}")
            concat_list_path = os.path.join(splice_dir, "concat_list.txt")
            with open(concat_list_path, 'w') as f:
                for p in segment_paths:
                    f.write(f"file '{p}'\n")
            fallback_cmd = [
                "ffmpeg", "-y", "-hide_banner",
                "-f", "concat", "-safe", "0",
                "-i", concat_list_path,
                "-c", "copy",
                "-movflags", "+faststart",
                spliced_path
            ]
            proc2 = await asyncio.create_subprocess_exec(
                *fallback_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr2 = await asyncio.wait_for(proc2.communicate(), timeout=60)
            if proc2.returncode != 0 or not os.path.exists(spliced_path):
                logger.warning(f"[ai-clip] V2.30: Fallback concat also failed: {stderr2.decode(errors='replace')[-200:]}")
                return (None, 0)
            logger.info(f"[ai-clip] V2.30: Fallback simple concat succeeded")
    except (asyncio.TimeoutError, Exception) as e:
        logger.warning(f"[ai-clip] V2.30: Concat error: {e}")
        return (None, 0)
    
    # Get actual duration of spliced file
    probe_cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", spliced_path]
    probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=15)
    new_duration = sum(e - s for s, e in keep_segments)  # fallback
    try:
        probe_data = json.loads(probe_result.stdout)
        if "format" in probe_data and "duration" in probe_data["format"]:
            new_duration = float(probe_data["format"]["duration"])
    except Exception:
        pass
    
    logger.info(f"[ai-clip] V2.29: Pre-splice complete: {len(segment_paths)} segments -> "
                f"{os.path.getsize(spliced_path)//1024}KB, duration={new_duration:.2f}s")
    
    # Cleanup individual segment files to save disk space
    for p in segment_paths:
        try:
            os.remove(p)
        except OSError:
            pass
    
    return (spliced_path, new_duration)


# ─── V2: Build Advanced ffmpeg Filter Chain ──────────────────────────────────────────
def _remap_time_for_silence_trim(original_time: float, keep_segments: list) -> float:
    """V2.18: 元の動画の時間を、無音カット後の出力時間軸にリマッピングする。
    
    keep_segments: [(start, end), ...] - 保持されるセグメント（元の時間軸）
    original_time: 元の動画での時間（秒）
    
    Returns: 出力動画での対応する時間（秒）。
    元の時間がカットされた区間内の場合、直前のkeepセグメントの終端に対応する出力時間を返す。
    """
    output_time = 0.0
    for seg_start, seg_end in keep_segments:
        if original_time <= seg_start:
            # この時間はこのセグメントの前にある（カットされた区間）
            return output_time
        elif original_time <= seg_end:
            # この時間はこのセグメント内にある
            return output_time + (original_time - seg_start)
        else:
            # このセグメントは完全に通過済み
            output_time += (seg_end - seg_start)
    # 全セグメントを超えている場合
    return output_time


def _remap_overlay_images_for_silence_trim(overlay_images: list, keep_segments: list) -> list:
    """V2.18: overlay_imagesのタイミングをkeep_segmentsに基づいてリマッピングする。
    
    無音カット（select filter + setpts）後の出力時間軸に合わせて、
    各overlay画像の表示開始・終了時間を再計算する。
    カットされた区間に完全に含まれるoverlayは除外する。
    """
    if not overlay_images or len(keep_segments) <= 1:
        return overlay_images
    
    remapped = []
    for png_path, start_t, end_t in overlay_images:
        new_start = _remap_time_for_silence_trim(start_t, keep_segments)
        new_end = _remap_time_for_silence_trim(end_t, keep_segments)
        
        # カットされた区間に完全に含まれる場合（new_start == new_end）はスキップ
        if new_end - new_start < 0.05:
            continue
        
        remapped.append((png_path, new_start, new_end))
    
    return remapped


def _remap_zoom_keyframes_for_silence_trim(zoom_keyframes: list, keep_segments: list) -> list:
    """V2.18: zoom_keyframesのタイミングをkeep_segmentsに基づいてリマッピングする。"""
    if not zoom_keyframes or len(keep_segments) <= 1:
        return zoom_keyframes
    
    remapped = []
    for t, zf in zoom_keyframes:
        new_t = _remap_time_for_silence_trim(t, keep_segments)
        # カットされた区間内のズームポイントは除外
        # （直前のセグメント終端と同じ時間になるポイントは、次のポイントと重なる可能性があるのでスキップ）
        if remapped and abs(new_t - remapped[-1][0]) < 0.1:
            continue
        remapped.append((new_t, zf))
    
    return remapped


def _build_advanced_ffmpeg_command(video_path: str, ass_path: str, output_path: str,
    video_width: int, video_height: int, duration: float,
    req, zoom_keyframes: list, keep_segments: list,
    enable_progress_bar: bool = True,
    enable_flash_intro: bool = True,
    enable_loop_fade: bool = True,
    styled_captions: list = None,
    hook_text: str = None,
    cta_text: str = None,
    position_y: float = 75,
    overlay_images: list = None,
    video_fps: int = 30,
    scene_change_times: list = None,
) -> list:
    """V2.10: Pillow overlay方式の高度なffmpegフィルタチェーンを構築する。

    フィルタ構成:
    1. 無音カット（select filter）
    2. ズームパルス（crop+scale）
    3. 最初0.3秒フラッシュ（eq brightness boost）
    4. 字幕・Hook・CTA: Pillow生成PNG + overlayフィルタ（drawtext/ASS廃止）
    5. 進行バー（drawbox）
    6. ループ感フェードアウト（fade filter）
    
    overlay_images: list of (png_path, start_time, end_time) from _generate_overlay_images()
    """
    overlay_images = overlay_images or []
    # Build video filter chain parts (effects only, no text rendering)
    vf_parts = []
    # ── 0. V2.23: 画面安定化（去闪烁のみ） ──
    # V2.23改訂: deshakeを削除。
    # 理由: deshakeはカメラの物理的手ぶれを補正するが、silence cutのjump cutには無効。
    #   さらに、deshakeがフレーム間の動きを「補正」しようとして画面が微妙にズレる副作用がある。
    #   V2.34.5: 切点zoom burstも廃止。切点は直接拼接する。
    # V2.34.4: deflickerフィルタを完全に削除
    # 理由: deflicker=size=5:mode=am はフレーム間の輝度平滑化を行うが、
    #   silence cut後のセグメント結合時に逆に輝度ジッターを引き起こす。
    #   また、zoom pulseのcrop+scaleと組み合わさるとフレーム間の微妙な明暗差が
    #   「画面の揺れ」として知覚される原因となっていた。
    #   安定した画面を優先するため、完全に無効化する。
    logger.info(f"[ai-clip] V2.34.4: Stabilization/deflicker DISABLED (causes visual jitter)")
    # ── 1. Silence trimming (select filter) ──
    # V2.28: 帧境界量子化 — keep_segmentsの全境界を正確なフレーム時刻にスナップ
    # 問題: silence検出の境界（例: 5.317s）がフレーム時刻（5.300s, 5.333s）と一致しない場合、
    #   select filterの半開区間で境界フレームが重複選択されたり、1フレーム回溯が発生する。
    # 修正: 全てのstart/endをフレーム時刻にスナップ（startは次のフレームに切り上げ、endは前のフレームに切り下げ）
    if len(keep_segments) > 1:
        frame_dur = 1.0 / video_fps
        quantized_segments = []
        for i, (seg_start, seg_end) in enumerate(keep_segments):
            if i == 0:
                # 最初のsegment: startはそのまま（動画の先頭）、endは切り下げ
                q_start = seg_start
            else:
                # startを次のフレーム境界に切り上げ（前のsegmentの最後のフレームと重複しない）
                q_start = math.ceil(seg_start / frame_dur) * frame_dur
            if i == len(keep_segments) - 1:
                # 最後のsegment: endはそのまま（動画の末尾）
                q_end = seg_end
            else:
                # endを前のフレーム境界に切り下げ
                q_end = math.floor(seg_end / frame_dur) * frame_dur
            # 量子化後にsegmentが有効か確認（最低1フレーム）
            if q_end - q_start >= frame_dur:
                quantized_segments.append((round(q_start, 4), round(q_end, 4)))
            else:
                # segmentが短すぎる場合はスキップ
                logger.warning(f"[ai-clip] V2.28: Skipping too-short segment after quantization: "
                               f"({seg_start:.3f}, {seg_end:.3f}) -> ({q_start:.3f}, {q_end:.3f})")
        if quantized_segments:
            keep_segments = quantized_segments
            logger.info(f"[ai-clip] V2.28: Frame-boundary quantization applied to {len(keep_segments)} segments "
                        f"(fps={video_fps}, frame_dur={frame_dur:.4f}s)")

    # V13: keep_segments > 20 の場合、隣接セグメントをマージして20以下にする
    if len(keep_segments) > 20:
        # 最も短いギャップからマージしてセグメント数を削減
        while len(keep_segments) > 20:
            # 隣接セグメント間のギャップを計算し、最小ギャップをマージ
            min_gap = float('inf')
            min_gap_idx = 0
            for i in range(len(keep_segments) - 1):
                gap = keep_segments[i + 1][0] - keep_segments[i][1]
                if gap < min_gap:
                    min_gap = gap
                    min_gap_idx = i
            # マージ: i番目とi+1番目を結合（間のギャップも含む）
            merged = (keep_segments[min_gap_idx][0], keep_segments[min_gap_idx + 1][1])
            keep_segments = keep_segments[:min_gap_idx] + [merged] + keep_segments[min_gap_idx + 2:]
        logger.info(f"[ai-clip] Merged keep_segments to {len(keep_segments)} (was >20)")
    use_silence_trim = len(keep_segments) > 1 and len(keep_segments) <= 20
    af_chain = None
    if use_silence_trim:
        # V2.22: 帧回溯/坏帧残留問題の根本修正
        # 問題: select+between(t,s,e)は包含境界（>=s AND <=e）のため、
        #   VFR入力や浮動小数点精度の問題で境界フレームが重複選択される可能性がある。
        #   また、入力がVFR（可変フレームレート）の場合、FRAME_RATE変数が不正確で
        #   setpts=N/FRAME_RATE/TBが正しいPTSを生成しない。
        # 修正:
        #   1. select前にfps=30で強制CFR化（VFR入力のPTS不連続を解消）
        #   2. select表達式で半開区間を使用（最初のsegmentのstartのみ>=、endは<次のsegmentのstart）
        #      これにより境界フレームの重複選択を完全に防止
        #   3. setpts=N/FRAME_RATE/TBでCFR出力を保証（fps=30後なのでFRAME_RATE=30で正確）
        
        # Step 1: 強制CFR化（VFR→実際の帧率で固定）
        # video_fpsはffprobeから取得した実際の帧率（デフォルト30）
        vf_parts.append(f"fps={video_fps}")
        
        # Step 2: 半開区間selectで境界フレーム重複を防止
        # 最初のsegment: between(t, start, end-epsilon)
        # 中間/最後: gte(t, start) * lt(t, end) ただし最後のsegmentのendは<=
        # V2.28: select式でフレーム境界量子化済みの値を使用（.4f精度）
        select_parts = []
        for i, (s, e) in enumerate(keep_segments):
            if i == len(keep_segments) - 1:
                # 最後のsegment: endを含む（動画の最後まで）
                select_parts.append(f"between(t,{s:.4f},{e:.4f})")
            else:
                # 中間segment: endを含まない（次のsegmentのstartとの重複を防止）
                # gte(t,s)*lt(t,e) = t>=s AND t<e
                select_parts.append(f"gte(t\\,{s:.4f})*lt(t\\,{e:.4f})")
        select_expr = "+".join(select_parts)
        vf_parts.append(f"select='{select_expr}'")
        # V2.21: 回退setpts統合 - 音視頻同期問題の根本修正
        vf_parts.append("setpts=N/FRAME_RATE/TB")
        
        # 音声も同様に半開区間で選択（フレーム境界量子化済みの値を使用）
        aselect_parts = []
        for i, (s, e) in enumerate(keep_segments):
            if i == len(keep_segments) - 1:
                aselect_parts.append(f"between(t,{s:.4f},{e:.4f})")
            else:
                aselect_parts.append(f"gte(t\\,{s:.4f})*lt(t\\,{e:.4f})")
        aselect_expr = "+".join(aselect_parts)
        af_chain = f"aselect='{aselect_expr}',asetpts=N/SR/TB"
        # V14.2: セグメント境界フェードを完全に無効化
        # 【根本原因】ffmpegのfadeフィルタをチェーンで使うと全フレームが黒になる:
        #   fade=t=out:st=S:d=D → S+D以降は完全に黒
        #   fade=t=in:st=S:d=D → S以前は完全に黒
        #   この2つをチェーンすると、fade=t=inがfade=t=outの出力（S以前は通常）を
        #   黒に上書きし、fade=t=outがfade=t=inの出力（S+D以降は通常）を黒に上書きする。
        #   結果: 全フレームが黒になる。
        # 【証明】テストで確認済み: fade=t=out:st=5:d=0.15,fade=t=in:st=5:d=0.15 → 全ピクセル=0
        # 【解決】セグメント境界フェードは視覚的効果が微小（0.15秒）であり、
        #   黒画面リスクに見合わないため完全に無効化する。
        #   カット境界のスムーズ化はselect+setptsによる自然な結合で十分。
        if len(keep_segments) >= 2:
            logger.info(f"[ai-clip] V14.2: Segment boundary fades DISABLED "
                        f"(segments={len(keep_segments)}, reason=ffmpeg fade chain causes black screen)")
        # V2.18: overlay_imagesとzoom_keyframesのタイミングをリマッピング
        # select+setpts後の出力時間軸に合わせる（字幕ズレ修正）
        overlay_images = _remap_overlay_images_for_silence_trim(overlay_images, keep_segments)
        zoom_keyframes = _remap_zoom_keyframes_for_silence_trim(zoom_keyframes, keep_segments)
        # V2.21: overlay/zoomのタイミング縮小は1bのsetptsで自動的に適用される
        # （speed_factorのsetpts=0.9524*PTSがoverlay後のPTSを縮小するため）
        duration = sum(e - s for s, e in keep_segments)
        # V2.34.5: 切点zoom burst完全廃止 — 切点は直接拼接（画面安定性最優先）
        # zoom burstもフラッシュも不要。切点はそのまま自然に繋げる。
        self_cut_boundaries = []
        logger.info(f"[ai-clip] V2.21 remap: {len(overlay_images)} overlays, "
                                        f"{len(zoom_keyframes)} zoom keyframes remapped to trimmed timeline "
                    f"(output_duration={duration:.1f}s)")
    else:
        # V2.34.5: 切点エフェクト廃止
        self_cut_boundaries = []
    # ── 1b. Speed factor (V13: TikTok風テンポアップ) ──
    speed_factor = getattr(req, 'speed_factor', 1.05)
    if speed_factor > 1.0:
        if use_silence_trim:
            # V2.21: silence trimありの場合も通常通りsetpts+atempoを追加
            # select+setpts=N/FRAME_RATE/TBで正常帧率に戻した後、速度変更を適用
            vf_parts.append(f"setpts={1.0/speed_factor:.4f}*PTS")
            af_chain = f"{af_chain},atempo={speed_factor:.4f}"
        else:
            # silence trimなし: 単純にsetpts + atempo
            vf_parts.append(f"setpts={1.0/speed_factor:.4f}*PTS")
            af_chain = f"atempo={speed_factor:.4f}"

    # ── 2. Zoom Pulse via crop+scale ──
    # V2.34.3: 切点zoom burstは短い持続時間(0.15秒)、通常zoomは0.4秒
    # 重要: crop filter中のtはsetpts=0.9524*PTS後のPTSなので、
    # zoom_keyframesの時間をspeed_factorで割る必要がある
    effective_speed = speed_factor if speed_factor > 1.0 else 1.0
    if zoom_keyframes:
        parts = []
        for t_orig, zf in zoom_keyframes:
            # speed_factor適用後の実際のPTS時刻に変換
            t = t_orig / effective_speed
            # V2.34.4: 全zoomを短いパルスに統一（0.25秒）
            # 強度が1.04-1.06と非常に微細なので、長いパルスは不要
            pulse_dur = 0.25 / effective_speed
            freq = math.pi / pulse_dur
            parts.append(
                f"if(between(t,{t:.3f},{t+pulse_dur:.3f}),"
                f"{zf:.3f}*sin((t-{t:.3f})*{freq:.4f})+"
                f"(1-sin((t-{t:.3f})*{freq:.4f})),"
            )
        zoom_expr = "1.0"
        for part in reversed(parts):
            zoom_expr = part + zoom_expr + ")"
        crop_f = (
            f"crop="
            f"'iw/({zoom_expr})':"
            f"'ih/({zoom_expr})':"
            f"'(iw-iw/({zoom_expr}))/2':"
            f"'(ih-ih/({zoom_expr}))/2'"
        )
        vf_parts.append(crop_f)
        vf_parts.append(f"scale={video_width}:{video_height}")
    # V2.34.5: 切点輝度フラッシュ廃止 — 切点は直接拼接、エフェクト不要
    # ── 3. Flash intro (multiple variations, softer than before) ──
    if enable_flash_intro:
        flash_variant = random.choice(["soft_flash", "warm_glow", "contrast_pop", "fade_in", "cool_flash"])
        if flash_variant == "soft_flash":
            # Gentle brightness boost (0.15 max, was 0.4 which caused white-out)
            flash_expr = "if(lt(t,0.25),0.15*(1-t/0.25),0)"
            vf_parts.append(f"eq=brightness='{flash_expr}':eval=frame")
        elif flash_variant == "warm_glow":
            # Warm saturation + slight brightness
            bright_expr = "if(lt(t,0.3),0.08*(1-t/0.3),0)"
            sat_expr = "if(lt(t,0.3),1.3-0.3*(t/0.3),1)"
            vf_parts.append(f"eq=brightness='{bright_expr}':saturation='{sat_expr}':eval=frame")
        elif flash_variant == "contrast_pop":
            # Quick contrast increase then normalize
            contrast_expr = "if(lt(t,0.2),1.3-0.3*(t/0.2),1)"
            vf_parts.append(f"eq=contrast='{contrast_expr}':eval=frame")
        elif flash_variant == "fade_in":
            # Fade from black (0.4s)
            vf_parts.append(f"fade=t=in:st=0:d=0.4")
        elif flash_variant == "cool_flash":
            # Slight desaturation + brightness for a cool look
            bright_expr = "if(lt(t,0.2),0.12*(1-t/0.2),0)"
            sat_expr = "if(lt(t,0.3),0.7+0.3*(t/0.3),1)"
            vf_parts.append(f"eq=brightness='{bright_expr}':saturation='{sat_expr}':eval=frame")

    # ── 4. Subtitles/Hook/CTA: Pillow overlay (NO drawtext/ASS) ──
    # Text rendering is handled by Pillow PNG images + ffmpeg overlay filter.
    # This completely bypasses libass and drawtext CJK rendering issues.
    # overlay_images are added as additional inputs to ffmpeg.

    # ── 5. Progress bar at bottom ──
    if enable_progress_bar:
        bar_height = 8
        bar_y = video_height - bar_height
        vf_parts.append(f"drawbox=x=0:y={bar_y}:w=iw:h={bar_height}:color=black@0.5:t=fill")
        bar_w_expr = f"t/{duration:.2f}*iw"
        vf_parts.append(f"drawbox=x=0:y={bar_y}:w='{bar_w_expr}':h={bar_height}:color=red@0.9:t=fill")

    # ── 6. Loop fade (DISABLED - causes black screen while speaker is still talking) ──
    # Previously: fade=t=out at the end of the video
    # Removed because it cuts off the speaker mid-sentence
    # if enable_loop_fade and duration > 5:
    #     fade_start = max(0, duration - 1.5)
    #     vf_parts.append(f"fade=t=out:st={fade_start:.2f}:d=1.5")

    # ── Assemble ffmpeg command ──
    # Strategy: Always use -filter_complex to combine video effects + overlay images.
    # This avoids the -vf mode which caused hangs with drawtext.
    
    # Build input arguments
    input_args = ["-i", video_path]
    for png_path, _, _ in overlay_images:
        input_args.extend(["-i", png_path])
    
    # Build filter_complex string
    fc_parts = []
    
    # Step A: Apply video effects to the main video
    video_chain = ",".join(vf_parts) if vf_parts else "null"
    
    if overlay_images:
        # Apply effects first, then chain overlays
        fc_parts.append(f"[0:v]{video_chain}[vfx]")
        
        # Step B: Chain overlay filters for each PNG image
        current_label = "[vfx]"
        for i, (png_path, start_t, end_t) in enumerate(overlay_images):
            input_idx = i + 1  # 0 is the video
            is_last = (i == len(overlay_images) - 1)
            out_label = "[vout]" if is_last else f"[v{i}]"
            # Use enable=between() to control when overlay is visible
            # Commas in between() must be escaped with \\ in filter_complex
            # V13: speed_factorでタイミング調整（速度変更後の時間軸に合わせる）
            adj_start = start_t / speed_factor if speed_factor > 1.0 else start_t
            adj_end = end_t / speed_factor if speed_factor > 1.0 else end_t
            enable_expr = f"between(t\\,{adj_start:.3f}\\,{adj_end:.3f})"
            fc_parts.append(
                f"{current_label}[{input_idx}:v]overlay=0:0:"
                f"enable='{enable_expr}':"
                f"format=auto{out_label}"
            )
            current_label = out_label
        
        # Step C: Audio chain (if silence trimming)
        if af_chain:
            fc_parts.append(f"[0:a]{af_chain}[aout]")
        
        fc_str = ";".join(fc_parts)
        
        cmd = [
            "ffmpeg", "-y",
            "-threads", "2",
            *input_args,
            "-filter_complex", fc_str,
            "-map", "[vout]",
            "-map", "[aout]" if af_chain else "0:a",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "23",
            # V2.21: 拼接点硬切モード（-vsync vfrで音視頻同期を保証） - 補帧/插值を完全に無効化
            # -bf 0: B-frame禁止（拼接点で前後フレームを参照しない）
            # -flags +cgop: Closed GOP（各GOPが独立、他のGOPを参照しない）
            # -sc_threshold 0: シーンチェンジ検出を無効化（拼接点で不要なキーフレーム挿入を防止）
            # -vsync drop: フレームの重複/ドロップを禁止（元フレームをそのまま出力）
            "-bf", "0",
            "-flags", "+cgop",
            "-sc_threshold", "0",
            "-vsync", "vfr",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            "-t", str(min(duration, getattr(req, 'max_duration', 90))),
            output_path,
        ]
    elif af_chain:
        # No overlays but has audio filter
        fc = f"[0:v]{video_chain}[vout];[0:a]{af_chain}[aout]"
        cmd = [
            "ffmpeg", "-y",
            "-threads", "2",
            *input_args,
            "-filter_complex", fc,
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "23",
            # V2.21: 拼接点硬切モード（-vsync vfrで音視頻同期を保証）
            "-bf", "0",
            "-flags", "+cgop",
            "-sc_threshold", "0",
            "-vsync", "vfr",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            output_path,
        ]
    else:
        # No overlays, no audio filter - simple -vf mode
        vf_str = ",".join(vf_parts) if vf_parts else "null"
        cmd = [
            "ffmpeg", "-y",
            "-threads", "2",
            *input_args,
            "-vf", vf_str,
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "23",
            # V2.21: 拼接点硬切モード（-vsync vfrで音視頻同期を保証）
            "-bf", "0",
            "-flags", "+cgop",
            "-sc_threshold", "0",
            "-vsync", "vfr",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            "-t", str(min(duration, getattr(req, 'max_duration', 90))),
            output_path,
        ]

    # Log the ffmpeg command for debugging
    logger.info(f"[ai-clip] ffmpeg cmd: {' '.join(cmd[:6])}... ({len(cmd)} args)")
    logger.info(f"[ai-clip] vf_parts={len(vf_parts)}, overlays={len(overlay_images)}, af={'yes' if af_chain else 'no'}")
    return cmd


# ─── V2: Enhanced ASS with Animations & Highlights ──────────────────────────
def _highlight_keywords(text_content: str, product_name: str = "",
                        product_keywords: list = None) -> str:
    """キーワードをASS override tagでハイライトする（V2.1強化版）
    - 商品名: 黄色+太字
    - CTAキーワード: 緑色+太字
    - 強調キーワード: 黄色
    - 商品マスターからの動的キーワードも対応
    """
    result = text_content
    # Build keyword list (product name first, then CTA, then emphasis)
    # Format: {keyword: (color, bold)}
    highlight_map = {}
    # 1. Product name keywords (highest priority - yellow + bold)
    if product_name:
        # Split by common separators
        parts = re.split(r'[\s　/・\-]+', product_name)
        for word in parts:
            if len(word) >= 2:
                highlight_map[word] = (_HIGHLIGHT_COLOR, True)
    # 2. Product master keywords (from DB)
    if product_keywords:
        for kw in product_keywords:
            if kw and len(kw) >= 2 and kw in result:
                highlight_map[kw] = (_HIGHLIGHT_COLOR, True)
    # 3. CTA keywords (green + bold)
    for kw in _CTA_KEYWORDS:
        if kw in result and kw not in highlight_map:
            highlight_map[kw] = (_HIGHLIGHT_CTA_COLOR, True)
    # 4. Emphasis keywords (yellow, no bold)
    for kw in _EMPHASIS_KEYWORDS:
        if kw in result and kw not in highlight_map:
            highlight_map[kw] = (_HIGHLIGHT_COLOR, False)
    # Apply highlights (ASS override tags) - longest keywords first to avoid partial matches
    for keyword, (color, bold) in sorted(highlight_map.items(), key=lambda x: len(x[0]), reverse=True):
        if keyword in result:
            if bold:
                result = result.replace(
                    keyword,
                    f"{{\\c{color}\\b1}}{keyword}{{\\b0\\c}}"
                )
            else:
                result = result.replace(
                    keyword,
                    f"{{\\c{color}}}{keyword}{{\\c}}"
                )
    return result


def _generate_cta_text(captions: list, clip: dict) -> str:
    """最後の3秒に表示するCTAテキストを生成
    V2.22: CTA話術を強導向性の行動喚起に変更
    - 目的: 観客を直接「左下角の商品リンク」へ誘導
    - 曖昧な結語（コメントで質問OK等）を排除
    - 「今すぐ購入」系の強いCTAのみ使用
    """
    product_name = clip.get("product_name") or ""

    # Try GPT-based CTA generation
    try:
        import openai
        azure_key = os.getenv("AZURE_OPENAI_KEY", "")
        azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
        azure_model = os.getenv("GPT5_MODEL") or os.getenv("GPT5_DEPLOYMENT") or "gpt-4.1-mini"

        if azure_key and azure_endpoint:
            from urllib.parse import urlparse as _urlparse
            _parsed = _urlparse(azure_endpoint)
            clean_endpoint = f"{_parsed.scheme}://{_parsed.netloc}/"

            client = openai.AzureOpenAI(
                api_key=azure_key,
                azure_endpoint=clean_endpoint,
                api_version=os.getenv("GPT5_API_VERSION", "2025-04-01-preview"),
            )

            transcript = " ".join(c.get("text", "") for c in (captions or []))[:300]
            prompt = f"""以下のTikTokライブコマース動画の最後に表示するCTA（行動喚起）テキストを1つ生成してください。

条件:
- 15文字以内
- 必ず「左下」「画面左下」「商品リンク」のいずれかの言葉を含めること
- 観客に今すぐ購入/クリックするよう強く促すこと（「抢购」「ゲット」「タップ」等の行動動詞を使う）
- 曖昧な表現（質問OK、フォロー、いいね等）は絶対に使わない
- 絵文字は使わない（フォント非対応のため）
- 商品名: {product_name}
- 動画内容: {transcript}

参考例:
- 「画面左下の商品リンクから抢购」
- 「左下タップで今すぐゲット」
- 「左下から今すぐ購入」

CTAテキストのみ出力（説明不要）:"""

            response = client.responses.create(
                model=azure_model,
                input=[{"role": "user", "content": prompt}],
                max_output_tokens=50,
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
                return result.strip('"\'「」『』').strip()[:20]

    except Exception as e:
        logger.warning(f"[ai-clip] CTA generation via GPT failed: {e}")

    # Fallback CTAs: V2.22 強導向性CTA（全て「左下」への購入誘導に統一）
    # 曖昧な結語（コメントで質問OK、フォロー、いいね等）を完全排除
    ctas = [
        "左下の商品リンクから抢购",
        "左下タップで今すぐゲット",
        "画面左下から今すぐ購入",
        "左下から抢购 数量限定",
        "左下のリンクをタップ",
        "左下から今すぐゲット",
        "画面左下タップで購入",
        "左下リンクから即購入",
    ]
    return random.choice(ctas)


# ─── V2.33: 卖点关键词动态覆盖系统 ────────────────────────────────────────────

async def _generate_selling_points(captions: list, product_name: str,
                                    product_keywords: list = None) -> list:
    """V2.33: GPTで商品の核心卖点フレーズを生成する。

    产品展示段落に同期して画面上方に表示する卖点キーワードを生成。
    各卖点は強制単行・15文字以内。

    Returns:
        list of {"text": str, "start": float, "end": float} — 卖点とその表示タイミング
    """
    import openai

    if not captions or not product_name:
        return []

    azure_key = os.getenv("AZURE_OPENAI_KEY", "")
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    azure_model = os.getenv("GPT5_MODEL") or os.getenv("GPT5_DEPLOYMENT") or "gpt-4.1-mini"

    if not azure_key or not azure_endpoint:
        # GPT使えない場合はフォールバック卖点を生成
        return _fallback_selling_points(captions, product_name, product_keywords)

    from urllib.parse import urlparse as _urlparse
    _parsed = _urlparse(azure_endpoint)
    clean_endpoint = f"{_parsed.scheme}://{_parsed.netloc}/"

    client = openai.AsyncAzureOpenAI(
        api_key=azure_key,
        azure_endpoint=clean_endpoint,
        api_version=os.getenv("GPT5_API_VERSION", "2025-04-01-preview"),
    )

    # 字幕テキストを要約
    transcript = " ".join(c.get("text", "") for c in captions)[:500]
    keywords_str = "、".join(product_keywords[:10]) if product_keywords else "なし"

    prompt = f"""以下はライブコマース動画の字幕テキストです。商品の核心卖点を抽出してください。

商品名: {product_name}
商品キーワード: {keywords_str}
字幕内容: {transcript}

【タスク】
この商品の核心的な卖点・訴求ポイントを3〜5個生成してください。

【ルール】
- 各卖点は15文字以内（厳守）
- 単行表示（改行禁止）
- 商品の特徴・効果・成分・差別化ポイントを端的に表現
- 広告コピーのように簡潔でインパクトのある表現
- 絵文字禁止
- 日本語で出力

【出力形式】
JSON配列で出力。各要素は卖点テキストのみ。
例: ["超低刺激処方", "サロン級の仕上がり", "48時間持続保湿"]

卖点のみ出力（説明不要）:"""

    try:
        response = await client.chat.completions.create(
            model=azure_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.3,
        )

        result_text = response.choices[0].message.content.strip()
        if result_text.startswith("```"):
            result_text = result_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        selling_points = json.loads(result_text)
        if not isinstance(selling_points, list):
            selling_points = []

        # 15文字制限を強制
        selling_points = [sp.strip()[:15] for sp in selling_points if sp and sp.strip()]
        # 最大5個
        selling_points = selling_points[:5]

        if selling_points:
            logger.info(f"[ai-clip] V2.33 Generated {len(selling_points)} selling points: {selling_points}")

        return selling_points

    except Exception as e:
        logger.warning(f"[ai-clip] V2.33 Selling point generation failed: {e}")
        return _fallback_selling_points(captions, product_name, product_keywords)


def _fallback_selling_points(captions: list, product_name: str,
                              product_keywords: list = None) -> list:
    """V2.33: GPT使用不可時のフォールバック卖点生成。
    product_keywordsまたは字幕内容からキーワードを抽出。
    """
    points = []

    # product_keywordsがあればそれを使用
    if product_keywords:
        for kw in product_keywords[:5]:
            if kw and len(kw) <= 15:
                points.append(kw)

    # 足りなければ字幕から特徴的なフレーズを抽出
    if len(points) < 3 and captions:
        import re
        feature_patterns = [
            r'(\S{2,8}(?:配合|処方|成分|効果|仕上がり|ケア|対応|設計|構造))',
            r'((?:超|高|低|無)\S{2,6})',
            r'(サロン級\S{0,6})',
            r'(プロ(?:仕様|級|用)\S{0,6})',
        ]
        full_text = " ".join(c.get("text", "") for c in captions)
        for pattern in feature_patterns:
            for m in re.finditer(pattern, full_text):
                candidate = m.group(1).strip()[:15]
                if candidate and candidate not in points and len(candidate) >= 3:
                    points.append(candidate)
                    if len(points) >= 5:
                        break
            if len(points) >= 5:
                break

    return points[:5]


def _assign_selling_point_timings(selling_points: list, captions: list,
                                   duration: float) -> list:
    """V2.33: 卖点フレーズに表示タイミングを割り当てる。

    ロジック:
    - 動画全体を均等に分割し、各卖点を順番に表示
    - 各卖点の表示時間: 3〜5秒
    - フック(最初3秒)とCTA(最後3.5秒)の区間は避ける

    Returns:
        list of {"text": str, "start": float, "end": float}
    """
    if not selling_points or duration <= 0:
        return []

    # 卖点表示可能区間: フック後〜CTA前
    sp_start_time = 3.5  # フック終了後
    sp_end_time = max(sp_start_time + 3, duration - 4.0)  # CTA開始前
    available_duration = sp_end_time - sp_start_time

    if available_duration < 3.0:
        return []  # 表示区間が短すぎる

    n = len(selling_points)
    # 各卖点の表示時間を計算（均等分割、最大5秒、最小3秒）
    per_sp_duration = min(5.0, max(3.0, available_duration / n))
    # 卖点間のギャップ
    total_needed = per_sp_duration * n
    gap = max(0.5, (available_duration - total_needed) / max(1, n))

    timed_points = []
    current_time = sp_start_time

    for sp_text in selling_points:
        if current_time + per_sp_duration > sp_end_time + 1.0:
            break  # 時間切れ
        sp_end = min(current_time + per_sp_duration, sp_end_time)
        timed_points.append({
            "text": sp_text,
            "start": current_time,
            "end": sp_end,
        })
        current_time = sp_end + gap

    logger.info(f"[ai-clip] V2.33 Assigned timings to {len(timed_points)} selling points "
                f"(range: {sp_start_time:.1f}s - {sp_end_time:.1f}s)")
    return timed_points


def _generate_enhanced_ass(styled_captions: list, hook_text: Optional[str],
                            cta_text: Optional[str], ass_path: str,
                            video_width: int, video_height: int,
                            duration: float, position_y: float,
                            product_name: str = "",
                            enable_animations: bool = True,
                            enable_highlights: bool = True):
    """V2: アニメーション・ハイライト付きASS字幕ファイルを生成する"""
    if position_y < 33:
        alignment = 8
        margin_v = max(20, int(video_height * position_y / 100))
    elif position_y < 66:
        alignment = 5
        margin_v = 30
    else:
        alignment = 2
        margin_v = max(20, int(video_height * (100 - position_y) / 100))

    base_width = 1080
    scale_factor = min(video_width, video_height) / base_width

    # Detect the actual CJK font name available on the system
    cjk_font_name = _detect_cjk_font_name()
    logger.info(f"[ai-clip] ASS using font: {cjk_font_name}")

    ass = "[Script Info]\n"
    ass += "ScriptType: v4.00+\n"
    ass += f"PlayResX: {video_width}\n"
    ass += f"PlayResY: {video_height}\n"
    ass += "WrapStyle: 0\n\n"

    ass += "[V4+ Styles]\n"
    ass += "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"

    for style_name, s in _ASS_STYLES.items():
        fontsize = max(40, int(s['fontsize'] * scale_factor))
        outline_val = max(2, int(s['outline'] * scale_factor))
        shadow_val = max(0, int(s.get('shadow', 0) * scale_factor))
        secondary = s.get('secondary_color', '&H0000FFFF')
        ass += (f"Style: {style_name},{cjk_font_name},{fontsize},{s['primary_color']},{secondary},"
                f"{s['outline_color']},{s['back_color']},{s['bold']},0,0,0,100,100,2,0,"
                f"{s['border_style']},{outline_val},{shadow_val},{alignment},"
                f"40,40,{margin_v},1\n")

    # Hook style
    # Pick a random hook style for variety
    _hook_style = _pick_hook_style()
    hook_fontsize = max(60, int(_hook_style['fontsize'] * scale_factor))
    hook_outline = max(3, int(_hook_style['outline'] * scale_factor))
    hook_shadow = max(0, int(_hook_style.get('shadow', 0) * scale_factor))
    ass += (f"Style: hook,{cjk_font_name},{hook_fontsize},{_hook_style['primary_color']},&H0000FFFF,"
            f"{_hook_style['outline_color']},{_hook_style['back_color']},{_hook_style['bold']},0,0,0,100,100,2,0,"
            f"{_hook_style['border_style']},{hook_outline},{hook_shadow},8,"
            f"40,40,100,1\n")

    # CTA style
    cta_fontsize = max(50, int(_CTA_STYLE['fontsize'] * scale_factor))
    cta_outline = max(3, int(_CTA_STYLE['outline'] * scale_factor))
    cta_shadow = max(0, int(_CTA_STYLE['shadow'] * scale_factor))
    ass += (f"Style: cta,{cjk_font_name},{cta_fontsize},{_CTA_STYLE['primary_color']},&H0000FFFF,"
            f"{_CTA_STYLE['outline_color']},{_CTA_STYLE['back_color']},{_CTA_STYLE['bold']},0,0,0,100,100,2,0,"
            f"{_CTA_STYLE['border_style']},{cta_outline},{cta_shadow},8,"
            f"40,40,200,1\n")

    # Default style (safety net for any unresolved style references)
    default_fontsize = max(40, int(80 * scale_factor))
    default_outline = max(2, int(5 * scale_factor))
    ass += (f"Style: Default,{cjk_font_name},{default_fontsize},&H00FFFFFF,&H0000FFFF,"
            f"&H00000000,&H80000000,1,0,0,0,100,100,2,0,"
            f"1,{default_outline},3,{alignment},"
            f"40,40,{margin_v},1\n")

    ass += "\n[Events]\n"
    ass += "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"

    # ── Hook text (first 3 seconds) with animation ──
    if hook_text:
        hook_start = _seconds_to_ass_time(0)
        hook_end = _seconds_to_ass_time(3.0)
        safe_hook = _strip_emoji(hook_text).replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
        if enable_animations:
            # Fade in 200ms, fade out 300ms + scale animation
            anim_tag = "{\\fad(200,300)\\fscx120\\fscy120\\t(0,200,\\fscx100\\fscy100)}"
            ass += f"Dialogue: 1,{hook_start},{hook_end},hook,,0,0,0,,{anim_tag}{safe_hook}\n"
        else:
            ass += f"Dialogue: 1,{hook_start},{hook_end},hook,,0,0,0,,{safe_hook}\n"

    # ── Styled captions with animations and highlights ──
    # V2.31: 字幕テキスト前処理ヘルパー
    import re as _re_sub
    _TRAILING_PUNCT = _re_sub.compile(r'[、。！？!?.,，．…]+$')  # 末尾標点除去
    _SPLIT_PUNCT = _re_sub.compile(r'([、。！？!?，])(?!$)')  # 文中標点で分割（末尾以外）
    _PUNCT_CHARS = set('、。！？!?，')  # 標点文字セット（分割結果の判定用）
    _MAX_LINE_CHARS = 15  # 1行あたりの最大文字数（超えたら分割を試みる）

    def _process_subtitle_text(text: str) -> str:
        """V2.31: 字幕テキストの標点処理
        1. 末尾標点を除去
        2. 長い文は標点位置でASS改行に分割
        """
        # Step 1: 末尾標点除去
        text = _TRAILING_PUNCT.sub('', text)
        if not text:
            return text
        # Step 2: 長い文は標点で分割して改行
        if len(text) > _MAX_LINE_CHARS:
            # 標点位置でチャンクに分割（標点は前のチャンクに含める）
            parts = _SPLIT_PUNCT.split(text)
            if len(parts) >= 3:  # regex split with capture group: [text, punct, text, ...]
                # チャンクを再構成: 標点をスキップしてテキストのみ取得
                chunks = []
                for i_p, part in enumerate(parts):
                    if len(part) == 1 and part in _PUNCT_CHARS:
                        # これは標点文字 — スキップ
                        continue
                    if part:  # 空文字列もスキップ
                        chunks.append(part)
                # チャンクを行にまとめる
                lines = []
                current_line = ''
                for chunk in chunks:
                    if not chunk:
                        continue
                    if current_line and len(current_line) + len(chunk) > _MAX_LINE_CHARS:
                        lines.append(current_line)
                        current_line = chunk
                    else:
                        current_line += chunk
                if current_line:
                    lines.append(current_line)
                if len(lines) >= 2:
                    # 各行の末尾標点も除去
                    lines = [_TRAILING_PUNCT.sub('', l) for l in lines]
                    text = '\\N'.join(l for l in lines if l)
        return text

    MIN_DISPLAY = 2.5
    for i, cap in enumerate(styled_captions):
        cap_start = float(cap.get("start", 0))
        cap_end = float(cap.get("end", 0))
        cap_text = _strip_emoji(cap.get("text", "").strip())
        style = cap.get("style", "box")
        if not cap_text:
            continue
        # V2.31: 標点処理（末尾除去 + 長文分行）
        cap_text = _process_subtitle_text(cap_text)
        if cap_end <= cap_start:
            cap_end = cap_start + MIN_DISPLAY
        if cap_end - cap_start < MIN_DISPLAY:
            cap_end = cap_start + MIN_DISPLAY
            if i + 1 < len(styled_captions):
                next_start = float(styled_captions[i + 1].get("start", 0))
                if next_start > cap_start:
                    cap_end = min(cap_end, next_start)

        start_ts = _seconds_to_ass_time(cap_start)
        end_ts = _seconds_to_ass_time(cap_end)

        # Apply keyword highlights
        if enable_highlights:
            cap_text = _highlight_keywords(cap_text, product_name)

        safe_text = cap_text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
        # But preserve our highlight tags (they use { } which we just escaped)
        # We need to un-escape our intentional override tags
        safe_text = cap_text  # Use original with highlights intact

        # Escape only non-override braces
        import re
        # Split by our override tags, escape the rest
        parts = re.split(r'(\{\\[^}]+\})', safe_text)
        final_text = ""
        for part in parts:
            if part.startswith("{\\") and part.endswith("}"):
                final_text += part  # Keep override tags
            else:
                final_text += part.replace("\\", "\\\\")

        if enable_animations:
            # Fade in 150ms, fade out 100ms
            anim_prefix = "{\\fad(150,100)}"
            ass += f"Dialogue: 0,{start_ts},{end_ts},{style},,0,0,0,,{anim_prefix}{final_text}\n"
        else:
            ass += f"Dialogue: 0,{start_ts},{end_ts},{style},,0,0,0,,{final_text}\n"

    # ── CTA text (last 3 seconds) with animation ──
    if cta_text and duration > 5:
        cta_start = _seconds_to_ass_time(max(0, duration - 3.5))
        cta_end = _seconds_to_ass_time(duration - 0.3)
        safe_cta = _strip_emoji(cta_text).replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
        if enable_animations:
            cta_anim = "{\\fad(300,200)\\fscx80\\fscy80\\t(0,300,\\fscx100\\fscy100)}"
            ass += f"Dialogue: 2,{cta_start},{cta_end},cta,,0,0,0,,{cta_anim}{safe_cta}\n"
        else:
            ass += f"Dialogue: 2,{cta_start},{cta_end},cta,,0,0,0,,{safe_cta}\n"

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass)

    caption_count = len(styled_captions)
    logger.info(f"[ai-clip] Generated enhanced ASS: {caption_count} captions, "
                f"hook={'yes' if hook_text else 'no'}, cta={'yes' if cta_text else 'no'}, "
                f"animations={enable_animations}, highlights={enable_highlights}")


# ─── V2: Enhanced Thumbnail with Text Overlay ────────────────────────────────
async def _generate_enhanced_thumbnail(video_path: str, tmp_dir: str, clip_id: str,
                                        hook_text: str = "", product_name: str = "") -> Optional[str]:
    """V2: テキスト入り＋背景ぼかしサムネイルを生成"""
    thumbnail_path = os.path.join(tmp_dir, "thumbnail.jpg")
    thumb_text = hook_text or product_name or ""

    if thumb_text:
        # Generate thumbnail with text overlay using ffmpeg
        font_path = _find_cjk_font()
        # Escape for ffmpeg drawtext
        safe_text = thumb_text.replace("'", "\\'").replace(":", "\\:")
        safe_font = font_path.replace("'", "\\'").replace(":", "\\:")

        # Complex filter: extract frame, add blur background + sharp foreground + text
        vf_parts = [
            # Split into two: blurred bg and sharp fg
            "split[bg][fg]",
        ]
        vf_bg = "[bg]boxblur=20:5[blurred]"
        vf_fg = "[fg]crop=iw*0.9:ih*0.9:iw*0.05:ih*0.05,scale=iw*0.95:-1[sharp]"
        vf_overlay = f"[blurred][sharp]overlay=(W-w)/2:(H-h)/2"
        vf_text = (
            f"drawtext=fontfile='{safe_font}':"
            f"text='{safe_text}':"
            f"fontsize=60:fontcolor=white:borderw=4:bordercolor=black:"
            f"x=(w-text_w)/2:y=h*0.1"
        )

        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-ss", "1.5", "-vframes", "1",
            "-vf", f"{vf_overlay},{vf_text}",
            "-filter_complex", f"{vf_bg};{vf_fg}",
            "-q:v", "2",
            thumbnail_path,
        ]

        # Simpler approach: just frame + text overlay (more reliable)
        safe_text_simple = thumb_text.replace("'", "").replace(":", " ")
        cmd_simple = [
            "ffmpeg", "-y", "-i", video_path,
            "-ss", "1.5", "-vframes", "1",
            "-vf", (
                f"scale=720:-1,"
                f"drawtext=fontfile='{safe_font}':"
                f"text='{safe_text_simple}':"
                f"fontsize=48:fontcolor=white:borderw=3:bordercolor=black:"
                f"x=(w-text_w)/2:y=h*0.08"
            ),
            "-q:v", "2",
            thumbnail_path,
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd_simple, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await asyncio.wait_for(proc.communicate(), timeout=30)

        if proc.returncode != 0 or not os.path.exists(thumbnail_path):
            logger.warning(f"[ai-clip] Enhanced thumbnail failed, falling back to simple")
            # Fallback to simple frame extraction
            cmd_fallback = [
                "ffmpeg", "-y", "-i", video_path,
                "-ss", "1.5", "-vframes", "1",
                "-vf", "scale=720:-1",
                "-q:v", "2",
                thumbnail_path,
            ]
            proc2 = await asyncio.create_subprocess_exec(
                *cmd_fallback, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            await asyncio.wait_for(proc2.communicate(), timeout=30)
    else:
        # No text, just extract frame
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-ss", "1.5", "-vframes", "1",
            "-vf", "scale=720:-1",
            "-q:v", "2",
            thumbnail_path,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await asyncio.wait_for(proc.communicate(), timeout=30)

    if not os.path.exists(thumbnail_path):
        return None

    # Upload to blob
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

        try:
            sas_url = generate_read_sas_from_url(blob_url, expires_hours=720)
            if sas_url:
                return sas_url
        except Exception as sas_err:
            logger.warning(f"[ai-clip] Thumbnail SAS failed: {sas_err}")

        cdn_host = os.getenv("CDN_HOST", "https://cdn.aitherhub.com")
        blob_host = f"https://{ACCOUNT_NAME}.blob.core.windows.net"
        if cdn_host and blob_host in blob_url:
            return blob_url.replace(blob_host, cdn_host)
        return blob_url

    except Exception as e:
        logger.warning(f"[ai-clip] Thumbnail upload failed: {e}")
        return None


# ─── Pydantic Models ─────────────────────────────────────────────────────────
class GenerateRequest(BaseModel):
    """全自動AIクリップ生成リクエスト V2"""
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
    max_duration: float = Field(90.0, le=180.0, description="最大クリップ長（秒）V10: 45-90秒推奨")
    min_cta_score: int = Field(0, ge=0, le=5, description="最小CTAスコア")
    min_importance: float = Field(0.0, ge=0.0, description="最小重要度スコア")
    target_language: str = Field("auto", description="字幕言語 (auto/ja/zh/zh-tw)")
    position_y: float = Field(75.0, ge=0, le=100, description="字幕Y位置（%）")
    # V2 new options
    enable_silence_cut: bool = Field(True, description="無音区間を自動カットするか")
    enable_content_cut: bool = Field(True, description="商品無関係・フィラー区間を自動カットするか（GPT判定）")
    enable_zoom_pulse: bool = Field(True, description="ズームパルスを有効にするか")
    enable_progress_bar: bool = Field(True, description="進行バーを表示するか")
    enable_flash_intro: bool = Field(True, description="最初0.5秒のフラッシュ演出")
    enable_loop_fade: bool = Field(True, description="ループ感フェードアウト")
    enable_cta: bool = Field(True, description="最後3秒にCTAテキストを表示")
    enable_keyword_highlight: bool = Field(True, description="キーワードハイライト")
    enable_subtitle_animation: bool = Field(True, description="字幕出現アニメーション")
    zoom_intensity: float = Field(1.08, ge=1.0, le=1.3, description="ズーム倍率 (1.0=なし, 1.3=最大)")
    silence_threshold_db: float = Field(-22.0, ge=-60.0, le=-10.0, description="無音検出閾値(dB) V2.18: -25→-22に変更してより積極的に検出")
    # V13: テンポアップ（TikTok風の微妙なスピードアップ）
    speed_factor: float = Field(1.05, ge=1.0, le=1.3, description="再生速度倍率 (1.0=変更なし, 1.05=TikTok風微速, 1.3=最大)")
    # V12: 編集スタイル学習
    editing_profile_id: Optional[str] = Field(None, description="編集プロファイルID（スタイル学習済みプロファイルを適用）")
    # V2.22: 画面安定化（防抖・去闪烁）
    enable_stabilization: bool = Field(True, description="画面安定化（防抖+去闪烁）を有効にするか")
    # V2.35: 言語翻訳+リップシンク（主播の声で他言語に翻訳）
    output_language: str = Field("original", description="出力言語: original=元のまま, zh=中国語簡体, zh-tw=中国語繁体, en=英語, ko=韓国語")
    voice_id: Optional[str] = Field(None, description="ElevenLabs音声ID（翻訳時に使用、省略時はデフォルト音声）")


class GenerateFromClipRequest(BaseModel):
    """特定クリップIDを指定してAIクリップ生成するリクエスト"""
    clip_id: str = Field(..., description="対象のクリップID (video_clips.id)")
    subtitle_style: str = Field("auto", description="字幕スタイル (auto/simple/box/outline/pop/gradient/karaoke)")
    enable_sfx: bool = Field(True, description="効果音を自動挿入するか")
    enable_transitions: bool = Field(True, description="トランジションを追加するか")
    transition_type: str = Field("fade", description="トランジション種類")
    transition_duration: float = Field(0.5, ge=0.1, le=2.0, description="トランジション時間（秒）")
    enable_hook: bool = Field(True, description="最初3秒のフックテキストを生成するか")
    hook_text: Optional[str] = Field(None, description="カスタムフックテキスト（空の場合はAI生成）")
    enable_thumbnail: bool = Field(True, description="サムネイルを自動生成するか")
    target_language: str = Field("auto", description="字幕言語 (auto/ja/zh/zh-tw)")
    position_y: float = Field(75.0, ge=0, le=100, description="字幕Y位置（%）")
    enable_silence_cut: bool = Field(True, description="無音区間を自動カットするか")
    enable_content_cut: bool = Field(True, description="商品無関係・フィラー区間を自動カットするか（GPT判定）")
    enable_zoom_pulse: bool = Field(True, description="ズームパルスを有効にするか")
    enable_progress_bar: bool = Field(True, description="進行バーを表示するか")
    enable_flash_intro: bool = Field(True, description="最初0.5秒のフラッシュ演出")
    enable_loop_fade: bool = Field(True, description="ループ感フェードアウト")
    enable_cta: bool = Field(True, description="最後3秒にCTAテキストを表示")
    enable_keyword_highlight: bool = Field(True, description="キーワードハイライト")
    enable_subtitle_animation: bool = Field(True, description="字幕出現アニメーション")
    zoom_intensity: float = Field(1.08, ge=1.0, le=1.3, description="ズーム倍率")
    silence_threshold_db: float = Field(-22.0, ge=-60.0, le=-10.0, description="無音検出閾値(dB) V2.18: -25→-22に変更してより積極的に検出")
    # V13: テンポアップ
    speed_factor: float = Field(1.05, ge=1.0, le=1.3, description="再生速度倍率 (1.0=変更なし, 1.05=TikTok風微速, 1.3=最大)")
    # V3: 映像モード選択
    video_mode: str = Field("original", description="映像モード: original=そのまま, product_overlay=商品画像オーバーレイ, audio_only=音声+商品スライドショー")
    product_image_urls: Optional[list] = Field(None, description="商品画像URLリスト（video_mode=product_overlay/audio_onlyの場合に使用）")
    product_video_urls: Optional[list] = Field(None, description="商品動甾URLリスト（PiPで動甾をオーバーレイ表示する場合に使用）")
    # V12: 編集スタイル学習
    editing_profile_id: Optional[str] = Field(None, description="編集プロファイルID（スタイル学習済みプロファイルを適用）")
    # V2.22: 画面安定化（防抖・去闪烁）
    enable_stabilization: bool = Field(True, description="画面安定化（防抖+去闪烁）を有効にするか")
    # V2.35: 言語翻訳+リップシンク
    output_language: str = Field("original", description="出力言語: original=元のまま, zh=中国語簡体, zh-tw=中国語繁体, en=英語, ko=韓国語")
    voice_id: Optional[str] = Field(None, description="ElevenLabs音声ID（翻訳時に使用、省略時はデフォルト音声）")

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
    verify_admin(x_admin_key)
    async with get_session() as session:
        result = await session.execute(text("""
            SELECT wc.client_id, wc.client_name, wc.brand_name,
                   COUNT(wca.clip_id) as clip_count
            FROM widget_clients wc
            LEFT JOIN widget_clip_assignments wca ON wca.client_id = wc.client_id AND wca.is_active = TRUE
            GROUP BY wc.client_id, wc.client_name, wc.brand_name
            ORDER BY clip_count DESC
        """))
        rows = result.fetchall()
    return {
        "brands": [
            {"client_id": r.client_id, "client_name": r.client_name,
             "brand_name": r.brand_name, "clip_count": r.clip_count}
            for r in rows
        ]
    }


@router.get("/candidates")
async def list_candidates(
    brand_id: Optional[str] = Query(None),
    min_duration: float = Query(5.0),
    max_duration: float = Query(180.0),
    min_cta_score: int = Query(0),
    limit: int = Query(20, ge=1, le=100),
    x_admin_key: Optional[str] = Header(None),
):
    verify_admin(x_admin_key)
    conditions = [
        "vc.status = 'completed'",
        "vc.clip_url IS NOT NULL",
        "COALESCE(vc.is_unusable, FALSE) = FALSE",
    ]
    params: dict = {"limit": limit}

    if brand_id:
        conditions.append("""
            vc.id::text IN (
                SELECT wca.clip_id FROM widget_clip_assignments wca
                WHERE wca.client_id = :brand_id AND wca.is_active = TRUE
            )
        """)
        params["brand_id"] = brand_id
    else:
        conditions.append("""
            vc.id::text IN (
                SELECT wca.clip_id FROM widget_clip_assignments wca
                WHERE wca.is_active = TRUE
            )
        """)

    if min_duration > 0:
        conditions.append("COALESCE(vc.duration_sec, 0) >= :min_dur")
        params["min_dur"] = min_duration
    if max_duration > 0:
        conditions.append("COALESCE(vc.duration_sec, 0) <= :max_dur")
        params["max_dur"] = max_duration
    if min_cta_score > 0:
        conditions.append("COALESCE(vc.cta_score, 0) >= :min_cta")
        params["min_cta"] = min_cta_score

    where_clause = " AND ".join(conditions)

    async with get_session() as session:
        result = await session.execute(text(f"""
            SELECT vc.id as clip_id, vc.video_id, vc.duration_sec,
                   vc.transcript_text, vc.product_name, vc.cta_score,
                   vc.importance_score, vc.thumbnail_url, vc.liver_name,
                   vc.captions IS NOT NULL as has_captions
            FROM video_clips vc
            WHERE {where_clause}
            ORDER BY COALESCE(vc.importance_score, 0) DESC,
                     COALESCE(vc.cta_score, 0) DESC
            LIMIT :limit
        """), params)
        rows = result.fetchall()

    return {
        "candidates": [
            {
                "clip_id": str(r.clip_id), "video_id": str(r.video_id),
                "duration_sec": r.duration_sec, "transcript_text": r.transcript_text,
                "product_name": r.product_name, "cta_score": r.cta_score,
                "importance_score": r.importance_score, "thumbnail_url": r.thumbnail_url,
                "liver_name": r.liver_name, "has_captions": r.has_captions,
            }
            for r in rows
        ],
        "total": len(rows),
    }


@router.get("/templates")
async def list_templates(x_admin_key: Optional[str] = Header(None)):
    verify_admin(x_admin_key)
    return {
        "templates": [
            {
                "id": "sales_highlight",
                "name": "セールスハイライト",
                "description": "CTAスコアが高いクリップを優先。フック＋字幕＋CTA付き。",
                "config": {
                    "min_cta_score": 3, "subtitle_style": "auto",
                    "enable_hook": True, "enable_cta": True,
                    "enable_zoom_pulse": True, "enable_silence_cut": True,
                    "enable_progress_bar": True, "enable_flash_intro": True,
                },
            },
            {
                "id": "product_intro",
                "name": "商品紹介",
                "description": "商品名が含まれるクリップを優先。box字幕で読みやすく。",
                "config": {
                    "subtitle_style": "box", "enable_hook": True,
                    "enable_cta": True, "enable_zoom_pulse": True,
                    "enable_silence_cut": True, "enable_progress_bar": True,
                },
            },
            {
                "id": "viral_short",
                "name": "バズ狙いショート",
                "description": "15秒以内の短尺。pop字幕＋フック＋全エフェクトON。",
                "config": {
                    "max_duration": 15, "subtitle_style": "pop",
                    "enable_hook": True, "enable_cta": True,
                    "enable_zoom_pulse": True, "enable_silence_cut": True,
                    "enable_progress_bar": True, "enable_flash_intro": True,
                    "enable_loop_fade": True, "zoom_intensity": 1.12,
                },
            },
            {
                "id": "minimal",
                "name": "ミニマル",
                "description": "エフェクト最小限。字幕のみのシンプルな仕上がり。",
                "config": {
                    "subtitle_style": "simple", "enable_hook": False,
                    "enable_cta": False, "enable_zoom_pulse": False,
                    "enable_silence_cut": False, "enable_progress_bar": False,
                    "enable_flash_intro": False, "enable_loop_fade": False,
                },
            },
            {
                "id": "full_production",
                "name": "フルプロダクション",
                "description": "全エフェクトON。最高品質の仕上がり。",
                "config": {
                    "subtitle_style": "auto", "enable_hook": True,
                    "enable_cta": True, "enable_zoom_pulse": True,
                    "enable_silence_cut": True, "enable_progress_bar": True,
                    "enable_flash_intro": True, "enable_loop_fade": True,
                    "enable_keyword_highlight": True, "enable_subtitle_animation": True,
                    "zoom_intensity": 1.08,
                },
            },
        ]
    }


@router.get("/diagnostics")
async def diagnostics(x_admin_key: Optional[str] = Header(None)):
    verify_admin(x_admin_key)
    azure_key = os.getenv("AZURE_OPENAI_KEY", "")
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    gpt_model = os.getenv("GPT5_MODEL") or os.getenv("GPT5_DEPLOYMENT") or "gpt-4.1-mini"
    font_found = _find_cjk_font()
    try:
        ffmpeg_ok = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5).returncode == 0
        ffprobe_ok = subprocess.run(["ffprobe", "-version"], capture_output=True, timeout=5).returncode == 0
    except Exception:
        ffmpeg_ok = False
        ffprobe_ok = False
    # DB table check with test write
    db_status = {"table_exists": False, "job_count": 0, "error": None, "last_save_error": _LAST_DB_SAVE_ERROR, "test_write": None}
    try:
        await _ensure_jobs_table()
        # Test write: insert a test job and then delete it
        test_id = f"diag-test-{uuid.uuid4().hex[:8]}"
        async with engine.begin() as conn:
            await conn.execute(text("""
                INSERT INTO ai_clip_jobs (job_id, status, progress_pct, current_step, clips_completed, clips_total, results, config, created_at, updated_at)
                VALUES (:job_id, 'test', 0, 'diagnostic test', 0, 0, '[]'::jsonb, '{}'::jsonb, NOW(), NOW())
            """), {"job_id": test_id})
        # Verify the test write
        async with engine.connect() as conn:
            result = await conn.execute(text(
                "SELECT COUNT(*) as cnt FROM ai_clip_jobs WHERE job_id = :job_id"
            ), {"job_id": test_id})
            row = result.fetchone()
            db_status["test_write"] = "OK" if (row and row.cnt > 0) else "FAILED (not found after insert)"
        # Clean up test row
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM ai_clip_jobs WHERE job_id = :job_id"), {"job_id": test_id})
        # Count actual jobs
        async with engine.connect() as conn:
            result = await conn.execute(text(
                "SELECT COUNT(*) as cnt FROM ai_clip_jobs"
            ))
            row = result.fetchone()
            db_status["table_exists"] = True
            db_status["job_count"] = row.cnt if row else 0
    except Exception as e:
        import traceback
        db_status["error"] = f"{type(e).__name__}: {str(e)[:200]}\n{traceback.format_exc()[-200:]}"

    return {
        "version": "2.19",
        "azure_openai_key_set": bool(azure_key),
        "azure_openai_endpoint": azure_endpoint or "NOT SET",
        "gpt_model": gpt_model,
        "font_found": font_found,
        "ffmpeg_available": ffmpeg_ok,
        "ffprobe_available": ffprobe_ok,
        "job_dir": _AI_CLIP_JOB_DIR,
        "job_storage": "DB (ai_clip_jobs) + file fallback",
        "db_status": db_status,
        "features": {
            "silence_cut": True,
            "zoom_pulse": True,
            "progress_bar": True,
            "flash_intro": True,
            "loop_fade": True,
            "cta_generation": True,
            "keyword_highlight": True,
            "subtitle_animation": True,
            "enhanced_thumbnail": True,
            "v9_scene_classifier": True,
            "v9_product_demo_priority": True,
            "v9_discount_penalty": True,
            "v9_quality_scoring": True,
            "v9_auto_reject": True,
            "v2_17_subtitle_gap": True,
            "v2_17_smart_linebreak": True,
            "v2_17_keyword_highlight_pillow": True,
            "v2_17_dynamic_fontsize": True,
        },
    }


@router.get("/debug-fonts")
async def debug_fonts(x_admin_key: Optional[str] = Header(None)):
    """Debug endpoint to check font availability on the server."""
    verify_admin(x_admin_key)
    import glob
    font_path = _find_cjk_font()
    font_name = _detect_cjk_font_name()
    
    # Check fc-list output
    fc_list_output = ""
    try:
        result = subprocess.run(
            ['fc-list', ':lang=ja', 'family'],
            capture_output=True, text=True, timeout=10
        )
        fc_list_output = result.stdout[:2000] if result.stdout else f"(empty, rc={result.returncode})"
    except Exception as e:
        fc_list_output = f"Error: {e}"
    
    # Check font files
    noto_fonts = glob.glob('/usr/share/fonts/**/NotoSans*CJK*', recursive=True)
    all_fonts = glob.glob('/usr/share/fonts/**/*.ttf', recursive=True) + \
               glob.glob('/usr/share/fonts/**/*.ttc', recursive=True)
    
    # Check ffmpeg ASS/libass support
    ffmpeg_ass_info = ""
    try:
        r = subprocess.run(['ffmpeg', '-filters'], capture_output=True, text=True, timeout=10)
        for line in r.stdout.split('\n'):
            if 'ass' in line.lower() or 'subtitle' in line.lower():
                ffmpeg_ass_info += line.strip() + '\n'
    except Exception as e:
        ffmpeg_ass_info = f"Error: {e}"

    # Check libass version
    libass_info = ""
    try:
        r = subprocess.run(['dpkg', '-l', 'libass9', 'libass-dev'], capture_output=True, text=True, timeout=10)
        libass_info = r.stdout[:500]
    except Exception as e:
        libass_info = f"Error: {e}"

    # Quick ASS render test - multiple patterns
    ass_test_results = {}
    import tempfile
    
    # Test patterns: different font names and fontsdir combinations
    test_patterns = {
        "fontsdir_otf": {"fontname": font_name, "fontsdir": "/tmp/aitherhub_fonts"},
        "fontsdir_noto": {"fontname": font_name, "fontsdir": "/usr/share/fonts/opentype/noto"},
        "fontsdir_all": {"fontname": font_name, "fontsdir": "/usr/share/fonts"},
        "no_fontsdir": {"fontname": font_name, "fontsdir": None},
        "sans_serif": {"fontname": "sans-serif", "fontsdir": None},
        "arial": {"fontname": "Arial", "fontsdir": None},
    }
    
    for pattern_name, cfg in test_patterns.items():
        try:
            test_ass = tempfile.NamedTemporaryFile(suffix='.ass', mode='w', delete=False)
            test_ass.write('[Script Info]\nScriptType: v4.00+\nPlayResX: 400\nPlayResY: 400\n\n')
            test_ass.write('[V4+ Styles]\n')
            test_ass.write('Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n')
            test_ass.write(f'Style: Default,{cfg["fontname"]},40,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,2,0,5,10,10,10,1\n\n')
            test_ass.write('[Events]\n')
            test_ass.write('Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n')
            test_ass.write('Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,\u30c6\u30b9\u30c8 Test ABC\n')
            test_ass.close()
            
            test_out = test_ass.name.replace('.ass', f'_{pattern_name}.png')
            vf = f"ass='{test_ass.name}'"
            if cfg["fontsdir"]:
                vf += f":fontsdir='{cfg['fontsdir']}'"
            
            r = subprocess.run(
                ['ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=c=black:s=400x400:d=1',
                 '-vf', vf, '-frames:v', '1', test_out],
                capture_output=True, text=True, timeout=15
            )
            file_size = os.path.getsize(test_out) if os.path.exists(test_out) else 0
            ass_test_results[pattern_name] = {
                "rc": r.returncode,
                "output_size_bytes": file_size,
                "has_content": file_size > 3000,  # >3KB means text was rendered
                "stderr_tail": (r.stderr or '')[-200:],
            }
            os.unlink(test_ass.name)
            if os.path.exists(test_out):
                os.unlink(test_out)
        except Exception as e:
            ass_test_results[pattern_name] = {"error": str(e)}

    # ffmpeg binary info
    ffmpeg_which = ""
    ffmpeg_version = ""
    ffmpeg_drawtext = False
    try:
        rw = subprocess.run(['which', 'ffmpeg'], capture_output=True, text=True, timeout=5)
        ffmpeg_which = rw.stdout.strip()
        rv = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True, timeout=5)
        ffmpeg_version = rv.stdout.split('\n')[0] if rv.stdout else ''
        # Check all video filters
        rf = subprocess.run(['ffmpeg', '-filters'], capture_output=True, text=True, timeout=10)
        for line in rf.stdout.split('\n'):
            if 'drawtext' in line:
                ffmpeg_drawtext = True
                break
    except Exception as e:
        ffmpeg_which = f"Error: {e}"

    return {
        "font_path": font_path,
        "font_name_for_ass": font_name,
        "font_path_exists": os.path.exists(font_path),
        "noto_cjk_files": noto_fonts[:20],
        "total_font_files": len(all_fonts),
        "fc_list_japanese": fc_list_output,
        "search_paths_status": {p: os.path.exists(p) for p in _FONT_SEARCH_PATHS},
        "ffmpeg_ass_filters": ffmpeg_ass_info,
        "ffmpeg_which": ffmpeg_which,
        "ffmpeg_version": ffmpeg_version,
        "ffmpeg_has_drawtext": ffmpeg_drawtext,
        "libass_packages": libass_info,
        "ass_render_tests": ass_test_results,
    }


@router.post("/cleanup-stuck-jobs")
async def cleanup_stuck_jobs(
    max_age_minutes: int = 30,
    x_admin_key: Optional[str] = Header(None),
):
    """Stuck状態のAIクリップジョブを自動クリーンアップする。
    
    - processing/selecting状態で max_age_minutes 以上更新がないジョブをfailedに変更
    - 既にデプロイ済みのclip_job_timeout_monitorはvideo_clipsテーブルを監視するが、
      このエンドポイントはai_clip_jobsテーブル（フロントエンド表示用）をクリーンアップする
    """
    verify_admin(x_admin_key)
    
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text("""
                UPDATE ai_clip_jobs
                SET status = 'failed',
                    error = 'Auto-cleanup: job stuck for over ' || :age || ' minutes without progress update',
                    updated_at = NOW()
                WHERE status IN ('processing', 'selecting', 'queued')
                  AND updated_at < NOW() - INTERVAL '1 minute' * :age
                RETURNING job_id, current_step, progress_pct, updated_at
            """), {"age": max_age_minutes})
            cleaned = result.fetchall()
        
        cleaned_jobs = [
            {
                "job_id": str(row.job_id),
                "last_step": row.current_step,
                "last_progress": row.progress_pct,
                "last_updated": str(row.updated_at),
            }
            for row in cleaned
        ]
        
        logger.info(f"[ai-clip] Cleanup: {len(cleaned_jobs)} stuck jobs marked as failed")
        return {
            "cleaned_count": len(cleaned_jobs),
            "max_age_minutes": max_age_minutes,
            "cleaned_jobs": cleaned_jobs,
        }
    except Exception as e:
        logger.error(f"[ai-clip] Cleanup error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate-from-clip")
async def generate_ai_clip_from_clip(
    req: GenerateFromClipRequest,
    background_tasks: BackgroundTasks,
    x_admin_key: Optional[str] = Header(None),
):
    """特定のクリップIDを指定してAIクリップを生成する（クリップDBから直接）"""
    verify_admin(x_admin_key)

    # UUID形式のバリデーション
    try:
        uuid.UUID(req.clip_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail=f"無効なクリップID形式です（UUID形式が必要）: {req.clip_id}")

    # クリップ情報をDBから取得
    async with get_session() as session:
        result = await session.execute(text("""
            SELECT vc.id as clip_id, vc.video_id, vc.phase_index,
                   vc.time_start, vc.time_end, vc.duration_sec,
                   vc.clip_url, vc.thumbnail_url, vc.transcript_text,
                   vc.product_name, vc.cta_score, vc.importance_score,
                   vc.captions, vc.subtitle_style, vc.liver_name
            FROM video_clips vc
            WHERE vc.id = CAST(:clip_id AS uuid)
        """), {"clip_id": req.clip_id})
        row = result.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"クリップが見つかりません: {req.clip_id}")
    if not row.clip_url:
        raise HTTPException(status_code=400, detail="このクリップにはclip_urlがありません")

    # material_onlyのクリップはAI生成候補から除外
    async with get_session() as session:
        mat_check = await session.execute(text("""
            SELECT rating FROM clip_feedback
            WHERE video_id = CAST(:video_id AS uuid)
              AND phase_index = :phase_index
            ORDER BY created_at DESC LIMIT 1
        """), {"video_id": str(row.video_id), "phase_index": row.phase_index})
        mat_row = mat_check.fetchone()
        if mat_row and mat_row.rating == 'material_only':
            raise HTTPException(
                status_code=400,
                detail="このクリップは「素材のみ」と評価されているため、AIクリップ生成の対象外です。"
            )

    clip_data = {
        "clip_id": row.clip_id, "video_id": row.video_id,
        "phase_index": row.phase_index, "time_start": row.time_start,
        "time_end": row.time_end, "duration_sec": row.duration_sec,
        "clip_url": row.clip_url, "thumbnail_url": row.thumbnail_url,
        "transcript_text": row.transcript_text, "product_name": row.product_name,
        "cta_score": row.cta_score, "importance_score": row.importance_score,
        "captions": row.captions, "subtitle_style": row.subtitle_style,
        "liver_name": row.liver_name,
    }

    # V3: video_mode対応 — 商品画像を取得
    product_image_urls = req.product_image_urls or []
    if req.video_mode in ("product_overlay", "audio_only") and not product_image_urls:
        # widget_clip_assignmentsから商品画像を取得する
        async with get_session() as session:
            img_result = await session.execute(text("""
                SELECT DISTINCT wca.product_image_url
                FROM widget_clip_assignments wca
                WHERE wca.clip_id = :clip_id_str
                  AND wca.product_image_url IS NOT NULL
                  AND wca.product_image_url != ''
                  AND wca.is_active = TRUE
            """), {"clip_id_str": req.clip_id})
            img_rows = img_result.fetchall()
            product_image_urls = [r.product_image_url for r in img_rows]
        # フォールバック: video_product_exposuresからも取得
        if not product_image_urls:
            async with get_session() as session:
                exp_result = await session.execute(text("""
                    SELECT DISTINCT product_image_url
                    FROM video_product_exposures
                    WHERE video_id = CAST(:video_id AS uuid)
                      AND product_image_url IS NOT NULL
                      AND product_image_url != ''
                    LIMIT 5
                """), {"video_id": str(row.video_id)})
                exp_rows = exp_result.fetchall()
                product_image_urls = [r.product_image_url for r in exp_rows]
    clip_data["video_mode"] = req.video_mode
    clip_data["product_image_urls"] = product_image_urls
    clip_data["product_video_urls"] = req.product_video_urls or []  # V12: 商品動町PiP対応

    # GenerateRequestに変換（_process_single_clip_v2が受け取る形式）
    gen_req = GenerateRequest(
        max_clips=1,
        subtitle_style=req.subtitle_style,
        enable_sfx=req.enable_sfx,
        enable_transitions=req.enable_transitions,
        transition_type=req.transition_type,
        transition_duration=req.transition_duration,
        enable_hook=req.enable_hook,
        hook_text=req.hook_text,
        enable_thumbnail=req.enable_thumbnail,
        target_language=req.target_language,
        position_y=req.position_y,
        enable_silence_cut=req.enable_silence_cut,
        enable_zoom_pulse=req.enable_zoom_pulse,
        enable_progress_bar=req.enable_progress_bar,
        enable_flash_intro=req.enable_flash_intro,
        enable_loop_fade=req.enable_loop_fade,
        enable_cta=req.enable_cta,
        enable_keyword_highlight=req.enable_keyword_highlight,
        enable_subtitle_animation=req.enable_subtitle_animation,
        zoom_intensity=req.zoom_intensity,
        silence_threshold_db=req.silence_threshold_db,
        output_language=req.output_language,
        voice_id=req.voice_id,
    )

    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    job_data = {
        "job_id": job_id,
        "status": "queued",
        "progress_pct": 0,
        "current_step": f"クリップ {req.clip_id[:8]}... の処理を開始します",
        "clips_completed": 0,
        "clips_total": 1,
        "results": [],
        "error": None,
        "created_at": now,
        "updated_at": now,
        "config": {"clip_id": req.clip_id, "mode": "single_clip", **req.dict()},
        "source_clip": {
            "clip_id": str(row.clip_id),
            "product_name": row.product_name,
            "liver_name": row.liver_name,
            "duration_sec": row.duration_sec,
        },
    }
    await _save_job(job_id, job_data)
    background_tasks.add_task(_run_single_clip_generation, job_id, gen_req, clip_data)
    return {
        "job_id": job_id,
        "status": "queued",
        "clip_id": req.clip_id,
        "message": f"クリップ {req.clip_id[:8]}... のAIクリップ生成を開始しました",
    }


async def _run_single_clip_generation(job_id: str, req: GenerateRequest, clip_data: dict):
    """単一クリップのAIクリップ生成バックグラウンドタスク"""
    try:
        async with _AI_CLIP_SEMAPHORE:
            clip_id = str(clip_data["clip_id"])
            await _update_job(job_id, status="processing", progress_pct=2,
                        current_step=f"クリップ {clip_id[:8]}... 処理開始...")

            result = await _process_single_clip_v2(job_id, clip_data, req, 0, 1)
            await _update_job(
                job_id, status="done", progress_pct=100,
                current_step="完了: 1/1件成功",
                clips_completed=1, results=[result],
            )
            logger.info(f"[ai-clip {job_id}] Single clip generation complete: {clip_id}")
    except Exception as e:
        logger.error(f"[ai-clip {job_id}] Single clip generation failed: {e}", exc_info=True)
        await _update_job(job_id, status="failed", error=str(e)[:500],
                    current_step=f"エラー: {str(e)[:200]}")


@router.post("/generate")
async def generate_ai_clip(
    req: GenerateRequest,
    background_tasks: BackgroundTasks,
    x_admin_key: Optional[str] = Header(None),
):
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
    await _save_job(job_id, job_data)
    background_tasks.add_task(_run_ai_clip_generation, job_id, req)
    return {"job_id": job_id, "status": "queued", "message": "全自動AIクリップ生成ジョブを開始しました (V2)"}


@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str, x_admin_key: Optional[str] = Header(None)):
    verify_admin(x_admin_key)
    # Try file first (for in-progress jobs), then DB
    job = _load_job(job_id)
    if not job:
        job = await _load_job_db(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/jobs")
async def list_jobs(
    limit: int = Query(20, ge=1, le=100),
    source_clip_id: Optional[str] = Query(None, description="Filter by source clip_id"),
    x_admin_key: Optional[str] = Header(None),
):
    verify_admin(x_admin_key)
    # Get from DB (persistent) and merge with file-based (in-progress)
    db_jobs = await _list_jobs_db(limit=500 if source_clip_id else limit)
    file_jobs = _list_jobs(limit=500 if source_clip_id else limit)
    # Merge: DB is source of truth, but file may have more recent progress
    db_ids = {j["job_id"] for j in db_jobs}
    merged = list(db_jobs)
    for fj in file_jobs:
        if fj.get("job_id") not in db_ids:
            merged.append(fj)
    # Filter by source_clip_id if specified
    if source_clip_id:
        merged = [
            j for j in merged
            if (j.get("config") or {}).get("clip_id") == source_clip_id
            or (j.get("source_clip") or {}).get("clip_id") == source_clip_id
        ]
    merged.sort(key=lambda j: j.get("created_at", ""), reverse=True)
    merged = merged[:limit]
    # Refresh SAS tokens for download_url in results (may be expired)
    import os as _os
    from app.services.storage_service import generate_read_sas_from_url
    _cdn_host = _os.getenv("CDN_HOST", "https://cdn.aitherhub.com")
    _blob_host_prefix = f"https://{_os.getenv('AZURE_STORAGE_ACCOUNT_NAME', 'aitherhub')}.blob.core.windows.net"
    for job in merged:
        for r in (job.get("results") or []):
            blob_url = r.get("blob_url", "")
            # Convert CDN URL to blob URL for SAS generation
            actual_blob_url = blob_url
            if blob_url and _cdn_host and blob_url.startswith(_cdn_host):
                actual_blob_url = blob_url.replace(_cdn_host, _blob_host_prefix)
            if actual_blob_url and "blob.core.windows.net" in actual_blob_url:
                try:
                    fresh_url = generate_read_sas_from_url(actual_blob_url, expires_hours=2)
                    if fresh_url:
                        r["download_url"] = fresh_url
                except Exception:
                    pass
    return {"jobs": merged, "total": len(merged)}


@router.get("/completed-clips")
async def list_completed_clips(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    x_admin_key: Optional[str] = Header(None),
):
    """完成動画一覧: DB + ファイルマージで全ジョブの成功クリップを一覧表示"""
    verify_admin(x_admin_key)
    await _ensure_jobs_table()
    try:
        # Get from DB (persistent) and merge with file-based (in-progress)
        db_jobs = await _list_jobs_db(limit=500)
        file_jobs = _list_jobs(limit=500)
        # Merge: DB is source of truth, but file may have more recent data
        db_ids = {j["job_id"] for j in db_jobs}
        merged = list(db_jobs)
        for fj in file_jobs:
            if fj.get("job_id") not in db_ids:
                merged.append(fj)

        clips = []
        for job in merged:
            if job.get("status") != "done":
                continue
            results_data = job.get("results", [])
            if isinstance(results_data, str):
                try:
                    results_data = json.loads(results_data)
                except Exception:
                    results_data = []
            config_data = job.get("config", {})
            if isinstance(config_data, str):
                try:
                    config_data = json.loads(config_data)
                except Exception:
                    config_data = {}
            source_data = job.get("source_clip")
            if isinstance(source_data, str):
                try:
                    source_data = json.loads(source_data)
                except Exception:
                    source_data = None
            for r in results_data:
                if r.get("status") == "done" and (r.get("blob_url") or r.get("download_url")):
                    clips.append({
                        "job_id": job.get("job_id"),
                        "clip_id": r.get("clip_id"),
                        "download_url": r.get("download_url"),
                        "blob_url": r.get("blob_url"),
                        "thumbnail_url": r.get("thumbnail_url"),
                        "file_size": r.get("file_size"),
                        "duration_sec": r.get("duration_sec"),
                        "hook_text": r.get("hook_text"),
                        "cta_text": r.get("cta_text"),
                        "captions_count": r.get("captions_count", 0),
                        "effects_applied": r.get("effects_applied", {}),
                        "created_at": str(job.get("created_at")) if job.get("created_at") else None,
                        "config": config_data,
                        "source_clip": source_data,
                    })

        # Sort by created_at desc
        clips.sort(key=lambda c: c.get("created_at", "") or "", reverse=True)
        total = len(clips)
        paginated = clips[offset:offset + limit]
        return {"clips": paginated, "total": total, "offset": offset, "limit": limit}
    except Exception as e:
        logger.error(f"[ai-clip] Failed to list completed clips: {e}", exc_info=True)
        return {"clips": [], "total": 0, "offset": offset, "limit": limit, "error": str(e)[:200]}


# ─── Caption Editing & Regeneration ───────────────────────────────────────────

class CaptionSegment(BaseModel):
    start: float
    end: float
    text: str
    style: Optional[str] = None
    scene_type: Optional[str] = None

class UpdateCaptionsRequest(BaseModel):
    captions: List[CaptionSegment]
    hook_text: Optional[str] = None
    cta_text: Optional[str] = None


@router.get("/jobs/{job_id}/captions")
async def get_job_captions(
    job_id: str,
    x_admin_key: str = Header(None),
):
    """ジョブの字幕データを取得する（編集用）"""
    verify_admin(x_admin_key)
    # _load_job is sync (file-based), then fallback to async DB
    job = _load_job(job_id)
    if not job:
        job = await _load_job_db(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="ジョブが見つかりません")
    results = job.get("results", [])
    if not results:
        raise HTTPException(status_code=404, detail="生成結果がありません")
    # Get captions from the first result
    result = results[0] if isinstance(results, list) else results
    captions = result.get("captions", [])
    clip_id = result.get("clip_id") or job.get("config", {}).get("clip_id", "")
    # Fallback: if captions not in results (pre-v2.4 jobs), try video_clips table
    if not captions and clip_id:
        try:
            async with engine.connect() as conn:
                row = await conn.execute(text(
                    "SELECT captions FROM video_clips WHERE id = CAST(:cid AS uuid) LIMIT 1"
                ), {"cid": clip_id})
                clip_row = row.fetchone()
                if clip_row and clip_row.captions:
                    captions = clip_row.captions if isinstance(clip_row.captions, list) else json.loads(clip_row.captions or "[]")
                    logger.info(f"[ai-clip] Loaded {len(captions)} captions from video_clips for {clip_id}")
        except Exception as e:
            logger.warning(f"[ai-clip] Failed to load captions from video_clips: {e}")
    return {
        "job_id": job_id,
        "clip_id": clip_id,
        "captions": captions,
        "hook_text": result.get("hook_text"),
        "cta_text": result.get("cta_text"),
        "captions_count": len(captions),
        "duration_sec": result.get("duration_sec"),
    }


@router.patch("/jobs/{job_id}/captions")
async def update_job_captions(
    job_id: str,
    req: UpdateCaptionsRequest,
    x_admin_key: str = Header(None),
):
    """ジョブの字幕・フック・CTAテキストを更新する"""
    verify_admin(x_admin_key)
    # _load_job is sync (file-based), then fallback to async DB
    job = _load_job(job_id)
    if not job:
        job = await _load_job_db(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="ジョブが見つかりません")
    results = job.get("results", [])
    if not results:
        raise HTTPException(status_code=404, detail="生成結果がありません")
    # Update captions in the first result
    if isinstance(results, list) and len(results) > 0:
        results[0]["captions"] = [c.dict() for c in req.captions]
        results[0]["captions_count"] = len(req.captions)
        if req.hook_text is not None:
            results[0]["hook_text"] = req.hook_text
        if req.cta_text is not None:
            results[0]["cta_text"] = req.cta_text
    job["results"] = results
    await _save_job(job_id, job)
    return {
        "status": "updated",
        "job_id": job_id,
        "captions_count": len(req.captions),
        "hook_text": req.hook_text if req.hook_text is not None else results[0].get("hook_text"),
        "cta_text": req.cta_text if req.cta_text is not None else results[0].get("cta_text"),
    }


class RegenerateRequest(BaseModel):
    subtitle_style: str = "auto"
    position_y: float = 75.0
    enable_subtitle_animation: bool = True
    enable_keyword_highlight: bool = True


@router.post("/jobs/{job_id}/regenerate")
async def regenerate_clip(
    job_id: str,
    req: RegenerateRequest,
    background_tasks: BackgroundTasks,
    x_admin_key: str = Header(None),
):
    """修正済み字幕で動画を再エンコードする（Whisperスキップ）"""
    verify_admin(x_admin_key)
    # _load_job is sync (file-based), then fallback to async DB
    job = _load_job(job_id)
    if not job:
        job = await _load_job_db(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="ジョブが見つかりません")
    results = job.get("results", [])
    if not results or not isinstance(results, list) or len(results) == 0:
        raise HTTPException(status_code=404, detail="生成結果がありません")
    result = results[0]
    captions = result.get("captions", [])
    # Get source clip info
    source_clip = job.get("source_clip") or job.get("config", {})
    clip_id = result.get("clip_id") or source_clip.get("clip_id", "")
    # Fallback: if captions not in results (pre-v2.4 jobs), try video_clips table
    if not captions and clip_id:
        try:
            async with engine.connect() as conn:
                row = await conn.execute(text(
                    "SELECT captions FROM video_clips WHERE id = CAST(:cid AS uuid) LIMIT 1"
                ), {"cid": clip_id})
                clip_row = row.fetchone()
                if clip_row and clip_row.captions:
                    captions = clip_row.captions if isinstance(clip_row.captions, list) else json.loads(clip_row.captions or "[]")
                    logger.info(f"[ai-clip regen] Loaded {len(captions)} captions from video_clips for {clip_id}")
        except Exception as e:
            logger.warning(f"[ai-clip regen] Failed to load captions from video_clips: {e}")
    if not captions:
        raise HTTPException(status_code=400, detail="字幕データがありません。先にcaptionsを設定してください")
    # Create a new regeneration job
    regen_job_id = str(uuid.uuid4())
    regen_job = {
        "job_id": regen_job_id,
        "status": "processing",
        "progress_pct": 0,
        "current_step": "再エンコード準備中...",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "type": "regenerate",
            "original_job_id": job_id,
            "clip_id": clip_id,
            "subtitle_style": req.subtitle_style,
        },
        "results": [],
    }
    await _save_job(regen_job_id, regen_job)
    # Start background regeneration
    background_tasks.add_task(
        _run_regeneration,
        regen_job_id, job_id, clip_id, captions,
        result.get("hook_text"), result.get("cta_text"),
        req.subtitle_style, req.position_y,
        req.enable_subtitle_animation, req.enable_keyword_highlight,
        source_clip,
    )
    return {
        "job_id": regen_job_id,
        "status": "processing",
        "message": "再エンコードを開始しました。字幕修正が反映されます。",
    }


async def _run_regeneration(
    job_id: str, original_job_id: str, clip_id: str,
    captions: list, hook_text: Optional[str], cta_text: Optional[str],
    subtitle_style: str, position_y: float,
    enable_animations: bool, enable_highlights: bool,
    source_clip: dict,
):
    """バックグラウンドで再エンコードを実行する"""
    try:
        await _update_job(job_id, progress_pct=10, current_step="元動画をダウンロード中...")
        # IMPORTANT: Use the ORIGINAL source video from clip-db (not the generated output
        # which already has subtitles burned in)
        video_url = None
        # First try video_clips table (original video without burned-in subtitles)
        try:
            async with engine.connect() as conn:
                row = await conn.execute(text(
                    "SELECT clip_url FROM video_clips WHERE id = CAST(:cid AS uuid) LIMIT 1"
                ), {"cid": clip_id})
                clip_row = row.fetchone()
                if clip_row:
                    # Prefer HD URL, fallback to regular clip_url
                    raw_url = clip_row.clip_url
                    if raw_url:
                        # If URL is a blob URL without SAS, generate a fresh SAS token
                        if "blob.core.windows.net" in raw_url and "?" not in raw_url:
                            from app.services.storage_service import generate_read_sas_from_url
                            sas_url = generate_read_sas_from_url(raw_url, expires_hours=2)
                            video_url = sas_url or raw_url
                        elif "cdn.aitherhub.com" in raw_url:
                            # CDN URLs need to be converted to blob URL for SAS
                            blob_url = raw_url.replace("https://cdn.aitherhub.com", f"https://aitherhub.blob.core.windows.net")
                            from app.services.storage_service import generate_read_sas_from_url
                            sas_url = generate_read_sas_from_url(blob_url, expires_hours=2)
                            video_url = sas_url or raw_url
                        else:
                            video_url = raw_url
                        logger.info(f"[ai-clip regen] Using video_clips clip_url for {clip_id}")
        except Exception as e:
            logger.warning(f"[ai-clip regen] Failed to get video_clips video: {e}")
        # Fallback: use the generated video's blob_url with fresh SAS
        if not video_url:
            original_job = _load_job(original_job_id)
            if not original_job:
                original_job = await _load_job_db(original_job_id)
            if original_job:
                original_result = original_job.get("results", [{}])[0]
                blob_url = original_result.get("blob_url", "")
                if blob_url:
                    # Generate fresh SAS for the blob URL
                    try:
                        if "cdn.aitherhub.com" in blob_url:
                            blob_url = blob_url.replace("https://cdn.aitherhub.com", "https://aitherhub.blob.core.windows.net")
                        from app.services.storage_service import generate_read_sas_from_url
                        sas_url = generate_read_sas_from_url(blob_url, expires_hours=2)
                        video_url = sas_url or blob_url
                    except Exception:
                        video_url = blob_url
                if not video_url:
                    video_url = original_result.get("download_url")
                logger.warning(f"[ai-clip regen] Falling back to generated video (may have old subtitles)")
        if not video_url:
            await _update_job(job_id, status="error", error="元動画のURLが取得できません")
            return
        # Download the original video
        await _update_job(job_id, progress_pct=20, current_step="元動画をダウンロード中...")
        tmp_dir = tempfile.mkdtemp(prefix="regen_")
        video_path = os.path.join(tmp_dir, "source.mp4")
        import httpx
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.get(video_url)
            if resp.status_code != 200:
                await _update_job(job_id, status="error", error=f"動画ダウンロード失敗: HTTP {resp.status_code}")
                return
            with open(video_path, "wb") as f:
                f.write(resp.content)
        # Get video dimensions
        await _update_job(job_id, progress_pct=30, current_step="動画情報を取得中...")
        probe_cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", video_path]
        probe_proc = await asyncio.create_subprocess_exec(
            *probe_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        probe_out, _ = await probe_proc.communicate()
        video_width, video_height = 1080, 1920
        duration = 30.0
        if probe_out:
            probe_data = json.loads(probe_out)
            for stream in probe_data.get("streams", []):
                if stream.get("codec_type") == "video":
                    video_width = int(stream.get("width", 1080))
                    video_height = int(stream.get("height", 1920))
                    dur_str = stream.get("duration")
                    if dur_str:
                        duration = float(dur_str)
                    break
        # Generate Pillow overlay images (V2.10)
        await _update_job(job_id, progress_pct=40, current_step="字幕画像生成中 (Pillow)...")
        styled_captions = _assign_scene_styles(captions, duration, subtitle_style)
        font_path = _find_cjk_font()
        # V2.33: 卖点キーワード生成（regenパス）
        regen_sp_product = source_clip.get('product_name', '') if source_clip else ''
        regen_sp_timed = []
        if regen_sp_product and captions:
            try:
                sp_raw = await _generate_selling_points(captions, regen_sp_product)
                if sp_raw:
                    regen_sp_timed = _assign_selling_point_timings(sp_raw, captions, duration)
            except Exception:
                pass
        overlay_images = _generate_overlay_images(
            styled_captions=styled_captions,
            hook_text=hook_text,
            cta_text=cta_text,
            video_width=video_width,
            video_height=video_height,
            duration=duration,
            font_path=font_path,
            tmp_dir=tmp_dir,
            position_y=position_y,
            clip_duration=duration,  # For regen, clip_duration = actual video duration
            product_name=regen_sp_product,
            selling_points=regen_sp_timed,
        )
        logger.info(f"[ai-clip regen] Generated {len(overlay_images)} overlay images "
                    f"(selling_points={len(regen_sp_timed)})")
        # Build ffmpeg command for re-encoding with Pillow overlay
        await _update_job(job_id, progress_pct=50, current_step="再エンコード中 (Pillow overlay)...")
        output_path = os.path.join(tmp_dir, "output.mp4")
        # Build overlay filter chain
        input_args = ["-i", video_path]
        for png_path, _, _ in overlay_images:
            input_args.extend(["-i", png_path])
        if overlay_images:
            fc_parts = ["[0:v]null[vfx]"]
            current_label = "[vfx]"
            for i, (png_path, start_t, end_t) in enumerate(overlay_images):
                input_idx = i + 1
                is_last = (i == len(overlay_images) - 1)
                out_label = "[vout]" if is_last else f"[v{i}]"
                enable_expr = f"between(t\\,{start_t:.3f}\\,{end_t:.3f})"
                fc_parts.append(
                    f"{current_label}[{input_idx}:v]overlay=0:0:"
                    f"enable='{enable_expr}':"
                    f"format=auto{out_label}"
                )
                current_label = out_label
            fc_str = ";".join(fc_parts)
            ffmpeg_cmd = [
                "ffmpeg", "-y",
                *input_args,
                "-filter_complex", fc_str,
                "-map", "[vout]", "-map", "0:a",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                # V2.21: 拼接点硬切モード（-vsync vfrで音視頻同期を保証）
                "-bf", "0", "-flags", "+cgop", "-sc_threshold", "0",
                "-vsync", "vfr",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                output_path,
            ]
        else:
            # No overlays - just copy
            ffmpeg_cmd = [
                "ffmpeg", "-y", "-i", video_path,
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                # V2.21: 拼接点硬切モード（-vsync vfrで音視頻同期を保証）
                "-bf", "0", "-flags", "+cgop", "-sc_threshold", "0",
                "-vsync", "vfr",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                output_path,
            ]
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        if proc.returncode != 0:
            err_msg = stderr.decode()[-500:] if stderr else "Unknown ffmpeg error"
            await _update_job(job_id, status="error", error=f"ffmpegエラー: {err_msg}")
            return
        # Upload to Azure Blob Storage
        await _update_job(job_id, progress_pct=80, current_step="アップロード中...")
        output_size = os.path.getsize(output_path)
        download_url, blob_url = await _upload_to_blob(output_path, clip_id, job_id)
        # Update job with result
        await _update_job(job_id, progress_pct=100, current_step="完了", status="done")
        regen_result = {
            "clip_id": clip_id,
            "status": "done",
            "download_url": download_url,
            "blob_url": blob_url,
            "file_size": output_size,
            "duration_sec": duration,
            "hook_text": hook_text,
            "cta_text": cta_text,
            "captions_count": len(captions),
            "captions": captions,
            "effects_applied": {"regenerated": True, "subtitle_style": subtitle_style},
        }
        job_data = _load_job(job_id)
        if not job_data:
            job_data = await _load_job_db(job_id)
        if job_data:
            job_data["results"] = [regen_result]
            job_data["status"] = "done"
            job_data["progress_pct"] = 100
            await _save_job(job_id, job_data)
        # Cleanup
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
        logger.info(f"[ai-clip] Regeneration complete: {job_id}")
    except Exception as e:
        logger.error(f"[ai-clip] Regeneration failed: {e}", exc_info=True)
        await _update_job(job_id, status="error", error=str(e)[:500])


# ─── Background Processing ───────────────────────────────────────────────────
async def _run_ai_clip_generation(job_id: str, req: GenerateRequest):
    try:
        async with _AI_CLIP_SEMAPHORE:
            await _run_ai_clip_generation_inner(job_id, req)
    except Exception as e:
        logger.error(f"[ai-clip {job_id}] Fatal error: {e}", exc_info=True)
        await _update_job(job_id, status="failed", error=str(e)[:500])


async def _run_ai_clip_generation_inner(job_id: str, req: GenerateRequest):
    import httpx

    logger.info(f"[ai-clip {job_id}] Starting V2 generation pipeline")
    await _update_job(job_id, status="selecting", progress_pct=1, current_step="準備中...")
    await _update_job(job_id, progress_pct=2, current_step="候補クリップ検索中...")

    candidates = await _select_candidates(req)
    if not candidates:
        await _update_job(job_id, status="failed", error="条件に合うクリップが見つかりませんでした")
        return

    # ── V9 Quality Filter: 品質スコアリング + GPT検証 ──
    await _update_job(job_id, progress_pct=3, current_step="品質フィルタリング中...")
    candidates = await _validate_and_filter_candidates(candidates, req, job_id)
    if not candidates:
        await _update_job(job_id, status="failed", error="品質基準を満たすクリップが見つかりませんでした")
        return

    clips_total = min(len(candidates), req.max_clips)
    await _update_job(job_id, clips_total=clips_total, progress_pct=5,
                current_step=f"{clips_total}件の高品質クリップを選定完了")

    results = []
    PARALLEL_BATCH = 3  # V14.2: 3クリップずつ並列処理 (matches _AI_CLIP_SEMAPHORE=3)
    selected = candidates[:clips_total]

    for batch_start in range(0, len(selected), PARALLEL_BATCH):
        batch = selected[batch_start:batch_start + PARALLEL_BATCH]
        batch_pct = 10 + int((batch_start / clips_total) * 80)
        await _update_job(job_id, status="processing", progress_pct=batch_pct,
                    current_step=f"クリップ {batch_start+1}-{batch_start+len(batch)}/{clips_total} 並列処理中...",
                    clips_completed=len(results))

        async def _safe_process(clip, idx):
            clip_id = str(clip["clip_id"])
            try:
                result = await _process_single_clip_v2(job_id, clip, req, idx, clips_total)
                # V9: Post-generation quality check
                result = await _post_generation_quality_check(result, clip, job_id)
                return result
            except Exception as e:
                logger.error(f"[ai-clip {job_id}] Clip {idx+1} failed: {e}", exc_info=True)
                return {"clip_id": clip_id, "status": "failed", "error": str(e)[:200]}

        batch_results = await asyncio.gather(
            *[_safe_process(clip, batch_start + i) for i, clip in enumerate(batch)]
        )
        results.extend(batch_results)
        await _update_job(job_id, clips_completed=len(results), results=results)
        for i, br in enumerate(batch_results):
            cid = str(batch[i]["clip_id"])
            logger.info(f"[ai-clip {job_id}] Clip {batch_start+i+1}/{clips_total} {'done' if br.get('status')=='done' else 'failed'}: {cid}")

    success_count = sum(1 for r in results if r.get("status") == "done")
    await _update_job(
        job_id, status="done", progress_pct=100,
        current_step=f"完了: {success_count}/{clips_total}件成功",
        clips_completed=clips_total, results=results,
    )
    logger.info(f"[ai-clip {job_id}] V2 Pipeline complete: {success_count}/{clips_total} success")


async def _select_candidates(req: GenerateRequest) -> list:
    conditions = [
        "vc.status = 'completed'",
        "vc.clip_url IS NOT NULL",
        "COALESCE(vc.is_unusable, FALSE) = FALSE",
        # V11: Minimum duration filter (too short clips are rarely useful)
        "COALESCE(vc.duration_sec, 0) >= 15",
        # V11 NG率改善: 同じ動画の同じフェーズからのNGクリップが3件以上ある場合、そのフェーズを除外
        """NOT EXISTS (
            SELECT 1 FROM video_clips ng_vc
            WHERE ng_vc.video_id = vc.video_id
              AND ng_vc.phase_index = vc.phase_index
              AND ng_vc.is_unusable = TRUE
            GROUP BY ng_vc.video_id, ng_vc.phase_index
            HAVING COUNT(*) >= 3
        )""",
        # V11: 最低限のトランスクリプト長（短すぎるトランスクリプトは商品紹介として不十分）
        "COALESCE(LENGTH(vc.transcript_text), 0) >= 30",
    ]
    # V11: Fetch more candidates (8x) to allow stronger quality filtering
    params: dict = {"limit": req.max_clips * 8}

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
            ORDER BY COALESCE(vc.cta_score, 0) DESC,
                     COALESCE(vc.importance_score, 0) DESC,
                     COALESCE(LENGTH(vc.transcript_text), 0) DESC,
                     vc.created_at DESC
            LIMIT :limit
        """), params)
        rows = result.fetchall()

    return [
        {
            "clip_id": r.clip_id, "video_id": r.video_id,
            "phase_index": r.phase_index, "time_start": r.time_start,
            "time_end": r.time_end, "duration_sec": r.duration_sec,
            "clip_url": r.clip_url, "thumbnail_url": r.thumbnail_url,
            "transcript_text": r.transcript_text, "product_name": r.product_name,
            "cta_score": r.cta_score, "importance_score": r.importance_score,
            "captions": r.captions, "subtitle_style": r.subtitle_style,
            "liver_name": r.liver_name,
        }
        for r in rows
    ]


# ─── V9 Quality Scoring & Content Validation ─────────────────────────────────

def _compute_clip_quality_score(clip: dict) -> float:
    """
    V9品質スコアリング: 各クリップの「動画として使える品質」を0-100でスコアリング。
    高スコア = 商品紹介として有効、低スコア = 雑談/無関係/無音。
    """
    score = 0.0
    transcript = clip.get("transcript_text") or ""
    product_name = clip.get("product_name") or ""
    cta_score = float(clip.get("cta_score") or 0)
    importance = float(clip.get("importance_score") or 0)
    duration = float(clip.get("duration_sec") or 0)
    captions = clip.get("captions")
    if isinstance(captions, str):
        try:
            captions = json.loads(captions)
        except Exception:
            captions = []
    captions_count = len(captions) if captions else 0

    # ── 1. Transcript quality (0-30 points) ──
    transcript_len = len(transcript.strip())
    if transcript_len == 0:
        score += 0  # 無音/無字幕は0点
    elif transcript_len < 20:
        score += 5  # 極短すぎる
    elif transcript_len < 50:
        score += 12
    elif transcript_len < 150:
        score += 20
    else:
        score += 30  # 十分な発話量

    # ── 2. CTA score contribution (0-25 points) ──
    score += (cta_score / 5.0) * 25

    # ── 3. Importance score contribution (0-15 points) ──
    if importance > 0:
        score += min(importance / 15.0, 1.0) * 15

    # ── 4. Product relevance (0-15 points) ──
    if product_name:
        score += 10
        # 商品名がtranscriptに含まれていればボーナス
        if product_name.lower() in transcript.lower():
            score += 5
    else:
        # 商品名がなくても、商品関連キーワードがあればポイント
        product_keywords = [
            "商品", "セット", "限定", "在庫", "購入", "お得", "割引", "送料",
            "プレゼント", "キャンペーン", "今だけ", "残り", "ラスト",
            "産品", "限量", "優惠", "折扣", "免運", "搶購", "最後",
            "product", "limited", "discount", "sale", "buy",
            "使い方", "効果", "成分", "おすすめ", "人気", "ランキング",
            "塗る", "つける", "洗う", "乾かす", "仕上がり",
        ]
        keyword_hits = sum(1 for kw in product_keywords if kw in transcript)
        score += min(keyword_hits * 3, 12)

    # ── 5. Duration appropriateness (0-10 points) V10: 45-120秒が理想 ──
    if 45 <= duration <= 120:
        score += 10  # V10理想的な長さ
    elif 30 <= duration < 45 or 120 < duration <= 180:
        score += 7
    elif 15 <= duration < 30:
        score += 5
    elif duration < 15:
        score += 2  # 短すぎ
    else:
        score += 4  # 長すぎ

    # ── 6. Captions density (0-5 points) ──
    if captions_count > 0 and duration > 0:
        captions_per_sec = captions_count / duration
        if captions_per_sec >= 0.3:  # 10秒に3字幕以上 = 活発
            score += 5
        elif captions_per_sec >= 0.15:
            score += 3
        else:
            score += 1

    # ── Penalty: Repetitive content ──
    if transcript_len > 30:
        words = transcript.split()
        if len(words) > 5:
            unique_ratio = len(set(words)) / len(words)
            if unique_ratio < 0.3:  # 70%以上が重複 = 繰り返し
                score -= 15
            elif unique_ratio < 0.5:
                score -= 8

    # ── Penalty: Filler/chat content (V11強化: 雑談ペナルティを大幅増加) ──
    filler_patterns = [
        "こんにちは", "おはよう", "ありがとう", "お疲れ", "バイバイ",
        "聞こえますか", "見えてますか", "コメント読み", "ちょっと待って",
        "大家好", "謝謝", "掰掰", "等一下", "聽得到嗎",
        "hello", "thank you", "bye",
        # V11追加: よくある雑談パターン
        "お元気ですか", "今日も来てくれて", "初見さん", "いらっしゃい",
        "こんばんは", "おやすみ", "おつかれさまです",
        "どこから", "何歳", "天気", "雨",
        "次回の配信", "また来てね", "フォローして",
    ]
    filler_count = sum(1 for p in filler_patterns if p in transcript.lower())
    if filler_count >= 4:
        score -= 20  # V11: 雑談が非常に多い → 大幅減点
    elif filler_count >= 3:
        score -= 15  # V11: 10→15 雑談が多い
    elif filler_count >= 2:
        score -= 8   # V11: 5→8

    # ── V11 Penalty: No product relevance at all (NG率改善の核心) ──
    # 商品名がなく、商品関連キーワードもないクリップは大幅減点
    if not product_name:
        product_relevance_keywords = [
            "商品", "セット", "限定", "在庫", "購入", "お得", "割引", "送料",
            "プレゼント", "キャンペーン", "今だけ", "残り", "ラスト",
            "產品", "限量", "優惠", "折扣", "免運", "搶購", "最後",
            "product", "limited", "discount", "sale", "buy",
            "使い方", "効果", "成分", "おすすめ", "人気", "ランキング",
            "塗る", "つける", "洗う", "乾かす", "仕上がり",
            "クーポン", "値段", "円", "個", "本",
        ]
        has_any_product_keyword = any(kw in transcript for kw in product_relevance_keywords)
        if not has_any_product_keyword and transcript_len > 30:
            score -= 20  # V11: 商品と完全に無関係なコンテンツ

    # ── V2.19 Penalty: Low speech coverage ratio (発話率が低いクリップ) ──
    # 字幕のタイムスタンプから発話がカバーする時間比率を計算
    # 例: 23秒のクリップで4秒しか喋っていない → 発話率17% → 大幅減点
    if duration > 0 and captions and isinstance(captions, list) and len(captions) > 0:
        speech_time = 0.0
        for cap in captions:
            cap_start = float(cap.get("start", 0) if isinstance(cap, dict) else 0)
            cap_end = float(cap.get("end", 0) if isinstance(cap, dict) else 0)
            if cap_end > cap_start:
                speech_time += (cap_end - cap_start)
        speech_ratio = speech_time / duration
        if speech_ratio < 0.15:
            score -= 30  # 発話率15%未満: ほぼ無音 → 致命的減点
        elif speech_ratio < 0.25:
            score -= 20  # 発話率25%未満: 大部分が無音
        elif speech_ratio < 0.35:
            score -= 12  # 発話率35%未満: 無音が多い
        elif speech_ratio < 0.50:
            score -= 5   # 発話率50%未満: やや無音が多い

    return max(0.0, min(100.0, score))


async def _gpt_content_quality_score(clip: dict, job_id: str = "") -> dict:
    """
    GPTによるコンテンツ品質評価。「売れる動画かどうか」を判定する。
    構造スコア（_compute_clip_quality_score）とは別に、内容の質を評価。
    
    Returns:
        {
            "content_score": float (0-100),
            "breakdown": {
                "product_appeal": float (0-30),  # 商品訴求力
                "hook_quality": float (0-20),    # フックの質
                "story_flow": float (0-20),      # ストーリー性
                "viewer_engagement": float (0-15), # 視聴者への語りかけ
                "specificity": float (0-15),     # 具体性
            },
            "reasons": [str],  # 評価理由
            "sellability": str,  # "high" / "medium" / "low"
        }
    """
    transcript = (clip.get("transcript_text") or "")[:600]
    product_name = clip.get("product_name") or ""
    phase_description = (clip.get("phase_description") or "")[:400]
    video_id = clip.get("video_id") or ""
    
    # ─── フィードバック学習データ取得（同じ動画のNG/Good履歴を参照）───
    feedback_context = ""
    try:
        async with get_session() as _fb_session:
            # 同じ動画のフィードバック統計を取得
            fb_stats_sql = text("""
                SELECT 
                    COUNT(*) FILTER (WHERE rating = 'bad') as bad_count,
                    COUNT(*) FILTER (WHERE rating = 'good') as good_count,
                    array_agg(DISTINCT unnested_tag) FILTER (WHERE rating = 'bad') as bad_reason_tags
                FROM clip_feedback
                LEFT JOIN LATERAL unnest(
                    CASE WHEN jsonb_typeof(reason_tags) = 'array' 
                         THEN ARRAY(SELECT jsonb_array_elements_text(reason_tags))
                         ELSE ARRAY[]::text[] END
                ) AS unnested_tag ON TRUE
                WHERE video_id = CAST(:vid AS uuid)
            """)
            fb_result = await _fb_session.execute(fb_stats_sql, {"vid": video_id})
            fb_row = fb_result.fetchone()
            
            # 同じ動画のgoodクリップのtranscriptサンプルを取得（最大3件）
            good_samples_sql = text("""
                SELECT vc.transcript_text
                FROM clip_feedback cf
                JOIN video_clips vc ON vc.id = cf.clip_id
                WHERE cf.video_id = CAST(:vid AS uuid)
                  AND cf.rating = 'good'
                  AND vc.transcript_text IS NOT NULL
                  AND LENGTH(vc.transcript_text) > 30
                ORDER BY cf.created_at DESC
                LIMIT 3
            """)
            good_result = await _fb_session.execute(good_samples_sql, {"vid": video_id})
            good_rows = good_result.fetchall()
            
            # グローバルなNG理由の統計（全動画から学習）
            global_ng_sql = text("""
                SELECT reason_tag, COUNT(*) as cnt
                FROM (
                    SELECT jsonb_array_elements_text(reason_tags) as reason_tag
                    FROM clip_feedback
                    WHERE rating = 'bad' AND reason_tags IS NOT NULL
                      AND jsonb_typeof(reason_tags) = 'array'
                ) sub
                GROUP BY reason_tag
                ORDER BY cnt DESC
                LIMIT 5
            """)
            global_ng_result = await _fb_session.execute(global_ng_sql)
            global_ng_rows = global_ng_result.fetchall()
            
            # フィードバックコンテキストを構築
            if fb_row and (fb_row.bad_count or fb_row.good_count):
                feedback_context += f"\n【過去のフィードバック学習データ（この動画）】\n"
                feedback_context += f"- NG判定: {fb_row.bad_count}件 / Good判定: {fb_row.good_count}件\n"
                if fb_row.bad_reason_tags:
                    clean_tags = [t for t in fb_row.bad_reason_tags if t]
                    if clean_tags:
                        feedback_context += f"- NG理由: {', '.join(clean_tags[:5])}\n"
            
            if good_rows:
                feedback_context += "\n【この動画で高評価だったクリップの例】\n"
                for i, gr in enumerate(good_rows[:2], 1):
                    sample_text = (gr.transcript_text or "")[:150]
                    feedback_context += f"  例{i}: {sample_text}...\n"
                feedback_context += "→ 上記のような内容は高く評価してください。\n"
            
            if global_ng_rows:
                feedback_context += "\n【全体のNG傾向（学習済み）】\n"
                tag_map = {"irrelevant": "商品と無関係", "hook_weak": "フックが弱い", 
                           "too_short": "内容が薄い", "cut_position": "切り出し位置が悪い",
                           "subtitle": "字幕問題"}
                for ng_row in global_ng_rows[:3]:
                    tag_label = tag_map.get(ng_row.reason_tag, ng_row.reason_tag)
                    feedback_context += f"  - {tag_label}: {ng_row.cnt}件\n"
                feedback_context += "→ 上記に該当する場合は厳しく減点してください。\n"
    except Exception as _fb_err:
        logger.debug(f"[gpt-quality {job_id}] Feedback context fetch failed (non-fatal): {_fb_err}")
        feedback_context = ""
    
    # トランスクリプトが空の場合、phase_descriptionをフォールバックとして使用
    if not transcript.strip():
        if phase_description.strip():
            # phase_descriptionがある場合はそれを使ってGPT評価を実行
            transcript = f"[動画内容の説明] {phase_description}"
        else:
            # 両方空の場合のみスコア0を返す
            return {
                "content_score": 0.0,
                "breakdown": {
                    "product_appeal": 0, "hook_quality": 0,
                    "story_flow": 0, "viewer_engagement": 0, "specificity": 0,
                },
                "reasons": ["発話なし（音声認識・内容説明ともに未取得）"],
                "sellability": "low",
            }
    
    try:
        import openai
        azure_key = os.getenv("AZURE_OPENAI_KEY", "")
        azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
        azure_model = os.getenv("GPT5_MODEL") or os.getenv("GPT5_DEPLOYMENT") or "gpt-4.1-mini"
        
        if not azure_key or not azure_endpoint:
            # GPTが使えない場合は構造スコアのみで推定
            return _estimate_content_score_without_gpt(transcript, product_name)
        
        from urllib.parse import urlparse as _urlparse
        _parsed = _urlparse(azure_endpoint)
        clean_endpoint = f"{_parsed.scheme}://{_parsed.netloc}/"
        
        client = openai.AzureOpenAI(
            api_key=azure_key,
            azure_endpoint=clean_endpoint,
            api_version=os.getenv("GPT5_API_VERSION", "2025-04-01-preview"),
        )
        
        prompt = f"""あなたはTikTokライブコマース動画の品質評価専門家です。
以下の動画の書き起こし内容を読み、「この動画を見た視聴者が商品を購入したくなるか」を評価してください。
{feedback_context}
【評価項目】各項目を0点〜満点で採点してください。

1. 商品訴求力 (0-30点): 商品の効果・使い方・メリットを具体的に説明しているか
   - 30点: 商品の具体的な効果や使用感を詳しく説明、ビフォーアフターあり
   - 20点: 商品について触れているが具体性がやや不足
   - 10点: 商品名は出るが説明が薄い
   - 0点: 商品について全く触れていない

2. フックの質 (0-20点): 最初の部分で「見たい」と思わせる内容か
   - 20点: 具体的な悩み解決や驚きの結果を提示
   - 10点: 興味は引くが具体性に欠ける
   - 0点: 意味不明な切り出しや無関係な内容

3. ストーリー性 (0-20点): 問題提起→解決→CTAの流れがあるか
   - 20点: 明確な起承転結がある
   - 10点: 部分的に流れがある
   - 0点: 脈絡なく話が飛ぶ

4. 視聴者への語りかけ (0-15点): 共感・質問・呼びかけがあるか
   - 15点: 「〜で悩んでませんか？」等の共感要素が豊富
   - 8点: 多少の語りかけがある
   - 0点: 一方的な独り言

5. 具体性 (0-15点): 数字・体験談・比較など具体的な情報があるか
   - 15点: 具体的な数字や体験談が複数ある
   - 8点: 多少の具体的情報がある
   - 0点: 抽象的な表現のみ

商品名: {product_name or '（不明）'}
動画の書き起こし:
{transcript}

JSON形式で回答してください（JSONのみ出力）:
{{"product_appeal": 数値, "hook_quality": 数値, "story_flow": 数値, "viewer_engagement": 数値, "specificity": 数値, "reasons": ["理由1", "理由2"], "sellability": "high/medium/low"}}"""
        
        response = client.responses.create(
            model=azure_model,
            input=[{"role": "user", "content": prompt}],
            max_output_tokens=300,
        )
        
        result_text = ""
        if hasattr(response, "output_text") and response.output_text:
            result_text = response.output_text.strip()
        elif hasattr(response, "output") and response.output:
            for item in response.output:
                if hasattr(item, "content"):
                    for part in item.content:
                        if hasattr(part, "text"):
                            result_text += part.text
            result_text = result_text.strip()
        
        # JSONパース
        json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
        if json_match:
            evaluation = json.loads(json_match.group())
            breakdown = {
                "product_appeal": min(float(evaluation.get("product_appeal", 0)), 30),
                "hook_quality": min(float(evaluation.get("hook_quality", 0)), 20),
                "story_flow": min(float(evaluation.get("story_flow", 0)), 20),
                "viewer_engagement": min(float(evaluation.get("viewer_engagement", 0)), 15),
                "specificity": min(float(evaluation.get("specificity", 0)), 15),
            }
            content_score = sum(breakdown.values())
            reasons = evaluation.get("reasons", [])
            sellability = evaluation.get("sellability", "low")
            
            logger.info(
                f"[gpt-quality {job_id}] Content score: {content_score:.1f} "
                f"(appeal={breakdown['product_appeal']}, hook={breakdown['hook_quality']}, "
                f"story={breakdown['story_flow']}, engage={breakdown['viewer_engagement']}, "
                f"specific={breakdown['specificity']}) sellability={sellability}"
            )
            
            return {
                "content_score": content_score,
                "breakdown": breakdown,
                "reasons": reasons[:5],
                "sellability": sellability,
            }
        else:
            logger.warning(f"[gpt-quality {job_id}] Failed to parse GPT response: {result_text[:200]}")
            return _estimate_content_score_without_gpt(transcript, product_name)
    
    except Exception as e:
        logger.warning(f"[gpt-quality {job_id}] GPT content evaluation failed: {e}")
        return _estimate_content_score_without_gpt(transcript, product_name)


def _estimate_content_score_without_gpt(transcript: str, product_name: str) -> dict:
    """
    GPTが使えない場合のフォールバック: キーワードベースでコンテンツ品質を推定。
    """
    score = 0.0
    breakdown = {
        "product_appeal": 0.0, "hook_quality": 0.0,
        "story_flow": 0.0, "viewer_engagement": 0.0, "specificity": 0.0,
    }
    reasons = []
    
    # 商品訴求力の推定
    product_keywords = [
        "効果", "使い方", "成分", "仕上がり", "変わる", "改善",
        "塗る", "つける", "洗う", "乾かす", "ツヤ", "サラサラ",
        "ダメージ", "補修", "保湿", "香り", "テクスチャ",
    ]
    appeal_hits = sum(1 for kw in product_keywords if kw in transcript)
    if product_name and product_name in transcript:
        appeal_hits += 3
    breakdown["product_appeal"] = min(appeal_hits * 5, 30)
    if appeal_hits >= 3:
        reasons.append("商品関連キーワードが複数含まれる")
    
    # 視聴者への語りかけ推定
    engagement_patterns = [
        "ですよね", "でしょ", "ませんか", "知ってる", "悩んで",
        "おすすめ", "試して", "使ってみて", "見て",
    ]
    engage_hits = sum(1 for p in engagement_patterns if p in transcript)
    breakdown["viewer_engagement"] = min(engage_hits * 5, 15)
    
    # 具体性の推定（数字があるか）
    import re as _re
    numbers = _re.findall(r'\d+[%％円個本ml]', transcript)
    breakdown["specificity"] = min(len(numbers) * 5, 15)
    if numbers:
        reasons.append(f"具体的な数値が{len(numbers)}箇所")
    
    # フックの質（最初の50文字に商品関連語があるか）
    first_50 = transcript[:50]
    if product_name and product_name in first_50:
        breakdown["hook_quality"] = 15
    elif any(kw in first_50 for kw in product_keywords[:8]):
        breakdown["hook_quality"] = 10
    else:
        breakdown["hook_quality"] = 5
    
    # ストーリー性（長さと構造から推定）
    if len(transcript) > 200:
        breakdown["story_flow"] = 10
    elif len(transcript) > 100:
        breakdown["story_flow"] = 5
    
    content_score = sum(breakdown.values())
    sellability = "high" if content_score >= 60 else "medium" if content_score >= 35 else "low"
    
    return {
        "content_score": content_score,
        "breakdown": breakdown,
        "reasons": reasons or ["GPT未使用（キーワードベース推定）"],
        "sellability": sellability,
    }


async def _compute_combined_quality_score(clip: dict, job_id: str = "") -> dict:
    """
    統合品質スコア: 構造スコア（40%）+ GPTコンテンツスコア（60%）を統合。
    
    Returns:
        {
            "total_score": float (0-100),
            "structure_score": float (0-100),
            "content_score": float (0-100),
            "breakdown": dict,
            "reasons": [str],
            "sellability": str,
        }
    """
    structure_score = _compute_clip_quality_score(clip)
    content_eval = await _gpt_content_quality_score(clip, job_id)
    content_score = content_eval["content_score"]
    
    # 統合スコア: 構造40% + コンテンツ60%
    total_score = (structure_score * 0.4) + (content_score * 0.6)
    total_score = max(0.0, min(100.0, total_score))
    
    return {
        "total_score": total_score,
        "structure_score": structure_score,
        "content_score": content_score,
        "breakdown": content_eval["breakdown"],
        "reasons": content_eval["reasons"],
        "sellability": content_eval["sellability"],
    }


async def _validate_and_filter_candidates(
    candidates: list, req: GenerateRequest, job_id: str
) -> list:
    """
    V9品質フィルター: 候補クリップを品質スコアでフィルタリング＋GPT検証。
    低品質クリップを除外し、高品質なもののみ返す。
    """
    if not candidates:
        return []

    MIN_QUALITY_SCORE = 50  # V11: 35→50 閾値引き上げ（NG率改善: 低品質クリップをより積極的に除外）
    GPT_VALIDATE_THRESHOLD = 65  # V11: 55→65 GPT検証範囲を拡大

    scored_candidates = []
    for clip in candidates:
        quality_score = _compute_clip_quality_score(clip)
        clip["_quality_score"] = quality_score
        if quality_score >= MIN_QUALITY_SCORE:
            scored_candidates.append(clip)
        else:
            logger.info(
                f"[ai-clip {job_id}] Rejected clip {clip['clip_id']} "
                f"(quality_score={quality_score:.1f} < {MIN_QUALITY_SCORE})"
            )

    if not scored_candidates:
        logger.warning(f"[ai-clip {job_id}] All candidates rejected by quality filter")
        return []

    # GPT検証: 中間スコアのクリップに対してGPTで内容を検証
    borderline = [c for c in scored_candidates if c["_quality_score"] < GPT_VALIDATE_THRESHOLD]
    if borderline and len(borderline) <= 10:  # V11: 5→10 より多くのクリップをGPT検証
        validated = await _gpt_validate_clips(borderline, job_id)
        # GPTが「無効」と判断したクリップを除外
        rejected_ids = {v["clip_id"] for v in validated if not v["is_valid"]}
        if rejected_ids:
            scored_candidates = [
                c for c in scored_candidates
                if str(c["clip_id"]) not in rejected_ids
            ]
            logger.info(
                f"[ai-clip {job_id}] GPT rejected {len(rejected_ids)} borderline clips"
            )

    # 品質スコア順にソート（高い順）
    scored_candidates.sort(key=lambda c: c["_quality_score"], reverse=True)

    top_scores = [f"{c.get('_quality_score', 0):.0f}" for c in scored_candidates[:5]]
    logger.info(
        f"[ai-clip {job_id}] Quality filter: {len(candidates)} -> {len(scored_candidates)} candidates "
        f"(top scores: {top_scores})"
    )
    return scored_candidates


async def _gpt_validate_clips(clips: list, job_id: str) -> list:
    """
    GPTを使って、クリップの内容が「商品紹介・セールス動画」として有効かどうかを判定。
    """
    results = []
    try:
        import openai
        azure_key = os.getenv("AZURE_OPENAI_KEY", "")
        azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
        azure_model = os.getenv("GPT5_MODEL") or os.getenv("GPT5_DEPLOYMENT") or "gpt-4.1-mini"
        if not azure_key or not azure_endpoint:
            return [{"clip_id": str(c["clip_id"]), "is_valid": True} for c in clips]

        from urllib.parse import urlparse as _urlparse
        _parsed = _urlparse(azure_endpoint)
        clean_endpoint = f"{_parsed.scheme}://{_parsed.netloc}/"
        client = openai.AzureOpenAI(
            api_key=azure_key,
            azure_endpoint=clean_endpoint,
            api_version=os.getenv("GPT5_API_VERSION", "2025-04-01-preview"),
        )

        # バッチで検証（1回のAPI呼び出しで複数クリップを判定）
        clips_info = []
        for i, clip in enumerate(clips):
            transcript = (clip.get("transcript_text") or "")[:200]
            product = clip.get("product_name") or "不明"
            clips_info.append(
                f"クリップ{i+1}: 商品={product}, 内容=「{transcript}」"
            )

        prompt = f"""以下のライブ配信クリップが「商品紹介・セールス動画」として有効かどうかを判定してください。

【有効の基準】
- 主播（配信者）が商品について具体的に説明している
- 商品のデモンストレーション・使い方を見せている
- 購入を促すCTA（行動喚起）がある
- 商品の特徴・効果・価格について言及している

【無効の基準】
- 単なる挨拶・雑談・コメント読み上げのみ
- 商品と無関係な話題
- 音声が不明瞭で内容が判別できない
- 同じフレーズの無意味な繰り返し
- 技術トラブル・配信準備中

{chr(10).join(clips_info)}

各クリップについて、JSON配列で回答してください。
形式: [{{"clip": 1, "valid": true/false, "reason": "理由"}}]
回答（JSONのみ）:"""

        response = client.responses.create(
            model=azure_model,
            input=[{"role": "user", "content": prompt}],
            max_output_tokens=500,
        )

        result_text = ""
        if hasattr(response, "output_text") and response.output_text:
            result_text = response.output_text.strip()
        elif hasattr(response, "output") and response.output:
            for item in response.output:
                if hasattr(item, "content"):
                    for part in item.content:
                        if hasattr(part, "text"):
                            result_text += part.text
            result_text = result_text.strip()

        # JSONパース
        json_match = re.search(r'\[.*\]', result_text, re.DOTALL)
        if json_match:
            validations = json.loads(json_match.group())
            for i, clip in enumerate(clips):
                is_valid = True
                for v in validations:
                    if v.get("clip") == i + 1:
                        is_valid = v.get("valid", True)
                        if not is_valid:
                            logger.info(
                                f"[ai-clip {job_id}] GPT rejected clip {clip['clip_id']}: "
                                f"{v.get('reason', 'unknown')}"
                            )
                        break
                results.append({"clip_id": str(clip["clip_id"]), "is_valid": is_valid})
        else:
            results = [{"clip_id": str(c["clip_id"]), "is_valid": True} for c in clips]

    except Exception as e:
        logger.warning(f"[ai-clip {job_id}] GPT validation failed: {e}")
        results = [{"clip_id": str(c["clip_id"]), "is_valid": True} for c in clips]

    return results


async def _post_generation_quality_check(
    result: dict, clip: dict, job_id: str
) -> dict:
    """
    V2.19生成後品質チェック: 生成されたクリップの最終品質を検証。
    - 出力動画の尺が短すぎないか
    - 字幕数が適切か
    - フックテキストが生成されているか
    - V2.19: 無音比率が高すぎるクリップを排除
    """
    if result.get("status") != "done":
        return result

    quality_flags = []
    duration = result.get("duration_sec", 0)
    captions_count = result.get("captions_count", 0)
    hook_text = result.get("hook_text", "")
    captions = result.get("captions", [])

    # 尺チェック
    if duration < 5:
        quality_flags.append("too_short")
    # 字幕チェック
    if captions_count == 0 and duration > 10:
        quality_flags.append("no_captions")
    # フックチェック
    if not hook_text:
        quality_flags.append("no_hook")

    # V2.19: 無音比率チェック —— 発話がクリップ全体の30%未満なら低品質と判定
    if duration > 0 and captions and isinstance(captions, list):
        speech_time = 0.0
        for cap in captions:
            if isinstance(cap, dict):
                cap_start = float(cap.get("start", 0))
                cap_end = float(cap.get("end", 0))
                if cap_end > cap_start:
                    speech_time += (cap_end - cap_start)
        speech_ratio = speech_time / duration if duration > 0 else 0
        result["speech_ratio"] = round(speech_ratio, 3)

        if speech_ratio < 0.25:
            # V11: 発話率25%未満: 無音が多すぎる → 完成リストから除外 (V10は0.15だったが、NG率改善のため0.25に引き上げ)
            quality_flags.append("mostly_silent")
            result["status"] = "rejected"
            result["rejection_reason"] = f"発話率が低すぎます ({speech_ratio*100:.0f}%)。クリップの大部分が無音です。"
            logger.warning(
                f"[ai-clip {job_id}] REJECTED clip {result.get('clip_id')}: "
                f"speech_ratio={speech_ratio:.2f} (<0.25), mostly silent"
            )
        elif speech_ratio < 0.40:
            # V11: 0.30→0.40 警告範囲も拡大
            quality_flags.append("low_speech")
            logger.info(
                f"[ai-clip {job_id}] Low speech ratio for clip {result.get('clip_id')}: "
                f"{speech_ratio:.2f} (warning only)"
            )

    if quality_flags:
        result["quality_warnings"] = quality_flags
        if result.get("status") != "rejected":
            logger.warning(
                f"[ai-clip {job_id}] Quality warnings for clip {result.get('clip_id')}: {quality_flags}"
            )

    # 品質スコアを結果に含める
    result["quality_score"] = clip.get("_quality_score", 0)

    return result


async def _process_single_clip_v2(job_id: str, clip: dict, req: GenerateRequest,
                                    idx: int, total: int) -> dict:
    """V2: 1つのクリップを全自動処理する（全エフェクト付き）"""
    import httpx

    clip_id = str(clip["clip_id"])
    clip_url = clip["clip_url"]
    captions = clip.get("captions")
    product_name = clip.get("product_name") or ""
    tmp_dir = tempfile.mkdtemp(prefix=f"ai_clip_{clip_id[:8]}_")

    try:
        # ── 1. Download clip ── (3%)
        await _update_job(job_id, progress_pct=3, current_step=f"クリップ {idx+1}/{total}: ダウンロード準備中...")
        video_path = os.path.join(tmp_dir, "input.mp4")

        download_url = clip_url
        if "?" not in download_url or "sig=" not in download_url:
            try:
                from app.services.storage_service import generate_read_sas_from_url
                sas_url = generate_read_sas_from_url(download_url, expires_hours=2)
                if sas_url:
                    download_url = sas_url
            except Exception as e:
                logger.warning(f"[ai-clip {job_id}] SAS generation failed: {e}")

        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("GET", download_url) as resp:
                resp.raise_for_status()
                total_bytes = int(resp.headers.get('content-length', 0))
                downloaded = 0
                last_dl_pct = 3
                with open(video_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_bytes > 0:
                            dl_pct = 3 + int((downloaded / total_bytes) * 4)  # 3% to 7%
                            if dl_pct > last_dl_pct:
                                last_dl_pct = dl_pct
                                await _update_job(job_id, progress_pct=min(dl_pct, 7),
                                    current_step=f"\u30af\u30ea\u30c3\u30d7 {idx+1}/{total}: \u30c0\u30a6\u30f3\u30ed\u30fc\u30c9\u4e2d {downloaded//1024}KB/{total_bytes//1024}KB")

        file_size = os.path.getsize(video_path)
        logger.info(f"[ai-clip {job_id}] Downloaded clip: {file_size} bytes")
        await _update_job(job_id, progress_pct=8, current_step=f"クリップ {idx+1}/{total}: ダウンロード完了 ({file_size//1024}KB)")

        # ── 2. Get video info ── (10%)
        probe_cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", video_path
        ]
        probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
        probe_data = json.loads(probe_result.stdout) if probe_result.returncode == 0 else {}

        video_width = 1080
        video_height = 1920
        video_fps = 30  # V2.22: default, will be overridden by ffprobe
        duration = clip.get("duration_sec") or 30.0
        for stream in probe_data.get("streams", []):
            if stream.get("codec_type") == "video":
                video_width = int(stream.get("width", 1080))
                video_height = int(stream.get("height", 1920))
                if "duration" in stream:
                    duration = float(stream["duration"])
                # V2.22: 帧率を取得（VFR対応のためfps過滤器に使用）
                r_frame_rate = stream.get("r_frame_rate", "30/1")
                try:
                    num, den = r_frame_rate.split("/")
                    video_fps = round(int(num) / int(den))
                except (ValueError, ZeroDivisionError):
                    video_fps = 30
                # 帧率を30-60の範囲にクランプ
                video_fps = max(24, min(60, video_fps))
                break
        if "format" in probe_data and "duration" in probe_data["format"]:
            duration = float(probe_data["format"]["duration"])

        await _update_job(job_id, progress_pct=10, current_step=f"クリップ {idx+1}/{total}: 動画情報取得中...")

        # ── 2b. V12: 編集プロファイル適用 ──
        editing_profile_params = {}
        if getattr(req, 'editing_profile_id', None):
            try:
                editing_profile_params = await _load_editing_profile(req.editing_profile_id)
                if editing_profile_params:
                    logger.info(f"[ai-clip {job_id}] Applying editing profile: {req.editing_profile_id} "
                                f"params={list(editing_profile_params.keys())}")
                    # プロファイルのパラメータでreqをオーバーライド
                    req = _apply_editing_profile_to_request(req, editing_profile_params)
            except Exception as ep_err:
                logger.warning(f"[ai-clip {job_id}] Editing profile load failed (non-fatal): {ep_err}")

        # ── 3. Audio analysis (V2: volume peaks + silence detection) ── (12-20%)
        volume_peaks = []
        silence_periods = []
        keep_segments = [(0, duration)]

        if req.enable_zoom_pulse:
            await _update_job(job_id, progress_pct=12, current_step=f"クリップ {idx+1}/{total}: 音声分析中（ズームポイント検出）...")
            volume_peaks = _detect_volume_peaks(video_path)
            await _update_job(job_id, progress_pct=16, current_step=f"クリップ {idx+1}/{total}: ズームポイント{len(volume_peaks)}件検出")

        if req.enable_silence_cut:
            await _update_job(job_id, progress_pct=18, current_step=f"クリップ {idx+1}/{total}: 無音区間検出中...")
            silence_periods = _detect_silence_periods(
                video_path, noise_db=req.silence_threshold_db, min_duration=0.25  # V2.27: 0.25s以上の無音を検出（0.3s以上を完全削除）
            )
            await _update_job(job_id, progress_pct=20, current_step=f"クリップ {idx+1}/{total}: 無音{len(silence_periods)}区間検出")

        # ── 4. Transcribe ── (22-35%)
        if not captions:
            await _update_job(job_id, progress_pct=22, current_step=f"クリップ {idx+1}/{total}: 音声認識中 (Whisper)...")
            captions = await _transcribe_clip(video_path, req.target_language)
            await _update_job(job_id, progress_pct=35, current_step=f"クリップ {idx+1}/{total}: 音声認識完了")
        else:
            await _update_job(job_id, progress_pct=35, current_step=f"クリップ {idx+1}/{total}: 既存字幕使用")

        if isinstance(captions, str):
            try:
                captions = json.loads(captions)
            except Exception:
                captions = []
        if not captions:
            captions = []
        # Always merge short segments first (V2.32), then split long ones
        if captions:
            captions = _merge_short_segments(captions)
            captions = _split_long_segments(captions)

        # ── 4b. GPT content relevance analysis (V11: 商品無関係区間の検出) ──
        filler_cut_segments = []
        if getattr(req, 'enable_content_cut', True) and captions:
            await _update_job(job_id, progress_pct=36, current_step=f"クリップ {idx+1}/{total}: コンテンツ関連性分析中 (GPT)...")
            try:
                filler_cut_segments = await _analyze_content_relevance(captions, product_name, duration, editing_profile_params)
                logger.info(f"[ai-clip {job_id}] Content cut: {len(filler_cut_segments)} irrelevant segments detected")
            except Exception as content_err:
                logger.warning(f"[ai-clip {job_id}] Content relevance analysis failed (non-fatal): {content_err}")
                filler_cut_segments = []

        # ── 4c. Build silence trim segments (after captions, to protect caption regions) ──
        # V13: enable_content_cutがTrueの場合、filler_cut_segmentsだけでもkeep_segmentsを更新
        if (req.enable_silence_cut and (silence_periods or filler_cut_segments)) or \
           (getattr(req, 'enable_content_cut', True) and filler_cut_segments):
            keep_segments = _build_silence_trim_segments(
                duration, silence_periods, captions,
                filler_cut_segments=filler_cut_segments
            )

        # ── 5. Generate zoom keyframes ──
        zoom_keyframes = []
        if req.enable_zoom_pulse and (volume_peaks or captions):
            zoom_keyframes = _generate_zoom_keyframes(
                duration, volume_peaks, captions, max_zoom=req.zoom_intensity
            )

        # V2.34.5: Scene change zoom burst廃止 — 切点は直接拼接
        scene_change_times = []

        # ── 6. Hook generation ── (38%)
        hook_text = None
        if req.enable_hook:
            await _update_job(job_id, progress_pct=38, current_step=f"クリップ {idx+1}/{total}: フックテキスト生成中...")
            hook_text = await _generate_hook(captions, clip, req)
            await _update_job(job_id, progress_pct=42, current_step=f"クリップ {idx+1}/{total}: フック生成完了")

        # ── 7. CTA generation (V2) ── (44%)
        cta_text = None
        if req.enable_cta:
            await _update_job(job_id, progress_pct=44, current_step=f"クリップ {idx+1}/{total}: CTAテキスト生成中...")
            cta_text = _generate_cta_text(captions, clip)

        # ── 7b. V2.33: 卖点キーワード生成 ── (45%)
        selling_points_timed = []
        if product_name and captions:
            await _update_job(job_id, progress_pct=45, current_step=f"クリップ {idx+1}/{total}: 卖点キーワード生成中 (GPT)...")
            try:
                # product_masterからキーワードを取得試行
                pm_keywords = []
                try:
                    async with get_session() as session:
                        pm_result = await session.execute(text("""
                            SELECT keywords FROM product_master
                            WHERE is_active = TRUE
                              AND (LOWER(product_name) LIKE '%' || LOWER(:name) || '%'
                                   OR LOWER(:name) LIKE '%' || LOWER(product_name) || '%')
                            LIMIT 1
                        """), {"name": product_name.strip()})
                        pm_row = pm_result.fetchone()
                        if pm_row and pm_row.keywords:
                            pm_keywords = pm_row.keywords
                except Exception:
                    pass  # product_masterがなくても続行

                selling_points_raw = await _generate_selling_points(
                    captions, product_name, pm_keywords
                )
                if selling_points_raw:
                    selling_points_timed = _assign_selling_point_timings(
                        selling_points_raw, captions, clip_duration if clip_duration > 0 else duration
                    )
                    logger.info(f"[ai-clip {job_id}] V2.33: {len(selling_points_timed)} selling points ready")
            except Exception as sp_err:
                logger.warning(f"[ai-clip {job_id}] V2.33 Selling point generation failed (non-fatal): {sp_err}")
                selling_points_timed = []

        # ── 7c. V2.35: Translate captions text (before subtitle rendering) ──
        if getattr(req, 'output_language', 'original') != 'original' and captions:
            await _update_job(job_id, progress_pct=45, current_step=f"クリップ {idx+1}/{total}: 字幕テキスト翻訳中 ({req.output_language})...")
            try:
                captions = await _translate_captions_text(captions, req.output_language, job_id)
                logger.info(f"[ai-clip {job_id}] Captions translated to {req.output_language}")
            except Exception as cap_translate_err:
                logger.warning(f"[ai-clip {job_id}] Caption translation failed (non-fatal): {cap_translate_err}")
                # Keep original captions if translation fails

        # ── 8. Scene classification & style assignment ── (46%)
        await _update_job(job_id, progress_pct=46, current_step=f"クリップ {idx+1}/{total}: シーン分析中...")
        styled_captions = _assign_scene_styles(captions, duration, req.subtitle_style)

        # ── 9. Generate Pillow overlay images (V2.10) ── (48%)
        await _update_job(job_id, progress_pct=48, current_step=f"クリップ {idx+1}/{total}: 字幕画像生成中 (Pillow)...")
        font_path = _find_cjk_font()
        # Calculate effective clip duration (limited by max_duration)
        clip_duration = min(duration, getattr(req, 'max_duration', 90))
        overlay_images = _generate_overlay_images(
            styled_captions=styled_captions,
            hook_text=hook_text,
            cta_text=cta_text,
            video_width=video_width,
            video_height=video_height,
            duration=duration,
            font_path=font_path,
            tmp_dir=tmp_dir,
            position_y=req.position_y,
            clip_duration=clip_duration,
            product_name=product_name,
            selling_points=selling_points_timed,
        )
        logger.info(f"[ai-clip {job_id}] Generated {len(overlay_images)} overlay images "
                    f"(clip_duration={clip_duration:.1f}s, selling_points={len(selling_points_timed)})")

        # Also generate ASS file as backup metadata (not used for rendering)
        ass_path = os.path.join(tmp_dir, "subtitles.ass")
        try:
            _generate_enhanced_ass(
                styled_captions, hook_text, cta_text, ass_path,
                video_width, video_height, duration, req.position_y,
                product_name=product_name,
                enable_animations=req.enable_subtitle_animation,
                enable_highlights=req.enable_keyword_highlight,
            )
        except Exception as ass_err:
            logger.warning(f"[ai-clip {job_id}] ASS generation failed (non-fatal): {ass_err}")

        await _update_job(job_id, progress_pct=52, current_step=f"クリップ {idx+1}/{total}: 字幕{len(overlay_images)}枚生成完了")

         # ── 10. Video mode processing & Build ffmpeg command ── (54%)
        video_mode = clip.get("video_mode", "original")
        product_imgs = clip.get("product_image_urls") or []

        # ── 10a. 商品マスター自動連携: product_nameから画像を自動取得 ──
        if not product_imgs and product_name and video_mode in ("product_overlay", "audio_only"):
            try:
                master_imgs = await _get_product_images_from_master(product_name)
                if master_imgs:
                    product_imgs = master_imgs
                    logger.info(f"[ai-clip {job_id}] Auto-matched product master: '{product_name}' -> {len(master_imgs)} images")
            except Exception as pm_err:
                logger.warning(f"[ai-clip {job_id}] Product master lookup failed: {pm_err}")

        output_path = os.path.join(tmp_dir, "output.mp4")
        if video_mode == "audio_only" and product_imgs:
            # 音声+商品スライドショーモード: 元映像の音声を保持し、商品画像のスライドショーを映像として使用
            await _update_job(job_id, progress_pct=54, current_step=f"クリップ {idx+1}/{total}: 商品スライドショー生成中...")
            slideshow_path = await _generate_product_slideshow(
                product_imgs, duration, video_width, video_height, tmp_dir, job_id
            )
            if slideshow_path and os.path.exists(slideshow_path):
                video_path = slideshow_path  # 映像をスライドショーに差し替え
                logger.info(f"[ai-clip {job_id}] Using product slideshow as video source")

        elif video_mode == "product_overlay" and (product_imgs or clip.get("product_video_urls")):
            # V13: PiPモード改善 - 時間帯分離表示、角配置、ループなし
            # 商品動画と商品画像がある場合:
            #   前半: 商品動画を1回完全再生（右下角）
            #   後半: 商品画像を順番に1回ずつ表示（右下角、ループなし）
            # 商品画像のみ: 順番に1回ずつ表示（右下角、ループなし）
            # 商品動画のみ: 1回完全再生（右下角）
            product_videos = clip.get("product_video_urls") or []
            pip_applied = False

            if product_videos and product_imgs:
                # V13: 時間帯分離 - 動画→画像の順番で表示
                await _update_job(job_id, progress_pct=54, current_step=f"クリップ {idx+1}/{total}: PiP合成中（時間帯分離）...")
                pip_combined_path = await _generate_pip_combined_sequential(
                    video_path, product_videos, product_imgs, duration,
                    video_width, video_height, tmp_dir, job_id
                )
                if pip_combined_path and os.path.exists(pip_combined_path):
                    video_path = pip_combined_path
                    pip_applied = True
                    logger.info(f"[ai-clip {job_id}] V13 PiP sequential applied (videos={len(product_videos)}, images={len(product_imgs)})")
            elif product_imgs:
                # 商品画像のみ: 角に配置、ループなし
                await _update_job(job_id, progress_pct=54, current_step=f"クリップ {idx+1}/{total}: PiP合成中（商品画像）...")
                pip_img_path = await _generate_pip_video(
                    video_path, product_imgs, duration, video_width, video_height, tmp_dir, job_id
                )
                if pip_img_path and os.path.exists(pip_img_path):
                    video_path = pip_img_path
                    pip_applied = True
                    logger.info(f"[ai-clip {job_id}] PiP image overlay applied ({len(product_imgs)} images)")
            elif product_videos:
                # 商品動画のみ: 1回完全再生
                await _update_job(job_id, progress_pct=56, current_step=f"クリップ {idx+1}/{total}: PiP合成中（商品動画）...")
                pip_vid_path = await _generate_pip_video_overlay(
                    video_path, product_videos, duration, video_width, video_height, tmp_dir, job_id
                )
                if pip_vid_path and os.path.exists(pip_vid_path):
                    video_path = pip_vid_path
                    pip_applied = True
                    logger.info(f"[ai-clip {job_id}] PiP video overlay applied ({len(product_videos)} videos)")

            if pip_applied:
                logger.info(f"[ai-clip {job_id}] V13 PiP composite applied (images={len(product_imgs)}, videos={len(product_videos)})")
            else:
                logger.warning(f"[ai-clip {job_id}] PiP generation failed for all inputs (images={len(product_imgs)}, videos={len(product_videos)})")

        await _update_job(job_id, progress_pct=58, current_step=f"クリップ {idx+1}/{total}: エンコード準備中...")

        # ── V2.29: Pre-splice silence trim (concat-based, eliminates frame regression) ──
        # Instead of using select filter inside ffmpeg (which causes 1-frame regression at cut points),
        # pre-splice the keep_segments into a clean intermediate file using concat demuxer.
        if len(keep_segments) > 1:
            await _update_job(job_id, progress_pct=58, current_step=f"クリップ {idx+1}/{total}: 無音カット結合中 (V2.29)...")
            spliced_path, spliced_duration = await _pre_splice_segments(
                video_path, keep_segments, tmp_dir, video_fps
            )
            if spliced_path and os.path.exists(spliced_path):
                # Remap overlay_images and zoom_keyframes to the new timeline BEFORE replacing
                overlay_images = _remap_overlay_images_for_silence_trim(overlay_images, keep_segments)
                zoom_keyframes = _remap_zoom_keyframes_for_silence_trim(zoom_keyframes, keep_segments)
                # Replace video_path with spliced file and reset keep_segments
                video_path = spliced_path
                duration = spliced_duration
                keep_segments = [(0, spliced_duration)]  # Single segment = no select filter needed
                logger.info(f"[ai-clip {job_id}] V2.29: Using pre-spliced file "
                            f"(duration={spliced_duration:.2f}s, no select filter needed)")
            else:
                logger.warning(f"[ai-clip {job_id}] V2.29: Pre-splice failed, falling back to select filter")

        ffmpeg_cmd = _build_advanced_ffmpeg_command(
            video_path, ass_path, output_path,
            video_width, video_height, duration, req,
            zoom_keyframes=zoom_keyframes,
            keep_segments=keep_segments,
            enable_progress_bar=req.enable_progress_bar,
            enable_flash_intro=req.enable_flash_intro,
            enable_loop_fade=req.enable_loop_fade,
            styled_captions=styled_captions,
            hook_text=hook_text,
            cta_text=cta_text,
            position_y=req.position_y,
            overlay_images=overlay_images,
            video_fps=video_fps,
            scene_change_times=scene_change_times,
        )
        ffmpeg_cmd_str = ' '.join(ffmpeg_cmd)
        logger.info(f"[ai-clip {job_id}] ffmpeg V2 cmd: {ffmpeg_cmd_str}")
        await _update_job(job_id, progress_pct=60, current_step=f"\u30af\u30ea\u30c3\u30d7 {idx+1}/{total}: \u30a8\u30f3\u30b3\u30fc\u30c9\u4e2d (ffmpeg)...")

        # Run ffmpeg with -progress pipe:1 for structured progress output
        ffmpeg_cmd_with_progress = ffmpeg_cmd[:1] + ["-progress", "pipe:1"] + ffmpeg_cmd[1:]
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd_with_progress,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Read stdout (progress) and stderr concurrently
        last_encode_pct = 60

        async def _read_progress_stdout():
            """Read ffmpeg -progress output from stdout (key=value format)"""
            nonlocal last_encode_pct
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                line_str = line.decode(errors='replace').strip()
                # ffmpeg -progress outputs: out_time_us=12345678
                if line_str.startswith('out_time_us='):
                    try:
                        us = int(line_str.split('=')[1])
                        elapsed = us / 1_000_000.0
                        encode_pct = min(elapsed / max(duration, 1), 1.0)
                        new_pct = 60 + int(encode_pct * 14)  # 60% to 74%
                        if new_pct > last_encode_pct:
                            last_encode_pct = new_pct
                            await _update_job(job_id, progress_pct=new_pct,
                                current_step=f"\u30af\u30ea\u30c3\u30d7 {idx+1}/{total}: \u30a8\u30f3\u30b3\u30fc\u30c9\u4e2d {int(encode_pct*100)}%")
                    except (ValueError, IndexError):
                        pass

        async def _read_stderr_collect():
            """Collect stderr for error reporting"""
            data = await proc.stderr.read()
            return data.decode(errors='replace') if data else ""

        # Read progress and stderr concurrently, then wait for process
        _, ffmpeg_stderr_str = await asyncio.gather(
            _read_progress_stdout(),
            _read_stderr_collect(),
        )
        await asyncio.wait_for(proc.wait(), timeout=600)

        logger.info(f"[ai-clip {job_id}] ffmpeg stderr (last 500): {ffmpeg_stderr_str[-500:]}")

        if proc.returncode != 0:
            logger.error(f"[ai-clip {job_id}] ffmpeg FULL stderr:\n{ffmpeg_stderr_str}")
            raise RuntimeError(f"ffmpeg failed: {ffmpeg_stderr_str[-800:]}")

        output_size = os.path.getsize(output_path)
        logger.info(f"[ai-clip {job_id}] Encoded V2: {output_size} bytes")

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

        # ── 10.5. Sound Effects (SE) insertion ──
        if getattr(req, 'enable_sfx', True):
            se_output = output_path + ".se.mp4"
            cta_start_time = max(0, actual_duration - 3.0) if cta_text else None
            se_applied = await _apply_sound_effects(
                video_path=output_path,
                output_path=se_output,
                duration=actual_duration,
                hook_time=0.0,
                cta_time=cta_start_time,
                enable_hook_se=bool(hook_text),
                enable_cta_se=bool(cta_text),
            )
            if se_applied and os.path.exists(se_output) and os.path.getsize(se_output) > 1000:
                os.replace(se_output, output_path)
                output_size = os.path.getsize(output_path)
                logger.info(f"[ai-clip {job_id}] SE applied, new size: {output_size} bytes")
            else:
                # Clean up failed SE output
                if os.path.exists(se_output):
                    os.unlink(se_output)
                logger.info(f"[ai-clip {job_id}] SE skipped (not available or failed)")

        await _update_job(job_id, progress_pct=75, current_step=f"クリップ {idx+1}/{total}: エンコード完了 ({output_size//1024}KB)")

        # ── 10.8. Language Translation + Lip Sync (V2.35) ──
        if getattr(req, 'output_language', 'original') != 'original':
            await _update_job(job_id, progress_pct=76, current_step=f"クリップ {idx+1}/{total}: 言語翻訳+リップシンク中 ({req.output_language})...")
            try:
                translated_path = await _translate_and_lipsync_clip(
                    video_path=output_path,
                    captions=captions,
                    target_lang=req.output_language,
                    voice_id=getattr(req, 'voice_id', None),
                    tmp_dir=tmp_dir,
                    job_id=job_id,
                    clip_id=clip_id,
                )
                if translated_path and os.path.exists(translated_path):
                    os.replace(translated_path, output_path)
                    output_size = os.path.getsize(output_path)
                    logger.info(f"[ai-clip {job_id}] Translation + lip sync applied: {req.output_language}, {output_size} bytes")
                    await _update_job(job_id, progress_pct=79, current_step=f"クリップ {idx+1}/{total}: 翻訳+リップシンク完了")
                else:
                    logger.warning(f"[ai-clip {job_id}] Translation returned no output, keeping original")
            except Exception as translate_err:
                logger.error(f"[ai-clip {job_id}] Translation + lip sync failed (non-fatal): {translate_err}", exc_info=True)
                # Non-fatal: keep original video if translation fails

        # ── 11. Enhanced thumbnail (V2) ── (80%)
        thumbnail_url = None
        if req.enable_thumbnail:
            await _update_job(job_id, progress_pct=80, current_step=f"クリップ {idx+1}/{total}: サムネイル生成中...")
            thumbnail_url = await _generate_enhanced_thumbnail(
                output_path, tmp_dir, clip_id,
                hook_text=hook_text or "", product_name=product_name,
            )

        # ── 12. Upload to Azure Blob Storage ── (85%)
        await _update_job(job_id, progress_pct=85, current_step=f"クリップ {idx+1}/{total}: アップロード中 ({output_size//1024}KB)...")
        download_url, blob_url = await _upload_to_blob(output_path, clip_id, job_id)
        await _update_job(job_id, progress_pct=92, current_step=f"クリップ {idx+1}/{total}: アップロード完了")

        # ── 13. Save to DB ── (95%)
        await _update_job(job_id, progress_pct=95, current_step=f"クリップ {idx+1}/{total}: DB保存中...")
        await _save_export_record(clip_id, blob_url, thumbnail_url,
                                      exported_duration=actual_duration)

        return {
            "clip_id": clip_id,
            "status": "done",
            "download_url": download_url,
            "blob_url": blob_url,
            "thumbnail_url": thumbnail_url,
            "file_size": output_size,
            "duration_sec": actual_duration,
            "hook_text": hook_text,
            "cta_text": cta_text,
            "captions_count": len(captions),
            "captions": captions,  # 字幕データを保存（後で編集可能にするため）
            "zoom_points": len(zoom_keyframes),
            "silence_cuts": max(0, len(keep_segments) - 1),
            "effects_applied": {
                "silence_cut": req.enable_silence_cut and len(keep_segments) > 1,
                "zoom_pulse": req.enable_zoom_pulse and len(zoom_keyframes) > 0,
                "progress_bar": req.enable_progress_bar,
                "flash_intro": req.enable_flash_intro,
                "loop_fade": req.enable_loop_fade,
                "cta": req.enable_cta and bool(cta_text),
                "keyword_highlight": req.enable_keyword_highlight,
                "subtitle_animation": req.enable_subtitle_animation,
            },
            "ffmpeg_cmd": ffmpeg_cmd_str[-2000:],  # Last 2000 chars for debugging
            "ffmpeg_stderr": ffmpeg_stderr_str[-1000:],  # Last 1000 chars
        }

    except Exception as e:
        raise
    finally:
        import shutil
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


# ─── Whisper Transcription (unchanged from V1, proven working) ───────────────


def _merge_short_segments(segments: list, max_chars: int = 18, max_gap: float = 0.15) -> list:
    """V2.32: 短いWhisperセグメントを結合して単語切断を防止する。

    Whisperは時々、連続した発話を非常に短いセグメントに分割する。
    例: 「クーポン」→「クーポ」(seg1) +「ンによって」(seg2)
    これにより字幕が不自然に切断される。

    2パス結合:
    Pass 1 (forward): 極短フラグメント(<6文字)を次のセグメントに前方結合
    Pass 2 (backward): 残りの短セグメントを前のセグメントに後方結合
    """
    if not segments or len(segments) < 2:
        return segments

    _SENTENCE_END = set('。！？!?…')
    FRAGMENT_THRESHOLD = 6  # Characters below this = word fragment

    # ── Pass 1: Forward merge fragments ──
    # Very short segments (<6 chars) are likely word fragments.
    # Merge them FORWARD into the next segment (not backward into prev).
    forward_merged = []
    pending_fragment = None  # Fragment waiting to be prepended to next segment

    for seg in segments:
        seg = dict(seg)  # shallow copy
        text = seg.get('text', '').strip()

        if pending_fragment is not None:
            # Prepend the pending fragment to this segment
            frag_text = pending_fragment.get('text', '').strip()
            frag_start = float(pending_fragment.get('start', 0))
            seg_end = float(seg.get('end', 0))
            gap = float(seg.get('start', 0)) - float(pending_fragment.get('end', 0))
            combined = frag_text + text

            if gap <= max_gap and len(combined) <= max_chars and (seg_end - frag_start) <= 4.0:
                # Merge fragment into this segment
                seg['text'] = combined
                seg['start'] = pending_fragment['start']
                forward_merged.append(seg)
            else:
                # Can't merge - keep both separately
                forward_merged.append(pending_fragment)
                forward_merged.append(seg)
            pending_fragment = None
        elif len(text) < FRAGMENT_THRESHOLD and not any(text.endswith(c) for c in _SENTENCE_END):
            # This is a fragment - hold it for forward merge
            pending_fragment = seg
        else:
            forward_merged.append(seg)

    # If last segment was a fragment, just append it
    if pending_fragment is not None:
        forward_merged.append(pending_fragment)

    # ── Pass 2: Backward merge remaining short segments ──
    # Merge short segments (<max_chars) backward into previous if:
    # - gap is small, combined fits, and previous doesn't end with sentence-end
    if len(forward_merged) < 2:
        result = forward_merged
    else:
        result = [dict(forward_merged[0])]
        for i in range(1, len(forward_merged)):
            prev = result[-1]
            curr = forward_merged[i]

            prev_text = prev.get('text', '').strip()
            curr_text = curr.get('text', '').strip()
            prev_end = float(prev.get('end', 0))
            curr_start = float(curr.get('start', 0))
            curr_end = float(curr.get('end', 0))

            gap = curr_start - prev_end
            combined_text = prev_text + curr_text
            combined_duration = curr_end - float(prev.get('start', 0))

            should_merge = (
                gap <= max_gap
                and len(curr_text) < FRAGMENT_THRESHOLD  # Only merge fragments backward
                and len(combined_text) <= max_chars
                and combined_duration <= 4.0
                and not any(prev_text.endswith(c) for c in _SENTENCE_END)
            )

            if should_merge:
                result[-1]['text'] = combined_text
                result[-1]['end'] = curr_end
            else:
                result.append(dict(curr))

    if len(result) != len(segments):
        logger.info(f"[ai-clip] V2.32 merged short segments: {len(segments)} -> {len(result)}")

    return result


def _split_long_segments(segments: list, max_chars: int = 18, max_duration: float = 3.5) -> list:
    """Whisperセグメントを一文ずつに分割する（字幕多行表示問題の根本修正）。
    
    Whisperは1セグメントに複数文を含むことがある（5-15秒、30-100文字）。
    これをそのまま字幕にすると画面に複数行が同時表示される。
    
    分割ルール:
    1. 句読点（。、！、？、…）で分割
    2. max_chars以上のテキストは読点（、，）で分割
    3. それでも長い場合は意味的に自然な位置（助詞・接続助詞の後）で分割
    4. 分割後の各パートに元セグメントの時間を文字数比例で配分
    5. 短いセグメント（max_chars以下 かつ max_duration以下）はそのまま
    
    V2.20改善:
    - 助詞パターンを大幅拡張（単独助詞 + 複合助詞 + 接続助詞）
    - 文法的に不自然な位置での分割を防止（助詞の直前ではなく直後で切る）
    - 短すぎる断片（4文字未満）は前のチャンクにマージ
    - 「ので」「から」「けど」「ても」等の接続助詞を優先分割位置に
    """
    import re as _re
    
    # V2.20: 意味的に自然な分割位置パターン（優先度順）
    # Pythonのlookbehindは固定長のみ対応なので、文字数別にパターンを分割
    # 接続助詞・複合助詞の後が最も自然な分割位置
    _CONJ_2CHAR = _re.compile(r'(?<=ので|から|けど|ても|のに|たら|れば|ると|って|のは|のが|ては|では|には|とは|まで|より|ほど|だけ|しか)')
    _CONJ_3CHAR = _re.compile(r'(?<=けれど|ながら|ばかり|ことが|ことを|ことは|ために|として|ところ)')
    _CONJ_4CHAR = _re.compile(r'(?<=について|に対して|によって)')
    # 単独助詞の後（接続助詞より優先度低い）
    _PARTICLE_SPLIT_PATTERN = _re.compile(
        r'(?<=[はがをにでもとのへよねば])'
    )
    
    def _split_at_natural_boundary(text: str, target_len: int) -> list:
        """max_chars以上のテキストを意味的に自然な位置で分割
        
        分割優先度:
        1. 接続助詞・複合助詞の後（最も自然）
        2. 単独助詞の後
        3. 強制分割（最終手段）
        """
        if len(text) <= target_len:
            return [text]
        
        # Step 1: Try splitting at conjunction/compound particle boundaries first
        # Collect positions from all conjunction patterns (2/3/4 char lookbehinds)
        conj_positions = []
        for pat in [_CONJ_2CHAR, _CONJ_3CHAR, _CONJ_4CHAR]:
            conj_positions.extend(m.start() for m in pat.finditer(text))
        conj_positions = sorted(set(conj_positions))
        # Step 2: Also collect single particle positions as fallback
        particle_positions = [m.start() for m in _PARTICLE_SPLIT_PATTERN.finditer(text)]
        
        # Combine: prefer conjunction positions, then particle positions
        # Mark each position with priority (0=conjunction=better, 1=particle)
        all_positions = [(p, 0) for p in conj_positions] + [(p, 1) for p in particle_positions]
        # Remove duplicates, keep lowest priority (best)
        seen = {}
        for pos, pri in all_positions:
            if pos not in seen or pri < seen[pos]:
                seen[pos] = pri
        # Sort by position
        sorted_positions = sorted(seen.keys())
        
        if not sorted_positions:
            # No natural split points found - force split at target_len
            chunks = []
            for i in range(0, len(text), target_len):
                chunk = text[i:i+target_len]
                if chunk:
                    chunks.append(chunk)
            return chunks
        
        # Greedy split: find the best split position near target_len
        chunks = []
        last_cut = 0
        
        while last_cut < len(text):
            remaining_text = text[last_cut:]
            if len(remaining_text) <= target_len:
                # Remaining text fits in one chunk
                if chunks and len(remaining_text) < 4:
                    # Too short, merge with previous
                    chunks[-1] += remaining_text
                else:
                    chunks.append(remaining_text)
                break
            
            # Find best split position: closest to target_len but not exceeding it too much
            best_pos = None
            best_score = float('inf')
            
            for pos in sorted_positions:
                if pos <= last_cut:
                    continue
                chunk_len = pos - last_cut
                # Prefer positions that produce chunks close to target_len
                # but not shorter than 6 chars (too short = unnatural)
                if chunk_len < 6:
                    continue
                # Score: distance from target_len (lower is better)
                if chunk_len <= target_len:
                    score = abs(target_len - chunk_len)
                else:
                    # Allow up to 5 chars over target_len for conjunction breaks
                    if chunk_len <= target_len + 5 and pos in conj_positions:
                        score = (chunk_len - target_len) * 1.5  # Mild penalty for conjunctions
                    elif chunk_len <= target_len + 3:
                        score = (chunk_len - target_len) * 3  # Moderate penalty
                    else:
                        score = (chunk_len - target_len) * 10  # Heavy penalty
                
                # Strong bonus for conjunction positions (much more natural break)
                if pos in conj_positions:
                    score -= 8
                
                if score < best_score:
                    best_score = score
                    best_pos = pos
            
            if best_pos is None or best_pos <= last_cut:
                # No good position found, force split
                cut_point = last_cut + target_len
                chunks.append(text[last_cut:cut_point])
                last_cut = cut_point
            else:
                chunks.append(text[last_cut:best_pos])
                last_cut = best_pos
        
        # Post-process: merge very short trailing chunks
        if len(chunks) > 1 and len(chunks[-1]) < 4:
            chunks[-2] += chunks[-1]
            chunks.pop()
        
        return chunks if chunks else [text]
    
    result = []
    for seg in segments:
        text = seg.get('text', '').strip()
        start = float(seg.get('start', 0))
        end = float(seg.get('end', 0))
        duration = end - start
        
        # Short segment: keep as-is
        if len(text) <= max_chars and duration <= max_duration:
            result.append(seg)
            continue
        
        # Step 1: Split by sentence-ending punctuation
        parts = _re.split(r'(?<=[。！？!?…])', text)
        
        # Step 2: Further split on 、， if parts are still too long
        refined_parts = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if len(part) > max_chars:
                sub_parts = _re.split(r'(?<=[、，])', part)
                for sp in sub_parts:
                    sp = sp.strip()
                    if sp:
                        refined_parts.append(sp)
            else:
                refined_parts.append(part)
        
        # Step 3: Split remaining long parts at natural boundaries (improved V2.20)
        final_parts = []
        for part in refined_parts:
            if len(part) > max_chars:
                chunks = _split_at_natural_boundary(part, max_chars)
                final_parts.extend(chunks)
            else:
                final_parts.append(part)
        
        # Step 4: Merge very short fragments (< 4 chars) with adjacent parts
        if len(final_parts) > 1:
            merged = [final_parts[0]]
            for i in range(1, len(final_parts)):
                if len(final_parts[i]) < 4:
                    # Merge with previous part (prefer keeping text together)
                    merged[-1] += final_parts[i]
                elif len(merged[-1]) < 4:
                    # Previous was too short, merge current into it
                    merged[-1] += final_parts[i]
                else:
                    merged.append(final_parts[i])
            final_parts = merged
        
        # If splitting produced nothing useful, force split by character count
        if not final_parts or (len(final_parts) == 1 and len(final_parts[0]) > max_chars):
            # Force split at fixed intervals
            forced = []
            txt = final_parts[0] if final_parts else text
            for i in range(0, len(txt), max_chars):
                chunk = txt[i:i+max_chars]
                if chunk:
                    forced.append(chunk)
            if len(forced) > 1:
                final_parts = forced
        
        # If only 1 part and it's short enough, keep original
        if len(final_parts) <= 1:
            result.append(seg)
            continue
        
        # Distribute time proportionally based on character count
        total_chars = sum(len(p) for p in final_parts)
        if total_chars == 0:
            result.append(seg)
            continue
        
        current_time = start
        for pi, part_text in enumerate(final_parts):
            char_ratio = len(part_text) / total_chars
            part_duration = duration * char_ratio
            # Ensure minimum 0.8s per part
            part_duration = max(0.8, part_duration)
            part_end = min(current_time + part_duration, end)
            # Last part gets remaining time
            if pi == len(final_parts) - 1:
                part_end = end
            
            result.append({
                'start': round(current_time, 3),
                'end': round(part_end, 3),
                'text': part_text,
            })
            current_time = part_end
    
    logger.info(f"[ai-clip] Split segments: {len(segments)} -> {len(result)} "
                f"(avg {sum(len(s.get('text','')) for s in result)/max(len(result),1):.0f} chars/segment)")
    return result


async def _transcribe_clip(video_path: str, target_language: str = "auto") -> list:
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

    whisper_lang_map = {"ja": "ja", "zh-tw": "zh", "zh": "zh"}
    is_auto = target_language == "auto"
    whisper_language = None if is_auto else whisper_lang_map.get(target_language, "ja")

    whisper_prompt = (
        "全頭ブリーチ、カラーリング、ヘアケア、ケラチン、アミノ酸、コラーゲン、"
        "頭皮、毛穴、髪質、ダメージ、補修、シャンプー、トリートメント、"
        "ヘアマスク、KYOGOKU、京極、ライブ配信、ライブコマース"
    )

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

    # Extract audio to mp3 (Whisper 25MB limit)
    whisper_file = video_path
    tmp_audio_dir = os.path.dirname(video_path)
    audio_path = os.path.join(tmp_audio_dir, "audio_for_whisper.mp3")
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-acodec", "libmp3lame", "-ar", "16000", "-ac", "1", "-b:a", "64k",
            audio_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout_audio, stderr_audio = await asyncio.wait_for(proc.communicate(), timeout=60)
        if proc.returncode == 0 and os.path.exists(audio_path):
            audio_size = os.path.getsize(audio_path)
            logger.info(f"[ai-clip] Extracted audio: {audio_size/1024/1024:.1f} MB")
            whisper_file = audio_path
        else:
            logger.warning(f"[ai-clip] Audio extraction failed, using original video")
    except Exception as audio_err:
        logger.warning(f"[ai-clip] Audio extraction error: {audio_err}")

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

        logger.info(f"[ai-clip] Whisper response type: {type(response).__name__}")

        segments = []
        if hasattr(response, "segments") and response.segments:
            for seg in response.segments:
                s = getattr(seg, "start", 0) if hasattr(seg, "start") else (seg.get("start", 0) if isinstance(seg, dict) else 0)
                e = getattr(seg, "end", 0) if hasattr(seg, "end") else (seg.get("end", 0) if isinstance(seg, dict) else 0)
                t = getattr(seg, "text", "") if hasattr(seg, "text") else (seg.get("text", "") if isinstance(seg, dict) else "")
                segments.append({"start": float(s), "end": float(e), "text": str(t).strip()})
        elif hasattr(response, "text") and response.text:
            try:
                probe_res = subprocess.run(
                    ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", whisper_file],
                    capture_output=True, text=True, timeout=10
                )
                if probe_res.returncode == 0:
                    pdata = json.loads(probe_res.stdout)
                    est_dur = float(pdata.get("format", {}).get("duration", 30))
                else:
                    est_dur = 30.0
            except Exception:
                est_dur = 30.0
            segments.append({"start": 0, "end": est_dur, "text": response.text.strip()})

        logger.info(f"[ai-clip] Transcribed: {len(segments)} segments (raw)")
        # V2.32: Merge short segments first, then split long ones
        segments = _merge_short_segments(segments)
        segments = _split_long_segments(segments)
        return segments

    except Exception as e:
        logger.error(f"[ai-clip] Whisper failed: {e}", exc_info=True)
        return []


# ─── Hook Generation (unchanged from V1) ─────────────────────────────────────

async def _generate_hook(captions: list, clip: dict, req: GenerateRequest) -> str:
    if req.hook_text:
        # カスタムフックが指定されている場合でも15文字以内に制限
        return req.hook_text[:15]

    transcript = " ".join(c.get("text", "") for c in (captions or []))[:500]
    product_name = clip.get("product_name") or ""

    if not transcript.strip():
        return _generate_simple_hook(product_name, transcript)

    try:
        import openai
        azure_key = os.getenv("AZURE_OPENAI_KEY", "")
        azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
        azure_model = os.getenv("GPT5_MODEL") or os.getenv("GPT5_DEPLOYMENT") or "gpt-4.1-mini"

        if not azure_key or not azure_endpoint:
            return _generate_simple_hook(product_name, transcript)

        from urllib.parse import urlparse as _urlparse
        _parsed = _urlparse(azure_endpoint)
        clean_endpoint = f"{_parsed.scheme}://{_parsed.netloc}/"

        client = openai.AzureOpenAI(
            api_key=azure_key,
            azure_endpoint=clean_endpoint,
            api_version=os.getenv("GPT5_API_VERSION", "2025-04-01-preview"),
        )

        prompt = f"""あなたはTikTok動画のフックテキスト専門家です。
以下の動画の書き起こし内容を読み、その内容に直接関係する短いフックテキストを生成してください。

【絶対ルール】
- 最大10文字以内（厳守！）
- 絵文字・句読点は使わない
- 【最重要】フックは「動画で実際に話している具体的な内容」から作ること！
  → 動画内容を読んで、そこに出てくる具体的な商品名・効果・特徴をフックに使う
  → 汎用的なフレーズ（知らないと損、これマジ、衝撃、ヤバい等）は絶対禁止
- 商品名/ブランド名がある場合は必ずフックに含める

【判定基準】
このフックを見て「何の動画か」が分かるか？
→ 分からない = NG（汎用フック）
→ 分かる = OK（具体的フック）

【良い例】
- 動画が靴KEENの紹介 → 「KEENが神」「KEENの本気」
- 動画が脱毛クリームの効果 → 「ツル肌の秘密」「この脱毛が神」
- 動画がシャンプーの香り → 「この香りヤバい」「髪が変わる」
- 動画がまつ毛美容液 → 「まつ毛が伸びた」「まつ育の答え」

【禁止フック（動画内容と無関係な汎用表現）】
「知らないと損」「これマジですごい」「衝撃の結果」「見ないと後悔」「まじありえない」→ 全て禁止

商品名: {product_name or '（なし）'}
動画の書き起こし内容（これを読んでフックを作る）:
{transcript[:400]}

上記の書き起こし内容に直接関係するフックテキスト（10文字以内、1つだけ出力）:"""

        response = client.responses.create(
            model=azure_model,
            input=[{"role": "user", "content": prompt}],
            max_output_tokens=30,
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
            result = result.strip('"\'「」『』【】').strip()
            # 絶対に15文字以内に制限
            if len(result) > 15:
                result = result[:15]
            # V12: 汎用フック検出バリデーション
            _BANNED_HOOKS = [
                "知らないと損", "これマジ", "衝撃の結果", "見ないと後悔",
                "まじありえない", "ヤバすぎ", "神すぎ", "ガチで",
                "マジですごい", "衰撃", "衝撃", "これ見て",
            ]
            is_generic = any(banned in result for banned in _BANNED_HOOKS)
            if is_generic:
                logger.warning(f"[ai-clip] GPT returned generic hook '{result}', falling back to product-based hook")
                return _generate_simple_hook(product_name, transcript)
            return result

    except Exception as e:
        logger.warning(f"[ai-clip] Hook generation via GPT failed: {e}")

    return _generate_simple_hook(product_name, transcript)


def _generate_simple_hook(product_name: str, transcript: str) -> str:
    """GPTが使えない場合のフォールバックフック生成。V11: 商品名必須。"""
    # 商品名がある場合は商品名を含むフック（必ず商品に関連）
    if product_name:
        # 商品名が長い場合は短縮
        short_name = product_name[:6] if len(product_name) > 6 else product_name
        product_hooks = [
            f"{short_name}が神",
            f"{short_name}の本気",
            f"{short_name}が凄い",
            f"{short_name}の答え",
            f"{short_name}で変わる",
            f"この{short_name}が正解",
            f"{short_name}の実力",
            f"{short_name}が最強",
        ]
        return random.choice(product_hooks)[:15]

    # 商品名がない場合: transcriptからキーワードを抽出してフックに使用
    # 汎用フックは最終手段
    if transcript:
        # トランスクリプトから名詞を抽出してフックに使用
        words = transcript[:100].split()
        # 最初の意味のある単語を使用
        for w in words:
            w = w.strip()
            if len(w) >= 2 and len(w) <= 8:
                return f"{w}が変わる"[:15]

    # 最終フォールバック（極力使わない）
    return "これ見て"


def _assign_scene_styles(captions: list, total_duration: float, base_style: str) -> list:
    # Validate base_style against known ASS styles
    valid_styles = list(_ASS_STYLES.keys())  # simple, box, outline, pop, gradient, karaoke
    if base_style != "auto" and base_style not in valid_styles:
        logger.warning(f"[ai-clip] Invalid subtitle_style '{base_style}', falling back to 'box'")
        base_style = "box"

    # For "auto" mode: pick a random dominant style for this clip (adds variety between clips)
    # 50% chance: use scene classification, 50% chance: use a random dominant style
    use_random_dominant = (base_style == "auto" and random.random() < 0.5)
    random_dominant = random.choice(valid_styles) if use_random_dominant else None

    styled = []
    for cap in captions:
        cap_start = float(cap.get("start", 0))
        cap_text = cap.get("text", "")
        if base_style != "auto":
            style = base_style
        elif use_random_dominant:
            # Use dominant style with occasional variation (20% chance of scene-based override)
            if random.random() < 0.2:
                scene = _classify_scene(cap_text, cap_start, total_duration)
                style = _SCENE_STYLE_MAP.get(scene, random_dominant)
            else:
                style = random_dominant
        else:
            scene = _classify_scene(cap_text, cap_start, total_duration)
            style = _SCENE_STYLE_MAP.get(scene, 'box')
        # Final safety: ensure style exists in _ASS_STYLES
        if style not in valid_styles:
            style = 'box'
        styled.append({**cap, "style": style})
    return styled



# ─── V2.35: Caption Text Translation ───────────────────────────────────────────

async def _translate_captions_text(
    captions: list,
    target_lang: str,
    job_id: str,
) -> list:
    """
    V2.35: Translate all caption text entries to the target language.
    Preserves timing (start, end) and other metadata, only translates the 'text' field.
    Uses GPT-4.1-mini for high-quality contextual translation.
    """
    import openai
    import re

    if not captions:
        return captions

    azure_key = os.getenv("AZURE_OPENAI_KEY", "")
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    azure_model = os.getenv("GPT5_MODEL") or os.getenv("GPT5_DEPLOYMENT") or "gpt-4.1-mini"

    if not azure_key or not azure_endpoint:
        logger.error(f"[ai-clip {job_id}] Azure OpenAI not configured for caption translation")
        return captions

    from urllib.parse import urlparse as _urlparse
    _parsed = _urlparse(azure_endpoint)
    clean_endpoint = f"{_parsed.scheme}://{_parsed.netloc}/"

    client = openai.AsyncAzureOpenAI(
        api_key=azure_key,
        azure_endpoint=clean_endpoint,
        api_version=os.getenv("GPT5_API_VERSION", "2025-04-01-preview"),
    )

    lang_names = {
        "zh": "中文（简体）",
        "zh-tw": "中文（繁體）",
        "en": "English",
        "ko": "한국어",
    }
    target_lang_name = lang_names.get(target_lang, target_lang)

    # Build the text list for batch translation
    texts = [c.get("text", "") for c in captions]
    numbered_texts = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))

    translation_prompt = f"""Translate the following numbered lines from Japanese to natural, spoken {target_lang_name}.
These are live stream subtitles from a beauty product presenter.

RULES:
- Output ONLY the translated lines, one per line, with the same numbering
- Keep the same number of lines as input
- Maintain conversational tone and enthusiasm
- Preserve product names (KYOGOKU, 京極) as-is
- Keep similar character length per line (for subtitle timing)
- Do NOT add explanations or formatting

Input:
{numbered_texts}"""

    try:
        # gpt-5.x models require max_completion_tokens instead of max_tokens
        gpt_params = {
            "model": azure_model,
            "messages": [
                {"role": "system", "content": "You are a subtitle translator for live commerce content. Output only numbered translated lines."},
                {"role": "user", "content": translation_prompt},
            ],
            "temperature": 0.3,
        }
        if "gpt-5" in azure_model or "o1" in azure_model or "o3" in azure_model or "o4" in azure_model:
            gpt_params["max_completion_tokens"] = 4000
        else:
            gpt_params["max_tokens"] = 4000
        gpt_response = await client.chat.completions.create(**gpt_params)
        result_text = gpt_response.choices[0].message.content.strip()

        # Parse numbered lines back
        translated_lines = []
        for line in result_text.split("\n"):
            line = line.strip()
            if not line:
                continue
            # Remove numbering prefix like "1. " or "1."
            cleaned = re.sub(r'^\d+\.\s*', '', line)
            if cleaned:
                translated_lines.append(cleaned)

        # Apply translations back to captions
        if len(translated_lines) >= len(captions):
            for i, cap in enumerate(captions):
                cap["text"] = translated_lines[i]
            logger.info(f"[ai-clip {job_id}] All {len(captions)} captions translated to {target_lang}")
        elif len(translated_lines) > 0:
            # Partial match - apply what we can
            for i in range(min(len(translated_lines), len(captions))):
                captions[i]["text"] = translated_lines[i]
            logger.warning(f"[ai-clip {job_id}] Partial caption translation: {len(translated_lines)}/{len(captions)} lines")
        else:
            logger.warning(f"[ai-clip {job_id}] Caption translation returned no parseable lines")

    except Exception as e:
        logger.error(f"[ai-clip {job_id}] Caption text translation failed: {e}")
        # Return original captions on failure

    return captions


# ─── V2.35: Translation + Lip Sync Pipeline ───────────────────────────────────

async def _translate_and_lipsync_clip(
    video_path: str,
    captions: list,
    target_lang: str,
    voice_id: Optional[str],
    tmp_dir: str,
    job_id: str,
    clip_id: str,
) -> Optional[str]:
    """
    V2.35: Translate clip audio to target language with voice cloning + lip sync.
    Pipeline:
      1. Extract transcript from captions (or re-transcribe)
      2. Translate text via GPT-4.1-mini
      3. Generate translated audio via ElevenLabs TTS (cloned voice)
      4. Upload video + audio to blob for Sync.so
      5. Apply lip sync via Sync.so
      6. Download result
    Returns path to the translated+lip-synced video, or None on failure.
    """
    import httpx
    import openai
    from app.services.elevenlabs_tts_service import ElevenLabsTTSService
    from app.services.sync_lip_sync_service import SyncLipSyncService
    from app.services.storage_service import (
        generate_upload_sas, generate_read_sas_from_url,
        CONNECTION_STRING, ACCOUNT_NAME, CONTAINER_NAME,
    )

    logger.info(f"[ai-clip {job_id}] V2.35 Translation pipeline: {target_lang}")

    # ── Step 1: Extract transcript text from captions ──
    if captions and isinstance(captions, list) and len(captions) > 0:
        transcript_text = " ".join(
            (c.get("text") or "").strip() for c in captions if c.get("text")
        )
    else:
        logger.warning(f"[ai-clip {job_id}] No captions available for translation")
        return None

    if not transcript_text or len(transcript_text.strip()) < 5:
        logger.warning(f"[ai-clip {job_id}] Transcript too short for translation: '{transcript_text}'")
        return None

    logger.info(f"[ai-clip {job_id}] Transcript ({len(transcript_text)} chars): {transcript_text[:100]}...")

    # ── Step 2: Translate via GPT-4.1-mini ──
    azure_key = os.getenv("AZURE_OPENAI_KEY", "")
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    azure_model = os.getenv("GPT5_MODEL") or os.getenv("GPT5_DEPLOYMENT") or "gpt-4.1-mini"

    if not azure_key or not azure_endpoint:
        logger.error(f"[ai-clip {job_id}] Azure OpenAI not configured for translation")
        return None

    from urllib.parse import urlparse as _urlparse
    _parsed = _urlparse(azure_endpoint)
    clean_endpoint = f"{_parsed.scheme}://{_parsed.netloc}/"

    client = openai.AsyncAzureOpenAI(
        api_key=azure_key,
        azure_endpoint=clean_endpoint,
        api_version=os.getenv("GPT5_API_VERSION", "2025-04-01-preview"),
    )

    lang_names = {
        "zh": "中文（简体）",
        "zh-tw": "中文（繁體）",
        "en": "English",
        "ko": "한국어",
    }
    target_lang_name = lang_names.get(target_lang, target_lang)

    translation_prompt = f"""You are a professional translator specializing in live commerce and beauty product content.
Translate the following Japanese live stream transcript into natural, spoken {target_lang_name}.

IMPORTANT RULES:
- Maintain the speaker's tone, enthusiasm, and personality
- Keep it natural and conversational (this will be spoken aloud)
- Preserve product names (KYOGOKU, 京極) as-is or use the most common localized form
- Do NOT add any explanations, notes, or formatting
- Output ONLY the translated text, nothing else
- Keep similar length/timing to the original (for lip sync purposes)
- If the text contains filler words or hesitations, translate them naturally

Original transcript:
{transcript_text}"""

    try:
        # gpt-5.x models require max_completion_tokens instead of max_tokens
        lipsync_gpt_params = {
            "model": azure_model,
            "messages": [
                {"role": "system", "content": "You are a professional translator for live commerce content."},
                {"role": "user", "content": translation_prompt},
            ],
            "temperature": 0.3,
        }
        if "gpt-5" in azure_model or "o1" in azure_model or "o3" in azure_model or "o4" in azure_model:
            lipsync_gpt_params["max_completion_tokens"] = 2000
        else:
            lipsync_gpt_params["max_tokens"] = 2000
        gpt_response = await client.chat.completions.create(**lipsync_gpt_params)
        translated_text = gpt_response.choices[0].message.content.strip()
        logger.info(f"[ai-clip {job_id}] Translation result ({len(translated_text)} chars): {translated_text[:100]}...")
    except Exception as gpt_err:
        logger.error(f"[ai-clip {job_id}] GPT translation failed: {gpt_err}")
        return None

    if not translated_text or len(translated_text) < 3:
        logger.warning(f"[ai-clip {job_id}] Translation result too short")
        return None

    # ── Step 3: Generate translated audio via ElevenLabs TTS ──
    tts = ElevenLabsTTSService()
    effective_voice_id = voice_id or os.getenv("ELEVENLABS_VOICE_ID", "")
    if not effective_voice_id:
        logger.error(f"[ai-clip {job_id}] No voice_id available for TTS")
        return None

    # Map target_lang to ElevenLabs language code
    elevenlabs_lang_map = {
        "zh": "zh",
        "zh-tw": "zh",
        "en": "en",
        "ko": "ko",
    }
    tts_lang = elevenlabs_lang_map.get(target_lang, "zh")

    try:
        tts_audio_bytes = await tts.text_to_speech(
            text=translated_text,
            voice_id=effective_voice_id,
            language_code=tts_lang,
            output_format="mp3_44100_128",
        )
        logger.info(f"[ai-clip {job_id}] TTS audio generated: {len(tts_audio_bytes)} bytes")
    except Exception as tts_err:
        logger.error(f"[ai-clip {job_id}] ElevenLabs TTS failed: {tts_err}")
        return None

    if not tts_audio_bytes or len(tts_audio_bytes) < 1000:
        logger.warning(f"[ai-clip {job_id}] TTS audio too small")
        return None

    # Save TTS audio locally
    tts_audio_path = os.path.join(tmp_dir, f"{clip_id}-translated-tts.mp3")
    with open(tts_audio_path, "wb") as f:
        f.write(tts_audio_bytes)

    # ── Step 4: Upload video and audio to Azure Blob for Sync.so ──
    # Upload TTS audio
    _, tts_upload_url, tts_blob_url, _ = await generate_upload_sas(
        email="ai-clip-translate@aitherhub.com",
        video_id=f"{clip_id}-translate-tts",
        filename=f"{clip_id}-translate-tts.mp3",
    )
    async with httpx.AsyncClient(timeout=120) as upload_client:
        resp = await upload_client.put(
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
        logger.error(f"[ai-clip {job_id}] Failed to generate SAS URL for TTS audio")
        return None

    # Upload the video to blob for Sync.so to access
    video_bytes = None
    with open(video_path, "rb") as f:
        video_bytes = f.read()
    _, video_upload_url, video_blob_url, _ = await generate_upload_sas(
        email="ai-clip-translate@aitherhub.com",
        video_id=f"{clip_id}-translate-video",
        filename=f"{clip_id}-translate-video.mp4",
    )
    async with httpx.AsyncClient(timeout=120) as upload_client:
        resp = await upload_client.put(
            video_upload_url,
            content=video_bytes,
            headers={
                "x-ms-blob-type": "BlockBlob",
                "Content-Type": "video/mp4",
            },
        )
        resp.raise_for_status()
    video_sas_url = generate_read_sas_from_url(video_blob_url)
    if not video_sas_url:
        logger.error(f"[ai-clip {job_id}] Failed to generate SAS URL for video")
        return None

    logger.info(f"[ai-clip {job_id}] Uploaded video + TTS audio to blob for Sync.so")

    # ── Step 5: Apply lip sync via Sync.so ──
    sync_service = SyncLipSyncService()
    try:
        sync_result = await sync_service.lip_sync(
            video_url=video_sas_url,
            audio_url=tts_sas_url,
            model="lipsync-2",
            sync_mode="cut_off",
            max_wait_sec=600,
            poll_interval=5,
        )
        output_url = sync_result.get("output_url")
        if not output_url:
            logger.error(f"[ai-clip {job_id}] Sync.so returned no output URL")
            return None
        logger.info(f"[ai-clip {job_id}] Sync.so lip sync completed: {output_url[:80]}...")
    except Exception as sync_err:
        logger.error(f"[ai-clip {job_id}] Sync.so lip sync failed: {sync_err}")
        return None

    # ── Step 6: Download the lip-synced result ──
    translated_output_path = os.path.join(tmp_dir, f"{clip_id}-translated-lipsync.mp4")
    try:
        await sync_service.download_result(output_url, translated_output_path)
        if os.path.exists(translated_output_path) and os.path.getsize(translated_output_path) > 1000:
            logger.info(f"[ai-clip {job_id}] Translation + lip sync output: {os.path.getsize(translated_output_path)} bytes")
            return translated_output_path
        else:
            logger.error(f"[ai-clip {job_id}] Downloaded file too small or missing")
            return None
    except Exception as dl_err:
        logger.error(f"[ai-clip {job_id}] Failed to download lip-synced video: {dl_err}")
        return None


# ─── Upload & DB Save (unchanged from V1) ────────────────────────────────────

async def _upload_to_blob(output_path: str, clip_id: str, job_id: str) -> tuple:
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

    try:
        disposition = f'attachment; filename="ai_clip_{clip_id[:8]}.mp4"'
        download_url = generate_read_sas_from_url(blob_url, expires_hours=72, content_disposition=disposition)
        if not download_url:
            download_url = blob_url
    except Exception:
        download_url = blob_url

    cdn_host = os.getenv("CDN_HOST", "https://cdn.aitherhub.com")
    blob_host = f"https://{ACCOUNT_NAME}.blob.core.windows.net"
    if cdn_host and blob_host in blob_url:
        blob_url = blob_url.replace(blob_host, cdn_host)

    return download_url, blob_url


async def _save_export_record(clip_id: str, blob_url: str, thumbnail_url: Optional[str],
                              exported_duration: Optional[float] = None):
    try:
        async with get_session() as session:
            if exported_duration is not None:
                await session.execute(text("""
                    UPDATE video_clips
                    SET exported_url = :exported_url,
                        exported_at = NOW(),
                        exported_duration = :exported_duration
                    WHERE id = CAST(:clip_id AS uuid)
                """), {"clip_id": clip_id, "exported_url": blob_url,
                       "exported_duration": exported_duration})
            else:
                await session.execute(text("""
                    UPDATE video_clips
                    SET exported_url = :exported_url,
                        exported_at = NOW()
                    WHERE id = CAST(:clip_id AS uuid)
                """), {"clip_id": clip_id, "exported_url": blob_url})
            logger.info(f"[ai-clip] Saved export record for clip {clip_id} (duration={exported_duration})")
    except Exception as e:
        logger.warning(f"[ai-clip] Failed to save export record: {e}")



# ─── V3: Video Mode Helpers (product_overlay / audio_only) ─────────────────────

async def _generate_product_slideshow(
    product_image_urls: list, duration: float,
    width: int, height: int, tmp_dir: str, job_id: str
) -> Optional[str]:
    """商品画像のスライドショー動画を生成する（音声なし）。
    元動画の音声と後で合成される。
    """
    import httpx
    from PIL import Image, ImageFilter
    from app.services.storage_service import generate_read_sas_from_url

    if not product_image_urls:
        return None

    # Resolve SAS URLs for blob storage access (blob_url without SAS returns 409)
    resolved_urls = []
    for url in product_image_urls[:10]:
        if "blob.core.windows.net" in url and "?" not in url:
            sas_url = generate_read_sas_from_url(url, expires_hours=2)
            resolved_urls.append(sas_url or url)
        else:
            resolved_urls.append(url)
    logger.info(f"[ai-clip {job_id}] Slideshow: resolved {len(resolved_urls)} product image URLs")

    # Download product images
    downloaded_images = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        for i, url in enumerate(resolved_urls):  # Max 10 images
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                img_path = os.path.join(tmp_dir, f"product_{i}.jpg")
                with open(img_path, "wb") as f:
                    f.write(resp.content)
                downloaded_images.append(img_path)
            except Exception as e:
                logger.warning(f"[ai-clip {job_id}] Failed to download product image {i}: {e} (URL: {url[:80]})")

    if not downloaded_images:
        logger.warning(f"[ai-clip {job_id}] No product images downloaded, skipping slideshow")
        return None

    # Generate slideshow frames using Pillow
    # Each image shows for (duration / num_images) seconds with crossfade
    num_images = len(downloaded_images)
    display_time = max(2.0, duration / num_images)  # At least 2 seconds per image
    fps = 30
    total_frames = int(duration * fps)
    frames_per_image = int(display_time * fps)
    fade_frames = min(15, frames_per_image // 4)  # 0.5s crossfade

    # Create processed product images (centered on blurred background)
    processed_images = []
    for img_path in downloaded_images:
        try:
            img = Image.open(img_path).convert("RGBA")
            # Create background (dark gradient)
            bg = Image.new("RGBA", (width, height), (15, 15, 25, 255))
            # Blur a scaled version of the product image as background
            bg_blur = img.copy().resize((width, height), Image.LANCZOS)
            bg_blur = bg_blur.filter(ImageFilter.GaussianBlur(radius=30))
            bg_blur = bg_blur.convert("RGBA")
            # Darken the blurred background
            from PIL import ImageEnhance
            enhancer = ImageEnhance.Brightness(bg_blur)
            bg_blur = enhancer.enhance(0.3)
            bg.paste(bg_blur, (0, 0))
            # Scale product image to fit (80% of canvas, maintaining aspect ratio)
            max_w = int(width * 0.8)
            max_h = int(height * 0.7)
            img_w, img_h = img.size
            scale = min(max_w / img_w, max_h / img_h)
            new_w = int(img_w * scale)
            new_h = int(img_h * scale)
            img_resized = img.resize((new_w, new_h), Image.LANCZOS)
            # Center the product image
            x_offset = (width - new_w) // 2
            y_offset = (height - new_h) // 2
            bg.paste(img_resized, (x_offset, y_offset), img_resized)
            processed_images.append(bg.convert("RGB"))
        except Exception as e:
            logger.warning(f"[ai-clip {job_id}] Failed to process product image: {e}")

    if not processed_images:
        return None

    # Write frames as individual PNGs and use ffmpeg to create video
    # More efficient: write a concat file with image durations
    slideshow_path = os.path.join(tmp_dir, "slideshow.mp4")

    # Create a concat demuxer file
    concat_file = os.path.join(tmp_dir, "concat.txt")
    frame_paths = []
    for i, pimg in enumerate(processed_images):
        frame_path = os.path.join(tmp_dir, f"slide_{i:03d}.png")
        pimg.save(frame_path, "PNG")
        frame_paths.append(frame_path)

    # Use ffmpeg to create slideshow with crossfade transitions
    if len(frame_paths) == 1:
        # Single image: just loop it for the duration
        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-i", frame_paths[0],
            "-t", str(duration), "-vf", f"scale={width}:{height}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-r", str(fps), slideshow_path
        ]
    else:
        # Multiple images: use xfade filter for crossfade transitions
        inputs = []
        for fp in frame_paths:
            inputs.extend(["-loop", "1", "-t", str(display_time), "-i", fp])

        # Build xfade filter chain
        filter_parts = []
        current_input = "[0:v]"
        fade_duration = 0.5
        for i in range(1, len(frame_paths)):
            next_input = f"[{i}:v]"
            offset = display_time * i - fade_duration * i
            if offset < 0:
                offset = display_time * i * 0.8
            out_label = f"[v{i}]" if i < len(frame_paths) - 1 else "[outv]"
            filter_parts.append(
                f"{current_input}{next_input}xfade=transition=fade:duration={fade_duration}:offset={offset:.2f}{out_label}"
            )
            current_input = out_label

        if not filter_parts:
            # Fallback: single image
            cmd = [
                "ffmpeg", "-y", "-loop", "1", "-i", frame_paths[0],
                "-t", str(duration), "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-r", str(fps), slideshow_path
            ]
        else:
            filter_complex = ";".join(filter_parts)
            cmd = ["ffmpeg", "-y"] + inputs + [
                "-filter_complex", filter_complex,
                "-map", "[outv]",
                "-t", str(duration),
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-r", str(fps), slideshow_path
            ]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    if proc.returncode != 0:
        logger.error(f"[ai-clip {job_id}] Slideshow ffmpeg failed: {stderr.decode()[-500:]}")
        return None

    # Now merge audio from original video with slideshow video
    merged_path = os.path.join(tmp_dir, "slideshow_with_audio.mp4")
    original_video = os.path.join(tmp_dir, "input.mp4")
    merge_cmd = [
        "ffmpeg", "-y",
        "-i", slideshow_path,
        "-i", original_video,
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy", "-c:a", "aac", "-shortest",
        merged_path
    ]
    proc2 = await asyncio.create_subprocess_exec(
        *merge_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout2, stderr2 = await asyncio.wait_for(proc2.communicate(), timeout=60)
    if proc2.returncode != 0:
        logger.warning(f"[ai-clip {job_id}] Audio merge failed, using slideshow without audio: {stderr2.decode()[-300:]}")
        return slideshow_path

    return merged_path


async def _generate_pip_video(
    video_path: str, product_image_urls: list, duration: float,
    width: int, height: int, tmp_dir: str, job_id: str
) -> Optional[str]:
    """V13: PiP (Picture-in-Picture) 合成 - 商品画像を右下角に配置。
    各画像を1回ずつ順番に表示（ループなし）。
    表示時間: 5秒/画像、非表示時間: 3秒。
    配置: 右下角（ライブ配信者の顔を避ける）。
    サイズ: 画面の25%幅（主体を遮らない）。
    """
    import httpx
    from PIL import Image, ImageDraw, ImageFilter
    from app.services.storage_service import generate_read_sas_from_url

    if not product_image_urls:
        return None

    # Resolve SAS URLs for blob storage access (blob_url without SAS returns 409)
    resolved_urls = []
    for url in product_image_urls:
        if "blob.core.windows.net" in url and "?" not in url:
            sas_url = generate_read_sas_from_url(url, expires_hours=2)
            resolved_urls.append(sas_url or url)
        elif "cdn.aitherhub.com" in url:
            # CDN URL -> convert to blob URL and generate SAS
            blob_url = url.replace("https://cdn.aitherhub.com", "https://aitherhub.blob.core.windows.net")
            sas_url = generate_read_sas_from_url(blob_url, expires_hours=2)
            resolved_urls.append(sas_url or url)
        else:
            resolved_urls.append(url)
    logger.info(f"[ai-clip {job_id}] PiP rotation: resolved {len(resolved_urls)} product image URLs")

    # Download ALL product images (no limit)
    product_img_paths = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        for i, img_url in enumerate(resolved_urls):  # Use ALL images
            try:
                resp = await client.get(img_url)
                resp.raise_for_status()
                ext = "png" if "png" in img_url.lower() else "jpg"
                img_path = os.path.join(tmp_dir, f"pip_product_{i}.{ext}")
                with open(img_path, "wb") as f:
                    f.write(resp.content)
                product_img_paths.append(img_path)
            except Exception as e:
                logger.warning(f"[ai-clip {job_id}] Failed to download product image {i}: {e}")

    if not product_img_paths:
        logger.error(f"[ai-clip {job_id}] No product images downloaded for PiP rotation")
        return None

    num_images = len(product_img_paths)
    logger.info(f"[ai-clip {job_id}] PiP rotation: {num_images} images downloaded")

    # V14.3: Create overlay images for each product (rounded corners, white card background)
    # Size: 30% width, 25% height — large enough to see product clearly
    # Position is now top-right so it won't block face or subtitles
    overlay_size = int(width * 0.30)
    overlay_h = int(height * 0.25)
    corner_radius = 16
    padding = 12
    inner_w = overlay_size - padding * 2
    inner_h = overlay_h - padding * 2

    overlay_paths = []
    for i, img_path in enumerate(product_img_paths):
        try:
            product_img = Image.open(img_path).convert("RGBA")
            img_w, img_h = product_img.size
            scale = min(inner_w / img_w, inner_h / img_h)
            new_w = int(img_w * scale)
            new_h = int(img_h * scale)
            product_resized = product_img.resize((new_w, new_h), Image.LANCZOS)

            # Create rounded rectangle background
            overlay_bg = Image.new("RGBA", (overlay_size, overlay_h), (0, 0, 0, 0))
            mask = Image.new("L", (overlay_size, overlay_h), 0)
            draw = ImageDraw.Draw(mask)
            draw.rounded_rectangle([(0, 0), (overlay_size - 1, overlay_h - 1)], radius=corner_radius, fill=255)
            white_bg = Image.new("RGBA", (overlay_size, overlay_h), (255, 255, 255, 230))
            overlay_bg.paste(white_bg, (0, 0), mask)
            # Paste product image centered
            x_off = (overlay_size - new_w) // 2
            y_off = (overlay_h - new_h) // 2
            overlay_bg.paste(product_resized, (x_off, y_off), product_resized)
            overlay_bg.putalpha(mask)

            overlay_path = os.path.join(tmp_dir, f"pip_overlay_{i}.png")
            overlay_bg.save(overlay_path, "PNG")
            overlay_paths.append(overlay_path)
        except Exception as e:
            logger.warning(f"[ai-clip {job_id}] Failed to create overlay for image {i}: {e}")

    if not overlay_paths:
        logger.error(f"[ai-clip {job_id}] No overlay images created")
        return None

    # V13: Smart PiP timing - NO LOOP, each image shown ONCE
    # Show duration: 5s per image (was 3s - too short)
    # Hide duration: 3s between images
    # No loop: each image appears exactly once
    show_duration = 5.0   # seconds visible per image
    hide_duration = 3.0   # seconds hidden between images
    first_appear = 3.0    # first appearance at 3 seconds (give viewer time to focus)
    cycle_duration = show_duration + hide_duration  # 8s per image slot

    # Build schedule: [(start_time, end_time, image_index), ...]
    # Each image shown EXACTLY ONCE (no loop/modulo)
    schedule = []
    t = first_appear
    for img_idx in range(len(overlay_paths)):
        if t + show_duration > duration - 1.0:
            break  # Don't show if it would extend past video end
        schedule.append((t, t + show_duration, img_idx))
        t += cycle_duration

    # If video is very short, show at least one image for longer
    if not schedule and duration > 3:
        show_t = min(show_duration, duration - 2.0)
        schedule.append((1.5, 1.5 + show_t, 0))

    if not schedule:
        logger.warning(f"[ai-clip {job_id}] Video too short for PiP rotation (duration={duration})")
        return None

    logger.info(f"[ai-clip {job_id}] V13 PiP schedule: {len(schedule)} appearances (no loop), {len(overlay_paths)} unique images")

    # V14.3: Position overlay in TOP-RIGHT CORNER (avoid blocking subtitles)
    # Subtitles are rendered at ~75% height, so PiP must be above that zone.
    # Margin: 20px from right, 100px from top (below safe area)
    overlay_x = width - overlay_size - 20
    overlay_y = 100

    pip_output = os.path.join(tmp_dir, "pip_output.mp4")

    # Build ffmpeg command with multiple overlay inputs (one per unique image)
    # Each overlay has its own enable expression for when it should appear
    input_args = ["-i", video_path]
    for op in overlay_paths:
        input_args.extend(["-i", op])

    # Build filter_complex: chain overlays one after another
    # [0:v][1:v]overlay=...enable='...'[tmp1]; [tmp1][2:v]overlay=...enable='...'[tmp2]; ...
    filter_parts = []
    num_overlays = len(overlay_paths)

    # Group schedule by image index
    enable_by_image = {}
    for start, end, img_i in schedule:
        if img_i not in enable_by_image:
            enable_by_image[img_i] = []
        enable_by_image[img_i].append(f"between(t,{start:.1f},{end:.1f})")

    # Build overlay chain
    prev_label = "0:v"
    active_overlays = sorted(enable_by_image.keys())
    for chain_idx, img_i in enumerate(active_overlays):
        enable_expr = "+".join(enable_by_image[img_i])
        input_idx = img_i + 1  # +1 because input 0 is the video
        out_label = f"tmp{chain_idx}" if chain_idx < len(active_overlays) - 1 else "outv"
        filter_parts.append(
            f"[{prev_label}][{input_idx}:v]overlay={overlay_x}:{overlay_y}:enable='{enable_expr}'[{out_label}]"
        )
        prev_label = out_label

    filter_complex = ";".join(filter_parts)

    pip_cmd = [
        "ffmpeg", "-y",
        *input_args,
        "-filter_complex", filter_complex,
        "-map", "[outv]", "-map", "0:a?",
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-r", "30",
        pip_output
    ]

    logger.info(f"[ai-clip {job_id}] PiP rotation: {len(overlay_paths)} images, filter_complex={filter_complex[:200]}...")

    proc = await asyncio.create_subprocess_exec(
        *pip_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
    if proc.returncode != 0:
        logger.error(f"[ai-clip {job_id}] PiP rotation ffmpeg failed: {stderr.decode()[-500:]}")
        # Fallback: use only first image, always visible
        pip_cmd_fallback = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", overlay_paths[0],
            "-filter_complex",
            f"[0:v][1:v]overlay={overlay_x}:{overlay_y}[outv]",
            "-map", "[outv]", "-map", "0:a?",
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-r", "30",
            pip_output
        ]
        proc2 = await asyncio.create_subprocess_exec(
            *pip_cmd_fallback, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout2, stderr2 = await asyncio.wait_for(proc2.communicate(), timeout=180)
        if proc2.returncode != 0:
            logger.error(f"[ai-clip {job_id}] PiP fallback also failed: {stderr2.decode()[-500:]}")
            return None
        logger.info(f"[ai-clip {job_id}] PiP fallback succeeded (first image always shown)")

    return pip_output


# ─── V11: PiP Video Overlay (Product Video) ─────────────────────────────────────────────

async def _generate_pip_video_overlay(
    video_path: str, product_video_urls: list, duration: float,
    width: int, height: int, tmp_dir: str, job_id: str
) -> Optional[str]:
    """V13: PiP合成 - 商品動画をメイン動画にオーバーレイ表示。
    商品動画を1回完全再生（ループなし）。
    配置: 右下角（顔を避ける）。
    """
    import httpx
    from app.services.storage_service import generate_read_sas_from_url

    if not product_video_urls:
        return None

    # Resolve SAS URLs
    resolved_urls = []
    for url in product_video_urls:
        if "blob.core.windows.net" in url and "?" not in url:
            sas_url = generate_read_sas_from_url(url, expires_hours=2)
            resolved_urls.append(sas_url or url)
        elif "cdn.aitherhub.com" in url:
            blob_url = url.replace("https://cdn.aitherhub.com", "https://aitherhub.blob.core.windows.net")
            sas_url = generate_read_sas_from_url(blob_url, expires_hours=2)
            resolved_urls.append(sas_url or url)
        else:
            resolved_urls.append(url)

    # Download first product video
    product_video_path = os.path.join(tmp_dir, "pip_product_video.mp4")
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(resolved_urls[0])
            resp.raise_for_status()
            with open(product_video_path, "wb") as f:
                f.write(resp.content)
    except Exception as e:
        logger.error(f"[ai-clip {job_id}] Failed to download product video: {e}")
        return None

    # V13: Determine PiP display timing - play product video ONCE completely (no loop)
    # Get product video duration using ffprobe
    try:
        probe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", product_video_path]
        probe_proc = await asyncio.create_subprocess_exec(
            *probe_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        probe_out, _ = await asyncio.wait_for(probe_proc.communicate(), timeout=10)
        product_video_duration = float(probe_out.decode().strip())
    except Exception:
        product_video_duration = 10.0  # Default fallback

    # Start at 3s, play for the full product video duration (capped to main video length)
    pip_start = 3.0
    pip_duration = min(product_video_duration, duration - pip_start - 1.0)
    if pip_duration < 2.0:
        pip_start = 1.0
        pip_duration = min(product_video_duration, duration - 2.0)

    logger.info(f"[ai-clip {job_id}] V13 PiP video: product_dur={product_video_duration:.1f}s, "
                f"pip_start={pip_start:.1f}s, pip_duration={pip_duration:.1f}s (1x play, no loop)")

    # V14.3: PiP overlay size and position (TOP-right corner, 30% of screen)
    # Moved from bottom-right to top-right to avoid blocking subtitles
    pip_w = int(width * 0.30)
    pip_h = int(height * 0.25)
    pip_x = width - pip_w - 20  # 20px margin from right
    pip_y = 100  # 100px from top (avoid subtitle zone at bottom 75%)

    pip_output = os.path.join(tmp_dir, "pip_video_output.mp4")

    # V13: Single enable expression - play once, no loop
    enable_expr = f"between(t,{pip_start:.1f},{pip_start+pip_duration:.1f})"

    filter_complex = (
        f"[1:v]scale={pip_w}:{pip_h}:force_original_aspect_ratio=decrease,"
        f"pad={pip_w}:{pip_h}:(ow-iw)/2:(oh-ih)/2:color=black@0[pip];"
        f"[0:v][pip]overlay={pip_x}:{pip_y}:enable='{enable_expr}'[outv]"
    )

    pip_cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", product_video_path,
        "-filter_complex", filter_complex,
        "-map", "[outv]", "-map", "0:a?",
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-r", "30",
        pip_output
    ]

    logger.info(f"[ai-clip {job_id}] V13 PiP video overlay: start={pip_start:.1f}s, duration={pip_duration:.1f}s (1x play, no loop)")

    proc = await asyncio.create_subprocess_exec(
        *pip_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
    if proc.returncode != 0:
        logger.error(f"[ai-clip {job_id}] PiP video overlay ffmpeg failed: {stderr.decode()[-500:]}")
        return None

    logger.info(f"[ai-clip {job_id}] PiP video overlay generated successfully")
    return pip_output



# ─── V13: Combined Sequential PiP (Video then Images) ────────────────────────────────────

async def _generate_pip_combined_sequential(
    video_path: str, product_video_urls: list, product_image_urls: list,
    duration: float, width: int, height: int, tmp_dir: str, job_id: str
) -> Optional[str]:
    """V13: 時間帯分離PiP合成 - 商品動画を先に1回再生、その後商品画像を順番に表示。
    同時表示なし、ループなし。配置は全て右下角。
    """
    import httpx
    from PIL import Image, ImageDraw
    from app.services.storage_service import generate_read_sas_from_url

    # --- Step 1: Download product video ---
    resolved_video_urls = []
    for url in product_video_urls[:1]:  # Use first video only
        if "blob.core.windows.net" in url and "?" not in url:
            sas_url = generate_read_sas_from_url(url, expires_hours=2)
            resolved_video_urls.append(sas_url or url)
        elif "cdn.aitherhub.com" in url:
            blob_url = url.replace("https://cdn.aitherhub.com", "https://aitherhub.blob.core.windows.net")
            sas_url = generate_read_sas_from_url(blob_url, expires_hours=2)
            resolved_video_urls.append(sas_url or url)
        else:
            resolved_video_urls.append(url)

    product_video_path = os.path.join(tmp_dir, "pip_seq_product_video.mp4")
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(resolved_video_urls[0])
            resp.raise_for_status()
            with open(product_video_path, "wb") as f:
                f.write(resp.content)
    except Exception as e:
        logger.error(f"[ai-clip {job_id}] V13 seq: Failed to download product video: {e}")
        # Fallback: just do image PiP
        return await _generate_pip_video(
            video_path, product_image_urls, duration, width, height, tmp_dir, job_id
        )

    # Get product video duration
    try:
        probe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", product_video_path]
        probe_proc = await asyncio.create_subprocess_exec(
            *probe_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        probe_out, _ = await asyncio.wait_for(probe_proc.communicate(), timeout=10)
        product_video_duration = float(probe_out.decode().strip())
    except Exception:
        product_video_duration = 10.0

    # --- Step 2: Download product images ---
    resolved_img_urls = []
    for url in product_image_urls:
        if "blob.core.windows.net" in url and "?" not in url:
            sas_url = generate_read_sas_from_url(url, expires_hours=2)
            resolved_img_urls.append(sas_url or url)
        elif "cdn.aitherhub.com" in url:
            blob_url = url.replace("https://cdn.aitherhub.com", "https://aitherhub.blob.core.windows.net")
            sas_url = generate_read_sas_from_url(blob_url, expires_hours=2)
            resolved_img_urls.append(sas_url or url)
        else:
            resolved_img_urls.append(url)

    product_img_paths = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        for i, img_url in enumerate(resolved_img_urls):
            try:
                resp = await client.get(img_url)
                resp.raise_for_status()
                ext = "png" if "png" in img_url.lower() else "jpg"
                img_path = os.path.join(tmp_dir, f"pip_seq_product_{i}.{ext}")
                with open(img_path, "wb") as f:
                    f.write(resp.content)
                product_img_paths.append(img_path)
            except Exception as e:
                logger.warning(f"[ai-clip {job_id}] V13 seq: Failed to download image {i}: {e}")

    # --- Step 3: Build timeline ---
    # Phase 1: Product video (starts at 3s, plays once completely)
    video_pip_start = 3.0
    video_pip_end = min(video_pip_start + product_video_duration, duration - 2.0)

    # Phase 2: Product images (starts after video ends + 3s gap)
    img_start_time = video_pip_end + 3.0
    img_show_duration = 5.0
    img_hide_duration = 3.0

    # V14.3: PiP size and position (TOP-right corner - avoid subtitle overlap)
    pip_w = int(width * 0.30)
    pip_h = int(height * 0.25)
    pip_x = width - pip_w - 20
    pip_y = 100  # 100px from top (subtitles are at bottom 75%)

    logger.info(f"[ai-clip {job_id}] V13 seq: video_phase={video_pip_start:.1f}-{video_pip_end:.1f}s, "
                f"image_phase_start={img_start_time:.1f}s, {len(product_img_paths)} images")

    # --- Step 4: Create image overlays ---
    overlay_size_w = pip_w
    overlay_size_h = pip_h
    corner_radius = 12
    padding = 8
    inner_w = overlay_size_w - padding * 2
    inner_h = overlay_size_h - padding * 2

    overlay_paths = []
    for i, img_path in enumerate(product_img_paths):
        try:
            product_img = Image.open(img_path).convert("RGBA")
            img_w, img_h = product_img.size
            scale = min(inner_w / img_w, inner_h / img_h)
            new_w = int(img_w * scale)
            new_h = int(img_h * scale)
            product_resized = product_img.resize((new_w, new_h), Image.LANCZOS)

            overlay_bg = Image.new("RGBA", (overlay_size_w, overlay_size_h), (0, 0, 0, 0))
            mask = Image.new("L", (overlay_size_w, overlay_size_h), 0)
            draw = ImageDraw.Draw(mask)
            draw.rounded_rectangle([(0, 0), (overlay_size_w - 1, overlay_size_h - 1)], radius=corner_radius, fill=255)
            white_bg = Image.new("RGBA", (overlay_size_w, overlay_size_h), (255, 255, 255, 220))
            overlay_bg.paste(white_bg, (0, 0), mask)
            x_off = (overlay_size_w - new_w) // 2
            y_off = (overlay_size_h - new_h) // 2
            overlay_bg.paste(product_resized, (x_off, y_off), product_resized)
            overlay_bg.putalpha(mask)

            overlay_path = os.path.join(tmp_dir, f"pip_seq_overlay_{i}.png")
            overlay_bg.save(overlay_path, "PNG")
            overlay_paths.append(overlay_path)
        except Exception as e:
            logger.warning(f"[ai-clip {job_id}] V13 seq: Failed to create overlay {i}: {e}")

    # --- Step 5: Build ffmpeg command ---
    # Inputs: [0]=main video, [1]=product video, [2..N]=product images
    input_args = ["-i", video_path, "-i", product_video_path]
    for op in overlay_paths:
        input_args.extend(["-i", op])

    # Filter: video PiP first, then image overlays
    filter_parts = []

    # Video PiP: scale product video and overlay at right-bottom
    video_enable = f"between(t,{video_pip_start:.1f},{video_pip_end:.1f})"
    filter_parts.append(
        f"[1:v]scale={pip_w}:{pip_h}:force_original_aspect_ratio=decrease,"
        f"pad={pip_w}:{pip_h}:(ow-iw)/2:(oh-ih)/2:color=black@0[vpip];"
        f"[0:v][vpip]overlay={pip_x}:{pip_y}:enable='{video_enable}'[vtmp]"
    )

    # Image overlays: each shown once sequentially after video phase
    prev_label = "vtmp"
    t = img_start_time
    added_images = 0
    for i, op in enumerate(overlay_paths):
        if t + img_show_duration > duration - 1.0:
            break
        input_idx = i + 2  # +2 because [0]=main, [1]=product video
        enable = f"between(t,{t:.1f},{t+img_show_duration:.1f})"
        # Determine output label
        is_last = (i == len(overlay_paths) - 1) or (t + img_show_duration + img_hide_duration + img_show_duration > duration - 1.0)
        out_label = "outv" if is_last else f"itmp{i}"
        filter_parts.append(
            f"[{prev_label}][{input_idx}:v]overlay={pip_x}:{pip_y}:enable='{enable}'[{out_label}]"
        )
        prev_label = out_label
        t += img_show_duration + img_hide_duration
        added_images += 1
        if out_label == "outv":
            break

    # If no images were scheduled, rename vtmp to outv
    if prev_label == "vtmp":
        filter_parts[0] = filter_parts[0].replace("[vtmp]", "[outv]")

    filter_complex = ";".join(filter_parts)

    pip_output = os.path.join(tmp_dir, "pip_seq_output.mp4")
    pip_cmd = [
        "ffmpeg", "-y",
        *input_args,
        "-filter_complex", filter_complex,
        "-map", "[outv]", "-map", "0:a?",
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-r", "30",
        pip_output
    ]

    logger.info(f"[ai-clip {job_id}] V13 seq PiP: filter_complex={filter_complex[:300]}...")

    proc = await asyncio.create_subprocess_exec(
        *pip_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
    if proc.returncode != 0:
        logger.error(f"[ai-clip {job_id}] V13 seq PiP ffmpeg failed: {stderr.decode()[-500:]}")
        # Fallback: try just image PiP
        return await _generate_pip_video(
            video_path, product_image_urls, duration, width, height, tmp_dir, job_id
        )

    logger.info(f"[ai-clip {job_id}] V13 seq PiP generated successfully (video+{added_images} images)")
    return pip_output



# ─── Product Image/Video Upload & AI Analysis Endpoints ──────────────────────────────────────

@router.post("/upload-product-media")
async def upload_product_media(
    file: UploadFile = File(...),
    file_type: str = Form("product-media"),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """V12: 商品画像または商品動画をAzure Blobにアップロードする（PiP合成用）
    対応フォーマット: 画像(jpg/png/webp/gif) + 動画(mp4/mov/avi/webm/mkv)
    """
    verify_admin(x_admin_key)
    from app.services.storage_service import generate_upload_sas
    import httpx

    # V12: 動画フォーマットもサポート
    ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    ALLOWED_VIDEO_TYPES = {"video/mp4", "video/quicktime", "video/x-msvideo", "video/webm", "video/x-matroska"}
    ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".mov", ".avi", ".webm", ".mkv"}

    try:
        # ファイルタイプ判定
        content_type = file.content_type or ""
        filename = file.filename or f"product_{int(time.time())}"
        ext = os.path.splitext(filename)[1].lower()

        is_video = content_type in ALLOWED_VIDEO_TYPES or ext in {".mp4", ".mov", ".avi", ".webm", ".mkv"}
        is_image = content_type in ALLOWED_IMAGE_TYPES or ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"}

        if not is_video and not is_image:
            raise HTTPException(
                status_code=400,
                detail=f"サポートされていないファイル形式です。対応: {', '.join(ALLOWED_EXTENSIONS)}"
            )

        content = await file.read()
        max_size = 100 * 1024 * 1024 if is_video else 20 * 1024 * 1024
        if len(content) > max_size:
            size_label = "100MB" if is_video else "20MB"
            raise HTTPException(status_code=400, detail=f"ファイルが大きすぎます（最大{size_label}）")

        file_id = f"ai-clip-product-{int(time.time())}-{uuid.uuid4().hex[:8]}"

        vid, upload_url, blob_url, expiry = await generate_upload_sas(
            email="ai-clip-generator@aitherhub.com",
            video_id=file_id,
            filename=filename,
        )

        # Content-Typeを正しく設定
        upload_content_type = content_type
        if is_video and not upload_content_type:
            upload_content_type = "video/mp4"
        elif is_image and not upload_content_type:
            upload_content_type = "image/jpeg"

        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.put(
                upload_url,
                content=content,
                headers={
                    "x-ms-blob-type": "BlockBlob",
                    "Content-Type": upload_content_type,
                },
            )
            resp.raise_for_status()

        # Generate read SAS URL
        from app.services.storage_service import generate_read_sas_from_url
        read_url = generate_read_sas_from_url(blob_url)
        if not read_url:
            read_url = blob_url

        media_type = "video" if is_video else "image"
        logger.info(f"[ai-clip] Product {media_type} uploaded: {filename} ({len(content)} bytes) \u2192 {blob_url}")
        return {
            "success": True,
            "blob_url": blob_url,
            "read_url": read_url,
            "file_size": len(content),
            "filename": filename,
            "media_type": media_type,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[ai-clip] Product media upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"アップロードに失敗しました: {str(e)}")


@router.post("/upload-product-image")
async def upload_product_image(
    file: UploadFile = File(...),
    file_type: str = Form("product-image"),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """V12: 後方互換性のために残すが、内部でupload_product_mediaを呼ぶ"""
    return await upload_product_media(file=file, file_type=file_type, x_admin_key=x_admin_key)


class AnalyzeProductImageRequest(BaseModel):
    image_url: Optional[str] = Field(None, description="分析する商品画像のURL")
    image_base64: Optional[str] = Field(None, description="Base64エンコードされた画像データ")
    content_type: Optional[str] = Field("image/jpeg", description="画像のContent-Type")


@router.post("/analyze-product-image")
async def analyze_product_image(
    req: AnalyzeProductImageRequest,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """商品画像をAI分析し、最適な演出を提案する"""
    verify_admin(x_admin_key)
    import openai
    import base64
    import httpx
    from app.services.storage_service import generate_read_sas_from_url

    try:
        # Method 1: Base64 image data provided directly
        if req.image_base64:
            b64_image = req.image_base64
            content_type = req.content_type or "image/jpeg"
            data_url = f"data:{content_type};base64,{b64_image}"
            logger.info(f"[ai-clip] Analyzing product image from base64 ({len(b64_image)} chars)")
        # Method 2: Download from URL (with SAS if needed)
        elif req.image_url:
            access_url = req.image_url
            if "blob.core.windows.net" in req.image_url and "?" not in req.image_url:
                sas_url = generate_read_sas_from_url(req.image_url)
                if sas_url:
                    access_url = sas_url
                    logger.info(f"[ai-clip] Generated SAS URL for analysis: {req.image_url[:60]}...")

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(access_url)
                resp.raise_for_status()
                image_content = resp.content

            content_type = "image/jpeg"
            if req.image_url.lower().endswith(".png"):
                content_type = "image/png"
            elif req.image_url.lower().endswith(".webp"):
                content_type = "image/webp"

            b64_image = base64.b64encode(image_content).decode("utf-8")
            data_url = f"data:{content_type};base64,{b64_image}"
        else:
            raise HTTPException(status_code=400, detail="image_url または image_base64 が必要です")

        ai_client = openai.AsyncOpenAI()
        response = await ai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "あなたは動画制作のプロフェッショナルです。商品画像を分析し、TikTok/ショート動画で使う際の最適な演出を提案してください。\n"
                        "以下のJSON形式で返してください（マークダウンコードブロックなし）:\n"
                        "{\n"
                        '  "product_name": "商品名（推測）",\n'
                        '  "image_type": "パッケージ写真/使用シーン/ビフォーアフター/テクスチャー/集合写真/モデル使用 のいずれか",\n'
                        '  "recommended_effects": ["推奨エフェクト1", "推奨エフェクト2", "推奨エフェクト3"],\n'
                        '  "recommended_mode": "pip または audio_only のどちらが最適か",\n'
                        '  "color_palette": ["#hex1", "#hex2", "#hex3"],\n'
                        '  "text_position": "テキスト配置の推奨位置（上部/下部/左/右）",\n'
                        '  "animation_suggestion": "Ken Burns/ズームイン/パン/フェード/スプリットスクリーン等の推奨アニメーション",\n'
                        '  "caption_color_suggestion": "#推奨テロップカラー（画像に合う色）",\n'
                        '  "reasoning": "この提案の理由を1-2文で"\n'
                        "}\n"
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "この商品画像を分析して、TikTok動画での最適な演出を提案してください。",
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url, "detail": "high"},
                        },
                    ],
                },
            ],
            max_tokens=800,
            temperature=0.3,
        )

        raw_text = response.choices[0].message.content.strip()

        # Parse JSON
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw_text)
        if json_match:
            raw_text = json_match.group(1).strip()

        try:
            analysis = json.loads(raw_text)
        except json.JSONDecodeError:
            # Try to extract JSON from the response
            json_start = raw_text.find('{')
            json_end = raw_text.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                analysis = json.loads(raw_text[json_start:json_end])
            else:
                analysis = {"error": "AI応答のパースに失敗しました", "raw": raw_text[:200]}

        logger.info(f"[ai-clip] Product image analyzed: {analysis.get('product_name', 'unknown')}")
        return analysis

    except openai.OpenAIError as e:
        logger.error(f"[ai-clip] OpenAI analysis failed: {e}")
        raise HTTPException(status_code=500, detail=f"AI分析に失敗しました: {str(e)}")
    except Exception as e:
        logger.error(f"[ai-clip] Product image analysis failed: {e}")
        raise HTTPException(status_code=500, detail=f"画像分析に失敗しました: {str(e)}")



# ─── V10: Clip Regeneration from Source ──────────────────────────────────────
class RegenFromSourceRequest(BaseModel):
    """V10: clip-dbの既存クリップから元動画を参照して再生成（AI自動最適化）"""
    target_duration: float = Field(0, ge=0, le=180, description="目標クリップ長（秒）。0=元クリップの長さを維持（推奨）")
    # All other options are auto-optimized by AI
    subtitle_style: str = Field("auto", description="字幕スタイル")
    enable_sfx: bool = Field(True)
    enable_transitions: bool = Field(True)
    enable_hook: bool = Field(True)
    enable_cta: bool = Field(True)
    enable_zoom_pulse: bool = Field(True)
    enable_progress_bar: bool = Field(True)
    enable_subtitle_animation: bool = Field(True)
    enable_keyword_highlight: bool = Field(True)
    position_y: float = Field(75.0, ge=0, le=100)

class BatchRegenRequest(BaseModel):
    """V10: 一括再生成リクエスト（AI自動最適化）"""
    clip_ids: List[str] = Field(..., min_length=1, max_length=50, description="再生成対象のclip_id一覧")
    target_duration: float = Field(0, ge=0, le=180, description="目標クリップ長（秒）。0=元クリップの長さを維持（推奨）")

@router.post("/clips/{clip_id}/regenerate-from-source")
async def regenerate_clip_from_source(
    clip_id: str,
    req: RegenFromSourceRequest,
    background_tasks: BackgroundTasks,
    x_admin_key: str = Header(None),
):
    """V10: clip-dbの既存クリップから元動画を参照し、最新AIで再生成する"""
    verify_admin(x_admin_key)
    try:
        return await _regenerate_clip_from_source_impl(clip_id, req, background_tasks)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[v10-regen] Endpoint error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"V10 regeneration error: {type(e).__name__}: {str(e)[:300]}")

async def _regenerate_clip_from_source_impl(clip_id: str, req: RegenFromSourceRequest, background_tasks: BackgroundTasks):
    """V10: 実装本体"""
    # 1. Get clip info from video_clips
    async with engine.connect() as conn:
        result = await conn.execute(text("""
            SELECT vc.id, vc.video_id, vc.phase_index, vc.time_start, vc.time_end,
                   vc.duration_sec, vc.clip_url, vc.transcript_text,
                   vc.product_name, vc.cta_score, vc.importance_score, vc.captions,
                   vc.subtitle_style, vc.liver_name, vc.tags,
                   v.compressed_blob_url, v.user_id, v.original_filename,
                   u.email as user_email
            FROM video_clips vc
            LEFT JOIN videos v ON v.id = vc.video_id
            LEFT JOIN users u ON v.user_id = u.id
            WHERE vc.id = CAST(:clip_id AS uuid)
        """), {"clip_id": clip_id})
        clip_row = result.fetchone()
        # Convert SQLAlchemy Row to dict INSIDE session (Row can't be accessed after session close)
        clip_data = None
        if clip_row:
            clip_data = {
                "id": str(clip_row.id),
                "video_id": str(clip_row.video_id) if clip_row.video_id else None,
                "phase_index": clip_row.phase_index,
                "time_start": clip_row.time_start,
                "time_end": clip_row.time_end,
                "duration_sec": clip_row.duration_sec,
                "clip_url": clip_row.clip_url,
                "transcript_text": clip_row.transcript_text,
                "product_name": clip_row.product_name,
                "cta_score": clip_row.cta_score,
                "importance_score": clip_row.importance_score,
                "captions": clip_row.captions,
                "subtitle_style": clip_row.subtitle_style,
                "liver_name": clip_row.liver_name,
                "tags": clip_row.tags,
                "compressed_blob_url": clip_row.compressed_blob_url,
                "user_id": str(clip_row.user_id) if clip_row.user_id else None,
                "original_filename": clip_row.original_filename,
                "user_email": clip_row.user_email,
            }
    if not clip_data:
        raise HTTPException(status_code=404, detail="クリップが見つかりません")
    # 2. Compute original quality score for comparison
    original_clip_data = {
        "transcript_text": clip_data["transcript_text"],
        "product_name": clip_data["product_name"],
        "cta_score": clip_data["cta_score"],
        "importance_score": clip_data["importance_score"],
        "duration_sec": clip_data["duration_sec"],
        "captions": clip_data["captions"],
    }
    original_quality_score = _compute_clip_quality_score(original_clip_data)
    # 3. Create regeneration job
    regen_job_id = str(uuid.uuid4())
    regen_job = {
        "job_id": regen_job_id,
        "status": "processing",
        "progress_pct": 0,
        "current_step": "V11再生成準備中...",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "type": "regenerate_from_source",
            "source_clip_id": clip_id,
            "video_id": clip_data["video_id"],
            "original_time_start": clip_data["time_start"],
            "original_time_end": clip_data["time_end"],
            "expand_mode": "auto",
            "target_duration": req.target_duration,
            "subtitle_style": req.subtitle_style,
            "original_quality_score": original_quality_score,
        },
        "source_clip": {
            "clip_id": clip_id,
            "video_id": clip_data["video_id"],
            "product_name": clip_data["product_name"] or "",
            "liver_name": clip_data["liver_name"] or "",
            "original_duration": clip_data["duration_sec"],
            "original_quality_score": original_quality_score,
        },
        "results": [],
    }
    await _save_job(regen_job_id, regen_job)
    # 4. Start background regeneration
    background_tasks.add_task(
        _run_regeneration_from_source,
        regen_job_id, clip_data, req,
    )
    return {
        "job_id": regen_job_id,
        "status": "processing",
        "message": "V11再生成を開始しました。元クリップの範囲を尊重しつつ、最新AI処理を適用します。",
        "original_quality_score": original_quality_score,
        "source_clip_id": clip_id,
    }

@router.post("/clips/batch-regenerate")
async def batch_regenerate_clips(
    req: BatchRegenRequest,
    background_tasks: BackgroundTasks,
    x_admin_key: str = Header(None),
):
    """V10: 複数クリップを一括で再生成する"""
    verify_admin(x_admin_key)
    # Validate all clip_ids exist
    async with get_session() as session:
        result = await session.execute(text("""
            SELECT vc.id
            FROM video_clips vc
            WHERE vc.id = ANY(CAST(:clip_ids AS uuid[]))
        """), {"clip_ids": req.clip_ids})
        clips = result.fetchall()
    if not clips:
        raise HTTPException(status_code=404, detail="指定されたクリップが見つかりません")
    found_ids = {str(c.id) for c in clips}
    missing_ids = [cid for cid in req.clip_ids if cid not in found_ids]
    # Create batch job
    batch_job_id = str(uuid.uuid4())
    batch_job = {
        "job_id": batch_job_id,
        "status": "processing",
        "progress_pct": 0,
        "current_step": f"一括再生成準備中... ({len(clips)}件)",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "type": "batch_regenerate_from_source",
            "clip_count": len(clips),
            "target_duration": req.target_duration,
            "mode": "ai_auto_optimize",
        },
        "results": [],
    }
    await _save_job(batch_job_id, batch_job)
    # Start background batch processing
    background_tasks.add_task(
        _run_batch_regeneration,
        batch_job_id, req,
    )
    return {
        "job_id": batch_job_id,
        "status": "processing",
        "message": f"{len(clips)}件のクリップを一括再生成します。",
        "total_clips": len(clips),
        "missing_ids": missing_ids,
    }

@router.get("/clips/{clip_id}/regen-compare")
async def get_regen_comparison(
    clip_id: str,
    x_admin_key: str = Header(None),
):
    """V10: 旧版vs新版の品質スコア比較データを取得"""
    verify_admin(x_admin_key)
    # Get original clip data
    async with get_session() as session:
        result = await session.execute(text("""
            SELECT vc.id, vc.transcript_text, vc.product_name, vc.cta_score,
                   vc.importance_score, vc.duration_sec, vc.captions,
                   vc.clip_url, vc.thumbnail_url, vc.liver_name
            FROM video_clips vc
            WHERE vc.id = CAST(:clip_id AS uuid)
        """), {"clip_id": clip_id})
        original = result.fetchone()
    if not original:
        raise HTTPException(status_code=404, detail="クリップが見つかりません")
    original_score = _compute_clip_quality_score({
        "transcript_text": original.transcript_text,
        "product_name": original.product_name,
        "cta_score": original.cta_score,
        "importance_score": original.importance_score,
        "duration_sec": original.duration_sec,
        "captions": original.captions,
    })
    # Find regeneration jobs for this clip
    regen_jobs = []
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("""
                SELECT job_id, status, progress_pct, results, config, created_at
                FROM ai_clip_jobs
                WHERE config::text LIKE :pattern
                ORDER BY created_at DESC
                LIMIT 10
            """), {"pattern": f'%"source_clip_id": "{clip_id}"%'})
            rows = result.fetchall()
            for row in rows:
                job_results = row.results if isinstance(row.results, list) else json.loads(row.results or "[]")
                job_config = row.config if isinstance(row.config, dict) else json.loads(row.config or "{}")
                regen_jobs.append({
                    "job_id": row.job_id,
                    "status": row.status,
                    "progress_pct": row.progress_pct,
                    "created_at": str(row.created_at),
                    "results": job_results,
                    "new_quality_score": job_config.get("new_quality_score"),
                })
    except Exception as e:
        logger.warning(f"[v10-regen] Failed to fetch regen jobs: {e}")
    return {
        "clip_id": clip_id,
        "original": {
            "clip_url": original.clip_url,
            "thumbnail_url": original.thumbnail_url,
            "quality_score": original_score,
            "duration_sec": original.duration_sec,
            "product_name": original.product_name,
            "liver_name": original.liver_name,
            "transcript_text": (original.transcript_text or "")[:200],
        },
        "regenerations": regen_jobs,
    }

async def _run_regeneration_from_source(job_id: str, clip_row, req: RegenFromSourceRequest):
    """V10: 元動画から拡張範囲で再カット＋最新AI処理"""
    try:
        async with _AI_CLIP_SEMAPHORE:
            await _run_regeneration_from_source_inner(job_id, clip_row, req)
    except Exception as e:
        logger.error(f"[v10-regen {job_id}] Fatal error: {e}", exc_info=True)
        await _update_job(job_id, status="error", error=str(e)[:500])

async def _run_regeneration_from_source_inner(job_id: str, clip_row, req: RegenFromSourceRequest):
    """V10: 再生成の実際の処理"""
    import httpx
    clip_id = str(clip_row["id"] if isinstance(clip_row, dict) else clip_row.id)
    video_id = str(clip_row["video_id"] if isinstance(clip_row, dict) else clip_row.video_id) if (clip_row.get("video_id") if isinstance(clip_row, dict) else clip_row.video_id) else None
    logger.info(f"[v10-regen {job_id}] Starting regeneration for clip {clip_id} from video {video_id}")
    await _update_job(job_id, progress_pct=5, current_step="元動画URL取得中...")
    # ── Step 1: Get the FULL original video URL ──
    video_url = None
    # Strategy: Get full video from videos table via compressed_blob_url
    if video_id:
        try:
            from app.services.storage_service import (
                generate_read_sas_from_url, ACCOUNT_NAME, CONTAINER_NAME,
            )
            compressed_blob = clip_row.get('compressed_blob_url') if isinstance(clip_row, dict) else getattr(clip_row, 'compressed_blob_url', None)
            user_email = clip_row.get('user_email') if isinstance(clip_row, dict) else getattr(clip_row, 'user_email', None)
            if compressed_blob:
                # Build full URL from compressed_blob_url
                import re as _re
                segments = compressed_blob.split("/")
                if "@" in segments[0] or len(segments) >= 3:
                    blob_name = compressed_blob
                else:
                    if user_email:
                        uuid_match = _re.search(
                            r'([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})',
                            segments[-1]
                        )
                        original_case_vid = uuid_match.group(1) if uuid_match else video_id
                        blob_name = f"{user_email}/{original_case_vid}/{compressed_blob}"
                    else:
                        blob_name = compressed_blob
                full_url = f"https://{ACCOUNT_NAME}.blob.core.windows.net/{CONTAINER_NAME}/{blob_name}"
                sas_url = generate_read_sas_from_url(full_url, expires_hours=2)
                if sas_url:
                    video_url = sas_url
                    logger.info(f"[v10-regen {job_id}] Got full video URL from compressed_blob_url")
            # Fallback: generate from email/video_id/video_id.mp4
            if not video_url and user_email:
                blob_name = f"{user_email}/{video_id}/{video_id}.mp4"
                full_url = f"https://{ACCOUNT_NAME}.blob.core.windows.net/{CONTAINER_NAME}/{blob_name}"
                sas_url = generate_read_sas_from_url(full_url, expires_hours=2)
                if sas_url:
                    video_url = sas_url
                    logger.info(f"[v10-regen {job_id}] Got full video URL from email/video_id path")
        except Exception as e:
            logger.warning(f"[v10-regen {job_id}] Failed to get full video URL: {e}")
    # Fallback: Use the clip_url directly (already-cut segment)
    if not video_url:
        raw_url = clip_row["clip_url"] if isinstance(clip_row, dict) else clip_row.clip_url
        if raw_url:
            try:
                from app.services.storage_service import generate_read_sas_from_url
                if "blob.core.windows.net" in raw_url and "?" not in raw_url:
                    sas_url = generate_read_sas_from_url(raw_url, expires_hours=2)
                    video_url = sas_url or raw_url
                elif "cdn.aitherhub.com" in raw_url:
                    blob_url_conv = raw_url.replace("https://cdn.aitherhub.com", "https://aitherhub.blob.core.windows.net")
                    sas_url = generate_read_sas_from_url(blob_url_conv, expires_hours=2)
                    video_url = sas_url or raw_url
                else:
                    video_url = raw_url
            except Exception:
                video_url = raw_url
            logger.warning(f"[v10-regen {job_id}] Using clip_url as fallback (cannot expand time range)")
    if not video_url:
        await _update_job(job_id, status="error", error="元動画のURLが取得できません")
        return
    # ── Step 2: Download the video ──
    await _update_job(job_id, progress_pct=10, current_step="元動画をダウンロード中...")
    tmp_dir = tempfile.mkdtemp(prefix="v10_regen_")
    full_video_path = os.path.join(tmp_dir, "full_source.mp4")
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream("GET", video_url) as resp:
                resp.raise_for_status()
                with open(full_video_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
    except Exception as e:
        await _update_job(job_id, status="error", error=f"動画ダウンロード失敗: {str(e)[:200]}")
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return
    # ── Step 3: Get full video duration via ffprobe ──
    await _update_job(job_id, progress_pct=20, current_step="動画情報を取得中...")
    probe_cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", full_video_path]
    probe_proc = await asyncio.create_subprocess_exec(
        *probe_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    probe_out, _ = await probe_proc.communicate()
    full_video_duration = 0.0
    video_width, video_height = 1080, 1920
    if probe_out:
        probe_data = json.loads(probe_out)
        for stream in probe_data.get("streams", []):
            if stream.get("codec_type") == "video":
                video_width = int(stream.get("width", 1080))
                video_height = int(stream.get("height", 1920))
                dur_str = stream.get("duration")
                if dur_str:
                    full_video_duration = float(dur_str)
                break
        if not full_video_duration:
            fmt_dur = probe_data.get("format", {}).get("duration")
            if fmt_dur:
                full_video_duration = float(fmt_dur)
    # ── Step 4: Calculate expanded time range ──
    original_start = float((clip_row["time_start"] if isinstance(clip_row, dict) else clip_row.time_start) or 0)
    original_end = float((clip_row["time_end"] if isinstance(clip_row, dict) else clip_row.time_end) or original_start + 30)
    # Determine if we have the full video or just the clip segment
    is_full_video = full_video_duration > (original_end - original_start) * 1.5
    original_duration = original_end - original_start
    # V11 NG率改善: target_duration=0の場合は元クリップの長さを維持（過剰拡張を防止）
    target_dur = req.target_duration if req.target_duration > 0 else original_duration
    # V11: 拡張は最大+15秒までに制限（過剰拡張が無関係コンテンツを含む主原因）
    MAX_EXPAND_SEC = 15.0
    if is_full_video:
        # V11: 拡張は控えめに。元のクリップが良質なので、元の範囲を尊重する。
        needed_extra = max(0, min(target_dur - original_duration, MAX_EXPAND_SEC))
        # Expand slightly toward the end for natural flow
        auto_expand_before = min(original_start, needed_extra * 0.3)
        auto_expand_after = min(
            (full_video_duration - original_end) if full_video_duration > original_end else 0,
            needed_extra * 0.7
        )
        # If one side can't expand enough, give the remainder to the other
        remaining = needed_extra - auto_expand_before - auto_expand_after
        if remaining > 0:
            extra_before = min(original_start - auto_expand_before, remaining)
            auto_expand_before += max(0, extra_before)
            remaining -= max(0, extra_before)
            if remaining > 0:
                extra_after = min((full_video_duration - original_end - auto_expand_after) if full_video_duration > (original_end + auto_expand_after) else 0, remaining)
                auto_expand_after += max(0, extra_after)
        new_start = max(0, original_start - auto_expand_before)
        new_end = min(full_video_duration, original_end + auto_expand_after) if full_video_duration > 0 else original_end + auto_expand_after
        actual_duration = new_end - new_start
    else:
        # We only have the clip segment - use full duration
        new_start = 0
        new_end = full_video_duration or (original_end - original_start)
        actual_duration = new_end - new_start
    logger.info(f"[v10-regen {job_id}] Time range: {original_start:.1f}-{original_end:.1f} \u2192 {new_start:.1f}-{new_end:.1f} (duration: {actual_duration:.1f}s, expand={actual_duration-original_duration:.1f}s, full_video={is_full_video})")
    # ── Step 5: Cut the expanded segment from full video ──
    await _update_job(job_id, progress_pct=30, current_step=f"拡張範囲でカット中 ({new_start:.0f}s-{new_end:.0f}s)...")
    cut_video_path = os.path.join(tmp_dir, "cut_segment.mp4")
    if is_full_video:
        cut_cmd = [
            "ffmpeg", "-y",
            "-ss", str(new_start),
            "-i", full_video_path,
            "-t", str(actual_duration),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            cut_video_path,
        ]
        cut_proc = await asyncio.create_subprocess_exec(
            *cut_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, cut_stderr = await asyncio.wait_for(cut_proc.communicate(), timeout=120)
        if cut_proc.returncode != 0:
            err_msg = cut_stderr.decode()[-300:] if cut_stderr else "Unknown"
            await _update_job(job_id, status="error", error=f"カット失敗: {err_msg}")
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return
    else:
        # Just use the downloaded file as-is
        cut_video_path = full_video_path
    # ── Step 6: Whisper transcription on the new segment ──
    await _update_job(job_id, progress_pct=40, current_step="Whisper音声認識中...")
    captions = []
    try:
        captions = await _transcribe_clip(cut_video_path, target_language="auto")
    except Exception as e:
        logger.warning(f"[v10-regen {job_id}] Whisper failed, using original captions: {e}")
        # Fallback: use original captions if available
        orig_captions = clip_row["captions"] if isinstance(clip_row, dict) else clip_row.captions
        if orig_captions:
            if isinstance(orig_captions, str):
                try:
                    captions = json.loads(orig_captions)
                except Exception:
                    captions = []
            elif isinstance(orig_captions, list):
                captions = orig_captions
    # V2.32: Merge short segments first, then split long ones
    if captions:
        captions = _merge_short_segments(captions)
        captions = _split_long_segments(captions)
    # ── V11 Quality Gate: 拡張後のトランスクリプト品質チェック ──
    transcript_text = " ".join(c.get("text", "") for c in captions if c.get("text"))
    regen_quality_clip = {
        "transcript_text": transcript_text,
        "product_name": (clip_row["product_name"] if isinstance(clip_row, dict) else clip_row.product_name) or "",
        "cta_score": float((clip_row["cta_score"] if isinstance(clip_row, dict) else clip_row.cta_score) or 0),
        "importance_score": float((clip_row["importance_score"] if isinstance(clip_row, dict) else clip_row.importance_score) or 0),
        "duration_sec": actual_duration,
        "captions": captions,
    }
    regen_quality_score = _compute_clip_quality_score(regen_quality_clip)
    # V11: 元クリップの品質スコアも計算して比較
    orig_clip_for_score = {
        "transcript_text": (clip_row["transcript_text"] if isinstance(clip_row, dict) else clip_row.get("transcript_text", "")) or "",
        "product_name": (clip_row["product_name"] if isinstance(clip_row, dict) else clip_row.get("product_name", "")) or "",
        "cta_score": float((clip_row["cta_score"] if isinstance(clip_row, dict) else clip_row.get("cta_score", 0)) or 0),
        "importance_score": float((clip_row["importance_score"] if isinstance(clip_row, dict) else clip_row.get("importance_score", 0)) or 0),
        "duration_sec": original_duration,
        "captions": (clip_row["captions"] if isinstance(clip_row, dict) else clip_row.get("captions")) or [],
    }
    original_quality_score = _compute_clip_quality_score(orig_clip_for_score)
    logger.info(f"[v10-regen {job_id}] Quality check: regen_score={regen_quality_score:.1f}, original_score={original_quality_score:.1f}")
    # V11: 再生成後の品質が低すぎる場合は警告ログ（将来的には元の範囲にフォールバックするが、まずはデータ収集）
    if regen_quality_score < 40:
        logger.warning(
            f"[v10-regen {job_id}] LOW QUALITY after expansion: score={regen_quality_score:.1f}. "
            f"Expanded content may contain irrelevant material."
        )
    # ── Step 7: Generate hook text and CTA ──
    await _update_job(job_id, progress_pct=50, current_step="フック＆CTA生成中...")
    # Build a clip dict compatible with _generate_hook / _generate_cta_text
    clip_dict = {
        "clip_id": clip_id,
        "product_name": (clip_row["product_name"] if isinstance(clip_row, dict) else clip_row.product_name) or "",
        "liver_name": (clip_row["liver_name"] if isinstance(clip_row, dict) else clip_row.liver_name) or "",
        "transcript_text": transcript_text,
    }
    # Build a GenerateRequest for hook generation
    gen_req = GenerateRequest(
        subtitle_style=req.subtitle_style,
        enable_sfx=req.enable_sfx,
        enable_transitions=req.enable_transitions,
        enable_hook=req.enable_hook,
        enable_cta=req.enable_cta,
        enable_zoom_pulse=req.enable_zoom_pulse,
        enable_progress_bar=req.enable_progress_bar,
        enable_subtitle_animation=req.enable_subtitle_animation,
        enable_keyword_highlight=req.enable_keyword_highlight,
        position_y=req.position_y,
        max_duration=req.target_duration,
        min_duration=30.0,
    )
    hook_text = None
    cta_text = None
    if req.enable_hook:
        try:
            hook_text = await _generate_hook(captions, clip_dict, gen_req)
        except Exception as e:
            logger.warning(f"[v10-regen {job_id}] Hook generation failed: {e}")
    if req.enable_cta:
        try:
            cta_text = _generate_cta_text(captions, clip_dict)
        except Exception as e:
            logger.warning(f"[v10-regen {job_id}] CTA generation failed: {e}")
    # ── Step 8: Apply V2 effects (subtitles, zoom, progress bar, etc.) ──
    await _update_job(job_id, progress_pct=60, current_step="エフェクト適用中...")
    subtitle_style = req.subtitle_style or "auto"
    styled_captions = _assign_scene_styles(captions, actual_duration, subtitle_style)
    font_path = _find_cjk_font()
    regen_product_name = (clip_row["product_name"] if isinstance(clip_row, dict) else clip_row.product_name) or ""

    # V2.33: 卖点キーワード生成（regenパス）
    regen_selling_points = []
    if regen_product_name and captions:
        try:
            sp_raw = await _generate_selling_points(captions, regen_product_name)
            if sp_raw:
                regen_selling_points = _assign_selling_point_timings(sp_raw, captions, actual_duration)
        except Exception as sp_err:
            logger.warning(f"[v10-regen {job_id}] V2.33 selling points failed: {sp_err}")

    overlay_images = _generate_overlay_images(
        styled_captions=styled_captions,
        hook_text=hook_text,
        cta_text=cta_text,
        video_width=video_width,
        video_height=video_height,
        duration=actual_duration,
        font_path=font_path,
        tmp_dir=tmp_dir,
        position_y=req.position_y,
        clip_duration=actual_duration,
        product_name=regen_product_name,
        selling_points=regen_selling_points,
    )
    logger.info(f"[v10-regen {job_id}] Generated {len(overlay_images)} overlay images "
                f"(selling_points={len(regen_selling_points)})")
    # ── Step 9: Build ffmpeg command and encode ──
    await _update_job(job_id, progress_pct=70, current_step="動画エンコード中...")
    output_path = os.path.join(tmp_dir, "output.mp4")
    input_args = ["-i", cut_video_path]
    for png_path, _, _ in overlay_images:
        input_args.extend(["-i", png_path])
    if overlay_images:
        fc_parts = ["[0:v]null[vfx]"]
        current_label = "[vfx]"
        for i, (png_path, start_t, end_t) in enumerate(overlay_images):
            input_idx = i + 1
            is_last = (i == len(overlay_images) - 1)
            out_label = "[vout]" if is_last else f"[v{i}]"
            enable_expr = f"between(t\\,{start_t:.3f}\\,{end_t:.3f})"
            fc_parts.append(
                f"{current_label}[{input_idx}:v]overlay=0:0:"
                f"enable='{enable_expr}':"
                f"format=auto{out_label}"
            )
            current_label = out_label
        fc_str = ";".join(fc_parts)
        ffmpeg_cmd = [
            "ffmpeg", "-y",
            *input_args,
            "-filter_complex", fc_str,
            "-map", "[vout]", "-map", "0:a",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            # V2.21: 拼接点硬切モード（-vsync vfrで音視頻同期を保証）
            "-bf", "0", "-flags", "+cgop", "-sc_threshold", "0",
            "-vsync", "vfr",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            output_path,
        ]
    else:
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-i", cut_video_path,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            # V2.21: 拼接点硬切モード（-vsync vfrで音視頻同期を保証）
            "-bf", "0", "-flags", "+cgop", "-sc_threshold", "0",
            "-vsync", "vfr",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            output_path,
        ]
    proc = await asyncio.create_subprocess_exec(
        *ffmpeg_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
    if proc.returncode != 0:
        err_msg = stderr.decode()[-500:] if stderr else "Unknown ffmpeg error"
        await _update_job(job_id, status="error", error=f"ffmpegエラー: {err_msg}")
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return
    # ── Step 10: Upload to Azure Blob Storage ──
    await _update_job(job_id, progress_pct=90, current_step="アップロード中...")
    output_size = os.path.getsize(output_path)
    download_url, blob_url = await _upload_to_blob(output_path, clip_id, job_id)
    # ── Step 11: Compute new quality score (V12: GPTコンテンツ品質評価統合) ──
    new_clip_data = {
        "transcript_text": transcript_text,
        "product_name": (clip_row["product_name"] if isinstance(clip_row, dict) else clip_row.product_name) or "",
        "cta_score": (clip_row["cta_score"] if isinstance(clip_row, dict) else clip_row.cta_score) or 0,
        "importance_score": (clip_row["importance_score"] if isinstance(clip_row, dict) else clip_row.importance_score) or 0,
        "duration_sec": actual_duration,
        "captions": captions,
    }
    orig_clip_data = {
        "transcript_text": clip_row["transcript_text"] if isinstance(clip_row, dict) else clip_row.transcript_text,
        "product_name": clip_row["product_name"] if isinstance(clip_row, dict) else clip_row.product_name,
        "cta_score": clip_row["cta_score"] if isinstance(clip_row, dict) else clip_row.cta_score,
        "importance_score": clip_row["importance_score"] if isinstance(clip_row, dict) else clip_row.importance_score,
        "duration_sec": clip_row["duration_sec"] if isinstance(clip_row, dict) else clip_row.duration_sec,
        "captions": clip_row["captions"] if isinstance(clip_row, dict) else clip_row.captions,
    }
    # V12: 統合スコア（構造40% + GPTコンテンツ60%）
    try:
        combined_new = await _compute_combined_quality_score(new_clip_data, job_id)
        new_quality_score = combined_new["total_score"]
        content_evaluation = {
            "content_score": combined_new["content_score"],
            "structure_score": combined_new["structure_score"],
            "breakdown": combined_new["breakdown"],
            "reasons": combined_new["reasons"],
            "sellability": combined_new["sellability"],
        }
        logger.info(
            f"[v12-regen {job_id}] Combined score: {new_quality_score:.1f} "
            f"(structure={combined_new['structure_score']:.1f}, content={combined_new['content_score']:.1f}, "
            f"sellability={combined_new['sellability']})"
        )
    except Exception as e:
        logger.warning(f"[v12-regen {job_id}] GPT scoring failed, falling back to structure only: {e}")
        new_quality_score = _compute_clip_quality_score(new_clip_data)
        content_evaluation = None
    
    original_quality_score = _compute_clip_quality_score(orig_clip_data)
    
    regen_result = {
        "clip_id": clip_id,
        "status": "done",
        "download_url": download_url,
        "blob_url": blob_url,
        "file_size": output_size,
        "duration_sec": actual_duration,
        "hook_text": hook_text,
        "cta_text": cta_text,
        "captions_count": len(captions),
        "captions": captions,
        "transcript_text": transcript_text[:500],
        "original_quality_score": original_quality_score,
        "new_quality_score": new_quality_score,
        "content_evaluation": content_evaluation,
        "source_clip_id": clip_id,
        "expanded_time_range": {"start": new_start, "end": new_end, "duration": actual_duration},
        "effects_applied": {
            "v10_regenerated": True,
            "subtitle_style": subtitle_style,
            "overlays": len(overlay_images),
            "is_full_video": is_full_video,
        },
    }
    # Update video_clips with V10 regeneration mark and new exported_url
    from datetime import date as _date
    v11_version_tag = f"v11.{_date.today().strftime('%Y%m%d')}"
    try:
        async with get_session() as session:
            await session.execute(text("""
                UPDATE video_clips
                SET ml_model_version = :version_tag,
                    exported_url = :exported_url,
                    exported_at = NOW(),
                    exported_duration = :duration,
                    quality_score = :quality_score
                WHERE id = CAST(:clip_id AS uuid)
            """), {
                "clip_id": clip_id,
                "version_tag": v11_version_tag,
                "exported_url": blob_url,
                "duration": actual_duration,
                "quality_score": new_quality_score,
            })
            await session.commit()
        logger.info(f"[v11-regen {job_id}] Updated clip {clip_id} with version={v11_version_tag}")
    except Exception as e:
        logger.warning(f"[v10-regen {job_id}] Failed to update clip version: {e}")
    # Save final result
    await _update_job(job_id, status="done", progress_pct=100, current_step="V11再生成完了")
    job_data = _load_job(job_id)
    if not job_data:
        job_data = await _load_job_db(job_id)
    if job_data:
        job_data["results"] = [regen_result]
        job_data["status"] = "done"
        job_data["progress_pct"] = 100
        job_data["config"]["new_quality_score"] = new_quality_score
        if content_evaluation:
            job_data["config"]["content_evaluation"] = content_evaluation
        await _save_job(job_id, job_data)
    # Cleanup
    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)
    logger.info(
        f"[v12-regen {job_id}] Complete! Score: {original_quality_score:.1f} → {new_quality_score:.1f}"
        f" (sellability={content_evaluation['sellability'] if content_evaluation else 'n/a'})"
    )

async def _run_batch_regeneration(batch_job_id: str, req: BatchRegenRequest):
    """V12: 一括再生成のバックグラウンド処理（GPTプレフィルター付き）"""
    try:
        clip_ids = req.clip_ids
        total = len(clip_ids)
        completed = 0
        skipped = 0
        results = []
        
        # ─── Phase 1: GPTプレフィルター（商品訴求力チェック）───
        await _update_job(batch_job_id, progress_pct=5,
                          current_step=f"GPT事前評価中... (0/{total})")
        
        prefilter_results = []  # [{clip_id, clip_row, eligible, reason, content_eval}]
        
        for i, clip_id in enumerate(clip_ids):
            try:
                # Get clip data
                async with get_session() as session:
                    result = await session.execute(text("""
                        SELECT vc.id, vc.video_id, vc.phase_index, vc.time_start, vc.time_end,
                               vc.duration_sec, vc.clip_url, vc.transcript_text,
                               vc.product_name, vc.cta_score, vc.importance_score, vc.captions,
                               vc.subtitle_style, vc.liver_name, vc.tags,
                               vc.phase_description,
                               v.compressed_blob_url, v.user_id, v.original_filename,
                               u.email as user_email
                        FROM video_clips vc
                        LEFT JOIN videos v ON v.id = vc.video_id
                        LEFT JOIN users u ON v.user_id = u.id
                        WHERE vc.id = CAST(:clip_id AS uuid)
                    """), {"clip_id": clip_id})
                    clip_row = result.fetchone()
                
                if not clip_row:
                    prefilter_results.append({
                        "clip_id": clip_id, "clip_row": None,
                        "eligible": False, "reason": "クリップが見つかりません",
                        "content_eval": None,
                    })
                    continue
                
                # Convert Row to dict
                clip_dict = {
                    "id": str(clip_row.id),
                    "video_id": str(clip_row.video_id) if clip_row.video_id else None,
                    "phase_index": clip_row.phase_index,
                    "time_start": clip_row.time_start,
                    "time_end": clip_row.time_end,
                    "duration_sec": clip_row.duration_sec,
                    "clip_url": clip_row.clip_url,
                    "transcript_text": clip_row.transcript_text,
                    "product_name": clip_row.product_name,
                    "cta_score": clip_row.cta_score,
                    "importance_score": clip_row.importance_score,
                    "captions": clip_row.captions,
                    "subtitle_style": clip_row.subtitle_style,
                    "liver_name": clip_row.liver_name,
                    "tags": clip_row.tags,
                    "phase_description": clip_row.phase_description,
                    "compressed_blob_url": clip_row.compressed_blob_url,
                    "user_id": str(clip_row.user_id) if clip_row.user_id else None,
                    "original_filename": clip_row.original_filename,
                    "user_email": clip_row.user_email,
                }
                
                # GPTプレフィルター: コンテンツ品質を事前評価
                content_eval = await _gpt_content_quality_score(clip_dict, batch_job_id)
                product_appeal = content_eval.get("breakdown", {}).get("product_appeal", 0)
                sellability = content_eval.get("sellability", "low")
                
                # 判定: 商品訴求力が5以下 かつ sellability=low → スキップ
                if product_appeal <= 5 and sellability == "low":
                    skip_reason = content_eval.get("reasons", ["商品説明なし"])
                    prefilter_results.append({
                        "clip_id": clip_id, "clip_row": clip_dict,
                        "eligible": False,
                        "reason": f"商品訴求力不足 (appeal={product_appeal}/30): {skip_reason[0] if skip_reason else '商品説明なし'}",
                        "content_eval": content_eval,
                    })
                    logger.info(f"[v12-batch {batch_job_id}] SKIP clip {clip_id}: appeal={product_appeal}, sellability={sellability}")
                else:
                    prefilter_results.append({
                        "clip_id": clip_id, "clip_row": clip_dict,
                        "eligible": True, "reason": None,
                        "content_eval": content_eval,
                    })
                    logger.info(f"[v12-batch {batch_job_id}] PASS clip {clip_id}: appeal={product_appeal}, sellability={sellability}")
                
                await _update_job(batch_job_id, progress_pct=int(5 + (i + 1) / total * 25),
                                  current_step=f"GPT事前評価中... ({i+1}/{total})")
            except Exception as e:
                logger.error(f"[v12-batch {batch_job_id}] Prefilter error for {clip_id}: {e}")
                prefilter_results.append({
                    "clip_id": clip_id, "clip_row": None,
                    "eligible": False, "reason": f"評価エラー: {str(e)[:100]}",
                    "content_eval": None,
                })
        
        # ─── Phase 2: eligible なクリップのみ再生成実行 ───
        eligible_clips = [p for p in prefilter_results if p["eligible"]]
        skipped_clips = [p for p in prefilter_results if not p["eligible"]]
        skipped = len(skipped_clips)
        
        # スキップされたクリップの結果を先に追加 + DBに永続化
        for sc in skipped_clips:
            results.append({
                "clip_id": sc["clip_id"],
                "status": "skipped",
                "reason": sc["reason"],
                "content_eval": sc["content_eval"],
            })
            # DBにスキップ情報を永続化（将来の切り抜き精度向上に活用）
            try:
                async with engine.begin() as conn:
                    await conn.execute(text("""
                        UPDATE video_clips
                        SET regen_skipped = TRUE,
                            skip_reason = :reason,
                            skip_evaluated_at = NOW()
                        WHERE id = CAST(:clip_id AS uuid)
                    """), {"clip_id": sc["clip_id"], "reason": sc["reason"]})
                logger.info(f"[v12-batch {batch_job_id}] Persisted skip for {sc['clip_id']}: {sc['reason']}")
            except Exception as e:
                logger.warning(f"[v12-batch {batch_job_id}] Failed to persist skip for {sc['clip_id']}: {e}")
        
        if not eligible_clips:
            # 全てスキップされた場合
            await _update_job(batch_job_id, status="done", progress_pct=100,
                              current_step=f"完了: 全{total}件スキップ（商品訴求力不足）")
            batch_data = _load_job(batch_job_id)
            if not batch_data:
                batch_data = await _load_job_db(batch_job_id)
            if batch_data:
                batch_data["results"] = results
                batch_data["status"] = "done"
                batch_data["progress_pct"] = 100
                batch_data["config"]["skipped_count"] = skipped
                batch_data["config"]["eligible_count"] = 0
                await _save_job(batch_job_id, batch_data)
            logger.info(f"[v12-batch {batch_job_id}] All {total} clips skipped (low product appeal)")
            return
        
        eligible_total = len(eligible_clips)
        await _update_job(batch_job_id, progress_pct=30,
                          current_step=f"再生成実行中... (0/{eligible_total}) [スキップ: {skipped}件]")
        
        for i, ec in enumerate(eligible_clips):
            clip_id = ec["clip_id"]
            clip_row = ec["clip_row"]
            try:
                await _update_job(batch_job_id, progress_pct=int(30 + (i / eligible_total) * 65),
                                  current_step=f"再生成中... ({i+1}/{eligible_total}) [スキップ: {skipped}件]")
                # Create individual regen request (AI auto-optimizes all settings)
                single_req = RegenFromSourceRequest(
                    target_duration=req.target_duration,
                )
                # Run regeneration inline (sequential within batch)
                sub_job_id = str(uuid.uuid4())
                sub_job = {
                    "job_id": sub_job_id,
                    "status": "processing",
                    "progress_pct": 0,
                    "current_step": "処理中...",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "config": {"type": "regenerate_from_source", "source_clip_id": clip_id},
                    "results": [],
                }
                await _save_job(sub_job_id, sub_job)
                await _run_regeneration_from_source_inner(sub_job_id, clip_row, single_req)
                # Get result
                sub_data = _load_job(sub_job_id)
                if not sub_data:
                    sub_data = await _load_job_db(sub_job_id)
                if sub_data and sub_data.get("status") == "done":
                    sub_results = sub_data.get("results", [])
                    results.append({
                        "clip_id": clip_id,
                        "status": "done",
                        "sub_job_id": sub_job_id,
                        "result": sub_results[0] if sub_results else None,
                        "content_eval": ec["content_eval"],
                    })
                    completed += 1
                else:
                    results.append({
                        "clip_id": clip_id,
                        "status": "error",
                        "error": sub_data.get("error", "Unknown error") if sub_data else "Job not found",
                        "content_eval": ec["content_eval"],
                    })
            except Exception as e:
                logger.error(f"[v12-batch {batch_job_id}] Clip {clip_id} failed: {e}")
                results.append({"clip_id": clip_id, "status": "error", "error": str(e)[:200]})
        
        # ─── Phase 3: 結果保存 ───
        await _update_job(batch_job_id, status="done", progress_pct=100,
                          current_step=f"一括再生成完了 ({completed}/{eligible_total}件成功, {skipped}件スキップ)")
        batch_data = _load_job(batch_job_id)
        if not batch_data:
            batch_data = await _load_job_db(batch_job_id)
        if batch_data:
            batch_data["results"] = results
            batch_data["status"] = "done"
            batch_data["progress_pct"] = 100
            batch_data["config"]["skipped_count"] = skipped
            batch_data["config"]["eligible_count"] = eligible_total
            batch_data["config"]["completed_count"] = completed
            await _save_job(batch_job_id, batch_data)
        logger.info(f"[v12-batch {batch_job_id}] Complete: {completed}/{eligible_total} succeeded, {skipped} skipped")
    except Exception as e:
        logger.error(f"[v12-batch {batch_job_id}] Fatal error: {e}", exc_info=True)
        await _update_job(batch_job_id, status="error", error=str(e)[:500])


# ─── Product Master CRUD Endpoints ──────────────────────────────────────────

@router.get("/product-master")
async def list_product_master(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """商品マスター一覧を取得"""
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
    await _ensure_product_master_table()
    async with get_session() as session:
        result = await session.execute(text("""
            SELECT id, product_name, brand_name, product_image_urls,
                   keywords, is_active, created_at, updated_at
            FROM product_master
            WHERE is_active = TRUE
            ORDER BY product_name ASC
        """))
        rows = result.fetchall()
    return [
        {
            "id": str(r.id),
            "product_name": r.product_name,
            "brand_name": r.brand_name or "",
            "product_image_urls": _refresh_product_image_urls(r.product_image_urls) if r.product_image_urls else [],
            "keywords": list(r.keywords) if r.keywords else [],
            "is_active": r.is_active,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rows
    ]


@router.post("/product-master")
async def create_product_master(
    request: Request,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """商品マスターに新規商品を登録"""
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
    await _ensure_product_master_table()
    body = await request.json()
    product_name = body.get("product_name", "").strip()
    if not product_name:
        raise HTTPException(status_code=400, detail="product_name is required")
    brand_name = body.get("brand_name", "").strip()
    product_image_urls = body.get("product_image_urls", [])
    keywords = body.get("keywords", [])
    if isinstance(product_image_urls, str):
        product_image_urls = [product_image_urls]
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]
    import uuid as _uuid
    new_id = str(_uuid.uuid4())
    try:
        async with get_session() as session:
            await session.execute(text("""
                INSERT INTO product_master (id, product_name, brand_name, product_image_urls, keywords)
                VALUES (CAST(:id AS uuid), :product_name, :brand_name, CAST(:product_image_urls AS jsonb), :keywords)
            """), {
                "id": new_id,
                "product_name": product_name,
                "brand_name": brand_name,
                "product_image_urls": json.dumps(product_image_urls),
                "keywords": keywords if keywords else [],
            })
    except Exception as e:
        logger.error(f"[product-master] Insert error: {e}")
        raise HTTPException(status_code=500, detail=f"DB insert error: {str(e)}")
    logger.info(f"[product-master] Created: {product_name} with {len(product_image_urls)} images")
    return {"id": new_id, "product_name": product_name, "status": "created"}


@router.put("/product-master/{product_id}")
async def update_product_master(
    product_id: str,
    request: Request,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """商品マスターを更新"""
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
    await _ensure_product_master_table()
    body = await request.json()
    updates = []
    params = {"pid": product_id}
    if "product_name" in body:
        updates.append("product_name = :product_name")
        params["product_name"] = body["product_name"].strip()
    if "brand_name" in body:
        updates.append("brand_name = :brand_name")
        params["brand_name"] = body["brand_name"].strip()
    if "product_image_urls" in body:
        urls = body["product_image_urls"]
        if isinstance(urls, str):
            urls = [urls]
        updates.append("product_image_urls = CAST(:product_image_urls AS jsonb)")
        params["product_image_urls"] = json.dumps(urls)
    if "keywords" in body:
        kws = body["keywords"]
        if isinstance(kws, str):
            kws = [k.strip() for k in kws.split(",") if k.strip()]
        updates.append("keywords = :keywords")
        params["keywords"] = kws if kws else []
    if "is_active" in body:
        updates.append("is_active = :is_active")
        params["is_active"] = bool(body["is_active"])
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    updates.append("updated_at = NOW()")
    set_clause = ", ".join(updates)
    async with get_session() as session:
        result = await session.execute(text(f"""
            UPDATE product_master SET {set_clause}
            WHERE id = CAST(:pid AS uuid)
        """), params)
        await session.commit()
    logger.info(f"[product-master] Updated: {product_id}")
    return {"id": product_id, "status": "updated"}


@router.delete("/product-master/{product_id}")
async def delete_product_master(
    product_id: str,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """商品マスターを論理削除"""
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
    await _ensure_product_master_table()
    async with get_session() as session:
        await session.execute(text("""
            UPDATE product_master SET is_active = FALSE, updated_at = NOW()
            WHERE id = CAST(:pid AS uuid)
        """), {"pid": product_id})
        await session.commit()
    logger.info(f"[product-master] Deleted (soft): {product_id}")
    return {"id": product_id, "status": "deleted"}


async def _get_product_images_from_master(product_name: str) -> list:
    """商品マスターから商品名でマッチする画像URLリストを取得する。
    完全一致 → 部分一致 → キーワードマッチの順で検索。
    """
    await _ensure_product_master_table()
    if not product_name:
        return []
    async with get_session() as session:
        # 1. 完全一致
        result = await session.execute(text("""
            SELECT product_image_urls FROM product_master
            WHERE LOWER(product_name) = LOWER(:name) AND is_active = TRUE
            LIMIT 1
        """), {"name": product_name.strip()})
        row = result.fetchone()
        if row and row.product_image_urls:
            urls = row.product_image_urls if isinstance(row.product_image_urls, list) else json.loads(row.product_image_urls)
            if urls:
                logger.info(f"[product-master] Exact match for '{product_name}': {len(urls)} images")
                return urls

        # 2. 部分一致（商品名がマスターに含まれる or マスターが商品名に含まれる）
        result = await session.execute(text("""
            SELECT product_name, product_image_urls FROM product_master
            WHERE is_active = TRUE
              AND (LOWER(product_name) LIKE '%' || LOWER(:name) || '%'
                   OR LOWER(:name) LIKE '%' || LOWER(product_name) || '%')
            ORDER BY LENGTH(product_name) DESC
            LIMIT 1
        """), {"name": product_name.strip()})
        row = result.fetchone()
        if row and row.product_image_urls:
            urls = row.product_image_urls if isinstance(row.product_image_urls, list) else json.loads(row.product_image_urls)
            if urls:
                logger.info(f"[product-master] Partial match '{product_name}' -> '{row.product_name}': {len(urls)} images")
                return urls

        # 3. キーワードマッチ
        result = await session.execute(text("""
            SELECT product_name, product_image_urls FROM product_master
            WHERE is_active = TRUE
              AND keywords && ARRAY[:keyword]::text[]
            LIMIT 1
        """), {"keyword": product_name.strip().lower()})
        row = result.fetchone()
        if row and row.product_image_urls:
            urls = row.product_image_urls if isinstance(row.product_image_urls, list) else json.loads(row.product_image_urls)
            if urls:
                logger.info(f"[product-master] Keyword match '{product_name}' -> '{row.product_name}': {len(urls)} images")
                return urls

    return []


# ─── Clip Feedback / Delete API ──────────────────────────────────────────────

@router.post("/clips/{job_id}/{clip_id}/delete")
async def delete_clip_with_feedback(
    job_id: str,
    clip_id: str,
    request: Request,
    x_admin_key: str = Header(None, alias="X-Admin-Key"),
):
    """Delete a clip from job results and save feedback for AI learning"""
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    await _ensure_feedback_table()
    await _ensure_jobs_table()
    
    body = await request.json()
    reason = body.get("reason", "")
    reason_category = body.get("reason_category", "other")
    
    try:
        async with AsyncSession(engine) as session:
            # Get current job data
            result = await session.execute(
                text("SELECT results, config FROM ai_clip_jobs WHERE job_id = :jid"),
                {"jid": job_id}
            )
            row = result.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Job not found")
            
            results = row.results if isinstance(row.results, list) else json.loads(row.results or "[]")
            
            # Find the clip to delete and save its metadata
            clip_metadata = {}
            new_results = []
            for r in results:
                if r.get("clip_id") == clip_id:
                    clip_metadata = r
                else:
                    new_results.append(r)
            
            if not clip_metadata:
                raise HTTPException(status_code=404, detail="Clip not found in job results")
            
            # Save feedback for AI learning
            await session.execute(text("""
                INSERT INTO ai_clip_deletion_feedback (job_id, clip_id, action, reason, reason_category, clip_metadata)
                VALUES (:job_id, :clip_id, 'delete', :reason, :reason_category, CAST(:metadata AS jsonb))
            """), {
                "job_id": job_id,
                "clip_id": clip_id,
                "reason": reason,
                "reason_category": reason_category,
                "metadata": json.dumps(clip_metadata, ensure_ascii=False),
            })
            
            # Update job results (remove the clip)
            await session.execute(text("""
                UPDATE ai_clip_jobs
                SET results = CAST(:results AS jsonb),
                    clips_completed = GREATEST(clips_completed - 1, 0),
                    updated_at = NOW()
                WHERE job_id = :jid
            """), {
                "results": json.dumps(new_results, ensure_ascii=False),
                "jid": job_id,
            })
            
            await session.commit()
            
        logger.info(f"[ai-clip] Clip {clip_id} deleted from job {job_id}. Reason: {reason_category} - {reason}")
        return {"status": "deleted", "reason_saved": True, "remaining_clips": len(new_results)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[ai-clip] Failed to delete clip: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/feedback")
async def get_clip_feedback(
    limit: int = 50,
    category: str = None,
    x_admin_key: str = Header(None, alias="X-Admin-Key"),
):
    """Get clip feedback history for AI learning analysis"""
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    await _ensure_feedback_table()
    
    try:
        async with AsyncSession(engine) as session:
            query = "SELECT * FROM ai_clip_deletion_feedback"
            params = {}
            if category:
                query += " WHERE reason_category = :cat"
                params["cat"] = category
            query += " ORDER BY created_at DESC LIMIT :limit"
            params["limit"] = limit
            
            result = await session.execute(text(query), params)
            rows = result.fetchall()
            
            feedback_list = []
            for row in rows:
                feedback_list.append({
                    "id": str(row.id),
                    "job_id": row.job_id,
                    "clip_id": row.clip_id,
                    "action": row.action,
                    "reason": row.reason,
                    "reason_category": row.reason_category,
                    "clip_metadata": row.clip_metadata,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                })
            
            return {"feedback": feedback_list, "total": len(feedback_list)}
    except Exception as e:
        logger.error(f"[ai-clip] Failed to get feedback: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── AI Clip Download Tracking ────────────────────────────────────────────────

@router.post("/track-download")
async def track_ai_clip_download(
    request: Request,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """AIクリップのダウンロードを記録する（管理画面DL + 共有リンク経由）"""
    body = await request.json()
    job_id = body.get("job_id")
    clip_id = body.get("clip_id")
    source = body.get("source", "admin")  # admin, share_link

    if not job_id:
        raise HTTPException(status_code=422, detail="job_id is required")
    if source not in ("admin", "share_link"):
        source = "admin"

    try:
        async with get_session() as session:
            # Ensure table exists (idempotent)
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS ai_clip_download_log (
                    id BIGSERIAL PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    clip_id TEXT,
                    source TEXT NOT NULL DEFAULT 'admin',
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            await session.execute(text("""
                INSERT INTO ai_clip_download_log (job_id, clip_id, source)
                VALUES (:job_id, :clip_id, :source)
            """), {"job_id": job_id, "clip_id": clip_id, "source": source})
            await session.commit()

            # Return updated count
            result = await session.execute(text("""
                SELECT COUNT(*) as cnt FROM ai_clip_download_log
                WHERE job_id = :job_id AND (:clip_id IS NULL OR clip_id = :clip_id)
            """), {"job_id": job_id, "clip_id": clip_id})
            row = result.fetchone()
            count = row.cnt if row else 1

        logger.info(f"[ai-clip] Download tracked: job={job_id} clip={clip_id} source={source} total={count}")
        return {"ok": True, "download_count": count}
    except Exception as e:
        logger.error(f"[ai-clip] Failed to track download: {e}")
        raise HTTPException(status_code=500, detail=str(e)[:200])


@router.get("/download-counts")
async def get_ai_clip_download_counts(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """全AIクリップのダウンロード回数を一括取得する"""
    verify_admin(x_admin_key)
    try:
        async with get_session() as session:
            # Ensure table exists
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS ai_clip_download_log (
                    id BIGSERIAL PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    clip_id TEXT,
                    source TEXT NOT NULL DEFAULT 'admin',
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            result = await session.execute(text("""
                SELECT job_id, clip_id, COUNT(*) as download_count
                FROM ai_clip_download_log
                GROUP BY job_id, clip_id
            """))
            rows = result.fetchall()

        counts = {}
        for r in rows:
            key = f"{r.job_id}_{r.clip_id}" if r.clip_id else r.job_id
            counts[key] = r.download_count
        return {"counts": counts}
    except Exception as e:
        logger.error(f"[ai-clip] Failed to get download counts: {e}")
        return {"counts": {}}


# ─── Product Master Search (for AI Clip Generation UI) ────────────────────────

@router.get("/product-master/search")
async def search_product_master(
    q: str = Query("", description="Search query (product name or brand name)"),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """商品マスターを検索する（名前・ブランド名で部分一致検索）"""
    verify_admin(x_admin_key)
    await _ensure_product_master_table()

    try:
        async with get_session() as session:
            if q.strip():
                result = await session.execute(text("""
                    SELECT id, product_name, brand_name, product_image_urls,
                           keywords, is_active, created_at
                    FROM product_master
                    WHERE is_active = TRUE
                      AND (
                        product_name ILIKE :q
                        OR brand_name ILIKE :q
                        OR EXISTS (SELECT 1 FROM unnest(keywords) kw WHERE kw ILIKE :q)
                      )
                    ORDER BY product_name ASC
                    LIMIT 20
                """), {"q": f"%{q.strip()}%"})
            else:
                # Return all active products (limited)
                result = await session.execute(text("""
                    SELECT id, product_name, brand_name, product_image_urls,
                           keywords, is_active, created_at
                    FROM product_master
                    WHERE is_active = TRUE
                    ORDER BY updated_at DESC
                    LIMIT 30
                """))
            rows = result.fetchall()

        return {
            "products": [
                {
                    "id": str(r.id),
                    "product_name": r.product_name,
                    "brand_name": r.brand_name or "",
                    "product_image_urls": _refresh_product_image_urls(r.product_image_urls) if r.product_image_urls else [],
                    "keywords": list(r.keywords) if r.keywords else [],
                }
                for r in rows
            ]
        }
    except Exception as e:
        logger.error(f"[ai-clip] Product master search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)[:200])



# ─── V12: Editing Profile Integration ─────────────────────────────────────────

async def _load_editing_profile(profile_id: str) -> dict:
    """編集プロファイルのstyle_paramsをDBから読み込む"""
    try:
        async with get_session() as session:
            result = await session.execute(text("""
                SELECT style_params, status FROM editing_profiles
                WHERE id = :id
            """), {"id": profile_id})
            row = result.fetchone()
        if row and row.style_params:
            params = row.style_params if isinstance(row.style_params, dict) else json.loads(row.style_params)
            logger.info(f"[ai-clip] Loaded editing profile {profile_id}: status={row.status}, "
                        f"params_count={len(params)}")
            return params
        return {}
    except Exception as e:
        logger.warning(f"[ai-clip] Failed to load editing profile {profile_id}: {e}")
        return {}


def _apply_editing_profile_to_request(req: "GenerateRequest", profile_params: dict) -> "GenerateRequest":
    """編集プロファイルのパラメータをGenerateRequestに適用する。
    プロファイルの学習結果に基づいてreqのパラメータを調整する。
    既存のreqフィールドを直接変更せず、新しいオブジェクトを返す。
    """
    # Create a mutable copy of the request
    req_dict = req.dict() if hasattr(req, 'dict') else {}

    # ── Silence threshold adjustment ──
    # プロファイルが「silence_threshold_sec」を持っている場合、無音カット閾値を調整
    if "silence_threshold_sec" in profile_params:
        silence_sec = float(profile_params["silence_threshold_sec"])
        # silence_threshold_sec → silence_threshold_db の変換
        # 短い閾値 = より厳しい無音カット = 高いdB閾値
        if silence_sec <= 0.3:
            req_dict["silence_threshold_db"] = -25.0  # Very aggressive
        elif silence_sec <= 0.5:
            req_dict["silence_threshold_db"] = -28.0  # Aggressive
        elif silence_sec <= 1.0:
            req_dict["silence_threshold_db"] = -30.0  # Normal
        else:
            req_dict["silence_threshold_db"] = -35.0  # Lenient

    # ── Silence handling from pair analysis ──
    if "silence_handling" in profile_params:
        handling = profile_params["silence_handling"]
        if handling == "strict":
            req_dict["silence_threshold_db"] = -25.0
            req_dict["enable_silence_cut"] = True
        elif handling == "lenient":
            req_dict["silence_threshold_db"] = -38.0

    # ── Content cut aggressiveness ──
    if "content_filter" in profile_params:
        cf = profile_params["content_filter"]
        if cf == "strict":
            req_dict["enable_content_cut"] = True
        elif cf == "lenient":
            req_dict["enable_content_cut"] = False

    # ── Filler handling ──
    if "filler_handling" in profile_params:
        fh = profile_params["filler_handling"]
        if fh == "always_cut":
            req_dict["enable_content_cut"] = True
        elif fh == "keep":
            req_dict["enable_content_cut"] = False

    # ── Pacing → speed_factor + zoom_intensity + content_cut ──
    if "pacing" in profile_params:
        pacing = profile_params["pacing"]
        if pacing == "fast":
            req_dict["speed_factor"] = 1.10  # Fast tempo
            req_dict["zoom_intensity"] = 1.12  # More dynamic
            req_dict["enable_content_cut"] = True  # Aggressive cut
            req_dict["enable_silence_cut"] = True
        elif pacing == "medium":
            req_dict["speed_factor"] = 1.05  # Normal TikTok speed
            req_dict["zoom_intensity"] = 1.08
        elif pacing == "slow":
            req_dict["speed_factor"] = 1.0  # No speed change
            req_dict["zoom_intensity"] = 1.04  # Subtle

    # ── Cut aggressiveness → silence/content cut control ──
    if "cut_aggressiveness" in profile_params:
        agg = float(profile_params["cut_aggressiveness"])
        # Scale: 0.0=no cut, 0.5=moderate, 1.0=aggressive
        if agg > 0.6:
            req_dict["enable_silence_cut"] = True
            req_dict["enable_content_cut"] = True
            req_dict["silence_threshold_db"] = -25.0  # Very aggressive
        elif agg > 0.3:
            req_dict["enable_silence_cut"] = True
            req_dict["enable_content_cut"] = True
        # Low aggressiveness: keep defaults (don't disable)

    # ── Cut ratio → speed_factor fine-tuning ──
    if "cut_ratio" in profile_params:
        ratio = float(profile_params["cut_ratio"])
        # cut_ratio: 0.0=no cuts, 1.0=cut everything
        # High cut_ratio means the reference video was heavily edited
        if ratio > 0.7:
            # Reference was heavily cut → be aggressive
            req_dict["enable_content_cut"] = True
            req_dict["enable_silence_cut"] = True

    # ── Content density → speed adjustment ──
    if "content_density" in profile_params:
        density = profile_params["content_density"]
        if density == "high":
            # High density = fast-paced content, slight speed up
            if req_dict.get("speed_factor", 1.05) < 1.08:
                req_dict["speed_factor"] = 1.08

    # ── Subtitle style preference ──
    if "subtitle_style_preference" in profile_params:
        pref = profile_params["subtitle_style_preference"]
        if pref in ("pop", "simple", "box", "gradient", "outline", "karaoke"):
            req_dict["subtitle_style"] = pref

    # ── Transition style ──
    if "transition_preference" in profile_params or "transition_style" in profile_params:
        ts = profile_params.get("transition_preference") or profile_params.get("transition_style", "")
        if ts == "hard_cut" or ts == "hard":
            req_dict["enable_transitions"] = False
        elif ts == "fade":
            req_dict["enable_transitions"] = True
            req_dict["transition_type"] = "fade"
            req_dict["transition_duration"] = 0.3

    # ── Energy level → effects ──
    if "energy_level" in profile_params:
        energy = profile_params["energy_level"]
        if energy == "high":
            req_dict["enable_flash_intro"] = True
            req_dict["enable_zoom_pulse"] = True
            req_dict["enable_sfx"] = True
        elif energy == "low":
            req_dict["enable_flash_intro"] = False
            req_dict["enable_zoom_pulse"] = False
            req_dict["enable_sfx"] = False

    # ── Preferred clip duration → max_duration ──
    if "preferred_clip_duration_sec" in profile_params:
        pref_dur = float(profile_params["preferred_clip_duration_sec"])
        if 15 <= pref_dur <= 180:
            req_dict["max_duration"] = pref_dur

    # ── Preferred segment duration (from pair analysis) ──
    if "preferred_segment_duration" in profile_params:
        seg_dur = float(profile_params["preferred_segment_duration"])
        if 10 <= seg_dur <= 180:
            req_dict["max_duration"] = seg_dur

    # Log applied params for debugging
    changed_keys = [k for k in req_dict if k not in (req.dict() if hasattr(req, 'dict') else {}) or
                    req_dict[k] != (req.dict() if hasattr(req, 'dict') else {}).get(k)]
    if changed_keys:
        logger.info(f"[ai-clip] Editing profile applied changes: {changed_keys}")

    # Reconstruct request object
    try:
        return GenerateRequest(**req_dict)
    except Exception as e:
        logger.warning(f"[ai-clip] Failed to apply editing profile params: {e}")
        return req


# ═══════════════════════════════════════════════════════════════════════════════
# V3.0: 按产品自动分段模式 (Product-based Auto-Segmentation)
# ═══════════════════════════════════════════════════════════════════════════════
# 从完整直播视频中按产品自动分段、提炼卖点字幕、气口剪切后硬切拼接，
# 输出多个独立clip文件。与现有AI Clip模式并存。
# ═══════════════════════════════════════════════════════════════════════════════


class GenerateByProductRequest(BaseModel):
    """V3.0: 按产品自动分段生成リクエスト"""
    video_id: str = Field(..., description="完整直播视频ID (videos表)")
    brand_id: Optional[str] = Field(None, description="ブランドID（product_masterフィルタ用）")
    subtitle_style: str = Field("auto", description="字幕スタイル")
    enable_silence_cut: bool = Field(True, description="≥1秒の気口/停顿を自動カット")
    silence_threshold_db: float = Field(-22.0, ge=-60.0, le=-10.0, description="無音検出閾値(dB)")
    speed_factor: float = Field(1.05, ge=1.0, le=1.3, description="再生速度倍率")
    position_y: float = Field(75.0, ge=0, le=100, description="字幕Y位置（%）")
    target_language: str = Field("auto", description="字幕言語 (auto/ja/zh/zh-tw)")
    # 卖点字幕位置（视频上方）
    highlight_position_y: float = Field(12.0, ge=0, le=50, description="卖点字幕Y位置（%）- 视频上方")
    # 气口剪切参数（V3.0: ≥1秒才剪切，比V2更保守）
    min_silence_duration: float = Field(1.0, ge=0.5, le=3.0, description="最小気口長（秒）- これ以上の停顿をカット")
    # 卖点字幕显示时间
    highlight_display_duration: float = Field(4.0, ge=2.0, le=8.0, description="各卖点字幕の表示時間（秒）")


# ─── V3.0 API Endpoint ───────────────────────────────────────────────────────

@router.post("/generate-by-product")
async def generate_by_product(
    req: GenerateByProductRequest,
    background_tasks: BackgroundTasks,
    x_admin_key: Optional[str] = Header(None),
):
    """V3.0: 按产品自动分段 - 完整直播视频から产品ごとに独立clip生成"""
    verify_admin(x_admin_key)

    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    job_data = {
        "job_id": job_id,
        "status": "queued",
        "progress_pct": 0,
        "current_step": "V3.0 按产品分段モード: 準備中...",
        "clips_completed": 0,
        "clips_total": 0,
        "results": [],
        "error": None,
        "created_at": now,
        "updated_at": now,
        "config": {**req.dict(), "mode": "by_product_v3"},
    }
    await _save_job(job_id, job_data)
    background_tasks.add_task(_run_product_segmentation, job_id, req)
    return {
        "job_id": job_id,
        "status": "queued",
        "message": "V3.0 按产品自動分段ジョブを開始しました",
    }


# ─── V3.0 Main Pipeline ──────────────────────────────────────────────────────

async def _run_product_segmentation(job_id: str, req: GenerateByProductRequest):
    """V3.0 按产品分段のメインパイプライン"""
    try:
        async with _AI_CLIP_SEMAPHORE:
            await _run_product_segmentation_inner(job_id, req)
    except Exception as e:
        logger.error(f"[ai-clip-v3 {job_id}] Fatal error: {e}", exc_info=True)
        await _update_job(job_id, status="failed", error=str(e)[:500])


async def _run_product_segmentation_inner(job_id: str, req: GenerateByProductRequest):
    """V3.0 按产品分段の内部実装"""
    import httpx

    logger.info(f"[ai-clip-v3 {job_id}] Starting V3.0 product segmentation pipeline for video {req.video_id}")
    await _update_job(job_id, status="processing", progress_pct=1,
                      current_step="V3.0: 動画情報取得中...")

    # ── 1. Get video info and download URL ──
    video_url = None
    original_filename = None
    try:
        async with get_session() as session:
            result = await session.execute(text("""
                SELECT v.id, v.user_id, v.original_filename, v.compressed_blob_url, u.email
                FROM videos v
                LEFT JOIN users u ON v.user_id = u.id
                WHERE v.id = CAST(:video_id AS uuid)
            """), {"video_id": req.video_id})
            row = result.fetchone()
            if not row:
                await _update_job(job_id, status="failed", error=f"Video not found: {req.video_id}")
                return
            original_filename = row.original_filename or "video.mp4"

            # Prefer compressed version
            from app.services.storage_service import (
                generate_download_sas, generate_read_sas_from_url,
                ACCOUNT_NAME, CONTAINER_NAME,
            )
            compressed = getattr(row, 'compressed_blob_url', None)
            if compressed:
                try:
                    full_url = f"https://{ACCOUNT_NAME}.blob.core.windows.net/{CONTAINER_NAME}/{compressed}"
                    sas_url = generate_read_sas_from_url(full_url, expires_hours=4)
                    if sas_url:
                        video_url = sas_url
                        logger.info(f"[ai-clip-v3 {job_id}] Using compressed video: {compressed[:60]}")
                except Exception as e:
                    logger.warning(f"[ai-clip-v3 {job_id}] Compressed SAS failed: {e}")

            if not video_url and row.email:
                try:
                    dl_url, _ = await generate_download_sas(
                        email=row.email,
                        video_id=req.video_id,
                        filename=original_filename,
                        expires_in_minutes=1440,
                    )
                    if dl_url:
                        video_url = dl_url
                except Exception as e:
                    logger.warning(f"[ai-clip-v3 {job_id}] Download SAS failed: {e}")

    except Exception as e:
        await _update_job(job_id, status="failed", error=f"DB error: {str(e)[:200]}")
        return

    if not video_url:
        await _update_job(job_id, status="failed", error="動画のダウンロードURLが取得できません")
        return

    await _update_job(job_id, progress_pct=3, current_step="V3.0: 動画ダウンロード中...")

    # ── 2. Download video ──
    tmp_dir = tempfile.mkdtemp(prefix=f"ai_clip_v3_{job_id[:8]}_")
    video_path = os.path.join(tmp_dir, "full_video.mp4")
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("GET", video_url) as resp:
                resp.raise_for_status()
                total_bytes = int(resp.headers.get('content-length', 0))
                downloaded = 0
                with open(video_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=131072):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_bytes > 0:
                            dl_pct = 3 + int((downloaded / total_bytes) * 7)
                            await _update_job(job_id, progress_pct=min(dl_pct, 10),
                                current_step=f"V3.0: ダウンロード中 {downloaded//1024//1024}MB/{total_bytes//1024//1024}MB")

        file_size = os.path.getsize(video_path)
        logger.info(f"[ai-clip-v3 {job_id}] Downloaded video: {file_size//1024//1024}MB")
    except Exception as e:
        await _update_job(job_id, status="failed", error=f"Download failed: {str(e)[:200]}")
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return

    await _update_job(job_id, progress_pct=10, current_step="V3.0: 動画情報解析中...")

    # ── 3. Get video info (ffprobe) ──
    probe_cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", video_path
    ]
    probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
    probe_data = json.loads(probe_result.stdout) if probe_result.returncode == 0 else {}
    video_width = 1080
    video_height = 1920
    video_fps = 30
    total_duration = 0.0
    for stream in probe_data.get("streams", []):
        if stream.get("codec_type") == "video":
            video_width = int(stream.get("width", 1080))
            video_height = int(stream.get("height", 1920))
            r_frame_rate = stream.get("r_frame_rate", "30/1")
            try:
                num, den = r_frame_rate.split("/")
                video_fps = round(int(num) / int(den))
            except (ValueError, ZeroDivisionError):
                video_fps = 30
            video_fps = max(24, min(60, video_fps))
            if "duration" in stream:
                total_duration = float(stream["duration"])
            break
    if "format" in probe_data and "duration" in probe_data["format"]:
        total_duration = float(probe_data["format"]["duration"])
    if total_duration <= 0:
        await _update_job(job_id, status="failed", error="動画の長さが取得できません")
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return

    logger.info(f"[ai-clip-v3 {job_id}] Video: {video_width}x{video_height}, {total_duration:.1f}s, {video_fps}fps")
    await _update_job(job_id, progress_pct=12, current_step="V3.0: 全文音声認識中 (Whisper)...")

    # ── 4. Full transcription (Whisper) ──
    full_captions = await _transcribe_clip(video_path, req.target_language)
    if not full_captions:
        await _update_job(job_id, status="failed", error="音声認識に失敗しました（字幕なし）")
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return

    # V2.32: Merge short segments first, then split long ones
    full_captions = _merge_short_segments(full_captions)
    full_captions = _split_long_segments(full_captions)
    logger.info(f"[ai-clip-v3 {job_id}] Transcription: {len(full_captions)} segments, "
                f"total text: {sum(len(c.get('text','')) for c in full_captions)} chars")
    await _update_job(job_id, progress_pct=25, current_step="V3.0: 产品分段検出中...")

    # ── 5. Detect product segments ──
    product_segments = await _v3_detect_product_segments(
        job_id, req.video_id, req.brand_id, full_captions, total_duration
    )
    if not product_segments:
        await _update_job(job_id, status="failed", error="产品分段が検出できませんでした")
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return

    clips_total = len(product_segments)
    await _update_job(job_id, clips_total=clips_total, progress_pct=35,
                      current_step=f"V3.0: {clips_total}个产品分段を検出、卖点提炼中...")
    logger.info(f"[ai-clip-v3 {job_id}] Detected {clips_total} product segments")

    # ── 6. Extract highlights for each product ──
    for i, seg in enumerate(product_segments):
        try:
            highlights = await _v3_extract_highlights(
                seg["transcript"], seg["product_name"], job_id
            )
            seg["highlights"] = highlights
            logger.info(f"[ai-clip-v3 {job_id}] Product '{seg['product_name']}': {len(highlights)} highlights")
        except Exception as e:
            logger.warning(f"[ai-clip-v3 {job_id}] Highlight extraction failed for '{seg['product_name']}': {e}")
            seg["highlights"] = []

    await _update_job(job_id, progress_pct=40, current_step="V3.0: 各产品clip生成開始...")

    # ── 7. Process each product segment into independent clip ──
    results = []
    for i, seg in enumerate(product_segments):
        clip_pct = 40 + int((i / clips_total) * 55)
        await _update_job(job_id, progress_pct=clip_pct, clips_completed=i,
                          current_step=f"V3.0: 产品 {i+1}/{clips_total} 「{seg['product_name']}」処理中...")

        try:
            result = await _v3_process_product_clip(
                job_id, video_path, seg, req, i, clips_total,
                video_width, video_height, video_fps, tmp_dir
            )
            results.append(result)
            logger.info(f"[ai-clip-v3 {job_id}] Product clip {i+1}/{clips_total} "
                        f"'{seg['product_name']}': {result.get('status', 'unknown')}")
        except Exception as e:
            logger.error(f"[ai-clip-v3 {job_id}] Product clip {i+1} failed: {e}", exc_info=True)
            results.append({
                "product_name": seg["product_name"],
                "status": "failed",
                "error": str(e)[:200],
            })
        await _update_job(job_id, clips_completed=i + 1, results=results)

    # ── 8. Finalize ──
    success_count = sum(1 for r in results if r.get("status") == "done")
    await _update_job(
        job_id, status="done", progress_pct=100,
        current_step=f"V3.0 完了: {success_count}/{clips_total}件成功",
        clips_completed=clips_total, results=results,
    )
    logger.info(f"[ai-clip-v3 {job_id}] Pipeline complete: {success_count}/{clips_total} success")

    # Cleanup
    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)


# ─── V3.0 Product Segment Detection ──────────────────────────────────────────

async def _v3_detect_product_segments(
    job_id: str, video_id: str, brand_id: Optional[str],
    captions: list, total_duration: float
) -> list:
    """
    产品分段検出:
    1. video_product_exposures テーブルから既存データを取得
    2. なければ product_master のキーワードで転写テキストをマッチング
    3. それでもなければ GPT-4o で产品切替点を識別
    
    Returns: [{product_name, time_start, time_end, transcript, captions, highlights}]
    """
    segments = []

    # ── Strategy 1: Use existing video_product_exposures ──
    try:
        async with get_session() as session:
            result = await session.execute(text("""
                SELECT product_name, brand_name, time_start, time_end, confidence
                FROM video_product_exposures
                WHERE video_id = CAST(:video_id AS uuid)
                ORDER BY time_start ASC
            """), {"video_id": video_id})
            rows = result.fetchall()
            if rows:
                for row in rows:
                    # Get captions within this time range
                    seg_captions = [c for c in captions
                                    if float(c.get("start", 0)) >= row.time_start - 1.0
                                    and float(c.get("end", 0)) <= row.time_end + 1.0]
                    transcript = " ".join(c.get("text", "") for c in seg_captions).strip()
                    if transcript and (row.time_end - row.time_start) >= 10.0:
                        segments.append({
                            "product_name": row.product_name,
                            "brand_name": row.brand_name or "",
                            "time_start": row.time_start,
                            "time_end": row.time_end,
                            "transcript": transcript,
                            "captions": seg_captions,
                            "confidence": row.confidence or 0.8,
                            "source": "video_product_exposures",
                        })
                if segments:
                    logger.info(f"[ai-clip-v3 {job_id}] Strategy 1: Found {len(segments)} segments from video_product_exposures")
                    return segments
    except Exception as e:
        logger.warning(f"[ai-clip-v3 {job_id}] Strategy 1 failed: {e}")

    # ── Strategy 2: Match product_master keywords against transcript ──
    try:
        products = []
        async with get_session() as session:
            pm_sql = "SELECT product_name, brand_name, keywords FROM product_master WHERE is_active = TRUE"
            if brand_id:
                pm_sql += " AND brand_name = (SELECT client_name FROM widget_clients WHERE client_id = :brand_id LIMIT 1)"
            result = await session.execute(text(pm_sql), {"brand_id": brand_id} if brand_id else {})
            products = result.fetchall()

        if products:
            # Build full transcript text with timestamps
            full_text = " ".join(c.get("text", "") for c in captions)
            matched_products = []
            for prod in products:
                keywords = prod.keywords or []
                product_name = prod.product_name
                # Check if product name or any keyword appears in transcript
                found = product_name.lower() in full_text.lower()
                if not found:
                    for kw in keywords:
                        if kw and kw.lower() in full_text.lower():
                            found = True
                            break
                if found:
                    matched_products.append({
                        "product_name": product_name,
                        "brand_name": prod.brand_name or "",
                        "keywords": keywords,
                    })

            if matched_products:
                # Find time ranges for each matched product using keyword positions in captions
                segments = _v3_find_product_time_ranges(matched_products, captions, total_duration)
                if segments:
                    logger.info(f"[ai-clip-v3 {job_id}] Strategy 2: Found {len(segments)} segments from keyword matching")
                    return segments
    except Exception as e:
        logger.warning(f"[ai-clip-v3 {job_id}] Strategy 2 failed: {e}")

    # ── Strategy 3: GPT-4o product detection ──
    try:
        segments = await _v3_gpt_detect_products(job_id, captions, total_duration)
        if segments:
            logger.info(f"[ai-clip-v3 {job_id}] Strategy 3: GPT detected {len(segments)} product segments")
            return segments
    except Exception as e:
        logger.warning(f"[ai-clip-v3 {job_id}] Strategy 3 (GPT) failed: {e}")

    return segments


def _v3_find_product_time_ranges(matched_products: list, captions: list, total_duration: float) -> list:
    """
    从转写文本中找到每个产品的时间范围。
    基于关键词首次出现位置和后续相关内容来确定产品介绍的起止时间。
    """
    segments = []
    used_ranges = []  # Track used time ranges to avoid overlap

    for prod in matched_products:
        product_name = prod["product_name"]
        keywords = [product_name] + (prod.get("keywords") or [])
        keywords_lower = [k.lower() for k in keywords if k]

        # Find all captions mentioning this product
        mention_indices = []
        for i, cap in enumerate(captions):
            cap_text = (cap.get("text") or "").lower()
            for kw in keywords_lower:
                if kw in cap_text:
                    mention_indices.append(i)
                    break

        if not mention_indices:
            continue

        # Determine time range: from first mention to last mention + context
        first_idx = mention_indices[0]
        last_idx = mention_indices[-1]

        # Extend range: include 2 captions before first mention and 5 after last
        range_start_idx = max(0, first_idx - 2)
        range_end_idx = min(len(captions) - 1, last_idx + 5)

        time_start = float(captions[range_start_idx].get("start", 0))
        time_end = float(captions[range_end_idx].get("end", 0))

        # Minimum segment duration: 10 seconds
        if time_end - time_start < 10.0:
            time_end = min(time_start + 30.0, total_duration)

        # Check overlap with existing segments
        overlap = False
        for used_start, used_end in used_ranges:
            if time_start < used_end and time_end > used_start:
                overlap = True
                break
        if overlap:
            continue

        # Get captions within range
        seg_captions = [c for c in captions
                        if float(c.get("start", 0)) >= time_start - 0.5
                        and float(c.get("end", 0)) <= time_end + 0.5]
        transcript = " ".join(c.get("text", "") for c in seg_captions).strip()

        if transcript:
            segments.append({
                "product_name": product_name,
                "brand_name": prod.get("brand_name", ""),
                "time_start": time_start,
                "time_end": time_end,
                "transcript": transcript,
                "captions": seg_captions,
                "confidence": 0.7,
                "source": "keyword_match",
            })
            used_ranges.append((time_start, time_end))

    # Sort by time
    segments.sort(key=lambda x: x["time_start"])
    return segments


async def _v3_gpt_detect_products(job_id: str, captions: list, total_duration: float) -> list:
    """
    GPT-4oを使って转写テキストから产品切替点を検出する。
    """
    import openai

    azure_key = os.getenv("AZURE_OPENAI_KEY", "")
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    azure_model = os.getenv("GPT5_MODEL") or os.getenv("GPT5_DEPLOYMENT") or "gpt-4.1-mini"

    if not azure_key or not azure_endpoint:
        logger.warning(f"[ai-clip-v3 {job_id}] No Azure OpenAI credentials for GPT detection")
        return []

    from urllib.parse import urlparse as _urlparse
    _parsed = _urlparse(azure_endpoint)
    clean_endpoint = f"{_parsed.scheme}://{_parsed.netloc}/"

    client = openai.AsyncAzureOpenAI(
        api_key=azure_key,
        azure_endpoint=clean_endpoint,
        api_version=os.getenv("GPT5_API_VERSION", "2025-04-01-preview"),
    )

    # Format captions with timestamps
    caption_lines = []
    for cap in captions:
        s = float(cap.get("start", 0))
        e = float(cap.get("end", 0))
        text_content = (cap.get("text") or "").strip()
        if text_content:
            caption_lines.append(f"[{s:.1f}-{e:.1f}] {text_content}")

    # Limit to avoid token overflow (keep first 500 lines)
    if len(caption_lines) > 500:
        caption_lines = caption_lines[:500]

    transcript_text = "\n".join(caption_lines)

    prompt = f"""あなたはライブコマース動画の分析エキスパートです。
以下はライブ配信の音声書き起こし（タイムスタンプ付き）です。

この配信で紹介されている各商品/製品を特定し、それぞれの紹介開始時刻と終了時刻を検出してください。

ルール:
1. 各商品の紹介区間は、商品名が最初に言及された前後から、次の商品に切り替わるまでの範囲とする
2. 商品名が明確でない場合は、説明内容から推測して名前をつける（例：「保湿クリーム」「ヘアオイル」）
3. 同じ商品が複数回言及される場合は、最初の連続した紹介区間のみを1つのセグメントとする
4. 最低10秒以上の紹介区間のみ出力する
5. 雑談・挨拶・Q&Aのみの区間は除外する

出力形式（JSON配列）:
[
  {{"product_name": "商品名", "time_start": 開始秒, "time_end": 終了秒}},
  ...
]

音声書き起こし:
{transcript_text}

JSON配列のみを出力してください。説明は不要です。"""

    try:
        response = await client.chat.completions.create(
            model=azure_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=2000,
        )
        content = response.choices[0].message.content.strip()

        # Parse JSON from response
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
        if content.startswith("json"):
            content = content[4:].strip()

        detected = json.loads(content)
        if not isinstance(detected, list):
            return []

        segments = []
        for item in detected:
            product_name = item.get("product_name", "").strip()
            time_start = float(item.get("time_start", 0))
            time_end = float(item.get("time_end", 0))

            if not product_name or time_end - time_start < 10.0:
                continue
            if time_start < 0 or time_end > total_duration + 5:
                continue

            # Get captions within range
            seg_captions = [c for c in captions
                            if float(c.get("start", 0)) >= time_start - 1.0
                            and float(c.get("end", 0)) <= time_end + 1.0]
            transcript = " ".join(c.get("text", "") for c in seg_captions).strip()

            if transcript:
                segments.append({
                    "product_name": product_name,
                    "brand_name": "",
                    "time_start": time_start,
                    "time_end": time_end,
                    "transcript": transcript,
                    "captions": seg_captions,
                    "confidence": 0.6,
                    "source": "gpt_detection",
                })

        return segments

    except Exception as e:
        logger.error(f"[ai-clip-v3 {job_id}] GPT product detection failed: {e}")
        return []


# ─── V3.0 Highlight Extraction ───────────────────────────────────────────────

async def _v3_extract_highlights(transcript: str, product_name: str, job_id: str) -> list:
    """
    GPT-4oで产品紹介テキストから卖点（セールスポイント）を提炼する。
    各卖点にはテキストと推定表示タイミング（相対秒数）を含む。
    
    Returns: [{text: "500ml大容量", order: 1}, ...]
    """
    import openai

    azure_key = os.getenv("AZURE_OPENAI_KEY", "")
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    azure_model = os.getenv("GPT5_MODEL") or os.getenv("GPT5_DEPLOYMENT") or "gpt-4.1-mini"

    if not azure_key or not azure_endpoint:
        return _v3_extract_highlights_local(transcript, product_name)

    from urllib.parse import urlparse as _urlparse
    _parsed = _urlparse(azure_endpoint)
    clean_endpoint = f"{_parsed.scheme}://{_parsed.netloc}/"

    client = openai.AsyncAzureOpenAI(
        api_key=azure_key,
        azure_endpoint=clean_endpoint,
        api_version=os.getenv("GPT5_API_VERSION", "2025-04-01-preview"),
    )

    prompt = f"""あなたはライブコマースの卖点コピーライターです。
以下は「{product_name}」の商品紹介の音声書き起こしです。

この紹介から、視聴者に最もアピールする卖点（セールスポイント）を2〜5個抽出してください。

卖点の例:
- ブランド名・産地（例: "韓国発コスメ"）
- 容量・サイズ（例: "500ml大容量"）
- 効果・効能（例: "24時間保湿持続"）
- 成分・技術（例: "ヒアルロン酸配合"）
- 価格・コスパ（例: "1本で3役"）
- デザイン・特徴（例: "ポンプ式で衛生的"）

ルール:
1. 各卖点は10文字以内の短いキャッチコピーにする
2. 紹介テキスト中での出現順序に基づいて並べる
3. 重複する内容は1つにまとめる
4. 最低2個、最大5個

出力形式（JSON配列）:
[
  {{"text": "卖点テキスト", "order": 1}},
  {{"text": "卖点テキスト", "order": 2}},
  ...
]

商品紹介テキスト:
{transcript[:2000]}

JSON配列のみを出力してください。"""

    try:
        response = await client.chat.completions.create(
            model=azure_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=500,
        )
        content = response.choices[0].message.content.strip()

        # Parse JSON
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
        if content.startswith("json"):
            content = content[4:].strip()

        highlights = json.loads(content)
        if not isinstance(highlights, list):
            return _v3_extract_highlights_local(transcript, product_name)

        # Validate and clean
        result = []
        for h in highlights:
            text_content = h.get("text", "").strip()
            if text_content and len(text_content) <= 15:
                result.append({"text": text_content, "order": h.get("order", len(result) + 1)})

        return result[:5] if result else _v3_extract_highlights_local(transcript, product_name)

    except Exception as e:
        logger.warning(f"[ai-clip-v3 {job_id}] GPT highlight extraction failed: {e}")
        return _v3_extract_highlights_local(transcript, product_name)


def _v3_extract_highlights_local(transcript: str, product_name: str) -> list:
    """
    ローカルフォールバック: 简单なキーワード抽出で卖点を生成。
    """
    highlights = []
    if product_name:
        highlights.append({"text": product_name[:10], "order": 1})

    patterns = [
        (r'(\d+)\s*(ml|ML|g|kg|本|個|枚)', "容量/数量"),
        (r'(保湿|美白|修復|補修|潤い|ツヤ)', "効果"),
        (r'(無添加|オーガニック|天然|植物由来)', "成分"),
        (r'(\d+)%\s*(OFF|オフ|割引)', "価格"),
    ]
    for pattern, category in patterns:
        match = re.search(pattern, transcript)
        if match:
            highlights.append({"text": match.group(0)[:10], "order": len(highlights) + 1})
            if len(highlights) >= 4:
                break

    return highlights if highlights else [{"text": product_name[:10] or "おすすめ", "order": 1}]


# ─── V3.0 Product Clip Processing ────────────────────────────────────────────

async def _v3_process_product_clip(
    job_id: str, video_path: str, segment: dict,
    req: GenerateByProductRequest, idx: int, total: int,
    video_width: int, video_height: int, video_fps: int,
    parent_tmp_dir: str,
) -> dict:
    """
    单个产品clip的处理流程:
    1. 从完整视频中切出产品时间段
    2. 检测气口/停顿（≥1秒）
    3. 生成卖点字幕overlay（视频上方）
    4. 生成普通字幕overlay（视频下方）
    5. ffmpeg合成（气口剪切 + 字幕overlay + 速度调整）
    6. 上传到blob storage
    """
    product_name = segment["product_name"]
    time_start = segment["time_start"]
    time_end = segment["time_end"]
    seg_captions = segment.get("captions", [])
    highlights = segment.get("highlights", [])
    segment_duration = time_end - time_start

    clip_tmp_dir = tempfile.mkdtemp(prefix=f"v3_clip_{idx}_", dir=parent_tmp_dir)

    # ── 1. Extract segment from full video ──
    segment_path = os.path.join(clip_tmp_dir, "segment.mp4")
    extract_cmd = [
        "ffmpeg", "-y", "-ss", str(time_start), "-to", str(time_end),
        "-i", video_path, "-c", "copy", "-avoid_negative_ts", "make_zero",
        segment_path
    ]
    proc = subprocess.run(extract_cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0 or not os.path.exists(segment_path):
        # Fallback: re-encode if copy fails
        extract_cmd = [
            "ffmpeg", "-y", "-ss", str(time_start), "-to", str(time_end),
            "-i", video_path, "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k", segment_path
        ]
        proc = subprocess.run(extract_cmd, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            return {"product_name": product_name, "status": "failed",
                    "error": f"Segment extraction failed: {proc.stderr[:200]}"}

    # ── 2. Detect silence/pauses (>= min_silence_duration) ──
    silence_periods = []
    if req.enable_silence_cut:
        silence_periods = _detect_silence_periods(
            segment_path, noise_db=req.silence_threshold_db,
            min_duration=req.min_silence_duration
        )
        logger.info(f"[ai-clip-v3 {job_id}] Product '{product_name}': {len(silence_periods)} silence periods detected")

    # ── 3. Calculate keep_segments ──
    seg_probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", segment_path],
        capture_output=True, text=True, timeout=15
    )
    seg_duration = segment_duration
    try:
        seg_probe_data = json.loads(seg_probe.stdout)
        if "format" in seg_probe_data and "duration" in seg_probe_data["format"]:
            seg_duration = float(seg_probe_data["format"]["duration"])
    except Exception:
        pass

    keep_segments = [(0, seg_duration)]
    if silence_periods:
        cuttable = [(s, e) for s, e in silence_periods if (e - s) >= req.min_silence_duration]
        if cuttable:
            cuttable.sort(key=lambda x: x[0])
            merged_cuts = []
            for cs, ce in cuttable:
                if merged_cuts and cs <= merged_cuts[-1][1]:
                    merged_cuts[-1] = (merged_cuts[-1][0], max(merged_cuts[-1][1], ce))
                else:
                    merged_cuts.append((cs, ce))

            keep_segments = []
            prev_end = 0
            for cut_start, cut_end in merged_cuts:
                if cut_start > prev_end:
                    keep_segments.append((prev_end, cut_start))
                prev_end = cut_end
            if prev_end < seg_duration:
                keep_segments.append((prev_end, seg_duration))

            if not keep_segments:
                keep_segments = [(0, seg_duration)]

    # ── 4. Adjust caption timestamps (relative to segment start) ──
    adjusted_captions = []
    for cap in seg_captions:
        adj_cap = dict(cap)
        adj_cap["start"] = float(cap.get("start", 0)) - time_start
        adj_cap["end"] = float(cap.get("end", 0)) - time_start
        adj_cap["start"] = max(0, adj_cap["start"])
        adj_cap["end"] = min(seg_duration, adj_cap["end"])
        if adj_cap["end"] > adj_cap["start"]:
            adjusted_captions.append(adj_cap)

    # ── 5. Generate highlight overlay images (top of video) ──
    highlight_overlays = []
    if highlights:
        highlight_overlays = _v3_generate_highlight_overlays(
            highlights, adjusted_captions, seg_duration, keep_segments,
            video_width, video_height, clip_tmp_dir,
            req.highlight_position_y, req.highlight_display_duration,
        )
        logger.info(f"[ai-clip-v3 {job_id}] Generated {len(highlight_overlays)} highlight overlays for '{product_name}'")

    # ── 6. Generate subtitle overlay images (bottom of video) ──
    font_path = _v3_get_font_path()
    subtitle_overlays = _generate_overlay_images(
        styled_captions=adjusted_captions,
        hook_text=None,
        cta_text=None,
        video_width=video_width,
        video_height=video_height,
        duration=seg_duration,
        font_path=font_path,
        tmp_dir=clip_tmp_dir,
        position_y=req.position_y,
        clip_duration=seg_duration,
        product_name=product_name,
    )

    # Combine all overlays
    all_overlays = highlight_overlays + subtitle_overlays

    # ── 7. Build ffmpeg command ──
    output_path = os.path.join(clip_tmp_dir, "output.mp4")

    # Create a minimal request-like object for _build_advanced_ffmpeg_command compatibility
    class _V3ReqCompat:
        def __init__(self, r):
            self.enable_silence_cut = r.enable_silence_cut
            self.enable_zoom_pulse = False
            self.enable_progress_bar = False
            self.enable_flash_intro = False
            self.enable_loop_fade = False
            self.enable_cta = False
            self.speed_factor = r.speed_factor
            self.zoom_intensity = 1.0
            self.silence_threshold_db = r.silence_threshold_db

    v3_compat_req = _V3ReqCompat(req)

    # ── V2.29: Pre-splice silence trim for V3 (same concat-based approach) ──
    v3_video_path = segment_path
    v3_duration = seg_duration
    if len(keep_segments) > 1:
        spliced_path, spliced_duration = await _pre_splice_segments(
            segment_path, keep_segments, clip_tmp_dir, video_fps
        )
        if spliced_path and os.path.exists(spliced_path):
            # Remap overlays to new timeline
            all_overlays = _remap_overlay_images_for_silence_trim(all_overlays, keep_segments)
            v3_video_path = spliced_path
            v3_duration = spliced_duration
            keep_segments = [(0, spliced_duration)]
            logger.info(f"[ai-clip-v3 {job_id}] V2.29: Pre-spliced '{product_name}' "
                        f"(duration={spliced_duration:.2f}s)")
        else:
            logger.warning(f"[ai-clip-v3 {job_id}] V2.29: Pre-splice failed, using select filter fallback")

    ffmpeg_cmd = _build_advanced_ffmpeg_command(
        video_path=v3_video_path,
        ass_path="",
        output_path=output_path,
        video_width=video_width,
        video_height=video_height,
        duration=v3_duration,
        req=v3_compat_req,
        zoom_keyframes=[],
        keep_segments=keep_segments,
        enable_progress_bar=False,
        enable_flash_intro=False,
        enable_loop_fade=False,
        overlay_images=all_overlays,
        video_fps=video_fps,
    )

    # ── 8. Execute ffmpeg ──
    logger.info(f"[ai-clip-v3 {job_id}] Running ffmpeg for '{product_name}' "
                f"(keep_segments={len(keep_segments)}, overlays={len(all_overlays)})")
    try:
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
        if proc.returncode != 0:
            stderr_text = stderr.decode()[-500:] if stderr else "unknown error"
            logger.error(f"[ai-clip-v3 {job_id}] ffmpeg failed for '{product_name}': {stderr_text}")
            return {"product_name": product_name, "status": "failed",
                    "error": f"ffmpeg error: {stderr_text[:200]}"}
    except asyncio.TimeoutError:
        return {"product_name": product_name, "status": "failed", "error": "ffmpeg timeout (>600s)"}

    if not os.path.exists(output_path) or os.path.getsize(output_path) < 1000:
        return {"product_name": product_name, "status": "failed", "error": "Output file too small or missing"}

    # ── 9. Upload to blob storage ──
    clip_id = str(uuid.uuid4())
    try:
        download_url, blob_url = await _upload_to_blob(output_path, clip_id, job_id)
    except Exception as e:
        return {"product_name": product_name, "status": "failed",
                "error": f"Upload failed: {str(e)[:200]}"}

    # Get output duration
    out_probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", output_path],
        capture_output=True, text=True, timeout=15
    )
    output_duration = seg_duration
    try:
        out_data = json.loads(out_probe.stdout)
        if "format" in out_data and "duration" in out_data["format"]:
            output_duration = float(out_data["format"]["duration"])
    except Exception:
        pass

    # ── 10. Save export record ──
    await _save_export_record(clip_id, blob_url, None, output_duration)

    return {
        "clip_id": clip_id,
        "product_name": product_name,
        "brand_name": segment.get("brand_name", ""),
        "status": "done",
        "download_url": download_url,
        "blob_url": blob_url,
        "duration_sec": round(output_duration, 1),
        "original_duration_sec": round(segment_duration, 1),
        "time_range": f"{time_start:.1f}s - {time_end:.1f}s",
        "highlights": [h.get("text", "") for h in highlights],
        "silence_cuts": len(silence_periods),
        "source": segment.get("source", "unknown"),
    }


# ─── V3.0 Highlight Overlay Generation ───────────────────────────────────────

def _v3_generate_highlight_overlays(
    highlights: list, captions: list, duration: float, keep_segments: list,
    video_width: int, video_height: int, tmp_dir: str,
    position_y_pct: float = 12.0, display_duration: float = 4.0,
) -> list:
    """
    卖点字幕overlay画像を生成する。
    视频上方に配置、各卖点は順番に表示される（逐条出现）。
    
    Returns: list of (png_path, start_time, end_time)
    """
    from PIL import Image, ImageDraw, ImageFont

    if not highlights:
        return []

    overlays = []
    font_path = _v3_get_font_path()

    # Calculate effective duration after silence cuts
    effective_duration = sum(e - s for s, e in keep_segments) if keep_segments else duration

    # Distribute highlights evenly across the effective duration
    num_highlights = len(highlights)
    interval = effective_duration / (num_highlights + 1)

    for i, h in enumerate(highlights):
        h_text = h.get("text", "").strip()
        if not h_text:
            continue

        # Calculate display timing in compressed timeline
        start_time = interval * (i + 1) - display_duration / 2
        start_time = max(0.5, start_time)
        end_time = start_time + display_duration

        # Ensure no overlap with next highlight
        if i < num_highlights - 1:
            next_start = interval * (i + 2) - display_duration / 2
            if end_time > next_start - 0.3:
                end_time = next_start - 0.3

        # Generate overlay image
        base_width = 1080
        scale = min(video_width, video_height) / base_width
        font_size = max(48, int(64 * scale))
        padding_x = int(30 * scale)
        padding_y = int(15 * scale)

        try:
            font = ImageFont.truetype(font_path, font_size)
        except Exception:
            font = ImageFont.load_default()

        # Calculate text size
        dummy_img = Image.new("RGBA", (1, 1))
        dummy_draw = ImageDraw.Draw(dummy_img)
        bbox = dummy_draw.textbbox((0, 0), h_text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        # Create full-frame canvas
        img_width = text_width + padding_x * 2
        canvas_width = video_width
        canvas_height = video_height
        img = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Background rectangle (semi-transparent dark)
        y_pos = int(canvas_height * position_y_pct / 100)
        x_pos = (canvas_width - img_width) // 2
        img_height = text_height + padding_y * 2
        bg_rect = [x_pos, y_pos, x_pos + img_width, y_pos + img_height]
        draw.rounded_rectangle(bg_rect, radius=int(10 * scale), fill=(0, 0, 0, 180))

        # Text with outline
        text_x = x_pos + padding_x
        text_y = y_pos + padding_y

        # Draw outline (black)
        outline_width = max(2, int(3 * scale))
        for ox in range(-outline_width, outline_width + 1):
            for oy in range(-outline_width, outline_width + 1):
                if ox * ox + oy * oy <= outline_width * outline_width:
                    draw.text((text_x + ox, text_y + oy), h_text, font=font, fill=(0, 0, 0, 255))

        # Draw text (yellow/gold)
        draw.text((text_x, text_y), h_text, font=font, fill=(255, 215, 0, 255))

        # Save
        overlay_path = os.path.join(tmp_dir, f"highlight_{i}.png")
        img.save(overlay_path, "PNG")
        overlays.append((overlay_path, start_time, end_time))

    return overlays


def _v3_get_font_path() -> str:
    """Get the best available font path for CJK text rendering."""
    font_candidates = [
        f'{_EXTRACTED_FONTS_DIR}/NotoSansCJK-JP-Bold.otf',
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
        '/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc',
        '/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc',
        '/usr/share/fonts/truetype/fonts-japanese-gothic.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    ]
    for fp in font_candidates:
        if os.path.exists(fp):
            return fp
    import glob
    ttf_files = glob.glob("/usr/share/fonts/**/*.ttf", recursive=True)
    ttc_files = glob.glob("/usr/share/fonts/**/*.ttc", recursive=True)
    all_fonts = ttc_files + ttf_files
    if all_fonts:
        for f in all_fonts:
            if "CJK" in f or "japanese" in f.lower() or "noto" in f.lower():
                return f
        return all_fonts[0]
    return ""


# ─── V2.35 Debug: Test translation pipeline ─────────────────────────────────
@router.post("/debug/test-translation")
async def debug_test_translation(
    x_admin_key: Optional[str] = Header(None),
):
    """Debug endpoint to test if translation pipeline works."""
    verify_admin(x_admin_key)
    import traceback
    results = {}
    
    # Test 1: Check env vars
    azure_key = os.getenv("AZURE_OPENAI_KEY", "")
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    elevenlabs_voice_id = os.getenv("ELEVENLABS_VOICE_ID", "")
    sync_api_key = os.getenv("SYNC_API_KEY", "")
    results["env_check"] = {
        "AZURE_OPENAI_KEY": bool(azure_key),
        "AZURE_OPENAI_ENDPOINT": bool(azure_endpoint),
        "ELEVENLABS_VOICE_ID": elevenlabs_voice_id[:8] + "..." if elevenlabs_voice_id else "NOT SET",
        "SYNC_API_KEY": bool(sync_api_key),
    }
    
    # Test 2: Direct GPT translation test (bypass _translate_captions_text to see raw response)
    import openai as _openai
    import re as _re
    azure_model = os.getenv("GPT5_MODEL") or os.getenv("GPT5_DEPLOYMENT") or "gpt-4.1-mini"
    results["model_info"] = {
        "GPT5_MODEL": os.getenv("GPT5_MODEL", "NOT SET"),
        "GPT5_DEPLOYMENT": os.getenv("GPT5_DEPLOYMENT", "NOT SET"),
        "effective_model": azure_model,
    }
    try:
        from urllib.parse import urlparse as _up
        _p = _up(azure_endpoint)
        clean_ep = f"{_p.scheme}://{_p.netloc}/"
        _client = _openai.AsyncAzureOpenAI(
            api_key=azure_key,
            azure_endpoint=clean_ep,
            api_version=os.getenv("GPT5_API_VERSION", "2025-04-01-preview"),
        )
        prompt = """Translate the following numbered lines from Japanese to natural, spoken 中文（简体）.
These are live stream subtitles from a beauty product presenter.
RULES:
- Output ONLY the translated lines, one per line, with the same numbering
- Keep the same number of lines as input
- Maintain conversational tone and enthusiasm
- Preserve product names (KYOGOKU, 京極) as-is
- Keep similar character length per line (for subtitle timing)
- Do NOT add explanations or formatting
Input:
1. カラーケアシャンプー
2. ね、やっぱそうだね"""
        gpt_resp = await _client.chat.completions.create(
            model=azure_model,
            messages=[
                {"role": "system", "content": "You are a subtitle translator for live commerce content. Output only numbered translated lines."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            **({
                "max_completion_tokens": 200
            } if "gpt-5" in azure_model or "o1" in azure_model or "o3" in azure_model or "o4" in azure_model else {
                "max_tokens": 200
            }),
        )
        raw_text = gpt_resp.choices[0].message.content.strip()
        # Parse
        parsed_lines = []
        for line in raw_text.split("\n"):
            line = line.strip()
            if line:
                cleaned = _re.sub(r'^\d+\.\s*', '', line)
                if cleaned:
                    parsed_lines.append(cleaned)
        results["caption_translation"] = {
            "status": "success",
            "raw_gpt_response": raw_text,
            "parsed_lines": parsed_lines,
            "model_used": azure_model,
        }
    except Exception as e:
        results["caption_translation"] = {
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc()[-500:],
        }
    
    # Test 3: Try ElevenLabs TTS
    try:
        from app.services.elevenlabs_tts_service import ElevenLabsTTSService
        tts = ElevenLabsTTSService()
        effective_voice_id = elevenlabs_voice_id
        if effective_voice_id:
            audio_bytes = await tts.text_to_speech(
                text="你好，这是测试",
                voice_id=effective_voice_id,
                language_code="zh",
                output_format="mp3_44100_128",
            )
            results["tts_test"] = {
                "status": "success",
                "audio_bytes": len(audio_bytes),
                "voice_id": effective_voice_id[:8] + "...",
            }
        else:
            results["tts_test"] = {"status": "skipped", "reason": "No ELEVENLABS_VOICE_ID set"}
    except Exception as e:
        results["tts_test"] = {
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc()[-500:],
        }
    
    # Test 4: Check Sync.so
    try:
        from app.services.sync_lip_sync_service import SyncLipSyncService
        sync = SyncLipSyncService()
        health = await sync.health_check()
        results["sync_so_health"] = health
    except Exception as e:
        results["sync_so_health"] = {"status": "error", "error": str(e)}
    
    return results
