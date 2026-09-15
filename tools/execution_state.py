"""Shared execution-state predicates used by repository validators.

The execution state is a durable handoff record, not a snapshot tied to one
specialist.  Validators must therefore accept the current Step30 handoff
after Step29/G7 has closed while still rejecting skipped or mis-ordered steps.
"""

from __future__ import annotations

from typing import Any


CURRENT_ROLE_BY_STEP = {
    6: "database_engineer",
    7: "senior_data_engineer",
    8: "data_profiling_specialist",
    9: "data_quality_engineer",
    10: "data_security_privacy_engineer",
    11: "database_security_specialist",
    12: "dependency_discovery_engineer",
    13: "schema_matching_engineer",
    14: "entity_resolution_engineer",
    15: "applied_ml_engineer",
    16: "llm_semantic_ai_engineer",
    17: "evidence_fusion_engineer",
    18: "ml_evaluation_engineer",
    19: "canonical_model_engineer",
    20: "olap_engineer",
    21: "analytical_semantic_layer_engineer",
    22: "data_qa_engineer",
    23: "data_platform_engineer",
    24: "distributed_data_engineer",
    25: "ux_product_designer",
    26: "data_visualization_engineer",
    27: "senior_backend_engineer",
    28: "distributed_job_processing_engineer",
    29: "frontend_engineer",
    30: "devops_engineer",
    31: "qa_automation_engineer",
    32: "compatibility_test_engineer",
}

PREVIOUS_ROLE_ALIASES = {
    26: {"ux_product_designer", "ux_designer"},
}


def execution(state: dict[str, Any]) -> dict[str, Any]:
    value = state.get("specialist_execution", {})
    return value if isinstance(value, dict) else {}


def gates(state: dict[str, Any]) -> dict[str, Any]:
    value = state.get("gates", {})
    return value if isinstance(value, dict) else {}


def is_authorized_specialist_handoff(
    state: dict[str, Any], *, minimum_current_step: int = 6, maximum_current_step: int = 41
) -> bool:
    """Return whether the durable pointer is a sequential specialist handoff."""

    specialist = execution(state)
    current_step = specialist.get("current_step")
    last_step = specialist.get("last_completed_step")
    if not isinstance(current_step, int) or not isinstance(last_step, int):
        return False
    if not minimum_current_step <= current_step <= maximum_current_step or last_step != current_step - 1:
        return False
    expected_role = CURRENT_ROLE_BY_STEP.get(current_step)
    if expected_role is None or specialist.get("current_role") != expected_role:
        return False
    previous_roles = PREVIOUS_ROLE_ALIASES.get(current_step)
    if previous_roles is None:
        previous_role = CURRENT_ROLE_BY_STEP.get(last_step)
        if previous_role is not None and specialist.get("last_completed_role") != previous_role:
            return False
    elif specialist.get("last_completed_role") not in previous_roles:
        return False
    return str(specialist.get("next_step", "")).startswith(f"Step{current_step}")


def step29_g7_closed(state: dict[str, Any]) -> bool:
    """Recognize the accepted Step29 real-pipeline/G7 closure."""

    specialist = execution(state)
    step29 = specialist.get("step29_frontend", {})
    return (
        isinstance(step29, dict)
        and step29.get("step29_started") is True
        and step29.get("status") == "PASS"
        and step29.get("g7_status") == "PASS"
        and specialist.get("step29_started") is True
        and specialist.get("step29_status") == "COMPLETED_FRONTEND_G7_PASS_REAL_PIPELINE"
        and gates(state).get("G7_END_TO_END_PRODUCT") == "PASS"
    )


def step30_handoff(state: dict[str, Any]) -> bool:
    specialist = execution(state)
    step29 = specialist.get("step29_frontend", {})
    step30_started = specialist.get("step30_started", step29.get("step30_started") if isinstance(step29, dict) else None)
    step30_status = specialist.get("step30_status", step29.get("step30_status") if isinstance(step29, dict) else None)
    return (
        is_authorized_specialist_handoff(state, minimum_current_step=30, maximum_current_step=30)
        and specialist.get("last_completed_step") == 29
        and specialist.get("last_completed_role") == "frontend_engineer"
        and step30_started is False
        and step30_status == "NOT_STARTED"
        and gates(state).get("G7_END_TO_END_PRODUCT") == "PASS"
        and state.get("blocked") is False
    )


def step30_g8_closed(state: dict[str, Any]) -> bool:
    """Recognize the accepted Step30/G8 closure and Step31 handoff."""

    specialist = execution(state)
    return (
        is_authorized_specialist_handoff(state, minimum_current_step=31, maximum_current_step=31)
        and specialist.get("last_completed_step") == 30
        and specialist.get("last_completed_role") == "devops_engineer"
        and specialist.get("last_completed_specialist") == "Step30 - DevOps Engineer"
        and specialist.get("step30_started") is True
        and specialist.get("step30_status") == "COMPLETED_DEVOPS_G8_PASS"
        and specialist.get("step31_started") is False
        and specialist.get("step31_status") == "NOT_STARTED"
        and gates(state).get("G8_REPRODUCIBLE_BUILD") == "PASS"
        and state.get("blocked") is False
    )


def step31_qa_closed(state: dict[str, Any]) -> bool:
    """Recognize the accepted Step31 QA closure and Step32 handoff."""

    specialist = execution(state)
    qa = specialist.get("step31_qa_automation", {})
    return (
        is_authorized_specialist_handoff(state, minimum_current_step=32, maximum_current_step=32)
        and specialist.get("last_completed_step") == 31
        and specialist.get("last_completed_role") == "qa_automation_engineer"
        and specialist.get("last_completed_specialist") == "Step31 - QA Automation Engineer"
        and specialist.get("last_completed_content_commit") == (qa.get("content_commit") if isinstance(qa, dict) else None)
        and specialist.get("step31_started") is True
        and specialist.get("step31_status") == "COMPLETED_QA_AUTOMATION"
        and specialist.get("step32_started") is False
        and specialist.get("step32_status") == "NOT_STARTED"
        and isinstance(qa, dict)
        and qa.get("step31_started") is True
        and qa.get("status") == "PASS"
        and gates(state).get("G6_DATA_CORRECTNESS") == "PASS"
        and gates(state).get("G7_END_TO_END_PRODUCT") == "PASS"
        and gates(state).get("G8_REPRODUCIBLE_BUILD") == "PASS"
        and state.get("blocked") is False
    )


def prior_gate_state_is_coherent(state: dict[str, Any]) -> bool:
    current_gates = gates(state)
    return (
        current_gates.get("G3_SOURCE_SAFETY") in {"PENDING", "PASS", "BLOCKED"}
        and current_gates.get("G4_BOUNDED_INTELLIGENCE") in {"PENDING", "PASS"}
        and current_gates.get("G5_INFERENCE_VALIDITY") in {"PENDING", "REVIEW_ONLY_VALIDATED", "PASS"}
        and current_gates.get("G6_DATA_CORRECTNESS") in {"PENDING", "PASS"}
        and all(
            current_gates.get(key) == "PENDING"
            or (key == "G7_END_TO_END_PRODUCT" and step29_g7_closed(state))
            or (key == "G8_REPRODUCIBLE_BUILD" and step30_g8_closed(state))
            for key in ("G7_END_TO_END_PRODUCT", "G8_REPRODUCIBLE_BUILD", "G9_FUNCTIONAL_SUPPORT", "G10_APPLICATION_SECURITY", "G11_RESILIENCE", "G12_CAPACITY", "G13_ADVERSARIAL_SECURITY", "G14_USABILITY", "G15_RELEASE")
        )
    )
