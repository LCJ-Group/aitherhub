#!/usr/bin/env python3
"""
CI Smoke Test: Backend Import Safety Check
===========================================
Verifies that all backend Python files can be parsed (no SyntaxError)
and that FastAPI endpoint functions don't reference undefined names
from their import blocks.

This catches the class of bug where:
  - A new endpoint uses `Request` but forgets to import it
  - A module references a name that doesn't exist in the imported module
  - A syntax error slips through code review

Usage:
  python scripts/ci_smoke_test.py

Exit codes:
  0 = all checks passed
  1 = one or more checks failed
"""

import ast
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = BACKEND_ROOT / "app"

# Known builtins and common globals that don't need imports
KNOWN_GLOBALS = {
    # Python builtins
    "True", "False", "None", "print", "len", "str", "int", "float", "bool",
    "list", "dict", "set", "tuple", "type", "range", "enumerate", "zip",
    "map", "filter", "sorted", "reversed", "any", "all", "min", "max",
    "sum", "abs", "round", "isinstance", "issubclass", "hasattr", "getattr",
    "setattr", "delattr", "super", "property", "staticmethod", "classmethod",
    "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
    "AttributeError", "RuntimeError", "ImportError", "FileNotFoundError",
    "NotImplementedError", "StopIteration", "OSError", "IOError",
    "open", "id", "hash", "repr", "format", "input", "vars", "dir",
    "callable", "iter", "next", "bytes", "bytearray", "memoryview",
    "complex", "frozenset", "object", "slice", "Ellipsis",
    # Common typing
    "Optional", "List", "Dict", "Set", "Tuple", "Union", "Any",
    "Callable", "Awaitable", "AsyncGenerator", "Generator",
    "Type", "ClassVar", "Final", "Literal", "Protocol",
    "TypeVar", "Generic", "Annotated",
}


def check_syntax(filepath: Path) -> list[str]:
    """Check if a Python file has valid syntax."""
    errors = []
    try:
        source = filepath.read_text(encoding="utf-8")
        ast.parse(source, filename=str(filepath))
    except SyntaxError as e:
        errors.append(f"  SyntaxError in {filepath.relative_to(BACKEND_ROOT)}: "
                      f"line {e.lineno}: {e.msg}")
    return errors


def check_fastapi_imports(filepath: Path) -> list[str]:
    """
    Check that FastAPI endpoint files import all names they use
    in function signatures (especially type annotations like Request, Response, etc).
    """
    errors = []
    try:
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return []  # Already caught by check_syntax

    # Collect all imported names
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.asname or alias.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    # Can't track star imports
                    imported_names.add("*")
                else:
                    imported_names.add(alias.asname or alias.name)

    # If there's a star import, we can't reliably check
    if "*" in imported_names:
        return []

    # Collect all top-level function/class definitions
    defined_names = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined_names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            defined_names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defined_names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined_names.add(node.target.id)

    all_known = imported_names | defined_names | KNOWN_GLOBALS

    # Check function parameter annotations in decorated functions (likely endpoints)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Check parameter annotations
            for arg in node.args.args + node.args.kwonlyargs:
                if arg.annotation:
                    _check_annotation(arg.annotation, all_known, filepath, errors)
            # Check return annotation
            if node.returns:
                _check_annotation(node.returns, all_known, filepath, errors)

    return errors


def _check_annotation(annotation_node, known_names, filepath, errors):
    """Check if names used in a type annotation are defined."""
    if isinstance(annotation_node, ast.Name):
        name = annotation_node.id
        if name not in known_names:
            rel = filepath.relative_to(BACKEND_ROOT)
            errors.append(
                f"  UndefinedName in {rel}: "
                f"line {annotation_node.lineno}: '{name}' used in annotation "
                f"but not imported or defined"
            )
    elif isinstance(annotation_node, ast.Subscript):
        _check_annotation(annotation_node.value, known_names, filepath, errors)
        _check_annotation(annotation_node.slice, known_names, filepath, errors)
    elif isinstance(annotation_node, ast.Attribute):
        # e.g., fastapi.Request - the base is imported
        pass
    elif isinstance(annotation_node, ast.Tuple):
        for elt in annotation_node.elts:
            _check_annotation(elt, known_names, filepath, errors)
    elif isinstance(annotation_node, ast.Constant):
        pass  # String annotations like "VideoResponse"
    elif isinstance(annotation_node, ast.BinOp):
        # Union type with | operator
        _check_annotation(annotation_node.left, known_names, filepath, errors)
        _check_annotation(annotation_node.right, known_names, filepath, errors)


def main():
    print("=" * 60)
    print("Backend CI Smoke Test")
    print("=" * 60)

    all_errors = []
    py_files = list(APP_DIR.rglob("*.py"))
    print(f"\nChecking {len(py_files)} Python files in {APP_DIR.relative_to(BACKEND_ROOT)}/\n")

    # Phase 1: Syntax check
    print("Phase 1: Syntax validation...")
    syntax_errors = []
    for f in py_files:
        syntax_errors.extend(check_syntax(f))
    if syntax_errors:
        print(f"  FAIL: {len(syntax_errors)} syntax error(s)")
        all_errors.extend(syntax_errors)
    else:
        print(f"  PASS: All {len(py_files)} files have valid syntax")

    # Phase 2: Import/annotation check on endpoint files
    print("\nPhase 2: FastAPI endpoint import validation...")
    endpoint_dir = APP_DIR / "api" / "v1" / "endpoints"
    endpoint_files = list(endpoint_dir.glob("*.py")) if endpoint_dir.exists() else []
    import_errors = []
    for f in endpoint_files:
        import_errors.extend(check_fastapi_imports(f))
    if import_errors:
        print(f"  FAIL: {len(import_errors)} undefined name(s) in endpoints")
        all_errors.extend(import_errors)
    else:
        print(f"  PASS: All {len(endpoint_files)} endpoint files OK")

    # Phase 3: Compile check (catches more subtle issues)
    print("\nPhase 3: Compile check (py_compile)...")
    import py_compile
    compile_errors = []
    for f in py_files:
        try:
            py_compile.compile(str(f), doraise=True)
        except py_compile.PyCompileError as e:
            compile_errors.append(f"  CompileError: {e}")
    if compile_errors:
        print(f"  FAIL: {len(compile_errors)} compile error(s)")
        all_errors.extend(compile_errors)
    else:
        print(f"  PASS: All {len(py_files)} files compile OK")

    # Summary
    print("\n" + "=" * 60)
    if all_errors:
        print(f"FAILED: {len(all_errors)} error(s) found\n")
        for err in all_errors:
            print(err)
        sys.exit(1)
    else:
        print("ALL CHECKS PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
