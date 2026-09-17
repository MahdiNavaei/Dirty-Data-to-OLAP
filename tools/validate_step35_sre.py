"""Validate Step35 SRE evidence without reading protected quality artifacts."""

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
REPORT = ROOT / "output" / "step35_sre_validation.json"
MATRIX = ROOT / "docs" / "sre" / "recovery-matrix.json"
SLO = ROOT / "docs" / "sre" / "slo.md"
READINESS = ROOT / "docs" / "sre" / "production-readiness.md"
RECEIPT = ROOT / "docs" / "execution" / "STEP35_SRE_REVIEW.md"
SHA = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_RUNBOOKS = (
    "stuck-run.md",
    "worker-stopped-restarted.md",
    "control-store-unavailable.md",
    "control-store-restore.md",
    "queue-backlog.md",
    "provider-unavailable.md",
    "artifact-integrity.md",
    "disk-materialization-failure.md",
    "telemetry-degradation.md",
    "graceful-shutdown-restart.md",
)
REQUIRED_SCENARIOS = (
    "SRE-SLI-001", "SRE-SLI-002", "SRE-SLI-003",
    "BACKUP-001", "BACKUP-002", "BACKUP-003", "RESTORE-001", "RESTORE-002",
    "RESTART-001", "RESTART-002", "RESTART-003", "SHUTDOWN-001", "SHUTDOWN-002", "SHUTDOWN-003",
    "CTRL-001", "CTRL-002", "CTRL-003", "ART-001", "ART-002",
    "RETRY-001", "RETRY-002", "RETRY-003", "STUCK-001", "REVIEW-001", "CANCEL-001",
    "DEGRADE-001", "DEGRADE-002", "RUNBOOK-001", "PRR-001",
)


class ValidationFailure(RuntimeError):
    pass


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationFailure(f"{label} must be an object")
    return value


def load_state() -> dict[str, Any]:
    return mapping(yaml.safe_load((ROOT / "docs" / "execution" / "MASTER_EXECUTION_STATE.yml").read_text(encoding="utf-8")), "execution state")


def validate_state(state: dict[str, Any], document: dict[str, Any]) -> str:
    from tools.execution_state import step34_observability_closed, step35_sre_closed, step36_resilience_closed

    specialist = mapping(state.get("specialist_execution"), "specialist_execution")
    current_gates = mapping(state.get("gates"), "gates")
    if state.get("blocked") is not False:
        raise ValidationFailure("Step35 requires blocked=false")
    if any(current_gates.get(name) != "PASS" for name in ("G6_DATA_CORRECTNESS", "G7_END_TO_END_PRODUCT", "G8_REPRODUCIBLE_BUILD", "G9_FUNCTIONAL_SUPPORT", "G10_APPLICATION_SECURITY")):
        raise ValidationFailure("G6-G10 must remain PASS")
    later_step36_closure = specialist.get("current_step") == 37 and current_gates.get("G11_RESILIENCE") == "PASS" and all(current_gates.get(name) == "PENDING" for name in ("G12_CAPACITY", "G13_ADVERSARIAL_SECURITY", "G14_USABILITY", "G15_RELEASE"))
    if not later_step36_closure and any(current_gates.get(name) != "PENDING" for name in ("G11_RESILIENCE", "G12_CAPACITY", "G13_ADVERSARIAL_SECURITY", "G14_USABILITY", "G15_RELEASE")):
        raise ValidationFailure("G11-G15 must remain PENDING")
    if specialist.get("current_step") == 35:
        if not step34_observability_closed(state) or specialist.get("current_role") != "sre" or specialist.get("step35_started") is not False or specialist.get("step35_status") != "NOT_STARTED":
            raise ValidationFailure("state is not the Step35 content phase")
        if document.get("phase") != "STEP35_CONTENT" or document.get("assessed_commit") != "PENDING_CONTENT_COMMIT":
            raise ValidationFailure("content report is not explicitly pending its content commit")
        return "STEP35_CONTENT"
    if specialist.get("current_step") == 36:
        if not step35_sre_closed(state):
            raise ValidationFailure("state is not the authorized Step35 -> Step36 handoff")
        sre = mapping(specialist.get("step35_sre"), "step35_sre")
        if document.get("phase") != "STEP35_CLOSURE" or document.get("assessed_commit") != sre.get("content_commit"):
            raise ValidationFailure("closure report is not bound to the Step35 content commit")
        return "STEP35_CLOSURE"
    if specialist.get("current_step") == 37:
        if not step36_resilience_closed(state):
            raise ValidationFailure("state is not the authorized later Step36 closure")
        sre = mapping(specialist.get("step35_sre"), "step35_sre")
        if document.get("phase") != "STEP35_CLOSURE" or document.get("assessed_commit") != sre.get("content_commit"):
            raise ValidationFailure("closure report is not bound to the Step35 content commit")
        return "STEP35_CLOSURE"
    raise ValidationFailure("state is neither Step35 content phase nor authorized Step36 handoff")


