import json
import os
import time
import random
import asyncio
from functools import partial

from openai import AzureOpenAI, RateLimitError, APIError, APITimeoutError
from decouple import config
from best_phase_pipeline import extract_attention_metrics


MAX_CONCURRENCY = 20

# ======================================================
# ENV / CLIENT
# ======================================================

def env(key, default=None):
    return os.getenv(key) or config(key, default=default)


GPT5_MODEL = env("GPT5_MODEL")
AZURE_OPENAI_ENDPOINT = env("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_KEY = env("AZURE_OPENAI_KEY")
GPT5_API_VERSION = env("GPT5_API_VERSION")

client = AzureOpenAI(
    api_key=AZURE_OPENAI_KEY,
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    api_version=GPT5_API_VERSION
)


# =========================================================
# STRUCTURE FEATURE COMPARATORS (for Report 3)
# =========================================================

STRUCTURE_FEATURE_TYPES = {
    "phase_count": "scalar",
    "avg_phase_duration": "scalar",
    "switch_rate": "scalar",

    "early_ratio": "distribution",
    "mid_ratio": "distribution",
    "late_ratio": "distribution",

    "structure_embedding": "vector",
}


def compare_scalar(a, b):
    try:
        if a is None or b is None or b == 0:
            return 0.0
        return (float(a) - float(b)) / float(b)
    except Exception:
        return 0.0


def compare_distribution(a: dict, b: dict):
    if not isinstance(a, dict) or not isinstance(b, dict):
        return 0.0

    keys = set(a.keys()) | set(b.keys())
    dist = 0.0
    for k in keys:
        try:
            dist += abs(float(a.get(k, 0.0)) - float(b.get(k, 0.0)))
        except Exception as _e:
            print(f"Suppressed: {_e}")
    return dist


def cosine_distance(a: list, b: list):
    if not isinstance(a, list) or not isinstance(b, list) or not a or not b:
        return 0.0

    try:
        import math

        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))

        if na == 0 or nb == 0:
            return 0.0

        return 1.0 - dot / (na * nb)
    except Exception:
        return 0.0


def compare_feature(feature_name, cur_v, ref_v):
    t = STRUCTURE_FEATURE_TYPES.get(feature_name)

    if t == "scalar":
        return compare_scalar(cur_v, ref_v)

    if t == "distribution":
        return compare_distribution(cur_v, ref_v)

    if t == "vector":
        return cosine_distance(cur_v, ref_v)

    return None



# ======================================================
# REPORT 1 – TIMELINE / PHASE BREAKDOWN
# ======================================================

def build_report_1_timeline(phase_units):
    """
    Build timeline report for frontend rendering.
    No GPT involved.
    Includes sales data if available (from Excel uploads).
    """
    out = []

    for p in phase_units:
        start = p["metric_timeseries"]["start"] or {}
        end   = p["metric_timeseries"]["end"] or {}

        entry = {
            "phase_index": p["phase_index"],
            "group_id": p.get("group_id"),
            "phase_description": p["phase_description"],
            "time_range": p["time_range"],
            "metrics": {
                "view_start": start.get("viewer_count"),
                "view_end": end.get("viewer_count"),
                "like_start": start.get("like_count"),
                "like_end": end.get("like_count"),
                "delta_view": (
                    end.get("viewer_count") - start.get("viewer_count")
                    if start.get("viewer_count") is not None
                    and end.get("viewer_count") is not None
                    else None
                ),
                "delta_like": (
                    end.get("like_count") - start.get("like_count")
                    if start.get("like_count") is not None
                    and end.get("like_count") is not None
                    else None
                )
            }
        }

        # Add CTA score if available
        cta = p.get("cta_score")
        if cta is not None:
            entry["cta_score"] = cta

        # Add sales data if available
        sales = p.get("sales_data")
        if sales:
            entry["sales"] = {
                "revenue": sales.get("sales"),
                "orders": sales.get("orders"),
                "products_sold": sales.get("products_sold", []),
            }

        out.append(entry)

    return out


