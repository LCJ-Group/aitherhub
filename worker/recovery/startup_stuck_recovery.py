"""
Startup Stuck Video Recovery
==============================
On worker startup, detect videos stuck in processing (STEP_* or 'uploaded')
that have no active worker and re-enqueue them via the Azure queue.

This is a safety net that runs ONCE at startup, independent of the API
server's stuck_video_monitor. It catches videos that:
  - Were being processed when the worker was killed (deploy/crash)
  - Were never enqueued due to SAS URL generation failures
  - Were stuck because the API monitor was not running

The recovery generates fresh SAS URLs and enqueues jobs directly,
bypassing the API server entirely. This means it works even if the
API server is down.

Key design decisions:
  - Runs synchronously at startup (blocks until complete)
  - Uses its own DB engine (not shared with main worker)
  - Limits to 50 videos per startup to avoid overwhelming the queue
  - Increments dequeue_count to prevent infinite retry loops
  - Records all actions to video_error_logs for observability
"""

import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("worker.startup_recovery")

# Configuration
STUCK_THRESHOLD_HOURS = 2       # Videos stuck for more than 2 hours
MAX_RETRIES = 5                 # Don't retry videos that have been retried too many times
BATCH_LIMIT = 50                # Max videos to recover per startup
SAS_EXPIRY_MINUTES = 1440       # 24 hours


def recover_stuck_on_startup():
    """
    Detect and re-enqueue stuck videos at worker startup.
    Runs synchronously (blocks until complete).
    """
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(_async_recover())
        if result["total_found"] > 0:
            logger.info(
                "[startup-recovery] Complete: found=%d retried=%d failed=%d skipped=%d",
                result["total_found"], result["retried"],
                result["failed"], result["skipped"],
            )
        else:
            logger.info("[startup-recovery] No stuck videos found.")
    except Exception as e:
        logger.error("[startup-recovery] Failed: %s", e)
    finally:
        loop.close()


