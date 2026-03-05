"""
train.py  –  LCJ AI 学習パイプライン
=====================================
generate_dataset.py が出力した train.jsonl を読み込み、
「このeventは売れるか」を予測するモデルを学習する。

モデル:
  1. LightGBM (メイン)
  2. LogisticRegression (ベースライン)

ラベル:
  label_strong_window = 1  →  ±150s窓にstrongモーメントがある
  (= クリック+注文が同時にスパイクした「売れた瞬間」の近く)

特徴量:
  - duration (フェーズの長さ)
  - cta_score (CTA強度 1-5)
  - gmv, order_count, viewer_count, like_count, comment_count
  - product_clicks, conversion_rate, gpm, importance_score
  - product_match (商品言及あり)
  - event_type (カテゴリ → one-hot)
  - audio features (あれば)

出力:
  - model_lgbm.pkl (LightGBMモデル)
  - model_lr.pkl (LogisticRegressionモデル)
  - feature_names.json (特徴量名リスト)
  - eval_metrics.json (評価指標)

使い方:
  python train.py --input /tmp/train.jsonl --output-dir /tmp/models/
"""

import argparse
import json
import os
import sys
import pickle
import warnings
warnings.filterwarnings("ignore")

import numpy as np

# ── Feature engineering ──

# Numeric features (直接使用)
NUMERIC_FEATURES = [
    "duration",
    "cta_score",
    "gmv",
    "order_count",
    "viewer_count",
    "like_count",
    "comment_count",
    "share_count",
    "new_followers",
    "product_clicks",
    "conversion_rate",
    "gpm",
    "importance_score",
]

# Boolean features
BOOL_FEATURES = [
    "product_match",
]

# Audio features (optional)
AUDIO_FEATURES = [
    "audio_energy_mean",
    "audio_tempo",
    "audio_pitch_mean",
    "audio_speech_rate",
]

# Event type categories for one-hot encoding
KNOWN_EVENT_TYPES = [
    "HOOK", "GREETING", "INTRO", "DEMO", "PRICE",
    "CTA", "OBJECTION", "SOCIAL_PROOF", "URGENCY",
    "EMPATHY", "CHAT", "TRANSITION", "CLOSING", "UNKNOWN",
]

LABEL_COL = "label_strong_window"


def load_jsonl(path):
    """Load JSONL file into list of dicts."""
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def extract_features(records):
    """Convert records to feature matrix X and label vector y."""
    feature_names = []

    # Build feature name list
    feature_names.extend(NUMERIC_FEATURES)
    feature_names.extend(BOOL_FEATURES)
    feature_names.extend(AUDIO_FEATURES)
    feature_names.extend([f"event_{et}" for et in KNOWN_EVENT_TYPES])

    X = np.zeros((len(records), len(feature_names)), dtype=np.float32)
    y = np.zeros(len(records), dtype=np.int32)

    for i, rec in enumerate(records):
        col = 0

        # Numeric features
        for feat in NUMERIC_FEATURES:
            val = rec.get(feat)
            X[i, col] = float(val) if val is not None else 0.0
            col += 1

        # Boolean features
        for feat in BOOL_FEATURES:
            X[i, col] = 1.0 if rec.get(feat) else 0.0
            col += 1

        # Audio features
        for feat in AUDIO_FEATURES:
            val = rec.get(feat)
            X[i, col] = float(val) if val is not None else 0.0
            col += 1

        # Event type one-hot
        event_type = rec.get("event_type", "UNKNOWN")
        for et in KNOWN_EVENT_TYPES:
            X[i, col] = 1.0 if event_type == et else 0.0
            col += 1

        # Label
        y[i] = int(rec.get(LABEL_COL, 0))

    return X, y, feature_names