# ======================================================
# REPORT 2 – PHASE INSIGHTS (RAW)
# ======================================================
def gpt_rewrite_report_2(item):
    payload = json.dumps(item, ensure_ascii=False)

    resp = client.responses.create(
        model=GPT5_MODEL,
        input=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": "You are analyzing a livestream phase."
                    }
                ]
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": PROMPT_REPORT_2.format(data=payload)
                    }
                ]
            }
        ],
        max_output_tokens=2048
    )

    return resp.output_text.strip() if resp.output_text else ""

async def gpt_rewrite_report_2_async(
    item,
    sem: asyncio.Semaphore,
    max_retry: int = 5,
):
    async with sem:
        loop = asyncio.get_event_loop()

        for attempt in range(max_retry):
            try:
                text = await loop.run_in_executor(
                    None,
                    partial(gpt_rewrite_report_2, item)
                )

                if text and not is_gpt_report_2_invalid(text):
                    return text

            except (RateLimitError, APITimeoutError, APIError):
                sleep = (2 ** attempt) + random.uniform(0.5, 1.5)
                await asyncio.sleep(sleep)

            except Exception:
                return None

        return None

async def process_one_report2_task(
    item,
    sem,
    results,
):
    text = await gpt_rewrite_report_2_async(item, sem)

    if not text:
        text = (
            "このフェーズについては、"
            "現在の比較データから明確な改善ポイントを特定することができません。"
            "今後、追加の配信データが蓄積され次第、"
            "より具体的な改善提案が可能になります。"
        )

    results[item["phase_index"]] = {
        "phase_index": item["phase_index"],
        "group_id": item["group_id"],
        "insight": text
    }


def build_report_2_phase_insights_raw(phase_units, best_data, excel_data=None):
    """
    Compare each phase with the best historical phase
    of the same group using rule-based metrics.
    Includes sales data if available.
    """
    out = []

    for p in phase_units:
        gid = p.get("group_id")
        if not gid:
            continue

        gid = str(gid)
        best_group = best_data["groups"].get(gid)
        if not best_group or not best_group["phases"]:
            continue

        best = best_group["phases"][0]

        cur = extract_attention_metrics(p)
        ref = best["metrics"]

        findings = []

        if cur["view_velocity"] is not None and ref["view_velocity"] is not None:
            if cur["view_velocity"] < ref["view_velocity"]:
                findings.append("view_velocity_lower_than_best")

        if cur["like_per_viewer"] is not None and ref["like_per_viewer"] is not None:
            if cur["like_per_viewer"] < ref["like_per_viewer"]:
                findings.append("like_per_viewer_lower_than_best")

        item = {
            "phase_index": p["phase_index"],
            "group_id": gid,
            "phase_description": p["phase_description"],
            "speech_text": p.get("speech_text", ""),
            "current_metrics": cur,
            "benchmark_metrics": ref,
            "findings": findings,
        }

        # Add CTA score if available
        cta = p.get("cta_score")
        if cta is not None:
            item["cta_score"] = cta

        # Add audio features if available
        af = p.get("audio_features")
        if af:
            item["audio_features"] = af

        # Add sales data if available
        sales = p.get("sales_data")
        if sales:
            item["sales_data"] = sales

        # Add csv_metrics if available
        csv_m = p.get("csv_metrics")
        if csv_m:
            item["csv_metrics"] = csv_m

        out.append(item)

    return out


def is_gpt_report_2_invalid(text: str) -> bool:
    if not text:
        return True

    t = text.strip().lower()

    refusal_signals = [
        # EN
        "i'm sorry",
        "i am sorry",
        "cannot assist",
        "can't assist",
        "cannot help",
        "cannot comply",
        "not able to help",

        # JP
        "申し訳ありません",
        "対応できません",
        "お手伝いできません",
    ]

    return any(sig in t for sig in refusal_signals)


