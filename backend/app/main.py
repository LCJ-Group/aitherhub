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
                        "Access-Control-Allow-Headers": "Content-Type, X-Admin-Key, Authorization",
                        "Access-Control-Max-Age": "86400",
                    },
                )
            response = await call_next(request)
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Admin-Key, Authorization"
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

        # Middleware order (LIFO: last added = outermost = first to process):
        # Request → WidgetCORSMiddleware → CORSMiddleware → RequestIdMiddleware → app
        #
        # WidgetCORSMiddleware MUST be outermost so it can intercept /widget/
        # preflight (OPTIONS) requests before CORSMiddleware rejects unknown origins.

        # 1. Add RequestIdMiddleware FIRST (innermost)
        self.app.add_middleware(RequestIdMiddleware)

        # 2. Add CORSMiddleware (handles non-widget CORS)
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=_all_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # 3. Add WidgetCORSMiddleware LAST (outermost — intercepts /widget/ before CORSMiddleware)
        self.app.add_middleware(WidgetCORSMiddleware)

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


# ── OGP proxy for /v/{clip_id} — serves OGP HTML to crawlers, redirects browsers to SPA ──
@app.get("/v/{clip_id}")
async def ogp_proxy(clip_id: str, request: Request):
    """Proxy /v/{clip_id} to the OGP endpoint for SNS crawler support.
    Browsers get redirected to the SPA frontend.
    Crawlers get server-rendered HTML with proper OGP meta tags."""
    from fastapi.responses import HTMLResponse, RedirectResponse
    from app.core.db import get_async_session
    from app.api.v1.endpoints.widget import _get_share_clip_meta_impl, _escape_html

    user_agent = (request.headers.get("user-agent") or "").lower()
    crawler_keywords = [
        "bot", "crawler", "spider", "facebookexternalhit", "twitterbot",
        "slackbot", "discordbot", "linebot", "linkedinbot", "whatsapp",
        "telegrambot", "applebot", "googlebot", "bingbot", "yandex",
        "pinterest", "redditbot", "embedly", "quora", "outbrain",
        "vkshare", "skypeuripreview", "nuzzel", "w3c_validator",
    ]
    is_crawler = any(kw in user_agent for kw in crawler_keywords)

    if not is_crawler:
        return RedirectResponse(
            url=f"https://www.aitherhub.com/v/{clip_id}",
            status_code=302,
        )

    # Crawler → render OGP HTML
    try:
        async for db in get_async_session():
            meta = await _get_share_clip_meta_impl(clip_id, db)
            break
    except Exception:
        return HTMLResponse(
            content="<html><head><title>Not Found</title></head><body>Video not found</body></html>",
            status_code=404,
        )

    og = meta.get("og", {})
    title = _escape_html(og.get("title") or "AitherHub Video")
    description = _escape_html(og.get("description") or "")
    image = _escape_html(og.get("image") or "")
    video = _escape_html(og.get("video") or "")
    url = _escape_html(og.get("url") or f"https://www.aitherhub.com/v/{clip_id}")

    html = f"""<!DOCTYPE html>
<html lang="ja" prefix="og: https://ogp.me/ns#">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:type" content="video.other">
<meta property="og:url" content="{url}">
<meta property="og:site_name" content="AitherHub">
{"<meta property='og:image' content='" + image + "'>" if image else ""}
{"<meta property='og:image:width' content='1200'>" if image else ""}
{"<meta property='og:image:height' content='630'>" if image else ""}
{"<meta property='og:video' content='" + video + "'>" if video else ""}
{"<meta property='og:video:type' content='video/mp4'>" if video else ""}
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{description}">
{"<meta name='twitter:image' content='" + image + "'>" if image else ""}
<link rel="canonical" href="{url}">
</head>
<body>
<h1>{title}</h1>
<p>{description}</p>
<p><a href="{url}">動画を見る</a></p>
</body>
</html>"""

    return HTMLResponse(content=html, status_code=200)


@app.on_event("startup")
async def unified_startup():
    """Single unified startup: run ALL DDL migrations and restore tasks.
    
    Consolidates 8 separate startup events into one to:
    - Open DB connection only once (not 8 times)
    - Reduce total startup time from ~5min to ~30s
    - Prevent cascading timeout failures
    """
    startup_start = time.time()
    logger.info("=== Unified startup BEGIN ===")

    from app.main_startup import (
        run_all_ddl_migrations,
        restore_runtime_state,
        start_background_tasks,
    )

    # Phase 1: All DDL migrations in a single DB connection
    await run_all_ddl_migrations()

    # Phase 2: Restore in-memory state
    await restore_runtime_state()

    # Phase 3: Background tasks (non-blocking)
    start_background_tasks()

    elapsed = time.time() - startup_start
    logger.info(f"=== Unified startup DONE in {elapsed:.1f}s ===")
