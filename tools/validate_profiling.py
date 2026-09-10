"""Fail-closed Step08 profiling contract and boundary validator."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "dirty_data_to_olap"


def main() -> int:
    errors: list[str] = []

    required = (
        "src/dirty_data_to_olap/domain/contracts/profiling.py",
        "src/dirty_data_to_olap/application/profiling.py",
        "src/dirty_data_to_olap/application/profiling_adapter.py",
        "src/dirty_data_to_olap/adapters/profiling/dataprofiler.py",
        "src/dirty_data_to_olap/adapters/profiling/artifacts.py",
        "docs/profiling/PROFILING_CONTRACT.md",
        "docs/profiling/METRICS_AND_SCOPE.md",
        "docs/profiling/DATAPROFILER_INTEGRATION.md",
        "docs/profiling/PROFILE_DIFF.md",
        "docs/oss/REUSE_RESEARCH_LEDGER.md",
    )
    for relative in required:
        if not (ROOT / relative).is_file():
            errors.append(f"missing required artifact: {relative}")

    try:
        state = yaml.safe_load((ROOT / "docs/execution/MASTER_EXECUTION_STATE.yml").read_text(encoding="utf-8"))
        execution = state["specialist_execution"]
        if execution["current_step"] not in (8, 9, 10, 11, 12, 13, 14):
            errors.append("state is not in Step08 implementation or a later specialist handoff")
        if execution["current_step"] == 9 and execution["last_completed_step"] != 8:
            errors.append("Step09 state must record Step08 completion")
        if execution["current_step"] == 10 and execution["last_completed_step"] != 9:
            errors.append("Step10 state must record Step09 completion")
        if execution["current_step"] == 11 and execution["last_completed_step"] != 10:
            errors.append("Step11 state must record Step10 completion")
        if execution["current_step"] == 12 and execution["last_completed_step"] != 11:
            errors.append("Step12 state must record Step11 completion")
        if execution["current_step"] == 13 and execution["last_completed_step"] != 12:
            errors.append("Step13 state must record Step12 completion")
        if execution["current_step"] == 14 and execution["last_completed_step"] != 13:
            errors.append("Step14 state must record Step13 completion")
        if state["gates"].get("G3_SOURCE_SAFETY") not in {"PENDING", "PASS", "BLOCKED"}:
            errors.append("formal G3 state is invalid")
    except Exception as exc:
        errors.append(f"cannot read execution state: {exc.__class__.__name__}")

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if '"DataProfiler==0.13.4"' not in pyproject:
        errors.append("official DataProfiler dependency is not pinned")
    if "[full]" in pyproject or "[ml]" in pyproject or "report" in pyproject.lower():
        errors.append("DataProfiler ML/full/report extras are not allowed")

    for root in (SRC / "domain", SRC / "application"):
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                else:
                    continue
                forbidden = ("dataprofiler", "pandas", "numpy", "pyarrow", "pickle")
                if any(item == token or item.startswith(token + ".") for item in modules for token in forbidden):
                    errors.append(f"vendor/native import outside adapter: {path.relative_to(ROOT)}")

    profiling_text = "\n".join(path.read_text(encoding="utf-8") for path in (SRC / "adapters" / "profiling").rglob("*.py"))
    if "pickle" in profiling_text.lower():
        errors.append("pickle appears in profiling adapter")
    if not re.search(r"class\s+DataProfilerAdapter\b", profiling_text):
        errors.append("DataProfilerAdapter is missing")
    if "PublicationState.COMPLETE" not in profiling_text or "content hash" not in profiling_text:
        errors.append("profiling adapter does not verify complete content-hashed batches")
    if "BATCH_INTEGRITY_FAILED" not in profiling_text:
        errors.append("batch integrity failure semantics are missing")
    if "deterministic_reservoir_v1" not in profiling_text:
        errors.append("deterministic sampling provenance is missing")
    if "report({" not in profiling_text:
        errors.append("DataProfiler report boundary is not explicit")
    if (ROOT / "research/oss/DataProfiler").exists():
        errors.append("DataProfiler research clone remains after finalization")

    if errors:
        print("FAIL")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print("PASS: project-owned profiling contracts, bounded DataProfiler adapter, privacy boundary and Step08 state verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
