"""
Shopee Live Service — AI自動ライブ配信システム

Sales DashのブリッジAPIからShopeeトークンを取得し、
Shopee APIを直接呼び出して商品データ取得・Livestream管理・コメント取得を行う。

Architecture:
  AitherHub → Sales Dash Bridge API → Shopee Token
  AitherHub → Shopee API (direct) → Products, Livestream, Comments
"""
import os
import time
import hmac
import hashlib
import logging
import asyncio
from typing import Optional, Dict, List, Any
import httpx

logger = logging.getLogger(__name__)

# ============================================================
# Configuration
# ============================================================
SALES_DASH_URL = os.getenv("SALES_DASH_URL", "https://salesdash.buzzdrop.co.jp")
SALES_DASH_API_KEY = os.getenv("SALES_DASH_API_KEY", "")
SHOPEE_BASE_URL = "https://partner.shopeemobile.com"
DEFAULT_SHOP_ID = 1542634108  # KYOGOKU SG


class ShopeeTokenCache:
    """Shopeeトークンのインメモリキャッシュ（5分TTL）"""
    def __init__(self):
        self._cache: Dict[int, Dict] = {}
        self._timestamps: Dict[int, float] = {}
        self.TTL = 300  # 5 minutes

    def get(self, shop_id: int) -> Optional[Dict]:
        if shop_id in self._cache:
            if time.time() - self._timestamps[shop_id] < self.TTL:
                return self._cache[shop_id]
            del self._cache[shop_id]
            del self._timestamps[shop_id]
        return None

    def set(self, shop_id: int, data: Dict):
        self._cache[shop_id] = data
        self._timestamps[shop_id] = time.time()


_token_cache = ShopeeTokenCache()


# ============================================================
# Shopee API Signature Generation
# ============================================================
def _generate_sign(partner_key: str, base_string: str) -> str:
    """HMAC-SHA256署名を生成"""
    return hmac.new(
        partner_key.encode('utf-8'),
        base_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()


def _generate_shop_sign(
    partner_id: int,
    partner_key: str,
    path: str,
    timestamp: int,
    access_token: str,
    shop_id: int
) -> str:
    """Shop-level API用の署名を生成"""
    base_string = f"{partner_id}{path}{timestamp}{access_token}{shop_id}"
    return _generate_sign(partner_key, base_string)


# ============================================================
# Sales Dash Bridge API
# ============================================================
async def get_shopee_token(shop_id: int = DEFAULT_SHOP_ID) -> Optional[Dict]:
    """Sales DashブリッジAPIからShopeeトークンを取得（キャッシュ付き）"""
    cached = _token_cache.get(shop_id)
    if cached:
        logger.info(f"[ShopeeService] Token cache hit for shop {shop_id}")
        return cached

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # REST API v1 経由でtRPCプロシージャを呼び出し
            resp = await client.get(
                f"{SALES_DASH_URL}/api/v1/trpc/aitherhubBridge.getShopeeToken",
                params={"input": f'{{"json":{{"shopId":{shop_id}}}}}'},
                headers={"Authorization": f"Bearer {SALES_DASH_API_KEY}"}
            )
            resp.raise_for_status()
            result = resp.json()

            # tRPC superjson response format
            data = result.get("result", {}).get("data", {}).get("json", result)
            if data.get("success") and data.get("data"):
                token_data = data["data"]
                _token_cache.set(shop_id, token_data)
                logger.info(f"[ShopeeService] Token fetched for shop {shop_id}, country={token_data.get('country')}")
                return token_data
            else:
                logger.error(f"[ShopeeService] Token fetch failed: {data.get('error')}")
                return None
    except Exception as e:
        logger.error(f"[ShopeeService] Failed to get token from Sales Dash: {e}")
        return None


# ============================================================
# Shopee API Direct Calls
# ============================================================
async def _call_shopee_api(
    path: str,
    token_data: Dict,
    params: Dict = None,
    method: str = "GET",
    body: Dict = None,
) -> Optional[Dict]:
    """Shopee APIを直接呼び出す（署名付き）"""
    partner_id = token_data["partnerId"]
    partner_key = token_data["partnerKey"]
    access_token = token_data["accessToken"]
    shop_id = token_data["shopId"]
    is_test = token_data.get("isTest", False)

    base_url = "https://partner.test-stable.shopeemobile.com" if is_test else SHOPEE_BASE_URL
    timestamp = int(time.time())
    sign = _generate_shop_sign(partner_id, partner_key, path, timestamp, access_token, shop_id)

    query_params = {
        "partner_id": str(partner_id),
        "timestamp": str(timestamp),
        "sign": sign,
        "access_token": access_token,
        "shop_id": str(shop_id),
    }
    if params:
        query_params.update({k: str(v) for k, v in params.items()})

    url = f"{base_url}{path}"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            if method == "GET":
                resp = await client.get(url, params=query_params)
            else:
                resp = await client.post(
                    url,
                    params=query_params,
                    json=body or {},
                    headers={"Content-Type": "application/json"}
                )
            resp.raise_for_status()
            data = resp.json()

            if data.get("error") and data["error"] not in ("", "-"):
                logger.error(f"[ShopeeAPI] {path} Error: {data.get('error')} - {data.get('message')}")
                return None
            return data
    except Exception as e:
        logger.error(f"[ShopeeAPI] {path} Exception: {e}")
        return None


# ============================================================
# Product APIs
# ============================================================
async def get_product_list(
    shop_id: int = DEFAULT_SHOP_ID,
    offset: int = 0,
    page_size: int = 50,
) -> Optional[List[Dict]]:
    """Sales DashブリッジAPI経由で商品一覧を取得"""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{SALES_DASH_URL}/api/v1/trpc/aitherhubBridge.getShopeeProducts",
                params={"input": f'{{"json":{{"shopId":{shop_id},"offset":{offset},"pageSize":{page_size}}}}}'},
                headers={"Authorization": f"Bearer {SALES_DASH_API_KEY}"}
            )
            resp.raise_for_status()
            result = resp.json()
            data = result.get("result", {}).get("data", {}).get("json", result)
            if data.get("success"):
                return data["data"]
            logger.error(f"[ShopeeService] get_product_list failed: {data.get('error')}")
            return None
    except Exception as e:
        logger.error(f"[ShopeeService] get_product_list exception: {e}")
        return None


