"""Validate Step39 adversarial-security evidence and the G13 handoff."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
HEX40 = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_SCENARIOS = {
    "RT-AUTH-001",
    "RT-AUTH-002",
    "RT-AUTH-003",
    "RT-AUTH-004",
    "RT-PLAN-001",
    "RT-REVIEW-001",
    "RT-REVIEW-002",
    "RT-ARTIFACT-001",
    "RT-ARTIFACT-002",
    "RT-SQL-001",
    "RT-SSRF-001",
    "RT-INPUT-001",
    "RT-SOURCE-001",
}
REQUIRED_SURFACES = {
    "HTTP authentication and authorization",
    "project/run isolation",
    "review decisions and replay",
    "execution-plan authority",
    "artifact identity and content integrity",
    "semantic SQL execution",
    "SQL connector credential boundary",
    "file upload and source staging",
    "filesystem/symlink confinement",
    "error and diagnostic disclosure",
}
REQUIRED_NEGATIVE_CONTROLS = {
    "missing_scenario_rejected",
    "forged_pass_receipt_rejected",
    "wrong_commit_rejected",
    "gate_advance_without_evidence_rejected",
    "step40_start_rejected",
}


class Step39ValidationError(ValueError):
    """A Step39 receipt or state is not admissible."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise Step39ValidationError(message)


def validate_document(evidence: dict[str, Any], *, expected_commit: str | None = None) -> None:
    _require(evidence.get("schema_version") == "1.0", "wrong evidence schema")
    _require(evidence.get("step") == 39, "wrong evidence step")
    assessed = evidence.get("assessed_commit")
    _require(isinstance(assessed, str) and HEX40.fullmatch(assessed) is not None, "assessed_commit is not a full Git commit")
    if expected_commit is not None:
        _require(assessed == expected_commit, "receipt is not bound to expected commit")
    _require(evidence.get("status") == "PASS" and evidence.get("overall_result") == "PASS", "overall result is not PASS")
    surfaces = set(evidence.get("attack_surface_inventory", []))
    _require(REQUIRED_SURFACES <= surfaces, "attack-surface inventory is incomplete")
    scenarios = evidence.get("scenarios")
    _require(isinstance(scenarios, dict) and REQUIRED_SCENARIOS <= set(scenarios), "mandatory scenario set is incomplete")
    for scenario_id in REQUIRED_SCENARIOS:
        scenario = scenarios[scenario_id]
        _require(isinstance(scenario, dict), f"scenario is not an object: {scenario_id}")
        _require(scenario.get("executed") is True and scenario.get("result") == "PASS", f"scenario did not pass: {scenario_id}")
        _require(bool(scenario.get("trust_boundary")) and bool(scenario.get("disposition")), f"scenario receipt is incomplete: {scenario_id}")
    findings = evidence.get("findings")
    _require(isinstance(findings, list), "findings must be a list")
    _require(evidence.get("critical_open") == 0 and evidence.get("high_open") == 0, "Critical/High findings remain open")
    for finding in findings:
        _require(finding.get("severity") in {"INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"}, "finding severity is invalid")
        _require(finding.get("status") in {"CLOSED", "ACCEPTED_LIMITATION", "NOT_REPRODUCED"}, "finding disposition is invalid")
        _require(bool(finding.get("retest")), "finding is missing retest evidence")
    controls = evidence.get("negative_controls")
    _require(isinstance(controls, dict) and REQUIRED_NEGATIVE_CONTROLS == set(controls), "negative-control set is incomplete")
    _require(all(value is True for value in controls.values()), "negative-control did not pass")
    upstream = evidence.get("upstream_gates", {})
    _require(all(upstream.get(key) == "PASS" for key in ("G6", "G7", "G8", "G9", "G10", "G11", "G12")), "an upstream gate is not preserved")
    _require(upstream.get("G13") == "PASS" and upstream.get("G14") == "PENDING" and upstream.get("G15") == "PENDING", "downstream gate state is invalid")
    claims = evidence.get("claims", {})
    for key in ("universal_security", "production_security", "external_system_testing", "real_secret_exfiltration", "destructive_source_testing", "step40_started"):
        _require(claims.get(key) is False, f"forbidden claim is not false: {key}")
    _require(evidence.get("protected_quality_artifacts") == "unread, untouched, unstaged and uncommitted", "protected artifact declaration is not preserved")