PROMPT_REPORT_2 = """
あなたはライブコマースで「売上を最大化する」ための専門コンサルタントです。

以下は、ある配信フェーズの情報です。
- フェーズの説明（配信者の行動・発話内容）
- 視聴者数・いいね数の推移
- 売上データ（ある場合）
- 過去のベストパフォーマンスとの比較
- CTAスコア（1〜5、購買を促す発言の強度。ある場合）
- 音声特徴量（声の熱量・抑揚・話速・沈黙率。ある場合）

あなたの役割：
- このフェーズで「どう売っているか」を分析する
- 「どうすればもっと売れるか」を具体的にアドバイスする
- 動画の描写やシーンの説明は一切不要

分析の観点：
- セールストーク（購買を促す言い回し、限定感、緊急性の演出）
- 商品の見せ方（デモ、ビフォーアフター、使用感の伝え方）
- 購買導線（カートへの誘導タイミング、価格提示のタイミング）
- 視聴者エンゲージメント（コメント誘導、質問への対応）
- 売上データがある場合：なぜこの時間帯に売れた/売れなかったかの分析
- CTAスコアがある場合：購買を促す発言の強さと頻度の評価。スコアが低いフェーズでは「もっと強く購買を促すべき」等の具体的アドバイス
- 音声特徴量がある場合：声の熱量（energy_mean）や抑揚（pitch_std）が低い場合は「もっと感情を込めて話すべき」、話速（speech_rate）が速すぎる場合は「ゆっくり丁寧に説明すべき」等のアドバイス

出力ルール：
- 最大2つの具体的なセールス改善アドバイス
- 各アドバイスは「現状の売り方の問題点」→「具体的な改善アクション」の順で書く
- 「〜のシーンでは配信者が〜している」のような動画の描写は書かない
- すぐに次の配信で実践できるレベルの具体性で書く
- 1項目＝最大3文まで

制約：
- データを捏造しない
- 抽象的な表現を避ける（「もっと工夫する」ではなく「価格を先に見せてから限定数を伝える」のように書く）
- 音声特徴量の数値を直接引用しない（「energy_meanが0.03」ではなく「声の熱量が低い」のように自然な表現で書く）

入力：
{data}

""".strip()

def rewrite_report_2_with_gpt(raw_items, excel_data=None):
    results = {}

    async def runner():
        sem = asyncio.Semaphore(MAX_CONCURRENCY)
        tasks = []

        for item in raw_items:
            tasks.append(
                process_one_report2_task(
                    item,
                    sem,
                    results
                )
            )

        await asyncio.gather(*tasks)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        fut = asyncio.run_coroutine_threadsafe(runner(), loop)
        fut.result()
    else:
        asyncio.run(runner())

    return [results[k] for k in sorted(results)]


# ======================================================
# REPORT 3 – VIDEO INSIGHTS (RAW)
# ======================================================

