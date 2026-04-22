"""
train.py  –  LCJ AI 学習パイプライン v7
========================================
変更点 (v7 - Level 2 品質特徴量):
  - フレーム品質特徴量5個追加 (fq_blur_score, fq_brightness_mean, fq_brightness_std,
    fq_color_saturation, fq_scene_change_count)
  - 音声品質特徴量7個追加 (af_energy_mean, af_energy_max, af_pitch_mean, af_pitch_std,
    af_speech_rate, af_silence_ratio, af_energy_trend)
  - 合計 76 + 12 = 88 特徴量
  - generate_dataset.py v7 との整合性確保

変更点 (v6):
  - NG feedback 特徴量追加 (is_ng, has_ng_feedback, ng_reason_tag_count)
  - unusable_reason one-hot (6特徴量) 追加
  - generate_dataset.py v6 との整合性確保

変更点 (v5):
  - human_sales_tags one-hot (行動8 + 販売心理14 = 22特徴量) 追加
  - user_rating, has_human_review, human_tag_count 追加
  - comment_length + comment_kw_* (6特徴量) 追加
  - generate_dataset.py v3 との整合性確保

変更点 (v4):
  - Repeated GroupKFold (5fold × 3回 = 15評価) → mean±std
  - GroupStratified 自作 (greedy均等分割で正例率を均等化)
  - Holdout (直近20%の動画) → 最終判定
  - Precision@5, @10, AUC, F1 全て mean±std
  - Foldごとのメトリクス保存
  - manifest.json 完全版 (dataset_version, dataset_hash, features_used, label_def, fold_metrics)
  - GroupKFold = 採用値 (realistic), StratifiedKFold = 参考値 (optimistic)

使い方:
  python train.py --input-dir /tmp/datasets --output-dir /tmp/models/
"""

import argparse
import json
import os
import sys
import pickle
import warnings
import hashlib
import subprocess
from datetime import datetime, timezone
warnings.filterwarnings("ignore")

import numpy as np

# ── Feature definitions (NO information leak) ──

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
    # ── Human review features (v5) ──
    "user_rating",
    "has_human_review",
    "human_tag_count",
    "comment_length",
]

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

PRODUCT_FEATURES = [
    "product_match",
    "product_match_top3",
    "matched_product_count",
]

# ── Human sales tag one-hot features (v5) ──
BEHAVIOR_TAGS = [
    "HOOK", "CHAT", "PREP", "PHONE_OP",
    "LONG_GREET", "COMMENT_READ", "SILENCE", "PRICE_SHOW",
]
PSYCHOLOGY_TAGS = [
    "EMPATHY", "PROBLEM", "EDUCATION", "SOLUTION",
    "DEMONSTRATION", "COMPARISON", "PROOF", "TRUST", "SOCIAL_PROOF",
    "OBJECTION_HANDLING", "URGENCY", "LIMITED_OFFER", "BONUS", "CTA",
]
ALL_HUMAN_TAGS = BEHAVIOR_TAGS + PSYCHOLOGY_TAGS
HUMAN_TAG_FEATURES = [f"htag_{t}" for t in ALL_HUMAN_TAGS]

# ── Comment keyword features (v5) ──
COMMENT_KEYWORD_FEATURES = [
    "comment_kw_price",
    "comment_kw_cta",
    "comment_kw_positive",
    "comment_kw_negative",
    "comment_kw_emotion",
    "comment_kw_timing",
]

# ── NG feedback features (v6) ──
NG_NUMERIC_FEATURES = [
    "is_ng",
    "has_ng_feedback",
    "ng_reason_tag_count",
]
UNUSABLE_REASON_FEATURES = [
    "unusable_reason_irrelevant",
    "unusable_reason_too_short",
    "unusable_reason_too_long",
    "unusable_reason_no_product",
    "unusable_reason_audio_bad",
    "unusable_reason_low_quality",
]

# ── Quality features (v7) ──
FRAME_QUALITY_FEATURES = [
    "fq_blur_score",
    "fq_brightness_mean",
    "fq_brightness_std",
    "fq_color_saturation",
    "fq_scene_change_count",
]
AUDIO_QUALITY_FEATURES = [
    "af_energy_mean",
    "af_energy_max",
    "af_pitch_mean",
    "af_pitch_std",
    "af_speech_rate",
    "af_silence_ratio",
    "af_energy_trend",
]

KNOWN_EVENT_TYPES = [
    "HOOK", "GREETING", "INTRO", "DEMONSTRATION", "PRICE",
    "CTA", "OBJECTION", "SOCIAL_PROOF", "URGENCY",
    "EMPATHY", "EDUCATION", "CHAT", "TRANSITION", "CLOSING", "UNKNOWN",
]
MODEL_VERSION = 7
DATE_TAG = datetime.now().strftime("%Y%m%d")

# ── Label definition (for manifest traceability) ──
LABEL_DEFINITION = {
    "window_seconds": 150,
    "y_click": "event overlaps with click_spike moment ±150s",
    "y_order": "event overlaps with order_spike moment ±150s",
    "y_strong": "event overlaps with strong moment ±150s",
    "weight": "exp(-distance/60) decay from moment center",
    "sampling": "positive:negative = 1:3",
}


