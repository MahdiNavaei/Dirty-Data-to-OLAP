import subprocess
import sys
import json
from pathlib import Path

import pytest

from dirty_data_to_olap.application.canonical import CanonicalFinalizationService, CanonicalIdentityProposalService, CanonicalizationError
from dirty_data_to_olap.application.review_policy import ReviewPolicyService
from dirty_data_to_olap.domain.contracts.canonical import CanonicalIdentityMembership, CanonicalIdentityProposal, CanonicalModelHypothesis, IdentityDerivationBasis, ReviewDecision
from dirty_data_to_olap.domain.contracts.entity_resolution import EntityResolutionResult
from dirty_data_to_olap.domain.contracts.source import stable_digest


def test_step19_reference_flow_and_behavioral_validator_pass():
    root = Path(__file__).parents[2]
    run = subprocess.run([sys.executable, str(root / "tools" / "run_step19_reference.py")], cwd=root, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    validation = subprocess.run([sys.executable, str(root / "tools" / "validate_step19_canonical.py")], cwd=root, capture_output=True, text=True)
    assert validation.returncode == 0, validation.stderr + validation.stdout


def test_direct_services_consume_exact_reviewed_proposal_and_finalize_event():
    root = Path(__file__).parents[2]
    subprocess.run([sys.executable, str(root / "tools" / "run_step19_reference.py")], cwd=root, check=True, capture_output=True, text=True)
    run = root / "workspace" / "runs" / "step19-reference-run" / "canonical"
    hypothesis = CanonicalModelHypothesis.model_validate(json.loads((run / "canonical_model_hypothesis.json").read_text(encoding="utf-8")))
    proposal = CanonicalIdentityProposal.model_validate(json.loads((run / "canonical_identity_proposal.json").read_text(encoding="utf-8")))
    review = ReviewDecision.model_validate(json.loads((run / "review_canonical_identity.json").read_text(encoding="utf-8")))
    er = EntityResolutionResult.model_validate(json.loads((run / "entity_resolution_reference_result.json").read_text(encoding="utf-8")))
    model = CanonicalFinalizationService(ReviewPolicyService()).finalize(
        hypothesis=hypothesis,
        identity_proposal=proposal,
        identity_review=review,
        er_results={"person": er},
        source_record_metadata={"crm-r1": {"source_id": "crm", "snapshot_id": "snapshot-crm", "table_id": "customers"}, "erp-r1": {"source_id": "erp", "snapshot_id": "snapshot-erp", "table_id": "clients"}, "order-r1": {"source_id": "crm", "snapshot_id": "snapshot-crm", "table_id": "orders"}},
    )
    assert {item.canonical_entity_type_id for item in model.instances} == {"cet_customer", "cet_order"}
    assert {item.record_ref for item in model.source_record_maps} == {"crm-r1", "erp-r1", "order-r1"}
    changed = proposal.model_copy(update={"memberships": proposal.memberships[:-1]})
    with pytest.raises(ValueError):
        CanonicalFinalizationService(ReviewPolicyService()).finalize(hypothesis=hypothesis, identity_proposal=changed, identity_review=review, er_results={"person": er}, source_record_metadata={})


def _membership(record_refs, edge_refs):
    return CanonicalIdentityMembership(
        membership_group_id="test-membership",
        canonical_entity_type_id="cet_customer",
        entity_resolution_family="person",
        source_record_refs=tuple(record_refs),
        derivation_basis=IdentityDerivationBasis.ER_AUTHORIZED_LINKAGE,
        authorized_edge_refs=tuple(edge_refs),
        evidence_refs=tuple(edge_refs),
        policy_refs=("er-clustering-v1",),
        rationale="direct graph integrity test",
        provenance_refs=("integration-test",),
    )


def _graph_result(er, pairs):
    edges = tuple(er.edges[0].model_copy(update={"edge_id": edge_id, "left_record_ref": left, "right_record_ref": right}) for edge_id, left, right in pairs)
    return er.model_copy(update={"edges": edges, "clusters": ()})


def test_required_er_cannot_be_bypassed_by_human_override_and_graph_is_exactly_connected():
    root = Path(__file__).parents[2]
    subprocess.run([sys.executable, str(root / "tools" / "run_step19_reference.py")], cwd=root, check=True, capture_output=True, text=True)
    run = root / "workspace" / "runs" / "step19-reference-run" / "canonical"
    hypothesis = CanonicalModelHypothesis.model_validate(json.loads((run / "canonical_model_hypothesis.json").read_text(encoding="utf-8")))
    er = EntityResolutionResult.model_validate(json.loads((run / "entity_resolution_reference_result.json").read_text(encoding="utf-8")))
    human = CanonicalIdentityMembership(
        membership_group_id="human-required",
        canonical_entity_type_id="cet_customer",
        entity_resolution_family="person",
        source_record_refs=("crm-r1", "erp-r1"),
        derivation_basis=IdentityDerivationBasis.HUMAN_DOMAIN_REVIEW,
        actor="reviewer",
        actor_source="domain-review",
        domain_assertion_refs=("domain-assertion-customer-order",),
        evidence_refs=("domain-assertion-customer-order",),
        policy_refs=("manual-identity-v1",),
        rationale="explicitly reviewed interpretation",
        provenance_refs=("integration-test",),
    )
    builder = CanonicalIdentityProposalService()
    with pytest.raises(CanonicalizationError, match="MISSING_REQUIRED_ER"):
        builder.build(hypothesis=hypothesis, memberships=(human,), er_results={}, policy_refs=("canonical-identity-v1",), provenance_refs=("integration-test",))
    with pytest.raises(CanonicalizationError, match="INCOMPATIBLE_ER_RESULT"):
        builder.build(hypothesis=hypothesis, memberships=(human.model_copy(update={"source_record_refs": ("outside-r1",)}),), er_results={"person": er}, policy_refs=("canonical-identity-v1",), provenance_refs=("integration-test",))
    override = builder.build(hypothesis=hypothesis, memberships=(human,), er_results={"person": er}, policy_refs=("canonical-identity-v1",), provenance_refs=("integration-test",))
    assert override.er_result_hashes["person"] == stable_digest(er.model_dump(mode="json"))
    identity_context = CanonicalFinalizationService(ReviewPolicyService()).identity_context(hypothesis, override, {"person": stable_digest(er.model_dump(mode="json"))})
    assert identity_context.source_schema_fingerprints["er:person"] == override.er_result_hashes["person"]

    cases = {
        "partial": (("A", "B", "C"), (("e-ab", "A", "B"),)),
        "disconnected": (("A", "B", "C", "D"), (("e-ab", "A", "B"), ("e-cd", "C", "D"))),
        "outside-endpoint": (("A", "B"), (("e-ac", "A", "C"),)),
        "outside-selected-edge": (("A", "B"), (("e-ab", "A", "B"), ("e-cd", "C", "D"))),
    }
    for members, pairs in cases.values():
        result = _graph_result(er, pairs)
        with pytest.raises(CanonicalizationError):
            CanonicalIdentityProposalService.validate_er_membership(hypothesis, _membership(members, tuple(item[0] for item in pairs)), result, "person")
    connected = _graph_result(er, (("e-ab", "A", "B"), ("e-bc", "B", "C")))
    CanonicalIdentityProposalService.validate_er_membership(hypothesis, _membership(("A", "B", "C"), ("e-ab", "e-bc")), connected, "person")
    triangle = _graph_result(er, (("e-ab", "A", "B"), ("e-bc", "B", "C"), ("e-ac", "A", "C")))
    CanonicalIdentityProposalService.validate_er_membership(hypothesis, _membership(("A", "B", "C"), ("e-ab", "e-bc", "e-ac")), triangle, "person")
def test_finalizer_rechecks_required_er_and_er_hash_binding():
    root = Path(__file__).parents[2]
    subprocess.run([sys.executable, str(root / "tools" / "run_step19_reference.py")], cwd=root, check=True, capture_output=True, text=True)
    run = root / "workspace" / "runs" / "step19-reference-run" / "canonical"
    hypothesis = CanonicalModelHypothesis.model_validate(json.loads((run / "canonical_model_hypothesis.json").read_text(encoding="utf-8")))
    proposal = CanonicalIdentityProposal.model_validate(json.loads((run / "canonical_identity_proposal.json").read_text(encoding="utf-8")))
    review = ReviewDecision.model_validate(json.loads((run / "review_canonical_identity.json").read_text(encoding="utf-8")))
    er = EntityResolutionResult.model_validate(json.loads((run / "entity_resolution_reference_result.json").read_text(encoding="utf-8")))
    finalizer = CanonicalFinalizationService(ReviewPolicyService())
    with pytest.raises(CanonicalizationError, match="MISSING_REQUIRED_ER"):
        finalizer.finalize(hypothesis=hypothesis, identity_proposal=proposal, identity_review=review, er_results={}, source_record_metadata={})
    changed_er = er.model_copy(update={"edges": (er.edges[0].model_copy(update={"match_weight": er.edges[0].match_weight + 1.0}),)})
    with pytest.raises(CanonicalizationError, match="STALE_REVIEW"):
        finalizer.finalize(hypothesis=hypothesis, identity_proposal=proposal, identity_review=review, er_results={"person": changed_er}, source_record_metadata={})
    incompatible_er = er.model_copy(update={"spec": er.spec.model_copy(update={"spec_id": "different-er-spec"})})
    with pytest.raises(CanonicalizationError, match="INCOMPATIBLE_ER_RESULT"):
        finalizer.finalize(hypothesis=hypothesis, identity_proposal=proposal, identity_review=review, er_results={"person": incompatible_er}, source_record_metadata={})
