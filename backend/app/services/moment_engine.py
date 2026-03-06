"""
Commerce Reaction Moment Engine (Rule-Based MVP)

Detects sales moments from product_snapshots diff and raw_events.
This is the core intelligence layer of the extension MVP.

Detection Flow:
  1. Fetch consecutive product_snapshots for a session
  2. Compute per-product deltas (click, cart, sales, gmv)
  3. Compute baselines (rolling average)
  4. Apply spike rules (click_spike, cart_spike, sale_spike, gmv_spike)
  5. Check for engagement signals (comment_spike, viewer rise)
  6. Check for trigger events (product_pinned, product_switched) within ±150s
  7. Classify moment type (trigger / conversion / strong)
  8. Compute strength_score
  9. Store ext_sales_moments and moment_event_links

Spike Rules (from spec section 10.2):
  - click_spike:  delta >= 20  OR  delta >= baseline * 2
  - cart_spike:   delta >= 5   OR  delta >= baseline * 2
  - sale_spike:   delta >= 3   OR  delta >= baseline * 2
  - gmv_spike:    delta >= baseline * 2

Strong Moment:
  (click_spike OR cart_spike) AND (sale_spike OR gmv_spike)
  AND product_pinned/switched/comment_spike within 150s

Strength Score:
  0.30 * norm(click) + 0.20 * norm(cart) + 0.25 * norm(sales)
  + 0.15 * norm(gmv) + 0.05 * norm(comment) + 0.05 * norm(viewer)
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select, func as sa_func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orm.extension_events import (
    ExtSession,
    RawEvent,
    ProductSnapshot,
    ExtSalesMoment,
    MomentEventLink,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════

# Spike thresholds (absolute minimums)
CLICK_SPIKE_ABS = 20
CART_SPIKE_ABS = 5
SALE_SPIKE_ABS = 3

# Spike threshold (relative to baseline)
SPIKE_RATIO = 2.0

# Time window for trigger event proximity (seconds)
TRIGGER_WINDOW_SEC = 150

# Moment time window (seconds) - how wide a moment is
MOMENT_WINDOW_SEC = 300  # 5 minutes (aligned with dashboard 5-min buckets)

# Strength score weights
W_CLICK = Decimal("0.30")
W_CART = Decimal("0.20")
W_SALES = Decimal("0.25")
W_GMV = Decimal("0.15")
W_COMMENT = Decimal("0.05")
W_VIEWER = Decimal("0.05")

# Minimum baseline count for reliable comparison
MIN_BASELINE_SNAPSHOTS = 3

# Event types that count as trigger events
TRIGGER_EVENT_TYPES = {
    "product_pinned",
    "product_switched",
    "comment_spike",
    "manual_marker_added",
    "purchase_notice_detected",
}


# ═══════════════════════════════════════════════════════════════════
# Data Structures
# ═══════════════════════════════════════════════════════════════════

class ProductDelta:
    """Delta between two consecutive product snapshots."""

    def __init__(
        self,
        product_id: str,
        product_name: Optional[str],
        captured_at: datetime,
        prev_captured_at: Optional[datetime],
        click_delta: int,
        cart_delta: int,
        sales_delta: int,
        gmv_delta: float,
    ):
        self.product_id = product_id
        self.product_name = product_name
        self.captured_at = captured_at
        self.prev_captured_at = prev_captured_at
        self.click_delta = click_delta
        self.cart_delta = cart_delta
        self.sales_delta = sales_delta
        self.gmv_delta = gmv_delta


class SpikeResult:
    """Result of spike detection for a single delta."""

    def __init__(self):
        self.click_spike = False
        self.cart_spike = False
        self.sale_spike = False
        self.gmv_spike = False
        self.is_trigger = False
        self.is_conversion = False
        self.is_strong = False
        self.has_trigger_event = False


class MomentCandidate:
    """A candidate moment before final scoring."""

    def __init__(
        self,
        session_id: str,
        moment_start: datetime,
        moment_end: datetime,
        primary_product_id: Optional[str],
        click_delta: int,
        cart_delta: int,
        sales_delta: int,
        gmv_delta: float,
        comment_delta: int,
        viewer_delta: int,
        spike: SpikeResult,
        trigger_events: List[dict],
    ):
        self.session_id = session_id
        self.moment_start = moment_start
        self.moment_end = moment_end
        self.primary_product_id = primary_product_id
        self.click_delta = click_delta
        self.cart_delta = cart_delta
        self.sales_delta = sales_delta
        self.gmv_delta = gmv_delta
        self.comment_delta = comment_delta
        self.viewer_delta = viewer_delta
        self.spike = spike
        self.trigger_events = trigger_events


# ═══════════════════════════════════════════════════════════════════
# Core Engine
# ═══════════════════════════════════════════════════════════════════

class MomentEngine:
    """
    Rule-based Commerce Reaction Moment detection engine.
    Designed to be called after a session ends, or periodically during a session.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def detect_moments(
        self, session_id: str, force: bool = False
    ) -> List[dict]:
        """
        Main entry point: detect all moments for a session.

        Args:
            session_id: The ext_session ID
            force: If True, delete existing moments and re-detect

        Returns:
            List of created moment dicts
        """
        logger.info(f"[MomentEngine] Starting detection for session {session_id}")

        # 0. Optionally clear existing moments
        if force:
            await self._clear_existing_moments(session_id)

        # 1. Fetch product snapshots and compute deltas
        deltas = await self._compute_product_deltas(session_id)
        if not deltas:
            logger.info(f"[MomentEngine] No product deltas found for session {session_id}")
            return []

        logger.info(f"[MomentEngine] Computed {len(deltas)} product deltas")

        # 2. Compute baselines (rolling averages per product)
        baselines = self._compute_baselines(deltas)

        # 3. Detect spikes
        spike_deltas = self._detect_spikes(deltas, baselines)

        # 4. Group spikes into moment windows
        candidates = await self._build_moment_candidates(session_id, spike_deltas)

        if not candidates:
            logger.info(f"[MomentEngine] No moment candidates found")
            return []

        logger.info(f"[MomentEngine] Found {len(candidates)} moment candidates")

        # 5. Score and classify moments
        moments = self._score_moments(candidates)

        # 6. Store moments and event links
        stored = await self._store_moments(moments)

        logger.info(
            f"[MomentEngine] Stored {len(stored)} moments for session {session_id}"
        )
        return stored

    # ─── Step 1: Compute Product Deltas ───────────────────────────

    async def _compute_product_deltas(
        self, session_id: str
    ) -> List[ProductDelta]:
        """
        Fetch consecutive product snapshots and compute deltas.
        Returns deltas sorted by captured_at.
        """
        result = await self.db.execute(
            select(ProductSnapshot)
            .where(ProductSnapshot.session_id == session_id)
            .order_by(ProductSnapshot.product_id, ProductSnapshot.captured_at)
        )
        snapshots = result.scalars().all()

        if not snapshots:
            return []

        # Group by product_id
        by_product: Dict[str, List[ProductSnapshot]] = {}
        for s in snapshots:
            by_product.setdefault(s.product_id, []).append(s)

        deltas = []
        for product_id, product_snapshots in by_product.items():
            for i in range(1, len(product_snapshots)):
                prev = product_snapshots[i - 1]
                curr = product_snapshots[i]

                click_d = (curr.click_count or 0) - (prev.click_count or 0)
                cart_d = (curr.add_to_cart_count or 0) - (prev.add_to_cart_count or 0)
                sales_d = (curr.sales_count or 0) - (prev.sales_count or 0)
                gmv_d = float(curr.gmv or 0) - float(prev.gmv or 0)

                # Skip negative deltas (data reset or error)
                if click_d < 0 or cart_d < 0 or sales_d < 0 or gmv_d < 0:
                    continue

                # Skip zero deltas (no change)
                if click_d == 0 and cart_d == 0 and sales_d == 0 and gmv_d == 0:
                    continue

                deltas.append(ProductDelta(
                    product_id=product_id,
                    product_name=curr.product_name,
                    captured_at=curr.captured_at,
                    prev_captured_at=prev.captured_at,
                    click_delta=click_d,
                    cart_delta=cart_d,
                    sales_delta=sales_d,
                    gmv_delta=gmv_d,
                ))

        # Sort by time
        deltas.sort(key=lambda d: d.captured_at)
        return deltas

    # ─── Step 2: Compute Baselines ────────────────────────────────

    def _compute_baselines(
        self, deltas: List[ProductDelta]
    ) -> Dict[str, Dict[str, float]]:
        """
        Compute rolling average baselines per product.
        Returns: { product_id: { 'click': avg, 'cart': avg, 'sales': avg, 'gmv': avg } }
        """
        by_product: Dict[str, List[ProductDelta]] = {}
        for d in deltas:
            by_product.setdefault(d.product_id, []).append(d)

        baselines = {}
        for product_id, product_deltas in by_product.items():
            n = len(product_deltas)
            if n < MIN_BASELINE_SNAPSHOTS:
                # Not enough data for reliable baseline, use absolute thresholds only
                baselines[product_id] = {
                    "click": 0, "cart": 0, "sales": 0, "gmv": 0
                }
            else:
                baselines[product_id] = {
                    "click": sum(d.click_delta for d in product_deltas) / n,
                    "cart": sum(d.cart_delta for d in product_deltas) / n,
                    "sales": sum(d.sales_delta for d in product_deltas) / n,
                    "gmv": sum(d.gmv_delta for d in product_deltas) / n,
                }

        return baselines

    # ─── Step 3: Detect Spikes ────────────────────────────────────

    def _detect_spikes(
        self,
        deltas: List[ProductDelta],
        baselines: Dict[str, Dict[str, float]],
    ) -> List[Tuple[ProductDelta, SpikeResult]]:
        """
        Apply spike rules to each delta.
        Returns list of (delta, spike_result) tuples where at least one spike is True.
        """
        results = []

        for delta in deltas:
            baseline = baselines.get(delta.product_id, {})
            spike = SpikeResult()

            # Click spike
            click_base = baseline.get("click", 0)
            if delta.click_delta >= CLICK_SPIKE_ABS or (
                click_base > 0 and delta.click_delta >= click_base * SPIKE_RATIO
            ):
                spike.click_spike = True

            # Cart spike
            cart_base = baseline.get("cart", 0)
            if delta.cart_delta >= CART_SPIKE_ABS or (
                cart_base > 0 and delta.cart_delta >= cart_base * SPIKE_RATIO
            ):
                spike.cart_spike = True

            # Sale spike
            sales_base = baseline.get("sales", 0)
            if delta.sales_delta >= SALE_SPIKE_ABS or (
                sales_base > 0 and delta.sales_delta >= sales_base * SPIKE_RATIO
            ):
                spike.sale_spike = True

            # GMV spike
            gmv_base = baseline.get("gmv", 0)
            if gmv_base > 0 and delta.gmv_delta >= gmv_base * SPIKE_RATIO:
                spike.gmv_spike = True

            # Classify
            spike.is_trigger = spike.click_spike or spike.cart_spike
            spike.is_conversion = spike.sale_spike or spike.gmv_spike

            # At least one spike detected
            if spike.is_trigger or spike.is_conversion:
                results.append((delta, spike))

        return results

    # ─── Step 4: Build Moment Candidates ──────────────────────────

    async def _build_moment_candidates(
        self,
        session_id: str,
        spike_deltas: List[Tuple[ProductDelta, SpikeResult]],
    ) -> List[MomentCandidate]:
        """
        Group spikes into moment windows and check for trigger events.
        Also fetch engagement deltas (comment, viewer) for the window.
        """
        if not spike_deltas:
            return []

        candidates = []

        # Merge overlapping spike windows
        # Sort by time
        spike_deltas.sort(key=lambda x: x[0].captured_at)

        # Group into windows
        windows = []
        current_window = [spike_deltas[0]]

        for i in range(1, len(spike_deltas)):
            delta, spike = spike_deltas[i]
            prev_delta, _ = current_window[-1]

            # If within MOMENT_WINDOW_SEC of the previous spike, merge
            time_diff = (delta.captured_at - prev_delta.captured_at).total_seconds()
            if time_diff <= MOMENT_WINDOW_SEC:
                current_window.append(spike_deltas[i])
            else:
                windows.append(current_window)
                current_window = [spike_deltas[i]]

        windows.append(current_window)

        # Process each window
        for window in windows:
            # Determine window time range
            start_time = min(d.captured_at for d, _ in window)
            end_time = max(d.captured_at for d, _ in window)

            # Expand to at least MOMENT_WINDOW_SEC
            if (end_time - start_time).total_seconds() < MOMENT_WINDOW_SEC:
                center = start_time + (end_time - start_time) / 2
                half_window = timedelta(seconds=MOMENT_WINDOW_SEC / 2)
                start_time = center - half_window
                end_time = center + half_window

            # Aggregate deltas across all products in window
            total_click = sum(d.click_delta for d, _ in window)
            total_cart = sum(d.cart_delta for d, _ in window)
            total_sales = sum(d.sales_delta for d, _ in window)
            total_gmv = sum(d.gmv_delta for d, _ in window)

            # Merge spike flags
            merged_spike = SpikeResult()
            for _, spike in window:
                merged_spike.click_spike = merged_spike.click_spike or spike.click_spike
                merged_spike.cart_spike = merged_spike.cart_spike or spike.cart_spike
                merged_spike.sale_spike = merged_spike.sale_spike or spike.sale_spike
                merged_spike.gmv_spike = merged_spike.gmv_spike or spike.gmv_spike

            merged_spike.is_trigger = merged_spike.click_spike or merged_spike.cart_spike
            merged_spike.is_conversion = merged_spike.sale_spike or merged_spike.gmv_spike

            # Find primary product (highest combined delta)
            product_scores = {}
            for d, _ in window:
                score = product_scores.get(d.product_id, 0)
                score += d.click_delta + d.cart_delta * 3 + d.sales_delta * 5 + d.gmv_delta * 0.01
                product_scores[d.product_id] = score

            primary_product_id = max(product_scores, key=product_scores.get) if product_scores else None

            # Fetch engagement signals (comments and viewers) in the window
            comment_delta, viewer_delta = await self._get_engagement_deltas(
                session_id, start_time, end_time
            )

            # Check for trigger events within ±TRIGGER_WINDOW_SEC
            trigger_events = await self._get_trigger_events(
                session_id,
                start_time - timedelta(seconds=TRIGGER_WINDOW_SEC),
                end_time + timedelta(seconds=TRIGGER_WINDOW_SEC),
            )

            merged_spike.has_trigger_event = len(trigger_events) > 0

            # Strong moment check
            merged_spike.is_strong = (
                merged_spike.is_trigger
                and merged_spike.is_conversion
                and merged_spike.has_trigger_event
            )

            candidates.append(MomentCandidate(
                session_id=session_id,
                moment_start=start_time,
                moment_end=end_time,
                primary_product_id=primary_product_id,
                click_delta=total_click,
                cart_delta=total_cart,
                sales_delta=total_sales,
                gmv_delta=total_gmv,
                comment_delta=comment_delta,
                viewer_delta=viewer_delta,
                spike=merged_spike,
                trigger_events=trigger_events,
            ))

        return candidates

    async def _get_engagement_deltas(
        self,
        session_id: str,
        start: datetime,
        end: datetime,
    ) -> Tuple[int, int]:
        """
        Count comment events and compute viewer delta in the time window.
        """
        # Comment count
        comment_count = await self.db.scalar(
            select(sa_func.count(RawEvent.id))
            .where(
                and_(
                    RawEvent.session_id == session_id,
                    RawEvent.event_type == "comment_added",
                    RawEvent.captured_at_client >= start,
                    RawEvent.captured_at_client <= end,
                )
            )
        ) or 0

        # Viewer delta: max - min viewer_count_snapshot in window
        viewer_result = await self.db.execute(
            select(
                sa_func.max(RawEvent.numeric_value),
                sa_func.min(RawEvent.numeric_value),
            )
            .where(
                and_(
                    RawEvent.session_id == session_id,
                    RawEvent.event_type == "viewer_count_snapshot",
                    RawEvent.captured_at_client >= start,
                    RawEvent.captured_at_client <= end,
                )
            )
        )
        row = viewer_result.one_or_none()
        if row and row[0] is not None and row[1] is not None:
            viewer_delta = int(row[0]) - int(row[1])
        else:
            viewer_delta = 0

        return comment_count, viewer_delta

    async def _get_trigger_events(
        self,
        session_id: str,
        start: datetime,
        end: datetime,
    ) -> List[dict]:
        """
        Fetch trigger events (product_pinned, product_switched, comment_spike, etc.)
        within the time window.
        """
        result = await self.db.execute(
            select(RawEvent)
            .where(
                and_(
                    RawEvent.session_id == session_id,
                    RawEvent.event_type.in_(TRIGGER_EVENT_TYPES),
                    RawEvent.captured_at_client >= start,
                    RawEvent.captured_at_client <= end,
                )
            )
            .order_by(RawEvent.captured_at_client)
        )
        events = result.scalars().all()

        return [
            {
                "id": e.id,
                "event_type": e.event_type,
                "captured_at": e.captured_at_client,
                "product_id": e.product_id,
                "text_value": e.text_value,
            }
            for e in events
        ]

    # ─── Step 5: Score and Classify ───────────────────────────────

    def _score_moments(
        self, candidates: List[MomentCandidate]
    ) -> List[MomentCandidate]:
        """
        Compute strength_score and classify moment_type for each candidate.
        """
        # First, find max values across all candidates for normalization
        if not candidates:
            return []

        max_click = max(c.click_delta for c in candidates) or 1
        max_cart = max(c.cart_delta for c in candidates) or 1
        max_sales = max(c.sales_delta for c in candidates) or 1
        max_gmv = max(c.gmv_delta for c in candidates) or 1
        max_comment = max(c.comment_delta for c in candidates) or 1
        max_viewer = max(c.viewer_delta for c in candidates) or 1

        for c in candidates:
            # Normalize each component (0.0 - 1.0)
            norm_click = Decimal(str(c.click_delta / max_click))
            norm_cart = Decimal(str(c.cart_delta / max_cart))
            norm_sales = Decimal(str(c.sales_delta / max_sales))
            norm_gmv = Decimal(str(c.gmv_delta / max_gmv)) if max_gmv > 0 else Decimal("0")
            norm_comment = Decimal(str(c.comment_delta / max_comment))
            norm_viewer = Decimal(str(c.viewer_delta / max_viewer)) if max_viewer > 0 else Decimal("0")

            # Compute weighted score
            score = (
                W_CLICK * norm_click
                + W_CART * norm_cart
                + W_SALES * norm_sales
                + W_GMV * norm_gmv
                + W_COMMENT * norm_comment
                + W_VIEWER * norm_viewer
            )

            # Clamp to [0, 1]
            c._strength_score = min(max(score, Decimal("0")), Decimal("1"))

            # Classify moment type
            if c.spike.is_strong:
                c._moment_type = "strong"
            elif c.spike.is_conversion:
                c._moment_type = "conversion"
            elif c.spike.is_trigger:
                c._moment_type = "trigger"
            else:
                c._moment_type = "trigger"  # fallback

        return candidates

    # ─── Step 6: Store Moments ────────────────────────────────────

    async def _store_moments(
        self, candidates: List[MomentCandidate]
    ) -> List[dict]:
        """
        Store moment candidates as ext_sales_moments and create event links.
        """
        stored = []

        for c in candidates:
            moment_id = str(uuid.uuid4())

            moment = ExtSalesMoment(
                id=moment_id,
                session_id=c.session_id,
                moment_start_at=c.moment_start,
                moment_end_at=c.moment_end,
                primary_product_id=c.primary_product_id,
                click_delta=c.click_delta,
                cart_delta=c.cart_delta,
                sales_delta=c.sales_delta,
                gmv_delta=Decimal(str(c.gmv_delta)),
                comment_delta=c.comment_delta,
                viewer_delta=c.viewer_delta,
                strength_score=c._strength_score,
                moment_type=c._moment_type,
                evidence_level="estimated",
                status="candidate",
            )
            self.db.add(moment)

            # Create event links for trigger events
            for evt in c.trigger_events:
                time_dist = int(
                    (evt["captured_at"] - c.moment_start).total_seconds() * 1000
                )

                # Determine relation type
                if evt["captured_at"] < c.moment_start:
                    relation = "before"
                elif evt["captured_at"] > c.moment_end:
                    relation = "after"
                else:
                    relation = "inside"

                # If it's a trigger-type event, mark as trigger_candidate
                if evt["event_type"] in {"product_pinned", "product_switched"}:
                    relation = "trigger_candidate"

                link = MomentEventLink(
                    sales_moment_id=moment_id,
                    raw_event_id=evt["id"],
                    relation_type=relation,
                    time_distance_ms=time_dist,
                )
                self.db.add(link)

            # Also link nearby raw events (comments, viewer snapshots, etc.)
            await self._link_nearby_events(
                moment_id,
                c.session_id,
                c.moment_start - timedelta(seconds=60),  # 60s before
                c.moment_end + timedelta(seconds=30),     # 30s after
            )

            stored.append({
                "id": moment_id,
                "session_id": c.session_id,
                "moment_start_at": c.moment_start.isoformat(),
                "moment_end_at": c.moment_end.isoformat(),
                "primary_product_id": c.primary_product_id,
                "click_delta": c.click_delta,
                "cart_delta": c.cart_delta,
                "sales_delta": c.sales_delta,
                "gmv_delta": c.gmv_delta,
                "comment_delta": c.comment_delta,
                "viewer_delta": c.viewer_delta,
                "strength_score": float(c._strength_score),
                "moment_type": c._moment_type,
            })

        await self.db.commit()
        return stored

    async def _link_nearby_events(
        self,
        moment_id: str,
        session_id: str,
        start: datetime,
        end: datetime,
    ):
        """
        Link raw events near the moment for context.
        Includes comments, viewer snapshots, KPI snapshots.
        """
        # Fetch nearby events (limit to avoid excessive linking)
        result = await self.db.execute(
            select(RawEvent)
            .where(
                and_(
                    RawEvent.session_id == session_id,
                    RawEvent.captured_at_client >= start,
                    RawEvent.captured_at_client <= end,
                    RawEvent.event_type.in_({
                        "comment_added",
                        "viewer_count_snapshot",
                        "dashboard_kpi_snapshot",
                        "product_metrics_snapshot",
                        "purchase_notice_detected",
                    }),
                )
            )
            .order_by(RawEvent.captured_at_client)
            .limit(100)
        )
        events = result.scalars().all()

        for evt in events:
            time_dist = int(
                (evt.captured_at_client - start).total_seconds() * 1000
            )

            # Check if link already exists (from trigger events)
            existing = await self.db.execute(
                select(MomentEventLink)
                .where(
                    and_(
                        MomentEventLink.sales_moment_id == moment_id,
                        MomentEventLink.raw_event_id == evt.id,
                    )
                )
            )
            if existing.scalar_one_or_none():
                continue

            # Determine relation
            moment_start_time = start + timedelta(seconds=60)  # undo the -60s offset
            moment_end_time = end - timedelta(seconds=30)      # undo the +30s offset

            if evt.captured_at_client < moment_start_time:
                relation = "before"
            elif evt.captured_at_client > moment_end_time:
                relation = "after"
            else:
                relation = "inside"

            link = MomentEventLink(
                sales_moment_id=moment_id,
                raw_event_id=evt.id,
                relation_type=relation,
                time_distance_ms=time_dist,
            )
            self.db.add(link)

    # ─── Utility ──────────────────────────────────────────────────

    async def _clear_existing_moments(self, session_id: str):
        """Delete existing moments and links for a session."""
        # Get moment IDs
        result = await self.db.execute(
            select(ExtSalesMoment.id)
            .where(ExtSalesMoment.session_id == session_id)
        )
        moment_ids = [row[0] for row in result.all()]

        if moment_ids:
            # Delete links first (FK constraint)
            from sqlalchemy import delete
            await self.db.execute(
                delete(MomentEventLink)
                .where(MomentEventLink.sales_moment_id.in_(moment_ids))
            )
            # Delete moments
            await self.db.execute(
                delete(ExtSalesMoment)
                .where(ExtSalesMoment.session_id == session_id)
            )
            await self.db.commit()

            logger.info(
                f"[MomentEngine] Cleared {len(moment_ids)} existing moments "
                f"for session {session_id}"
            )
