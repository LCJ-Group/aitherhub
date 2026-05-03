"""
Database operations for batch worker.
Provides synchronous wrappers around async SQLAlchemy operations.
"""
import asyncio, uuid
import os, json
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text



# Load environment variables
load_dotenv()

# Database configuration
# ─────────────────────────────────────────────────────────────────
# asyncpg does NOT support the `sslmode` query parameter that
# psycopg2/libpq uses.  If DATABASE_URL contains `?sslmode=require`
# we must strip it and pass an ssl.SSLContext via connect_args.
# ─────────────────────────────────────────────────────────────────
import ssl as _ssl
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set in environment")

def _prepare_database_url(url: str):
    """Strip sslmode from URL and return (cleaned_url, connect_args)."""
    parsed = urlparse(url)
    qp = parse_qs(parsed.query)
    connect_args = {}
    if "sslmode" in qp:
        mode = qp.pop("sslmode")[0]
        if mode == "require":
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
            connect_args["ssl"] = ctx
        elif mode in ("verify-ca", "verify-full"):
            connect_args["ssl"] = _ssl.create_default_context()
    # Also handle `ssl=require` (already converted in .env)
    if "ssl" in qp:
        mode = qp.pop("ssl")[0]
        if mode == "require":
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
            connect_args["ssl"] = ctx
    new_query = urlencode(qp, doseq=True)
    cleaned = urlunparse((parsed.scheme, parsed.netloc, parsed.path,
                          parsed.params, new_query, parsed.fragment))
    return cleaned, connect_args

_cleaned_url, _connect_args = _prepare_database_url(DATABASE_URL)

# Create async engine
engine = create_async_engine(
    _cleaned_url,
    pool_pre_ping=True,
    echo=False,
    connect_args=_connect_args,
    pool_size=5,
    max_overflow=10,
    pool_recycle=1800,  # Recycle connections every 30 minutes
    pool_timeout=30,
)

AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Global event loop for reuse (avoids asyncpg pool conflicts)
_loop = None


def get_event_loop():
    """Get or create a persistent event loop for DB operations."""
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop


@asynccontextmanager
async def get_session():
    """Async context manager for database sessions."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    async with get_session() as session:
        await session.execute(text("SELECT 1"))
    print("[DB] Database connection initialized successfully")


async def close_db():
    """Close database engine and cleanup."""
    await engine.dispose()
    print("[DB] Database connection closed")


def init_db_sync():
    """Synchronous wrapper for database initialization."""
    loop = get_event_loop()
    loop.run_until_complete(init_db())


def close_db_sync():
    """Synchronous wrapper for database cleanup."""
    loop = get_event_loop()
    loop.run_until_complete(close_db())


async def reset_pool():
    """Dispose and recreate the connection pool to recover from stale connections."""
    global _loop
    try:
        await engine.dispose()
    except Exception:
        pass
    # Force new connections on next use
    print("[DB] Connection pool reset")


def reset_pool_sync():
    """Synchronous wrapper for pool reset."""
    loop = get_event_loop()
    loop.run_until_complete(reset_pool())


async def insert_phase(
    video_id: str,
    phase_index: int,
    phase_description: str | None,
    time_start: float | None,
    time_end: float | None,
    view_start: int | None,
    view_end: int | None,
    like_start: int | None,
    like_end: int | None,
    delta_view: int | None,
    delta_like: int | None,
    phase_group_id: int | None = None,
):
    """Insert a phase row and return the generated UUID as string."""
    sql = text(
        """
        INSERT INTO phases (
            video_id, phase_group_id, phase_index, phase_description,
            time_start, time_end, view_start, view_end,
            like_start, like_end, delta_view, delta_like
        ) VALUES (
            :video_id, :phase_group_id, :phase_index, :phase_description,
            :time_start, :time_end, :view_start, :view_end,
            :like_start, :like_end, :delta_view, :delta_like
        ) RETURNING id
        """
    )

    async with AsyncSessionLocal() as session:
        result = await session.execute(sql, {
            "video_id": video_id,
            "phase_group_id": phase_group_id,
            "phase_index": phase_index,
            "phase_description": phase_description,
            "time_start": time_start,
            "time_end": time_end,
            "view_start": view_start,
            "view_end": view_end,
            "like_start": like_start,
            "like_end": like_end,
            "delta_view": delta_view,
            "delta_like": delta_like,
        })
        row = result.fetchone()
        await session.commit()

    if row is None:
        raise RuntimeError("Failed to insert phase")

    # returned id is UUID object (if driver returns), convert to str
    return str(row[0])


def insert_phase_sync(*args, **kwargs):
    """Synchronous wrapper for `insert_phase` that returns the new id as string."""
    loop = get_event_loop()
    return loop.run_until_complete(insert_phase(*args, **kwargs))


# ---------- Helper ----------
async def get_user_id_of_video(video_id: str) -> int | None:
    sql = text("SELECT user_id FROM videos WHERE id = :video_id")
    async with AsyncSessionLocal() as session:
        r = await session.execute(sql, {"video_id": video_id})
        row = r.fetchone()
    return row[0] if row else None


def get_user_id_of_video_sync(video_id: str):
    loop = get_event_loop()
    return loop.run_until_complete(get_user_id_of_video(video_id))


# ---------- STEP 5: insert video_phases ----------

async def insert_video_phase(
    user_id: int,
    video_id: str,
    phase_index: int,
    phase_description: str | None,
    time_start: float | None,
    time_end: float | None,
    view_start: int | None,
    view_end: int | None,
    like_start: int | None,
    like_end: int | None,
    delta_view: int | None,
    delta_like: int | None,
):
    
    sql = text("""
        INSERT INTO video_phases (
            id, video_id, user_id, phase_index, group_id,
            phase_description,
            time_start, time_end,
            view_start, view_end,
            like_start, like_end,
            delta_view, delta_like
        ) VALUES (
            :id, :video_id, :user_id, :phase_index, NULL,
            :phase_description,
            :time_start, :time_end,
            :view_start, :view_end,
            :like_start, :like_end,
            :delta_view, :delta_like
        )
        RETURNING id
    """)

    new_id = str(uuid.uuid4())

    async with AsyncSessionLocal() as session:
        result = await session.execute(sql, {
            "id": new_id,
            "video_id": video_id,
            "user_id": user_id,
            "phase_index": phase_index,
            "phase_description": phase_description,
            "time_start": time_start,
            "time_end": time_end,
            "view_start": view_start,
            "view_end": view_end,
            "like_start": like_start,
            "like_end": like_end,
            "delta_view": delta_view,
            "delta_like": delta_like,
        })
        await session.commit()

    return new_id


def insert_video_phase_sync(*args, **kwargs):
    loop = get_event_loop()
    return loop.run_until_complete(insert_video_phase(*args, **kwargs))


# ---------- STEP 6: update phase_description ----------

async def update_video_phase_description(
    video_id: str,
    phase_index: int,
    phase_description: str,
):
    sql = text("""
        UPDATE video_phases
        SET phase_description = :phase_description,
            updated_at = now()
        WHERE video_id = :video_id
          AND phase_index = :phase_index
    """)

    async with AsyncSessionLocal() as session:
        await session.execute(sql, {
            "video_id": video_id,
            "phase_index": phase_index,
            "phase_description": phase_description,
        })
        await session.commit()


def update_video_phase_description_sync(*args, **kwargs):
    loop = get_event_loop()
    return loop.run_until_complete(update_video_phase_description(*args, **kwargs))


# ---------- STEP 5.5: persist audio_text (speech_text) to video_phases ----------
async def update_video_phase_audio_text(
    video_id: str,
    phase_index: int,
    audio_text: str,
):
    sql = text("""
        UPDATE video_phases
        SET audio_text = :audio_text,
            updated_at = now()
        WHERE video_id = :video_id
          AND phase_index = :phase_index
    """)

    async with AsyncSessionLocal() as session:
        await session.execute(sql, {
            "video_id": video_id,
            "phase_index": phase_index,
            "audio_text": audio_text,
        })
        await session.commit()


def update_video_phase_audio_text_sync(*args, **kwargs):
    loop = get_event_loop()
    return loop.run_until_complete(update_video_phase_audio_text(*args, **kwargs))


# ---------- STEP 7: upsert phase_groups + update video_phases ----------
async def get_all_phase_groups(user_id: int):
    """
    Load phase_groups scoped by user.

    Rules:
    - user-specific groups first
    - still include legacy groups (user_id IS NULL)
    """
    sql = text("""
        SELECT
            id,
            centroid,
            size
        FROM phase_groups
        WHERE user_id = :user_id
           
        ORDER BY id ASC
    """)

    async with AsyncSessionLocal() as session:
        result = await session.execute(sql, {
            "user_id": user_id
        })
        rows = result.fetchall()

    groups = []
    for r in rows:
        groups.append({
            "group_id": r.id,
            "centroid": r.centroid,
            "size": r.size,
        })

    return groups


def get_all_phase_groups_sync(user_id: int):
    loop = get_event_loop()
    return loop.run_until_complete(get_all_phase_groups(user_id))



# async def upsert_phase_group(group_id: int, centroid: list[float], size: int):
#     sql = text("""
#         INSERT INTO phase_groups (id, centroid, size)
#         VALUES (:id, :centroid, :size)
#         ON CONFLICT (id)
#         DO UPDATE SET
#             centroid = EXCLUDED.centroid,
#             size = EXCLUDED.size,
#             updated_at = now()
#     """)

