"""
Admin dashboard API endpoint.
Provides platform-wide statistics for the master dashboard.
Each query is isolated with rollback on failure to prevent cascade errors.
"""
from fastapi import APIRouter, Depends, HTTPException, Header, Request
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
        except Exception as _rb_err:
            logger.debug(f"Rollback cleanup failed: {_rb_err}")
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
    Supports both standard videos and LiveBoost (live_boost) videos.
    For standard videos: generates a fresh SAS URL and pushes a new job.
    For LiveBoost videos: creates/resets a LiveAnalysisJob and enqueues live_analysis."""
    if x_admin_key != f"{ADMIN_ID}:{ADMIN_PASS}":
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        # Get video info (including upload_type)
        sql = text("""
            SELECT v.id, v.original_filename, v.status, v.user_id,
                   v.upload_type, u.email as user_email
            FROM videos v
            LEFT JOIN users u ON v.user_id = u.id
            WHERE v.id = :vid
        """)
        result = await db.execute(sql, {"vid": video_id})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Video not found")

        upload_type = row.upload_type or ""

        # ── LiveBoost (live_boost) videos: use live_analysis pipeline ──
        if upload_type == "live_boost":
            return await _retry_live_boost_admin(
                db=db,
                video_id=str(row.id),
                user_id=row.user_id,
                user_email=row.user_email,
            )

        # ── Standard videos: use standard pipeline ──
        # Generate fresh SAS URL
        from app.services.storage_service import generate_download_sas
        download_url, expiry = await generate_download_sas(
            email=row.user_email,
            video_id=str(row.id),
            filename=row.original_filename,
            expires_in_minutes=1440,  # 24 hours
        )

        # Reset status (use STEP_0 instead of 'uploaded' per project rules)
        await db.execute(
            text("UPDATE videos SET status = 'STEP_0_EXTRACT_FRAMES', step_progress = 0 WHERE id = :vid"),
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


async def _retry_live_boost_admin(
    db: AsyncSession,
    video_id: str,
    user_id: int,
    user_email: str,
) -> dict:
    """Admin retry for LiveBoost videos — creates/resets LiveAnalysisJob and enqueues."""
    import uuid as uuid_module
    from datetime import datetime, timezone
    from sqlalchemy import select as sa_select, update as sa_update

    try:
        from app.models.orm.live_analysis_job import LiveAnalysisJob
        from app.services.queue_service import enqueue_job

        # Check for existing job
        result = await db.execute(
            sa_select(LiveAnalysisJob).where(
                LiveAnalysisJob.video_id == video_id,
            ).order_by(LiveAnalysisJob.created_at.desc())
        )
        existing_job = result.scalar_one_or_none()

        if existing_job:
            existing_job.status = "pending"
            existing_job.current_step = None
            existing_job.progress = 0
            existing_job.error_message = None
            existing_job.started_at = None
            existing_job.completed_at = None
            existing_job.results = None
            job = existing_job
            total_chunks = existing_job.total_chunks
            stream_source = existing_job.stream_source or "tiktok_live"
        else:
            job = LiveAnalysisJob(
                id=uuid_module.uuid4(),
                video_id=video_id,
                user_id=user_id,
                stream_source="tiktok_live",
                status="pending",
                progress=0,
            )
            db.add(job)
            total_chunks = None
            stream_source = "tiktok_live"

        # Reset video status
        await db.execute(
            text("""
                UPDATE videos
                SET status = 'STEP_0_EXTRACT_FRAMES',
                    step_progress = 0,
                    error_message = NULL,
                    updated_at = now()
                WHERE id = :vid
            """),
            {"vid": video_id},
        )
        await db.commit()
        await db.refresh(job)

        # Enqueue live_analysis job
        enqueue_result = await enqueue_job({
            "job_type": "live_analysis",
            "job_id": str(job.id),
            "video_id": video_id,
            "user_id": user_id,
            "stream_source": stream_source,
            "total_chunks": total_chunks,
            "email": user_email or "",
        })

        if enqueue_result.success:
            await db.execute(
                sa_update(LiveAnalysisJob)
                .where(LiveAnalysisJob.id == job.id)
                .values(
                    queue_message_id=enqueue_result.message_id,
                    queue_enqueued_at=enqueue_result.enqueued_at,
                    started_at=datetime.now(timezone.utc),
                )
            )
            await db.commit()
            logger.info(
                f"[admin/retry-video/live_boost] Enqueued OK job={job.id} video={video_id}"
            )
        else:
            logger.error(
                f"[admin/retry-video/live_boost] Enqueue FAILED: {enqueue_result.error}"
            )
            raise HTTPException(
                status_code=500,
                detail=f"LiveBoost enqueue failed: {enqueue_result.error}",
            )

        return {
            "status": "ok",
            "video_id": video_id,
            "job_id": str(job.id),
            "message": f"LiveBoost analysis re-enqueued (job={job.id})",
        }
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.exception(f"[admin/retry-video/live_boost] Failed: {e}")
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
            except Exception as _rb_err:
                logger.debug(f"Rollback cleanup failed: {_rb_err}")
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
            except Exception as _rb_err:
                logger.debug(f"Rollback cleanup failed: {_rb_err}")
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
            except Exception as _rb_err:
                logger.debug(f"Rollback cleanup failed: {_rb_err}")
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
            except Exception as _rb_err:
                logger.debug(f"Rollback cleanup failed: {_rb_err}")
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

        # ── Recent stage events (from upload_event_log) ──
        recent_stage_events = []
        try:
            stage_result = await db.execute(text("""
                SELECT
                    video_id, upload_id, user_id, stage, status,
                    duration_ms, error_message, error_type, created_at
                FROM upload_event_log
                WHERE status = 'error'
                ORDER BY created_at DESC
                LIMIT 20
            """))
            stage_rows = stage_result.fetchall()
            recent_stage_events = [
                {
                    "video_id": str(row.video_id) if row.video_id else None,
                    "upload_id": str(row.upload_id) if row.upload_id else None,
                    "stage": row.stage,
                    "status": row.status,
                    "duration_ms": row.duration_ms,
                    "error_message": row.error_message,
                    "error_type": row.error_type,
                    "created_at": str(row.created_at) if row.created_at else None,
                }
                for row in stage_rows
            ]
        except Exception as e:
            logger.warning(f"Failed to fetch stage events (table may not exist): {e}")
            try:
                await db.rollback()
            except Exception as _rb_err:
                logger.debug(f"Rollback cleanup failed: {_rb_err}")

        # ── Videos with upload_error_stage ──
        failed_stage_videos = []
        try:
            fs_result = await db.execute(text("""
                SELECT
                    v.id, v.original_filename, v.status,
                    v.upload_last_stage, v.upload_error_stage,
                    v.upload_error_message, v.created_at,
                    u.email as user_email
                FROM videos v
                LEFT JOIN users u ON v.user_id = u.id
                WHERE v.upload_error_stage IS NOT NULL
                ORDER BY v.created_at DESC
                LIMIT 10
            """))
            fs_rows = fs_result.fetchall()
            failed_stage_videos = [
                {
                    "video_id": str(row.id),
                    "filename": row.original_filename,
                    "status": row.status,
                    "last_stage": row.upload_last_stage,
                    "error_stage": row.upload_error_stage,
                    "error_message": (row.upload_error_message or "")[:200],
                    "created_at": str(row.created_at) if row.created_at else None,
                    "user_email": row.user_email,
                }
                for row in fs_rows
            ]
        except Exception as e:
            logger.warning(f"Failed to fetch failed stage videos (columns may not exist): {e}")
            try:
                await db.rollback()
            except Exception as _rb_err:
                logger.debug(f"Rollback cleanup failed: {_rb_err}")

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
            "recent_stage_events": recent_stage_events,
            "failed_stage_videos": failed_stage_videos,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to get upload health: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Recompute Phase Metrics (v2 — service-based) ─────────────────────────────

@router.post("/recompute-phase-metrics/{video_id}")
async def recompute_phase_metrics(
    video_id: str,
    dry_run: bool = True,
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None),
):
    """
    既存動画の Derived Data（phase metrics）を最新ロジックで再計算する。

    Raw Data / Human Data は一切変更しない。

    Parameters
    ----------
    video_id : str
        対象動画の UUID
    dry_run : bool
        True（デフォルト）の場合、計算結果を返すが DB は更新しない。
        False の場合、DB を更新する。
    """
    if x_admin_key != f"{ADMIN_ID}:{ADMIN_PASS}":
        raise HTTPException(status_code=403, detail="Forbidden")

    # Ensure migration tables exist
    try:
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS phase_metrics_recalc_log (
                id BIGSERIAL PRIMARY KEY,
                video_id VARCHAR(255) NOT NULL,
                triggered_by VARCHAR(255),
                mode VARCHAR(20) NOT NULL DEFAULT 'dry-run',
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                logic_version INTEGER NOT NULL DEFAULT 1,
                before_json JSONB, after_json JSONB,
                diff_json JSONB, logs_json JSONB,
                error_message TEXT, duration_ms INTEGER,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        await db.execute(text(
            "ALTER TABLE video_phases ADD COLUMN IF NOT EXISTS "
            "phase_metrics_version_applied INTEGER DEFAULT NULL"
        ))
        await db.execute(text(
            "ALTER TABLE videos ADD COLUMN IF NOT EXISTS "
            "phase_metrics_version_applied INTEGER DEFAULT NULL"
        ))
        await db.execute(text(
            "ALTER TABLE videos ADD COLUMN IF NOT EXISTS "
            "last_recalculated_at TIMESTAMPTZ DEFAULT NULL"
        ))
        await db.commit()
    except Exception as mig_err:
        logger.warning(f"Migration check: {mig_err}")
        try:
            await db.rollback()
        except Exception as _rb_err:
            logger.debug(f"Rollback cleanup failed: {_rb_err}")

    try:
        from app.services.phase_metrics_recalculator import recalculate_phase_metrics as _recalc
    except ImportError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Cannot import recalculator service: {e}",
        )

    result = await _recalc(
        video_id=video_id,
        db=db,
        dry_run=dry_run,
        triggered_by=f"admin:{x_admin_key}",
    )

    if result["status"] == "error":
        error_logs = [l for l in result.get("logs", []) if "ERROR" in l]
        detail = error_logs[0] if error_logs else "Recalculation failed"
        raise HTTPException(status_code=400, detail=detail)

    return result


@router.get("/recalc-log/{video_id}")
async def get_recalc_log(
    video_id: str,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None),
):
    """
    動画の再計算履歴を取得する。
    """
    if x_admin_key != f"{ADMIN_ID}:{ADMIN_PASS}":
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        r = await db.execute(text("""
            SELECT id, triggered_by, mode, status, logic_version,
                   diff_json, error_message, duration_ms, created_at
            FROM phase_metrics_recalc_log
            WHERE video_id = :vid
            ORDER BY created_at DESC
            LIMIT :lim
        """), {"vid": video_id, "lim": limit})
        rows = r.fetchall()
    except Exception:
        return {"logs": [], "message": "recalc_log table may not exist yet"}

    logs = []
    for row in rows:
        import json as _json
        diff = None
        try:
            diff = _json.loads(row[5]) if row[5] else None
        except Exception:
            diff = row[5]
        logs.append({
            "id":             row[0],
            "triggered_by":   row[1],
            "mode":           row[2],
            "status":         row[3],
            "logic_version":  row[4],
            "diff":           diff,
            "error_message":  row[6],
            "duration_ms":    row[7],
            "created_at":     row[8].isoformat() if row[8] else None,
        })

    return {"video_id": video_id, "logs": logs}


@router.post("/recalc-all")
async def recalc_all_videos(
    dry_run: bool = True,
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None),
):
    """
    全動画の phase metrics を一括再計算する。
    """
    if x_admin_key != f"{ADMIN_ID}:{ADMIN_PASS}":
        raise HTTPException(status_code=403, detail="Forbidden")

    # Get all eligible videos
    r = await db.execute(text("""
        SELECT v.id
        FROM videos v
        WHERE v.status IN ('completed', 'DONE')
          AND v.upload_type = 'clean_video'
          AND v.excel_trend_blob_url IS NOT NULL
          AND LENGTH(v.excel_trend_blob_url) > 5
          AND v.id IN (SELECT DISTINCT video_id FROM video_phases)
        ORDER BY v.created_at DESC
    """))
    video_ids = [str(row[0]) for row in r.fetchall()]

    if not video_ids:
        return {"status": "ok", "message": "No eligible videos found", "results": []}

    try:
        from app.services.phase_metrics_recalculator import recalculate_phase_metrics as _recalc
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"Cannot import recalculator: {e}")

    results = []
    for vid in video_ids:
        try:
            result = await _recalc(
                video_id=vid,
                db=db,
                dry_run=dry_run,
                triggered_by=f"admin:recalc-all",
            )
            results.append({
                "video_id": vid,
                "status": result["status"],
                "phases_updated": result.get("phases_updated", 0),
                "diff_summary": {
                    "phases_changed": result.get("diff", {}).get("phases_changed", 0),
                    "gmv_delta": result.get("diff", {}).get("gmv_delta", 0),
                },
            })
        except Exception as e:
            results.append({"video_id": vid, "status": "error", "error": str(e)})

    return {
        "status": "ok",
        "dry_run": dry_run,
        "total_videos": len(video_ids),
        "success": sum(1 for r in results if r["status"] == "success"),
        "errors": sum(1 for r in results if r["status"] == "error"),
        "results": results,
    }


# ──────────────────────────────────────────────────────────────────────
# Frontend Diagnostics endpoints
# ──────────────────────────────────────────────────────────────────────

@router.post("/frontend-diagnostics")
async def report_frontend_error(
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """
    Frontend からセクションエラーを受信して DB に保存する。
    認証不要（エラー報告はログイン失敗時にも送れる必要がある）。

    Payload:
        video_id      - 動画ID
        section_name  - セクション名 (e.g., "MomentClips")
        endpoint      - APIエンドポイント
        error_type    - エラータイプ (auth/not_found/timeout/server/network/parse/unknown)
        error_message - エラーメッセージ
        http_status   - HTTPステータスコード
        request_id    - X-Request-Id
        page_url      - ページURL
        user_agent    - ブラウザUA
    """
    try:
        # Auto-create table if not exists (same pattern as phase_metrics_recalc_log)
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS frontend_diagnostics (
                id BIGSERIAL PRIMARY KEY,
                video_id VARCHAR(255) NOT NULL DEFAULT '',
                section_name VARCHAR(100) NOT NULL DEFAULT 'unknown',
                endpoint VARCHAR(500) DEFAULT '',
                error_type VARCHAR(50) NOT NULL DEFAULT 'unknown',
                error_message TEXT,
                http_status INTEGER,
                request_id VARCHAR(100) DEFAULT '',
                page_url VARCHAR(1000) DEFAULT '',
                user_agent VARCHAR(500) DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        await db.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_fd_video_id ON frontend_diagnostics (video_id)
        """))
        await db.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_fd_section_name ON frontend_diagnostics (section_name)
        """))
        await db.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_fd_error_type ON frontend_diagnostics (error_type)
        """))
        await db.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_fd_created_at ON frontend_diagnostics (created_at)
        """))

        sql = text("""
            INSERT INTO frontend_diagnostics
                (video_id, section_name, endpoint, error_type, error_message,
                 http_status, request_id, page_url, user_agent)
            VALUES
                (:video_id, :section_name, :endpoint, :error_type, :error_message,
                 :http_status, :request_id, :page_url, :user_agent)
        """)
        await db.execute(sql, {
            "video_id": payload.get("video_id", ""),
            "section_name": payload.get("section_name", "unknown"),
            "endpoint": payload.get("endpoint", ""),
            "error_type": payload.get("error_type", "unknown"),
            "error_message": str(payload.get("error_message", ""))[:2000],
            "http_status": payload.get("http_status"),
            "request_id": payload.get("request_id", ""),
            "page_url": str(payload.get("page_url", ""))[:1000],
            "user_agent": str(payload.get("user_agent", ""))[:500],
        })
        await db.commit()
        return {"status": "ok"}
    except Exception as e:
        logger.warning(f"Failed to save frontend diagnostic: {e}")
        try:
            await db.rollback()
        except Exception as _rb_err:
            logger.debug(f"Rollback cleanup failed: {_rb_err}")
        # エラー報告の保存失敗はフロントに影響させない
        return {"status": "ok", "note": "save_failed"}


@router.get("/frontend-diagnostics")
async def get_frontend_diagnostics(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
    video_id: Optional[str] = None,
    section_name: Optional[str] = None,
    error_type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    """
    Admin 用: Frontend エラーログを取得する。
    フィルタ: video_id, section_name, error_type
    """
    expected_key = f"{ADMIN_ID}:{ADMIN_PASS}"
    if x_admin_key != expected_key:
        raise HTTPException(status_code=403, detail="Invalid admin credentials")

    try:
        # Ensure table exists
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS frontend_diagnostics (
                id BIGSERIAL PRIMARY KEY,
                video_id VARCHAR(255) NOT NULL DEFAULT '',
                section_name VARCHAR(100) NOT NULL DEFAULT 'unknown',
                endpoint VARCHAR(500) DEFAULT '',
                error_type VARCHAR(50) NOT NULL DEFAULT 'unknown',
                error_message TEXT,
                http_status INTEGER,
                request_id VARCHAR(100) DEFAULT '',
                page_url VARCHAR(1000) DEFAULT '',
                user_agent VARCHAR(500) DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))

        conditions = []
        params = {"lim": limit, "off": offset}

        if video_id:
            conditions.append("video_id = :vid")
            params["vid"] = video_id
        if section_name:
            conditions.append("section_name = :sn")
            params["sn"] = section_name
        if error_type:
            conditions.append("error_type = :et")
            params["et"] = error_type

        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        # 集計
        count_sql = text(f"SELECT COUNT(*) FROM frontend_diagnostics {where_clause}")
        total = (await db.execute(count_sql, params)).scalar() or 0

        # エラーログ一覧
        sql = text(f"""
            SELECT id, video_id, section_name, endpoint, error_type,
                   error_message, http_status, request_id, page_url,
                   user_agent, created_at
            FROM frontend_diagnostics
            {where_clause}
            ORDER BY created_at DESC
            LIMIT :lim OFFSET :off
        """)
        result = await db.execute(sql, params)
        rows = result.fetchall()

        errors = []
        for r in rows:
            errors.append({
                "id": r.id,
                "video_id": r.video_id,
                "section_name": r.section_name,
                "endpoint": r.endpoint,
                "error_type": r.error_type,
                "error_message": r.error_message,
                "http_status": r.http_status,
                "request_id": r.request_id,
                "page_url": r.page_url,
                "created_at": str(r.created_at) if r.created_at else None,
            })

        # セクション別集計
        summary_sql = text("""
            SELECT section_name, error_type, COUNT(*) as cnt
            FROM frontend_diagnostics
            WHERE created_at >= NOW() - INTERVAL '24 hours'
            GROUP BY section_name, error_type
            ORDER BY cnt DESC
        """)
        summary_result = await db.execute(summary_sql)
        summary_rows = summary_result.fetchall()

        section_summary = {}
        for sr in summary_rows:
            sn = sr.section_name
            if sn not in section_summary:
                section_summary[sn] = {"total": 0, "by_type": {}}
            section_summary[sn]["total"] += sr.cnt
            section_summary[sn]["by_type"][sr.error_type] = sr.cnt

        return {
            "total": total,
            "errors": errors,
            "section_summary_24h": section_summary,
        }
    except Exception as e:
        logger.exception(f"Failed to fetch frontend diagnostics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/frontend-diagnostics/summary")
async def get_frontend_diagnostics_summary(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
    hours: int = 24,
):
    """
    Admin 用: Frontend エラーのサマリーを取得する。
    - セクション別エラー件数
    - エラータイプ別件数
    - 直近のエラー傾向
    """
    expected_key = f"{ADMIN_ID}:{ADMIN_PASS}"
    if x_admin_key != expected_key:
        raise HTTPException(status_code=403, detail="Invalid admin credentials")

    try:
        # Ensure table exists
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS frontend_diagnostics (
                id BIGSERIAL PRIMARY KEY,
                video_id VARCHAR(255) NOT NULL DEFAULT '',
                section_name VARCHAR(100) NOT NULL DEFAULT 'unknown',
                endpoint VARCHAR(500) DEFAULT '',
                error_type VARCHAR(50) NOT NULL DEFAULT 'unknown',
                error_message TEXT,
                http_status INTEGER,
                request_id VARCHAR(100) DEFAULT '',
                page_url VARCHAR(1000) DEFAULT '',
                user_agent VARCHAR(500) DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))

        interval = f"{hours} hours"

        # セクション別
        by_section_sql = text(f"""
            SELECT section_name, COUNT(*) as cnt
            FROM frontend_diagnostics
            WHERE created_at >= NOW() - INTERVAL '{interval}'
            GROUP BY section_name
            ORDER BY cnt DESC
        """)
        by_section = await db.execute(by_section_sql)
        section_counts = {r.section_name: r.cnt for r in by_section.fetchall()}

        # エラータイプ別
        by_type_sql = text(f"""
            SELECT error_type, COUNT(*) as cnt
            FROM frontend_diagnostics
            WHERE created_at >= NOW() - INTERVAL '{interval}'
            GROUP BY error_type
            ORDER BY cnt DESC
        """)
        by_type = await db.execute(by_type_sql)
        type_counts = {r.error_type: r.cnt for r in by_type.fetchall()}

        # 総件数
        total_sql = text(f"""
            SELECT COUNT(*) FROM frontend_diagnostics
            WHERE created_at >= NOW() - INTERVAL '{interval}'
        """)
        total = (await db.execute(total_sql)).scalar() or 0

        # 直近10件
        recent_sql = text(f"""
            SELECT video_id, section_name, error_type, request_id, created_at
            FROM frontend_diagnostics
            WHERE created_at >= NOW() - INTERVAL '{interval}'
            ORDER BY created_at DESC
            LIMIT 10
        """)
        recent_result = await db.execute(recent_sql)
        recent = [
            {
                "video_id": r.video_id,
                "section_name": r.section_name,
                "error_type": r.error_type,
                "request_id": r.request_id,
                "created_at": str(r.created_at) if r.created_at else None,
            }
            for r in recent_result.fetchall()
        ]

        return {
            "period_hours": hours,
            "total_errors": total,
            "by_section": section_counts,
            "by_error_type": type_counts,
            "recent_errors": recent,
        }
    except Exception as e:
        logger.exception(f"Failed to fetch diagnostics summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── CSV Validation Log ───

@router.post("/csv-validation-log")
async def log_csv_validation(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    CSV Date/Time Validation Gate の判定結果とユーザーの選択をログ保存する。
    テーブルが存在しない場合は自動作成する。
    """
    try:
        body = await request.json()

        # テーブル自動作成 (PostgreSQL互換)
        create_sql = text("""
            CREATE TABLE IF NOT EXISTS csv_validation_logs (
                id BIGSERIAL PRIMARY KEY,
                verdict VARCHAR(20),
                decision VARCHAR(20),
                video_filename VARCHAR(500),
                trend_filename VARCHAR(500),
                product_filename VARCHAR(500),
                checks JSONB,
                user_email VARCHAR(255),
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await db.execute(create_sql)
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_csv_val_created ON csv_validation_logs (created_at)",
            "CREATE INDEX IF NOT EXISTS idx_csv_val_verdict ON csv_validation_logs (verdict)",
            "CREATE INDEX IF NOT EXISTS idx_csv_val_decision ON csv_validation_logs (decision)",
        ]:
            await db.execute(text(idx_sql))

        insert_sql = text("""
            INSERT INTO csv_validation_logs
                (verdict, decision, video_filename, trend_filename, product_filename, checks, user_email)
            VALUES
                (:verdict, :decision, :video_filename, :trend_filename, :product_filename, :checks, :user_email)
        """)
        import json as json_mod
        await db.execute(insert_sql, {
            "verdict": body.get("verdict"),
            "decision": body.get("decision"),
            "video_filename": body.get("video_filename", "")[:500],
            "trend_filename": body.get("trend_filename", "")[:500],
            "product_filename": body.get("product_filename", "")[:500],
            "checks": json_mod.dumps(body.get("checks", []), ensure_ascii=False),
            "user_email": body.get("user_email", "")[:255],
        })
        await db.commit()

        return {"status": "ok"}
    except Exception as e:
        logger.warning(f"Failed to log CSV validation: {e}")
        return {"status": "error", "detail": str(e)}


@router.get("/csv-validation-logs")
async def get_csv_validation_logs(
    request: Request,
    db: AsyncSession = Depends(get_db),
    limit: int = 50,
    offset: int = 0,
    verdict: str = None,
    decision: str = None,
):
    """
    CSV Validation ログ一覧を取得する（Admin用）。
    """
    try:
        # テーブル自動作成 (PostgreSQL互換)
        create_sql = text("""
            CREATE TABLE IF NOT EXISTS csv_validation_logs (
                id BIGSERIAL PRIMARY KEY,
                verdict VARCHAR(20),
                decision VARCHAR(20),
                video_filename VARCHAR(500),
                trend_filename VARCHAR(500),
                product_filename VARCHAR(500),
                checks JSONB,
                user_email VARCHAR(255),
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await db.execute(create_sql)
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_csv_val_created ON csv_validation_logs (created_at)",
            "CREATE INDEX IF NOT EXISTS idx_csv_val_verdict ON csv_validation_logs (verdict)",
            "CREATE INDEX IF NOT EXISTS idx_csv_val_decision ON csv_validation_logs (decision)",
        ]:
            await db.execute(text(idx_sql))

        where_clauses = ["1=1"]
        params = {"limit_val": limit, "offset_val": offset}

        if verdict:
            where_clauses.append("verdict = :verdict")
            params["verdict"] = verdict
        if decision:
            where_clauses.append("decision = :decision")
            params["decision"] = decision

        where_str = " AND ".join(where_clauses)

        sql = text(f"""
            SELECT id, verdict, decision, video_filename, trend_filename, product_filename,
                   checks, user_email, created_at
            FROM csv_validation_logs
            WHERE {where_str}
            ORDER BY created_at DESC
            LIMIT :limit_val OFFSET :offset_val
        """)
        result = await db.execute(sql, params)
        rows = result.fetchall()

        return {
            "logs": [
                {
                    "id": r.id,
                    "verdict": r.verdict,
                    "decision": r.decision,
                    "video_filename": r.video_filename,
                    "trend_filename": r.trend_filename,
                    "product_filename": r.product_filename,
                    "checks": r.checks,
                    "user_email": r.user_email,
                    "created_at": str(r.created_at) if r.created_at else None,
                }
                for r in rows
            ],
            "limit": limit,
            "offset": offset,
        }
    except Exception as e:
        logger.exception(f"Failed to fetch CSV validation logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Recalculate CSV Metrics from Excel ───

@router.post("/recalc-csv-metrics/{video_id}")
async def recalc_csv_metrics(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None),
):
    """
    ExcelトレンドデータからCSVメトリクスを再計算し、video_phasesテーブルに保存する。
    ワーカーVMに依存せず、バックエンド側で直接計算を行う。

    按分ロジック:
    - CSVの各30分スロットを、そのスロット内のフェーズに時間比例で按分
    - 加算型メトリクス（GMV, orders等）: 按分
    - スナップショット型メトリクス（viewers, likes等）: 最大値
    """
    if x_admin_key != f"{ADMIN_ID}:{ADMIN_PASS}":
        raise HTTPException(status_code=403, detail="Forbidden")

    import json as _json
    import httpx
    import tempfile
    import os
    import re
    from datetime import datetime, timedelta

    try:
        from app.services.storage_service import generate_read_sas_from_url
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"Cannot import storage_service: {e}")

    # Step 1: Get video info
    result = await db.execute(text("""
        SELECT v.excel_trend_blob_url, v.upload_type, v.time_offset_seconds,
               u.email
        FROM videos v
        LEFT JOIN users u ON v.user_id = u.id
        WHERE v.id = :vid
    """), {"vid": video_id})
    video_row = result.fetchone()
    if not video_row:
        raise HTTPException(status_code=404, detail="Video not found")

    trend_blob_url = video_row[0]
    upload_type = video_row[1]
    time_offset_seconds = float(video_row[2] or 0)

    if not trend_blob_url:
        raise HTTPException(status_code=400, detail="No trend Excel URL for this video")

    # Step 2: Get all phases
    phases_result = await db.execute(text("""
        SELECT phase_index, time_start, time_end
        FROM video_phases
        WHERE video_id = :vid
        ORDER BY phase_index ASC
    """), {"vid": video_id})
    phases = [{"phase_index": r[0], "time_start": float(r[1] or 0), "time_end": float(r[2] or 0)}
              for r in phases_result.fetchall()]

    if not phases:
        raise HTTPException(status_code=400, detail="No phases found for this video")

    # Step 3: Download and parse trend Excel
    async def _parse_excel(blob_url: str) -> list:
        sas_url = generate_read_sas_from_url(blob_url, expires_hours=1)
        if not sas_url:
            return []
        import openpyxl
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(sas_url)
            if resp.status_code != 200:
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
                                    elif hasattr(val, 'hour') and hasattr(val, 'minute'):
                                        # datetime.time object → format as HH:MM
                                        item[headers[i]] = f"{val.hour:02d}:{val.minute:02d}"
                                    else:
                                        item[headers[i]] = str(val)
                            items.append(item)
                wb.close()
                return items
            finally:
                os.unlink(tmp_path)

    trends = await _parse_excel(trend_blob_url)
    if not trends:
        raise HTTPException(status_code=400, detail="Failed to parse trend Excel or no data")

    # Step 4: Detect column keys
    sample = trends[0]
    logs = [f"Trend entries: {len(trends)}", f"Phase count: {len(phases)}"]
    logs.append(f"Column headers: {list(sample.keys())}")

    # KPI aliases for column detection
    KPI_ALIASES = {
        "time": ["時間", "time", "timestamp", "时间", "시간"],
        "gmv": ["GMV", "gmv", "売上", "sales", "revenue", "成交金额", "매출"],
        "order_count": ["注文", "order", "orders", "SKU注文数", "订单", "주문"],
        "viewer_count": ["視聴者", "viewer", "viewers", "视聴者", "观众", "시청자"],
        "like_count": ["いいね", "like", "likes", "点赞", "좋아요"],
        "comment_count": ["コメント", "comment", "comments", "评论", "댓글"],
        "share_count": ["シェア", "share", "shares", "分享", "공유"],
        "new_followers": ["フォロワー", "follower", "followers", "新規フォロワー", "粉丝"],
        "product_clicks": ["商品クリック", "product_click", "clicks", "点击"],
        "ctor": ["CTOR", "ctor", "conversion"],
        "gpm": ["GPM", "gpm", "視聴GPM"],
    }

    def _find_key(sample_dict, aliases):
        for alias in aliases:
            for k in sample_dict.keys():
                if alias.lower() in k.lower():
                    return k
        return None

    def _safe_float(val):
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    def _parse_time_to_seconds(val):
        if val is None:
            return None
        if hasattr(val, 'hour') and hasattr(val, 'minute'):
            return val.hour * 3600 + val.minute * 60 + getattr(val, 'second', 0)
        val_str = str(val).strip()
        try:
            return float(val_str)
        except (ValueError, TypeError):
            pass
        parts = val_str.split(":")
        try:
            if len(parts) == 2:
                h, m = int(parts[0]), int(parts[1])
                if h < 24:
                    return h * 3600 + m * 60
                else:
                    return h * 60 + m
            elif len(parts) == 3:
                h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
                return h * 3600 + m * 60 + s
        except (ValueError, TypeError):
            pass
        return None

    time_key = _find_key(sample, KPI_ALIASES["time"])
    gmv_key = _find_key(sample, KPI_ALIASES["gmv"])
    order_key = _find_key(sample, KPI_ALIASES["order_count"])
    viewer_key = _find_key(sample, KPI_ALIASES["viewer_count"])
    like_key = _find_key(sample, KPI_ALIASES["like_count"])
    comment_key = _find_key(sample, KPI_ALIASES["comment_count"])
    share_key = _find_key(sample, KPI_ALIASES["share_count"])
    follower_key = _find_key(sample, KPI_ALIASES["new_followers"])
    click_key = _find_key(sample, KPI_ALIASES["product_clicks"])
    conv_key = _find_key(sample, KPI_ALIASES["ctor"])
    gpm_key = _find_key(sample, KPI_ALIASES["gpm"])

    logs.append(f"Detected keys: time={time_key}, gmv={gmv_key}, order={order_key}, "
                f"viewer={viewer_key}, like={like_key}, comment={comment_key}, "
                f"share={share_key}, follower={follower_key}, click={click_key}, "
                f"conv={conv_key}, gpm={gpm_key}")

    if not time_key:
        raise HTTPException(status_code=400, detail=f"Cannot detect time column. Headers: {list(sample.keys())}")

    # Step 5: Build timed entries
    timed_entries = []
    for entry in trends:
        t_sec = _parse_time_to_seconds(entry.get(time_key))
        if t_sec is not None:
            timed_entries.append({"time_sec": t_sec, "entry": entry})
    timed_entries.sort(key=lambda x: x["time_sec"])

    if not timed_entries:
        raise HTTPException(status_code=400, detail="No valid time entries found in trend data")

    csv_first_sec = timed_entries[0]["time_sec"]
    logs.append(f"Timed entries: {len(timed_entries)}")
    logs.append(f"CSV first sec: {csv_first_sec}, time_offset: {time_offset_seconds}")
    logs.append(f"CSV times: {[te['time_sec'] for te in timed_entries]}")

    # Step 6: Build CSV slots
    csv_slots = []
    for i, te in enumerate(timed_entries):
        slot_start = te["time_sec"]
        if i + 1 < len(timed_entries):
            slot_end = timed_entries[i + 1]["time_sec"]
        else:
            video_end_abs = csv_first_sec + time_offset_seconds + phases[-1]["time_end"]
            slot_end = max(slot_start + 1800, video_end_abs)
        csv_slots.append({"start": slot_start, "end": slot_end, "entry": te["entry"]})

    slot_strs = [f"{s['start']:.0f}-{s['end']:.0f}" for s in csv_slots]
    logs.append(f"CSV slots: {slot_strs}")

    # Step 7: Calculate metrics for each phase
    updates = []
    for p in phases:
        start_sec = p["time_start"]
        end_sec = p["time_end"]

        phase_abs_start = csv_first_sec + time_offset_seconds + start_sec
        phase_abs_end = csv_first_sec + time_offset_seconds + end_sec

        phase_gmv = 0.0
        phase_orders = 0.0
        phase_viewers = 0
        phase_likes = 0
        phase_comments = 0.0
        phase_shares = 0.0
        phase_followers = 0.0
        phase_clicks = 0.0
        phase_conv = 0.0
        phase_gpm = 0.0
        match_count = 0

        for slot in csv_slots:
            overlap_start = max(phase_abs_start, slot["start"])
            overlap_end = min(phase_abs_end, slot["end"])
            overlap = max(0, overlap_end - overlap_start)
            if overlap <= 0:
                continue

            slot_dur = max(slot["end"] - slot["start"], 1)
            ratio = overlap / slot_dur
            e = slot["entry"]
            match_count += 1

            if gmv_key:
                phase_gmv += (_safe_float(e.get(gmv_key)) or 0) * ratio
            if order_key:
                phase_orders += (_safe_float(e.get(order_key)) or 0) * ratio
            if comment_key:
                phase_comments += (_safe_float(e.get(comment_key)) or 0) * ratio
            if share_key:
                phase_shares += (_safe_float(e.get(share_key)) or 0) * ratio
            if follower_key:
                phase_followers += (_safe_float(e.get(follower_key)) or 0) * ratio
            if click_key:
                phase_clicks += (_safe_float(e.get(click_key)) or 0) * ratio

            if viewer_key:
                phase_viewers = max(phase_viewers, int(_safe_float(e.get(viewer_key)) or 0))
            if like_key:
                phase_likes = max(phase_likes, int(_safe_float(e.get(like_key)) or 0))
            if conv_key:
                phase_conv = max(phase_conv, _safe_float(e.get(conv_key)) or 0)
            if gpm_key:
                phase_gpm = max(phase_gpm, _safe_float(e.get(gpm_key)) or 0)

        phase_orders = int(round(phase_orders))
        phase_comments = int(round(phase_comments))
        phase_shares = int(round(phase_shares))
        phase_followers = int(round(phase_followers))
        phase_clicks = int(round(phase_clicks))

        updates.append({
            "phase_index": p["phase_index"],
            "gmv": round(phase_gmv, 2),
            "order_count": phase_orders,
            "viewer_count": phase_viewers,
            "like_count": phase_likes,
            "comment_count": phase_comments,
            "share_count": phase_shares,
            "new_followers": phase_followers,
            "product_clicks": phase_clicks,
            "conversion_rate": round(phase_conv, 6),
            "gpm": round(phase_gpm, 2),
            "match_count": match_count,
        })

    # Log sample
    if updates:
        logs.append(f"Phase 1: gmv={updates[0]['gmv']}, viewers={updates[0]['viewer_count']}, matches={updates[0]['match_count']}")
        mid = len(updates) // 2
        logs.append(f"Phase {updates[mid]['phase_index']}: gmv={updates[mid]['gmv']}, viewers={updates[mid]['viewer_count']}, matches={updates[mid]['match_count']}")
        logs.append(f"Phase {updates[-1]['phase_index']}: gmv={updates[-1]['gmv']}, viewers={updates[-1]['viewer_count']}, matches={updates[-1]['match_count']}")

    phases_with_data = sum(1 for u in updates if u["gmv"] > 0 or u["viewer_count"] > 0)
    logs.append(f"Phases with data: {phases_with_data}/{len(updates)}")

    # Step 8: Update DB
    updated_count = 0
    for u in updates:
        try:
            await db.execute(text("""
                UPDATE video_phases
                SET gmv = :gmv, order_count = :order_count,
                    viewer_count = :viewer_count, like_count = :like_count,
                    comment_count = :comment_count, share_count = :share_count,
                    new_followers = :new_followers, product_clicks = :product_clicks,
                    conversion_rate = :conversion_rate, gpm = :gpm,
                    importance_score = :match_count,
                    updated_at = now()
                WHERE video_id = :video_id AND phase_index = :phase_index
            """), {
                "video_id": video_id,
                "phase_index": u["phase_index"],
                "gmv": u["gmv"],
                "order_count": u["order_count"],
                "viewer_count": u["viewer_count"],
                "like_count": u["like_count"],
                "comment_count": u["comment_count"],
                "share_count": u["share_count"],
                "new_followers": u["new_followers"],
                "product_clicks": u["product_clicks"],
                "conversion_rate": u["conversion_rate"],
                "gpm": u["gpm"],
                "match_count": u["match_count"],
            })
            updated_count += 1
        except Exception as e:
            logs.append(f"ERROR updating phase {u['phase_index']}: {e}")

    await db.commit()
    logs.append(f"Updated {updated_count}/{len(updates)} phases in DB")

    return {
        "status": "success",
        "video_id": video_id,
        "phases_total": len(phases),
        "phases_with_data": phases_with_data,
        "phases_updated": updated_count,
        "logs": logs,
        "sample_metrics": updates[:3] if updates else [],
    }


