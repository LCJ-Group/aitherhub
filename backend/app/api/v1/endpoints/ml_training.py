"""
ml_training.py – AI切り抜き学習 管理API
========================================
- 学習履歴一覧
- 精度メトリクス推移
- 特徴量重要度
- 手動学習トリガー
- モデルステータス
"""
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.core.db import AsyncSessionLocal
from contextlib import asynccontextmanager


@asynccontextmanager
async def get_session():
    async with AsyncSessionLocal() as session:
        yield session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ml-training", tags=["ML Training"])


# ── Auth ──
def _check_admin(key: Optional[str]):
    if key != "aither:hub":
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── Models ──
class TrainingTriggerRequest(BaseModel):
    target: str = "both"  # click, order, both


class TrainingRunResponse(BaseModel):
    id: int
    run_id: str
    target: str
    model_version: Optional[str] = None
    started_at: str
    completed_at: Optional[str] = None
    status: str
    dataset_size: Optional[int] = None
    positive_count: Optional[int] = None
    negative_count: Optional[int] = None
    auc_score: Optional[float] = None
    precision_at_5: Optional[float] = None
    recall_at_5: Optional[float] = None
    f1_score: Optional[float] = None
    feature_importance: Optional[dict] = None
    config: Optional[dict] = None
    error_message: Optional[str] = None


# ── Endpoints ──

