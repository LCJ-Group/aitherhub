import logging
import os
import time
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from app.api.v1.routes import routers as v1_routers
from app.core.config import configs
from app.core.container import Container
from app.core.request_id_middleware import RequestIdMiddleware, RequestIdFilter
from app.utils.class_object import singleton

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

        # Request ID middleware (must be added before CORS)
        self.app.add_middleware(RequestIdMiddleware)

        # CORS
        if configs.BACKEND_CORS_ORIGINS:
            self.app.add_middleware(
                CORSMiddleware,
                allow_origins=[str(origin) for origin in configs.BACKEND_CORS_ORIGINS],
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

        async with engine.begin() as conn:
            await conn.run_sync(
                LiveAnalysisJob.__table__.create,
                checkfirst=True,
            )
        logger.info("live_analysis_jobs table verified/created successfully")
    except Exception as e:
        logger.warning(f"Failed to ensure tables exist on startup: {e}")

    # Subtitle feedback & style migration
    try:
        from app.core.db import engine
        from sqlalchemy import text

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
            ]:
                try:
                    await conn.execute(text(col_sql))
                except Exception as _e:
                    logger.debug(f"DDL skipped (likely already exists): {_e}")
        logger.info("subtitle_feedback table & video_clips columns verified/created")
    except Exception as e:
        logger.warning(f"Failed to ensure subtitle tables on startup: {e}")

    # Video error logs table – stores every error occurrence per video
    try:
        from app.core.db import engine
        from sqlalchemy import text as _text

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
    except Exception as e:
        logger.warning(f"Failed to ensure video_error_logs table on startup: {e}")

    # ── Bug reports & Work logs tables ──
    try:
        from app.core.db import engine
        from sqlalchemy import text as _text

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
    except Exception as e:
        logger.warning(f"Failed to ensure bug_reports/work_logs tables on startup: {e}")

    # ── Feedback Loop tables: clip_feedback extensions, sales_confirmation, clip_edit_log ──
    try:
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
    except Exception as e:
        logger.warning(f"Failed to ensure feedback loop tables on startup: {e}")

    # ── lessons_learned: プロジェクトの永続記憶 ──
    try:
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
    except Exception as e:
        logger.warning(f"Failed to ensure lessons_learned table on startup: {e}")


@app.on_event("startup")
async def ensure_auto_video_jobs_table():
    """Ensure auto_video_jobs table exists and restore jobs from DB."""
    try:
        from app.core.db import engine
        from app.models.orm.auto_video_job import AutoVideoJob

        async with engine.begin() as conn:
            await conn.run_sync(
                AutoVideoJob.__table__.create,
                checkfirst=True,
            )
        logger.info("auto_video_jobs table verified/created successfully")
    except Exception as e:
        logger.warning(f"Failed to ensure auto_video_jobs table on startup: {e}")

    # Restore completed/errored jobs from DB into memory
    try:
        from app.services.auto_video_db import restore_jobs_to_memory
        from app.services.auto_video_pipeline_service import auto_video_jobs

        count = await restore_jobs_to_memory(auto_video_jobs)
        if count > 0:
            logger.info(f"Restored {count} auto video jobs from database")
        else:
            logger.info("No auto video jobs to restore from database")
    except Exception as e:
        logger.warning(f"Failed to restore auto video jobs on startup: {e}")


@app.on_event("startup")
async def restore_live_sessions():
    """Restore active live sessions from DB on startup."""
    try:
        from app.core.db import AsyncSessionLocal
        from app.services.live_event_service import restore_active_sessions

        async with AsyncSessionLocal() as db_session:
            count = await restore_active_sessions(db_session)
            if count > 0:
                logger.info(f"Restored {count} active live sessions from database")
            else:
                logger.info("No active live sessions to restore")
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
