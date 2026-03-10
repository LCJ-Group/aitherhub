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

        # Init DI container & DB
        self.container = Container()
        self.container.wire(modules=[__name__])
        self.db = self.container.db()
        # self.db.create_database()

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
db = app_creator.db
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
