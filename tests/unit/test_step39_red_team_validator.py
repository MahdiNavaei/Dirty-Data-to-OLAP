"""Positive and negative receipt controls for Step39 evidence."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from tools.validate_step39_red_team import REQUIRED_NEGATIVE_CONTROLS, REQUIRED_SCENARIOS, Step39ValidationError, validate_document, validate_state


def _receipt() -> dict:
    return {
        "schema_version": "1.0",
        "step": 39,
        "assessed_commit": "a" * 40,
        "status": "PASS",
        "overall_result": "PASS",
        "attack_surface_inventory": [
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
        ],
        "scenarios": {key: {"executed": True, "result": "PASS", "trust_boundary": "synthetic", "disposition": "closed"} for key in REQUIRED_SCENARIOS},
        "findings": [],
        "critical_open": 0,
        "high_open": 0,
        "negative_controls": {key: True for key in REQUIRED_NEGATIVE_CONTROLS},
        "upstream_gates": {"G6": "PASS", "G7": "PASS", "G8": "PASS", "G9": "PASS", "G10": "PASS", "G11": "PASS", "G12": "PASS", "G13": "PASS", "G14": "PENDING", "G15": "PENDING"},
        "claims": {"universal_security": False, "production_security": False, "external_system_testing": False, "real_secret_exfiltration": False, "destructive_source_testing": False, "step40_started": False},
        "protected_quality_artifacts": "unread, untouched, unstaged and uncommitted",
    }


def test_step39_receipt_accepts_complete_evidence() -> None:
    validate_document(_receipt())


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value["scenarios"].pop(next(iter(REQUIRED_SCENARIOS))), "mandatory scenario"),
        (lambda value: value.update(status="PASS", overall_result="PASS", critical_open=1), "Critical/High"),
        (lambda value: value["negative_controls"].update(wrong_commit_rejected=False), "negative-control"),
        (lambda value: value["upstream_gates"].update(G13="PASS", G14="PASS"), "downstream gate"),
        (lambda value: value["claims"].update(universal_security=True), "forbidden claim"),
    ],
)
def test_step39_receipt_rejects_forged_or_incomplete_evidence(mutation, message: str) -> None:
    value = deepcopy(_receipt())
    mutation(value)
    with pytest.raises(Step39ValidationError, match=message):
        validate_document(value)


def test_step39_authoritative_state_accepts_g13_pass_and_step40_handoff() -> None:
    root = Path(__file__).resolve().parents[2]
    state = yaml.safe_load((root / "docs/execution/MASTER_EXECUTION_STATE.yml").read_text(encoding="utf-8"))
    validate_state(state)


def test_step39_state_rejects_starting_step40_or_losing_g13() -> None:
    root = Path(__file__).resolve().parents[2]
    state = yaml.safe_load((root / "docs/execution/MASTER_EXECUTION_STATE.yml").read_text(encoding="utf-8"))
    forged = deepcopy(state)
    forged["specialist_execution"]["step40_started"] = True
    with pytest.raises(Step39ValidationError, match="Step40"):
        validate_state(forged)
    forged = deepcopy(state)
    forged["gates"]["G13_ADVERSARIAL_SECURITY"] = "PENDING"
    with pytest.raises(Step39ValidationError, match="G13"):
        validate_state(forged)