def build_report_3_video_insights_raw(phase_units, product_exposures=None):
    """
    Build video-level insights from phase data.
    Now includes:
    - Per-group performance (view/like deltas)
    - Sales data per group (GMV, orders) from csv_metrics
    - Product exposure summary (which products, when, how long)
    - Sales trigger analysis (which phases drove sales spikes)
    """
    groups = {}

    for p in phase_units:
        gid = p.get("group_id")
        if not gid:
            continue

        gid = str(gid)
        m = extract_attention_metrics(p)
        csv_m = p.get("csv_metrics", {})

        g = groups.setdefault(gid, {
            "phase_count": 0,
            "total_delta_view": 0,
            "total_delta_like": 0,
            "total_gmv": 0,
            "total_orders": 0,
            "total_product_clicks": 0,
            "max_gpm": 0,
            "max_conversion_rate": 0,
            "phases_with_sales": 0,
        })

        g["phase_count"] += 1

        if m["delta_view"] is not None:
            g["total_delta_view"] += m["delta_view"]

        if m["delta_like"] is not None:
            g["total_delta_like"] += m["delta_like"]

        # Aggregate CSV metrics per group
        if csv_m:
            g["total_gmv"] += csv_m.get("gmv", 0) or 0
            g["total_orders"] += csv_m.get("order_count", 0) or 0
            g["total_product_clicks"] += csv_m.get("product_clicks", 0) or 0
            g["max_gpm"] = max(g["max_gpm"], csv_m.get("gpm", 0) or 0)
            g["max_conversion_rate"] = max(
                g["max_conversion_rate"],
                csv_m.get("conversion_rate", 0) or 0
            )
            if (csv_m.get("order_count", 0) or 0) > 0:
                g["phases_with_sales"] += 1

    # ---- Sales trigger analysis ----
    # Identify phases where sales spiked (top performers)
    sales_phases = []
    for p in phase_units:
        csv_m = p.get("csv_metrics", {})
        gmv = csv_m.get("gmv", 0) or 0 if csv_m else 0
        orders = csv_m.get("order_count", 0) or 0 if csv_m else 0
        if gmv > 0 or orders > 0:
            tr = p.get("time_range", {})
            duration = (tr.get("end_sec", 0) - tr.get("start_sec", 0))
            gmv_per_min = (gmv / (duration / 60.0)) if duration > 0 else 0
            sales_phases.append({
                "phase_index": p["phase_index"],
                "group_id": str(p.get("group_id", "")),
                "gmv": round(gmv, 2),
                "orders": orders,
                "gmv_per_minute": round(gmv_per_min, 2),
                "duration_sec": round(duration, 1),
                "cta_score": p.get("cta_score"),
                "phase_description": p.get("phase_description", "")[:100],
            })

    # Sort by GMV per minute (sales efficiency)
    sales_phases.sort(key=lambda x: x["gmv_per_minute"], reverse=True)
    top_sales_phases = sales_phases[:5]  # Top 5 sales-driving phases

    # ---- Product exposure summary ----
    product_summary = {}
    if product_exposures:
        for exp in product_exposures:
            pname = exp.get("product_name", "unknown")
            ps = product_summary.setdefault(pname, {
                "total_duration_sec": 0,
                "segment_count": 0,
                "total_gmv": 0,
                "total_orders": 0,
                "avg_confidence": 0,
                "sources": set(),
            })
            dur = (exp.get("time_end", 0) - exp.get("time_start", 0))
            ps["total_duration_sec"] += dur
            ps["segment_count"] += 1
            ps["total_gmv"] += exp.get("gmv", 0) or 0
            ps["total_orders"] += exp.get("order_count", 0) or 0
            ps["avg_confidence"] += exp.get("confidence", 0) or 0
            for src in (exp.get("sources") or []):
                ps["sources"].add(src)

        # Finalize averages
        for pname, ps in product_summary.items():
            if ps["segment_count"] > 0:
                ps["avg_confidence"] = round(
                    ps["avg_confidence"] / ps["segment_count"], 3
                )
            ps["total_duration_sec"] = round(ps["total_duration_sec"], 1)
            ps["total_gmv"] = round(ps["total_gmv"], 2)
            ps["sources"] = sorted(ps["sources"])

    # ---- Calculate total video metrics ----
    total_gmv = sum(g["total_gmv"] for g in groups.values())
    total_orders = sum(g["total_orders"] for g in groups.values())

    result = {
        "total_phases": len(phase_units),
        "total_gmv": round(total_gmv, 2),
        "total_orders": total_orders,
        "group_performance": [
            {
                "group_id": gid,
                "phase_count": g["phase_count"],
                "total_delta_view": g["total_delta_view"],
                "total_delta_like": g["total_delta_like"],
                "total_gmv": round(g["total_gmv"], 2),
                "total_orders": g["total_orders"],
                "total_product_clicks": g["total_product_clicks"],
                "max_gpm": round(g["max_gpm"], 2),
                "max_conversion_rate": round(g["max_conversion_rate"], 4),
                "phases_with_sales": g["phases_with_sales"],
            }
            for gid, g in groups.items()
        ],
    }

    # Add sales trigger analysis if data exists
    if top_sales_phases:
        result["sales_trigger_analysis"] = {
            "top_sales_phases": top_sales_phases,
            "insight": (
                f"売上上位{len(top_sales_phases)}フェーズが "
                f"全体GMV {round(total_gmv, 0)} の "
                f"{round(sum(sp['gmv'] for sp in top_sales_phases) / total_gmv * 100, 1) if total_gmv > 0 else 0}% を占めています"
            ),
        }

    # Add product summary if data exists
    if product_summary:
        result["product_performance"] = [
            {
                "product_name": pname,
                **ps,
            }
            for pname, ps in sorted(
                product_summary.items(),
                key=lambda x: x[1]["total_gmv"],
                reverse=True,
            )
        ]

    return result


