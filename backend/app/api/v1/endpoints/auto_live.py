"""
Auto Live API Endpoints v2

AI自動ライブ配信の制御エンドポイント。
- 商品なしでも開始可能（雑談モード）
- 手動商品追加（画像+テキスト）
- Shopee商品データ取得
- ホストペルソナ設定
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
    shopee_session_id: Optional[int] = None
    language: str = "en"
    style: str = "professional"  # professional, casual, energetic
    product_item_ids: Optional[List[int]] = None
    products_manual: Optional[List[Dict[str, Any]]] = None
    # v2: Allow starting without products (chat-only mode)
    skip_shopee: bool = False  # If true, don't try to fetch from Shopee
    # v2: Host persona
    host_name: str = ""
    host_persona: str = ""


class AddProductRequest(BaseModel):
    session_id: str
    item_name: str
    description: str = ""
    price: str = ""
    brand: str = ""
    image_url: str = ""  # URL of product image
    custom_notes: str = ""  # Free-form notes


class SessionIdRequest(BaseModel):
    session_id: str


# ============================================================
# Endpoints
# ============================================================
@router.post("/start")
async def start_auto_live(req: StartAutoLiveRequest):
    """
    自動ライブ配信を開始 (v2)
    
    Modes:
    1. With Shopee products: Fetches from Shopee API
    2. With manual products: Uses products_manual data
    3. Chat-only: No products, just conversation (skip_shopee=true)
    """
    from app.services.auto_live_engine import start_auto_live as _start

    products = []

    # 手動で商品データが渡された場合はそれを使用
    if req.products_manual:
        products = req.products_manual
    # Shopeeから商品データを取得（skip_shopeeでなければ）
    elif not req.skip_shopee:
        try:
            from app.services.shopee_live_service import get_product_list, get_product_detail
            if req.product_item_ids:
                detail = await get_product_detail(req.product_item_ids, req.shop_id)
                if detail:
                    products = detail
            else:
                result = await get_product_list(req.shop_id)
                if result and result.get("items"):
                    products = result["items"]
        except Exception as e:
            logger.warning(f"[AutoLive] Failed to fetch Shopee products: {e}")
            # Don't fail — continue in chat-only mode if no products

    # v2: Allow starting without products (chat-only mode)
    # Old behavior: raise error if no products
    # New behavior: start in chat-only mode
    if not products and not req.skip_shopee and not req.products_manual:
        logger.info("[AutoLive] No products available, starting in chat-only mode")

    result = await _start(
        session_id=req.session_id,
        products=products,
        language=req.language,
        style=req.style,
        shopee_session_id=req.shopee_session_id,
        host_name=req.host_name,
        host_persona=req.host_persona,
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


@router.post("/add-product")
async def add_product(req: AddProductRequest):
    """
    実行中のセッションに商品を追加（手動）
    
    画像URLとテキスト説明で商品を追加。
    Shopee不要で使える。
    """
    from app.services.auto_live_engine import add_product_to_session

    result = await add_product_to_session(
        session_id=req.session_id,
        product_data={
            "item_name": req.item_name,
            "description": req.description,
            "price": req.price,
            "brand": req.brand,
            "image_url": req.image_url,
            "custom_notes": req.custom_notes,
        },
    )

    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Session not found. Start auto live first.")

    return result


@router.get("/status/{session_id}")
async def get_status(session_id: str):
    """自動ライブ配信のステータスを取得"""
    from app.services.auto_live_engine import get_auto_live_status
    return get_auto_live_status(session_id)


class MarkConsumedRequest(BaseModel):
    session_id: str
    count: int = 1


@router.post("/mark-consumed")
async def mark_consumed_endpoint(req: MarkConsumedRequest):
    """
    フロントエンドがspeak_queueからアイテムを消費した時に呼ぶ。
    これによりバックエンドが「未消費アイテム数」を正確に追跡でき、
    新しいテキスト生成を継続できる。
    """
    from app.services.auto_live_engine import mark_consumed
    mark_consumed(req.session_id, req.count)
    return {"success": True, "consumed": req.count}


@router.get("/sessions")
async def list_sessions():
    """アクティブな自動ライブセッション一覧"""
    from app.services.auto_live_engine import list_active_sessions
    return list_active_sessions()
