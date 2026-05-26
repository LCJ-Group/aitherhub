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

# ─── Clip Feedback Table (AI Learning) ────────────────────────────────────────
_FEEDBACK_TABLE_ENSURED = False

async def _ensure_feedback_table():
    """Create clip_feedback table for AI learning from deletions (idempotent)"""
    global _FEEDBACK_TABLE_ENSURED
    if _FEEDBACK_TABLE_ENSURED:
        return
    try:
        async with engine.begin() as conn:
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS clip_feedback (
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
                CREATE INDEX IF NOT EXISTS ix_clip_feedback_job
                ON clip_feedback (job_id)
            """))
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_clip_feedback_category
                ON clip_feedback (reason_category)
            """))
        _FEEDBACK_TABLE_ENSURED = True
        logger.info("[ai-clip] clip_feedback table ensured")
    except Exception as e:
        logger.warning(f"[ai-clip] Failed to ensure clip_feedback table: {e}")

# Concurrency limiter
_AI_CLIP_SEMAPHORE = asyncio.Semaphore(2)  # 2並列まで許可

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
    'intro': 'pop',
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


# ─── V2: Smart Silence Trimming ──────────────────────────────────────────────
def _build_silence_trim_segments(duration: float, silence_periods: list,
                                  captions: list, keep_margin: float = 0.12) -> list:
    """無音区間をカットするためのセグメントリストを構築（V2.1改善版）。
    - 字幕がある区間は絶対にカットしない
    - 1.5秒以上の無音は積極的にカット（自然なポーズ0.3秒残す）
    - 0.6〜1.5秒の無音は部分カット（40%除去）
    - 最大カット量は元の動画の30%まで（テンポ良くしつつ不自然にならない）
    Returns list of (start, end) tuples representing segments to KEEP.
    """
    if not silence_periods:
        return [(0, duration)]

    # Build "protected" intervals from captions (don't cut these)
    protected = []
    for cap in (captions or []):
        s = float(cap.get("start", 0))
        e = float(cap.get("end", 0))
        if e > s:
            protected.append((max(0, s - 0.15), min(duration, e + 0.15)))

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
    # Sort by length (longest first) to prioritize removing the most impactful silences
    cuttable = []
    total_cut_time = 0.0
    max_cut_ratio = 0.30  # 最大30%カット
    max_cut_time = duration * max_cut_ratio

    for s_start, s_end in sorted(silence_periods, key=lambda x: x[1]-x[0], reverse=True):
        silence_len = s_end - s_start
        # Don't cut first 0.3s or last 0.3s of video
        if s_start < 0.3 or s_end > duration - 0.3:
            continue
        # Don't cut if overlaps with caption
        if _is_protected(s_start, s_end):
            continue
        # Skip very short silences (natural breathing pauses)
        if silence_len < 0.6:
            continue
        # Check max cut limit
        if total_cut_time >= max_cut_time:
            break

        if silence_len >= 1.5:
            # Long silence: aggressive cut, keep only 0.3s natural pause
            cut_start = s_start + 0.15
            cut_end = s_end - 0.15
        elif silence_len >= 0.6:
            # Medium silence: partial cut (remove 40% from center)
            mid = (s_start + s_end) / 2
            half_cut = silence_len * 0.4 / 2
            cut_start = mid - half_cut
            cut_end = mid + half_cut
        else:
            continue

        if cut_end > cut_start:
            actual_cut = cut_end - cut_start
            if total_cut_time + actual_cut > max_cut_time:
                # Trim to fit within budget
                cut_end = cut_start + (max_cut_time - total_cut_time)
                if cut_end <= cut_start:
                    break
            cuttable.append((cut_start, cut_end))
            total_cut_time += (cut_end - cut_start)

    if not cuttable:
        return [(0, duration)]

    # Build keep segments (inverse of cuttable, sorted by time)
    cuttable.sort(key=lambda x: x[0])
    keep_segments = []
    prev_end = 0
    for cut_start, cut_end in cuttable:
        if cut_start > prev_end:
            keep_segments.append((prev_end, cut_start))
        prev_end = cut_end
    if prev_end < duration:
        keep_segments.append((prev_end, duration))

    total_kept = sum(e - s for s, e in keep_segments)
    logger.info(f"[ai-clip] Silence trim V2.1: {len(cuttable)} cuts, "
                f"{total_cut_time:.1f}s removed ({total_cut_time/duration*100:.0f}%), "
                f"{total_kept:.1f}s kept, {len(keep_segments)} segments")
    return keep_segments