PROMPT_REPORT_3 = """
あなたはライブコマースで「売上を最大化する」ための専門コンサルタントです。

提供される情報：
- 配信全体のフェーズ別パフォーマンス（視聴者数・いいね数の変動）
- 売上データ（GMV・注文数・GPM。ある場合）
- 商品別パフォーマンス（紹介時間・売上。ある場合）
- 売上トリガー分析（どのフェーズで売上が跳ねたか。ある場合）

あなたの役割：
- 配信全体の「売り方の流れ」を俯瞰的に分析する
- どのタイミングで売上が伸びているか、どこで機会損失があるかを特定する
- 売上を最大化するための「配信構成の改善」を具体的に提案する
- 動画の描写やシーンの説明は一切不要

分析の観点：
- オープニング（最初のフック）の効果
- 商品紹介のタイミングと順番
- 購買ピークの作り方（限定感・緊急性・価格提示）
- クロージング（最後の押し）の強さ
- 視聴者離脱が起きているポイントとその原因
- 売上データがある場合：GMV効率が高いフェーズの特徴と、低いフェーズの改善点
- 商品別データがある場合：紹介時間と売上の関係、紹介順序の最適化
- 売上トリガーがある場合：売上が跳ねたフェーズの共通点と再現方法

【必須ルール】：
- 数値を捏造しない
- group_id や内部IDを一切言及しない
- 動画の描写やシーンの説明は書かない
- すぐに実践できるレベルの具体性で書く
- 出力は必ず JSON のみ
- 各インサイトは1オブジェクト＝1インサイト

出力形式（厳守）：
{
  "video_insights": [
    {
      "title": "売上に直結する短いタイトル",
      "content": "具体的な売り方の改善アドバイス（数文）"
    }
  ]
}

入力データ：
{data}
""".strip()


def safe_json_load(text):
    if not text:
        return None

    text = text.strip()

    if text.startswith("```"):
        lines = text.splitlines()
        # コードブロックの開始行を除去
        if lines[0].startswith("```"):
            lines = lines[1:]
        # コードブロックの終了行を除去
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def rewrite_report_3_with_gpt(raw_video_insight, max_retry: int = 5):
    payload = json.dumps(raw_video_insight, ensure_ascii=False)
    prompt = PROMPT_REPORT_3.replace("{data}", payload)

    for attempt in range(max_retry):
        try:
            resp = client.responses.create(
                model=GPT5_MODEL,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": prompt
                            }
                        ]
                    }
                ],
                max_output_tokens=2048
            )

            parsed = safe_json_load(resp.output_text)
            if parsed and "video_insights" in parsed:
                return parsed

        except (RateLimitError, APITimeoutError, APIError):
            sleep = (2 ** attempt) + random.uniform(0.5, 1.5)
            time.sleep(sleep)

        except Exception:
            break

    return {
        "video_insights": [
            {
                "title": "分析できませんでした",
                "content": "GPT が期待された形式で結果を返しませんでした。"
            }
        ]
    }



