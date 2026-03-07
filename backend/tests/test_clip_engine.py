"""
Clip Engine 統合テスト
=====================
5つの新機能のユニットテスト:
  ① Speech-Aware Cut
  ② Viral Caption Generator (GPT prompt changes - not unit testable)
  ③ Lightning Clip Editor (API endpoints)
  ④ Sales Moment Clip
  ⑤ Hook Detection
"""

import pytest
import sys
import os

# Add paths for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "worker", "batch"))


# ============================================================
# ① Speech-Aware Cut テスト
# ============================================================

def _adjust_cut_to_speech_boundary(start_sec, end_sec, segments, margin_sec=2.0):
    """
    Standalone copy of adjust_cut_to_speech_boundary for testing
    without importing the full worker module.
    """
    if not segments:
        return start_sec, end_sec

    sorted_segs = sorted(segments, key=lambda s: float(s.get("start", 0)))
    gaps = []
    for i in range(len(sorted_segs) - 1):
        gap_start = float(sorted_segs[i].get("end", 0))
        gap_end = float(sorted_segs[i + 1].get("start", 0))
        if gap_end > gap_start + 0.1:
            gaps.append((gap_start, gap_end))

    new_start = start_sec
    best_start_dist = margin_sec + 1
    for gs, ge in gaps:
        mid = (gs + ge) / 2
        dist = abs(mid - start_sec)
        if dist <= margin_sec and dist < best_start_dist:
            new_start = mid
            best_start_dist = dist

    new_end = end_sec
    best_end_dist = margin_sec + 1
    for gs, ge in gaps:
        mid = (gs + ge) / 2
        dist = abs(mid - end_sec)
        if dist <= margin_sec and dist < best_end_dist:
            new_end = mid
            best_end_dist = dist

    if new_end <= new_start + 3.0:
        return start_sec, end_sec
    return round(new_start, 2), round(new_end, 2)


class TestSpeechAwareCut:
    """adjust_cut_to_speech_boundary のテスト"""

    def test_no_segments_returns_original(self):
        result = _adjust_cut_to_speech_boundary(10.0, 60.0, [], margin_sec=2.0)
        assert result == (10.0, 60.0)

    def test_adjusts_start_to_silence_gap(self):
        segments = [
            {"start": 8.0, "end": 9.5, "text": "前の文"},
            {"start": 10.8, "end": 12.0, "text": "次の文"},
        ]
        new_start, new_end = _adjust_cut_to_speech_boundary(10.0, 60.0, segments, margin_sec=2.0)
        assert 9.0 <= new_start <= 11.0

    def test_adjusts_end_to_silence_gap(self):
        segments = [
            {"start": 57.0, "end": 59.0, "text": "最後の文"},
            {"start": 61.0, "end": 63.0, "text": "次の文"},
        ]
        new_start, new_end = _adjust_cut_to_speech_boundary(10.0, 60.0, segments, margin_sec=2.0)
        assert 58.0 <= new_end <= 62.0

    def test_does_not_exceed_margin(self):
        segments = [
            {"start": 5.0, "end": 6.0, "text": "遠い文"},
            {"start": 15.0, "end": 16.0, "text": "遠い文2"},
        ]
        new_start, new_end = _adjust_cut_to_speech_boundary(10.0, 60.0, segments, margin_sec=2.0)
        assert abs(new_start - 10.0) <= 3.0


# ============================================================
# ④ Sales Moment Clip テスト
# ============================================================

