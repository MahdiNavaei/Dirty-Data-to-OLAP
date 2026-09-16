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


def test_step33_receipt_validates_in_content_phase() -> None:
    receipt, state = _fixtures()
    assert validate_document(receipt, state)["status"] == "PASS"


@pytest.mark.parametrize(
    "mutation",
    (
        lambda receipt, state: receipt["scenarios"].pop(),
        lambda receipt, state: receipt["scenarios"][0].update({"evidence": {"result": "PASS"}}),
        lambda receipt, state: receipt["findings"].update({"high_open": 1}),
        lambda receipt, state: state["specialist_execution"].update({"current_step": 35}),
    ),
)
def test_step33_receipt_rejects_missing_evidence_or_open_state(mutation) -> None:
    receipt, state = _fixtures()
    changed_receipt = copy.deepcopy(receipt)
    changed_state = copy.deepcopy(state)
    mutation(changed_receipt, changed_state)
    with pytest.raises(ValidationFailure):
        validate_document(changed_receipt, changed_state)
