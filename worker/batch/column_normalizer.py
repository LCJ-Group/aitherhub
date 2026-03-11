"""
Column Normalizer – Excel/CSVの列名を標準メトリクス名にマッピングする。

スコアリングベースの段階的フォールバックにより、
データ提供元（TikTok, SellerCompass等）が列名を変更しても
自動的に正しいメトリクスにマッピングする。

スコアリングルール:
  完全一致:       +100
  単語境界一致:    +80  (_gmv_, gmv_, _gmv)
  同義語一致:      +60  (includeリスト内の語が列名に含まれる)
  コアキーワード:   +40  (core_keywordsが列名に含まれる)
  除外語:          -50  (excludeリスト内の語が列名に含まれる)
  数値列ボーナス:   +10  (列の値が数値型の場合)

閾値: スコアが 40 未満の場合は「未検出」
"""

import logging
import os
import re
from pathlib import Path

import yaml

logger = logging.getLogger("process_video")

# ======================================================
# CONSTANTS
# ======================================================

SCORE_EXACT_MATCH = 100
SCORE_WORD_BOUNDARY = 80
SCORE_SYNONYM_MATCH = 60
SCORE_CORE_KEYWORD = 40
SCORE_EXCLUDE_PENALTY = -50
SCORE_NUMERIC_BONUS = 10
SCORE_THRESHOLD = 40

# ======================================================
# LOAD MAPPING CONFIG
# ======================================================

_mapping_cache = None


def _load_mapping() -> dict:
    """metric_mapping.yml を読み込んでキャッシュする。"""
    global _mapping_cache
    if _mapping_cache is not None:
        return _mapping_cache

    yml_path = Path(__file__).parent / "metric_mapping.yml"
    if not yml_path.exists():
        logger.warning("[NORMALIZER] metric_mapping.yml not found at %s", yml_path)
        _mapping_cache = {}
        return _mapping_cache

    with open(yml_path, "r", encoding="utf-8") as f:
        _mapping_cache = yaml.safe_load(f) or {}

    logger.info("[NORMALIZER] Loaded metric_mapping.yml: %d metrics defined", len(_mapping_cache))
    return _mapping_cache


def reload_mapping():
    """設定ファイルを再読み込みする（テスト用）。"""
    global _mapping_cache
    _mapping_cache = None
    return _load_mapping()


# ======================================================
# NORMALIZATION
# ======================================================

def _normalize_col(name: str) -> str:
    """列名を正規化: 小文字化、空白/記号除去、snake_case化。"""
    s = name.strip().lower()
    # 全角→半角（基本的なASCII範囲）
    s = s.replace("\u3000", " ")
    # 空白・ハイフン・ドットをアンダースコアに
    s = re.sub(r"[\s\-\.]+", "_", s)
    # 連続アンダースコアを1つに
    s = re.sub(r"_+", "_", s)
    # 先頭・末尾のアンダースコアを除去
    s = s.strip("_")
    return s


# ======================================================
# SCORING ENGINE
# ======================================================