#     async with AsyncSessionLocal() as session:
#         await session.execute(sql, {
#             "id": group_id,
#             "centroid": json.dumps(centroid),
#             "size": size,
#         })
#         await session.commit()


async def update_phase_group_for_video_phase(video_id: str, phase_index: int, group_id: int):
    sql = text("""
        UPDATE video_phases
        SET group_id = :group_id
        WHERE video_id = :video_id
          AND phase_index = :phase_index
    """)

    async with AsyncSessionLocal() as session:
        await session.execute(sql, {
            "group_id": group_id,
            "video_id": video_id,
            "phase_index": phase_index,
        })
        await session.commit()


# def upsert_phase_group_sync(*args, **kwargs):
#     loop = get_event_loop()
#     return loop.run_until_complete(upsert_phase_group(*args, **kwargs))


def update_phase_group_for_video_phase_sync(*args, **kwargs):
    loop = get_event_loop()
    return loop.run_until_complete(update_phase_group_for_video_phase(*args, **kwargs))


async def create_phase_group(
    user_id: int,
    centroid: list[float],
    size: int,
):
    sql = text("""
        INSERT INTO phase_groups (
            user_id,
            centroid,
            size
        )
        VALUES (
            :user_id,
            :centroid,
            :size
        )
        RETURNING id
    """)

    async with AsyncSessionLocal() as session:
        result = await session.execute(sql, {
            "user_id": user_id,
            "centroid": json.dumps(centroid),
            "size": size,
        })
        row = result.fetchone()
        await session.commit()

    return row[0]



def create_phase_group_sync(*args, **kwargs):
    loop = get_event_loop()
    return loop.run_until_complete(create_phase_group(*args, **kwargs))


async def update_phase_group(group_id: int, centroid: list[float], size: int):
    sql = text("""
        UPDATE phase_groups
        SET centroid = :centroid,
            size = :size,
            updated_at = now()
        WHERE id = :id
    """)
    async with AsyncSessionLocal() as session:
        await session.execute(sql, {
            "id": group_id,
            "centroid": json.dumps(centroid),
            "size": size,
        })
        await session.commit()


def update_phase_group_sync(*args, **kwargs):
    loop = get_event_loop()
    return loop.run_until_complete(update_phase_group(*args, **kwargs))

# ---------- STEP 8: upsert group_best_phases ----------
# ---------- STEP 8 BULK OPS ----------

async def bulk_upsert_group_best_phases(
    user_id: int,
    rows: list[dict],
):
    """
    rows = [
      {
        "group_id": int,
        "video_id": str,
        "phase_index": int,
        "score": float,
        "view_velocity": float,
        "like_velocity": float,
        "like_per_viewer": float,
      }
    ]
    """

    if not rows:
        return

    sql = text("""
        INSERT INTO group_best_phases (
            id,
            user_id,
            group_id,
            video_id,
            phase_index,
            score,
            view_velocity,
            like_velocity,
            like_per_viewer,
            ml_model_version
        )
        SELECT
            gen_random_uuid(),
            :user_id,
            x.group_id,
            x.video_id,
            x.phase_index,
            x.score,
            x.view_velocity,
            x.like_velocity,
            x.like_per_viewer,
            x.ml_model_version
        FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS x(
            group_id int,
            video_id uuid,
            phase_index int,
            score float8,
            view_velocity float8,
            like_velocity float8,
            like_per_viewer float8,
            ml_model_version text
        )
    """)

    async with AsyncSessionLocal() as session:
        await session.execute(sql, {
            "user_id": user_id,
            "rows": json.dumps(rows),
        })
        await session.commit()


def bulk_upsert_group_best_phases_sync(user_id, rows):
    loop = get_event_loop()
    return loop.run_until_complete(bulk_upsert_group_best_phases(user_id, rows))


async def bulk_refresh_phase_insights(
    user_id: int,
    best_rows: list[dict],
):
    """
    best_rows = same list as bulk_upsert_group_best_phases
    """

    if not best_rows:
        return

    # 1) Mark all phase_insights of affected groups = true (scoped by user)
    sql_mark = text("""
        UPDATE phase_insights pi
        SET needs_refresh = true,
            updated_at = now()
        WHERE pi.group_id IN (
            SELECT DISTINCT (x->>'group_id')::int
            FROM jsonb_array_elements(CAST(:rows AS jsonb)) x
        )
          AND (pi.user_id = :user_id)
    """)

    # 2) Clear needs_refresh for the best phases themselves (scoped by user)
    sql_clear = text("""
        UPDATE phase_insights pi
        SET needs_refresh = false,
            updated_at = now()
        FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS x(
            group_id int,
            video_id uuid,
            phase_index int,
            score float8,
            view_velocity float8,
            like_velocity float8,
            like_per_viewer float8
        )
        WHERE pi.video_id = x.video_id
          AND pi.phase_index = x.phase_index
          AND (pi.user_id = :user_id)
    """)

    async with AsyncSessionLocal() as session:
        await session.execute(text("SET statement_timeout = '30s'"))

        await session.execute(sql_mark, {
            "user_id": user_id,
            "rows": json.dumps(best_rows),
        })
        await session.execute(sql_clear, {
            "user_id": user_id,
            "rows": json.dumps(best_rows),
        })
        await session.commit()



def bulk_refresh_phase_insights_sync(user_id, rows):
    loop = get_event_loop()
    return loop.run_until_complete(
        bulk_refresh_phase_insights(user_id, rows)
    )



# =========================
# Phase insight refresh flags
# =========================

# ---------- STEP 12: upsert phase_insights ----------

# async def upsert_phase_insight(
#     user_id: int,
#     video_id: str,
#     phase_index: int,
#     group_id: int | None,
#     insight: str,
# ):
#     sql = text("""
#         INSERT INTO phase_insights (
#             id,
#             user_id,
#             video_id,
#             phase_index,
#             group_id,
#             insight,
#             needs_refresh
#         )
#         VALUES (
#             :id,
#             :user_id,
#             :video_id,
#             :phase_index,
#             :group_id,
#             :insight,
#             false
#         )
#         ON CONFLICT (user_id, video_id, phase_index)
#         DO UPDATE SET
#             group_id = EXCLUDED.group_id,
#             insight = EXCLUDED.insight,
#             needs_refresh = false,
#             updated_at = now()
#     """)