# ─── V2: Zoom Pulse Keyframes ────────────────────────────────────────────────
def _generate_zoom_keyframes(duration: float, volume_peaks: list,
                              captions: list, max_zoom: float = 1.08) -> list:
    """音量ピークとキャプションの強調ポイントでズームキーフレームを生成。
    Returns list of (time, zoom_factor) tuples.
    """
    keyframes = []

    # Add zoom at volume peaks (sudden loud moments)
    for peak_time in volume_peaks:
        if 0.5 < peak_time < duration - 1.0:
            keyframes.append((peak_time, max_zoom))

    # Add zoom at emphasis keywords in captions
    for cap in (captions or []):
        cap_text = cap.get("text", "").lower()
        cap_start = float(cap.get("start", 0))
        if any(kw in cap_text for kw in _EMPHASIS_KEYWORDS):
            if 0.5 < cap_start < duration - 1.0:
                keyframes.append((cap_start, max_zoom * 0.95))

    # Deduplicate (keep only one zoom per 2-second window)
    keyframes.sort(key=lambda x: x[0])
    filtered = []
    for kf in keyframes:
        if not filtered or kf[0] - filtered[-1][0] > 2.0:
            filtered.append(kf)

    # Limit to max 8 zooms per clip (too many = distracting)
    if len(filtered) > 8:
        filtered = sorted(filtered, key=lambda x: x[1], reverse=True)[:8]
        filtered.sort(key=lambda x: x[0])

    logger.info(f"[ai-clip] Generated {len(filtered)} zoom keyframes")
    return filtered


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
) -> list:
    """Pillowで全overlay画像（フック・字幕・CTA）を生成する。
    
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
    KEYWORD_HIGHLIGHT_COLOR = (255, 215, 0, 255)  # Gold for keywords
    KEYWORD_CTA_COLOR = (0, 255, 128, 255)  # Green for CTA
    KEYWORD_NUMBER_COLOR = (255, 140, 0, 255)  # Orange for numbers/prices
    
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
    processed_captions = []
    prev_end = -1.0
    for cap in raw_captions:
        # Ensure this caption starts after previous one ended (with gap)
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
            text_color = (255, 225, 53, 255)  # Yellow
            outline_clr = (255, 107, 53, 255)  # Orange
            bg_clr = (0, 0, 0, 120)
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
    
    logger.info(f"[ai-clip] Generated {len(overlays)} overlay images "
                f"(hook={'yes' if hook_text else 'no'}, "
                f"captions={len(styled_captions or [])}, "
                f"cta={'yes' if cta_text else 'no'})")
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


# ─── V2: Build Advanced ffmpeg Filter Chain ──────────────────────────────────────────
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
    
    # ── 1. Silence trimming (select filter) ──
    use_silence_trim = len(keep_segments) > 1 and len(keep_segments) <= 20
    af_chain = None
    if use_silence_trim:
        select_parts = [f"between(t,{s:.3f},{e:.3f})" for s, e in keep_segments]
        select_expr = "+".join(select_parts)
        vf_parts.append(f"select='{select_expr}'")
        vf_parts.append("setpts=N/FRAME_RATE/TB")
        af_chain = f"aselect='{select_expr}',asetpts=N/SR/TB"

    # ── 2. Zoom Pulse via crop+scale ──
    if zoom_keyframes:
        parts = []
        for t, zf in zoom_keyframes:
            freq = math.pi / 0.4
            parts.append(
                f"if(between(t,{t:.2f},{t+0.4:.2f}),"
                f"{zf:.3f}*sin((t-{t:.2f})*{freq:.4f})+"
                f"(1-sin((t-{t:.2f})*{freq:.4f})),"
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

    # ── 6. Loop fade (last 1.5s fade to black) ──
    if enable_loop_fade and duration > 5:
        fade_start = max(0, duration - 1.5)
        vf_parts.append(f"fade=t=out:st={fade_start:.2f}:d=1.5")

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
            enable_expr = f"between(t\\,{start_t:.3f}\\,{end_t:.3f})"
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
            *input_args,
            "-filter_complex", fc_str,
            "-map", "[vout]",
            "-map", "[aout]" if af_chain else "0:a",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "23",
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
            *input_args,
            "-filter_complex", fc,
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            "-vsync", "vfr",
            output_path,
        ]
    else:
        # No overlays, no audio filter - simple -vf mode
        vf_str = ",".join(vf_parts) if vf_parts else "null"
        cmd = [
            "ffmpeg", "-y",
            *input_args,
            "-vf", vf_str,
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "23",
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
    """最後の3秒に表示するCTAテキストを生成"""
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
- TikTokの購入導線を意識する（左下のカートボタン、プロフィールリンク）
- 具体的なアクションを促す（例: 「左下タップで購入」「プロフからチェック」「カートに追加してね」）
- 絵文字は使わない（フォント非対応のため）
- 商品名: {product_name}
- 動画内容: {transcript}

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

    # Fallback CTAs (TikTok購入導線に最適化、絵文字なし)
    ctas = [
        "左下タップで購入できます",
        "プロフィールから購入",
        "カートに追加してね",
        "左下から今すぐゲット",
        "いいね&保存してね",
        "フォローで最新情報",
        "コメントで質問OK",
        "プロフからチェック",
    ]
    return random.choice(ctas)


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
    MIN_DISPLAY = 2.5
    for i, cap in enumerate(styled_captions):
        cap_start = float(cap.get("start", 0))
        cap_end = float(cap.get("end", 0))
        cap_text = _strip_emoji(cap.get("text", "").strip())
        style = cap.get("style", "box")
        if not cap_text:
            continue
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
    enable_zoom_pulse: bool = Field(True, description="ズームパルスを有効にするか")
    enable_progress_bar: bool = Field(True, description="進行バーを表示するか")
    enable_flash_intro: bool = Field(True, description="最初0.5秒のフラッシュ演出")
    enable_loop_fade: bool = Field(True, description="ループ感フェードアウト")
    enable_cta: bool = Field(True, description="最後3秒にCTAテキストを表示")
    enable_keyword_highlight: bool = Field(True, description="キーワードハイライト")
    enable_subtitle_animation: bool = Field(True, description="字幕出現アニメーション")
    zoom_intensity: float = Field(1.08, ge=1.0, le=1.3, description="ズーム倍率 (1.0=なし, 1.3=最大)")
    silence_threshold_db: float = Field(-30.0, ge=-60.0, le=-10.0, description="無音検出閾値(dB)")


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
    enable_zoom_pulse: bool = Field(True, description="ズームパルスを有効にするか")
    enable_progress_bar: bool = Field(True, description="進行バーを表示するか")
    enable_flash_intro: bool = Field(True, description="最初0.5秒のフラッシュ演出")
    enable_loop_fade: bool = Field(True, description="ループ感フェードアウト")
    enable_cta: bool = Field(True, description="最後3秒にCTAテキストを表示")
    enable_keyword_highlight: bool = Field(True, description="キーワードハイライト")
    enable_subtitle_animation: bool = Field(True, description="字幕出現アニメーション")
    zoom_intensity: float = Field(1.08, ge=1.0, le=1.3, description="ズーム倍率")
    silence_threshold_db: float = Field(-30.0, ge=-60.0, le=-10.0, description="無音検出閾値(dB)")
    # V3: 映像モード選択
    video_mode: str = Field("original", description="映像モード: original=そのまま, product_overlay=商品画像オーバーレイ, audio_only=音声+商品スライドショー")
    product_image_urls: Optional[list] = Field(None, description="商品画像URLリスト（video_mode=product_overlay/audio_onlyの場合に使用）")


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
        "version": "2.17",
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
            product_name=source_clip.get('product_name', '') if source_clip else '',
        )
        logger.info(f"[ai-clip regen] Generated {len(overlay_images)} overlay images")
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
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                output_path,
            ]
        else:
            # No overlays - just copy
            ffmpeg_cmd = [
                "ffmpeg", "-y", "-i", video_path,
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
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
    PARALLEL_BATCH = 2  # 2クリップずつ並列処理
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
        # V9: Exclude clips with empty/too-short transcript (likely unusable)
        "COALESCE(LENGTH(vc.transcript_text), 0) >= 10",
        # V9: Minimum duration filter (too short clips are rarely useful)
        "COALESCE(vc.duration_sec, 0) >= 8",
    ]
    # V9: Fetch more candidates (5x) to allow quality filtering to work effectively
    params: dict = {"limit": req.max_clips * 5}

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

    # ── Penalty: Filler/chat content ──
    filler_patterns = [
        "こんにちは", "おはよう", "ありがとう", "お疲れ", "バイバイ",
        "聞こえますか", "見えてますか", "コメント読み", "ちょっと待って",
        "大家好", "謝謝", "掰掰", "等一下", "聽得到嗎",
        "hello", "thank you", "bye",
    ]
    filler_count = sum(1 for p in filler_patterns if p in transcript.lower())
    if filler_count >= 3:
        score -= 10  # 挨拶/雑談が多い
    elif filler_count >= 2:
        score -= 5

    return max(0.0, min(100.0, score))


async def _validate_and_filter_candidates(
    candidates: list, req: GenerateRequest, job_id: str
) -> list:
    """
    V9品質フィルター: 候補クリップを品質スコアでフィルタリング＋GPT検証。
    低品質クリップを除外し、高品質なもののみ返す。
    """
    if not candidates:
        return []

    MIN_QUALITY_SCORE = 35  # この閾値以下は自動除外
    GPT_VALIDATE_THRESHOLD = 55  # この閾値以下はGPT検証を実施

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
    if borderline and len(borderline) <= 5:
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
    V9生成後品質チェック: 生成されたクリップの最終品質を検証。
    - 出力動画の尺が短すぎないか
    - 字幕数が適切か
    - フックテキストが生成されているか
    """
    if result.get("status") != "done":
        return result

    quality_flags = []
    duration = result.get("duration_sec", 0)
    captions_count = result.get("captions_count", 0)
    hook_text = result.get("hook_text", "")

    # 尺チェック
    if duration < 5:
        quality_flags.append("too_short")
    # 字幕チェック
    if captions_count == 0 and duration > 10:
        quality_flags.append("no_captions")
    # フックチェック
    if not hook_text:
        quality_flags.append("no_hook")

    if quality_flags:
        result["quality_warnings"] = quality_flags
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

        await _update_job(job_id, progress_pct=10, current_step=f"クリップ {idx+1}/{total}: 動画情報取得中...")

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
                video_path, noise_db=req.silence_threshold_db
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
        # Always split long segments to ensure one-line-per-subtitle
        if captions:
            captions = _split_long_segments(captions)

        # ── 4b. Build silence trim segments (after captions, to protect caption regions) ──
        if req.enable_silence_cut and silence_periods:
            keep_segments = _build_silence_trim_segments(duration, silence_periods, captions)

        # ── 5. Generate zoom keyframes ──
        zoom_keyframes = []
        if req.enable_zoom_pulse and (volume_peaks or captions):
            zoom_keyframes = _generate_zoom_keyframes(
                duration, volume_peaks, captions, max_zoom=req.zoom_intensity
            )

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
        )
        logger.info(f"[ai-clip {job_id}] Generated {len(overlay_images)} overlay images "
                    f"(clip_duration={clip_duration:.1f}s)")

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

        elif video_mode == "product_overlay" and product_imgs:
            # PiPモード: 動画メイン+商品画像ポップアップ
            await _update_job(job_id, progress_pct=54, current_step=f"クリップ {idx+1}/{total}: PiP合成中（商品ポップアップ）...")
            pip_path = await _generate_pip_video(
                video_path, product_imgs, duration, video_width, video_height, tmp_dir, job_id
            )
            if pip_path and os.path.exists(pip_path):
                video_path = pip_path
                logger.info(f"[ai-clip {job_id}] Using PiP composite as video source")

        await _update_job(job_id, progress_pct=58, current_step=f"クリップ {idx+1}/{total}: エンコード準備中...")
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


