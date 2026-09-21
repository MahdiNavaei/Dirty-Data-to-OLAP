"""Validate a completed Prompt02 receipt without reading raw source rows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

import duckdb
import yaml


def _commit() -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--target", type=Path)
    args = parser.parse_args()
    receipt = json.loads(args.evidence.read_text(encoding="utf-8"))
    oracle = yaml.safe_load(args.oracle.read_text(encoding="utf-8"))
    errors: list[str] = []
    if _commit() != args.expected_commit or receipt.get("content_commit") != args.expected_commit:
        errors.append("receipt and validator are not bound to the exact checked-out commit")
    if receipt.get("status") != "PASS":
        errors.append("receipt status is not PASS")
    if not receipt.get("oracle", {}).get("loaded_after_product_run"):
        errors.append("independent oracle was not loaded after the product run")
    if receipt.get("oracle", {}).get("oracle_id") != oracle.get("oracle_id"):
        errors.append("receipt oracle ID does not match the independent oracle")
    expected = oracle.get("expected", {})
    source_evidence = {item.get("source_id"): item for item in receipt.get("sources", ())}
    accounting = {item.get("source_id"): item for item in receipt.get("record_accounting", ())}
    expected_source_ids = set(expected.get("source_input_records", {}))
    if set(source_evidence) != expected_source_ids:
        errors.append("receipt source estate does not match the independent oracle")
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
    analytical = receipt.get("analytical") or {}
    if set(analytical.get("dimensions", ())) != {"dim_customer", "dim_date", "dim_source"}:
        errors.append("fact output does not contain the required three dimensions")
    if tuple(analytical.get("measures", ())) != ("quantity",):
        errors.append("quantity-only measure policy is not proven")
    if analytical.get("fact_row_count") != expected.get("fact_rows") or analytical.get("quantity_sum") != expected.get("quantity_sum"):
        errors.append("analytical aggregate does not match the independent oracle")
    if any(value != "PASS" for value in (receipt.get("negative_controls") or {}).values()) or len(receipt.get("negative_controls") or {}) != 14:
        errors.append("all fourteen negative controls are not explicitly PASS")
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
