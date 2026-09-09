from __future__ import annotations

import json
from pathlib import Path

from dirty_data_to_olap.application.quality import QualityAnalysisService
from dirty_data_to_olap.domain.contracts.profiling import (
    ColumnProfile,
    ProfileCompleteness,
    ProfileMode,
    ProfileObservationScope,
    ProfileObservationStatus,
    ProfileProvenance,
    ProfileRequest,
    TableProfile,
    UniquenessSemantics,
    PatternType,
)
from dirty_data_to_olap.domain.contracts.quality import (
    QualityDimension,
    QualityRequest,
    QualityRule,
    QualityRuleScope,
    QualityRuleSet,
    QualityRuleType,
    QualitySeverity,
    Repairability,
    QualityStagedRow,
    constraint_ref_for,
    quality_profile_fingerprint,
)
from dirty_data_to_olap.domain.contracts.source import (
    AdapterReference,
    ColumnDescriptor,
    ExtractionMetrics,
    RowAccounting,
    SelectionScope,
    SourceCatalog,
    SourceDescriptor,
    SourceSnapshot,
    SourceSnapshotResult,
    SourceTableKind,
    SourceType,
    TableDescriptor,
    TableObservationStatus,
    ObservationMode,
    SnapshotConsistency,
    ExtractionPolicy,
    DeclaredConstraint,
    TableSnapshotObservation,
)
from dirty_data_to_olap.domain.patterns import matches_pattern


class FakeReader:
    def __init__(self, rows):
        self.rows = tuple(QualityStagedRow(record_ref=f"ref-{index}", extraction_ordinal=index, values=row) for index, row in enumerate(rows))

    def iter_table(self, snapshot_result, catalog, table, physical_columns, *, project_root):
        yield from (QualityStagedRow(record_ref=row.record_ref, extraction_ordinal=row.extraction_ordinal, values={name: row.values.get(name) for name in physical_columns}) for row in self.rows)


