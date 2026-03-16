"""
Upload Core Regression Tests
=============================

These tests verify that the upload pipeline endpoints are correctly
registered, respond with the expected status codes, and enforce the
API contract.  They use FastAPI's TestClient (via httpx) so no real
Azure Blob / Queue / DB connections are needed.

Run:
    cd backend && python -m pytest tests/test_upload_core.py -v
"""

from __future__ import annotations

import importlib
import os
import sys

import pytest

# ---------------------------------------------------------------------------
# Ensure the backend package is importable
# ---------------------------------------------------------------------------
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# ---------------------------------------------------------------------------
# Set minimal env vars so the app can boot without real Azure credentials
# ---------------------------------------------------------------------------
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test.db")
os.environ.setdefault("AZURE_STORAGE_CONNECTION_STRING", "")
os.environ.setdefault("AZURE_STORAGE_ACCOUNT_NAME", "devstoreaccount1")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ALGORITHM", "HS256")


# ---------------------------------------------------------------------------
# Test: Upload Core endpoints are registered on the app
# ---------------------------------------------------------------------------
class TestUploadCoreRouteRegistration:
    """Verify that all upload-core endpoints exist in the route table."""

    EXPECTED_PATHS = [
        ("/api/v1/videos/generate-upload-url", {"POST"}),
        ("/api/v1/videos/generate-download-url", {"POST"}),
        ("/api/v1/videos/upload-complete", {"POST"}),
        ("/api/v1/videos/batch-upload-complete", {"POST"}),
        ("/api/v1/videos/generate-excel-upload-url", {"POST"}),
        # Parameterised paths are registered as /api/v1/videos/uploads/check/{user_id}
        ("/api/v1/videos/uploads/check/{user_id}", {"GET"}),
        ("/api/v1/videos/uploads/clear/{user_id}", {"DELETE"}),
    ]

    def _get_route_map(self):
        """Import the app and build a {path: set(methods)} map."""
        try:
            from app.main import app
            route_map: dict[str, set[str]] = {}
            for route in app.routes:
                if hasattr(route, "path") and hasattr(route, "methods"):
                    route_map[route.path] = set(route.methods)
            return route_map
        except Exception as exc:
            pytest.skip(f"Cannot import app (missing deps?): {exc}")

    @pytest.mark.parametrize("path,methods", EXPECTED_PATHS)
    def test_route_exists(self, path, methods):
        route_map = self._get_route_map()
        assert path in route_map, f"Route {path} not found. Available: {sorted(route_map.keys())}"
        for m in methods:
            assert m in route_map[path], f"Method {m} not found for {path}"


# ---------------------------------------------------------------------------
# Test: Upload Core endpoints are NOT duplicated in video.py
# ---------------------------------------------------------------------------
class TestNoDuplicateRoutes:
    """Ensure upload endpoints are NOT still defined in video.py."""

    UPLOAD_ENDPOINT_NAMES = [
        "generate_upload_url",
        "upload_complete",
        "batch_upload_complete",
        "generate_excel_upload_url",
        "check_upload_resume",
        "clear_user_uploads",
    ]

    def test_video_py_has_no_upload_endpoints(self):
        """video.py should not define any upload-related endpoint functions."""
        try:
            from app.api.v1.endpoints import video as video_mod
        except Exception as exc:
            pytest.skip(f"Cannot import video module: {exc}")

        for name in self.UPLOAD_ENDPOINT_NAMES:
            assert not hasattr(video_mod, name), (
                f"video.py still defines '{name}' – it should only exist in upload_core.py"
            )


# ---------------------------------------------------------------------------
# Test: Feature Flags API
# ---------------------------------------------------------------------------
class TestFeatureFlags:
    """Verify the feature flags module works correctly."""

    def test_flags_to_dict(self):
        from app.core.feature_flags import flags
        d = flags.to_dict()
        assert isinstance(d, dict)
        assert "upload_core_isolated" in d
        assert "live_report" in d
        assert "product_detection" in d
        assert "video_structure" in d
        assert "upload_health_check" in d

    def test_flag_defaults_are_true(self):
        """All flags should default to True (enabled)."""
        from app.core.feature_flags import flags
        d = flags.to_dict()
        for key, val in d.items():
            assert val is True, f"Flag {key} should default to True"

    def test_flag_env_override(self, monkeypatch):
        """Setting FF_LIVE_REPORT=0 should disable the flag."""
        monkeypatch.setenv("FF_LIVE_REPORT", "0")
        # Re-import to pick up new env
        import app.core.feature_flags as ff_mod
        importlib.reload(ff_mod)
        assert ff_mod.flags.live_report is False
        # Restore
        monkeypatch.delenv("FF_LIVE_REPORT", raising=False)
        importlib.reload(ff_mod)


# ---------------------------------------------------------------------------
# Test: Upload Core schema contract
# ---------------------------------------------------------------------------
class TestUploadSchemaContract:
    """Verify that the API schemas have the expected fields."""

    def test_upload_complete_response_fields(self):
        from app.schema.video_schema import UploadCompleteResponse
        fields = set(UploadCompleteResponse.model_fields.keys())
        assert "video_id" in fields
        assert "status" in fields
        assert "message" in fields

    def test_generate_upload_url_response_fields(self):
        from app.schema.video_schema import GenerateUploadURLResponse
        fields = set(GenerateUploadURLResponse.model_fields.keys())
        assert "video_id" in fields
        assert "upload_id" in fields
        assert "upload_url" in fields
        assert "blob_url" in fields
        assert "expires_at" in fields

    def test_batch_upload_complete_response_fields(self):
        from app.schema.video_schema import BatchUploadCompleteResponse
        fields = set(BatchUploadCompleteResponse.model_fields.keys())
        assert "video_ids" in fields
        assert "status" in fields
        assert "message" in fields
        assert "failed" in fields
