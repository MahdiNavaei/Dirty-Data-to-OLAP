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
    from dirty_data_to_olap.domain.contracts.source import AdapterReference, DeclaredConstraint, SelectionScope, SourceCatalog, SourceDescriptor, SourceType

    dependency = _dependency_result()
    declared = DeclaredConstraint(constraint_type="FOREIGN_KEY", source_id="sales", table_id="orders", columns=("customer_id",), referenced_table_id="customers", referenced_table_name="customers", referenced_columns=("id",), declared=True, provenance=AdapterReference(name="catalog", version="1", config_fingerprint="catalog"))
    source = SourceDescriptor(source_id="sales", display_name="Sales fixture", source_type=SourceType.CSV, file_locator="fixture.csv", selection_scope=SelectionScope(), schema_fingerprint="schema", adapter_reference=AdapterReference(name="catalog", version="1", config_fingerprint="catalog"))
    catalog = SourceCatalog(source=source, tables=(), columns=(), declared_constraints=(declared,))
    result = EvidenceFusionService().fuse(EvidenceFusionRequest(request_id="catalog", execution_context_id="catalog", relationship_candidate_ids=("rel-actual",), policy=EvidenceFusionService.load_policy()), EvidenceFusionInputs(producer_statuses=_statuses()), dependency_result=dependency, source_catalogs=(catalog,))
    assert any(item.family is EvidenceFamily.DECLARED_CONSTRAINT and item.snapshot_binding is FusionSnapshotBinding.NOT_APPLICABLE_SCHEMA_METADATA for item in result.signals)
    assert not any(item.kind is FusionFailureKind.SNAPSHOT_SCOPE_MISMATCH for item in result.failures)
    assert any(item.conflict_type is ConflictType.DECLARED_DATA_CONFLICT for item in result.conflicts)


def test_valid_schema_match_result_reaches_review_ready_mapping_without_manual_fusion_items():
    from dirty_data_to_olap.domain.contracts.schema_matching import (
        SchemaMatchCapability, SchemaMatchCapabilityStatus, SchemaMatchCandidate,
        SchemaMatchMode, SchemaMatchObservationScope, SchemaMatchPruningSummary,
        SchemaMatchRequest, SchemaMatchResult, SchemaMatchScore, SchemaMatchSignal,
        SchemaMatchSignalFamily, SchemaMatchStatus, SchemaMatcherReference,
    )

    scope = SchemaMatchObservationScope(source_ids=("crm", "erp"), snapshot_ids={"crm": "crm-snap", "erp": "erp-snap"}, table_ids_by_source={"crm": ("orders",), "erp": ("customers",)}, column_ids_by_table={"orders": ("customer_code",), "customers": ("client_no",)}, source_snapshot_modes={"crm": ObservationMode.FULL, "erp": ObservationMode.FULL}, complete_by_table={"orders": True, "customers": True}, staged_rows_by_table={"orders": 2, "customers": 2}, sampled_rows_by_table={"orders": 2, "customers": 2}, sample_seed=7, sample_mode="FULL", sample_algorithm_version="v1", sample_identity="schema-sample")
    request = SchemaMatchRequest(request_id="schema-valid", source_ids=("crm", "erp"), snapshot_ids=scope.snapshot_ids, selected_table_ids_by_source=scope.table_ids_by_source, selected_column_ids_by_table=scope.column_ids_by_table, mode=SchemaMatchMode.SCHEMA_ONLY)
    coma = SchemaMatcherReference(matcher_id="valentine-coma-schema-v1", name="Coma", version="1.0.0", configuration={})
    candidate = SchemaMatchCandidate(candidate_id="map-valid", source_id="crm", source_snapshot_id="crm-snap", source_table_id="orders", source_column_id="customer_code", source_column_name="customer_code", target_source_id="erp", target_snapshot_id="erp-snap", target_table_id="customers", target_column_id="client_no", target_column_name="client_no", score_refs=("coma-score",), signal_refs=("type-signal", "name-signal"), observation_scope=scope)
    score = SchemaMatchScore(score_id="coma-score", matcher=coma, source_column_id="customer_code", target_column_id="client_no", raw_native_score=.9, native_score_name="native", native_score_semantics="rank only", rank=1, config_hash="config", mode=SchemaMatchMode.SCHEMA_ONLY, observation_scope=scope, provenance=AdapterReference(name="valentine", version="1", config_fingerprint="config"))
    signals = (SchemaMatchSignal(signal_id="type-signal", family=SchemaMatchSignalFamily.SCHEMA_STRUCTURAL, value=1.0, semantics="normalized physical type compatibility"), SchemaMatchSignal(signal_id="name-signal", family=SchemaMatchSignalFamily.NAME_LEXICAL, value=.9, semantics="lexical context only"))
    pruning = SchemaMatchPruningSummary(source_pairs_before_bound=1, source_pairs_evaluated=1, tables_before_bound=2, tables_evaluated=2, table_pairs_before_bound=1, table_pairs_evaluated=1, column_pairs_before_pruning=1, column_pairs_pruned_by_type=0, column_pairs_pruned_by_scope=0, column_pairs_pruned_by_context=0, column_pairs_pruned_by_budget=0, column_pairs_evaluated=1, matcher_calls=1, evaluated_by_matcher={"valentine-coma-schema-v1": 1}, returned_by_matcher={"valentine-coma-schema-v1": 1}, output_candidates_emitted=1, output_truncated=False)
    schema = SchemaMatchResult(request=request, observation_scope=scope, candidates=(candidate,), scores=(score,), signals=signals, capabilities=(SchemaMatchCapability(capability_id="valentine", status=SchemaMatchCapabilityStatus.AVAILABLE, engine="valentine", engine_version="1", requested_matchers=("valentine-coma-schema-v1",), detail="fixture"),), pruning=pruning, status=SchemaMatchStatus.COMPLETE)
    result = EvidenceFusionService().fuse(EvidenceFusionRequest(request_id="mapping-valid", execution_context_id="mapping-valid", cross_source_mapping_scope=True, mapping_candidate_ids=("map-valid",), policy=EvidenceFusionService.load_policy("mapping")), EvidenceFusionInputs(producer_statuses=_statuses() + (ProducerEvidenceStatus(producer_id="dependency", family=EvidenceFamily.DEPENDENCY, state=ProducerResultState.COMPLETE, result_id="dependency-valid"),)), schema_match_result=schema)
    assert len(result.mappings) == 1
    assert result.mappings[0].decision_state is DecisionState.REVIEW_REQUIRED
    assert result.completeness is EvidenceFusionCompleteness.COMPLETE_REVIEW_READY
    assert result.mappings[0].score.score_semantics == "UNCALIBRATED_DECISION_SCORE"
    assert {item.raw_metric_name for item in result.signals} >= {"matcher_rank:coma", "type_compatibility", "schema_signal:name_lexical"}