async def get_product_detail(
    item_ids: List[int],
    shop_id: int = DEFAULT_SHOP_ID,
) -> Optional[List[Dict]]:
    """Sales DashブリッジAPI経由で商品詳細を取得"""
    try:
        import json
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{SALES_DASH_URL}/api/v1/trpc/aitherhubBridge.getShopeeProductDetail",
                params={"input": json.dumps({"json": {"shopId": shop_id, "itemIds": item_ids}})},
                headers={"Authorization": f"Bearer {SALES_DASH_API_KEY}"}
            )
            resp.raise_for_status()
            result = resp.json()
            data = result.get("result", {}).get("data", {}).get("json", result)
            if data.get("success"):
                return data["data"]
            logger.error(f"[ShopeeService] get_product_detail failed: {data.get('error')}")
            return None
    except Exception as e:
        logger.error(f"[ShopeeService] get_product_detail exception: {e}")
        return None


# ============================================================
# Livestream APIs (Direct Shopee API calls)
# ============================================================
async def create_livestream_session(
    title: str = "KYOGOKU Live",
    cover_image_url: str = "",
    shop_id: int = DEFAULT_SHOP_ID,
) -> Optional[Dict]:
    """Shopeeライブセッションを作成"""
    token = await get_shopee_token(shop_id)
    if not token:
        return None

    return await _call_shopee_api(
        "/api/v2/livestream/create_session",
        token,
        method="POST",
        body={
            "title": title,
            "cover_image_url": cover_image_url,
        }
    )


async def get_livestream_session_detail(
    session_id: int,
    shop_id: int = DEFAULT_SHOP_ID,
) -> Optional[Dict]:
    """ライブセッション詳細を取得（RTMP URLを含む）"""
    token = await get_shopee_token(shop_id)
    if not token:
        return None

    return await _call_shopee_api(
        "/api/v2/livestream/get_session_detail",
        token,
        params={"session_id": session_id}
    )


