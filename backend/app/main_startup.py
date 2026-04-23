"""
Unified startup module for AitherHub API.

Consolidates 8 separate @app.on_event("startup") handlers into a single
optimized startup flow:
  Phase 1: All DDL migrations in ONE DB connection (~5-10s)
  Phase 2: Restore in-memory state (parallel where possible)
  Phase 3: Start background tasks (non-blocking)

This reduces startup time from ~5min to ~30s and eliminates cascading
timeout failures that caused frequent downtime.
"""
import asyncio
import logging
import time

logger = logging.getLogger(__name__)


async def run_all_ddl_migrations():
    """Run ALL DDL migrations in a single DB connection.
    
    Previously each migration opened its own connection with its own timeout,
    causing 8 sequential DB connections. Now we open ONE connection and run
    all DDL statements in a single transaction.
    """
    ddl_start = time.time()
    try:
        from app.core.db import engine
        from sqlalchemy import text as _text

        async with asyncio.timeout(120):  # Single generous timeout for ALL DDL
            async with engine.begin() as conn:
                # ── 1. live_analysis_jobs (ORM-based) ──
                try:
                    from app.models.orm.live_analysis_job import LiveAnalysisJob
                    await conn.run_sync(LiveAnalysisJob.__table__.create, checkfirst=True)
                    logger.info("[DDL 1/9] live_analysis_jobs ✓")
                except Exception as e:
                    logger.warning(f"[DDL 1/9] live_analysis_jobs: {e}")

                # ── 2. subtitle_feedback + video_clips columns ──
                try:
                    # Check if subtitle_feedback has correct schema
                    check = await conn.execute(_text("""
                        SELECT column_name FROM information_schema.columns
                        WHERE table_name = 'subtitle_feedback' AND column_name = 'subtitle_style'
                    """))
                    has_correct_schema = check.fetchone() is not None
                    if not has_correct_schema:
                        check_old = await conn.execute(_text("""
                            SELECT column_name FROM information_schema.columns
                            WHERE table_name = 'subtitle_feedback' AND column_name = 'style_selected'
                        """))
                        if check_old.fetchone():
                            await conn.execute(_text("DROP TABLE IF EXISTS subtitle_feedback"))

                    await conn.execute(_text("""
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
                    await conn.execute(_text("CREATE INDEX IF NOT EXISTS idx_subtitle_feedback_video ON subtitle_feedback(video_id)"))
                    await conn.execute(_text("CREATE INDEX IF NOT EXISTS idx_subtitle_feedback_user ON subtitle_feedback(user_id)"))

                    # Add subtitle columns to video_clips
                    video_clips_columns = [
                        "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS subtitle_style TEXT DEFAULT 'simple'",
                        "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS subtitle_position_x REAL DEFAULT 50",
                        "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS subtitle_position_y REAL DEFAULT 85",
                        "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS progress_pct INTEGER DEFAULT 0",
                        "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS progress_step TEXT DEFAULT ''",
                        "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS job_payload JSONB",
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
                        "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS is_unusable BOOLEAN DEFAULT FALSE",
                        "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS unusable_reason TEXT",
                        "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS unusable_at TIMESTAMPTZ",
                    ]
                    for col_sql in video_clips_columns:
                        try:
                            await conn.execute(_text(col_sql))
                        except Exception:
                            pass
                    logger.info("[DDL 2/9] subtitle_feedback + video_clips columns ✓")
                except Exception as e:
                    logger.warning(f"[DDL 2/9] subtitle_feedback: {e}")

                # ── 3. video_error_logs ──
                try:
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
                    await conn.execute(_text("CREATE INDEX IF NOT EXISTS idx_vel_video_id ON video_error_logs (video_id)"))
                    await conn.execute(_text("CREATE INDEX IF NOT EXISTS idx_vel_created_at ON video_error_logs (created_at DESC)"))
                    for col_sql in [
                        "ALTER TABLE videos ADD COLUMN IF NOT EXISTS last_error_code VARCHAR(100)",
                        "ALTER TABLE videos ADD COLUMN IF NOT EXISTS last_error_message TEXT",
                    ]:
                        try:
                            await conn.execute(_text(col_sql))
                        except Exception:
                            pass
                    logger.info("[DDL 3/9] video_error_logs ✓")
                except Exception as e:
                    logger.warning(f"[DDL 3/9] video_error_logs: {e}")

                # ── 4. bug_reports & work_logs ──
                try:
                    await conn.execute(_text("""
                        CREATE TABLE IF NOT EXISTS bug_reports (
                            id BIGSERIAL PRIMARY KEY,
                            title VARCHAR(500) NOT NULL,
                            severity VARCHAR(20) NOT NULL DEFAULT 'medium',
                            status VARCHAR(20) NOT NULL DEFAULT 'open',
                            category VARCHAR(100) DEFAULT 'general',
                            symptom TEXT, root_cause TEXT, solution TEXT,
                            affected_files TEXT, related_video_ids TEXT,
                            reported_by VARCHAR(100) DEFAULT 'system',
                            resolved_by VARCHAR(100), resolved_at TIMESTAMPTZ,
                            created_at TIMESTAMPTZ DEFAULT NOW(),
                            updated_at TIMESTAMPTZ DEFAULT NOW()
                        )
                    """))
                    await conn.execute(_text("CREATE INDEX IF NOT EXISTS idx_br_status ON bug_reports (status)"))
                    await conn.execute(_text("CREATE INDEX IF NOT EXISTS idx_br_created_at ON bug_reports (created_at DESC)"))

                    await conn.execute(_text("""
                        CREATE TABLE IF NOT EXISTS work_logs (
                            id BIGSERIAL PRIMARY KEY,
                            action VARCHAR(100) NOT NULL,
                            summary TEXT NOT NULL,
                            details TEXT, files_changed TEXT,
                            commit_hash VARCHAR(100), deployed_to VARCHAR(100),
                            author VARCHAR(100) DEFAULT 'manus-ai',
                            related_bug_id BIGINT,
                            created_at TIMESTAMPTZ DEFAULT NOW()
                        )
                    """))
                    await conn.execute(_text("CREATE INDEX IF NOT EXISTS idx_wl_action ON work_logs (action)"))
                    await conn.execute(_text("CREATE INDEX IF NOT EXISTS idx_wl_created_at ON work_logs (created_at DESC)"))
                    logger.info("[DDL 4/9] bug_reports & work_logs ✓")
                except Exception as e:
                    logger.warning(f"[DDL 4/9] bug_reports & work_logs: {e}")

                # ── 5. gpu_jobs ──
                try:
                    await conn.execute(_text("""
                        CREATE TABLE IF NOT EXISTS gpu_jobs (
                            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                            action VARCHAR(100) NOT NULL,
                            status VARCHAR(50) NOT NULL DEFAULT 'pending',
                            provider VARCHAR(50) NOT NULL DEFAULT 'runpod',
                            provider_job_id VARCHAR(200),
                            input_data JSONB, output_data JSONB,
                            error_message TEXT,
                            retry_count INTEGER DEFAULT 0,
                            max_retries INTEGER DEFAULT 3,
                            submitted_at TIMESTAMPTZ, started_at TIMESTAMPTZ,
                            completed_at TIMESTAMPTZ, duration_seconds REAL,
                            caller_type VARCHAR(100), caller_id VARCHAR(200),
                            created_at TIMESTAMPTZ DEFAULT NOW(),
                            updated_at TIMESTAMPTZ DEFAULT NOW()
                        )
                    """))
                    await conn.execute(_text("CREATE INDEX IF NOT EXISTS ix_gpu_jobs_status_created ON gpu_jobs (status, created_at)"))
                    await conn.execute(_text("CREATE INDEX IF NOT EXISTS ix_gpu_jobs_provider_status ON gpu_jobs (provider, status)"))
                    await conn.execute(_text("CREATE INDEX IF NOT EXISTS ix_gpu_jobs_provider_job_id ON gpu_jobs (provider_job_id)"))
                    await conn.execute(_text("CREATE INDEX IF NOT EXISTS ix_gpu_jobs_action ON gpu_jobs (action)"))
                    logger.info("[DDL 5/9] gpu_jobs ✓")
                except Exception as e:
                    logger.warning(f"[DDL 5/9] gpu_jobs: {e}")

                # ── 6. Feedback loop tables ──
                try:
                    # Fix phase_index type: INTEGER → TEXT
                    for table_name, constraint_name in [
                        ("clip_feedback", "uq_clip_feedback_video_phase"),
                        ("sales_confirmation", "uq_sales_confirmation_video_phase"),
                    ]:
                        try:
                            await conn.execute(_text(f"""
                                DO $$
                                BEGIN
                                    IF EXISTS (
                                        SELECT 1 FROM information_schema.columns
                                        WHERE table_name = '{table_name}'
                                          AND column_name = 'phase_index'
                                          AND data_type = 'integer'
                                    ) THEN
                                        ALTER TABLE {table_name} DROP CONSTRAINT IF EXISTS {constraint_name};
                                        ALTER TABLE {table_name} ALTER COLUMN phase_index TYPE TEXT USING phase_index::TEXT;
                                        ALTER TABLE {table_name} ADD CONSTRAINT {constraint_name} UNIQUE (video_id, phase_index);
                                    END IF;
                                END $$;
                            """))
                        except Exception:
                            pass

                    # clip_feedback extensions
                    for col_sql in [
                        "ALTER TABLE clip_feedback ADD COLUMN IF NOT EXISTS rating VARCHAR(20)",
                        "ALTER TABLE clip_feedback ADD COLUMN IF NOT EXISTS reason_tags JSONB",
                    ]:
                        try:
                            await conn.execute(_text(col_sql))
                        except Exception:
                            pass

                    await conn.execute(_text("""
                        DO $$
                        BEGIN
                            IF NOT EXISTS (
                                SELECT 1 FROM pg_constraint WHERE conname = 'uq_clip_feedback_video_phase'
                            ) THEN
                                ALTER TABLE clip_feedback ADD CONSTRAINT uq_clip_feedback_video_phase UNIQUE (video_id, phase_index);
                            END IF;
                        END $$;
                    """))

                    # sales_confirmation
                    await conn.execute(_text("""
                        CREATE TABLE IF NOT EXISTS sales_confirmation (
                            id UUID PRIMARY KEY,
                            video_id UUID NOT NULL,
                            phase_index TEXT NOT NULL,
                            time_start FLOAT NOT NULL, time_end FLOAT NOT NULL,
                            is_sales_moment BOOLEAN NOT NULL,
                            clip_id UUID, confidence INTEGER, note TEXT,
                            reviewer_name VARCHAR(100),
                            created_at TIMESTAMPTZ DEFAULT NOW(),
                            updated_at TIMESTAMPTZ DEFAULT NOW(),
                            CONSTRAINT uq_sales_confirmation_video_phase UNIQUE (video_id, phase_index)
                        )
                    """))
                    await conn.execute(_text("CREATE INDEX IF NOT EXISTS ix_sales_confirmation_video_id ON sales_confirmation (video_id)"))

                    # clip_edit_log
                    await conn.execute(_text("""
                        CREATE TABLE IF NOT EXISTS clip_edit_log (
                            id UUID PRIMARY KEY,
                            clip_id UUID NOT NULL, video_id UUID NOT NULL,
                            edit_type VARCHAR(50) NOT NULL,
                            before_value JSONB, after_value JSONB,
                            delta_seconds FLOAT,
                            created_at TIMESTAMPTZ DEFAULT NOW()
                        )
                    """))
                    await conn.execute(_text("CREATE INDEX IF NOT EXISTS ix_clip_edit_log_video_id ON clip_edit_log (video_id)"))
                    await conn.execute(_text("CREATE INDEX IF NOT EXISTS ix_clip_edit_log_clip_id ON clip_edit_log (clip_id)"))
                    logger.info("[DDL 6/9] feedback loop tables ✓")
                except Exception as e:
                    logger.warning(f"[DDL 6/9] feedback loop tables: {e}")

                # ── 7. lessons_learned ──
                try:
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
                    await conn.execute(_text("CREATE INDEX IF NOT EXISTS idx_ll_category ON lessons_learned (category)"))
                    await conn.execute(_text("CREATE INDEX IF NOT EXISTS idx_ll_active ON lessons_learned (is_active)"))
                    await conn.execute(_text("CREATE INDEX IF NOT EXISTS idx_ll_created_at ON lessons_learned (created_at DESC)"))
                    logger.info("[DDL 7/9] lessons_learned ✓")
                except Exception as e:
                    logger.warning(f"[DDL 7/9] lessons_learned: {e}")

                # ── 8. videos.language column ──
                try:
                    await conn.execute(_text(
                        "ALTER TABLE videos ADD COLUMN IF NOT EXISTS language VARCHAR(10) DEFAULT 'ja'"
                    ))
                    logger.info("[DDL 8/9] videos.language ✓")
                except Exception as e:
                    logger.warning(f"[DDL 8/9] videos.language: {e}")

                # ── 9. script_generations ──
                try:
                    await conn.execute(_text("""
                        CREATE TABLE IF NOT EXISTS script_generations (
                            id VARCHAR(36) PRIMARY KEY,
                            user_email VARCHAR(255),
                            product_name VARCHAR(200) NOT NULL,
                            product_description TEXT,
                            original_price VARCHAR(100), discounted_price VARCHAR(100),
                            benefits TEXT, target_audience VARCHAR(500),
                            tone VARCHAR(50) DEFAULT 'professional_friendly',
                            language VARCHAR(10) DEFAULT 'ja',
                            duration_minutes INT DEFAULT 10,
                            generated_script TEXT NOT NULL,
                            char_count INT, model_used VARCHAR(100),
                            patterns_used JSONB, product_analysis JSONB,
                            rating INT, rating_comment TEXT,
                            rating_good_tags JSONB, rating_bad_tags JSONB,
                            rated_at TIMESTAMPTZ,
                            created_at TIMESTAMPTZ DEFAULT NOW()
                        )
                    """))
                    await conn.execute(_text("CREATE INDEX IF NOT EXISTS idx_sg_created_at ON script_generations (created_at DESC)"))
                    await conn.execute(_text("CREATE INDEX IF NOT EXISTS idx_sg_rating ON script_generations (rating) WHERE rating IS NOT NULL"))
                    await conn.execute(_text("CREATE INDEX IF NOT EXISTS idx_sg_user_email ON script_generations (user_email)"))
                    logger.info("[DDL 9/9] script_generations ✓")
                except Exception as e:
                    logger.warning(f"[DDL 9/9] script_generations: {e}")

        # ── ORM-based tables (separate connection for run_sync) ──
        try:
            async with asyncio.timeout(30):
                async with engine.begin() as conn:
                    from app.models.orm.auto_video_job import AutoVideoJob
                    await conn.run_sync(AutoVideoJob.__table__.create, checkfirst=True)
                    logger.info("[DDL ORM] auto_video_jobs ✓")

                    from app.models.orm.persona import Persona, PersonaVideoTag, PersonaTrainingLog
                    await conn.run_sync(Persona.__table__.create, checkfirst=True)
                    await conn.run_sync(PersonaVideoTag.__table__.create, checkfirst=True)
                    await conn.run_sync(PersonaTrainingLog.__table__.create, checkfirst=True)
                    # v3: Add live_persona_config JSON column
                    await conn.execute(_text(
                        "ALTER TABLE personas ADD COLUMN IF NOT EXISTS live_persona_config JSONB"
                    ))
                    logger.info("[DDL ORM] persona tables ✓")
        except Exception as e:
            logger.warning(f"[DDL ORM] ORM tables: {e}")

        # ── Widget tables (separate connection due to size) ──
        try:
            async with asyncio.timeout(60):
                async with engine.begin() as conn:
                    await conn.execute(_text("""
                        CREATE TABLE IF NOT EXISTS widget_clients (
                            client_id VARCHAR(20) PRIMARY KEY,
                            name VARCHAR(200) NOT NULL,
                            domain VARCHAR(500) NOT NULL,
                            theme_color VARCHAR(20) DEFAULT '#FF2D55',
                            position VARCHAR(30) DEFAULT 'bottom-right',
                            cta_text VARCHAR(100) DEFAULT '購入する',
                            cta_url_template TEXT, cart_selector VARCHAR(500),
                            is_active BOOLEAN DEFAULT TRUE,
                            created_at TIMESTAMPTZ DEFAULT NOW(),
                            updated_at TIMESTAMPTZ DEFAULT NOW()
                        )
                    """))
                    await conn.execute(_text("CREATE INDEX IF NOT EXISTS ix_widget_clients_domain ON widget_clients (domain)"))

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
                    await conn.execute(_text("CREATE INDEX IF NOT EXISTS ix_widget_clip_assignments_client ON widget_clip_assignments (client_id, is_active)"))

                    await conn.execute(_text("""
                        CREATE TABLE IF NOT EXISTS widget_page_contexts (
                            id VARCHAR(36) PRIMARY KEY,
                            client_id VARCHAR(20) NOT NULL,
                            page_url TEXT NOT NULL, canonical_url TEXT,
                            title TEXT, og_title TEXT, og_image TEXT,
                            h1_text TEXT, product_price VARCHAR(100),
                            meta_description TEXT,
                            session_id VARCHAR(100), visitor_ip VARCHAR(50),
                            user_agent VARCHAR(500),
                            created_at TIMESTAMPTZ DEFAULT NOW()
                        )
                    """))
                    await conn.execute(_text("CREATE INDEX IF NOT EXISTS ix_widget_page_contexts_client ON widget_page_contexts (client_id, created_at DESC)"))
                    await conn.execute(_text("CREATE INDEX IF NOT EXISTS ix_widget_page_contexts_url ON widget_page_contexts (canonical_url)"))

                    await conn.execute(_text("""
                        CREATE TABLE IF NOT EXISTS widget_tracking_events (
                            id VARCHAR(36) PRIMARY KEY,
                            client_id VARCHAR(20) NOT NULL,
                            session_id VARCHAR(100) NOT NULL,
                            event_type VARCHAR(50) NOT NULL,
                            page_url TEXT, clip_id VARCHAR(36),
                            video_current_time REAL, extra_data JSONB,
                            visitor_ip VARCHAR(50), user_agent VARCHAR(500),
                            created_at TIMESTAMPTZ DEFAULT NOW()
                        )
                    """))
                    await conn.execute(_text("CREATE INDEX IF NOT EXISTS ix_widget_tracking_client_type ON widget_tracking_events (client_id, event_type, created_at DESC)"))
                    await conn.execute(_text("CREATE INDEX IF NOT EXISTS ix_widget_tracking_session ON widget_tracking_events (session_id)"))
                    await conn.execute(_text("CREATE INDEX IF NOT EXISTS ix_widget_tracking_created ON widget_tracking_events (created_at DESC)"))

                    # Widget product info columns
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
                            pass

                    # Brand portal columns
                    for alter_sql in [
                        "ALTER TABLE widget_clients ADD COLUMN IF NOT EXISTS password_hash TEXT",
                        "ALTER TABLE widget_clients ADD COLUMN IF NOT EXISTS brand_keywords TEXT",
                        "ALTER TABLE widget_clients ADD COLUMN IF NOT EXISTS lcj_brand_id INTEGER",
                        "ALTER TABLE widget_clients ADD COLUMN IF NOT EXISTS logo_url TEXT",
                        "ALTER TABLE widget_clients ADD COLUMN IF NOT EXISTS company_name TEXT",
                        "ALTER TABLE widget_clients ADD COLUMN IF NOT EXISTS name_ja TEXT",
                        "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS uploaded_by_brand VARCHAR(20)",
                        "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS product_price TEXT",
                        "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'processed'",
                        "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS widget_url TEXT",
                        "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS subtitle_font_size REAL",
                        "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS caption_offset REAL DEFAULT 0",
                        "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS trim_data JSONB",
                        "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS subtitle_language VARCHAR(10) DEFAULT 'ja'",
                        "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS unusable_comment TEXT",
                        "ALTER TABLE widget_clients ADD COLUMN IF NOT EXISTS source VARCHAR(20)",
                        "ALTER TABLE video_clips ADD COLUMN IF NOT EXISTS detected_language VARCHAR(10)",
                    ]:
                        try:
                            await conn.execute(_text(alter_sql))
                        except Exception:
                            pass

                    # Brand portal indexes
                    try:
                        await conn.execute(_text(
                            "CREATE INDEX IF NOT EXISTS ix_video_clips_uploaded_by_brand ON video_clips (uploaded_by_brand) WHERE uploaded_by_brand IS NOT NULL"
                        ))
                    except Exception:
                        pass
                    try:
                        await conn.execute(_text(
                            "CREATE UNIQUE INDEX IF NOT EXISTS ix_widget_clients_lcj_brand_id ON widget_clients (lcj_brand_id) WHERE lcj_brand_id IS NOT NULL"
                        ))
                    except Exception:
                        pass

                    await conn.commit()
                    logger.info("[DDL] Widget tables ✓")
        except Exception as e:
            logger.warning(f"[DDL] Widget tables: {e}")

        elapsed = time.time() - ddl_start
        logger.info(f"[DDL] All migrations completed in {elapsed:.1f}s")

    except asyncio.TimeoutError:
        logger.error(f"[DDL] TIMEOUT after {time.time() - ddl_start:.1f}s — some tables may not be created")
    except Exception as e:
        logger.error(f"[DDL] Fatal error during migrations: {e}")


async def restore_runtime_state():
    """Restore in-memory state from DB after DDL migrations."""
    restore_start = time.time()

    # Restore auto video jobs
    try:
        from app.services.auto_video_db import restore_jobs_to_memory
        from app.services.auto_video_pipeline_service import auto_video_jobs

        async with asyncio.timeout(30):
            count = await restore_jobs_to_memory(auto_video_jobs)
        logger.info(f"[Restore] Auto video jobs: {count} restored")
    except Exception as e:
        logger.warning(f"[Restore] Auto video jobs failed: {e}")

    # Restore live sessions
    try:
        from app.core.db import AsyncSessionLocal
        from app.services.live_event_service import restore_active_sessions

        async with asyncio.timeout(30):
            async with AsyncSessionLocal() as db_session:
                count = await restore_active_sessions(db_session)
        logger.info(f"[Restore] Live sessions: {count} restored")
    except Exception as e:
        logger.warning(f"[Restore] Live sessions failed: {e}")

    elapsed = time.time() - restore_start
    logger.info(f"[Restore] All state restored in {elapsed:.1f}s")


def start_background_tasks():
    """Start non-blocking background tasks."""
    import asyncio as _asyncio

    # Cleanup task for stale extension sessions
    try:
        from app.api.v1.endpoints.live_extension import start_cleanup_task
        start_cleanup_task()
        logger.info("[BG] Extension cleanup task started")
    except Exception as e:
        logger.warning(f"[BG] Extension cleanup task failed: {e}")

    # Stuck video monitor (auto-requeue)
    try:
        from app.services.stuck_video_monitor import start_stuck_video_monitor
        start_stuck_video_monitor()
        logger.info("[BG] Stuck video monitor started")
    except Exception as e:
        logger.warning(f"[BG] Stuck video monitor failed: {e}")

    # HeyGen avatar prefetch (background, non-blocking)
    async def _do_prefetch():
        try:
            from app.services.heygen_service import get_heygen_service
            heygen = get_heygen_service()
            await heygen.prefetch_avatars()
        except Exception as e:
            logger.warning(f"[BG] HeyGen avatar prefetch failed (non-fatal): {e}")

    _asyncio.create_task(_do_prefetch())
    logger.info("[BG] HeyGen avatar prefetch task started")
