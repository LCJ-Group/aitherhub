"""
Admin dashboard API endpoint.
Provides platform-wide statistics for the master dashboard.
Each query is isolated with rollback on failure to prevent cascade errors.
"""
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from loguru import logger
from typing import Optional

from app.core.dependencies import get_db, get_current_user

router = APIRouter(prefix="/admin", tags=["Admin"])

ADMIN_ID = "aither"
ADMIN_PASS = "hub"


async def _q(db: AsyncSession, sql: str, default=0):
    """Run a scalar query with rollback on failure to keep the session alive."""
    try:
        r = await db.execute(text(sql))
        val = r.scalar()
        return val if val is not None else default
    except Exception as e:
        logger.warning(f"Admin query error: {e}")
        try:
            await db.rollback()
        except Exception:
            pass
        return default


async def _get_dashboard_data(db: AsyncSession) -> dict:
    """Gather all dashboard statistics."""

    # ── Data Volume ──
    total_videos = await _q(db, "SELECT COUNT(*) FROM videos")
    analyzed_videos = await _q(db, "SELECT COUNT(*) FROM videos WHERE status = 'DONE'")
    pending_videos = total_videos - analyzed_videos

    # time_end is double precision (seconds)
    total_duration_seconds = await _q(db, """
        SELECT COALESCE(SUM(max_sec), 0) FROM (
            SELECT video_id, MAX(COALESCE(time_end, 0)) as max_sec
            FROM video_phases
            WHERE time_end IS NOT NULL
            GROUP BY video_id
        ) sub
    """)
    total_duration_seconds = int(total_duration_seconds)

    # ── Video Types ──
    screen_recording_count = await _q(
        db,
        "SELECT COUNT(*) FROM videos WHERE upload_type = 'screen_recording' OR upload_type IS NULL",
    )
    clean_video_count = await _q(
        db,
        "SELECT COUNT(*) FROM videos WHERE upload_type = 'clean_video'",
    )
    if screen_recording_count == 0 and clean_video_count == 0 and total_videos > 0:
        screen_recording_count = total_videos

    latest_upload_raw = await _q(db, "SELECT MAX(created_at) FROM videos", default=None)
    latest_upload = str(latest_upload_raw) if latest_upload_raw else None

    # ── User Scale ──
    total_users = await _q(db, "SELECT COUNT(*) FROM users WHERE is_active = true")
    if total_users == 0:
        total_users = await _q(db, "SELECT COUNT(*) FROM users")

    total_streamers = await _q(db, "SELECT COUNT(DISTINCT user_id) FROM videos")
    this_month_uploaders = await _q(
        db,
        "SELECT COUNT(DISTINCT user_id) FROM videos "
        "WHERE created_at >= DATE_TRUNC('month', CURRENT_DATE)",
    )

    # Format duration
    total_hours = total_duration_seconds // 3600
    total_minutes = (total_duration_seconds % 3600) // 60

    return {
        "data_volume": {
            "total_videos": total_videos,
            "analyzed_videos": analyzed_videos,
            "pending_videos": pending_videos,
            "total_duration_seconds": total_duration_seconds,
            "total_duration_display": f"{total_hours}時間{total_minutes}分",
        },
        "video_types": {
            "screen_recording_count": screen_recording_count,
            "clean_video_count": clean_video_count,
            "latest_upload": latest_upload,
        },
        "user_scale": {
            "total_users": total_users,
            "total_streamers": total_streamers,
            "this_month_uploaders": this_month_uploaders,
        },
    }