#     new_id = str(uuid.uuid4())

#     async with AsyncSessionLocal() as session:
#         await session.execute(sql, {
#             "id": new_id,
#             "user_id": user_id,
#             "video_id": video_id,
#             "phase_index": phase_index,
#             "group_id": group_id,
#             "insight": insight,
#         })
#         await session.commit()

async def upsert_phase_insight(
    user_id: int,
    video_id: str,
    phase_index: int,
    group_id: int | None,
    insight: str,
):
    """
    Upsert phase_insight WITHOUT ON CONFLICT.
    Safe for legacy data where user_id IS NULL.
    """

    new_id = str(uuid.uuid4())

    sql_update = text("""
        UPDATE phase_insights
        SET
            group_id = :group_id,
            insight = :insight,
            needs_refresh = false,
            updated_at = now()
        WHERE video_id = :video_id
          AND phase_index = :phase_index
          AND (user_id = :user_id )
    """)

    sql_insert = text("""
        INSERT INTO phase_insights (
            id,
            user_id,
            video_id,
            phase_index,
            group_id,
            insight,
            needs_refresh
        )
        VALUES (
            :id,
            :user_id,
            :video_id,
            :phase_index,
            :group_id,
            :insight,
            false
        )
    """)

    async with AsyncSessionLocal() as session:
        result = await session.execute(sql_update, {
            "user_id": user_id,
            "video_id": video_id,
            "phase_index": phase_index,
            "group_id": group_id,
            "insight": insight,
        })

        # Nếu không update được row nào → INSERT
        if result.rowcount == 0:
            await session.execute(sql_insert, {
                "id": new_id,
                "user_id": user_id,
                "video_id": video_id,
                "phase_index": phase_index,
                "group_id": group_id,
                "insight": insight,
            })

        await session.commit()



def upsert_phase_insight_sync(*args, **kwargs):
    loop = get_event_loop()
    return loop.run_until_complete(upsert_phase_insight(*args, **kwargs))


# =========================
# Video Insights (Report 3)
# =========================

async def insert_video_insight(
    video_id: str,
    title: str,
    content: str,
):
    sql = text("""
        INSERT INTO video_insights (
            id, video_id, title, content
        ) VALUES (
            :id, :video_id, :title, :content
        )
    """)

    new_id = str(uuid.uuid4())

    async with AsyncSessionLocal() as session:
        await session.execute(sql, {
            "id": new_id,
            "video_id": video_id,
            "title": title,
            "content": content,
        })
        await session.commit()

    return new_id


def insert_video_insight_sync(*args, **kwargs):
    loop = get_event_loop()
    return loop.run_until_complete(insert_video_insight(*args, **kwargs))




# ---------- update video status processing ----------
async def update_video_status(video_id: str, status: str):
    sql = text("""
        UPDATE videos
        SET status = :status,
            step_progress = 0,
            updated_at = now()
        WHERE id = :video_id
    """)
    async with AsyncSessionLocal() as session:
        await session.execute(sql, {
            "video_id": video_id,
            "status": status,
        })
        await session.commit()


def update_video_status_sync(video_id: str, status: str):
    loop = get_event_loop()
    return loop.run_until_complete(update_video_status(video_id, status))


async def update_video_step_progress(video_id: str, step_progress: int):
    """Update intra-step progress (0-100) for real-time progress display."""
    sql = text("""
        UPDATE videos
        SET step_progress = :step_progress,
            updated_at = now()
        WHERE id = :video_id
    """)
    async with AsyncSessionLocal() as session:
        await session.execute(sql, {
            "video_id": video_id,
            "step_progress": min(max(step_progress, 0), 100),
        })
        await session.commit()


def update_video_step_progress_sync(video_id: str, step_progress: int):
    loop = get_event_loop()
    return loop.run_until_complete(update_video_step_progress(video_id, step_progress))


async def get_video_status(video_id: str):
    sql = text("SELECT status FROM videos WHERE id = :video_id")
    async with AsyncSessionLocal() as session:
        result = await session.execute(sql, {"video_id": video_id})
        row = result.fetchone()
    return row[0] if row else None

def get_video_status_sync(video_id: str):
    loop = get_event_loop()
    return loop.run_until_complete(get_video_status(video_id))


# ---------- Load phase_units for resume ----------

async def load_video_phases(
    video_id: str,
    user_id: int,
):
    sql = text("""
        SELECT
            phase_index,
            phase_description,
            time_start, time_end,
            view_start, view_end,
            like_start, like_end,
            delta_view, delta_like,
            group_id,
            human_sales_tags,
            sales_psychology_tags,
            audio_text
        FROM video_phases
        WHERE video_id = :video_id
          AND (user_id = :user_id )
        ORDER BY phase_index ASC
    """)

    async with AsyncSessionLocal() as session:
        result = await session.execute(sql, {
            "video_id": video_id,
            "user_id": user_id,
        })
        rows = result.fetchall()

    phases = []
    for r in rows:
        phases.append({
            "video_id": video_id,
            "phase_index": r.phase_index,
            "phase_description": r.phase_description,

            "time_start": r.time_start,
            "time_end": r.time_end,

            "time_range": {
                "start": r.time_start,
                "end": r.time_end,
                "start_sec": float(r.time_start) if r.time_start is not None else 0.0,
                "end_sec": float(r.time_end) if r.time_end is not None else 0.0,
            },

            "view_start": r.view_start,
            "view_end": r.view_end,
            "like_start": r.like_start,
            "like_end": r.like_end,
            "delta_view": r.delta_view,
            "delta_like": r.delta_like,
            "group_id": r.group_id,
            "human_sales_tags": r.human_sales_tags,
            "sales_psychology_tags": r.sales_psychology_tags,
            "speech_text": r.audio_text,

            "metrics": {
                "delta_view": r.delta_view,
                "delta_like": r.delta_like,
            },

            "metric_timeseries": {
                "start": {
                    "view": r.view_start,
                    "like": r.like_start,
                },
                "end": {
                    "view": r.view_end,
                    "like": r.like_end,
                }
            }
        })

    return phases


def load_video_phases_sync(video_id: str, user_id: int):
    loop = get_event_loop()
    return loop.run_until_complete(
        load_video_phases(video_id, user_id)
    )



# =========================
# STEP 9 (VIDEO STRUCTURE): upsert video_structure_features
# =========================

async def upsert_video_structure_features(
    user_id: int,
    video_id: str,
    phase_count: int,
    avg_phase_duration: float,
    switch_rate: float,
    early_ratio: dict,
    mid_ratio: dict,
    late_ratio: dict,
    structure_embedding: list,
):
    sql_update = text("""
        UPDATE video_structure_features
        SET
            phase_count = :phase_count,
            avg_phase_duration = :avg_phase_duration,
            switch_rate = :switch_rate,
            early_ratio = :early_ratio,
            mid_ratio = :mid_ratio,
            late_ratio = :late_ratio,
            structure_embedding = :structure_embedding
        WHERE video_id = :video_id
          AND (user_id = :user_id )
    """)

    sql_insert = text("""
        INSERT INTO video_structure_features (
            video_id,
            user_id,
            phase_count,
            avg_phase_duration,
            switch_rate,
            early_ratio,
            mid_ratio,
            late_ratio,
            structure_embedding
        )
        VALUES (
            :video_id,
            :user_id,
            :phase_count,
            :avg_phase_duration,
            :switch_rate,
            :early_ratio,
            :mid_ratio,
            :late_ratio,
            :structure_embedding
        )
    """)

    async with AsyncSessionLocal() as session:
        result = await session.execute(sql_update, {
            "user_id": user_id,
            "video_id": video_id,
            "phase_count": phase_count,
            "avg_phase_duration": avg_phase_duration,
            "switch_rate": switch_rate,
            "early_ratio": json.dumps(early_ratio),
            "mid_ratio": json.dumps(mid_ratio),
            "late_ratio": json.dumps(late_ratio),
            "structure_embedding": json.dumps(structure_embedding),
        })

        if result.rowcount == 0:
            await session.execute(sql_insert, {
                "user_id": user_id,
                "video_id": video_id,
                "phase_count": phase_count,
                "avg_phase_duration": avg_phase_duration,
                "switch_rate": switch_rate,
                "early_ratio": json.dumps(early_ratio),
                "mid_ratio": json.dumps(mid_ratio),
                "late_ratio": json.dumps(late_ratio),
                "structure_embedding": json.dumps(structure_embedding),
            })

        await session.commit()




