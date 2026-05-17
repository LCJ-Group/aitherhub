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

from fastapi import APIRouter, HTTPException, Query, Header, BackgroundTasks
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
]

# Product/emphasis keywords
_EMPHASIS_KEYWORDS = [
    '商品', '製品', '成分', '効果', '使い方', 'すごい', 'やばい',
    '最高', 'おすすめ', '人気', '話題', 'プロ', '美容師', 'サロン',
    'KYOGOKU', '京極', 'ケラチン', 'コラーゲン', 'アミノ酸',
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
    "\U000024C2-\U0001F251"  # enclosed chars
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA00-\U0001FA6F"  # chess symbols
    "\U0001FA70-\U0001FAFF"  # symbols extended-A
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
    "\U00003297"             # circled ideograph
    "\U00003299"             # circled ideograph
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
                                  captions: list, keep_margin: float = 0.15) -> list:
    """無音区間をカットするためのセグメントリストを構築。
    字幕がある区間は絶対にカットしない。
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
            protected.append((max(0, s - 0.2), min(duration, e + 0.2)))

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
    cuttable = []
    for s_start, s_end in silence_periods:
        # Don't cut first 0.5s or last 0.5s
        if s_start < 0.5 or s_end > duration - 0.5:
            continue
        # Don't cut if overlaps with caption
        if _is_protected(s_start, s_end):
            continue
        # Only cut silences longer than 0.8s (keep natural pauses)
        if s_end - s_start < 0.8:
            continue
        # Keep a small margin at start and end of silence
        cut_start = s_start + keep_margin
        cut_end = s_end - keep_margin
        if cut_end > cut_start:
            cuttable.append((cut_start, cut_end))

    if not cuttable:
        return [(0, duration)]

    # Build keep segments (inverse of cuttable)
    keep_segments = []
    prev_end = 0
    for cut_start, cut_end in sorted(cuttable):
        if cut_start > prev_end:
            keep_segments.append((prev_end, cut_start))
        prev_end = cut_end
    if prev_end < duration:
        keep_segments.append((prev_end, duration))

    logger.info(f"[ai-clip] Silence trim: {len(cuttable)} cuts, {len(keep_segments)} keep segments")
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


# ─── V2: Build Advanced ffmpeg Filter Chain ──────────────────────────────────
def _build_advanced_ffmpeg_command(
    video_path: str, ass_path: str, output_path: str,
    video_width: int, video_height: int, duration: float,
    req, zoom_keyframes: list, keep_segments: list,
    enable_progress_bar: bool = True,
    enable_flash_intro: bool = True,
    enable_loop_fade: bool = True,
) -> list:
    """V2: 高度なffmpegフィルタチェーンを構築する。

    フィルタ構成:
    1. 無音カット（concat demuxer or select filter）
    2. ズームパルス（zoompan or crop+scale）
    3. 最初0.5秒フラッシュ（eq brightness boost）
    4. ASS字幕焼き込み
    5. 進行バー（drawbox）
    6. ループ感フェードアウト（fade filter）
    """
    # ── Strategy: use -filter_complex with semicolons to avoid comma escaping ──
    # This is more robust than -vf because semicolons separate filter chains,
    # allowing commas inside expressions without escaping.
    
    # Build video filter chain parts (will be joined with commas)
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
    # Use simple per-keyframe approach: each zoom is a short crop pulse
    if zoom_keyframes:
        # Build a nested if() expression for zoom factor
        # Inside crop filter params, commas are parameter separators
        # so we use the single-string form: crop=w:h:x:y
        parts = []
        for t, zf in zoom_keyframes:
            # sin((t-t0)*pi/0.4) goes 0→1→0 over 0.4s window
            freq = math.pi / 0.4
            parts.append(
                f"if(between(t,{t:.2f},{t+0.4:.2f}),"
                f"{zf:.3f}*sin((t-{t:.2f})*{freq:.4f})+"
                f"(1-sin((t-{t:.2f})*{freq:.4f})),"
            )
        # Build nested expression with fallback 1.0
        zoom_expr = "1.0"
        for part in reversed(parts):
            zoom_expr = part + zoom_expr + ")"

        # Use crop=w:h:x:y positional form (no named params, no colons in values)
        # crop filter: crop=out_w:out_h:x:y
        crop_f = (
            f"crop="
            f"'iw/({zoom_expr})':"
            f"'ih/({zoom_expr})':"
            f"'(iw-iw/({zoom_expr}))/2':"
            f"'(ih-ih/({zoom_expr}))/2'"
        )
        vf_parts.append(crop_f)
        vf_parts.append(f"scale={video_width}:{video_height}")

    # ── 3. Flash intro (first 0.3s brightness boost) ──
    if enable_flash_intro:
        flash_expr = "if(lt(t,0.3),0.4*(1-t/0.3),0)"
        vf_parts.append(f"eq=brightness='{flash_expr}':eval=frame")

    # ── 4. ASS subtitles ──
    # Prefer extracted OTF fonts dir over TTC dir for libass compatibility.
    # libass 0.15.0 on Azure cannot read CJK glyphs from TTC files.
    escaped_ass = ass_path.replace(":", "\\:").replace("'", "'\\''")
    font_path = _find_cjk_font()
    # Use extracted fonts dir if available, otherwise use the font's own dir
    if os.path.isdir(_EXTRACTED_FONTS_DIR) and any(
        f.endswith('.otf') for f in os.listdir(_EXTRACTED_FONTS_DIR)
    ):
        font_dir = _EXTRACTED_FONTS_DIR
        logger.info(f"[ai-clip] Using extracted fonts dir: {font_dir}")
    else:
        font_dir = os.path.dirname(font_path)
        logger.info(f"[ai-clip] Using original fonts dir: {font_dir}")
    escaped_fontdir = font_dir.replace(":", "\\:").replace("'", "'\\''")
    # Set FONTCONFIG_PATH env var for the ffmpeg subprocess
    os.environ.setdefault("FONTCONFIG_PATH", "/etc/fonts")
    os.environ.setdefault("FONTCONFIG_FILE", "/etc/fonts/fonts.conf")
    vf_parts.append(f"ass='{escaped_ass}':fontsdir='{escaped_fontdir}'")

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

    # ── Assemble -filter_complex ──
    # Use -filter_complex with [0:v] and [0:a] labels
    # Semicolons separate video and audio chains
    video_chain = ",".join(vf_parts) if vf_parts else "null"
    
    if af_chain:
        # Both video and audio filter chains
        fc = f"[0:v]{video_chain}[vout];[0:a]{af_chain}[aout]"
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-filter_complex", fc,
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "22",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            "-vsync", "vfr",
            output_path,
        ]
    else:
        # Video filter only, pass audio through
        fc = f"[0:v]{video_chain}[vout]"
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-filter_complex", fc,
            "-map", "[vout]", "-map", "0:a",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "22",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            "-t", str(min(duration, getattr(req, 'max_duration', 60))),
            output_path,
        ]

    return cmd


# ─── V2: Enhanced ASS with Animations & Highlights ──────────────────────────
def _highlight_keywords(text_content: str, product_name: str = "") -> str:
    """キーワードをASS override tagでハイライトする"""
    result = text_content

    # Build keyword list (product name first, then CTA, then emphasis)
    highlight_map = {}
    if product_name:
        for word in product_name.split():
            if len(word) >= 2:
                highlight_map[word] = _HIGHLIGHT_COLOR

    for kw in _CTA_KEYWORDS:
        if kw in result:
            highlight_map[kw] = _HIGHLIGHT_CTA_COLOR

    for kw in _EMPHASIS_KEYWORDS:
        if kw in result:
            highlight_map[kw] = _HIGHLIGHT_COLOR

    # Apply highlights (ASS override tags)
    for keyword, color in highlight_map.items():
        if keyword in result:
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
    max_duration: float = Field(60.0, le=180.0, description="最大クリップ長（秒）")
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
        "version": "2.4",
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

    # Quick ASS render test
    ass_test_result = ""
    try:
        import tempfile
        test_ass = tempfile.NamedTemporaryFile(suffix='.ass', mode='w', delete=False)
        test_ass.write('[Script Info]\nScriptType: v4.00+\nPlayResX: 100\nPlayResY: 100\n\n')
        test_ass.write('[V4+ Styles]\n')
        test_ass.write('Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n')
        test_ass.write(f'Style: Default,{font_name},20,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,2,0,5,10,10,10,1\n\n')
        test_ass.write('[Events]\n')
        test_ass.write('Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n')
        test_ass.write('Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,\u30c6\u30b9\u30c8 Test\n')
        test_ass.close()
        
        test_out = test_ass.name.replace('.ass', '.png')
        font_dir = os.path.dirname(font_path)
        r = subprocess.run(
            ['ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=c=black:s=200x200:d=1',
             '-vf', f"ass='{test_ass.name}':fontsdir='{font_dir}'",
             '-frames:v', '1', test_out],
            capture_output=True, text=True, timeout=15
        )
        ass_test_result = f"rc={r.returncode} stderr_tail={r.stderr[-300:] if r.stderr else 'none'}"
        os.unlink(test_ass.name)
        if os.path.exists(test_out):
            os.unlink(test_out)
    except Exception as e:
        ass_test_result = f"Error: {e}"

    return {
        "font_path": font_path,
        "font_name_for_ass": font_name,
        "font_path_exists": os.path.exists(font_path),
        "noto_cjk_files": noto_fonts[:20],
        "total_font_files": len(all_fonts),
        "fc_list_japanese": fc_list_output,
        "search_paths_status": {p: os.path.exists(p) for p in _FONT_SEARCH_PATHS},
        "ffmpeg_ass_filters": ffmpeg_ass_info,
        "libass_packages": libass_info,
        "ass_render_test": ass_test_result,
    }


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
    x_admin_key: Optional[str] = Header(None),
):
    verify_admin(x_admin_key)
    # Get from DB (persistent) and merge with file-based (in-progress)
    db_jobs = await _list_jobs_db(limit=limit)
    file_jobs = _list_jobs(limit=limit)
    # Merge: DB is source of truth, but file may have more recent progress
    db_ids = {j["job_id"] for j in db_jobs}
    merged = list(db_jobs)
    for fj in file_jobs:
        if fj.get("job_id") not in db_ids:
            merged.append(fj)
    merged.sort(key=lambda j: j.get("created_at", ""), reverse=True)
    merged = merged[:limit]
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
                    "SELECT clip_url, clip_url_hd FROM video_clips WHERE id = CAST(:cid AS uuid) LIMIT 1"
                ), {"cid": clip_id})
                clip_row = row.fetchone()
                if clip_row:
                    # Prefer HD URL, fallback to regular clip_url
                    raw_url = clip_row.clip_url_hd or clip_row.clip_url
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
        # Generate ASS subtitle file with edited captions
        await _update_job(job_id, progress_pct=40, current_step="字幕ファイル生成中...")
        styled_captions = _assign_scene_styles(captions, duration, subtitle_style)
        ass_path = os.path.join(tmp_dir, "subtitles.ass")
        product_name = source_clip.get("product_name", "") if source_clip else ""
        _generate_enhanced_ass(
            styled_captions, hook_text, cta_text, ass_path,
            video_width, video_height, duration, position_y,
            product_name=product_name,
            enable_animations=enable_animations,
            enable_highlights=enable_highlights,
        )
        # Build ffmpeg command for re-encoding (subtitles only, no other effects)
        await _update_job(job_id, progress_pct=50, current_step="再エンコード中...")
        output_path = os.path.join(tmp_dir, "output.mp4")
        font_path = _find_cjk_font()
        fontsdir = os.path.dirname(font_path)
        # Simple re-encode with subtitles
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", f"subtitles={ass_path}:fontsdir={fontsdir}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
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
    await _update_job(job_id, status="selecting", progress_pct=5, current_step="候補クリップ選定中...")

    candidates = await _select_candidates(req)
    if not candidates:
        await _update_job(job_id, status="failed", error="条件に合うクリップが見つかりませんでした")
        return

    clips_total = min(len(candidates), req.max_clips)
    await _update_job(job_id, clips_total=clips_total, progress_pct=8,
                current_step=f"{clips_total}件のクリップを選定完了")

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
                return await _process_single_clip_v2(job_id, clip, req, idx, clips_total)
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
    ]
    params: dict = {"limit": req.max_clips * 3}

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
        # ── 1. Download clip ── (5%)
        await _update_job(job_id, progress_pct=5, current_step=f"クリップ {idx+1}/{total}: ダウンロード中...")
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
            resp = await client.get(download_url)
            resp.raise_for_status()
            with open(video_path, "wb") as f:
                f.write(resp.content)

        file_size = os.path.getsize(video_path)
        logger.info(f"[ai-clip {job_id}] Downloaded clip: {file_size} bytes")

        # ── 2. Get video info ──
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

        # ── 3. Audio analysis (V2: volume peaks + silence detection) ── (15%)
        volume_peaks = []
        silence_periods = []
        keep_segments = [(0, duration)]

        if req.enable_zoom_pulse:
            await _update_job(job_id, progress_pct=15, current_step=f"クリップ {idx+1}/{total}: 音声分析中（ズームポイント検出）...")
            volume_peaks = _detect_volume_peaks(video_path)

        if req.enable_silence_cut:
            await _update_job(job_id, progress_pct=20, current_step=f"クリップ {idx+1}/{total}: 無音区間検出中...")
            silence_periods = _detect_silence_periods(
                video_path, noise_db=req.silence_threshold_db
            )

        # ── 4. Transcribe ── (25%)
        if not captions:
            await _update_job(job_id, progress_pct=25, current_step=f"クリップ {idx+1}/{total}: 字幕生成中 (Whisper)...")
            captions = await _transcribe_clip(video_path, req.target_language)

        if isinstance(captions, str):
            try:
                captions = json.loads(captions)
            except Exception:
                captions = []
        if not captions:
            captions = []

        # ── 4b. Build silence trim segments (after captions, to protect caption regions) ──
        if req.enable_silence_cut and silence_periods:
            keep_segments = _build_silence_trim_segments(duration, silence_periods, captions)

        # ── 5. Generate zoom keyframes ──
        zoom_keyframes = []
        if req.enable_zoom_pulse and (volume_peaks or captions):
            zoom_keyframes = _generate_zoom_keyframes(
                duration, volume_peaks, captions, max_zoom=req.zoom_intensity
            )

        # ── 6. Hook generation ── (40%)
        hook_text = None
        if req.enable_hook:
            await _update_job(job_id, progress_pct=40, current_step=f"クリップ {idx+1}/{total}: フック生成中...")
            hook_text = await _generate_hook(captions, clip, req)

        # ── 7. CTA generation (V2) ── (45%)
        cta_text = None
        if req.enable_cta:
            await _update_job(job_id, progress_pct=45, current_step=f"クリップ {idx+1}/{total}: CTA生成中...")
            cta_text = _generate_cta_text(captions, clip)

        # ── 8. Scene classification & style assignment ── (50%)
        await _update_job(job_id, progress_pct=50, current_step=f"クリップ {idx+1}/{total}: シーン分析中...")
        styled_captions = _assign_scene_styles(captions, duration, req.subtitle_style)

        # ── 9. Generate enhanced ASS subtitle file (V2) ── (55%)
        await _update_job(job_id, progress_pct=55, current_step=f"クリップ {idx+1}/{total}: 字幕ファイル生成中...")
        ass_path = os.path.join(tmp_dir, "subtitles.ass")
        _generate_enhanced_ass(
            styled_captions, hook_text, cta_text, ass_path,
            video_width, video_height, duration, req.position_y,
            product_name=product_name,
            enable_animations=req.enable_subtitle_animation,
            enable_highlights=req.enable_keyword_highlight,
        )

        # ── 10. Build advanced ffmpeg command (V2) ── (60%)
        await _update_job(job_id, progress_pct=60, current_step=f"クリップ {idx+1}/{total}: エンコード中（V2フィルタ適用）...")
        output_path = os.path.join(tmp_dir, "output.mp4")
        ffmpeg_cmd = _build_advanced_ffmpeg_command(
            video_path, ass_path, output_path,
            video_width, video_height, duration, req,
            zoom_keyframes=zoom_keyframes,
            keep_segments=keep_segments,
            enable_progress_bar=req.enable_progress_bar,
            enable_flash_intro=req.enable_flash_intro,
            enable_loop_fade=req.enable_loop_fade,
        )

        logger.info(f"[ai-clip {job_id}] ffmpeg V2 cmd: {' '.join(ffmpeg_cmd)}")
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)

        if proc.returncode != 0:
            err_full = stderr.decode() if stderr else "Unknown error"
            logger.error(f"[ai-clip {job_id}] ffmpeg FULL stderr:\n{err_full}")
            raise RuntimeError(f"ffmpeg failed: {err_full[-800:]}")

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

        # ── 11. Enhanced thumbnail (V2) ── (85%)
        thumbnail_url = None
        if req.enable_thumbnail:
            await _update_job(job_id, progress_pct=85, current_step=f"クリップ {idx+1}/{total}: サムネイル生成中...")
            thumbnail_url = await _generate_enhanced_thumbnail(
                output_path, tmp_dir, clip_id,
                hook_text=hook_text or "", product_name=product_name,
            )

        # ── 12. Upload to Azure Blob Storage ── (90%)
        await _update_job(job_id, progress_pct=90, current_step=f"クリップ {idx+1}/{total}: アップロード中...")
        download_url, blob_url = await _upload_to_blob(output_path, clip_id, job_id)

        # ── 13. Save to DB ── (95%)
        await _update_job(job_id, progress_pct=95, current_step=f"クリップ {idx+1}/{total}: DB保存中...")
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

        logger.info(f"[ai-clip] Transcribed: {len(segments)} segments")
        return segments

    except Exception as e:
        logger.error(f"[ai-clip] Whisper failed: {e}", exc_info=True)
        return []


# ─── Hook Generation (unchanged from V1) ─────────────────────────────────────

async def _generate_hook(captions: list, clip: dict, req: GenerateRequest) -> str:
    if req.hook_text:
        return req.hook_text

    from app.services.hook_detection_service import detect_hooks, suggest_hook_placement

    if captions:
        hooks = detect_hooks(captions, max_candidates=5)
        if hooks and hooks[0].hook_score >= 50:
            return hooks[0].text[:50]

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

        prompt = f"""以下の動画の内容から、TikTok/Reelsの最初3秒で視聴者のスクロールを止める
