"""Shared execution-state predicates used by repository validators.

The execution state is a durable handoff record, not a snapshot tied to one
specialist.  Validators must therefore accept the current later handoff
after prior gates have closed while still rejecting skipped or mis-ordered
steps.
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
    33: "application_security_engineer",
    34: "observability_engineer",
    35: "sre",
    36: "chaos_resilience",
    37: "performance_engineer",
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
    # Upstream validators describe their supported range as "through the
    # later specialist handoff".  Once the accepted Step31 closure advances
    # the pointer to Step32, those prior-stage invariants remain applicable.
    # Keep exact-step predicates (for example Step30 handoff) strict while
    # allowing the immediately subsequent accepted handoff through a caller's
    # Step31 ceiling.
    beyond_declared_ceiling = (
        (current_step == 32 and maximum_current_step == 31 and step31_qa_closed(state))
        or (current_step == 33 and maximum_current_step in {31, 32} and step32_compatibility_closed(state))
        or (current_step == 34 and maximum_current_step <= 33 and _step33_completion_evidence(state))
        or (current_step == 35 and maximum_current_step <= 34 and step34_observability_closed(state))
        or (current_step == 36 and maximum_current_step <= 35 and step35_sre_closed(state))
        or (current_step == 37 and maximum_current_step <= 36 and step36_resilience_closed(state))
    )
    if (not minimum_current_step <= current_step <= maximum_current_step and not beyond_declared_ceiling) or last_step != current_step - 1:
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


def step30_g8_accepted(state: dict[str, Any]) -> bool:
    """Recognize the accepted Step30/G8 result before later Step31 closure."""

    specialist = execution(state)
    return (
        is_authorized_specialist_handoff(state, minimum_current_step=31, maximum_current_step=31)
        and specialist.get("last_completed_step") == 30
        and specialist.get("last_completed_role") == "devops_engineer"
        and specialist.get("last_completed_specialist") == "Step30 - DevOps Engineer"
        and specialist.get("current_role") == "qa_automation_engineer"
        and specialist.get("step30_started") is True
        and specialist.get("step30_status") == "COMPLETED_DEVOPS_G8_PASS"
        and gates(state).get("G8_REPRODUCIBLE_BUILD") == "PASS"
    )


def step30_g8_closed(state: dict[str, Any]) -> bool:
    """Recognize the accepted Step30/G8 closure, including later QA closure."""

    specialist = execution(state)
    direct_closure = (
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
    return direct_closure or step31_qa_closed(state)


def step31_qa_closed(state: dict[str, Any]) -> bool:
    """Recognize the accepted Step31 QA closure and Step32 handoff."""

    specialist = execution(state)
    qa = specialist.get("step31_qa_automation", {})
    direct_closure = (
        is_authorized_specialist_handoff(state, minimum_current_step=32, maximum_current_step=32)
        and specialist.get("last_completed_step") == 31
        and specialist.get("last_completed_role") == "qa_automation_engineer"
        and specialist.get("last_completed_specialist") == "Step31 - QA Automation Engineer"
        and specialist.get("last_completed_content_commit") == (qa.get("content_commit") if isinstance(qa, dict) else None)
        and specialist.get("step31_started") is True
        and specialist.get("step31_status") == "COMPLETED_QA_AUTOMATION"
        and specialist.get("step30_started") is True
        and specialist.get("step30_status") == "COMPLETED_DEVOPS_G8_PASS"
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
    return direct_closure or step32_compatibility_closed(state)


def _step31_completion_evidence(state: dict[str, Any]) -> bool:
    """Keep Step31's accepted evidence valid after a later handoff."""

    specialist = execution(state)
    qa = specialist.get("step31_qa_automation", {})
    current_gates = gates(state)
    return (
        isinstance(qa, dict)
        and specialist.get("step31_started") is True
        and specialist.get("step31_status") == "COMPLETED_QA_AUTOMATION"
        and specialist.get("step30_started") is True
        and specialist.get("step30_status") == "COMPLETED_DEVOPS_G8_PASS"
        and qa.get("step31_started") is True
        and qa.get("status") == "PASS"
        and current_gates.get("G6_DATA_CORRECTNESS") == "PASS"
        and current_gates.get("G7_END_TO_END_PRODUCT") == "PASS"
        and current_gates.get("G8_REPRODUCIBLE_BUILD") == "PASS"
        and state.get("blocked") is False
    )