def upsert_video_structure_features_sync(*args, **kwargs):
    loop = get_event_loop()
    return loop.run_until_complete(upsert_video_structure_features(*args, **kwargs))


# =========================
# STEP 10 (VIDEO STRUCTURE): grouping ops
# =========================

# ---------- get video_structure_features ----------

async def get_video_structure_features(
    video_id: str,
    user_id: int,
):
    sql = text("""
        SELECT
            video_id,
            phase_count,
            avg_phase_duration,
            switch_rate,
            early_ratio,
            mid_ratio,
            late_ratio,
            structure_embedding
        FROM video_structure_features
        WHERE video_id = :video_id
          AND (user_id = :user_id )
        ORDER BY user_id DESC NULLS LAST
        LIMIT 1
    """)

    async with AsyncSessionLocal() as session:
        result = await session.execute(sql, {
            "video_id": video_id,
            "user_id": user_id,
        })
        row = result.mappings().first()

    return dict(row) if row else None



def get_video_structure_features_sync(video_id: str, user_id: int):
    loop = get_event_loop()
    return loop.run_until_complete(
        get_video_structure_features(video_id, user_id)
    )


# ---------- get all video_structure_groups ----------

async def get_all_video_structure_groups(user_id: int):
    sql = text("""
        SELECT *
        FROM video_structure_groups
        WHERE user_id = :user_id 
        ORDER BY id
    """)
    async with AsyncSessionLocal() as session:
        r = await session.execute(sql, {"user_id": user_id})
        rows = r.mappings().all()
    return rows


def get_all_video_structure_groups_sync(user_id: int):
    loop = get_event_loop()
    return loop.run_until_complete(get_all_video_structure_groups(user_id))


# ---------- create video_structure_group ----------

async def create_video_structure_group(
    user_id: int,
    structure_embedding: list,
    phase_count: int,
    avg_phase_duration: float,
    avg_switch_rate: float,
    early_ratio: dict,
    mid_ratio: dict,
    late_ratio: dict,
):
    sql = text("""
        INSERT INTO video_structure_groups (
            id,
            user_id,
            structure_embedding,
            avg_phase_count,
            avg_phase_duration,
            avg_switch_rate,
            early_ratio,
            mid_ratio,
            late_ratio,
            video_count
        )
        VALUES (
            gen_random_uuid(),
            :user_id,
            :structure_embedding,
            :avg_phase_count,
            :avg_phase_duration,
            :avg_switch_rate,
            :early_ratio,
            :mid_ratio,
            :late_ratio,
            1
        )
        RETURNING id
    """)
    async with AsyncSessionLocal() as session:
        r = await session.execute(sql, {
            "user_id": user_id,
            "structure_embedding": json.dumps(structure_embedding),
            "avg_phase_count": phase_count,
            "avg_phase_duration": avg_phase_duration,
            "avg_switch_rate": avg_switch_rate,
            "early_ratio": json.dumps(early_ratio),
            "mid_ratio": json.dumps(mid_ratio),
            "late_ratio": json.dumps(late_ratio),
        })
        row = r.fetchone()
        await session.commit()
    return row[0]

def create_video_structure_group_sync(*args, **kwargs):
    loop = get_event_loop()
    return loop.run_until_complete(create_video_structure_group(*args, **kwargs))



# ---------- update video_structure_group ----------

async def update_video_structure_group(
    user_id: int,
    group_id: str,
    structure_embedding: list,
    avg_phase_count: float,
    avg_phase_duration: float,
    avg_switch_rate: float,
    early_ratio: dict,
    mid_ratio: dict,
    late_ratio: dict,
    video_count: int,
):
    sql = text("""
        UPDATE video_structure_groups
        SET
            structure_embedding = :structure_embedding,
            avg_phase_count = :avg_phase_count,
            avg_phase_duration = :avg_phase_duration,
            avg_switch_rate = :avg_switch_rate,
            early_ratio = :early_ratio,
            mid_ratio = :mid_ratio,
            late_ratio = :late_ratio,
            video_count = :video_count,
            updated_at = now()
        WHERE id = :group_id
          AND (user_id = :user_id )
    """)
    async with AsyncSessionLocal() as session:
        await session.execute(sql, {
            "user_id": user_id,
            "group_id": group_id,
            "structure_embedding": json.dumps(structure_embedding),
            "avg_phase_count": avg_phase_count,
            "avg_phase_duration": avg_phase_duration,
            "avg_switch_rate": avg_switch_rate,
            "early_ratio": json.dumps(early_ratio),
            "mid_ratio": json.dumps(mid_ratio),
            "late_ratio": json.dumps(late_ratio),
            "video_count": video_count,
        })
        await session.commit()


def update_video_structure_group_sync(*args, **kwargs):
    loop = get_event_loop()
    return loop.run_until_complete(update_video_structure_group(*args, **kwargs))


# ---------- upsert video_structure_group_members ----------

async def upsert_video_structure_group_member(
    user_id: int,
    video_id: str,
    group_id: str,
    distance: float | None,
):
    sql_update = text("""
        UPDATE video_structure_group_members
        SET
            group_id = :group_id,
            distance = :distance
        WHERE video_id = :video_id
          AND (user_id = :user_id )
    """)

    sql_insert = text("""
        INSERT INTO video_structure_group_members (
            id,
            user_id,
            video_id,
            group_id,
            distance
        )
        VALUES (
            :id,
            :user_id,
            :video_id,
            :group_id,
            :distance
        )
    """)

    new_id = str(uuid.uuid4())

    async with AsyncSessionLocal() as session:
        res = await session.execute(sql_update, {
            "user_id": user_id,
            "video_id": video_id,
            "group_id": group_id,
            "distance": distance,
        })
        if res.rowcount == 0:
            await session.execute(sql_insert, {
                "id": new_id,
                "user_id": user_id,
                "video_id": video_id,
                "group_id": group_id,
                "distance": distance,
            })
        await session.commit()



def upsert_video_structure_group_member_sync(*args, **kwargs):
    loop = get_event_loop()
    return loop.run_until_complete(upsert_video_structure_group_member(*args, **kwargs))


# =========================
# STEP 11 (VIDEO STRUCTURE): recompute group stats
# =========================

# ---------- get members by group ----------

async def get_video_structure_group_members_by_group(
    group_id: str,
    user_id: int,
):
    sql = text("""
        SELECT video_id
        FROM video_structure_group_members
        WHERE group_id = :group_id
          AND (user_id = :user_id )
    """)
    async with AsyncSessionLocal() as session:
        r = await session.execute(sql, {
            "group_id": group_id,
            "user_id": user_id,
        })
        rows = r.fetchall()
    return [x[0] for x in rows]



def get_video_structure_group_members_by_group_sync(group_id: str, user_id: int):
    loop = get_event_loop()
    return loop.run_until_complete(
        get_video_structure_group_members_by_group(group_id, user_id)
    )


# ---------- get group id of video ----------

async def get_video_structure_group_id_of_video(
    video_id: str,
    user_id: int,
):
    sql = text("""
        SELECT group_id
        FROM video_structure_group_members
        WHERE video_id = :video_id
          AND (user_id = :user_id )
        ORDER BY user_id DESC NULLS LAST
        LIMIT 1
    """)
    async with AsyncSessionLocal() as session:
        r = await session.execute(sql, {
            "video_id": video_id,
            "user_id": user_id,
        })
        row = r.fetchone()
    return row[0] if row else None



