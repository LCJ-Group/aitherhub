"""
train.py  –  LCJ AI 学習パイプライン v2
========================================
generate_dataset.py v2 が出力した train_click.jsonl / train_order.jsonl を読み込み、
「このeventは売れるか（click_spike / order_spike）」を予測するモデルを学習する。

モデル:
  1. LightGBM (メイン)
  2. LogisticRegression (ベースライン)

ラベル:
  y_click = 1 : event区間が click_spike 窓に重なる
  y_order = 1 : event区間が order_spike 窓に重なる

特徴量 (情報リーク防止 - GMV/注文数/クリック数は使わない):
  構造系: event_duration, event_position_min, event_position_pct, tag_count
  CTA/AI系: cta_score, importance_score
  テキスト系: text_length, has_number, exclamation_count
  キーワード系: kw_price, kw_discount, kw_urgency, kw_cta, kw_quantity,
                kw_comparison, kw_quality, kw_number
  商品系: product_match, product_match_top3, matched_product_count
  イベント分類: event_type (one-hot)

出力:
  - model_{target}_lgbm.pkl
  - model_{target}_lr.pkl
  - feature_names.json
  - eval_metrics.json

使い方:
  python train.py --input-dir /tmp/datasets --output-dir /tmp/models/
  python train.py --input /tmp/datasets/train_click.jsonl --target click --output-dir /tmp/models/
"""

import argparse
import json
import os
import sys
import pickle
import warnings
warnings.filterwarnings("ignore")

import numpy as np

# ── Feature definitions (NO information leak) ──

# Numeric features (safe: no GMV/order/click/viewer counts)
NUMERIC_FEATURES = [
    "event_duration",
    "event_position_min",
    "event_position_pct",
    "tag_count",
    "cta_score",
    "importance_score",
    "text_length",
    "has_number",
    "exclamation_count",
]

# Keyword flags (binary)
KEYWORD_FEATURES = [
    "kw_price",
    "kw_discount",
    "kw_urgency",
    "kw_cta",
    "kw_quantity",
    "kw_comparison",
    "kw_quality",
    "kw_number",
]

# Product match features
PRODUCT_FEATURES = [
    "product_match",
    "product_match_top3",
    "matched_product_count",
]

# Event type categories for one-hot encoding
KNOWN_EVENT_TYPES = [
    "HOOK", "GREETING", "INTRO", "DEMONSTRATION", "PRICE",
    "CTA", "OBJECTION", "SOCIAL_PROOF", "URGENCY",
    "EMPATHY", "EDUCATION", "CHAT", "TRANSITION", "CLOSING", "UNKNOWN",
]


def load_jsonl(path):
    """Load JSONL file into list of dicts."""
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def extract_features(records, target="click"):
    """
    Convert records to feature matrix X, label vector y, and sample weights w.

    Args:
        records: list of dicts from JSONL
        target: "click" or "order" - determines which y label to use
    """
    y_key = f"y_{target}"
    w_key = f"weight_{target}"

    # Build feature name list
    feature_names = []
    feature_names.extend(NUMERIC_FEATURES)
    feature_names.extend(KEYWORD_FEATURES)
    feature_names.extend(PRODUCT_FEATURES)
    feature_names.extend([f"event_{et}" for et in KNOWN_EVENT_TYPES])

    X = np.zeros((len(records), len(feature_names)), dtype=np.float32)
    y = np.zeros(len(records), dtype=np.int32)
    w = np.ones(len(records), dtype=np.float32)

    for i, rec in enumerate(records):
        col = 0

        # Numeric features
        for feat in NUMERIC_FEATURES:
            val = rec.get(feat)
            X[i, col] = float(val) if val is not None else 0.0
            col += 1

        # Keyword flags
        for feat in KEYWORD_FEATURES:
            X[i, col] = 1.0 if rec.get(feat) else 0.0
            col += 1

        # Product features
        for feat in PRODUCT_FEATURES:
            val = rec.get(feat)
            X[i, col] = float(val) if val is not None else 0.0
            col += 1

        # Event type one-hot
        event_type = rec.get("event_type", "UNKNOWN")
        for et in KNOWN_EVENT_TYPES:
            X[i, col] = 1.0 if event_type == et else 0.0
            col += 1

        # Label
        y[i] = int(rec.get(y_key, 0))

        # Sample weight (distance-decay for positives)
        sample_w = rec.get("sample_weight", 1.0)
        if sample_w and sample_w > 0:
            w[i] = float(sample_w)
        else:
            w[i] = 1.0

    return X, y, w, feature_names