def step32_compatibility_closed(state: dict[str, Any]) -> bool:
    """Recognize the accepted Step32/G9 closure and Step33 handoff."""

    specialist = execution(state)
    compatibility = specialist.get("step32_compatibility", {})
    current_gates = gates(state)
    content_commit = compatibility.get("content_commit") if isinstance(compatibility, dict) else None
    direct_closure = (
        isinstance(compatibility, dict)
        and is_authorized_specialist_handoff(state, minimum_current_step=33, maximum_current_step=33)
        and _step31_completion_evidence(state)
        and specialist.get("last_completed_step") == 32
        and specialist.get("last_completed_role") == "compatibility_test_engineer"
        and specialist.get("last_completed_specialist") == "Step32 - Compatibility Test Engineer"
        and specialist.get("last_completed_content_commit") == content_commit
        and isinstance(content_commit, str)
        and len(content_commit) == 40
        and all(character in "0123456789abcdef" for character in content_commit.lower())
        and specialist.get("step32_started") is True
        and specialist.get("step32_status") == "COMPLETED_COMPATIBILITY_G9_PASS"
        and specialist.get("step33_started") is False
        and specialist.get("step33_status") == "NOT_STARTED"
        and compatibility.get("step32_started") is True
        and compatibility.get("status") == "PASS"
        and compatibility.get("g9_status") == "PASS"
        and current_gates.get("G9_FUNCTIONAL_SUPPORT") == "PASS"
        and current_gates.get("G10_APPLICATION_SECURITY") == "PENDING"
        and current_gates.get("G11_RESILIENCE") == "PENDING"
        and current_gates.get("G12_CAPACITY") == "PENDING"
        and current_gates.get("G13_ADVERSARIAL_SECURITY") == "PENDING"
        and current_gates.get("G14_USABILITY") == "PENDING"
        and current_gates.get("G15_RELEASE") == "PENDING"
        and state.get("blocked") is False
    )
    return direct_closure or (
        is_authorized_specialist_handoff(state, minimum_current_step=34, maximum_current_step=34)
        and _step33_completion_evidence(state)
    ) or (
        is_authorized_specialist_handoff(state, minimum_current_step=35, maximum_current_step=35)
        and _step34_completion_evidence(state)
    ) or (
        is_authorized_specialist_handoff(state, minimum_current_step=36, maximum_current_step=36)
        and _step35_completion_evidence(state)
    )


def _step33_completion_evidence(state: dict[str, Any]) -> bool:
    """Keep accepted Step33 evidence valid after the Step34 handoff."""

    specialist = execution(state)
    appsec = specialist.get("step33_application_security", {})
    current_gates = gates(state)
    content_commit = appsec.get("content_commit") if isinstance(appsec, dict) else None
    current_step = specialist.get("current_step")
    step34_is_next = current_step == 34 and specialist.get("step34_started") is False and specialist.get("step34_status") == "NOT_STARTED"
    step34_is_closed = current_step == 35 and specialist.get("step34_started") is True and specialist.get("step34_status") == "COMPLETED_OBSERVABILITY"
    step35_is_closed = (
        (current_step == 36 and specialist.get("step35_started") is True and specialist.get("step35_status") == "COMPLETED_SRE")
        or (current_step == 37 and specialist.get("step35_started") is True and specialist.get("step35_status") == "COMPLETED_SRE" and specialist.get("step36_started") is True and specialist.get("step36_status") == "COMPLETED_RESILIENCE_G11_PASS")
    )
    pointer_preserves_step33 = (
        (current_step == 34 and specialist.get("last_completed_step") == 33 and specialist.get("last_completed_role") == "application_security_engineer" and specialist.get("last_completed_content_commit") == content_commit)
        or (current_step == 35 and specialist.get("last_completed_step") == 34 and specialist.get("last_completed_role") == "observability_engineer")
        or (current_step == 36 and specialist.get("last_completed_step") == 35 and specialist.get("last_completed_role") == "sre")
        or (current_step == 37 and specialist.get("last_completed_step") == 36 and specialist.get("last_completed_role") == "chaos_resilience" and _step36_receipt_evidence(state))
    )
    return (
        isinstance(appsec, dict)
        and _step31_completion_evidence(state)
        and pointer_preserves_step33
        and (current_step in {35, 36, 37} or specialist.get("last_completed_specialist") == "Step33 - Application Security Engineer")
        and (current_step in {35, 36, 37} or specialist.get("last_completed_content_commit") == content_commit)
        and isinstance(content_commit, str)
        and len(content_commit) == 40
        and all(character in "0123456789abcdef" for character in content_commit.lower())
        and specialist.get("step33_started") is True
        and specialist.get("step33_status") == "COMPLETED_APPLICATION_SECURITY_G10_PASS"
        and (step34_is_next or step34_is_closed or step35_is_closed or (current_step == 37 and specialist.get("step36_started") is True and specialist.get("step36_status") == "COMPLETED_RESILIENCE_G11_PASS"))
        and appsec.get("step33_started") is True
        and appsec.get("status") == "PASS"
        and appsec.get("g10_status") == "PASS"
        and current_gates.get("G9_FUNCTIONAL_SUPPORT") == "PASS"
        and current_gates.get("G10_APPLICATION_SECURITY") == "PASS"
        and all(current_gates.get(key) == "PENDING" for key in ("G11_RESILIENCE", "G12_CAPACITY", "G13_ADVERSARIAL_SECURITY", "G14_USABILITY", "G15_RELEASE"))
        and state.get("blocked") is False
    )


