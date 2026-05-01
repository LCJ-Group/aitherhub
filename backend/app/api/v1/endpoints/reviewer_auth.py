"""
Reviewer Authentication & Session Management API
=================================================
Provides login/logout, session tracking, and heartbeat for clip reviewers.
Reviewers are users with role='reviewer' in the existing users table.
"""
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.utils.jwt import create_access_token, create_refresh_token, decode_token
from app.utils.password import verify_password, hash_password

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Admin key for reviewer management ──
import os
ADMIN_ID = os.getenv("ADMIN_ID", "aither")
ADMIN_PASS = os.getenv("ADMIN_PASS", "hub")


# ── Pydantic models ──
class ReviewerLoginRequest(BaseModel):
    email: str
    password: str


class ReviewerCreateRequest(BaseModel):
    email: str
    password: str
    display_name: str


class ReviewerUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None


# ── Helper: get current reviewer from JWT ──
async def get_current_reviewer(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Extract reviewer from JWT Bearer token. Raises 401 if invalid."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="認証が必要です")
    token = authorization.split(" ", 1)[1]
    try:
        payload = decode_token(token)
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="無効なトークン")
    except Exception:
        raise HTTPException(status_code=401, detail="トークンの有効期限が切れています")

    result = await db.execute(
        text("SELECT id, email, display_name, role, is_active FROM users WHERE id = :uid"),
        {"uid": int(user_id)},
    )
    user = result.mappings().fetchone()
    if not user:
        raise HTTPException(status_code=401, detail="ユーザーが見つかりません")
    if user["role"] not in ("reviewer", "admin"):
        raise HTTPException(status_code=403, detail="採点者権限がありません")
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="アカウントが無効化されています")
    return dict(user)


# ══════════════════════════════════════════════════════════════
# Reviewer Login / Logout / Me / Heartbeat
# ══════════════════════════════════════════════════════════════

@router.post("/reviewer/login")
async def reviewer_login(req: ReviewerLoginRequest, db: AsyncSession = Depends(get_db)):
    """Authenticate reviewer and start a review session."""
    result = await db.execute(
        text("SELECT id, email, display_name, role, is_active, hashed_password FROM users WHERE email = :email"),
        {"email": req.email},
    )
    user = result.mappings().fetchone()
    if not user:
        raise HTTPException(status_code=401, detail="メールアドレスまたはパスワードが正しくありません")
    if user["role"] not in ("reviewer", "admin"):
        raise HTTPException(status_code=403, detail="採点者アカウントではありません")
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="アカウントが無効化されています")
    if not user["hashed_password"] or not verify_password(req.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="メールアドレスまたはパスワードが正しくありません")

    # Create JWT tokens
    access_token = create_access_token(str(user["id"]))
    refresh_token = create_refresh_token(str(user["id"]))

    # Start a new review session
    session_id = str(uuid.uuid4())
    await db.execute(text("""
        INSERT INTO review_sessions (id, reviewer_id, started_at)
        VALUES (:sid, :rid, NOW())
    """), {"sid": session_id, "rid": user["id"]})
    await db.commit()

    logger.info(f"[Reviewer] Login: {user['email']} (id={user['id']}), session={session_id}")
    return {
        "success": True,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "session_id": session_id,
        "reviewer": {
            "id": user["id"],
            "email": user["email"],
            "display_name": user["display_name"],
            "role": user["role"],
        },
    }


