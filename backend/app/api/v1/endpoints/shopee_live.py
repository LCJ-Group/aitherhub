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
    # Shopee APIエラーレスポンスもそのまま返す
    return result


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