@router.get("/dashboard")
async def get_dashboard_stats(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """JWT auth, admin role required."""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return await _get_dashboard_data(db)


@router.get("/dashboard-public")
async def get_dashboard_stats_public(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Simple ID:password auth via header."""
    expected_key = f"{ADMIN_ID}:{ADMIN_PASS}"
    if x_admin_key != expected_key:
        raise HTTPException(status_code=403, detail="Invalid admin credentials")
    return await _get_dashboard_data(db)


@router.get("/feedbacks")
async def get_all_feedbacks(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get all phase feedbacks (ratings + comments) across all users and videos.
    Returns a list sorted by most recent first.
    """
    expected_key = f"{ADMIN_ID}:{ADMIN_PASS}"
    if x_admin_key != expected_key:
        raise HTTPException(status_code=403, detail="Invalid admin credentials")

    try:
        sql = text("""
            SELECT
                vp.video_id,
                vp.phase_index,
                vp.time_start,
                vp.time_end,
                vp.phase_description,
                vp.user_rating,
                vp.user_comment,
                vp.rated_at,
                vp.importance_score,
                v.original_filename,
                v.user_id,
                u.email as user_email
            FROM video_phases vp
            JOIN videos v ON CAST(vp.video_id AS UUID) = v.id
            LEFT JOIN users u ON v.user_id = u.id
            WHERE vp.user_rating IS NOT NULL
            ORDER BY vp.rated_at DESC NULLS LAST
        """)
        result = await db.execute(sql)
        rows = result.fetchall()

        feedbacks = []
        for r in rows:
            feedbacks.append({
                "video_id": r.video_id,
                "phase_index": r.phase_index,
                "time_start": r.time_start,
                "time_end": r.time_end,
                "summary": r.phase_description[:200] if r.phase_description else None,
                "user_rating": r.user_rating,
                "user_comment": r.user_comment,
                "rated_at": str(r.rated_at) if r.rated_at else None,
                "importance_score": r.importance_score,
                "video_name": r.original_filename,
                "user_id": r.user_id,
                "user_email": r.user_email,
            })

        # Summary stats
        total = len(feedbacks)
        avg_rating = sum(f["user_rating"] for f in feedbacks) / total if total > 0 else 0
        rating_dist = {i: 0 for i in range(1, 6)}
        for f in feedbacks:
            if f["user_rating"] in rating_dist:
                rating_dist[f["user_rating"]] += 1
        with_comments = sum(1 for f in feedbacks if f.get("user_comment"))

        return {
            "summary": {
                "total_feedbacks": total,
                "average_rating": round(avg_rating, 2),
                "rating_distribution": rating_dist,
                "with_comments": with_comments,
            },
            "feedbacks": feedbacks,
        }
    except Exception as e:
        logger.exception(f"Failed to fetch feedbacks: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch feedbacks: {e}")


@router.get("/stuck-videos")
async def get_stuck_videos(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
):
    """List videos that are stuck in processing (not DONE/ERROR, older than 30 min)."""
    expected_key = f"{ADMIN_ID}:{ADMIN_PASS}"
    if x_admin_key != expected_key:
        raise HTTPException(status_code=403, detail="Invalid admin credentials")

    try:
        sql = text("""
            SELECT v.id, v.original_filename, v.status, v.step_progress,
                   v.upload_type, v.created_at, v.updated_at,
                   u.email as user_email
            FROM videos v
            LEFT JOIN users u ON v.user_id = u.id
            WHERE v.status NOT IN ('DONE', 'ERROR')
            ORDER BY v.created_at DESC
            LIMIT 50
        """)
        result = await db.execute(sql)
        rows = result.fetchall()

        videos = []
        for r in rows:
            videos.append({
                "id": str(r.id),
                "filename": r.original_filename,
                "status": r.status,
                "step_progress": r.step_progress,
                "upload_type": r.upload_type,
                "created_at": str(r.created_at) if r.created_at else None,
                "updated_at": str(r.updated_at) if r.updated_at else None,
                "user_email": r.user_email,
            })

        return {"count": len(videos), "videos": videos}
    except Exception as e:
        logger.exception(f"Failed to fetch stuck videos: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────────
# Video Processing / Learning Log endpoints
# ──────────────────────────────────────────────────────────────────────

@router.get("/videos")
async def get_video_list(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
    status_filter: Optional[str] = None,
    upload_type_filter: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    """List all videos with processing status, phase count, sales_moment count,
    human label stats, and dataset inclusion status."""
    expected_key = f"{ADMIN_ID}:{ADMIN_PASS}"
    if x_admin_key != expected_key:
        raise HTTPException(status_code=403, detail="Invalid admin credentials")

    try:
        # Build WHERE clause
        conditions = []
        params = {"lim": limit, "off": offset}
        if status_filter:
            conditions.append("v.status = :sf")
            params["sf"] = status_filter
        if upload_type_filter:
            conditions.append("v.upload_type = :uf")
            params["uf"] = upload_type_filter
        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        sql = text(f"""
            SELECT
                v.id,
                v.original_filename,
                v.upload_type,
                v.status,
                v.step_progress,
                v.created_at,
                v.updated_at,
                u.email AS user_email,
                COALESCE(ph.phase_count, 0) AS phase_count,
                COALESCE(sm.moment_count, 0) AS moment_count,
                COALESCE(sm.csv_moment_count, 0) AS csv_moment_count,
                COALESCE(sm.screen_moment_count, 0) AS screen_moment_count,
                COALESCE(hl.rating_count, 0) AS rating_count,
                COALESCE(hl.tag_count, 0) AS tag_count,
                COALESCE(hl.comment_count, 0) AS comment_count,
                vps.frames_extracted,
                vps.audio_extracted,
                vps.speech_done,
                vps.vision_done
            FROM videos v
            LEFT JOIN users u ON v.user_id = u.id
            LEFT JOIN (
                SELECT video_id, COUNT(*) AS phase_count
                FROM video_phases
                GROUP BY video_id
            ) ph ON CAST(ph.video_id AS UUID) = v.id
            LEFT JOIN (
                SELECT video_id,
                       COUNT(*) AS moment_count,
                       COUNT(CASE WHEN source = 'csv' THEN 1 END) AS csv_moment_count,
                       COUNT(CASE WHEN source = 'screen' THEN 1 END) AS screen_moment_count
                FROM video_sales_moments
                GROUP BY video_id
            ) sm ON CAST(sm.video_id AS UUID) = v.id
            LEFT JOIN (
                SELECT video_id,
                       COUNT(CASE WHEN user_rating IS NOT NULL THEN 1 END) AS rating_count,
                       COUNT(CASE WHEN human_sales_tags IS NOT NULL AND human_sales_tags != '[]' THEN 1 END) AS tag_count,
                       COUNT(CASE WHEN user_comment IS NOT NULL AND user_comment != '' THEN 1 END) AS comment_count
                FROM video_phases
                GROUP BY video_id
            ) hl ON CAST(hl.video_id AS UUID) = v.id
            LEFT JOIN video_processing_state vps ON CAST(vps.video_id AS UUID) = v.id
            {where_clause}
            ORDER BY v.created_at DESC
            LIMIT :lim OFFSET :off
        """)

        result = await db.execute(sql, params)
        rows = result.fetchall()

        # Total count
        count_sql = text(f"SELECT COUNT(*) FROM videos v {where_clause}")
        total = (await db.execute(count_sql, params)).scalar() or 0

        videos = []
        for r in rows:
            # Determine dataset status
            ds_status = "excluded"
            ds_reason = None
            if r.status == "DONE":
                if r.moment_count > 0:
                    ds_status = "included"
                else:
                    ds_status = "excluded"
                    ds_reason = "no_sales_moments"
            elif r.status == "ERROR":
                ds_status = "excluded"
                ds_reason = "processing_error"
            else:
                ds_status = "pending"
                ds_reason = "still_processing"

            videos.append({
                "id": str(r.id),
                "filename": r.original_filename,
                "upload_type": r.upload_type or "screen_recording",
                "status": r.status,
                "step_progress": r.step_progress,
                "created_at": str(r.created_at) if r.created_at else None,
                "updated_at": str(r.updated_at) if r.updated_at else None,
                "user_email": r.user_email,
                "phase_count": r.phase_count,
                "moment_count": r.moment_count,
                "moment_sources": {
                    "csv": r.csv_moment_count,
                    "screen": r.screen_moment_count,
                } if r.moment_count > 0 else None,
                "rating_count": r.rating_count,
                "tag_count": r.tag_count,
                "comment_count": r.comment_count,
                "dataset_status": ds_status,
                "dataset_excluded_reason": ds_reason,
                "processing_state": {
                    "frames_extracted": r.frames_extracted if r.frames_extracted is not None else False,
                    "audio_extracted": r.audio_extracted if r.audio_extracted is not None else False,
                    "speech_done": r.speech_done if r.speech_done is not None else False,
                    "vision_done": r.vision_done if r.vision_done is not None else False,
                } if r.frames_extracted is not None else None,
            })

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "videos": videos,
        }
    except Exception as e:
        logger.exception(f"Failed to fetch video list: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/videos/{video_id}")
async def get_video_detail(
    video_id: str,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Get detailed processing and learning log for a specific video."""
    expected_key = f"{ADMIN_ID}:{ADMIN_PASS}"
    if x_admin_key != expected_key:
        raise HTTPException(status_code=403, detail="Invalid admin credentials")

    try:
        # ── A. Basic info ──
        video_sql = text("""
            SELECT v.id, v.original_filename, v.upload_type, v.status,
                   v.step_progress, v.created_at, v.updated_at,
                   v.excel_product_blob_url, v.excel_trend_blob_url,
                   v.compressed_blob_url, v.top_products,
                   v.time_offset_seconds,
                   v.queue_enqueued_at, v.worker_claimed_at,
                   v.worker_instance_id, v.dequeue_count,
                   v.enqueue_status, v.enqueue_error,
                   u.email AS user_email
            FROM videos v
            LEFT JOIN users u ON v.user_id = u.id
            WHERE v.id = :vid
        """)
        result = await db.execute(video_sql, {"vid": video_id})
        video = result.fetchone()
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        # ── B. Processing state ──
        state_sql = text("""
            SELECT * FROM video_processing_state WHERE video_id = :vid
        """)
        state_result = await db.execute(state_sql, {"vid": video_id})
        state = state_result.fetchone()

        # ── C. Phases summary ──
        phases_sql = text("""
            SELECT
                COUNT(*) AS total_phases,
                COUNT(CASE WHEN user_rating IS NOT NULL THEN 1 END) AS rated_phases,
                COUNT(CASE WHEN human_sales_tags IS NOT NULL AND human_sales_tags != '[]' THEN 1 END) AS tagged_phases,
                COUNT(CASE WHEN user_comment IS NOT NULL AND user_comment != '' THEN 1 END) AS commented_phases,
                AVG(user_rating) AS avg_rating,
                MIN(time_start) AS min_time,
                MAX(time_end) AS max_time
            FROM video_phases
            WHERE video_id = :vid
        """)
        phases_result = await db.execute(phases_sql, {"vid": video_id})
        phases_summary = phases_result.fetchone()

        # ── D. Sales moments breakdown ──
        # Try with source column first, fallback without it
        try:
            moments_sql = text("""
                SELECT
                    COALESCE(source, 'csv') AS source,
                    moment_type,
                    moment_type_detail,
                    COUNT(*) AS count,
                    AVG(confidence) AS avg_confidence
                FROM video_sales_moments
                WHERE video_id = :vid
                GROUP BY source, moment_type, moment_type_detail
                ORDER BY source, count DESC
            """)
            moments_result = await db.execute(moments_sql, {"vid": video_id})
            moments_rows = moments_result.fetchall()

            moments_total = sum(r.count for r in moments_rows)
            moments_by_source = {}
            for r in moments_rows:
                src = r.source or "csv"
                if src not in moments_by_source:
                    moments_by_source[src] = []
                moments_by_source[src].append({
                    "moment_type": r.moment_type,
                    "moment_type_detail": r.moment_type_detail,
                    "count": r.count,
                    "avg_confidence": round(float(r.avg_confidence), 3) if r.avg_confidence else None,
                })
        except Exception:
            await db.rollback()
            # Fallback: source/moment_type_detail/confidence columns not yet migrated
            moments_sql = text("""
                SELECT
                    moment_type,
                    COUNT(*) AS count
                FROM video_sales_moments
                WHERE video_id = :vid
                GROUP BY moment_type
                ORDER BY count DESC
            """)
            moments_result = await db.execute(moments_sql, {"vid": video_id})
            moments_rows = moments_result.fetchall()
            moments_total = sum(r.count for r in moments_rows)
            moments_by_source = {"csv": [
                {"moment_type": r.moment_type, "moment_type_detail": None, "count": r.count, "avg_confidence": None}
                for r in moments_rows
            ]}

        # ── E. Reports check ──
        try:
            reports_sql = text("""
                SELECT COUNT(*) AS report_count
                FROM reports
                WHERE video_id = :vid
            """)
            reports_result = await db.execute(reports_sql, {"vid": video_id})
            report_count = reports_result.scalar() or 0
        except Exception:
            await db.rollback()
            report_count = 0  # table may not exist

        # ── F. Transcript check ──
        transcript_sql = text("""
            SELECT COUNT(*) AS segment_count
            FROM video_speech_segments
            WHERE video_id = :vid
        """)
        try:
            transcript_result = await db.execute(transcript_sql, {"vid": video_id})
            transcript_count = transcript_result.scalar() or 0
        except Exception:
            await db.rollback()
            transcript_count = -1  # table may not exist

        # ── G. Build pipeline steps status ──
        # Derive step completion from video status
        status = video.status or ""
        step_order = [
            "STEP_0_EXTRACT_FRAMES",
            "STEP_1_DETECT_PHASES",
            "STEP_2_EXTRACT_METRICS",
            "STEP_3_TRANSCRIBE_AUDIO",
            "STEP_4_IMAGE_CAPTION",
            "STEP_5_BUILD_PHASE_UNITS",
            "STEP_6_BUILD_PHASE_DESCRIPTION",
            "STEP_7_GROUPING",
            "STEP_8_UPDATE_BEST_PHASE",
            "STEP_9_BUILD_VIDEO_STRUCTURE_FEATURES",
            "STEP_10_ASSIGN_VIDEO_STRUCTURE_GROUP",
            "STEP_11_UPDATE_VIDEO_STRUCTURE_GROUP_STATS",
            "STEP_12_UPDATE_VIDEO_STRUCTURE_BEST",
            "STEP_12_5_PRODUCT_DETECTION",
            "STEP_13_BUILD_REPORTS",
            "STEP_14_FINALIZE",
        ]

        step_labels = {
            "STEP_0_EXTRACT_FRAMES": "フレーム抽出",
            "STEP_1_DETECT_PHASES": "フェーズ検出",
            "STEP_2_EXTRACT_METRICS": "メトリクス抽出",
            "STEP_3_TRANSCRIBE_AUDIO": "音声文字起こし",
            "STEP_4_IMAGE_CAPTION": "画像キャプション",
            "STEP_5_BUILD_PHASE_UNITS": "フェーズ構築 (CSV/Screen統合含む)",
            "STEP_6_BUILD_PHASE_DESCRIPTION": "AI要約生成",
            "STEP_7_GROUPING": "グルーピング",
            "STEP_8_UPDATE_BEST_PHASE": "ベストフェーズ選定",
            "STEP_9_BUILD_VIDEO_STRUCTURE_FEATURES": "動画構造特徴量",
            "STEP_10_ASSIGN_VIDEO_STRUCTURE_GROUP": "構造グループ割当",
            "STEP_11_UPDATE_VIDEO_STRUCTURE_GROUP_STATS": "グループ統計更新",
            "STEP_12_UPDATE_VIDEO_STRUCTURE_BEST": "構造ベスト更新",
            "STEP_12_5_PRODUCT_DETECTION": "商品検出",
            "STEP_13_BUILD_REPORTS": "レポート生成",
            "STEP_14_FINALIZE": "最終処理",
        }

        if status == "DONE":
            current_step_idx = len(step_order)  # all done
        elif status == "ERROR":
            # Find last known step from status pattern
            current_step_idx = -1  # unknown
        elif status in step_order:
            current_step_idx = step_order.index(status)
        else:
            current_step_idx = -1

        pipeline_steps = []
        for i, step_name in enumerate(step_order):
            if status == "DONE":
                step_status = "success"
            elif status == "ERROR" and current_step_idx == -1:
                # Can't determine which step failed
                step_status = "unknown"
            elif i < current_step_idx:
                step_status = "success"
            elif i == current_step_idx:
                step_status = "running" if status not in ("DONE", "ERROR") else "failed"
            else:
                step_status = "pending"

            pipeline_steps.append({
                "step_name": step_name,
                "label": step_labels.get(step_name, step_name),
                "status": step_status,
            })

        # ── H. Duration ──
        duration_sec = None
        if phases_summary and phases_summary.max_time:
            duration_sec = round(float(phases_summary.max_time), 1)

        # ── I. Dataset status ──
        ds_status = "excluded"
        ds_reason = None
        if status == "DONE":
            if moments_total > 0:
                ds_status = "included"
            else:
                ds_status = "excluded"
                ds_reason = "no_sales_moments"
        elif status == "ERROR":
            ds_status = "excluded"
            ds_reason = "processing_error"
        else:
            ds_status = "pending"
            ds_reason = "still_processing"

        return {
            "basic_info": {
                "video_id": str(video.id),
                "filename": video.original_filename,
                "upload_type": video.upload_type or "screen_recording",
                "status": video.status,
                "step_progress": video.step_progress,
                "duration_sec": duration_sec,
                "created_at": str(video.created_at) if video.created_at else None,
                "updated_at": str(video.updated_at) if video.updated_at else None,
                "user_email": video.user_email,
                "has_excel_product": bool(video.excel_product_blob_url),
                "has_excel_trend": bool(video.excel_trend_blob_url),
                "has_compressed": bool(video.compressed_blob_url),
                "top_products": video.top_products,
                "time_offset_seconds": video.time_offset_seconds,
            },
            "queue_info": {
                "enqueued_at": str(video.queue_enqueued_at) if video.queue_enqueued_at else None,
                "worker_claimed_at": str(video.worker_claimed_at) if video.worker_claimed_at else None,
                "worker_instance_id": video.worker_instance_id,
                "dequeue_count": video.dequeue_count,
                "enqueue_status": video.enqueue_status,
                "enqueue_error": video.enqueue_error,
            },
            "processing_state": {
                "frames_extracted": state.frames_extracted if state else None,
                "audio_extracted": state.audio_extracted if state else None,
                "speech_done": state.speech_done if state else None,
                "vision_done": state.vision_done if state else None,
                "updated_at": str(state.updated_at) if state and state.updated_at else None,
            } if state else None,
            "pipeline_steps": pipeline_steps,
            "phases": {
                "total": phases_summary.total_phases if phases_summary else 0,
                "duration_sec": duration_sec,
                "rated": phases_summary.rated_phases if phases_summary else 0,
                "tagged": phases_summary.tagged_phases if phases_summary else 0,
                "commented": phases_summary.commented_phases if phases_summary else 0,
                "avg_rating": round(float(phases_summary.avg_rating), 2) if phases_summary and phases_summary.avg_rating else None,
                "reviewers": None,  # requires reviewer_name column migration
            },
            "sales_moments": {
                "total": moments_total,
                "by_source": moments_by_source,
            },
            "reports": {
                "count": report_count,
            },
            "transcript": {
                "segment_count": transcript_count,
            },
            "human_labels": {
                "rated_phases": phases_summary.rated_phases if phases_summary else 0,
                "tagged_phases": phases_summary.tagged_phases if phases_summary else 0,
                "commented_phases": phases_summary.commented_phases if phases_summary else 0,
                "avg_rating": round(float(phases_summary.avg_rating), 2) if phases_summary and phases_summary.avg_rating else None,
                "reviewers": None,  # requires reviewer_name column migration
            },
            "dataset": {
                "status": ds_status,
                "excluded_reason": ds_reason,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to fetch video detail: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/retry-video/{video_id}")
async def retry_video(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None),
):
    """Re-enqueue a stuck video for processing.
    Generates a fresh SAS URL and pushes a new job to the queue."""
    if x_admin_key != f"{ADMIN_ID}:{ADMIN_PASS}":
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        # Get video info
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

        # Generate fresh SAS URL
        from app.services.storage_service import generate_download_sas
        download_url, expiry = await generate_download_sas(
            email=row.user_email,
            video_id=str(row.id),
            filename=row.original_filename,
            expires_in_minutes=1440,  # 24 hours
        )

        # Reset status to uploaded
        await db.execute(
            text("UPDATE videos SET status = 'uploaded', step_progress = 0 WHERE id = :vid"),
            {"vid": video_id},
        )
        await db.commit()

        # Enqueue job
        from app.services.queue_service import enqueue_job
        await enqueue_job({
            "video_id": str(row.id),
            "blob_url": download_url,
            "original_filename": row.original_filename,
        })

        return {
            "status": "ok",
            "video_id": video_id,
            "message": f"Re-enqueued with fresh SAS URL (expires {expiry})",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to retry video: {e}")
        raise HTTPException(status_code=500, detail=str(e))



# ── Upload Health Check ──────────────────────────────────────────────────
@router.get("/upload-health")
async def get_upload_health(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Upload pipeline health metrics for the admin dashboard.

    Returns success/failure rates, average processing times, stuck uploads,
    and recent upload history.
    """
    expected_key = f"{ADMIN_ID}:{ADMIN_PASS}"
    if x_admin_key != expected_key:
        raise HTTPException(status_code=403, detail="Invalid admin credentials")

    try:
        # ── Overall counts ──
        total_uploads = await _q(db, "SELECT COUNT(*) FROM videos")
        done_count = await _q(db, "SELECT COUNT(*) FROM videos WHERE status = 'DONE'")
        error_count = await _q(db, "SELECT COUNT(*) FROM videos WHERE status = 'ERROR'")
        processing_count = await _q(
            db,
            "SELECT COUNT(*) FROM videos WHERE status NOT IN ('DONE', 'ERROR', 'NEW', 'uploaded')",
        )
        queued_count = await _q(
            db,
            "SELECT COUNT(*) FROM videos WHERE status IN ('uploaded', 'NEW')",
        )

        success_rate = round(done_count / total_uploads * 100, 1) if total_uploads > 0 else 0.0
        error_rate = round(error_count / total_uploads * 100, 1) if total_uploads > 0 else 0.0

        # ── Last 24h ──
        uploads_24h = await _q(
            db,
            "SELECT COUNT(*) FROM videos WHERE created_at >= NOW() - INTERVAL '24 hours'",
        )
        done_24h = await _q(
            db,
            "SELECT COUNT(*) FROM videos WHERE status = 'DONE' AND created_at >= NOW() - INTERVAL '24 hours'",
        )
        error_24h = await _q(
            db,
            "SELECT COUNT(*) FROM videos WHERE status = 'ERROR' AND created_at >= NOW() - INTERVAL '24 hours'",
        )

        # ── Last 7 days ──
        uploads_7d = await _q(
            db,
            "SELECT COUNT(*) FROM videos WHERE created_at >= NOW() - INTERVAL '7 days'",
        )
        done_7d = await _q(
            db,
            "SELECT COUNT(*) FROM videos WHERE status = 'DONE' AND created_at >= NOW() - INTERVAL '7 days'",
        )
        error_7d = await _q(
            db,
            "SELECT COUNT(*) FROM videos WHERE status = 'ERROR' AND created_at >= NOW() - INTERVAL '7 days'",
        )

        # ── Stuck videos (processing > 2 hours) ──
        stuck_count = await _q(
            db,
            """
            SELECT COUNT(*) FROM videos
            WHERE status NOT IN ('DONE', 'ERROR', 'NEW', 'uploaded')
              AND created_at < NOW() - INTERVAL '2 hours'
            """,
        )

        # ── Recent uploads (last 20) ──
        try:
            recent_result = await db.execute(text("""
                SELECT
                    v.id,
                    v.original_filename,
                    v.status,
                    v.upload_type,
                    v.created_at,
                    u.email as user_email
                FROM videos v
                LEFT JOIN users u ON v.user_id = u.id
                ORDER BY v.created_at DESC
                LIMIT 20
            """))
            recent_rows = recent_result.fetchall()
            recent_uploads = [
                {
                    "video_id": str(row.id),
                    "filename": row.original_filename,
                    "status": row.status,
                    "upload_type": row.upload_type,
                    "created_at": str(row.created_at) if row.created_at else None,
                    "user_email": row.user_email,
                }
                for row in recent_rows
            ]
        except Exception as e:
            logger.warning(f"Failed to fetch recent uploads: {e}")
            try:
                await db.rollback()
            except Exception:
                pass
            recent_uploads = []

        # ── Status distribution ──
        try:
            status_result = await db.execute(text("""
                SELECT status, COUNT(*) as cnt
                FROM videos
                GROUP BY status
                ORDER BY cnt DESC
            """))
            status_rows = status_result.fetchall()
            status_distribution = {row.status: row.cnt for row in status_rows}
        except Exception as e:
            logger.warning(f"Failed to fetch status distribution: {e}")
            try:
                await db.rollback()
            except Exception:
                pass
            status_distribution = {}

        # ── Error breakdown (last 7 days) ──
        try:
            error_result = await db.execute(text("""
                SELECT
                    v.id,
                    v.original_filename,
                    v.status,
                    v.created_at,
                    u.email as user_email
                FROM videos v
                LEFT JOIN users u ON v.user_id = u.id
                WHERE v.status = 'ERROR'
                  AND v.created_at >= NOW() - INTERVAL '7 days'
                ORDER BY v.created_at DESC
                LIMIT 10
            """))
            error_rows = error_result.fetchall()
            recent_errors = [
                {
                    "video_id": str(row.id),
                    "filename": row.original_filename,
                    "created_at": str(row.created_at) if row.created_at else None,
                    "user_email": row.user_email,
                }
                for row in error_rows
            ]
        except Exception as e:
            logger.warning(f"Failed to fetch error breakdown: {e}")
            try:
                await db.rollback()
            except Exception:
                pass
            recent_errors = []

        # ── Enqueue statistics ──
        enqueue_ok_count = await _q(
            db, "SELECT COUNT(*) FROM videos WHERE enqueue_status = 'OK'"
        )
        enqueue_failed_count = await _q(
            db, "SELECT COUNT(*) FROM videos WHERE enqueue_status = 'FAILED'"
        )
        enqueue_ok_24h = await _q(
            db,
            "SELECT COUNT(*) FROM videos WHERE enqueue_status = 'OK'"
            " AND created_at >= NOW() - INTERVAL '24 hours'",
        )
        enqueue_failed_24h = await _q(
            db,
            "SELECT COUNT(*) FROM videos WHERE enqueue_status = 'FAILED'"
            " AND created_at >= NOW() - INTERVAL '24 hours'",
        )
        enqueue_total = enqueue_ok_count + enqueue_failed_count
        enqueue_rate_pct = (
            round(enqueue_ok_count / enqueue_total * 100, 1) if enqueue_total > 0 else None
        )

        # ── Retry candidates (enqueue FAILED, not yet DONE/ERROR) ──
        try:
            retry_result = await db.execute(text("""
                SELECT
                    v.id,
                    v.original_filename,
                    v.status,
                    v.enqueue_status,
                    v.enqueue_error,
                    v.created_at,
                    u.email as user_email
                FROM videos v
                LEFT JOIN users u ON v.user_id = u.id
                WHERE v.enqueue_status = 'FAILED'
                  AND v.status NOT IN ('DONE', 'ERROR')
                ORDER BY v.created_at DESC
                LIMIT 10
            """))
            retry_rows = retry_result.fetchall()
            retry_candidates = [
                {
                    "video_id": str(row.id),
                    "filename": row.original_filename,
                    "status": row.status,
                    "enqueue_error": row.enqueue_error,
                    "created_at": str(row.created_at) if row.created_at else None,
                    "user_email": row.user_email,
                }
                for row in retry_rows
            ]
        except Exception as e:
            logger.warning(f"Failed to fetch retry candidates: {e}")
            try:
                await db.rollback()
            except Exception:
                pass
            retry_candidates = []

        # ── Pipeline stage distribution ──
        uploaded_waiting = await _q(
            db, "SELECT COUNT(*) FROM videos WHERE status = 'uploaded'"
        )
        pipeline_stages = {
            "uploaded_waiting": uploaded_waiting,
            "processing": processing_count,
            "done": done_count,
            "error": error_count,
            "enqueue_failed": enqueue_failed_count,
            "stuck_gt_2h": stuck_count,
        }

        return {
            "overall": {
                "total_uploads": total_uploads,
                "done": done_count,
                "error": error_count,
                "processing": processing_count,
                "queued": queued_count,
                "success_rate_pct": success_rate,
                "error_rate_pct": error_rate,
            },
            "last_24h": {
                "uploads": uploads_24h,
                "done": done_24h,
                "error": error_24h,
            },
            "last_7d": {
                "uploads": uploads_7d,
                "done": done_7d,
                "error": error_7d,
            },
            "enqueue_stats": {
                "total_ok": enqueue_ok_count,
                "total_failed": enqueue_failed_count,
                "ok_last_24h": enqueue_ok_24h,
                "failed_last_24h": enqueue_failed_24h,
                "enqueue_success_rate_pct": enqueue_rate_pct,
            },
            "pipeline_stages": pipeline_stages,
            "retry_candidates": retry_candidates,
            "stuck_videos": stuck_count,
            "status_distribution": status_distribution,
            "recent_uploads": recent_uploads,
            "recent_errors": recent_errors,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to get upload health: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Recompute Phase Metrics ──────────────────────────────────────────────────

@router.post("/recompute-phase-metrics/{video_id}")
async def recompute_phase_metrics(
    video_id: str,
    dry_run: bool = False,
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None),
):
    """
    既存動画の video_phases テーブルの CSV 由来指標を再計算して上書きする。

    修正後ロジック（csv_first_sec + time_offset_seconds + start_sec）で
    gmv / order_count / viewer_count / like_count / comment_count /
    share_count / new_followers / product_clicks / conversion_rate / gpm
    を再集計する。

    Parameters
    ----------
    video_id : str
        対象動画の UUID
    dry_run : bool
        True の場合、計算結果を返すが DB は更新しない

    Headers
    -------
    X-Admin-Key : str
        認証キー（aither:hub）
    """
    if x_admin_key != f"{ADMIN_ID}:{ADMIN_PASS}":
        raise HTTPException(status_code=403, detail="Forbidden")

    # ── 動画情報を取得 ──────────────────────────────────────────
    sql_video = text("""
        SELECT id, original_filename, upload_type,
               excel_trend_blob_url, time_offset_seconds
        FROM videos
        WHERE id = :vid
    """)
    result = await db.execute(sql_video, {"vid": video_id})
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Video not found")

    vid = str(row[0])
    fname = str(row[1] or "?")
    upload_type = str(row[2] or "")
    trend_url = str(row[3] or "")
    time_offset = float(row[4] or 0)

    if upload_type != "clean_video":
        raise HTTPException(
            status_code=400,
            detail=f"Video upload_type={upload_type!r} is not 'clean_video'. "
                   "Only clean_video has CSV trend data.",
        )
    if not trend_url or len(trend_url) < 5:
        raise HTTPException(status_code=400, detail="No excel_trend_blob_url found for this video")

    # ── フェーズ一覧を取得 ──────────────────────────────────────
    sql_phases = text("""
        SELECT phase_index, time_start, time_end
        FROM video_phases
        WHERE video_id = :vid
        ORDER BY phase_index
    """)
    r2 = await db.execute(sql_phases, {"vid": vid})
    phase_rows = r2.fetchall()
    phases = [
        {"phase_index": pr[0], "time_start": pr[1], "time_end": pr[2]}
        for pr in phase_rows
    ]
    if not phases:
        raise HTTPException(status_code=400, detail="No phases found for this video")

    # ── Excel/CSV を読み込む ────────────────────────────────────
    try:
        import sys, os
        worker_batch = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "..", "worker", "batch"
        )
        if worker_batch not in sys.path:
            sys.path.insert(0, os.path.abspath(worker_batch))
        from excel_parser import load_excel_data
    except ImportError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Cannot import excel_parser from worker: {e}. "
                   "Run backfill_phase_metrics.py on the Worker VM instead.",
        )

    excel_urls = {
        "excel_trend_blob_url":   trend_url,
        "excel_product_blob_url": None,
        "upload_type":            "clean_video",
        "time_offset_seconds":    time_offset,
    }
    excel_data = load_excel_data(vid, excel_urls)
    if not excel_data or not excel_data.get("has_trend_data"):
        raise HTTPException(status_code=400, detail="No trend data loaded from Excel/CSV")

    trends = excel_data["trends"]

    # ── 指標を再計算 ────────────────────────────────────────────
    try:
        from csv_slot_filter import (
            _find_key, _safe_float, _parse_time_to_seconds,
            _detect_time_key, compute_slot_scores, KPI_ALIASES,
        )
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"Cannot import csv_slot_filter: {e}")

    try:
        from column_normalizer import detect_all_columns
        detection_result = detect_all_columns(trends[0] if trends else {})
        detected = detection_result["detected"]
        gmv_key      = detected.get("gmv")
        order_key    = detected.get("order_count")
        viewer_key   = detected.get("viewer_count")
        like_key     = detected.get("like_count")
        comment_key  = detected.get("comment_count")
        share_key    = detected.get("share_count")
        follower_key = detected.get("new_followers")
        click_key    = detected.get("product_clicks")
        conv_key     = detected.get("ctor")
        gpm_key      = detected.get("gpm")
    except ImportError:
        sample = trends[0] if trends else {}
        gmv_key      = _find_key(sample, KPI_ALIASES["gmv"])
        order_key    = _find_key(sample, KPI_ALIASES["order_count"])
        viewer_key   = _find_key(sample, KPI_ALIASES["viewer_count"])
        like_key     = _find_key(sample, KPI_ALIASES["like_count"])
        comment_key  = _find_key(sample, KPI_ALIASES["comment_count"])
        share_key    = _find_key(sample, KPI_ALIASES["share_count"])
        follower_key = _find_key(sample, KPI_ALIASES["new_followers"])
        click_key    = _find_key(sample, KPI_ALIASES["product_clicks"])
        conv_key     = _find_key(sample, KPI_ALIASES["ctor"])
        gpm_key      = _find_key(sample, KPI_ALIASES["gpm"])

    time_key = _detect_time_key(trends)
    timed_entries = []
    if time_key:
        for entry in trends:
            t_sec = _parse_time_to_seconds(entry.get(time_key))
            if t_sec is not None:
                timed_entries.append({"time_sec": t_sec, "entry": entry})
        timed_entries.sort(key=lambda x: x["time_sec"])

    if not timed_entries:
        raise HTTPException(status_code=400, detail="No timed entries found in CSV")

    csv_first_sec = timed_entries[0]["time_sec"]
    scored_slots = compute_slot_scores(trends)
    score_map = {s["time_sec"]: s["score"] for s in scored_slots}

    phase_results = []
    for ph in phases:
        phase_index = ph["phase_index"]
        start_sec   = float(ph["time_start"] or 0)
        end_sec     = float(ph["time_end"] or 0)

        # 正しい変換: csv_first_sec + time_offset_seconds + phase_relative_sec
        phase_abs_start = csv_first_sec + time_offset + start_sec
        phase_abs_end   = csv_first_sec + time_offset + end_sec

        phase_gmv = 0.0; phase_orders = 0; phase_viewers = 0
        phase_likes = 0; phase_comments = 0; phase_shares = 0
        phase_followers = 0; phase_clicks = 0
        phase_conv = 0.0; phase_gpm = 0.0; phase_score = 0.0
        match_count = 0

        for te in timed_entries:
            t = te["time_sec"]
            e = te["entry"]
            if phase_abs_start <= t <= phase_abs_end:
                match_count += 1
                if gmv_key:      phase_gmv      += _safe_float(e.get(gmv_key)) or 0
                if order_key:    phase_orders   += int(_safe_float(e.get(order_key)) or 0)
                if viewer_key:   phase_viewers   = max(phase_viewers, int(_safe_float(e.get(viewer_key)) or 0))
                if like_key:     phase_likes     = max(phase_likes, int(_safe_float(e.get(like_key)) or 0))
                if comment_key:  phase_comments += int(_safe_float(e.get(comment_key)) or 0)
                if share_key:    phase_shares   += int(_safe_float(e.get(share_key)) or 0)
                if follower_key: phase_followers += int(_safe_float(e.get(follower_key)) or 0)
                if click_key:    phase_clicks   += int(_safe_float(e.get(click_key)) or 0)
                if conv_key:     phase_conv      = max(phase_conv, _safe_float(e.get(conv_key)) or 0)
                if gpm_key:      phase_gpm       = max(phase_gpm, _safe_float(e.get(gpm_key)) or 0)
                phase_score = max(phase_score, score_map.get(t, 0))

        metrics = {
            "phase_index":     phase_index,
            "gmv":             round(phase_gmv, 2),
            "order_count":     phase_orders,
            "viewer_count":    phase_viewers,
            "like_count":      phase_likes,
            "comment_count":   phase_comments,
            "share_count":     phase_shares,
            "new_followers":   phase_followers,
            "product_clicks":  phase_clicks,
            "conversion_rate": round(phase_conv, 4),
            "gpm":             round(phase_gpm, 2),
            "importance_score":round(phase_score, 4),
            "_debug": {
                "phase_abs_start":  round(phase_abs_start, 1),
                "phase_abs_end":    round(phase_abs_end, 1),
                "csv_match_count":  match_count,
            },
        }
        phase_results.append(metrics)

        # DB 更新
        if not dry_run:
            sql_upd = text("""
                UPDATE video_phases
                SET
                    gmv              = :gmv,
                    order_count      = :order_count,
                    viewer_count     = :viewer_count,
                    like_count       = :like_count,
                    comment_count    = :comment_count,
                    share_count      = :share_count,
                    new_followers    = :new_followers,
                    product_clicks   = :product_clicks,
                    conversion_rate  = :conversion_rate,
                    gpm              = :gpm,
                    importance_score = :importance_score,
                    updated_at       = now()
                WHERE video_id = :video_id
                  AND phase_index = :phase_index
            """)
            await db.execute(sql_upd, {
                "video_id":         vid,
                "phase_index":      phase_index,
                "gmv":              metrics["gmv"],
                "order_count":      metrics["order_count"],
                "viewer_count":     metrics["viewer_count"],
                "like_count":       metrics["like_count"],
                "comment_count":    metrics["comment_count"],
                "share_count":      metrics["share_count"],
                "new_followers":    metrics["new_followers"],
                "product_clicks":   metrics["product_clicks"],
                "conversion_rate":  metrics["conversion_rate"],
                "gpm":              metrics["gpm"],
                "importance_score": metrics["importance_score"],
            })

    if not dry_run:
        await db.commit()

    return {
        "status":              "ok" if not dry_run else "dry_run",
        "video_id":            vid,
        "filename":            fname,
        "dry_run":             dry_run,
        "csv_first_sec":       round(csv_first_sec, 1),
        "time_offset_seconds": time_offset,
        "trend_rows":          len(trends),
        "phases_updated":      len(phase_results),
        "phase_metrics":       phase_results,
    }