@router.post("/reviewer/logout")
async def reviewer_logout(
    reviewer: dict = Depends(get_current_reviewer),
    db: AsyncSession = Depends(get_db),
):
    """End the active review session and record stats."""
    # Find the most recent open session for this reviewer
    result = await db.execute(text("""
        SELECT id, started_at FROM review_sessions
        WHERE reviewer_id = :rid AND ended_at IS NULL
        ORDER BY started_at DESC LIMIT 1
    """), {"rid": reviewer["id"]})
    session = result.mappings().fetchone()

    if session:
        # Count clips reviewed during this session
        clips_count = 0
        count_result = await db.execute(text("""
            SELECT COUNT(*) FROM video_phases
            WHERE rated_by_reviewer_id = :rid
              AND rated_at >= :started
              AND rated_at <= NOW()
        """), {"rid": reviewer["id"], "started": session["started_at"]})
        clips_count = count_result.scalar() or 0

        await db.execute(text("""
            UPDATE review_sessions
            SET ended_at = NOW(),
                clips_reviewed = :cnt,
                duration_minutes = EXTRACT(EPOCH FROM (NOW() - started_at)) / 60.0
            WHERE id = :sid
        """), {"sid": session["id"], "cnt": clips_count})
        await db.commit()
        logger.info(f"[Reviewer] Logout: {reviewer['email']}, session={session['id']}, clips={clips_count}")
    else:
        logger.warning(f"[Reviewer] Logout without active session: {reviewer['email']}")

    return {"success": True, "message": "ログアウトしました"}


@router.get("/reviewer/me")
async def reviewer_me(
    reviewer: dict = Depends(get_current_reviewer),
    db: AsyncSession = Depends(get_db),
):
    """Get current reviewer info + active session + today's stats."""
    # Active session
    sess_result = await db.execute(text("""
        SELECT id, started_at FROM review_sessions
        WHERE reviewer_id = :rid AND ended_at IS NULL
        ORDER BY started_at DESC LIMIT 1
    """), {"rid": reviewer["id"]})
    active_session = sess_result.mappings().fetchone()

    # Today's stats
    stats_result = await db.execute(text("""
        SELECT
            COUNT(*) as today_rated,
            COALESCE(AVG(user_rating), 0) as today_avg_rating
        FROM video_phases
        WHERE rated_by_reviewer_id = :rid
          AND rated_at >= CURRENT_DATE
    """), {"rid": reviewer["id"]})
    today_stats = stats_result.mappings().fetchone()

    # All-time stats
    all_result = await db.execute(text("""
        SELECT
            COUNT(*) as total_rated,
            COALESCE(AVG(user_rating), 0) as avg_rating,
            COUNT(CASE WHEN user_rating = 1 THEN 1 END) as r1,
            COUNT(CASE WHEN user_rating = 2 THEN 1 END) as r2,
            COUNT(CASE WHEN user_rating = 3 THEN 1 END) as r3,
            COUNT(CASE WHEN user_rating = 4 THEN 1 END) as r4,
            COUNT(CASE WHEN user_rating = 5 THEN 1 END) as r5
        FROM video_phases
        WHERE rated_by_reviewer_id = :rid
    """), {"rid": reviewer["id"]})
    all_stats = all_result.mappings().fetchone()

    return {
        "reviewer": reviewer,
        "active_session": {
            "id": active_session["id"],
            "started_at": str(active_session["started_at"]),
        } if active_session else None,
        "today": {
            "rated_count": today_stats["today_rated"] if today_stats else 0,
            "avg_rating": round(float(today_stats["today_avg_rating"]), 2) if today_stats else 0,
        },
        "all_time": {
            "total_rated": all_stats["total_rated"] if all_stats else 0,
            "avg_rating": round(float(all_stats["avg_rating"]), 2) if all_stats else 0,
            "distribution": {
                "1": all_stats["r1"] if all_stats else 0,
                "2": all_stats["r2"] if all_stats else 0,
                "3": all_stats["r3"] if all_stats else 0,
                "4": all_stats["r4"] if all_stats else 0,
                "5": all_stats["r5"] if all_stats else 0,
            },
        },
    }


@router.post("/reviewer/heartbeat")
async def reviewer_heartbeat(
    reviewer: dict = Depends(get_current_reviewer),
    db: AsyncSession = Depends(get_db),
):
    """Keep the review session alive. Called periodically from frontend."""
    result = await db.execute(text("""
        UPDATE review_sessions
        SET last_heartbeat = NOW()
        WHERE reviewer_id = :rid AND ended_at IS NULL
        RETURNING id
    """), {"rid": reviewer["id"]})
    await db.commit()
    row = result.fetchone()
    return {"success": True, "session_active": row is not None}