def get_video_structure_group_id_of_video_sync(video_id: str, user_id: int):
    loop = get_event_loop()
    return loop.run_until_complete(
        get_video_structure_group_id_of_video(video_id, user_id)
    )


# =========================================================
# STEP 12 – VIDEO STRUCTURE BEST (DB OPS)
# =========================================================

# ---------- get phase points for velocity ----------

async def get_video_phase_points(video_id: str):
    sql = text("""
        SELECT
            (time_start + time_end) / 2.0 AS t,
            view_end,
            like_end
        FROM video_phases
        WHERE video_id = :video_id
          AND time_start IS NOT NULL
          AND time_end IS NOT NULL
          AND time_end > time_start
        ORDER BY t ASC
    """)
    async with AsyncSessionLocal() as session:
        result = await session.execute(sql, {"video_id": video_id})
        rows = result.mappings().all()
    return [dict(r) for r in rows]


def get_video_phase_points_sync(video_id: str):
    loop = get_event_loop()
    return loop.run_until_complete(get_video_phase_points(video_id))


# ---------- get best video of structure group ----------

async def get_video_structure_group_best_video(
    group_id: str,
    user_id: int,
):
    sql = text("""
        SELECT
            group_id,
            video_id,
            score
        FROM video_structure_group_best_videos
        WHERE group_id = :group_id
          AND (user_id = :user_id )
        ORDER BY user_id DESC NULLS LAST, score DESC
        LIMIT 1
    """)

    async with AsyncSessionLocal() as session:
        r = await session.execute(sql, {
            "group_id": group_id,
            "user_id": user_id,
        })
        row = r.mappings().first()

    return dict(row) if row else None



def get_video_structure_group_best_video_sync(group_id: str, user_id: int):
    loop = get_event_loop()
    return loop.run_until_complete(
        get_video_structure_group_best_video(group_id, user_id)
    )



# ---------- upsert best video ----------

async def upsert_video_structure_group_best_video(
    user_id: int,
    group_id: str,
    video_id: str,
    score: float,
    metrics: dict,
):
    sql_update = text("""
        UPDATE video_structure_group_best_videos
        SET
            video_id = :video_id,
            score = :score,
            metrics = :metrics,
            updated_at = now()
        WHERE group_id = :group_id
          AND (user_id = :user_id)
    """)

    sql_insert = text("""
        INSERT INTO video_structure_group_best_videos (
            id,
            user_id,
            group_id,
            video_id,
            score,
            metrics
        )
        VALUES (
            gen_random_uuid(),
            :user_id,
            :group_id,
            :video_id,
            :score,
            :metrics
        )
    """)

    async with AsyncSessionLocal() as session:
        r = await session.execute(sql_update, {
            "user_id": user_id,
            "group_id": group_id,
            "video_id": video_id,
            "score": score,
            "metrics": json.dumps(metrics),
        })

        if r.rowcount == 0:
            await session.execute(sql_insert, {
                "user_id": user_id,
                "group_id": group_id,
                "video_id": video_id,
                "score": score,
                "metrics": json.dumps(metrics),
            })

        await session.commit()


def upsert_video_structure_group_best_video_sync(*args, **kwargs):
    loop = get_event_loop()
    return loop.run_until_complete(
        upsert_video_structure_group_best_video(*args, **kwargs)
    )


# ---------- mark video_insights need refresh (except best) ----------

async def mark_video_insights_need_refresh_by_structure_group(
    user_id: int,
    group_id: str,
    except_video_id: str,
):
    sql = text("""
        UPDATE video_insights vi
        SET needs_refresh = true,
            updated_at = now()
        WHERE vi.video_id IN (
            SELECT vsgm.video_id
            FROM video_structure_group_members vsgm
            WHERE vsgm.group_id = :group_id
              AND vsgm.video_id != :except_video_id
              AND (vsgm.user_id = :user_id)
        )
          AND (vi.user_id = :user_id)
    """)

    async with AsyncSessionLocal() as session:
        await session.execute(sql, {
            "user_id": user_id,
            "group_id": group_id,
            "except_video_id": except_video_id,
        })
        await session.commit()


def mark_video_insights_need_refresh_by_structure_group_sync(*args, **kwargs):
    loop = get_event_loop()
    return loop.run_until_complete(
        mark_video_insights_need_refresh_by_structure_group(*args, **kwargs)
    )


# ---------- clear need refresh flag of best video ----------

async def clear_video_insight_need_refresh(video_id: str):
    sql = text("""
        UPDATE video_insights
        SET needs_refresh = false,
            updated_at = now()
        WHERE video_id = :video_id
    """)

    async with AsyncSessionLocal() as session:
        await session.execute(sql, {"video_id": video_id})
        await session.commit()


def clear_video_insight_need_refresh_sync(video_id: str):
    loop = get_event_loop()
    return loop.run_until_complete(clear_video_insight_need_refresh(video_id))

# ---------- get video_structure_group stats ----------

async def get_video_structure_group_stats(
    group_id: str,
    user_id: int,
):
    sql = text("""
        SELECT
            id AS group_id,
            avg_phase_count,
            avg_phase_duration,
            avg_switch_rate,
            early_ratio,
            mid_ratio,
            late_ratio,
            video_count
        FROM video_structure_groups
        WHERE id = :group_id
          AND (user_id = :user_id )
        LIMIT 1
    """)

    async with AsyncSessionLocal() as session:
        result = await session.execute(sql, {
            "group_id": group_id,
            "user_id": user_id,
        })
        row = result.mappings().first()

    return dict(row) if row else None


def get_video_structure_group_stats_sync(group_id: str, user_id: int):
    loop = get_event_loop()
    return loop.run_until_complete(
        get_video_structure_group_stats(group_id, user_id)
    )



# =========================
# Split status (VIDEO)
# =========================

async def get_video_split_status(video_id: str):
    sql = text("""
        SELECT split_status
        FROM videos
        WHERE id = :video_id
    """)
    async with AsyncSessionLocal() as session:
        result = await session.execute(sql, {"video_id": video_id})
        row = result.fetchone()
    return row[0] if row else None


def get_video_split_status_sync(video_id: str):
    loop = get_event_loop()
    return loop.run_until_complete(get_video_split_status(video_id))


async def update_video_split_status(video_id: str, split_status: str):
    sql = text("""
        UPDATE videos
        SET split_status = :split_status,
            updated_at = now()
        WHERE id = :video_id
    """)
    async with AsyncSessionLocal() as session:
        await session.execute(sql, {
            "video_id": video_id,
            "split_status": split_status,
        })
        await session.commit()


def update_video_split_status_sync(video_id: str, split_status: str):
    loop = get_event_loop()
    return loop.run_until_complete(
        update_video_split_status(video_id, split_status)
    )

# ---------- Excel URLs for clean video ----------

async def get_video_excel_urls(video_id: str):
    """
    Get upload_type, excel_product_blob_url, excel_trend_blob_url
    for a given video.
    """
    sql = text("""
        SELECT upload_type, excel_product_blob_url, excel_trend_blob_url,
               COALESCE(time_offset_seconds, 0) as time_offset_seconds
        FROM videos
        WHERE id = :video_id
    """)
    async with AsyncSessionLocal() as session:
        result = await session.execute(sql, {"video_id": video_id})
        row = result.fetchone()
    if not row:
        return None
    return {
        "upload_type": row[0],
        "excel_product_blob_url": row[1],
        "excel_trend_blob_url": row[2],
        "time_offset_seconds": float(row[3]) if row[3] else 0,
    }


def get_video_excel_urls_sync(video_id: str):
    loop = get_event_loop()
    return loop.run_until_complete(get_video_excel_urls(video_id))