def _fixture():
    adapter = AdapterReference(name="fixture", version="1", config_fingerprint="fixture")
    source = SourceDescriptor(source_id="source-1", display_name="fixture", source_type=SourceType.CSV, file_locator="fixture.csv", selection_scope=SelectionScope(), schema_fingerprint="schema-1", adapter_reference=adapter)
    table = TableDescriptor(table_id="table-1", source_id="source-1", physical_name="rows", table_kind=SourceTableKind.FILE, row_count=4)
    columns = tuple(ColumnDescriptor(column_id=f"column-{name}", table_id="table-1", physical_name=name, ordinal=index, native_physical_type="TEXT", normalized_physical_type="text") for index, name in enumerate(("id", "email", "status", "amount")))
    catalog = SourceCatalog(source=source, tables=(table,), columns=columns, declared_constraints=())
    scope = ProfileObservationScope(source_snapshot_mode="full", source_snapshot_was_bounded=False, source_table_observation_status=TableObservationStatus.FULLY_OBSERVED, source_rows_observed=4, rows_available_in_snapshot=4, rows_profiled=4, profiling_mode=ProfileMode.FULL, sample_method="none", all_available_staged_rows_covered=True, completeness=ProfileObservationStatus.FULLY_OBSERVED)
    provenance = ProfileProvenance(execution_context_id="run-1", source_id="source-1", snapshot_id="snapshot-1", table_id="table-1", schema_fingerprint="schema-1", input_batch_ids=(), input_batch_hashes=(), profiling_adapter=adapter, dataprofiler_version="fixture", profile_config_hash="profile-config", created_at="2026-09-10T00:00:00Z")
    profiles = tuple(ColumnProfile(profile_id=f"profile-{column.column_id}", source_id="source-1", snapshot_id="snapshot-1", table_id="table-1", column_id=column.column_id, physical_type="TEXT", primitive_type_observations={"string": 4}, physical_null_count=1 if column.physical_name == "email" else 0, configured_null_marker_count=0, unknown_missing_count=0, rows_observed=4, non_missing_observed_count=3 if column.physical_name == "email" else 4, observed_distinct_count=3, observed_distinct_ratio=0.75, uniqueness_semantics=UniquenessSemantics.EXACT_ON_FULL_SCOPE, observation_scope=scope, provenance=provenance, status=ProfileCompleteness.COMPLETE) for column in columns)
    table_profile = TableProfile(profile_id="table-profile", source_id="source-1", snapshot_id="snapshot-1", table_id="table-1", table_kind=SourceTableKind.FILE, observation_scope=scope, rows_available_in_snapshot=4, rows_profiled=4, column_profile_refs=tuple(profile.profile_id for profile in profiles), duplicate_row_count=1, duplicate_observation_complete=True, provenance=provenance, status=ProfileCompleteness.COMPLETE)
    profile_request = ProfileRequest(profile_request_id="profile-request", source_id="source-1", snapshot_id="snapshot-1", selected_table_ids=("table-1",), mode=ProfileMode.FULL)
    from dirty_data_to_olap.domain.contracts.profiling import ProfileResult
    profile_result = ProfileResult(profile_request=profile_request, tables=(table_profile,), columns=profiles, patterns=(), failures=(), completeness=ProfileCompleteness.COMPLETE)
    snapshot = SourceSnapshot(source_id="source-1", snapshot_id="snapshot-1", execution_context_id="run-1", schema_fingerprint="schema-1", source_fingerprint="fixture", observed_at="2026-09-10T00:00:00Z", selection_scope=SelectionScope(), observation_scope=__import__("dirty_data_to_olap.domain.contracts.source", fromlist=["ObservationScope"]).ObservationScope(mode=ObservationMode.FULL, chunk_size=10, input_records_observed=4), extraction_policy=ExtractionPolicy(chunk_size=10), consistency=SnapshotConsistency.FILE_IMMUTABLE, adapter_reference=adapter)
    snapshot_result = SourceSnapshotResult(snapshot=snapshot, batches=(), record_references=(), accounting=RowAccounting(input_records_observed=4, successfully_staged_records=4, explicitly_quarantined_records=0, unresolved_records=0, accounting_complete=True), metrics=ExtractionMetrics(input_records_observed=4, staged_records=4, batch_count=0, configured_chunk_size=10, bytes_staged=0), table_observations=())
    rows = ({"id": "1", "email": "good@example.com", "status": "A", "amount": "4"}, {"id": "2", "email": "bad", "status": "Z", "amount": "-1"}, {"id": "2", "email": "bad", "status": "Z", "amount": "-1"}, {"id": "3", "email": None, "status": "B", "amount": "2"})
    return catalog, snapshot_result, profile_result, rows


def _rule(rule_id, rule_type, column_ids=(), **kwargs):
    entity_type = kwargs.pop("entity_type", "column")
    repairability = kwargs.pop("repairability", Repairability.REVIEW_REQUIRED)
    return QualityRule(rule_id=rule_id, rule_version="1.0", rule_type=rule_type, scope=QualityRuleScope.USER_POLICY, entity_type=entity_type, table_id="table-1", column_ids=column_ids, severity=QualitySeverity.MEDIUM, repairability=repairability, provenance="unit-test", **kwargs)


def _run(rules, *, artifact_root: Path | None = None):
    catalog, snapshot, profile_result, rows = _fixture()
    request = QualityRequest(quality_run_id="quality-run", source_id="source-1", snapshot_id="snapshot-1", rule_set=QualityRuleSet(rule_set_id="test-rules", version="1", rules=tuple(rules), provenance="unit-test"), profile_result_fingerprint=quality_profile_fingerprint(profile_result), provenance="unit-test")
    return QualityAnalysisService(FakeReader(rows), project_root=Path.cwd()).analyze(request, catalog, snapshot, profile_result, artifact_root=artifact_root)


