"""Validate Step37 performance evidence without reading protected artifacts."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PROTECTED = "tests/quality_unit_artifacts"


def _fail(message: str) -> None:
    raise SystemExit("STEP37_VALIDATION_FAILED: " + message)


def _head() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def _state() -> dict[str, Any]:
    import yaml

    return yaml.safe_load((ROOT / "docs/execution/MASTER_EXECUTION_STATE.yml").read_text(encoding="utf-8"))


def validate(evidence_path: Path, report_path: Path | None, expected_commit: str | None) -> int:
    if PROTECTED in str(evidence_path).replace("\\", "/") or (report_path and PROTECTED in str(report_path).replace("\\", "/")):
        _fail("protected path incorporated")
    if not evidence_path.is_file():
        _fail("machine-readable receipt is missing")
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    required = ("schema_version", "step", "assessed_commit", "environment", "truth_fixture_links", "benchmarks", "baseline_results", "profiling_findings", "optimizations", "before_after_results", "empirical_quality_before_after", "semantic_equivalence", "memory_results", "io_results", "candidate_growth", "duckdb_results", "telemetry_overhead", "large_scale_execution_status", "ci_regression_results", "upstream_gates", "overall_result")
    missing = [key for key in required if key not in payload]
    if missing:
        _fail("missing fields: " + ",".join(missing))
    if payload["step"] != 37 or payload["schema_version"] != "1.0":
        _fail("wrong evidence schema or step")
    assessed = payload["assessed_commit"]
    if not isinstance(assessed, str) or not SHA_RE.fullmatch(assessed):
        _fail("receipt assessed_commit is not a full SHA")
    if expected_commit and assessed != expected_commit:
        _fail("receipt assessed_commit is not bound to the expected content commit")
    if report_path is not None and not report_path.is_file():
        _fail("performance report is missing")
    environment = payload["environment"]
    for key in ("os", "architecture", "python", "cpu_logical_count", "duckdb_version", "memory_measurement"):
        if environment.get(key) in (None, ""):
            _fail("environment metadata missing: " + key)
    benchmarks = payload["benchmarks"]
    if not benchmarks:
        _fail("no benchmark records")
    ids = {item.get("benchmark_id") for item in benchmarks}
    for required_id in ("PERF-E2E-001", "PERF-MAT-001", "PERF-MAT-002", "PERF-VAL-001", "PERF-QUERY-001", "PERF-QUERY-001-WARM", "PERF-TELEMETRY-001", "PERF-REG-001"):
        if required_id not in ids:
            _fail("required benchmark missing: " + required_id)
    for item in benchmarks:
        if not isinstance(item.get("wall_seconds"), (int, float)) or item["wall_seconds"] < 0:
            _fail("benchmark wall time missing units: " + str(item.get("benchmark_id")))
        memory = item.get("peak_memory")
        if not isinstance(memory, dict) or not memory.get("method"):
            _fail("memory measurement undefined: " + str(item.get("benchmark_id")))
        if PROTECTED in json.dumps(item, sort_keys=True).replace("\\", "/"):
            _fail("protected path incorporated in benchmark")
    for family in ("dependency", "schema_matching", "entity_resolution"):
        rows = payload["candidate_growth"].get(family)
        if not isinstance(rows, list) or not rows or any("status" not in row for row in rows):
            _fail("candidate evidence absent: " + family)
    if not any(item.get("details", {}).get("materialization_mode") == "PROJECT_DUCKDB_MATERIALIZER" and item.get("status") == "PASS" for item in benchmarks):
        _fail("DuckDB materialization evidence absent")
    if payload["semantic_equivalence"].get("status") != "PASS":
        _fail("semantic-equivalence result absent or failed")
    for optimization in payload["optimizations"]:
        if not isinstance(optimization, dict) or not str(optimization.get("finding_id", "")).startswith("PERF-FIND-"):
            _fail("optimization lacks PERF-FIND evidence")
        if not payload["empirical_quality_before_after"]:
            _fail("inference optimization lacks empirical-quality regression")
    if not payload["before_after_results"]:
        _fail("before/after fixture comparison missing")
    for scale, allowed in (("1M", {"EXECUTED", "NOT_EXECUTED"}), ("several-million", {"EXECUTED_LOCAL", "EXECUTED_CI", "NOT_EXECUTED"}), ("10M", {"EXECUTED_REFERENCE", "NOT_EXECUTED_OPTIONAL"}), ("100M", {"FEASIBILITY_DESIGNED", "EXECUTED_REFERENCE"})):
        if payload["large_scale_execution_status"].get(scale) not in allowed:
            _fail("unexecuted large scale claimed as executed: " + scale)
    gates = payload["upstream_gates"]
    for key in ("G6", "G7", "G8", "G9", "G10", "G11"):
        if gates.get(key) != "PASS":
            _fail("upstream gate is not PASS: " + key)
    for key in ("G12", "G13", "G14", "G15"):
        if gates.get(key) != "PENDING":
            _fail("future gate is not PENDING: " + key)
    if gates.get("blocked") is not False or gates.get("step38_started") is not False:
        _fail("Step38 or blocked state is invalid")
    state = _state()
    specialist = state.get("specialist_execution", {})
    state_gates = state.get("gates", {})
    if any(state_gates.get("G" + str(number) + suffix) != "PASS" for number, suffix in ((6, "_DATA_CORRECTNESS"), (7, "_END_TO_END_PRODUCT"), (8, "_REPRODUCIBLE_BUILD"), (9, "_FUNCTIONAL_SUPPORT"), (10, "_APPLICATION_SECURITY"), (11, "_RESILIENCE"))):
        _fail("authoritative state G6-G11 is not PASS")
    if any(state_gates.get(key) != "PENDING" for key in ("G12_CAPACITY", "G13_ADVERSARIAL_SECURITY", "G14_USABILITY", "G15_RELEASE")):
        _fail("authoritative state G12-G15 is not PENDING")
    if state.get("blocked") is not False:
        _fail("authoritative blocked state is not false")
    if specialist.get("current_step") == 37:
        if specialist.get("step37_started") is not False or specialist.get("step37_status") != "NOT_STARTED":
            _fail("content-phase Step37 state changed prematurely")
    elif specialist.get("current_step") == 38:
        receipt = specialist.get("step37_performance", {})
        if specialist.get("last_completed_step") != 37 or specialist.get("last_completed_role") != "performance_engineer" or specialist.get("current_role") != "load_stress" or specialist.get("step38_started") is not False or specialist.get("step38_status") != "NOT_STARTED" or receipt.get("content_commit") != assessed:
            _fail("final Step37 to Step38 handoff is inconsistent")
    else:
        _fail("unsupported authoritative current step")
    if payload.get("overall_result") != "PASS":
        _fail("documentation-only or non-PASS overall receipt")
    print(json.dumps({"status": "PASS", "benchmarks": len(benchmarks), "assessed_commit": assessed, "authoritative_current_step": specialist.get("current_step"), "protected_path": "NOT_USED"}, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", default=str(ROOT / "output" / "step37_performance_validation.json"))
    parser.add_argument("--report")
    parser.add_argument("--expected-commit")
    parser.add_argument("--ci", action="store_true")
    args = parser.parse_args()
    return validate(Path(args.evidence).resolve(), Path(args.report).resolve() if args.report else None, args.expected_commit)


if __name__ == "__main__":
    raise SystemExit(main())