# =========================
# Video Language
# =========================
async def get_video_language(video_id: str) -> str:
    """Get the language setting for a video (defaults to 'ja')."""
    sql = text("""
        SELECT COALESCE(language, 'ja') as language
        FROM videos
        WHERE id = :video_id
    """)
    async with AsyncSessionLocal() as session:
        result = await session.execute(sql, {"video_id": video_id})
        row = result.fetchone()
    if not row:
        return "ja"
    return row[0] or "ja"

def get_video_language_sync(video_id: str) -> str:
    loop = get_event_loop()
    return loop.run_until_complete(get_video_language(video_id))



# =========================
# CTA Score (PHASE)
# =========================

async def update_video_phase_cta_score(video_id: str, phase_index: int, cta_score: int):
    sql = text("""
        UPDATE video_phases
        SET cta_score = :cta_score,
            updated_at = now()
        WHERE video_id = :video_id
          AND phase_index = :phase_index
    """)
    async with AsyncSessionLocal() as session:
        await session.execute(sql, {
            "video_id": video_id,
            "phase_index": phase_index,
            "cta_score": cta_score,
        })
        await session.commit()


def update_video_phase_cta_score_sync(video_id: str, phase_index: int, cta_score: int):
    loop = get_event_loop()
    return loop.run_until_complete(
        update_video_phase_cta_score(video_id, phase_index, cta_score)
    )


# =========================
# Sales Psychology Tags (PHASE)
# =========================

async def update_video_phase_sales_tags(video_id: str, phase_index: int, sales_tags_json: str):
    """
    Store sales psychology tags as JSON array text.
    sales_tags_json should be a JSON string like:
    '["HOOK", "DEMONSTRATION", "CTA"]'
    """
    sql = text("""
        UPDATE video_phases
        SET sales_psychology_tags = :sales_tags,
            updated_at = now()
        WHERE video_id = :video_id
          AND phase_index = :phase_index
    """)
    async with AsyncSessionLocal() as session:
        await session.execute(sql, {
            "video_id": video_id,
            "phase_index": phase_index,
            "sales_tags": sales_tags_json,
        })
        await session.commit()


def update_video_phase_sales_tags_sync(video_id: str, phase_index: int, sales_tags_json: str):
    loop = get_event_loop()
    return loop.run_until_complete(
        update_video_phase_sales_tags(video_id, phase_index, sales_tags_json)
    )


# =========================
# Audio Features (PHASE)
# =========================

async def update_video_phase_audio_features(video_id: str, phase_index: int, audio_features_json: str):
    """
    Store audio paralinguistic features as JSON text.
    audio_features_json should be a JSON string like:
    '{"energy_mean": 0.01, "pitch_mean": 210.5, ...}'
    """
    sql = text("""
        UPDATE video_phases
        SET audio_features = :audio_features,
            updated_at = now()
        WHERE video_id = :video_id
          AND phase_index = :phase_index
    """)
    async with AsyncSessionLocal() as session:
        await session.execute(sql, {
            "video_id": video_id,
            "phase_index": phase_index,
            "audio_features": audio_features_json,
        })
        await session.commit()


def update_video_phase_audio_features_sync(video_id: str, phase_index: int, audio_features_json: str):
    loop = get_event_loop()
    return loop.run_until_complete(
        update_video_phase_audio_features(video_id, phase_index, audio_features_json)
    )


# =========================
# CSV Metrics (PHASE) – was missing from codebase
# =========================

async def update_video_phase_csv_metrics(
    video_id: str,
    phase_index: int,
    **kwargs
):
    """
    Update CSV-derived metrics for a video phase.
    Accepts any combination of:
      gmv, order_count, viewer_count, like_count, comment_count,
      share_count, new_followers, product_clicks, conversion_rate,
      gpm, importance_score
    """
    if not kwargs:
        return

    set_clauses = []
    params = {"video_id": video_id, "phase_index": phase_index}

    for key, value in kwargs.items():
        set_clauses.append(f"{key} = :{key}")
        params[key] = value

    set_clauses.append("updated_at = now()")
    set_sql = ", ".join(set_clauses)

    sql = text(f"""
        UPDATE video_phases
        SET {set_sql}
        WHERE video_id = :video_id
          AND phase_index = :phase_index
    """)

    async with AsyncSessionLocal() as session:
        await session.execute(sql, params)
        await session.commit()


def update_video_phase_csv_metrics_sync(video_id: str, phase_index: int, **kwargs):
    loop = get_event_loop()
    return loop.run_until_complete(
        update_video_phase_csv_metrics(video_id, phase_index, **kwargs)
    )


# =========================================================
# Product Exposure Timeline (video_product_exposures)
# =========================================================