def precision_at_k(y_true, y_scores, k=5):
    """Compute Precision@K: among top-K scored events, how many are positive."""
    if len(y_true) <= k:
        k = len(y_true)
    top_k_idx = np.argsort(y_scores)[::-1][:k]
    return float(np.sum(y_true[top_k_idx])) / k


def train_and_evaluate(X, y, w, feature_names, target, output_dir):
    """Train models and evaluate with Precision@K and AUC."""
    from sklearn.model_selection import StratifiedKFold, cross_val_predict
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import (
        roc_auc_score, precision_score, recall_score,
        f1_score, classification_report
    )
    from sklearn.preprocessing import StandardScaler

    try:
        import lightgbm as lgb
        has_lgbm = True
    except ImportError:
        print("[train] LightGBM not installed. Using only LogisticRegression.")
        has_lgbm = False

    os.makedirs(output_dir, exist_ok=True)
    metrics = {"target": target}

    n_positive = int(y.sum())
    n_total = len(y)
    print(f"\n[train] Target: {target}")
    print(f"[train] Dataset: {n_total} samples, {n_positive} positive ({n_positive/max(n_total,1)*100:.1f}%)")

    if n_positive < 3 or (n_total - n_positive) < 3:
        print("[train] WARNING: Too few samples for meaningful training.")
        with open(os.path.join(output_dir, "feature_names.json"), "w") as f:
            json.dump(feature_names, f, indent=2)
        metrics["status"] = "insufficient_data"
        metrics["n_total"] = n_total
        metrics["n_positive"] = n_positive
        with open(os.path.join(output_dir, f"eval_metrics_{target}.json"), "w") as f:
            json.dump(metrics, f, indent=2)
        return metrics

    # ── Cross-validation setup ──
    n_splits = min(5, n_positive, n_total - n_positive)
    if n_splits < 2:
        n_splits = 2
    cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)

    # ── 1. LogisticRegression (baseline) ──
    print(f"\n[train] Training LogisticRegression (baseline) for {target}...")
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
        p_at_5_lr = precision_at_k(y, y_pred_lr, k=5)

        print(f"  AUC:          {auc_lr:.4f}")
        print(f"  Precision:    {prec_lr:.4f}")
        print(f"  Recall:       {rec_lr:.4f}")
        print(f"  F1:           {f1_lr:.4f}")
        print(f"  Precision@5:  {p_at_5_lr:.4f}")

        metrics["lr"] = {
            "auc": round(auc_lr, 4),
            "precision": round(prec_lr, 4),
            "recall": round(rec_lr, 4),
            "f1": round(f1_lr, 4),
            "precision_at_5": round(p_at_5_lr, 4),
        }
    except Exception as e:
        print(f"  LR cross-val failed: {e}")
        metrics["lr"] = {"error": str(e)}

    # Train final LR model on all data
    lr.fit(X_scaled, y, sample_weight=w)
    with open(os.path.join(output_dir, f"model_{target}_lr.pkl"), "wb") as f:
        pickle.dump({"model": lr, "scaler": scaler, "target": target}, f)
    print(f"  Saved: model_{target}_lr.pkl")

    # ── 2. LightGBM (main) ──
    if has_lgbm:
        print(f"\n[train] Training LightGBM (main) for {target}...")

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
            y_pred_lgbm = cross_val_predict(
                lgbm_model, X, y, cv=cv, method="predict_proba",
                fit_params={"sample_weight": w}
            )[:, 1]
            auc_lgbm = roc_auc_score(y, y_pred_lgbm)
            y_pred_binary_lgbm = (y_pred_lgbm >= 0.5).astype(int)
            prec_lgbm = precision_score(y, y_pred_binary_lgbm, zero_division=0)
            rec_lgbm = recall_score(y, y_pred_binary_lgbm, zero_division=0)
            f1_lgbm = f1_score(y, y_pred_binary_lgbm, zero_division=0)
            p_at_5_lgbm = precision_at_k(y, y_pred_lgbm, k=5)

            print(f"  AUC:          {auc_lgbm:.4f}")
            print(f"  Precision:    {prec_lgbm:.4f}")
            print(f"  Recall:       {rec_lgbm:.4f}")
            print(f"  F1:           {f1_lgbm:.4f}")
            print(f"  Precision@5:  {p_at_5_lgbm:.4f}")

            metrics["lgbm"] = {
                "auc": round(auc_lgbm, 4),
                "precision": round(prec_lgbm, 4),
                "recall": round(rec_lgbm, 4),
                "f1": round(f1_lgbm, 4),
                "precision_at_5": round(p_at_5_lgbm, 4),
            }

            # Feature importance
            lgbm_model.fit(X, y, sample_weight=w)
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
            lgbm_model.fit(X, y, sample_weight=w)
            metrics["lgbm"] = {"error": str(e), "trained": True}

        # Save final model
        with open(os.path.join(output_dir, f"model_{target}_lgbm.pkl"), "wb") as f:
            pickle.dump({"model": lgbm_model, "target": target}, f)
        print(f"  Saved: model_{target}_lgbm.pkl")

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

    with open(os.path.join(output_dir, f"eval_metrics_{target}.json"), "w") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)

    print(f"\n[train] Best model for {target}: {best_model} (AUC={best_auc:.4f})")
    return metrics


