"""Validate Step38 load/stress evidence and the authoritative handoff."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
HEX40 = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_PATTERNS = {"STEADY", "RAMP", "BURST", "OVERLOAD"}
REQUIRED_CONTROLS = {
    "queue_depth", "queue_wait_latency", "control_store_contention", "source_read_only_protection",
    "provider_concurrency", "backpressure", "artifact_staging_concurrency", "multi_run_isolation",
    "review_pause_resume", "cancellation", "controlled_overload_recovery", "bounded_soak_memory",
    "state_machine_invariants", "at_least_once_replay_safety", "g6_concurrent_correctness", "g11_regression",
}


def fail(message: str) -> None:
    raise SystemExit(f"STEP38_VALIDATION_FAILED: {message}")


def head() -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    return result.stdout.strip()


def validate(evidence_path: Path, report_path: Path | None = None, expected_commit: str | None = None) -> int:
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"evidence unreadable: {error.__class__.__name__}")
    if evidence.get("schema_version") != "1.0" or evidence.get("step") != 38:
        fail("wrong evidence schema or step")
    assessed = evidence.get("assessed_commit")
    if not isinstance(assessed, str) or not HEX40.fullmatch(assessed):
        fail("assessed_commit is not a full Git commit")
    if expected_commit and assessed != expected_commit:
        fail("evidence is not bound to expected commit")
    if report_path is not None and not report_path.is_file():
        fail("report is missing")
    if evidence.get("overall_result") != "PASS" or evidence.get("status") != "PASS":
        fail("overall load/stress result is not PASS")
    environment = evidence.get("environment")
    for key in ("os", "architecture", "python", "cpu_logical_count", "api_process", "control_store", "source_caps", "queue_limits", "git_head"):
        if not environment or key not in environment:
            fail(f"environment receipt missing {key}")
    patterns = evidence.get("arrival_patterns", {})
    if not REQUIRED_PATTERNS <= set(patterns):
        fail("required arrival patterns are incomplete")
    for name in REQUIRED_PATTERNS:
        if patterns[name].get("latency", {}).get("sample_count", 0) <= 0 and name != "OVERLOAD":
            fail(f"arrival pattern has no latency samples: {name}")
    ramp = evidence.get("worker_ramp", {}).get("ramp", [])
    if [item.get("worker_count") for item in ramp] != [1, 2, 4, 8]:
        fail("worker ramp is not exactly 1/2/4/8")
    if any(item.get("successful_jobs") != item.get("jobs") for item in ramp):
        fail("worker ramp contains failed jobs")
    controls = evidence.get("controls", {})
    if REQUIRED_CONTROLS != set(controls):
        fail("required control set is incomplete")
    if any(not bool(value.get("pass")) for value in controls.values()):
        fail("one or more required controls did not pass")
    api = evidence.get("api_real_listener", {})
    if api.get("real_listener") is not True or api.get("submission_concurrency") != 20 or api.get("runs_prepared") != 20:
        fail("real listener did not execute twenty prepared submissions")
    if api.get("submissions", {}).get("successful_requests") != 20:
        fail("not all twenty real API submissions were accepted")
    if len(api.get("g6_successes", [])) < 2:
        fail("concurrent G6 correctness evidence has fewer than two PASS runs")
    if evidence.get("safe_operating_point", {}).get("production_claim") is not False:
        fail("safe operating point is not explicitly local-only")
    if evidence.get("first_observed_saturation_region", {}).get("breakpoint") not in {"NOT_REACHED_WITHIN_SAFE_TEST_BOUND", "MEASURED_BOUNDARY"}:
        fail("saturation boundary is not explicit")
    claims = evidence.get("claims", {})
    for key in ("exactly_once", "production_capacity", "production_source_stress", "public_sla"):
        if claims.get(key) is not False:
            fail(f"forbidden overclaim flag is not false: {key}")
    if claims.get("step39_started") is not False:
        fail("Step39 was started")
    if evidence.get("protected_quality_artifacts") != "unread, untouched, unstaged and uncommitted":
        fail("protected artifact declaration is not preserved")
    gates = evidence.get("upstream_gates", {})
    if any(gates.get(key) != "PASS" for key in ("G6", "G7", "G8", "G9", "G10", "G11", "G12")):
        fail("G6-G12 gate receipt is not PASS")
    if any(gates.get(key) != "PENDING" for key in ("G13", "G14", "G15")):
        fail("later gates were advanced prematurely")
    state = yaml.safe_load((ROOT / "docs/execution/MASTER_EXECUTION_STATE.yml").read_text(encoding="utf-8"))
    specialist = state.get("specialist_execution", {})
    if state.get("blocked") is not False:
        fail("authoritative state is blocked")
    expected_state = {"last_completed_step": 38, "last_completed_role": "load_stress", "current_step": 39, "current_role": "penetration_red_team", "step38_started": True, "step38_status": "COMPLETED_LOAD_STRESS_G12_PASS", "step39_started": False, "step39_status": "NOT_STARTED"}
    for key, value in expected_state.items():
        if specialist.get(key) != value:
            fail(f"authoritative state mismatch for {key}: {specialist.get(key)!r}")
    state_gates = state.get("gates", {})
    if state_gates.get("G12_CAPACITY") != "PASS" or any(state_gates.get(key) != "PENDING" for key in ("G13_ADVERSARIAL_SECURITY", "G14_USABILITY", "G15_RELEASE")):
        fail("authoritative gate state mismatch")
    print(json.dumps({"status": "PASS", "step": 38, "assessed_commit": assessed, "current_step": specialist.get("current_step"), "g12": state_gates.get("G12_CAPACITY")}, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", default=str(ROOT / "output" / "step38_load_stress_validation.json"))
    parser.add_argument("--report")
    parser.add_argument("--expected-commit")
    args = parser.parse_args()
    return validate(Path(args.evidence).resolve(), Path(args.report).resolve() if args.report else None, args.expected_commit)


if __name__ == "__main__":
    raise SystemExit(main())
