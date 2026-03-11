from typing import List
import json
import uuid as uuid_module
import asyncio
from datetime import datetime, timedelta, timezone

from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, select
from loguru import logger

from app.schema.video_schema import (
    RenameVideoRequest,
    RenameVideoResponse,
    DeleteVideoResponse,
    VideoResponse,
    LiveCaptureRequest,
    LiveCaptureResponse,
    LiveCheckResponse,
)
from app.services.video_service import VideoService
from app.repository.video_repository import VideoRepository
from app.core.dependencies import get_db, get_current_user
from app.utils.video_progress import calculate_progress, get_status_message
from app.core.container import Container
from app.models.orm.video import Video

router = APIRouter(
    prefix="/videos",
    tags=["videos"],
)

# Initialize service (could be injected via DI container)
video_service = VideoService()


import os as _os

_BLOB_HOST = _os.getenv("AZURE_BLOB_HOST", "https://aitherhub.blob.core.windows.net")
_CDN_HOST = _os.getenv("CDN_HOST", "https://cdn.aitherhub.com")


def _replace_blob_url_to_cdn(url: str) -> str:
    """Replace blob storage domain with CDN domain if applicable."""
    if url and isinstance(url, str):
        return url.replace(_BLOB_HOST, _CDN_HOST)
    return url





@router.get("/user/{user_id}", response_model=List[VideoResponse])
async def get_videos_by_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    Return list of videos for the given `user_id`.

    This endpoint requires authentication and only allows a user to fetch their own videos.
    """
    try:
        # Enforce that a user can only access their own videos
        if current_user and current_user.get("id") != user_id:
            raise HTTPException(status_code=403, detail="Forbidden")

        video_repo = VideoRepository(lambda: db)
        videos = await video_repo.get_videos_by_user(user_id=user_id)

        return [VideoResponse.from_orm(v) for v in videos]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch videos: {exc}")




@router.get("/user/{user_id}/with-clips")
async def get_videos_by_user_with_clips(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    Return list of videos for the given `user_id` with clip counts.
    This is used by the sidebar to show clip availability indicators.
    """
    try:
        if current_user and current_user.get("id") != user_id:
            raise HTTPException(status_code=403, detail="Forbidden")

        # Get videos with clip counts + sales/duration summary + memo count in a single query
        sql = text("""
            SELECT v.id, v.original_filename, v.status,
                   v.upload_type, v.created_at, v.updated_at,
                   COALESCE(c.clip_count, 0) as clip_count,
                   COALESCE(c.completed_count, 0) as completed_clip_count,
                   p.total_gmv,
                   p.max_time_end,
                   COALESCE(m.memo_count, 0) as memo_count,
                   v.top_products as top_products_json
            FROM videos v
            LEFT JOIN (
                SELECT video_id,
                       COUNT(DISTINCT phase_index) as clip_count,
                       COUNT(DISTINCT CASE WHEN status = 'completed' THEN phase_index END) as completed_count
                FROM video_clips
                GROUP BY video_id
            ) c ON v.id = c.video_id
            LEFT JOIN (
                SELECT video_id,
                       SUM(COALESCE(gmv, 0)) as total_gmv,
                       MAX(time_end) as max_time_end
                FROM video_phases
                GROUP BY video_id
            ) p ON v.id = p.video_id
            LEFT JOIN (
                SELECT video_id,
                       COUNT(*) as memo_count
                FROM video_phases
                WHERE (user_comment IS NOT NULL AND user_comment != '')
                   OR (user_rating IS NOT NULL AND user_rating > 0)
                GROUP BY video_id
            ) m ON v.id = m.video_id
            WHERE (v.user_id = :user_id OR v.user_id IS NULL)
            ORDER BY v.created_at DESC
        """)
        result = await db.execute(sql, {"user_id": user_id})
        rows = result.fetchall()

        import json as _json

        videos = []
        for row in rows:
            vid = str(row.id)
            # Parse cached top_products from videos table
            top_prods = []
            if row.top_products_json:
                try:
                    top_prods = _json.loads(row.top_products_json)
                except (ValueError, TypeError):
                    top_prods = []
            videos.append({
                "id": vid,
                "original_filename": row.original_filename,
                "status": row.status,
                "upload_type": row.upload_type,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                "clip_count": row.clip_count,
                "completed_clip_count": row.completed_clip_count,
                "total_gmv": float(row.total_gmv) if row.total_gmv and float(row.total_gmv) > 0 else None,
                "stream_duration": float(row.max_time_end) if row.max_time_end else None,
                "memo_count": row.memo_count,
                "top_products": top_prods,
            })

        return videos

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to fetch videos with clips: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch videos: {exc}")