def validate_matrix() -> dict[str, Any]:
    document = mapping(json.loads(MATRIX.read_text(encoding="utf-8")), "recovery matrix")
    if document.get("schema_version") != "1.0" or document.get("step") != 35 or document.get("status") != "PASS":
        raise ValidationFailure("recovery matrix identity/result is invalid")
    values = document.get("scenarios")
    if not isinstance(values, list):
        raise ValidationFailure("recovery matrix scenarios are missing")
    by_id = {item.get("id"): item for item in values if isinstance(item, dict)}
    missing = [item for item in REQUIRED_SCENARIOS if item not in by_id]
    if missing:
        raise ValidationFailure(f"required SRE scenarios missing: {missing}")
    for scenario_id in REQUIRED_SCENARIOS:
        scenario = mapping(by_id[scenario_id], scenario_id)
        if scenario.get("status") != "PASS":
            raise ValidationFailure(f"{scenario_id} is not PASS")
        evidence = mapping(scenario.get("evidence"), f"{scenario_id} evidence")
        test = evidence.get("test")
        if not isinstance(test, str) or not test.strip() or "quality_unit_artifacts" in test:
            raise ValidationFailure(f"{scenario_id} lacks safe executable evidence")
    return {"required": len(REQUIRED_SCENARIOS), "pass": len(REQUIRED_SCENARIOS)}


def validate_documents() -> dict[str, Any]:
    slo = SLO.read_text(encoding="utf-8")
    readiness = READINESS.read_text(encoding="utf-8")
    receipt = RECEIPT.read_text(encoding="utf-8")
    if "candidate" not in slo.lower() or "not production" not in slo.lower():
        raise ValidationFailure("SLO document must classify objectives as candidate/non-production")
    if "G11" not in readiness or "PENDING" not in readiness or "exactly-once" not in readiness:
        raise ValidationFailure("readiness document does not preserve the Step36 boundary")
    if re.search(r"G11[^\n]*PASS", "\n".join((slo, readiness, receipt)), re.IGNORECASE):
        raise ValidationFailure("Step35 evidence must not claim G11 PASS")
    runbooks = ROOT / "docs" / "sre" / "runbooks"
    missing = [name for name in REQUIRED_RUNBOOKS if not (runbooks / name).is_file()]
    if missing:
        raise ValidationFailure(f"required runbooks missing: {missing}")
    return {"runbooks": len(REQUIRED_RUNBOOKS), "slo": "PASS", "readiness": "CONDITIONAL"}


def run_executable_suite() -> dict[str, Any]:
    command = [sys.executable, "-m", "pytest", "tests/sre/test_step35_sre.py", "tests/unit/test_step35_state_classification.py", "-q"]
    result = subprocess.run(command, cwd=ROOT, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=300, check=False)
    output = (result.stdout + "\n" + result.stderr).strip()
    if result.returncode != 0:
        raise ValidationFailure(f"Step35 SRE tests failed: {output[-2000:]}")
    match = re.search(r"(?P<passed>\d+) passed", output)
    passed = int(match.group("passed")) if match else 0
    if passed < 1:
        raise ValidationFailure("Step35 test command did not report a passing test")
    return {"command": "python -m pytest tests/sre/test_step35_sre.py tests/unit/test_step35_state_classification.py -q", "status": "PASS", "passed": passed}


def validate_report(document: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    if document.get("schema_version") != "1.0" or document.get("step") != 35 or document.get("overall_result") != "PASS":
        raise ValidationFailure("Step35 report identity/result is invalid")
    if any(document.get(key) != "PENDING" for key in ("g11_resilience", "g12_capacity", "g13_adversarial_security", "g14_usability", "g15_release")):
        raise ValidationFailure("Step35 report must preserve pending future gates")
    phase = validate_state(state, document)
    assessed = document.get("assessed_commit")
    if phase == "STEP35_CLOSURE" and (not isinstance(assessed, str) or not SHA.fullmatch(assessed)):
        raise ValidationFailure("closure report assessed_commit is not a SHA")
    return {"phase": phase, "matrix": validate_matrix(), "documents": validate_documents(), "tests": run_executable_suite(), "status": "PASS"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Step35 SRE evidence")
    parser.add_argument("--ci", action="store_true", help="require the final Step36 handoff")
    args = parser.parse_args()
    try:
        document = mapping(json.loads(REPORT.read_text(encoding="utf-8")), "Step35 report")
        result = validate_report(document, load_state())
        if args.ci and result["phase"] != "STEP35_CLOSURE":
            raise ValidationFailure("CI mode requires the final Step36 handoff state")
    except (OSError, json.JSONDecodeError, ValidationFailure) as error:
        print(json.dumps({"status": "FAIL", "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