# ══════════════════════════════════════════════════════════════
# Reviewer Rating API (authenticated)
# ══════════════════════════════════════════════════════════════

@router.put("/reviewer/rate/{video_id}/{phase_index}")
async def reviewer_rate_phase(
    video_id: str,
    phase_index: int,
    request_body: dict,
    reviewer: dict = Depends(get_current_reviewer),
    db: AsyncSession = Depends(get_db),
):
    """Rate a phase as an authenticated reviewer. Tracks who rated what."""
    rating = request_body.get("rating")
    comment = request_body.get("comment", "")
    if rating is None or not isinstance(rating, int) or rating < 1 or rating > 5:
        raise HTTPException(status_code=400, detail="ratingは1〜5の整数で指定してください")

    importance_score = (rating - 1) / 4.0

    result = await db.execute(text("""
        UPDATE video_phases
        SET user_rating = :rating,
            user_comment = :comment,
            importance_score = :importance_score,
            rated_at = NOW(),
            rated_by_reviewer_id = :reviewer_id,
            updated_at = NOW()
        WHERE video_id = :video_id AND phase_index = :phase_index
    """), {
        "rating": rating,
        "comment": comment,
        "importance_score": importance_score,
        "reviewer_id": reviewer["id"],
        "video_id": video_id,
        "phase_index": phase_index,
    })
    await db.commit()

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="フェーズが見つかりません")

    logger.info(f"[Reviewer] Rate: reviewer={reviewer['email']}, video={video_id}, phase={phase_index}, rating={rating}")
    return {
        "success": True,
        "video_id": video_id,
        "phase_index": phase_index,
        "rating": rating,
        "comment": comment,
        "reviewer_id": reviewer["id"],
        "reviewer_name": reviewer["display_name"],
    }


# ══════════════════════════════════════════════════════════════
# Reviewer Feedbacks API (paginated list for reviewer UI)
# ══════════════════════════════════════════════════════════════