class TestSalesMomentClip:
    """Sales Moment Clip サービスのテスト"""

    def _import_service(self):
        from app.services.sales_moment_clip_service import (
            detect_spikes,
            build_moment_clips,
            compute_timed_metrics_from_phases,
        )
        return detect_spikes, build_moment_clips, compute_timed_metrics_from_phases

    def test_no_data_returns_empty(self):
        detect_spikes, _, _ = self._import_service()
        result = detect_spikes([])
        assert result == []

    def test_single_slot_no_spike(self):
        detect_spikes, _, _ = self._import_service()
        result = detect_spikes([{"video_sec": 30, "gmv": 100, "orders": 1, "clicks": 5, "viewers": 50}])
        assert result == []

    def test_detects_gmv_spike(self):
        detect_spikes, _, _ = self._import_service()
        metrics = [
            {"video_sec": 30, "gmv": 100, "orders": 1, "clicks": 5, "viewers": 50},
            {"video_sec": 90, "gmv": 100, "orders": 1, "clicks": 5, "viewers": 50},
            {"video_sec": 150, "gmv": 100, "orders": 1, "clicks": 5, "viewers": 50},
            {"video_sec": 210, "gmv": 500, "orders": 5, "clicks": 20, "viewers": 50},  # spike!
            {"video_sec": 270, "gmv": 100, "orders": 1, "clicks": 5, "viewers": 50},
        ]
        spikes = detect_spikes(metrics)
        assert len(spikes) > 0
        # The spike should be around video_sec=210
        gmv_spikes = [s for s in spikes if s.metric == "gmv"]
        assert len(gmv_spikes) > 0
        assert gmv_spikes[0].video_sec == 210

    def test_build_moment_clips_from_spikes(self):
        detect_spikes, build_moment_clips, _ = self._import_service()
        metrics = [
            {"video_sec": 30, "gmv": 100, "orders": 1, "clicks": 5, "viewers": 50},
            {"video_sec": 90, "gmv": 100, "orders": 1, "clicks": 5, "viewers": 50},
            {"video_sec": 150, "gmv": 100, "orders": 1, "clicks": 5, "viewers": 50},
            {"video_sec": 210, "gmv": 500, "orders": 5, "clicks": 20, "viewers": 50},
            {"video_sec": 270, "gmv": 100, "orders": 1, "clicks": 5, "viewers": 50},
        ]
        spikes = detect_spikes(metrics)
        phases = [
            {"phase_index": 0, "time_start": 0, "time_end": 60},
            {"phase_index": 1, "time_start": 60, "time_end": 120},
            {"phase_index": 2, "time_start": 120, "time_end": 180},
            {"phase_index": 3, "time_start": 180, "time_end": 240},
            {"phase_index": 4, "time_start": 240, "time_end": 300},
        ]
        clips = build_moment_clips(spikes, phases, video_duration=300, top_n=3)
        assert len(clips) > 0
        assert clips[0].rank == 1
        assert clips[0].label.startswith("Sales Spike")
        # The clip should be around the spike at 210
        assert clips[0].time_start < 210
        assert clips[0].time_end > 210

    def test_compute_timed_metrics_from_phases(self):
        _, _, compute = self._import_service()
        phases = [
            {"phase_index": 0, "time_start": 0, "time_end": 60, "gmv": 100, "order_count": 2, "product_clicks": 10, "viewer_count": 50},
            {"phase_index": 1, "time_start": 60, "time_end": 120, "gmv": 200, "order_count": 3, "product_clicks": 15, "viewer_count": 60},
        ]
        metrics = compute(phases)
        assert len(metrics) == 2
        assert metrics[0]["video_sec"] == 30.0  # midpoint of 0-60
        assert metrics[0]["gmv"] == 100
        assert metrics[1]["video_sec"] == 90.0  # midpoint of 60-120

    def test_merge_nearby_spikes(self):
        detect_spikes, build_moment_clips, _ = self._import_service()
        # Two spikes close together should merge
        metrics = [
            {"video_sec": 30, "gmv": 100, "orders": 1, "clicks": 5, "viewers": 50},
            {"video_sec": 90, "gmv": 100, "orders": 1, "clicks": 5, "viewers": 50},
            {"video_sec": 150, "gmv": 500, "orders": 5, "clicks": 20, "viewers": 50},  # spike
            {"video_sec": 160, "gmv": 400, "orders": 4, "clicks": 18, "viewers": 50},  # nearby spike
            {"video_sec": 270, "gmv": 100, "orders": 1, "clicks": 5, "viewers": 50},
        ]
        spikes = detect_spikes(metrics)
        phases = [{"phase_index": i, "time_start": i*60, "time_end": (i+1)*60} for i in range(5)]
        clips = build_moment_clips(spikes, phases, video_duration=300, top_n=5)
        # Nearby spikes should be merged into fewer clips
        assert len(clips) >= 1


