#!/usr/bin/env python3
"""
Run LiveAnalysis Pipeline — Subprocess Entry Point
====================================================
Called by queue_worker.py as a subprocess to run the LiveBoost
analysis pipeline for a single job.

Usage:
    python run_live_analysis.py \
        --job-id <uuid> \
        --video-id <video_id> \
        --email <email> \
        [--total-chunks <N>] \
        [--stream-source <source>]

Exit codes:
    0 = success
    1 = failure
    2 = input validation error
"""
import argparse
import asyncio
import logging
import os
import sys

# Ensure backend/ is on sys.path so we can import app.services.*
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKEND_DIR = os.path.join(PROJECT_ROOT, "backend")
sys.path.insert(0, BACKEND_DIR)
sys.path.insert(0, PROJECT_ROOT)

# BUILD 36b: Ensure venv site-packages are available (system Python may lack them)
_venv_lib = os.path.join(PROJECT_ROOT, ".venv", "lib")
if os.path.isdir(_venv_lib):
    for _d in sorted(os.listdir(_venv_lib), reverse=True):
        _sp = os.path.join(_venv_lib, _d, "site-packages")
        if os.path.isdir(_sp) and _sp not in sys.path:
            sys.path.insert(1, _sp)
            break

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("run_live_analysis")


def parse_args():
    parser = argparse.ArgumentParser(description="Run LiveAnalysis Pipeline")
    parser.add_argument("--job-id", required=True, help="Analysis job UUID")
    parser.add_argument("--video-id", required=True, help="Video ID")
    parser.add_argument("--email", required=True, help="User email")
    parser.add_argument("--total-chunks", type=int, default=None, help="Total chunk count")
    parser.add_argument("--stream-source", default="tiktok_live", help="Stream source type")
    return parser.parse_args()


async def main():
    args = parse_args()

    if not args.job_id or not args.video_id:
        logger.error("Missing required arguments: --job-id and --video-id")
        sys.exit(2)

    logger.info(
        f"[run_live_analysis] Starting: job={args.job_id} video={args.video_id} "
        f"chunks={args.total_chunks} source={args.stream_source}"
    )

    # Import after sys.path setup
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    from app.services.live_analysis_pipeline import LiveAnalysisPipeline

    # Setup database connection
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        logger.error("DATABASE_URL environment variable is required")
        sys.exit(1)

    # Convert postgres:// to postgresql+asyncpg://
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    # asyncpg does not accept 'sslmode' — strip it and pass SSLContext via connect_args
    import ssl as _ssl
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

    parsed = urlparse(database_url)
    qp = parse_qs(parsed.query)
    connect_args = {}
    for key in ("sslmode", "ssl"):
        if key in qp:
            mode = qp.pop(key)[0]
            if mode == "require":
                ctx = _ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = _ssl.CERT_NONE
                connect_args["ssl"] = ctx
            elif mode in ("verify-ca", "verify-full"):
                connect_args["ssl"] = _ssl.create_default_context()
    new_query = urlencode(qp, doseq=True)
    database_url = urlunparse(parsed._replace(query=new_query))

    # Debug: log sanitized URL (mask password)
    _safe_url = database_url
    if "@" in _safe_url:
        _pre, _post = _safe_url.split("@", 1)
        _safe_url = _pre.rsplit(":", 1)[0] + ":***@" + _post
    logger.info(f"[run_live_analysis] DB URL (sanitized): {_safe_url}")
    logger.info(f"[run_live_analysis] connect_args keys: {list(connect_args.keys())}")

    engine = create_async_engine(database_url, echo=False, connect_args=connect_args)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with session_factory() as db:
            pipeline = LiveAnalysisPipeline(db)
            results = await pipeline.run(
                job_id=args.job_id,
                video_id=args.video_id,
                email=args.email,
                total_chunks=args.total_chunks,
                stream_source=args.stream_source,
            )

            sales_count = results.get("total_sales_detected", 0)
            clip_count = len(results.get("clip_candidates", []))
            logger.info(
                f"[run_live_analysis] Completed: job={args.job_id} "
                f"sales_moments={sales_count} clips={clip_count}"
            )

    except Exception as exc:
        from app.services.live_analysis_pipeline import ChunkNotFoundError
        if isinstance(exc, ChunkNotFoundError):
            logger.error(f"[run_live_analysis] CHUNK_NOT_FOUND (non-retryable): job={args.job_id} error={exc}")
            await engine.dispose()
            sys.exit(2)  # exit 2 = skip, don't retry
        logger.error(f"[run_live_analysis] Failed: job={args.job_id} error={exc}")
        sys.exit(1)
    finally:
        await engine.dispose()

    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
# Worker deploy trigger - 20260312T205239
# Worker deploy trigger - 20260312T180354
# Worker deploy trigger - BUILD 33 20260312T232700