def test_explicit_rules_detect_issues_without_inventing_requiredness_or_key_claims():
    artifact_root = Path.cwd() / "tests" / "quality_unit_artifacts"
    result = _run((_rule("required.email", QualityRuleType.REQUIRED_VALUE, ("column-email",)), _rule("pattern.email", QualityRuleType.EXPECTED_PATTERN, ("column-email",), expected_pattern="EMAIL_LIKE"), _rule("domain.status", QualityRuleType.ALLOWED_DOMAIN, ("column-status",), allowed_values=("A", "B")), _rule("range.amount", QualityRuleType.NUMERIC_RANGE, ("column-amount",), minimum=0, maximum=10), _rule("unique.id", QualityRuleType.UNIQUE_VALUES, ("column-id",)), _rule("duplicate.rows", QualityRuleType.EXACT_ROW_DUPLICATION, entity_type="table", column_ids=(), detector_config={"repair_authorized": True})), artifact_root=artifact_root)
    issue_types = {issue.issue_type for issue in result.issues}
    assert {"REQUIRED_VALUE_MISSING", "EXPECTED_PATTERN_MISMATCH", "ALLOWED_DOMAIN_VIOLATION", "NUMERIC_RANGE_VIOLATION", "UNIQUE_VALUES_VIOLATION", "EXACT_DUPLICATE_ROWS_OBSERVED"} <= issue_types
    assert not any("DUPLICATE_ENTITY" in issue.issue_type for issue in result.issues)
    assert result.repair_proposals
    assert all(proposal.status.value == "PROPOSED" for proposal in result.repair_proposals)
    assert not hasattr(result.repair_proposals[0], "approved")
    serialized = json.dumps(result.model_dump(mode="json"))
    assert "good@example.com" not in serialized
    assert result.dimension_summaries
    assert "overall" not in serialized.lower()


def test_no_quality_rule_means_missing_values_are_not_a_requiredness_issue():
    result = _run(())
    assert not result.issues
    completeness = next(item for item in result.dimension_summaries if item.dimension is QualityDimension.COMPLETENESS)
    assert completeness.status.value == "UNMEASURED"


def test_exact_duplicate_proposal_requires_review_and_has_validation_plan():
    result = _run((_rule("duplicate.rows", QualityRuleType.EXACT_ROW_DUPLICATION, entity_type="table", detector_config={"repair_authorized": True}),))
    proposal = result.repair_proposals[0]
    assert proposal.repairability is Repairability.REVIEW_REQUIRED
    assert proposal.review_required is True
    assert proposal.validation_plan.invariants


def test_quality_dimensions_use_one_common_population_not_sum_of_rules():
    result = _run((_rule("required.email", QualityRuleType.REQUIRED_VALUE, ("column-email",)), _rule("required.status", QualityRuleType.REQUIRED_VALUE, ("column-status",))))
    completeness = next(item for item in result.dimension_summaries if item.dimension is QualityDimension.COMPLETENESS)
    assert completeness.measured_row_count == 4
    assert completeness.denominator_semantics == "COMMON_RECORD_REF_POPULATION"


def test_quality_evidence_binds_to_actual_table_profile_id():
    result = _run((_rule("required.email", QualityRuleType.REQUIRED_VALUE, ("column-email",)),))
    table_evidence = next(ref for issue in result.issues for ref in issue.evidence_refs if ref.evidence_type == "TableProfile")
    assert table_evidence.evidence_id == "table-profile"
    assert table_evidence.evidence_id != "table-1"


def test_all_pattern_types_have_shared_positive_near_miss_and_negative_behavior():
    examples = {
        PatternType.EMAIL_LIKE: ("a@example.com", "a@example", "a@example.com "),
        PatternType.PHONE_LIKE: ("+989121234567", "1212", "+989121234567x"),
        PatternType.UUID_LIKE: ("123e4567-e89b-12d3-a456-426614174000", "123e4567-e89b-12d3-0456-426614174000", "not-a-uuid"),
        PatternType.INTEGER_STRING: ("-12", "12.0", "12x"),
        PatternType.DECIMAL_STRING: ("12.50", "12", "12.5x"),
        PatternType.DATE_STRING: ("2026-09-10", "2026-9-10", "10/09/2026"),
        PatternType.URL_LIKE: ("https://example.com/a", "example.com", "https://example.com/a b"),
    }
    for pattern, (positive, near_miss, negative) in examples.items():
        assert matches_pattern(pattern.value, positive)
        assert not matches_pattern(pattern.value, near_miss)
        assert not matches_pattern(pattern.value, negative)


