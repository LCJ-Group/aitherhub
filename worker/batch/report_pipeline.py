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


def _parse_sales_tags(raw) -> list[str]:
    """Parse human_sales_tags or sales_psychology_tags from DB into a list of strings.

    Handles: None, list, JSON string, plain string.
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(t) for t in raw if t]
    if isinstance(raw, str):
        s = raw.strip()
        if not s or s == "[]":
            return []
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return [str(t) for t in parsed if t]
        except (json.JSONDecodeError, ValueError):
            pass
        return [s]
    return []

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
def gpt_rewrite_report_2(item, language="ja"):
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
                        "text": (PROMPT_REPORT_2_ZHTW if language == "zh-TW" else PROMPT_REPORT_2_EN if language == "en" else PROMPT_REPORT_2).format(data=payload)
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
    language: str = "ja",
):
    async with sem:
        loop = asyncio.get_event_loop()

        for attempt in range(max_retry):
            try:
                text = await loop.run_in_executor(
                    None,
                    partial(gpt_rewrite_report_2, item, language=language)
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
    language: str = "ja",
):
    text = await gpt_rewrite_report_2_async(item, sem, language=language)

    if not text:
        if language == "zh-TW":
            text = (
                "關於此階段，"
                "目前的比較數據尚無法明確找出改善重點。"
                "隨著更多直播數據的累積，"
                "將能提供更具體的改善建議。"
            )
        elif language == "en":
            text = (
                "Regarding this phase, "
                "the current comparison data is insufficient to identify clear improvement points. "
                "As more broadcast data accumulates, "
                "more specific improvement suggestions will become available."
            )
        else:
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

        # Add human_sales_tags if available (expert-annotated sales technique tags)
        human_tags = _parse_sales_tags(p.get("human_sales_tags"))
        if human_tags:
            item["human_sales_tags"] = human_tags

        # Add AI-generated sales_psychology_tags if available
        ai_tags = _parse_sales_tags(p.get("sales_psychology_tags"))
        if ai_tags:
            item["sales_psychology_tags"] = ai_tags

        # Add NG (unusable) flag if clip was marked as bad by human reviewer
        if p.get("is_unusable"):
            item["is_unusable"] = True
            item["unusable_reason"] = p.get("unusable_reason", "unknown")
            if p.get("unusable_comment"):
                item["unusable_comment"] = p["unusable_comment"]

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
- セールスタグ（専門家が付与したセールス手法タグ。ある場合）
- NG判定（人間レビュアーが「使えない」と判定したフェーズ。理由付き。ある場合）

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
- セールスタグ（human_sales_tags / sales_psychology_tags）がある場合：配信者が使っているセールス手法（例：HOOK、EMPATHY、CTA、SCARCITY、SOCIAL_PROOF等）を踏まえて、足りないテクニックや強化すべきポイントを具体的にアドバイスする。タグが少ないフェーズでは「どのセールス手法を追加すべきか」を提案する
- NG判定（is_unusable=true）がある場合：このフェーズは人間レビュアーにより「使えない」と判定されている。NG理由（unusable_reason）を踏まえて、なぜこのフェーズが問題なのか、次回の配信でどう改善すべきかを具体的にアドバイスする。例：「音声が悪い」→マイク環境の改善提案、「カット位置が悪い」→話の区切りを意識した構成提案
- NGコメント（unusable_comment）がある場合：レビュアーが記入した具体的なフィードバック。このコメントは「なぜ使えないのか」の最も具体的な情報であるため、改善アドバイスの最優先根拠として活用する。例：「照明が暗くて商品の色味が分からない」→照明環境の具体的改善提案

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


PROMPT_REPORT_2_ZHTW = """
你是一位專精於「銷售額最大化」的直播電商專業顧問。

以下是某個直播階段的資訊：
- 階段描述（主播的行為與發言內容）
- 觀看人數、按讚數的變化
- 銷售數據（如有）
- 與過去最佳表現的比較
- CTA評分（1〜5，促購發言的強度。如有）
- 語音特徵值（聲音熱度、抑揚、語速、沈默率。如有）
- 銷售標籤（專家標註的銷售技巧標籤。如有）
- NG判定（人工審查員判定為「不可用」的階段。附理由。如有）

你的角色：
- 分析這個階段「如何在賣」
- 具體建議「如何賣得更好」
- 完全不需要描述影片畫面或場景

