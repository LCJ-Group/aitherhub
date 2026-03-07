"""
SalesClipService
================
各フェーズに sales_score を付与し、売上につながる可能性が高い
クリップ候補（TOP3〜5）を自動抽出するサービス。

スコア設計（合計 100 点満点）
──────────────────────────────
  GMV（売上）           30 pt  最大値で正規化
  注文数                20 pt  最大値で正規化
  クリック数            15 pt  最大値で正規化
  視聴者数              10 pt  最大値で正規化
  sales_moments 密度     10 pt  フェーズ内の moment 数
  CTA スコア             5 pt  cta_score (1-5) → 0-5 pt
  human_rating           5 pt  user_rating (1-5) → 0-5 pt
  purchase_popup 存在    3 pt  moment_type = purchase_popup
  価格提示タグ           2 pt  sales_psychology_tags に price_mention

クラスタリング
──────────────
  隣接フェーズ（gap ≤ 30 秒）は 1 クリップに統合。
  統合後のクリップ長が 3 分を超える場合は先頭 3 分に切り詰め。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

# ── スコアの重み定義 ──────────────────────────────────────
WEIGHT_GMV = 30.0
WEIGHT_ORDER = 20.0
WEIGHT_CLICK = 15.0
WEIGHT_VIEWER = 10.0
WEIGHT_MOMENTS = 10.0
WEIGHT_CTA = 5.0
WEIGHT_HUMAN = 5.0
WEIGHT_PURCHASE_POPUP = 3.0
WEIGHT_PRICE_MENTION = 2.0

MAX_CLIP_DURATION = 180.0   # 3 分
CLUSTER_GAP_SEC = 30.0      # 隣接フェーズ統合の閾値（秒）
TOP_N_DEFAULT = 5           # デフォルト候補数


# ── データクラス ──────────────────────────────────────────
@dataclass
class PhaseScore:
    phase_index: int
    time_start: float
    time_end: float
    sales_score: float          # 0〜100
    score_breakdown: dict       # 各要素の寄与点
    reasons: list[str]          # 表示用の理由テキスト
    raw: dict = field(default_factory=dict)   # 元データ（デバッグ用）


@dataclass
class ClipCandidate:
    rank: int
    phase_indices: list[int]    # 統合されたフェーズ番号
    time_start: float
    time_end: float
    duration: float
    sales_score: float          # 代表スコア（最高フェーズのスコア）
    score_breakdown: dict
    reasons: list[str]
    label: str                  # "TOP1", "TOP2", …
    # 既存 requestClipGeneration() に渡す用
    phase_index: int            # 代表フェーズ（スコア最高）


# ── ユーティリティ ────────────────────────────────────────
def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _normalize(value: float, max_val: float) -> float:
    """0〜1 に正規化（max_val が 0 なら 0 を返す）"""
    if max_val <= 0:
        return 0.0
    return min(value / max_val, 1.0)


def _parse_tags(tags_raw: Any) -> list[str]:
    """sales_psychology_tags / human_sales_tags を list[str] に変換"""
    if tags_raw is None:
        return []
    if isinstance(tags_raw, list):
        return [str(t) for t in tags_raw]
    if isinstance(tags_raw, str):
        try:
            parsed = json.loads(tags_raw)
            if isinstance(parsed, list):
                return [str(t) for t in parsed]
        except Exception:
            pass
        return [tags_raw]
    return []


# ── メインロジック ────────────────────────────────────────
def compute_sales_scores(
    phases: list[dict],
    sales_moments: list[dict],
) -> list[PhaseScore]:
    """
    各フェーズに sales_score を付与して PhaseScore のリストを返す。

    Parameters
    ----------
    phases : list[dict]
        video_phases テーブルの行（dict 形式）。
        必須キー: phase_index, time_start, time_end
    sales_moments : list[dict]
        video_sales_moments テーブルの行（dict 形式）。
        必須キー: video_sec, moment_type
    """
    if not phases:
        return []

    # ── グローバル最大値を計算（正規化用）
    max_gmv = max((_safe_float(p.get("gmv")) for p in phases), default=0.0)
    max_order = max((_safe_float(p.get("order_count")) for p in phases), default=0.0)
    max_click = max((_safe_float(p.get("product_clicks")) for p in phases), default=0.0)
    max_viewer = max((_safe_float(p.get("viewer_count")) for p in phases), default=0.0)

    # ── sales_moments をフェーズ時間帯にマッピング
    def _moments_in_phase(t_start: float, t_end: float) -> list[dict]:
        return [
            m for m in sales_moments
            if t_start <= _safe_float(m.get("video_sec")) < t_end
        ]

    results: list[PhaseScore] = []

    for p in phases:
        t_start = _safe_float(p.get("time_start"))
        t_end = _safe_float(p.get("time_end"))
        if t_end <= t_start:
            t_end = t_start + 1.0

        # ── 各要素のスコア計算
        gmv = _safe_float(p.get("gmv"))
        order = _safe_float(p.get("order_count"))
        click = _safe_float(p.get("product_clicks"))
        viewer = _safe_float(p.get("viewer_count"))
        cta = _safe_float(p.get("cta_score"))       # 1-5
        rating = _safe_float(p.get("user_rating"))  # 1-5

        phase_moments = _moments_in_phase(t_start, t_end)
        purchase_popup_count = sum(
            1 for m in phase_moments
            if str(m.get("moment_type", "")).lower() == "purchase_popup"
        )

        tags = _parse_tags(p.get("sales_psychology_tags")) + _parse_tags(p.get("human_sales_tags"))
        has_price_mention = any(
            "price" in t.lower() or "価格" in t or "値段" in t or "円" in t
            for t in tags
        )

        # ── 重み付きスコア（合計 100 点満点）
        s_gmv = _normalize(gmv, max_gmv) * WEIGHT_GMV
        s_order = _normalize(order, max_order) * WEIGHT_ORDER
        s_click = _normalize(click, max_click) * WEIGHT_CLICK
        s_viewer = _normalize(viewer, max_viewer) * WEIGHT_VIEWER
        # moments: フェーズ内の moment 数（5 件以上で満点）
        s_moments = min(len(phase_moments) / 5.0, 1.0) * WEIGHT_MOMENTS
        # cta: 1-5 → 0-5 pt（0 は 0 pt）
        s_cta = (max(cta - 1, 0) / 4.0) * WEIGHT_CTA if cta >= 1 else 0.0
        # human rating: 1-5 → 0-5 pt
        s_human = (max(rating - 1, 0) / 4.0) * WEIGHT_HUMAN if rating >= 1 else 0.0
        s_popup = WEIGHT_PURCHASE_POPUP if purchase_popup_count > 0 else 0.0
        s_price = WEIGHT_PRICE_MENTION if has_price_mention else 0.0

        total = s_gmv + s_order + s_click + s_viewer + s_moments + s_cta + s_human + s_popup + s_price
        total = round(min(total, 100.0), 2)

        breakdown = {
            "gmv": round(s_gmv, 2),
            "order": round(s_order, 2),
            "click": round(s_click, 2),
            "viewer": round(s_viewer, 2),
            "moments": round(s_moments, 2),
            "cta": round(s_cta, 2),
            "human_rating": round(s_human, 2),
            "purchase_popup": round(s_popup, 2),
            "price_mention": round(s_price, 2),
        }

        # ── 理由テキスト生成（上位 3 要素を日本語で表示）
        reasons = _build_reasons(
            gmv=gmv, order=order, click=click, viewer=viewer,
            moments=phase_moments, cta=cta, rating=rating,
            purchase_popup=purchase_popup_count, has_price=has_price_mention,
        )

        results.append(PhaseScore(
            phase_index=p.get("phase_index", 0),
            time_start=t_start,
            time_end=t_end,
            sales_score=total,
            score_breakdown=breakdown,
            reasons=reasons,
            raw={
                "gmv": gmv, "order_count": order,
                "product_clicks": click, "viewer_count": viewer,
                "cta_score": cta, "user_rating": rating,
                "moments_count": len(phase_moments),
            },
        ))

    return results


def extract_clip_candidates(
    phase_scores: list[PhaseScore],
    top_n: int = TOP_N_DEFAULT,
    cluster_gap_sec: float = CLUSTER_GAP_SEC,
    max_clip_duration: float = MAX_CLIP_DURATION,
) -> list[ClipCandidate]:
    """
    PhaseScore リストから TOP-N クリップ候補を抽出する。
    隣接フェーズはクラスタリングして 1 クリップに統合する。
    """
    if not phase_scores:
        return []

    # スコア降順でソート
    sorted_phases = sorted(phase_scores, key=lambda x: x.sales_score, reverse=True)

    # TOP-N × 2 のフェーズを候補プールとして取得（クラスタリング後に絞り込む）
    pool_size = min(top_n * 3, len(sorted_phases))
    pool = sorted_phases[:pool_size]

    # フェーズ番号順に並び替えてクラスタリング
    pool_sorted = sorted(pool, key=lambda x: x.phase_index)

    clusters: list[list[PhaseScore]] = []
    current_cluster: list[PhaseScore] = []

    for ps in pool_sorted:
        if not current_cluster:
            current_cluster.append(ps)
        else:
            prev = current_cluster[-1]
            gap = ps.time_start - prev.time_end
            if gap <= cluster_gap_sec:
                current_cluster.append(ps)
            else:
                clusters.append(current_cluster)
                current_cluster = [ps]
    if current_cluster:
        clusters.append(current_cluster)

    # 各クラスターを ClipCandidate に変換
    cluster_candidates: list[ClipCandidate] = []
    for cluster in clusters:
        best = max(cluster, key=lambda x: x.sales_score)
        t_start = min(ps.time_start for ps in cluster)
        t_end = max(ps.time_end for ps in cluster)
        # 最大クリップ長の制限
        if t_end - t_start > max_clip_duration:
            # best フェーズを中心に前後を切り取る
            center = (best.time_start + best.time_end) / 2
            t_start = max(t_start, center - max_clip_duration / 2)
            t_end = min(t_end, t_start + max_clip_duration)

        duration = round(t_end - t_start, 1)

        # クラスター代表スコア = 最高フェーズのスコア
        cluster_candidates.append(ClipCandidate(
            rank=0,  # 後で設定
            phase_indices=[ps.phase_index for ps in cluster],
            time_start=round(t_start, 1),
            time_end=round(t_end, 1),
            duration=duration,
            sales_score=best.sales_score,
            score_breakdown=best.score_breakdown,
            reasons=best.reasons,
            label="",  # 後で設定
            phase_index=best.phase_index,
        ))

    # スコア降順でソートして TOP-N に絞り込み
    cluster_candidates.sort(key=lambda x: x.sales_score, reverse=True)
    top_candidates = cluster_candidates[:top_n]

    # rank と label を付与
    for i, c in enumerate(top_candidates, 1):
        c.rank = i
        c.label = f"TOP{i}"

    return top_candidates


def _build_reasons(
    gmv: float, order: float, click: float, viewer: float,
    moments: list[dict], cta: float, rating: float,
    purchase_popup: int, has_price: bool,
) -> list[str]:
    """スコアの根拠を日本語テキストで返す（上位 4 件）"""
    items: list[tuple[float, str]] = []

    if gmv > 0:
        items.append((gmv, f"売上 {_fmt_number(gmv)} 円"))
    if order > 0:
        items.append((order * 1000, f"注文 {int(order)} 件"))
    if click > 0:
        items.append((click * 100, f"クリック {int(click)} 回"))
    if viewer > 0:
        items.append((viewer * 10, f"視聴者 {int(viewer)} 人"))
    if moments:
        items.append((len(moments) * 500, f"売れた瞬間 {len(moments)} 件"))
    if purchase_popup > 0:
        items.append((purchase_popup * 800, f"購入ポップアップ {purchase_popup} 回"))
    if cta >= 4:
        items.append((cta * 200, f"CTA スコア {int(cta)}/5"))
    if rating >= 4:
        items.append((rating * 200, f"人間評価 {int(rating)}/5"))
    if has_price:
        items.append((300.0, "価格提示あり"))

    # 降順ソートして上位 4 件
    items.sort(key=lambda x: x[0], reverse=True)
    return [text for _, text in items[:4]]


def _fmt_number(n: float) -> str:
    """数値を読みやすい形式に変換"""
    if n >= 10000:
        return f"{n / 10000:.1f}万"
    return f"{int(n):,}"
