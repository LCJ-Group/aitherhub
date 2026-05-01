import os
import json
import logging

logger = logging.getLogger(__name__)

# ML Scorer (lazy-loaded)
_ml_scorer = None
_ml_scorer_attempted = False

def _get_ml_scorer():
    """Lazy-load ML scorer. Returns None if models not available."""
    global _ml_scorer, _ml_scorer_attempted
    if _ml_scorer_attempted:
        return _ml_scorer
    _ml_scorer_attempted = True
    try:
        from ml_scorer import get_ml_scorer, extract_phase_features_for_ml
        _ml_scorer = get_ml_scorer()
        if _ml_scorer:
            logger.info(f"[best_phase] ML scorer loaded (version: {_ml_scorer.get_model_version()})")
    except Exception as e:
        logger.warning(f"[best_phase] ML scorer not available: {e}")
    return _ml_scorer


def get_group_root(art_root: str, video_id: str):
    return os.path.join(art_root, video_id, "group")

def get_best_phase_file(art_root: str, video_id: str):
    return os.path.join(get_group_root(art_root, video_id), "group_best_phases.json")


TOP_K = 1


# =========================
# ATTENTION METRICS
# =========================

def extract_attention_metrics(phase):
    start = phase["metric_timeseries"].get("start") or {}
    end   = phase["metric_timeseries"].get("end") or {}

    def _safe_num(v):
        """Convert value to int/float safely, return None on failure."""
        if v is None:
            return None
        try:
            return int(v)
        except (ValueError, TypeError):
            try:
                return float(v)
            except (ValueError, TypeError):
                return None

    start_view = _safe_num(start.get("viewer_count"))
    end_view   = _safe_num(end.get("viewer_count"))

    start_like = _safe_num(start.get("like_count"))
    end_like   = _safe_num(end.get("like_count"))

    duration = (
        phase["time_range"]["end_sec"]
        - phase["time_range"]["start_sec"]
    )

    delta_view = (
        end_view - start_view
        if start_view is not None and end_view is not None
        else None
    )

    delta_like = (
        end_like - start_like
        if start_like is not None and end_like is not None
        else None
    )

    view_velocity = (
        delta_view / duration
        if delta_view is not None and duration > 0
        else None
    )

    like_velocity = (
        delta_like / duration
        if delta_like is not None and duration > 0
        else None
    )

    avg_view = (
        (start_view + end_view) / 2
        if start_view is not None and end_view is not None
        else None
    )

    like_per_viewer = (
        delta_like / avg_view
        if delta_like is not None and avg_view and avg_view > 0
        else None
    )

    return {
        "delta_view": delta_view,
        "view_velocity": view_velocity,
        "delta_like": delta_like,
        "like_velocity": like_velocity,
        "like_per_viewer": like_per_viewer
    }


# =========================
# ATTENTION SCORE
# =========================

def compute_attention_score(m):
    score = 0.0

    if m["view_velocity"] is not None:
        score += 0.4 * m["view_velocity"]

    if m["like_velocity"] is not None:
        score += 0.3 * m["like_velocity"]

    if m["like_per_viewer"] is not None:
        score += 0.2 * m["like_per_viewer"]

    if m["delta_view"] is not None:
        score += 0.1 * m["delta_view"]

    return score


def compute_combined_score(phase, attention_metrics):
    """
    Compute combined score: attention_score + ML model prediction.
    If ML model is not available, falls back to attention_score only.
    
    Weighting:
      - attention_score: 40% (real-time engagement signals)
      - ml_score: 60% (learned quality prediction from historical data)
    """
    attention_score = compute_attention_score(attention_metrics)
    
    scorer = _get_ml_scorer()
    if scorer is None:
        return attention_score
    
    try:
        from ml_scorer import extract_phase_features_for_ml
        features = extract_phase_features_for_ml(phase)
        ml_score = scorer.predict_combined(features)
        
        if ml_score is not None:
            # Normalize attention_score to [0, 1] range for fair weighting
            # attention_score can be negative/large, so we use sigmoid-like normalization
            import math
            norm_attention = 1 / (1 + math.exp(-attention_score * 0.1))
            combined = 0.4 * norm_attention + 0.6 * ml_score
            return combined
    except Exception as e:
        logger.debug(f"[best_phase] ML scoring failed for phase: {e}")
    
    return attention_score


# =========================
# LOAD / SAVE
# =========================

def load_group_best_phases(art_root: str, video_id: str):
    path = get_best_phase_file(art_root, video_id)

    if not os.path.exists(path):
        return {
            "version": "v1_attention",
            "groups": {}
        }

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_group_best_phases(best_data, art_root: str, video_id: str):
    root = get_group_root(art_root, video_id)
    path = get_best_phase_file(art_root, video_id)

    os.makedirs(root, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(best_data, f, ensure_ascii=False, indent=2)

    print(f"[OK] Best phases saved → {path}")


# =========================
# STEP 8 – UPDATE BEST PHASES
# =========================

def update_group_best_phases(phase_units, best_data, video_id):
    n_skipped_ng = 0
    for p in phase_units:
        group_id = p.get("group_id")
        if not group_id:
            continue

        # v6: Skip NG (unusable) phases from best-phase candidates
        if p.get("is_unusable"):
            n_skipped_ng += 1
            continue

        group_id = str(group_id)

        metrics = extract_attention_metrics(p)
        score = compute_combined_score(p, metrics)

        # Prefer DB-generated phase_id if available, otherwise keep legacy string
        phase_id_val = p.get("phase_id") or f"{video_id}_phase_{p['phase_index']}"

        phase_entry = {
            "phase_id": phase_id_val,
            "video_id": video_id,
            "phase_index": p["phase_index"],
            "score": score,
            "metrics": metrics
        }

        group = best_data["groups"].setdefault(
            group_id,
            {
                "top_k": TOP_K,
                "phases": []
            }
        )

        group["phases"].append(phase_entry)

        # sort giảm dần theo score
        group["phases"].sort(
            key=lambda x: x["score"],
            reverse=True
        )

        # giữ top K
        group["phases"] = group["phases"][:TOP_K]

    if n_skipped_ng > 0:
        print(f"[best_phase] Skipped {n_skipped_ng} NG (unusable) phases from best-phase candidates")

    return best_data
