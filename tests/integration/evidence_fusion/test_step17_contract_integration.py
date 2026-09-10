from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from dirty_data_to_olap.application.evidence_fusion import EvidenceFusionService
from dirty_data_to_olap.domain.contracts.dependency import *
from dirty_data_to_olap.domain.contracts.evidence_fusion import *
from dirty_data_to_olap.domain.contracts.source import AdapterReference, ObservationMode


def _dependency_result() -> DependencyResult:
    scope = DependencyObservationScope(source_id="sales", snapshot_id="sales-snapshot", table_ids=("orders", "customers"), mode=ObservationMode.FULL, rows_by_table={"orders": 10, "customers": 4}, complete_by_table={"orders": True, "customers": True}, input_batch_ids=("batch-orders", "batch-customers"), input_batch_hashes=("hash-orders", "hash-customers"), input_record_reference_count=14)
    provenance = DependencyProvenance(source_id="sales", snapshot_id="sales-snapshot", table_ids=scope.table_ids, engine="project-test", engine_version="1", algorithm="bounded-ind-v1", algorithm_config_hash="config", adapter=AdapterReference(name="project-test", version="1", config_fingerprint="config"), created_at="2026-09-10T00:00:00Z")
    request = DependencyRequest(request_id="dependency-integration", source_id="sales", snapshot_id="sales-snapshot", selected_table_ids=scope.table_ids)
    ind = InclusionDependencyEvidence(evidence_id="ind-actual", source_id="sales", snapshot_id="sales-snapshot", left_table_id="orders", left_columns=("customer_id",), right_table_id="customers", right_columns=("id",), coverage_ratio=.97, violation_ratio=.03, left_distinct_count=100, right_distinct_count=4, orphan_count=3, target_uniqueness_ratio=1.0, type_compatible=True, null_policy=NullPolicy.EXCLUDE_PHYSICAL_NULL, observation_scope=scope, state=DependencyEvidenceState.OBSERVED, provenance=provenance)
    candidate = RelationshipCandidate(candidate_id="rel-actual", source_id="sales", snapshot_id="sales-snapshot", from_table="orders", from_columns=("customer_id",), to_table="customers", to_columns=("id",), source_orphan_ratio=.03, target_uniqueness_ratio=1.0, type_compatible=True, low_cardinality_risk=False, evidence_refs=("ind-actual",))
    stats = DependencySearchStats(input_tables=2, input_columns=2, candidate_column_pairs=1, pruned_column_pairs=0, evaluated_pairs=1, searched_determinants=0, emitted_candidates=1)
    return DependencyResult(request=request, observation_scope=scope, inclusion_dependencies=(ind,), relationship_candidates=(candidate,), search_stats=stats, status=DependencyStageStatus.COMPLETE)


def _statuses():
    return (
        ProducerEvidenceStatus(producer_id="profiling", family=EvidenceFamily.PROFILE, state=ProducerResultState.COMPLETE, result_id="profile-integration"),
        ProducerEvidenceStatus(producer_id="quality", family=EvidenceFamily.QUALITY, state=ProducerResultState.COMPLETE, result_id="quality-integration"),
    )


def test_actual_dependency_contract_fuses_and_publishes_byte_hashed_result():
    root = Path("workspace/test-temp/evidence-fusion/integration").resolve()
    if root.exists():
        shutil.rmtree(root)
    try:
        result = EvidenceFusionService(artifact_root=root).fuse(EvidenceFusionRequest(request_id="integration", execution_context_id="integration", relationship_candidate_ids=("rel-actual",), policy=EvidenceFusionService.load_policy()), EvidenceFusionInputs(producer_statuses=_statuses()), dependency_result=_dependency_result())
        assert result.relationships[0].decision_state is DecisionState.REVIEW_REQUIRED
        assert result.relationships[0].score.score_semantics == "UNCALIBRATED_DECISION_SCORE"
        target = Path.cwd() / result.artifacts[0].location
        assert hashlib.sha256(target.read_bytes()).hexdigest() == result.artifacts[0].content_hash
        assert target.parent.name == "manifests"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_actual_catalog_metadata_is_not_a_fake_snapshot_and_conflict_is_explicit():
    from dirty_data_to_olap.domain.contracts.source import DeclaredConstraint, SourceCatalog

    dependency = _dependency_result()
    declared = DeclaredConstraint.model_construct(constraint_type="FOREIGN_KEY", source_id="sales", table_id="orders", columns=("customer_id",), referenced_table_id="customers", referenced_table_name="customers", referenced_columns=("id",), declared=True, provenance=None)
    catalog = SourceCatalog.model_construct(source=None, tables=(), columns=(), declared_constraints=(declared,))
    result = EvidenceFusionService().fuse(EvidenceFusionRequest(request_id="catalog", execution_context_id="catalog", relationship_candidate_ids=("rel-actual",), policy=EvidenceFusionService.load_policy()), EvidenceFusionInputs(producer_statuses=_statuses()), dependency_result=dependency, source_catalogs=(catalog,))
    assert any(item.family is EvidenceFamily.DECLARED_CONSTRAINT and item.snapshot_binding is FusionSnapshotBinding.NOT_APPLICABLE_SCHEMA_METADATA for item in result.signals)
    assert not any(item.kind is FusionFailureKind.SNAPSHOT_SCOPE_MISMATCH for item in result.failures)
    assert any(item.conflict_type is ConflictType.DECLARED_DATA_CONFLICT for item in result.conflicts)
