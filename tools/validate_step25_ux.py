"""Executable design-contract validator for Specialist Step 25.

This validator checks the UX contract against the project-owned source models.
It deliberately validates design boundaries and does not pretend that a
frontend, browser flow, user research, or later specialist implementation
exists.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping

import yaml


ROOT = Path(__file__).resolve().parents[1]
UX_ROOT = ROOT / "docs" / "ux"
SPEC_PATH = UX_ROOT / "specs" / "review_experience.yml"
STATES_PATH = UX_ROOT / "specs" / "interaction_states.yml"

REQUIRED_DOCS = (
    "README.md",
    "USER_JOURNEY.md",
    "INFORMATION_ARCHITECTURE.md",
    "REVIEW_WORKFLOWS.md",
    "REVIEW_OBJECT_PATTERN.md",
    "EVIDENCE_AND_UNCERTAINTY_PRESENTATION.md",
    "CONTENT_AND_TERMINOLOGY_GUIDELINES.md",
    "ERROR_EMPTY_PARTIAL_STATES.md",
    "BULK_ACTION_AND_HIGH_IMPACT_SAFETY.md",
    "ACCESSIBILITY_REQUIREMENTS.md",
    "COGNITIVE_WALKTHROUGHS.md",
)

CHECKPOINTS = (
    "REVIEW_EVIDENCE_DECISIONS",
    "REVIEW_CANONICAL_IDENTITY",
    "REVIEW_ANALYTICAL_PLAN",
    "REVIEW_MATERIALIZATION_PLAN",
)

REVIEW_FIELDS = (
    "subject_stage",
    "subject_artifact_id",
    "subject_content_hash",
    "subject_schema_version",
    "model_version",
    "source_schema_fingerprints",
    "policy_version",
    "domain_assertion_refs",
    "subject_semantic_id",
    "applicability_fingerprint",
    "actor",
    "actor_source",
    "reviewed_at",
    "rationale",
)

REQUIRED_STATES = (
    "NO_EVIDENCE",
    "NO_CANDIDATES",
    "CAPABILITY_UNAVAILABLE",
    "PROVIDER_FAILED",
    "TIMEOUT",
    "INCOMPLETE",
    "PRIVACY_BLOCKED",
    "REVIEW_REQUIRED",
    "STALE",
    "INVALIDATED",
    "SUPERSEDED",
    "TARGET_CHANGED",
    "VALIDATION_FAILED",
    "NOT_EVALUATED",
    "NOT_APPLICABLE",
    "PARTIAL_SUCCESS",
    "LONG_RUNNING",
)


def _check(checks: list[dict[str, Any]], name: str, condition: bool, detail: str) -> None:
    checks.append({"name": name, "status": "PASS" if condition else "FAIL", "detail": detail})


def _read(root: Path, relative: str) -> str:
    return (root / relative).read_text(encoding="utf-8")


def _evaluate(
    spec: Mapping[str, Any],
    states_doc: Mapping[str, Any],
    source_texts: Mapping[str, str],
    docs_text: Mapping[str, str],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    checkpoints = spec.get("exact_checkpoints", [])
    checkpoint_ids = [item.get("checkpoint_id") for item in checkpoints]
    _check(checks, "step is 25", spec.get("step") == 25, "the contract is scoped to Step25")
    _check(checks, "design-only scope", spec.get("status") == "DESIGN_CONTRACT_ONLY", "no application implementation is claimed")
    _check(checks, "exact four checkpoints", tuple(checkpoint_ids) == CHECKPOINTS, "checkpoint order and names match the architecture contract")
    _check(checks, "checkpoint IDs are unique", len(checkpoint_ids) == len(set(checkpoint_ids)), "no review checkpoint is shadowed")

    review_source = source_texts.get("canonical", "")
    review_policy = source_texts.get("review_policy", "")
    evidence_source = source_texts.get("evidence", "")
    quality_source = source_texts.get("quality", "")
    analytical_source = source_texts.get("analytical", "")
    validation_source = source_texts.get("validation", "")
    privacy_source = source_texts.get("privacy", "")
    architecture_source = source_texts.get("architecture", "")

    _check(checks, "ReviewDecision source contract exists", "class ReviewDecision" in review_source, "actual project-owned review model is present")
    _check(checks, "ReviewDecision exact subject fields", all(f"    {field}:" in review_source or f"    {field} =" in review_source for field in REVIEW_FIELDS), "subject, actor and rationale are source-bound")
    _check(checks, "review invalidation is explicit", "ReviewDecisionStatus.INVALIDATED" in review_source and "invalidation_reason" in review_source and "superseded_by" in review_source, "stale decisions preserve history and require a reason/link")
    _check(checks, "review satisfying guard is narrow", "ReviewDecisionStatus.ACCEPTED, ReviewDecisionStatus.SKIPPED" in review_source, "rejected/deferred/invalidated decisions cannot satisfy compatibility")
    _check(checks, "review policy owns compatibility", "class ReviewPolicyService" in review_policy and "def require_compatible" in review_policy, "the UI contract points to the reusable semantic authority")
    _check(checks, "architecture names all checkpoints", all(item in architecture_source for item in CHECKPOINTS), "UX does not invent a parallel checkpoint model")

    spec_fields = set(spec.get("review_object_required_fields", []))
    required_object_fields = {"subject", "state", "meaning", "risk", "evidence", "conflicts", "missing", "scope", "provenance", "downstream", "technical_details", "actions", "freshness"}
    _check(checks, "review object is complete", required_object_fields <= spec_fields, "cards expose subject, evidence, scope, provenance, actions and freshness")
    for item in checkpoints:
        fields = set(item.get("review_fields", []))
        _check(checks, f"{item.get('checkpoint_id')} exact context", set(REVIEW_FIELDS) <= fields, "each checkpoint captures exact subject context and human decision metadata")
        _check(checks, f"{item.get('checkpoint_id')} has consequence", bool(item.get("guarded_stage")) and bool(item.get("invalidating_changes")), "guarded stage and re-review triggers are visible")

    evidence_terms = (
        "raw_metric_value",
        "raw_metric_semantics",
        "normalized_value",
        "presence",
        "reliability",
        "supporting_evidence_refs",
        "contradicting_evidence_refs",
        "missing_evidence_refs",
        "unavailable_evidence_refs",
    )
    _check(checks, "evidence source preserves uncertainty", all(term in evidence_source for term in evidence_terms), "raw semantics, provider status, conflict and missing references are typed")
    _check(checks, "score is not probability", spec.get("evidence_semantics", {}).get("raw_score_is_probability") is False and spec.get("evidence_semantics", {}).get("probability_requires_explicit_calibration") is True, "uncalibrated scores remain decision/ranking evidence")
    _check(checks, "human/model meanings are separate", spec.get("evidence_semantics", {}).get("human_decision_is_model_evidence") is False and "HUMAN_OR_DOMAIN_ASSERTION" in evidence_source, "actor decision and producer evidence are distinguishable")
    _check(checks, "sample is not full", spec.get("evidence_semantics", {}).get("sample_is_full_snapshot") is False and "SAMPLED" in evidence_source, "sampled and full observations cannot be conflated")
    _check(checks, "conflicts are explicit", "class Conflict" in evidence_source and "conflict_refs" in evidence_source, "contradiction is first-class")
    _check(checks, "provider failures are distinct", all(term in evidence_source for term in ("UNAVAILABLE", "FAILED", "INCOMPLETE", "PRIVACY_BLOCKED")), "unavailable, failed, incomplete and privacy-blocked are not one empty state")

    quality_terms = ("observation_scope", "measurement_semantics", "affected_count", "evidence_refs", "repair_proposal_refs", "provenance")
    _check(checks, "quality review has scope and provenance", all(term in quality_source for term in quality_terms), "quality issues identify where/how they were measured")
    _check(checks, "quality failures are explicit", "class QualityFailure" in quality_source and "INCOMPLETE_PROFILE" in quality_source, "failed/incomplete quality runs are not clean results")
    analytical_terms = ("class GrainSpec", "class MeasureSpec", "AggregationClass", "review_state", "provenance_refs")
    _check(checks, "analytical review has grain and measures", all(term in analytical_source for term in analytical_terms), "OLAP meaning is reviewed downstream of canonical semantics")
    _check(checks, "nonadditive measure guard", "NON_ADDITIVE" in analytical_source and spec.get("evidence_semantics", {}).get("nonadditive_measure_default_aggregation") == "forbidden", "non-additive measures have no default additive action")
    validation_terms = ("PASS", "FAIL", "REVIEW_REQUIRED", "NOT_APPLICABLE", "NOT_EVALUATED", "g6_eligible", "expected_gate")
    _check(checks, "validation statuses are typed", all(term in validation_source for term in validation_terms), "validation result is derived from required checks")
    _check(checks, "validation cannot be promoted", "validation status does not match required check statuses" in validation_source and "expected_gate" in validation_source, "FAIL/NOT_EVALUATED/REVIEW_REQUIRED cannot be laundered into PASS")
    _check(checks, "cluster is not canonical", spec.get("evidence_semantics", {}).get("cluster_is_canonical_identity") is False and "EntityCluster" in architecture_source, "identity membership remains distinct from canonical identity")
    _check(checks, "privacy boundary is real", "EXPOSURE_BLOCKED" in privacy_source and "raw_value_allowed" in privacy_source, "privacy failure state and exposure controls are source-owned")

    state_items = states_doc.get("states", [])
    state_ids = [item.get("state_id") for item in state_items]
    _check(checks, "interaction state registry is complete", tuple(state_ids) == REQUIRED_STATES, "all required empty, error, partial, stale and terminal states exist")
    _check(checks, "interaction state IDs are unique", len(state_ids) == len(set(state_ids)), "state labels cannot collide")
    required_state_fields = set(states_doc.get("state_contract", {}).get("required_fields", []))
    _check(checks, "state records are actionable", all(required_state_fields <= set(item) for item in state_items), "each state gives meaning, scope, recovery and consequence")
    _check(checks, "partial success is bounded", states_doc.get("state_contract", {}).get("partial_success_is_not_overall_success") is True and next((item for item in state_items if item.get("state_id") == "PARTIAL_SUCCESS"), {}).get("downstream_consequence") == "No overall success claim", "partial completion cannot be reported as overall success")
    _check(checks, "failure is not negative evidence", states_doc.get("state_contract", {}).get("failure_is_not_negative_evidence") is True, "provider failure does not become a negative finding")

    bulk = spec.get("bulk_policy", {})
    _check(checks, "bulk review is bounded", all(bulk.get(key) is True for key in ("bounded_scope_required", "preview_required", "conflict_exclusion_required", "stale_exclusion_required", "partial_selection_must_be_visible", "confirmation_and_audit_required")), "bulk controls require scope, preview, exclusions and audit")
    _check(checks, "mass high-impact action is blocked", bulk.get("arbitrary_mass_entity_merge_allowed") is False and bulk.get("high_impact_mass_approval_allowed") is False, "identity merges and high-impact approvals remain item-level")
    accessibility = spec.get("accessibility", {})
    _check(checks, "accessibility is nonvisual", accessibility.get("color_alone_sufficient") is False and accessibility.get("keyboard_operable") is True and accessibility.get("nonvisual_relationship_and_lineage_representation") == "required", "status and graph meaning do not depend on color or pointer input")
    _check(checks, "privacy-safe UX examples", spec.get("privacy", {}).get("raw_pii_in_urls_navigation_audit_or_fixtures") == "forbidden" and spec.get("privacy", {}).get("allowed_example_identifiers") == "synthetic_opaque_ids_only", "raw PII is excluded from UX surfaces")

    all_docs = "\n".join(docs_text.values()).lower()
    _check(checks, "docs state no frontend claim", "does not implement a frontend" in all_docs and "not user research" in all_docs, "Step25 records a design contract and walkthrough, not product usability evidence")
    _check(checks, "docs include ten walkthroughs", len([line for line in docs_text.get("COGNITIVE_WALKTHROUGHS.md", "").splitlines() if line.startswith("|") and "Case" not in line and "---" not in line]) >= 10, "the required cognitive cases are explicit")
    _check(checks, "docs include exact subject language", "exact subject" in all_docs and "content hash" in all_docs and "schema fingerprint" in all_docs, "reviewer-facing copy preserves identity and freshness")
    _check(checks, "docs include privacy and recovery", "privacy_blocked" in all_docs and "retry" in all_docs and "invalidation" in all_docs, "blocked and recoverable states are user-visible")
    _check(checks, "docs prohibit unsafe mass merge", "no arbitrary mass merge" in all_docs and "item-level review" in all_docs, "high-impact bulk actions are visibly distinct")

    handoff = spec.get("handoff", {})
    _check(checks, "Step26 handoff is explicit", handoff.get("next_step") == 26 and handoff.get("next_role") == "data_visualization_engineer" and handoff.get("step26_implementation_authorized") is False, "Step25 does not begin Step26")
    _check(checks, "no Step25 application implementation", not (ROOT / "src" / "dirty_data_to_olap" / "application" / "ux.py").exists(), "the UX pass adds contracts/tests only")
    return checks


def validate(root: Path = ROOT) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for relative in REQUIRED_DOCS:
        _check(checks, f"document exists: {relative}", (root / "docs" / "ux" / relative).is_file(), "required UX contract document is present")
    try:
        spec = yaml.safe_load(_read(root, "docs/ux/specs/review_experience.yml"))
        states_doc = yaml.safe_load(_read(root, "docs/ux/specs/interaction_states.yml"))
        _check(checks, "review_experience YAML parses", isinstance(spec, dict), "machine-readable review contract loaded")
        _check(checks, "interaction_states YAML parses", isinstance(states_doc, dict), "machine-readable state contract loaded")
    except (OSError, yaml.YAMLError) as exc:
        _check(checks, "machine-readable UX specs parse", False, str(exc))
        return checks
    source_texts = {
        "canonical": _read(root, "src/dirty_data_to_olap/domain/contracts/canonical.py"),
        "review_policy": _read(root, "src/dirty_data_to_olap/application/review_policy.py"),
        "evidence": _read(root, "src/dirty_data_to_olap/domain/contracts/evidence_fusion.py"),
        "quality": _read(root, "src/dirty_data_to_olap/domain/contracts/quality.py"),
        "analytical": _read(root, "src/dirty_data_to_olap/domain/contracts/analytical.py"),
        "validation": _read(root, "src/dirty_data_to_olap/domain/contracts/validation.py"),
        "privacy": _read(root, "src/dirty_data_to_olap/domain/contracts/privacy.py"),
        "architecture": _read(root, "docs/architecture/SOFTWARE_ARCHITECTURE_CONTRACT.md") + _read(root, "docs/architecture/specs/review_checkpoints.yml"),
    }
    docs_text = {relative: _read(root, f"docs/ux/{relative}") for relative in REQUIRED_DOCS if (root / "docs" / "ux" / relative).is_file()}
    return checks + _evaluate(spec, states_doc, source_texts, docs_text)


def main() -> int:
    checks = validate()
    failures = [item for item in checks if item["status"] != "PASS"]
    for item in checks:
        print(f"[{item['status']}] {item['name']}: {item['detail']}")
    print(f"RESULT={'PASS' if not failures else 'FAIL'} checks={len(checks)} failures={len(failures)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
