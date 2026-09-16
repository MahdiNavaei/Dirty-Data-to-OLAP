"""Fail-closed validator for the Step33 application-security receipt."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "output" / "step33_appsec_validation.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.execution_state import step32_compatibility_closed, step33_application_security_closed


REQUIRED_SCENARIOS = {
    "SEC-AUTH-001", "SEC-AUTH-002", "SEC-AUTH-003", "SEC-AUTH-004",
    "SEC-REV-001", "SEC-REV-002", "SEC-REV-003",
    "SEC-SQL-001", "SEC-SQL-002", "SEC-SQL-003",
    "SEC-NET-001", "SEC-NET-002",
    "SEC-FS-001", "SEC-FS-002", "SEC-FS-003", "SEC-FS-004",
    "SEC-DESER-001", "SEC-JOB-001", "SEC-JOB-002", "SEC-JOB-003",
    "SEC-XSS-001", "SEC-API-001", "SEC-API-002", "SEC-SEC-001", "SEC-DEP-001",
}
SHA = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)


class ValidationFailure(RuntimeError):
    """Raised when a committed Step33 receipt cannot support G10."""


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationFailure(f"{label} must be an object")
    return value


def _state() -> dict[str, Any]:
    value = yaml.safe_load((ROOT / "docs" / "execution" / "MASTER_EXECUTION_STATE.yml").read_text(encoding="utf-8"))
    return _mapping(value, "execution state")


def _validate_state(state: dict[str, Any]) -> str:
    execution = _mapping(state.get("specialist_execution"), "specialist_execution")
    gates = _mapping(state.get("gates"), "gates")
    content_phase = (
        execution.get("current_step") == 33
        and execution.get("current_role") == "application_security_engineer"
        and execution.get("last_completed_step") == 32
        and execution.get("last_completed_role") == "compatibility_test_engineer"
        and execution.get("next_step") == "Step33 - Application Security Engineer"
        and execution.get("step34_started") is not True
        and execution.get("step34_status", "NOT_STARTED") == "NOT_STARTED"
        and gates.get("G9_FUNCTIONAL_SUPPORT") == "PASS"
        and gates.get("G10_APPLICATION_SECURITY") == "PENDING"
        and state.get("blocked") is False
        and step32_compatibility_closed(state)
    )
    if content_phase:
        return "STEP33_CONTENT"
    if step33_application_security_closed(state):
        return "STEP33_CLOSURE"
    raise ValidationFailure("execution state is neither the Step33 content phase nor the authorized Step34 closure")


def _validate_scenarios(document: dict[str, Any]) -> None:
    scenarios = document.get("scenarios")
    if not isinstance(scenarios, list):
        raise ValidationFailure("scenarios must be a list")
    seen: set[str] = set()
    for scenario_value in scenarios:
        scenario = _mapping(scenario_value, "scenario")
        scenario_id = scenario.get("id")
        if not isinstance(scenario_id, str) or scenario_id in seen:
            raise ValidationFailure(f"missing or duplicate scenario id: {scenario_id!r}")
        seen.add(scenario_id)
        if scenario.get("status") not in {"PASS", "NOT_APPLICABLE"}:
            raise ValidationFailure(f"scenario {scenario_id} has no closed status")
        evidence = _mapping(scenario.get("evidence"), f"evidence for {scenario_id}")
        if scenario.get("status") == "PASS":
            if evidence.get("result") != "PASS" or not isinstance(evidence.get("test"), str) or not evidence["test"].strip():
                raise ValidationFailure(f"scenario {scenario_id} lacks executable PASS evidence")
        else:
            architecture_evidence = evidence.get("architecture_evidence")
            if evidence.get("result") != "NOT_APPLICABLE" or not isinstance(architecture_evidence, str) or len(architecture_evidence.strip()) < 20:
                raise ValidationFailure(f"N/A scenario {scenario_id} lacks concrete architecture evidence")
    if seen != REQUIRED_SCENARIOS:
        raise ValidationFailure(f"scenario inventory mismatch: missing={sorted(REQUIRED_SCENARIOS - seen)} extra={sorted(seen - REQUIRED_SCENARIOS)}")


def _validate_findings(document: dict[str, Any]) -> None:
    findings = _mapping(document.get("findings"), "findings")
    for key, value in findings.items():
        if not isinstance(value, int) or value < 0:
            raise ValidationFailure(f"finding count {key} is not a non-negative integer")
    for key in ("critical_open", "high_open", "medium_open", "low_open"):
        if findings.get(key) != 0:
            raise ValidationFailure(f"open finding remains: {key}={findings.get(key)}")


def _validate_dependencies(document: dict[str, Any]) -> None:
    dependencies = _mapping(document.get("dependencies"), "dependencies")
    for key in ("python_locked_audit", "frontend_runtime_audit", "frontend_full_audit"):
        item = _mapping(dependencies.get(key), key)
        if item.get("status") != "PASS":
            raise ValidationFailure(f"dependency audit failed: {key}")
    python_audit = _mapping(dependencies["python_locked_audit"], "python_locked_audit")
    if python_audit.get("unresolved_applicable_high_or_critical") != 0:
        raise ValidationFailure("unresolved applicable Python High/Critical dependency finding")
    for key in ("frontend_runtime_audit", "frontend_full_audit"):
        if dependencies[key].get("vulnerabilities") != 0:
            raise ValidationFailure(f"frontend vulnerabilities remain: {key}")


def _validate_tests(document: dict[str, Any]) -> None:
    tests = document.get("tests")
    if not isinstance(tests, list) or not tests:
        raise ValidationFailure("no executable Step33 test evidence")
    for test_value in tests:
        test = _mapping(test_value, "test evidence")
        if test.get("status") != "PASS" or test.get("failed") != 0 or not isinstance(test.get("passed"), int) or test["passed"] <= 0:
            raise ValidationFailure("test evidence is not a passing non-empty run")


def _validate_content_commit_binding(document: dict[str, Any], state: dict[str, Any], phase: str) -> None:
    """Bind a closure receipt to the content commit recorded by execution state."""

    if phase != "STEP33_CLOSURE":
        return
    execution = _mapping(state.get("specialist_execution"), "specialist_execution")
    appsec = _mapping(execution.get("step33_application_security"), "step33_application_security")
    assessed_commit = document.get("assessed_commit")
    content_commit = appsec.get("content_commit")
    last_completed_content_commit = execution.get("last_completed_content_commit")
    if not (
        isinstance(content_commit, str)
        and SHA.fullmatch(content_commit)
        and isinstance(last_completed_content_commit, str)
        and SHA.fullmatch(last_completed_content_commit)
        and assessed_commit == content_commit == last_completed_content_commit
    ):
        raise ValidationFailure(
            "receipt assessed_commit must equal specialist_execution.step33_application_security.content_commit "
            "and specialist_execution.last_completed_content_commit"
        )


def validate_document(document: dict[str, Any], state: dict[str, Any]) -> dict[str, str]:
    if document.get("schema_version") != "1.0" or document.get("step") != 33 or document.get("gate") != "G10_APPLICATION_SECURITY":
        raise ValidationFailure("receipt identity is invalid")
    assessed_commit = document.get("assessed_commit")
    if not isinstance(assessed_commit, str) or not SHA.fullmatch(assessed_commit):
        raise ValidationFailure("receipt assessed_commit is not a commit SHA")
    if document.get("evidence_gate_status") != "PASS":
        raise ValidationFailure("evidence gate is not PASS")
    phase = _validate_state(state)
    if document.get("phase") != "STEP33_CONTENT":
        raise ValidationFailure("Step33 receipt must remain a content-phase evidence receipt")
    _validate_content_commit_binding(document, state, phase)
    _validate_scenarios(document)
    _validate_findings(document)
    _validate_dependencies(document)
    _validate_tests(document)
    scope_guard = _mapping(document.get("scope_guard"), "scope_guard")
    if scope_guard.get("protected_paths_touched") is not False or "tests/quality_unit_artifacts/" not in scope_guard.get("protected_paths", []):
        raise ValidationFailure("protected quality artifacts scope guard is not closed")
    return {"receipt_phase": document["phase"], "execution_phase": phase, "status": "PASS"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Step33 application-security evidence")
    parser.add_argument("--ci", action="store_true", help="also require the authorized post-closure execution state")
    args = parser.parse_args()
    try:
        document = _mapping(json.loads(REPORT.read_text(encoding="utf-8")), "receipt")
        state = _state()
        result = validate_document(document, state)
        if args.ci and result["execution_phase"] != "STEP33_CLOSURE":
            raise ValidationFailure("CI requires the final Step34 handoff with G10 PASS")
    except (OSError, json.JSONDecodeError, ValidationFailure) as error:
        print(json.dumps({"status": "FAIL", "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps({**result, "status": "PASS"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
