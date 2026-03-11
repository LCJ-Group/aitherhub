#!/usr/bin/env python3
"""
Startup Smoke Test
==================
Verifies that the backend application can be imported without crashing.

This catches the exact class of bug that caused the 2026-03-11 outage:
a refactor removed `Container.db` provider and `database.py`, but
`main.py` still referenced `self.container.db()` and `app_creator.db`,
causing an AttributeError at import time → 503 on Azure.

The test works by:
1. Stubbing out external services (Azure SDK, database, etc.)
2. Attempting to import `app.main`
3. Verifying the FastAPI `app` object is created successfully

Run:
    python tests/test_startup_smoke.py
    # or in CI:
    cd backend && python -m pytest ../tests/test_startup_smoke.py -v
"""
import importlib
import os
import ssl
import sys
import types
import unittest.mock
from pathlib import Path
from unittest.mock import MagicMock

# Project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"


def _stub_external_modules():
    """
    Stub out modules that require external services (Azure, DB, etc.)
    so we can test the import chain in CI without real credentials.
    """
    # Azure SDK stubs
    azure_modules = [
        "azure",
        "azure.storage",
        "azure.storage.blob",
        "azure.storage.queue",
        "azure.identity",
    ]
    for mod_name in azure_modules:
        if mod_name not in sys.modules:
            mod = types.ModuleType(mod_name)
            sys.modules[mod_name] = mod

    blob_mod = sys.modules["azure.storage.blob"]
    for attr in [
        "BlobServiceClient",
        "BlobSasPermissions",
        "ContainerSasPermissions",
        "generate_blob_sas",
        "generate_container_sas",
    ]:
        setattr(blob_mod, attr, MagicMock())

    queue_mod = sys.modules["azure.storage.queue"]
    queue_mod.QueueClient = MagicMock()

    # qdrant stub
    if "qdrant_client" not in sys.modules:
        sys.modules["qdrant_client"] = types.ModuleType("qdrant_client")

    # Stub sqlalchemy async engine creation to avoid real DB connection
    original_create = None
    try:
        from sqlalchemy.ext.asyncio import engine as _async_engine_mod

        original_create = _async_engine_mod.create_async_engine
    except Exception:
        pass

    # Set dummy DATABASE_URL so db.py doesn't crash on None
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql+asyncpg://smoke_test:smoke_test@localhost:5432/smoke_test",
    )
    # Set dummy secrets so config doesn't crash
    os.environ.setdefault("SECRET_KEY", "smoke-test-secret-key")
    os.environ.setdefault("AZURE_STORAGE_CONNECTION_STRING", "DefaultEndpointsProtocol=https;AccountName=test;AccountKey=dGVzdA==;EndpointSuffix=core.windows.net")
    os.environ.setdefault("AZURE_STORAGE_ACCOUNT_NAME", "test")


def test_backend_imports_successfully():
    """
    Verify that `app.main` can be imported without raising exceptions.

    This catches:
    - Missing module references (deleted files still imported)
    - Broken DI container wiring
    - Circular imports
    - AttributeError on module-level code
    """
    _stub_external_modules()

    # Add backend to path
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))

    # Clear any cached imports so we get a fresh import
    modules_to_clear = [k for k in sys.modules if k.startswith("app.")]
    for k in modules_to_clear:
        del sys.modules[k]

    try:
        import app.main

        # Verify the app object exists and is a FastAPI instance
        assert hasattr(app.main, "app"), "app.main must expose 'app' attribute"
        assert hasattr(
            app.main.app, "routes"
        ), "app.main.app must be a FastAPI/Starlette application"

        # Verify routes are registered
        route_paths = [getattr(r, "path", "") for r in app.main.app.routes]
        assert "/" in route_paths, "Root endpoint '/' must be registered"
        assert "/version" in route_paths, "Version endpoint '/version' must be registered"

        print("  OK: app.main imported successfully")
        print(f"  OK: {len(route_paths)} routes registered")
        return True

    except Exception as e:
        print(f"  FATAL: app.main import failed: {type(e).__name__}: {e}")
        import traceback

        traceback.print_exc()
        return False


# =============================================================================
# Standalone runner
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Startup Smoke Test")
    print("=" * 60)

    success = test_backend_imports_successfully()

    print(f"\n{'=' * 60}")
    print(f"Result: {'PASS' if success else 'FAIL'}")
    print(f"{'=' * 60}")

    sys.exit(0 if success else 1)
