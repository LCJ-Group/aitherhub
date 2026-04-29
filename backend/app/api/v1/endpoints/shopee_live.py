"""
Shopee Live API Endpoints

AI自動ライブ配信システムのShopee連携エンドポイント。
商品データ取得、Livestream管理、コメント取得/応答を提供。
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/shopee-live", tags=["shopee-live"])


# ============================================================
# Request/Response Models
# ============================================================
class CreateSessionRequest(BaseModel):
    title: str = "KYOGOKU Live"
    cover_image_url: str = ""
    shop_id: int = 1542634108

class PostCommentRequest(BaseModel):
    session_id: int
    content: str
    shop_id: int = 1542634108

class SetShowItemRequest(BaseModel):
    session_id: int
    item_id: int
    shop_id: int = 1542634108

class SessionActionRequest(BaseModel):
    session_id: int
    shop_id: int = 1542634108


# ============================================================
# OAuth Token Exchange
# ============================================================
class TokenExchangeRequest(BaseModel):
    code: str
    shop_id: int = 1542634108


@router.get("/auth/callback")
async def auth_callback(code: str = Query(...), shop_id: int = Query(default=1542634108)):
    """
    Shopee OAuth callbackエンドポイント。
    認証後のリダイレクトを受けて、codeでトークンを取得。
    """
    from app.services.shopee_live_service import exchange_auth_code
    result = await exchange_auth_code(code, shop_id)
    if result is None:
        return {"status": "error", "message": "Failed to exchange auth code for tokens"}
    return {
        "status": "ok",
        "message": "Shopee tokens refreshed successfully",
        "shop_id": shop_id,
        "has_access_token": bool(result.get("access_token")),
        "has_refresh_token": bool(result.get("refresh_token")),
        "expire_in": result.get("expire_in"),
    }


@router.post("/auth/exchange")
async def exchange_token(req: TokenExchangeRequest):
    """
    手動トークン交換エンドポイント。
    OAuth codeを使ってaccess_token/refresh_tokenを取得。
    """
    from app.services.shopee_live_service import exchange_auth_code
    result = await exchange_auth_code(req.code, req.shop_id)
    if result is None:
        raise HTTPException(status_code=500, detail="Failed to exchange auth code for tokens")
    return {
        "status": "ok",
        "message": "Shopee tokens refreshed successfully",
        "shop_id": req.shop_id,
        "has_access_token": bool(result.get("access_token")),
        "has_refresh_token": bool(result.get("refresh_token")),
        "expire_in": result.get("expire_in"),
    }


# ============================================================
# Health Check
# ============================================================
@router.get("/health")
async def health_check(test_api: bool = Query(default=False)):
    """Shopeeトークン取得テスト（自己完結型）"""
    from app.services.shopee_live_service import get_shopee_token, get_product_list
    token = await get_shopee_token()
    if not token:
        return {"status": "error", "message": "Failed to get Shopee token"}
    result = {
        "status": "ok",
        "shop_id": token.get("shop_id"),
        "partner_id": token.get("partner_id"),
        "has_access_token": bool(token.get("access_token")),
        "has_refresh_token": bool(token.get("refresh_token")),
    }
    if test_api:
        # 実際にShopee APIを呼んでテスト
        products = await get_product_list(token.get("shop_id", 1542634108), 0, 1)
        result["test_api_result"] = products
    return result


# ============================================================
# Helpers
# ============================================================
async def _normalize_product_list(raw: dict, shop_id: int = 1542634108) -> dict:
    """
    Shopee APIの生レスポンスをフロントエンドが期待する形式に変換。
    商品一覧(item_id のみ)を取得後、get_item_base_info で詳細(名前・画像・価格)を
    自動取得して結合する。

    Shopee get_item_list: { response: { item: [{item_id, item_status, ...}] } }
    Shopee get_item_base_info: { response: { item_list: [{item_id, item_name, image, price_info, ...}] } }
    Frontend expects: { items: [{item_id, item_name, image_url, price, ...}] }
    """
    from app.services.shopee_live_service import get_product_detail

    items = []
    error = None
    if isinstance(raw, dict):
        # Shopee APIエラーチェック
        if raw.get("error") and raw["error"] not in ("", "-"):
            error = raw.get("error")
        resp = raw.get("response") or {}
        items = resp.get("item") or []

    if not items or error:
        return {
            "items": items,
            "total": len(items),
            "has_next_page": bool((raw.get("response") or {}).get("has_next_page")),
            "error": error,
        }

    # ── 商品詳細を取得して結合 ──
    item_ids = [it["item_id"] for it in items if "item_id" in it]
    enriched_items = []

    # Shopee APIは1回50件まで → バッチ処理
    BATCH = 50
    detail_map: dict = {}
    for i in range(0, len(item_ids), BATCH):
        batch_ids = item_ids[i:i + BATCH]
        try:
            detail_raw = await get_product_detail(batch_ids, shop_id)
            if detail_raw and isinstance(detail_raw, dict):
                detail_resp = detail_raw.get("response") or {}
                for d in detail_resp.get("item_list") or []:
                    detail_map[d["item_id"]] = d
        except Exception as e:
            logger.warning(f"Failed to fetch product details for batch: {e}")

    for it in items:
        iid = it.get("item_id")
        detail = detail_map.get(iid, {})
        # 画像URL: 最初の画像を使用
        image_urls = (detail.get("image") or {}).get("image_url_list") or []
        image_url = image_urls[0] if image_urls else ""
        # 価格情報
        price_info = detail.get("price_info") or []
        price = ""
        original_price = ""
        currency = ""
        if price_info:
            p = price_info[0] if isinstance(price_info, list) else price_info
            price = p.get("current_price") or p.get("original_price") or ""
            original_price = p.get("original_price") or ""
            currency = p.get("currency") or ""

        enriched_items.append({
            "item_id": iid,
            "item_name": detail.get("item_name") or "",
            "item_status": it.get("item_status") or detail.get("item_status") or "",
            "image_url": image_url,
            "price": price,
            "original_price": original_price,
            "currency": currency,
            "description": (detail.get("description") or "")[:200],
            "has_model": detail.get("has_model", False),
            "category_id": detail.get("category_id", 0),
        })

    return {
        "items": enriched_items,
        "total": len(enriched_items),
        "has_next_page": bool((raw.get("response") or {}).get("has_next_page")),
        "error": error,
    }


# ============================================================
# Product Endpoints
# ============================================================
@router.get("/products")
async def list_products(
    shop_id: int = Query(default=1542634108),
    offset: int = Query(default=0),
    page_size: int = Query(default=50),
):
    """Shopee商品一覧を取得"""
    from app.services.shopee_live_service import get_product_list
    result = await get_product_list(shop_id, offset, page_size)
    if result is None:
        raise HTTPException(status_code=500, detail="Failed to fetch products from Shopee API")
    # フロントエンド互換: response.item → items に変換（詳細情報も自動取得）
    return await _normalize_product_list(result, shop_id)


@router.get("/products/detail")
async def get_product_details(
    item_ids: str = Query(..., description="Comma-separated item IDs"),
    shop_id: int = Query(default=1542634108),
):
    """Shopee商品詳細を取得（AIセールストーク生成用）"""
    from app.services.shopee_live_service import get_product_detail
    ids = [int(x.strip()) for x in item_ids.split(",") if x.strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="No item IDs provided")
    result = await get_product_detail(ids, shop_id)
    if result is None:
        raise HTTPException(status_code=500, detail="Failed to fetch product details")
    return result


@router.get("/products/{shop_id}")
async def list_products_by_path(
    shop_id: int,
    offset: int = Query(default=0),
    page_size: int = Query(default=50),
):
    """Shopee商品一覧を取得（パスパラメータ版 - フロントエンド互換）"""
    from app.services.shopee_live_service import get_product_list
    result = await get_product_list(shop_id, offset, page_size)
    if result is None:
        raise HTTPException(status_code=500, detail="Failed to fetch products from Shopee API")
    # フロントエンド互換: response.item → items に変換（詳細情報も自動取得）
    return await _normalize_product_list(result, shop_id)


# ============================================================
# Livestream Session Endpoints
# ============================================================
@router.post("/session/create")
async def create_session(req: CreateSessionRequest):
    """Shopeeライブセッションを作成"""
    from app.services.shopee_live_service import create_livestream_session
    result = await create_livestream_session(req.title, req.cover_image_url, req.shop_id)
    if result is None:
        raise HTTPException(status_code=500, detail="Failed to create livestream session")
    return result


@router.get("/session/detail")
async def get_session_detail(
    session_id: int = Query(...),
    shop_id: int = Query(default=1542634108),
):
    """ライブセッション詳細を取得（RTMP URL含む）"""
    from app.services.shopee_live_service import get_livestream_session_detail
    result = await get_livestream_session_detail(session_id, shop_id)
    if result is None:
        raise HTTPException(status_code=500, detail="Failed to get session detail")
    return result


@router.post("/session/start")
async def start_session(req: SessionActionRequest):
    """ライブセッションを開始"""
    from app.services.shopee_live_service import start_livestream_session
    result = await start_livestream_session(req.session_id, req.shop_id)
    if result is None:
        raise HTTPException(status_code=500, detail="Failed to start session")
    return result


@router.post("/session/end")
async def end_session(req: SessionActionRequest):
    """ライブセッションを終了"""
    from app.services.shopee_live_service import end_livestream_session
    result = await end_livestream_session(req.session_id, req.shop_id)
    if result is None:
        raise HTTPException(status_code=500, detail="Failed to end session")
    return result


# ============================================================
# Livestream Item Endpoints
# ============================================================
@router.get("/session/items")
async def get_session_items(
    session_id: int = Query(...),
    shop_id: int = Query(default=1542634108),
):
    """ライブセッションに登録された商品一覧を取得"""
    from app.services.shopee_live_service import get_livestream_items
    result = await get_livestream_items(session_id, shop_id)
    if result is None:
        raise HTTPException(status_code=500, detail="Failed to get session items")
    return result


@router.post("/session/show-item")
async def set_show_item(req: SetShowItemRequest):
    """ライブ中に表示する商品を設定"""
    from app.services.shopee_live_service import set_show_item
    result = await set_show_item(req.session_id, req.item_id, req.shop_id)
    if result is None:
        raise HTTPException(status_code=500, detail="Failed to set show item")
    return result


# ============================================================
# Comment Endpoints
# ============================================================
@router.get("/comments")
async def get_comments(
    session_id: int = Query(...),
    shop_id: int = Query(default=1542634108),
):
    """ライブセッションの最新コメントを取得"""
    from app.services.shopee_live_service import get_latest_comments
    result = await get_latest_comments(session_id, shop_id)
    if result is None:
        raise HTTPException(status_code=500, detail="Failed to get comments")
    return result


@router.post("/comments/post")
async def post_comment_endpoint(req: PostCommentRequest):
    """ライブセッションにコメントを投稿"""
    from app.services.shopee_live_service import post_comment
    result = await post_comment(req.session_id, req.content, req.shop_id)
    if result is None:
        raise HTTPException(status_code=500, detail="Failed to post comment")
    return result


# ============================================================
# Metrics Endpoints
# ============================================================
@router.get("/metrics/session")
async def get_metrics(
    session_id: int = Query(...),
    shop_id: int = Query(default=1542634108),
):
    """ライブセッションのメトリクスを取得"""
    from app.services.shopee_live_service import get_session_metrics
    result = await get_session_metrics(session_id, shop_id)
    if result is None:
        raise HTTPException(status_code=500, detail="Failed to get metrics")
    return result


@router.get("/metrics/items")
async def get_item_metrics_endpoint(
    session_id: int = Query(...),
    shop_id: int = Query(default=1542634108),
):
    """ライブセッションの商品別メトリクスを取得"""
    from app.services.shopee_live_service import get_item_metrics
    result = await get_item_metrics(session_id, shop_id)
    if result is None:
        raise HTTPException(status_code=500, detail="Failed to get item metrics")
    return result
