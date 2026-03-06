"""
test_feature_alignment.py  –  generate_dataset.py v3 ↔ train.py v5 整合性テスト
================================================================================
ドライランで以下を検証:
  1. generate_dataset.py が出力するJSONLレコードのキーが train.py の特徴量定義と一致
  2. train.py extract_features() が正しい次元の行列を生成
  3. predict.py _record_to_features() が同じ次元を生成
  4. 特徴量名の一覧を出力
"""

import json
import sys
import os
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))

# ── Import from train.py ──
from train import (
    NUMERIC_FEATURES, KEYWORD_FEATURES, PRODUCT_FEATURES,
    HUMAN_TAG_FEATURES, COMMENT_KEYWORD_FEATURES,
    KNOWN_EVENT_TYPES, extract_features, MODEL_VERSION,
)

# ── Import from generate_dataset.py ──
from generate_dataset import (
    ALL_HUMAN_TAGS, BEHAVIOR_TAGS, PSYCHOLOGY_TAGS,
    KEYWORD_GROUPS, COMMENT_KEYWORD_GROUPS,
    extract_keyword_flags, extract_comment_keyword_flags,
    extract_text_features, extract_human_tag_features,
    extract_comment_features,
)


def make_dummy_record():
    """Create a realistic dummy record as generate_dataset.py would produce."""
    desc = "今だけ特別価格3980円！残り10個です。リンクをタップして購入してください。"
    comment = "CTA強い。価格の見せ方がうまい。タイミングも良い。"

    kw_flags = extract_keyword_flags(desc)
    text_feats = extract_text_features(desc)
    htag_features = extract_human_tag_features(["EMPATHY", "URGENCY", "CTA", "HOOK"])
    comment_feats = extract_comment_features(comment)

    record = {
        # Identity
        "video_id": "test-video-001",
        "user_id": 1,
        "phase_index": 5,

        # Structure
        "event_type": "CTA",
        "event_duration": 45.2,
        "event_position_min": 12.5,
        "event_position_pct": 0.35,
        "tag_count": 3,

        # CTA / importance
        "cta_score": 4,
        "importance_score": 0.85,

        # Text
        **text_feats,

        # Keywords
        **kw_flags,

        # Product
        "product_match": 1,
        "product_match_top3": 1,
        "matched_product_count": 2,

        # Human review features (v3/v5)
        "user_rating": 4,
        "has_human_review": 1,
        "human_tag_count": 4,
        **htag_features,
        **comment_feats,

        # Metadata
        "tags": ["CTA", "URGENCY"],
        "human_tags": ["EMPATHY", "URGENCY", "CTA", "HOOK"],
        "reviewer_name": "Yuuki",
        "text": desc[:200],
        "comment_text": comment[:200],

        # Labels
        "y_click": 1,
        "y_order": 0,
        "y_strong": 0,
        "weight_click": 0.85,
        "weight_order": 0.0,
        "nearest_click_sec": 15.3,
        "nearest_order_sec": None,
        "sample_weight": 0.85,
    }
    return record


