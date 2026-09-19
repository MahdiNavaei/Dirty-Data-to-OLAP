"""Validate Step36 resilience evidence without reading protected artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

MATRIX = ROOT / "docs" / "resilience" / "step36-fault-matrix.json"
FAULT_MODEL = ROOT / "docs" / "resilience" / "fault-model.md"
RECEIPT = ROOT / "docs" / "execution" / "STEP36_CHAOS_RESILIENCE_REVIEW.md"
GATE_RECEIPT = ROOT / "docs" / "execution" / "gates" / "G11_RESILIENCE.md"
REPORT = ROOT / "output" / "step36_resilience_validation.json"
TEST_FILE = ROOT / "tests" / "chaos" / "test_step36_chaos.py"
SHA = re.compile(r"^[0-9a-f]{40}$")

REQUIRED_SCENARIOS = (
    "CHAOS-WORKER-001", "CHAOS-WORKER-002", "CHAOS-WORKER-003", "CHAOS-WORKER-004", "CHAOS-WORKER-005",
    "CHAOS-SOURCE-001", "CHAOS-SOURCE-002", "CHAOS-SOURCE-003",
    "CHAOS-ENGINE-001", "CHAOS-ENGINE-002", "CHAOS-ENGINE-003",
    "CHAOS-CTRL-001", "CHAOS-CTRL-002", "CHAOS-CTRL-003", "CHAOS-CTRL-004",
    "CHAOS-QUEUE-001", "CHAOS-QUEUE-002", "CHAOS-QUEUE-003",
    "CHAOS-DISK-001", "CHAOS-DISK-002",
    "CHAOS-ART-001", "CHAOS-ART-002", "CHAOS-ART-003",
    "CHAOS-REVIEW-001", "CHAOS-REVIEW-002", "CHAOS-CANCEL-001",
    "CHAOS-TELEM-001", "CHAOS-RECOVERY-001", "CHAOS-G11-001",
)


class ValidationFailure(RuntimeError):
    pass


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationFailure(f"{label} must be an object")
    return value


def load_state() -> dict[str, Any]:
    path = ROOT / "docs" / "execution" / "MASTER_EXECUTION_STATE.yml"
    return mapping(yaml.safe_load(path.read_text(encoding="utf-8")), "execution state")


def validate_state(state: dict[str, Any], report: dict[str, Any], *, ci: bool) -> str:
    from tools.execution_state import step35_sre_closed, step36_resilience_closed, step37_performance_closed, step38_load_stress_closed, step39_red_team_closed, step40_g14_closed

    specialist = mapping(state.get("specialist_execution"), "specialist_execution")
    gates = mapping(state.get("gates"), "gates")
    if state.get("blocked") is not False:
        raise ValidationFailure("Step36 requires blocked=false")
    if any(gates.get(name) != "PASS" for name in (
        "G6_DATA_CORRECTNESS", "G7_END_TO_END_PRODUCT", "G8_REPRODUCIBLE_BUILD",
        "G9_FUNCTIONAL_SUPPORT", "G10_APPLICATION_SECURITY",
    )):
        raise ValidationFailure("G6-G10 must remain PASS")
    if any(gates.get(name) != "PENDING" for name in (
        "G11_RESILIENCE", "G12_CAPACITY", "G13_ADVERSARIAL_SECURITY", "G14_USABILITY", "G15_RELEASE",
    )) and specialist.get("current_step") == 36:
        raise ValidationFailure("content phase must preserve G11-G15 pending")

    if specialist.get("current_step") == 36:
        if not step35_sre_closed(state):
            raise ValidationFailure("state is not the authorized Step35 -> Step36 handoff")
        if specialist.get("current_role") != "chaos_resilience" or specialist.get("step36_started") is not False or specialist.get("step36_status") != "NOT_STARTED":
            raise ValidationFailure("state is not the Step36 content phase")
        if report.get("phase") != "STEP36_CONTENT" or report.get("assessed_commit") != "PENDING_CONTENT_COMMIT":
            raise ValidationFailure("content report is not explicitly pending its content commit")
        if ci and report.get("content_ci") != "PENDING":
            raise ValidationFailure("content phase CI receipt must remain pending until the run is recorded")
        return "STEP36_CONTENT"

    if specialist.get("current_step") == 37:
        if not step36_resilience_closed(state):
            raise ValidationFailure("state is not the authorized Step36 -> Step37 handoff")
        resilience = mapping(specialist.get("step36_chaos_resilience"), "step36_chaos_resilience")
        assessed = resilience.get("content_commit")
        if report.get("phase") != "STEP36_CLOSURE" or report.get("assessed_commit") != assessed:
            raise ValidationFailure("closure report is not bound to the Step36 content commit")
        if gates.get("G11_RESILIENCE") != "PASS":
            raise ValidationFailure("closure state must set G11 PASS")
        if ci:
            current_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
            if not SHA.fullmatch(current_head):
                raise ValidationFailure("closure CI is not running at a valid repository head")
        return "STEP36_CLOSURE"

    if specialist.get("current_step") == 38:
        if not step37_performance_closed(state):
            raise ValidationFailure("state is not the authorized Step37 -> Step38 handoff")
        resilience = mapping(specialist.get("step36_chaos_resilience"), "step36_chaos_resilience")
        assessed = resilience.get("content_commit")
        if report.get("phase") != "STEP36_CLOSURE" or report.get("assessed_commit") != assessed:
            raise ValidationFailure("closure report is not bound to the Step36 content commit")
        if gates.get("G11_RESILIENCE") != "PASS":
            raise ValidationFailure("closure state must set G11 PASS")
        return "STEP36_CLOSURE"

    if specialist.get("current_step") == 39:
        if not step38_load_stress_closed(state):
            raise ValidationFailure("state is not the authorized later Step38 closure")
        resilience = mapping(specialist.get("step36_chaos_resilience"), "step36_chaos_resilience")
        assessed = resilience.get("content_commit")
        if report.get("phase") != "STEP36_CLOSURE" or report.get("assessed_commit") != assessed:
            raise ValidationFailure("closure report is not bound to the Step36 content commit")
        if gates.get("G11_RESILIENCE") != "PASS":
            raise ValidationFailure("closure state must set G11 PASS")
        return "STEP36_CLOSURE"

    if specialist.get("current_step") == 40:
        if not step39_red_team_closed(state):
            raise ValidationFailure("state is not the authorized later Step39 closure")
        resilience = mapping(specialist.get("step36_chaos_resilience"), "step36_chaos_resilience")
        assessed = resilience.get("content_commit")
        if report.get("phase") != "STEP36_CLOSURE" or report.get("assessed_commit") != assessed:
            raise ValidationFailure("closure report is not bound to the Step36 content commit")
        if gates.get("G11_RESILIENCE") != "PASS":
            raise ValidationFailure("closure state must keep G11 PASS")
        return "STEP36_CLOSURE"

    if specialist.get("current_step") == 41:
        if not step40_g14_closed(state):
            raise ValidationFailure("state is not the authorized later Step40 closure")
        resilience = mapping(specialist.get("step36_chaos_resilience"), "step36_chaos_resilience")
        assessed = resilience.get("content_commit")
        if report.get("phase") != "STEP36_CLOSURE" or report.get("assessed_commit") != assessed:
            raise ValidationFailure("closure report is not bound to the Step36 content commit")
        if gates.get("G11_RESILIENCE") != "PASS":
            raise ValidationFailure("closure state must keep G11 PASS")
        return "STEP36_CLOSURE"

    raise ValidationFailure("state is neither Step36 content phase nor authorized Step37 handoff")


def validate_matrix() -> dict[str, Any]:
    document = mapping(json.loads(MATRIX.read_text(encoding="utf-8")), "fault matrix")
    if document.get("schema_version") != "1.0" or document.get("step") != 36 or document.get("status") != "PASS":
        raise ValidationFailure("fault matrix identity/result is invalid")
    values = document.get("scenarios")
    if not isinstance(values, list) or len(values) != len(REQUIRED_SCENARIOS):
        raise ValidationFailure("fault matrix must contain exactly 29 scenarios")
    by_id = {item.get("id"): item for item in values if isinstance(item, dict)}
    if set(by_id) != set(REQUIRED_SCENARIOS):
        raise ValidationFailure("fault matrix scenario IDs do not exactly match the Step36 contract")
    required_fields = ("family", "fault_boundary", "steady_state", "fault_injection", "expected_transition", "recovery_oracle", "telemetry_oracle", "integrity_oracle", "test")
    for scenario_id in REQUIRED_SCENARIOS:
        scenario = mapping(by_id[scenario_id], scenario_id)
        if scenario.get("status") != "PASS":
            raise ValidationFailure(f"{scenario_id} is not PASS")
        if any(not isinstance(scenario.get(field), str) or not scenario[field].strip() for field in required_fields):
            raise ValidationFailure(f"{scenario_id} lacks an explicit fault/recovery/integrity contract")
        if "tests/chaos/test_step36_chaos.py::" not in scenario["test"] or "quality_unit_artifacts" in scenario["test"]:
            raise ValidationFailure(f"{scenario_id} lacks safe executable evidence")
    return {"required": len(REQUIRED_SCENARIOS), "pass": len(REQUIRED_SCENARIOS)}


def validate_documents(state: dict[str, Any]) -> dict[str, Any]:
    fault_model = FAULT_MODEL.read_text(encoding="utf-8")
    receipt = RECEIPT.read_text(encoding="utf-8")
    receipt_lower = receipt.lower()
    if not all(term in fault_model for term in ("Recovery oracle", "G11 oracle", "production", "randomized")):
        raise ValidationFailure("fault model does not state the recovery oracle and evidence boundary")
    if not all(term in receipt_lower for term in ("chaos", "g11", "step37", "protected")):
        raise ValidationFailure("Step36 receipt is incomplete")
    if state["specialist_execution"]["current_step"] == 36 and re.search(r"G11[^\n]*PASS", receipt, re.IGNORECASE):
        raise ValidationFailure("content receipt must not claim G11 PASS")
    if state["specialist_execution"]["current_step"] in {37, 38, 39, 40} and not GATE_RECEIPT.is_file():
        raise ValidationFailure("G11 gate receipt is missing from closure")
    return {"fault_model": "PASS", "receipt": "PASS"}


def validate_report(report: dict[str, Any], state: dict[str, Any], *, ci: bool) -> str:
    if report.get("schema_version") != "1.0" or report.get("step") != 36 or report.get("overall_result") != "PASS":
        raise ValidationFailure("Step36 report identity/result is invalid")
    if report.get("required_scenarios") != 29 or report.get("passed_scenarios") != 29:
        raise ValidationFailure("Step36 report must record all 29 scenarios")
    for key in ("g11_resilience", "g12_capacity", "g13_adversarial_security", "g14_usability", "g15_release"):
        expected = "PASS" if key == "g11_resilience" and state["specialist_execution"]["current_step"] in {37, 38, 39, 40, 41} else "PENDING"
        if report.get(key) != expected:
            raise ValidationFailure(f"{key} has an invalid Step36 value")
    return validate_state(state, report, ci=ci)


def run_executable_suite() -> dict[str, Any]:
    command = [sys.executable, "-m", "pytest", "tests/chaos/test_step36_chaos.py", "-q"]
    result = subprocess.run(command, cwd=ROOT, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=600, check=False)
    output = (result.stdout + "\n" + result.stderr).strip()
    if result.returncode != 0:
        raise ValidationFailure(f"Step36 chaos tests failed: {output[-3000:]}")
    match = re.search(r"(?P<passed>\d+) passed", output)
    passed = int(match.group("passed")) if match else 0
    if passed < len(REQUIRED_SCENARIOS):
        raise ValidationFailure("Step36 test command did not report 29 passing tests")
    return {"command": "python -m pytest tests/chaos/test_step36_chaos.py -q", "status": "PASS", "passed": passed}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Step36 chaos/resilience evidence")
    parser.add_argument("--ci", action="store_true", help="run executable evidence and enforce the current CI phase")
    args = parser.parse_args()
    try:
        report = mapping(json.loads(REPORT.read_text(encoding="utf-8")), "Step36 report")
        state = load_state()
        phase = validate_report(report, state, ci=args.ci)
        result = {"status": "PASS", "phase": phase, "matrix": validate_matrix(), "documents": validate_documents(state), "tests": run_executable_suite()}
    except (OSError, json.JSONDecodeError, ValidationFailure, subprocess.CalledProcessError) as error:
        print(json.dumps({"status": "FAIL", "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
