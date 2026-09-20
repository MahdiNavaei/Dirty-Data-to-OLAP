"""Executable Step34 observability validator.

This validator checks the typed implementation, dashboard references, receipt
and execution-state handoff, then runs the real Step34 test module. It does
not turn optional provider absence into a success claim and never reads the
protected quality-artifact directory.
"""

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
REPORT = ROOT / "output" / "step34_observability_validation.json"
RECEIPT = ROOT / "docs" / "execution" / "STEP34_OBSERVABILITY_REVIEW.md"
DASHBOARD = ROOT / "docs" / "observability" / "dashboards" / "run-health.json"
SHA = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_SCENARIOS = (
    "OBS-CORR-001", "OBS-CORR-002", "OBS-CORR-003", "OBS-CORR-004",
    "OBS-LOG-001", "OBS-LOG-002", "OBS-LOG-003",
    "OBS-MET-001", "OBS-MET-002", "OBS-MET-003", "OBS-MET-004", "OBS-MET-005",
    "OBS-TRACE-001", "OBS-TRACE-002", "OBS-TRACE-003", "OBS-TRACE-004",
    "OBS-DIAG-001", "OBS-DIAG-002", "OBS-DIAG-003", "OBS-DIAG-004",
    "OBS-FAIL-001", "OBS-FAIL-002", "OBS-FAIL-003",
    "OBS-EQV-001", "OBS-DASH-001",
)
FORBIDDEN_CANARIES = ("STEP34_SECRET_CANARY", "password=", "email@example.com", "Authorization: Bearer")


class ValidationFailure(RuntimeError):
    pass


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationFailure(f"{label} must be an object")
    return value


def load_state() -> dict[str, Any]:
    return mapping(yaml.safe_load((ROOT / "docs" / "execution" / "MASTER_EXECUTION_STATE.yml").read_text(encoding="utf-8")), "execution state")


def validate_state(state: dict[str, Any]) -> str:
    from tools.execution_state import step33_application_security_closed, step34_observability_closed, step35_sre_closed, step36_resilience_closed, step37_performance_closed, step38_load_stress_closed, step39_red_team_closed, step40_g14_closed, step41_g15_closed

    specialist = mapping(state.get("specialist_execution"), "specialist_execution")
    gates = mapping(state.get("gates"), "gates")
    terminal = step41_g15_closed(state)
    if state.get("blocked") is not False:
        raise ValidationFailure("Step34 requires blocked=false")
    if gates.get("G6_DATA_CORRECTNESS") != "PASS" or gates.get("G7_END_TO_END_PRODUCT") != "PASS" or gates.get("G8_REPRODUCIBLE_BUILD") != "PASS" or gates.get("G9_FUNCTIONAL_SUPPORT") != "PASS" or gates.get("G10_APPLICATION_SECURITY") != "PASS":
        raise ValidationFailure("G6-G10 must remain PASS")
    later_step36_closure = specialist.get("current_step") in {37, 38} and gates.get("G11_RESILIENCE") == "PASS" and all(gates.get(name) == "PENDING" for name in ("G12_CAPACITY", "G13_ADVERSARIAL_SECURITY", "G14_USABILITY", "G15_RELEASE")) and (step36_resilience_closed(state) if specialist.get("current_step") == 37 else step37_performance_closed(state))
    later_step38_closure = specialist.get("current_step") == 39 and gates.get("G11_RESILIENCE") == "PASS" and gates.get("G12_CAPACITY") == "PASS" and all(gates.get(name) == "PENDING" for name in ("G13_ADVERSARIAL_SECURITY", "G14_USABILITY", "G15_RELEASE")) and step38_load_stress_closed(state)
    later_step39_closure = specialist.get("current_step") == 40 and gates.get("G11_RESILIENCE") == "PASS" and gates.get("G12_CAPACITY") == "PASS" and gates.get("G13_ADVERSARIAL_SECURITY") == "PASS" and all(gates.get(name) == "PENDING" for name in ("G14_USABILITY", "G15_RELEASE")) and step39_red_team_closed(state)
    later_step40_closure = specialist.get("current_step") == 41 and gates.get("G11_RESILIENCE") == "PASS" and gates.get("G12_CAPACITY") == "PASS" and gates.get("G13_ADVERSARIAL_SECURITY") == "PASS" and gates.get("G14_USABILITY") == "PASS" and gates.get("G15_RELEASE") == "PENDING" and step40_g14_closed(state)
    if not (terminal or later_step36_closure or later_step38_closure or later_step39_closure or later_step40_closure) and any(gates.get(name) != "PENDING" for name in ("G11_RESILIENCE", "G12_CAPACITY", "G13_ADVERSARIAL_SECURITY", "G14_USABILITY", "G15_RELEASE")):
        raise ValidationFailure("G11-G15 must remain PENDING")
    if terminal:
        return "STEP34_CLOSURE"
    if specialist.get("current_step") == 34:
        if not step33_application_security_closed(state) or specialist.get("current_role") != "observability_engineer" or specialist.get("step34_started") is not False or specialist.get("step34_status") != "NOT_STARTED":
            raise ValidationFailure("state is not the Step34 content phase")
        return "STEP34_CONTENT"
    if specialist.get("current_step") == 35:
        if not step34_observability_closed(state):
            raise ValidationFailure("state is not the authorized Step34 -> Step35 handoff")
        return "STEP34_CLOSURE"
    if specialist.get("current_step") == 36:
        if not step35_sre_closed(state):
            raise ValidationFailure("state is not the authorized Step35 -> Step36 handoff")
        return "STEP34_CLOSURE"
    if specialist.get("current_step") == 37:
        if not step36_resilience_closed(state):
            raise ValidationFailure("state is not the authorized later Step36 closure")
        return "STEP34_CLOSURE"
    if specialist.get("current_step") == 38:
        if not step37_performance_closed(state):
            raise ValidationFailure("state is not the authorized later Step37 closure")
        return "STEP34_CLOSURE"
    if specialist.get("current_step") == 39:
        if not step38_load_stress_closed(state):
            raise ValidationFailure("state is not the authorized later Step38 closure")
        return "STEP34_CLOSURE"
    if specialist.get("current_step") == 40:
        if not step39_red_team_closed(state):
            raise ValidationFailure("state is not the authorized later Step39 closure")
        return "STEP34_CLOSURE"
    if specialist.get("current_step") == 41:
        if not step40_g14_closed(state):
            raise ValidationFailure("state is not the authorized later Step40 closure")
        return "STEP34_CLOSURE"
    raise ValidationFailure("state is neither Step34 content phase nor authorized Step35 handoff")


