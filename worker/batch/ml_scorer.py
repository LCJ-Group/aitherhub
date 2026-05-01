"""
ml_scorer.py – ML Model Scorer for Phase Selection
====================================================
学習済みモデル（LightGBM/LogisticRegression）を使用して
フェーズのクリック/注文予測スコアを計算する。

best_phase_pipeline.py の compute_attention_score() と組み合わせて使用。
MLスコアが利用可能な場合は、attention_score + ml_score の加重平均でランキング。

使い方:
    from ml_scorer import get_ml_scorer
    scorer = get_ml_scorer()  # モデルをロード（初回のみ）
    if scorer:
        ml_score = scorer.predict_phase(phase_features)
"""
import os
import json
import pickle
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Model directory (relative to this file)
MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
LATEST_MODEL_DIR = os.path.join(MODELS_DIR, "latest")

# Singleton scorer instance
_scorer_instance = None


class MLScorer:
    """Loads and caches ML models for phase scoring."""

    def __init__(self, model_dir: str = None):
        self.model_dir = model_dir or LATEST_MODEL_DIR
        self.click_model = None
        self.order_model = None
        self.feature_names = None
        self.manifest = None
        self._loaded = False

    def load(self) -> bool:
        """Load models from disk. Returns True if successful."""
        if self._loaded:
            return True

        # Try latest first, then v6 fallback
        dirs_to_try = [self.model_dir]
        # Add versioned dirs
        if os.path.exists(MODELS_DIR):
            versions = sorted(
                [d for d in os.listdir(MODELS_DIR) if d.startswith("v")],
                key=lambda x: int(x[1:]) if x[1:].isdigit() else 0,
                reverse=True
            )
            for v in versions:
                dirs_to_try.append(os.path.join(MODELS_DIR, v))

        for d in dirs_to_try:
            if not os.path.exists(d):
                continue
            manifest_path = os.path.join(d, "manifest.json")
            if not os.path.exists(manifest_path):
                continue

            try:
                with open(manifest_path) as f:
                    self.manifest = json.load(f)

                # Load feature names
                fn_path = os.path.join(d, "feature_names.json")
                if os.path.exists(fn_path):
                    with open(fn_path) as f:
                        self.feature_names = json.load(f)

                # Load click model
                click_info = self.manifest.get("models", {}).get("click", {})
                click_best = click_info.get("best_model", "lgbm")
                click_file = os.path.join(d, f"model_click_{click_best}.pkl")
                if os.path.exists(click_file):
                    with open(click_file, "rb") as f:
                        self.click_model = pickle.load(f)
                    logger.info(f"[ml_scorer] Loaded click model from {click_file}")

                # Load order model
                order_info = self.manifest.get("models", {}).get("order", {})
                order_best = order_info.get("best_model", "lgbm")
                order_file = os.path.join(d, f"model_order_{order_best}.pkl")
                if os.path.exists(order_file):
                    with open(order_file, "rb") as f:
                        self.order_model = pickle.load(f)
                    logger.info(f"[ml_scorer] Loaded order model from {order_file}")

                self._loaded = True
                logger.info(f"[ml_scorer] Models loaded from {d} (version: {self.manifest.get('model_version')})")
                return True

            except Exception as e:
                logger.warning(f"[ml_scorer] Failed to load models from {d}: {e}")
                continue

        logger.warning("[ml_scorer] No valid model directory found")
        return False

    def predict_click(self, features: Dict[str, Any]) -> Optional[float]:
        """Predict click probability for a phase."""
        if not self._loaded or not self.click_model:
            return None
        return self._predict(self.click_model, features)

    def predict_order(self, features: Dict[str, Any]) -> Optional[float]:
        """Predict order probability for a phase."""
        if not self._loaded or not self.order_model:
            return None
        return self._predict(self.order_model, features)

    def predict_combined(self, features: Dict[str, Any], 
                         click_weight: float = 0.4, 
                         order_weight: float = 0.6) -> Optional[float]:
        """Combined score: weighted average of click and order predictions."""
        click_score = self.predict_click(features)
        order_score = self.predict_order(features)

        if click_score is None and order_score is None:
            return None
        if click_score is None:
            return order_score
        if order_score is None:
            return click_score

        return click_weight * click_score + order_weight * order_score

    def _predict(self, model_payload, features: Dict[str, Any]) -> Optional[float]:
        """Run prediction using the model payload (dict with 'model', 'scaler', 'feature_names')."""
        try:
            import numpy as np

            model = model_payload.get("model") if isinstance(model_payload, dict) else model_payload
            scaler = model_payload.get("scaler") if isinstance(model_payload, dict) else None
            feat_names = model_payload.get("feature_names") if isinstance(model_payload, dict) else self.feature_names

            if feat_names is None:
                feat_names = self.feature_names
            if feat_names is None:
                logger.warning("[ml_scorer] No feature names available")
                return None

            # Build feature vector as DataFrame to preserve feature names
            import pandas as pd
            row = {}
            for fname in feat_names:
                val = features.get(fname, 0)
                if val is None:
                    val = 0
                try:
                    row[fname] = float(val)
                except (ValueError, TypeError):
                    row[fname] = 0.0
            X = pd.DataFrame([row], columns=feat_names)

            # Apply scaler if available
            if scaler is not None:
                X = scaler.transform(X)

            # Predict probability
            if hasattr(model, "predict_proba"):
                prob = model.predict_proba(X)[0, 1]
            else:
                prob = model.predict(X)[0]

            return float(prob)

        except Exception as e:
            logger.warning(f"[ml_scorer] Prediction failed: {e}")
            return None

    def get_model_version(self) -> Optional[str]:
        """Get the loaded model version."""
        if self.manifest:
            return self.manifest.get("model_version")
        return None