分析觀點：
- 銷售話術（促購用語、限量感、緊迫感的營造）
- 商品展示方式（示範、前後對比、使用感的傳達）
- 購買動線（加購物車的引導時機、價格提示的時機）
- 觀眾互動（留言引導、回應提問）
- 如有銷售數據：分析為何這個時段賣得好/賣不好
- 如有CTA評分：評估促購發言的強度與頻率。低分階段應建議「需要更強力地促購」等具體建議
- 如有語音特徵值：聲音熱度（energy_mean）或抑揚（pitch_std）偏低時建議「應更有感情地說話」，語速（speech_rate）過快時建議「應放慢速度仔細說明」
- 如有銷售標籤（human_sales_tags / sales_psychology_tags）：根據主播使用的銷售技巧（例如：HOOK、EMPATHY、CTA、SCARCITY、SOCIAL_PROOF等），具體建議缺少的技巧或應強化的重點。標籤較少的階段應建議「應增加哪些銷售技巧」
- 如有NG判定（is_unusable=true）：此階段已被人工審查員判定為「不可用」。根據NG理由（unusable_reason），具體建議為何此階段有問題以及下次直播應如何改善。例：「音質差」→建議改善麥克風環境，「剪輯位置不當」→建議注意話題轉換的節奏
- 如有NG評論（unusable_comment）：審查員填寫的具體反饋。此評論是「為何不可用」的最具體資訊，應作為改善建議的最優先依據。例：「燈光太暗看不清商品顏色」→具體建議改善燈光環境

輸出規則：
- 最多2個具體的銷售改善建議
- 每個建議按「目前賣法的問題點」→「具體的改善行動」順序撰寫
- 不要寫「在某個畫面中主播正在做某事」這類影片描述
- 具體到下一場直播就能立即實踐的程度
- 每項最多3句

限制：
- 不捏造數據
- 避免抽象表達（不要寫「多加工夫」，而是寫「先展示價格再告知限量數量」這樣的具體建議）
- 不直接引用語音特徵值數字（不要寫「energy_mean為0.03」，而是用「聲音熱度偏低」這樣自然的表達）

輸入：
{data}

""".strip()


PROMPT_REPORT_2_EN = """
You are an expert consultant specializing in "maximizing sales" in live commerce.