def train_and_evaluate(X, y, feature_names, output_dir):
    """Train models and evaluate."""
    from sklearn.model_selection import StratifiedKFold, cross_val_predict
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import (
        roc_auc_score, precision_score, recall_score,
        f1_score, classification_report, confusion_matrix
    )
    from sklearn.preprocessing import StandardScaler

    try:
        import lightgbm as lgb
        has_lgbm = True
    except ImportError:
        print("[train] LightGBM not installed. Using only LogisticRegression.")
        has_lgbm = False

    os.makedirs(output_dir, exist_ok=True)
    metrics = {}

    n_positive = int(y.sum())
    n_total = len(y)
    print(f"\n[train] Dataset: {n_total} samples, {n_positive} positive ({n_positive/max(n_total,1)*100:.1f}%)")

    if n_positive < 3 or (n_total - n_positive) < 3:
        print("[train] WARNING: Too few samples for meaningful training.")
        print("[train] Saving feature_names and skipping model training.")
        with open(os.path.join(output_dir, "feature_names.json"), "w") as f:
            json.dump(feature_names, f, indent=2)
        metrics["status"] = "insufficient_data"
        metrics["n_total"] = n_total
        metrics["n_positive"] = n_positive
        with open(os.path.join(output_dir, "eval_metrics.json"), "w") as f:
            json.dump(metrics, f, indent=2)
        return metrics

    # ── Cross-validation setup ──
    n_splits = min(5, n_positive, n_total - n_positive)
    if n_splits < 2:
        n_splits = 2
    cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)

    # ── 1. LogisticRegression (baseline) ──
    print("\n[train] Training LogisticRegression (baseline)...")
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    lr = LogisticRegression(
        class_weight="balanced",
        max_iter=1000,
        random_state=42,
        C=1.0,
    )

    try:
        y_pred_lr = cross_val_predict(lr, X_scaled, y, cv=cv, method="predict_proba")[:, 1]
        auc_lr = roc_auc_score(y, y_pred_lr)
        y_pred_binary_lr = (y_pred_lr >= 0.5).astype(int)
        prec_lr = precision_score(y, y_pred_binary_lr, zero_division=0)
        rec_lr = recall_score(y, y_pred_binary_lr, zero_division=0)
        f1_lr = f1_score(y, y_pred_binary_lr, zero_division=0)

        print(f"  AUC:       {auc_lr:.4f}")
        print(f"  Precision: {prec_lr:.4f}")
        print(f"  Recall:    {rec_lr:.4f}")
        print(f"  F1:        {f1_lr:.4f}")

        metrics["lr"] = {
            "auc": round(auc_lr, 4),
            "precision": round(prec_lr, 4),
            "recall": round(rec_lr, 4),
            "f1": round(f1_lr, 4),
        }
    except Exception as e:
        print(f"  LR cross-val failed: {e}")
        metrics["lr"] = {"error": str(e)}

    # Train final LR model on all data
    lr.fit(X_scaled, y)
    with open(os.path.join(output_dir, "model_lr.pkl"), "wb") as f:
        pickle.dump({"model": lr, "scaler": scaler}, f)
    print("  Saved: model_lr.pkl")

    # ── 2. LightGBM (main) ──
    if has_lgbm:
        print("\n[train] Training LightGBM (main)...")

        # Compute scale_pos_weight for imbalanced data
        n_neg = n_total - n_positive
        scale_pos_weight = n_neg / max(n_positive, 1)

        lgbm_params = {
            "objective": "binary",
            "metric": "auc",
            "verbosity": -1,
            "n_estimators": 200,
            "max_depth": 4,
            "learning_rate": 0.05,
            "num_leaves": 15,
            "min_child_samples": max(3, n_positive // 5),
            "scale_pos_weight": scale_pos_weight,
            "random_state": 42,
            "n_jobs": -1,
        }

        lgbm_model = lgb.LGBMClassifier(**lgbm_params)

        try:
            y_pred_lgbm = cross_val_predict(lgbm_model, X, y, cv=cv, method="predict_proba")[:, 1]
            auc_lgbm = roc_auc_score(y, y_pred_lgbm)
            y_pred_binary_lgbm = (y_pred_lgbm >= 0.5).astype(int)
            prec_lgbm = precision_score(y, y_pred_binary_lgbm, zero_division=0)
            rec_lgbm = recall_score(y, y_pred_binary_lgbm, zero_division=0)
            f1_lgbm = f1_score(y, y_pred_binary_lgbm, zero_division=0)

            print(f"  AUC:       {auc_lgbm:.4f}")
            print(f"  Precision: {prec_lgbm:.4f}")
            print(f"  Recall:    {rec_lgbm:.4f}")
            print(f"  F1:        {f1_lgbm:.4f}")

            metrics["lgbm"] = {
                "auc": round(auc_lgbm, 4),
                "precision": round(prec_lgbm, 4),
                "recall": round(rec_lgbm, 4),
                "f1": round(f1_lgbm, 4),
            }

            # Feature importance
            lgbm_model.fit(X, y)
            importances = lgbm_model.feature_importances_
            feat_imp = sorted(
                zip(feature_names, importances.tolist()),
                key=lambda x: x[1], reverse=True
            )
            print("\n  Top 10 features:")
            for fname, imp in feat_imp[:10]:
                print(f"    {fname:30s} {imp:6.0f}")

            metrics["lgbm"]["feature_importance"] = [
                {"feature": fn, "importance": imp} for fn, imp in feat_imp[:20]
            ]

        except Exception as e:
            print(f"  LightGBM cross-val failed: {e}")
            lgbm_model.fit(X, y)
            metrics["lgbm"] = {"error": str(e), "trained": True}

        # Save final model
        with open(os.path.join(output_dir, "model_lgbm.pkl"), "wb") as f:
            pickle.dump(lgbm_model, f)
        print("  Saved: model_lgbm.pkl")

    # ── Save metadata ──
    with open(os.path.join(output_dir, "feature_names.json"), "w") as f:
        json.dump(feature_names, f, indent=2)

    metrics["status"] = "success"
    metrics["n_total"] = n_total
    metrics["n_positive"] = n_positive
    metrics["positive_rate"] = round(n_positive / max(n_total, 1), 4)
    metrics["n_features"] = len(feature_names)

    # Determine best model
    best_model = "lr"
    best_auc = metrics.get("lr", {}).get("auc", 0)
    if has_lgbm and metrics.get("lgbm", {}).get("auc", 0) > best_auc:
        best_model = "lgbm"
        best_auc = metrics["lgbm"]["auc"]
    metrics["best_model"] = best_model
    metrics["best_auc"] = best_auc

    with open(os.path.join(output_dir, "eval_metrics.json"), "w") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)

    print(f"\n[train] Best model: {best_model} (AUC={best_auc:.4f})")
    print(f"[train] All outputs saved to: {output_dir}")

    return metrics


def main():
    parser = argparse.ArgumentParser(description="Train LCJ AI prediction model")
    parser.add_argument("--input", "-i", default="/tmp/train.jsonl",
                        help="Input JSONL dataset file")
    parser.add_argument("--output-dir", "-o", default="/tmp/models/",
                        help="Output directory for models and metrics")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"[train] ERROR: Input file not found: {args.input}")
        sys.exit(1)

    print(f"[train] Loading dataset from: {args.input}")
    records = load_jsonl(args.input)
    print(f"[train] Loaded {len(records)} records")

    if len(records) < 10:
        print("[train] WARNING: Very few records. Model quality will be limited.")

    X, y, feature_names = extract_features(records)
    print(f"[train] Feature matrix: {X.shape}")

    metrics = train_and_evaluate(X, y, feature_names, args.output_dir)

    return 0 if metrics.get("status") == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
