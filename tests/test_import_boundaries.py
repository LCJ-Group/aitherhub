#!/usr/bin/env python3
"""
Import Boundary Enforcement Tests
===================================
These tests ensure the architectural boundary between API and Worker
is never violated. Run in CI to prevent accidental cross-imports.

Rules:
    1. worker/ MUST NOT import from backend/app/
    2. backend/app/ MUST NOT import from worker/
    3. shared/ MUST NOT import from backend/app/ or worker/
    4. worker/ MAY import from shared/
    5. backend/app/ MAY import from shared/

Run:
    python -m pytest tests/test_import_boundaries.py -v
    # or standalone:
    python tests/test_import_boundaries.py
"""
import os
import re
import sys
from pathlib import Path

# Project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def find_python_files(directory: str) -> list:
    """Find all .py files in a directory recursively."""
    base = PROJECT_ROOT / directory
    if not base.exists():
        return []
    return [
        str(p.relative_to(PROJECT_ROOT))
        for p in base.rglob("*.py")
        if "__pycache__" not in str(p)
    ]


def extract_imports(filepath: str) -> list:
    """Extract all import statements from a Python file."""
    full_path = PROJECT_ROOT / filepath
    imports = []
    try:
        with open(full_path, "r", errors="ignore") as f:
            for lineno, line in enumerate(f, 1):
                stripped = line.strip()
                # Skip comments and empty lines
                if not stripped or stripped.startswith("#"):
                    continue
                # Match import statements
                if stripped.startswith("from ") or stripped.startswith("import "):
                    imports.append((lineno, stripped))
    except Exception:
        pass
    return imports


def check_forbidden_imports(source_dir: str, forbidden_patterns: list, description: str) -> list:
    """Check for forbidden import patterns in a directory.

    Returns list of violations: (filepath, lineno, import_line, pattern)
    """
    violations = []
    files = find_python_files(source_dir)
    for filepath in files:
        for lineno, import_line in extract_imports(filepath):
            for pattern in forbidden_patterns:
                if re.search(pattern, import_line):
                    violations.append((filepath, lineno, import_line, pattern))
    return violations


# =============================================================================
# Test Cases
# =============================================================================

def test_worker_does_not_import_backend():
    """RULE 1: worker/ MUST NOT import from backend/app/"""
    violations = check_forbidden_imports(
        source_dir="worker",
        forbidden_patterns=[
            r"\bfrom\s+app\.",
            r"\bimport\s+app\.",
            r"\bfrom\s+backend\.",
            r"\bimport\s+backend\.",
        ],
        description="worker/ → backend/app/",
    )

    # Known exceptions (to be removed as we migrate)
    known_exceptions = {
        # backfill_phase_metrics.py has a conditional fallback import
        # that will be removed in a future PR
        "worker/batch/backfill_phase_metrics.py",
        # run_live_analysis.py intentionally imports from backend/app/services/
        # as it runs as a subprocess with backend/ on sys.path
        "worker/batch/run_live_analysis.py",
    }

    actual_violations = [
        v for v in violations
        if v[0] not in known_exceptions
    ]

    if actual_violations:
        msg = "BOUNDARY VIOLATION: worker/ imports from backend/app/\n"
        for filepath, lineno, import_line, pattern in actual_violations:
            msg += f"  {filepath}:{lineno}: {import_line}\n"
        raise AssertionError(msg)


def test_backend_does_not_import_worker():
    """RULE 2: backend/app/ MUST NOT import from worker/"""
    violations = check_forbidden_imports(
        source_dir="backend/app",
        forbidden_patterns=[
            r"\bfrom\s+worker\.",
            r"\bimport\s+worker\.",
        ],
        description="backend/app/ → worker/",
    )

    if violations:
        msg = "BOUNDARY VIOLATION: backend/app/ imports from worker/\n"
        for filepath, lineno, import_line, pattern in violations:
            msg += f"  {filepath}:{lineno}: {import_line}\n"
        raise AssertionError(msg)


def test_shared_does_not_import_app_or_worker():
    """RULE 3: shared/ MUST NOT import from backend/app/ or worker/"""
    violations = check_forbidden_imports(
        source_dir="shared",
        forbidden_patterns=[
            r"\bfrom\s+app\.",
            r"\bimport\s+app\.",
            r"\bfrom\s+backend\.",
            r"\bimport\s+backend\.",
            r"\bfrom\s+worker\.",
            r"\bimport\s+worker\.",
        ],
        description="shared/ → backend/app/ or worker/",
    )

    if violations:
        msg = "BOUNDARY VIOLATION: shared/ imports from backend/app/ or worker/\n"
        for filepath, lineno, import_line, pattern in violations:
            msg += f"  {filepath}:{lineno}: {import_line}\n"
        raise AssertionError(msg)


def test_no_fastapi_in_worker():
    """Worker MUST NOT depend on FastAPI."""
    violations = check_forbidden_imports(
        source_dir="worker",
        forbidden_patterns=[
            r"\bfrom\s+fastapi\b",
            r"\bimport\s+fastapi\b",
        ],
        description="worker/ → fastapi",
    )

    if violations:
        msg = "BOUNDARY VIOLATION: worker/ imports FastAPI\n"
        for filepath, lineno, import_line, pattern in violations:
            msg += f"  {filepath}:{lineno}: {import_line}\n"
        raise AssertionError(msg)


def test_no_fastapi_in_shared():
    """Shared layer MUST NOT depend on FastAPI."""
    violations = check_forbidden_imports(
        source_dir="shared",
        forbidden_patterns=[
            r"\bfrom\s+fastapi\b",
            r"\bimport\s+fastapi\b",
        ],
        description="shared/ → fastapi",
    )

    if violations:
        msg = "BOUNDARY VIOLATION: shared/ imports FastAPI\n"
        for filepath, lineno, import_line, pattern in violations:
            msg += f"  {filepath}:{lineno}: {import_line}\n"
        raise AssertionError(msg)


# =============================================================================
# Standalone runner
# =============================================================================

if __name__ == "__main__":
    tests = [
        test_worker_does_not_import_backend,
        test_backend_does_not_import_worker,
        test_shared_does_not_import_app_or_worker,
        test_no_fastapi_in_worker,
        test_no_fastapi_in_shared,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {test.__name__}")
            print(f"        {e}")
            failed += 1

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed")
    print(f"{'='*60}")

    sys.exit(1 if failed > 0 else 0)