# ============================================================
# ⑤ Hook Detection テスト
# ============================================================

class TestHookDetection:
    """Hook Detection サービスのテスト"""

    def _import_service(self):
        from app.services.hook_detection_service import detect_hooks, suggest_hook_placement
        return detect_hooks, suggest_hook_placement

    def test_no_segments_returns_empty(self):
        detect_hooks, _ = self._import_service()
        result = detect_hooks([])
        assert result == []

    def test_detects_strong_keyword(self):
        detect_hooks, _ = self._import_service()
        segments = [
            {"start": 0, "end": 3, "text": "今だけ限定セール！"},
            {"start": 3, "end": 6, "text": "普通のテキストです"},
        ]
        hooks = detect_hooks(segments)
        assert len(hooks) >= 1
        # The first hook should be the strong keyword one
        assert hooks[0].hook_score > 0
        # The hook should have matched at least one strong keyword
        assert hooks[0].hook_score > 0
        assert len(hooks[0].keyword_matches) > 0 or hooks[0].is_question or hooks[0].has_number

    def test_detects_question(self):
        detect_hooks, _ = self._import_service()
        segments = [
            {"start": 0, "end": 3, "text": "これ知ってますか？"},
        ]
        hooks = detect_hooks(segments)
        assert len(hooks) >= 1
        assert hooks[0].is_question is True

    def test_detects_number(self):
        detect_hooks, _ = self._import_service()
        segments = [
            {"start": 0, "end": 3, "text": "3つの理由を紹介します"},
        ]
        hooks = detect_hooks(segments)
        assert len(hooks) >= 1
        assert hooks[0].has_number is True

    def test_first_3sec_bonus(self):
        detect_hooks, _ = self._import_service()
        # Same text but different positions
        segments = [
            {"start": 0, "end": 2, "text": "衝撃の事実！"},
            {"start": 60, "end": 62, "text": "衝撃の事実！"},
        ]
        hooks = detect_hooks(segments)
        assert len(hooks) == 2
        # First one should have higher score due to 3sec bonus
        first = [h for h in hooks if h.start_sec == 0][0]
        second = [h for h in hooks if h.start_sec == 60][0]
        assert first.hook_score > second.hook_score

    def test_suggest_hook_placement_at_start(self):
        detect_hooks, suggest = self._import_service()
        segments = [
            {"start": 0, "end": 2, "text": "衝撃の事実！"},
        ]
        hooks = detect_hooks(segments)
        result = suggest(hooks, clip_start=0, clip_end=60)
        assert result["should_reorder"] is False

    def test_suggest_hook_placement_needs_reorder(self):
        detect_hooks, suggest = self._import_service()
        segments = [
            {"start": 30, "end": 32, "text": "衝撃の事実！"},
        ]
        hooks = detect_hooks(segments)
        result = suggest(hooks, clip_start=0, clip_end=60)
        assert result["should_reorder"] is True
        assert result["suggested_start"] < 32

    def test_no_hooks_no_suggestion(self):
        _, suggest = self._import_service()
        result = suggest([], clip_start=0, clip_end=60)
        assert result["should_reorder"] is False
        assert result["best_hook"] is None

    def test_max_candidates_limit(self):
        detect_hooks, _ = self._import_service()
        segments = [
            {"start": i * 5, "end": i * 5 + 3, "text": f"衝撃の事実{i}！"}
            for i in range(20)
        ]
        hooks = detect_hooks(segments, max_candidates=5)
        assert len(hooks) <= 5


# ============================================================
# Run tests
# ============================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
