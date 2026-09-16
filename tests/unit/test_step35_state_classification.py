from copy import deepcopy
from pathlib import Path

import yaml

from tools.execution_state import is_authorized_specialist_handoff, prior_gate_state_is_coherent, step35_sre_closed


STATE_PATH = Path(__file__).parents[2] / "docs" / "execution" / "MASTER_EXECUTION_STATE.yml"


def _closed_step35_state() -> dict:
    state = yaml.safe_load(STATE_PATH.read_text(encoding="utf-8"))
    execution = state["specialist_execution"]
    content_commit = "e" * 40
    execution.update(
        {
            "current_step": 36,
            "current_role": "chaos_resilience",
            "current_specialist": "Step36 - Chaos / Resilience Engineer",
            "last_completed_step": 35,
            "last_completed_role": "sre",
            "last_completed_specialist": "Step35 - Site Reliability Engineer",
            "last_completed_content_commit": content_commit,
            "next_step": "Step36 - Chaos / Resilience Engineer",
            "step35_started": True,
            "step35_status": "COMPLETED_SRE",
            "step36_started": False,
            "step36_status": "NOT_STARTED",
            "step35_sre": {
                "step35_started": True,
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
            "G11_RESILIENCE": "PENDING",
            "G12_CAPACITY": "PENDING",
            "G13_ADVERSARIAL_SECURITY": "PENDING",
            "G14_USABILITY": "PENDING",
            "G15_RELEASE": "PENDING",
        }
    )
    state["blocked"] = False
    return state


def test_step35_closure_requires_exact_step36_handoff() -> None:
    state = _closed_step35_state()
    assert step35_sre_closed(state) is True
    assert is_authorized_specialist_handoff(state, minimum_current_step=36, maximum_current_step=36) is True
    assert prior_gate_state_is_coherent(state) is True


def test_step35_closure_rejects_bad_receipts_or_premature_state() -> None:
    state = _closed_step35_state()
    mutations = (
        lambda value: value["specialist_execution"]["step35_sre"].update({"content_ci_head": "f" * 40}),
        lambda value: value["specialist_execution"].update({"last_completed_role": "wrong_engineer"}),
        lambda value: value["specialist_execution"].update({"step36_started": True}),
        lambda value: value["specialist_execution"].update({"current_step": 37, "current_role": "unknown", "next_step": "Step37 - unknown"}),
        lambda value: value["gates"].update({"G11_RESILIENCE": "PASS"}),
    )
    for mutation in mutations:
        candidate = deepcopy(state)
        mutation(candidate)
        assert step35_sre_closed(candidate) is False
        assert prior_gate_state_is_coherent(candidate) is False
