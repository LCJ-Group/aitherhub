import asyncio
import logging
import os
import time
from fastapi import FastAPI, Request
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware

from app.api.v1.routes import routers as v1_routers
from app.core.config import configs
from app.core.container import Container
from app.core.request_id_middleware import RequestIdMiddleware, RequestIdFilter
from app.utils.class_object import singleton


class WidgetCORSMiddleware(BaseHTTPMiddleware):
    """Allow any origin for /widget/ endpoints (SaaS widget loaded on client sites)."""

    async def dispatch(self, request: Request, call_next):
        # Only apply to widget endpoints
        if "/widget/" in request.url.path:
            origin = request.headers.get("origin", "*")
            # Handle preflight
            if request.method == "OPTIONS":
                return Response(
                    status_code=200,
                    headers={
                        "Access-Control-Allow-Origin": origin,
                        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                        "Access-Control-Allow-Headers": "Content-Type, X-Admin-Key",
                        "Access-Control-Max-Age": "86400",
                    },
                )
            response = await call_next(request)
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Admin-Key"
            return response
        return await call_next(request)

# RequestIdFilter を root logger に追加して、全ログに request_id/video_id/user_id を自動付与
_rid_filter = RequestIdFilter()
logging.getLogger().addFilter(_rid_filter)

logger = logging.getLogger(__name__)

# Track startup time for diagnostics
_startup_time = time.time()

# Build-time version info (injected by CI/CD)
_GIT_COMMIT = os.environ.get("GIT_COMMIT_SHA", "unknown")
_GIT_BRANCH = os.environ.get("GIT_BRANCH", "unknown")
_BUILD_TIME = os.environ.get("BUILD_TIME", "unknown")
_DEPLOY_TIME = os.environ.get("DEPLOY_TIME", "unknown")


@singleton
class AppCreator:
    def __init__(self):
        # Init FastAPI
        self.app = FastAPI(
            title=configs.PROJECT_NAME,
            version="0.0.1",
            openapi_url=f"{configs.API_V1_STR}/openapi.json",
        )

        # Init DI container (legacy — kept for backward compatibility)
        self.container = Container()
        self.container.wire(modules=[__name__])
        # NOTE: db provider was removed from Container in b959117.
        # The project now uses app.core.db (async sessions) directly.

        # ── Middleware order ──
        # Starlette add_middleware uses LIFO: last added = outermost wrapper.
        # We want: Request → CORSMiddleware → RequestIdMiddleware → app
        # So CORSMiddleware must be the OUTERMOST (added LAST).
        #
        # IMPORTANT: BaseHTTPMiddleware (used by RequestIdMiddleware) has known
        # compatibility issues with CORSMiddleware when it wraps CORS.
        # By adding CORS LAST, it becomes the outermost middleware and
        # can properly handle OPTIONS preflight and add CORS headers.

        # CORS origins – always enabled with hardcoded origins as fallback
        _REQUIRED_ORIGINS = [
            "https://www.aitherhub.com",
            "https://aitherhub.com",
        ]
        _DEV_ORIGINS = [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
        ]
        _config_origins = [str(o) for o in configs.BACKEND_CORS_ORIGINS] if configs.BACKEND_CORS_ORIGINS else []
        _all_origins = list(dict.fromkeys(_REQUIRED_ORIGINS + _DEV_ORIGINS + _config_origins))
        logger.info(f"CORS origins: {_all_origins}")

        # 1. Add RequestIdMiddleware FIRST (will be inner / closer to app)
        self.app.add_middleware(RequestIdMiddleware)

        # 2. Add WidgetCORSMiddleware for /widget/ endpoints (any origin)
        self.app.add_middleware(WidgetCORSMiddleware)

        # 3. Add CORSMiddleware LAST (will be outer / first to process requests)
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=_all_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # ── Health check endpoints ──

        @self.app.get("/")
        async def root():
            return {"status": "service is working"}

        @self.app.get("/version")
        async def version():
            """Return current deployment version info for verification."""
            return {
                "app": "aitherhub-api",
                "commit": _GIT_COMMIT,
                "branch": _GIT_BRANCH,
                "built_at": _BUILD_TIME,
                "deployed_at": _DEPLOY_TIME,
                "uptime_seconds": round(time.time() - _startup_time, 1),
            }

        @self.app.get("/health/ready")
        async def health_ready():
            """
            Readiness probe for Azure App Service Health Check.
            Verifies that the app is fully started and can serve requests.
            Configure Azure Health Check path to: /health/ready
            """
            from app.core.db import get_async_session

            checks = {
                "app": "ok",
                "database": "unknown",
                "uptime_seconds": round(time.time() - _startup_time, 1),
            }

            # Verify database connectivity
            try:
                async for session in get_async_session():
                    from sqlalchemy import text
                    result = await session.execute(text("SELECT 1"))
                    result.scalar()
                    checks["database"] = "ok"
                    break
            except Exception as e:
                checks["database"] = f"error: {type(e).__name__}"
                logger.warning(f"Health check: database connectivity failed: {e}")
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=503,
                    content={"status": "degraded", "checks": checks}
                )

            return {"status": "healthy", "checks": checks}

        # API v1 routes
        self.app.include_router(
            v1_routers,
            prefix=configs.API_V1_STR,
        )


