"""Prompt02 architecture-equivalence gates.

These tests deliberately distinguish the accepted product runtime from a
provider-backed test-only vertical slice.  They are static/assembly gates and
must not be weakened to make the old receipt green.
"""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRODUCT_RUNTIME = ROOT / "src" / "dirty_data_to_olap" / "application" / "product_runtime.py"
MULTI_SOURCE = ROOT / "src" / "dirty_data_to_olap" / "application" / "multi_source_product.py"
MULTI_SOURCE_RUNTIME = ROOT / "src" / "dirty_data_to_olap" / "application" / "multi_source_runtime.py"
ACCEPTANCE = ROOT / "tests" / "product_acceptance" / "test_multi_source_v1.py"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for item in ast.walk(node):
        if isinstance(item, ast.Call):
            if isinstance(item.func, ast.Name):
                names.add(item.func.id)
            elif isinstance(item.func, ast.Attribute):
                names.add(item.func.attr)
    return names


def test_multi_source_builder_is_an_accepted_runtime_composition_root() -> None:
    """The builder must expose the same durable runtime boundary as V1."""

    tree = _tree(PRODUCT_RUNTIME)
    builder = _function(tree, "build_multi_source_product")
    calls = _called_names(builder)
    source = ast.unparse(builder)

    assert "MultiSourceProductService" not in source
    assert "LocalProductRuntime" in source or "MultiSourceProductRuntime" in source
    assert "ExecutionPlanService" in source
    assert "BackendService" in source
    returns = [node for node in ast.walk(builder) if isinstance(node, ast.Return)]
    assert any(
        isinstance(node.value, ast.Tuple)
        and [item.id for item in node.value.elts if isinstance(item, ast.Name)] == ["platform", "backend", "runtime"]
        for node in returns
    )


def test_multi_source_module_has_no_primary_path_bypasses() -> None:
    """Direct DuckDB and synthetic success/review construction are forbidden."""

    tree = _tree(MULTI_SOURCE)
    source = MULTI_SOURCE.read_text(encoding="utf-8")
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    function_names = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }

    assert "duckdb" not in imported_modules
    assert "_stage" not in function_names
    assert "_review" not in function_names
    assert "_materialize" not in function_names
    assert "status=\"SUCCEEDED\"" not in source


def test_multi_source_acceptance_enters_through_supported_product_boundary() -> None:
    """The acceptance harness must exercise run, plan, review and resume APIs."""

    source = ACCEPTANCE.read_text(encoding="utf-8")
    assert "build_multi_source_product" in source
    assert "service.prepare(" not in source
    assert "service.resume(" not in source
    assert ".bind_product_source_set(" in source
    assert ".prepare_execution_plan(" in source
    assert ".submit(" in source
    assert ".review(" in source
    assert ".resume(" in source


def test_required_stage_boundaries_are_registered_by_the_multi_source_runtime() -> None:
    """The accepted handler registry must own every selected product stage."""

    source = PRODUCT_RUNTIME.read_text(encoding="utf-8") + MULTI_SOURCE_RUNTIME.read_text(encoding="utf-8")
    required = {
        "SOURCE_DISCOVERY",
        "SOURCE_SNAPSHOT_STAGE",
        "PROFILING",
        "DEPENDENCY_DISCOVERY",
        "SCHEMA_MATCHING",
        "QUALITY_ANALYSIS",
        "EVIDENCE_FUSION",
        "CANONICAL_HYPOTHESES",
        "ENTITY_RESOLUTION",
        "CANONICAL_IDENTITY_PREPARATION",
        "CANONICAL_FINALIZATION",
        "ANALYTICAL_PLANNING",
        "COMPILATION",
        "MATERIALIZATION",
        "SEMANTIC_MODELING",
        "VALIDATION_RECONCILIATION",
    }
    assert "StageHandlerRegistry" in source
    assert all(f'"{stage}"' in source or f"'{stage}'" in source for stage in required)
