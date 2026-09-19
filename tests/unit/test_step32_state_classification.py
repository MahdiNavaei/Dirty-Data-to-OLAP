from copy import deepcopy
from pathlib import Path

import yaml

from tools.execution_state import (
    is_authorized_specialist_handoff,
    prior_gate_state_is_coherent,
    step30_g8_closed,
    step31_qa_closed,
    step32_compatibility_closed,
)


STATE_PATH = Path(__file__).parents[2] / "docs" / "execution" / "MASTER_EXECUTION_STATE.yml"


def _closed_step32_state() -> dict:
    state = deepcopy(yaml.safe_load(STATE_PATH.read_text(encoding="utf-8")))
    execution = state["specialist_execution"]
    content_commit = "a" * 40
    execution.update(
        {
            "current_step": 33,
            "current_role": "application_security_engineer",
            "current_specialist": "Step33 - Application Security Engineer",
            "last_completed_step": 32,
            "last_completed_role": "compatibility_test_engineer",
            "last_completed_specialist": "Step32 - Compatibility Test Engineer",
            "last_completed_content_commit": content_commit,
            "next_step": "Step33 - Application Security Engineer",
            "step32_started": True,
            "step32_status": "COMPLETED_COMPATIBILITY_G9_PASS",
            "step33_started": False,
            "step33_status": "NOT_STARTED",
            "step32_compatibility": {
                "step32_started": True,
                "status": "PASS",
                "g9_status": "PASS",
                "content_commit": content_commit,
            },
        }
    )
    for step in range(33, 41):
        execution[f"step{step}_started"] = False
        execution[f"step{step}_status"] = "NOT_STARTED"
    for key in (
        "step33_application_security",
        "step34_observability",
        "step35_sre",
        "step36_resilience",
        "step37_performance",
        "step38_load_stress",
        "step39_red_team",
    ):
        execution.pop(key, None)
    state["gates"]["G9_FUNCTIONAL_SUPPORT"] = "PASS"
    for gate in (
        "G10_APPLICATION_SECURITY",
        "G11_RESILIENCE",
        "G12_CAPACITY",
        "G13_ADVERSARIAL_SECURITY",
        "G14_USABILITY",
        "G15_RELEASE",
    ):
        state["gates"][gate] = "PENDING"
    execution.pop("step33_application_security", None)
    state["blocked"] = False
    return state


def test_closed_step32_state_is_retained_by_prior_validators() -> None:
    state = _closed_step32_state()

    assert step32_compatibility_closed(state) is True
    assert step31_qa_closed(state) is True
    assert step30_g8_closed(state) is True
    assert is_authorized_specialist_handoff(state, minimum_current_step=16, maximum_current_step=31) is True
    assert prior_gate_state_is_coherent(state) is True


def test_step32_closure_rejects_a_fake_content_commit() -> None:
    state = _closed_step32_state()
    state["specialist_execution"]["last_completed_content_commit"] = "b" * 40

    assert step32_compatibility_closed(state) is False
    assert is_authorized_specialist_handoff(state, minimum_current_step=16, maximum_current_step=31) is False
