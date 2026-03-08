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


def _replace_blob_url_to_cdn(url: str) -> str:
    """Replace blob storage domain with CDN domain if applicable."""
    if url and isinstance(url, str):
        return url.replace(
            "https://aitherhub.blob.core.windows.net",
            "https://cdn.aitherhub.com"
        )
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
                    except Exception:
                        pass

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
                except (json.JSONDecodeError, TypeError):
                    pass

                # Parse sales_psychology_tags JSON text
                sales_tags_parsed = []
                try:
                    raw_tags = getattr(r, 'sales_psychology_tags', None)
                    if raw_tags:
                        sales_tags_parsed = json.loads(raw_tags) if isinstance(raw_tags, str) else raw_tags
                except (json.JSONDecodeError, TypeError):
                    pass

                # Parse human_sales_tags JSON text
                human_tags_parsed = None
                try:
                    raw_human_tags = getattr(r, 'human_sales_tags', None)
                    if raw_human_tags:
                        human_tags_parsed = json.loads(raw_human_tags) if isinstance(raw_human_tags, str) else raw_human_tags
                except (json.JSONDecodeError, TypeError):
                    pass

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
            except Exception:
                pass  # Non-critical

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
        import os
        from azure.storage.blob import generate_blob_sas, BlobSasPermissions
        from datetime import timedelta

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

        response_data = {
            "products": [],
            "trends": [],
            "has_product_data": False,
            "has_trend_data": False,
        }

        # Helper: generate SAS download URL from blob URL
        def _generate_sas_url(blob_url: str) -> str:
            """Generate a SAS-signed download URL from a raw blob URL."""
            conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
            account_name = ""
            account_key = ""
            for part in conn_str.split(";"):
                if part.startswith("AccountName="):
                    account_name = part.split("=", 1)[1]
                elif part.startswith("AccountKey="):
                    account_key = part.split("=", 1)[1]

            # Extract blob path from URL
            # URL format: https://account.blob.core.windows.net/videos/email/video_id/excel/filename.xlsx
            try:
                from urllib.parse import urlparse, unquote
                parsed = urlparse(blob_url)
                path = unquote(parsed.path)  # /videos/email/video_id/excel/filename.xlsx
                # Remove leading /videos/ to get blob_name
                if path.startswith("/videos/"):
                    blob_name = path[len("/videos/"):]
                else:
                    blob_name = path.lstrip("/")
                    # Remove container name if present
                    if blob_name.startswith("videos/"):
                        blob_name = blob_name[len("videos/"):]
            except Exception:
                # Fallback: construct blob name from email/video_id
                filename = blob_url.split("/")[-1].split("?")[0]
                blob_name = f"{email}/{video_id}/excel/{filename}"

            expiry = datetime.now(timezone.utc) + timedelta(minutes=30)
            sas = generate_blob_sas(
                account_name=account_name,
                container_name="videos",
                blob_name=blob_name,
                account_key=account_key,
                permission=BlobSasPermissions(read=True),
                expiry=expiry,
            )
            return f"https://{account_name}.blob.core.windows.net/videos/{blob_name}?{sas}"

        # Helper: download and parse Excel file
        async def _parse_excel(blob_url: str) -> list:
            """Download Excel via SAS URL and parse rows into list of dicts."""
            sas_url = _generate_sas_url(blob_url)
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



# =========================
# Clip generation endpoints
# =========================

