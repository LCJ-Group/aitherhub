"""
Sales Data Ingester for aitherhub RAG system (v2).

Handles ingestion of sales data from multiple sources:
1. TikTok LIVE dashboard screenshots (OCR via GPT-4o vision)
2. LCJ system API (structured JSON data)
3. Manual CSV/JSON upload

This module normalizes data from all sources into a unified
sales_data structure for storage in the knowledge base.
"""

import os
import re
import json
import base64
import logging
import csv
from io import StringIO
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from openai import AzureOpenAI

logger = logging.getLogger("sales_data_ingester")

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


# ============================================================
# TikTok Dashboard Screenshot OCR (Pattern A)
# ============================================================

TIKTOK_DASHBOARD_PROMPT = """
あなたはTikTok LIVEのダッシュボードスクリーンショットからデータを抽出する専門家です。

このスクリーンショットから、以下の情報を可能な限り正確に読み取ってください。
読み取れない項目はnullとしてください。数値はカンマなしの数字で返してください。

必ず以下のJSON形式で返してください:

{
    "gmv": <GMV/総売上金額 (数値、円)>,
    "total_orders": <注文数 (数値)>,
    "product_sales_count": <商品販売数 (数値)>,
    "viewers": <累計視聴者数 (数値)>,
    "impressions": <インプレッション数 (数値)>,
    "product_impressions": <商品インプレッション数 (数値)>,
    "product_clicks": <商品クリック数 (数値)>,
    "live_ctr": <LIVE CTR (パーセント、数値)>,
    "cvr": <CVR/転換率 (パーセント、数値)>,
    "tap_through_rate": <タップスルー率 (パーセント、数値)>,
    "comment_rate": <コメント率 (パーセント、数値)>,
    "avg_gpm": <時間あたりGMV (数値、円)>,
    "duration_minutes": <配信時間 (分、数値)>,
    "follower_ratio": <フォロワー率 (パーセント、数値)>,
    "traffic_sources": [
        {
            "channel": "<チャネル名>",
            "gmv_pct": <GMV割合 (パーセント)>,
            "impression_pct": <インプレッション割合 (パーセント)>,
            "viewer_pct": <視聴者割合 (パーセント)>
        }
    ]
}
"""

TIKTOK_PRODUCTS_PROMPT = """
あなたはTikTok LIVEの商品販売データスクリーンショットからデータを抽出する専門家です。

このスクリーンショットから、販売されたセット商品の情報を読み取ってください。

必ず以下のJSON形式で返してください:

{
    "set_products": [
        {
            "name": "<セット商品名>",
            "price": <販売価格 (数値、円)>,
            "original_price": <定価 (数値、円)>,
            "discount_rate": <割引率 (パーセント、数値)>,
            "quantity_sold": <販売セット数 (数値)>,
            "set_revenue": <セット売上 (数値、円)>,
            "items": ["<含まれる商品1>", "<含まれる商品2>"]
        }
    ]
}
"""


