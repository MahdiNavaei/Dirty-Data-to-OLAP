import json

from dirty_data_to_olap.application.evidence_fusion import EvidenceFusionService
from dirty_data_to_olap.domain.contracts.evidence_fusion import *


def test_untrusted_qualitative_canary_is_not_published_in_fusion_contracts():
    policy = FusionPolicyReference(policy_id="p", version="1", status=FusionPolicyStatus.UNCALIBRATED, content_hash="hash")
    item = FusionEvidenceItem(evidence_id="e", subject_id="rel:orders:id->customers:id", producer_id="p", family=EvidenceFamily.SEMANTIC_AI, role=EvidenceRole.DERIVED_INTERPRETATION, metric_name="semantic", metric_value=None, metric_semantics="qualitative", direction=EvidenceDirection.CONTEXT, scope_id="scope", correlation_group="e", score_bearing=False, qualitative_text="synthetic.person@example.test")
    request = EvidenceFusionRequest(request_id="privacy", execution_context_id="privacy", relationship_candidate_ids=("rel-1",), policy=policy)
    statuses = tuple(ProducerEvidenceStatus(producer_id=name, family=family, state=ProducerResultState.COMPLETE, result_id=name) for name, family in (("p", EvidenceFamily.PROFILE), ("d", EvidenceFamily.DEPENDENCY), ("q", EvidenceFamily.QUALITY)))
    result = EvidenceFusionService().fuse(request, EvidenceFusionInputs(producer_statuses=statuses, relationship_candidates=({"candidate_id":"rel-1","from_table":"orders","from_columns":("id",),"to_table":"customers","to_columns":("id",)},), evidence_items=(item,)))
    encoded = json.dumps(result.model_dump(mode="json"))
    assert "synthetic.person@example.test" not in encoded
