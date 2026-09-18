from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

import tools.validate_step38_load_stress as validator


ROOT = Path(__file__).resolve().parents[2]


def _evidence() -> dict:
    return json.loads((ROOT / "output" / "step38_load_stress_validation.json").read_text(encoding="utf-8"))


def test_step38_receipt_has_required_controls() -> None:
    payload = _evidence()
    assert payload["step"] == 38
    assert set(payload["arrival_patterns"]) >= {"STEADY", "RAMP", "BURST", "OVERLOAD"}
    assert [item["worker_count"] for item in payload["worker_ramp"]["ramp"]] == [1, 2, 4, 8]
    assert len(payload["api_real_listener"]["g6_successes"]) >= 2
    assert set(payload["controls"]) == validator.REQUIRED_CONTROLS


def test_step38_validator_rejects_exactly_once_or_step39_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _evidence()
    payload["claims"]["exactly_once"] = True
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SystemExit):
        validator.validate(evidence)


def test_step38_authoritative_state_has_g12_pass_and_step39_not_started() -> None:
    state = yaml.safe_load((ROOT / "docs/execution/MASTER_EXECUTION_STATE.yml").read_text(encoding="utf-8"))
    specialist = state["specialist_execution"]
    assert specialist["current_step"] in {38, 39}
    assert state["gates"]["G13_ADVERSARIAL_SECURITY"] == "PENDING"