def get_git_commit():
    """Get current git commit hash."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def compute_dataset_hash(path):
    """Compute SHA-256 hash of a dataset file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


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
    Convert records to feature matrix X, label vector y, sample weights w,
    group IDs (video_id) for GroupKFold, and raw video_id strings.
    """
    y_key = f"y_{target}"

    # Build feature name list
    feature_names = []
    feature_names.extend(NUMERIC_FEATURES)
    feature_names.extend(KEYWORD_FEATURES)
    feature_names.extend(PRODUCT_FEATURES)
    feature_names.extend(HUMAN_TAG_FEATURES)
    feature_names.extend(COMMENT_KEYWORD_FEATURES)
    feature_names.extend(NG_NUMERIC_FEATURES)
    feature_names.extend(UNUSABLE_REASON_FEATURES)
    feature_names.extend(FRAME_QUALITY_FEATURES)
    feature_names.extend(AUDIO_QUALITY_FEATURES)
    feature_names.extend([f"event_{et}" for et in KNOWN_EVENT_TYPES])

    X = np.zeros((len(records), len(feature_names)), dtype=np.float32)
    y = np.zeros(len(records), dtype=np.int32)
    w = np.ones(len(records), dtype=np.float32)
    video_ids_raw = []  # raw video_id strings

    for i, rec in enumerate(records):
        col = 0

        # Numeric features (includes user_rating, has_human_review, human_tag_count, comment_length)
        for feat in NUMERIC_FEATURES:
            val = rec.get(feat)
            X[i, col] = float(val) if val is not None else 0.0
            col += 1

        # Keyword flags (from phase description)
        for feat in KEYWORD_FEATURES:
            X[i, col] = 1.0 if rec.get(feat) else 0.0
            col += 1

        # Product features
        for feat in PRODUCT_FEATURES:
            val = rec.get(feat)
            X[i, col] = float(val) if val is not None else 0.0
            col += 1

        # Human tag one-hot features (行動8 + 販売心理14 = 22)
        for feat in HUMAN_TAG_FEATURES:
            X[i, col] = 1.0 if rec.get(feat) else 0.0
            col += 1

        # Comment keyword features (6)
        for feat in COMMENT_KEYWORD_FEATURES:
            X[i, col] = 1.0 if rec.get(feat) else 0.0
            col += 1

        # NG feedback features (v6)
        for feat in NG_NUMERIC_FEATURES:
            val = rec.get(feat)
            X[i, col] = float(val) if val is not None else 0.0
            col += 1

        # Unusable reason one-hot (v6)
        for feat in UNUSABLE_REASON_FEATURES:
            X[i, col] = 1.0 if rec.get(feat) else 0.0
            col += 1

        # Frame quality features (v7)
        for feat in FRAME_QUALITY_FEATURES:
            val = rec.get(feat)
            X[i, col] = float(val) if val is not None else 0.0
            col += 1

        # Audio quality features (v7)
        for feat in AUDIO_QUALITY_FEATURES:
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

        # Sample weight
        sample_w = rec.get("sample_weight", 1.0)
        w[i] = float(sample_w) if sample_w and sample_w > 0 else 1.0

        # Video ID
        video_ids_raw.append(rec.get("video_id", f"unknown_{i}"))

    # Convert video_id strings to integer group IDs
    unique_vids = sorted(set(video_ids_raw))
    vid_to_gid = {v: i for i, v in enumerate(unique_vids)}
    group_ids = np.array([vid_to_gid[g] for g in video_ids_raw], dtype=np.int32)

    return X, y, w, group_ids, feature_names, unique_vids, video_ids_raw


def precision_at_k(y_true, y_scores, k=5):
    """Compute Precision@K."""
    if len(y_true) < k:
        k = len(y_true)
    if k == 0:
        return 0.0
    top_k_idx = np.argsort(y_scores)[::-1][:k]
    return float(np.sum(y_true[top_k_idx])) / k


# ── GroupStratified: greedy均等分割 ──

def group_stratified_split(y, group_ids, n_splits=5, random_state=42):
    """
    Greedy GroupStratified split: assign video groups to folds
    so that positive rate is as even as possible across folds.

    Returns list of (train_idx, val_idx) tuples.
    """
    rng = np.random.RandomState(random_state)

    # Compute positive count per group
    unique_groups = np.unique(group_ids)
    group_pos = {}
    group_indices = {}
    for g in unique_groups:
        mask = group_ids == g
        group_pos[g] = int(y[mask].sum())
        group_indices[g] = np.where(mask)[0]

    # Shuffle groups
    groups_shuffled = list(unique_groups)
    rng.shuffle(groups_shuffled)

    # Sort by positive count descending (greedy: assign biggest first)
    groups_sorted = sorted(groups_shuffled, key=lambda g: group_pos[g], reverse=True)

    # Greedy assignment: assign each group to the fold with fewest positives
    fold_pos_counts = [0] * n_splits
    fold_groups = [[] for _ in range(n_splits)]

    for g in groups_sorted:
        # Find fold with minimum positive count
        min_fold = int(np.argmin(fold_pos_counts))
        fold_groups[min_fold].append(g)
        fold_pos_counts[min_fold] += group_pos[g]

    # Build train/val index pairs
    splits = []
    for fold_idx in range(n_splits):
        val_groups = set(fold_groups[fold_idx])
        val_idx = np.concatenate([group_indices[g] for g in val_groups]) if val_groups else np.array([], dtype=int)
        train_idx = np.array([i for i in range(len(y)) if group_ids[i] not in val_groups], dtype=int)
        if len(val_idx) > 0 and len(train_idx) > 0:
            splits.append((train_idx, val_idx))

    return splits


def evaluate_fold(X_tr, y_tr, w_tr, X_val, y_val, model_class, model_params,
                  use_scaler=False, use_sample_weight=True):
    """Train on one fold and return predictions + metrics."""
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score, precision_score, recall_score, f1_score

    if use_scaler:
        scaler = StandardScaler()
        X_tr = scaler.fit_transform(X_tr)
        X_val = scaler.transform(X_val)

    model = model_class(**model_params)
    if use_sample_weight:
        try:
            model.fit(X_tr, y_tr, sample_weight=w_tr)
        except TypeError:
            model.fit(X_tr, y_tr)
    else:
        model.fit(X_tr, y_tr)

    y_pred = model.predict_proba(X_val)[:, 1]

    # Metrics
    try:
        auc = roc_auc_score(y_val, y_pred)
    except ValueError:
        auc = float("nan")

    y_binary = (y_pred >= 0.5).astype(int)
    prec = precision_score(y_val, y_binary, zero_division=0)
    rec = recall_score(y_val, y_binary, zero_division=0)
    f1 = f1_score(y_val, y_binary, zero_division=0)
    p_at_5 = precision_at_k(y_val, y_pred, k=5)
    p_at_10 = precision_at_k(y_val, y_pred, k=10)

    return {
        "auc": round(auc, 4) if not np.isnan(auc) else None,
        "precision": round(prec, 4),
        "recall": round(rec, 4),
        "f1": round(f1, 4),
        "precision_at_5": round(p_at_5, 4),
        "precision_at_10": round(p_at_10, 4),
        "n_val": len(y_val),
        "n_val_pos": int(y_val.sum()),
        "pos_rate": round(float(y_val.sum()) / max(len(y_val), 1), 4),
    }


def repeated_group_cv(X, y, w, group_ids, model_class, model_params,
                      n_splits=5, n_repeats=3, use_scaler=False, use_sample_weight=True):
    """
    Repeated GroupStratified cross-validation.
    Returns: aggregate metrics (mean±std) and per-fold details.
    """
    all_fold_metrics = []

    for repeat_idx in range(n_repeats):
        seed = 42 + repeat_idx * 17
        splits = group_stratified_split(y, group_ids, n_splits=n_splits, random_state=seed)

        for fold_idx, (train_idx, val_idx) in enumerate(splits):
            fold_m = evaluate_fold(
                X[train_idx], y[train_idx], w[train_idx],
                X[val_idx], y[val_idx],
                model_class, model_params,
                use_scaler=use_scaler,
                use_sample_weight=use_sample_weight,
            )
            fold_m["repeat"] = repeat_idx + 1
            fold_m["fold"] = fold_idx + 1
            all_fold_metrics.append(fold_m)

    # Aggregate: mean ± std
    metric_keys = ["auc", "precision", "recall", "f1", "precision_at_5", "precision_at_10"]
    agg = {}
    for key in metric_keys:
        values = [m[key] for m in all_fold_metrics if m[key] is not None]
        if values:
            agg[f"{key}_mean"] = round(float(np.mean(values)), 4)
            agg[f"{key}_std"] = round(float(np.std(values)), 4)
        else:
            agg[f"{key}_mean"] = None
            agg[f"{key}_std"] = None

    # Fold pos_rate stats
    pos_rates = [m["pos_rate"] for m in all_fold_metrics]
    agg["fold_pos_rate_mean"] = round(float(np.mean(pos_rates)), 4) if pos_rates else None
    agg["fold_pos_rate_std"] = round(float(np.std(pos_rates)), 4) if pos_rates else None
    agg["fold_pos_rate_min"] = round(float(np.min(pos_rates)), 4) if pos_rates else None
    agg["fold_pos_rate_max"] = round(float(np.max(pos_rates)), 4) if pos_rates else None

    agg["n_repeats"] = n_repeats
    agg["n_splits"] = n_splits
    agg["n_total_folds"] = len(all_fold_metrics)

    return agg, all_fold_metrics


def holdout_evaluate(X, y, w, group_ids, video_ids_raw, unique_vids,
                     model_class, model_params, holdout_ratio=0.2,
                     use_scaler=False, use_sample_weight=True):
    """
    Holdout evaluation: last N% of videos as test set.
    Videos are sorted by their first appearance order (proxy for time).
    """
    # Sort videos by first appearance index (proxy for chronological order)
    vid_first_idx = {}
    for i, vid in enumerate(video_ids_raw):
        if vid not in vid_first_idx:
            vid_first_idx[vid] = i
    vids_ordered = sorted(vid_first_idx.keys(), key=lambda v: vid_first_idx[v])

    n_holdout = max(1, int(len(vids_ordered) * holdout_ratio))
    holdout_vids = set(vids_ordered[-n_holdout:])
    train_vids = set(vids_ordered[:-n_holdout])

    if not holdout_vids or not train_vids:
        return None, None

    train_idx = np.array([i for i, vid in enumerate(video_ids_raw) if vid in train_vids])
    test_idx = np.array([i for i, vid in enumerate(video_ids_raw) if vid in holdout_vids])

    if len(train_idx) == 0 or len(test_idx) == 0:
        return None, None

    fold_m = evaluate_fold(
        X[train_idx], y[train_idx], w[train_idx],
        X[test_idx], y[test_idx],
        model_class, model_params,
        use_scaler=use_scaler,
        use_sample_weight=use_sample_weight,
    )
    fold_m["holdout_videos"] = list(holdout_vids)
    fold_m["train_videos"] = list(train_vids)
    fold_m["n_holdout_videos"] = len(holdout_vids)
    fold_m["n_train_videos"] = len(train_vids)

    return fold_m, {"holdout_vids": list(holdout_vids), "train_vids": list(train_vids)}


def train_and_evaluate(X, y, w, group_ids, feature_names, unique_vids, video_ids_raw,
                       target, output_dir, dataset_path):
    """Train models with Repeated GroupKFold + Holdout, compare with StratifiedKFold."""
    from sklearn.model_selection import StratifiedKFold
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    try:
        import lightgbm as lgb
        has_lgbm = True
    except ImportError:
        print("[train] LightGBM not installed. Using only LogisticRegression.")
        has_lgbm = False

    os.makedirs(output_dir, exist_ok=True)
    metrics = {"target": target, "model_version": MODEL_VERSION}

    n_positive = int(y.sum())
    n_total = len(y)
    n_videos = len(unique_vids)

    print(f"\n[train] Target: {target}")
    print(f"[train] Dataset: {n_total} samples, {n_positive} positive ({n_positive/max(n_total,1)*100:.1f}%)")
    print(f"[train] Videos: {n_videos} unique")

    if n_positive < 3 or (n_total - n_positive) < 3:
        print("[train] WARNING: Too few samples for meaningful training.")
        metrics["status"] = "insufficient_data"
        metrics["n_total"] = n_total
        metrics["n_positive"] = n_positive
        with open(os.path.join(output_dir, f"eval_metrics_{target}.json"), "w") as f:
            json.dump(metrics, f, indent=2)
        return metrics, None, None

    # ── Dataset metadata ──
    dataset_hash = compute_dataset_hash(dataset_path) if dataset_path and os.path.exists(dataset_path) else "unknown"

    # ── Model configs ──
    lr_params = {
        "class_weight": "balanced",
        "max_iter": 1000,
        "random_state": 42,
        "C": 1.0,
    }

    n_neg = n_total - n_positive
    lgbm_params = {
        "objective": "binary",
        "metric": "auc",
        "verbosity": -1,
        "n_estimators": 200,
        "max_depth": 4,
        "learning_rate": 0.05,
        "num_leaves": 15,
        "min_child_samples": max(3, n_positive // 5),
        "scale_pos_weight": n_neg / max(n_positive, 1),
        "random_state": 42,
        "n_jobs": -1,
    }

    # ── CV config ──
    n_group_splits = min(5, n_videos)
    if n_group_splits < 2:
        n_group_splits = 2
    n_repeats = 3

    # ═══════════════════════════════════════════════════
    # 1. Repeated GroupStratified KFold (ADOPTED = realistic)
    # ═══════════════════════════════════════════════════
    print(f"\n[train] === Repeated GroupStratified KFold ({n_group_splits}fold × {n_repeats}repeat = {n_group_splits * n_repeats} evals) ===")
    print(f"[train] (ADOPTED: realistic evaluation)")

    # LR + Repeated GroupKFold
    print(f"  LogisticRegression + Repeated GroupKFold...")
    try:
        m_lr_group_agg, m_lr_group_folds = repeated_group_cv(
            X, y, w, group_ids, LogisticRegression, lr_params,
            n_splits=n_group_splits, n_repeats=n_repeats, use_scaler=True
        )
        print(f"    AUC={m_lr_group_agg['auc_mean']:.4f}±{m_lr_group_agg['auc_std']:.4f}"
              f"  P@5={m_lr_group_agg['precision_at_5_mean']:.4f}±{m_lr_group_agg['precision_at_5_std']:.4f}"
              f"  F1={m_lr_group_agg['f1_mean']:.4f}±{m_lr_group_agg['f1_std']:.4f}")
        print(f"    Fold pos_rate: mean={m_lr_group_agg['fold_pos_rate_mean']:.4f}"
              f"  std={m_lr_group_agg['fold_pos_rate_std']:.4f}"
              f"  range=[{m_lr_group_agg['fold_pos_rate_min']:.4f}, {m_lr_group_agg['fold_pos_rate_max']:.4f}]")
    except Exception as e:
        print(f"    Failed: {e}")
        m_lr_group_agg = {"error": str(e)}
        m_lr_group_folds = []

    # LightGBM + Repeated GroupKFold
    m_lgbm_group_agg, m_lgbm_group_folds = None, []
    if has_lgbm:
        print(f"  LightGBM + Repeated GroupKFold...")
        try:
            m_lgbm_group_agg, m_lgbm_group_folds = repeated_group_cv(
                X, y, w, group_ids, lgb.LGBMClassifier, lgbm_params,
                n_splits=n_group_splits, n_repeats=n_repeats, use_scaler=False
            )
            print(f"    AUC={m_lgbm_group_agg['auc_mean']:.4f}±{m_lgbm_group_agg['auc_std']:.4f}"
                  f"  P@5={m_lgbm_group_agg['precision_at_5_mean']:.4f}±{m_lgbm_group_agg['precision_at_5_std']:.4f}"
                  f"  F1={m_lgbm_group_agg['f1_mean']:.4f}±{m_lgbm_group_agg['f1_std']:.4f}")
            print(f"    Fold pos_rate: mean={m_lgbm_group_agg['fold_pos_rate_mean']:.4f}"
                  f"  std={m_lgbm_group_agg['fold_pos_rate_std']:.4f}"
                  f"  range=[{m_lgbm_group_agg['fold_pos_rate_min']:.4f}, {m_lgbm_group_agg['fold_pos_rate_max']:.4f}]")
        except Exception as e:
            print(f"    Failed: {e}")
            m_lgbm_group_agg = {"error": str(e)}

    # ═══════════════════════════════════════════════════
    # 2. StratifiedKFold (REFERENCE = optimistic)
    # ═══════════════════════════════════════════════════
    n_strat_splits = min(5, n_positive, n_total - n_positive)
    if n_strat_splits < 2:
        n_strat_splits = 2
    strat_cv = StratifiedKFold(n_splits=n_strat_splits, shuffle=True, random_state=42)

    print(f"\n[train] === StratifiedKFold ({n_strat_splits} folds) ===")
    print(f"[train] (REFERENCE: optimistic, may leak across videos)")

    # LR + StratifiedKFold
    print(f"  LogisticRegression + StratifiedKFold...")
    m_lr_strat_folds = []
    try:
        for fold_idx, (train_idx, val_idx) in enumerate(strat_cv.split(X, y)):
            fold_m = evaluate_fold(
                X[train_idx], y[train_idx], w[train_idx],
                X[val_idx], y[val_idx],
                LogisticRegression, lr_params, use_scaler=True
            )
            fold_m["fold"] = fold_idx + 1
            m_lr_strat_folds.append(fold_m)
        m_lr_strat_agg = _aggregate_folds(m_lr_strat_folds)
        print(f"    AUC={m_lr_strat_agg['auc_mean']:.4f}±{m_lr_strat_agg['auc_std']:.4f}"
              f"  P@5={m_lr_strat_agg['precision_at_5_mean']:.4f}±{m_lr_strat_agg['precision_at_5_std']:.4f}")
    except Exception as e:
        print(f"    Failed: {e}")
        m_lr_strat_agg = {"error": str(e)}

    # LightGBM + StratifiedKFold
    m_lgbm_strat_agg = None
    m_lgbm_strat_folds = []
    if has_lgbm:
        print(f"  LightGBM + StratifiedKFold...")
        try:
            strat_cv2 = StratifiedKFold(n_splits=n_strat_splits, shuffle=True, random_state=42)
            for fold_idx, (train_idx, val_idx) in enumerate(strat_cv2.split(X, y)):
                fold_m = evaluate_fold(
                    X[train_idx], y[train_idx], w[train_idx],
                    X[val_idx], y[val_idx],
                    lgb.LGBMClassifier, lgbm_params, use_scaler=False
                )
                fold_m["fold"] = fold_idx + 1
                m_lgbm_strat_folds.append(fold_m)
            m_lgbm_strat_agg = _aggregate_folds(m_lgbm_strat_folds)
            print(f"    AUC={m_lgbm_strat_agg['auc_mean']:.4f}±{m_lgbm_strat_agg['auc_std']:.4f}"
                  f"  P@5={m_lgbm_strat_agg['precision_at_5_mean']:.4f}±{m_lgbm_strat_agg['precision_at_5_std']:.4f}")
        except Exception as e:
            print(f"    Failed: {e}")
            m_lgbm_strat_agg = {"error": str(e)}

    # ═══════════════════════════════════════════════════
    # 3. Holdout (last 20% of videos = final judgment)
    # ═══════════════════════════════════════════════════
    print(f"\n[train] === Holdout (last 20% of videos) ===")
    print(f"[train] (FINAL JUDGMENT: one-time evaluation)")

    m_lr_holdout, holdout_info = holdout_evaluate(
        X, y, w, group_ids, video_ids_raw, unique_vids,
        LogisticRegression, lr_params, holdout_ratio=0.2, use_scaler=True
    )
    if m_lr_holdout:
        print(f"  LR Holdout: AUC={m_lr_holdout.get('auc', '?')}"
              f"  P@5={m_lr_holdout.get('precision_at_5', '?')}"
              f"  ({m_lr_holdout['n_holdout_videos']} test videos, {m_lr_holdout['n_val']} events)")
    else:
        print(f"  LR Holdout: skipped (insufficient data)")

    m_lgbm_holdout = None
    if has_lgbm:
        m_lgbm_holdout, _ = holdout_evaluate(
            X, y, w, group_ids, video_ids_raw, unique_vids,
            lgb.LGBMClassifier, lgbm_params, holdout_ratio=0.2, use_scaler=False
        )
        if m_lgbm_holdout:
            print(f"  LGBM Holdout: AUC={m_lgbm_holdout.get('auc', '?')}"
                  f"  P@5={m_lgbm_holdout.get('precision_at_5', '?')}"
                  f"  ({m_lgbm_holdout['n_holdout_videos']} test videos, {m_lgbm_holdout['n_val']} events)")
        else:
            print(f"  LGBM Holdout: skipped (insufficient data)")

    # ═══════════════════════════════════════════════════
    # 4. Comparison Summary
    # ═══════════════════════════════════════════════════
    print(f"\n[train] === COMPARISON ===")
    print(f"  {'Model':<15} {'GroupKFold AUC':>20} {'StratKFold AUC':>20} {'Delta':>10} {'Holdout AUC':>15}")
    print(f"  {'-'*80}")
    for algo, g_agg, s_agg, h_m in [
        ("LogReg", m_lr_group_agg, m_lr_strat_agg, m_lr_holdout),
        ("LightGBM", m_lgbm_group_agg, m_lgbm_strat_agg, m_lgbm_holdout),
    ]:
        if g_agg is None or "error" in g_agg:
            continue
        g_auc = g_agg.get("auc_mean", 0) or 0
        g_std = g_agg.get("auc_std", 0) or 0
        s_auc = s_agg.get("auc_mean", 0) if s_agg and "error" not in s_agg else 0
        s_std = s_agg.get("auc_std", 0) if s_agg and "error" not in s_agg else 0
        delta = (s_auc or 0) - (g_auc or 0)
        h_auc = h_m.get("auc", "?") if h_m else "N/A"

        print(f"  {algo:<15} {g_auc:>8.4f}±{g_std:<8.4f} {s_auc:>8.4f}±{s_std:<8.4f} {delta:>+10.4f} {str(h_auc):>15}")
        if delta > 0.05:
            print(f"  ⚠️  {algo}: StratifiedKFold is {delta:.4f} higher → possible video-level leak")
        else:
            print(f"  ✅  {algo}: GroupKFold holds up well (delta < 0.05)")

    # ═══════════════════════════════════════════════════
    # 5. Train final models on all data
    # ═══════════════════════════════════════════════════
    print(f"\n[train] Training final models on all data...")

    # Final LR
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    lr_final = LogisticRegression(**lr_params)
    lr_final.fit(X_scaled, y, sample_weight=w)

    lr_filename = f"model_{target}_lr_v{MODEL_VERSION}_{DATE_TAG}.pkl"
    model_payload_lr = {
        "model": lr_final, "scaler": scaler, "target": target,
        "version": MODEL_VERSION, "date": DATE_TAG,
        "feature_names": feature_names,
    }
    with open(os.path.join(output_dir, lr_filename), "wb") as f:
        pickle.dump(model_payload_lr, f)
    with open(os.path.join(output_dir, f"model_{target}_lr.pkl"), "wb") as f:
        pickle.dump(model_payload_lr, f)
    print(f"  Saved: {lr_filename}")

    # Final LightGBM
    lgbm_filename = None
    feat_imp_list = []
    if has_lgbm:
        lgbm_final = lgb.LGBMClassifier(**lgbm_params)
        lgbm_final.fit(X, y, sample_weight=w)

        lgbm_filename = f"model_{target}_lgbm_v{MODEL_VERSION}_{DATE_TAG}.pkl"
        model_payload_lgbm = {
            "model": lgbm_final, "target": target,
            "version": MODEL_VERSION, "date": DATE_TAG,
            "feature_names": feature_names,
        }
        with open(os.path.join(output_dir, lgbm_filename), "wb") as f:
            pickle.dump(model_payload_lgbm, f)
        with open(os.path.join(output_dir, f"model_{target}_lgbm.pkl"), "wb") as f:
            pickle.dump(model_payload_lgbm, f)
        print(f"  Saved: {lgbm_filename}")

        # Feature importance
        importances = lgbm_final.feature_importances_
        feat_imp_list = sorted(
            zip(feature_names, importances.tolist()),
            key=lambda x: x[1], reverse=True
        )
        print("\n  Top 10 features:")
        for fname, imp in feat_imp_list[:10]:
            print(f"    {fname:30s} {imp:6.0f}")

    # ── Determine best model (based on GroupKFold mean AUC) ──
    best_model = "lr"
    best_auc = m_lr_group_agg.get("auc_mean", 0) if isinstance(m_lr_group_agg, dict) and "error" not in m_lr_group_agg else 0
    if m_lgbm_group_agg and isinstance(m_lgbm_group_agg, dict) and "error" not in m_lgbm_group_agg:
        lgbm_auc = m_lgbm_group_agg.get("auc_mean", 0) or 0
        if lgbm_auc > (best_auc or 0):
            best_model = "lgbm"
            best_auc = lgbm_auc

    # ── Build complete metrics ──
    metrics["best_model"] = best_model
    metrics["best_auc_group_kfold"] = best_auc
    metrics["status"] = "success"
    metrics["n_total"] = n_total
    metrics["n_positive"] = n_positive
    metrics["n_videos"] = n_videos
    metrics["positive_rate"] = round(n_positive / max(n_total, 1), 4)
    metrics["n_features"] = len(feature_names)

    # Dataset metadata
    metrics["dataset"] = {
        "dataset_version": f"dataset_v{MODEL_VERSION}_{DATE_TAG}",
        "dataset_hash": dataset_hash,
        "dataset_event_count": n_total,
        "dataset_video_count": n_videos,
        "dataset_path": dataset_path,
    }

    # Evaluation results
    metrics["evaluation"] = {
        "repeated_group_kfold": {
            "label": "ADOPTED (realistic)",
            "n_splits": n_group_splits,
            "n_repeats": n_repeats,
            "n_total_folds": n_group_splits * n_repeats,
            "lr": m_lr_group_agg,
            "lr_fold_details": m_lr_group_folds,
        },
        "stratified_kfold": {
            "label": "REFERENCE (optimistic)",
            "n_splits": n_strat_splits,
            "lr": m_lr_strat_agg,
            "lr_fold_details": m_lr_strat_folds,
        },
        "holdout": {
            "label": "FINAL JUDGMENT",
            "holdout_ratio": 0.2,
            "lr": m_lr_holdout,
        },
    }
    if m_lgbm_group_agg is not None:
        metrics["evaluation"]["repeated_group_kfold"]["lgbm"] = m_lgbm_group_agg
        metrics["evaluation"]["repeated_group_kfold"]["lgbm_fold_details"] = m_lgbm_group_folds
    if m_lgbm_strat_agg is not None:
        metrics["evaluation"]["stratified_kfold"]["lgbm"] = m_lgbm_strat_agg
        metrics["evaluation"]["stratified_kfold"]["lgbm_fold_details"] = m_lgbm_strat_folds
    if m_lgbm_holdout is not None:
        metrics["evaluation"]["holdout"]["lgbm"] = m_lgbm_holdout

    if feat_imp_list:
        metrics["feature_importance"] = [
            {"feature": fn, "importance": imp} for fn, imp in feat_imp_list[:20]
        ]

    # Save metrics
    with open(os.path.join(output_dir, f"eval_metrics_{target}.json"), "w") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)

    # Save feature names
    with open(os.path.join(output_dir, "feature_names.json"), "w") as f:
        json.dump(feature_names, f, indent=2)

    print(f"\n[train] Best model for {target}: {best_model} (GroupKFold AUC mean={best_auc:.4f})")
    return metrics, lr_filename, lgbm_filename


def _aggregate_folds(fold_metrics_list):
    """Aggregate a list of fold metrics into mean±std."""
    metric_keys = ["auc", "precision", "recall", "f1", "precision_at_5", "precision_at_10"]
    agg = {}
    for key in metric_keys:
        values = [m[key] for m in fold_metrics_list if m.get(key) is not None]
        if values:
            agg[f"{key}_mean"] = round(float(np.mean(values)), 4)
            agg[f"{key}_std"] = round(float(np.std(values)), 4)
        else:
            agg[f"{key}_mean"] = None
            agg[f"{key}_std"] = None
    pos_rates = [m.get("pos_rate", 0) for m in fold_metrics_list]
    agg["fold_pos_rate_mean"] = round(float(np.mean(pos_rates)), 4) if pos_rates else None
    agg["fold_pos_rate_std"] = round(float(np.std(pos_rates)), 4) if pos_rates else None
    agg["n_total_folds"] = len(fold_metrics_list)
    return agg


def build_manifest(all_metrics, output_dir, feature_names, dataset_paths):
    """Build manifest.json for model registry (complete version)."""
    commit = get_git_commit()

    manifest = {
        "model_version": f"v{MODEL_VERSION}.{DATE_TAG}",
        "model_version_int": MODEL_VERSION,
        "date": DATE_TAG,
        "commit_hash": commit,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "features_used": feature_names,
        "n_features": len(feature_names),
        "label_definition": LABEL_DEFINITION,
        "notes": {
            "leak_features_removed": True,
            "cv_method": "repeated_group_stratified_kfold",
            "cv_adopted": "GroupKFold (realistic)",
            "cv_reference": "StratifiedKFold (optimistic)",
            "holdout_method": "last 20% videos by appearance order",
        },
        "models": {},
    }

    for target, (metrics, lr_file, lgbm_file) in all_metrics.items():
        ds = metrics.get("dataset", {})
        eval_data = metrics.get("evaluation", {})
        group_eval = eval_data.get("repeated_group_kfold", {})
        holdout_eval = eval_data.get("holdout", {})

        # Get adopted metrics (GroupKFold)
        best = metrics.get("best_model", "lr")
        adopted = group_eval.get(best, {})

        manifest["models"][target] = {
            "best_model": best,
            "adopted_metrics": {
                "auc_mean": adopted.get("auc_mean"),
                "auc_std": adopted.get("auc_std"),
                "precision_at_5_mean": adopted.get("precision_at_5_mean"),
                "precision_at_5_std": adopted.get("precision_at_5_std"),
                "precision_at_10_mean": adopted.get("precision_at_10_mean"),
                "precision_at_10_std": adopted.get("precision_at_10_std"),
                "f1_mean": adopted.get("f1_mean"),
                "f1_std": adopted.get("f1_std"),
            },
            "holdout_metrics": {
                "auc": holdout_eval.get(best, {}).get("auc") if holdout_eval.get(best) else None,
                "precision_at_5": holdout_eval.get(best, {}).get("precision_at_5") if holdout_eval.get(best) else None,
            },
            "dataset": {
                "dataset_version": ds.get("dataset_version"),
                "dataset_hash": ds.get("dataset_hash"),
                "dataset_event_count": ds.get("dataset_event_count"),
                "dataset_video_count": ds.get("dataset_video_count"),
                "dataset_path": dataset_paths.get(target),
            },
            "n_total": metrics.get("n_total", 0),
            "n_positive": metrics.get("n_positive", 0),
            "n_videos": metrics.get("n_videos", 0),
            "files": {
                "lr": lr_file,
                "lgbm": lgbm_file,
            },
        }

    manifest_path = os.path.join(output_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(f"\n[train] Manifest → {manifest_path}")
    return manifest


def filter_records_by_source(records, source_filter):
    """
    Filter dataset records by moment source.

    source_filter:
      'all'    → use all records (default, mixed training)
      'csv'    → only records where moment_source is 'csv' or 'both' or 'none'
                 (i.e., include all, but positives must come from csv)
      'screen' → only records where moment_source is 'screen' or 'both' or 'none'
                 (i.e., include all, but positives must come from screen)
      'csv_only'    → strict: positive only from csv source
      'screen_only' → strict: positive only from screen source
    """
    if source_filter == "all":
        return records

    filtered = []
    for r in records:
        src = r.get("moment_source", "none")
        is_positive = any(r.get(f"y_{t}", 0) == 1 for t in ["click", "order", "strong"])

        if not is_positive:
            # Always include negatives
            filtered.append(r)
        elif source_filter == "csv_only":
            if src in ("csv", "both"):
                filtered.append(r)
        elif source_filter == "screen_only":
            if src in ("screen", "both"):
                filtered.append(r)
        elif source_filter == "csv":
            if src != "screen":  # csv, both, none
                filtered.append(r)
        elif source_filter == "screen":
            if src != "csv":  # screen, both, none
                filtered.append(r)
        else:
            filtered.append(r)

    return filtered


def main():
    parser = argparse.ArgumentParser(description="Train LCJ AI prediction model v5")
    parser.add_argument("--input", "-i", default=None,
                        help="Input JSONL dataset file (single target)")
    parser.add_argument("--input-dir", default=None,
                        help="Input directory containing train_click.jsonl and train_order.jsonl")
    parser.add_argument("--target", "-t", default="click",
                        choices=["click", "order"],
                        help="Target label (click or order)")
    parser.add_argument("--output-dir", "-o", default="/tmp/models/",
                        help="Output directory for models and metrics")
    parser.add_argument("--source-filter", default="all",
                        choices=["all", "csv", "screen", "csv_only", "screen_only"],
                        help="Filter training data by moment source (default: all = mixed)")
    args = parser.parse_args()

    print(f"[train] Source filter: {args.source_filter}")

    all_results = {}
    feature_names_final = None
    dataset_paths = {}

    if args.input_dir:
        for target in ["click", "order"]:
            input_path = os.path.join(args.input_dir, f"train_{target}.jsonl")
            if not os.path.exists(input_path):
                print(f"[train] Skipping {target}: {input_path} not found")
                continue

            print(f"\n{'='*70}")
            print(f"[train] Loading {target} dataset from: {input_path}")
            records = load_jsonl(input_path)
            print(f"[train] Loaded {len(records)} records")

            # Apply source filter
            records = filter_records_by_source(records, args.source_filter)
            print(f"[train] After source filter '{args.source_filter}': {len(records)} records")

            X, y, w, group_ids, feature_names, unique_vids, video_ids_raw = extract_features(records, target=target)
            feature_names_final = feature_names
            dataset_paths[target] = input_path
            print(f"[train] Feature matrix: {X.shape}, {len(unique_vids)} videos")

            result = train_and_evaluate(
                X, y, w, group_ids, feature_names, unique_vids, video_ids_raw,
                target, args.output_dir, input_path
            )
            all_results[target] = result

    elif args.input:
        if not os.path.exists(args.input):
            print(f"[train] ERROR: Input file not found: {args.input}")
            sys.exit(1)

        records = load_jsonl(args.input)

        # Apply source filter
        records = filter_records_by_source(records, args.source_filter)
        print(f"[train] After source filter '{args.source_filter}': {len(records)} records")

        X, y, w, group_ids, feature_names, unique_vids, video_ids_raw = extract_features(records, target=args.target)
        feature_names_final = feature_names
        dataset_paths[args.target] = args.input

        result = train_and_evaluate(
            X, y, w, group_ids, feature_names, unique_vids, video_ids_raw,
            args.target, args.output_dir, args.input
        )
        all_results[args.target] = result

    else:
        print("[train] ERROR: Specify --input or --input-dir")
        sys.exit(1)

    # Build manifest
    if feature_names_final and all_results:
        manifest = build_manifest(all_results, args.output_dir, feature_names_final, dataset_paths)
        manifest["source_filter"] = args.source_filter

    # Summary
    print(f"\n{'='*70}")
    print("[train] FINAL SUMMARY")
    print(f"  Model Version: v{MODEL_VERSION} ({DATE_TAG})")
    print(f"  Commit: {get_git_commit()}")
    for target, (m, _, _) in all_results.items():
        best = m.get("best_model", "?")
        g_eval = m.get("evaluation", {}).get("repeated_group_kfold", {})
        h_eval = m.get("evaluation", {}).get("holdout", {})
        adopted = g_eval.get(best, {})
        holdout = h_eval.get(best, {})

        print(f"\n  [{target}]")
        print(f"    Best model: {best}")
        print(f"    GroupKFold AUC:     {adopted.get('auc_mean', '?')}±{adopted.get('auc_std', '?')}")
        print(f"    GroupKFold P@5:     {adopted.get('precision_at_5_mean', '?')}±{adopted.get('precision_at_5_std', '?')}")
        print(f"    GroupKFold P@10:    {adopted.get('precision_at_10_mean', '?')}±{adopted.get('precision_at_10_std', '?')}")
        print(f"    GroupKFold F1:      {adopted.get('f1_mean', '?')}±{adopted.get('f1_std', '?')}")
        if holdout:
            print(f"    Holdout AUC:       {holdout.get('auc', '?')}")
            print(f"    Holdout P@5:       {holdout.get('precision_at_5', '?')}")
        else:
            print(f"    Holdout: N/A")

    print(f"\n[train] All outputs saved to: {args.output_dir}")

    success = all(m.get("status") == "success" for m, _, _ in all_results.values())
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