app_creator = AppCreator()
app = app_creator.app
# NOTE: db is now accessed via app.core.db (async sessions), not via container
container = app_creator.container


@app.on_event("startup")
async def ensure_tables_exist():
    """Ensure live_analysis_jobs table exists in the database."""
    try:
        from app.core.db import engine
        from app.models.orm.live_analysis_job import LiveAnalysisJob

        async with asyncio.timeout(30):
            async with engine.begin() as conn:
                await conn.run_sync(
                    LiveAnalysisJob.__table__.create,
                    checkfirst=True,
                )
        logger.info("live_analysis_jobs table verified/created successfully")
    except asyncio.TimeoutError:
        logger.warning("Timeout (30s) while ensuring live_analysis_jobs table on startup")
    except Exception as e:
        logger.warning(f"Failed to ensure tables exist on startup: {e}")

    # Subtitle feedback & style migration
    try:
        from app.core.db import engine
        from sqlalchemy import text

        async with asyncio.timeout(60):
          async with engine.begin() as conn:
            # Drop old table if schema is wrong (missing subtitle_style column)
            try:
                check = await conn.execute(text("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'subtitle_feedback' AND column_name = 'subtitle_style'
                """))
                has_correct_schema = check.fetchone() is not None
                if not has_correct_schema:
                    # Check if old table exists
                    check_old = await conn.execute(text("""
                        SELECT column_name FROM information_schema.columns
                        WHERE table_name = 'subtitle_feedback' AND column_name = 'style_selected'
                    """))
                    if check_old.fetchone():
                        logger.info("Dropping old subtitle_feedback table with wrong schema")
                        await conn.execute(text("DROP TABLE IF EXISTS subtitle_feedback"))
            except Exception as _e:
                logger.debug(f"Schema check skipped: {_e}")

            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS subtitle_feedback (
                    id SERIAL PRIMARY KEY,
                    video_id TEXT NOT NULL,
                    clip_id TEXT,
                    user_id TEXT,
                    subtitle_style TEXT DEFAULT 'box',
                    vote TEXT,
                    tags JSONB DEFAULT '[]'::jsonb,
                    position_x REAL DEFAULT 50,
                    position_y REAL DEFAULT 85,
                    ai_recommended_style TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_subtitle_feedback_video
                ON subtitle_feedback(video_id)
            """))
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_subtitle_feedback_user
                ON subtitle_feedback(user_id)
            """))
            # Add subtitle columns to video_clips if not exist
            for col_sql in [
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS subtitle_style TEXT DEFAULT 'simple'",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS subtitle_position_x REAL DEFAULT 50",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS subtitle_position_y REAL DEFAULT 85",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS progress_pct INTEGER DEFAULT 0",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS progress_step TEXT DEFAULT ''",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS job_payload JSONB",
                # ── Clip DB columns (searchable metadata) ──
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS transcript_text TEXT",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS product_name TEXT",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS product_category TEXT",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS tags JSONB",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS is_sold BOOLEAN",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS gmv REAL DEFAULT 0",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS viewer_count INTEGER DEFAULT 0",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS liver_name TEXT",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS stream_date DATE",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS thumbnail_url TEXT",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS duration_sec REAL",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS embedding_id TEXT",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS phase_description TEXT",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS cta_score INTEGER",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS importance_score REAL",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS enriched_at TIMESTAMPTZ",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS exported_url TEXT",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS exported_at TIMESTAMPTZ",
                # ── Unusable clip marking ──
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS is_unusable BOOLEAN DEFAULT FALSE",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS unusable_reason TEXT",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS unusable_at TIMESTAMPTZ",
            ]:
                try:
                    await conn.execute(text(col_sql))
                except Exception as _e:
                    logger.debug(f"DDL skipped (likely already exists): {_e}")
        logger.info("subtitle_feedback table & video_clips columns verified/created")
    except asyncio.TimeoutError:
        logger.warning("Timeout (60s) while ensuring subtitle tables on startup")
    except Exception as e:
        logger.warning(f"Failed to ensure subtitle tables on startup: {e}")

    # Video error logs table – stores every error occurrence per video
    try:
        from app.core.db import engine
        from sqlalchemy import text as _text

        async with asyncio.timeout(30):
          async with engine.begin() as conn:
            await conn.execute(_text("""
                CREATE TABLE IF NOT EXISTS video_error_logs (
                    id BIGSERIAL PRIMARY KEY,
                    video_id UUID NOT NULL,
                    error_code VARCHAR(100) NOT NULL,
                    error_step VARCHAR(100),
                    error_message TEXT,
                    error_detail TEXT,
                    source VARCHAR(50) DEFAULT 'worker',
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            await conn.execute(_text("""
                CREATE INDEX IF NOT EXISTS idx_vel_video_id
                ON video_error_logs (video_id)
            """))
            await conn.execute(_text("""
                CREATE INDEX IF NOT EXISTS idx_vel_created_at
                ON video_error_logs (created_at DESC)
            """))
            # Also add last_error_code / last_error_message columns to videos
            for col_sql in [
                "ALTER TABLE videos ADD COLUMN IF NOT EXISTS last_error_code VARCHAR(100)",
                "ALTER TABLE videos ADD COLUMN IF NOT EXISTS last_error_message TEXT",
            ]:
                try:
                    await conn.execute(_text(col_sql))
                except Exception as _e:
                    logger.debug(f"DDL skipped (likely already exists): {_e}")
        logger.info("video_error_logs table & videos error columns verified/created")
    except asyncio.TimeoutError:
        logger.warning("Timeout (30s) while ensuring video_error_logs table on startup")
    except Exception as e:
        logger.warning(f"Failed to ensure video_error_logs table on startup: {e}")

    # ── Bug reports & Work logs tables ──
    try:
        from app.core.db import engine
        from sqlalchemy import text as _text

        async with asyncio.timeout(30):
          async with engine.begin() as conn:
            # bug_reports: 問題→原因→解決策の記録
            await conn.execute(_text("""
                CREATE TABLE IF NOT EXISTS bug_reports (
                    id BIGSERIAL PRIMARY KEY,
                    title VARCHAR(500) NOT NULL,
                    severity VARCHAR(20) NOT NULL DEFAULT 'medium',
                    status VARCHAR(20) NOT NULL DEFAULT 'open',
                    category VARCHAR(100) DEFAULT 'general',
                    symptom TEXT,
                    root_cause TEXT,
                    solution TEXT,
                    affected_files TEXT,
                    related_video_ids TEXT,
                    reported_by VARCHAR(100) DEFAULT 'system',
                    resolved_by VARCHAR(100),
                    resolved_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            await conn.execute(_text("""
                CREATE INDEX IF NOT EXISTS idx_br_status ON bug_reports (status)
            """))
            await conn.execute(_text("""
                CREATE INDEX IF NOT EXISTS idx_br_created_at ON bug_reports (created_at DESC)
            """))

            # work_logs: デプロイ・修正・作業の履歴
            await conn.execute(_text("""
                CREATE TABLE IF NOT EXISTS work_logs (
                    id BIGSERIAL PRIMARY KEY,
                    action VARCHAR(100) NOT NULL,
                    summary TEXT NOT NULL,
                    details TEXT,
                    files_changed TEXT,
                    commit_hash VARCHAR(100),
                    deployed_to VARCHAR(100),
                    author VARCHAR(100) DEFAULT 'manus-ai',
                    related_bug_id BIGINT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            await conn.execute(_text("""
                CREATE INDEX IF NOT EXISTS idx_wl_action ON work_logs (action)
            """))
            await conn.execute(_text("""
                CREATE INDEX IF NOT EXISTS idx_wl_created_at ON work_logs (created_at DESC)
            """))
        logger.info("bug_reports & work_logs tables verified/created")
    except asyncio.TimeoutError:
        logger.warning("Timeout (30s) while ensuring bug_reports/work_logs tables on startup")
    except Exception as e:
        logger.warning(f"Failed to ensure bug_reports/work_logs tables on startup: {e}")

    # ── GPU Jobs table: persistent job queue for GPU processing ──
    try:
        from app.core.db import engine
        from sqlalchemy import text as _text

        async with asyncio.timeout(30):
          async with engine.begin() as conn:
            await conn.execute(_text("""
                CREATE TABLE IF NOT EXISTS gpu_jobs (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    action VARCHAR(100) NOT NULL,
                    status VARCHAR(50) NOT NULL DEFAULT 'pending',
                    provider VARCHAR(50) NOT NULL DEFAULT 'runpod',
                    provider_job_id VARCHAR(200),
                    input_data JSONB,
                    output_data JSONB,
                    error_message TEXT,
                    retry_count INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 3,
                    submitted_at TIMESTAMPTZ,
                    started_at TIMESTAMPTZ,
                    completed_at TIMESTAMPTZ,
                    duration_seconds REAL,
                    caller_type VARCHAR(100),
                    caller_id VARCHAR(200),
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            await conn.execute(_text("""
                CREATE INDEX IF NOT EXISTS ix_gpu_jobs_status_created
                ON gpu_jobs (status, created_at)
            """))
            await conn.execute(_text("""
                CREATE INDEX IF NOT EXISTS ix_gpu_jobs_provider_status
                ON gpu_jobs (provider, status)
            """))
            await conn.execute(_text("""
                CREATE INDEX IF NOT EXISTS ix_gpu_jobs_provider_job_id
                ON gpu_jobs (provider_job_id)
            """))
            await conn.execute(_text("""
                CREATE INDEX IF NOT EXISTS ix_gpu_jobs_action
                ON gpu_jobs (action)
            """))
        logger.info("gpu_jobs table verified/created")
    except asyncio.TimeoutError:
        logger.warning("Timeout (30s) while ensuring gpu_jobs table on startup")
    except Exception as e:
        logger.warning(f"Failed to ensure gpu_jobs table on startup: {e}")

    # ── Feedback Loop tables: clip_feedback extensions, sales_confirmation, clip_edit_log ──
    try:
        async with asyncio.timeout(60):
          async with engine.begin() as conn:
            # Fix phase_index type: INTEGER → TEXT (Moment clips use string IDs like 'moment_strong_test4')
            try:
                await conn.execute(_text("""
                    DO $$
                    BEGIN
                        IF EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name = 'clip_feedback'
                              AND column_name = 'phase_index'
                              AND data_type = 'integer'
                        ) THEN
                            -- Drop the unique constraint first
                            ALTER TABLE clip_feedback DROP CONSTRAINT IF EXISTS uq_clip_feedback_video_phase;
                            -- Change column type
                            ALTER TABLE clip_feedback ALTER COLUMN phase_index TYPE TEXT USING phase_index::TEXT;
                            -- Re-add the unique constraint
                            ALTER TABLE clip_feedback ADD CONSTRAINT uq_clip_feedback_video_phase UNIQUE (video_id, phase_index);
                            RAISE NOTICE 'clip_feedback.phase_index changed from INTEGER to TEXT';
                        END IF;
                    END $$;
                """))
            except Exception as _e:
                logger.debug(f"clip_feedback phase_index type change skipped: {_e}")

            try:
                await conn.execute(_text("""
                    DO $$
                    BEGIN
                        IF EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_name = 'sales_confirmation'
                              AND column_name = 'phase_index'
                              AND data_type = 'integer'
                        ) THEN
                            -- Drop the unique constraint first
                            ALTER TABLE sales_confirmation DROP CONSTRAINT IF EXISTS uq_sales_confirmation_video_phase;
                            -- Change column type
                            ALTER TABLE sales_confirmation ALTER COLUMN phase_index TYPE TEXT USING phase_index::TEXT;
                            -- Re-add the unique constraint
                            ALTER TABLE sales_confirmation ADD CONSTRAINT uq_sales_confirmation_video_phase UNIQUE (video_id, phase_index);
                            RAISE NOTICE 'sales_confirmation.phase_index changed from INTEGER to TEXT';
                        END IF;
                    END $$;
                """))
            except Exception as _e:
                logger.debug(f"sales_confirmation phase_index type change skipped: {_e}")

            # Ensure clip_feedback has rating + reason_tags columns
            for col_sql in [
                "ALTER TABLE clip_feedback ADD COLUMN IF NOT EXISTS rating VARCHAR(20)",
                "ALTER TABLE clip_feedback ADD COLUMN IF NOT EXISTS reason_tags JSONB",
            ]:
                try:
                    await conn.execute(_text(col_sql))
                except Exception as _e:
                    logger.debug(f"DDL skipped (likely already exists): {_e}")

            # Ensure UNIQUE constraint on (video_id, phase_index) for ON CONFLICT
            await conn.execute(_text("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'uq_clip_feedback_video_phase'
                    ) THEN
                        ALTER TABLE clip_feedback
                        ADD CONSTRAINT uq_clip_feedback_video_phase
                        UNIQUE (video_id, phase_index);
                    END IF;
                END $$;
            """))

            # Create sales_confirmation table
            await conn.execute(_text("""
                CREATE TABLE IF NOT EXISTS sales_confirmation (
                    id UUID PRIMARY KEY,
                    video_id UUID NOT NULL,
                    phase_index TEXT NOT NULL,
                    time_start FLOAT NOT NULL,
                    time_end FLOAT NOT NULL,
                    is_sales_moment BOOLEAN NOT NULL,
                    clip_id UUID,
                    confidence INTEGER,
                    note TEXT,
                    reviewer_name VARCHAR(100),
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    CONSTRAINT uq_sales_confirmation_video_phase UNIQUE (video_id, phase_index)
                )
            """))
            await conn.execute(_text("""
                CREATE INDEX IF NOT EXISTS ix_sales_confirmation_video_id
                ON sales_confirmation (video_id)
            """))

            # Create clip_edit_log table
            await conn.execute(_text("""
                CREATE TABLE IF NOT EXISTS clip_edit_log (
                    id UUID PRIMARY KEY,
                    clip_id UUID NOT NULL,
                    video_id UUID NOT NULL,
                    edit_type VARCHAR(50) NOT NULL,
                    before_value JSONB,
                    after_value JSONB,
                    delta_seconds FLOAT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            await conn.execute(_text("""
                CREATE INDEX IF NOT EXISTS ix_clip_edit_log_video_id
                ON clip_edit_log (video_id)
            """))
            await conn.execute(_text("""
                CREATE INDEX IF NOT EXISTS ix_clip_edit_log_clip_id
                ON clip_edit_log (clip_id)
            """))
        logger.info("Feedback loop tables (clip_feedback extensions, sales_confirmation, clip_edit_log) verified/created")
    except asyncio.TimeoutError:
        logger.warning("Timeout (60s) while ensuring feedback loop tables on startup")
    except Exception as e:
        logger.warning(f"Failed to ensure feedback loop tables on startup: {e}")

    # ── lessons_learned: プロジェクトの永続記憶 ──
    try:
        async with asyncio.timeout(30):
          async with engine.begin() as conn:
            await conn.execute(_text("""
                CREATE TABLE IF NOT EXISTS lessons_learned (
                    id BIGSERIAL PRIMARY KEY,
                    category VARCHAR(50) NOT NULL DEFAULT 'lesson',
                    title VARCHAR(500) NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    related_files TEXT DEFAULT '',
                    related_feature VARCHAR(200) DEFAULT '',
                    source_bug_id BIGINT,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            await conn.execute(_text("""
                CREATE INDEX IF NOT EXISTS idx_ll_category ON lessons_learned (category)
            """))
            await conn.execute(_text("""
                CREATE INDEX IF NOT EXISTS idx_ll_active ON lessons_learned (is_active)
            """))
            await conn.execute(_text("""
                CREATE INDEX IF NOT EXISTS idx_ll_created_at ON lessons_learned (created_at DESC)
            """))
        logger.info("lessons_learned table verified/created")
    except asyncio.TimeoutError:
        logger.warning("Timeout (30s) while ensuring lessons_learned table on startup")
    except Exception as e:
        logger.warning(f"Failed to ensure lessons_learned table on startup: {e}")


@app.on_event("startup")
async def ensure_videos_language_column():
    """Ensure 'language' column exists on videos table.
    
    The column was added in migration 20260416_add_video_language but Azure Web App
    deployment does not run alembic upgrade head, so we ensure it here.
    """
    try:
        from app.core.db import engine
        from sqlalchemy import text as _text

        async with asyncio.timeout(30):
            async with engine.begin() as conn:
                await conn.execute(_text(
                    "ALTER TABLE videos ADD COLUMN IF NOT EXISTS language VARCHAR(10) DEFAULT 'ja'"
                ))
        logger.info("videos.language column verified/created successfully")
    except asyncio.TimeoutError:
        logger.warning("Timeout (30s) while ensuring videos.language column on startup")
    except Exception as e:
        logger.warning(f"Failed to ensure videos.language column on startup: {e}")


@app.on_event("startup")
async def ensure_auto_video_jobs_table():
    """Ensure auto_video_jobs table exists and restore jobs from DB."""
    try:
        from app.core.db import engine
        from app.models.orm.auto_video_job import AutoVideoJob

        async with asyncio.timeout(30):
            async with engine.begin() as conn:
                await conn.run_sync(
                    AutoVideoJob.__table__.create,
                    checkfirst=True,
                )
        logger.info("auto_video_jobs table verified/created successfully")
    except asyncio.TimeoutError:
        logger.warning("Timeout (30s) while ensuring auto_video_jobs table on startup")
    except Exception as e:
        logger.warning(f"Failed to ensure auto_video_jobs table on startup: {e}")

    # Restore completed/errored jobs from DB into memory
    try:
        from app.services.auto_video_db import restore_jobs_to_memory
        from app.services.auto_video_pipeline_service import auto_video_jobs

        async with asyncio.timeout(30):
            count = await restore_jobs_to_memory(auto_video_jobs)
        if count > 0:
            logger.info(f"Restored {count} auto video jobs from database")
        else:
            logger.info("No auto video jobs to restore from database")
    except asyncio.TimeoutError:
        logger.warning("Timeout (30s) while restoring auto video jobs on startup")
    except Exception as e:
        logger.warning(f"Failed to restore auto video jobs on startup: {e}")


@app.on_event("startup")
async def restore_live_sessions():
    """Restore active live sessions from DB on startup."""
    try:
        from app.core.db import AsyncSessionLocal
        from app.services.live_event_service import restore_active_sessions

        async with asyncio.timeout(30):
            async with AsyncSessionLocal() as db_session:
                count = await restore_active_sessions(db_session)
            if count > 0:
                logger.info(f"Restored {count} active live sessions from database")
            else:
                logger.info("No active live sessions to restore")
    except asyncio.TimeoutError:
        logger.warning("Timeout (30s) while restoring live sessions on startup")
    except Exception as e:
        logger.warning(f"Failed to restore live sessions on startup: {e}")

    # Start background cleanup task for stale extension sessions
    try:
        from app.api.v1.endpoints.live_extension import start_cleanup_task
        start_cleanup_task()
    except Exception as e:
        logger.warning(f"Failed to start cleanup task: {e}")

    # Start background monitor for stuck videos (auto-requeue)
    try:
        from app.services.stuck_video_monitor import start_stuck_video_monitor
        start_stuck_video_monitor()
    except Exception as e:
        logger.warning(f"Failed to start stuck video monitor: {e}")


@app.on_event("startup")
async def ensure_persona_tables():
    """Ensure persona-related tables exist on startup."""
    try:
        from app.core.db import engine
        from app.models.orm.persona import Persona, PersonaVideoTag, PersonaTrainingLog

        async with asyncio.timeout(30):
            async with engine.begin() as conn:
                await conn.run_sync(Persona.__table__.create, checkfirst=True)
                await conn.run_sync(PersonaVideoTag.__table__.create, checkfirst=True)
                await conn.run_sync(PersonaTrainingLog.__table__.create, checkfirst=True)
        logger.info("Persona tables verified/created successfully")
    except asyncio.TimeoutError:
        logger.warning("Timeout (30s) while ensuring persona tables on startup")
    except Exception as e:
        logger.warning(f"Failed to ensure persona tables on startup: {e}")


@app.on_event("startup")
async def prefetch_heygen_avatars():
    """Pre-fetch HeyGen avatar list to warm the cache.
    
    HeyGen API v2/avatars is very slow on first call (~120s).
    By pre-fetching during startup in the background, the first
    frontend request will hit the cache and respond instantly.
    """
    import asyncio as _asyncio

    async def _do_prefetch():
        try:
            from app.services.heygen_service import get_heygen_service
            heygen = get_heygen_service()
            await heygen.prefetch_avatars()
        except Exception as e:
            logger.warning(f"HeyGen avatar prefetch failed (non-fatal): {e}")

    # Run in background so it doesn't block other startup tasks
    _asyncio.create_task(_do_prefetch())
    logger.info("HeyGen avatar prefetch task started in background")


@app.on_event("startup")
async def ensure_script_generations_table():
    """Ensure script_generations table exists for script scoring/learning."""
    try:
        from app.core.db import engine
        from sqlalchemy import text as _text

        async with asyncio.timeout(30):
          async with engine.begin() as conn:
            await conn.execute(_text("""
                CREATE TABLE IF NOT EXISTS script_generations (
                    id VARCHAR(36) PRIMARY KEY,
                    user_email VARCHAR(255),
                    product_name VARCHAR(200) NOT NULL,
                    product_description TEXT,
                    original_price VARCHAR(100),
                    discounted_price VARCHAR(100),
                    benefits TEXT,
                    target_audience VARCHAR(500),
                    tone VARCHAR(50) DEFAULT 'professional_friendly',
                    language VARCHAR(10) DEFAULT 'ja',
                    duration_minutes INT DEFAULT 10,
                    generated_script TEXT NOT NULL,
                    char_count INT,
                    model_used VARCHAR(100),
                    patterns_used JSONB,
                    product_analysis JSONB,
                    rating INT,
                    rating_comment TEXT,
                    rating_good_tags JSONB,
                    rating_bad_tags JSONB,
                    rated_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            await conn.execute(_text("""
                CREATE INDEX IF NOT EXISTS idx_sg_created_at ON script_generations (created_at DESC)
            """))
            await conn.execute(_text("""
                CREATE INDEX IF NOT EXISTS idx_sg_rating ON script_generations (rating) WHERE rating IS NOT NULL
            """))
            await conn.execute(_text("""
                CREATE INDEX IF NOT EXISTS idx_sg_user_email ON script_generations (user_email)
            """))
        logger.info("script_generations table verified/created successfully")
    except asyncio.TimeoutError:
        logger.warning("Timeout (30s) while ensuring script_generations table on startup")
    except Exception as e:
        logger.warning(f"Failed to ensure script_generations table on startup: {e}")


@app.on_event("startup")
async def ensure_widget_tables():
    """Ensure widget-related tables exist for GTM widget system."""
    try:
        from app.core.db import engine
        from sqlalchemy import text as _text

        async with asyncio.timeout(60):
          async with engine.begin() as conn:
            # ── widget_clients: stores client (brand) configurations ──
            await conn.execute(_text("""
                CREATE TABLE IF NOT EXISTS widget_clients (
                    client_id VARCHAR(20) PRIMARY KEY,
                    name VARCHAR(200) NOT NULL,
                    domain VARCHAR(500) NOT NULL,
                    theme_color VARCHAR(20) DEFAULT '#FF2D55',
                    position VARCHAR(30) DEFAULT 'bottom-right',
                    cta_text VARCHAR(100) DEFAULT '購入する',
                    cta_url_template TEXT,
                    cart_selector VARCHAR(500),
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            await conn.execute(_text("""
                CREATE INDEX IF NOT EXISTS ix_widget_clients_domain
                ON widget_clients (domain)
            """))

            # ── widget_clip_assignments: which clips are assigned to which client ──
            await conn.execute(_text("""
                CREATE TABLE IF NOT EXISTS widget_clip_assignments (
                    id VARCHAR(36) PRIMARY KEY,
                    client_id VARCHAR(20) NOT NULL REFERENCES widget_clients(client_id),
                    clip_id VARCHAR(36) NOT NULL,
                    page_url_pattern TEXT,
                    sort_order INTEGER DEFAULT 0,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    CONSTRAINT uq_widget_clip_client UNIQUE (client_id, clip_id)
                )
            """))
            await conn.execute(_text("""
                CREATE INDEX IF NOT EXISTS ix_widget_clip_assignments_client
                ON widget_clip_assignments (client_id, is_active)
            """))

            # ── widget_page_contexts: Hack 1 — DOM auto-scraped data ──
            await conn.execute(_text("""
                CREATE TABLE IF NOT EXISTS widget_page_contexts (
                    id VARCHAR(36) PRIMARY KEY,
                    client_id VARCHAR(20) NOT NULL,
                    page_url TEXT NOT NULL,
                    canonical_url TEXT,
                    title TEXT,
                    og_title TEXT,
                    og_image TEXT,
                    h1_text TEXT,
                    product_price VARCHAR(100),
                    meta_description TEXT,
                    session_id VARCHAR(100),
                    visitor_ip VARCHAR(50),
                    user_agent VARCHAR(500),
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            await conn.execute(_text("""
                CREATE INDEX IF NOT EXISTS ix_widget_page_contexts_client
                ON widget_page_contexts (client_id, created_at DESC)
            """))
            await conn.execute(_text("""
                CREATE INDEX IF NOT EXISTS ix_widget_page_contexts_url
                ON widget_page_contexts (canonical_url)
            """))

            # ── widget_tracking_events: Hack 3 — Shadow Tracking events ──
            await conn.execute(_text("""
                CREATE TABLE IF NOT EXISTS widget_tracking_events (
                    id VARCHAR(36) PRIMARY KEY,
                    client_id VARCHAR(20) NOT NULL,
                    session_id VARCHAR(100) NOT NULL,
                    event_type VARCHAR(50) NOT NULL,
                    page_url TEXT,
                    clip_id VARCHAR(36),
                    video_current_time REAL,
                    extra_data JSONB,
                    visitor_ip VARCHAR(50),
                    user_agent VARCHAR(500),
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            await conn.execute(_text("""
                CREATE INDEX IF NOT EXISTS ix_widget_tracking_client_type
                ON widget_tracking_events (client_id, event_type, created_at DESC)
            """))
            await conn.execute(_text("""
                CREATE INDEX IF NOT EXISTS ix_widget_tracking_session
                ON widget_tracking_events (session_id)
            """))
            await conn.execute(_text("""
                CREATE INDEX IF NOT EXISTS ix_widget_tracking_created
                ON widget_tracking_events (created_at DESC)
            """))

            # ── Add product info columns to widget_clip_assignments (Phase 1: link-based product card) ──
            for col_sql in [
                "ALTER TABLE widget_clip_assignments ADD COLUMN IF NOT EXISTS product_name TEXT",
                "ALTER TABLE widget_clip_assignments ADD COLUMN IF NOT EXISTS product_price TEXT",
                "ALTER TABLE widget_clip_assignments ADD COLUMN IF NOT EXISTS product_image_url TEXT",
                "ALTER TABLE widget_clip_assignments ADD COLUMN IF NOT EXISTS product_url TEXT",
                "ALTER TABLE widget_clip_assignments ADD COLUMN IF NOT EXISTS product_cart_url TEXT",
            ]:
                try:
                    await conn.execute(_text(col_sql))
                except Exception:
                    pass  # Column already exists

        # ── Add brand portal columns ──
            for alter_sql in [
                "ALTER TABLE widget_clients ADD COLUMN IF NOT EXISTS password_hash TEXT",
                "ALTER TABLE widget_clients ADD COLUMN IF NOT EXISTS brand_keywords TEXT",
                "ALTER TABLE widget_clients ADD COLUMN IF NOT EXISTS lcj_brand_id INTEGER",
                "ALTER TABLE widget_clients ADD COLUMN IF NOT EXISTS logo_url TEXT",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS uploaded_by_brand VARCHAR(20)",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS product_price TEXT",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'processed'",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS widget_url TEXT",
                # ── Clip editor auto-save columns ──
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS subtitle_font_size REAL",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS caption_offset REAL DEFAULT 0",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS trim_data JSONB",
                "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS subtitle_language VARCHAR(10) DEFAULT 'ja'",
            ]:
                try:
                    await conn.execute(_text(alter_sql))
                except Exception as e:
                    logger.warning(f"ALTER TABLE (brand portal): {e}")

            # Create index for brand uploads
            try:
                await conn.execute(_text(
                    "CREATE INDEX IF NOT EXISTS ix_video_clips_uploaded_by_brand ON video_clips (uploaded_by_brand) WHERE uploaded_by_brand IS NOT NULL"
                ))
            except Exception as e:
                logger.warning(f"Index creation (brand portal): {e}")

            # Create unique index for LCJ brand sync
            try:
                await conn.execute(_text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_widget_clients_lcj_brand_id ON widget_clients (lcj_brand_id) WHERE lcj_brand_id IS NOT NULL"
                ))
            except Exception as e:
                logger.warning(f"Index creation (lcj_brand_id): {e}")

            await conn.commit()
        logger.info("Widget tables (widget_clients, widget_clip_assignments, widget_page_contexts, widget_tracking_events) verified/created")
    except asyncio.TimeoutError:
        logger.warning("Timeout (60s) while ensuring widget tables on startup")
    except Exception as e:
        logger.warning(f"Failed to ensure widget tables on startup: {e}")