def get_ml_scorer() -> Optional[MLScorer]:
    """Get or create the singleton MLScorer instance."""
    global _scorer_instance
    if _scorer_instance is None:
        _scorer_instance = MLScorer()
        if not _scorer_instance.load():
            _scorer_instance = None
    return _scorer_instance


def extract_phase_features_for_ml(phase: dict) -> Dict[str, Any]:
    """
    Extract features from a phase_unit dict (as used in process_video.py)
    for ML model prediction.
    
    This maps the phase_unit structure to the feature names used in training.
    """
    features = {}

    # Basic features
    time_range = phase.get("time_range", {})
    duration = time_range.get("end_sec", 0) - time_range.get("start_sec", 0)
    total_duration = phase.get("video_duration", 3600)  # fallback

    features["event_duration"] = duration
    features["event_position_min"] = time_range.get("start_sec", 0) / 60.0
    features["event_position_pct"] = time_range.get("start_sec", 0) / max(total_duration, 1)

    # Tag/text features
    tags = phase.get("tags", [])
    features["tag_count"] = len(tags) if isinstance(tags, list) else 0
    features["cta_score"] = phase.get("cta_score", 0) or 0
    features["importance_score"] = phase.get("importance_score", 0) or 0

    text = phase.get("text", "") or phase.get("summary", "") or ""
    features["text_length"] = len(text)
    features["has_number"] = 1 if any(c.isdigit() for c in text) else 0
    features["exclamation_count"] = text.count("!")

    # Keyword features
    text_lower = text.lower()
    features["kw_price"] = 1 if any(w in text_lower for w in ["円", "¥", "価格", "値段", "安い", "お得"]) else 0
    features["kw_discount"] = 1 if any(w in text_lower for w in ["割引", "セール", "off", "オフ", "半額"]) else 0
    features["kw_urgency"] = 1 if any(w in text_lower for w in ["今だけ", "限定", "残り", "急いで", "ラスト"]) else 0
    features["kw_cta"] = 1 if any(w in text_lower for w in ["買って", "購入", "注文", "カート", "リンク"]) else 0
    features["kw_quantity"] = 1 if any(w in text_lower for w in ["個", "本", "セット", "パック"]) else 0
    features["kw_comparison"] = 1 if any(w in text_lower for w in ["比べ", "違い", "より", "比較"]) else 0
    features["kw_quality"] = 1 if any(w in text_lower for w in ["品質", "高品質", "プレミアム", "最高"]) else 0
    features["kw_number"] = 1 if any(c.isdigit() for c in text) else 0

    # Product features
    features["product_match"] = 1 if phase.get("product_names") else 0

    # Human review features (may not be available at prediction time)
    features["user_rating"] = phase.get("user_rating", 0) or 0
    features["has_human_review"] = 1 if phase.get("user_rating") else 0
    features["human_tag_count"] = 0
    features["comment_length"] = 0

    # NG features
    features["is_ng"] = 1 if phase.get("is_unusable") else 0
    features["has_ng_feedback"] = 0
    features["ng_reason_tag_count"] = 0

    # Quality features (if available from DB)
    for qf in ["fq_blur_score", "fq_brightness_mean", "fq_brightness_std",
               "fq_color_saturation", "fq_scene_change_count",
               "af_energy_mean", "af_energy_max", "af_pitch_mean", "af_pitch_std",
               "af_speech_rate", "af_silence_ratio"]:
        features[qf] = phase.get(qf, 0) or 0

    # af_energy_trend (categorical → numeric)
    trend = phase.get("af_energy_trend", "stable")
    if trend == "rising":
        features["af_energy_trend"] = 1
    elif trend == "falling":
        features["af_energy_trend"] = -1
    else:
        features["af_energy_trend"] = 0

    return features