@router.get("/runs")
async def get_training_runs(
    limit: int = 20,
    target: Optional[str] = None,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """学習履歴一覧を取得"""
    _check_admin(x_admin_key)

    query = """
        SELECT id, run_id, target, model_version, started_at, completed_at,
               status, dataset_size, positive_count, negative_count,
               auc_score, precision_at_5, recall_at_5, f1_score,
               feature_importance, config, error_message
        FROM ml_training_runs
    """
    params = {"limit": limit}

    if target:
        query += " WHERE target = :target"
        params["target"] = target

    query += " ORDER BY started_at DESC LIMIT :limit"

    async with get_session() as session:
        result = await session.execute(text(query), params)
        rows = result.fetchall()

    runs = []
    for row in rows:
        runs.append({
            "id": row[0],
            "run_id": row[1],
            "target": row[2],
            "model_version": row[3],
            "started_at": row[4].isoformat() if row[4] else None,
            "completed_at": row[5].isoformat() if row[5] else None,
            "status": row[6],
            "dataset_size": row[7],
            "positive_count": row[8],
            "negative_count": row[9],
            "auc_score": row[10],
            "precision_at_5": row[11],
            "recall_at_5": row[12],
            "f1_score": row[13],
            "feature_importance": row[14],
            "config": row[15],
            "error_message": row[16],
        })

    return {"runs": runs, "total": len(runs)}


@router.get("/metrics")
async def get_metrics_history(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """精度メトリクス推移を取得（グラフ用）"""
    _check_admin(x_admin_key)

    query = """
        SELECT target, model_version, started_at, auc_score, 
               precision_at_5, recall_at_5, f1_score, dataset_size
        FROM ml_training_runs
        WHERE status = 'completed' AND auc_score IS NOT NULL
        ORDER BY started_at ASC
    """

    async with get_session() as session:
        result = await session.execute(text(query))
        rows = result.fetchall()

    metrics = {"click": [], "order": []}
    for row in rows:
        entry = {
            "model_version": row[1],
            "date": row[2].isoformat() if row[2] else None,
            "auc": row[3],
            "precision_at_5": row[4],
            "recall_at_5": row[5],
            "f1": row[6],
            "dataset_size": row[7],
        }
        target = row[0]
        if target in metrics:
            metrics[target].append(entry)

    return metrics


@router.get("/status")
async def get_model_status(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """現在のモデルステータス（最新モデル情報）"""
    _check_admin(x_admin_key)

    query = """
        SELECT target, model_version, completed_at, auc_score, 
               precision_at_5, f1_score, dataset_size, model_path
        FROM ml_training_runs
        WHERE status = 'completed'
        ORDER BY completed_at DESC
    """

    async with get_session() as session:
        result = await session.execute(text(query))
        rows = result.fetchall()

    # Get latest for each target
    latest = {}
    for row in rows:
        target = row[0]
        if target not in latest:
            latest[target] = {
                "target": target,
                "model_version": row[1],
                "last_trained": row[2].isoformat() if row[2] else None,
                "auc_score": row[3],
                "precision_at_5": row[4],
                "f1_score": row[5],
                "dataset_size": row[6],
                "model_path": row[7],
            }

    # Count total training data
    data_query = """
        SELECT 
            (SELECT COUNT(*) FROM video_phases) as total_phases,
            (SELECT COUNT(*) FROM video_phases WHERE user_rating IS NOT NULL) as rated_phases,
            (SELECT COUNT(*) FROM clip_feedback WHERE rating = 'bad') as ng_phases,
            (SELECT COUNT(*) FROM video_clips) as total_clips,
            (SELECT COUNT(*) FROM video_clips WHERE is_sold = true) as sold_clips
    """

    async with get_session() as session:
        result = await session.execute(text(data_query))
        data_row = result.fetchone()

    return {
        "models": latest,
        "data_summary": {
            "total_phases": data_row[0] if data_row else 0,
            "rated_phases": data_row[1] if data_row else 0,
            "ng_phases": data_row[2] if data_row else 0,
            "total_clips": data_row[3] if data_row else 0,
            "sold_clips": data_row[4] if data_row else 0,
        },
    }


@router.post("/trigger")
async def trigger_training(
    req: TrainingTriggerRequest,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """手動で学習をトリガー（GPU VMで実行）"""
    _check_admin(x_admin_key)

    run_id = f"manual_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

    # Record the training run as 'queued'
    targets = ["click", "order"] if req.target == "both" else [req.target]

    async with get_session() as session:
        for target in targets:
            await session.execute(
                text("""
                    INSERT INTO ml_training_runs (run_id, target, status, started_at)
                    VALUES (:run_id, :target, 'queued', NOW())
                """),
                {"run_id": f"{run_id}_{target}", "target": target},
            )
        await session.commit()

    # Trigger GPU VM training via SSH
    import asyncio
    import httpx

    WORKER_HEALTH_URL = os.getenv("WORKER_HEALTH_URL", "http://52.185.188.210:8081")

    async def _trigger_retrain_via_worker():
        """Trigger retrain via Worker VM health API."""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(f"{WORKER_HEALTH_URL}/trigger-retrain")
                if resp.status_code == 202:
                    logger.info("Successfully triggered retrain on Worker VM")
                else:
                    logger.warning(f"Worker retrain trigger returned {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Failed to trigger retrain on Worker VM: {e}")

    # Fire and forget
    asyncio.create_task(_trigger_retrain_via_worker())

    return {
        "message": f"Training triggered for targets: {targets}",
        "run_id": run_id,
        "status": "queued",
    }


@router.get("/feature-importance")
async def get_feature_importance(
    target: str = "click",
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """最新モデルの特徴量重要度を取得"""
    _check_admin(x_admin_key)

    query = """
        SELECT feature_importance, model_version, completed_at
        FROM ml_training_runs
        WHERE status = 'completed' AND target = :target 
              AND feature_importance IS NOT NULL
        ORDER BY completed_at DESC
        LIMIT 1
    """

    async with get_session() as session:
        result = await session.execute(text(query), {"target": target})
        row = result.fetchone()

    if not row:
        return {"features": [], "model_version": None}

    return {
        "features": row[0],
        "model_version": row[1],
        "trained_at": row[2].isoformat() if row[2] else None,
    }


@router.get("/effectiveness")
async def get_effectiveness_by_version(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """バージョン別の効果計測データを取得
    
    - 各バージョンで生成されたクリップの平均採点スコア
    - NG率（unusable率）
    - 採用率（adopt率）
    - AUCスコアの推移
    """
    _check_admin(x_admin_key)

    # 1. バージョン別のクリップ統計
    clip_stats_query = """
        SELECT 
            COALESCE(vc.ml_model_version, 'pre-AI') as version,
            COUNT(*) as total_clips,
            COUNT(CASE WHEN vc.is_unusable = true THEN 1 END) as ng_count,
            COUNT(CASE WHEN cf.feedback = 'adopt' THEN 1 END) as adopt_count,
            COUNT(CASE WHEN cf.feedback = 'reject' THEN 1 END) as reject_count,
            COUNT(CASE WHEN cf.feedback IS NOT NULL THEN 1 END) as reviewed_count,
            AVG(CASE WHEN cf.ai_score_at_feedback IS NOT NULL 
                THEN CAST(cf.ai_score_at_feedback AS FLOAT) END) as avg_ai_score,
            MIN(vc.created_at) as first_clip_at,
            MAX(vc.created_at) as last_clip_at
        FROM video_clips vc
        LEFT JOIN clip_feedback cf ON cf.video_id = vc.video_id 
            AND cf.phase_index = CAST(vc.phase_index AS TEXT)
        GROUP BY COALESCE(vc.ml_model_version, 'pre-AI')
        ORDER BY first_clip_at ASC
    """

    # 2. バージョン別のgroup_best_phases統計
    best_phase_stats_query = """
        SELECT 
            COALESCE(ml_model_version, 'pre-AI') as version,
            COUNT(*) as total_best_phases,
            AVG(score) as avg_score,
            MIN(created_at) as first_at,
            MAX(created_at) as last_at
        FROM group_best_phases
        GROUP BY COALESCE(ml_model_version, 'pre-AI')
        ORDER BY first_at ASC
    """

    # 3. AUCスコアの推移（ml_training_runsから）
    auc_history_query = """
        SELECT model_version, target, auc_score, completed_at, dataset_size
        FROM ml_training_runs
        WHERE status = 'completed' AND auc_score IS NOT NULL
        ORDER BY completed_at ASC
    """

    async with get_session() as session:
        # Check if ml_model_version column exists
        col_check = await session.execute(text("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'video_clips' AND column_name = 'ml_model_version'
            )
        """))
        has_ml_version_col = col_check.scalar()

        if not has_ml_version_col:
            # Column doesn't exist yet - return empty data with explanation
            return {
                "version_clip_stats": [{"version": "pre-AI", "total_clips": 0, "ng_count": 0, "ng_rate": 0, "adopt_count": 0, "reject_count": 0, "reviewed_count": 0, "adopt_rate": 0, "avg_ai_score": None, "first_clip_at": None, "last_clip_at": None}],
                "version_best_phase_stats": [],
                "auc_history": [],
                "note": "ml_model_version column not yet created. Will be available after next app restart."
            }

        clip_result = await session.execute(text(clip_stats_query))
        clip_rows = clip_result.fetchall()

        # Check group_best_phases column too
        gbp_col_check = await session.execute(text("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'group_best_phases' AND column_name = 'ml_model_version'
            )
        """))
        has_gbp_ml_col = gbp_col_check.scalar()

        best_rows = []
        if has_gbp_ml_col:
            best_result = await session.execute(text(best_phase_stats_query))
            best_rows = best_result.fetchall()

        auc_result = await session.execute(text(auc_history_query))
        auc_rows = auc_result.fetchall()

    # Format clip stats
    version_stats = []
    for row in clip_rows:
        total = row[1] or 0
        ng = row[2] or 0
        adopt = row[3] or 0
        reject = row[4] or 0
        reviewed = row[5] or 0
        version_stats.append({
            "version": row[0],
            "total_clips": total,
            "ng_count": ng,
            "ng_rate": round(ng / total * 100, 1) if total > 0 else 0,
            "adopt_count": adopt,
            "reject_count": reject,
            "reviewed_count": reviewed,
            "adopt_rate": round(adopt / reviewed * 100, 1) if reviewed > 0 else 0,
            "avg_ai_score": round(row[6], 2) if row[6] else None,
            "first_clip_at": row[7].isoformat() if row[7] else None,
            "last_clip_at": row[8].isoformat() if row[8] else None,
        })

    # Format best phase stats
    best_phase_versions = []
    for row in best_rows:
        best_phase_versions.append({
            "version": row[0],
            "total_best_phases": row[1],
            "avg_score": round(row[2], 4) if row[2] else None,
            "first_at": row[3].isoformat() if row[3] else None,
            "last_at": row[4].isoformat() if row[4] else None,
        })

    # Format AUC history
    auc_history = []
    for row in auc_rows:
        auc_history.append({
            "model_version": row[0],
            "target": row[1],
            "auc_score": row[2],
            "trained_at": row[3].isoformat() if row[3] else None,
            "dataset_size": row[4],
        })

    return {
        "version_clip_stats": version_stats,
        "version_best_phase_stats": best_phase_versions,
        "auc_history": auc_history,
    }


# ── Active Learning Endpoints (v8) ──

@router.get("/active-learning/uncertain-phases")
async def get_uncertain_phases(
    limit: int = 20,
    video_id: Optional[str] = None,
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """v8: アクティブラーニング - 不確実なフェーズ一覧を取得

    MLスコアが0.4〜0.6の「不確実領域」にあるフェーズを返す。
    これらのフェーズを人間がレビューすることで、モデル精度が最も効率的に向上する。

    Returns:
        phases: レビュー優先度順にソートされたフェーズ一覧
        stats: 不確実フェーズの統計情報
    """
    _check_admin(x_admin_key)

    try:
        # Fetch phases with their features
        # Fix: wrap OR condition in parentheses to prevent precedence issues with AND
        conditions = ["(vp.user_rating IS NULL OR vp.user_rating = 0)"]  # 未レビュー優先
        params = {}
        if video_id:
            conditions.append("vp.video_id = :video_id")
            params["video_id"] = video_id

        where = "WHERE " + " AND ".join(conditions)

        query = f"""
            SELECT 
                vp.video_id,
                vp.phase_index,
                vp.phase_description,
                vp.time_start,
                vp.time_end,
                vp.cta_score,
                vp.importance_score,
                vp.sales_psychology_tags,
                vp.human_sales_tags,
                vp.user_rating,
                vp.user_comment,
                vp.reviewer_name,
                vp.frame_quality,
                vp.audio_features,
                v.original_filename as video_title,
                v.created_at as video_created_at
            FROM video_phases vp
            LEFT JOIN videos v ON v.id = vp.video_id
            {where}
            ORDER BY vp.video_id, vp.phase_index
            LIMIT 500
        """

        async with get_session() as session:
            result = await session.execute(text(query), params)
            rows = result.fetchall()

        if not rows:
            return {"phases": [], "stats": {"total_uncertain": 0, "total_scanned": 0}}

        # Try to use ml_scorer for uncertainty detection
        import sys
        import os
        worker_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "worker", "batch")
        worker_path = os.path.abspath(worker_path)
        if worker_path not in sys.path:
            sys.path.insert(0, worker_path)

        from ml_scorer import get_ml_scorer, extract_phase_features_for_ml

        scorer = get_ml_scorer()
        if scorer:
            phases_with_scores = []
            for row in rows:
                # Build phase dict for feature extraction
                phase_dict = {
                    "video_id": str(row[0]),
                    "phase_index": row[1],
                    "text": row[2] or "",
                    "time_range": {
                        "start_sec": float(row[3]) if row[3] else 0,
                        "end_sec": float(row[4]) if row[4] else 0,
                    },
                    "cta_score": row[5] or 0,
                    "importance_score": float(row[6]) if row[6] else 0,
                    "tags": _parse_json_safe(row[7]),
                    "human_sales_tags": _parse_json_safe(row[8]),
                    "user_rating": row[9] or 0,
                    "user_comment": row[10] or "",
                    "frame_quality": _parse_json_safe(row[12]),
                    "audio_features": _parse_json_safe(row[13]),
                }

                features = extract_phase_features_for_ml(phase_dict)
                prediction = scorer.predict_with_uncertainty(features)

                if prediction["is_uncertain"]:
                    phases_with_scores.append({
                        "video_id": str(row[0]),
                        "phase_index": row[1],
                        "phase_description": (row[2] or "")[:200],
                        "time_start": float(row[3]) if row[3] else 0,
                        "time_end": float(row[4]) if row[4] else 0,
                        "video_title": row[14] or "",
                        "ml_score": prediction["score"],
                        "click_score": prediction["click_score"],
                        "order_score": prediction["order_score"],
                        "uncertainty": prediction["uncertainty"],
                        "review_priority": prediction["review_priority"],
                        "has_human_review": 1 if row[9] and row[9] > 0 else 0,
                        "user_rating": row[9] or 0,
                    })

            # Sort by review priority
            priority_order = {"high": 0, "medium": 1, "low": 2}
            phases_with_scores.sort(
                key=lambda x: (priority_order.get(x["review_priority"], 3), x["uncertainty"])
            )

            return {
                "phases": phases_with_scores[:limit],
                "stats": {
                    "total_uncertain": len(phases_with_scores),
                    "total_scanned": len(rows),
                    "uncertainty_rate": round(len(phases_with_scores) / max(len(rows), 1) * 100, 1),
                    "model_version": scorer.get_model_version(),
                },
            }
        else:
            # ML scorer not available (no model files on this server)
            # Return unreviewed phases as candidates for review (heuristic fallback)
            fallback_phases = []
            for row in rows[:limit]:
                fallback_phases.append({
                    "video_id": str(row[0]),
                    "phase_index": row[1],
                    "phase_description": (row[2] or "")[:200],
                    "time_start": float(row[3]) if row[3] else 0,
                    "time_end": float(row[4]) if row[4] else 0,
                    "video_title": row[14] or "",
                    "ml_score": None,
                    "click_score": None,
                    "order_score": None,
                    "uncertainty": None,
                    "review_priority": "medium",
                    "has_human_review": 1 if row[9] and row[9] > 0 else 0,
                    "user_rating": row[9] or 0,
                })
            return {
                "phases": fallback_phases,
                "stats": {
                    "total_uncertain": len(rows),
                    "total_scanned": len(rows),
                    "note": "ML scorer not available on this server; showing unreviewed phases as fallback",
                },
            }

    except Exception as e:
        logger.error(f"[active-learning] Error: {e}", exc_info=True)
        return {
            "phases": [],
            "stats": {
                "total_uncertain": 0,
                "total_scanned": 0,
                "error": str(e),
            },
        }


@router.get("/active-learning/stats")
async def get_active_learning_stats(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """v8: アクティブラーニング統計情報

    Returns:
        review_coverage: レビュー済みフェーズの割合
        rating_distribution: 評価分布
        reviewer_stats: レビュアー別統計
        uncertainty_estimate: 不確実フェーズの推定数
    """
    _check_admin(x_admin_key)

    query = """
        SELECT 
            COUNT(*) as total_phases,
            COUNT(CASE WHEN user_rating IS NOT NULL AND user_rating > 0 THEN 1 END) as rated_phases,
            COUNT(CASE WHEN user_rating = 5 THEN 1 END) as rating_5,
            COUNT(CASE WHEN user_rating = 4 THEN 1 END) as rating_4,
            COUNT(CASE WHEN user_rating = 3 THEN 1 END) as rating_3,
            COUNT(CASE WHEN user_rating = 2 THEN 1 END) as rating_2,
            COUNT(CASE WHEN user_rating = 1 THEN 1 END) as rating_1
        FROM video_phases
    """

    reviewer_query = """
        SELECT 
            reviewer_name,
            COUNT(*) as review_count,
            AVG(user_rating) as avg_rating,
            STDDEV(user_rating) as std_rating,
            MIN(user_rating) as min_rating,
            MAX(user_rating) as max_rating
        FROM video_phases
        WHERE reviewer_name IS NOT NULL AND user_rating > 0
        GROUP BY reviewer_name
        ORDER BY review_count DESC
    """

    async with get_session() as session:
        result = await session.execute(text(query))
        row = result.fetchone()

        reviewer_result = await session.execute(text(reviewer_query))
        reviewer_rows = reviewer_result.fetchall()

    total = row[0] if row else 0
    rated = row[1] if row else 0

    reviewers = []
    for r in reviewer_rows:
        reviewers.append({
            "reviewer_name": r[0],
            "review_count": r[1],
            "avg_rating": round(float(r[2]), 2) if r[2] else None,
            "std_rating": round(float(r[3]), 2) if r[3] else None,
            "min_rating": r[4],
            "max_rating": r[5],
        })

    return {
        "review_coverage": {
            "total_phases": total,
            "rated_phases": rated,
            "unrated_phases": total - rated,
            "coverage_rate": round(rated / max(total, 1) * 100, 1),
        },
        "rating_distribution": {
            "5": row[2] if row else 0,
            "4": row[3] if row else 0,
            "3": row[4] if row else 0,
            "2": row[5] if row else 0,
            "1": row[6] if row else 0,
        },
        "reviewer_stats": reviewers,
        "recommendations": {
            "target_coverage": 30.0,
            "current_coverage": round(rated / max(total, 1) * 100, 1),
            "phases_needed": max(0, int(total * 0.3) - rated),
            "note": "30%以上のレビューカバレッジでモデル精度が大幅に向上します",
        },
    }


def _parse_json_safe(val):
    """Safely parse a JSON value."""
    if val is None:
        return []
    if isinstance(val, (list, dict)):
        return val
    if isinstance(val, str):
        try:
            import json
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return []
    return []


# ── v10: Auto-retrain trigger & learning progress ──

@router.get("/auto-retrain/status")
async def get_auto_retrain_status(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """v10: 自動再学習の状態を確認

    新しい採点データが一定数溜まったら自動で再学習をトリガーするかどうかの判定。
    閾値: 50件の新しい採点（前回学習以降）で自動トリガー。

    Returns:
        should_retrain: 再学習すべきかどうか
        new_ratings_since_last_train: 前回学習以降の新しい採点数
        new_ng_since_last_train: 前回学習以降の新しいNG判定数
        threshold: 自動トリガー閾値
        last_train_at: 前回学習日時
    """
    _check_admin(x_admin_key)

    AUTO_RETRAIN_THRESHOLD = 50  # 50件の新しい採点で自動トリガー

    async with get_session() as session:
        # 前回の学習日時を取得
        last_train_result = await session.execute(text("""
            SELECT MAX(started_at) as last_train
            FROM ml_training_runs
            WHERE status IN ('completed', 'success')
        """))
        last_train_row = last_train_result.fetchone()
        last_train_at = last_train_row[0] if last_train_row and last_train_row[0] else None

        # 前回学習以降の新しい採点数（フィードバックタブ）
        if last_train_at:
            rating_result = await session.execute(text("""
                SELECT COUNT(*) FROM video_phases
                WHERE user_rating IS NOT NULL AND user_rating > 0
                AND updated_at > :last_train
            """), {"last_train": last_train_at})
        else:
            rating_result = await session.execute(text("""
                SELECT COUNT(*) FROM video_phases
                WHERE user_rating IS NOT NULL AND user_rating > 0
            """))
        new_ratings = rating_result.scalar() or 0

        # 前回学習以降の新しいNG判定数（クリップDB）
        if last_train_at:
            ng_result = await session.execute(text("""
                SELECT COUNT(*) FROM video_clips
                WHERE unusable_at IS NOT NULL AND unusable_at > :last_train
            """), {"last_train": last_train_at})
        else:
            ng_result = await session.execute(text("""
                SELECT COUNT(*) FROM video_clips
                WHERE unusable_at IS NOT NULL
            """))
        new_ngs = ng_result.scalar() or 0

        # 前回学習以降の新しいブランド割当数
        if last_train_at:
            brand_result = await session.execute(text("""
                SELECT COUNT(*) FROM widget_clip_assignments
                WHERE created_at > :last_train
            """), {"last_train": last_train_at})
        else:
            brand_result = await session.execute(text("""
                SELECT COUNT(*) FROM widget_clip_assignments
            """))
        new_brands = brand_result.scalar() or 0

        # 前回学習以降の新しい再生成採点数（regen_grade）
        if last_train_at:
            regen_grade_result = await session.execute(text("""
                SELECT COUNT(*) FROM ai_clip_jobs
                WHERE config->>'type' = 'regenerate_from_source'
                  AND config->>'regen_grade' IS NOT NULL
                  AND config->>'regen_grade' != ''
                  AND updated_at > :last_train
            """), {"last_train": last_train_at})
        else:
            regen_grade_result = await session.execute(text("""
                SELECT COUNT(*) FROM ai_clip_jobs
                WHERE config->>'type' = 'regenerate_from_source'
                  AND config->>'regen_grade' IS NOT NULL
                  AND config->>'regen_grade' != ''
            """))
        new_regen_grades = regen_grade_result.scalar() or 0

    total_new_signals = new_ratings + new_ngs + new_brands + new_regen_grades
    should_retrain = total_new_signals >= AUTO_RETRAIN_THRESHOLD

    return {
        "should_retrain": should_retrain,
        "total_new_signals": total_new_signals,
        "new_ratings_since_last_train": new_ratings,
        "new_ng_since_last_train": new_ngs,
        "new_brand_assignments_since_last_train": new_brands,
        "new_regen_grades_since_last_train": new_regen_grades,
        "threshold": AUTO_RETRAIN_THRESHOLD,
        "last_train_at": str(last_train_at) if last_train_at else None,
        "recommendation": "再学習を推奨します" if should_retrain else f"あと{AUTO_RETRAIN_THRESHOLD - total_new_signals}件の採点で自動トリガー",
    }


@router.post("/auto-retrain/trigger")
async def auto_retrain_trigger(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """v10: 自動再学習トリガー

    新しい採点データが閾値を超えている場合に自動で再学習を開始する。
    """
    _check_admin(x_admin_key)

    # まず状態を確認
    import httpx
    import asyncio

    AUTO_RETRAIN_THRESHOLD = 50

    async with get_session() as session:
        last_train_result = await session.execute(text("""
            SELECT MAX(started_at) as last_train
            FROM ml_training_runs
            WHERE status IN ('completed', 'success')
        """))
        last_train_row = last_train_result.fetchone()
        last_train_at = last_train_row[0] if last_train_row and last_train_row[0] else None

        # 合計新シグナル数を計算
        if last_train_at:
            total_result = await session.execute(text("""
                SELECT 
                    (SELECT COUNT(*) FROM video_phases WHERE user_rating > 0 AND updated_at > :lt) +
                    (SELECT COUNT(*) FROM video_clips WHERE unusable_at > :lt) +
                    (SELECT COUNT(*) FROM widget_clip_assignments WHERE created_at > :lt) +
                    (SELECT COUNT(*) FROM ai_clip_jobs WHERE config->>'type' = 'regenerate_from_source' AND config->>'regen_grade' IS NOT NULL AND config->>'regen_grade' != '' AND updated_at > :lt) as total
            """), {"lt": last_train_at})
        else:
            total_result = await session.execute(text("""
                SELECT 
                    (SELECT COUNT(*) FROM video_phases WHERE user_rating > 0) +
                    (SELECT COUNT(*) FROM video_clips WHERE unusable_at IS NOT NULL) +
                    (SELECT COUNT(*) FROM widget_clip_assignments) +
                    (SELECT COUNT(*) FROM ai_clip_jobs WHERE config->>'type' = 'regenerate_from_source' AND config->>'regen_grade' IS NOT NULL AND config->>'regen_grade' != '') as total
            """))
        total_new = total_result.scalar() or 0

    if total_new < AUTO_RETRAIN_THRESHOLD:
        return {
            "triggered": False,
            "reason": f"新しい学習シグナルが{total_new}件（閾値: {AUTO_RETRAIN_THRESHOLD}件）",
            "total_new_signals": total_new,
        }

    # トリガー実行
    WORKER_HEALTH_URL = os.getenv("WORKER_HEALTH_URL", "http://52.185.188.210:8081")

    run_id = f"auto_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

    async with get_session() as session:
        for target in ["click", "order"]:
            await session.execute(
                text("""
                    INSERT INTO ml_training_runs (run_id, target, status, started_at)
                    VALUES (:run_id, :target, 'queued', NOW())
                """),
                {"run_id": f"{run_id}_{target}", "target": target},
            )
        await session.commit()

    async def _trigger():
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(f"{WORKER_HEALTH_URL}/trigger-retrain")
                if resp.status_code == 202:
                    logger.info(f"[auto-retrain] Successfully triggered (run_id={run_id}, signals={total_new})")
                else:
                    logger.warning(f"[auto-retrain] Worker returned {resp.status_code}")
        except Exception as e:
            logger.error(f"[auto-retrain] Failed: {e}")

    asyncio.create_task(_trigger())

    return {
        "triggered": True,
        "run_id": run_id,
        "total_new_signals": total_new,
        "message": f"自動再学習をトリガーしました（新シグナル: {total_new}件）",
    }


@router.get("/learning-progress")
async def get_learning_progress(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """v10: 学習進捗ダッシュボード - 全体の学習状態を一覧表示

    Returns:
        data_signals: 各データソースからの学習シグナル数
        model_info: 現在のモデル情報
        training_history: 直近の学習履歴
        quality_metrics: 品質指標
    """
    _check_admin(x_admin_key)

    async with get_session() as session:
        # 各データソースの学習シグナル数（各テーブルが存在しない場合に備えて個別クエリ）
        total_ratings = 0
        total_ng = 0
        total_brand = 0
        total_feedback = 0
        total_phases = 0
        total_videos = 0
        total_regen_grades = 0
        total_regen_ok = 0
        total_regen_ng = 0

        try:
            r = await session.execute(text("SELECT COUNT(*) FROM video_phases WHERE user_rating > 0"))
            total_ratings = r.scalar() or 0
        except Exception:
            await session.rollback()

        try:
            r = await session.execute(text("SELECT COUNT(*) FROM video_clips WHERE is_unusable = true"))
            total_ng = r.scalar() or 0
        except Exception:
            await session.rollback()

        try:
            r = await session.execute(text("SELECT COUNT(*) FROM widget_clip_assignments"))
            total_brand = r.scalar() or 0
        except Exception:
            await session.rollback()

        try:
            r = await session.execute(text("SELECT COUNT(*) FROM clip_feedback"))
            total_feedback = r.scalar() or 0
        except Exception:
            await session.rollback()

        try:
            r = await session.execute(text("SELECT COUNT(*) FROM video_phases"))
            total_phases = r.scalar() or 0
            r2 = await session.execute(text("SELECT COUNT(DISTINCT video_id) FROM video_phases"))
            total_videos = r2.scalar() or 0
        except Exception:
            await session.rollback()

        try:
            r = await session.execute(text("SELECT COUNT(*) FROM ai_clip_jobs WHERE config->>'type' = 'regenerate_from_source' AND config->>'regen_grade' IS NOT NULL AND config->>'regen_grade' != ''"))
            total_regen_grades = r.scalar() or 0
            r2 = await session.execute(text("SELECT COUNT(*) FROM ai_clip_jobs WHERE config->>'type' = 'regenerate_from_source' AND config->>'regen_grade' = 'ok'"))
            total_regen_ok = r2.scalar() or 0
            r3 = await session.execute(text("SELECT COUNT(*) FROM ai_clip_jobs WHERE config->>'type' = 'regenerate_from_source' AND config->>'regen_grade' = 'ng'"))
            total_regen_ng = r3.scalar() or 0
        except Exception:
            await session.rollback()

        # 直近の学習履歴
        history_rows = []
        try:
            history_result = await session.execute(text("""
                SELECT run_id, target, status, started_at, completed_at,
                       metrics
                FROM ml_training_runs
                ORDER BY started_at DESC
                LIMIT 10
            """))
            history_rows = history_result.fetchall()
        except Exception:
            await session.rollback()

        # 日別の採点トレンド（直近14日）
        trend_rows = []
        try:
            trend_result = await session.execute(text("""
                SELECT 
                    DATE(updated_at) as review_date,
                    COUNT(*) as review_count
                FROM video_phases
                WHERE user_rating > 0 AND updated_at > NOW() - INTERVAL '14 days'
                GROUP BY DATE(updated_at)
                ORDER BY review_date
            """))
            trend_rows = trend_result.fetchall()
        except Exception:
            await session.rollback()

    # 学習履歴のフォーマット
    training_history = []
    for r in history_rows:
        metrics = r[5]
        if isinstance(metrics, str):
            try:
                import json
                metrics = json.loads(metrics)
            except Exception:
                metrics = {}
        training_history.append({
            "run_id": r[0],
            "target": r[1],
            "status": r[2],
            "started_at": str(r[3]) if r[3] else None,
            "completed_at": str(r[4]) if r[4] else None,
            "metrics": metrics or {},
        })

    # 採点トレンド
    review_trend = [
        {"date": str(r[0]), "count": r[1]}
        for r in trend_rows
    ]

    # All signal values are already set above from individual queries

    return {
        "data_signals": {
            "total_ratings": total_ratings,
            "total_ng_judgments": total_ng,
            "total_brand_assignments": total_brand,
            "total_clip_feedback": total_feedback,
            "total_phases": total_phases,
            "total_videos": total_videos,
            "coverage_rate": round(total_ratings / max(total_phases, 1) * 100, 1),
            "total_regen_grades": total_regen_grades,
            "total_regen_ok": total_regen_ok,
            "total_regen_ng": total_regen_ng,
        },
        "signal_quality": {
            "rating_diversity": "good" if total_ratings > 100 else "needs_more",
            "ng_diversity": "good" if total_ng > 50 else "needs_more",
            "brand_signal_strength": "strong" if total_brand > 200 else "moderate" if total_brand > 50 else "weak",
            "regen_grade_strength": "strong" if total_regen_grades > 50 else "moderate" if total_regen_grades > 10 else "weak",
        },
        "training_history": training_history,
        "review_trend": review_trend,
        "recommendations": _generate_learning_recommendations(
            total_ratings, total_ng, total_brand, total_phases
        ),
    }


def _generate_learning_recommendations(total_ratings, total_ng, total_brand, total_phases):
    """学習改善のための推奨アクションを生成"""
    recommendations = []

    coverage = total_ratings / max(total_phases, 1) * 100
    if coverage < 10:
        recommendations.append({
            "priority": "high",
            "action": "フィードバックタブでの採点を増やす",
            "reason": f"レビューカバレッジが{coverage:.1f}%（推奨: 30%以上）",
            "impact": "モデル精度が大幅に向上",
        })
    elif coverage < 30:
        recommendations.append({
            "priority": "medium",
            "action": "フィードバックタブでの採点を継続",
            "reason": f"レビューカバレッジが{coverage:.1f}%（推奨: 30%以上）",
            "impact": "モデル精度が向上",
        })

    if total_ng < 100:
        recommendations.append({
            "priority": "medium",
            "action": "クリップDBでのNG判定を増やす",
            "reason": f"NG判定が{total_ng}件（推奨: 100件以上）",
            "impact": "NGパターンの学習精度が向上",
        })

    if total_brand < 200:
        recommendations.append({
            "priority": "medium",
            "action": "クリップDBでのブランド割当を増やす",
            "reason": f"ブランド割当が{total_brand}件（推奨: 200件以上）",
            "impact": "成功パターンの学習精度が向上",
        })

    if not recommendations:
        recommendations.append({
            "priority": "low",
            "action": "データ量は十分です。定期的な再学習を推奨",
            "reason": "全指標が推奨値を超えています",
            "impact": "継続的な精度向上",
        })

    return recommendations