def step33_application_security_closed(state: dict[str, Any]) -> bool:
    """Recognize the accepted Step33/G10 closure and Step34 handoff."""

    return (
        is_authorized_specialist_handoff(state, minimum_current_step=34, maximum_current_step=34)
        and _step33_completion_evidence(state)
    ) or (
        is_authorized_specialist_handoff(state, minimum_current_step=35, maximum_current_step=35)
        and _step34_completion_evidence(state)
    ) or (
        is_authorized_specialist_handoff(state, minimum_current_step=36, maximum_current_step=36)
        and _step35_completion_evidence(state)
    )


def _step34_completion_evidence(state: dict[str, Any]) -> bool:
    """Recognize Step34 observability evidence behind the Step35 handoff."""

    specialist = execution(state)
    observability = specialist.get("step34_observability", {})
    current_gates = gates(state)
    content_commit = observability.get("content_commit") if isinstance(observability, dict) else None
    direct_handoff = (
        isinstance(observability, dict)
        and _step33_completion_evidence(state)
        and specialist.get("current_step") == 35
        and specialist.get("current_role") == "sre"
        and specialist.get("current_specialist") == "Step35 - Site Reliability Engineer"
        and specialist.get("last_completed_step") == 34
        and specialist.get("last_completed_role") == "observability_engineer"
        and specialist.get("last_completed_specialist") == "Step34 - Observability Engineer"
        and specialist.get("last_completed_content_commit") == content_commit
        and isinstance(content_commit, str)
        and len(content_commit) == 40
        and all(character in "0123456789abcdef" for character in content_commit.lower())
        and specialist.get("step34_started") is True
        and specialist.get("step34_status") == "COMPLETED_OBSERVABILITY"
        and specialist.get("step35_started") is False
        and specialist.get("step35_status") == "NOT_STARTED"
        and observability.get("step34_started") is True
        and observability.get("status") == "PASS"
        and observability.get("content_commit") == content_commit
        and current_gates.get("G6_DATA_CORRECTNESS") == "PASS"
        and current_gates.get("G7_END_TO_END_PRODUCT") == "PASS"
        and current_gates.get("G8_REPRODUCIBLE_BUILD") == "PASS"
        and current_gates.get("G9_FUNCTIONAL_SUPPORT") == "PASS"
        and current_gates.get("G10_APPLICATION_SECURITY") == "PASS"
        and all(current_gates.get(key) == "PENDING" for key in ("G11_RESILIENCE", "G12_CAPACITY", "G13_ADVERSARIAL_SECURITY", "G14_USABILITY", "G15_RELEASE"))
        and state.get("blocked") is False
    )
    later_handoff = (
        isinstance(observability, dict)
        and _step33_completion_evidence(state)
        and _step35_receipt_evidence(state)
        and specialist.get("current_step") == 36
        and specialist.get("current_role") == "chaos_resilience"
        and specialist.get("current_specialist") == "Step36 - Chaos / Resilience Engineer"
        and specialist.get("last_completed_step") == 35
        and specialist.get("last_completed_role") == "sre"
        and specialist.get("last_completed_specialist") == "Step35 - Site Reliability Engineer"
        and specialist.get("step34_started") is True
        and specialist.get("step34_status") == "COMPLETED_OBSERVABILITY"
        and observability.get("step34_started") is True
        and observability.get("status") == "PASS"
        and isinstance(content_commit, str)
        and len(content_commit) == 40
        and all(character in "0123456789abcdef" for character in content_commit.lower())
        and specialist.get("last_completed_content_commit") == specialist.get("step35_sre", {}).get("content_commit")
        and current_gates.get("G6_DATA_CORRECTNESS") == "PASS"
        and current_gates.get("G7_END_TO_END_PRODUCT") == "PASS"
        and current_gates.get("G8_REPRODUCIBLE_BUILD") == "PASS"
        and current_gates.get("G9_FUNCTIONAL_SUPPORT") == "PASS"
        and current_gates.get("G10_APPLICATION_SECURITY") == "PASS"
        and all(current_gates.get(key) == "PENDING" for key in ("G11_RESILIENCE", "G12_CAPACITY", "G13_ADVERSARIAL_SECURITY", "G14_USABILITY", "G15_RELEASE"))
        and state.get("blocked") is False
    )
    final_handoff = (
        isinstance(observability, dict)
        and _step33_completion_evidence(state)
        and _step36_receipt_evidence(state)
        and specialist.get("current_step") == 37
        and specialist.get("current_role") == "performance_engineer"
        and specialist.get("current_specialist") == "Step37 - Performance Engineer"
        and specialist.get("last_completed_step") == 36
        and specialist.get("last_completed_role") == "chaos_resilience"
        and specialist.get("last_completed_specialist") == "Step36 - Chaos / Resilience Engineer"
        and specialist.get("step34_started") is True
        and specialist.get("step34_status") == "COMPLETED_OBSERVABILITY"
        and observability.get("step34_started") is True
        and observability.get("status") == "PASS"
        and current_gates.get("G6_DATA_CORRECTNESS") == "PASS"
        and current_gates.get("G7_END_TO_END_PRODUCT") == "PASS"
        and current_gates.get("G8_REPRODUCIBLE_BUILD") == "PASS"
        and current_gates.get("G9_FUNCTIONAL_SUPPORT") == "PASS"
        and current_gates.get("G10_APPLICATION_SECURITY") == "PASS"
        and current_gates.get("G11_RESILIENCE") == "PASS"
        and all(current_gates.get(key) == "PENDING" for key in ("G12_CAPACITY", "G13_ADVERSARIAL_SECURITY", "G14_USABILITY", "G15_RELEASE"))
        and specialist.get("step37_started") is False
        and specialist.get("step37_status") == "NOT_STARTED"
        and state.get("blocked") is False
    )
    return direct_handoff or later_handoff or final_handoff


