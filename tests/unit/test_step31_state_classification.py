from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from tools.execution_state import step30_g8_closed, step31_external_ci_blocked, step31_qa_closed, step41_g15_closed
from tools.validate_step31_qa import ValidationFailure, classify_state


STATE_PATH = Path(__file__).parents[2] / "docs" / "execution" / "MASTER_EXECUTION_STATE.yml"


def _state() -> dict:
    state = yaml.safe_load(STATE_PATH.read_text(encoding="utf-8"))
    execution = state["specialist_execution"]
    qa = execution["step31_qa_automation"]
    execution.update(
        {
            "current_step": 32,
            "current_role": "compatibility_test_engineer",
            "current_specialist": "Step32 - Compatibility Test Engineer",
            "last_completed_step": 31,
            "last_completed_role": "qa_automation_engineer",
            "last_completed_specialist": "Step31 - QA Automation Engineer",
            "last_completed_content_commit": qa["content_commit"],
            "next_step": "Step32 - Compatibility Test Engineer",
            "step31_started": True,
            "step31_status": "COMPLETED_QA_AUTOMATION",
            "step32_started": False,
            "step32_status": "NOT_STARTED",
        }
    )
    state["gates"]["G9_FUNCTIONAL_SUPPORT"] = "PENDING"
    state["blocked"] = False
    return state


def _terminal_state() -> dict:
    return deepcopy(yaml.safe_load(STATE_PATH.read_text(encoding="utf-8")))


def _pre_step31_state() -> dict:
    state = deepcopy(_state())
    execution = state["specialist_execution"]
    execution.update(
        {
            "current_step": 31,
            "current_role": "qa_automation_engineer",
            "current_specialist": "Step31 - QA Automation Engineer",
            "last_completed_step": 30,
            "last_completed_role": "devops_engineer",
            "last_completed_specialist": "Step30 - DevOps Engineer",
            "next_step": "Step31 - QA Automation Engineer",
            "step31_started": False,
            "step31_status": "NOT_STARTED",
            "step32_started": False,
            "step32_status": "NOT_STARTED",
        }
    )
    state["blocked"] = False
    return state


def _blocked_external_state() -> dict:
    state = _pre_step31_state()
    execution = state["specialist_execution"]
    qa = execution["step31_qa_automation"]
    content_commit = qa["content_commit"]
    execution.update(
        {
            "last_completed_content_commit": content_commit,
            "step31_started": True,
            "step31_status": "BLOCKED_EXTERNAL_FINAL_CI",
            "step31_blocking_reason": "BLOCKED_EXTERNAL_FINAL_CI",
        }
    )
    qa.update(
        {
            "step31_started": True,
            "status": "PASS",
            "implementation_result": "PASS",
            "content_ci_run": "34962240176",
            "content_ci_result": "PASS",
            "final_head": "1881e6e8a5a1394f6606823282a3dfb79db80609",
            "final_head_ci_run": "34975659130",
            "final_head_ci_result": "BLOCKED_EXTERNAL",
            "final_head_ci_blocker": "GitHub Actions billing/spending-limit restriction",
        }
    )
    state["blocked"] = True
    state["gates"]["G9_FUNCTIONAL_SUPPORT"] = "PENDING"
    return state


def test_pre_step31_handoff_is_classified_before_qa() -> None:
    result = classify_state(_pre_step31_state())

    assert result["status"] == "PASS"
    assert result["phase"] == "pre-Step31"
    assert step30_g8_closed(_pre_step31_state()) is True


def test_post_step31_closure_wins_over_retained_g8_closure() -> None:
    state = _state()

    assert step31_qa_closed(state) is True
    assert step30_g8_closed(state) is True
    result = classify_state(state)

    assert result == {
        "status": "PASS",
        "phase": "post-Step31",
        "current_step": "32",
        "g6": "PASS",
        "g7": "PASS",
        "g8": "PASS",
    }


def test_terminal_step41_g15_closure_is_accepted_by_historical_g8_path() -> None:
    state = _terminal_state()

    assert step41_g15_closed(state) is True
    assert step30_g8_closed(state) is True


def _terminal_g15_pending(state: dict) -> None:
    state["gates"]["G15_RELEASE"] = "PENDING"


def _terminal_last_step_40(state: dict) -> None:
    state["specialist_execution"]["last_completed_step"] = 40


def _terminal_current_step_42(state: dict) -> None:
    state["specialist_execution"]["current_step"] = 42


def _terminal_next_step_42(state: dict) -> None:
    state["specialist_execution"]["next_step"] = "Step42 - Unauthorised Specialist"


def _terminal_step41_not_started(state: dict) -> None:
    state["specialist_execution"].update({"step41_started": False, "step41_status": "NOT_STARTED"})


def _terminal_receipt_missing(state: dict) -> None:
    state["specialist_execution"].pop("step41_documentation", None)


def _terminal_prior_gate_pending(state: dict) -> None:
    state["gates"]["G8_REPRODUCIBLE_BUILD"] = "PENDING"


@pytest.mark.parametrize(
    "mutate",
    [
        _terminal_g15_pending,
        _terminal_last_step_40,
        _terminal_current_step_42,
        _terminal_next_step_42,
        _terminal_step41_not_started,
        _terminal_receipt_missing,
        _terminal_prior_gate_pending,
    ],
    ids=[
        "g15-pending",
        "last-completed-step-40",
        "current-step-42",
        "next-step-42",
        "step41-not-started",
        "missing-step41-receipt",
        "prior-gate-pending",
    ],
)
def test_terminal_step41_predicate_rejects_incomplete_or_forged_state(mutate) -> None:
    state = _terminal_state()
    mutate(state)

    assert step41_g15_closed(state) is False
    assert step30_g8_closed(state) is False


def test_external_ci_blocker_is_distinct_from_completed_or_pre_step31() -> None:
    state = _blocked_external_state()

    assert step31_external_ci_blocked(state) is True
    assert step31_qa_closed(state) is False
    result = classify_state(state)

    assert result["status"] == "BLOCKED_EXTERNAL"
    assert result["phase"] == "final-head-ci-blocked"


def _malformed_fake_completion(state: dict) -> None:
    state["specialist_execution"]["last_completed_content_commit"] = "0" * 40


def _skipped_nonsequential_handoff(state: dict) -> None:
    state["specialist_execution"].update({"current_step": 33})


def _arbitrary_blocked(state: dict) -> None:
    state["blocked"] = True


@pytest.mark.parametrize(
    "mutate",
    [_malformed_fake_completion, _skipped_nonsequential_handoff, _arbitrary_blocked],
    ids=["malformed-fake-completion", "skipped-nonsequential-handoff", "arbitrary-blocked"],
)
def test_invalid_step31_states_fail_closed(mutate) -> None:
    state = _state()
    mutate(state)
    with pytest.raises(ValidationFailure):
        classify_state(state)