async def ensure_product_exposures_table():
    """CREATE TABLE IF NOT EXISTS for video_product_exposures."""
    sql = text("""
        CREATE TABLE IF NOT EXISTS video_product_exposures (
            id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
            video_id UUID NOT NULL,
            user_id INTEGER,
            product_name TEXT NOT NULL,
            brand_name TEXT,
            product_image_url TEXT,
            time_start FLOAT NOT NULL,
            time_end FLOAT NOT NULL,
            confidence FLOAT DEFAULT 0.8,
            source VARCHAR(20) DEFAULT 'ai',
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    idx1 = text("""
        CREATE INDEX IF NOT EXISTS ix_vpe_video_id
        ON video_product_exposures (video_id)
    """)
    idx2 = text("""
        CREATE INDEX IF NOT EXISTS ix_vpe_video_time
        ON video_product_exposures (video_id, time_start, time_end)
    """)
    async with AsyncSessionLocal() as session:
        await session.execute(sql)
        await session.execute(idx1)
        await session.execute(idx2)
        await session.commit()


def ensure_product_exposures_table_sync():
    loop = get_event_loop()
    return loop.run_until_complete(ensure_product_exposures_table())


async def bulk_insert_product_exposures(
    video_id: str,
    user_id,
    exposures: list,
):
    """
    AI検出結果を一括挿入する。
    既存のAI生成データは削除してから挿入（冪等性）。
    """
    if not exposures:
        return

    delete_sql = text("""
        DELETE FROM video_product_exposures
        WHERE video_id = :video_id AND source = 'ai'
    """)

    insert_sql = text("""
        INSERT INTO video_product_exposures
            (video_id, user_id, product_name, brand_name,
             product_image_url, time_start, time_end, confidence, source)
        VALUES
            (:video_id, :user_id, :product_name, :brand_name,
             :product_image_url, :time_start, :time_end, :confidence, 'ai')
    """)

    async with AsyncSessionLocal() as session:
        await session.execute(delete_sql, {"video_id": video_id})

        for exp in exposures:
            await session.execute(insert_sql, {
                "video_id": video_id,
                "user_id": user_id,
                "product_name": exp.get("product_name", ""),
                "brand_name": exp.get("brand_name", ""),
                "product_image_url": exp.get("product_image_url", ""),
                "time_start": exp.get("time_start", 0),
                "time_end": exp.get("time_end", 0),
                "confidence": exp.get("confidence", 0.8),
            })

        await session.commit()


def bulk_insert_product_exposures_sync(video_id, user_id, exposures):
    loop = get_event_loop()
    return loop.run_until_complete(
        bulk_insert_product_exposures(video_id, user_id, exposures)
    )


async def get_product_exposures(video_id: str):
    """動画の商品タイムラインを取得する"""
    sql = text("""
        SELECT id, video_id, user_id, product_name, brand_name,
               product_image_url, time_start, time_end, confidence, source,
               created_at, updated_at
        FROM video_product_exposures
        WHERE video_id = :video_id
        ORDER BY time_start ASC
    """)
    async with AsyncSessionLocal() as session:
        result = await session.execute(sql, {"video_id": video_id})
        rows = result.fetchall()
        return [dict(row._mapping) for row in rows]


def get_product_exposures_sync(video_id: str):
    loop = get_event_loop()
    return loop.run_until_complete(get_product_exposures(video_id))


# ---------- worker claimed evidence (Improvement 2) ----------
async def update_worker_claimed(video_id: str, instance_id: str, dequeue_count: int):
    """Record that the worker has claimed this job.

    Written at the moment the worker dequeues the message, before any
    processing begins.  This lets the UI show 'ワーカー受信' instead of
    the ambiguous 'キュー待ち' state.
    """
    sql = text("""
        UPDATE videos
        SET worker_claimed_at = now(),
            worker_instance_id = :instance_id,
            dequeue_count = :dequeue_count,
            updated_at = now()
        WHERE id = :video_id
    """)
    async with AsyncSessionLocal() as session:
        await session.execute(sql, {
            "video_id": video_id,
            "instance_id": instance_id,
            "dequeue_count": dequeue_count,
        })
        await session.commit()


def update_worker_claimed_sync(video_id: str, instance_id: str, dequeue_count: int):
    loop = get_event_loop()
    return loop.run_until_complete(update_worker_claimed(video_id, instance_id, dequeue_count))


# =========================================================
# Sales Moments (video_sales_moments)
# =========================================================
# AI学習の「正解ラベル」として使用される。
# trend_statsのclick_spike/order_spikeから検出した
# 「売れた瞬間」を保存する。
# ルールA: 既存テーブルは触らない。完全に新規テーブル。

async def ensure_sales_moments_table():
    """CREATE TABLE IF NOT EXISTS for video_sales_moments."""
    sql = text("""
        CREATE TABLE IF NOT EXISTS video_sales_moments (
            id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
            video_id UUID NOT NULL,
            time_key VARCHAR(50),
            time_sec FLOAT NOT NULL,
            video_sec FLOAT NOT NULL,
            moment_type VARCHAR(20) NOT NULL,
            moment_type_detail VARCHAR(50),
            source VARCHAR(20) DEFAULT 'csv' NOT NULL,
            click_value FLOAT DEFAULT 0,
            click_delta FLOAT DEFAULT 0,
            click_sigma_score FLOAT DEFAULT 0,
            order_value FLOAT DEFAULT 0,
            order_delta FLOAT DEFAULT 0,
            gmv_value FLOAT DEFAULT 0,
            confidence FLOAT DEFAULT 0,
            reasons TEXT,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    idx1 = text("""
        CREATE INDEX IF NOT EXISTS ix_vsm_video_id
        ON video_sales_moments (video_id)
    """)
    idx2 = text("""
        CREATE INDEX IF NOT EXISTS ix_vsm_video_time
        ON video_sales_moments (video_id, video_sec)
    """)
    idx3 = text("""
        CREATE INDEX IF NOT EXISTS ix_vsm_moment_type
        ON video_sales_moments (moment_type)
    """)
    idx4 = text("""
        CREATE INDEX IF NOT EXISTS ix_vsm_source
        ON video_sales_moments (source)
    """)
    idx5 = text("""
        CREATE INDEX IF NOT EXISTS ix_vsm_video_source
        ON video_sales_moments (video_id, source)
    """)
    # ALTER TABLE for existing tables (idempotent)
    alter1 = text("""
        ALTER TABLE video_sales_moments
        ADD COLUMN IF NOT EXISTS source VARCHAR(20) DEFAULT 'csv' NOT NULL
    """)
    alter2 = text("""
        ALTER TABLE video_sales_moments
        ADD COLUMN IF NOT EXISTS moment_type_detail VARCHAR(50)
    """)
    async with AsyncSessionLocal() as session:
        await session.execute(sql)
        await session.execute(idx1)
        await session.execute(idx2)
        await session.execute(idx3)
        # Ensure new columns exist (for tables created before migration)
        try:
            await session.execute(alter1)
            await session.execute(alter2)
        except Exception:
            pass  # columns already exist
        await session.execute(idx4)
        await session.execute(idx5)
        await session.commit()


def ensure_sales_moments_table_sync():
    loop = get_event_loop()
    return loop.run_until_complete(ensure_sales_moments_table())


async def bulk_insert_sales_moments(
    video_id: str,
    moments: list,
    source: str = "csv",
):
    """
    検出されたsales_momentsを一括挿入する。
    同一source内の既存データは削除してから挿入（冪等性）。
    異なるsourceのデータは保持する。

    Args:
        video_id: 動画ID
        moments: 検出されたmomentのリスト
        source: 'csv' or 'screen'
    """
    if not moments:
        return

    import json as _json

    # 同一sourceのデータのみ削除（他sourceは保持）
    delete_sql = text("""
        DELETE FROM video_sales_moments
        WHERE video_id = :video_id AND source = :source
    """)

    insert_sql = text("""
        INSERT INTO video_sales_moments
            (video_id, time_key, time_sec, video_sec, moment_type,
             moment_type_detail, source,
             click_value, click_delta, click_sigma_score,
             order_value, order_delta, gmv_value,
             confidence, reasons, frame_meta)
        VALUES
            (:video_id, :time_key, :time_sec, :video_sec, :moment_type,
             :moment_type_detail, :source,
             :click_value, :click_delta, :click_sigma_score,
             :order_value, :order_delta, :gmv_value,
             :confidence, :reasons, :frame_meta)
    """)

    async with AsyncSessionLocal() as session:
        await session.execute(delete_sql, {"video_id": video_id, "source": source})

        for m in moments:
            await session.execute(insert_sql, {
                "video_id": video_id,
                "time_key": m.get("time_key", ""),
                "time_sec": m.get("time_sec", 0),
                "video_sec": m.get("video_sec", 0),
                "moment_type": m.get("moment_type", "click"),
                "moment_type_detail": m.get("moment_type_detail", m.get("moment_type", "click")),
                "source": source,
                "click_value": m.get("click_value", 0),
                "click_delta": m.get("click_delta", 0),
                "click_sigma_score": m.get("click_sigma_score", 0),
                "order_value": m.get("order_value", 0),
                "order_delta": m.get("order_delta", 0),
                "gmv_value": m.get("gmv_value", 0),
                "confidence": m.get("confidence", 0),
                "reasons": _json.dumps(m.get("reasons", []), ensure_ascii=False),
                "frame_meta": _json.dumps(m.get("frame_meta", {}), ensure_ascii=False) if m.get("frame_meta") else None,
            })

        await session.commit()


def bulk_insert_sales_moments_sync(video_id: str, moments: list, source: str = "csv"):
    loop = get_event_loop()
    return loop.run_until_complete(
        bulk_insert_sales_moments(video_id, moments, source=source)
    )


async def get_sales_moments(video_id: str, source: str = None):
    """動画のsales_momentsを取得する。source指定でフィルタ可能。"""
    if source:
        sql = text("""
            SELECT id, video_id, time_key, time_sec, video_sec, moment_type,
                   moment_type_detail, source,
                   click_value, click_delta, click_sigma_score,
                   order_value, order_delta, gmv_value,
                   confidence, reasons, created_at
            FROM video_sales_moments
            WHERE video_id = :video_id AND source = :source
            ORDER BY video_sec ASC
        """)
        params = {"video_id": video_id, "source": source}
    else:
        sql = text("""
            SELECT id, video_id, time_key, time_sec, video_sec, moment_type,
                   moment_type_detail, source,
                   click_value, click_delta, click_sigma_score,
                   order_value, order_delta, gmv_value,
                   confidence, reasons, created_at
            FROM video_sales_moments
            WHERE video_id = :video_id
            ORDER BY video_sec ASC
        """)
        params = {"video_id": video_id}
    async with AsyncSessionLocal() as session:
        result = await session.execute(sql, params)
        rows = result.fetchall()
        return [dict(row._mapping) for row in rows]


def get_sales_moments_sync(video_id: str):
    loop = get_event_loop()
    return loop.run_until_complete(get_sales_moments(video_id))


# ─────────────────────────────────────────────────────────────────
# Video Error Log helpers
# ─────────────────────────────────────────────────────────────────

async def insert_video_error_log(
    video_id: str,
    error_code: str,
    error_step: str = None,
    error_message: str = None,
    error_detail: str = None,
    source: str = "worker",
    update_last_error: bool = True,
):
    """Insert a row into video_error_logs and optionally update videos.last_error_*.

    Args:
        update_last_error: If False, only insert into video_error_logs without
            overwriting videos.last_error_code/last_error_message. Use False for
            non-fatal warnings (e.g. PRODUCT_DATA_MISMATCH) that should not mask
            the real fatal error.
    """
    # Truncate long strings to avoid DB column overflow
    if error_message and len(error_message) > 2000:
        error_message = error_message[:2000] + "...(truncated)"
    if error_detail and len(error_detail) > 10000:
        error_detail = error_detail[:10000] + "...(truncated)"

    sql_insert = text("""
        INSERT INTO video_error_logs
            (video_id, error_code, error_step, error_message, error_detail, source)
        VALUES
            (:video_id, :error_code, :error_step, :error_message, :error_detail, :source)
    """)
    sql_update = text("""
        UPDATE videos
        SET last_error_code = :error_code,
            last_error_message = :error_message,
            updated_at = NOW()
        WHERE id = :video_id
    """)
    async with AsyncSessionLocal() as session:
        await session.execute(sql_insert, {
            "video_id": video_id,
            "error_code": error_code,
            "error_step": error_step,
            "error_message": error_message,
            "error_detail": error_detail,
            "source": source,
        })
        if update_last_error:
            await session.execute(sql_update, {
                "video_id": video_id,
                "error_code": error_code,
                "error_message": error_message,
            })
        await session.commit()


def insert_video_error_log_sync(
    video_id: str,
    error_code: str,
    error_step: str = None,
    error_message: str = None,
    error_detail: str = None,
    source: str = "worker",
    update_last_error: bool = True,
):
    """Synchronous wrapper for insert_video_error_log."""
    loop = get_event_loop()
    return loop.run_until_complete(
        insert_video_error_log(
            video_id=video_id,
            error_code=error_code,
            error_step=error_step,
            error_message=error_message,
            error_detail=error_detail,
            source=source,
            update_last_error=update_last_error,
        )
    )


async def get_video_error_logs(video_id: str, limit: int = 50):
    """Fetch error logs for a video, newest first."""
    sql = text("""
        SELECT id, video_id, error_code, error_step, error_message,
               error_detail, source, created_at
        FROM video_error_logs
        WHERE video_id = :video_id
        ORDER BY created_at DESC
        LIMIT :limit
    """)
    async with AsyncSessionLocal() as session:
        result = await session.execute(sql, {
            "video_id": video_id,
            "limit": limit,
        })
        rows = result.fetchall()
        return [dict(row._mapping) for row in rows]


def get_video_error_logs_sync(video_id: str, limit: int = 50):
    loop = get_event_loop()
    return loop.run_until_complete(get_video_error_logs(video_id, limit))


async def update_video_error_message(video_id: str, error_message: str):
    """Update the error_message column on the videos table."""
    sql = text("""
        UPDATE videos
        SET error_message = :msg,
            updated_at = NOW()
        WHERE id = :vid
    """)
    async with AsyncSessionLocal() as session:
        await session.execute(sql, {"vid": video_id, "msg": error_message})
        await session.commit()


def update_video_error_message_sync(video_id: str, error_message: str):
    loop = get_event_loop()
    loop.run_until_complete(update_video_error_message(video_id, error_message))


# ---------- human_sales_tags lookup for report enrichment ----------

async def get_phase_human_sales_tags(video_id: str, user_id: int) -> dict:
    """Return {phase_index: parsed_tags_list} for phases that have human_sales_tags."""
    sql = text("""
        SELECT phase_index, human_sales_tags
        FROM video_phases
        WHERE video_id = :video_id
          AND (user_id = :user_id)
          AND human_sales_tags IS NOT NULL
          AND human_sales_tags != '[]'
        ORDER BY phase_index ASC
    """)

    async with AsyncSessionLocal() as session:
        result = await session.execute(sql, {
            "video_id": video_id,
            "user_id": user_id,
        })
        rows = result.fetchall()

    out = {}
    for r in rows:
        raw = r.human_sales_tags
        if raw is None:
            continue
        if isinstance(raw, list):
            out[r.phase_index] = raw
        elif isinstance(raw, str):
            try:
                import json as _json
                parsed = _json.loads(raw)
                if isinstance(parsed, list):
                    out[r.phase_index] = parsed
            except Exception:
                pass
    return out


def get_phase_human_sales_tags_sync(video_id: str, user_id: int) -> dict:
    loop = get_event_loop()
    return loop.run_until_complete(
        get_phase_human_sales_tags(video_id, user_id)
    )


async def get_unusable_phases(video_id: str) -> dict:
    """Return {phase_index: {reason, comment}} for phases that have unusable clips."""
    sql = text("""
        SELECT DISTINCT
            CASE
                WHEN vc.phase_index ~ '^[0-9]+$' THEN CAST(vc.phase_index AS INTEGER)
                ELSE -1
            END AS phase_index,
            vc.unusable_reason,
            vc.unusable_comment
        FROM video_clips vc
        WHERE vc.video_id = :video_id
          AND COALESCE(vc.is_unusable, FALSE) = TRUE
        ORDER BY phase_index ASC
    """)

    async with AsyncSessionLocal() as session:
        result = await session.execute(sql, {"video_id": video_id})
        rows = result.fetchall()

    out = {}
    for r in rows:
        if r.phase_index >= 0:
            out[r.phase_index] = {
                "reason": r.unusable_reason or "unknown",
                "comment": getattr(r, "unusable_comment", None) or "",
            }
    return out


def get_unusable_phases_sync(video_id: str) -> dict:
    loop = get_event_loop()
    return loop.run_until_complete(get_unusable_phases(video_id))
# retrigger deploy


# ---------- Video Processing Logs (realtime AI log panel) ----------

async def update_video_processing_log(video_id: str, log_message: str, step: str = "", pct: int = 0):
    """Append a structured log entry to videos.processing_logs JSONB array.
    
    Uses CAST(:param AS jsonb) syntax to avoid asyncpg ::jsonb cast issue.
    Falls back gracefully if the column doesn't exist yet.
    """
    from datetime import datetime as _dt
    try:
        log_entry = json.dumps({
            "ts": _dt.now().strftime("%H:%M:%S"),
            "pct": pct,
            "step": step,
            "msg": log_message,
        })
        async with AsyncSessionLocal() as session:
            sql = text("""
                UPDATE videos
                SET processing_logs = COALESCE(processing_logs, CAST('[]' AS jsonb)) || CAST(:log_entry AS jsonb),
                    updated_at = now()
                WHERE id = CAST(:video_id AS uuid)
            """)
            await session.execute(sql, {
                "log_entry": log_entry,
                "video_id": video_id,
            })
            await session.commit()
    except Exception:
        # Column may not exist yet — silently ignore
        pass


def update_video_processing_log_sync(video_id: str, log_message: str, step: str = "", pct: int = 0):
    """Synchronous wrapper for update_video_processing_log."""
    loop = get_event_loop()
    return loop.run_until_complete(update_video_processing_log(video_id, log_message, step, pct))


async def reset_video_processing_logs(video_id: str):
    """Reset processing_logs to empty array at the start of analysis."""
    try:
        async with AsyncSessionLocal() as session:
            sql = text("""
                UPDATE videos
                SET processing_logs = CAST('[]' AS jsonb),
                    updated_at = now()
                WHERE id = CAST(:video_id AS uuid)
            """)
            await session.execute(sql, {"video_id": video_id})
            await session.commit()
    except Exception:
        pass


def reset_video_processing_logs_sync(video_id: str):
    """Synchronous wrapper for reset_video_processing_logs."""
    loop = get_event_loop()
    return loop.run_until_complete(reset_video_processing_logs(video_id))