def validate_state(state: dict[str, Any], *, require_closed: bool = True) -> None:
    specialist = state.get("specialist_execution", {})
    gates = state.get("gates", {})
    _require(state.get("blocked") is False, "authoritative state is blocked")
    _require(all(gates.get(key) == "PASS" for key in ("G6_DATA_CORRECTNESS", "G7_END_TO_END_PRODUCT", "G8_REPRODUCIBLE_BUILD", "G9_FUNCTIONAL_SUPPORT", "G10_APPLICATION_SECURITY", "G11_RESILIENCE", "G12_CAPACITY")), "upstream gate state is not PASS")
    if not require_closed:
        _require(specialist.get("current_step") == 39 and specialist.get("step39_started") is False and specialist.get("step39_status") == "NOT_STARTED", "pre-closure state is not Step39")
        _require(gates.get("G13_ADVERSARIAL_SECURITY") == "PENDING", "pre-closure G13 is not pending")
        return
    if specialist.get("current_step") == 41:
        _require(specialist.get("current_role") == "technical_writer", "current role is not technical_writer")
        _require(specialist.get("current_specialist") == "Step41 - Technical Writer", "current specialist is not Step41")
        _require(specialist.get("last_completed_step") == 40, "last completed step is not 40")
        _require(specialist.get("last_completed_role") == "developer_experience_engineer", "last completed role is not developer_experience_engineer")
        _require(specialist.get("last_completed_specialist") == "Step40 - Developer Experience Engineer", "last completed specialist is not Step40")
        _require(specialist.get("next_step") == "Step41 - Technical Writer", "next step is not Step41")
        _require(specialist.get("step40_started") is True and specialist.get("step40_status") == "COMPLETED_DEVELOPER_EXPERIENCE_G14_PASS", "Step40 closure is incomplete")
        _require(specialist.get("step41_started") is False and specialist.get("step41_status") == "NOT_STARTED", "Step41 has started")
        _require(gates.get("G14_USABILITY") == "PASS", "G14 is not PASS")
    else:
        _require(specialist.get("current_step") == 40, "current step is not 40")
        _require(specialist.get("current_role") == "developer_experience_engineer", "current role is not developer_experience_engineer")
        _require(specialist.get("current_specialist") == "Step40 - Developer Experience Engineer", "current specialist is not Step40")
        _require(specialist.get("last_completed_step") == 39, "last completed step is not 39")
        _require(specialist.get("last_completed_role") == "penetration_red_team", "last completed role is not penetration_red_team")
        _require(specialist.get("last_completed_specialist") == "Step39 - Penetration Tester / Red Team", "last completed specialist is not Step39")
        _require(specialist.get("next_step") == "Step40 - Developer Experience Engineer", "next step is not Step40")
        _require(specialist.get("step40_started") is False and specialist.get("step40_status") == "NOT_STARTED", "Step40 has started")
        _require(gates.get("G14_USABILITY") == "PENDING", "G14 is not pending")
    _require(specialist.get("step39_started") is True and specialist.get("step39_status") == "COMPLETED_RED_TEAM_G13_PASS", "Step39 closure is incomplete")
    _require(gates.get("G13_ADVERSARIAL_SECURITY") == "PASS", "G13 is not PASS")
    _require(gates.get("G15_RELEASE") == "PENDING", "G15 advanced prematurely")


def validate(evidence_path: Path, *, state_path: Path | None = None, expected_commit: str | None = None, require_closed: bool = True) -> dict[str, Any]:
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise Step39ValidationError(f"evidence unreadable: {error.__class__.__name__}") from error
    validate_document(evidence, expected_commit=expected_commit)
    if state_path is not None:
        try:
            state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as error:
            raise Step39ValidationError(f"state unreadable: {error.__class__.__name__}") from error
        validate_state(state, require_closed=require_closed)
        if require_closed:
            _require(state["specialist_execution"]["step39_red_team"]["content_commit"] == evidence["assessed_commit"], "state and receipt content commits differ")
    current_step = 39
    if require_closed:
        current_step = 41 if state_path is not None and state.get("specialist_execution", {}).get("current_step") == 41 else 40
    return {"status": "PASS", "step": 39, "assessed_commit": evidence["assessed_commit"], "g13": "PASS", "current_step": current_step}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", default=str(ROOT / "output" / "step39_red_team_validation.json"))
    parser.add_argument("--state", default=str(ROOT / "docs" / "execution" / "MASTER_EXECUTION_STATE.yml"))
    parser.add_argument("--expected-commit")
    parser.add_argument("--allow-preclosure", action="store_true")
    args = parser.parse_args()
    try:
        result = validate(Path(args.evidence), state_path=Path(args.state), expected_commit=args.expected_commit, require_closed=not args.allow_preclosure)
    except Step39ValidationError as error:
        raise SystemExit(f"STEP39_VALIDATION_FAILED: {error}") from error
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