def validate_metrics() -> dict[str, Any]:
    from dirty_data_to_olap.observability import FORBIDDEN_METRIC_LABELS, METRIC_DEFINITIONS

    names: set[str] = set()
    inventory: list[dict[str, Any]] = []
    for definition in METRIC_DEFINITIONS:
        if definition.name in names:
            raise ValidationFailure(f"duplicate metric: {definition.name}")
        names.add(definition.name)
        if FORBIDDEN_METRIC_LABELS.intersection(definition.allowed_labels):
            raise ValidationFailure(f"prohibited metric label in {definition.name}")
        if not definition.metric_type or not definition.unit or not definition.description or not definition.aggregation or not definition.instrumentation_point:
            raise ValidationFailure(f"incomplete metric definition: {definition.name}")
        inventory.append({"name": definition.name, "type": definition.metric_type, "unit": definition.unit, "allowed_labels": definition.allowed_labels, "description": definition.description})
    return {"count": len(inventory), "definitions": inventory, "forbidden_labels": sorted(FORBIDDEN_METRIC_LABELS)}


def validate_dashboard(metric_names: set[str], label_names: set[str]) -> dict[str, Any]:
    dashboard = mapping(json.loads(DASHBOARD.read_text(encoding="utf-8")), "dashboard")
    panels = dashboard.get("panels")
    if not isinstance(panels, list) or not panels:
        raise ValidationFailure("dashboard has no panels")
    for panel_value in panels:
        panel = mapping(panel_value, "dashboard panel")
        if panel.get("metric") not in metric_names:
            raise ValidationFailure(f"dashboard references undefined metric: {panel.get('metric')}")
        if not set(panel.get("group_by", [])) <= label_names:
            raise ValidationFailure(f"dashboard uses an undefined label: {panel.get('id')}")
    if dashboard.get("alerts") != [] or dashboard.get("slo_targets") != []:
        raise ValidationFailure("Step34 dashboard must not invent alert thresholds or SLO targets")
    return {"dashboard_id": dashboard.get("dashboard_id"), "panel_count": len(panels), "metric_references": sorted({panel["metric"] for panel in panels})}


def validate_scenarios(document: dict[str, Any]) -> dict[str, Any]:
    values = document.get("scenarios")
    if not isinstance(values, list):
        raise ValidationFailure("scenario matrix is missing")
    by_id = {item.get("id"): item for item in values if isinstance(item, dict)}
    missing = [scenario for scenario in REQUIRED_SCENARIOS if scenario not in by_id]
    if missing:
        raise ValidationFailure(f"required scenarios missing: {missing}")
    for scenario in REQUIRED_SCENARIOS:
        item = mapping(by_id[scenario], scenario)
        if item.get("status") == "PASS":
            evidence = mapping(item.get("evidence"), f"{scenario} evidence")
            test = evidence.get("test")
            if not isinstance(test, str) or "tests/observability/" not in test:
                raise ValidationFailure(f"{scenario} PASS lacks executable Step34 evidence")
        elif item.get("status") == "NOT_APPLICABLE":
            evidence = mapping(item.get("evidence"), f"{scenario} evidence")
            if not isinstance(evidence.get("architecture_evidence"), str) or not evidence["architecture_evidence"].strip():
                raise ValidationFailure(f"{scenario} N/A lacks architecture evidence")
        else:
            raise ValidationFailure(f"{scenario} is not PASS or explicit N/A")
    return {"required": len(REQUIRED_SCENARIOS), "pass": sum(by_id[item].get("status") == "PASS" for item in REQUIRED_SCENARIOS), "not_applicable": sum(by_id[item].get("status") == "NOT_APPLICABLE" for item in REQUIRED_SCENARIOS)}