async def _async_recover():
    """Async implementation of stuck video recovery."""
    from shared.config import DATABASE_URL, prepare_database_url
    from shared.config import AZURE_STORAGE_CONNECTION_STRING
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import text

    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")

    cleaned_url, connect_args = prepare_database_url(DATABASE_URL)
    engine = create_async_engine(
        cleaned_url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=3,
        pool_recycle=300,
        echo=False,
        connect_args=connect_args,
    )

    factory = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    result = {"total_found": 0, "retried": 0, "failed": 0, "skipped": 0}

    try:
        async with factory() as db:
            # Use naive datetime to avoid asyncpg offset-naive vs offset-aware mismatch
            threshold = datetime.utcnow() - timedelta(hours=STUCK_THRESHOLD_HOURS)

            # Find stuck videos: STEP_* or uploaded, not updated recently,
            # no active worker, under retry limit
            sql = text("""
                SELECT v.id, v.original_filename, v.status, v.user_id,
                       v.dequeue_count, v.upload_type,
                       u.email as user_email
                FROM videos v
                LEFT JOIN users u ON v.user_id = u.id
                WHERE (v.status IN ('uploaded', 'QUEUED') OR v.status LIKE 'STEP_%%')
                  AND v.status != 'completed'
                  AND v.updated_at < :threshold
                  AND (v.worker_claimed_at IS NULL
                       OR v.worker_claimed_at < :threshold)
                  AND COALESCE(v.dequeue_count, 0) < :max_retries
                ORDER BY v.updated_at ASC
                LIMIT :batch_limit
            """)
            rows_result = await db.execute(sql, {
                "threshold": threshold,
                "max_retries": MAX_RETRIES,
                "batch_limit": BATCH_LIMIT,
            })
            stuck_rows = rows_result.fetchall()
            result["total_found"] = len(stuck_rows)

            if not stuck_rows:
                return result

            logger.info(
                "[startup-recovery] Found %d stuck video(s), processing...",
                len(stuck_rows),
            )

            for row in stuck_rows:
                video_id = str(row.id)
                upload_type = row.upload_type or ""

                # Skip live_boost videos (different pipeline)
                if upload_type == "live_boost":
                    result["skipped"] += 1
                    continue

                try:
                    # Generate fresh SAS URL
                    email = row.user_email or "unknown@unknown.com"
                    filename = row.original_filename

                    from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions
                    blob_service = BlobServiceClient.from_connection_string(
                        AZURE_STORAGE_CONNECTION_STRING
                    )

                    # Determine container and blob path
                    container_name = "videos"
                    blob_name = f"{email}/{video_id}/{filename}"

                    # Check if blob exists
                    blob_client = blob_service.get_blob_client(
                        container=container_name, blob=blob_name
                    )
                    try:
                        blob_client.get_blob_properties()
                    except Exception:
                        # Try alternate path without email
                        blob_name = f"{video_id}/{filename}"
                        blob_client = blob_service.get_blob_client(
                            container=container_name, blob=blob_name
                        )
                        try:
                            blob_client.get_blob_properties()
                        except Exception:
                            logger.warning(
                                "[startup-recovery] Blob not found for %s (%s), skipping",
                                video_id, filename,
                            )
                            result["skipped"] += 1
                            continue

                    # Generate SAS token
                    account_name = None
                    account_key = None
                    for part in AZURE_STORAGE_CONNECTION_STRING.split(";"):
                        if part.startswith("AccountName="):
                            account_name = part.split("=", 1)[1]
                        elif part.startswith("AccountKey="):
                            account_key = part.split("=", 1)[1]

                    sas_token = generate_blob_sas(
                        account_name=account_name,
                        container_name=container_name,
                        blob_name=blob_name,
                        account_key=account_key,
                        permission=BlobSasPermissions(read=True),
                        expiry=datetime.now(timezone.utc) + timedelta(minutes=SAS_EXPIRY_MINUTES),
                    )
                    download_url = f"https://{account_name}.blob.core.windows.net/{container_name}/{blob_name}?{sas_token}"

                    # Update video status
                    resume_status = row.status if row.status.startswith("STEP_") else "STEP_0_EXTRACT_FRAMES"
                    await db.execute(
                        text("""
                            UPDATE videos
                            SET status = :status,
                                step_progress = 0,
                                error_message = NULL,
                                enqueue_status = NULL,
                                enqueue_error = NULL,
                                last_error_code = NULL,
                                last_error_message = NULL,
                                worker_claimed_at = NULL,
                                dequeue_count = COALESCE(dequeue_count, 0) + 1,
                                updated_at = NOW()
                            WHERE id = :vid
                        """),
                        {"vid": video_id, "status": resume_status},
                    )
                    await db.commit()

                    # Enqueue to Azure queue directly
                    from shared.queue.client import get_queue_client
                    queue_client = get_queue_client()
                    import base64
                    payload = json.dumps({
                        "video_id": video_id,
                        "blob_url": download_url,
                        "original_filename": filename,
                    }, ensure_ascii=False)
                    encoded = base64.b64encode(payload.encode("utf-8")).decode("utf-8")
                    msg = queue_client.send_message(encoded)

                    # Update enqueue evidence
                    try:
                        await db.execute(
                            text("""
                                UPDATE videos
                                SET enqueue_status = 'OK',
                                    queue_message_id = :msg_id,
                                    enqueue_error = NULL
                                WHERE id = :vid
                            """),
                            {
                                "vid": video_id,
                                "msg_id": str(msg.id) if msg else None,
                            },
                        )
                        await db.commit()
                    except Exception:
                        try:
                            await db.rollback()
                        except Exception:
                            pass

                    # Record to error logs
                    try:
                        await db.execute(
                            text("""
                                INSERT INTO video_error_logs
                                    (video_id, error_code, error_step, error_message, source)
                                VALUES
                                    (:vid, 'STARTUP_RECOVERY', :step, :msg, 'worker')
                            """),
                            {
                                "vid": video_id,
                                "step": row.status or "UNKNOWN",
                                "msg": f"Worker startup recovery: was {row.status}, "
                                       f"requeued as {resume_status}. "
                                       f"dequeue_count={row.dequeue_count or 0}+1",
                            },
                        )
                        await db.commit()
                    except Exception:
                        try:
                            await db.rollback()
                        except Exception:
                            pass

                    logger.info(
                        "[startup-recovery] Requeued: %s (%s) was=%s now=%s",
                        video_id, filename, row.status, resume_status,
                    )
                    result["retried"] += 1

                except Exception as e:
                    logger.warning(
                        "[startup-recovery] Failed to recover %s: %s",
                        video_id, e,
                    )
                    result["failed"] += 1
                    try:
                        await db.rollback()
                    except Exception:
                        pass

    finally:
        await engine.dispose()

    return result