フックテキスト（キャッチコピー）を1つ生成してください。

条件:
- 15文字以内（厳守）
- 絵文字は使わない（フォント非対応のため）
- 美容や髪に限定しない。動画の実際の内容に合わせる
- 以下のパターンからランダムに選んで使う:
  * 疑問文で好奇心を刺激（「なぜ〇〇は△△なの？」）
  * 衝撃的な事実や数字（「99%の人が知らない」）
  * 禁止・警告系（「絶対やめて！」「まだ〇〇してるの？」）
  * 共感・あるある系（「これ分かる人いる？」）
  * 対比・ギャップ（「高い vs 安い」「プロ vs 素人」）
  * 秘密・裏技系（「誰も教えてくれない〇〇」）
- 前回と違うパターンを使うこと
- 商品名: {product_name or '（なし）'}
- 動画内容: {transcript[:300]}

フックテキストのみを出力（説明不要、括弧不要）:"""

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
            result = result.strip('"\'「」『』').strip()
            return result[:30]

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
        return random.choice(product_hooks)[:25]

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
    return random.choice(generic_hooks)


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


async def _save_export_record(clip_id: str, blob_url: str, thumbnail_url: Optional[str]):
    try:
        async with get_session() as session:
            await session.execute(text("""
                UPDATE video_clips
                SET exported_url = :exported_url,
                    exported_at = NOW()
                WHERE id = CAST(:clip_id AS uuid)
            """), {"clip_id": clip_id, "exported_url": blob_url})
            logger.info(f"[ai-clip] Saved export record for clip {clip_id}")
    except Exception as e:
        logger.warning(f"[ai-clip] Failed to save export record: {e}")