def test_manual_and_ambiguous_rules_do_not_auto_propose_quarantine():
    result = _run((_rule("domain.status", QualityRuleType.ALLOWED_DOMAIN, ("column-status",), allowed_values=("A",), repairability=Repairability.MANUAL_BUSINESS_DECISION), _rule("range.amount", QualityRuleType.NUMERIC_RANGE, ("column-amount",), minimum=0, repairability=Repairability.REVIEW_REQUIRED)))
    assert result.issues
    assert not result.repair_proposals


def test_declared_fk_requires_full_target_coverage_and_reports_orphans():
    catalog, snapshot, profile_result, rows = _fixture()
    target = TableDescriptor(table_id="table-2", source_id="source-1", physical_name="parents", table_kind=SourceTableKind.FILE, row_count=2)
    target_column = ColumnDescriptor(column_id="column-parent-id", table_id="table-2", physical_name="parent_id", ordinal=0, native_physical_type="TEXT", normalized_physical_type="text")
    constraint = DeclaredConstraint(constraint_type="FOREIGN_KEY", source_id="source-1", table_id="table-1", columns=("id",), referenced_table_id="table-2", referenced_table_name="parents", referenced_columns=("parent_id",), provenance=AdapterReference(name="fixture", version="1", config_fingerprint="fixture"))
    catalog = catalog.model_copy(update={"tables": catalog.tables + (target,), "columns": catalog.columns + (target_column,), "declared_constraints": (constraint,)})
    snapshot = snapshot.model_copy(update={"table_observations": (TableSnapshotObservation(table_id="table-1", rows_observed=4, status=TableObservationStatus.FULLY_OBSERVED), TableSnapshotObservation(table_id="table-2", rows_observed=2, status=TableObservationStatus.FULLY_OBSERVED))})
    class MultiReader(FakeReader):
        def iter_table(self, snapshot_result, catalog, table, physical_columns, *, project_root):
            values = rows if table.table_id == "table-1" else (({"parent_id": "1"}), ({"parent_id": "3"}))
            yield from (QualityStagedRow(record_ref=f"{table.table_id}-ref-{index}", extraction_ordinal=index, values={name: row.get(name) for name in physical_columns}) for index, row in enumerate(values))
    request = QualityRequest(quality_run_id="fk-run", source_id="source-1", snapshot_id="snapshot-1", rule_set=QualityRuleSet(rule_set_id="fk-rules", version="1", rules=(QualityRule(rule_id="declared.fk", rule_version="1", rule_type=QualityRuleType.DECLARED_REFERENTIAL_INTEGRITY, scope=QualityRuleScope.SOURCE_DECLARED, entity_type="column", table_id="table-1", column_ids=("column-id",), severity=QualitySeverity.HIGH, repairability=Repairability.MANUAL_BUSINESS_DECISION, declared_constraint_ref=constraint_ref_for(constraint), provenance="unit-test"),), provenance="unit-test"), profile_result_fingerprint=quality_profile_fingerprint(profile_result), provenance="unit-test")
    result = QualityAnalysisService(MultiReader(rows), project_root=Path.cwd()).analyze(request, catalog, snapshot, profile_result)
    assert result.issues[0].issue_type == "DECLARED_REFERENTIAL_INTEGRITY_VIOLATION"
    assert result.issues[0].affected_count == 2
    partial = snapshot.model_copy(update={"table_observations": (TableSnapshotObservation(table_id="table-1", rows_observed=4, status=TableObservationStatus.FULLY_OBSERVED), TableSnapshotObservation(table_id="table-2", rows_observed=1, status=TableObservationStatus.PARTIALLY_OBSERVED))})
    inconclusive = QualityAnalysisService(MultiReader(rows), project_root=Path.cwd()).analyze(request, catalog, partial, profile_result)
    assert inconclusive.issues[0].status.value == "INCONCLUSIVE"


