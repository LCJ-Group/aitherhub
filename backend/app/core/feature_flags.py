"""
Feature Flags – environment-variable-based feature toggles.

Usage:
    from app.core.feature_flags import flags

    if flags.live_report:
        # show live report UI
    if flags.product_detection:
        # run product detection pipeline

Adding a new flag:
    1. Add an env var (e.g. FF_MY_FEATURE=1) to Azure App Service configuration.
    2. Add a property below with the same pattern.
    3. The flag defaults to False (off) unless explicitly set to "1" or "true".

This module intentionally has ZERO database dependency so it can be
imported anywhere without side effects.
"""

from __future__ import annotations

import os
from functools import lru_cache


def _is_truthy(key: str, default: bool = False) -> bool:
    """Read an environment variable and return True if it looks truthy."""
    val = os.getenv(key, "").strip().lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return default


class FeatureFlags:
    """Singleton-style feature flag reader.

    Each property reads from an environment variable prefixed with ``FF_``.
    Values are evaluated once per process start (no hot-reload).
    """

    # ── Upload Core ──────────────────────────────────
    @property
    def upload_core_isolated(self) -> bool:
        """Upload endpoints served from upload_core.py (always True after migration)."""
        return _is_truthy("FF_UPLOAD_CORE_ISOLATED", default=True)

    # ── Live Report v1 ───────────────────────────────
    @property
    def live_report(self) -> bool:
        """Enable the Live Report generation feature."""
        return _is_truthy("FF_LIVE_REPORT", default=True)

    # ── Product Detection ────────────────────────────
    @property
    def product_detection(self) -> bool:
        """Enable product detection pipeline step."""
        return _is_truthy("FF_PRODUCT_DETECTION", default=True)

    # ── Video Structure Analysis ─────────────────────
    @property
    def video_structure(self) -> bool:
        """Enable video structure grouping and best-phase analysis."""
        return _is_truthy("FF_VIDEO_STRUCTURE", default=True)

    # ── Upload Health Check (Admin) ──────────────────
    @property
    def upload_health_check(self) -> bool:
        """Enable upload health check endpoint in admin dashboard."""
        return _is_truthy("FF_UPLOAD_HEALTH_CHECK", default=True)

    def to_dict(self) -> dict[str, bool]:
        """Return all flags as a dict (for API responses / debugging)."""
        return {
            "upload_core_isolated": self.upload_core_isolated,
            "live_report": self.live_report,
            "product_detection": self.product_detection,
            "video_structure": self.video_structure,
            "upload_health_check": self.upload_health_check,
        }


# Module-level singleton
flags = FeatureFlags()