def run_executable_suite() -> dict[str, Any]:
    command = [sys.executable, "-m", "pytest", "tests/observability/test_step34_observability.py", "-q"]
    result = subprocess.run(command, cwd=ROOT, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=300, check=False)
    output = (result.stdout + "\n" + result.stderr).strip()
    if result.returncode != 0:
        raise ValidationFailure(f"Step34 observability tests failed: {output[-2000:]}")
    match = re.search(r"(?P<passed>\d+) passed", output)
    passed = int(match.group("passed")) if match else 0
    if passed < 1:
        raise ValidationFailure("Step34 test command did not report a passing test")
    skipped_match = re.search(r"(?P<skipped>\d+) skipped", output)
    return {"command": "python -m pytest tests/observability/test_step34_observability.py -q", "status": "PASS", "passed": passed, "skipped": int(skipped_match.group("skipped")) if skipped_match else 0}


def validate_document(document: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    from tools.execution_state import step35_sre_closed, step36_resilience_closed, step37_performance_closed, step38_load_stress_closed, step39_red_team_closed, step40_g14_closed

    if document.get("schema_version") != "1.0" or document.get("step") != 34 or document.get("overall_result") != "PASS":
        raise ValidationFailure("Step34 machine receipt identity/result is invalid")
    assessed = document.get("assessed_commit")
    if not isinstance(assessed, str) or not SHA.fullmatch(assessed):
        raise ValidationFailure("Step34 assessed_commit is not a SHA")
    phase = validate_state(state)
    execution = mapping(state.get("specialist_execution"), "specialist_execution")
    observability = execution.get("step34_observability")
    if phase == "STEP34_CLOSURE":
        observability = mapping(observability, "step34_observability")
        content_commit = observability.get("content_commit")
        pointer_matches = execution.get("last_completed_content_commit") == content_commit
        if execution.get("current_step") == 36:
            pointer_matches = step35_sre_closed(state)
        if execution.get("current_step") == 37:
            pointer_matches = step36_resilience_closed(state)
        if execution.get("current_step") == 38:
            pointer_matches = step37_performance_closed(state)
        if execution.get("current_step") == 39:
            pointer_matches = step38_load_stress_closed(state)
        if execution.get("current_step") == 40:
            pointer_matches = step39_red_team_closed(state)
        if execution.get("current_step") == 41:
            pointer_matches = step40_g14_closed(state)
        if not isinstance(content_commit, str) or not SHA.fullmatch(content_commit) or assessed != content_commit or not pointer_matches:
            raise ValidationFailure("receipt SHA is not bound to authoritative Step34 content commit")
        if observability.get("content_ci_result") != "PASS" or observability.get("status") != "PASS":
            raise ValidationFailure("Step34 closure evidence is not marked PASS")
    elif observability is not None:
        raise ValidationFailure("content phase must not contain a Step34 closure block")
    if "G11_RESILIENCE=PASS" in RECEIPT.read_text(encoding="utf-8") or re.search(r"G11[^\n]*PASS", document.__repr__(), re.I):
        raise ValidationFailure("Step34 must not claim G11 PASS")
    encoded = json.dumps(document, ensure_ascii=False)
    if any(canary.lower() in encoded.lower() for canary in FORBIDDEN_CANARIES):
        raise ValidationFailure("machine evidence contains a forbidden canary")
    scenario_summary = validate_scenarios(document)
    metrics = validate_metrics()
    dashboard = validate_dashboard({item["name"] for item in metrics["definitions"]}, {label for item in metrics["definitions"] for label in item["allowed_labels"]})
    tests = run_executable_suite()
    return {"phase": phase, "scenario_summary": scenario_summary, "metrics": metrics, "dashboard": dashboard, "tests": tests, "status": "PASS"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Step34 observability evidence")
    parser.add_argument("--ci", action="store_true", help="require the final Step35 handoff instead of the content phase")
    args = parser.parse_args()
    try:
        document = mapping(json.loads(REPORT.read_text(encoding="utf-8")), "Step34 receipt")
        state = load_state()
        result = validate_document(document, state)
        if args.ci and result["phase"] != "STEP34_CLOSURE":
            raise ValidationFailure("CI mode requires the final Step35 handoff state")
    except (OSError, json.JSONDecodeError, ValidationFailure) as error:
        print(json.dumps({"status": "FAIL", "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
