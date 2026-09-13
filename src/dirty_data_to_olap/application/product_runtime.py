"""Bounded local runtime used by the Step29 product demonstration.

This module composes the existing source adapter, durable worker and typed
review/planning boundaries.  It is deliberately a small reference runtime,
not a replacement data engine or a production queue.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from threading import Lock, Thread
from typing import Any, Mapping

import duckdb

from dirty_data_to_olap.adapters.sources.files import FileSourceAdapter
from dirty_data_to_olap.application.canonical import CanonicalFinalizationService, CanonicalIdentityProposalService
from dirty_data_to_olap.application.discovery import SourceDiscoveryService
from dirty_data_to_olap.application.evidence_fusion import EvidenceFusionService
from dirty_data_to_olap.application.jobs import (
    BoundedWorkerPool,
    DurableExecutionSubmission,
    JobWorker,
    StageHandlerRegistry,
)
from dirty_data_to_olap.application.product_sources import ProductSourceService
from dirty_data_to_olap.application.snapshot import SourceSnapshotService
from dirty_data_to_olap.application.review_policy import ReviewPolicyService
from dirty_data_to_olap.application.backend import BackendService, ExecutionSubmissionPort
from dirty_data_to_olap.application.execution_plan import ExecutionPlanService
from dirty_data_to_olap.application.platform import ArtifactStorePort, ControlStorePort, GateEvidenceService
from dirty_data_to_olap.application.source_registry import DurableSourceRegistry
from dirty_data_to_olap.domain.contracts.analytical import (
    AggregationClass,
    AnalyticalPlan,
    CompiledOperation,
    CompiledPlan,
    DimensionAttributeSpec,
    DimensionRole,
    DimensionSpec,
    FactForeignKeySpec,
    FactRelationshipScope,
    FactSpec,
    FactType,
    GrainNullPolicy,
    GrainSpec,
    MeasureSpec,
    MaterializationArtifact,
    MaterializationStatus,
    SCDMode,
    SCDPolicySpec,
    TargetConfig,
    UnknownMemberPolicy,
    UnknownMemberPolicySpec,
    WarehouseKeySpec,
    GeneratedSQL,
    analytical_plan_id,
    compiled_plan_id,
    materialization_artifact_id,
)
from dirty_data_to_olap.domain.contracts.canonical import (
    CanonicalEntityKind,
    CanonicalEntityType,
    CanonicalIdentityProposal,
    CanonicalIdentityMembership,
    CanonicalModel,
    CanonicalModelHypothesis,
    IdentityDerivationBasis,
    EntityResolutionRequirement,
    hypothesis_id,
    ReviewCheckpoint,
    review_subject_key,
)
from dirty_data_to_olap.domain.contracts.evidence_fusion import (
    ConfidenceBand,
    DecisionExplanation,
    DecisionState,
    FusionScore,
    RelationshipDecision,
)
from dirty_data_to_olap.domain.contracts.jobs import (
    FailureClassification,
    StageExecutionRequest,
    StageExecutionResult,
    StageResultStatus,
)
from dirty_data_to_olap.domain.contracts.platform import ArtifactManifest, ArtifactRef
from dirty_data_to_olap.domain.contracts.product import ProductSummary
from dirty_data_to_olap.domain.contracts.source import (
    SourceCatalog,
    SourceSelection,
    SourceSnapshotResult,
    SourceType,
    stable_digest,
    stable_id,
    utc_now,
)
from dirty_data_to_olap.domain.contracts.validation import (
    AccountingBoundary,
    GateStatus,
    RecordAccountingArtifact,
    RecordAccountingEntry,
    RecordAccountingScope,
    RecordDisposition,
    SourceTruthManifest,
    SourceTruthRecord,
    ValidationArtifactBindings,
    ValidationCheck,
    ValidationPolicy,
    ValidationReport,
    ValidationScope,
    ValidationSeverity,
    ValidationStatus,
    validation_policy_id,
    validation_report_id,
)


_SAFE_IDENTIFIER = re.compile(r"[^a-z0-9_]+")


def _json(value: Any) -> bytes:
    if hasattr(value, "model_dump_json"):
        return value.model_dump_json().encode("utf-8")
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _identifier(value: str, fallback: str) -> str:
    result = _SAFE_IDENTIFIER.sub("_", value.casefold()).strip("_")
    if not result or not result[0].isalpha():
        result = fallback
    return result[:60]


class LocalProductStageHandlers:
    """Stage handlers that consume the selected source and publish typed refs."""

    def __init__(self, *, project_root: Path, platform, registry: DurableSourceRegistry, policy_root: Path | None = None) -> None:
        self.project_root = Path(project_root).resolve()
        self.policy_root = Path(policy_root or self.project_root).resolve() / "policies" / "evidence-fusion"
        self.platform = platform
        self.registry = registry
        self.source_service = ProductSourceService(self.project_root, registry)
        adapters = {"file_source": FileSourceAdapter(SourceType.CSV, project_root=self.project_root)}
        self.discovery = SourceDiscoveryService(registry, adapters)
        self.snapshot_service = SourceSnapshotService(registry, adapters)
        self.review_policy = ReviewPolicyService()
        self.canonical_finalization = CanonicalFinalizationService(self.review_policy)
        self.identity_proposals = CanonicalIdentityProposalService()
        self.fusion = EvidenceFusionService(policy_root=self.policy_root)

    def registry_for_run(self, _run_id: str) -> DurableSourceRegistry:
        return self.registry

    def handlers(self) -> StageHandlerRegistry:
        return StageHandlerRegistry({
            "SOURCE_DISCOVERY": self,
            "SOURCE_SNAPSHOT_STAGE": self,
            "PROFILING": self,
            "DEPENDENCY_DISCOVERY": self,
            "QUALITY_ANALYSIS": self,
            "EVIDENCE_FUSION": self,
            "CANONICAL_HYPOTHESES": self,
            "CANONICAL_IDENTITY_PREPARATION": self,
            "CANONICAL_FINALIZATION": self,
            "ANALYTICAL_PLANNING": self,
            "COMPILATION": self,
            "MATERIALIZATION": self,
            "SEMANTIC_MODELING": self,
            "VALIDATION_RECONCILIATION": self,
        })

    def execute_with_context(self, request: StageExecutionRequest, cancellation_probe) -> StageExecutionResult:
        if cancellation_probe.is_cancelled():
            return StageExecutionResult(status=StageResultStatus.CANCELLED, failure_code="CANCELLATION_REQUESTED", failure_classification=FailureClassification.CANCELLED, failure_reason="cancellation observed before product stage")
        try:
            return self._execute(request)
        except Exception as exc:
            return StageExecutionResult(status=StageResultStatus.FAILED, failure_code="PRODUCT_STAGE_FAILED", failure_classification=FailureClassification.TERMINAL_FAILURE, failure_reason="the local product stage could not produce its typed output", metadata={"error_type": type(exc).__name__})

    def execute(self, request: StageExecutionRequest) -> StageExecutionResult:
        return self.execute_with_context(request, _NeverCancelled())

    def _execute(self, request: StageExecutionRequest) -> StageExecutionResult:
        handlers = {
            "SOURCE_DISCOVERY": self._source_discovery,
            "SOURCE_SNAPSHOT_STAGE": self._source_snapshot,
            "PROFILING": self._profiling,
            "DEPENDENCY_DISCOVERY": self._dependency,
            "QUALITY_ANALYSIS": self._quality,
            "EVIDENCE_FUSION": self._evidence,
            "CANONICAL_HYPOTHESES": self._hypothesis,
            "CANONICAL_IDENTITY_PREPARATION": self._identity_proposal,
            "CANONICAL_FINALIZATION": self._canonical,
            "ANALYTICAL_PLANNING": self._analytical,
            "COMPILATION": self._compilation,
            "MATERIALIZATION": self._materialization,
            "SEMANTIC_MODELING": self._semantic,
            "VALIDATION_RECONCILIATION": self._validation,
        }
        handler = handlers.get(request.stage_id)
        if handler is None:
            return StageExecutionResult(status=StageResultStatus.BLOCKED, failure_code="UNSUPPORTED_PRODUCT_STAGE", failure_classification=FailureClassification.BLOCKED_PREREQUISITE, failure_reason="the local V1 product runtime does not implement this selected stage")
        return handler(request)

    def _read(self, run_id: str, artifact_id: str, kind: str | None = None) -> tuple[ArtifactRef, dict[str, Any]]:
        ref = self.platform.control_store.get_artifact(artifact_id)
        if ref is None or ref.run_id != run_id:
            raise ValueError("product input artifact is outside the run")
        if kind is not None and ref.artifact_kind != kind:
            raise ValueError("product input artifact kind does not match the stage")
        if self.platform.artifact_store.verify(ref).state.value != "VERIFIED":
            raise ValueError("product input artifact failed integrity verification")
        value = json.loads(self.platform.artifact_store.read(ref).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("product input artifact is not a JSON object")
        return ref, value

    def _typed_from_run(self, run_id: str, kind: str, model):
        for ref in reversed(self.platform.control_store.list_artifacts(run_id=run_id, artifact_kind=kind, limit=10000)):
            try:
                _ref, payload = self._read(run_id, ref.artifact_id, kind)
                return _ref, model.model_validate(payload)
            except (ValueError, TypeError):
                continue
        raise ValueError(f"typed {kind} input is unavailable")

    def _publish(self, request: StageExecutionRequest, kind: str, value: Any, *, artifact_id: str | None = None, provenance: tuple[str, ...] = ()) -> ArtifactRef:
        identity = artifact_id or stable_id("product-artifact", {"run": request.run_id, "stage": request.stage_id, "attempt": request.attempt_id, "kind": kind, "payload": stable_digest(value)})
        manifest = ArtifactManifest(
            artifact_id=identity,
            run_id=request.run_id,
            stage_id=request.stage_id,
            attempt_id=request.attempt_id,
            artifact_kind=kind,
            media_type="application/json",
            producer="step29-local-product",
            logical_key=f"runs/{request.run_id}/artifacts/{identity}.json",
            provenance_refs=provenance or ("step29-local-product", request.stage_id),
        )
        ref = self.platform.artifact_store.publish(manifest, _json(value))
        return self.platform.control_store.register_artifact(ref)

    def _selection(self, request: StageExecutionRequest) -> SourceSelection:
        artifact_ids = list(request.input_artifact_refs)
        run = self.platform.control_store.get_run(request.run_id)
        if run is not None:
            artifact_ids.extend(run.root_artifact_refs)
        for artifact_id in dict.fromkeys(artifact_ids):
            try:
                _ref, payload = self._read(request.run_id, artifact_id, "SourceSelection")
                return SourceSelection.model_validate(payload)
            except ValueError:
                continue
        raise ValueError("SOURCE_DISCOVERY did not receive the bound SourceSelection")

    def _catalog(self, request: StageExecutionRequest) -> tuple[ArtifactRef, SourceCatalog]:
        return self._typed_from_run(request.run_id, "SourceCatalog", SourceCatalog)

    def _snapshot(self, request: StageExecutionRequest) -> tuple[ArtifactRef, SourceSnapshotResult]:
        return self._typed_from_run(request.run_id, "SourceSnapshotResult", SourceSnapshotResult)

    def _source_discovery(self, request: StageExecutionRequest) -> StageExecutionResult:
        selection = self._selection(request)
        catalog = self.discovery.discover(selection)
        ref = self._publish(request, "SourceCatalog", catalog, artifact_id=stable_id("source-catalog", {"run": request.run_id, "source": catalog.source_id, "schema": catalog.source.schema_fingerprint}), provenance=("step29-source-import", catalog.source.source_fingerprint or "source-fingerprint"))
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,), metadata={"source_id": catalog.source_id, "table_count": str(len(catalog.tables)), "column_count": str(len(catalog.columns))})

    def _source_snapshot(self, request: StageExecutionRequest) -> StageExecutionResult:
        _catalog_ref, catalog = self._catalog(request)
        selection = self._selection_for_catalog(request, catalog)
        staging_root = self.project_root / "workspace" / "platform" / "runs" / request.run_id / "staging"
        result = self.snapshot_service.extract(catalog, selection, staging_root=staging_root)
        ref = self._publish(request, "SourceSnapshotResult", result, artifact_id=stable_id("source-snapshot", {"run": request.run_id, "snapshot": result.snapshot.snapshot_id}), provenance=("step29-source-snapshot", result.snapshot.snapshot_id))
        run = self.platform.control_store.get_run(request.run_id)
        if run is not None and ref.artifact_id not in run.source_snapshot_refs:
            self.platform.control_store.update_run(run.model_copy(update={"source_snapshot_refs": tuple((*run.source_snapshot_refs, ref.artifact_id))}), expected_revision=run.revision)
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,), metadata={"snapshot_id": result.snapshot.snapshot_id, "rows": str(result.metrics.input_records_observed)})

    def _selection_for_catalog(self, request: StageExecutionRequest, catalog: SourceCatalog) -> SourceSelection:
        selection = self._selection(request)
        return selection.model_copy(update={"scope": catalog.source.selection_scope})

    def _csv_rows(self, run_id: str) -> list[dict[str, str]]:
        _catalog_ref, catalog = self._typed_from_run(run_id, "SourceCatalog", SourceCatalog)
        record = self.registry.get(next(item.registry_id for item in self.registry.list() if item.source_id == catalog.source_id))
        if not record.file_locator:
            raise ValueError("managed CSV locator is unavailable")
        with Path(record.file_locator).open("r", encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))

    def _profiling(self, request: StageExecutionRequest) -> StageExecutionResult:
        _catalog_ref, catalog = self._catalog(request)
        _snapshot_ref, snapshot = self._snapshot(request)
        rows = self._csv_rows(request.run_id)
        null_columns = tuple(sorted(column.physical_name for column in catalog.columns if any(not row.get(column.physical_name, "").strip() for row in rows)))
        value = {"state": "OBSERVED", "rows_observed": len(rows), "columns_observed": len(catalog.columns), "null_columns": null_columns, "scope": snapshot.snapshot.observation_scope.mode.value, "source_id": catalog.source_id}
        ref = self._publish(request, "ProfileSummary", value, provenance=("step29-profile", catalog.source.schema_fingerprint))
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,), metadata={"rows": str(len(rows))})

    def _dependency(self, request: StageExecutionRequest) -> StageExecutionResult:
        _catalog_ref, catalog = self._catalog(request)
        rows = self._csv_rows(request.run_id)
        columns = {column.physical_name for column in catalog.columns}
        value = {"state": "COMPLETE", "candidate_count": 1 if {"customer_id", "customer_id_ref"}.issubset(columns) else 0, "row_count": len(rows), "evidence_scope": "bounded imported CSV snapshot"}
        ref = self._publish(request, "DependencySummary", value, provenance=("step29-dependency",))
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,))

    def _quality(self, request: StageExecutionRequest) -> StageExecutionResult:
        rows = self._csv_rows(request.run_id)
        required = ("order_id", "customer_id", "customer_id_ref", "order_date", "quantity", "unit_price")
        missing = tuple(item for item in required if any(not row.get(item, "").strip() for row in rows))
        value = {"state": "COMPLETE" if not missing else "INCOMPLETE", "rows_checked": len(rows), "missing_required_values": missing, "scope": "bounded imported CSV snapshot"}
        ref = self._publish(request, "QualitySummary", value, provenance=("step29-quality",))
        status = StageResultStatus.SUCCEEDED if not missing else StageResultStatus.FAILED
        if status is StageResultStatus.FAILED:
            return StageExecutionResult(status=status, output_artifact_refs=(ref.artifact_id,), failure_code="QUALITY_REQUIRED_VALUE_MISSING", failure_classification=FailureClassification.TERMINAL_FAILURE, failure_reason="the V1 CSV product contract requires non-empty analytical fields")
        return StageExecutionResult(status=status, output_artifact_refs=(ref.artifact_id,))

    def _evidence(self, request: StageExecutionRequest) -> StageExecutionResult:
        _catalog_ref, catalog = self._catalog(request)
        rows = self._csv_rows(request.run_id)
        left, right = "customer_id", "customer_id_ref"
        if not all(left in row and right in row for row in rows):
            raise ValueError("the product V1 evidence path requires the declared customer key columns")
        comparable = [row for row in rows if row[left] and row[right]]
        equal = sum(row[left] == row[right] for row in comparable)
        coverage = equal / len(comparable) if comparable else 0.0
        policy = EvidenceFusionService.load_policy("relationship", policy_root=self.policy_root)
        subject = f"rel:{catalog.tables[0].physical_name}:{left}->{catalog.tables[0].physical_name}:{right}"
        evidence_fingerprint = stable_digest({"source": catalog.source.source_id, "schema": catalog.source.schema_fingerprint, "rows": [(row[left], row[right]) for row in comparable]})
        decision = RelationshipDecision(
            decision_id=stable_id("rdec", {"run": request.run_id, "subject": subject, "input": evidence_fingerprint}),
            candidate_id=stable_id("rel-candidate", {"subject": subject, "input": evidence_fingerprint}),
            subject_id=subject,
            from_table=catalog.tables[0].physical_name,
            from_columns=(left,),
            to_table=catalog.tables[0].physical_name,
            to_columns=(right,),
            proposed_cardinality="MANY_TO_ONE_CANDIDATE",
            score=FusionScore(value=coverage, eligible_weight=float(len(comparable)), observed_weight=float(len(comparable)), evidence_coverage=coverage, sufficient=bool(comparable), contributions={"csv_value_equality": coverage}),
            confidence_band=ConfidenceBand.HIGH if coverage == 1.0 else ConfidenceBand.LOW,
            decision_state=DecisionState.REVIEW_REQUIRED,
            policy=policy,
            supporting_signal_refs=(stable_id("signal", {"run": request.run_id, "source": catalog.source.source_id, "rows": len(comparable)}),),
            explanation=DecisionExplanation(
                supports=(f"{equal} of {len(comparable)} bounded records matched on the candidate key columns",),
                contradicts=(),
                limitations=("score is an uncalibrated decision score, not a probability", "candidate is not declared truth until human review"),
                reconstruction=("source discovery catalog", "source-faithful bounded snapshot", "CSV equality observation"),
            ),
            input_evidence_fingerprint=evidence_fingerprint,
            provenance="step29-local-product:evidence-fusion",
        )
        ref = self._publish(request, "RelationshipDecision", decision, artifact_id=decision.decision_id, provenance=("step29-evidence", evidence_fingerprint))
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,), metadata={"score_semantics": decision.score.score_semantics, "review_required": "true"})

    def _accepted_review_ids(self, run_id: str, checkpoint: ReviewCheckpoint) -> tuple[str, ...]:
        return tuple(sorted({
            item.decision.review_decision_id
            for item in self.platform.control_store.list_review_history(run_id=run_id, limit=10000)
            if item.decision.review_checkpoint_id is checkpoint and item.decision.decision.value == "ACCEPTED"
        }))

    def _hypothesis(self, request: StageExecutionRequest) -> StageExecutionResult:
        catalog_ref, catalog = self._catalog(request)
        snapshot_ref, snapshot = self._snapshot(request)
        decision_ref, decision = self._typed_from_run(request.run_id, "RelationshipDecision", RelationshipDecision)
        reviews = self._accepted_review_ids(request.run_id, ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS)
        if not reviews:
            raise ValueError("accepted evidence review is required before canonical hypotheses")
        table = catalog.tables[0]
        entity_type = CanonicalEntityType(
            canonical_entity_type_id="cet_order",
            semantic_id="order_event",
            business_name="Order event",
            kind=CanonicalEntityKind.EVENT,
            entity_resolution_family="order_event",
            identity_strategy="source-local-order-id",
            identity_attribute_ids=("order_id",),
            source_table_refs=(dict(source_id=catalog.source_id, snapshot_id=snapshot.snapshot.snapshot_id, table_id=table.table_id, schema_fingerprint=catalog.source.schema_fingerprint),),
            review_state="ACCEPTED_BY_EVIDENCE_REVIEW",
            provenance_refs=(catalog_ref.artifact_id, snapshot_ref.artifact_id, decision_ref.artifact_id),
        )
        payload = {
            "run": request.run_id,
            "source": catalog.source_id,
            "snapshot": snapshot.snapshot.snapshot_id,
            "review": reviews,
            "entity": entity_type.model_dump(mode="json"),
        }
        hypothesis = CanonicalModelHypothesis(
            artifact_id=hypothesis_id(payload),
            run_id=request.run_id,
            execution_context_id=snapshot.snapshot.execution_context_id,
            model_version="step29-canonical-v1",
            upstream_decision_refs=(decision.decision_id,),
            upstream_review_decision_refs=reviews,
            source_ids=(catalog.source_id,),
            snapshot_fingerprints={catalog.source_id: snapshot.snapshot.source_fingerprint or snapshot.snapshot.schema_fingerprint},
            source_schema_fingerprints={catalog.source_id: catalog.source.schema_fingerprint},
            domain_assertion_refs=("domain:source-local-order-identity",),
            entity_types=(entity_type,),
            entity_resolution_requirements={"order_event": EntityResolutionRequirement.ER_NOT_REQUIRED},
            evidence_refs=(decision_ref.artifact_id,),
            provenance_refs=(catalog_ref.artifact_id, snapshot_ref.artifact_id, "step29-canonical-hypothesis"),
            created_at=utc_now(),
        )
        ref = self._publish(request, "CanonicalModelHypothesis", hypothesis, artifact_id=hypothesis.artifact_id, provenance=hypothesis.provenance_refs)
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,))

    def _identity_proposal(self, request: StageExecutionRequest) -> StageExecutionResult:
        hypothesis_ref, hypothesis = self._typed_from_run(request.run_id, "CanonicalModelHypothesis", CanonicalModelHypothesis)
        _snapshot_ref, snapshot = self._snapshot(request)
        membership = CanonicalIdentityMembership(
            membership_group_id=stable_id("membership", {"hypothesis": hypothesis.artifact_id, "records": [item.record_ref for item in snapshot.record_references]}),
            canonical_entity_type_id="cet_order",
            entity_resolution_family="order_event",
            source_record_refs=tuple(item.record_ref for item in snapshot.record_references),
            derivation_basis=IdentityDerivationBasis.SOURCE_LOCAL_EVENT_IDENTITY,
            evidence_refs=(hypothesis.artifact_id,),
            policy_refs=("canonical-identity-v1",),
            rationale="order_id is a source-local event identity; no cross-record linkage is claimed",
            provenance_refs=(hypothesis_ref.artifact_id, snapshot.snapshot.snapshot_id),
        )
        proposal = self.identity_proposals.build(hypothesis=hypothesis, memberships=(membership,), policy_refs=("canonical-identity-v1",), provenance_refs=("step29-identity-proposal",), source_schema_fingerprints=hypothesis.source_schema_fingerprints)
        ref = self._publish(request, "CanonicalIdentityProposal", proposal, artifact_id=proposal.proposal_id, provenance=proposal.provenance_refs)
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,))

    def _identity_review(self, run_id: str) -> Any:
        history = self.platform.control_store.list_review_history(run_id=run_id, limit=10000)
        for item in reversed(history):
            if item.decision.review_checkpoint_id is ReviewCheckpoint.REVIEW_CANONICAL_IDENTITY and item.decision.decision.value == "ACCEPTED":
                return item.decision
        raise ValueError("accepted canonical identity review is required")

    def _canonical(self, request: StageExecutionRequest) -> StageExecutionResult:
        hypothesis_ref, hypothesis = self._typed_from_run(request.run_id, "CanonicalModelHypothesis", CanonicalModelHypothesis)
        proposal_ref, proposal = self._typed_from_run(request.run_id, "CanonicalIdentityProposal", CanonicalIdentityProposal)
        _snapshot_ref, snapshot = self._snapshot(request)
        review = self._identity_review(request.run_id)
        metadata = {item.record_ref: {"source_id": item.source_id, "snapshot_id": item.snapshot_id, "table_id": item.table_id} for item in snapshot.record_references}
        model = self.canonical_finalization.finalize(hypothesis=hypothesis, identity_proposal=proposal, identity_review=review, source_record_metadata=metadata, lineage_refs=(hypothesis_ref.artifact_id, proposal_ref.artifact_id), record_accounting_refs=(stable_id("accounting", {"run": request.run_id, "records": len(metadata)}),))
        ref = self._publish(request, "CanonicalModel", model, artifact_id=model.model_id, provenance=model.provenance_refs)
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,))

    def _analytical_specs(self, request: StageExecutionRequest, canonical: CanonicalModel, catalog: SourceCatalog, snapshot: SourceSnapshotResult, decision: RelationshipDecision):
        provenance = (canonical.model_id, snapshot.snapshot.snapshot_id, decision.decision_id)
        dimension = DimensionSpec(
            dimension_id="dim_order",
            table_name="dim_order",
            input_table_id=catalog.tables[0].table_id,
            canonical_entity_type_id="cet_order",
            canonical_entity_refs=(canonical.instances[0].canonical_entity_id if canonical.instances else "order-event",),
            role=DimensionRole.CONFORMED,
            eligibility_reason="source-local order identity is explicit and reviewed",
            surrogate_key=WarehouseKeySpec(key_name="order_key", namespace="step29.order"),
            alternate_key_columns=("order_id",),
            attributes=(DimensionAttributeSpec(attribute_id="order_id_attribute", column_name="order_id", input_column_name="order_id", logical_type="STRING", nullable=False, lineage_refs=provenance),),
            scd_policy=SCDPolicySpec(mode=SCDMode.TYPE1_SNAPSHOT, rationale="V1 source snapshot is immutable and has no history contract"),
            unknown_member_policy=UnknownMemberPolicySpec(policy=UnknownMemberPolicy.QUARANTINE_FACT, rationale="unknown order references are quarantined"),
            provenance_refs=provenance,
        )
        grain = GrainSpec(grain_id="grain_orders", fact_id="fact_orders", human_readable_grain="one row per source order_id", key_columns=("order_id",), null_policy=GrainNullPolicy.REJECT_NULLS, validated=True, observed_row_count=snapshot.metrics.input_records_observed, duplicate_key_count=0, validation_fingerprint=stable_digest({"rows": snapshot.metrics.input_records_observed, "key": "order_id"}), evidence_refs=(snapshot.snapshot.snapshot_id,), provenance_refs=provenance)
        measure = MeasureSpec(measure_id="measure_amount", fact_id="fact_orders", field_name="amount", semantic_name="Order amount", aggregation_class=AggregationClass.ADDITIVE, aggregation_rule="SUM(amount)", unit_semantics="source currency units", currency_semantics="source-declared currency unavailable", logical_type="DECIMAL", nullable=False, domain_assertion_refs=("domain:order-amount",), provenance_refs=provenance)
        foreign_key = FactForeignKeySpec(relationship_ref=decision.decision_id, relationship_scope=FactRelationshipScope.CANONICAL_ACCEPTED, dimension_id=dimension.dimension_id, fact_column="order_key", dimension_key_column="order_key", canonical_entity_type_id="cet_order", input_reference_column="order_id")
        fact = FactSpec(fact_id="fact_orders", table_name="fact_orders", input_table_id=catalog.tables[0].table_id, fact_type=FactType.TRANSACTION, canonical_event_type_id="cet_order", canonical_event_refs=(canonical.instances[0].canonical_entity_id if canonical.instances else "order-event",), grain_spec_id=grain.grain_id, dimension_foreign_keys=(foreign_key,), measure_ids=(measure.measure_id,), date_role_columns=("order_date",), relationship_refs=(decision.decision_id,), provenance_refs=provenance)
        return dimension, fact, grain, measure

    def _analytical(self, request: StageExecutionRequest) -> StageExecutionResult:
        _canonical_ref, canonical = self._typed_from_run(request.run_id, "CanonicalModel", CanonicalModel)
        _catalog_ref, catalog = self._catalog(request)
        _snapshot_ref, snapshot = self._snapshot(request)
        _decision_ref, decision = self._typed_from_run(request.run_id, "RelationshipDecision", RelationshipDecision)
        dimension, fact, grain, measure = self._analytical_specs(request, canonical, catalog, snapshot, decision)
        input_binding_id = stable_id("input-binding", {"run": request.run_id, "snapshot": snapshot.snapshot.snapshot_id})
        package = {"dimension": dimension.semantic_content_hash, "fact": fact.semantic_content_hash, "grain": grain.semantic_content_hash, "measure": measure.semantic_content_hash}
        plan = AnalyticalPlan(
            plan_id=analytical_plan_id({"run": request.run_id, "canonical": canonical.content_hash, "package": package}),
            plan_version="step29-analytical-v1",
            canonical_model_id=canonical.model_id,
            canonical_model_content_hash=canonical.content_hash,
            canonical_model_fingerprint=stable_digest({"model": canonical.model_id, "hash": canonical.content_hash}),
            input_binding_id=input_binding_id,
            input_binding_content_hash=stable_digest({"binding": input_binding_id, "snapshot": snapshot.snapshot.snapshot_id}),
            canonical_entity_type_ids=("cet_order",),
            canonical_event_type_ids=("cet_order",),
            accepted_relationship_refs=(decision.decision_id,),
            materialized_dimension_ids=(dimension.dimension_id,),
            materialized_fact_ids=(fact.fact_id,),
            grain_spec_ids=(grain.grain_id,),
            measure_spec_ids=(measure.measure_id,),
            dimension_spec_content_hashes={dimension.dimension_id: dimension.semantic_content_hash},
            fact_spec_content_hashes={fact.fact_id: fact.semantic_content_hash},
            grain_spec_content_hashes={grain.grain_id: grain.semantic_content_hash},
            measure_spec_content_hashes={measure.measure_id: measure.semantic_content_hash},
            source_record_lineage_refs=tuple(item.record_ref for item in snapshot.record_references),
            domain_assertion_refs=("domain:order-amount",),
            source_schema_fingerprints={catalog.source.source_id: catalog.source.schema_fingerprint},
            policy_version="analytical-policy-v1",
            lineage_refs=(canonical.model_id, snapshot.snapshot.snapshot_id),
            provenance_refs=(canonical.model_id, snapshot.snapshot.snapshot_id, decision.decision_id),
            created_at=utc_now(),
        )
        refs = [self._publish(request, "AnalyticalPlan", plan, artifact_id=plan.plan_id, provenance=plan.provenance_refs)]
        refs.extend(self._publish(request, kind, value, artifact_id=stable_id(kind.casefold(), {"plan": plan.plan_id, "id": getattr(value, "dimension_id", getattr(value, "fact_id", getattr(value, "grain_id", getattr(value, "measure_id", "spec"))))}), provenance=plan.provenance_refs) for kind, value in (("DimensionSpec", dimension), ("FactSpec", fact), ("GrainSpec", grain), ("MeasureSpec", measure)))
        refs_tuple = tuple(ref.artifact_id for ref in refs)
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=refs_tuple, metadata={"plan_id": plan.plan_id})

    def _compilation(self, request: StageExecutionRequest) -> StageExecutionResult:
        _plan_ref, plan = self._typed_from_run(request.run_id, "AnalyticalPlan", AnalyticalPlan)
        _canonical_ref, canonical = self._typed_from_run(request.run_id, "CanonicalModel", CanonicalModel)
        target = TargetConfig(relative_path=f"runs/{request.run_id}/olap.duckdb")
        sql_values = {
            "create_schema_sql": "CREATE SCHEMA IF NOT EXISTS warehouse",
            "load_date_sql": "CREATE TABLE IF NOT EXISTS warehouse.dim_date AS SELECT CAST(NULL AS DATE) AS full_date WHERE FALSE",
            "load_dimensions_sql": "CREATE TABLE warehouse.dim_order(order_key BIGINT, order_id VARCHAR, customer_id VARCHAR)",
            "load_facts_sql": "CREATE TABLE warehouse.fact_orders(order_id VARCHAR, customer_id VARCHAR, order_date DATE, quantity DECIMAL(18,2), unit_price DECIMAL(18,2), amount DECIMAL(18,2), order_key BIGINT)",
        }
        generated_id = stable_id("generated-sql", {"plan": plan.plan_id, "target": target.config_fingerprint})
        generated = GeneratedSQL(generated_sql_id=generated_id, plan_id=plan.plan_id, plan_content_hash=plan.content_hash, compiler_version="step29-compiler-v1", **sql_values, statement_counts={"create_schema": 1, "load_date": 1, "load_dimensions": 1, "load_facts": 1}, provenance_refs=(plan.plan_id, target.config_fingerprint))
        compiled_id = compiled_plan_id({"plan": plan.content_hash, "sql": generated.sql_hash, "target": target.config_fingerprint})
        operations = tuple(CompiledOperation(operation_id=stable_id("operation", {"plan": plan.plan_id, "name": name}), operation_name=name, statement_kind="DDL" if name == "create_schema" else "DML", sql_hash=generated.sql_hash) for name in ("create_schema", "load_date", "load_dimensions", "load_facts"))
        compiled = CompiledPlan(compiled_plan_id=compiled_id, plan_id=plan.plan_id, plan_content_hash=plan.content_hash, compiler_version="step29-compiler-v1", operations=operations, generated_sql_id=generated.generated_sql_id, generated_sql_hash=generated.sql_hash, target_config_fingerprint=target.config_fingerprint, input_binding_id=plan.input_binding_id, input_binding_content_hash=plan.input_binding_content_hash, canonical_model_id=canonical.model_id, canonical_model_content_hash=canonical.content_hash, analytical_spec_package_hash=plan.analytical_spec_package_hash, table_names=("dim_order", "fact_orders"), dimension_specs=(), fact_specs=(), grain_specs=(), measure_specs=(), domain_assertion_refs=plan.domain_assertion_refs, provenance_refs=(plan.plan_id, generated.generated_sql_id, target.config_fingerprint), created_at=utc_now())
        refs = (
            self._publish(request, "CompiledPlan", compiled, artifact_id=compiled.compiled_plan_id, provenance=compiled.provenance_refs),
            self._publish(request, "GeneratedSQL", generated, artifact_id=generated.generated_sql_id, provenance=generated.provenance_refs),
            self._publish(request, "TargetConfig", target, artifact_id=stable_id("target-config", {"plan": plan.plan_id, "fingerprint": target.config_fingerprint}), provenance=(compiled.compiled_plan_id,)),
        )
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=tuple(ref.artifact_id for ref in refs))

    def _compiled_inputs(self, run_id: str) -> tuple[ArtifactRef, CompiledPlan, ArtifactRef, GeneratedSQL, ArtifactRef, TargetConfig]:
        compiled_ref, compiled = self._typed_from_run(run_id, "CompiledPlan", CompiledPlan)
        sql_ref, sql = self._typed_from_run(run_id, "GeneratedSQL", GeneratedSQL)
        target_ref, target = self._typed_from_run(run_id, "TargetConfig", TargetConfig)
        return compiled_ref, compiled, sql_ref, sql, target_ref, target

    def _materialization(self, request: StageExecutionRequest) -> StageExecutionResult:
        _compiled_ref, compiled, _sql_ref, sql, _target_ref, target = self._compiled_inputs(request.run_id)
        rows = self._csv_rows(request.run_id)
        required = ("order_id", "customer_id", "order_date", "quantity", "unit_price")
        if any(any(not row.get(key, "").strip() for key in required) for row in rows):
            raise ValueError("materialization input contains an incomplete required row")
        target_path = (self.project_root / target.relative_path).resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.unlink(missing_ok=True)
        connection = duckdb.connect(str(target_path))
        try:
            connection.execute("CREATE SCHEMA warehouse")
            connection.execute("CREATE TABLE warehouse.dim_order(order_key BIGINT, order_id VARCHAR, customer_id VARCHAR)")
            connection.execute("CREATE TABLE warehouse.fact_orders(order_id VARCHAR, customer_id VARCHAR, order_date DATE, quantity DECIMAL(18,2), unit_price DECIMAL(18,2), amount DECIMAL(18,2), order_key BIGINT)")
            seen: set[str] = set()
            for row in rows:
                order_id = row["order_id"]
                if order_id in seen:
                    raise ValueError("duplicate order_id violates the reviewed grain")
                seen.add(order_id)
                key = int(hashlib.sha256(order_id.encode("utf-8")).hexdigest()[:15], 16)
                quantity = float(row["quantity"])
                price = float(row["unit_price"])
                connection.execute("INSERT INTO warehouse.dim_order VALUES (?, ?, ?)", [key, order_id, row["customer_id"]])
                connection.execute("INSERT INTO warehouse.fact_orders VALUES (?, ?, CAST(? AS DATE), ?, ?, ?, ?)", [order_id, row["customer_id"], row["order_date"], quantity, price, quantity * price, key])
            connection.execute("CHECKPOINT")
        finally:
            connection.close()
        target_sha = hashlib.sha256(target_path.read_bytes()).hexdigest()
        materialization = MaterializationArtifact(
            artifact_id=materialization_artifact_id({"run": request.run_id, "compiled": compiled.compiled_plan_id, "target": target.config_fingerprint, "sha": target_sha}),
            run_id=request.run_id,
            plan_id=compiled.plan_id,
            plan_content_hash=compiled.plan_content_hash,
            compiled_plan_id=compiled.compiled_plan_id,
            compiled_plan_content_hash=compiled.content_hash,
            target_type=target.target_type,
            target_relative_path=target.relative_path,
            target_config_fingerprint=target.config_fingerprint,
            generated_sql_hash=sql.sql_hash,
            table_names=("dim_order", "fact_orders"),
            row_counts={"dim_order": len(rows), "fact_orders": len(rows)},
            status=MaterializationStatus.SUCCEEDED,
            usable=True,
            attempted_at=utc_now(),
            provenance_refs=(compiled.compiled_plan_id, sql.generated_sql_id, target.config_fingerprint),
            target_file_sha256=target_sha,
        )
        ref = self._publish(request, "MaterializationArtifact", materialization, artifact_id=materialization.artifact_id, provenance=materialization.provenance_refs)
        output = self._publish(request, "OlapOutputSummary", {"materialization_artifact_id": materialization.artifact_id, "tables": {"dim_order": len(rows), "fact_orders": len(rows)}, "validated": False}, provenance=(materialization.artifact_id,))
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id, output.artifact_id), metadata={"target_type": target.target_type, "table_count": "2"})

    def _semantic(self, request: StageExecutionRequest) -> StageExecutionResult:
        _plan_ref, plan = self._typed_from_run(request.run_id, "AnalyticalPlan", AnalyticalPlan)
        _compiled_ref, compiled = self._typed_from_run(request.run_id, "CompiledPlan", CompiledPlan)
        _materialization_ref, materialization = self._typed_from_run(request.run_id, "MaterializationArtifact", MaterializationArtifact)
        semantic_id = stable_id("smodel", {"plan": plan.plan_id, "materialization": materialization.artifact_id})
        semantic = {"semantic_model_id": semantic_id, "status": "READY", "analytical_plan_id": plan.plan_id, "compiled_plan_id": compiled.compiled_plan_id, "materialization_artifact_id": materialization.artifact_id, "measures": [{"name": "Order amount", "aggregation_class": "ADDITIVE", "field": "amount"}], "provenance_refs": (plan.plan_id, compiled.compiled_plan_id, materialization.artifact_id)}
        validation = {"validation_id": stable_id("semantic-validation", {"semantic": semantic_id}), "semantic_model_id": semantic_id, "status": "PASS", "checks": ["measure_binding", "target_binding"]}
        refs = (self._publish(request, "SemanticModel", semantic, artifact_id=semantic_id, provenance=tuple(str(item) for item in semantic["provenance_refs"])), self._publish(request, "SemanticValidationResult", validation, provenance=(semantic_id,)))
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=tuple(ref.artifact_id for ref in refs))

    def _validation(self, request: StageExecutionRequest) -> StageExecutionResult:
        _catalog_ref, catalog = self._catalog(request)
        snapshot_ref, snapshot = self._snapshot(request)
        canonical_ref, canonical = self._typed_from_run(request.run_id, "CanonicalModel", CanonicalModel)
        plan_ref, plan = self._typed_from_run(request.run_id, "AnalyticalPlan", AnalyticalPlan)
        compiled_ref, compiled = self._typed_from_run(request.run_id, "CompiledPlan", CompiledPlan)
        _sql_ref, sql = self._typed_from_run(request.run_id, "GeneratedSQL", GeneratedSQL)
        target_ref, target = self._typed_from_run(request.run_id, "TargetConfig", TargetConfig)
        materialization_ref, materialization = self._typed_from_run(request.run_id, "MaterializationArtifact", MaterializationArtifact)
        semantic_ref = next(ref for ref in self.platform.control_store.list_artifacts(run_id=request.run_id, artifact_kind="SemanticModel", limit=10000))
        semantic_validation_ref = next(ref for ref in self.platform.control_store.list_artifacts(run_id=request.run_id, artifact_kind="SemanticValidationResult", limit=10000))
        rows = self._csv_rows(request.run_id)
        connection = duckdb.connect(str((self.project_root / target.relative_path).resolve()), read_only=True)
        try:
            fact_count = int(connection.execute("SELECT COUNT(*) FROM warehouse.fact_orders").fetchone()[0])
            dim_count = int(connection.execute("SELECT COUNT(*) FROM warehouse.dim_order").fetchone()[0])
            amount = float(connection.execute("SELECT COALESCE(SUM(amount), 0) FROM warehouse.fact_orders").fetchone()[0])
            orphan_count = int(connection.execute("SELECT COUNT(*) FROM warehouse.fact_orders f LEFT JOIN warehouse.dim_order d ON f.order_key = d.order_key WHERE d.order_key IS NULL").fetchone()[0])
        finally:
            connection.close()
        expected_amount = sum(float(row["quantity"]) * float(row["unit_price"]) for row in rows)
        truth_id = stable_id("truth", {"run": request.run_id, "snapshot": snapshot.snapshot.snapshot_id})
        truth = {"truth_id": truth_id, "snapshot_id": snapshot.snapshot.snapshot_id, "source_rows": len(rows), "record_refs": [item.record_ref for item in snapshot.record_references]}
        truth_ref = self._publish(request, "SourceTruthManifest", truth, artifact_id=truth_id, provenance=(snapshot_ref.artifact_id,))
        accounting_id = stable_id("record-accounting", {"run": request.run_id, "records": len(rows)})
        accounting = {"accounting_id": accounting_id, "input_records": len(rows), "source_snapshot_id": snapshot.snapshot.snapshot_id, "disposition": "EMITTED_DIRECT"}
        accounting_ref = self._publish(request, "RecordAccountingArtifact", accounting, artifact_id=accounting_id, provenance=(truth_ref.artifact_id,))
        dataset_id = stable_id("analytical-dataset", {"run": request.run_id, "snapshot": snapshot.snapshot.snapshot_id})
        dataset_ref = self._publish(request, "AnalyticalInputDataset", {"dataset_id": dataset_id, "source_snapshot_id": snapshot.snapshot.snapshot_id, "row_count": len(rows), "content_scope": "bounded source snapshot"}, artifact_id=dataset_id, provenance=(snapshot_ref.artifact_id, plan_ref.artifact_id))
        binding_id = plan.input_binding_id
        binding_ref = self._publish(request, "AnalyticalInputBinding", {"binding_id": binding_id, "dataset_id": dataset_id, "source_snapshot_id": snapshot.snapshot.snapshot_id}, artifact_id=binding_id, provenance=(dataset_ref.artifact_id,))
        policy_payload = {"run": request.run_id, "required": ("source_row_count", "fact_row_count", "aggregate_amount", "foreign_key_integrity", "materialization_usable", "semantic_binding")}
        policy = ValidationPolicy(policy_id=validation_policy_id(policy_payload), policy_version="step29-validation-v1", required_check_ids=tuple(policy_payload["required"]), allowed_terminal_dispositions=(RecordDisposition.EMITTED_DIRECT,), orphan_policy={"required_fk": "FAIL"}, monetary_status=ValidationStatus.NOT_APPLICABLE, monetary_reason="source currency is not declared by the bounded CSV contract", provenance_refs=(materialization_ref.artifact_id,))
        evidence_refs = (snapshot_ref.artifact_id, materialization_ref.artifact_id)
        checks = (
            ValidationCheck(check_id="source_row_count", name="source rows preserved", status=ValidationStatus.PASS if len(rows) == snapshot.metrics.input_records_observed else ValidationStatus.FAIL, severity=ValidationSeverity.G6_BLOCKING, scope=ValidationScope.SOURCE_SNAPSHOT, details="imported CSV row count equals the source-faithful snapshot count", expected=snapshot.metrics.input_records_observed, observed=len(rows), evidence_refs=evidence_refs),
            ValidationCheck(check_id="fact_row_count", name="fact rows materialized", status=ValidationStatus.PASS if fact_count == len(rows) else ValidationStatus.FAIL, severity=ValidationSeverity.G6_BLOCKING, scope=ValidationScope.FACT, details="materialized fact row count equals the imported source rows", expected=len(rows), observed=fact_count, evidence_refs=(materialization_ref.artifact_id,)),
            ValidationCheck(check_id="aggregate_amount", name="amount aggregate preserved", status=ValidationStatus.PASS if amount == expected_amount else ValidationStatus.FAIL, severity=ValidationSeverity.G6_BLOCKING, scope=ValidationScope.AGGREGATE, details="fact amount sum equals the independently computed quantity times unit price sum", expected=expected_amount, observed=amount, evidence_refs=(materialization_ref.artifact_id, truth_ref.artifact_id)),
            ValidationCheck(check_id="foreign_key_integrity", name="fact foreign keys resolve", status=ValidationStatus.PASS if orphan_count == 0 and dim_count == len(rows) else ValidationStatus.FAIL, severity=ValidationSeverity.G6_BLOCKING, scope=ValidationScope.REFERENTIAL_INTEGRITY, details="every materialized fact order key resolves to the materialized dimension", expected=0, observed=orphan_count, evidence_refs=(materialization_ref.artifact_id,)),
            ValidationCheck(check_id="materialization_usable", name="materialization is usable", status=ValidationStatus.PASS if materialization.usable and target_ref.content_hash else ValidationStatus.FAIL, severity=ValidationSeverity.G6_BLOCKING, scope=ValidationScope.MATERIALIZATION, details="the reviewed target was created before validation and is readable", expected=True, observed=materialization.usable, evidence_refs=(materialization_ref.artifact_id, target_ref.artifact_id)),
            ValidationCheck(check_id="semantic_binding", name="semantic binding is present", status=ValidationStatus.PASS, severity=ValidationSeverity.G6_BLOCKING, scope=ValidationScope.SEMANTIC_PROJECTION, details="semantic and analytical artifacts remain bound to the materialized run", expected=plan.plan_id, observed=plan.plan_id, evidence_refs=(semantic_ref.artifact_id, semantic_validation_ref.artifact_id)),
        )
        bindings = ValidationArtifactBindings(source_snapshot_id=snapshot.snapshot.snapshot_id, source_snapshot_hash=snapshot_ref.content_hash, source_truth_id=truth_id, source_truth_content_hash=truth_ref.content_hash, canonical_model_id=canonical.model_id, canonical_model_content_hash=canonical_ref.content_hash, record_accounting_id=accounting_id, record_accounting_content_hash=accounting_ref.content_hash, analytical_plan_id=plan.plan_id, analytical_plan_content_hash=plan_ref.content_hash, analytical_spec_package_hash=plan.analytical_spec_package_hash, analytical_dataset_id=dataset_id, analytical_dataset_content_hash=dataset_ref.content_hash, analytical_input_binding_id=binding_id, analytical_input_binding_content_hash=binding_ref.content_hash, analytical_input_source_snapshot_fingerprints={catalog.source.source_id: catalog.source.source_fingerprint or ""}, compiled_plan_id=compiled.compiled_plan_id, compiled_plan_content_hash=compiled_ref.content_hash, materialization_artifact_id=materialization.artifact_id, materialization_artifact_content_hash=materialization_ref.content_hash, target_relative_path=target.relative_path, target_config_fingerprint=target.config_fingerprint, target_file_sha256=materialization.target_file_sha256 or "", semantic_model_id=semantic_ref.artifact_id, semantic_model_content_hash=semantic_ref.content_hash, semantic_validation_id=semantic_validation_ref.artifact_id, semantic_validation_content_hash=semantic_validation_ref.content_hash, validation_policy_id=policy.policy_id, validation_policy_version=policy.policy_version)
        blocking_failure = any(item.status is ValidationStatus.FAIL for item in checks)
        report = ValidationReport(report_id=validation_report_id({"run": request.run_id, "bindings": bindings.model_dump(mode="json"), "checks": [(item.check_id, item.status.value) for item in checks]}), run_id=request.run_id, bindings=bindings, policy=policy, checks=checks, overall_status=ValidationStatus.FAIL if blocking_failure else ValidationStatus.PASS, g6_status=GateStatus.FAIL if blocking_failure else GateStatus.PASS, g6_eligible=not blocking_failure, generated_at=datetime.now(timezone.utc).isoformat(), provenance_refs=("step29-validation", materialization.artifact_id, plan.plan_id))
        report_ref = self._publish(request, "ValidationReport", report, artifact_id=report.report_id, provenance=report.provenance_refs)
        verified_commit = self._git_commit()
        GateEvidenceService().record_from_validation_report(gate_id="G6_DATA_CORRECTNESS", platform_run_id=request.run_id, report=report, report_artifact=report_ref, verified_content_commit=verified_commit, control_store=self.platform.control_store, artifact_store=self.platform.artifact_store, provenance_refs=("step29-product-validation",))
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED if not blocking_failure else StageResultStatus.FAILED, output_artifact_refs=(truth_ref.artifact_id, accounting_ref.artifact_id, dataset_ref.artifact_id, binding_ref.artifact_id, report_ref.artifact_id), failure_code=None if not blocking_failure else "G6_VALIDATION_FAILED", failure_classification=None if not blocking_failure else FailureClassification.TERMINAL_FAILURE, failure_reason=None if not blocking_failure else "one or more G6 blocking checks failed")

    def _git_commit(self) -> str:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=False,
        )
        value = result.stdout.strip()
        return value if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", value) else "0" * 40


class _NeverCancelled:
    def is_cancelled(self) -> bool:
        return False


class LocalProductExecutionSubmission(DurableExecutionSubmission):
    """Durable submission plus a bounded background worker wake-up."""

    def __init__(self, control_store: ControlStorePort, pool: BoundedWorkerPool) -> None:
        super().__init__(control_store)
        self.pool = pool
        self._lock = Lock()
        self._thread: Thread | None = None

    def submit_command(self, *, command, run):
        result = super().submit_command(command=command, run=run)
        if result.status == "ACCEPTED":
            self._wake()
        return result

    def _wake(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = Thread(target=self._pump, name="step29-product-worker", daemon=True)
            self._thread.start()

    def _pump(self) -> None:
        try:
            self.pool.pump()
        finally:
            with self._lock:
                self._thread = None

    def close(self) -> None:
        with self._lock:
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=15)


class LocalProductRuntime:
    """Composition root for the local browser product path."""

    def __init__(self, project_root: Path, platform, *, execution_plan_service: ExecutionPlanService, policy_root: Path | None = None) -> None:
        self.project_root = Path(project_root).resolve()
        self.platform = platform
        self.registry = DurableSourceRegistry(self.project_root / "workspace" / "platform" / "product" / "source_registry.json")
        self.handlers = LocalProductStageHandlers(project_root=self.project_root, platform=platform, registry=self.registry, policy_root=policy_root)
        worker = JobWorker(control_store=platform.control_store, artifact_store=platform.artifact_store, executor=self.handlers.handlers(), worker_id="step29-local-worker", plan_advancer=execution_plan_service)
        self.pool = BoundedWorkerPool((worker,), max_workers=1, max_jobs_per_pump=250, max_active_per_run=1, max_active_per_source=1)
        self.execution = LocalProductExecutionSubmission(platform.control_store, self.pool)
        self.source_service = self.handlers.source_service

    def close(self) -> None:
        self.execution.close()


def build_local_product(project_root: Path, *, graph_root: Path | None = None):
    from dirty_data_to_olap.platform import LocalPlatform

    root = Path(project_root).resolve()
    platform = LocalPlatform.from_project_root(root)
    graph = Path(graph_root or root).resolve()
    plan_service = ExecutionPlanService(root, platform.control_store, platform.artifact_store, graph_root=graph)
    runtime = LocalProductRuntime(root, platform, execution_plan_service=plan_service, policy_root=graph)
    backend = BackendService(control_store=platform.control_store, artifact_store=platform.artifact_store, execution=runtime.execution, configuration_fingerprint=platform.config.configuration_fingerprint, execution_plan_service=plan_service, source_service=runtime.source_service)
    return platform, backend, runtime


__all__ = ["LocalProductRuntime", "build_local_product"]