def main():
    parser = argparse.ArgumentParser(description="Train LCJ AI prediction model v2")
    parser.add_argument("--input", "-i", default=None,
                        help="Input JSONL dataset file (single target)")
    parser.add_argument("--input-dir", default=None,
                        help="Input directory containing train_click.jsonl and train_order.jsonl")
    parser.add_argument("--target", "-t", default="click",
                        choices=["click", "order"],
                        help="Target label (click or order)")
    parser.add_argument("--output-dir", "-o", default="/tmp/models/",
                        help="Output directory for models and metrics")
    args = parser.parse_args()

    all_metrics = {}

    if args.input_dir:
        # Train both click and order models
        for target in ["click", "order"]:
            input_path = os.path.join(args.input_dir, f"train_{target}.jsonl")
            if not os.path.exists(input_path):
                print(f"[train] Skipping {target}: {input_path} not found")
                continue

            print(f"\n{'='*60}")
            print(f"[train] Loading {target} dataset from: {input_path}")
            records = load_jsonl(input_path)
            print(f"[train] Loaded {len(records)} records")

            if len(records) < 10:
                print(f"[train] WARNING: Very few records for {target}.")

            X, y, w, feature_names = extract_features(records, target=target)
            print(f"[train] Feature matrix: {X.shape}")

            metrics = train_and_evaluate(X, y, w, feature_names, target, args.output_dir)
            all_metrics[target] = metrics

    elif args.input:
        # Train single target
        if not os.path.exists(args.input):
            print(f"[train] ERROR: Input file not found: {args.input}")
            sys.exit(1)

        print(f"[train] Loading dataset from: {args.input}")
        records = load_jsonl(args.input)
        print(f"[train] Loaded {len(records)} records")

        X, y, w, feature_names = extract_features(records, target=args.target)
        print(f"[train] Feature matrix: {X.shape}")

        metrics = train_and_evaluate(X, y, w, feature_names, args.target, args.output_dir)
        all_metrics[args.target] = metrics

    else:
        print("[train] ERROR: Specify --input or --input-dir")
        sys.exit(1)

    # Summary
    print(f"\n{'='*60}")
    print("[train] SUMMARY")
    for target, m in all_metrics.items():
        status = m.get("status", "unknown")
        best = m.get("best_model", "?")
        auc = m.get("best_auc", 0)
        p5 = m.get(best, {}).get("precision_at_5", "?")
        print(f"  {target}: status={status}, best={best}, AUC={auc:.4f}, P@5={p5}")

    print(f"\n[train] All outputs saved to: {args.output_dir}")

    success = all(m.get("status") == "success" for m in all_metrics.values())
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