@router.post("/{video_id}/clips")
async def request_clip_generation(
    video_id: str,
    request_body: dict,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Request TikTok-style clip generation for a specific phase.
    
    Body:
    {
        "phase_index": 0,
        "time_start": 0.0,
        "time_end": 51.0,
        "speed_factor": 1.2  // optional, default 1.0 (1.0-1.5x)
    }
    """
    try:
        user_id = user.get("user_id") or user.get("id")
        phase_index = request_body.get("phase_index")
        time_start = request_body.get("time_start")
        time_end = request_body.get("time_end")
        speed_factor = float(request_body.get("speed_factor", 1.0))

        if phase_index is None or time_start is None or time_end is None:
            raise HTTPException(status_code=400, detail="phase_index, time_start, time_end are required")

        # Clamp speed_factor to safe range
        speed_factor = max(0.5, min(2.0, speed_factor))

        time_start = float(time_start)
        time_end = float(time_end)

        if time_end <= time_start:
            raise HTTPException(status_code=400, detail="time_end must be greater than time_start")

        # Check if clip already exists for this phase
        existing_sql = text("""
            SELECT id, status, clip_url
            FROM video_clips
            WHERE video_id = :video_id AND phase_index = :phase_index
            ORDER BY created_at DESC
            LIMIT 1
        """)
        existing = await db.execute(existing_sql, {"video_id": video_id, "phase_index": phase_index})
        existing_row = existing.fetchone()

        if existing_row:
            if existing_row.status == "completed" and existing_row.clip_url:
                # Already generated - return existing
                return {
                    "clip_id": str(existing_row.id),
                    "status": "completed",
                    "clip_url": _replace_blob_url_to_cdn(existing_row.clip_url),
                    "message": "Clip already generated",
                }
            elif existing_row.status in ("pending", "processing"):
                # Already in progress
                return {
                    "clip_id": str(existing_row.id),
                    "status": existing_row.status,
                    "message": "Clip generation already in progress",
                }
            # If failed, create a new one

        # Verify video belongs to user
        video_sql = text("SELECT id, user_id, original_filename FROM videos WHERE id = :video_id")
        vres = await db.execute(video_sql, {"video_id": video_id})
        video_row = vres.fetchone()

        if not video_row:
            raise HTTPException(status_code=404, detail="Video not found")
        if video_row.user_id != user_id:
            raise HTTPException(status_code=403, detail="Not authorized")

        # Get user email for blob path
        user_sql = text("SELECT email FROM users WHERE id = :user_id")
        ures = await db.execute(user_sql, {"user_id": user_id})
        user_row = ures.fetchone()
        email = user_row.email if user_row else None

        if not email:
            raise HTTPException(status_code=400, detail="User email not found")

        # Generate download SAS URL for source video
        from app.services.storage_service import generate_download_sas
        download_url, _ = await generate_download_sas(
            email=email,
            video_id=video_id,
            filename=video_row.original_filename,
            expires_in_minutes=1440,
        )

        # Create clip record
        clip_id = str(uuid_module.uuid4())
        insert_sql = text("""
            INSERT INTO video_clips (id, video_id, user_id, phase_index, time_start, time_end, status)
            VALUES (:id, :video_id, :user_id, :phase_index, :time_start, :time_end, 'pending')
        """)
        await db.execute(insert_sql, {
            "id": clip_id,
            "video_id": video_id,
            "user_id": user_id,
            "phase_index": phase_index,
            "time_start": time_start,
            "time_end": time_end,
        })
        await db.commit()

        # Enqueue clip generation job
        from app.services.queue_service import enqueue_job
        await enqueue_job({
            "job_type": "generate_clip",
            "clip_id": clip_id,
            "video_id": video_id,
            "blob_url": download_url,
            "time_start": time_start,
            "time_end": time_end,
            "phase_index": phase_index,
            "speed_factor": speed_factor,
        })

        logger.info(f"Clip generation requested: clip_id={clip_id}, video_id={video_id}, phase={phase_index}")

        return {
            "clip_id": clip_id,
            "status": "pending",
            "message": "Clip generation started",
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to request clip generation: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to request clip generation: {exc}")


@router.get("/{video_id}/clips/{phase_index}")
async def get_clip_status(
    video_id: str,
    phase_index: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Get clip generation status and download URL for a specific phase."""
    try:
        user_id = user.get("user_id") or user.get("id")

        sql = text("""
            SELECT id, status, clip_url, sas_token, sas_expireddate, error_message, created_at
            FROM video_clips
            WHERE video_id = :video_id AND phase_index = :phase_index
            ORDER BY created_at DESC
            LIMIT 1
        """)
        result = await db.execute(sql, {"video_id": video_id, "phase_index": phase_index})
        row = result.fetchone()

        if not row:
            return {
                "status": "not_found",
                "message": "No clip found for this phase",
            }

        response = {
            "clip_id": str(row.id),
            "status": row.status,
        }

        if row.status == "completed" and row.clip_url:
            # Generate or reuse SAS download URL
            clip_download_url = None

            # Check if existing SAS is still valid
            if row.sas_token and row.sas_expireddate:
                now = datetime.now(timezone.utc)
                expiry = row.sas_expireddate
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                if expiry > now:
                    clip_download_url = row.sas_token

            if not clip_download_url:
                # Generate new SAS URL for clip
                try:
                    # Get user email
                    user_sql = text("SELECT email FROM users WHERE id = :user_id")
                    ures = await db.execute(user_sql, {"user_id": user_id})
                    user_row = ures.fetchone()

                    if user_row:
                        # Extract blob path from clip_url
                        from app.services.storage_service import generate_download_sas
                        # Parse the clip blob name from the URL
                        clip_url = row.clip_url
                        # clip_url format: https://account.blob.core.windows.net/container/email/video_id/clips/clip_X_Y.mp4
                        parts = clip_url.split("/")
                        # Find the email/video_id/clips/filename part
                        try:
                            container_idx = parts.index("videos") if "videos" in parts else -1
                            if container_idx >= 0 and container_idx + 1 < len(parts):
                                blob_path = "/".join(parts[container_idx + 1:])
                                from azure.storage.blob import generate_blob_sas, BlobSasPermissions
                                import os as _os
                                conn_str = _os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
                                account_name = ""
                                account_key = ""
                                for p in conn_str.split(";"):
                                    if p.startswith("AccountName="):
                                        account_name = p.split("=", 1)[1]
                                    if p.startswith("AccountKey="):
                                        account_key = p.split("=", 1)[1]

                                if account_name and account_key:
                                    expiry_dt = datetime.now(timezone.utc) + timedelta(hours=24)
                                    sas = generate_blob_sas(
                                        account_name=account_name,
                                        container_name="videos",
                                        blob_name=blob_path,
                                        account_key=account_key,
                                        permission=BlobSasPermissions(read=True),
                                        expiry=expiry_dt,
                                    )
                                    clip_download_url = f"https://{account_name}.blob.core.windows.net/videos/{blob_path}?{sas}"
                                    clip_download_url = _replace_blob_url_to_cdn(clip_download_url)

                                    # Cache the SAS token
                                    update_sql = text("""
                                        UPDATE video_clips
                                        SET sas_token = :sas_token, sas_expireddate = :expiry
                                        WHERE id = :id
                                    """)
                                    await db.execute(update_sql, {
                                        "sas_token": clip_download_url,
                                        "expiry": expiry_dt,
                                        "id": row.id,
                                    })
                                    await db.commit()
                        except Exception as e:
                            logger.warning(f"Failed to parse clip blob path: {e}")
                except Exception as e:
                    logger.warning(f"Failed to generate clip SAS: {e}")

            response["clip_url"] = clip_download_url or _replace_blob_url_to_cdn(row.clip_url)

        elif row.status == "failed":
            response["error_message"] = row.error_message

        return response

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to get clip status: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to get clip status: {exc}")


@router.get("/{video_id}/clips")
async def list_clips(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """List all clips for a video."""
    try:
        user_id = user.get("user_id") or user.get("id")

        sql = text("""
            SELECT id, phase_index, time_start, time_end, status, clip_url, sas_token, sas_expireddate, created_at
            FROM video_clips
            WHERE video_id = :video_id
            ORDER BY phase_index ASC, created_at DESC
        """)
        result = await db.execute(sql, {"video_id": video_id})
        rows = result.fetchall()

        clips = []
        seen_phases = set()
        for row in rows:
            # Only include the latest clip per phase
            if row.phase_index in seen_phases:
                continue
            seen_phases.add(row.phase_index)

            clip = {
                "clip_id": str(row.id),
                "phase_index": row.phase_index,
                "time_start": row.time_start,
                "time_end": row.time_end,
                "status": row.status,
            }
            if row.status == "completed" and row.clip_url:
                # Generate or reuse SAS download URL (same logic as get_clip_status)
                clip_download_url = None

                # Check if existing SAS is still valid
                if row.sas_token and row.sas_expireddate:
                    now = datetime.now(timezone.utc)
                    expiry = row.sas_expireddate
                    if expiry.tzinfo is None:
                        expiry = expiry.replace(tzinfo=timezone.utc)
                    if expiry > now:
                        clip_download_url = row.sas_token

                if not clip_download_url:
                    # Generate new SAS URL for clip
                    try:
                        clip_url = row.clip_url
                        parts = clip_url.split("/")
                        container_idx = parts.index("videos") if "videos" in parts else -1
                        if container_idx >= 0 and container_idx + 1 < len(parts):
                            blob_path = "/".join(parts[container_idx + 1:])
                            from azure.storage.blob import generate_blob_sas, BlobSasPermissions
                            import os as _os
                            conn_str = _os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
                            account_name = ""
                            account_key = ""
                            for p in conn_str.split(";"):
                                if p.startswith("AccountName="):
                                    account_name = p.split("=", 1)[1]
                                if p.startswith("AccountKey="):
                                    account_key = p.split("=", 1)[1]

                            if account_name and account_key:
                                expiry_dt = datetime.now(timezone.utc) + timedelta(hours=24)
                                sas = generate_blob_sas(
                                    account_name=account_name,
                                    container_name="videos",
                                    blob_name=blob_path,
                                    account_key=account_key,
                                    permission=BlobSasPermissions(read=True),
                                    expiry=expiry_dt,
                                )
                                clip_download_url = f"https://{account_name}.blob.core.windows.net/videos/{blob_path}?{sas}"
                                clip_download_url = _replace_blob_url_to_cdn(clip_download_url)

                                # Cache the SAS token
                                update_sql = text("""
                                    UPDATE video_clips
                                    SET sas_token = :sas_token, sas_expireddate = :expiry
                                    WHERE id = :id
                                """)
                                await db.execute(update_sql, {
                                    "sas_token": clip_download_url,
                                    "expiry": expiry_dt,
                                    "id": row.id,
                                })
                                await db.commit()
                    except Exception as e:
                        logger.warning(f"Failed to generate clip SAS in list: {e}")

                clip["clip_url"] = clip_download_url or _replace_blob_url_to_cdn(row.clip_url)
            clips.append(clip)

        return {"clips": clips}

    except Exception as exc:
        logger.exception(f"Failed to list clips: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to list clips: {exc}")



# ──────────────────────────────────────────────────────────────
# Phase Rating (Human Feedback)
# ──────────────────────────────────────────────────────────────

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
# Product Exposure Timeline API
# =========================================================

@router.get("/{video_id}/product-exposures")
async def get_product_exposures(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Get AI-detected product exposure timeline for a video.
    Returns list of product exposure segments sorted by time_start.
    """
    try:
        # Verify video belongs to user
        result = await db.execute(
            text("SELECT user_id FROM videos WHERE id = :vid"),
            {"vid": video_id},
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Video not found")
        if row[0] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Forbidden")

        # Ensure table exists (safe for first-time access)
        try:
            await db.execute(text("""
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
            """))
            await db.commit()
        except Exception:
            await db.rollback()

        # Fetch exposures
        result = await db.execute(
            text("""
                SELECT id, video_id, user_id, product_name, brand_name,
                       product_image_url, time_start, time_end, confidence, source,
                       created_at, updated_at
                FROM video_product_exposures
                WHERE video_id = :vid
                ORDER BY time_start ASC
            """),
            {"vid": video_id},
        )
        rows = result.fetchall()

        exposures = []
        for r in rows:
            exposures.append({
                "id": str(r[0]),
                "video_id": str(r[1]),
                "user_id": r[2],
                "product_name": r[3],
                "brand_name": r[4],
                "product_image_url": r[5],
                "time_start": r[6],
                "time_end": r[7],
                "confidence": r[8],
                "source": r[9],
                "created_at": r[10].isoformat() if r[10] else None,
                "updated_at": r[11].isoformat() if r[11] else None,
            })

        return {"exposures": exposures, "count": len(exposures)}

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to get product exposures: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.put("/{video_id}/product-exposures/{exposure_id}")
async def update_product_exposure(
    video_id: str,
    exposure_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Update a product exposure segment (human edit).
    Payload can include: product_name, brand_name, time_start, time_end, confidence
    """
    try:
        # Verify video belongs to user
        result = await db.execute(
            text("SELECT user_id FROM videos WHERE id = :vid"),
            {"vid": video_id},
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Video not found")
        if row[0] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Forbidden")

        # Build dynamic SET clause
        allowed_fields = ["product_name", "brand_name", "time_start", "time_end", "confidence"]
        set_parts = []
        params = {"eid": exposure_id, "vid": video_id}

        for field in allowed_fields:
            if field in payload:
                set_parts.append(f"{field} = :{field}")
                params[field] = payload[field]

        if not set_parts:
            raise HTTPException(status_code=400, detail="No fields to update")

        # Mark as human-edited
        set_parts.append("source = 'human'")
        set_parts.append("updated_at = now()")

        sql = text(f"""
            UPDATE video_product_exposures
            SET {', '.join(set_parts)}
            WHERE id = :eid AND video_id = :vid
            RETURNING id
        """)

        result = await db.execute(sql, params)
        updated = result.fetchone()
        await db.commit()

        if not updated:
            raise HTTPException(status_code=404, detail="Exposure not found")

        return {"success": True, "id": str(updated[0])}

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to update product exposure: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{video_id}/product-exposures")
async def create_product_exposure(
    video_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Manually create a product exposure segment.
    Required: product_name, time_start, time_end
    Optional: brand_name, confidence
    """
    try:
        # Verify video belongs to user
        result = await db.execute(
            text("SELECT user_id FROM videos WHERE id = :vid"),
            {"vid": video_id},
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Video not found")
        if row[0] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Forbidden")

        product_name = payload.get("product_name")
        time_start = payload.get("time_start")
        time_end = payload.get("time_end")

        if not product_name or time_start is None or time_end is None:
            raise HTTPException(
                status_code=400,
                detail="product_name, time_start, time_end are required",
            )

        sql = text("""
            INSERT INTO video_product_exposures
                (video_id, user_id, product_name, brand_name,
                 time_start, time_end, confidence, source)
            VALUES
                (:vid, :uid, :product_name, :brand_name,
                 :time_start, :time_end, :confidence, 'human')
            RETURNING id
        """)

        result = await db.execute(sql, {
            "vid": video_id,
            "uid": current_user["id"],
            "product_name": product_name,
            "brand_name": payload.get("brand_name", ""),
            "time_start": time_start,
            "time_end": time_end,
            "confidence": payload.get("confidence", 1.0),
        })
        new_row = result.fetchone()
        await db.commit()

        return {"success": True, "id": str(new_row[0])}

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to create product exposure: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/{video_id}/product-exposures/{exposure_id}")
async def delete_product_exposure(
    video_id: str,
    exposure_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Delete a product exposure segment."""
    try:
        # Verify video belongs to user
        result = await db.execute(
            text("SELECT user_id FROM videos WHERE id = :vid"),
            {"vid": video_id},
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Video not found")
        if row[0] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Forbidden")
        result = await db.execute(
            text(""""
                DELETE FROM video_product_exposures
                WHERE id = :eid AND video_id = :vid
                RETURNING id
            """),
            {"eid": exposure_id, "vid": video_id},
        )
        deleted = result.fetchone()
        await db.commit()

        if not deleted:
            raise HTTPException(status_code=404, detail="Exposure not found")

        return {"success": True}

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to delete product exposure: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{video_id}/product-exposures/remap-names")
async def remap_product_exposure_names(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Remap generic product names (Product_0, Product_1, ...) to actual names
    from the Excel product data.
    
    Logic:
    1. Get all exposures for this video
    2. Get the product Excel data (same as product-data endpoint)
    3. Extract unique generic names, sort by index (Product_0, Product_1, ...)
    4. Map each Product_N to the Nth product in the Excel list
    5. Also try to find the actual product_name key in Excel data
    6. Bulk update all exposures with the real product names
    """
    try:
        # Verify video belongs to user
        result = await db.execute(
            text("SELECT user_id, excel_product_blob_url FROM videos WHERE id = :vid"),
            {"vid": video_id},
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Video not found")
        if row[0] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Forbidden")

        product_blob_url = row[1]
        if not product_blob_url:
            return {"success": False, "message": "No product Excel file uploaded for this video", "updated": 0}

        # --- Parse Excel to get product list ---
        import httpx
        import tempfile
        import os as _os
        import openpyxl
        from azure.storage.blob import generate_blob_sas, BlobSasPermissions
        from datetime import timedelta

        # Generate SAS URL
        conn_str = _os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
        account_name = ""
        account_key = ""
        for part in conn_str.split(";"):
            if part.startswith("AccountName="):
                account_name = part.split("=", 1)[1]
            elif part.startswith("AccountKey="):
                account_key = part.split("=", 1)[1]

        from urllib.parse import urlparse, unquote
        parsed = urlparse(product_blob_url)
        path = unquote(parsed.path)
        if path.startswith("/videos/"):
            blob_name = path[len("/videos/"):]
        else:
            blob_name = path.lstrip("/")
            if blob_name.startswith("videos/"):
                blob_name = blob_name[len("videos/"):]

        expiry = datetime.now(timezone.utc) + timedelta(minutes=30)
        sas = generate_blob_sas(
            account_name=account_name,
            container_name="videos",
            blob_name=blob_name,
            account_key=account_key,
            permission=BlobSasPermissions(read=True),
            expiry=expiry,
        )
        sas_url = f"https://{account_name}.blob.core.windows.net/videos/{blob_name}?{sas}"

        # Download and parse Excel
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(sas_url)
            if resp.status_code != 200:
                return {"success": False, "message": f"Failed to download Excel (HTTP {resp.status_code})", "updated": 0}

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            f.write(resp.content)
            tmp_path = f.name

        try:
            wb = openpyxl.load_workbook(tmp_path, read_only=True, data_only=True)
            ws = wb.active
            excel_products = []
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
                                item[headers[i]] = val
                        excel_products.append(item)
            wb.close()
        finally:
            _os.unlink(tmp_path)

        if not excel_products:
            return {"success": False, "message": "No products found in Excel file", "updated": 0}

        # --- Build name mapping ---
        # Find the product name column in Excel
        # Try common column names: 商品名, product_name, name, 商品タイトル
        name_keys = ["商品名", "product_name", "name", "商品タイトル", "Name", "Product Name", "商品"]
        product_name_key = None
        sample = excel_products[0]
        for key in name_keys:
            if key in sample and sample[key]:
                product_name_key = key
                break
        # If not found, try first string column
        if not product_name_key:
            for k, v in sample.items():
                if isinstance(v, str) and len(v) > 2:
                    product_name_key = k
                    break

        if not product_name_key:
            return {"success": False, "message": "Could not find product name column in Excel", "updated": 0}

        # Build ordered list of real product names from Excel
        real_names = []
        for p in excel_products:
            pname = p.get(product_name_key)
            if pname:
                real_names.append(str(pname).strip())
            else:
                real_names.append(None)

        logger.info(f"[REMAP] Found {len(real_names)} products in Excel, name_key='{product_name_key}'")
        logger.info(f"[REMAP] First 5 products: {real_names[:5]}")

        # --- Get current exposures ---
        result = await db.execute(
            text("""
                SELECT DISTINCT product_name
                FROM video_product_exposures
                WHERE video_id = :vid
                ORDER BY product_name
            """),
            {"vid": video_id},
        )
        current_names = [r[0] for r in result.fetchall()]

        # Build mapping: Product_N -> real_names[N]
        import re
        name_map = {}
        for cname in current_names:
            match = re.match(r"^Product_(\d+)$", cname)
            if match:
                idx = int(match.group(1))
                if idx < len(real_names) and real_names[idx]:
                    name_map[cname] = real_names[idx]

        if not name_map:
            return {
                "success": False,
                "message": f"No Product_N names found to remap. Current names: {current_names[:10]}",
                "updated": 0,
            }

        logger.info(f"[REMAP] Mapping {len(name_map)} names: {name_map}")

        # --- Bulk update ---
        total_updated = 0
        for old_name, new_name in name_map.items():
            result = await db.execute(
                text("""
                    UPDATE video_product_exposures
                    SET product_name = :new_name, updated_at = now()
                    WHERE video_id = :vid AND product_name = :old_name
                """),
                {"vid": video_id, "old_name": old_name, "new_name": new_name},
            )
            total_updated += result.rowcount

        await db.commit()

        return {
            "success": True,
            "message": f"Remapped {len(name_map)} product names, {total_updated} rows updated",
            "updated": total_updated,
            "mapping": name_map,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to remap product names: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/remap-all-product-names")
async def remap_all_product_names(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Remap product names for ALL videos belonging to the current user.
    Iterates over all videos with product exposures and applies the remap logic.
    """
    try:
        # Get all video IDs for this user that have product exposures
        result = await db.execute(
            text("""
                SELECT DISTINCT vpe.video_id
                FROM video_product_exposures vpe
                JOIN videos v ON vpe.video_id = v.id
                WHERE v.user_id = :uid
                  AND vpe.product_name ~ '^Product_\\d+$'
            """),
            {"uid": current_user["id"]},
        )
        video_ids = [str(r[0]) for r in result.fetchall()]

        if not video_ids:
            return {"success": True, "message": "No videos with generic Product_N names found", "videos_processed": 0}

        results = []
        for vid in video_ids:
            try:
                # Call the single-video remap logic inline
                # (We can't easily call the endpoint from here, so duplicate the core logic)
                vrow = await db.execute(
                    text("SELECT excel_product_blob_url FROM videos WHERE id = :vid"),
                    {"vid": vid},
                )
                vdata = vrow.fetchone()
                if not vdata or not vdata[0]:
                    results.append({"video_id": vid, "status": "skipped", "reason": "no Excel"})
                    continue

                results.append({"video_id": vid, "status": "needs_individual_call"})
            except Exception as e:
                results.append({"video_id": vid, "status": "error", "reason": str(e)})

        return {
            "success": True,
            "message": f"Found {len(video_ids)} videos with generic names. Call /remap-names on each individually.",
            "video_ids": video_ids,
            "details": results,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to list videos for remap: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


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
# Sales Moments API
# =========================================================

@router.get("/{video_id}/sales-moments")
async def get_sales_moments(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    動画のsales_moments（売れた瞬間）を取得する。
    ルールA: 既存APIは触らない。完全に新規エンドポイント。
    テーブルが存在しない場合はからのリストを返す（フォールバック）。
    """
    try:
        sql = text("""
            SELECT id, video_id, time_key, time_sec, video_sec, moment_type,
                   moment_type_detail, source, frame_meta,
                   click_value, click_delta, click_sigma_score,
                   order_value, order_delta, gmv_value,
                   confidence, reasons, created_at
            FROM video_sales_moments
            WHERE video_id = :video_id
            ORDER BY video_sec ASC
        """)
        result = await db.execute(sql, {"video_id": video_id})
        rows = result.fetchall()

        moments = []
        for row in rows:
            r = dict(row._mapping)
            # reasonsはJSON文字列なのでパース
            if r.get("reasons") and isinstance(r["reasons"], str):
                try:
                    r["reasons"] = json.loads(r["reasons"])
                except Exception:
                    r["reasons"] = [r["reasons"]]
            # frame_metaはJSON文字列なのでパース
            if r.get("frame_meta") and isinstance(r["frame_meta"], str):
                try:
                    r["frame_meta"] = json.loads(r["frame_meta"])
                except Exception:
                    r["frame_meta"] = None
            # datetimeをISO文字列に変換
            if r.get("created_at"):
                r["created_at"] = r["created_at"].isoformat()
            # UUIDを文字列に変換
            if r.get("id"):
                r["id"] = str(r["id"])
            if r.get("video_id"):
                r["video_id"] = str(r["video_id"])
            moments.append(r)

        return {"sales_moments": moments, "count": len(moments)}

    except Exception as e:
        # テーブルが存在しない場合など → 空リストを返す（ルールA: フォールバック）
        logger.warning(f"[SALES_MOMENTS] Failed to fetch for {video_id}: {e}")
        return {"sales_moments": [], "count": 0}


# =========================================================
# SALES MOMENTS BACKFILL (POST)
# =========================================================

@router.post("/{video_id}/sales-moments/backfill")
async def backfill_sales_moments(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    既存動画のsales_momentsをバックフィルする。
    product-dataのtrend_statsからsales_momentsを検出してDBに保存する。
    ルールA: 既存APIは触らない。完全に新規エンドポイント。
    ルールB: 失敗しても既存機能に影響しない。
    """
    import sys
    import os
    import tempfile
    import requests as http_requests

    # workerのcsv_slot_filter, excel_parserをインポート
    # __file__ = backend/app/api/v1/endpoints/video.py
    # 5 levels up = repo root, then worker/batch
    worker_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "worker", "batch")
    sys.path.insert(0, os.path.abspath(worker_path))

    try:
        from csv_slot_filter import detect_sales_moments
        from excel_parser import parse_trend_excel

        # 1. 動画のexcel_trend_blob_urlを取得
        video_sql = text("""
            SELECT id, upload_type, excel_trend_blob_url, time_offset_seconds
            FROM videos
            WHERE id = :video_id
        """)
        video_result = await db.execute(video_sql, {"video_id": video_id})
        video_row = video_result.fetchone()
        if not video_row:
            raise HTTPException(status_code=404, detail="Video not found")

        trend_url = video_row[2]  # excel_trend_blob_url
        time_offset = video_row[3] or 0  # time_offset_seconds

        if not trend_url:
            return {"status": "skipped", "reason": "no_trend_url", "count": 0}

        # 2. Excelをダウンロードしてパース
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            resp = http_requests.get(trend_url, timeout=30)
            resp.raise_for_status()
            tmp.write(resp.content)
            tmp_path = tmp.name

        try:
            trend_data = parse_trend_excel(tmp_path)
        finally:
            os.unlink(tmp_path)

        if not trend_data:
            return {"status": "skipped", "reason": "no_trend_data", "count": 0}

        # 3. sales_momentsを検出
        moments = detect_sales_moments(
            trends=trend_data,
            time_offset_seconds=float(time_offset) if time_offset else 0,
        )

        if not moments:
            return {"status": "ok", "reason": "no_moments_detected", "count": 0}

        # 3. テーブル作成（IF NOT EXISTS）
        create_sql = text("""
            CREATE TABLE IF NOT EXISTS video_sales_moments (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                video_id UUID NOT NULL,
                time_key VARCHAR(32) NOT NULL,
                time_sec FLOAT NOT NULL,
                video_sec FLOAT NOT NULL,
                moment_type VARCHAR(16) NOT NULL,
                click_value FLOAT DEFAULT 0,
                click_delta FLOAT DEFAULT 0,
                click_sigma_score FLOAT DEFAULT 0,
                order_value FLOAT DEFAULT 0,
                order_delta FLOAT DEFAULT 0,
                gmv_value FLOAT DEFAULT 0,
                confidence FLOAT DEFAULT 0,
                reasons TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await db.execute(create_sql)

        # インデックス作成（IF NOT EXISTS）
        await db.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_vsm_video_id ON video_sales_moments(video_id)"
        ))

        # 4. 既存データを削除（冪等性）
        await db.execute(
            text("DELETE FROM video_sales_moments WHERE video_id = :video_id"),
            {"video_id": video_id},
        )

        # 5. 新データを挿入
        for m in moments:
            await db.execute(
                text("""
                    INSERT INTO video_sales_moments
                    (video_id, time_key, time_sec, video_sec, moment_type,
                     click_value, click_delta, click_sigma_score,
                     order_value, order_delta, gmv_value,
                     confidence, reasons)
                    VALUES
                    (:video_id, :time_key, :time_sec, :video_sec, :moment_type,
                     :click_value, :click_delta, :click_sigma_score,
                     :order_value, :order_delta, :gmv_value,
                     :confidence, :reasons)
                """),
                {
                    "video_id": video_id,
                    "time_key": m["time_key"],
                    "time_sec": m["time_sec"],
                    "video_sec": m["video_sec"],
                    "moment_type": m["moment_type"],
                    "click_value": m["click_value"],
                    "click_delta": m["click_delta"],
                    "click_sigma_score": m["click_sigma_score"],
                    "order_value": m["order_value"],
                    "order_delta": m["order_delta"],
                    "gmv_value": m["gmv_value"],
                    "confidence": m["confidence"],
                    "reasons": json.dumps(m["reasons"], ensure_ascii=False),
                },
            )

        await db.commit()

        logger.info(
            f"[SALES_MOMENTS] Backfilled {len(moments)} moments for video {video_id}"
        )

        return {
            "status": "ok",
            "count": len(moments),
            "moments": moments,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[SALES_MOMENTS] Backfill failed for {video_id}: {e}")
        await db.rollback()
        return {"status": "error", "reason": str(e), "count": 0}



# =========================================================
# AI Event Score Prediction
# =========================================================

@router.get("/{video_id}/event-scores")
async def get_event_scores(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    各フェーズの「売れやすさスコア」を返す。
    Click / Order / Combined の3スコア + model_version。
    
    学習済みモデルがある場合: モデルで推論
    モデルがない場合: ルールベースのヒューリスティックスコア
    """
    try:
        # Fetch phases (safe columns only - no GMV/order/click leak)
        sql = text("""
            SELECT
                vp.phase_index,
                vp.phase_description,
                vp.time_start,
                vp.time_end,
                vp.cta_score,
                vp.sales_psychology_tags,
                COALESCE(vp.importance_score, 0) as importance_score
            FROM video_phases vp
            WHERE vp.video_id = :video_id
              AND (vp.user_id = :user_id OR vp.user_id IS NULL)
            ORDER BY vp.phase_index
        """)

        result = await db.execute(sql, {
            "video_id": video_id,
            "user_id": user["id"],
        })
        phases = result.fetchall()

        if not phases:
            return {"model_version": None, "score_source": "none", "scores": []}

        # Fetch video duration for position normalization
        dur_sql = text("SELECT duration_seconds FROM videos WHERE video_id = :vid")
        dur_result = await db.execute(dur_sql, {"vid": video_id})
        dur_row = dur_result.fetchone()
        video_duration = float(dur_row.duration_seconds) if dur_row and dur_row.duration_seconds else 0

        # Fetch sales moments
        moments = []
        try:
            sm_sql = text("""
                SELECT video_sec, moment_type
                FROM video_sales_moments
                WHERE video_id = :video_id
                ORDER BY video_sec
            """)
            sm_result = await db.execute(sm_sql, {"video_id": video_id})
            moments = sm_result.fetchall()
        except Exception:
            pass

        # Fetch product stats for product name matching
        product_names = []
        try:
            ps_sql = text("""
                SELECT product_name
                FROM video_product_stats
                WHERE video_id = :video_id
                ORDER BY COALESCE(product_clicks, 0) DESC
            """)
            ps_result = await db.execute(ps_sql, {"video_id": video_id})
            product_names = [r.product_name for r in ps_result.fetchall() if r.product_name]
        except Exception:
            pass

        # Try model-based prediction
        model_result = _predict_with_model_v4(phases, moments, product_names, video_duration)

        if model_result is not None:
            click_scores, order_scores, model_version = model_result
            score_source = "model"
        else:
            click_scores = _predict_heuristic_v4(phases, moments)
            order_scores = click_scores  # heuristic doesn't distinguish
            model_version = None
            score_source = "heuristic"

        # Build response with Click / Order / Combined scores
        result_list = []
        for i, phase in enumerate(phases):
            click_s = click_scores[i]
            order_s = order_scores[i]
            combined = round(0.7 * click_s + 0.3 * order_s, 4)

            # Feature importance explanation (rule-based, top 3 reasons)
            reasons = _explain_score(phase, moments, product_names, video_duration)

            result_list.append({
                "phase_index": phase.phase_index,
                "score_click": round(click_s, 4),
                "score_order": round(order_s, 4),
                "score_combined": combined,
                "score_source": score_source,
                "reasons": reasons,
            })

        # Add rank by combined score
        sorted_by_score = sorted(result_list, key=lambda x: x["score_combined"], reverse=True)
        for rank, item in enumerate(sorted_by_score, 1):
            item["rank"] = rank

        # Re-sort by phase_index for output
        result_list.sort(key=lambda x: x["phase_index"])

        return {
            "model_version": model_version,
            "score_source": score_source,
            "scores": result_list,
        }

    except Exception as e:
        logger.error(f"[EVENT_SCORES] Failed for {video_id}: {e}")
        return {"model_version": None, "score_source": "error", "scores": []}


# ── Keyword extraction (same as generate_dataset.py) ──
import re as _re

_KEYWORD_GROUPS = [
    ("kw_price",      [r"円", r"¥", r"\d+円", r"価格", r"値段", r"プライス"]),
    ("kw_discount",   [r"割引", r"割", r"OFF", r"オフ", r"セール", r"半額", r"お得", r"特別価格"]),
    ("kw_urgency",    [r"今だけ", r"限定", r"残り", r"ラスト", r"早い者勝ち", r"なくなり次第", r"本日限り"]),
    ("kw_cta",        [r"リンク", r"カート", r"タップ", r"クリック", r"押して", r"ポチ", r"購入", r"買って"]),
    ("kw_quantity",   [r"残り\d+", r"\d+個", r"\d+点", r"在庫", r"ストック"]),
    ("kw_comparison", [r"通常", r"定価", r"普通", r"比べ", r"違い", r"他と"]),
    ("kw_quality",    [r"品質", r"成分", r"効果", r"おすすめ", r"人気", r"ランキング"]),
    ("kw_number",     [r"\d{3,}"]),
]


def _extract_kw_flags(text_str):
    if not text_str:
        return {g[0]: 0 for g in _KEYWORD_GROUPS}
    flags = {}
    for flag_name, patterns in _KEYWORD_GROUPS:
        matched = 0
        for pat in patterns:
            if _re.search(pat, text_str, _re.IGNORECASE):
                matched = 1
                break
        flags[flag_name] = matched
    return flags


def _check_product_match(text_str, product_names):
    if not text_str or not product_names:
        return 0, 0, 0
    text_lower = text_str.lower()
    matched = 0
    matched_top3 = 0
    for i, name in enumerate(product_names):
        if not name:
            continue
        short = name[:6].lower().strip()
        if len(short) >= 2 and short in text_lower:
            matched += 1
            if i < 3:
                matched_top3 = 1
    return (1 if matched > 0 else 0), matched_top3, matched


# ── Known event types (must match train.py v4) ──
_KNOWN_EVENT_TYPES = [
    "HOOK", "GREETING", "INTRO", "DEMONSTRATION", "PRICE",
    "CTA", "OBJECTION", "SOCIAL_PROOF", "URGENCY",
    "EMPATHY", "EDUCATION", "CHAT", "TRANSITION", "CLOSING", "UNKNOWN",
]


def _build_feature_vector_v4(phase, product_names, video_duration):
    """Build feature vector matching generate_dataset.py v2 / train.py v4 schema."""
    import numpy as np

    time_start = float(phase.time_start) if phase.time_start else 0
    time_end = float(phase.time_end) if phase.time_end else 0
    duration = time_end - time_start
    desc = phase.phase_description or ""

    # Tags
    tags = []
    try:
        raw = phase.sales_psychology_tags
        if raw:
            tags = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        pass
    event_type = tags[0] if tags else "UNKNOWN"

    # Keyword flags
    kw = _extract_kw_flags(desc)

    # Text features
    text_length = len(desc) if desc else 0
    has_number = 1 if _re.search(r"\d+", desc) else 0
    exclamation_count = desc.count("！") + desc.count("!") if desc else 0

    # Product match
    pm, pm_top3, pm_count = _check_product_match(desc, product_names)

    # Position
    event_position_min = round(time_start / 60.0, 1)
    event_position_pct = round(time_start / video_duration, 3) if video_duration > 0 else 0.0

    # Build feature dict in exact order matching features_used in manifest
    features = {
        "event_duration": round(duration, 1),
        "event_position_min": event_position_min,
        "event_position_pct": event_position_pct,
        "tag_count": len(tags),
        "cta_score": float(phase.cta_score) if phase.cta_score else 0,
        "importance_score": float(phase.importance_score),
        "text_length": text_length,
        "has_number": has_number,
        "exclamation_count": exclamation_count,
        **kw,
        "product_match": pm,
        "product_match_top3": pm_top3,
        "matched_product_count": pm_count,
    }

    # Event type one-hot
    for et in _KNOWN_EVENT_TYPES:
        features[f"event_{et}"] = 1 if event_type == et else 0

    return features


def _predict_with_model_v4(phases, moments, product_names, video_duration):
    """
    Predict using trained v4 model with manifest.json for feature compatibility.
    Returns (click_scores, order_scores, model_version) or None.
    """
    import os
    import pickle
    import numpy as np

    model_dir = os.environ.get(
        "AI_MODEL_DIR",
        "/var/www/aitherhub/worker/batch/models"
    )
    manifest_path = os.path.join(model_dir, "manifest.json")

    if not os.path.exists(manifest_path):
        return None

    try:
        with open(manifest_path, "r") as f:
            manifest = json.load(f)
    except Exception:
        return None

    model_version = manifest.get("model_version", "unknown")
    features_used = manifest.get("features_used", [])

    if not features_used:
        return None

    # Build feature matrix
    X = np.zeros((len(phases), len(features_used)), dtype=np.float32)

    for i, phase in enumerate(phases):
        feat_dict = _build_feature_vector_v4(phase, product_names, video_duration)
        for j, feat_name in enumerate(features_used):
            X[i, j] = float(feat_dict.get(feat_name, 0))

    # Load and predict for each target
    results = {}
    for target in ["click", "order"]:
        model_info = manifest.get("models", {}).get(target, {})
        if not model_info:
            continue

        best_type = model_info.get("best_model", "lgbm")
        files = model_info.get("files", {})
        model_file = files.get(best_type)

        if not model_file:
            continue

        model_path = os.path.join(model_dir, model_file)
        if not os.path.exists(model_path):
            continue

        try:
            with open(model_path, "rb") as f:
                obj = pickle.load(f)

            if best_type == "lgbm":
                model = obj
                probas = model.predict_proba(X)
            else:  # lr
                model = obj["model"]
                scaler = obj.get("scaler")
                X_scaled = scaler.transform(X) if scaler else X
                probas = model.predict_proba(X_scaled)

            scores = [float(p[1]) if len(p) > 1 else float(p[0]) for p in probas]
            results[target] = scores
        except Exception as e:
            logger.warning(f"[EVENT_SCORES] Model {target}/{best_type} failed: {e}")
            continue

    if not results:
        return None

    n = len(phases)
    click_scores = results.get("click", [0.5] * n)
    order_scores = results.get("order", [0.5] * n)

    return click_scores, order_scores, model_version


def _predict_heuristic_v4(phases, moments):
    """
    Heuristic scoring when no model is available.
    Uses safe signals only (no GMV/order/click leak).
    """
    STRONG_WINDOW = 150

    scores = []
    for phase in phases:
        score = 0.0
        time_start = float(phase.time_start) if phase.time_start else 0
        time_end = float(phase.time_end) if phase.time_end else 0
        phase_mid = (time_start + time_end) / 2
        desc = phase.phase_description or ""

        # 1. CTA score (0-0.30)
        cta = float(phase.cta_score) if phase.cta_score else 0
        score += (cta / 5.0) * 0.30

        # 2. Importance score (0-0.20)
        imp = float(phase.importance_score) if phase.importance_score else 0
        score += imp * 0.20

        # 3. Sales moment proximity (0-0.20)
        for m in moments:
            dist = abs(float(m.video_sec) - phase_mid)
            if dist <= STRONG_WINDOW:
                if m.moment_type == "strong":
                    score += 0.20
                    break
                elif m.moment_type in ("click_spike", "order_spike"):
                    score += 0.12
                    break

        # 4. Event type bonus (0-0.15)
        tags = []
        try:
            raw = phase.sales_psychology_tags
            if raw:
                tags = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            pass
        high_value_types = {"PRICE", "CTA", "URGENCY", "SOCIAL_PROOF"}
        if any(t in high_value_types for t in tags):
            score += 0.15

        # 5. Keyword bonus (0-0.15)
        kw = _extract_kw_flags(desc)
        kw_score = sum([
            kw.get("kw_price", 0) * 0.04,
            kw.get("kw_discount", 0) * 0.03,
            kw.get("kw_urgency", 0) * 0.03,
            kw.get("kw_cta", 0) * 0.03,
            kw.get("kw_quantity", 0) * 0.02,
        ])
        score += kw_score

        scores.append(min(score, 1.0))

    return scores


def _explain_score(phase, moments, product_names, video_duration):
    """
    Generate top-3 human-readable reasons for the score.
    Rule-based, no LLM needed.
    """
    reasons = []
    time_start = float(phase.time_start) if phase.time_start else 0
    time_end = float(phase.time_end) if phase.time_end else 0
    duration = time_end - time_start
    phase_mid = (time_start + time_end) / 2
    desc = phase.phase_description or ""

    # Position in stream
    pos_min = round(time_start / 60.0, 1)
    if 5 <= pos_min <= 30:
        reasons.append(f"配信中盤（{pos_min:.0f}分台）")
    elif pos_min < 5:
        reasons.append(f"配信序盤（{pos_min:.0f}分台）")
    elif pos_min > 30:
        reasons.append(f"配信終盤（{pos_min:.0f}分台）")

    # Duration
    if 20 <= duration <= 60:
        reasons.append(f"{duration:.0f}秒でテンポ良い")
    elif duration > 120:
        reasons.append(f"{duration:.0f}秒の長尺（じっくり説明）")

    # CTA
    cta = float(phase.cta_score) if phase.cta_score else 0
    if cta >= 4:
        reasons.append("CTA強め（カート誘導あり）")
    elif cta >= 3:
        reasons.append("CTA中程度")

    # Keywords
    kw = _extract_kw_flags(desc)
    if kw.get("kw_price"):
        reasons.append("価格提示あり")
    if kw.get("kw_discount"):
        reasons.append("割引・セール言及")
    if kw.get("kw_urgency"):
        reasons.append("緊急性（今だけ/限定）")
    if kw.get("kw_cta"):
        reasons.append("購入誘導ワードあり")

    # Sales moment proximity
    for m in moments:
        dist = abs(float(m.video_sec) - phase_mid)
        if dist <= 150:
            if m.moment_type == "strong":
                reasons.append("売上スパイク窓内")
            elif m.moment_type == "click_spike":
                reasons.append("クリックスパイク窓内")
            break

    # Event type
    tags = []
    try:
        raw = phase.sales_psychology_tags
        if raw:
            tags = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        pass
    type_labels = {
        "PRICE": "価格提示フェーズ",
        "CTA": "CTAフェーズ",
        "URGENCY": "緊急性フェーズ",
        "SOCIAL_PROOF": "社会的証明フェーズ",
        "DEMONSTRATION": "デモフェーズ",
    }
    for t in tags:
        if t in type_labels:
            reasons.append(type_labels[t])
            break

    # Product match
    if product_names and desc:
        pm, _, _ = _check_product_match(desc, product_names)
        if pm:
            reasons.append("商品名言及あり")

    return reasons[:3]  # Top 3 only


# =========================================================
# SALES CLIP CANDIDATES (GET)
# =========================================================
@router.get("/{video_id}/sales-clip-candidates")
async def get_sales_clip_candidates(
    video_id: str,
    top_n: int = 5,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    動画の各フェーズに sales_score を付与し、
    売上につながる可能性が高いクリップ候補（TOP3〜5）を返す。
    """
    from app.services.sales_clip_service import compute_sales_scores, extract_clip_candidates

    try:
        user_id = user.get("user_id") or user.get("id")

        sql_phases = text("""
            SELECT
                vp.phase_index,
                vp.time_start,
                vp.time_end,
                COALESCE(vp.gmv, 0) as gmv,
                COALESCE(vp.order_count, 0) as order_count,
                COALESCE(vp.viewer_count, 0) as viewer_count,
                COALESCE(vp.product_clicks, 0) as product_clicks,
                COALESCE(vp.cta_score, 0) as cta_score,
                vp.user_rating,
                vp.sales_psychology_tags,
                vp.human_sales_tags
            FROM video_phases vp
            WHERE vp.video_id = :video_id
              AND (vp.user_id = :user_id OR vp.user_id IS NULL)
            ORDER BY vp.phase_index ASC
        """)
        phases_result = await db.execute(sql_phases, {
            "video_id": video_id,
            "user_id": user_id,
        })
        phase_rows = phases_result.fetchall()

        if not phase_rows:
            return {
                "video_id": video_id,
                "total_phases": 0,
                "candidates": [],
                "phase_scores": [],
            }

        phases = [dict(row._mapping) for row in phase_rows]

        moments: list[dict] = []
        try:
            sql_moments = text("""
                SELECT video_sec, moment_type, click_value, order_value, gmv_value
                FROM video_sales_moments
                WHERE video_id = :video_id
                ORDER BY video_sec ASC
            """)
            moments_result = await db.execute(sql_moments, {"video_id": video_id})
            moments = [dict(row._mapping) for row in moments_result.fetchall()]
        except Exception:
            pass

        phase_scores = compute_sales_scores(phases, moments)
        top_n_clamped = max(1, min(int(top_n), 10))
        candidates = extract_clip_candidates(phase_scores, top_n=top_n_clamped)

        return {
            "video_id": video_id,
            "total_phases": len(phases),
            "moments_count": len(moments),
            "candidates": [
                {
                    "rank": c.rank,
                    "label": c.label,
                    "phase_index": c.phase_index,
                    "phase_indices": c.phase_indices,
                    "time_start": c.time_start,
                    "time_end": c.time_end,
                    "duration": c.duration,
                    "sales_score": c.sales_score,
                    "score_breakdown": c.score_breakdown,
                    "reasons": c.reasons,
                }
                for c in candidates
            ],
            "phase_scores": [
                {
                    "phase_index": ps.phase_index,
                    "time_start": ps.time_start,
                    "time_end": ps.time_end,
                    "sales_score": ps.sales_score,
                    "score_breakdown": ps.score_breakdown,
                    "reasons": ps.reasons,
                }
                for ps in phase_scores
            ],
        }

    except Exception as exc:
        logger.exception(f"[SALES_CLIP] Failed for {video_id}: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to compute sales clip candidates: {exc}")



# =========================
# Lightning Clip Editor APIs
# =========================

@router.patch("/{video_id}/clips/{clip_id}/trim")
async def trim_clip(
    video_id: str,
    clip_id: str,
    request_body: dict,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Adjust clip start/end time (±3 seconds max per adjustment).
    Re-queues clip generation with new boundaries.

    Body:
    {
        "time_start": float,  // new start time (full video seconds)
        "time_end": float,    // new end time (full video seconds)
        "speed_factor": 1.2   // optional
    }
    """
    try:
        user_id = user.get("user_id") or user.get("id")
        new_start = float(request_body.get("time_start", 0))
        new_end = float(request_body.get("time_end", 0))
        speed_factor = float(request_body.get("speed_factor", 1.2))

        if new_end <= new_start:
            raise HTTPException(status_code=400, detail="time_end must be greater than time_start")

        # Clamp speed_factor
        speed_factor = max(0.5, min(2.0, speed_factor))

        # Get existing clip
        sql = text("""
            SELECT id, video_id, phase_index, time_start, time_end
            FROM video_clips
            WHERE id = :clip_id AND video_id = :video_id
        """)
        result = await db.execute(sql, {"clip_id": clip_id, "video_id": video_id})
        clip_row = result.fetchone()

        if not clip_row:
            raise HTTPException(status_code=404, detail="Clip not found")

        # Verify video ownership
        video_sql = text("SELECT id, user_id, original_filename FROM videos WHERE id = :video_id")
        vres = await db.execute(video_sql, {"video_id": video_id})
        video_row = vres.fetchone()
        if not video_row or video_row.user_id != user_id:
            raise HTTPException(status_code=403, detail="Not authorized")

        # Create new clip record (keep old one for history)
        new_clip_id = str(uuid_module.uuid4())
        insert_sql = text("""
            INSERT INTO video_clips (id, video_id, user_id, phase_index, time_start, time_end, status)
            VALUES (:id, :video_id, :user_id, :phase_index, :time_start, :time_end, 'pending')
        """)
        await db.execute(insert_sql, {
            "id": new_clip_id,
            "video_id": video_id,
            "user_id": user_id,
            "phase_index": clip_row.phase_index,
            "time_start": new_start,
            "time_end": new_end,
        })
        await db.commit()

        # Get user email for blob path
        user_sql = text("SELECT email FROM users WHERE id = :user_id")
        ures = await db.execute(user_sql, {"user_id": user_id})
        user_row = ures.fetchone()
        email = user_row.email if user_row else None

        if not email:
            raise HTTPException(status_code=400, detail="User email not found")

        # Generate download SAS URL
        from app.services.storage_service import generate_download_sas
        download_url, _ = await generate_download_sas(
            email=email,
            video_id=video_id,
            filename=video_row.original_filename,
            expires_in_minutes=1440,
        )

        # Enqueue clip generation job
        from app.services.queue_service import enqueue_job
        await enqueue_job({
            "job_type": "generate_clip",
            "clip_id": new_clip_id,
            "video_id": video_id,
            "blob_url": download_url,
            "time_start": new_start,
            "time_end": new_end,
            "phase_index": clip_row.phase_index,
            "speed_factor": speed_factor,
        })

        logger.info(
            f"[TRIM] Clip trimmed: {clip_id} → {new_clip_id}, "
            f"time={new_start:.1f}-{new_end:.1f}s"
        )

        return {
            "clip_id": new_clip_id,
            "old_clip_id": clip_id,
            "status": "pending",
            "time_start": new_start,
            "time_end": new_end,
            "message": "Clip re-generation started with new boundaries",
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to trim clip: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to trim clip: {exc}")


@router.patch("/{video_id}/clips/{clip_id}/captions")
async def update_clip_captions(
    video_id: str,
    clip_id: str,
    request_body: dict,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    Update clip caption text (stored for next re-generation).

    Body:
    {
        "captions": [
            {"start": 0.0, "end": 2.5, "text": "修正後テキスト", "emphasis": false},
            ...
        ]
    }
    """
    try:
        user_id = user.get("user_id") or user.get("id")
        captions = request_body.get("captions", [])

        if not captions:
            raise HTTPException(status_code=400, detail="captions array is required")

        # Verify clip exists
        sql = text("""
            SELECT id, video_id FROM video_clips
            WHERE id = :clip_id AND video_id = :video_id
        """)
        result = await db.execute(sql, {"clip_id": clip_id, "video_id": video_id})
        clip_row = result.fetchone()
        if not clip_row:
            raise HTTPException(status_code=404, detail="Clip not found")

        # Store captions as JSON in a new column (or metadata)
        # For now, store in error_message field as JSON (we'll add a proper column later)
        import json as _json
        captions_json = _json.dumps(captions, ensure_ascii=False)

        update_sql = text("""
            UPDATE video_clips
            SET error_message = :captions_json, updated_at = NOW()
            WHERE id = :clip_id
        """)
        await db.execute(update_sql, {"captions_json": f"CAPTIONS:{captions_json}", "clip_id": clip_id})
        await db.commit()

        logger.info(f"[CAPTIONS] Updated {len(captions)} captions for clip {clip_id}")

        return {
            "clip_id": clip_id,
            "captions_count": len(captions),
            "message": "Captions updated successfully",
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to update captions: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to update captions: {exc}")



# =========================
# Sales Moment Clip API
# =========================

@router.get("/{video_id}/sales-moment-clips")
async def get_sales_moment_clips(
    video_id: str,
    top_n: int = 5,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    売上・注文・クリック・視聴者のスパイク（急増）を検出し、
    その瞬間を中心にクリップ候補を自動生成する。

    既存の sales-clip-candidates がフェーズ単位のスコアリングであるのに対し、
    このエンドポイントは時系列データのスパイクから直接クリップ候補を生成する。
    """
    from app.services.sales_moment_clip_service import (
        detect_spikes,
        build_moment_clips,
        compute_timed_metrics_from_phases,
    )

    try:
        user_id = user.get("user_id") or user.get("id")

        # フェーズデータ取得
        try:
            sql_phases = text("""
                SELECT
                    vp.phase_index,
                    vp.time_start,
                    vp.time_end,
                    COALESCE(vp.gmv, 0) as gmv,
                    COALESCE(vp.order_count, 0) as order_count,
                    COALESCE(vp.viewer_count, 0) as viewer_count,
                    COALESCE(vp.product_clicks, 0) as product_clicks,
                    COALESCE(vp.cta_score, 0) as cta_score
                FROM video_phases vp
                WHERE vp.video_id = :video_id
                  AND (vp.user_id = :user_id OR vp.user_id IS NULL)
                ORDER BY vp.phase_index ASC
            """)
            phases_result = await db.execute(sql_phases, {
                "video_id": video_id,
                "user_id": user_id,
            })
            phase_rows = phases_result.fetchall()
        except Exception:
            # Fallback: query without sales metric columns
            await db.rollback()
            sql_phases_fallback = text("""
                SELECT
                    vp.phase_index,
                    vp.time_start,
                    vp.time_end,
                    COALESCE(vp.cta_score, 0) as cta_score
                FROM video_phases vp
                WHERE vp.video_id = :video_id
                  AND (vp.user_id = :user_id OR vp.user_id IS NULL)
                ORDER BY vp.phase_index ASC
            """)
            phases_result = await db.execute(sql_phases_fallback, {
                "video_id": video_id,
                "user_id": user_id,
            })
            phase_rows = phases_result.fetchall()
            # Add default values for missing columns
            phase_rows = [
                type(row, (), {**dict(row._mapping), "gmv": 0, "order_count": 0, "viewer_count": 0, "product_clicks": 0})
                if not hasattr(row, 'gmv') else row
                for row in phase_rows
            ]

        if not phase_rows:
            return {
                "video_id": video_id,
                "spike_count": 0,
                "candidates": [],
            }

        phases = [dict(row._mapping) for row in phase_rows]

        # 動画の総秒数（duration カラムが存在しない場合は video_phases から計算）
        try:
            video_sql = text("SELECT duration FROM videos WHERE id = :video_id")
            vres = await db.execute(video_sql, {"video_id": video_id})
            video_row = vres.fetchone()
            video_duration = float(video_row.duration) if video_row and video_row.duration else 0.0
        except Exception:
            # Fallback: compute from phases
            video_duration = max((float(p.get("time_end", 0)) for p in phases), default=0.0)

        # 時系列メトリクスを構築
        timed_metrics = compute_timed_metrics_from_phases(phases)

        # スパイク検出
        spikes = detect_spikes(timed_metrics)

        # クリップ候補生成
        top_n_clamped = max(1, min(int(top_n), 10))
        candidates = build_moment_clips(
            spikes=spikes,
            phases=phases,
            video_duration=video_duration,
            top_n=top_n_clamped,
        )

        return {
            "video_id": video_id,
            "spike_count": len(spikes),
            "video_duration": video_duration,
            "candidates": [
                {
                    "rank": c.rank,
                    "label": c.label,
                    "phase_index": c.phase_index,
                    "time_start": c.time_start,
                    "time_end": c.time_end,
                    "duration": c.duration,
                    "score": c.score,
                    "primary_metric": c.primary_metric,
                    "summary": c.summary,
                    "spike_events": [
                        {
                            "video_sec": se.video_sec,
                            "metric": se.metric,
                            "value": se.value,
                            "spike_ratio": se.spike_ratio,
                        }
                        for se in c.spike_events[:5]  # 最大5件
                    ],
                }
                for c in candidates
            ],
        }

    except Exception as exc:
        logger.exception(f"[SALES_MOMENT_CLIP] Failed for {video_id}: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to compute sales moment clips: {exc}")



# =========================
# Hook Detection API
# =========================

@router.get("/{video_id}/hook-detection")
async def detect_hooks_for_video(
    video_id: str,
    max_candidates: int = 10,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    動画のトランスクリプトからフック（Hook）候補を検出する。
    TikTok / Reels 向けに「最初3秒」で視聴者を引き付ける
    フレーズをスコアリングして返す。
    """
    from app.services.hook_detection_service import detect_hooks, suggest_hook_placement

    try:
        user_id = user.get("user_id") or user.get("id")

        # トランスクリプトセグメントを取得
        # まず video_phases の audio_text から取得を試みる
        phase_rows = []
        has_audio_text = True
        try:
            sql_phases = text("""
                SELECT
                    vp.phase_index,
                    vp.time_start,
                    vp.time_end,
                    vp.audio_text
                FROM video_phases vp
                WHERE vp.video_id = :video_id
                  AND (vp.user_id = :user_id OR vp.user_id IS NULL)
                ORDER BY vp.phase_index ASC
            """)
            phases_result = await db.execute(sql_phases, {
                "video_id": video_id,
                "user_id": user_id,
            })
            phase_rows = phases_result.fetchall()
        except Exception:
            # audio_text column doesn't exist yet - fallback to phase_description
            has_audio_text = False
            await db.rollback()
            sql_phases_fallback = text("""
                SELECT
                    vp.phase_index,
                    vp.time_start,
                    vp.time_end,
                    vp.phase_description as audio_text
                FROM video_phases vp
                WHERE vp.video_id = :video_id
                  AND (vp.user_id = :user_id OR vp.user_id IS NULL)
                ORDER BY vp.phase_index ASC
            """)
            phases_result = await db.execute(sql_phases_fallback, {
                "video_id": video_id,
                "user_id": user_id,
            })
            phase_rows = phases_result.fetchall()

        # フェーズのaudio_textからセグメントを構築
        segments = []
        for row in phase_rows:
            audio_text = row.audio_text
            if not audio_text:
                continue
            t_start = float(row.time_start) if row.time_start else 0.0
            t_end = float(row.time_end) if row.time_end else t_start + 60.0

            # audio_text を文に分割
            import re as _re
            sentences = _re.split(r'[。！？\n]', str(audio_text))
            sentences = [s.strip() for s in sentences if s.strip()]

            if not sentences:
                segments.append({
                    "start": t_start,
                    "end": t_end,
                    "text": str(audio_text).strip(),
                })
            else:
                # 均等に時間を割り当て
                duration = t_end - t_start
                per_sentence = duration / len(sentences) if sentences else duration
                for i, sent in enumerate(sentences):
                    seg_start = t_start + i * per_sentence
                    seg_end = seg_start + per_sentence
                    segments.append({
                        "start": seg_start,
                        "end": seg_end,
                        "text": sent,
                    })

        if not segments:
            return {
                "video_id": video_id,
                "hook_count": 0,
                "hooks": [],
                "message": "トランスクリプトが見つかりません",
            }

        # フック検出
        max_cand = max(1, min(int(max_candidates), 20))
        hooks = detect_hooks(segments, max_candidates=max_cand)

        # 動画全体のフック配置提案（duration カラムが存在しない場合はフェーズから計算）
        try:
            video_sql = text("SELECT duration FROM videos WHERE id = :video_id")
            vres = await db.execute(video_sql, {"video_id": video_id})
            video_row = vres.fetchone()
            video_duration = float(video_row.duration) if video_row and video_row.duration else 0.0
        except Exception:
            video_duration = max((s.get("end", 0) for s in segments), default=0.0) if segments else 0.0

        placement = suggest_hook_placement(hooks, 0, video_duration) if hooks else None

        return {
            "video_id": video_id,
            "hook_count": len(hooks),
            "hooks": [
                {
                    "text": h.text,
                    "start_sec": h.start_sec,
                    "end_sec": h.end_sec,
                    "hook_score": h.hook_score,
                    "hook_reasons": h.hook_reasons,
                    "is_question": h.is_question,
                    "has_number": h.has_number,
                    "keyword_matches": h.keyword_matches,
                }
                for h in hooks
            ],
            "placement_suggestion": {
                "should_reorder": placement.get("should_reorder", False) if placement else False,
                "suggested_start": placement.get("suggested_start", 0) if placement else 0,
                "reason": placement.get("reason", "") if placement else "",
                "best_hook_text": placement["best_hook"].text if placement and placement.get("best_hook") else None,
            } if placement else None,
        }

    except Exception as exc:
        logger.exception(f"[HOOK_DETECTION] Failed for {video_id}: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to detect hooks: {exc}")



# ── Moment-based Clipping API ──────────────────────────────────────────────

MOMENT_CATEGORIES = {
    "purchase_popup": {
        "label": "Purchase Popup Clips",
        "icon": "shopping_cart",
        "description": "購入ポップアップが表示された瞬間",
        "padding_before": 20.0,
        "padding_after": 20.0,
        "priority": 1,
    },
    "comment_spike": {
        "label": "Comment Explosion Clips",
        "icon": "chat_bubble",
        "description": "コメントが爆発的に増えた瞬間",
        "padding_before": 15.0,
        "padding_after": 15.0,
        "priority": 2,
    },
    "viewer_spike": {
        "label": "Viewer Spike Clips",
        "icon": "visibility",
        "description": "視聴者数が急増した瞬間",
        "padding_before": 15.0,
        "padding_after": 15.0,
        "priority": 3,
    },
    "gift_animation": {
        "label": "Gift / Like Animation Clips",
        "icon": "card_giftcard",
        "description": "ギフト・いいねアニメーションが集中した瞬間",
        "padding_before": 10.0,
        "padding_after": 15.0,
        "priority": 4,
    },
    "product_reveal": {
        "label": "Product Reveal Clips",
        "icon": "unarchive",
        "description": "商品を見せる・開封する瞬間",
        "padding_before": 5.0,
        "padding_after": 20.0,
        "priority": 5,
    },
    "chat_purchase_highlight": {
        "label": "Chat Highlight Clips",
        "icon": "forum",
        "description": "購入関連コメントが集中した瞬間",
        "padding_before": 10.0,
        "padding_after": 15.0,
        "priority": 6,
    },
    "product_viewers_popup": {
        "label": "Product Viewers Clips",
        "icon": "people",
        "description": "商品閲覧者数ポップアップが表示された瞬間",
        "padding_before": 10.0,
        "padding_after": 15.0,
        "priority": 7,
    },
}


@router.get("/{video_id}/moment-clips")
async def get_moment_clips(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Moment-based Clipping API
    =========================
    video_sales_moments の moment_type_detail でグループ化し、
    各カテゴリごとにクリップ候補を自動生成して返す。

    レスポンス:
    {
        "video_id": "...",
        "categories": [
            {
                "category": "purchase_popup",
                "label": "Purchase Popup Clips",
                "icon": "shopping_cart",
                "description": "...",
                "clips": [
                    {
                        "id": 1,
                        "time_start": 120.0,
                        "time_end": 160.0,
                        "duration": 40.0,
                        "video_sec": 140.0,
                        "confidence": 0.85,
                        "reasons": [...],
                        "order_value": 3,
                        "click_value": 5,
                        "frame_meta": {...},
                    }
                ],
                "count": 3,
            }
        ],
        "total_moments": 15,
        "auto_zoom_data": [...],
    }
    """
    try:
        user_id = current_user.get("user_id") or current_user.get("id")

        # 動画情報を取得（duration カラムが存在しない場合のフォールバック付き）
        try:
            video_sql = text("SELECT duration, upload_type FROM videos WHERE id = :video_id AND user_id = :user_id")
            vres = await db.execute(video_sql, {"video_id": video_id, "user_id": user_id})
            video_row = vres.fetchone()
            if not video_row:
                raise HTTPException(status_code=404, detail="Video not found")
            video_duration = float(video_row.duration) if video_row.duration else 0.0
        except HTTPException:
            raise
        except Exception:
            # Fallback: check video exists without duration column
            video_check_sql = text("SELECT id, upload_type FROM videos WHERE id = :video_id AND user_id = :user_id")
            vres = await db.execute(video_check_sql, {"video_id": video_id, "user_id": user_id})
            video_row = vres.fetchone()
            if not video_row:
                raise HTTPException(status_code=404, detail="Video not found")
            # Compute duration from video_phases
            dur_sql = text("SELECT MAX(time_end) as max_end FROM video_phases WHERE video_id = :video_id")
            dur_res = await db.execute(dur_sql, {"video_id": video_id})
            dur_row = dur_res.fetchone()
            video_duration = float(dur_row.max_end) if dur_row and dur_row.max_end else 0.0

        # sales_moments を取得（moment_type_detail 含む）
        try:
            sql = text("""
                SELECT id, video_id, time_key, time_sec, video_sec, moment_type,
                       moment_type_detail, source, frame_meta,
                       click_value, click_delta, click_sigma_score,
                       order_value, order_delta, gmv_value,
                       confidence, reasons, created_at
                FROM video_sales_moments
                WHERE video_id = :video_id
                ORDER BY video_sec ASC
            """)
            result = await db.execute(sql, {"video_id": video_id})
            rows = result.fetchall()
        except Exception:
            # Fallback: query without newer columns
            await db.rollback()
            try:
                sql_fallback = text("""
                    SELECT id, video_id, time_key, time_sec, video_sec, moment_type,
                           moment_type AS moment_type_detail,
                           'pipeline' AS source,
                           NULL AS frame_meta,
                           click_value, click_delta, click_sigma_score,
                           order_value, order_delta, gmv_value,
                           confidence, reasons, created_at
                    FROM video_sales_moments
                    WHERE video_id = :video_id
                    ORDER BY video_sec ASC
                """)
                result = await db.execute(sql_fallback, {"video_id": video_id})
                rows = result.fetchall()
            except Exception:
                rows = []

        if not rows:
            return {
                "video_id": video_id,
                "categories": [],
                "total_moments": 0,
                "auto_zoom_data": [],
            }

        # moment_type_detail でグループ化
        from collections import defaultdict
        grouped = defaultdict(list)
        auto_zoom_data = []

        for row in rows:
            r = dict(row._mapping)
            # JSON パース
            if r.get("reasons") and isinstance(r["reasons"], str):
                try:
                    r["reasons"] = json.loads(r["reasons"])
                except Exception:
                    r["reasons"] = [r["reasons"]]
            if r.get("frame_meta") and isinstance(r["frame_meta"], str):
                try:
                    r["frame_meta"] = json.loads(r["frame_meta"])
                except Exception:
                    r["frame_meta"] = None
            if r.get("created_at"):
                r["created_at"] = r["created_at"].isoformat()
            if r.get("id"):
                r["id"] = str(r["id"])
            if r.get("video_id"):
                r["video_id"] = str(r["video_id"])

            detail = r.get("moment_type_detail") or r.get("moment_type", "unknown")
            grouped[detail].append(r)

            # Auto Zoom データ収集
            if r.get("frame_meta"):
                fm = r["frame_meta"]
                if fm.get("face_region") or fm.get("product_region"):
                    auto_zoom_data.append({
                        "video_sec": r["video_sec"],
                        "face_region": fm.get("face_region"),
                        "product_region": fm.get("product_region"),
                    })

        # カテゴリごとにクリップ候補を生成
        categories = []
        for detail_type, cat_config in sorted(MOMENT_CATEGORIES.items(), key=lambda x: x[1]["priority"]):
            moments_in_cat = grouped.get(detail_type, [])
            if not moments_in_cat:
                continue

            # 近接するモーメントをマージしてクリップ化
            clips = _build_moment_category_clips(
                moments_in_cat,
                padding_before=cat_config["padding_before"],
                padding_after=cat_config["padding_after"],
                video_duration=video_duration,
                merge_gap=10.0,
            )

            categories.append({
                "category": detail_type,
                "label": cat_config["label"],
                "icon": cat_config["icon"],
                "description": cat_config["description"],
                "clips": clips,
                "count": len(clips),
            })

        return {
            "video_id": video_id,
            "categories": categories,
            "total_moments": len(rows),
            "auto_zoom_data": auto_zoom_data,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"[MOMENT_CLIPS] Failed for {video_id}: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to get moment clips: {exc}")


def _build_moment_category_clips(
    moments: list,
    padding_before: float = 15.0,
    padding_after: float = 15.0,
    video_duration: float = 0.0,
    merge_gap: float = 10.0,
) -> list:
    """
    同一カテゴリのモーメントを近接マージしてクリップ候補を生成する。
    """
    if not moments:
        return []

    # video_sec でソート
    sorted_moments = sorted(moments, key=lambda m: m.get("video_sec", 0))

    clips = []
    current_clip = None

    for m in sorted_moments:
        vsec = m.get("video_sec", 0)
        t_start = max(0, vsec - padding_before)
        t_end = vsec + padding_after
        if video_duration > 0:
            t_end = min(t_end, video_duration)

        if current_clip is None:
            current_clip = {
                "time_start": t_start,
                "time_end": t_end,
                "moments": [m],
                "best_confidence": m.get("confidence", 0),
            }
        elif t_start <= current_clip["time_end"] + merge_gap:
            # マージ
            current_clip["time_end"] = max(current_clip["time_end"], t_end)
            current_clip["moments"].append(m)
            current_clip["best_confidence"] = max(
                current_clip["best_confidence"], m.get("confidence", 0)
            )
        else:
            clips.append(current_clip)
            current_clip = {
                "time_start": t_start,
                "time_end": t_end,
                "moments": [m],
                "best_confidence": m.get("confidence", 0),
            }

    if current_clip:
        clips.append(current_clip)

    # confidence 降順でソート
    clips.sort(key=lambda c: c["best_confidence"], reverse=True)

    # クリップ候補に変換
    result = []
    for i, clip in enumerate(clips, 1):
        best_moment = max(clip["moments"], key=lambda m: m.get("confidence", 0))
        all_reasons = []
        for m in clip["moments"]:
            if m.get("reasons"):
                all_reasons.extend(m["reasons"] if isinstance(m["reasons"], list) else [m["reasons"]])

        # frame_meta を集約（最初に見つかったものを使用）
        frame_meta = None
        for m in clip["moments"]:
            if m.get("frame_meta"):
                frame_meta = m["frame_meta"]
                break

        result.append({
            "id": i,
            "time_start": round(clip["time_start"], 1),
            "time_end": round(clip["time_end"], 1),
            "duration": round(clip["time_end"] - clip["time_start"], 1),
            "video_sec": round(best_moment.get("video_sec", 0), 1),
            "confidence": round(clip["best_confidence"], 2),
            "moment_count": len(clip["moments"]),
            "reasons": all_reasons[:5],
            "order_value": sum(m.get("order_value", 0) for m in clip["moments"]),
            "click_value": sum(m.get("click_value", 0) for m in clip["moments"]),
            "frame_meta": frame_meta,
        })

    return result



# ─── CSV Replace (Excel差し替え) ───

@router.put("/{video_id}/replace-excel")
async def replace_excel(
    video_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    既存動画のExcelファイル（商品データ/トレンドデータ）を差し替える。

    フロー:
    1. 新しいExcelファイルはフロントから直接Blobにアップロード済み
    2. このAPIで videos テーブルの excel_*_blob_url を更新
    3. upload_type を clean_video に変更
    4. Worker再処理をキューに投入

    Request body:
    {
        "excel_product_blob_url": "https://...",  // optional
        "excel_trend_blob_url": "https://...",    // optional
        "reprocess": true  // Workerの再処理をトリガーするか
    }
    """
    try:
        user_id = current_user["id"]
        email = current_user["email"]
        body = await request.json()

        excel_product_blob_url = body.get("excel_product_blob_url")
        excel_trend_blob_url = body.get("excel_trend_blob_url")
        reprocess = body.get("reprocess", True)

        if not excel_product_blob_url and not excel_trend_blob_url:
            raise HTTPException(status_code=400, detail="At least one Excel URL is required")

        # 1. 動画の存在確認とオーナーチェック
        video_sql = text("""
            SELECT id, original_filename, status, upload_type, user_id,
                   excel_product_blob_url, excel_trend_blob_url
            FROM videos
            WHERE id = :video_id AND user_id = :user_id
        """)
        result = await db.execute(video_sql, {"video_id": video_id, "user_id": user_id})
        video = result.fetchone()

        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        old_product_url = video.excel_product_blob_url
        old_trend_url = video.excel_trend_blob_url

        # 2. Excel URLを更新
        update_fields = ["upload_type = 'clean_video'", "updated_at = NOW()"]
        params = {"video_id": video_id}

        if excel_product_blob_url:
            update_fields.append("excel_product_blob_url = :product_url")
            params["product_url"] = excel_product_blob_url
        if excel_trend_blob_url:
            update_fields.append("excel_trend_blob_url = :trend_url")
            params["trend_url"] = excel_trend_blob_url

        update_sql = text(f"""
            UPDATE videos SET {', '.join(update_fields)}
            WHERE id = :video_id
        """)
        await db.execute(update_sql, params)
        await db.commit()

        logger.info(
            f"[replace-excel] video_id={video_id} user_id={user_id} "
            f"product={'replaced' if excel_product_blob_url else 'unchanged'} "
            f"trend={'replaced' if excel_trend_blob_url else 'unchanged'}"
        )

        # 3. Worker再処理をトリガー
        reprocess_status = "skipped"
        if reprocess:
            try:
                from app.services.storage_service import generate_download_sas
                from app.services.queue_service import enqueue_job

                # 動画のダウンロードURLを生成
                download_url, _ = await generate_download_sas(
                    email=email,
                    video_id=video_id,
                    filename=video.original_filename,
                    expires_in_minutes=1440,
                )

                queue_payload = {
                    "video_id": video_id,
                    "blob_url": download_url,
                    "original_filename": video.original_filename,
                    "user_id": user_id,
                    "upload_type": "clean_video",
                    "time_offset_seconds": 0,
                    "is_reprocess": True,
                }

                # Excel SAS URLを生成して追加
                final_product_url = excel_product_blob_url or old_product_url
                final_trend_url = excel_trend_blob_url or old_trend_url

                if final_product_url:
                    try:
                        product_download_url, _ = await generate_download_sas(
                            email=email,
                            video_id=video_id,
                            filename=f"excel/{final_product_url.split('/')[-1].split('?')[0]}",
                            expires_in_minutes=1440,
                        )
                        queue_payload["excel_product_url"] = product_download_url
                    except Exception as exc:
                        logger.warning(f"[replace-excel] Excel product SAS failed: {exc}")

                if final_trend_url:
                    try:
                        trend_download_url, _ = await generate_download_sas(
                            email=email,
                            video_id=video_id,
                            filename=f"excel/{final_trend_url.split('/')[-1].split('?')[0]}",
                            expires_in_minutes=1440,
                        )
                        queue_payload["excel_trend_url"] = trend_download_url
                    except Exception as exc:
                        logger.warning(f"[replace-excel] Excel trend SAS failed: {exc}")

                await enqueue_job(queue_payload)
                reprocess_status = "queued"
                logger.info(f"[replace-excel] Reprocess queued for video_id={video_id}")
            except Exception as exc:
                logger.exception(f"[replace-excel] Failed to enqueue reprocess: {exc}")
                reprocess_status = f"failed: {str(exc)}"

        # 4. video_upload_assets テーブルに versioned attachment を記録
        try:
            # テーブル作成（初回のみ）
            create_assets_sql = text("""
                CREATE TABLE IF NOT EXISTS video_upload_assets (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    video_id VARCHAR(100) NOT NULL,
                    asset_type ENUM('video', 'trend_csv', 'product_csv') NOT NULL,
                    original_filename VARCHAR(500),
                    blob_url TEXT,
                    file_size BIGINT DEFAULT 0,
                    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    uploaded_by INT,
                    is_active TINYINT(1) DEFAULT 1,
                    version INT DEFAULT 1,
                    validation_status VARCHAR(20) DEFAULT 'unknown',
                    validation_result JSON,
                    replaced_by_id BIGINT DEFAULT NULL,
                    INDEX idx_vua_video (video_id),
                    INDEX idx_vua_video_type (video_id, asset_type),
                    INDEX idx_vua_active (video_id, asset_type, is_active)
                )
            """)
            await db.execute(create_assets_sql)

            # 旧アセットを非アクティブにして、新アセットを登録
            if excel_product_blob_url:
                # 旧product CSVの最大versionを取得
                ver_sql = text("""
                    SELECT COALESCE(MAX(version), 0) as max_ver
                    FROM video_upload_assets
                    WHERE video_id = :video_id AND asset_type = 'product_csv'
                """)
                ver_result = await db.execute(ver_sql, {"video_id": video_id})
                max_ver = ver_result.scalar() or 0

                # 旧アセットを非アクティブに
                await db.execute(text("""
                    UPDATE video_upload_assets
                    SET is_active = 0
                    WHERE video_id = :video_id AND asset_type = 'product_csv' AND is_active = 1
                """), {"video_id": video_id})

                # 新アセットを登録
                product_fn = excel_product_blob_url.split("?")[0].split("/")[-1] if excel_product_blob_url else None
                await db.execute(text("""
                    INSERT INTO video_upload_assets
                        (video_id, asset_type, original_filename, blob_url,
                         uploaded_by, version, is_active)
                    VALUES
                        (:video_id, 'product_csv', :filename, :blob_url,
                         :user_id, :version, 1)
                """), {
                    "video_id": video_id,
                    "filename": product_fn,
                    "blob_url": (excel_product_blob_url or "")[:2000],
                    "user_id": user_id,
                    "version": max_ver + 1,
                })

            if excel_trend_blob_url:
                ver_sql = text("""
                    SELECT COALESCE(MAX(version), 0) as max_ver
                    FROM video_upload_assets
                    WHERE video_id = :video_id AND asset_type = 'trend_csv'
                """)
                ver_result = await db.execute(ver_sql, {"video_id": video_id})
                max_ver = ver_result.scalar() or 0

                await db.execute(text("""
                    UPDATE video_upload_assets
                    SET is_active = 0
                    WHERE video_id = :video_id AND asset_type = 'trend_csv' AND is_active = 1
                """), {"video_id": video_id})

                trend_fn = excel_trend_blob_url.split("?")[0].split("/")[-1] if excel_trend_blob_url else None
                await db.execute(text("""
                    INSERT INTO video_upload_assets
                        (video_id, asset_type, original_filename, blob_url,
                         uploaded_by, version, is_active)
                    VALUES
                        (:video_id, 'trend_csv', :filename, :blob_url,
                         :user_id, :version, 1)
                """), {
                    "video_id": video_id,
                    "filename": trend_fn,
                    "blob_url": (excel_trend_blob_url or "")[:2000],
                    "user_id": user_id,
                    "version": max_ver + 1,
                })

            await db.commit()
            logger.info(f"[replace-excel] Assets recorded for video_id={video_id}")
        except Exception as exc:
            logger.warning(f"[replace-excel] Failed to record assets: {exc}")

        # 5. 差し替えログも記録（後方互換）
        try:
            create_log_sql = text("""
                CREATE TABLE IF NOT EXISTS excel_replace_logs (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    video_id VARCHAR(100),
                    user_id INT,
                    old_product_url TEXT,
                    old_trend_url TEXT,
                    new_product_url TEXT,
                    new_trend_url TEXT,
                    reprocess_status VARCHAR(50),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_excel_replace_video (video_id),
                    INDEX idx_excel_replace_created (created_at)
                )
            """)
            await db.execute(create_log_sql)

            insert_log_sql = text("""
                INSERT INTO excel_replace_logs
                    (video_id, user_id, old_product_url, old_trend_url,
                     new_product_url, new_trend_url, reprocess_status)
                VALUES
                    (:video_id, :user_id, :old_product, :old_trend,
                     :new_product, :new_trend, :reprocess_status)
            """)
            await db.execute(insert_log_sql, {
                "video_id": video_id,
                "user_id": user_id,
                "old_product": (old_product_url or "")[:500],
                "old_trend": (old_trend_url or "")[:500],
                "new_product": (excel_product_blob_url or "")[:500],
                "new_trend": (excel_trend_blob_url or "")[:500],
                "reprocess_status": reprocess_status,
            })
            await db.commit()
        except Exception as exc:
            logger.warning(f"[replace-excel] Failed to log replacement: {exc}")

        return {
            "status": "ok",
            "video_id": video_id,
            "product_replaced": bool(excel_product_blob_url),
            "trend_replaced": bool(excel_trend_blob_url),
            "reprocess_status": reprocess_status,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"[replace-excel] Unexpected error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{video_id}/excel-info")
async def get_excel_info(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    動画に紐付いているExcel（CSV）情報を取得する。
    video_upload_assets テーブルから versioned attachment 情報を返す。
    """
    try:
        user_id = current_user["id"]

        video_sql = text("""
            SELECT id, original_filename, upload_type,
                   excel_product_blob_url, excel_trend_blob_url,
                   created_at, updated_at
            FROM videos
            WHERE id = :video_id AND (user_id = :user_id OR user_id IS NULL)
        """)
        result = await db.execute(video_sql, {"video_id": video_id, "user_id": user_id})
        video = result.fetchone()

        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        def extract_filename(url):
            if not url:
                return None
            try:
                path = url.split("?")[0]
                return path.split("/")[-1]
            except Exception:
                return url

        product_filename = extract_filename(video.excel_product_blob_url)
        trend_filename = extract_filename(video.excel_trend_blob_url)

        # video_upload_assets からアセット情報を取得
        current_assets = {"product_csv": None, "trend_csv": None}
        asset_history = []
        try:
            # 現在アクティブなアセット
            active_sql = text("""
                SELECT id, asset_type, original_filename, blob_url, file_size,
                       uploaded_at, uploaded_by, version, validation_status, validation_result
                FROM video_upload_assets
                WHERE video_id = :video_id AND is_active = 1
                ORDER BY asset_type
            """)
            active_result = await db.execute(active_sql, {"video_id": video_id})
            for r in active_result.fetchall():
                current_assets[r.asset_type] = {
                    "id": r.id,
                    "filename": r.original_filename,
                    "version": r.version,
                    "uploaded_at": str(r.uploaded_at) if r.uploaded_at else None,
                    "uploaded_by": r.uploaded_by,
                    "file_size": r.file_size,
                    "validation_status": r.validation_status,
                    "validation_result": json.loads(r.validation_result) if r.validation_result else None,
                }

            # 全履歴（非アクティブ含む）
            history_sql = text("""
                SELECT id, asset_type, original_filename, version,
                       uploaded_at, uploaded_by, is_active, validation_status
                FROM video_upload_assets
                WHERE video_id = :video_id
                ORDER BY uploaded_at DESC
                LIMIT 50
            """)
            history_result = await db.execute(history_sql, {"video_id": video_id})
            for r in history_result.fetchall():
                asset_history.append({
                    "id": r.id,
                    "asset_type": r.asset_type,
                    "filename": r.original_filename,
                    "version": r.version,
                    "uploaded_at": str(r.uploaded_at) if r.uploaded_at else None,
                    "uploaded_by": r.uploaded_by,
                    "is_active": bool(r.is_active),
                    "validation_status": r.validation_status,
                })
        except Exception as exc:
            logger.debug(f"[excel-info] video_upload_assets not available: {exc}")

        # 差し替え履歴を取得（後方互換）
        replace_history = []
        try:
            history_sql = text("""
                SELECT id, old_product_url, old_trend_url,
                       new_product_url, new_trend_url,
                       reprocess_status, created_at
                FROM excel_replace_logs
                WHERE video_id = :video_id
                ORDER BY created_at DESC
                LIMIT 10
            """)
            history_result = await db.execute(history_sql, {"video_id": video_id})
            for r in history_result.fetchall():
                replace_history.append({
                    "id": r.id,
                    "old_product": extract_filename(r.old_product_url),
                    "old_trend": extract_filename(r.old_trend_url),
                    "new_product": extract_filename(r.new_product_url),
                    "new_trend": extract_filename(r.new_trend_url),
                    "reprocess_status": r.reprocess_status,
                    "created_at": str(r.created_at) if r.created_at else None,
                })
        except Exception:
            pass

        return {
            "video_id": video_id,
            "original_filename": video.original_filename,
            "upload_type": video.upload_type or "screen_recording",
            "has_product": bool(video.excel_product_blob_url),
            "has_trend": bool(video.excel_trend_blob_url),
            "product_filename": product_filename,
            "trend_filename": trend_filename,
            "current_assets": current_assets,
            "asset_history": asset_history,
            "created_at": str(video.created_at) if video.created_at else None,
            "updated_at": str(video.updated_at) if video.updated_at else None,
            "replace_history": replace_history,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"[excel-info] Unexpected error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# ============================================================
# CSV Preview / Re-validation / Validation Status Update APIs
# ============================================================

@router.get("/{video_id}/csv-preview")
async def get_csv_preview(
    video_id: str,
    asset_type: str = "trend_csv",
    max_rows: int = 10,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    CSVプレビュー: Excelファイルの先頭行とカラム情報を返す。
    asset_type: 'trend_csv' or 'product_csv'
    """
    import tempfile
    import os as _os
    import openpyxl
    import httpx
    from azure.storage.blob import generate_blob_sas, BlobSasPermissions

    try:
        user_id = current_user["id"]
        email = current_user.get("email", "")

        # 動画情報取得
        video_sql = text("""
            SELECT id, excel_product_blob_url, excel_trend_blob_url, user_id
            FROM videos
            WHERE id = :video_id AND (user_id = :user_id OR user_id IS NULL)
        """)
        result = await db.execute(video_sql, {"video_id": video_id, "user_id": user_id})
        video = result.fetchone()
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        # blob_url 取得
        if asset_type == "product_csv":
            blob_url = video.excel_product_blob_url
        else:
            blob_url = video.excel_trend_blob_url

        if not blob_url:
            return {
                "video_id": video_id,
                "asset_type": asset_type,
                "available": False,
                "message": f"No {asset_type} attached",
            }

        # SAS URL 生成
        conn_str = _os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
        account_name = ""
        account_key = ""
        for part in conn_str.split(";"):
            if part.startswith("AccountName="):
                account_name = part.split("=", 1)[1]
            elif part.startswith("AccountKey="):
                account_key = part.split("=", 1)[1]

        from urllib.parse import urlparse, unquote
        parsed = urlparse(blob_url)
        path = unquote(parsed.path)
        if path.startswith("/videos/"):
            blob_name = path[len("/videos/"):]
        else:
            blob_name = path.lstrip("/")
            if blob_name.startswith("videos/"):
                blob_name = blob_name[len("videos/"):]

        expiry = datetime.now(timezone.utc) + timedelta(minutes=30)
        sas = generate_blob_sas(
            account_name=account_name,
            container_name="videos",
            blob_name=blob_name,
            account_key=account_key,
            permission=BlobSasPermissions(read=True),
            expiry=expiry,
        )
        sas_url = f"https://{account_name}.blob.core.windows.net/videos/{blob_name}?{sas}"

        # ダウンロードしてパース
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(sas_url)
            if resp.status_code != 200:
                return {
                    "video_id": video_id,
                    "asset_type": asset_type,
                    "available": False,
                    "message": f"Failed to download file (HTTP {resp.status_code})",
                }

            file_size = len(resp.content)

            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
                f.write(resp.content)
                tmp_path = f.name

            try:
                wb = openpyxl.load_workbook(tmp_path, read_only=True, data_only=True)
                ws = wb.active
                if not ws:
                    return {
                        "video_id": video_id,
                        "asset_type": asset_type,
                        "available": True,
                        "file_size": file_size,
                        "message": "No active worksheet found",
                        "columns": [],
                        "rows": [],
                        "total_rows": 0,
                    }

                rows_data = list(ws.iter_rows(values_only=True))
                total_rows = len(rows_data) - 1 if len(rows_data) > 0 else 0

                headers = []
                preview_rows = []
                if len(rows_data) >= 1:
                    headers = [str(h).strip() if h else f"col_{i}" for i, h in enumerate(rows_data[0])]

                if len(rows_data) >= 2:
                    limit = min(max_rows, len(rows_data) - 1)
                    for data_row in rows_data[1:limit + 1]:
                        row_dict = {}
                        for i, val in enumerate(data_row):
                            if i < len(headers):
                                if val is None:
                                    row_dict[headers[i]] = None
                                elif isinstance(val, datetime):
                                    row_dict[headers[i]] = str(val)
                                elif isinstance(val, (int, float)):
                                    row_dict[headers[i]] = val
                                else:
                                    row_dict[headers[i]] = str(val)
                        preview_rows.append(row_dict)

                # カラム分析
                column_info = []
                for col_name in headers:
                    col_data = {
                        "name": col_name,
                        "non_null_count": 0,
                        "sample_values": [],
                    }
                    for row in preview_rows[:5]:
                        val = row.get(col_name)
                        if val is not None:
                            col_data["non_null_count"] += 1
                            if len(col_data["sample_values"]) < 3:
                                col_data["sample_values"].append(str(val)[:100])
                    column_info.append(col_data)

                # 日時カラム検出
                datetime_columns = []
                for col in headers:
                    cl = col.lower() if col else ""
                    if any(kw in cl for kw in ["日時", "time", "date", "timestamp", "開始", "start"]):
                        datetime_columns.append(col)

                wb.close()

                return {
                    "video_id": video_id,
                    "asset_type": asset_type,
                    "available": True,
                    "file_size": file_size,
                    "total_rows": total_rows,
                    "columns": headers,
                    "column_info": column_info,
                    "datetime_columns": datetime_columns,
                    "preview_rows": preview_rows,
                    "sheet_name": ws.title,
                }
            finally:
                _os.unlink(tmp_path)

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"[csv-preview] Unexpected error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/{video_id}/update-validation-status")
async def update_validation_status(
    video_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    フロントエンドからCSVバリデーション結果を保存する。
    video_upload_assets テーブルの validation_status/validation_result を更新。
    """
    try:
        body = await request.json()
        asset_type = body.get("asset_type", "trend_csv")
        validation_status = body.get("validation_status", "unknown")
        validation_result = body.get("validation_result")

        # video_upload_assets テーブルの該当アセットを更新
        update_sql = text("""
            UPDATE video_upload_assets
            SET validation_status = :status,
                validation_result = :result
            WHERE video_id = :video_id
              AND asset_type = :asset_type
              AND is_active = 1
        """)
        await db.execute(update_sql, {
            "video_id": video_id,
            "status": validation_status[:20],
            "result": json.dumps(validation_result) if validation_result else None,
            "asset_type": asset_type,
        })
        await db.commit()

        return {"status": "ok", "video_id": video_id, "asset_type": asset_type}
    except Exception as exc:
        logger.warning(f"[update-validation-status] Error: {exc}")
        # テーブルが存在しない場合も許容
        return {"status": "skipped", "reason": str(exc)}


@router.get("/{video_id}/asset-history")
async def get_asset_history(
    video_id: str,
    asset_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    アセットのバージョン履歴を取得する。
    """
    try:
        user_id = current_user["id"]

        # 動画の所有権確認
        video_sql = text("""
            SELECT id FROM videos
            WHERE id = :video_id AND (user_id = :user_id OR user_id IS NULL)
        """)
        result = await db.execute(video_sql, {"video_id": video_id, "user_id": user_id})
        if not result.fetchone():
            raise HTTPException(status_code=404, detail="Video not found")

        # アセット履歴取得
        if asset_type:
            history_sql = text("""
                SELECT id, asset_type, original_filename, blob_url, file_size,
                       uploaded_at, uploaded_by, is_active, version,
                       validation_status, validation_result
                FROM video_upload_assets
                WHERE video_id = :video_id AND asset_type = :asset_type
                ORDER BY version DESC
                LIMIT 50
            """)
            params = {"video_id": video_id, "asset_type": asset_type}
        else:
            history_sql = text("""
                SELECT id, asset_type, original_filename, blob_url, file_size,
                       uploaded_at, uploaded_by, is_active, version,
                       validation_status, validation_result
                FROM video_upload_assets
                WHERE video_id = :video_id
                ORDER BY asset_type, version DESC
                LIMIT 100
            """)
            params = {"video_id": video_id}

        result = await db.execute(history_sql, params)
        history = []
        for r in result.fetchall():
            history.append({
                "id": r.id,
                "asset_type": r.asset_type,
                "filename": r.original_filename,
                "file_size": r.file_size,
                "uploaded_at": str(r.uploaded_at) if r.uploaded_at else None,
                "uploaded_by": r.uploaded_by,
                "is_active": bool(r.is_active),
                "version": r.version,
                "validation_status": r.validation_status,
                "validation_result": json.loads(r.validation_result) if r.validation_result else None,
            })

        return {
            "video_id": video_id,
            "asset_type": asset_type,
            "history": history,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"[asset-history] Unexpected error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
