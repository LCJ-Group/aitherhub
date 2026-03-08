import logging
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

        # Health check
        @self.app.get("/")
        async def root():
            return {"status": "service is working"}

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