def test_composite_fk_uses_declared_column_order_and_partial_null_is_inconclusive():
    catalog, snapshot, profile_result, rows = _fixture()
    target = TableDescriptor(table_id="table-2", source_id="source-1", physical_name="parents", table_kind=SourceTableKind.FILE, row_count=3)
    target_columns = (ColumnDescriptor(column_id="column-parent-id", table_id="table-2", physical_name="parent_id", ordinal=0, native_physical_type="TEXT", normalized_physical_type="text"), ColumnDescriptor(column_id="column-parent-status", table_id="table-2", physical_name="parent_status", ordinal=1, native_physical_type="TEXT", normalized_physical_type="text"))
    constraint = DeclaredConstraint(constraint_type="FOREIGN_KEY", source_id="source-1", table_id="table-1", columns=("status", "id"), referenced_table_id="table-2", referenced_table_name="parents", referenced_columns=("parent_id", "parent_status"), provenance=AdapterReference(name="fixture", version="1", config_fingerprint="fixture"))
    catalog = catalog.model_copy(update={"tables": catalog.tables + (target,), "columns": catalog.columns + target_columns, "declared_constraints": (constraint,)})
    snapshot = snapshot.model_copy(update={"table_observations": (TableSnapshotObservation(table_id="table-1", rows_observed=4, status=TableObservationStatus.FULLY_OBSERVED), TableSnapshotObservation(table_id="table-2", rows_observed=3, status=TableObservationStatus.FULLY_OBSERVED))})

    class CompositeReader(FakeReader):
        def iter_table(self, snapshot_result, catalog, table, physical_columns, *, project_root):
            source_values = rows if table.table_id == "table-1" else (({"parent_id": "A", "parent_status": "1"}), ({"parent_id": "Z", "parent_status": "2"}), ({"parent_id": "B", "parent_status": "3"}))
            yield from (QualityStagedRow(record_ref=f"{table.table_id}-ref-{index}", extraction_ordinal=index, values={name: row.get(name) for name in physical_columns}) for index, row in enumerate(source_values))

    rule = QualityRule(rule_id="declared.composite", rule_version="1", rule_type=QualityRuleType.DECLARED_REFERENTIAL_INTEGRITY, scope=QualityRuleScope.SOURCE_DECLARED, entity_type="column", table_id="table-1", severity=QualitySeverity.HIGH, repairability=Repairability.MANUAL_BUSINESS_DECISION, declared_constraint_ref=constraint_ref_for(constraint), provenance="unit-test")
    request = QualityRequest(quality_run_id="composite-run", source_id="source-1", snapshot_id="snapshot-1", rule_set=QualityRuleSet(rule_set_id="composite-rules", version="1", rules=(rule,), provenance="unit-test"), profile_result_fingerprint=quality_profile_fingerprint(profile_result), provenance="unit-test")
    result = QualityAnalysisService(CompositeReader(rows), project_root=Path.cwd()).analyze(request, catalog, snapshot, profile_result)
    assert not result.issues

    partial_rows = rows + (({"id": None, "status": "A", "email": None, "amount": "1"}),)
    class PartialCompositeReader(CompositeReader):
        def iter_table(self, snapshot_result, catalog, table, physical_columns, *, project_root):
            source_values = partial_rows if table.table_id == "table-1" else (({"parent_id": "A", "parent_status": "1"}), ({"parent_id": "Z", "parent_status": "2"}), ({"parent_id": "B", "parent_status": "3"}))
            yield from (QualityStagedRow(record_ref=f"{table.table_id}-ref-{index}", extraction_ordinal=index, values={name: row.get(name) for name in physical_columns}) for index, row in enumerate(source_values))

    partial = QualityAnalysisService(PartialCompositeReader(partial_rows), project_root=Path.cwd()).analyze(request, catalog, snapshot, profile_result)
    assert partial.issues[0].issue_type == "DECLARED_REFERENTIAL_INTEGRITY_PARTIAL_NULL_INCONCLUSIVE"
    assert partial.issues[0].status is __import__("dirty_data_to_olap.domain.contracts.quality", fromlist=["QualityIssueStatus"]).QualityIssueStatus.INCONCLUSIVE