def ingest_from_dashboard_screenshot(
    screenshot_path: str,
) -> Dict:
    """
    Extract sales data from a TikTok LIVE dashboard screenshot.

    Uses GPT-4o vision to OCR the dashboard and extract structured
    sales metrics.

    Parameters:
        screenshot_path: Path to the dashboard screenshot image

    Returns:
        Normalized sales_data dictionary
    """
    try:
        with open(screenshot_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

        response = _get_client().chat.completions.create(
            model=VISION_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": TIKTOK_DASHBOARD_PROMPT},
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
            max_tokens=2000,
            temperature=0.1,
        )

        content = response.choices[0].message.content
        json_match = re.search(r"\{[\s\S]*\}", content)
        if json_match:
            sales_data = json.loads(json_match.group())
            sales_data = _normalize_sales_data(sales_data)
            logger.info(
                f"Extracted sales data from dashboard: "
                f"GMV=¥{sales_data.get('gmv', 0):,.0f}"
            )
            return sales_data
        else:
            logger.warning("No JSON found in dashboard OCR response")
            return {}

    except Exception as e:
        logger.error(f"Failed to extract dashboard data: {e}")
        return {}


def ingest_products_from_screenshot(
    screenshot_path: str,
) -> List[Dict]:
    """
    Extract set product data from a product sales screenshot.

    Parameters:
        screenshot_path: Path to the product sales screenshot

    Returns:
        List of set_product dictionaries
    """
    try:
        with open(screenshot_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

        response = _get_client().chat.completions.create(
            model=VISION_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": TIKTOK_PRODUCTS_PROMPT},
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
            max_tokens=2000,
            temperature=0.1,
        )

        content = response.choices[0].message.content
        json_match = re.search(r"\{[\s\S]*\}", content)
        if json_match:
            data = json.loads(json_match.group())
            products = data.get("set_products", [])
            products = [_normalize_product(p) for p in products]
            logger.info(f"Extracted {len(products)} set products from screenshot")
            return products
        else:
            logger.warning("No JSON found in product OCR response")
            return []

    except Exception as e:
        logger.error(f"Failed to extract product data: {e}")
        return []


# ============================================================
# LCJ System API Integration
# ============================================================

def ingest_from_lcj_api(
    lcj_data: Dict,
) -> Tuple[Dict, List[Dict]]:
    """
    Normalize sales data received from the LCJ system API.

    The LCJ system sends structured JSON data from its database,
    which may include TikTok dashboard data and product sales data.

    Parameters:
        lcj_data: Raw data from LCJ API

    Returns:
        Tuple of (sales_data, set_products)
    """
    sales_data = {}
    set_products = []

    # Extract sales summary
    if "sales_summary" in lcj_data:
        summary = lcj_data["sales_summary"]
        sales_data = {
            "gmv": _safe_float(summary.get("gmv")),
            "total_orders": _safe_int(summary.get("total_orders")),
            "product_sales_count": _safe_int(summary.get("product_sales_count")),
            "viewers": _safe_int(summary.get("viewers")),
            "impressions": _safe_int(summary.get("impressions")),
            "product_impressions": _safe_int(summary.get("product_impressions")),
            "product_clicks": _safe_int(summary.get("product_clicks")),
            "live_ctr": _safe_float(summary.get("live_ctr")),
            "cvr": _safe_float(summary.get("cvr")),
            "tap_through_rate": _safe_float(summary.get("tap_through_rate")),
            "comment_rate": _safe_float(summary.get("comment_rate")),
            "avg_gpm": _safe_float(summary.get("avg_gpm")),
            "duration_minutes": _safe_float(summary.get("duration_minutes")),
            "follower_ratio": _safe_float(summary.get("follower_ratio")),
        }

        # Traffic sources
        if "traffic_sources" in summary:
            sales_data["traffic_sources"] = summary["traffic_sources"]

    # Extract product data
    if "products" in lcj_data:
        for p in lcj_data["products"]:
            set_products.append(_normalize_product(p))

    # Extract from flat format (alternative LCJ format)
    if "gmv" in lcj_data and "sales_summary" not in lcj_data:
        sales_data = _normalize_sales_data(lcj_data)

    logger.info(
        f"Ingested LCJ data: GMV=¥{sales_data.get('gmv', 0):,.0f}, "
        f"{len(set_products)} products"
    )
    return sales_data, set_products


# ============================================================
# CSV/JSON Manual Upload
# ============================================================

def ingest_from_csv(csv_content: str) -> Tuple[Dict, List[Dict]]:
    """
    Parse sales data from a CSV string.

    Expected CSV format:
    metric,value
    gmv,150000
    total_orders,45
    ...

    Returns:
        Tuple of (sales_data, set_products)
    """
    reader = csv.DictReader(StringIO(csv_content))
    raw_data = {}
    for row in reader:
        key = row.get("metric", "").strip()
        value = row.get("value", "").strip()
        if key and value:
            raw_data[key] = value

    sales_data = _normalize_sales_data(raw_data)
    return sales_data, []


def ingest_from_json(json_content: str) -> Tuple[Dict, List[Dict]]:
    """
    Parse sales data from a JSON string.

    Accepts both flat format and nested LCJ format.

    Returns:
        Tuple of (sales_data, set_products)
    """
    data = json.loads(json_content)

    if "sales_summary" in data or "products" in data:
        return ingest_from_lcj_api(data)
    else:
        sales_data = _normalize_sales_data(data)
        set_products = [
            _normalize_product(p)
            for p in data.get("set_products", [])
        ]
        return sales_data, set_products


# ============================================================
# Normalization Helpers
# ============================================================

def _normalize_sales_data(raw: Dict) -> Dict:
    """Normalize raw sales data into a consistent format."""
    return {
        "gmv": _safe_float(raw.get("gmv")),
        "total_orders": _safe_int(raw.get("total_orders")),
        "product_sales_count": _safe_int(raw.get("product_sales_count")),
        "viewers": _safe_int(raw.get("viewers")),
        "impressions": _safe_int(raw.get("impressions")),
        "product_impressions": _safe_int(raw.get("product_impressions")),
        "product_clicks": _safe_int(raw.get("product_clicks")),
        "live_ctr": _safe_float(raw.get("live_ctr")),
        "cvr": _safe_float(raw.get("cvr")),
        "tap_through_rate": _safe_float(raw.get("tap_through_rate")),
        "comment_rate": _safe_float(raw.get("comment_rate")),
        "avg_gpm": _safe_float(raw.get("avg_gpm")),
        "duration_minutes": _safe_float(raw.get("duration_minutes")),
        "follower_ratio": _safe_float(raw.get("follower_ratio")),
        "traffic_sources": raw.get("traffic_sources", []),
    }


def _normalize_product(raw: Dict) -> Dict:
    """Normalize raw product data into a consistent format."""
    price = _safe_float(raw.get("price", 0))
    qty = _safe_int(raw.get("quantity_sold", 0))
    revenue = _safe_float(raw.get("set_revenue", 0))

    # Calculate revenue if not provided
    if not revenue and price and qty:
        revenue = price * qty

    return {
        "name": raw.get("name", ""),
        "price": price,
        "original_price": _safe_float(raw.get("original_price", 0)),
        "discount_rate": _safe_float(raw.get("discount_rate", 0)),
        "quantity_sold": qty,
        "set_revenue": revenue,
        "items": raw.get("items", []),
    }


def _safe_float(value) -> float:
    """Safely convert a value to float."""
    if value is None:
        return 0.0
    try:
        if isinstance(value, str):
            # Remove currency symbols, commas, percent signs
            cleaned = value.replace("¥", "").replace(",", "").replace("%", "").strip()
            return float(cleaned) if cleaned else 0.0
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def _safe_int(value) -> int:
    """Safely convert a value to int."""
    if value is None:
        return 0
    try:
        if isinstance(value, str):
            cleaned = value.replace(",", "").strip()
            return int(float(cleaned)) if cleaned else 0
        return int(value)
    except (ValueError, TypeError):
        return 0