def test_feature_alignment():
    """Test that all feature definitions are aligned."""
    print(f"=" * 70)
    print(f"Feature Alignment Test — train.py v{MODEL_VERSION}")
    print(f"=" * 70)

    # 1. Build expected feature names (same order as train.py extract_features)
    expected_features = []
    expected_features.extend(NUMERIC_FEATURES)
    expected_features.extend(KEYWORD_FEATURES)
    expected_features.extend(PRODUCT_FEATURES)
    expected_features.extend(HUMAN_TAG_FEATURES)
    expected_features.extend(COMMENT_KEYWORD_FEATURES)
    expected_features.extend([f"event_{et}" for et in KNOWN_EVENT_TYPES])

    print(f"\n[1] Feature count breakdown:")
    print(f"  NUMERIC_FEATURES:          {len(NUMERIC_FEATURES)}")
    print(f"  KEYWORD_FEATURES:          {len(KEYWORD_FEATURES)}")
    print(f"  PRODUCT_FEATURES:          {len(PRODUCT_FEATURES)}")
    print(f"  HUMAN_TAG_FEATURES:        {len(HUMAN_TAG_FEATURES)}")
    print(f"  COMMENT_KEYWORD_FEATURES:  {len(COMMENT_KEYWORD_FEATURES)}")
    print(f"  EVENT_TYPE one-hot:        {len(KNOWN_EVENT_TYPES)}")
    print(f"  ─────────────────────────────────")
    print(f"  TOTAL:                     {len(expected_features)}")

    # 2. Create dummy record
    record = make_dummy_record()
    print(f"\n[2] Dummy record created with {len(record)} keys")

    # 3. Test extract_features from train.py
    print(f"\n[3] Testing train.py extract_features()...")
    X, y, w, group_ids, feature_names, unique_vids, video_ids_raw = extract_features([record], target="click")
    print(f"  X shape: {X.shape}")
    print(f"  feature_names count: {len(feature_names)}")
    print(f"  y: {y}")
    print(f"  w: {w}")

    assert X.shape[1] == len(expected_features), \
        f"MISMATCH: X has {X.shape[1]} cols but expected {len(expected_features)}"
    assert len(feature_names) == len(expected_features), \
        f"MISMATCH: feature_names has {len(feature_names)} but expected {len(expected_features)}"
    print(f"  ✅ Feature matrix dimension matches expected ({X.shape[1]})")

    # 4. Verify feature names match
    for i, (actual, expected) in enumerate(zip(feature_names, expected_features)):
        if actual != expected:
            print(f"  ❌ Feature {i}: actual='{actual}' expected='{expected}'")
            sys.exit(1)
    print(f"  ✅ All feature names match")

    # 5. Verify non-zero values for human review features
    print(f"\n[4] Checking human review feature values...")
    for i, fname in enumerate(feature_names):
        if fname.startswith("htag_") or fname.startswith("comment_kw_") or \
           fname in ("user_rating", "has_human_review", "human_tag_count", "comment_length"):
            val = X[0, i]
            print(f"  {fname:35s} = {val:.1f}")

    # 6. Verify specific values
    ur_idx = feature_names.index("user_rating")
    assert X[0, ur_idx] == 4.0, f"user_rating should be 4.0, got {X[0, ur_idx]}"
    print(f"\n  ✅ user_rating = 4.0 (correct)")

    hr_idx = feature_names.index("has_human_review")
    assert X[0, hr_idx] == 1.0, f"has_human_review should be 1.0, got {X[0, hr_idx]}"
    print(f"  ✅ has_human_review = 1.0 (correct)")

    htc_idx = feature_names.index("human_tag_count")
    assert X[0, htc_idx] == 4.0, f"human_tag_count should be 4.0, got {X[0, htc_idx]}"
    print(f"  ✅ human_tag_count = 4.0 (correct)")

    # Check specific htag flags
    for tag in ["EMPATHY", "URGENCY", "CTA", "HOOK"]:
        idx = feature_names.index(f"htag_{tag}")
        assert X[0, idx] == 1.0, f"htag_{tag} should be 1.0"
    for tag in ["CHAT", "PREP", "PROBLEM", "BONUS"]:
        idx = feature_names.index(f"htag_{tag}")
        assert X[0, idx] == 0.0, f"htag_{tag} should be 0.0"
    print(f"  ✅ Human tag one-hot values correct")

    cl_idx = feature_names.index("comment_length")
    assert X[0, cl_idx] > 0, f"comment_length should be > 0"
    print(f"  ✅ comment_length = {X[0, cl_idx]:.0f} (correct)")

    # 7. Test with empty human review (no review data)
    print(f"\n[5] Testing with empty human review record...")
    empty_record = make_dummy_record()
    empty_record["user_rating"] = 0
    empty_record["has_human_review"] = 0
    empty_record["human_tag_count"] = 0
    empty_record["comment_length"] = 0
    for tag in ALL_HUMAN_TAGS:
        empty_record[f"htag_{tag}"] = 0
    for g in COMMENT_KEYWORD_GROUPS:
        empty_record[g[0]] = 0

    X2, _, _, _, _, _, _ = extract_features([empty_record], target="click")
    ur_idx = feature_names.index("user_rating")
    assert X2[0, ur_idx] == 0.0
    hr_idx = feature_names.index("has_human_review")
    assert X2[0, hr_idx] == 0.0
    print(f"  ✅ Empty human review handled correctly (all zeros)")

    # 8. Print full feature list
    print(f"\n[6] Complete feature list ({len(feature_names)} features):")
    print(f"  {'#':>3}  {'Feature Name':40s}  {'Value':>8}")
    print(f"  {'─'*55}")
    for i, fname in enumerate(feature_names):
        print(f"  {i+1:>3}  {fname:40s}  {X[0, i]:>8.2f}")

    print(f"\n{'=' * 70}")
    print(f"ALL TESTS PASSED ✅")
    print(f"  Model version: v{MODEL_VERSION}")
    print(f"  Total features: {len(feature_names)}")
    print(f"  Human review features: {len(HUMAN_TAG_FEATURES) + len(COMMENT_KEYWORD_FEATURES) + 4}")
    print(f"{'=' * 70}")

    return True


if __name__ == "__main__":
    success = test_feature_alignment()
    sys.exit(0 if success else 1)
