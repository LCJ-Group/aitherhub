"""
Chrome Extension Commerce Reaction API Endpoints

New event-sourcing API for the 2-screen (LIVE + Dashboard) Chrome extension MVP.
Receives raw events, product snapshots, and trend snapshots from the extension,
stores them in the Commerce Reaction DB for later moment detection.

Endpoints:
  POST /api/v1/ext/session/start          - Create ext_session
  PATCH /api/v1/ext/session/{id}/bind      - Bind tab (live or dashboard)
  POST /api/v1/ext/session/{id}/end        - End session
  POST /api/v1/ext/events                  - Batch insert raw_events
  POST /api/v1/ext/snapshots/products      - Batch insert product_snapshots
  POST /api/v1/ext/snapshots/trends        - Batch insert trend_snapshots
  POST /api/v1/ext/marker                  - Add manual marker event
  GET  /api/v1/ext/session/{id}/summary    - Session summary
  GET  /api/v1/ext/health                  - Health check
"""

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func as sa_func, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.core.db import get_db
from app.models.orm.extension_events import (
    ExtSession,
    RawEvent,
    ProductSnapshot,
    TrendSnapshot,
    ExtSalesMoment,
    MomentEventLink,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ext", tags=["Extension Events"])


# ═══════════════════════════════════════════════════════════════════
# Request / Response Schemas
# ═══════════════════════════════════════════════════════════════════

# ── Session ──

class SessionStartRequest(BaseModel):
    platform: str = "tiktok_live"
    creator_id: Optional[str] = None
    live_url: Optional[str] = None
    dashboard_url: Optional[str] = None
    live_tab_id: Optional[int] = None
    dashboard_tab_id: Optional[int] = None
    live_title: Optional[str] = None
    room_id: Optional[str] = None


class SessionStartResponse(BaseModel):
    session_id: str
    status: str
    message: str


class SessionBindRequest(BaseModel):
    tab_type: str  # "live" or "dashboard"
    tab_id: int
    url: Optional[str] = None


class SessionEndRequest(BaseModel):
    pass  # session_id comes from path


# ── Events ──

class RawEventItem(BaseModel):
    """Single raw event from the extension."""
    event_type: str
    source_type: str = "live_dom"
    captured_at: str  # ISO 8601
    video_sec: Optional[int] = None
    product_id: Optional[str] = None
    numeric_value: Optional[float] = None
    text_value: Optional[str] = None
    payload: Optional[dict] = None
    confidence_score: Optional[float] = None


class EventBatchRequest(BaseModel):
    session_id: str
    events: List[RawEventItem]


class EventBatchResponse(BaseModel):
    inserted: int
    session_id: str


# ── Product Snapshots ──

class ProductSnapshotItem(BaseModel):
    product_id: str
    product_name: Optional[str] = None
    gmv: float = 0
    sales_count: int = 0
    add_to_cart_count: int = 0
    click_count: int = 0
    impression_count: int = 0
    ctr: Optional[float] = None
    rank_on_table: Optional[int] = None


class ProductSnapshotBatchRequest(BaseModel):
    session_id: str
    captured_at: str  # ISO 8601
    products: List[ProductSnapshotItem]
    snapshot_seq: Optional[int] = None


# ── Trend Snapshots ──

class TrendSnapshotItem(BaseModel):
    metric_type: str  # gmv, impressions, pin_event_count
    bucket_start_at: str  # ISO 8601
    bucket_end_at: str  # ISO 8601
    metric_value: float = 0


class TrendSnapshotBatchRequest(BaseModel):
    session_id: str
    captured_at: str  # ISO 8601
    trends: List[TrendSnapshotItem]


# ── Manual Marker ──

class ManualMarkerRequest(BaseModel):
    session_id: str
    label: Optional[str] = None
    video_sec: Optional[int] = None


# ═══════════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════════

def _parse_iso(s: str) -> datetime:
    """Parse ISO 8601 string to timezone-aware datetime."""
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.now(timezone.utc)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def _get_session_or_404(
    db: AsyncSession, session_id: str
) -> ExtSession:
    """Fetch ext_session by ID or raise 404."""
    result = await db.execute(
        select(ExtSession).where(ExtSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return session


# ═══════════════════════════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════════════════════════

# ── Health ──

@router.get("/health")
async def ext_health():
    """Health check for extension event API."""
    return {
        "status": "ok",
        "service": "aitherhub-ext-events",
        "timestamp": _now_utc().isoformat(),
    }


# ── Session Management ──

@router.post("/session/start", response_model=SessionStartResponse)
async def start_session(
    request: SessionStartRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new extension session for 2-screen tracking.
    Called when the extension popup starts tracking.
    """
    session_id = str(uuid.uuid4())

    # Determine initial status based on what tabs are provided
    if request.live_tab_id and request.dashboard_tab_id:
        status = "active"
    elif request.live_tab_id:
        status = "waiting_dashboard"
    elif request.dashboard_tab_id:
        status = "waiting_live"
    else:
        status = "waiting_live"

    session = ExtSession(
        id=session_id,
        platform=request.platform,
        creator_id=request.creator_id,
        live_url=request.live_url,
        dashboard_url=request.dashboard_url,
        live_tab_id=request.live_tab_id,
        dashboard_tab_id=request.dashboard_tab_id,
        live_title=request.live_title,
        room_id=request.room_id,
        started_at=_now_utc(),
        status=status,
    )

    db.add(session)
    await db.commit()

    logger.info(
        f"Extension session started: {session_id} "
        f"(platform={request.platform}, creator={request.creator_id}, status={status})"
    )

    return SessionStartResponse(
        session_id=session_id,
        status=status,
        message=f"Session created ({status})",
    )


@router.patch("/session/{session_id}/bind")
async def bind_tab(
    session_id: str,
    request: SessionBindRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Bind a live or dashboard tab to the session.
    When both tabs are bound, session status becomes 'active'.
    """
    session = await _get_session_or_404(db, session_id)

    if request.tab_type == "live":
        session.live_tab_id = request.tab_id
        if request.url:
            session.live_url = request.url
    elif request.tab_type == "dashboard":
        session.dashboard_tab_id = request.tab_id
        if request.url:
            session.dashboard_url = request.url
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid tab_type: {request.tab_type}. Must be 'live' or 'dashboard'.",
        )

    # Update status
    if session.live_tab_id and session.dashboard_tab_id:
        session.status = "active"
    elif session.live_tab_id:
        session.status = "waiting_dashboard"
    elif session.dashboard_tab_id:
        session.status = "waiting_live"

    session.updated_at = _now_utc()
    await db.commit()

    logger.info(
        f"Tab bound: session={session_id}, type={request.tab_type}, "
        f"tab_id={request.tab_id}, status={session.status}"
    )

    return {
        "status": session.status,
        "live_tab_id": session.live_tab_id,
        "dashboard_tab_id": session.dashboard_tab_id,
    }


@router.post("/session/{session_id}/end")
async def end_session(
    session_id: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """End an extension session."""
    session = await _get_session_or_404(db, session_id)

    session.status = "ended"
    session.ended_at = _now_utc()
    session.updated_at = _now_utc()
    await db.commit()

    # Get summary counts
    event_count = await db.scalar(
        select(sa_func.count(RawEvent.id)).where(RawEvent.session_id == session_id)
    )
    snapshot_count = await db.scalar(
        select(sa_func.count(ProductSnapshot.id)).where(
            ProductSnapshot.session_id == session_id
        )
    )

    logger.info(
        f"Extension session ended: {session_id} "
        f"(events={event_count}, snapshots={snapshot_count})"
    )

    # Auto-detect moments on session end
    moments_detected = 0
    try:
        from app.services.moment_engine import MomentEngine
        engine = MomentEngine(db)
        moments = await engine.detect_moments(session_id, force=True)
        moments_detected = len(moments)
        logger.info(f"Auto-detected {moments_detected} moments for session {session_id}")
    except Exception as e:
        logger.error(f"Moment detection failed for session {session_id}: {e}")

    return {
        "status": "ended",
        "session_id": session_id,
        "total_events": event_count or 0,
        "total_product_snapshots": snapshot_count or 0,
        "moments_detected": moments_detected,
    }


# ── Raw Events ──

@router.post("/events", response_model=EventBatchResponse)
async def receive_events(
    request: EventBatchRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Batch insert raw events from the extension.
    Called every 3-5 seconds with buffered events.
    This is the core data ingestion endpoint.
    """
    if not request.events:
        return EventBatchResponse(inserted=0, session_id=request.session_id)

    # Verify session exists
    await _get_session_or_404(db, request.session_id)

    now = _now_utc()
    inserted = 0

    for evt in request.events:
        raw_event = RawEvent(
            id=str(uuid.uuid4()),
            session_id=request.session_id,
            source_type=evt.source_type,
            event_type=evt.event_type,
            captured_at_client=_parse_iso(evt.captured_at),
            captured_at_server=now,
            video_sec=evt.video_sec,
            product_id=evt.product_id,
            numeric_value=Decimal(str(evt.numeric_value)) if evt.numeric_value is not None else None,
            text_value=evt.text_value,
            payload_json=evt.payload,
            confidence_score=Decimal(str(evt.confidence_score)) if evt.confidence_score is not None else None,
        )
        db.add(raw_event)
        inserted += 1

    await db.commit()

    logger.debug(
        f"Events received: session={request.session_id}, count={inserted}"
    )

    return EventBatchResponse(inserted=inserted, session_id=request.session_id)


# ── Product Snapshots ──

@router.post("/snapshots/products")
async def receive_product_snapshots(
    request: ProductSnapshotBatchRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Batch insert product snapshots from the dashboard product table.
    Called every 30-60 seconds when the dashboard is visible.
    Each call captures the full product table state for diff-based analysis.
    """
    if not request.products:
        return {"inserted": 0, "session_id": request.session_id}

    await _get_session_or_404(db, request.session_id)

    captured_at = _parse_iso(request.captured_at)
    inserted = 0

    for idx, prod in enumerate(request.products):
        snapshot = ProductSnapshot(
            id=str(uuid.uuid4()),
            session_id=request.session_id,
            captured_at=captured_at,
            product_id=prod.product_id,
            product_name=prod.product_name,
            gmv=Decimal(str(prod.gmv)),
            sales_count=prod.sales_count,
            add_to_cart_count=prod.add_to_cart_count,
            click_count=prod.click_count,
            impression_count=prod.impression_count,
            ctr=Decimal(str(prod.ctr)) if prod.ctr is not None else None,
            snapshot_seq=request.snapshot_seq,
            rank_on_table=prod.rank_on_table or (idx + 1),
        )
        db.add(snapshot)
        inserted += 1

    await db.commit()

    # Also store as a raw event for the event log
    summary_event = RawEvent(
        id=str(uuid.uuid4()),
        session_id=request.session_id,
        source_type="dashboard_dom",
        event_type="product_metrics_snapshot",
        captured_at_client=captured_at,
        captured_at_server=_now_utc(),
        numeric_value=Decimal(str(len(request.products))),
        payload_json={
            "product_count": len(request.products),
            "snapshot_seq": request.snapshot_seq,
            "total_gmv": sum(p.gmv for p in request.products),
            "total_sales": sum(p.sales_count for p in request.products),
        },
    )
    db.add(summary_event)
    await db.commit()

    logger.debug(
        f"Product snapshots received: session={request.session_id}, "
        f"products={inserted}, seq={request.snapshot_seq}"
    )

    return {
        "inserted": inserted,
        "session_id": request.session_id,
        "snapshot_seq": request.snapshot_seq,
    }


# ── Trend Snapshots ──

@router.post("/snapshots/trends")
async def receive_trend_snapshots(
    request: TrendSnapshotBatchRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Batch insert trend snapshots from the dashboard trend graphs.
    5-minute bucket time-series data (gmv, impressions, pin_event_count).
    """
    if not request.trends:
        return {"inserted": 0, "session_id": request.session_id}

    await _get_session_or_404(db, request.session_id)

    captured_at = _parse_iso(request.captured_at)
    inserted = 0

    for trend in request.trends:
        snapshot = TrendSnapshot(
            id=str(uuid.uuid4()),
            session_id=request.session_id,
            captured_at=captured_at,
            metric_type=trend.metric_type,
            bucket_start_at=_parse_iso(trend.bucket_start_at),
            bucket_end_at=_parse_iso(trend.bucket_end_at),
            metric_value=Decimal(str(trend.metric_value)),
        )
        db.add(snapshot)
        inserted += 1

    await db.commit()

    logger.debug(
        f"Trend snapshots received: session={request.session_id}, count={inserted}"
    )

    return {"inserted": inserted, "session_id": request.session_id}


# ── Manual Marker ──

@router.post("/marker")
async def add_manual_marker(
    request: ManualMarkerRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Add a manual marker event (operator presses 'Mark' button).
    Very useful for training data.
    """
    await _get_session_or_404(db, request.session_id)

    now = _now_utc()
    event = RawEvent(
        id=str(uuid.uuid4()),
        session_id=request.session_id,
        source_type="manual",
        event_type="manual_marker_added",
        captured_at_client=now,
        captured_at_server=now,
        video_sec=request.video_sec,
        text_value=request.label or "manual_mark",
        payload_json={"label": request.label, "video_sec": request.video_sec},
        confidence_score=Decimal("1.0"),
    )
    db.add(event)
    await db.commit()

    logger.info(
        f"Manual marker added: session={request.session_id}, "
        f"label={request.label}, video_sec={request.video_sec}"
    )

    return {"status": "ok", "event_id": event.id}


# ── Moment Detection ──

@router.post("/session/{session_id}/detect-moments")
async def detect_moments(
    session_id: str,
    force: bool = Query(False, description="If true, re-detect moments (clear existing)"),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Run the rule-based moment detection engine for a session.
    Can be called:
      - Automatically when session ends
      - Manually from admin/debug UI
      - Periodically during a long session
    """
    from app.services.moment_engine import MomentEngine

    await _get_session_or_404(db, session_id)

    engine = MomentEngine(db)
    moments = await engine.detect_moments(session_id, force=force)

    logger.info(
        f"Moment detection complete: session={session_id}, "
        f"moments_found={len(moments)}, force={force}"
    )

    return {
        "session_id": session_id,
        "moments_detected": len(moments),
        "moments": moments,
    }


# ── Session Summary ──

@router.get("/session/{session_id}/summary")
async def get_session_summary(
    session_id: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get a comprehensive summary for a session.
    Used for the end-of-session summary UI.
    """
    session = await _get_session_or_404(db, session_id)

    # Count events by type
    event_counts_result = await db.execute(
        select(RawEvent.event_type, sa_func.count(RawEvent.id))
        .where(RawEvent.session_id == session_id)
        .group_by(RawEvent.event_type)
    )
    event_counts = {row[0]: row[1] for row in event_counts_result.all()}

    # Total events
    total_events = sum(event_counts.values())

    # Latest product snapshot (most recent full table)
    latest_products_result = await db.execute(
        select(ProductSnapshot)
        .where(ProductSnapshot.session_id == session_id)
        .order_by(ProductSnapshot.captured_at.desc())
        .limit(50)
    )
    latest_products = latest_products_result.scalars().all()

    # Deduplicate to latest per product_id
    seen_products = {}
    for p in latest_products:
        if p.product_id not in seen_products:
            seen_products[p.product_id] = p

    # Top products by different metrics
    products_list = list(seen_products.values())
    top_by_clicks = sorted(products_list, key=lambda p: p.click_count or 0, reverse=True)[:5]
    top_by_cart = sorted(products_list, key=lambda p: p.add_to_cart_count or 0, reverse=True)[:5]
    top_by_sales = sorted(products_list, key=lambda p: p.sales_count or 0, reverse=True)[:5]
    top_by_gmv = sorted(products_list, key=lambda p: float(p.gmv or 0), reverse=True)[:5]

    # Sales moments
    moments_result = await db.execute(
        select(ExtSalesMoment)
        .where(ExtSalesMoment.session_id == session_id)
        .order_by(ExtSalesMoment.strength_score.desc())
        .limit(10)
    )
    moments = moments_result.scalars().all()

    # Total GMV and sales from latest products
    total_gmv = sum(float(p.gmv or 0) for p in products_list)
    total_sales = sum(p.sales_count or 0 for p in products_list)

    def _product_summary(p):
        return {
            "product_id": p.product_id,
            "product_name": p.product_name,
            "gmv": float(p.gmv or 0),
            "sales_count": p.sales_count or 0,
            "add_to_cart_count": p.add_to_cart_count or 0,
            "click_count": p.click_count or 0,
            "impression_count": p.impression_count or 0,
            "ctr": float(p.ctr or 0),
        }

    def _moment_summary(m):
        return {
            "id": m.id,
            "moment_start_at": m.moment_start_at.isoformat() if m.moment_start_at else None,
            "moment_end_at": m.moment_end_at.isoformat() if m.moment_end_at else None,
            "primary_product_id": m.primary_product_id,
            "click_delta": m.click_delta,
            "cart_delta": m.cart_delta,
            "sales_delta": m.sales_delta,
            "gmv_delta": float(m.gmv_delta or 0),
            "strength_score": float(m.strength_score or 0),
            "moment_type": m.moment_type,
            "evidence_level": m.evidence_level,
        }

    return {
        "session": {
            "id": session.id,
            "platform": session.platform,
            "creator_id": session.creator_id,
            "live_title": session.live_title,
            "status": session.status,
            "started_at": session.started_at.isoformat() if session.started_at else None,
            "ended_at": session.ended_at.isoformat() if session.ended_at else None,
        },
        "totals": {
            "gmv": total_gmv,
            "sales": total_sales,
            "events": total_events,
            "products_tracked": len(products_list),
            "moments_detected": len(moments),
        },
        "event_counts": event_counts,
        "top_products": {
            "by_clicks": [_product_summary(p) for p in top_by_clicks],
            "by_cart": [_product_summary(p) for p in top_by_cart],
            "by_sales": [_product_summary(p) for p in top_by_sales],
            "by_gmv": [_product_summary(p) for p in top_by_gmv],
        },
        "strongest_moments": [_moment_summary(m) for m in moments[:5]],
    }