def _score_column(col_name: str, metric_config: dict, sample_value=None) -> int:
    """
    1つの列名に対して、1つのメトリクス定義のスコアを算出する。

    Args:
        col_name: 実際のExcel/CSV列名
        metric_config: metric_mapping.ymlの1メトリクス分の設定
        sample_value: その列のサンプル値（数値判定用）

    Returns:
        スコア（整数）
    """
    include_list = metric_config.get("include", [])
    exclude_list = metric_config.get("exclude", [])
    core_keywords = metric_config.get("core_keywords", [])

    col_lower = col_name.lower()
    col_normalized = _normalize_col(col_name)
    score = 0

    # --- 1. 完全一致 (+100) ---
    for alias in include_list:
        if col_lower == alias.lower():
            score = max(score, SCORE_EXACT_MATCH)
            break

    # --- 2. 単語境界一致 (+80) ---
    if score < SCORE_WORD_BOUNDARY:
        for alias in include_list:
            alias_norm = _normalize_col(alias)
            if not alias_norm:
                continue
            # _gmv_, gmv_, _gmv のパターン
            pattern = r"(?:^|_)" + re.escape(alias_norm) + r"(?:_|$)"
            if re.search(pattern, col_normalized):
                score = max(score, SCORE_WORD_BOUNDARY)
                break

    # --- 3. 同義語一致 (+60) ---
    if score < SCORE_SYNONYM_MATCH:
        for alias in include_list:
            alias_lower = alias.lower()
            if len(alias_lower) >= 3 and alias_lower in col_lower:
                score = max(score, SCORE_SYNONYM_MATCH)
                break

    # --- 4. コアキーワード一致 (+40) ---
    if score < SCORE_CORE_KEYWORD:
        for kw in core_keywords:
            kw_lower = kw.lower()
            if len(kw_lower) >= 2 and kw_lower in col_lower:
                score = max(score, SCORE_CORE_KEYWORD)
                break

    # --- 5. 除外語ペナルティ (-50) ---
    for ex in exclude_list:
        ex_lower = ex.lower()
        if ex_lower in col_lower:
            score += SCORE_EXCLUDE_PENALTY
            break  # 1回だけ適用

    # --- 6. 数値列ボーナス (+10) ---
    if sample_value is not None:
        try:
            float(sample_value)
            score += SCORE_NUMERIC_BONUS
        except (ValueError, TypeError) as _e:
            logger.debug(f"Suppressed: {_e}")

    return score


# ======================================================
# PUBLIC API
# ======================================================

def find_best_column(
    entry: dict,
    metric_name: str,
    candidate_keys: list[str] | None = None,
) -> str | None:
    """
    エントリの列名から、指定メトリクスに最もマッチする列を返す。

    旧 _find_key の完全上位互換。スコアリングベースで判定し、
    閾値未満の場合は None を返す。

    Args:
        entry: CSVの1行目（列名→値のdict）
        metric_name: 標準メトリクス名（"gmv", "order_count" 等）
        candidate_keys: 追加の候補キーリスト（後方互換用、省略可）

    Returns:
        マッチした実際の列名、または None
    """
    mapping = _load_mapping()
    metric_config = mapping.get(metric_name, {})

    # candidate_keysが渡された場合、includeリストにマージ
    if candidate_keys:
        merged_include = list(metric_config.get("include", []))
        for ck in candidate_keys:
            if ck not in merged_include:
                merged_include.append(ck)
        metric_config = {**metric_config, "include": merged_include}

    # 設定が空の場合（YAMLにない未知のメトリクス）
    if not metric_config.get("include") and not candidate_keys:
        return None

    best_col = None
    best_score = SCORE_THRESHOLD - 1  # 閾値未満は無視

    for col_name, col_value in entry.items():
        sc = _score_column(col_name, metric_config, sample_value=col_value)
        if sc > best_score:
            best_score = sc
            best_col = col_name

    return best_col


def detect_all_columns(
    entry: dict,
    metric_names: list[str] | None = None,
) -> dict:
    """
    エントリの全列名を全メトリクスに対してスコアリングし、
    最適なマッピングを返す。

    Args:
        entry: CSVの1行目（列名→値のdict）
        metric_names: 検出対象のメトリクス名リスト（省略時は全メトリクス）

    Returns:
        {
            "detected": {"gmv": "gmv_metric_name_short_ui", ...},
            "missing": ["viewer_count", ...],
            "candidates": {"viewer_count": [("col_a", 35), ("col_b", 20), ...]},
            "scores": {"gmv": {"gmv_metric_name_short_ui": 100, ...}, ...},
        }
    """
    mapping = _load_mapping()
    if metric_names is None:
        metric_names = list(mapping.keys())

    detected = {}
    missing = []
    candidates = {}
    all_scores = {}

    for metric_name in metric_names:
        metric_config = mapping.get(metric_name, {})
        if not metric_config:
            missing.append(metric_name)
            continue

        col_scores = {}
        for col_name, col_value in entry.items():
            sc = _score_column(col_name, metric_config, sample_value=col_value)
            if sc > 0:
                col_scores[col_name] = sc

        all_scores[metric_name] = col_scores

        # 最高スコアの列を選択
        if col_scores:
            best_col = max(col_scores, key=col_scores.get)
            best_score = col_scores[best_col]
            if best_score >= SCORE_THRESHOLD:
                detected[metric_name] = best_col
            else:
                missing.append(metric_name)
                # 候補Top5を記録
                sorted_candidates = sorted(
                    col_scores.items(), key=lambda x: x[1], reverse=True
                )[:5]
                candidates[metric_name] = sorted_candidates
        else:
            missing.append(metric_name)

    return {
        "detected": detected,
        "missing": missing,
        "candidates": candidates,
        "scores": all_scores,
    }