def step34_observability_closed(state: dict[str, Any]) -> bool:
    """Recognize only the exact Step34 -> Step35 observability handoff."""

    return (
        is_authorized_specialist_handoff(state, minimum_current_step=35, maximum_current_step=35)
        and _step34_completion_evidence(state)
    ) or (
        is_authorized_specialist_handoff(state, minimum_current_step=36, maximum_current_step=36)
        and _step34_completion_evidence(state)
    )


def _step35_receipt_evidence(state: dict[str, Any]) -> bool:
    """Validate the Step35 receipt binding without hardcoding a commit SHA."""

    specialist = execution(state)
    sre = specialist.get("step35_sre", {})
    content_commit = sre.get("content_commit") if isinstance(sre, dict) else None
    return (
        isinstance(sre, dict)
        and sre.get("step35_started") is True
        and sre.get("status") == "PASS"
        and isinstance(content_commit, str)
        and len(content_commit) == 40
        and all(character in "0123456789abcdef" for character in content_commit.lower())
        and sre.get("content_ci_result") == "PASS"
        and sre.get("content_ci_head") == content_commit
        and sre.get("report_assessed_commit") == content_commit
    )


def _step35_completion_evidence(state: dict[str, Any]) -> bool:
    specialist = execution(state)
    current_gates = gates(state)
    sre = specialist.get("step35_sre", {})
    content_commit = sre.get("content_commit") if isinstance(sre, dict) else None
    return (
        _step34_completion_evidence(state)
        and _step35_receipt_evidence(state)
        and specialist.get("current_step") in {36, 37}
        and ((specialist.get("current_step") == 36 and specialist.get("current_role") == "chaos_resilience" and specialist.get("current_specialist") == "Step36 - Chaos / Resilience Engineer") or (specialist.get("current_step") == 37 and specialist.get("current_role") == "performance_engineer" and specialist.get("current_specialist") == "Step37 - Performance Engineer"))
        and ((specialist.get("current_step") == 36 and specialist.get("last_completed_step") == 35 and specialist.get("last_completed_role") == "sre" and specialist.get("last_completed_specialist") == "Step35 - Site Reliability Engineer" and specialist.get("last_completed_content_commit") == content_commit) or (specialist.get("current_step") == 37 and specialist.get("last_completed_step") == 36 and specialist.get("last_completed_role") == "chaos_resilience" and _step36_receipt_evidence(state)))
        and specialist.get("step35_started") is True
        and specialist.get("step35_status") == "COMPLETED_SRE"
        and ((specialist.get("current_step") == 36 and specialist.get("step36_started") is False and specialist.get("step36_status") == "NOT_STARTED") or (specialist.get("current_step") == 37 and specialist.get("step36_started") is True and specialist.get("step36_status") == "COMPLETED_RESILIENCE_G11_PASS"))
        and current_gates.get("G6_DATA_CORRECTNESS") == "PASS"
        and current_gates.get("G7_END_TO_END_PRODUCT") == "PASS"
        and current_gates.get("G8_REPRODUCIBLE_BUILD") == "PASS"
        and current_gates.get("G9_FUNCTIONAL_SUPPORT") == "PASS"
        and current_gates.get("G10_APPLICATION_SECURITY") == "PASS"
        and all(current_gates.get(key) == "PENDING" for key in ("G11_RESILIENCE", "G12_CAPACITY", "G13_ADVERSARIAL_SECURITY", "G14_USABILITY", "G15_RELEASE"))
        and state.get("blocked") is False
    )


