from copy import deepcopy
from pathlib import Path

import yaml

from tools.execution_state import is_authorized_specialist_handoff, prior_gate_state_is_coherent, step34_observability_closed


STATE_PATH = Path(__file__).parents[2] / "docs" / "execution" / "MASTER_EXECUTION_STATE.yml"


def _closed_step34_state() -> dict:
    state = deepcopy(yaml.safe_load(STATE_PATH.read_text(encoding="utf-8")))
    execution = state["specialist_execution"]
    content_commit = "d" * 40
    execution.update(
        {
            "current_step": 35,
            "current_role": "sre",
            "current_specialist": "Step35 - Site Reliability Engineer",
            "last_completed_step": 34,
            "last_completed_role": "observability_engineer",
            "last_completed_specialist": "Step34 - Observability Engineer",
            "last_completed_content_commit": content_commit,
            "next_step": "Step35 - Site Reliability Engineer",
            "step34_started": True,
            "step34_status": "COMPLETED_OBSERVABILITY",
            "step35_started": False,
            "step35_status": "NOT_STARTED",
            "step34_observability": {"step34_started": True, "status": "PASS", "content_commit": content_commit, "content_ci_result": "PASS"},
        }
    )
    state["gates"].update({"G6_DATA_CORRECTNESS": "PASS", "G7_END_TO_END_PRODUCT": "PASS", "G8_REPRODUCIBLE_BUILD": "PASS", "G9_FUNCTIONAL_SUPPORT": "PASS", "G10_APPLICATION_SECURITY": "PASS", "G11_RESILIENCE": "PENDING", "G12_CAPACITY": "PENDING", "G13_ADVERSARIAL_SECURITY": "PENDING", "G14_USABILITY": "PENDING", "G15_RELEASE": "PENDING"})
    state["blocked"] = False
    return state


def test_step34_closure_requires_exact_step35_handoff() -> None:
    state = _closed_step34_state()
    assert step34_observability_closed(state) is True
    assert is_authorized_specialist_handoff(state, minimum_current_step=35, maximum_current_step=35) is True
    assert prior_gate_state_is_coherent(state) is True


def test_step34_closure_rejects_malformed_or_premature_state() -> None:
    state = _closed_step34_state()
    mutations = (
        lambda value: value["specialist_execution"].update({"last_completed_step": 33}),
        lambda value: value["specialist_execution"].update({"last_completed_role": "wrong_engineer"}),
        lambda value: value["specialist_execution"].update({"step34_started": False}),
        lambda value: value["specialist_execution"]["step34_observability"].update({"content_commit": "not-a-sha"}),
        lambda value: value["gates"].update({"G10_APPLICATION_SECURITY": "PENDING"}),
        lambda value: value["gates"].update({"G11_RESILIENCE": "PASS"}),
        lambda value: value["specialist_execution"].update({"current_step": 36, "current_role": "unknown", "next_step": "Step36 - unknown"}),
    )
    for mutation in mutations:
        candidate = deepcopy(state)
        mutation(candidate)
        assert step34_observability_closed(candidate) is False
        assert prior_gate_state_is_coherent(candidate) is False