def log_detection_result(result: dict, video_id: str = "unknown"):
    """
    detect_all_columns の結果をログに出力する。

    検出成功: INFO レベル
    未検出あり: WARNING レベル（候補Top5付き）
    """
    detected = result.get("detected", {})
    missing = result.get("missing", [])
    candidates = result.get("candidates", {})

    # 検出成功のログ
    detected_str = ", ".join(f"{k}={v}" for k, v in detected.items())
    logger.info(
        "[NORMALIZER] video=%s detected: {%s}",
        video_id, detected_str,
    )

    # 未検出のログ（WARNING）
    if missing:
        logger.warning(
            "[NORMALIZER] video=%s MISSING metrics: %s",
            video_id, missing,
        )
        for m in missing:
            cands = candidates.get(m, [])
            if cands:
                cand_str = ", ".join(f"{c[0]}(score={c[1]})" for c in cands)
                logger.warning(
                    "[NORMALIZER] video=%s   %s candidates: [%s]",
                    video_id, m, cand_str,
                )
            else:
                logger.warning(
                    "[NORMALIZER] video=%s   %s: no candidates found",
                    video_id, m,
                )


# ======================================================
# CRITICAL METRICS CHECK (監視)
# ======================================================

# 最低限これらが検出されないとデータが壊れる
CRITICAL_METRICS = ["gmv", "order_count", "viewer_count", "like_count"]


def check_critical_metrics(result: dict) -> tuple[bool, list[str]]:
    """
    クリティカルメトリクス（gmv, orders, viewers, likes）の
    検出状態をチェックする。

    Returns:
        (all_ok, missing_critical_list)
        all_ok: 全クリティカルメトリクスが検出されたか
        missing_critical_list: 未検出のクリティカルメトリクス名リスト
    """
    detected = result.get("detected", {})
    missing_critical = [m for m in CRITICAL_METRICS if m not in detected]
    return len(missing_critical) == 0, missing_critical


# ======================================================
# BACKWARD COMPATIBILITY
# ======================================================

def find_key_scored(entry: dict, candidate_keys: list[str]) -> str | None:
    """
    旧 _find_key の互換ラッパー。
    candidate_keys を使ってスコアリング検出を行う。

    既存コードからの移行を容易にするため、
    candidate_keys をそのまま include リストとして使う。
    """
    if not entry or not candidate_keys:
        return None

    # candidate_keysからメトリクス名を推定
    # （YAMLの設定があればそれを使い、なければcandidate_keysのみで判定）
    mapping = _load_mapping()

    # candidate_keysの最初の要素でYAMLのメトリクスを探す
    best_metric = None
    for metric_name, config in mapping.items():
        include_set = set(a.lower() for a in config.get("include", []))
        for ck in candidate_keys:
            if ck.lower() in include_set:
                best_metric = metric_name
                break
        if best_metric:
            break

    if best_metric:
        return find_best_column(entry, best_metric, candidate_keys)

    # YAMLにマッチしない場合: candidate_keysのみで簡易スコアリング
    temp_config = {
        "include": candidate_keys,
        "exclude": [],
        "core_keywords": [],
    }

    best_col = None
    best_score = SCORE_THRESHOLD - 1

    for col_name, col_value in entry.items():
        sc = _score_column(col_name, temp_config, sample_value=col_value)
        if sc > best_score:
            best_score = sc
            best_col = col_name

    return best_col
