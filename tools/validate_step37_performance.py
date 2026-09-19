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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.execution_state import step40_g14_closed
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PROTECTED = "tests/quality_unit_artifacts"


def _fail(message: str) -> None:
    raise SystemExit("STEP37_VALIDATION_FAILED: " + message)


def _head() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def _parent_head() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD^"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def _state() -> dict[str, Any]:
    import yaml

    return yaml.safe_load((ROOT / "docs/execution/MASTER_EXECUTION_STATE.yml").read_text(encoding="utf-8"))


def validate(evidence_path: Path, report_path: Path | None, expected_commit: str | None) -> int:
    if PROTECTED in str(evidence_path).replace("\\", "/") or (report_path and PROTECTED in str(report_path).replace("\\", "/")):
        _fail("protected path incorporated")
    state = _state()
    specialist = state.get("specialist_execution", {})
    if not evidence_path.is_file():
        if (
            report_path is None
            and specialist.get("current_step") == 37
            and specialist.get("step37_started") is False
            and specialist.get("step37_status") == "NOT_STARTED"
        ):
            print(json.dumps({"status": "PASS", "step37_execution": "NOT_STARTED", "protected_path": "NOT_USED"}, sort_keys=True))
            return 0
        _fail("machine-readable receipt is missing")
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    required = ("schema_version", "step", "assessed_commit", "content_commit", "environment", "datasets", "truth_fixture_links", "benchmarks", "baseline_results", "profiling_findings", "optimizations", "before_after_results", "empirical_quality_before_after", "semantic_equivalence", "memory_results", "io_results", "candidate_growth", "provider_stage_baselines", "medium_benchmark", "duckdb_results", "telemetry_overhead", "large_scale_execution_status", "large_scale_evidence", "ci_regression_results", "upstream_gates", "overall_result")
    missing = [key for key in required if key not in payload]
    if missing:
        _fail("missing fields: " + ",".join(missing))
    if payload["step"] != 37 or payload["schema_version"] != "1.0":
        _fail("wrong evidence schema or step")
    assessed = payload["assessed_commit"]
    if not isinstance(assessed, str) or not SHA_RE.fullmatch(assessed):
        _fail("receipt assessed_commit is not a full SHA")
    if payload.get("content_commit") != assessed:
        _fail("receipt content_commit is not bound to assessed_commit")
    if expected_commit and assessed != expected_commit:
        _fail("receipt assessed_commit is not bound to the expected content commit")
    if expected_commit and _head() != expected_commit:
        _fail("expected commit is not the checked-out HEAD")
    if report_path is not None and not report_path.is_file():
        _fail("performance report is missing")
    truth_paths = {item.get("path") for item in payload["truth_fixture_links"] if isinstance(item, dict)}
    required_truth_paths = {
        "benchmarks/inference_evaluation/relationship_truth.json",
        "benchmarks/inference_evaluation/schema_truth.json",
        "benchmarks/inference_evaluation/entity_truth.json",
        "benchmarks/validation/step22_retail_source_truth.json",
        "benchmarks/schema_matching/step13_labeled_fixture.json",
        "benchmarks/entity_resolution/step14_labeled_fixture.json",
    }
    if not required_truth_paths.issubset(truth_paths):
        _fail("required truth fixture links are missing")
    environment = payload["environment"]
    for key in ("os", "architecture", "python", "cpu_logical_count", "duckdb_version", "memory_measurement"):
        if environment.get(key) in (None, ""):
            _fail("environment metadata missing: " + key)
    datasets = payload["datasets"]
    if not isinstance(datasets, list) or not any(item.get("scale_class") == "Medium" and item.get("status") == "EXECUTED_PRODUCTION_BOUNDARY" and isinstance(item.get("row_count"), int) and item["row_count"] >= 100_000 for item in datasets):
        _fail("datasets do not contain an executed meaningful Medium boundary")
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
    by_id = {item.get("benchmark_id"): item for item in benchmarks}
    e2e = by_id.get("PERF-E2E-001")
    if not isinstance(e2e, dict) or e2e.get("status") != "PASS":
        _fail("real product E2E is not PASS")
    e2e_details = e2e.get("details", {})
    if (
        e2e_details.get("terminal_status") != "SUCCEEDED"
        or e2e_details.get("g6_status") != "PASS"
        or e2e_details.get("g6_eligible") is not True
        or e2e_details.get("validated_output") is not True
        or not e2e_details.get("accepted_review_checkpoints")
        or e2e_details.get("provider_source_revision") != "b211961f3f272ed8815ef1ffbda90573b11e1116"
        or not isinstance(e2e_details.get("provider_image_id"), str)
        or not e2e_details["provider_image_id"].startswith("sha256:")
        or e2e_details.get("network_allowed") is not False
        or e2e_details.get("container_read_only") is not True
        or e2e_details.get("input_mount") != "bind-readonly:/input"
    ):
        _fail("E2E lacks terminal SUCCEEDED, G6 validation, validated output or pinned provider proof")
    medium = by_id.get("PERF-MEDIUM-001")
    if not isinstance(medium, dict) or medium.get("status") != "PASS":
        _fail("executed Medium benchmark is absent or not PASS")
    if payload.get("medium_benchmark") != medium:
        _fail("medium_benchmark summary is not bound to the benchmark record")
    medium_details = medium.get("details", {})
    if medium_details.get("scale_class") != "Medium" or not isinstance(medium_details.get("row_count"), int) or medium_details["row_count"] < 100_000:
        _fail("Medium benchmark is missing a meaningful row-count bound")
    if (
        not isinstance(medium_details.get("input_bytes"), int)
        or medium_details["input_bytes"] <= 0
        or not isinstance(medium_details.get("staged_bytes"), int)
        or medium_details["staged_bytes"] <= 0
        or not isinstance(medium_details.get("output_db_bytes"), int)
        or medium_details["output_db_bytes"] <= 0
        or medium_details.get("provider_boundary") != "desbordante-docker"
        or medium_details.get("provider_source_revision") != "b211961f3f272ed8815ef1ffbda90573b11e1116"
        or not isinstance(medium_details.get("provider_image_id"), str)
        or not medium_details["provider_image_id"].startswith("sha256:")
        or medium_details.get("network_allowed") is not False
        or medium_details.get("container_read_only") is not True
        or medium_details.get("input_mount") != "bind-readonly:/input"
    ):
        _fail("Medium lacks real provider, I/O or read-only boundary evidence")
    medium_oracle = medium_details.get("semantic_oracle", {})
    if (
        medium_oracle.get("status") != "PASS"
        or medium_oracle.get("expected_fact_rows") != medium_details["row_count"]
        or medium_oracle.get("observed_fact_rows") != medium_details["row_count"]
        or medium_oracle.get("expected_quantity_sum") != medium_oracle.get("observed_quantity_sum")
    ):
        _fail("Medium semantic oracle is absent or inconsistent")
    medium_stages = medium_details.get("stages", {})
    required_medium_stages = ("source_discovery", "extraction_staging", "profiling", "dependency_candidate_generation", "duckdb_materialization", "validation")
    if any(stage not in medium_stages or medium_stages[stage].get("status") != "PASS" or medium_stages[stage].get("wall_seconds", 0) <= 0 for stage in required_medium_stages):
        _fail("Medium does not contain PASS stage-level measurements")
    dependency_search = medium_details.get("dependency_search", {})
    if dependency_search.get("completeness") != "COMPLETE" or dependency_search.get("provider_calls", 0) <= 0 or dependency_search.get("runtime_timeout") is not False:
        _fail("Medium dependency candidate generation is incomplete or unexecuted")
    stage_baselines = payload["provider_stage_baselines"]
    if not isinstance(stage_baselines, dict) or set(stage_baselines) != {"dependency", "schema_matching", "entity_resolution"}:
        _fail("required independent provider stage baselines are absent")
    for stage_name, stage_receipt in stage_baselines.items():
        if not isinstance(stage_receipt, dict) or stage_receipt.get("step") != 37 or stage_receipt.get("status") != "PASS" or stage_receipt.get("wall_seconds", 0) <= 0:
            _fail("provider stage baseline is not an executed PASS: " + stage_name)
        stage_details = stage_receipt.get("details", {})
        if not stage_details.get("integration") or stage_details.get("execution_boundary") != "project-owned adapter and service":
            _fail("provider stage baseline is not bound to a project adapter: " + stage_name)
        if stage_name == "dependency":
            if (
                not isinstance(stage_details.get("provider_image_id"), str)
                or not stage_details["provider_image_id"].startswith("sha256:")
                or stage_details.get("provider_source_revision") != "b211961f3f272ed8815ef1ffbda90573b11e1116"
                or stage_details.get("network_allowed") is not False
                or stage_details.get("container_read_only") is not True
                or stage_details.get("input_mount") != "bind-readonly:/input"
            ):
                _fail("dependency baseline lacks pinned read-only provider proof")
    for family, provider in (("dependency", "desbordante"), ("schema_matching", "valentine"), ("entity_resolution", "splink")):
        rows = payload["candidate_growth"].get(family)
        if any(row.get("status") != "EXECUTED" or provider not in str(row.get("provider", "")).lower() for row in rows):
            _fail("candidate growth is not an executed provider result: " + family)
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
    for scale, allowed in (("Tiny", {"EXECUTED_REFERENCE_ONLY"}), ("Medium", {"EXECUTED"}), ("1M", {"EXECUTED", "NOT_EXECUTED"}), ("several-million", {"EXECUTED_LOCAL", "EXECUTED_CI", "NOT_EXECUTED"}), ("10M", {"EXECUTED_REFERENCE", "NOT_EXECUTED_OPTIONAL"}), ("100M", {"FEASIBILITY_DESIGNED", "EXECUTED_REFERENCE"})):
        if payload["large_scale_execution_status"].get(scale) not in allowed:
            _fail("unexecuted large scale claimed as executed: " + scale)
    for scale in ("1M", "several-million", "10M", "100M"):
        status = payload["large_scale_execution_status"].get(scale)
        evidence = payload["large_scale_evidence"].get(scale)
        if not isinstance(evidence, dict) or not str(evidence.get("reason", "")).strip() or evidence.get("status") != status:
            _fail("large-scale feasibility/status evidence is missing: " + scale)
        if str(status).startswith("EXECUTED") and not evidence.get("result"):
            _fail("large-scale execution is claimed without a result: " + scale)
    ci_result = payload["ci_regression_results"]
    if ci_result.get("status") not in {"LOCAL_EXECUTED", "CI_EXECUTED"} or (ci_result.get("status") == "CI_EXECUTED" and not ci_result.get("run_id")):
        _fail("CI/local regression receipt is not explicit")
    gates = payload["upstream_gates"]
    for key in ("G6", "G7", "G8", "G9", "G10", "G11"):
        if gates.get(key) != "PASS":
            _fail("upstream gate is not PASS: " + key)
    for key in ("G12", "G13", "G14", "G15"):
        if gates.get(key) != "PENDING":
            _fail("future gate is not PENDING: " + key)
    if gates.get("blocked") is not False or gates.get("step38_started") is not False:
        _fail("Step38 or blocked state is invalid")
    state_gates = state.get("gates", {})
    if any(state_gates.get("G" + str(number) + suffix) != "PASS" for number, suffix in ((6, "_DATA_CORRECTNESS"), (7, "_END_TO_END_PRODUCT"), (8, "_REPRODUCIBLE_BUILD"), (9, "_FUNCTIONAL_SUPPORT"), (10, "_APPLICATION_SECURITY"), (11, "_RESILIENCE"))):
        _fail("authoritative state G6-G11 is not PASS")
    if specialist.get("current_step") == 41:
        if not step40_g14_closed(state) or state_gates.get("G12_CAPACITY") != "PASS" or state_gates.get("G13_ADVERSARIAL_SECURITY") != "PASS" or state_gates.get("G14_USABILITY") != "PASS" or state_gates.get("G15_RELEASE") != "PENDING":
            _fail("authoritative state G12-G15 is not a valid Step40/G14 closure")
        if specialist.get("step40_started") is not True or specialist.get("step40_status") != "COMPLETED_DEVELOPER_EXPERIENCE_G14_PASS":
            _fail("authoritative Step40 closure is incomplete")
    elif specialist.get("current_step") == 40:
        if state_gates.get("G12_CAPACITY") != "PASS" or state_gates.get("G13_ADVERSARIAL_SECURITY") != "PASS" or any(state_gates.get(key) != "PENDING" for key in ("G14_USABILITY", "G15_RELEASE")):
            _fail("authoritative state G12-G15 is not a valid Step39 closure")
    elif specialist.get("current_step") == 39:
        if state_gates.get("G12_CAPACITY") != "PASS" or any(state_gates.get(key) != "PENDING" for key in ("G13_ADVERSARIAL_SECURITY", "G14_USABILITY", "G15_RELEASE")):
            _fail("authoritative state G12-G15 is not a valid Step38 closure")
    elif any(state_gates.get(key) != "PENDING" for key in ("G12_CAPACITY", "G13_ADVERSARIAL_SECURITY", "G14_USABILITY", "G15_RELEASE")):
        _fail("authoritative state G12-G15 is not PENDING")
    if state.get("blocked") is not False:
        _fail("authoritative blocked state is not false")
    if specialist.get("current_step") == 37:
        if specialist.get("step37_started") is not False or specialist.get("step37_status") != "NOT_STARTED":
            _fail("content-phase Step37 state changed prematurely")
    elif specialist.get("current_step") == 38:
        receipt = specialist.get("step37_performance", {})
        if (
            specialist.get("last_completed_step") != 37
            or specialist.get("last_completed_role") != "performance_engineer"
            or specialist.get("current_role") != "load_stress"
            or not (
                (specialist.get("step38_started") is False and specialist.get("step38_status") == "NOT_STARTED")
                or (
                    specialist.get("step38_started") is True
                    and specialist.get("step38_status") == "INCOMPLETE_FULL_REGRESSION_BLOCKER"
                    and state_gates.get("G12_CAPACITY") == "PENDING"
                )
            )
            or (
                receipt.get("content_commit") != specialist.get("last_completed_content_commit")
                and assessed not in {_head(), _parent_head()}
            )
        ):
            _fail("final Step37 to Step38 handoff is inconsistent")
    elif specialist.get("current_step") == 40:
        if state_gates.get("G12_CAPACITY") != "PASS" or state_gates.get("G13_ADVERSARIAL_SECURITY") != "PASS" or any(state_gates.get(key) != "PENDING" for key in ("G14_USABILITY", "G15_RELEASE")):
            _fail("authoritative state G12-G15 is not a valid Step39 closure")
        if specialist.get("step39_started") is not True or specialist.get("step39_status") != "COMPLETED_RED_TEAM_G13_PASS":
            _fail("authoritative Step39 closure is incomplete")
    elif specialist.get("current_step") == 41:
        if not step40_g14_closed(state) or state_gates.get("G12_CAPACITY") != "PASS" or state_gates.get("G13_ADVERSARIAL_SECURITY") != "PASS" or state_gates.get("G14_USABILITY") != "PASS" or state_gates.get("G15_RELEASE") != "PENDING":
            _fail("authoritative state G12-G15 is not a valid Step40 closure")
        if specialist.get("step40_started") is not True or specialist.get("step40_status") != "COMPLETED_DEVELOPER_EXPERIENCE_G14_PASS":
            _fail("authoritative Step40 closure is incomplete")
    elif specialist.get("current_step") == 39:
        receipt = specialist.get("step38_load_stress", {})
        if (
            specialist.get("last_completed_step") != 38
            or specialist.get("last_completed_role") != "load_stress"
            or specialist.get("current_role") != "penetration_red_team"
            or specialist.get("step38_started") is not True
            or specialist.get("step38_status") != "COMPLETED_LOAD_STRESS_G12_PASS"
            or specialist.get("step39_started") is not False
            or specialist.get("step39_status") != "NOT_STARTED"
            or receipt.get("status") != "PASS"
        ):
            _fail("final Step38 to Step39 handoff is inconsistent")
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