def _split_long_segments(segments: list, max_chars: int = 18, max_duration: float = 3.5) -> list:
    """Whisperセグメントを一文ずつに分割する（字幕多行表示問題の根本修正）。
    
    Whisperは1セグメントに複数文を含むことがある（5-15秒、30-100文字）。
    これをそのまま字幕にすると画面に複数行が同時表示される。
    
    分割ルール:
    1. 句読点（。、！、？、…）で分割
    2. max_chars以上のテキストは助詞・接続詞の後で分割
    3. 分割後の各パートに元セグメントの時間を文字数比例で配分
    4. 短いセグメント（max_chars以下 かつ max_duration以下）はそのまま
    """
    import re as _re
    
    # 助詞・接続詞の後で分割するためのパターン
    _PARTICLE_SPLIT_PATTERN = _re.compile(
        r'(?<=[はがをにでもとのへよねけどして])'
    )
    
    def _split_at_particles(text: str, target_len: int) -> list:
        """max_chars以上のテキストを助詞の後で分割"""
        if len(text) <= target_len:
            return [text]
        
        # Find all possible split positions (after particles)
        positions = [m.start() for m in _PARTICLE_SPLIT_PATTERN.finditer(text)]
        if not positions:
            # No particles found - force split at target_len
            chunks = []
            for i in range(0, len(text), target_len):
                chunk = text[i:i+target_len]
                if chunk:
                    chunks.append(chunk)
            return chunks
        
        # Greedy split: accumulate chars, split at the last particle position before exceeding target_len
        chunks = []
        last_cut = 0
        for pos in positions:
            segment_so_far = text[last_cut:pos]
            if len(segment_so_far) >= target_len:
                # Cut here
                chunks.append(segment_so_far)
                last_cut = pos
        # Remaining text
        remaining = text[last_cut:]
        if remaining:
            if chunks and len(remaining) < 6:  # Too short, merge with previous
                chunks[-1] += remaining
            else:
                chunks.append(remaining)
        
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
        
        # Step 3: Split remaining long parts at particle boundaries
        final_parts = []
        for part in refined_parts:
            if len(part) > max_chars:
                chunks = _split_at_particles(part, max_chars)
                final_parts.extend(chunks)
            else:
                final_parts.append(part)
        
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
        # Split long segments into sentence-level captions (fixes multi-line subtitle issue)
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

        prompt = f"""TikTok/Reelsの最初3秒で視聴者のスクロールを止めるフックテキストを生成。

【絶対ルール】
- 最大10文字以内（厳守！それ以上は絶対ダメ）
- 絵文字は使わない
- 短くインパクトのある表現のみ
- 「。」「、」は使わない

【パターン例】
- 「衝撃の結果」「知らないと損」「絶対やめて」
- 「プロが愚いた」「これマジで凄い」「差がやばい」
- 「まだやってる？」「誰も教えない」「見ないと後悔」

商品名: {product_name or '（なし）'}
動画内容: {transcript[:200]}

フックテキストのみ出力（10文字以内、説明不要）:"""

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
            return result

    except Exception as e:
        logger.warning(f"[ai-clip] Hook generation via GPT failed: {e}")

    return _generate_simple_hook(product_name, transcript)


