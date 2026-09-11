import subprocess
import sys
import json
from pathlib import Path

import pytest

from dirty_data_to_olap.application.canonical import CanonicalFinalizationService
from dirty_data_to_olap.application.review_policy import ReviewPolicyService
from dirty_data_to_olap.domain.contracts.canonical import CanonicalIdentityProposal, CanonicalModelHypothesis, ReviewDecision
from dirty_data_to_olap.domain.contracts.entity_resolution import EntityResolutionResult


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