# ─── Force Video Status Update ───

@router.post("/force-status/{video_id}")
async def force_video_status(
    video_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None),
):
    """
    動画のステータスを強制的に変更する。
    payload: {"status": "DONE"}
    """
    if x_admin_key != f"{ADMIN_ID}:{ADMIN_PASS}":
        raise HTTPException(status_code=403, detail="Forbidden")

    new_status = payload.get("status")
    if not new_status:
        raise HTTPException(status_code=400, detail="status is required")

    await db.execute(
        text("UPDATE videos SET status = :status, step_progress = 0 WHERE id = :vid"),
        {"status": new_status, "vid": video_id},
    )
    await db.commit()

    return {"status": "ok", "video_id": video_id, "new_status": new_status}



# ═══════════════════════════════════════════════════════════════════════
# SYSTEM ERROR LOGS (video_error_logs) – 管理画面表示用
# ═══════════════════════════════════════════════════════════════════════

@router.get("/system-error-logs")
async def get_system_error_logs(
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None),
    video_id: Optional[str] = None,
    error_code: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """video_error_logs の一覧を返す（管理画面 Diagnostics 用）"""
    if x_admin_key != f"{ADMIN_ID}:{ADMIN_PASS}":
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        # Ensure table exists
        await db.execute(text("""
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

        conditions = []
        params = {"limit": limit, "offset": offset}
        if video_id:
            conditions.append("CAST(vel.video_id AS TEXT) LIKE :video_id")
            params["video_id"] = f"%{video_id}%"
        if error_code:
            conditions.append("vel.error_code ILIKE :error_code")
            params["error_code"] = f"%{error_code}%"
        if source:
            conditions.append("vel.source = :source")
            params["source"] = source

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        count_result = await db.execute(
            text(f"SELECT COUNT(*) FROM video_error_logs vel {where}"),
            params,
        )
        total = count_result.scalar() or 0

        result = await db.execute(
            text(f"""
                SELECT vel.id, vel.video_id, vel.error_code, vel.error_step,
                       vel.error_message, vel.source, vel.created_at,
                       v.original_filename
                FROM video_error_logs vel
                LEFT JOIN videos v ON vel.video_id = v.id
                {where}
                ORDER BY vel.created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            params,
        )
        rows = result.fetchall()
        errors = []
        for r in rows:
            errors.append({
                "id": r.id,
                "video_id": str(r.video_id),
                "filename": r.original_filename or "",
                "error_code": r.error_code,
                "error_step": r.error_step,
                "error_message": r.error_message,
                "source": r.source,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })

        return {"total": total, "errors": errors}
    except Exception as e:
        logger.exception(f"Failed to fetch system error logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/system-error-logs/summary")
async def get_system_error_logs_summary(
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None),
    hours: int = 24,
):
    """直近N時間のエラーサマリー"""
    if x_admin_key != f"{ADMIN_ID}:{ADMIN_PASS}":
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        cutoff = f"NOW() - INTERVAL '{hours} hours'"

        total_result = await db.execute(
            text(f"SELECT COUNT(*) FROM video_error_logs WHERE created_at >= {cutoff}")
        )
        total = total_result.scalar() or 0

        by_code_result = await db.execute(
            text(f"""
                SELECT error_code, COUNT(*) as cnt
                FROM video_error_logs WHERE created_at >= {cutoff}
                GROUP BY error_code ORDER BY cnt DESC LIMIT 20
            """)
        )
        by_code = {r.error_code: r.cnt for r in by_code_result.fetchall()}

        by_step_result = await db.execute(
            text(f"""
                SELECT error_step, COUNT(*) as cnt
                FROM video_error_logs WHERE created_at >= {cutoff}
                GROUP BY error_step ORDER BY cnt DESC LIMIT 20
            """)
        )
        by_step = {(r.error_step or "unknown"): r.cnt for r in by_step_result.fetchall()}

        by_source_result = await db.execute(
            text(f"""
                SELECT source, COUNT(*) as cnt
                FROM video_error_logs WHERE created_at >= {cutoff}
                GROUP BY source ORDER BY cnt DESC
            """)
        )
        by_source = {(r.source or "unknown"): r.cnt for r in by_source_result.fetchall()}

        return {
            "total_errors": total,
            "period_hours": hours,
            "by_error_code": by_code,
            "by_step": by_step,
            "by_source": by_source,
        }
    except Exception as e:
        logger.exception(f"Failed to fetch error summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════
# BUG REPORTS – 問題→原因→解決策の記録
# ═══════════════════════════════════════════════════════════════════════

@router.get("/bug-reports")
async def list_bug_reports(
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None),
    status: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """バグレポート一覧"""
    if x_admin_key != f"{ADMIN_ID}:{ADMIN_PASS}":
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        conditions = []
        params = {"limit": limit, "offset": offset}
        if status:
            conditions.append("status = :status")
            params["status"] = status
        if severity:
            conditions.append("severity = :severity")
            params["severity"] = severity

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        count_result = await db.execute(
            text(f"SELECT COUNT(*) FROM bug_reports {where}"), params
        )
        total = count_result.scalar() or 0

        result = await db.execute(
            text(f"""
                SELECT * FROM bug_reports {where}
                ORDER BY
                    CASE status WHEN 'open' THEN 0 WHEN 'investigating' THEN 1
                                WHEN 'resolved' THEN 2 ELSE 3 END,
                    created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            params,
        )
        rows = result.fetchall()
        reports = [dict(r._mapping) for r in rows]
        # Serialize datetimes
        for r in reports:
            for k in ("created_at", "updated_at", "resolved_at"):
                if r.get(k):
                    r[k] = r[k].isoformat()

        return {"total": total, "reports": reports}
    except Exception as e:
        logger.exception(f"Failed to fetch bug reports: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/bug-reports")
async def create_bug_report(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None),
):
    """バグレポート作成"""
    if x_admin_key != f"{ADMIN_ID}:{ADMIN_PASS}":
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        result = await db.execute(
            text("""
                INSERT INTO bug_reports
                    (title, severity, status, category, symptom, root_cause,
                     solution, affected_files, related_video_ids, reported_by)
                VALUES
                    (:title, :severity, :status, :category, :symptom, :root_cause,
                     :solution, :affected_files, :related_video_ids, :reported_by)
                RETURNING id
            """),
            {
                "title": payload.get("title", "Untitled"),
                "severity": payload.get("severity", "medium"),
                "status": payload.get("status", "open"),
                "category": payload.get("category", "general"),
                "symptom": payload.get("symptom", ""),
                "root_cause": payload.get("root_cause", ""),
                "solution": payload.get("solution", ""),
                "affected_files": payload.get("affected_files", ""),
                "related_video_ids": payload.get("related_video_ids", ""),
                "reported_by": payload.get("reported_by", "system"),
            },
        )
        bug_id = result.scalar()
        await db.commit()
        return {"status": "ok", "id": bug_id}
    except Exception as e:
        await db.rollback()
        logger.exception(f"Failed to create bug report: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/bug-reports/{bug_id}")
async def update_bug_report(
    bug_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None),
):
    """バグレポート更新（解決策の追記など）"""
    if x_admin_key != f"{ADMIN_ID}:{ADMIN_PASS}":
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        set_clauses = []
        params = {"bug_id": bug_id}

        allowed_fields = [
            "title", "severity", "status", "category", "symptom",
            "root_cause", "solution", "affected_files", "related_video_ids",
            "resolved_by",
        ]
        for field in allowed_fields:
            if field in payload:
                set_clauses.append(f"{field} = :{field}")
                params[field] = payload[field]

        if not set_clauses:
            raise HTTPException(status_code=400, detail="No fields to update")

        # Auto-set resolved_at when status changes to resolved
        if payload.get("status") == "resolved":
            set_clauses.append("resolved_at = NOW()")

        set_clauses.append("updated_at = NOW()")

        await db.execute(
            text(f"UPDATE bug_reports SET {', '.join(set_clauses)} WHERE id = :bug_id"),
            params,
        )
        await db.commit()
        return {"status": "ok", "id": bug_id}
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.exception(f"Failed to update bug report: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════
# WORK LOGS – デプロイ・修正・作業の履歴
# ═══════════════════════════════════════════════════════════════════════

@router.get("/work-logs")
async def list_work_logs(
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None),
    action: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """作業ログ一覧"""
    if x_admin_key != f"{ADMIN_ID}:{ADMIN_PASS}":
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        conditions = []
        params = {"limit": limit, "offset": offset}
        if action:
            conditions.append("action = :action")
            params["action"] = action

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        count_result = await db.execute(
            text(f"SELECT COUNT(*) FROM work_logs {where}"), params
        )
        total = count_result.scalar() or 0

        result = await db.execute(
            text(f"""
                SELECT * FROM work_logs {where}
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            params,
        )
        rows = result.fetchall()
        logs = [dict(r._mapping) for r in rows]
        for l in logs:
            if l.get("created_at"):
                l["created_at"] = l["created_at"].isoformat()

        return {"total": total, "logs": logs}
    except Exception as e:
        logger.exception(f"Failed to fetch work logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/work-logs")
async def create_work_log(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None),
):
    """作業ログ作成"""
    if x_admin_key != f"{ADMIN_ID}:{ADMIN_PASS}":
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        result = await db.execute(
            text("""
                INSERT INTO work_logs
                    (action, summary, details, files_changed,
                     commit_hash, deployed_to, author, related_bug_id)
                VALUES
                    (:action, :summary, :details, :files_changed,
                     :commit_hash, :deployed_to, :author, :related_bug_id)
                RETURNING id
            """),
            {
                "action": payload.get("action", "other"),
                "summary": payload.get("summary", ""),
                "details": payload.get("details", ""),
                "files_changed": payload.get("files_changed", ""),
                "commit_hash": payload.get("commit_hash", ""),
                "deployed_to": payload.get("deployed_to", ""),
                "author": payload.get("author", "manus-ai"),
                "related_bug_id": payload.get("related_bug_id"),
            },
        )
        log_id = result.scalar()
        await db.commit()
        return {"status": "ok", "id": log_id}
    except Exception as e:
        await db.rollback()
        logger.exception(f"Failed to create work log: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════════════
# AI CONTEXT ENDPOINT – AI読み取り用の構造化サマリー
# ═══════════════════════════════════════════════════════════════════════

@router.get("/ai-context")
async def get_ai_context(
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None),
    scope: Optional[str] = None,
):
    """
    AI（Manus）が毎回タスク開始時に読む唯一のエンドポイント。
    プロジェクトの永続記憶 + リアルタイム状態を2層構造で返す。

    クエリパラメータ:
      - scope: フィルタリング用。"aitherhub" or "liveboost" を指定すると
               そのプロジェクトに関連する教訓のみ返す。省略時は全て返す。

    第1層（常に返す・軽量）:
      - dangers: 絶対にやってはいけないこと
      - checklist_by_file: ファイル別の変更時チェックリスト
      - checklist_by_feature: 機能別の変更時チェックリスト
      - dependencies: ファイル間の依存マップ
      - rules: システムの正常状態の定義
      - preferences: ユーザーの方針
      - feature_status: 機能の現在の状態
      - open_bugs: 未解決のバグ
      - recent_errors: 直近24hのエラーサマリー
      - recent_work: 直近の作業ログ
      - error_videos: ERRORステータスの動画
      - stuck_videos: 停滞中の動画
      - action_required: 次のManusがやるべきこと
    """
    if x_admin_key != f"{ADMIN_ID}:{ADMIN_PASS}":
        raise HTTPException(status_code=403, detail="Forbidden")

    context = {}
    if scope:
        context["scope"] = scope

    # ━━ プロジェクトの永続記憶（lessons_learned）━━
    try:
        # scopeフィルタ: related_featureでフィルタリング
        # - scope=liveboost → related_feature='liveboost' のみ + 共通(NULL/空)
        # - scope=aitherhub → related_feature!='liveboost' (既存の日本語feature名も含む)
        # - scope省略 → 全件
        scope_filter = ""
        scope_params = {}
        if scope == "liveboost":
            scope_filter = "AND (related_feature = 'liveboost' OR related_feature IS NULL OR related_feature = '')"
        elif scope == "aitherhub":
            scope_filter = "AND (related_feature != 'liveboost' OR related_feature IS NULL OR related_feature = '')"

        result = await db.execute(text(f"""
            SELECT id, category, title, content, related_files, related_feature
            FROM lessons_learned
            WHERE is_active = TRUE {scope_filter}
            ORDER BY
                CASE category
                    WHEN 'danger' THEN 0 WHEN 'checklist' THEN 1
                    WHEN 'rule' THEN 2 WHEN 'dependency' THEN 3
                    WHEN 'status' THEN 4 WHEN 'preference' THEN 5
                    ELSE 6 END,
                created_at DESC
        """), scope_params)
        rows = result.fetchall()

        # danger: 絶対にやってはいけないこと
        context["dangers"] = [
            r.title for r in rows if r.category == "danger"
        ]

        # checklist: ファイル別・機能別の変更時チェック
        checklist_by_file = {}
        checklist_by_feature = {}
        for r in rows:
            if r.category != "checklist":
                continue
            # ファイル別
            if r.related_files:
                for f in r.related_files.split(","):
                    f = f.strip()
                    if f:
                        checklist_by_file.setdefault(f, []).append(r.title)
            # 機能別
            if r.related_feature:
                checklist_by_feature.setdefault(r.related_feature.strip(), []).append(r.title)
        context["checklist_by_file"] = checklist_by_file
        context["checklist_by_feature"] = checklist_by_feature

        # dependency: ファイル間の依存マップ
        dep_map = {}
        for r in rows:
            if r.category != "dependency":
                continue
            # title = 起点ファイル, content = 依存先（カンマ区切り）
            if r.title and r.content:
                dep_map[r.title] = [x.strip() for x in r.content.split(",") if x.strip()]
        context["dependencies"] = dep_map

        # rule: システムの正常状態の定義
        context["rules"] = [
            {"title": r.title, "detail": r.content[:200]} for r in rows if r.category == "rule"
        ]

        # preference: ユーザーの方針
        context["preferences"] = [
            r.title for r in rows if r.category == "preference"
        ]

        # status: 機能の現在の状態
        context["feature_status"] = [
            {"feature": r.related_feature or r.title, "status": r.content[:100]}
            for r in rows if r.category == "status"
        ]

        # lesson: 過去の失敗パターン（タイトルのみ、詳細は第2層）
        context["lessons"] = [
            {"id": r.id, "title": r.title}
            for r in rows if r.category == "lesson"
        ]

    except Exception as e:
        context["lessons_error"] = f"error: {e}"

    # ━━ リアルタイム状態 ━━

    # 1. Open bugs
    try:
        result = await db.execute(text("""
            SELECT id, title, severity, category, symptom, root_cause, status, created_at
            FROM bug_reports
            WHERE status IN ('open', 'investigating')
            ORDER BY
                CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                              WHEN 'medium' THEN 2 ELSE 3 END,
                created_at DESC
            LIMIT 20
        """))
        rows = result.fetchall()
        context["open_bugs"] = [
            {
                "id": r.id, "title": r.title, "severity": r.severity,
                "category": r.category, "symptom": (r.symptom or "")[:200],
                "root_cause": (r.root_cause or "")[:200], "status": r.status,
            }
            for r in rows
        ]
    except Exception as e:
        context["open_bugs"] = f"error: {e}"

    # 2. Recent errors (24h summary)
    try:
        total_result = await db.execute(text(
            "SELECT COUNT(*) FROM video_error_logs WHERE created_at >= NOW() - INTERVAL '24 hours'"
        ))
        total = total_result.scalar() or 0

        top_codes = await db.execute(text("""
            SELECT error_code, COUNT(*) as cnt
            FROM video_error_logs WHERE created_at >= NOW() - INTERVAL '24 hours'
            GROUP BY error_code ORDER BY cnt DESC LIMIT 10
        """))
        context["recent_errors"] = {
            "total_24h": total,
            "top_codes": {r.error_code: r.cnt for r in top_codes.fetchall()},
        }
    except Exception as e:
        context["recent_errors"] = f"error: {e}"

    # 3. Recent work logs
    try:
        result = await db.execute(text("""
            SELECT id, action, summary, commit_hash, created_at
            FROM work_logs ORDER BY created_at DESC LIMIT 10
        """))
        rows = result.fetchall()
        context["recent_work"] = [
            {
                "id": r.id, "action": r.action,
                "summary": (r.summary or "")[:200],
                "commit": r.commit_hash or "",
                "at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in rows
        ]
    except Exception as e:
        context["recent_work"] = f"error: {e}"

    # 4. Error videos
    try:
        result = await db.execute(text("""
            SELECT id, original_filename, status, last_error_code, last_error_message, updated_at
            FROM videos
            WHERE status IN ('ERROR', 'error')
            ORDER BY updated_at DESC
            LIMIT 20
        """))
        rows = result.fetchall()
        context["error_videos"] = [
            {
                "id": str(r.id),
                "file": r.original_filename or "",
                "error": r.last_error_code or "",
                "msg": (r.last_error_message or "")[:200],
            }
            for r in rows
        ]
    except Exception as e:
        context["error_videos"] = f"error: {e}"

    # 5. Stuck videos (uploaded/processing for > 30 min)
    #    Also detect videos stuck in STEP_* for > 6h (stalled pipeline)
    try:
        result = await db.execute(text("""
            SELECT id, original_filename, status, updated_at, created_at,
                   EXTRACT(EPOCH FROM (NOW() - updated_at)) / 3600.0 AS stall_hours
            FROM videos
            WHERE status NOT IN ('DONE', 'COMPLETED', 'ERROR', 'error', 'deleted')
              AND updated_at < NOW() - INTERVAL '30 minutes'
            ORDER BY updated_at ASC
            LIMIT 20
        """))
        rows = result.fetchall()
        context["stuck_videos"] = [
            {
                "id": str(r.id),
                "file": r.original_filename or "",
                "status": r.status or "",
                "since": r.updated_at.isoformat() if r.updated_at else "",
                "stall_hours": round(float(r.stall_hours), 1) if r.stall_hours else 0,
            }
            for r in rows
        ]
    except Exception as e:
        context["stuck_videos"] = f"error: {e}"

    # ━━ action_required: 次のManusがやるべきこと ━━
    action_required = []
    try:
        # 教訓登録忘れ検知: 直近のwork-logの後にlessonsが登録されているか
        result = await db.execute(text("""
            SELECT w.id, w.action, w.summary, w.created_at,
                   (SELECT COUNT(*) FROM lessons_learned l
                    WHERE l.created_at > w.created_at
                      AND l.created_at < w.created_at + INTERVAL '24 hours') as lessons_after
            FROM work_logs w
            ORDER BY w.created_at DESC
            LIMIT 3
        """))
        recent_logs = result.fetchall()
        for log in recent_logs:
            if log.lessons_after == 0 and log.action not in ('read', 'review', 'check'):
                action_required.append(
                    f"作業 '{log.summary[:80]}' (ID:{log.id}) の後に教訓が登録されていません。"
                    f"バグ修正・機能追加をした場合はlessonsに登録してください。"
                )
    except Exception:
        pass  # action_requiredは必須ではないのでエラーは無視

    # 未解決バグがあれば警告
    if context.get("open_bugs") and isinstance(context["open_bugs"], list) and len(context["open_bugs"]) > 0:
        critical = [b for b in context["open_bugs"] if b.get("severity") in ("critical", "high")]
        if critical:
            action_required.append(
                f"重要度の高い未解決バグが{len(critical)}件あります。作業前に確認してください。"
            )

    # stuck_videosがあれば警告
    if context.get("stuck_videos") and isinstance(context["stuck_videos"], list) and len(context["stuck_videos"]) > 0:
        stalled_critical = [v for v in context["stuck_videos"]
                            if isinstance(v, dict) and v.get("stall_hours", 0) >= 6]
        if stalled_critical:
            action_required.append(
                f"緊急: {len(stalled_critical)}件の動画が6時間以上停止しています。"
                f" admin retry-video APIで再投入してください。"
            )
        else:
            action_required.append(
                f"停滞中の動画が{len(context['stuck_videos'])}件あります。確認が必要かもしれません。"
            )

    context["action_required"] = action_required

    return context


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Lessons Learned — プロジェクトの永続記憶
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/lessons")
async def list_lessons(
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None),
    category: Optional[str] = None,
    is_active: Optional[bool] = True,
    related_files: Optional[str] = None,
    related_feature: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    """教訓一覧（カテゴリ・ファイル・機能でフィルタ可能）"""
    if x_admin_key != f"{ADMIN_ID}:{ADMIN_PASS}":
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        conditions = []
        params = {"limit": limit, "offset": offset}

        if is_active is not None:
            conditions.append("is_active = :is_active")
            params["is_active"] = is_active
        if category:
            conditions.append("category = :category")
            params["category"] = category
        if related_files:
            conditions.append("related_files ILIKE :related_files")
            params["related_files"] = f"%{related_files}%"
        if related_feature:
            conditions.append("related_feature ILIKE :related_feature")
            params["related_feature"] = f"%{related_feature}%"

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        count_result = await db.execute(
            text(f"SELECT COUNT(*) FROM lessons_learned {where}"), params
        )
        total = count_result.scalar() or 0

        result = await db.execute(
            text(f"""
                SELECT * FROM lessons_learned {where}
                ORDER BY
                    CASE category
                        WHEN 'danger' THEN 0 WHEN 'checklist' THEN 1
                        WHEN 'rule' THEN 2 WHEN 'dependency' THEN 3
                        WHEN 'lesson' THEN 4 WHEN 'status' THEN 5
                        ELSE 6 END,
                    created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            params,
        )
        rows = result.fetchall()
        lessons = [dict(r._mapping) for r in rows]
        for l in lessons:
            for k in ("created_at", "updated_at"):
                if l.get(k):
                    l[k] = l[k].isoformat()

        return {"total": total, "lessons": lessons}
    except Exception as e:
        logger.exception(f"Failed to fetch lessons: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/lessons")
async def create_lesson(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None),
):
    """教訓を新規作成"""
    if x_admin_key != f"{ADMIN_ID}:{ADMIN_PASS}":
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        result = await db.execute(
            text("""
                INSERT INTO lessons_learned
                    (category, title, content, related_files, related_feature, source_bug_id)
                VALUES
                    (:category, :title, :content, :related_files, :related_feature, :source_bug_id)
                RETURNING id
            """),
            {
                "category": payload.get("category", "lesson"),
                "title": payload.get("title", ""),
                "content": payload.get("content", ""),
                "related_files": payload.get("related_files", ""),
                "related_feature": payload.get("related_feature", ""),
                "source_bug_id": payload.get("source_bug_id"),
            },
        )
        new_id = result.scalar()
        await db.commit()
        return {"status": "ok", "id": new_id}
    except Exception as e:
        await db.rollback()
        logger.exception(f"Failed to create lesson: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/lessons/{lesson_id}")
async def update_lesson(
    lesson_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None),
):
    """教訓を更新"""
    if x_admin_key != f"{ADMIN_ID}:{ADMIN_PASS}":
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        set_clauses = []
        params = {"id": lesson_id}
        for field in ("category", "title", "content", "related_files", "related_feature", "is_active", "source_bug_id"):
            if field in payload:
                set_clauses.append(f"{field} = :{field}")
                params[field] = payload[field]

        if not set_clauses:
            raise HTTPException(status_code=400, detail="No fields to update")

        set_clauses.append("updated_at = NOW()")
        set_sql = ", ".join(set_clauses)

        await db.execute(
            text(f"UPDATE lessons_learned SET {set_sql} WHERE id = :id"),
            params,
        )
        await db.commit()
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.exception(f"Failed to update lesson: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/lessons/{lesson_id}")
async def deactivate_lesson(
    lesson_id: int,
    db: AsyncSession = Depends(get_db),
    x_admin_key: Optional[str] = Header(None),
):
    """教訓を無効化（DBからは削除しない）"""
    if x_admin_key != f"{ADMIN_ID}:{ADMIN_PASS}":
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        await db.execute(
            text("UPDATE lessons_learned SET is_active = FALSE, updated_at = NOW() WHERE id = :id"),
            {"id": lesson_id},
        )
        await db.commit()
        return {"status": "ok"}
    except Exception as e:
        await db.rollback()
        logger.exception(f"Failed to deactivate lesson: {e}")
        raise HTTPException(status_code=500, detail=str(e))
