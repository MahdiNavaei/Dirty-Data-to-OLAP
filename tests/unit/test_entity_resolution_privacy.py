from __future__ import annotations

from pathlib import Path

from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService
from dirty_data_to_olap.domain.contracts.entity_resolution import (
    ERBlockingRule, ERClusteringPolicy, ERComparisonSpecification, EntityResolutionMode,
    ERThresholdPolicy, ERTrainingPolicy, EntityResolutionSpec, IdentityFieldSpecification,
    EntityResolutionNormalizationRule,
)


def _spec():
    fields = tuple(IdentityFieldSpecification(field_id="email", source_id=source, snapshot_id=f"s-{source}", table_id=f"t-{source}", column_id=f"c-{source}", physical_name="email", semantic_role="email", normalization_rule_id="norm-email") for source in ("a", "b"))
    return EntityResolutionSpec(spec_id="privacy-er", entity_family="person", mode=EntityResolutionMode.LINK_ONLY, source_ids=("a", "b"), snapshot_ids={"a": "s-a", "b": "s-b"}, table_ids_by_source={"a": ("t-a",), "b": ("t-b",)}, identity_fields=fields, normalization_rules=(EntityResolutionNormalizationRule(rule_id="norm-email", version="1", applies_to=("email",)),), blocking_rules=(ERBlockingRule(rule_id="block-email", version="1", field_ids=("email",), sql_expression="l.email = r.email"),), comparisons=(ERComparisonSpecification(comparison_id="cmp-email", field_id="email"),), training_policy=ERTrainingPolicy(em_blocking_rule_ids=("block-email",)), threshold_policy=ERThresholdPolicy(), clustering_policy=ERClusteringPolicy(threshold_policy_id="er-threshold-v1"))


def test_entity_resolution_authorization_binds_spec_and_exact_scope(tmp_path: Path):
    spec = _spec()
    service = PrivacyPolicyService(project_root=tmp_path)
    decision = service.authorize_entity_resolution_analysis(spec.privacy_context, spec=spec, source_ids=spec.source_ids, snapshot_ids=spec.snapshot_ids, table_ids_by_source=spec.table_ids_by_source, identity_column_ids=("c-a", "c-b"), batch_ids=("ba", "bb"))
    assert decision.allowed
    authorization = service.entity_resolution_authorization_for_decision(decision)
    assert service.verify_entity_resolution_authorization(authorization, spec=spec, source_ids=spec.source_ids, snapshot_ids=spec.snapshot_ids, table_ids_by_source=spec.table_ids_by_source, identity_column_ids=("c-a", "c-b"), batch_ids=("ba", "bb"))
    assert not service.verify_entity_resolution_authorization(authorization, spec=spec.model_copy(update={"spec_id": "changed"}), source_ids=spec.source_ids, snapshot_ids=spec.snapshot_ids, table_ids_by_source=spec.table_ids_by_source, identity_column_ids=("c-a", "c-b"), batch_ids=("ba", "bb"))