def build_report_3_structure_vs_benchmark_raw(
    current_features: dict,
    best_features: dict,
    group_stats: dict | None = None,
    phase_units: list | None = None,
    product_exposures: list | None = None,
):
    """
    Deterministic, rule-based.
    Compare current video structure vs benchmark video structure.
    Now includes:
    - Original structure metrics comparison
    - Sales timing analysis (when sales happen relative to product intro)
    - Sales concentration analysis (how concentrated sales are)
    - Product intro timing effectiveness
    """

    FEATURES = [
        "phase_count",
        "avg_phase_duration",
        "switch_rate",
        "early_ratio",
        "mid_ratio",
        "late_ratio",
        "structure_embedding",
    ]

    result = {
        "type": "video_structure_vs_benchmark",
        "metrics": {},
        "judgements": [],
        "problems": [],
        "suggestions": [],
    }

    # =====================================================
    # 1) Compare each feature with BEST and GROUP
    # =====================================================
    for k in FEATURES:
        cur_v = current_features.get(k)
        best_v = best_features.get(k)
        group_v = group_stats.get(k) if group_stats else None

        delta_vs_best = compare_feature(k, cur_v, best_v)
        delta_vs_group = compare_feature(k, cur_v, group_v)

        result["metrics"][k] = {
            "type": STRUCTURE_FEATURE_TYPES.get(k),
            "current": cur_v,
            "benchmark": best_v,
            "group": group_v,
            "delta_vs_best": delta_vs_best,
            "delta_vs_group": delta_vs_group,
        }

    # =====================================================
    # 2) Rule-based judgements (use ONLY scalar metrics)
    # =====================================================

    # ---------- Pacing ----------
    d = result["metrics"].get("avg_phase_duration", {})
    delta = d.get("delta_vs_best")
    if isinstance(delta, (int, float)):
        if delta > 0.25:
            result["judgements"].append("pacing_slower_than_benchmark")
            result["problems"].append("average_phase_duration_too_long")
            result["suggestions"].append("shorten_each_phase_to_increase_pacing")
        elif delta < -0.25:
            result["judgements"].append("pacing_faster_than_benchmark")
        else:
            result["judgements"].append("pacing_similar_to_benchmark")

    # ---------- Switch rate ----------
    d = result["metrics"].get("switch_rate", {})
    delta = d.get("delta_vs_best")
    if isinstance(delta, (int, float)):
        if delta < -0.3:
            result["problems"].append("phase_switch_too_infrequent")
            result["suggestions"].append("increase_phase_switch_frequency")

    # ---------- Complexity (phase_count) ----------
    d = result["metrics"].get("phase_count", {})
    delta = d.get("delta_vs_best")
    if isinstance(delta, (int, float)):
        if delta < -0.3:
            result["problems"].append("too_few_phases_compared_to_benchmark")
            result["suggestions"].append("increase_number_of_phases_or_segments")
        elif delta > 0.5:
            result["problems"].append("too_many_phases_compared_to_benchmark")
            result["suggestions"].append("merge_or_simplify_phases")

    # ---------- Structure balance (distribution distance) ----------
    for key in ["early_ratio", "mid_ratio", "late_ratio"]:
        d = result["metrics"].get(key, {})
        dist = d.get("delta_vs_best")
        if isinstance(dist, (int, float)) and dist > 0.3:
            result["problems"].append(f"{key}_distribution_deviates_from_benchmark")
            result["suggestions"].append(f"adjust_{key}_distribution_toward_benchmark")

    # =====================================================
    # 3) Sales timing & product intro analysis (NEW)
    # =====================================================
    if phase_units:
        _analyze_sales_structure(result, phase_units, product_exposures)

    # =====================================================
    # 4) Overall judgement
    # =====================================================
    if result["problems"]:
        result["overall"] = "structure_quality_worse_than_benchmark"
    else:
        result["overall"] = "structure_quality_similar_or_better_than_benchmark"

    return result


