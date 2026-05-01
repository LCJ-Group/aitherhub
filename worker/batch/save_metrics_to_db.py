"""
save_metrics_to_db.py – 学習完了後にメトリクスをDBに保存
=========================================================
train.py完了後に実行:
  python save_metrics_to_db.py --models-dir /tmp/models

manifest.json と eval_metrics_{target}.json を読み込み、
ml_training_runs テーブルに INSERT する。
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

import psycopg2


def get_db_url():
    """Get DATABASE_URL from env or .env file."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.startswith("DATABASE_URL="):
                    return line.strip().split("=", 1)[1].strip('"').strip("'")
    # Try /opt/aitherhub/.env
    alt_env = "/opt/aitherhub/.env"
    if os.path.exists(alt_env):
        with open(alt_env) as f:
            for line in f:
                if line.startswith("DATABASE_URL="):
                    return line.strip().split("=", 1)[1].strip('"').strip("'")
    return None


def save_to_db(models_dir: str, run_id_prefix: str = None):
    """Read manifest.json and eval_metrics, save to ml_training_runs."""
    db_url = get_db_url()
    if not db_url:
        print("[save_metrics] ERROR: DATABASE_URL not found")
        sys.exit(1)

    manifest_path = os.path.join(models_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        print(f"[save_metrics] ERROR: manifest.json not found at {manifest_path}")
        sys.exit(1)

    with open(manifest_path) as f:
        manifest = json.load(f)

    model_version = manifest.get("model_version", "unknown")
    trained_at = manifest.get("trained_at", datetime.now(timezone.utc).isoformat())

    if not run_id_prefix:
        run_id_prefix = f"auto_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    for target, model_info in manifest.get("models", {}).items():
        # Read eval_metrics for feature importance
        eval_path = os.path.join(models_dir, f"eval_metrics_{target}.json")
        feature_importance = None
        if os.path.exists(eval_path):
            with open(eval_path) as f:
                eval_data = json.load(f)
                fi_raw = eval_data.get("feature_importance", [])
                if fi_raw:
                    # Convert list to dict for frontend
                    feature_importance = {item["feature"]: item["importance"] for item in fi_raw}

        adopted = model_info.get("adopted_metrics", {})
        auc_score = adopted.get("auc_mean")
        precision_at_5 = adopted.get("precision_at_5_mean")
        f1_score = adopted.get("f1_mean")

        dataset_info = model_info.get("dataset", {})
        dataset_size = model_info.get("n_total", 0)
        positive_count = model_info.get("n_positive", 0)
        negative_count = dataset_size - positive_count

        # Model file paths
        files = model_info.get("files", {})
        best_model = model_info.get("best_model", "lr")
        model_path = files.get(best_model, files.get("lr", ""))
        if model_path:
            model_path = os.path.join(models_dir, model_path)

        run_id = f"{run_id_prefix}_{target}"

        # Config info
        config = {
            "commit_hash": manifest.get("commit_hash"),
            "n_features": manifest.get("n_features"),
            "best_model_type": best_model,
            "holdout_metrics": model_info.get("holdout_metrics"),
            "dataset_hash": dataset_info.get("dataset_hash"),
        }

        # Check if run_id already exists
        cur.execute("SELECT id FROM ml_training_runs WHERE run_id = %s", (run_id,))
        existing = cur.fetchone()

        if existing:
            # Update existing record
            cur.execute("""
                UPDATE ml_training_runs SET
                    model_version = %s,
                    completed_at = %s,
                    status = 'completed',
                    dataset_size = %s,
                    positive_count = %s,
                    negative_count = %s,
                    auc_score = %s,
                    precision_at_5 = %s,
                    f1_score = %s,
                    feature_importance = %s,
                    config = %s,
                    model_path = %s
                WHERE run_id = %s
            """, (
                model_version, trained_at, dataset_size, positive_count,
                negative_count, auc_score, precision_at_5, f1_score,
                json.dumps(feature_importance) if feature_importance else None,
                json.dumps(config),
                model_path, run_id
            ))
            print(f"[save_metrics] Updated {target}: AUC={auc_score:.4f}, P@5={precision_at_5:.4f}")
        else:
            # Insert new record
            cur.execute("""
                INSERT INTO ml_training_runs 
                    (run_id, target, model_version, started_at, completed_at, status,
                     dataset_size, positive_count, negative_count,
                     auc_score, precision_at_5, f1_score,
                     feature_importance, config, model_path)
                VALUES (%s, %s, %s, %s, %s, 'completed', %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                run_id, target, model_version, trained_at, trained_at,
                dataset_size, positive_count, negative_count,
                auc_score, precision_at_5, f1_score,
                json.dumps(feature_importance) if feature_importance else None,
                json.dumps(config),
                model_path
            ))
            print(f"[save_metrics] Inserted {target}: AUC={auc_score:.4f}, P@5={precision_at_5:.4f}")

    conn.commit()
    cur.close()
    conn.close()
    print(f"[save_metrics] Done. Saved metrics for {list(manifest.get('models', {}).keys())}")


def main():
    parser = argparse.ArgumentParser(description="Save training metrics to DB")
    parser.add_argument("--models-dir", "-d", default="/tmp/models",
                        help="Directory containing manifest.json and eval_metrics")
    parser.add_argument("--run-id", default=None,
                        help="Run ID prefix (default: auto_YYYYMMDD_HHMMSS)")
    args = parser.parse_args()

    if not os.path.exists(args.models_dir):
        print(f"[save_metrics] ERROR: Models directory not found: {args.models_dir}")
        sys.exit(1)

    save_to_db(args.models_dir, args.run_id)


if __name__ == "__main__":
    main()
