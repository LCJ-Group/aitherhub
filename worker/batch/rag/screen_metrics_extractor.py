"""
Screen Metrics Extractor for aitherhub RAG system (v2).

Extracts real-time metrics from TikTok LIVE screen recordings
(Pattern B: when no sales dashboard screenshot is available).

Uses GPT-4o vision to read on-screen UI elements such as:
- Viewer count
- Like/Heart count
- Shopping rank
- Product browsing notifications
- Purchase notifications
- Comments
- Guest invitations
"""

import os
import re
import json
import base64
import logging
from typing import Dict, List, Optional

from openai import AzureOpenAI

logger = logging.getLogger("screen_metrics_extractor")

AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY")
VISION_API_VERSION = os.getenv("VISION_API_VERSION") or os.getenv("GPT5_API_VERSION", "2025-04-01-preview")
VISION_MODEL = os.getenv("VISION_MODEL", "gpt-4o")

# Lazy-initialized client
_client = None


def _get_client() -> AzureOpenAI:
    """Get or create the Azure OpenAI client (lazy initialization)."""
    global _client
    if _client is None:
        _client = AzureOpenAI(
            api_key=AZURE_OPENAI_KEY,
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_version=VISION_API_VERSION,
        )
    return _client

SCREEN_METRICS_PROMPT = """
あなたはTikTok LIVEの画面収録から数値データを抽出する専門家です。

この画面収録のフレームから、以下の情報を可能な限り読み取ってください。
読み取れない項目はnullとしてください。

必ず以下のJSON形式で返してください:

{
    "viewer_count": <リアルタイム視聴者数 (数値)>,
    "likes": <いいね数 (数値)>,
    "hearts": <ハート数 (数値)>,
    "shopping_rank": <ショッピングランキング番号 (数値)>,
    "product_browsing": "<商品閲覧状況のテキスト>",
    "purchase_notifications": ["<購入通知1>", "<購入通知2>"],
    "comments": ["<コメント1>", "<コメント2>"],
    "guest_invitations": "<ゲスト招待の状況>",
    "raffle_status": "<ラッキーラッフルの状態 (例: 28/50)>",
    "account_name": "<配信者アカウント名>",
    "follower_count": <フォロワー数 (数値)>
}
"""


def extract_metrics_from_frame(
    frame_path: str,
) -> Dict:
    """
    Extract real-time metrics from a single screen recording frame.

    Uses GPT-4o vision to read UI overlay elements from TikTok LIVE
    screen recordings.

    Parameters:
        frame_path: Path to the frame image file

    Returns:
        Dictionary of extracted metrics
    """
    try:
        with open(frame_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

        response = _get_client().chat.completions.create(
            model=VISION_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": SCREEN_METRICS_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_data}",
                                "detail": "high",
                            },
                        },
                    ],
                }
            ],
            max_tokens=1000,
            temperature=0.1,
        )

        content = response.choices[0].message.content
        # Extract JSON from response
        json_match = re.search(r"\{[\s\S]*\}", content)
        if json_match:
            metrics = json.loads(json_match.group())
            logger.info(f"Extracted metrics from frame: {frame_path}")
            return metrics
        else:
            logger.warning(f"No JSON found in response for frame: {frame_path}")
            return {}

    except Exception as e:
        logger.error(f"Failed to extract metrics from frame {frame_path}: {e}")
        return {}


def extract_metrics_from_keyframes(
    keyframe_paths: List[str],
    sample_interval: int = 5,
) -> Dict:
    """
    Extract and aggregate metrics from multiple keyframes.

    Samples keyframes at the given interval and aggregates the
    extracted metrics to build a comprehensive picture of the
    stream's real-time performance.

    Parameters:
        keyframe_paths: List of paths to keyframe images
        sample_interval: Process every Nth keyframe (default: 5)

    Returns:
        Aggregated metrics dictionary with trends
    """
    all_metrics = []
    sampled_paths = keyframe_paths[::sample_interval]

    # Limit to max 20 frames to control API costs
    if len(sampled_paths) > 20:
        step = len(sampled_paths) // 20
        sampled_paths = sampled_paths[::step][:20]

    for path in sampled_paths:
        metrics = extract_metrics_from_frame(path)
        if metrics:
            all_metrics.append(metrics)

    if not all_metrics:
        return {}

    # Aggregate metrics
    aggregated = _aggregate_metrics(all_metrics)
    return aggregated


def _aggregate_metrics(metrics_list: List[Dict]) -> Dict:
    """
    Aggregate metrics from multiple frames into a summary.

    Calculates min/max/avg for numeric fields and collects
    unique comments and notifications.
    """
    viewer_counts = [
        m.get("viewer_count") for m in metrics_list
        if m.get("viewer_count") is not None
    ]
    likes_list = [
        m.get("likes") for m in metrics_list
        if m.get("likes") is not None
    ]
    hearts_list = [
        m.get("hearts") for m in metrics_list
        if m.get("hearts") is not None
    ]

    all_comments = []
    all_purchases = []
    for m in metrics_list:
        all_comments.extend(m.get("comments", []))
        all_purchases.extend(m.get("purchase_notifications", []))

    # Deduplicate
    unique_comments = list(dict.fromkeys(all_comments))
    unique_purchases = list(dict.fromkeys(all_purchases))

    aggregated = {
        "viewer_count": max(viewer_counts) if viewer_counts else None,
        "viewer_count_min": min(viewer_counts) if viewer_counts else None,
        "viewer_count_avg": (
            sum(viewer_counts) / len(viewer_counts) if viewer_counts else None
        ),
        "viewer_trend": _calculate_trend(viewer_counts),
        "likes": max(likes_list) if likes_list else None,
        "hearts": max(hearts_list) if hearts_list else None,
        "shopping_rank": metrics_list[-1].get("shopping_rank")
        if metrics_list else None,
        "comments": unique_comments[:20],
        "comment_count": len(unique_comments),
        "purchase_notifications": unique_purchases[:20],
        "purchase_count": len(unique_purchases),
        "product_browsing": metrics_list[-1].get("product_browsing")
        if metrics_list else None,
        "guest_invitations": metrics_list[-1].get("guest_invitations")
        if metrics_list else None,
        "account_name": metrics_list[0].get("account_name")
        if metrics_list else None,
        "frames_analyzed": len(metrics_list),
    }

    return aggregated


def _calculate_trend(values: List) -> str:
    """Calculate trend direction from a list of numeric values."""
    if not values or len(values) < 2:
        return "insufficient_data"

    first_half = values[: len(values) // 2]
    second_half = values[len(values) // 2 :]

    avg_first = sum(first_half) / len(first_half)
    avg_second = sum(second_half) / len(second_half)

    if avg_second > avg_first * 1.1:
        return "increasing"
    elif avg_second < avg_first * 0.9:
        return "decreasing"
    else:
        return "stable"