def _analyze_sales_structure(result, phase_units, product_exposures=None):
    """
    Analyze the relationship between sales timing, product introductions,
    and the overall structure of the livestream.
    Adds findings to result["problems"] and result["suggestions"].
    """

    # ---- Sales concentration analysis ----
    # Are sales concentrated in a few phases or spread out?
    phase_gmvs = []
    total_video_duration = 0

    for p in phase_units:
        csv_m = p.get("csv_metrics", {})
        gmv = csv_m.get("gmv", 0) or 0 if csv_m else 0
        tr = p.get("time_range", {})
        duration = tr.get("end_sec", 0) - tr.get("start_sec", 0)
        total_video_duration = max(total_video_duration, tr.get("end_sec", 0))
        phase_gmvs.append({
            "phase_index": p["phase_index"],
            "gmv": gmv,
            "start_sec": tr.get("start_sec", 0),
            "end_sec": tr.get("end_sec", 0),
            "duration": duration,
        })

    total_gmv = sum(pg["gmv"] for pg in phase_gmvs)

    if total_gmv > 0 and len(phase_gmvs) > 3:
        # Sort by GMV descending
        sorted_by_gmv = sorted(phase_gmvs, key=lambda x: x["gmv"], reverse=True)

        # Top 20% of phases
        top_n = max(1, len(sorted_by_gmv) // 5)
        top_gmv = sum(pg["gmv"] for pg in sorted_by_gmv[:top_n])
        concentration = top_gmv / total_gmv

        result["metrics"]["sales_concentration"] = {
            "type": "scalar",
            "current": round(concentration, 3),
            "description": f"上位{top_n}フェーズ（全{len(phase_gmvs)}フェーズの20%）が全体GMVの{round(concentration * 100, 1)}%を占める",
        }

        if concentration > 0.8:
            result["problems"].append("sales_too_concentrated_in_few_phases")
            result["suggestions"].append(
                "distribute_sales_opportunities_across_more_phases"
            )
        elif concentration < 0.3:
            result["judgements"].append("sales_well_distributed_across_phases")

        # ---- Sales timing analysis ----
        # When do sales happen? Early/Mid/Late?
        if total_video_duration > 0:
            early_cutoff = total_video_duration * 0.33
            mid_cutoff = total_video_duration * 0.66

            early_gmv = sum(
                pg["gmv"] for pg in phase_gmvs
                if pg["start_sec"] < early_cutoff
            )
            mid_gmv = sum(
                pg["gmv"] for pg in phase_gmvs
                if early_cutoff <= pg["start_sec"] < mid_cutoff
            )
            late_gmv = sum(
                pg["gmv"] for pg in phase_gmvs
                if pg["start_sec"] >= mid_cutoff
            )

            result["metrics"]["sales_timing"] = {
                "type": "distribution",
                "early_pct": round(early_gmv / total_gmv * 100, 1) if total_gmv > 0 else 0,
                "mid_pct": round(mid_gmv / total_gmv * 100, 1) if total_gmv > 0 else 0,
                "late_pct": round(late_gmv / total_gmv * 100, 1) if total_gmv > 0 else 0,
            }

            # Problem: No sales in early phase (missed warm-up opportunity)
            if early_gmv == 0 and total_gmv > 0:
                result["problems"].append("no_sales_in_early_phase")
                result["suggestions"].append(
                    "introduce_a_hook_product_early_to_establish_buying_momentum"
                )

            # Problem: Sales drop off in late phase
            if late_gmv < total_gmv * 0.1 and total_gmv > 0:
                result["problems"].append("sales_drop_in_late_phase")
                result["suggestions"].append(
                    "add_closing_urgency_with_limited_time_offers_or_bundle_deals"
                )

    # ---- Product intro timing vs sales ----
    if product_exposures and total_gmv > 0:
        products_with_sales = []
        products_without_sales = []

        # Group exposures by product
        product_groups = {}
        for exp in product_exposures:
            pname = exp.get("product_name", "unknown")
            pg = product_groups.setdefault(pname, {
                "first_intro_sec": float("inf"),
                "total_duration_sec": 0,
                "total_gmv": 0,
                "total_orders": 0,
            })
            pg["first_intro_sec"] = min(
                pg["first_intro_sec"],
                exp.get("time_start", float("inf"))
            )
            dur = exp.get("time_end", 0) - exp.get("time_start", 0)
            pg["total_duration_sec"] += dur
            pg["total_gmv"] += exp.get("gmv", 0) or 0
            pg["total_orders"] += exp.get("order_count", 0) or 0

        for pname, pg in product_groups.items():
            if pg["total_gmv"] > 0:
                products_with_sales.append({
                    "product_name": pname,
                    **pg,
                })
            else:
                products_without_sales.append({
                    "product_name": pname,
                    **pg,
                })

        result["metrics"]["product_intro_effectiveness"] = {
            "products_with_sales": len(products_with_sales),
            "products_without_sales": len(products_without_sales),
            "conversion_rate": round(
                len(products_with_sales) /
                (len(products_with_sales) + len(products_without_sales))
                * 100, 1
            ) if (products_with_sales or products_without_sales) else 0,
        }

        # Problem: Many products introduced but not sold
        total_products = len(products_with_sales) + len(products_without_sales)
        if total_products > 0 and len(products_without_sales) / total_products > 0.5:
            result["problems"].append("many_products_introduced_without_sales")
            result["suggestions"].append(
                "reduce_product_count_and_focus_on_fewer_high_converting_items"
            )

        # Problem: Short intro duration for products that didn't sell
        for pw in products_without_sales:
            if pw["total_duration_sec"] < 60:
                result["problems"].append("product_intro_too_short_for_unsold_items")
                result["suggestions"].append(
                    "extend_product_introduction_time_with_demo_and_social_proof"
                )
                break  # Only add once


PROMPT_REPORT_3_STRUCTURE = """
あなたはライブコマースで「売上を最大化する」ための配信構成の専門家です。

以下は、この配信の「構造的な特徴」を数値化・要約したデータです。
（※内部的には比較分析された結果ですが、その事実には一切言及しないでください）

あなたの役割：
- この配信の構成を「売上を最大化する観点」からレビューする
- 売上に直結する構成の強み・弱みを明確にする
- 売上を伸ばすための「配信構成の改善」を具体的に提案する
- 動画の描写やシーンの説明は一切不要

分析の観点：
- 商品紹介の配置とタイミング（いつ、どの順番で商品を出すか）
- 購買ピークの作り方（セールストークの盛り上げ方）
- フェーズの切り替えテンポ（視聴者を飽きさせない進行）
- オープニングとクロージングの設計
- 売上の時間分布（序盤・中盤・終盤のバランス）
- 商品紹介時間と売上の関係（紹介が短すぎる商品、長すぎる商品）
- 売上集中度（特定フェーズに偏りすぎていないか）

重要なルール：
- 「ベンチマーク」「他の動画」「平均」などの言葉を一切使わない
- 数値や内部指標の話をしない
- 動画の描写やシーンの説明は書かない
- すぐに次の配信で実践できるレベルの具体性で書く
- 出力は必ず JSON のみ

出力形式：
{
  "video_insights": [
    {
      "title": "売上に直結する短いタイトル",
      "content": "具体的な配信構成の改善アドバイス（数文）"
    }
  ]
}

入力データ：
{data}
""".strip()

def rewrite_report_3_structure_with_gpt(raw_struct_report: dict, max_retry: int = 5):
    payload = json.dumps(raw_struct_report, ensure_ascii=False, indent=2)
    prompt = PROMPT_REPORT_3_STRUCTURE.replace("{data}", payload)

    for attempt in range(max_retry):
        try:
            resp = client.responses.create(
                model=GPT5_MODEL,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": prompt
                            }
                        ]
                    }
                ],
                max_output_tokens=2048
            )

            parsed = safe_json_load(resp.output_text)
            if parsed and "video_insights" in parsed:
                return parsed

        except (RateLimitError, APITimeoutError, APIError):
            sleep = (2 ** attempt) + random.uniform(0.5, 1.5)
            time.sleep(sleep)

        except Exception:
            break

    # フォールバック（パイプラインを止めない）
    return {
        "video_insights": [
            {
                "title": "分析できませんでした",
                "content": "GPT が期待された形式で結果を返しませんでした。"
            }
        ]
    }



# ======================================================
# SAVE REPORTS
# ======================================================

def save_reports(video_id, r1, r2_raw, r2_gpt, r3_raw, r3_gpt):
    out_dir = os.path.join("report", video_id)
    os.makedirs(out_dir, exist_ok=True)

    def dump(name, obj):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)

    dump("report_1_timeline.json", r1)
    dump("report_2_phase_insights_raw.json", r2_raw)
    dump("report_2_phase_insights_gpt.json", r2_gpt)
    dump("report_3_video_insights_raw.json", r3_raw)
    dump("report_3_video_insights_gpt.json", r3_gpt)
