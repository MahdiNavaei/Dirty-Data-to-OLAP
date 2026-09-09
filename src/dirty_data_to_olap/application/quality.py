"""Deterministic quality analysis over pinned staged evidence."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from dirty_data_to_olap.application.quality_reader import QualityInputIntegrityError, QualityStagedReader
from dirty_data_to_olap.domain.contracts.profiling import ProfileCompleteness, ProfileMode
from dirty_data_to_olap.domain.contracts.quality import (
    DetectionBasis,
    MeasurementSemantics,
    QualityDimension,
    QualityDimensionSummary,
    QualityDimensionStatus,
    QualityEvidenceRef,
    QualityFailure,
    QualityFailureKind,
    QualityIssue,
    QualityIssueStatus,
    QualityRequest,
    QualityResult,
    QualityRule,
    QualityRuleScope,
    QualityRuleType,
    QualityRuleEvaluation,
    RepairProposal,
    RepairProposalStatus,
    RepairValidationPlan,
    Repairability,
    RuleApplicability,
    quality_profile_fingerprint,
    quality_rule_dimension,
    constraint_ref_for,
)
from dirty_data_to_olap.domain.contracts.source import (
    ColumnDescriptor,
    SourceCatalog,
    SourceSnapshotResult,
    TableDescriptor,
    TableObservationStatus,
    stable_digest,
)


class QualityAnalysisService:
    def __init__(self, reader: QualityStagedReader, *, project_root: Path) -> None:
        self.reader = reader
        self.project_root = project_root.resolve()

    def analyze(self, request: QualityRequest, catalog: SourceCatalog, snapshot_result: SourceSnapshotResult, profile_result, *, artifact_root: Path | None = None) -> QualityResult:
        try:
            self._validate_inputs(request, catalog, snapshot_result, profile_result)
        except (ValueError, QualityInputIntegrityError) as error:
            result = self._input_failure_result(request, snapshot_result, str(error))
            if artifact_root is not None:
                from dirty_data_to_olap.adapters.quality.artifacts import QualityArtifactStore
                result = QualityArtifactStore(self.project_root).publish(result, run_root=artifact_root)
            return result
        profiles = {item.column_id: item for item in profile_result.columns}
        table_profiles = {item.table_id: item for item in profile_result.tables}
        issues: list[QualityIssue] = []
        proposals: list[RepairProposal] = []
        evaluations: list[QualityRuleEvaluation] = []
        failures: list[QualityFailure] = []
        if profile_result.completeness is not ProfileCompleteness.COMPLETE:
            failures.append(QualityFailure(failure_id=f"failure_{request.quality_run_id}_profile", kind=QualityFailureKind.INCOMPLETE_PROFILE, detail="profile result is incomplete; missing profile items are not treated as clean", source_id=request.source_id, snapshot_id=request.snapshot_id))
        for rule in request.rule_set.rules:
            if not rule.enabled:
                evaluations.append(QualityRuleEvaluation(rule_id=rule.rule_id, applicability=RuleApplicability.NOT_APPLICABLE, measurement_semantics=MeasurementSemantics.UNMEASURED))
                continue
            if rule.source_id and rule.source_id != request.source_id:
                evaluations.append(QualityRuleEvaluation(rule_id=rule.rule_id, applicability=RuleApplicability.NOT_APPLICABLE, measurement_semantics=MeasurementSemantics.UNMEASURED))
                continue
            table = next((item for item in catalog.tables if item.table_id == rule.table_id), None)
            if table is None:
                evaluations.append(QualityRuleEvaluation(rule_id=rule.rule_id, applicability=RuleApplicability.INSUFFICIENT_EVIDENCE, measurement_semantics=MeasurementSemantics.UNMEASURED, failure_ref=f"failure_{request.quality_run_id}_{rule.rule_id}"))
                failures.append(QualityFailure(failure_id=f"failure_{request.quality_run_id}_{rule.rule_id}", kind=QualityFailureKind.RULE_PREREQUISITE_MISSING, detail="quality rule table is absent from the supplied catalog", rule_id=rule.rule_id, source_id=request.source_id, snapshot_id=request.snapshot_id))
                continue
            table_profile = table_profiles.get(table.table_id)
            if table_profile is None:
                evaluations.append(QualityRuleEvaluation(rule_id=rule.rule_id, applicability=RuleApplicability.INSUFFICIENT_EVIDENCE, measurement_semantics=MeasurementSemantics.UNMEASURED, failure_ref=f"failure_{request.quality_run_id}_{rule.rule_id}"))
                failures.append(QualityFailure(failure_id=f"failure_{request.quality_run_id}_{rule.rule_id}", kind=QualityFailureKind.RULE_PREREQUISITE_MISSING, detail="table profile is absent; absence of a profile is not clean evidence", rule_id=rule.rule_id, table_id=table.table_id, source_id=request.source_id, snapshot_id=request.snapshot_id))
                continue
            if rule.scope is QualityRuleScope.REFERENCE_BENCHMARK and request.rule_set.runtime_default:
                failures.append(QualityFailure(failure_id=f"failure_{request.quality_run_id}_{rule.rule_id}", kind=QualityFailureKind.RULE_INVALID, detail="benchmark rule cannot run in the runtime default policy", rule_id=rule.rule_id, table_id=table.table_id, source_id=request.source_id, snapshot_id=request.snapshot_id))
                evaluations.append(QualityRuleEvaluation(rule_id=rule.rule_id, applicability=RuleApplicability.INSUFFICIENT_EVIDENCE, measurement_semantics=MeasurementSemantics.UNMEASURED, failure_ref=f"failure_{request.quality_run_id}_{rule.rule_id}"))
                continue
            missing_columns = [column_id for column_id in rule.column_ids if column_id not in profiles]
            if missing_columns and rule.rule_type is not QualityRuleType.EXACT_ROW_DUPLICATION:
                failure_id = f"failure_{request.quality_run_id}_{rule.rule_id}"
                failures.append(QualityFailure(failure_id=failure_id, kind=QualityFailureKind.RULE_PREREQUISITE_MISSING, detail="required column profile is absent", rule_id=rule.rule_id, table_id=table.table_id, source_id=request.source_id, snapshot_id=request.snapshot_id))
                evaluations.append(QualityRuleEvaluation(rule_id=rule.rule_id, applicability=RuleApplicability.INSUFFICIENT_EVIDENCE, measurement_semantics=MeasurementSemantics.UNMEASURED, failure_ref=failure_id))
                continue
            try:
                rule_issues, evaluated_count = self._evaluate_rule(rule, table, table_profile, catalog, snapshot_result, profiles, snapshot_id=request.snapshot_id, configured_missing_markers=profile_result.profile_request.null_marker_policy.configured_markers)
            except QualityInputIntegrityError as error:
                failure_id = f"failure_{request.quality_run_id}_{rule.rule_id}"
                failures.append(QualityFailure(failure_id=failure_id, kind=QualityFailureKind.INPUT_INTEGRITY_FAILED, detail=str(error), rule_id=rule.rule_id, table_id=table.table_id, source_id=request.source_id, snapshot_id=request.snapshot_id))
                evaluations.append(QualityRuleEvaluation(rule_id=rule.rule_id, applicability=RuleApplicability.INSUFFICIENT_EVIDENCE, measurement_semantics=MeasurementSemantics.UNMEASURED, failure_ref=failure_id))
                continue
            except Exception as error:
                failure_id = f"failure_{request.quality_run_id}_{rule.rule_id}"
                failures.append(QualityFailure(failure_id=failure_id, kind=QualityFailureKind.DETECTOR_FAILED, detail=f"detector failed: {error.__class__.__name__}", rule_id=rule.rule_id, table_id=table.table_id, source_id=request.source_id, snapshot_id=request.snapshot_id))
                evaluations.append(QualityRuleEvaluation(rule_id=rule.rule_id, applicability=RuleApplicability.INSUFFICIENT_EVIDENCE, measurement_semantics=MeasurementSemantics.UNMEASURED, failure_ref=failure_id))
                continue
            issues.extend(rule_issues)
            for issue in rule_issues:
                proposal = self._proposal_for(issue, rule)
                if proposal is not None:
                    proposals.append(proposal)
            semantics = rule_issues[0].measurement_semantics if rule_issues else self._measurement(table_profile.observation_scope)
            evaluations.append(QualityRuleEvaluation(rule_id=rule.rule_id, applicability=RuleApplicability.INCONCLUSIVE if any(item.status is QualityIssueStatus.INCONCLUSIVE for item in rule_issues) else RuleApplicability.APPLICABLE, measurement_semantics=semantics, evaluated_count=evaluated_count, affected_count=sum(item.affected_count for item in rule_issues), affected_ratio=(sum(item.affected_count for item in rule_issues) / evaluated_count if evaluated_count and rule_issues and all(item.status is QualityIssueStatus.OPEN for item in rule_issues) else None), issue_refs=tuple(item.issue_id for item in rule_issues), evidence_refs=tuple(ref for item in rule_issues for ref in item.evidence_refs)))
        proposal_refs = {issue_ref: proposal.proposal_id for proposal in proposals for issue_ref in proposal.issue_refs}
        issues = [issue.model_copy(update={"repair_proposal_refs": (proposal_refs[issue.issue_id],)}) if issue.issue_id in proposal_refs else issue for issue in issues]
        summaries = self._dimension_summaries(evaluations, issues, table_profiles, request.rule_set.rules)
        result = QualityResult(quality_run_id=request.quality_run_id, source_id=request.source_id, snapshot_id=request.snapshot_id, rule_set_id=request.rule_set.rule_set_id, rule_set_version=request.rule_set.version, issues=tuple(issues), repair_proposals=tuple(proposals), rule_evaluations=tuple(evaluations), failures=tuple(failures), dimension_summaries=summaries, input_profile_refs=request.profile_refs, input_batch_ids=request.batch_ids or tuple(batch.batch_id for batch in snapshot_result.batches), input_batch_hashes=request.batch_hashes or tuple(batch.content_hash for batch in snapshot_result.batches), provenance=request.provenance, completeness="INCOMPLETE" if failures else "COMPLETE")
        if artifact_root is not None:
            from dirty_data_to_olap.adapters.quality.artifacts import QualityArtifactStore
            result = QualityArtifactStore(self.project_root).publish(result, run_root=artifact_root)
        return result

    @staticmethod
    def _validate_inputs(request: QualityRequest, catalog: SourceCatalog, snapshot_result: SourceSnapshotResult, profile_result) -> None:
        if request.source_id != catalog.source_id or request.snapshot_id != snapshot_result.snapshot.snapshot_id:
            raise ValueError("quality request is not bound to the supplied source snapshot")
        if profile_result.profile_request.source_id != request.source_id or profile_result.profile_request.snapshot_id != request.snapshot_id:
            raise ValueError("profile result is not bound to the quality snapshot")
        if request.profile_result_fingerprint != quality_profile_fingerprint(profile_result):
            raise ValueError("profile result fingerprint mismatch")
        actual = {batch.batch_id: batch.content_hash for batch in snapshot_result.batches}
        if request.batch_hashes and any(actual.get(batch_id) != content_hash for batch_id, content_hash in zip(request.batch_ids, request.batch_hashes)):
            raise ValueError("quality request batch hash binding mismatch")

    @staticmethod
    def _input_failure_result(request: QualityRequest, snapshot_result: SourceSnapshotResult, detail: str) -> QualityResult:
        failure = QualityFailure(
            failure_id=f"failure_{request.quality_run_id}_input",
            kind=QualityFailureKind.INPUT_INTEGRITY_FAILED,
            detail=detail,
            source_id=request.source_id,
            snapshot_id=request.snapshot_id,
        )
        summaries = tuple(
            QualityDimensionSummary(
                dimension=dimension,
                status=QualityDimensionStatus.UNMEASURED,
                measurement_semantics=MeasurementSemantics.UNMEASURED,
                applicable_rule_count=0,
                evaluated_rule_count=0,
                inconclusive_rule_count=0,
                measured_row_count=0,
            )
            for dimension in QualityDimension
        )
        return QualityResult(
            quality_run_id=request.quality_run_id,
            source_id=request.source_id,
            snapshot_id=request.snapshot_id,
            rule_set_id=request.rule_set.rule_set_id,
            rule_set_version=request.rule_set.version,
            issues=(),
            repair_proposals=(),
            rule_evaluations=(),
            failures=(failure,),
            dimension_summaries=summaries,
            input_profile_refs=request.profile_refs,
            input_batch_ids=request.batch_ids or tuple(batch.batch_id for batch in snapshot_result.batches),
            input_batch_hashes=request.batch_hashes or tuple(batch.content_hash for batch in snapshot_result.batches),
            provenance=request.provenance,
            completeness="INCOMPLETE",
        )

    def _evaluate_rule(self, rule: QualityRule, table: TableDescriptor, table_profile, catalog: SourceCatalog, snapshot_result: SourceSnapshotResult, profiles: Mapping[str, Any], *, snapshot_id: str, configured_missing_markers: Sequence[str] = ()) -> tuple[list[QualityIssue], int]:
        scope = table_profile.observation_scope
        semantics = self._measurement(scope)
        if rule.applicability_conditions.get("requires_full_snapshot_scope") and semantics is not MeasurementSemantics.EXACT_ON_FULL_SNAPSHOT_SCOPE:
            issue = self._issue(rule, table, scope, 0, 0, (), MeasurementSemantics.INCONCLUSIVE, "QUALITY_RULE_INCONCLUSIVE_SCOPE", profiles, snapshot_id=snapshot_id, status=QualityIssueStatus.INCONCLUSIVE)
            return [issue], 0
        if rule.rule_type is QualityRuleType.EXACT_ROW_DUPLICATION:
            rows = self._scan(table, catalog, snapshot_result, [column.physical_name for column in catalog.columns if column.table_id == table.table_id])
            seen: dict[str, str] = {}
            affected: list[str] = []
            for row in rows:
                key = stable_digest(row.values)
                if key in seen:
                    affected.extend((seen[key], row.record_ref))
                else:
                    seen[key] = row.record_ref
            refs = tuple(dict.fromkeys(affected))
            return ([self._issue(rule, table, scope, len(refs), len(rows), refs, semantics, "EXACT_DUPLICATE_ROWS_OBSERVED", profiles, snapshot_id=snapshot_id)] if refs else []), len(rows)
        if rule.rule_type is QualityRuleType.DECLARED_REFERENTIAL_INTEGRITY:
            return self._referential_issue(rule, table, catalog, snapshot_result, table_profile, profiles, snapshot_id=snapshot_id)
        columns = tuple(item for item in catalog.columns if item.table_id == table.table_id and (not rule.column_ids or item.column_id in rule.column_ids))
        if not columns:
            raise ValueError("rule has no applicable columns")
        rows = self._scan(table, catalog, snapshot_result, [column.physical_name for column in columns])
        affected: list[str] = []
        config = dict(rule.detector_config)
        markers = set(str(item) for item in config.get("missing_markers", configured_missing_markers))
        include_markers = bool(config.get("treat_configured_markers_as_missing", True))
        evaluated = 0
        key_counts: dict[tuple[Any, ...], list[str]] = defaultdict(list)
        for row in rows:
            values = tuple(row.values.get(column.physical_name) for column in columns)
            missing = tuple(value is None or (include_markers and isinstance(value, str) and value in markers) for value in values)
            if rule.rule_type is QualityRuleType.REQUIRED_VALUE:
                evaluated += 1
                if any(missing):
                    affected.append(row.record_ref)
            elif rule.rule_type is QualityRuleType.UNIQUE_VALUES:
                if not any(missing):
                    evaluated += 1
                    key_counts[values].append(row.record_ref)
            else:
                value = values[0]
                if value is None or (include_markers and isinstance(value, str) and value in markers):
                    continue
                evaluated += 1
                if rule.rule_type is QualityRuleType.EXPECTED_PATTERN and (not isinstance(value, str) or not self._matches_pattern(value, rule)):
                    affected.append(row.record_ref)
                elif rule.rule_type is QualityRuleType.EXPECTED_PRIMITIVE_TYPE and self._primitive(value) != str(rule.expected_primitive_type).lower():
                    affected.append(row.record_ref)
                elif rule.rule_type is QualityRuleType.ALLOWED_DOMAIN and str(value) not in set(rule.allowed_values):
                    affected.append(row.record_ref)
                elif rule.rule_type is QualityRuleType.NUMERIC_RANGE and (not isinstance(value, (int, float)) or isinstance(value, bool) or (rule.minimum is not None and value < rule.minimum) or (rule.maximum is not None and value > rule.maximum)):
                    affected.append(row.record_ref)
                elif rule.rule_type is QualityRuleType.NORMALIZATION_OPPORTUNITY and config.get("operation") == "trim_whitespace" and isinstance(value, str) and value != value.strip():
                    affected.append(row.record_ref)
        if rule.rule_type is QualityRuleType.UNIQUE_VALUES:
            affected = [ref for refs in key_counts.values() if len(refs) > 1 for ref in refs]
        if not affected:
            return [], evaluated
        issue_type = {
            QualityRuleType.REQUIRED_VALUE: "REQUIRED_VALUE_MISSING",
            QualityRuleType.UNIQUE_VALUES: "UNIQUE_VALUES_VIOLATION",
            QualityRuleType.EXPECTED_PATTERN: "EXPECTED_PATTERN_MISMATCH",
            QualityRuleType.EXPECTED_PRIMITIVE_TYPE: "EXPECTED_PRIMITIVE_TYPE_MISMATCH",
            QualityRuleType.ALLOWED_DOMAIN: "ALLOWED_DOMAIN_VIOLATION",
            QualityRuleType.NUMERIC_RANGE: "NUMERIC_RANGE_VIOLATION",
            QualityRuleType.NORMALIZATION_OPPORTUNITY: "REPRESENTATION_NORMALIZATION_OPPORTUNITY",
        }[rule.rule_type]
        return [self._issue(rule, table, scope, len(set(affected)), evaluated, tuple(dict.fromkeys(affected)), semantics, issue_type, profiles, snapshot_id=snapshot_id)], evaluated

    def _referential_issue(self, rule: QualityRule, table: TableDescriptor, catalog: SourceCatalog, snapshot_result: SourceSnapshotResult, table_profile, profiles: Mapping[str, Any], *, snapshot_id: str) -> tuple[list[QualityIssue], int]:
        constraint = next((item for item in catalog.declared_constraints if constraint_ref_for(item) == rule.declared_constraint_ref), None)
        if constraint is None or not constraint.referenced_table_name:
            raise ValueError("declared referential rule does not bind to catalog constraint")
        target = next((item for item in catalog.tables if item.table_id == constraint.referenced_table_id or item.physical_name == constraint.referenced_table_name), None)
        if target is None:
            raise ValueError("declared referential target is absent")
        target_observation = next((item for item in snapshot_result.table_observations if item.table_id == target.table_id), None)
        if target_observation is None or target_observation.status is not TableObservationStatus.FULLY_OBSERVED:
            issue = self._issue(rule, table, table_profile.observation_scope, 0, 0, (), MeasurementSemantics.INCONCLUSIVE, "DECLARED_REFERENTIAL_INTEGRITY_INCONCLUSIVE", profiles, snapshot_id=snapshot_id, status=QualityIssueStatus.INCONCLUSIVE)
            return [issue], 0
        source_columns = tuple(item for item in catalog.columns if item.table_id == table.table_id and item.physical_name in constraint.columns)
        target_columns = tuple(item for item in catalog.columns if item.table_id == target.table_id and item.physical_name in constraint.referenced_columns)
        source_rows = self._scan(table, catalog, snapshot_result, [item.physical_name for item in source_columns])
        target_rows = self._scan(target, catalog, snapshot_result, [item.physical_name for item in target_columns])
        targets = {tuple(row.values.get(item.physical_name) for item in target_columns) for row in target_rows}
        affected = []
        evaluated = 0
        for row in source_rows:
            key = tuple(row.values.get(item.physical_name) for item in source_columns)
            if all(value is None for value in key):
                continue
            evaluated += 1
            if key not in targets:
                affected.append(row.record_ref)
        if not affected:
            return [], evaluated
        return [self._issue(rule, table, table_profile.observation_scope, len(affected), evaluated, tuple(affected), self._measurement(table_profile.observation_scope), "DECLARED_REFERENTIAL_INTEGRITY_VIOLATION", profiles, snapshot_id=snapshot_id)], evaluated

    def _scan(self, table: TableDescriptor, catalog: SourceCatalog, snapshot_result: SourceSnapshotResult, physical_columns: Sequence[str]):
        return tuple(self.reader.scan_table(snapshot_result, catalog, table, physical_columns, project_root=self.project_root))

    @staticmethod
    def _measurement(scope) -> MeasurementSemantics:
        if scope.profiling_mode is ProfileMode.SAMPLE:
            return MeasurementSemantics.SAMPLE_OBSERVATION
        if scope.source_table_observation_status is TableObservationStatus.FULLY_OBSERVED and scope.source_snapshot_mode == "full":
            return MeasurementSemantics.EXACT_ON_FULL_SNAPSHOT_SCOPE
        return MeasurementSemantics.EXACT_ON_OBSERVED_SCOPE

    @staticmethod
    def _primitive(value: Any) -> str:
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float):
            return "real"
        if isinstance(value, str):
            return "string"
        if value is None:
            return "null"
        return "nested"

    @staticmethod
    def _matches_pattern(value: str, rule: QualityRule) -> bool:
        import re
        patterns = {"EMAIL_LIKE": r"^[^@\s]+@[^@\s]+\.[^@\s]+$", "PHONE_LIKE": r"^\+?[0-9][0-9()\-\s]{6,}$", "UUID_LIKE": r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"}
        pattern = rule.expected_pattern.value if rule.expected_pattern is not None else ""
        return bool(patterns.get(pattern) and re.fullmatch(patterns[pattern], value))

    def _issue(self, rule: QualityRule, table: TableDescriptor, scope, affected_count: int, evaluated: int, refs: tuple[str, ...], semantics: MeasurementSemantics, issue_type: str, profiles: Mapping[str, Any], *, snapshot_id: str, status: QualityIssueStatus = QualityIssueStatus.OPEN) -> QualityIssue:
        issue_id = "issue_" + stable_digest({"run": rule.rule_id, "table": table.table_id, "refs": refs, "type": issue_type})[:32]
        if semantics is MeasurementSemantics.INCONCLUSIVE:
            basis = DetectionBasis.INCONCLUSIVE
        elif semantics is MeasurementSemantics.SAMPLE_OBSERVATION:
            basis = DetectionBasis.SAMPLED_OBSERVATION
        elif rule.scope is QualityRuleScope.SOURCE_DECLARED:
            basis = DetectionBasis.DECLARED_CONSTRAINT_CONTRADICTION
        elif rule.scope is QualityRuleScope.DOMAIN_ASSERTION:
            basis = DetectionBasis.DOMAIN_ASSERTED_RULE
        elif rule.scope is QualityRuleScope.USER_POLICY:
            basis = DetectionBasis.USER_RULE
        else:
            basis = DetectionBasis.EXACT_MEASUREMENT
        evidence = (QualityEvidenceRef(evidence_type="QualityRule", evidence_id=rule.rule_id, semantics="explicit rule"), QualityEvidenceRef(evidence_type="TableProfile", evidence_id=table.table_id, semantics=semantics.value))
        return QualityIssue(issue_id=issue_id, issue_type=issue_type, quality_dimension=quality_rule_dimension(rule.rule_type), entity_type=rule.entity_type, entity_id=table.table_id, source_id=table.source_id, snapshot_id=snapshot_id, table_id=table.table_id, column_ids=rule.column_ids, rule_id=rule.rule_id, severity=rule.severity, detection_basis=basis, measurement_semantics=semantics, observation_scope=scope, affected_count=affected_count, affected_ratio=affected_count / evaluated if evaluated and status is QualityIssueStatus.OPEN else None, affected_record_refs=refs, evidence_refs=evidence, profile_refs=tuple(profiles[item].profile_id for item in rule.column_ids if item in profiles), declared_constraint_refs=(rule.declared_constraint_ref,) if rule.declared_constraint_ref else (), domain_assertion_refs=rule.domain_assertion_refs, repairability=rule.repairability, status=status, provenance=rule.provenance)

    @staticmethod
    def _proposal_for(issue: QualityIssue, rule: QualityRule) -> RepairProposal | None:
        if rule.rule_type in {QualityRuleType.NORMALIZATION_OPPORTUNITY, QualityRuleType.EXPECTED_PRIMITIVE_TYPE, QualityRuleType.EXACT_ROW_DUPLICATION}:
            transform = "review_duplicate_handling" if rule.rule_type is QualityRuleType.EXACT_ROW_DUPLICATION else str(rule.detector_config.get("operation", "parse_expected_type"))
            repairability = Repairability.REVIEW_REQUIRED if rule.rule_type is QualityRuleType.EXACT_ROW_DUPLICATION else rule.repairability
            target_layer = "CONTROLLED_DERIVED_COPY"
            repair_type = transform
        elif rule.rule_type in {QualityRuleType.REQUIRED_VALUE, QualityRuleType.UNIQUE_VALUES, QualityRuleType.EXPECTED_PATTERN, QualityRuleType.ALLOWED_DOMAIN, QualityRuleType.NUMERIC_RANGE, QualityRuleType.DECLARED_REFERENTIAL_INTEGRITY}:
            transform = "quarantine_affected_rows"
            repairability = Repairability.REVIEW_REQUIRED if rule.repairability is Repairability.AUTO_SAFE else rule.repairability
            target_layer = "QUARANTINE_ARTIFACT"
            repair_type = transform
        else:
            return None
        proposal_id = "proposal_" + stable_digest({"issue": issue.issue_id, "transform": transform})[:32]
        plan = RepairValidationPlan(plan_id=f"plan_{proposal_id[9:]}", remeasure_metrics=("issue_affected_count", "profile_metrics"), invariants=("source remains unchanged", "source record references remain stable"), row_accounting_expectation="input plus explicit quarantine equals output; no silent row loss", abort_conditions=("batch hash or schema changes", "row accounting does not reconcile", "new higher-severity issue appears"), lineage_requirements=("preserve source_id, snapshot_id, table_id and record_ref",), expected_improvement=(f"reduce {issue.issue_type} affected count",), forbidden_new_issues=("raw-value exposure", "unaccounted rows", "semantic reinterpretation"))
        return RepairProposal(proposal_id=proposal_id, issue_refs=(issue.issue_id,), repair_type=repair_type, repairability=repairability, target_layer=target_layer, affected_entity_type=issue.entity_type, table_id=issue.table_id, column_ids=issue.column_ids, affected_record_refs=issue.affected_record_refs, transform_id=f"dirty_data_to_olap.{transform}", transform_version="1.0", preconditions=("explicit rule remains enabled", "input batches remain COMPLETE and hash-valid"), expected_effect="produce a separately validated derived representation; never overwrite source", risk_notes=("proposal is not approval", "domain meaning is not inferred"), lineage_requirement="retain source and record references", row_accounting_requirement="reconcile input, output and quarantine counts", validation_plan=plan, review_required=repairability in {Repairability.REVIEW_REQUIRED, Repairability.MANUAL_BUSINESS_DECISION}, status=RepairProposalStatus.PROPOSED, provenance=rule.provenance)

    @staticmethod
    def _dimension_summaries(evaluations, issues, table_profiles, rules):
        by_dimension: dict[QualityDimension, list[QualityIssue]] = defaultdict(list)
        rule_dimensions = {rule.rule_id: quality_rule_dimension(rule.rule_type) for rule in rules}
        for issue in issues:
            by_dimension[issue.quality_dimension].append(issue)
        summaries = []
        for dimension in QualityDimension:
            dimension_issues = by_dimension.get(dimension, [])
            dimension_evaluations = [item for item in evaluations if rule_dimensions.get(item.rule_id) is dimension]
            inconclusive = [item for item in dimension_issues if item.status is QualityIssueStatus.INCONCLUSIVE]
            refs = tuple(dict.fromkeys(ref for item in dimension_issues for ref in item.affected_record_refs))
            if inconclusive:
                status = QualityDimensionStatus.INCONCLUSIVE
                measurement = MeasurementSemantics.INCONCLUSIVE
            elif dimension_evaluations:
                status = QualityDimensionStatus.MEASURED
                measurement = dimension_issues[0].measurement_semantics if dimension_issues else dimension_evaluations[0].measurement_semantics
            else:
                status = QualityDimensionStatus.UNMEASURED
                measurement = MeasurementSemantics.UNMEASURED
            measured_rows = sum(item.evaluated_count for item in dimension_evaluations)
            summaries.append(__import__("dirty_data_to_olap.domain.contracts.quality", fromlist=["QualityDimensionSummary"]).QualityDimensionSummary(dimension=dimension, status=status, measurement_semantics=measurement, applicable_rule_count=len(dimension_evaluations), evaluated_rule_count=sum(item.applicability is RuleApplicability.APPLICABLE for item in dimension_evaluations), inconclusive_rule_count=len(inconclusive), affected_record_count=len(refs) if refs else (0 if dimension_evaluations and not inconclusive else None), measured_row_count=measured_rows, affected_ratio=(len(refs) / measured_rows if measured_rows and refs and not inconclusive else None), issue_refs=tuple(item.issue_id for item in dimension_issues)))
        return tuple(summaries)