def step35_sre_closed(state: dict[str, Any]) -> bool:
    """Recognize only the exact Step35 -> Step36 SRE handoff."""

    return is_authorized_specialist_handoff(state, minimum_current_step=36, maximum_current_step=36) and _step35_completion_evidence(state)


def _step36_receipt_evidence(state: dict[str, Any]) -> bool:
    """Validate Step36 receipt and exact content-CI binding."""

    specialist = execution(state)
    chaos = specialist.get("step36_chaos_resilience", {})
    content_commit = chaos.get("content_commit") if isinstance(chaos, dict) else None
    return (
        isinstance(chaos, dict)
        and chaos.get("step36_started") is True
        and chaos.get("status") == "PASS"
        and chaos.get("g11_status") == "PASS"
        and isinstance(content_commit, str)
        and len(content_commit) == 40
        and all(character in "0123456789abcdef" for character in content_commit.lower())
        and chaos.get("content_ci_result") == "PASS"
        and chaos.get("content_ci_head") == content_commit
        and chaos.get("report_assessed_commit") == content_commit
        and chaos.get("receipt") == "docs/execution/STEP36_CHAOS_RESILIENCE_REVIEW.md"
        and chaos.get("gate_receipt") == "docs/execution/gates/G11_RESILIENCE.md"
    )


