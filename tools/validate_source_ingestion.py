"""Deterministic Step07 source-ingestion boundary checks."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "dirty_data_to_olap"


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def main() -> int:
    errors: list[str] = []
    required = {
        "src/dirty_data_to_olap/application/source_adapter.py",
        "src/dirty_data_to_olap/application/discovery.py",
        "src/dirty_data_to_olap/application/snapshot.py",
        "src/dirty_data_to_olap/application/source_registry.py",
        "src/dirty_data_to_olap/domain/contracts/source.py",
        "src/dirty_data_to_olap/adapters/sources/files.py",
        "src/dirty_data_to_olap/adapters/sources/staging.py",
        "src/dirty_data_to_olap/adapters/sources/sql/dlt_sql.py",
        "docs/data-engineering/SOURCE_ADAPTER_CONTRACT.md",
        "docs/data-engineering/INGESTION_AND_STAGING.md",
        "docs/data-engineering/SOURCE_SUPPORT_MATRIX.md",
        "docs/execution/gates/G3A_SOURCE_USABILITY.md",
        "docs/oss/REUSE_RESEARCH_LEDGER.md",
    }
    for relative in required:
        if not (ROOT / relative).is_file():
            fail(errors, f"missing required Step07 artifact: {relative}")

    state = yaml.safe_load((ROOT / "docs/execution/MASTER_EXECUTION_STATE.yml").read_text(encoding="utf-8"))
    specialist = state["specialist_execution"]
    gates = state["gates"]
    if not ((specialist["current_step"] == 7 and specialist["last_completed_step"] == 6) or (specialist["current_step"] == 8 and specialist["last_completed_step"] == 7) or (specialist["current_step"] == 9 and specialist["last_completed_step"] == 8) or (specialist["current_step"] == 10 and specialist["last_completed_step"] == 9) or (specialist["current_step"] == 11 and specialist["last_completed_step"] == 10) or (specialist["current_step"] == 12 and specialist["last_completed_step"] == 11) or (specialist["current_step"] == 13 and specialist["last_completed_step"] == 12) or (specialist["current_step"] == 14 and specialist["last_completed_step"] == 13) or (specialist["current_step"] == 15 and specialist["last_completed_step"] == 14) or (specialist["current_step"] == 16 and specialist["last_completed_step"] == 15) or (specialist["current_step"] == 17 and specialist["last_completed_step"] == 16) or (specialist["current_step"] == 18 and specialist["last_completed_step"] == 17) or (specialist["current_step"] == 19 and specialist["last_completed_step"] == 18)):
        fail(errors, "execution state is not a valid Step07 implementation or later specialist handoff")
    if gates.get("G3_SOURCE_SAFETY") not in {"PENDING", "PASS", "BLOCKED"}:
        fail(errors, "formal G3 Source Safety state is invalid")
    if not (ROOT / "research" / "oss" / "dlt").exists():
        pass
    else:
        fail(errors, "dlt research clone remains after deletion verification")

    sys.path.insert(0, str(ROOT / "src"))
    try:
        from dirty_data_to_olap.application.source_adapter import SourceAdapter
        from dirty_data_to_olap.domain.contracts.source import BatchReference, SourceCatalog, SourceRecordReference, SourceSnapshot
    except Exception as exception:
        fail(errors, f"project source contracts cannot import: {exception.__class__.__name__}")
    else:
        public_port = {name for name in dir(SourceAdapter) if not name.startswith("_")}
        if not {"discover_source", "create_bounded_snapshot"} <= public_port:
            fail(errors, "SourceAdapter does not expose both required operations")
        for contract in (BatchReference, SourceCatalog, SourceRecordReference, SourceSnapshot):
            if "schema_version" not in contract.model_fields:
                fail(errors, f"{contract.__name__} has no schema_version field")

    source_text = "\n".join(path.read_text(encoding="utf-8") for path in SRC.rglob("*.py"))
    if "research/oss" in source_text.replace("\\", "/"):
        fail(errors, "production source contains a research clone path")
    if "pickle" in source_text.lower():
        fail(errors, "production source contains pickle persistence")
    if re.search(r"(?m)^\s*def\s+(execute|run_any_query)\s*\(", source_text):
        fail(errors, "uncontrolled arbitrary query API exists")
    for root in (SRC / "domain", SRC / "application"):
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [alias.name for alias in node.names]
                    module = node.module if isinstance(node, ast.ImportFrom) else ""
                    if any(name in {"dlt", "sqlalchemy", "pyarrow", "openpyxl"} or name.startswith(("dlt.", "sqlalchemy.", "pyarrow.", "openpyxl.")) for name in names + [module]):
                        fail(errors, f"vendor import outside concrete adapter: {path}")

    if errors:
        print("FAIL")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print("PASS: source contracts, adapter boundaries, staging artifacts, research deletion and G3 state verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
