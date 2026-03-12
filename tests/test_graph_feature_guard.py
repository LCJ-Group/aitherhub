"""
Graph Feature Guard Tests
==========================
These tests ensure the sales/product/viewer graph features are never
accidentally removed or broken by code changes.

The user has explicitly requested that graph functionality must be
preserved across all future updates.
"""
import os
import re
import sys

# ── Paths ──────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND = os.path.join(ROOT, "frontend", "src")
BACKEND = os.path.join(ROOT, "backend", "app")
WORKER = os.path.join(ROOT, "worker")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# ── Frontend: AnalyticsSection must exist and render graphs ────────
def test_analytics_section_exists():
    """AnalyticsSection.jsx must exist in the frontend."""
    path = os.path.join(FRONTEND, "components", "AnalyticsSection.jsx")
    assert os.path.isfile(path), (
        "AnalyticsSection.jsx is missing! "
        "This component renders the sales/viewer/product graphs. "
        "DO NOT remove it."
    )


def test_analytics_section_renders_charts():
    """AnalyticsSection must contain chart rendering logic."""
    path = os.path.join(FRONTEND, "components", "AnalyticsSection.jsx")
    content = _read(path)
    # Must contain chart/graph rendering keywords
    assert any(kw in content for kw in ["Chart", "chart", "ResponsiveContainer", "BarChart", "LineChart", "canvas"]), (
        "AnalyticsSection.jsx does not contain chart rendering code. "
        "The graph feature must be preserved."
    )


def test_analytics_section_uses_csv_metrics():
    """AnalyticsSection must reference csv_metrics data."""
    path = os.path.join(FRONTEND, "components", "AnalyticsSection.jsx")
    content = _read(path)
    assert "csv_metrics" in content or "gmv" in content or "viewer_count" in content, (
        "AnalyticsSection.jsx does not reference csv_metrics/gmv/viewer_count. "
        "The graph feature relies on this data."
    )


def test_analytics_section_imported_in_video_detail():
    """VideoDetail must import and render AnalyticsSection."""
    path = os.path.join(FRONTEND, "components", "VideoDetail.jsx")
    content = _read(path)
    assert "AnalyticsSection" in content, (
        "VideoDetail.jsx does not reference AnalyticsSection. "
        "The graph component must be rendered in the video detail page."
    )


# ── Backend: product-data API must exist ───────────────────────────
def test_product_data_endpoint_exists():
    """The product-data API endpoint must exist in video.py."""
    path = os.path.join(BACKEND, "api", "v1", "endpoints", "video.py")
    content = _read(path)
    assert "product-data" in content, (
        "product-data endpoint is missing from video.py! "
        "This API provides Excel-based product and trend data for graphs."
    )


def test_product_data_returns_products_and_trends():
    """product-data API must return both products and trends."""
    path = os.path.join(BACKEND, "api", "v1", "endpoints", "video.py")
    content = _read(path)
    assert "products" in content and "trends" in content, (
        "product-data API must return both 'products' and 'trends' data."
    )


# ── Backend: import os must be present in video.py ─────────────────
def test_video_py_imports_os():
    """video.py must import os (required for _parse_excel temp file cleanup)."""
    path = os.path.join(BACKEND, "api", "v1", "endpoints", "video.py")
    content = _read(path)
    assert re.search(r"^import os", content, re.MULTILINE), (
        "video.py does not 'import os'. "
        "This is required for _parse_excel to clean up temp files. "
        "Without it, the product-data API silently fails."
    )


# ── Worker: CSV matching must exist in process_video ───────────────
def test_csv_matching_exists():
    """process_video.py must contain CSV slot matching logic (STEP 5.5)."""
    path = os.path.join(WORKER, "batch", "process_video.py")
    content = _read(path)
    assert "STEP 5.5" in content or "csv_metrics" in content, (
        "process_video.py does not contain STEP 5.5 CSV matching logic. "
        "This step populates csv_metrics for the graph feature."
    )


# ── Storage: SAS generation fallback must exist ────────────────────
def test_sas_generation_fallback():
    """storage_service.py must have ACCOUNT_NAME fallback from CONNECTION_STRING."""
    path = os.path.join(BACKEND, "services", "storage_service.py")
    content = _read(path)
    assert "AccountName" in content, (
        "storage_service.py does not parse AccountName from CONNECTION_STRING. "
        "This fallback is required when AZURE_STORAGE_ACCOUNT_NAME env var is not set."
    )