@router.delete("/{video_id}", response_model=DeleteVideoResponse)
async def delete_video(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Delete a video and its related data (only owner can delete)."""
    try:
        user_id = current_user["id"]
        video_repo = VideoRepository(lambda: db)

        # Delete ALL related records first to avoid FK constraint violations
        # Order matters: delete child tables before parent tables
        # Use safe_delete to skip tables that may not exist yet
        tables_to_delete = [
            # Level 3: grandchild tables (FK to child tables)
            "DELETE FROM speech_segments WHERE audio_chunk_id IN (SELECT id FROM audio_chunks WHERE video_id = :vid)",
            "DELETE FROM frame_analysis_results WHERE frame_id IN (SELECT id FROM video_frames WHERE video_id = :vid)",
            # Level 2: child tables with video_id FK
            "DELETE FROM video_frames WHERE video_id = :vid",
            "DELETE FROM video_product_exposures WHERE video_id = :vid",
            "DELETE FROM video_clips WHERE video_id = :vid",
            "DELETE FROM audio_chunks WHERE video_id = :vid",
            "DELETE FROM chats WHERE video_id = :vid",
            "DELETE FROM group_best_phases WHERE video_id = :vid",
            "DELETE FROM phase_insights WHERE video_id = :vid",
            "DELETE FROM video_phases WHERE video_id = :vid",
            "DELETE FROM video_insights WHERE video_id = :vid",
            "DELETE FROM processing_jobs WHERE video_id = :vid",
            "DELETE FROM reports WHERE video_id = :vid",
            "DELETE FROM video_processing_state WHERE video_id = :vid",
            # Structure tables
            "DELETE FROM video_structure_group_best_videos WHERE video_id = :vid",
            "DELETE FROM video_structure_group_members WHERE video_id = :vid",
            "DELETE FROM video_structure_features WHERE video_id = :vid",
        ]

        for sql in tables_to_delete:
            try:
                await db.execute(text(sql), {"vid": video_id})
            except Exception as table_err:
                # Skip if table doesn't exist (e.g., migration not yet applied)
                logger.warning(f"Skipping delete for non-existent table: {table_err}")
                await db.rollback()
                # Re-start transaction for next delete
                continue

        await db.commit()

        # Delete the video record
        deleted = await video_repo.delete_video(video_id=video_id, user_id=user_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Video not found or not owned by user")

        return DeleteVideoResponse(id=video_id, message="Video deleted successfully")
    except HTTPException:
        raise
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to delete video: {exc}")


@router.patch("/{video_id}/rename", response_model=RenameVideoResponse)
async def rename_video(
    video_id: str,
    payload: RenameVideoRequest,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Rename a video (only owner can rename)."""
    try:
        user_id = current_user["id"]
        video_repo = VideoRepository(lambda: db)
        video = await video_repo.rename_video(
            video_id=video_id, user_id=user_id, new_name=payload.name
        )
        if not video:
            raise HTTPException(status_code=404, detail="Video not found or not owned by user")

        return RenameVideoResponse(
            id=str(video.id),
            original_filename=video.original_filename,
            message="Video renamed successfully",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to rename video: {exc}")


@router.get("/{video_id}/status/stream")
async def stream_video_status(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    Stream video processing status updates via Server-Sent Events (SSE).

    This endpoint provides real-time status updates for video processing.
    It polls the database every 2 seconds and sends status changes to the client.
    The stream automatically closes when processing reaches DONE or ERROR status.
    Supports long-running videos up to 4 hours with heartbeat messages every 30 seconds.

    Args:
        video_id: UUID of the video to monitor
        db: Database session
        current_user: Authenticated user

    Returns:
        StreamingResponse with SSE events containing:
        - status: Current processing status
        - progress: Progress percentage (0-100)
        - message: User-friendly Japanese status message
        - updated_at: Timestamp of last update
        - heartbeat: Boolean indicating heartbeat message (sent every 30 seconds)

    Example SSE events:
        data: {"video_id": "...", "status": "STEP_3_TRANSCRIBE_AUDIO", "progress": 40, "message": "音声書き起こし中...", "updated_at": "2026-01-20T..."}
        data: {"heartbeat": true, "timestamp": "2026-01-20T...", "poll_count": 15}
    """

    async def event_generator():
        last_status = None
        last_step_progress = None
        poll_count = 0
        max_polls = 7200  # 4 hours max for long videos (7200 * 2 seconds = 14400 seconds = 4 hours)

        try:
            # Verify video exists and ownership
            video_repo = VideoRepository(lambda: db)
            video = await video_repo.get_video_by_id(video_id)

            if not video:
                yield f"data: {json.dumps({'error': 'Video not found'})}\n\n"
                return

            if current_user and current_user.get("id") != video.user_id:
                yield f"data: {json.dumps({'error': 'Forbidden'})}\n\n"
                return

            # Stream status updates
            while poll_count < max_polls:
                try:
                    # Refresh video data
                    video = await video_repo.get_video_by_id(video_id)

                    if not video:
                        yield f"data: {json.dumps({'error': 'Video not found'})}\n\n"
                        break

                    current_status = video.status
                    current_step_progress = getattr(video, 'step_progress', None) or 0

                    # Send update if status changed OR step_progress changed
                    if current_status != last_status or current_step_progress != last_step_progress:
                        progress = calculate_progress(current_status)
                        message = get_status_message(current_status)

                        payload = {
                            "video_id": str(video.id),
                            "status": current_status,
                            "progress": progress,
                            "step_progress": current_step_progress,
                            "message": message,
                            "updated_at": video.updated_at.isoformat() if video.updated_at else None,
                            "created_at": video.created_at.isoformat() if video.created_at else None,
                            "server_now": datetime.utcnow().isoformat(),
                            # Enqueue & worker evidence
                            "enqueue_status": getattr(video, 'enqueue_status', None),
                            "queue_enqueued_at": video.queue_enqueued_at.isoformat() if getattr(video, 'queue_enqueued_at', None) else None,
                            "enqueue_error": getattr(video, 'enqueue_error', None),
                            "worker_claimed_at": video.worker_claimed_at.isoformat() if getattr(video, 'worker_claimed_at', None) else None,
                            "dequeue_count": getattr(video, 'dequeue_count', None),
                        }

                        yield f"data: {json.dumps(payload)}\n\n"
                        last_status = current_status
                        last_step_progress = current_step_progress

                        logger.info(f"SSE: Video {video_id} status={current_status} step_progress={current_step_progress}%")

                    # Send heartbeat every 30 seconds (15 * 2 seconds) to keep connection alive
                    if poll_count > 0 and poll_count % 15 == 0:
                        heartbeat_payload = {
                            "heartbeat": True,
                            "timestamp": datetime.utcnow().isoformat(),
                            "poll_count": poll_count
                        }
                        yield f"data: {json.dumps(heartbeat_payload)}\n\n"
                        logger.debug(f"SSE: Heartbeat sent for video {video_id} (poll {poll_count})")

                    # Stop streaming if processing complete or error
                    if current_status in ["DONE", "ERROR"]:
                        # On ERROR, fetch latest error log to include in final SSE event
                        if current_status == "ERROR":
                            try:
                                err_result = await db.execute(
                                    text("""
                                        SELECT error_code, error_step, error_message, source, created_at
                                        FROM video_error_logs
                                        WHERE video_id = :vid
                                        ORDER BY created_at DESC
                                        LIMIT 1
                                    """),
                                    {"vid": video_id},
                                )
                                err_row = err_result.fetchone()
                                if err_row:
                                    error_payload = {
                                        "video_id": str(video_id),
                                        "status": "ERROR",
                                        "latest_error": {
                                            "error_code": err_row.error_code,
                                            "error_step": err_row.error_step,
                                            "error_message": err_row.error_message,
                                            "source": err_row.source,
                                            "created_at": err_row.created_at.isoformat() if err_row.created_at else None,
                                        },
                                    }
                                    yield f"data: {json.dumps(error_payload)}\n\n"
                            except Exception as err_log_exc:
                                logger.warning(f"SSE: Failed to fetch error log for {video_id}: {err_log_exc}")
                        yield "data: [DONE]\n\n"
                        logger.info(f"SSE: Video {video_id} processing completed with status {current_status}")
                        break

                    # Poll every 2 seconds
                    await asyncio.sleep(2)
                    poll_count += 1

                except Exception as e:
                    logger.error(f"SSE poll error for video {video_id}: {e}")
                    yield f"data: {json.dumps({'error': f'Poll error: {str(e)}'})}\n\n"
                    break

            # Timeout reached
            if poll_count >= max_polls:
                logger.warning(f"SSE: Video {video_id} stream timeout after {max_polls * 2} seconds")
                yield f"data: {json.dumps({'error': 'Stream timeout'})}\n\n"

        except Exception as e:
            logger.error(f"SSE stream error for video {video_id}: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
            "Connection": "keep-alive",
        }
    )


@router.get("/{video_id}")
async def get_video_detail(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
        Video detail endpoint returning report 1 data.
        Optimized: single combined query, inline SAS generation, no ORM overhead.
    """
    import time as _time
    import os as _os
    from azure.storage.blob import generate_blob_sas as _generate_blob_sas, BlobSasPermissions as _BlobSasPermissions

    try:
        _t0 = _time.monotonic()

        # ---- Step 1: Single query to get video + user email ----
        sql_video = text("""
            SELECT v.id, v.original_filename, v.status, v.user_id,
                   v.upload_type, v.excel_product_blob_url, v.excel_trend_blob_url,
                   v.compressed_blob_url,
                   u.email
            FROM videos v
            JOIN users u ON v.user_id = u.id
            WHERE v.id = :video_id
        """)
        vres = await db.execute(sql_video, {"video_id": video_id})
        video_row = vres.fetchone()
        if not video_row:
            raise HTTPException(status_code=404, detail="Video not found")

        if current_user and current_user.get("id") != video_row.user_id:
            raise HTTPException(status_code=403, detail="Forbidden")

        email = video_row.email
        compressed_blob = video_row.compressed_blob_url
        _t1 = _time.monotonic()

        # ---- Step 2: Parallel fetch phase_insights + video_phases + video_insights ----
        sql_combined = text("""
            SELECT
                vp.id as phase_id, vp.phase_index, vp.phase_description,
                vp.time_start, vp.time_end,
                COALESCE(vp.gmv, 0) as gmv,
                COALESCE(vp.order_count, 0) as order_count,
                COALESCE(vp.viewer_count, 0) as viewer_count,
                COALESCE(vp.like_count, 0) as like_count,
                COALESCE(vp.comment_count, 0) as comment_count,
                COALESCE(vp.share_count, 0) as share_count,
                COALESCE(vp.new_followers, 0) as new_followers,
                COALESCE(vp.product_clicks, 0) as product_clicks,
                COALESCE(vp.conversion_rate, 0) as conversion_rate,
                COALESCE(vp.gpm, 0) as gpm,
                COALESCE(vp.importance_score, 0) as importance_score,
                vp.product_names,
                vp.user_rating,
                vp.user_comment,
                vp.sas_token,
                vp.sas_expireddate,
                vp.cta_score,
                vp.audio_features,
                vp.sales_psychology_tags,
                vp.human_sales_tags,
                pi.insight
            FROM video_phases vp
            LEFT JOIN phase_insights pi ON pi.video_id = vp.video_id AND pi.phase_index = vp.phase_index
            WHERE vp.video_id = :video_id
            ORDER BY vp.phase_index ASC
        """)

        sql_latest_insight = text("""
            SELECT title, content
            FROM video_insights
            WHERE video_id = :video_id
            ORDER BY created_at DESC
            LIMIT 1
        """)

        # Execute both queries concurrently
        # Fallback: if cta_score/audio_features columns don't exist yet, retry without them
        has_cta_columns = True
        try:
            combined_task = db.execute(sql_combined, {"video_id": video_id})
            insight_task = db.execute(sql_latest_insight, {"video_id": video_id})
            combined_res, insight_res = await asyncio.gather(combined_task, insight_task)
        except Exception:
            has_cta_columns = False
            await db.rollback()
            sql_combined_fallback = text("""
                SELECT
                    vp.id as phase_id, vp.phase_index, vp.phase_description,
                    vp.time_start, vp.time_end,
                    COALESCE(vp.gmv, 0) as gmv,
                    COALESCE(vp.order_count, 0) as order_count,
                    COALESCE(vp.viewer_count, 0) as viewer_count,
                    COALESCE(vp.like_count, 0) as like_count,
                    COALESCE(vp.comment_count, 0) as comment_count,
                    COALESCE(vp.share_count, 0) as share_count,
                    COALESCE(vp.new_followers, 0) as new_followers,
                    COALESCE(vp.product_clicks, 0) as product_clicks,
                    COALESCE(vp.conversion_rate, 0) as conversion_rate,
                    COALESCE(vp.gpm, 0) as gpm,
                    COALESCE(vp.importance_score, 0) as importance_score,
                    vp.product_names,
                    vp.user_rating,
                    vp.user_comment,
                    vp.sas_token,
                    vp.sas_expireddate,
                    NULL as cta_score,
                    NULL as audio_features,
                    NULL as sales_psychology_tags,
                    NULL as human_sales_tags,
                    pi.insight
                FROM video_phases vp
                LEFT JOIN phase_insights pi ON pi.video_id = vp.video_id AND pi.phase_index = vp.phase_index
                WHERE vp.video_id = :video_id
                ORDER BY vp.phase_index ASC
            """)
            combined_task = db.execute(sql_combined_fallback, {"video_id": video_id})
            insight_task = db.execute(sql_latest_insight, {"video_id": video_id})
            combined_res, insight_res = await asyncio.gather(combined_task, insight_task)

        combined_rows = combined_res.fetchall()
        latest_insight = insight_res.fetchone()
        _t2 = _time.monotonic()

        # ---- Step 3: Build SAS URLs inline (no async service call needed) ----
        conn_str = _os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
        account_name = _os.getenv("AZURE_STORAGE_ACCOUNT_NAME", "")
        container_name = _os.getenv("AZURE_BLOB_CONTAINER", "videos")
        account_key = ""
        for part in conn_str.split(";"):
            if part.startswith("AccountKey="):
                account_key = part.split("=", 1)[1]
                break

        now_utc = datetime.now(timezone.utc)
        now_naive = datetime.utcnow()
        sas_expiry = now_utc + timedelta(days=7)
        phases_needing_sas_update = []  # (phase_id, sas_url, expiry)

        def _make_sas_url(blob_name: str) -> str:
            """Generate SAS URL locally without any async/HTTP call."""
            sas = _generate_blob_sas(
                account_name=account_name,
                container_name=container_name,
                blob_name=blob_name,
                account_key=account_key,
                permission=_BlobSasPermissions(read=True),
                expiry=sas_expiry,
            )
            url = f"https://{account_name}.blob.core.windows.net/{container_name}/{blob_name}?{sas}"
            return _replace_blob_url_to_cdn(url)

        report1_items = []
        for r in combined_rows:
            # Check cached SAS
            video_clip_url = None
            if email and r.time_start is not None and r.time_end is not None:
                sas_token = r.sas_token
                sas_expire = r.sas_expireddate
                cache_valid = False
                if sas_token and sas_expire:
                    try:
                        if sas_expire.tzinfo is not None and sas_expire.tzinfo.utcoffset(sas_expire) is not None:
                            cache_valid = sas_expire.astimezone(timezone.utc) >= now_utc
                        else:
                            cache_valid = sas_expire >= now_naive
                    except Exception as _e:
                        logger.debug(f"Non-critical error suppressed: {_e}")

                if cache_valid:
                    video_clip_url = sas_token
                elif account_key:
                    try:
                        ts = float(r.time_start)
                        te = float(r.time_end)
                        fname = f"{ts:.1f}_{te:.1f}.mp4"
                        blob_name = f"{email}/{video_id}/reportvideo/{fname}"
                        video_clip_url = _make_sas_url(blob_name)
                        if r.phase_id:
                            phases_needing_sas_update.append((r.phase_id, video_clip_url, sas_expiry))
                    except Exception:
                        video_clip_url = None

            # Parse product_names
            product_names_list = []
            pn_raw = r.product_names
            if pn_raw:
                try:
                    product_names_list = json.loads(pn_raw) if isinstance(pn_raw, str) else pn_raw
                except (json.JSONDecodeError, TypeError):
                    product_names_list = []

            # Only include phases that have insights (matching original behavior)
            if r.insight is not None:
                # Parse audio_features JSON text
                audio_features_parsed = None
                try:
                    if r.audio_features:
                        audio_features_parsed = json.loads(r.audio_features) if isinstance(r.audio_features, str) else r.audio_features
                except (json.JSONDecodeError, TypeError) as _e:
                    logger.debug(f"JSON parse skipped: {_e}")

                # Parse sales_psychology_tags JSON text
                sales_tags_parsed = []
                try:
                    raw_tags = getattr(r, 'sales_psychology_tags', None)
                    if raw_tags:
                        sales_tags_parsed = json.loads(raw_tags) if isinstance(raw_tags, str) else raw_tags
                except (json.JSONDecodeError, TypeError) as _e:
                    logger.debug(f"JSON parse skipped: {_e}")

                # Parse human_sales_tags JSON text
                human_tags_parsed = None
                try:
                    raw_human_tags = getattr(r, 'human_sales_tags', None)
                    if raw_human_tags:
                        human_tags_parsed = json.loads(raw_human_tags) if isinstance(raw_human_tags, str) else raw_human_tags
                except (json.JSONDecodeError, TypeError) as _e:
                    logger.debug(f"JSON parse skipped: {_e}")

                report1_items.append({
                    "phase_index": int(r.phase_index),
                    "phase_description": r.phase_description,
                    "time_start": r.time_start,
                    "time_end": r.time_end,
                    "insight": r.insight,
                    "video_clip_url": video_clip_url,
                    "user_rating": r.user_rating,
                    "user_comment": r.user_comment,
                    "cta_score": getattr(r, 'cta_score', None),
                    "audio_features": audio_features_parsed,
                    "sales_psychology_tags": sales_tags_parsed,
                    "human_sales_tags": human_tags_parsed,
                    "csv_metrics": {
                        "gmv": r.gmv,
                        "order_count": r.order_count,
                        "viewer_count": r.viewer_count,
                        "like_count": r.like_count,
                        "comment_count": r.comment_count,
                        "share_count": r.share_count,
                        "new_followers": r.new_followers,
                        "product_clicks": r.product_clicks,
                        "conversion_rate": r.conversion_rate,
                        "gpm": r.gpm,
                        "importance_score": r.importance_score,
                        "product_names": product_names_list,
                    },
                })

        _t3 = _time.monotonic()

        # ---- Step 4: Batch persist new SAS tokens (fire-and-forget style) ----
        if phases_needing_sas_update:
            try:
                for pid, sas_url, exp_at in phases_needing_sas_update:
                    await db.execute(
                        text("UPDATE video_phases SET sas_token = :sas, sas_expireddate = :exp WHERE id = :id"),
                        {"sas": sas_url, "exp": exp_at, "id": pid}
                    )
                await db.commit()
            except Exception as _e:
                logger.debug(f"Non-critical error suppressed: {_e}")  # Non-critical

        # ---- Step 5: Build report3 ----
        report3 = []
        if latest_insight:
            parsed = latest_insight.content
            try:
                if isinstance(parsed, str):
                    s = parsed.lstrip()
                    if s.startswith("{") or s.startswith("["):
                        parsed = json.loads(parsed)

                if isinstance(parsed, dict) and parsed.get("video_insights") and isinstance(parsed.get("video_insights"), list):
                    for item in parsed.get("video_insights"):
                        report3.append({"title": item.get("title"), "content": item.get("content")})
                elif isinstance(parsed, list):
                    for item in parsed:
                        report3.append({"title": item.get("title"), "content": item.get("content")})
                else:
                    report3.append({"title": latest_insight.title, "content": latest_insight.content})
            except Exception:
                report3.append({"title": latest_insight.title, "content": latest_insight.content})

        # ---- Step 6: Generate preview URL (inline, no service call) ----
        preview_url = None
        if compressed_blob and email and account_key:
            try:
                preview_filename = compressed_blob.split('/')[-1] if '/' in compressed_blob else compressed_blob
                blob_name = f"{email}/{video_id}/{preview_filename}"
                preview_url = _make_sas_url(blob_name)
            except Exception:
                preview_url = None

        _t_end = _time.monotonic()
        _perf = {
            "video_query_ms": round((_t1-_t0)*1000),
            "combined_query_ms": round((_t2-_t1)*1000),
            "build_response_ms": round((_t3-_t2)*1000),
            "total_ms": round((_t_end-_t0)*1000),
            "phase_count": len(combined_rows),
            "sas_generated": len(phases_needing_sas_update),
        }
        logger.info(f"[PERF] {_perf}")

        return {
            "id": str(video_row.id),
            "original_filename": video_row.original_filename,
            "status": video_row.status,
            "step_progress": getattr(video_row, 'step_progress', None) or 0,
            "upload_type": video_row.upload_type,
            "excel_product_blob_url": video_row.excel_product_blob_url,
            "excel_trend_blob_url": video_row.excel_trend_blob_url,
            "compressed_blob_url": compressed_blob,
            "preview_url": preview_url,
            "reports_1": report1_items,
            "report3": report3,
            "_perf": _perf,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to fetch video detail: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch video detail: {exc}")


@router.get("/{video_id}/product-data")
async def get_video_product_data(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Fetch and parse the product Excel file for a video.
    Returns parsed product data as JSON.
    Uses SAS tokens to access Azure Blob Storage (public access is disabled).
    """
    try:
        import httpx
        import tempfile
        from app.services.storage_service import generate_read_sas_from_url

        # Get video's excel_product_blob_url and user email
        result = await db.execute(
            text("""
                SELECT v.excel_product_blob_url, v.excel_trend_blob_url, u.email
                FROM videos v
                JOIN users u ON v.user_id = u.id
                WHERE v.id = :vid
            """),
            {"vid": video_id},
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Video not found")

        product_blob_url = row[0]
        trend_blob_url = row[1]
        email = row[2]
        logger.info("[PRODUCT-DATA] video=%s product_url=%s trend_url=%s email=%s",
                    video_id, product_blob_url is not None, trend_blob_url is not None, email)

        response_data = {
            "products": [],
            "trends": [],
            "has_product_data": False,
            "has_trend_data": False,
        }

        # Helper: download and parse Excel file
        async def _parse_excel(blob_url: str) -> list:
            """Download Excel via SAS URL and parse rows into list of dicts."""
            sas_url = generate_read_sas_from_url(blob_url, expires_hours=1)
            logger.info("[PRODUCT-DATA] blob_url=%s sas_generated=%s", blob_url[:80] if blob_url else None, sas_url is not None)
            if not sas_url:
                logger.warning("Failed to generate SAS for Excel blob: %s", blob_url[:100] if blob_url else None)
                return []
            import openpyxl
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(sas_url)
                if resp.status_code != 200:
                    logger.warning(f"Failed to download Excel (HTTP {resp.status_code}): {sas_url[:100]}...")
                    return []

                with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
                    f.write(resp.content)
                    tmp_path = f.name

                try:
                    wb = openpyxl.load_workbook(tmp_path, read_only=True, data_only=True)
                    ws = wb.active
                    items = []
                    if ws:
                        rows_data = list(ws.iter_rows(values_only=True))
                        if len(rows_data) >= 2:
                            headers = [str(h).strip() if h else f"col_{i}" for i, h in enumerate(rows_data[0])]
                            for data_row in rows_data[1:]:
                                if all(v is None for v in data_row):
                                    continue
                                item = {}
                                for i, val in enumerate(data_row):
                                    if i < len(headers):
                                        if val is None:
                                            item[headers[i]] = None
                                        elif isinstance(val, (int, float)):
                                            item[headers[i]] = val
                                        else:
                                            item[headers[i]] = str(val)
                                items.append(item)
                    wb.close()
                    return items
                finally:
                    os.unlink(tmp_path)

        # Parse product Excel
        if product_blob_url:
            try:
                products = await _parse_excel(product_blob_url)
                response_data["products"] = products
                response_data["has_product_data"] = len(products) > 0

                # Cache top 2 products by GMV in videos table
                if products:
                    try:
                        # Detect GMV and name columns
                        gmv_key = None
                        name_key = None
                        sample = products[0]
                        for k in sample.keys():
                            kl = k.lower() if k else ""
                            if "gmv" in kl:
                                gmv_key = k
                            if "商品名" in k or "product" in kl or "name" in kl:
                                name_key = k
                        if gmv_key and name_key:
                            sorted_products = sorted(
                                products,
                                key=lambda x: float(x.get(gmv_key, 0) or 0),
                                reverse=True,
                            )
                            top2 = []
                            for p in sorted_products[:2]:
                                pname = p.get(name_key, "")
                                if pname:
                                    # Truncate long product names
                                    pname = str(pname)[:50]
                                    top2.append(pname)
                            if top2:
                                import json as _json
                                await db.execute(
                                    text("UPDATE videos SET top_products = :tp WHERE id = :vid"),
                                    {"tp": _json.dumps(top2, ensure_ascii=False), "vid": video_id},
                                )
                                await db.commit()
                                logger.info(f"Cached top_products for video {video_id}: {top2}")
                    except Exception as cache_err:
                        logger.warning(f"Failed to cache top_products: {cache_err}")
            except Exception as e:
                logger.warning(f"Failed to parse product Excel: {e}")

        # Parse trend Excel
        if trend_blob_url:
            try:
                trends = await _parse_excel(trend_blob_url)
                response_data["trends"] = trends
                response_data["has_trend_data"] = len(trends) > 0
            except Exception as e:
                logger.warning(f"Failed to parse trend Excel: {e}")

        return response_data

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Failed to fetch product data: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch product data: {exc}")



@router.put("/{video_id}/phases/{phase_index}/rating")
async def rate_phase(
    video_id: str,
    phase_index: int,
    request_body: dict,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Save a human rating (1-5) and optional comment for a specific phase.
    Also updates the quality_score in Qdrant for RAG learning.

    Body:
    {
        "rating": 1-5,
        "comment": "optional text"
    }
    """
    try:
        user_id = user.get("user_id") or user.get("id")
        rating = request_body.get("rating")
        comment = request_body.get("comment", "")
        reviewer_name = request_body.get("reviewer_name", "")

        if rating is None or not isinstance(rating, int) or rating < 1 or rating > 5:
            raise HTTPException(status_code=400, detail="rating must be an integer between 1 and 5")

        # Verify video belongs to user
        video_repo = VideoRepository(lambda: db)
        video = await video_repo.get_video_by_id(video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")
        if str(getattr(video, "user_id", None)) != str(user_id):
            raise HTTPException(status_code=403, detail="Forbidden")

        # Map rating (1-5) to importance_score (0.0-1.0)
        importance_score = (rating - 1) / 4.0  # 1->0.0, 2->0.25, 3->0.5, 4->0.75, 5->1.0

        # Update video_phases with user rating, comment, and importance_score
        # Use try-except for graceful fallback if columns don't exist yet
        try:
            sql_update = text("""
                UPDATE video_phases
                SET user_rating = :rating,
                    user_comment = :comment,
                    importance_score = :importance_score,
                    rated_at = NOW(),
                    updated_at = NOW()
                WHERE video_id = :video_id AND phase_index = :phase_index
            """)
            await db.execute(sql_update, {
                "rating": rating,
                "comment": comment,
                "importance_score": importance_score,
                "video_id": video_id,
                "phase_index": phase_index,
            })
            await db.commit()
        except Exception as db_err:
            await db.rollback()
            # Fallback: try without user_rating/user_comment columns
            try:
                sql_fallback = text("""
                    UPDATE video_phases
                    SET importance_score = :importance_score,
                        updated_at = NOW()
                    WHERE video_id = :video_id AND phase_index = :phase_index
                """)
                await db.execute(sql_fallback, {
                    "importance_score": importance_score,
                    "video_id": video_id,
                    "phase_index": phase_index,
                })
                await db.commit()
            except Exception:
                await db.rollback()
                logger.warning(f"Could not update video_phases for rating: {db_err}")

        # Update Qdrant quality_score for RAG learning (in background for faster response)
        def _update_qdrant_bg(vid, pidx, r, c):
            try:
                from app.services.rag.knowledge_store import update_quality_score_with_comment
                update_quality_score_with_comment(
                    video_id=vid, phase_index=pidx, rating=r, comment=c,
                )
            except ImportError:
                try:
                    from app.services.rag.knowledge_store import update_quality_score
                    old_rating = 1 if r >= 4 else (-1 if r <= 2 else 0)
                    update_quality_score(video_id=vid, phase_index=pidx, rating=old_rating)
                except Exception as rag_err:
                    logger.warning(f"Could not update Qdrant quality_score: {rag_err}")
            except Exception as rag_err:
                logger.warning(f"Could not update Qdrant quality_score: {rag_err}")

        background_tasks.add_task(_update_qdrant_bg, video_id, phase_index, rating, comment)

        logger.info(f"Phase rated: video={video_id}, phase={phase_index}, rating={rating}, comment={comment[:50] if comment else ''}")

        return {
            "success": True,
            "video_id": video_id,
            "phase_index": phase_index,
            "rating": rating,
            "comment": comment,
            "importance_score": importance_score,
            "reviewer_name": reviewer_name,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to rate phase: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to rate phase: {exc}")


# =========================================================
# Phase Comment API (save comment without requiring rating)
# =========================================================

@router.put("/{video_id}/phases/{phase_index}/comment")
async def save_phase_comment(
    video_id: str,
    phase_index: int,
    request_body: dict,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Save a comment for a specific phase (rating not required).
    Body:
    {
        "comment": "text",
        "reviewer_name": "optional"
    }
    """
    try:
        user_id = user.get("user_id") or user.get("id")
        comment = request_body.get("comment", "")
        reviewer_name = request_body.get("reviewer_name", "")

        # Verify video belongs to user
        video_repo = VideoRepository(lambda: db)
        video = await video_repo.get_video_by_id(video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")
        if str(getattr(video, "user_id", None)) != str(user_id):
            raise HTTPException(status_code=403, detail="Forbidden")

        sql_update = text("""
            UPDATE video_phases
            SET user_comment = :comment,
                updated_at = NOW()
            WHERE video_id = :video_id AND phase_index = :phase_index
        """)
        result = await db.execute(sql_update, {
            "comment": comment,
            "video_id": video_id,
            "phase_index": phase_index,
        })
        await db.commit()

        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Phase not found")

        logger.info(f"Phase comment saved: video={video_id}, phase={phase_index}, comment={comment[:50] if comment else ''}")

        return {
            "success": True,
            "video_id": video_id,
            "phase_index": phase_index,
            "comment": comment,
        }

    except HTTPException:
        raise
    except Exception as exc:
        await db.rollback()
        logger.exception(f"Failed to save phase comment: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to save phase comment: {exc}")


# =========================================================
# Human Sales Tags API (Human-in-the-loop)
# =========================================================

ALL_SALES_TAGS = {
    # Sales psychology tags
    "HOOK", "EMPATHY", "PROBLEM", "EDUCATION", "SOLUTION",
    "DEMONSTRATION", "COMPARISON", "PROOF", "TRUST", "SOCIAL_PROOF",
    "OBJECTION_HANDLING", "URGENCY", "LIMITED_OFFER", "BONUS", "CTA",
    # Phase behavior tags
    "CHAT", "PREP", "PHONE_OP", "LONG_GREET",
    "COMMENT_READ", "SILENCE", "PRICE_SHOW",
}


@router.patch("/{video_id}/phases/{phase_index}/tags")
async def update_human_sales_tags(
    video_id: str,
    phase_index: int,
    request_body: dict,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Save human-corrected sales psychology tags for a specific phase.
    Body:
    {
        "human_sales_tags": ["HOOK", "EMPATHY", "CTA"]
    }
    """
    try:
        user_id = user.get("user_id") or user.get("id")
        tags = request_body.get("human_sales_tags")
        reviewer_name = request_body.get("reviewer_name", "")

        if tags is None or not isinstance(tags, list):
            raise HTTPException(status_code=400, detail="human_sales_tags must be a list of tag strings")

        # Validate tags
        invalid = [t for t in tags if t not in ALL_SALES_TAGS]
        if invalid:
            raise HTTPException(status_code=400, detail=f"Invalid tags: {invalid}. Valid: {sorted(ALL_SALES_TAGS)}")

        # Verify video belongs to user
        video_repo = VideoRepository(lambda: db)
        video = await video_repo.get_video_by_id(video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")
        if str(getattr(video, "user_id", None)) != str(user_id):
            raise HTTPException(status_code=403, detail="Forbidden")

        tags_json = json.dumps(tags)

        sql_update = text("""
            UPDATE video_phases
            SET human_sales_tags = :tags,
                updated_at = NOW()
            WHERE video_id = :video_id AND phase_index = :phase_index
        """)
        result = await db.execute(sql_update, {
            "tags": tags_json,
            "video_id": video_id,
            "phase_index": phase_index,
        })
        await db.commit()

        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Phase not found")

        logger.info(f"Human tags saved: video={video_id}, phase={phase_index}, tags={tags}")

        return {
            "success": True,
            "video_id": video_id,
            "phase_index": phase_index,
            "human_sales_tags": tags,
        }

    except HTTPException:
        raise
    except Exception as exc:
        await db.rollback()
        logger.exception(f"Failed to save human sales tags: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to save human sales tags: {exc}")


# =========================================================
# ============================================================
# TikTok Live Capture Endpoints
# ============================================================

@router.post("/live-check", response_model=LiveCheckResponse)
async def live_check(
    payload: LiveCaptureRequest,
    current_user=Depends(get_current_user),
):
    """Check if a TikTok user is currently live."""
    from app.services.tiktok_service import TikTokLiveService

    try:
        info = await TikTokLiveService.check_and_get_info(payload.live_url)
        return LiveCheckResponse(
            is_live=info["is_live"],
            username=info.get("username"),
            room_id=info.get("room_id"),
            title=info.get("title"),
            message="LIVE" if info["is_live"] else "User is not currently live",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as exc:
        logger.exception(f"Live check failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Live check failed: {exc}")


@router.post("/live-capture", response_model=LiveCaptureResponse)
async def live_capture(
    payload: LiveCaptureRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Start capturing a TikTok live stream.
    1. Validates the URL and checks if the user is live
    2. Creates a video record in the database
    3. Enqueues a live_capture job for the worker
    """
    from app.services.tiktok_service import TikTokLiveService
    from app.services.queue_service import enqueue_job

    # Step 1: Check live status
    try:
        info = await TikTokLiveService.check_and_get_info(payload.live_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as exc:
        logger.exception(f"Live check failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to check live status: {exc}")

    if not info["is_live"]:
        raise HTTPException(
            status_code=400,
            detail=f"@{info.get('username', 'unknown')} is not currently live",
        )

    username = info["username"]
    title = info.get("title", "")

    # Step 2: Create video record
    video_id = str(uuid_module.uuid4())
    original_filename = f"tiktok_live_{username}.mp4"

    try:
        video_repo = VideoRepository(lambda: db)
        service = VideoService(video_repository=video_repo)

        video = await video_repo.create_video(
            user_id=current_user["id"],
            video_id=video_id,
            original_filename=original_filename,
            status="capturing",
            upload_type="live_capture",
        )
        await db.commit()
    except Exception as exc:
        logger.exception(f"Failed to create video record: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to create video record: {exc}")

    # Step 3: Enqueue live_capture job
    try:
        queue_payload = {
            "job_type": "live_capture",
            "video_id": video_id,
            "live_url": payload.live_url,
            "email": current_user["email"],
            "user_id": current_user["id"],
            "duration": payload.duration or 0,
            "username": username,
            "stream_title": title,
        }
        await enqueue_job(queue_payload)
    except Exception as exc:
        logger.exception(f"Failed to enqueue live capture job: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to start capture: {exc}")

    return LiveCaptureResponse(
        video_id=video_id,
        status="capturing",
        stream_title=title,
        username=username,
        message=f"Live capture started for @{username}; recording and analysis will begin automatically",
    )

# =========================================================
# =========================================================
# Retry Analysis API (user-facing)
# =========================================================

@router.post("/{video_id}/retry-analysis")
async def retry_analysis(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Re-enqueue a failed video for analysis without re-uploading.
    The uploaded video asset is preserved in Blob storage.
    Only the analysis job is re-submitted.
    """
    try:
        user_id = user.get("user_id") or user.get("id")

        # Verify video exists and belongs to user
        sql = text("""
            SELECT v.id, v.original_filename, v.status, v.user_id,
                   u.email as user_email
            FROM videos v
            LEFT JOIN users u ON v.user_id = u.id
            WHERE v.id = :vid
        """)
        result = await db.execute(sql, {"vid": video_id})
        row = result.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Video not found")
        if str(row.user_id) != str(user_id):
            raise HTTPException(status_code=403, detail="Forbidden")

        # Allow retry for ERROR, stuck QUEUED, or stalled processing states
        allowed_statuses = ("ERROR", "error", "uploaded", "UPLOADED", "QUEUED")
        # Also allow any STEP_* status (e.g. STEP_0_EXTRACT_FRAMES) that may be stalled
        is_stuck_step = row.status and row.status.startswith("STEP_")
        if row.status not in allowed_statuses and not is_stuck_step:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot retry: video status is '{row.status}'. "
                       f"Retry is only available for failed or stuck videos.",
            )

        # Generate fresh SAS URL for the existing blob
        from app.services.storage_service import generate_download_sas
        download_url, expiry = await generate_download_sas(
            email=row.user_email,
            video_id=str(row.id),
            filename=row.original_filename,
            expires_in_minutes=1440,  # 24 hours
        )

        # Determine resume status: keep current STEP_* status for resume,
        # only reset to 'uploaded' if status is ERROR or non-STEP
        previous_status = row.status
        is_step_status = previous_status and previous_status.startswith("STEP_")

        if is_step_status:
            # Keep the STEP_* status so worker can resume from this step
            resume_status = previous_status
            await db.execute(
                text("""
                    UPDATE videos
                    SET step_progress = 0,
                        error_message = NULL
                    WHERE id = :vid
                """),
                {"vid": video_id},
            )
        else:
            # ERROR or other status: reset to uploaded for full re-analysis
            resume_status = 'uploaded'
            await db.execute(
                text("""
                    UPDATE videos
                    SET status = 'uploaded',
                        step_progress = 0,
                        error_message = NULL
                    WHERE id = :vid
                """),
                {"vid": video_id},
            )
        await db.commit()

        # Enqueue analysis job
        from app.services.queue_service import enqueue_job
        await enqueue_job({
            "video_id": str(row.id),
            "blob_url": download_url,
            "original_filename": row.original_filename,
        })

        logger.info(
            f"[retry-analysis] User {user_id} retried analysis for video {video_id} "
            f"(was: {previous_status}, resume_from: {resume_status})"
        )

        return {
            "success": True,
            "video_id": video_id,
            "message": f"解析を再開しました。{resume_status}から再開します。",
            "new_status": resume_status,
        }

    except HTTPException:
        raise
    except Exception as exc:
        await db.rollback()
        logger.exception(f"[retry-analysis] Failed: {exc}")
        raise HTTPException(status_code=500, detail=f"解析の再試行に失敗しました: {exc}")



# =========================

# ============================================================
# Include split sub-modules
# ============================================================
from app.api.v1.endpoints.video_clips import router as clips_router
from app.api.v1.endpoints.video_products import router as products_router
from app.api.v1.endpoints.video_sales import router as sales_router
from app.api.v1.endpoints.video_excel import router as excel_router

# Merge sub-routers into the main video router
for sub in [clips_router, products_router, sales_router, excel_router]:
    for route in sub.routes:
        router.routes.append(route)



@router.get("/_debug/storage-info")
async def debug_storage_info(current_user=Depends(get_current_user)):
    """Temporary debug endpoint to check storage configuration."""
    from app.services.storage_service import ACCOUNT_NAME, CONNECTION_STRING, CONTAINER_NAME
    return {
        "account_name": ACCOUNT_NAME or "(empty)",
        "has_connection_string": bool(CONNECTION_STRING),
        "connection_string_len": len(CONNECTION_STRING) if CONNECTION_STRING else 0,
        "container": CONTAINER_NAME,
    }
