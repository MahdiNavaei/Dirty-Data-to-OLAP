from copy import deepcopy
from pathlib import Path

import yaml

from tools.execution_state import (
    is_authorized_specialist_handoff,
    prior_gate_state_is_coherent,
    step30_g8_closed,
    step32_compatibility_closed,
    step33_application_security_closed,
    step34_observability_closed,
    step35_sre_closed,
    step36_resilience_closed,
    step37_performance_closed,
)


STATE_PATH = Path(__file__).parents[2] / "docs" / "execution" / "MASTER_EXECUTION_STATE.yml"


def _closed_step37_state() -> dict:
    state = yaml.safe_load(STATE_PATH.read_text(encoding="utf-8"))
    execution = state["specialist_execution"]
    content_commit = "f" * 40
    execution.update(
        {
            "current_step": 38,
            "current_role": "load_stress",
            "current_specialist": "Step38 - Load / Stress Test Engineer",
            "last_completed_step": 37,
            "last_completed_role": "performance_engineer",
            "last_completed_specialist": "Step37 - Performance Engineer",
            "last_completed_content_commit": content_commit,
            "next_step": "Step38 - Load / Stress Test Engineer",
            "step36_started": True,
            "step36_status": "COMPLETED_RESILIENCE_G11_PASS",
            "step37_started": True,
            "step37_status": "COMPLETED_PERFORMANCE",
            "step38_started": False,
            "step38_status": "NOT_STARTED",
            "step37_performance": {
                "step37_started": True,
                "status": "PASS",
                "content_commit": content_commit,
                "content_ci_head": content_commit,
                "content_ci_result": "PASS",
                "report_assessed_commit": content_commit,
            },
        }
    )
    state["gates"].update(
        {
            "G6_DATA_CORRECTNESS": "PASS",
            "G7_END_TO_END_PRODUCT": "PASS",
            "G8_REPRODUCIBLE_BUILD": "PASS",
            "G9_FUNCTIONAL_SUPPORT": "PASS",
            "G10_APPLICATION_SECURITY": "PASS",
            "G11_RESILIENCE": "PASS",
            "G12_CAPACITY": "PENDING",
            "G13_ADVERSARIAL_SECURITY": "PENDING",
            "G14_USABILITY": "PENDING",
            "G15_RELEASE": "PENDING",
        }
    )
    state["blocked"] = False
    return state


def test_step37_closure_requires_exact_step38_handoff() -> None:
    state = _closed_step37_state()
    assert step37_performance_closed(state) is True
    assert step36_resilience_closed(state) is True
    assert step35_sre_closed(state) is True
    assert step34_observability_closed(state) is True
    assert step33_application_security_closed(state) is True
    assert step32_compatibility_closed(state) is True
    assert step30_g8_closed(state) is True
    assert is_authorized_specialist_handoff(state, minimum_current_step=38, maximum_current_step=38) is True
    assert prior_gate_state_is_coherent(state) is True


def test_step37_closure_rejects_bad_receipts_or_premature_state() -> None:
    state = _closed_step37_state()
    mutations = (
        lambda value: value["specialist_execution"]["step37_performance"].update({"content_ci_head": "e" * 40}),
        lambda value: value["specialist_execution"]["step37_performance"].update({"report_assessed_commit": "e" * 40}),
        lambda value: value["specialist_execution"].update({"step38_started": True}),
        lambda value: value["specialist_execution"].update({"current_role": "unknown", "next_step": "Step38 - unknown"}),
        lambda value: value["gates"].update({"G12_CAPACITY": "PASS"}),
        lambda value: value.update({"blocked": True}),
    )
    for mutation in mutations:
        candidate = deepcopy(state)
        mutation(candidate)
        assert step37_performance_closed(candidate) is False
        assert prior_gate_state_is_coherent(candidate) is False
