"""
Auto Live API Endpoints

AI自動ライブ配信の制御エンドポイント。
自動スピーチの開始/停止/一時停止/再開、ステータス取得を提供。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auto-live", tags=["auto-live"])


# ============================================================
# Request Models
# ============================================================
class StartAutoLiveRequest(BaseModel):
    session_id: str  # AitherHub LiveAvatar session ID
    shop_id: int = 1542634108
    shopee_session_id: Optional[int] = None  # Shopee livestream session ID (for comments)
    language: str = "en"
    style: str = "professional"  # professional, casual, energetic
    product_item_ids: Optional[List[int]] = None  # 特定の商品のみ（指定しない場合は全商品）
    products_manual: Optional[List[Dict[str, Any]]] = None  # 手動で商品データを渡す場合


class SessionIdRequest(BaseModel):
    session_id: str


# ============================================================
# Endpoints
# ============================================================
@router.post("/start")
async def start_auto_live(req: StartAutoLiveRequest):
    """
    自動ライブ配信を開始
    
    1. Shopee商品データを取得（または手動データを使用）
    2. AIセールストーク自動生成ループを開始
    3. コメント監視ループを開始（Shopeeセッションがある場合）
    """
    from app.services.auto_live_engine import start_auto_live as _start
    from app.services.shopee_live_service import get_product_list, get_product_detail

    products = []

    # 手動で商品データが渡された場合はそれを使用
    if req.products_manual:
        products = req.products_manual
    # Shopeeから商品データを取得
    elif req.product_item_ids:
        detail = await get_product_detail(req.product_item_ids, req.shop_id)
        if detail:
            products = detail
        else:
            raise HTTPException(status_code=500, detail="Failed to fetch product details from Shopee")
    else:
        # 全商品を取得
        result = await get_product_list(req.shop_id)
        if result and result.get("items"):
            products = result["items"]
        else:
            raise HTTPException(status_code=500, detail="Failed to fetch products from Shopee. Check Sales Dash bridge connection.")

    if not products:
        raise HTTPException(status_code=400, detail="No products available for auto live")

    result = await _start(
        session_id=req.session_id,
        products=products,
        language=req.language,
        style=req.style,
        shopee_session_id=req.shopee_session_id,
    )

    return result


@router.post("/stop")
async def stop_auto_live(req: SessionIdRequest):
    """自動ライブ配信を停止"""
    from app.services.auto_live_engine import stop_auto_live as _stop
    return await _stop(req.session_id)


@router.post("/pause")
async def pause_auto_live(req: SessionIdRequest):
    """自動ライブ配信を一時停止"""
    from app.services.auto_live_engine import pause_auto_live as _pause
    return await _pause(req.session_id)


@router.post("/resume")
async def resume_auto_live(req: SessionIdRequest):
    """自動ライブ配信を再開"""
    from app.services.auto_live_engine import resume_auto_live as _resume
    return await _resume(req.session_id)


@router.get("/status/{session_id}")
async def get_status(session_id: str):
    """自動ライブ配信のステータスを取得"""
    from app.services.auto_live_engine import get_auto_live_status
    return get_auto_live_status(session_id)


@router.get("/sessions")
async def list_sessions():
    """アクティブな自動ライブセッション一覧"""
    from app.services.auto_live_engine import list_active_sessions
    return list_active_sessions()
