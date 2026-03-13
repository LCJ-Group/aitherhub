"""
SalesMomentClipService
======================
売上・注文・クリック・視聴者のスパイク（急増）を検出し、
その瞬間を中心にクリップ候補を自動生成するサービス。

既存の sales_clip_service.py がフェーズ単位のスコアリングであるのに対し、
このサービスは「時系列データのスパイク」から直接クリップ候補を生成する。

スパイク検出アルゴリズム
──────────────────────
  1. 各メトリクス（GMV, orders, clicks, viewers）の時系列を取得
  2. 移動平均（window=3スロット）を計算
  3. 移動平均の 1.5倍 以上のスロットをスパイクとして検出
  4. 複数メトリクスのスパイクが重なる場合はスコアを加算
  5. スパイク前後 ±15秒 をクリップ候補区間とする
  6. 重複する候補はマージ

CTA フォールバック
──────────────────
  CSVメトリクス（GMV, orders, clicks, viewers）が全て0の場合、
  CTA スコアを代替メトリクスとしてスパイク検出を行う。
  CTA >= 4 のフェーズを「盛り上がり」として検出する。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# ── 設定 ────────────────────────────────────────────────
SPIKE_WINDOW = 3          # 移動平均のウィンドウサイズ（スロット数）
SPIKE_THRESHOLD = 1.5     # 移動平均の何倍でスパイクとするか
CLIP_PADDING_SEC = 15.0   # スパイク前後のパディング（秒）
MIN_CLIP_SEC = 10.0       # 最小クリップ長（秒）
MAX_CLIP_SEC = 90.0       # 最大クリップ長（秒）
MERGE_GAP_SEC = 10.0      # この秒数以内のスパイクはマージ
TOP_N_DEFAULT = 5         # デフォルト候補数

# スパイクの重み
SPIKE_WEIGHT = {
    "gmv": 40.0,
    "orders": 25.0,
    "clicks": 20.0,
    "viewers": 15.0,
}

# CTA フォールバック用の重み
CTA_SPIKE_WEIGHT = {
    "cta": 30.0,
}


@dataclass
class SpikeEvent:
    """検出されたスパイクイベント"""
    video_sec: float          # 動画内の秒数
    metric: str               # "gmv" | "orders" | "clicks" | "viewers" | "cta"
    value: float              # スパイク時の値
    moving_avg: float         # 移動平均値
    spike_ratio: float        # value / moving_avg
    score: float              # 重み付きスコア


@dataclass
class SalesMomentCandidate:
    """売れた瞬間クリップ候補"""
    rank: int
    time_start: float
    time_end: float
    duration: float
    score: float
    spike_events: list[SpikeEvent]
    label: str                # "Sales Spike #1" etc.
    primary_metric: str       # 最も強いスパイクのメトリクス
    summary: str              # 人間向けサマリー
    # 既存 API 互換
    phase_index: int          # 最も近いフェーズのインデックス


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _moving_average(values: list[float], window: int = SPIKE_WINDOW) -> list[float]:
    """移動平均を計算する"""
    result = []
    for i in range(len(values)):
        start = max(0, i - window + 1)
        window_vals = values[start:i + 1]
        result.append(sum(window_vals) / len(window_vals) if window_vals else 0.0)
    return result


def _has_any_csv_data(timed_metrics: list[dict]) -> bool:
    """CSVメトリクスに有効なデータがあるかチェック"""
    for metric_key in SPIKE_WEIGHT:
        values = [_safe_float(m.get(metric_key, 0)) for m in timed_metrics]
        if max(values, default=0) > 0:
            return True
    return False


def detect_spikes(
    timed_metrics: list[dict],
    threshold: float = SPIKE_THRESHOLD,
) -> list[SpikeEvent]:
    """
    時系列メトリクスからスパイクを検出する。

    CSVメトリクス（gmv, orders, clicks, viewers）が全て0の場合、
    CTAスコアをフォールバックメトリクスとして使用する。

    Parameters
    ----------
    timed_metrics : list[dict]
        時系列データ。各要素は:
        {
            "video_sec": float,
            "gmv": float,
            "orders": float,
            "clicks": float,
            "viewers": float,
            "cta": float,  # optional, used as fallback
        }
    threshold : float
        移動平均の何倍以上でスパイクとするか

    Returns
    -------
    list[SpikeEvent]
        検出されたスパイクのリスト（スコア降順）
    """
    if not timed_metrics or len(timed_metrics) < 2:
        return []

    spikes: list[SpikeEvent] = []

    # まずCSVメトリクスでスパイク検出を試みる
    has_csv = _has_any_csv_data(timed_metrics)

    if has_csv:
        # 通常のCSVメトリクスベースのスパイク検出
        weight_map = SPIKE_WEIGHT
    else:
        # CSVメトリクスが全て0 → CTAスコアをフォールバックとして使用
        weight_map = CTA_SPIKE_WEIGHT

    for metric_key, weight in weight_map.items():
        values = [_safe_float(m.get(metric_key, 0)) for m in timed_metrics]
        video_secs = [_safe_float(m.get("video_sec", 0)) for m in timed_metrics]

        # 全て0なら skip
        if max(values) <= 0:
            continue

        ma = _moving_average(values)

        for i, (val, avg) in enumerate(zip(values, ma)):
            if avg <= 0:
                continue
            ratio = val / avg
            if ratio >= threshold and val > 0:
                score = (ratio - 1.0) * weight * (val / max(values))
                spikes.append(SpikeEvent(
                    video_sec=video_secs[i],
                    metric=metric_key,
                    value=val,
                    moving_avg=round(avg, 2),
                    spike_ratio=round(ratio, 2),
                    score=round(score, 2),
                ))

    # CTAフォールバック: スパイクが見つからない場合、CTA >= 4 のフェーズを直接候補にする
    if not spikes and not has_csv:
        cta_values = [_safe_float(m.get("cta", 0)) for m in timed_metrics]
        video_secs = [_safe_float(m.get("video_sec", 0)) for m in timed_metrics]
        max_cta = max(cta_values, default=0)

        if max_cta >= 4:
            for i, (cta, vsec) in enumerate(zip(cta_values, video_secs)):
                if cta >= 4:
                    # CTA 4-5 を直接スパイクとして扱う
                    score = cta * 10.0 * (cta / max_cta)
                    spikes.append(SpikeEvent(
                        video_sec=vsec,
                        metric="cta",
                        value=cta,
                        moving_avg=round(sum(cta_values) / len(cta_values), 2),
                        spike_ratio=round(cta / (sum(cta_values) / len(cta_values)), 2) if sum(cta_values) > 0 else 1.0,
                        score=round(score, 2),
                    ))

    # スコア降順でソート
    spikes.sort(key=lambda x: x.score, reverse=True)
    return spikes


def build_moment_clips(
    spikes: list[SpikeEvent],
    phases: list[dict],
    video_duration: float = 0.0,
    top_n: int = TOP_N_DEFAULT,
    padding_sec: float = CLIP_PADDING_SEC,
    merge_gap_sec: float = MERGE_GAP_SEC,
) -> list[SalesMomentCandidate]:
    """
    スパイクイベントからクリップ候補を生成する。

    Parameters
    ----------
    spikes : list[SpikeEvent]
        detect_spikes() の結果
    phases : list[dict]
        video_phases テーブルの行
    video_duration : float
        動画の総秒数（クランプ用）
    top_n : int
        候補数
    padding_sec : float
        スパイク前後のパディング（秒）
    merge_gap_sec : float
        この秒数以内のスパイクはマージ

    Returns
    -------
    list[SalesMomentCandidate]
        クリップ候補のリスト
    """
    if not spikes:
        return []

    # スパイクをクリップ区間に変換
    raw_clips: list[dict] = []
    for spike in spikes:
        t_start = max(0, spike.video_sec - padding_sec)
        t_end = spike.video_sec + padding_sec
        if video_duration > 0:
            t_end = min(t_end, video_duration)
        # 最小クリップ長の保証
        if t_end - t_start < MIN_CLIP_SEC:
            t_end = t_start + MIN_CLIP_SEC
        raw_clips.append({
            "start": t_start,
            "end": t_end,
            "spikes": [spike],
            "score": spike.score,
        })

    # 重複するクリップをマージ
    merged: list[dict] = []
    for clip in raw_clips:
        merged_flag = False
        for existing in merged:
            # 重複チェック: 開始・終了が merge_gap_sec 以内
            if (clip["start"] <= existing["end"] + merge_gap_sec and
                    clip["end"] >= existing["start"] - merge_gap_sec):
                # マージ
                existing["start"] = min(existing["start"], clip["start"])
                existing["end"] = max(existing["end"], clip["end"])
                existing["spikes"].extend(clip["spikes"])
                existing["score"] = max(existing["score"], clip["score"])
                merged_flag = True
                break
        if not merged_flag:
            merged.append(dict(clip))

    # 最大クリップ長の制限
    for clip in merged:
        if clip["end"] - clip["start"] > MAX_CLIP_SEC:
            # 最高スコアのスパイクを中心に切り詰め
            best_spike = max(clip["spikes"], key=lambda s: s.score)
            center = best_spike.video_sec
            clip["start"] = max(0, center - MAX_CLIP_SEC / 2)
            clip["end"] = clip["start"] + MAX_CLIP_SEC

    # スコア降順でソート
    merged.sort(key=lambda x: x["score"], reverse=True)

    # TOP-N に絞り込み
    top_clips = merged[:top_n]

    # フェーズマッピング
    def _find_phase_index(video_sec: float) -> int:
        for p in phases:
            t_start = _safe_float(p.get("time_start"))
            t_end = _safe_float(p.get("time_end"))
            if t_start <= video_sec < t_end:
                return int(p.get("phase_index", 0))
        return 0

    # SalesMomentCandidate に変換
    candidates: list[SalesMomentCandidate] = []
    for i, clip in enumerate(top_clips, 1):
        best_spike = max(clip["spikes"], key=lambda s: s.score)
        phase_idx = _find_phase_index(best_spike.video_sec)

        # サマリー生成
        summary_parts = []
        metrics_seen = set()
        for spike in sorted(clip["spikes"], key=lambda s: s.score, reverse=True)[:3]:
            if spike.metric not in metrics_seen:
                metrics_seen.add(spike.metric)
                label = {
                    "gmv": "売上",
                    "orders": "注文",
                    "clicks": "クリック",
                    "viewers": "視聴者",
                    "cta": "CTA",
                }.get(spike.metric, spike.metric)
                summary_parts.append(
                    f"{label} {spike.spike_ratio:.1f}x スパイク"
                )

        candidates.append(SalesMomentCandidate(
            rank=i,
            time_start=round(clip["start"], 1),
            time_end=round(clip["end"], 1),
            duration=round(clip["end"] - clip["start"], 1),
            score=round(clip["score"], 2),
            spike_events=clip["spikes"],
            label=f"Sales Spike #{i}",
            primary_metric=best_spike.metric,
            summary=" / ".join(summary_parts) if summary_parts else "スパイク検出",
            phase_index=phase_idx,
        ))

    return candidates


def compute_timed_metrics_from_phases(phases: list[dict]) -> list[dict]:
    """
    video_phases テーブルのデータから時系列メトリクスを構築する。
    各フェーズを1スロットとして扱う。

    CSVメトリクスが全て0の場合に備え、CTAスコアも含める。
    """
    metrics = []
    for p in phases:
        t_start = _safe_float(p.get("time_start"))
        t_end = _safe_float(p.get("time_end"))
        mid = (t_start + t_end) / 2

        metrics.append({
            "video_sec": mid,
            "gmv": _safe_float(p.get("gmv")),
            "orders": _safe_float(p.get("order_count")),
            "clicks": _safe_float(p.get("product_clicks")),
            "viewers": _safe_float(p.get("viewer_count")),
            "cta": _safe_float(p.get("cta_score")),
        })

    return metrics
