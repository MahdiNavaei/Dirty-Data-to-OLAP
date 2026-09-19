from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from tools.validate_step33_appsec import ValidationFailure, validate_document


ROOT = Path(__file__).parents[2]


def _fixtures() -> tuple[dict, dict]:
    receipt = json.loads((ROOT / "output" / "step33_appsec_validation.json").read_text(encoding="utf-8"))
    state = yaml.safe_load((ROOT / "docs" / "execution" / "MASTER_EXECUTION_STATE.yml").read_text(encoding="utf-8"))
    return receipt, state


def _content_phase_state(state: dict) -> dict:
    candidate = copy.deepcopy(state)
    execution = candidate["specialist_execution"]
    step32_content_commit = execution["step32_compatibility"]["content_commit"]
    execution.update(
        {
            "current_step": 33,
            "current_role": "application_security_engineer",
            "current_specialist": "Step33 - Application Security Engineer",
            "last_completed_step": 32,
            "last_completed_role": "compatibility_test_engineer",
            "last_completed_specialist": "Step32 - Compatibility Test Engineer",
            "last_completed_content_commit": step32_content_commit,
            "next_step": "Step33 - Application Security Engineer",
            "step32_started": True,
            "step32_status": "COMPLETED_COMPATIBILITY_G9_PASS",
            "step33_started": False,
            "step33_status": "NOT_STARTED",
            "step34_started": False,
            "step34_status": "NOT_STARTED",
        }
    )
    execution.pop("step33_application_security", None)
    for step in range(34, 41):
        execution[f"step{step}_started"] = False
        execution[f"step{step}_status"] = "NOT_STARTED"
    for key in (
        "step34_observability",
        "step35_sre",
        "step36_resilience",
        "step37_performance",
        "step38_load_stress",
        "step39_red_team",
    ):
        execution.pop(key, None)
    for gate in (
        "G10_APPLICATION_SECURITY",
        "G11_RESILIENCE",
        "G12_CAPACITY",
        "G13_ADVERSARIAL_SECURITY",
        "G14_USABILITY",
        "G15_RELEASE",
    ):
        candidate["gates"][gate] = "PENDING"
    candidate["blocked"] = False
    return candidate


def test_step33_receipt_validates_in_content_phase() -> None:
    receipt, state = _fixtures()
    assert validate_document(receipt, _content_phase_state(state))["status"] == "PASS"


@pytest.mark.parametrize(
    "assessed_commit",
    (
        "788bb4bdeed9341278194a3e67adf84dfc88f7ba",
        "a" * 40,
        "29f5f77eed2b0a946aa348462ec1b009fe5c8264",
    ),
)
def test_step33_closure_rejects_unbound_valid_assessed_commits(assessed_commit: str) -> None:
    receipt, state = _fixtures()
    receipt["assessed_commit"] = assessed_commit
    with pytest.raises(ValidationFailure, match="assessed_commit"):
        validate_document(receipt, state)


@pytest.mark.parametrize("assessed_commit", (None, "not-a-commit-sha"))
def test_step33_rejects_missing_or_malformed_assessed_commit(assessed_commit: str | None) -> None:
    receipt, state = _fixtures()
    if assessed_commit is None:
        receipt.pop("assessed_commit")
    else:
        receipt["assessed_commit"] = assessed_commit
    with pytest.raises(ValidationFailure):
        validate_document(receipt, state)


def test_step33_closure_rejects_receipt_when_state_content_commit_changes() -> None:
    receipt, state = _fixtures()
    changed_commit = "b" * 40
    state["specialist_execution"]["step33_application_security"]["content_commit"] = changed_commit
    with pytest.raises(ValidationFailure, match="assessed_commit"):
        validate_document(receipt, state)


def test_step33_later_handoff_allows_current_pointer_change() -> None:
    receipt, state = _fixtures()
    state["specialist_execution"]["last_completed_content_commit"] = "c" * 40
    assert validate_document(receipt, state)["status"] == "PASS"


@pytest.mark.parametrize(
    "mutation",
    (
        lambda receipt, state: receipt["scenarios"].pop(),
        lambda receipt, state: receipt["scenarios"][0].update({"evidence": {"result": "PASS"}}),
        lambda receipt, state: receipt["findings"].update({"high_open": 1}),
        lambda receipt, state: state["specialist_execution"].update({"step35_status": "STARTED"}),
    ),
)
def test_step33_receipt_rejects_missing_evidence_or_open_state(mutation) -> None:
    receipt, state = _fixtures()
    changed_receipt = copy.deepcopy(receipt)
    changed_state = copy.deepcopy(state)
    mutation(changed_receipt, changed_state)
    with pytest.raises(ValidationFailure):
        validate_document(changed_receipt, changed_state)