Below is the information for a specific broadcast phase:
- Phase description (broadcaster's actions and speech content)
- Viewer count and like count trends
- Sales data (if available)
- Comparison with past best performance
- CTA score (1-5, intensity of purchase-prompting statements. If available)
- Audio features (voice energy, intonation, speech rate, silence ratio. If available)
- Sales tags (expert-assigned sales technique tags. If available)
- NG judgment (phases judged as "unusable" by human reviewers. With reasons. If available)

Your role:
- Analyze "how they are selling" in this phase
- Provide specific advice on "how to sell even better"
- Do NOT describe video scenes or visuals at all

Analysis perspectives:
- Sales talk (purchase-prompting phrases, creating scarcity, urgency)
- Product presentation (demos, before/after, conveying usage experience)
- Purchase funnel (timing of cart guidance, timing of price presentation)
- Viewer engagement (comment prompting, responding to questions)
- If sales data is available: analyze why sales were high/low during this period
- If CTA score is available: evaluate the strength and frequency of purchase-prompting statements. For low-score phases, provide specific advice like "should push harder for purchases"
- If audio features are available: if voice energy (energy_mean) or intonation (pitch_std) is low, advise "should speak with more emotion"; if speech rate is too fast, advise "should slow down and explain carefully"
- If sales tags (human_sales_tags / sales_psychology_tags) are available: based on the sales techniques used (e.g., HOOK, EMPATHY, CTA, SCARCITY, SOCIAL_PROOF), specifically advise on missing techniques or points to strengthen. For phases with few tags, suggest "which sales techniques should be added"
- If NG judgment (is_unusable=true) is available: this phase was judged "unusable" by a human reviewer. Based on the NG reason (unusable_reason), specifically advise why this phase is problematic and how to improve in the next broadcast. E.g., "poor audio" → suggest microphone environment improvements, "bad cut position" → suggest structuring around natural speech breaks
- If NG comment (unusable_comment) is available: specific feedback written by the reviewer. This comment is the most concrete information about "why it's unusable," so use it as the top-priority basis for improvement advice. E.g., "lighting is too dark to see product colors" → specific lighting improvement suggestions

Output rules:
- Maximum 2 specific sales improvement suggestions
- Each suggestion should follow: "current selling problem" → "specific improvement action"
- Do NOT write video descriptions like "in this scene the broadcaster is doing..."
- Write at a level specific enough to implement in the very next broadcast
- Maximum 3 sentences per item

Constraints:
- Do not fabricate data
- Avoid abstract expressions (not "try harder" but "show the price first, then announce the limited quantity")
- Do not directly cite audio feature numbers (not "energy_mean is 0.03" but "voice energy is low")

Input:
{data}

""".strip()


def rewrite_report_2_with_gpt(raw_items, excel_data=None, language="ja"):
    results = {}

    async def runner():
        sem = asyncio.Semaphore(MAX_CONCURRENCY)
        tasks = []

        for item in raw_items:
            tasks.append(
                process_one_report2_task(
                    item,
                    sem,
                    results,
                    language=language,
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


PROMPT_REPORT_3_ZHTW = """
你是一位專精於「銷售額最大化」的直播電商專業顧問。

提供的資訊：
- 整場直播各階段的表現（觀看人數、按讚數的變動）
- 銷售數據（GMV、訂單數、GPM。如有）
- 各商品表現（介紹時間、銷售額。如有）
- 銷售觸發分析（哪個階段銷售額暴增。如有）

你的角色：
- 從整場直播的「銷售流程」進行俯瞰式分析
- 找出銷售額成長的時機點，以及錯失機會的地方
- 具體提出「直播架構改善」方案以最大化銷售額
- 完全不需要描述影片畫面或場景

分析觀點：
- 開場（第一個吸引點）的效果
- 商品介紹的時機與順序
- 購買高峰的營造方式（限量感、緊迫感、價格提示）
- 收尾（最後的推動）的力度
- 觀眾流失的時間點及其原因
- 如有銷售數據：GMV效率高的階段特徵，以及低效階段的改善方向
- 如有各商品數據：介紹時間與銷售額的關係、介紹順序的最佳化
- 如有銷售觸發數據：銷售暴增階段的共通點與重現方法

【必要規則】：
- 不捏造數字
- 完全不提及group_id或內部ID
- 不寫影片描述或場景說明
- 具體到下一場直播就能立即實踐的程度
- 輸出必須僅為JSON
- 每個洞察為1個物件＝1個洞察

輸出格式（嚴格遵守）：
{
  "video_insights": [
    {
      "title": "直接影響銷售的簡短標題",
      "content": "具體的銷售改善建議（數句）"
    }
  ]
}

輸入數據：{data}

""".strip()


PROMPT_REPORT_3_EN = """
You are an expert consultant specializing in "maximizing sales" in live commerce.

Provided information:
- Phase-by-phase performance across the entire broadcast (viewer count and like count trends)
- Sales data (GMV, order count, GPM. If available)
- Per-product performance (introduction time, sales. If available)
- Sales trigger analysis (which phases saw sales spikes. If available)

Your role:
- Analyze the overall "sales flow" of the broadcast from a bird's-eye view
- Identify when sales grew and where opportunities were missed
- Propose specific "broadcast structure improvements" to maximize sales
- Do NOT describe video scenes or visuals at all

Analysis perspectives:
- Opening (first hook) effectiveness
- Product introduction timing and order
- Creating purchase peaks (scarcity, urgency, price presentation)
- Closing (final push) strength
- Points where viewer drop-off occurs and its causes
- If sales data is available: characteristics of high GMV-efficiency phases and improvement points for low-efficiency phases
- If per-product data is available: relationship between introduction time and sales, optimization of introduction order
- If sales trigger data is available: commonalities of sales-spike phases and how to replicate them

[Mandatory Rules]:
- Do not fabricate numbers
- Never mention group_id or internal IDs
- Do not write video descriptions or scene explanations
- Write at a level specific enough to implement immediately in the next broadcast
- Output must be JSON only
- Each insight = 1 object = 1 insight

Output format (strict):
{
  "video_insights": [
    {
      "title": "Short title directly related to sales",
      "content": "Specific sales improvement advice (a few sentences)"
    }
  ]
}

Input data:
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


def rewrite_report_3_with_gpt(raw_video_insight, max_retry: int = 5, language: str = "ja"):
    payload = json.dumps(raw_video_insight, ensure_ascii=False)
    prompt_tpl = PROMPT_REPORT_3_ZHTW if language == "zh-TW" else PROMPT_REPORT_3_EN if language == "en" else PROMPT_REPORT_3
    prompt = prompt_tpl.replace("{data}", payload)

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

    fallback_title = "無法完成分析" if language == "zh-TW" else "Analysis could not be completed" if language == "en" else "分析できませんでした"
    fallback_content = "GPT 未能以預期格式回傳結果。" if language == "zh-TW" else "GPT did not return results in the expected format." if language == "en" else "GPT が期待された形式で結果を返しませんでした。"
    return {
        "video_insights": [
            {
                "title": fallback_title,
                "content": fallback_content
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


PROMPT_REPORT_3_STRUCTURE_ZHTW = """
你是一位專精於「銷售額最大化」的直播架構專家。

以下是這場直播的「結構性特徵」的數值化與摘要數據。
（※內部已進行比較分析，但請完全不要提及這個事實）

你的角色：
- 從「銷售額最大化」的觀點審視這場直播的架構
- 明確指出影響銷售的架構優勢與劣勢
- 具體提出「直播架構改善」方案以提升銷售額
- 完全不需要描述影片畫面或場景

分析觀點：
- 商品介紹的配置與時機（何時、以什麼順序推出商品）
- 購買高峰的營造方式（銷售話術的高潮營造）
- 階段切換的節奏（不讓觀眾感到無聊的進行方式）
- 開場與收尾的設計
- 銷售的時間分布（前段、中段、後段的平衡）
- 商品介紹時間與銷售額的關係（介紹過短的商品、過長的商品）
- 銷售集中度（是否過度集中在特定階段）

重要規則：
- 完全不使用「基準」「其他影片」「平均」等用語
- 不談論數值或內部指標
- 不寫影片描述或場景說明
- 具體到下一場直播就能立即實踐的程度
- 輸出必須僅為JSON

輸出格式：
{
  "video_insights": [
    {
      "title": "直接影響銷售的簡短標題",
      "content": "具體的直播架構改善建議（數句）"
    }
  ]
}

輸入數據：
{data}
""".strip()


PROMPT_REPORT_3_STRUCTURE_EN = """
You are an expert in broadcast structure design for "maximizing sales" in live commerce.

Below is the numerical summary and structural characteristics of this broadcast.
(※This data has been internally benchmarked, but do NOT mention this fact at all.)

Your role:
- Review this broadcast's structure from the perspective of "maximizing sales"
- Clearly identify structural strengths and weaknesses that directly impact sales
- Propose specific "broadcast structure improvements" to boost sales
- Do NOT describe video scenes or visuals at all

Analysis perspectives:
- Product introduction placement and timing (when and in what order products are presented)
- Creating purchase peaks (building momentum in sales talk)
- Phase transition tempo (keeping viewers engaged without boredom)
- Opening and closing design
- Sales time distribution (balance across beginning, middle, and end)
- Relationship between product introduction duration and sales (products with too short/too long introductions)
- Sales concentration (whether sales are overly concentrated in specific phases)

Important rules:
- Never use words like "benchmark," "other videos," or "average"
- Do not discuss numbers or internal metrics
- Do not write video descriptions or scene explanations
- Write at a level specific enough to implement immediately in the next broadcast
- Output must be JSON only

Output format:
{
  "video_insights": [
    {
      "title": "Short title directly related to sales",
      "content": "Specific broadcast structure improvement advice (a few sentences)"
    }
  ]
}

Input data:
{data}
""".strip()

def rewrite_report_3_structure_with_gpt(raw_struct_report: dict, max_retry: int = 5, language: str = "ja"):
    payload = json.dumps(raw_struct_report, ensure_ascii=False, indent=2)
    prompt_tpl = PROMPT_REPORT_3_STRUCTURE_ZHTW if language == "zh-TW" else PROMPT_REPORT_3_STRUCTURE_EN if language == "en" else PROMPT_REPORT_3_STRUCTURE
    prompt = prompt_tpl.replace("{data}", payload)

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

    # Fallback (don't stop the pipeline)
    fallback_title = "無法完成分析" if language == "zh-TW" else "Analysis could not be completed" if language == "en" else "分析できませんでした"
    fallback_content = "GPT 未能以預期格式回傳結果。" if language == "zh-TW" else "GPT did not return results in the expected format." if language == "en" else "GPT が期待された形式で結果を返しませんでした。"
    return {
        "video_insights": [
            {
                "title": fallback_title,
                "content": fallback_content
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