async def start_livestream_session(
    session_id: int,
    shop_id: int = DEFAULT_SHOP_ID,
) -> Optional[Dict]:
    """ライブセッションを開始"""
    token = await get_shopee_token(shop_id)
    if not token:
        return None

    return await _call_shopee_api(
        "/api/v2/livestream/start_session",
        token,
        method="POST",
        body={"session_id": session_id}
    )


async def end_livestream_session(
    session_id: int,
    shop_id: int = DEFAULT_SHOP_ID,
) -> Optional[Dict]:
    """ライブセッションを終了"""
    token = await get_shopee_token(shop_id)
    if not token:
        return None

    return await _call_shopee_api(
        "/api/v2/livestream/end_session",
        token,
        method="POST",
        body={"session_id": session_id}
    )


# ============================================================
# Livestream Item APIs
# ============================================================
async def get_livestream_items(
    session_id: int,
    shop_id: int = DEFAULT_SHOP_ID,
) -> Optional[List[Dict]]:
    """ライブセッションに登録された商品一覧を取得"""
    token = await get_shopee_token(shop_id)
    if not token:
        return None

    result = await _call_shopee_api(
        "/api/v2/livestream/get_item_list",
        token,
        params={"session_id": session_id}
    )
    if result and result.get("response"):
        return result["response"].get("item_list", [])
    return None


async def set_show_item(
    session_id: int,
    item_id: int,
    shop_id: int = DEFAULT_SHOP_ID,
) -> Optional[Dict]:
    """ライブ中に表示する商品を設定"""
    token = await get_shopee_token(shop_id)
    if not token:
        return None

    return await _call_shopee_api(
        "/api/v2/livestream/set_show_item",
        token,
        method="POST",
        body={"session_id": session_id, "item_id": item_id}
    )


# ============================================================
# Comment APIs
# ============================================================
async def get_latest_comments(
    session_id: int,
    shop_id: int = DEFAULT_SHOP_ID,
) -> Optional[List[Dict]]:
    """ライブセッションの最新コメントを取得"""
    token = await get_shopee_token(shop_id)
    if not token:
        return None

    result = await _call_shopee_api(
        "/api/v2/livestream/get_latest_comment_list",
        token,
        params={"session_id": session_id}
    )
    if result and result.get("response"):
        return result["response"].get("comment_list", [])
    return None


async def post_comment(
    session_id: int,
    content: str,
    shop_id: int = DEFAULT_SHOP_ID,
) -> Optional[Dict]:
    """ライブセッションにコメントを投稿"""
    token = await get_shopee_token(shop_id)
    if not token:
        return None

    return await _call_shopee_api(
        "/api/v2/livestream/post_comment",
        token,
        method="POST",
        body={"session_id": session_id, "content": content}
    )


# ============================================================
# Metrics APIs
# ============================================================
async def get_session_metrics(
    session_id: int,
    shop_id: int = DEFAULT_SHOP_ID,
) -> Optional[Dict]:
    """ライブセッションのメトリクス（views, likes, comments, shares）を取得"""
    token = await get_shopee_token(shop_id)
    if not token:
        return None

    result = await _call_shopee_api(
        "/api/v2/livestream/get_session_metric",
        token,
        params={"session_id": session_id}
    )
    if result and result.get("response"):
        return result["response"]
    return None


async def get_item_metrics(
    session_id: int,
    shop_id: int = DEFAULT_SHOP_ID,
) -> Optional[List[Dict]]:
    """ライブセッションの商品別メトリクス（clicks, add-to-cart）を取得"""
    token = await get_shopee_token(shop_id)
    if not token:
        return None

    result = await _call_shopee_api(
        "/api/v2/livestream/get_session_item_metric",
        token,
        params={"session_id": session_id}
    )
    if result and result.get("response"):
        return result["response"].get("item_metric_list", [])
    return None
