from copy import deepcopy
from pathlib import Path

import yaml

from tools.execution_state import is_authorized_specialist_handoff, prior_gate_state_is_coherent, step33_application_security_closed


STATE_PATH = Path(__file__).parents[2] / "docs" / "execution" / "MASTER_EXECUTION_STATE.yml"


def _closed_step33_state() -> dict:
    state = deepcopy(yaml.safe_load(STATE_PATH.read_text(encoding="utf-8")))
    execution = state["specialist_execution"]
    content_commit = "c" * 40
    execution.update(
        {
            "current_step": 34,
            "current_role": "observability_engineer",
            "current_specialist": "Step34 - Observability Engineer",
            "last_completed_step": 33,
            "last_completed_role": "application_security_engineer",
            "last_completed_specialist": "Step33 - Application Security Engineer",
            "last_completed_content_commit": content_commit,
            "next_step": "Step34 - Observability Engineer",
            "step33_started": True,
            "step33_status": "COMPLETED_APPLICATION_SECURITY_G10_PASS",
            "step34_started": False,
            "step34_status": "NOT_STARTED",
            "step33_application_security": {
                "step33_started": True,
                "status": "PASS",
                "g10_status": "PASS",
                "content_commit": content_commit,
            },
        }
    )
    state["gates"].update(
        {
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


def test_step33_closure_authorizes_only_the_step34_handoff() -> None:
    state = _closed_step33_state()
    assert step33_application_security_closed(state) is True
    assert is_authorized_specialist_handoff(state, minimum_current_step=33, maximum_current_step=33) is True
    assert prior_gate_state_is_coherent(state) is True


def test_step33_closure_rejects_pending_gate_wrong_role_and_started_step34() -> None:
    state = _closed_step33_state()
    for mutation in (
        lambda value: value["gates"].update({"G10_APPLICATION_SECURITY": "PENDING"}),
        lambda value: value["specialist_execution"].update({"current_role": "application_security_engineer"}),
        lambda value: value["specialist_execution"].update({"step34_started": True}),
    ):
        candidate = deepcopy(state)
        mutation(candidate)
        assert step33_application_security_closed(candidate) is False
        assert prior_gate_state_is_coherent(candidate) is False


def test_step33_closure_rejects_skipped_or_premature_later_step() -> None:
    state = _closed_step33_state()
    state["specialist_execution"].update({"current_step": 35, "current_role": "unknown_engineer", "next_step": "Step35 - unknown"})
    assert step33_application_security_closed(state) is False
    assert is_authorized_specialist_handoff(state, minimum_current_step=33, maximum_current_step=34) is False
