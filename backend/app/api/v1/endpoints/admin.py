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
