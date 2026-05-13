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
    import subprocess

    async def _run_training_on_gpu():
        """Background task to trigger training on GPU VM."""
        try:
            ssh_cmd = (
                'ssh -i /home/azureuser/.ssh/workervm_key.pem '
                '-o StrictHostKeyChecking=no -o ConnectTimeout=10 '
                'azureuser@52.185.188.19 '
                '"cd /opt/aitherhub && '
                'sudo .venv/bin/python worker/batch/generate_dataset.py '
                '--output-dir /tmp/datasets && '
                'sudo .venv/bin/python worker/batch/train.py '
                '--input-dir /tmp/datasets --output-dir /tmp/models '
                '--save-to-db"'
            )
            # Non-blocking - just fire and forget
            subprocess.Popen(
                ssh_cmd, shell=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except Exception as e:
            logger.error(f"Failed to trigger GPU training: {e}")

    # Fire and forget - don't await
    asyncio.create_task(_run_training_on_gpu())

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

    # Fetch phases with their features
    conditions = ["vp.user_rating IS NULL OR vp.user_rating = 0"]  # 未レビュー優先
    params = {"limit": limit}
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
            v.title as video_title,
            v.created_at as video_created_at
        FROM video_phases vp
        LEFT JOIN videos v ON v.video_id = vp.video_id
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
    try:
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
            return {
                "phases": [],
                "stats": {
                    "total_uncertain": 0,
                    "total_scanned": len(rows),
                    "error": "ML scorer not available (no model loaded)",
                },
            }

    except Exception as e:
        logger.error(f"[active-learning] Error: {e}")
        return {
            "phases": [],
            "stats": {
                "total_uncertain": 0,
                "total_scanned": len(rows),
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