def _generate_simple_hook(product_name: str, transcript: str) -> str:
    """GPTが使えない場合のフォールバックフック生成。多様なパターンでランダムに選択。"""
    # 商品名がある場合は商品名を含むフック
    if product_name:
        product_hooks = [
            f"知らないと損！{product_name}",
            f"プロが選ぶ{product_name}",
            f"衝撃の{product_name}",
            f"なぜ{product_name}が人気？",
            f"{product_name}の真実",
            f"これが{product_name}の力",
            f"まだ{product_name}使ってない？",
            f"プロが愛用する理由",
            f"驚きの変化を見て",
        ]
        return random.choice(product_hooks)[:15]

    # 汎用的なバズるフック（美容に限定しない）
    generic_hooks = [
        # 疑問文系
        "これ知ってた？",
        "なぜ誰も教えないの？",
        "これマジですごい",
        "分かる人いる？",
        "まだ知らないの？",
        # 衝撃・数字系
        "99%が知らない事実",
        "たった3秒で分かる",
        "これ見たら変わる",
        "衝撃の結果がこちら",
        "プロも驚いた方法",
        # 禁止・警告系
        "絶対にやめて！",
        "これだけはやめて",
        "知らないと損する",
        "今すぐ確認して",
        # 秘密・裏技系
        "プロの裏技公開",
        "誰も教えない秘密",
        "業界の裏側見せます",
        "こっそり教えます",
        # 対比・ギャップ系
        "プロ vs 素人の差",
        "高いものと安いものの差",
        "ビフォーアフター",
        "差がやばすぎる",
        # 共感・あるある系
        "これ分かる人いる？",
        "みんなやってない？",
        "それ間違ってます",
        # 紧急・限定系
        "今だけのチャンス",
        "見ないと後悔する",
        "最後まで見て",
    ]
    return random.choice(generic_hooks)[:15]


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
    """PiP (Picture-in-Picture) 合成: 動画がフルスクリーンメイン、商品画像が中央にローテーション表示。
    複数画像を順番に切り替えてポップアップ表示する（3秒表示→4秒非表示→次の画像）。
    全画像を使い切ったら最初に戻ってループする。
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

    # Create overlay images for each product (rounded corners, white card background)
    overlay_size = int(width * 0.55)
    overlay_h = int(height * 0.35)
    corner_radius = 24
    padding = 20
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

    # Calculate popup timing: show for 3s, hide for 4s, rotate through images
    show_duration = 3.0  # seconds visible per image
    hide_duration = 4.0  # seconds hidden between images
    first_appear = 2.0   # first appearance at 2 seconds
    cycle_duration = show_duration + hide_duration  # 7s per image slot

    # Build schedule: [(start_time, end_time, image_index), ...]
    schedule = []
    t = first_appear
    img_idx = 0
    while t + show_duration <= duration:
        schedule.append((t, t + show_duration, img_idx % len(overlay_paths)))
        t += cycle_duration
        img_idx += 1
    # If video is very short, show at least one image
    if not schedule and duration > 2:
        schedule.append((1.0, min(duration - 0.5, 4.0), 0))

    if not schedule:
        logger.warning(f"[ai-clip {job_id}] Video too short for PiP rotation (duration={duration})")
        return None

    logger.info(f"[ai-clip {job_id}] PiP rotation schedule: {len(schedule)} appearances, {len(overlay_paths)} unique images")

    # Position overlay in center of video
    overlay_x = (width - overlay_size) // 2
    overlay_y = (height - overlay_h) // 2

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



# ─── Product Image Upload & AI Analysis Endpoints ─────────────────────────────

@router.post("/upload-product-image")
async def upload_product_image(
    file: UploadFile = File(...),
    file_type: str = Form("product-image"),
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """商品画像をAzure Blobにアップロードする（AIクリップ生成用）"""
    verify_admin(x_admin_key)
    from app.services.storage_service import generate_upload_sas
    import httpx

    try:
        content = await file.read()
        if len(content) > 20 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="画像が大きすぎます（最大20MB）")

        file_id = f"ai-clip-product-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        filename = file.filename or f"product_{int(time.time())}.jpg"

        vid, upload_url, blob_url, expiry = await generate_upload_sas(
            email="ai-clip-generator@aitherhub.com",
            video_id=file_id,
            filename=filename,
        )

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.put(
                upload_url,
                content=content,
                headers={
                    "x-ms-blob-type": "BlockBlob",
                    "Content-Type": file.content_type or "image/jpeg",
                },
            )
            resp.raise_for_status()

        # Generate read SAS URL for the uploaded image
        from app.services.storage_service import generate_read_sas_from_url
        read_url = generate_read_sas_from_url(blob_url)
        if not read_url:
            # Fallback: return blob_url and let analyze endpoint handle it
            read_url = blob_url

        logger.info(f"[ai-clip] Product image uploaded: {filename} ({len(content)} bytes) \u2192 {blob_url}")
        return {
            "success": True,
            "blob_url": blob_url,
            "read_url": read_url,
            "file_size": len(content),
            "filename": filename,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[ai-clip] Product image upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"アップロードに失敗しました: {str(e)}")


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
    target_duration: float = Field(90.0, ge=30, le=180, description="目標クリップ長（秒）")
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
    target_duration: float = Field(90.0, ge=30, le=180, description="目標クリップ長（秒）")

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
        "current_step": "V10再生成準備中...",
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
        "message": "V10再生成を開始しました。元動画から拡張範囲で再カット＋最新AI処理を適用します。",
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
    target_dur = req.target_duration  # default 90s
    if is_full_video:
        # AI auto-determines optimal expand range to reach target duration
        original_duration = original_end - original_start
        needed_extra = max(0, target_dur - original_duration)
        # Expand more toward the end (content continuation) than the beginning
        auto_expand_before = min(original_start, needed_extra * 0.35)
        auto_expand_after = min(
            (full_video_duration - original_end) if full_video_duration > original_end else 0,
            needed_extra * 0.65
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
        # Cap at target_duration
        actual_duration = min(new_end - new_start, target_dur)
        new_end = new_start + actual_duration
    else:
        # We only have the clip segment - use full duration
        new_start = 0
        new_end = full_video_duration or (original_end - original_start)
        actual_duration = new_end - new_start
    logger.info(f"[v10-regen {job_id}] Time range: {original_start:.1f}-{original_end:.1f} → {new_start:.1f}-{new_end:.1f} (duration: {actual_duration:.1f}s, full_video={is_full_video})")
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
    # Always split long segments to ensure one-line-per-subtitle
    if captions:
        captions = _split_long_segments(captions)
    # ── Step 7: Generate hook text and CTA ──
    await _update_job(job_id, progress_pct=50, current_step="フック＆CTA生成中...")
    transcript_text = " ".join(c.get("text", "") for c in captions if c.get("text"))
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
        product_name=(clip_row["product_name"] if isinstance(clip_row, dict) else clip_row.product_name) or "",
    )
    logger.info(f"[v10-regen {job_id}] Generated {len(overlay_images)} overlay images")
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
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            output_path,
        ]
    else:
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-i", cut_video_path,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
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
    # ── Step 11: Compute new quality score ──
    new_quality_score = _compute_clip_quality_score({
        "transcript_text": transcript_text,
        "product_name": (clip_row["product_name"] if isinstance(clip_row, dict) else clip_row.product_name) or "",
        "cta_score": (clip_row["cta_score"] if isinstance(clip_row, dict) else clip_row.cta_score) or 0,
        "importance_score": (clip_row["importance_score"] if isinstance(clip_row, dict) else clip_row.importance_score) or 0,
        "duration_sec": actual_duration,
        "captions": captions,
    })
    original_quality_score = _compute_clip_quality_score({
        "transcript_text": clip_row["transcript_text"] if isinstance(clip_row, dict) else clip_row.transcript_text,
        "product_name": clip_row["product_name"] if isinstance(clip_row, dict) else clip_row.product_name,
        "cta_score": clip_row["cta_score"] if isinstance(clip_row, dict) else clip_row.cta_score,
        "importance_score": clip_row["importance_score"] if isinstance(clip_row, dict) else clip_row.importance_score,
        "duration_sec": clip_row["duration_sec"] if isinstance(clip_row, dict) else clip_row.duration_sec,
        "captions": clip_row["captions"] if isinstance(clip_row, dict) else clip_row.captions,
    })
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
    v10_version_tag = f"v10.{_date.today().strftime('%Y%m%d')}"
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
                "version_tag": v10_version_tag,
                "exported_url": blob_url,
                "duration": actual_duration,
                "quality_score": new_quality_score,
            })
            await session.commit()
        logger.info(f"[v10-regen {job_id}] Updated clip {clip_id} with version={v10_version_tag}")
    except Exception as e:
        logger.warning(f"[v10-regen {job_id}] Failed to update clip version: {e}")
    # Save final result
    await _update_job(job_id, status="done", progress_pct=100, current_step="V10再生成完了")
    job_data = _load_job(job_id)
    if not job_data:
        job_data = await _load_job_db(job_id)
    if job_data:
        job_data["results"] = [regen_result]
        job_data["status"] = "done"
        job_data["progress_pct"] = 100
        job_data["config"]["new_quality_score"] = new_quality_score
        await _save_job(job_id, job_data)
    # Cleanup
    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)
    logger.info(f"[v10-regen {job_id}] Complete! Score: {original_quality_score:.1f} → {new_quality_score:.1f}")

async def _run_batch_regeneration(batch_job_id: str, req: BatchRegenRequest):
    """V10: 一括再生成のバックグラウンド処理"""
    try:
        clip_ids = req.clip_ids
        total = len(clip_ids)
        completed = 0
        results = []
        for i, clip_id in enumerate(clip_ids):
            try:
                await _update_job(batch_job_id, progress_pct=int((i / total) * 100),
                                  current_step=f"クリップ {i+1}/{total} 再生成中...")
                # Get clip data
                async with get_session() as session:
                    result = await session.execute(text("""
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
                if not clip_row:
                    results.append({"clip_id": clip_id, "status": "error", "error": "クリップが見つかりません"})
                    continue
                # Convert Row to dict for safe access after session close
                clip_row = {
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
                    })
                    completed += 1
                else:
                    results.append({
                        "clip_id": clip_id,
                        "status": "error",
                        "error": sub_data.get("error", "Unknown error") if sub_data else "Job not found",
                    })
            except Exception as e:
                logger.error(f"[v10-batch {batch_job_id}] Clip {clip_id} failed: {e}")
                results.append({"clip_id": clip_id, "status": "error", "error": str(e)[:200]})
        # Update batch job
        await _update_job(batch_job_id, status="done", progress_pct=100,
                          current_step=f"一括再生成完了 ({completed}/{total}件成功)")
        batch_data = _load_job(batch_job_id)
        if not batch_data:
            batch_data = await _load_job_db(batch_job_id)
        if batch_data:
            batch_data["results"] = results
            batch_data["status"] = "done"
            batch_data["progress_pct"] = 100
            await _save_job(batch_job_id, batch_data)
        logger.info(f"[v10-batch {batch_job_id}] Complete: {completed}/{total} succeeded")
    except Exception as e:
        logger.error(f"[v10-batch {batch_job_id}] Fatal error: {e}", exc_info=True)
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
            "product_image_urls": r.product_image_urls if r.product_image_urls else [],
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

@router.post("/ai-clip/clips/{job_id}/{clip_id}/delete")
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
                INSERT INTO clip_feedback (job_id, clip_id, action, reason, reason_category, clip_metadata)
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


@router.get("/ai-clip/feedback")
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
            query = "SELECT * FROM clip_feedback"
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
                    "product_image_urls": r.product_image_urls if r.product_image_urls else [],
                    "keywords": list(r.keywords) if r.keywords else [],
                }
                for r in rows
            ]
        }
    except Exception as e:
        logger.error(f"[ai-clip] Product master search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)[:200])