@router.get("/reviewer/feedbacks")
async def reviewer_get_feedbacks(
    page: int = 1,
    per_page: int = 20,
    filter_rating: Optional[str] = None,
    clip_filter: Optional[str] = None,
    reviewer: dict = Depends(get_current_reviewer),
    db: AsyncSession = Depends(get_db),
):
    """Get paginated feedbacks for the reviewer to rate. Same data as admin feedbacks but reviewer-authenticated."""
    conditions = []
    # Default: show unrated only
    if filter_rating == "all":
        pass
    elif filter_rating and filter_rating.isdigit():
        conditions.append(f"vp.user_rating = {int(filter_rating)}")
    elif filter_rating == "rated":
        conditions.append("vp.user_rating IS NOT NULL")
    elif filter_rating == "mine":
        conditions.append(f"vp.rated_by_reviewer_id = {reviewer['id']}")
    else:
        # Default: unrated
        conditions.append("vp.user_rating IS NULL")

    # Clip filter
    clip_join_sql = ""
    if clip_filter == "yes":
        clip_join_sql = """
            JOIN (
                SELECT DISTINCT ON (video_id, phase_index) video_id, phase_index
                FROM video_clips WHERE clip_url IS NOT NULL
                ORDER BY video_id, phase_index, created_at DESC
            ) vc_filter ON CAST(vp.video_id AS VARCHAR) = CAST(vc_filter.video_id AS VARCHAR)
                AND vp.phase_index::text = vc_filter.phase_index
        """
    elif clip_filter == "no":
        conditions.append("""
            NOT EXISTS (
                SELECT 1 FROM video_clips vc2
                WHERE CAST(vp.video_id AS VARCHAR) = CAST(vc2.video_id AS VARCHAR)
                  AND vp.phase_index::text = vc2.phase_index
                  AND vc2.clip_url IS NOT NULL
            )
        """)

    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

    # Count
    count_sql = text(f"""
        SELECT COUNT(*) FROM video_phases vp
        JOIN videos v ON CAST(vp.video_id AS UUID) = v.id
        {clip_join_sql}
        {where_clause}
    """)
    count_result = await db.execute(count_sql)
    total = count_result.scalar()

    offset = (max(1, page) - 1) * per_page
    sql = text(f"""
        SELECT
            vp.video_id,
            vp.phase_index,
            vp.time_start,
            vp.time_end,
            SUBSTRING(vp.phase_description, 1, 200) as phase_description,
            vp.user_rating,
            vp.user_comment,
            vp.rated_at,
            vp.rated_by_reviewer_id,
            vp.importance_score,
            v.original_filename,
            v.compressed_blob_url,
            vc.clip_url,
            vc.id as clip_id,
            vc.duration_sec as clip_duration_sec
        FROM video_phases vp
        JOIN videos v ON CAST(vp.video_id AS UUID) = v.id
        {clip_join_sql}
        LEFT JOIN LATERAL (
            SELECT id, clip_url, duration_sec
            FROM video_clips
            WHERE CAST(vp.video_id AS VARCHAR) = CAST(video_clips.video_id AS VARCHAR)
              AND vp.phase_index::text = video_clips.phase_index
              AND video_clips.clip_url IS NOT NULL
            ORDER BY video_clips.created_at DESC
            LIMIT 1
        ) vc ON true
        {where_clause}
        ORDER BY vp.rated_at DESC NULLS LAST, vp.video_id, vp.phase_index
        LIMIT :limit OFFSET :offset
    """)

    result = await db.execute(sql, {"limit": per_page, "offset": offset})
    rows = result.mappings().fetchall()

    feedbacks = []
    for r in rows:
        feedbacks.append({
            "video_id": r["video_id"],
            "phase_index": r["phase_index"],
            "time_start": float(r["time_start"]) if r["time_start"] else None,
            "time_end": float(r["time_end"]) if r["time_end"] else None,
            "phase_description": r["phase_description"],
            "user_rating": r["user_rating"],
            "user_comment": r["user_comment"],
            "rated_at": str(r["rated_at"]) if r["rated_at"] else None,
            "rated_by_reviewer_id": r["rated_by_reviewer_id"],
            "original_filename": r["original_filename"],
            "compressed_blob_url": r["compressed_blob_url"],
            "clip_url": r["clip_url"],
            "clip_id": str(r["clip_id"]) if r["clip_id"] else None,
            "clip_duration_sec": float(r["clip_duration_sec"]) if r["clip_duration_sec"] else None,
        })

    return {
        "feedbacks": feedbacks,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page if total else 0,
    }


# ══════════════════════════════════════════════════════════════
# Admin: Reviewer Management
# ══════════════════════════════════════════════════════════════

def _check_admin_key(key: Optional[str]):
    expected = f"{ADMIN_ID}:{ADMIN_PASS}"
    if key != expected:
        raise HTTPException(status_code=403, detail="Invalid admin credentials")


