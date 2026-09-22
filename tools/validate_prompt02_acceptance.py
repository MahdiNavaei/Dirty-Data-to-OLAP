"""Validate a completed Prompt02 receipt without reading raw source rows."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import duckdb
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dirty_data_to_olap.domain.contracts.multi_source import MultiSourceAcceptanceReceipt


def _commit() -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--oracle", type=Path)
    parser.add_argument("--expected-commit")
    parser.add_argument("--negative-control-evidence", type=Path)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--require-evidence", action="store_true", help="fail when a completed acceptance receipt is not supplied")
    args = parser.parse_args()
    supplied = (args.evidence, args.oracle, args.expected_commit, args.negative_control_evidence)
    if not all(supplied):
        if args.require_evidence:
            parser.error("--evidence, --negative-control-evidence, --oracle, and --expected-commit are required with --require-evidence")
        print("PROMPT02_ACCEPTANCE=NOT_RUN")
        print("reason=no completed Prompt02 receipt supplied")
        return 0
    receipt = json.loads(args.evidence.read_text(encoding="utf-8"))
    negative_execution = json.loads(args.negative_control_evidence.read_text(encoding="utf-8"))
    oracle = yaml.safe_load(args.oracle.read_text(encoding="utf-8"))
    errors: list[str] = []
    try:
        typed_receipt = MultiSourceAcceptanceReceipt.model_validate(receipt)
    except ValueError as exc:
        typed_receipt = None
        errors.append(f"receipt does not satisfy the typed acceptance contract: {exc}")
    if _commit() != args.expected_commit or receipt.get("content_commit") != args.expected_commit:
        errors.append("receipt and validator are not bound to the exact checked-out commit")
    if receipt.get("status") != "PASS":
        errors.append("receipt status is not PASS")
    if not receipt.get("oracle", {}).get("loaded_after_product_run"):
        errors.append("independent oracle was not loaded after the product run")
    if receipt.get("oracle", {}).get("oracle_id") != oracle.get("oracle_id"):
        errors.append("receipt oracle ID does not match the independent oracle")
    if not receipt.get("run_id") or not receipt.get("source_set_fingerprint"):
        errors.append("receipt is missing durable run or source-set identity")
    expected = oracle.get("expected", {})
    source_evidence = {item.get("source_id"): item for item in receipt.get("sources", ())}
    accounting = {item.get("source_id"): item for item in receipt.get("record_accounting", ())}
    expected_source_ids = set(expected.get("source_input_records", {}))
    if set(source_evidence) != expected_source_ids:
        errors.append("receipt source estate does not match the independent oracle")
    if any(not item.get("snapshot_id") or not item.get("snapshot_fingerprint") or not item.get("schema_fingerprint") or not item.get("source_unchanged_before_after") for item in source_evidence.values()):
        errors.append("receipt source evidence is not snapshot-bound and immutable")
    for source_id, expected_count in expected.get("source_input_records", {}).items():
        if source_evidence.get(source_id, {}).get("input_records") != expected_count:
            errors.append(f"input accounting mismatch for {source_id}")
    for source_id, expected_count in expected.get("source_emitted_records", {}).items():
        if accounting.get(source_id, {}).get("emitted_records") != expected_count:
            errors.append(f"emitted accounting mismatch for {source_id}")
    stages = {item.get("stage_id") for item in receipt.get("stages", ())}
    required_stages = {"SOURCE_DISCOVERY", "SOURCE_SNAPSHOT_STAGE", "SCHEMA_MATCHING", "ENTITY_RESOLUTION", "EVIDENCE_FUSION", "CANONICAL_FINALIZATION", "ANALYTICAL_PLANNING", "MATERIALIZATION", "VALIDATION_RECONCILIATION"}
    if not required_stages.issubset(stages):
        errors.append("required Prompt02 stages are missing from the receipt")
    for item in receipt.get("stages", ()):
        if item.get("stage_id") in required_stages and (item.get("status") != "SUCCEEDED" or item.get("attempts", 0) < 1 or not item.get("artifact_refs")):
            errors.append(f"stage evidence is not a durable successful attempt: {item.get('stage_id')}")
    reviews = receipt.get("reviews", ())
    if len(reviews) < 4 or any(item.get("decision") != "ACCEPTED" or not item.get("subject_id") or not item.get("actor") for item in reviews):
        errors.append("required durable review decisions are absent or incomplete")
    analytical = receipt.get("analytical") or {}
    if set(analytical.get("dimensions", ())) != {"dim_customer", "dim_date", "dim_source"}:
        errors.append("fact output does not contain the required three dimensions")
    if tuple(analytical.get("measures", ())) != ("quantity",):
        errors.append("quantity-only measure policy is not proven")
    if analytical.get("fact_row_count") != expected.get("fact_rows") or analytical.get("quantity_sum") != expected.get("quantity_sum"):
        errors.append("analytical aggregate does not match the independent oracle")
    if any(value != "PASS" for value in (receipt.get("negative_controls") or {}).values()) or len(receipt.get("negative_controls") or {}) != 14:
        errors.append("all fourteen negative controls are not explicitly PASS")
    control_evidence = receipt.get("negative_control_evidence") or ()
    expected_controls = {f"NC{i:02d}" for i in range(1, 15)}
    actual_controls = {item.get("control_id") for item in control_evidence}
    if actual_controls != expected_controls or len(control_evidence) != 14:
        errors.append("negative-control evidence does not contain exactly NC01-NC14")
    if any(item.get("implementation_status") != "IMPLEMENTED" or item.get("execution_status") != "PASS" or item.get("acceptance_status") != "PASS" or not item.get("injected_fault") or not item.get("execution_boundary") or not item.get("actual_rejection") or not item.get("durable_evidence") for item in control_evidence):
        errors.append("an unexecuted or structurally incomplete negative control is presented as acceptance evidence")
    executed_controls = negative_execution.get("controls", ())
    executed_by_id = {item.get("control_id"): item for item in executed_controls}
    if set(executed_by_id) != expected_controls or len(executed_controls) != 14:
        errors.append("negative-control execution result does not contain exactly NC01-NC14")
    for item in control_evidence:
        executed = executed_by_id.get(item.get("control_id"), {})
        if any(executed.get(field) != item.get(field) for field in ("requirement", "injected_fault", "execution_boundary", "expected_rejection", "actual_rejection", "implementation_status", "execution_status", "acceptance_status")):
            errors.append(f"negative-control receipt row is not identical to executed evidence: {item.get('control_id')}")
            continue
        if executed.get("pytest_return_code") != 0 or not executed.get("test_node"):
            errors.append(f"negative-control test did not pass: {item.get('control_id')}")
            continue
        junit_refs = [ref.removeprefix("junit:") for ref in executed.get("durable_evidence", ()) if isinstance(ref, str) and ref.startswith("junit:")]
        if len(junit_refs) != 1:
            errors.append(f"negative-control evidence is missing its JUnit result: {item.get('control_id')}")
            continue
        junit_path = args.negative_control_evidence.parent / junit_refs[0]
        try:
            root = ET.parse(junit_path).getroot()
            suites = (root,) if root.tag == "testsuite" else tuple(root.findall("testsuite"))
            passed = bool(suites) and all(int(suite.attrib.get("tests", "0")) == 1 and int(suite.attrib.get("failures", "0")) == 0 and int(suite.attrib.get("errors", "0")) == 0 and int(suite.attrib.get("skipped", "0")) == 0 for suite in suites)
        except (ET.ParseError, OSError, ValueError):
            passed = False
        if not passed:
            errors.append(f"negative-control JUnit result is not a single passing test: {item.get('control_id')}")
    if typed_receipt is None or typed_receipt.status != "PASS":
        errors.append("typed receipt status is not PASS")
    if not all(item.get("disposition_complete") for item in receipt.get("record_accounting", ())):
        errors.append("record accounting is incomplete")
    target = args.target or (Path.cwd() / Path(receipt.get("materialization", {}).get("duckdb_relative_path", "")))
    if not target or not target.is_file():
        errors.append("materialized DuckDB target is unavailable")
    else:
        connection = duckdb.connect(str(target), read_only=True)
        try:
            tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
            if tables != set(expected.get("target_tables", ())):
                errors.append("materialized table set does not match the independent oracle")
            fact_count = int(connection.execute("SELECT COUNT(*) FROM fact_order").fetchone()[0])
            quantity_sum = str(connection.execute("SELECT COALESCE(SUM(quantity), 0) FROM fact_order").fetchone()[0])
            if fact_count != expected.get("fact_rows") or quantity_sum != expected.get("quantity_sum"):
                errors.append("materialized DuckDB aggregates do not match the independent oracle")
            if receipt.get("materialization", {}).get("target_file_sha256") != hashlib.sha256(target.read_bytes()).hexdigest():
                errors.append("receipt target hash does not match the verified materialization")
        finally:
            connection.close()
    if "password" in json.dumps(receipt, sort_keys=True).lower() or "token" in json.dumps(receipt, sort_keys=True).lower():
        errors.append("receipt contains forbidden secret-bearing text")
    if errors:
        for error in errors:
            print("FAIL: " + error)
        return 1
    print("PROMPT02_ACCEPTANCE=PASS")
    print("content_commit=" + args.expected_commit)
    print("negative_controls=14/14")
    return 0


if __name__ == "__main__":
    sys.exit(main())