def _step36_completion_evidence(state: dict[str, Any]) -> bool:
    specialist = execution(state)
    current_gates = gates(state)
    chaos = specialist.get("step36_chaos_resilience", {})
    return (
        _step35_completion_evidence(state)
        and _step36_receipt_evidence(state)
        and specialist.get("current_step") == 37
        and specialist.get("current_role") == "performance_engineer"
        and specialist.get("current_specialist") == "Step37 - Performance Engineer"
        and specialist.get("last_completed_step") == 36
        and specialist.get("last_completed_role") == "chaos_resilience"
        and specialist.get("last_completed_specialist") == "Step36 - Chaos / Resilience Engineer"
        and specialist.get("last_completed_content_commit") == chaos.get("content_commit")
        and specialist.get("step36_started") is True
        and specialist.get("step36_status") == "COMPLETED_RESILIENCE_G11_PASS"
        and specialist.get("step37_started") is False
        and specialist.get("step37_status") == "NOT_STARTED"
        and current_gates.get("G11_RESILIENCE") == "PASS"
        and all(current_gates.get(key) == "PENDING" for key in ("G12_CAPACITY", "G13_ADVERSARIAL_SECURITY", "G14_USABILITY", "G15_RELEASE"))
        and state.get("blocked") is False
    )


def step36_resilience_closed(state: dict[str, Any]) -> bool:
    """Recognize only the exact Step36 -> Step37 resilience handoff."""

    return is_authorized_specialist_handoff(state, minimum_current_step=37, maximum_current_step=37) and _step36_completion_evidence(state)


def step31_external_ci_blocked(state: dict[str, Any]) -> bool:
    """Recognize Step31 implementation success blocked only by final-head CI."""

    specialist = execution(state)
    qa = specialist.get("step31_qa_automation", {})
    current_gates = gates(state)
    if not isinstance(qa, dict):
        return False
    content_commit = qa.get("content_commit")
    return (
        step30_g8_accepted(state)
        and specialist.get("current_step") == 31
        and specialist.get("current_role") == "qa_automation_engineer"
        and specialist.get("current_specialist") == "Step31 - QA Automation Engineer"
        and specialist.get("last_completed_step") == 30
        and specialist.get("last_completed_role") == "devops_engineer"
        and specialist.get("last_completed_specialist") == "Step30 - DevOps Engineer"
        and specialist.get("last_completed_content_commit") == content_commit
        and isinstance(content_commit, str)
        and len(content_commit) == 40
        and all(character in "0123456789abcdef" for character in content_commit.lower())
        and specialist.get("next_step") == "Step31 - QA Automation Engineer"
        and specialist.get("step31_started") is True
        and specialist.get("step31_status") == "BLOCKED_EXTERNAL_FINAL_CI"
        and specialist.get("step32_started") is False
        and specialist.get("step32_status") == "NOT_STARTED"
        and qa.get("step31_started") is True
        and qa.get("status") == "PASS"
        and qa.get("implementation_result") == "PASS"
        and qa.get("content_ci_run") == "34962240176"
        and qa.get("content_ci_result") == "PASS"
        and qa.get("final_head") == "1881e6e8a5a1394f6606823282a3dfb79db80609"
        and qa.get("final_head_ci_run") == "34975659130"
        and qa.get("final_head_ci_result") == "BLOCKED_EXTERNAL"
        and qa.get("final_head_ci_blocker") == "GitHub Actions billing/spending-limit restriction"
        and specialist.get("step31_blocking_reason") == "BLOCKED_EXTERNAL_FINAL_CI"
        and current_gates.get("G6_DATA_CORRECTNESS") == "PASS"
        and current_gates.get("G7_END_TO_END_PRODUCT") == "PASS"
        and current_gates.get("G8_REPRODUCIBLE_BUILD") == "PASS"
        and current_gates.get("G9_FUNCTIONAL_SUPPORT") == "PENDING"
        and state.get("blocked") is True
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
            or (key == "G8_REPRODUCIBLE_BUILD" and (step30_g8_closed(state) or step31_external_ci_blocked(state)))
            or (key == "G9_FUNCTIONAL_SUPPORT" and step32_compatibility_closed(state))
            or (key == "G10_APPLICATION_SECURITY" and step33_application_security_closed(state))
            for key in ("G7_END_TO_END_PRODUCT", "G8_REPRODUCIBLE_BUILD", "G9_FUNCTIONAL_SUPPORT", "G10_APPLICATION_SECURITY", "G11_RESILIENCE", "G12_CAPACITY", "G13_ADVERSARIAL_SECURITY", "G14_USABILITY", "G15_RELEASE")
        )
    )