@router.post("/admin/reviewers")
async def admin_create_reviewer(
    req: ReviewerCreateRequest,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Create a new reviewer account."""
    _check_admin_key(x_admin_key)

    # Check if email already exists
    existing = await db.execute(
        text("SELECT id, role FROM users WHERE email = :email"),
        {"email": req.email},
    )
    row = existing.mappings().fetchone()
    if row:
        if row["role"] == "reviewer":
            raise HTTPException(status_code=409, detail="この採点者アカウントは既に存在します")
        # Upgrade existing user to reviewer
        await db.execute(text("""
            UPDATE users SET role = 'reviewer', display_name = :name, is_active = true, hashed_password = :pw
            WHERE id = :uid
        """), {"uid": row["id"], "name": req.display_name, "pw": hash_password(req.password)})
        await db.commit()
        return {"success": True, "reviewer_id": row["id"], "message": "既存ユーザーを採点者に昇格しました"}

    # Create new user with reviewer role
    hashed = hash_password(req.password)
    result = await db.execute(text("""
        INSERT INTO users (email, hashed_password, display_name, role, is_active, provider, created_at, updated_at)
        VALUES (:email, :pw, :name, 'reviewer', true, 'local', NOW(), NOW())
        RETURNING id
    """), {"email": req.email, "pw": hashed, "name": req.display_name})
    await db.commit()
    new_id = result.scalar()

    logger.info(f"[Admin] Created reviewer: {req.email} (id={new_id})")
    return {"success": True, "reviewer_id": new_id, "email": req.email, "display_name": req.display_name}


@router.get("/admin/reviewers")
async def admin_list_reviewers(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
):
    """List all reviewers with their stats."""
    _check_admin_key(x_admin_key)

    result = await db.execute(text("""
        SELECT
            u.id,
            u.email,
            u.display_name,
            u.is_active,
            u.created_at,
            COALESCE(stats.total_rated, 0) as total_rated,
            COALESCE(stats.avg_rating, 0) as avg_rating,
            COALESCE(stats.today_rated, 0) as today_rated,
            COALESCE(sess.total_sessions, 0) as total_sessions,
            COALESCE(work.total_minutes, 0) as total_minutes,
            sess.last_session_at,
            COALESCE(stats.r1, 0) as r1,
            COALESCE(stats.r2, 0) as r2,
            COALESCE(stats.r3, 0) as r3,
            COALESCE(stats.r4, 0) as r4,
            COALESCE(stats.r5, 0) as r5
        FROM users u
        LEFT JOIN (
            SELECT
                rated_by_reviewer_id as rid,
                COUNT(*) as total_rated,
                AVG(user_rating) as avg_rating,
                COUNT(CASE WHEN rated_at >= CURRENT_DATE THEN 1 END) as today_rated,
                COUNT(CASE WHEN user_rating = 1 THEN 1 END) as r1,
                COUNT(CASE WHEN user_rating = 2 THEN 1 END) as r2,
                COUNT(CASE WHEN user_rating = 3 THEN 1 END) as r3,
                COUNT(CASE WHEN user_rating = 4 THEN 1 END) as r4,
                COUNT(CASE WHEN user_rating = 5 THEN 1 END) as r5
            FROM video_phases
            WHERE rated_by_reviewer_id IS NOT NULL
            GROUP BY rated_by_reviewer_id
        ) stats ON u.id = stats.rid
        LEFT JOIN (
            SELECT
                reviewer_id as rid,
                COUNT(*) as total_sessions,
                MAX(started_at) as last_session_at
            FROM review_sessions
            GROUP BY reviewer_id
        ) sess ON u.id = sess.rid
        LEFT JOIN (
            SELECT
                rated_by_reviewer_id as rid,
                COALESCE(
                    SUM(EXTRACT(EPOCH FROM (last_rated - first_rated)) / 60.0 + 5),
                    0
                ) as total_minutes
            FROM (
                SELECT
                    rated_by_reviewer_id,
                    DATE_TRUNC('day', rated_at) as work_day,
                    MIN(rated_at) as first_rated,
                    MAX(rated_at) as last_rated
                FROM video_phases
                WHERE rated_by_reviewer_id IS NOT NULL
                  AND rated_at IS NOT NULL
                GROUP BY rated_by_reviewer_id, DATE_TRUNC('day', rated_at)
            ) daily
            GROUP BY rated_by_reviewer_id
        ) work ON u.id = work.rid
        WHERE u.role = 'reviewer'
        ORDER BY COALESCE(stats.total_rated, 0) DESC
    """))
    rows = result.mappings().fetchall()

    reviewers = []
    for r in rows:
        reviewers.append({
            "id": r["id"],
            "email": r["email"],
            "display_name": r["display_name"],
            "is_active": r["is_active"],
            "created_at": str(r["created_at"]) if r["created_at"] else None,
            "total_rated": r["total_rated"],
            "avg_rating": round(float(r["avg_rating"]), 2),
            "today_rated": r["today_rated"],
            "total_sessions": r["total_sessions"],
            "total_minutes": round(float(r["total_minutes"]), 1),
            "last_session_at": str(r["last_session_at"]) if r["last_session_at"] else None,
            "distribution": {
                "1": r["r1"], "2": r["r2"], "3": r["r3"], "4": r["r4"], "5": r["r5"],
            },
        })

    return {"reviewers": reviewers, "total": len(reviewers)}


@router.put("/admin/reviewers/{reviewer_id}")
async def admin_update_reviewer(
    reviewer_id: int,
    req: ReviewerUpdateRequest,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Update a reviewer's info."""
    _check_admin_key(x_admin_key)

    updates = []
    params = {"rid": reviewer_id}

    if req.display_name is not None:
        updates.append("display_name = :name")
        params["name"] = req.display_name
    if req.is_active is not None:
        updates.append("is_active = :active")
        params["active"] = req.is_active
    if req.password is not None:
        updates.append("hashed_password = :pw")
        params["pw"] = hash_password(req.password)

    if not updates:
        raise HTTPException(status_code=400, detail="更新する項目がありません")

    updates.append("updated_at = NOW()")
    sql = text(f"UPDATE users SET {', '.join(updates)} WHERE id = :rid AND role = 'reviewer' RETURNING id")
    result = await db.execute(sql, params)
    await db.commit()

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="採点者が見つかりません")

    return {"success": True, "reviewer_id": reviewer_id}


