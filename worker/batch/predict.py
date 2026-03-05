"""
predict.py  –  LCJ AI 推論モジュール
=====================================
学習済みモデルを読み込み、各eventに「売れやすさスコア」を付与する。

使い方:
  # JSONL入力 → JSONL出力 (スコア付き)
  python predict.py --input /tmp/train.jsonl --model-dir /tmp/models/ --output /tmp/scored.jsonl

  # モジュールとしてimport
  from predict import EventScorer
  scorer = EventScorer("/tmp/models/")
  score = scorer.predict_one(record_dict)
"""

import json
import os
import pickle
import sys
import numpy as np

# Import feature config from train.py
from train import (
    NUMERIC_FEATURES, BOOL_FEATURES, AUDIO_FEATURES,
    KNOWN_EVENT_TYPES, extract_features, load_jsonl,
)


class EventScorer:
    """Load trained model and score events."""

    def __init__(self, model_dir: str):
        self.model_dir = model_dir
        self.model = None
        self.model_type = None
        self.scaler = None
        self.feature_names = None

        self._load()

    def _load(self):
        """Load the best available model."""
        # Load feature names
        fn_path = os.path.join(self.model_dir, "feature_names.json")
        if os.path.exists(fn_path):
            with open(fn_path, "r") as f:
                self.feature_names = json.load(f)

        # Load eval metrics to determine best model
        metrics_path = os.path.join(self.model_dir, "eval_metrics.json")
        best_model = "lgbm"  # default
        if os.path.exists(metrics_path):
            with open(metrics_path, "r") as f:
                metrics = json.load(f)
                best_model = metrics.get("best_model", "lgbm")

        # Try loading best model first, then fallback
        if best_model == "lgbm":
            load_order = ["model_lgbm.pkl", "model_lr.pkl"]
        else:
            load_order = ["model_lr.pkl", "model_lgbm.pkl"]

        for model_file in load_order:
            path = os.path.join(self.model_dir, model_file)
            if os.path.exists(path):
                with open(path, "rb") as f:
                    obj = pickle.load(f)

                if model_file == "model_lr.pkl":
                    self.model = obj["model"]
                    self.scaler = obj["scaler"]
                    self.model_type = "lr"
                else:
                    self.model = obj
                    self.model_type = "lgbm"

                print(f"[predict] Loaded model: {model_file} (type={self.model_type})")
                return

        raise FileNotFoundError(f"No model found in {self.model_dir}")

    def _record_to_features(self, record: dict) -> np.ndarray:
        """Convert a single record dict to feature vector."""
        features = []

        # Numeric
        for feat in NUMERIC_FEATURES:
            val = record.get(feat)
            features.append(float(val) if val is not None else 0.0)

        # Boolean
        for feat in BOOL_FEATURES:
            features.append(1.0 if record.get(feat) else 0.0)

        # Audio
        for feat in AUDIO_FEATURES:
            val = record.get(feat)
            features.append(float(val) if val is not None else 0.0)

        # Event type one-hot
        event_type = record.get("event_type", "UNKNOWN")
        for et in KNOWN_EVENT_TYPES:
            features.append(1.0 if event_type == et else 0.0)

        return np.array(features, dtype=np.float32).reshape(1, -1)

    def predict_one(self, record: dict) -> float:
        """Predict score for a single event record. Returns 0.0-1.0."""
        X = self._record_to_features(record)

        if self.model_type == "lr" and self.scaler:
            X = self.scaler.transform(X)

        proba = self.model.predict_proba(X)[0]
        # Return probability of positive class
        return float(proba[1]) if len(proba) > 1 else float(proba[0])

    def predict_batch(self, records: list) -> list:
        """Predict scores for a batch of records."""
        if not records:
            return []

        X, _, _ = extract_features(records)

        if self.model_type == "lr" and self.scaler:
            X = self.scaler.transform(X)

        probas = self.model.predict_proba(X)
        return [float(p[1]) if len(p) > 1 else float(p[0]) for p in probas]

    def predict_and_rank(self, records: list, top_k: int = 5) -> list:
        """Predict, rank, and return top-K events."""
        scores = self.predict_batch(records)

        # Attach scores
        scored = []
        for rec, score in zip(records, scores):
            scored.append({
                **rec,
                "ai_score": round(score, 4),
            })

        # Sort by score descending
        scored.sort(key=lambda x: x["ai_score"], reverse=True)

        return scored[:top_k]


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Score events with trained model")
    parser.add_argument("--input", "-i", required=True,
                        help="Input JSONL file")
    parser.add_argument("--model-dir", "-m", default="/tmp/models/",
                        help="Directory containing trained models")
    parser.add_argument("--output", "-o", default=None,
                        help="Output JSONL file (default: stdout)")
    parser.add_argument("--top-k", type=int, default=None,
                        help="Only output top-K scored events")
    args = parser.parse_args()

    scorer = EventScorer(args.model_dir)
    records = load_jsonl(args.input)
    print(f"[predict] Loaded {len(records)} records", file=sys.stderr)

    scores = scorer.predict_batch(records)

    # Attach scores
    for rec, score in zip(records, scores):
        rec["ai_score"] = round(score, 4)

    # Sort by score
    records.sort(key=lambda x: x["ai_score"], reverse=True)

    # Top-K filter
    if args.top_k:
        records = records[:args.top_k]

    # Output
    out = open(args.output, "w", encoding="utf-8") if args.output else sys.stdout
    for rec in records:
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")

    if args.output:
        out.close()
        print(f"[predict] Scored {len(records)} records → {args.output}", file=sys.stderr)

    # Print summary
    print(f"\n[predict] Score distribution:", file=sys.stderr)
    score_arr = np.array([r["ai_score"] for r in records])
    print(f"  Mean:   {score_arr.mean():.4f}", file=sys.stderr)
    print(f"  Median: {np.median(score_arr):.4f}", file=sys.stderr)
    print(f"  Min:    {score_arr.min():.4f}", file=sys.stderr)
    print(f"  Max:    {score_arr.max():.4f}", file=sys.stderr)

    if args.top_k:
        print(f"\n[predict] Top {args.top_k} events:", file=sys.stderr)
        for i, rec in enumerate(records[:args.top_k]):
            print(f"  {i+1}. score={rec['ai_score']:.4f}  type={rec.get('event_type','?')}  "
                  f"desc={rec.get('phase_description','')[:50]}", file=sys.stderr)


if __name__ == "__main__":
    main()
