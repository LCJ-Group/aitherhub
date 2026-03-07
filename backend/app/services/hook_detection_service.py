"""
HookDetectionService
====================
TikTok / Reels 向けに「最初3秒」で視聴者を引き付ける
フック（Hook）を検出するサービス。

検出対象
────────
  1. 強いキーワード（価格、限定、無料、衝撃的表現）
  2. 疑問文（〜？で終わる文）
  3. 感嘆表現（！が含まれる文）
  4. 数字を含む具体的な表現（「3つの理由」「1万円」等）
  5. 呼びかけ表現（「みなさん」「ちょっと聞いて」等）

スコアリング
────────────
  各フレーズに hook_score（0〜100）を付与。
  スコアが高いフレーズをクリップの先頭に配置することを推奨。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# ── フックキーワード辞書 ──────────────────────────────────
HOOK_KEYWORDS_STRONG = [
    # 価格・お得系
    "無料", "タダ", "0円", "半額", "割引", "セール", "限定",
    "今だけ", "特別", "お得", "激安", "最安",
    # 衝撃・驚き系
    "衝撃", "驚き", "ヤバい", "やばい", "すごい", "凄い",
    "マジで", "本当に", "信じられない", "びっくり",
    # 緊急系
    "急いで", "今すぐ", "ラスト", "残り", "売り切れ",
    # 秘密・独占系
    "秘密", "裏技", "知らないと損", "教えます", "暴露",
]

HOOK_KEYWORDS_MEDIUM = [
    # 呼びかけ系
    "みなさん", "皆さん", "あなた", "ちょっと", "聞いて",
    "見て", "注目", "必見", "おすすめ", "紹介",
    # 比較・変化系
    "ビフォーアフター", "before", "after", "変わった", "変化",
    # 結果系
    "結果", "効果", "レビュー", "使ってみた", "買ってみた",
]

# ── スコアの重み ──────────────────────────────────────────
SCORE_STRONG_KEYWORD = 25.0
SCORE_MEDIUM_KEYWORD = 15.0
SCORE_QUESTION = 20.0       # 疑問文
SCORE_EXCLAMATION = 10.0    # 感嘆文
SCORE_NUMBER = 15.0         # 数字を含む具体的表現
SCORE_SHORT_PUNCH = 10.0    # 短い文（パンチライン）
SCORE_FIRST_3SEC = 5.0      # 最初の3秒にある（ボーナス）


@dataclass
class HookCandidate:
    """検出されたフック候補"""
    text: str                 # フレーズテキスト
    start_sec: float          # 開始秒
    end_sec: float            # 終了秒
    hook_score: float         # 0〜100
    hook_reasons: list[str]   # スコアの根拠
    is_question: bool
    has_number: bool
    keyword_matches: list[str]


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def detect_hooks(
    transcript_segments: list[dict],
    max_candidates: int = 10,
) -> list[HookCandidate]:
    """
    トランスクリプトセグメントからフック候補を検出する。

    Parameters
    ----------
    transcript_segments : list[dict]
        Whisper / GPT の字幕セグメント。各要素は:
        {
            "start": float,  # 開始秒
            "end": float,    # 終了秒
            "text": str,     # テキスト
        }
    max_candidates : int
        最大候補数

    Returns
    -------
    list[HookCandidate]
        フック候補のリスト（hook_score 降順）
    """
    if not transcript_segments:
        return []

    candidates: list[HookCandidate] = []

    for seg in transcript_segments:
        text = str(seg.get("text", "")).strip()
        if not text or len(text) < 2:
            continue

        start = _safe_float(seg.get("start"))
        end = _safe_float(seg.get("end"))

        score = 0.0
        reasons: list[str] = []
        keyword_matches: list[str] = []

        # 1. 強いキーワード
        for kw in HOOK_KEYWORDS_STRONG:
            if kw in text:
                score += SCORE_STRONG_KEYWORD
                keyword_matches.append(kw)
                reasons.append(f"強キーワード「{kw}」")
                break  # 1つで十分

        # 2. 中程度のキーワード
        for kw in HOOK_KEYWORDS_MEDIUM:
            if kw in text:
                score += SCORE_MEDIUM_KEYWORD
                keyword_matches.append(kw)
                reasons.append(f"キーワード「{kw}」")
                break

        # 3. 疑問文
        is_question = text.endswith("？") or text.endswith("?") or "ですか" in text or "かな" in text
        if is_question:
            score += SCORE_QUESTION
            reasons.append("疑問文")

        # 4. 感嘆文
        if "！" in text or "!" in text:
            score += SCORE_EXCLAMATION
            reasons.append("感嘆表現")

        # 5. 数字を含む具体的表現
        has_number = bool(re.search(r'\d+', text))
        if has_number:
            score += SCORE_NUMBER
            reasons.append("数字を含む")

        # 6. 短い文（パンチライン） - 20文字以下
        if len(text) <= 20 and score > 0:
            score += SCORE_SHORT_PUNCH
            reasons.append("短いパンチライン")

        # 7. 最初の3秒ボーナス
        if start < 3.0:
            score += SCORE_FIRST_3SEC
            reasons.append("冒頭3秒")

        # スコアが0なら候補にしない
        if score <= 0:
            continue

        # 100点満点にクランプ
        score = min(score, 100.0)

        candidates.append(HookCandidate(
            text=text,
            start_sec=start,
            end_sec=end,
            hook_score=round(score, 1),
            hook_reasons=reasons,
            is_question=is_question,
            has_number=has_number,
            keyword_matches=keyword_matches,
        ))

    # スコア降順でソート
    candidates.sort(key=lambda x: x.hook_score, reverse=True)

    return candidates[:max_candidates]


def suggest_hook_placement(
    hooks: list[HookCandidate],
    clip_start: float,
    clip_end: float,
) -> dict:
    """
    クリップ内で最適なフック配置を提案する。

    Returns
    -------
    dict
        {
            "best_hook": HookCandidate or None,
            "should_reorder": bool,
            "suggested_start": float,  # フックを先頭に持ってくる場合の新しい開始秒
            "reason": str,
        }
    """
    if not hooks:
        return {
            "best_hook": None,
            "should_reorder": False,
            "suggested_start": clip_start,
            "reason": "フック候補なし",
        }

    # クリップ内のフック候補を抽出
    clip_hooks = [
        h for h in hooks
        if h.start_sec >= clip_start and h.end_sec <= clip_end
    ]

    if not clip_hooks:
        return {
            "best_hook": None,
            "should_reorder": False,
            "suggested_start": clip_start,
            "reason": "クリップ内にフック候補なし",
        }

    best = clip_hooks[0]  # 既にスコア降順

    # フックがクリップの最初の3秒以内にあるか
    is_at_start = best.start_sec - clip_start < 3.0

    if is_at_start:
        return {
            "best_hook": best,
            "should_reorder": False,
            "suggested_start": clip_start,
            "reason": f"フック「{best.text[:20]}...」は既に冒頭にあります",
        }

    # フックを先頭に持ってくることを提案
    return {
        "best_hook": best,
        "should_reorder": True,
        "suggested_start": max(0, best.start_sec - 1.0),  # 1秒前から開始
        "reason": f"フック「{best.text[:20]}...」を冒頭に配置することを推奨（{best.hook_score}pt）",
    }