@router.delete("/admin/reviewers/{reviewer_id}")
async def admin_deactivate_reviewer(
    reviewer_id: int,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
):
    """Deactivate a reviewer (soft delete)."""
    _check_admin_key(x_admin_key)

    result = await db.execute(text("""
        UPDATE users SET is_active = false, updated_at = NOW()
        WHERE id = :rid AND role = 'reviewer'
        RETURNING id
    """), {"rid": reviewer_id})
    await db.commit()

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="採点者が見つかりません")

    return {"success": True, "message": "採点者を無効化しました"}


@router.get("/admin/review-sessions")
async def admin_list_review_sessions(
    reviewer_id: Optional[int] = None,
    page: int = 1,
    per_page: int = 50,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
    db: AsyncSession = Depends(get_db),
):
    """List review sessions with optional reviewer filter."""
    _check_admin_key(x_admin_key)

    where = "WHERE 1=1"
    params = {"limit": per_page, "offset": (max(1, page) - 1) * per_page}
    if reviewer_id:
        where += " AND rs.reviewer_id = :rid"
        params["rid"] = reviewer_id

    result = await db.execute(text(f"""
        SELECT
            rs.id, rs.reviewer_id, rs.started_at, rs.ended_at,
            rs.clips_reviewed, rs.duration_minutes, rs.last_heartbeat,
            EXTRACT(EPOCH FROM (NOW() - rs.started_at)) / 60.0 as calc_duration,
            u.display_name, u.email
        FROM review_sessions rs
        JOIN users u ON rs.reviewer_id = u.id
        {where}
        ORDER BY rs.started_at DESC
        LIMIT :limit OFFSET :offset
    """), params)
    rows = result.mappings().fetchall()

    sessions = []
    for r in rows:
        sessions.append({
            "id": r["id"],
            "reviewer_id": r["reviewer_id"],
            "reviewer_name": r["display_name"],
            "reviewer_email": r["email"],
            "started_at": str(r["started_at"]) if r["started_at"] else None,
            "ended_at": str(r["ended_at"]) if r["ended_at"] else None,
            "clips_reviewed": r["clips_reviewed"] or 0,
            "duration_minutes": round(float(r["duration_minutes"]), 1) if r["duration_minutes"] else (round(float(r["calc_duration"]), 1) if r.get("calc_duration") else None),
            "last_heartbeat": str(r["last_heartbeat"]) if r["last_heartbeat"] else None,
        })

    return {"sessions": sessions, "page": page, "per_page": per_page}
