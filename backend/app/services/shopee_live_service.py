"""
Shopee Live Service — AI自動ライブ配信システム（自己完結型）

AitherHub自身がPartner ID/Keyでトークンを管理し、
Shopee APIを直接呼び出して商品データ取得・Livestream管理・コメント取得を行う。

Architecture:
  AitherHub → Shopee API (direct) → Token, Products, Livestream, Comments

Token Management:
  - access_token: 4時間有効、自動リフレッシュ
  - refresh_token: 30日有効、環境変数で初期値設定
  - インメモリキャッシュ + 自動リフレッシュ
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
SHOPEE_PARTNER_ID = int(os.getenv("SHOPEE_PARTNER_ID", "2033043"))
SHOPEE_PARTNER_KEY = os.getenv(
    "SHOPEE_PARTNER_KEY",
    "shpk68566c6b65466c6b526868556f6a4e564f6a7049657061476947506d4a79",
)
SHOPEE_BASE_URL = "https://partner.shopeemobile.com"
DEFAULT_SHOP_ID = 1542634108  # KYOGOKU SG
DEFAULT_USER_ID = 1543466652  # KYOGOKU SG user

# 初期トークン（環境変数から。一度リフレッシュされたらメモリ内で更新）
_INITIAL_ACCESS_TOKEN = os.getenv("SHOPEE_ACCESS_TOKEN", "")
_INITIAL_REFRESH_TOKEN = os.getenv("SHOPEE_REFRESH_TOKEN", "")

# Legacy: Sales Dash bridge (kept for backward compat but no longer primary)
SALES_DASH_URL = os.getenv("SALES_DASH_URL", "https://salesdash.buzzdrop.co.jp")
SALES_DASH_API_KEY = os.getenv("SALES_DASH_API_KEY", "")


# ============================================================
# Token Store (in-memory with auto-refresh)
# ============================================================
class ShopeeTokenStore:
    """
    Shopeeトークンのインメモリストア。
    access_tokenの有効期限を追跡し、期限切れ前に自動リフレッシュ。
    """

    def __init__(self):
        self._tokens: Dict[int, Dict] = {}
        self._lock = asyncio.Lock()

    def _is_valid(self, shop_id: int) -> bool:
        t = self._tokens.get(shop_id)
        if not t:
            return False
        # 5分前にリフレッシュ（バッファ）
        return time.time() < t.get("expire_at", 0) - 300

    def get(self, shop_id: int) -> Optional[Dict]:
        if self._is_valid(shop_id):
            return self._tokens[shop_id]
        return None

    def set(self, shop_id: int, access_token: str, refresh_token: str,
            expire_in: int, user_id: int = 0):
        self._tokens[shop_id] = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expire_at": time.time() + expire_in,
            "user_id": user_id or DEFAULT_USER_ID,
            "shop_id": shop_id,
            "partner_id": SHOPEE_PARTNER_ID,
            "partner_key": SHOPEE_PARTNER_KEY,
        }
        logger.info(
            f"[ShopeeToken] Stored token for shop {shop_id}, "
            f"expires in {expire_in}s"
        )

    def get_refresh_token(self, shop_id: int) -> Optional[str]:
        t = self._tokens.get(shop_id)
        if t:
            return t.get("refresh_token")
        return None


_token_store = ShopeeTokenStore()


# ============================================================
# Shopee API Signature Generation
# ============================================================
def _generate_sign(partner_key: str, base_string: str) -> str:
    """HMAC-SHA256署名を生成"""
    return hmac.new(
        partner_key.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _generate_shop_sign(
    partner_id: int,
    partner_key: str,
    path: str,
    timestamp: int,
    access_token: str,
    shop_id: int,
) -> str:
    """Shop-level API用の署名を生成"""
    base_string = f"{partner_id}{path}{timestamp}{access_token}{shop_id}"
    return _generate_sign(partner_key, base_string)


def _generate_public_sign(
    partner_id: int,
    partner_key: str,
    path: str,
    timestamp: int,
) -> str:
    """Public API用の署名を生成（access_token/shop_idなし）"""
    base_string = f"{partner_id}{path}{timestamp}"
    return _generate_sign(partner_key, base_string)


# ============================================================
# Token Management (Self-contained)
# ============================================================
async def _refresh_access_token(shop_id: int) -> Optional[Dict]:
    """refresh_tokenを使ってaccess_tokenをリフレッシュ"""
    refresh_token = _token_store.get_refresh_token(shop_id)
    if not refresh_token:
        # 初期refresh_tokenを使用
        refresh_token = _INITIAL_REFRESH_TOKEN
        if not refresh_token:
            logger.error("[ShopeeToken] No refresh_token available")
            return None

    path = "/api/v2/auth/access_token/get"
    timestamp = int(time.time())
    sign = _generate_public_sign(SHOPEE_PARTNER_ID, SHOPEE_PARTNER_KEY, path, timestamp)

    url = f"{SHOPEE_BASE_URL}{path}"
    params = {
        "partner_id": str(SHOPEE_PARTNER_ID),
        "timestamp": str(timestamp),
        "sign": sign,
    }
    body = {
        "refresh_token": refresh_token,
        "partner_id": SHOPEE_PARTNER_ID,
        "shop_id": shop_id,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, params=params, json=body)
            resp.raise_for_status()
            data = resp.json()

            if data.get("error") and data["error"] != "":
                logger.error(
                    f"[ShopeeToken] Refresh failed: {data.get('error')} - "
                    f"{data.get('message')}"
                )
                return None

            new_access = data.get("access_token", "")
            new_refresh = data.get("refresh_token", "")
            expire_in = data.get("expire_in", 14400)
            user_id = data.get("user_id", DEFAULT_USER_ID)

            if new_access and new_refresh:
                _token_store.set(shop_id, new_access, new_refresh, expire_in, user_id)
                logger.info(f"[ShopeeToken] Token refreshed for shop {shop_id}")
                return _token_store.get(shop_id)
            else:
                logger.error("[ShopeeToken] Empty tokens in refresh response")
                return None

    except Exception as e:
        logger.error(f"[ShopeeToken] Refresh exception: {e}")
        return None


async def get_shopee_token(shop_id: int = DEFAULT_SHOP_ID) -> Optional[Dict]:
    """
    Shopeeトークンを取得（自己完結型）。
    1. キャッシュにあればそれを返す
    2. なければrefresh_tokenでリフレッシュ
    3. 初回は環境変数の初期トークンを使用
    """
    # 1. キャッシュチェック
    cached = _token_store.get(shop_id)
    if cached:
        logger.debug(f"[ShopeeToken] Cache hit for shop {shop_id}")
        return cached

    # 2. 初期トークンがあれば先にストアに入れる（初回起動時）
    async with _token_store._lock:
        # ダブルチェック
        cached = _token_store.get(shop_id)
        if cached:
            return cached

        if _INITIAL_ACCESS_TOKEN:
            # 初期トークンをストアに入れて、まずこれを使う
            _token_store.set(
                shop_id,
                _INITIAL_ACCESS_TOKEN,
                _INITIAL_REFRESH_TOKEN,
                3600,  # 1時間と仮定（すぐリフレッシュされる）
                DEFAULT_USER_ID,
            )
            logger.info(f"[ShopeeToken] Loaded initial token for shop {shop_id}")
            return _token_store.get(shop_id)

        # 3. refresh_tokenでリフレッシュ
        result = await _refresh_access_token(shop_id)
        if result:
            return result

        logger.error(f"[ShopeeToken] All token acquisition methods failed for shop {shop_id}")
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
    partner_id = token_data["partner_id"]
    partner_key = token_data["partner_key"]
    access_token = token_data["access_token"]
    shop_id = token_data["shop_id"]
    user_id = token_data.get("user_id", DEFAULT_USER_ID)

    timestamp = int(time.time())
    sign = _generate_shop_sign(
        partner_id, partner_key, path, timestamp, access_token, shop_id
    )

    query_params = {
        "partner_id": str(partner_id),
        "timestamp": str(timestamp),
        "sign": sign,
        "access_token": access_token,
        "shop_id": str(shop_id),
    }
    # Livestream APIはuser_idが必要
    if "/livestream/" in path:
        query_params["user_id"] = str(user_id)
    if params:
        query_params.update({k: str(v) for k, v in params.items()})

    url = f"{SHOPEE_BASE_URL}{path}"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            if method == "GET":
                resp = await client.get(url, params=query_params)
            else:
                resp = await client.post(
                    url,
                    params=query_params,
                    json=body or {},
                    headers={"Content-Type": "application/json"},
                )
            resp.raise_for_status()
            data = resp.json()

            if data.get("error") and data["error"] not in ("", "-"):
                error_msg = data.get("error", "")
                # トークン期限切れの場合、自動リフレッシュして再試行
                if error_msg in ("error_auth", "error_token_expired"):
                    logger.warning(
                        f"[ShopeeAPI] Token expired for shop {shop_id}, refreshing..."
                    )
                    new_token = await _refresh_access_token(shop_id)
                    if new_token:
                        return await _call_shopee_api(
                            path, new_token, params, method, body
                        )
                logger.error(
                    f"[ShopeeAPI] {path} Error: {error_msg} - {data.get('message')}"
                )
                return data  # エラーでもレスポンスを返す（呼び出し元で判断）
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
    item_status: str = "NORMAL",
) -> Optional[Dict]:
    """Shopee商品一覧を取得"""
    token = await get_shopee_token(shop_id)
    if not token:
        return None
    return await _call_shopee_api(
        "/api/v2/product/get_item_list",
        token,
        params={
            "offset": offset,
            "page_size": page_size,
            "item_status": item_status,
        },
    )


async def get_product_detail(
    item_ids: List[int],
    shop_id: int = DEFAULT_SHOP_ID,
) -> Optional[Dict]:
    """Shopee商品詳細を取得"""
    token = await get_shopee_token(shop_id)
    if not token:
        return None
    ids_str = ",".join(str(i) for i in item_ids[:50])
    return await _call_shopee_api(
        "/api/v2/product/get_item_base_info",
        token,
        params={"item_id_list": ids_str},
    )


# ============================================================
# Livestream Session APIs
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
    body = {"title": title}
    if cover_image_url:
        body["cover_image_url"] = cover_image_url
    return await _call_shopee_api(
        "/api/v2/livestream/create_session",
        token,
        method="POST",
        body=body,
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
        params={"session_id": session_id},
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
        body={"session_id": session_id},
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
        body={"session_id": session_id},
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
        params={"session_id": session_id},
    )
    if result and result.get("response"):
        return result["response"].get("item_list", [])
    return None


async def add_livestream_items(
    session_id: int,
    item_ids: List[int],
    shop_id: int = DEFAULT_SHOP_ID,
) -> Optional[Dict]:
    """ライブセッションに商品を追加"""
    token = await get_shopee_token(shop_id)
    if not token:
        return None
    return await _call_shopee_api(
        "/api/v2/livestream/add_item_list",
        token,
        method="POST",
        body={"session_id": session_id, "item_id_list": item_ids},
    )


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
        body={"session_id": session_id, "item_id": item_id},
    )


# ============================================================
# Comment APIs
# ============================================================
async def get_latest_comments(
    session_id: int,
    shop_id: int = DEFAULT_SHOP_ID,
    cursor: str = "",
) -> Optional[List[Dict]]:
    """ライブセッションの最新コメントを取得"""
    token = await get_shopee_token(shop_id)
    if not token:
        return None
    p = {"session_id": session_id}
    if cursor:
        p["cursor"] = cursor
    result = await _call_shopee_api(
        "/api/v2/livestream/get_latest_comment_list",
        token,
        params=p,
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
        body={"session_id": session_id, "content": content},
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
        params={"session_id": session_id},
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
        params={"session_id": session_id},
    )
    if result and result.get("response"):
        return result["response"].get("item_metric_list", [])
    return None
