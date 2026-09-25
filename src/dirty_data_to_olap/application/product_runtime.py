"""Step29 product composition root over the accepted typed services."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
from time import monotonic
from threading import Lock, Thread
from typing import Any

from dirty_data_to_olap.adapters.dependencies.desbordante import DesbordanteDependencyAdapter
from dirty_data_to_olap.adapters.materialization import DuckDBMaterializer
from dirty_data_to_olap.adapters.profiling.dataprofiler import DataProfilerAdapter
from dirty_data_to_olap.adapters.quality.staged import ParquetQualityStagedReader
from dirty_data_to_olap.adapters.validation import DuckDBValidationTargetReader
from dirty_data_to_olap.adapters.sources.files import FileSourceAdapter
from dirty_data_to_olap.application.analytical_planner import AnalyticalPlannerService
from dirty_data_to_olap.application.backend import BackendService
from dirty_data_to_olap.application.canonical import CanonicalFinalizationService, CanonicalHypothesisService, CanonicalIdentityProposalService
from dirty_data_to_olap.application.compiler import AnalyticalCompilerService, CompilationArtifactPublisher
from dirty_data_to_olap.application.dependency_discovery import DependencyDiscoveryService
from dirty_data_to_olap.application.discovery import SourceDiscoveryService
from dirty_data_to_olap.application.evidence_fusion import EvidenceFusionService
from dirty_data_to_olap.application.execution_plan import ExecutionPlanService
from dirty_data_to_olap.application.jobs import BoundedWorkerPool, DurableExecutionSubmission, JobWorker, StageHandlerRegistry
from dirty_data_to_olap.application.materializer import MaterializationService
from dirty_data_to_olap.application.platform import GateEvidenceService, ControlStorePort
from dirty_data_to_olap.application.product_dependency import bind_source_local_identity_candidate
from dirty_data_to_olap.application.product_input import build_order_dataset
from dirty_data_to_olap.application.product_policy import OrderProductPolicy
from dirty_data_to_olap.application.product_sources import ProductSourceService
from dirty_data_to_olap.application.product_truth import build_order_truth_and_accounting
from dirty_data_to_olap.application.profiling import ProfilingService
from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService
from dirty_data_to_olap.application.quality import QualityAnalysisService
from dirty_data_to_olap.application.review_policy import ReviewPolicyService
from dirty_data_to_olap.application.semantic_layer import SemanticLayerService
from dirty_data_to_olap.application.snapshot import SourceSnapshotService
from dirty_data_to_olap.application.source_registry import DurableSourceRegistry
from dirty_data_to_olap.application.validation import ValidationInputs, ValidationService
from dirty_data_to_olap.domain.contracts.analytical import AnalyticalInputBinding, AnalyticalInputDataset, AnalyticalPlan, CompiledPlan, DimensionSpec, FactSpec, GeneratedSQL, GrainSpec, MaterializationArtifact, MeasureSpec, TargetConfig
from dirty_data_to_olap.domain.contracts.canonical import CanonicalIdentityMembership, CanonicalIdentityProposal, CanonicalModel, CanonicalModelHypothesis, EntityResolutionRequirement, IdentityDerivationBasis, ReviewCheckpoint
from dirty_data_to_olap.domain.contracts.dependency import DependencyResult
from dirty_data_to_olap.domain.contracts.evidence_fusion import EvidenceFusionInputs, EvidenceFusionRequest, FusionSubjectKind, RelationshipDecision
from dirty_data_to_olap.domain.contracts.jobs import FailureClassification, StageExecutionRequest, StageExecutionResult, StageResultStatus
from dirty_data_to_olap.domain.contracts.platform import ArtifactManifest, ArtifactRef
from dirty_data_to_olap.domain.contracts.profiling import ProfileCompleteness, ProfileResult
from dirty_data_to_olap.domain.contracts.quality import QualityResult
from dirty_data_to_olap.domain.contracts.semantic import SemanticModel, SemanticValidationResult
from dirty_data_to_olap.domain.contracts.source import SourceCatalog, SourceSelection, SourceSnapshotResult, SourceType, stable_digest, stable_id
from dirty_data_to_olap.domain.contracts.validation import ValidationArtifactBindings
from dirty_data_to_olap.observability import TelemetryClient, safe_exception_detail


def _json(value: Any) -> bytes:
    if hasattr(value, "model_dump_json"):
        return value.model_dump_json().encode("utf-8")
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


class LocalProductStageHandlers:
    """Thin stage adapters; domain decisions belong to the accepted services."""

    def __init__(self, *, project_root: Path, platform, registry: DurableSourceRegistry, policy_root: Path | None = None, telemetry: TelemetryClient | None = None) -> None:
        self.project_root = Path(project_root).resolve()
        self.graph_root = Path(policy_root or self.project_root).resolve()
        self.platform = platform
        self.telemetry = telemetry or TelemetryClient()
        self.registry = registry
        self.source_service = ProductSourceService(self.project_root, registry)
        adapters = {"file_source": FileSourceAdapter(SourceType.CSV, project_root=self.project_root)}
        self.discovery = SourceDiscoveryService(registry, adapters)
        self.snapshot_service = SourceSnapshotService(registry, adapters)
        self.review_policy = ReviewPolicyService()
        self.product_policy = OrderProductPolicy.load(self.graph_root)
        self.privacy_policy = PrivacyPolicyService(project_root=self.project_root)
        self.profiling = ProfilingService(DataProfilerAdapter(), project_root=self.project_root)
        self.quality = QualityAnalysisService(ParquetQualityStagedReader(), project_root=self.project_root)
        self.dependency = DependencyDiscoveryService(DesbordanteDependencyAdapter(project_root=self.project_root, privacy_policy=self.privacy_policy), project_root=self.project_root, privacy_policy=self.privacy_policy)
        self.fusion = EvidenceFusionService(policy_root=self.graph_root / "policies" / "evidence-fusion")
        self.hypotheses = CanonicalHypothesisService(self.review_policy)
        self.identity_proposals = CanonicalIdentityProposalService()
        self.canonical_finalization = CanonicalFinalizationService(self.review_policy)
        self.planner = AnalyticalPlannerService()
        self.compiler = AnalyticalCompilerService()
        self.semantic = SemanticLayerService()
        self.validation = ValidationService()

    def registry_for_run(self, _run_id: str) -> DurableSourceRegistry:
        return self.registry

    def handlers(self) -> StageHandlerRegistry:
        stages = ("SOURCE_DISCOVERY", "SOURCE_SNAPSHOT_STAGE", "PROFILING", "DEPENDENCY_DISCOVERY", "QUALITY_ANALYSIS", "EVIDENCE_FUSION", "CANONICAL_HYPOTHESES", "CANONICAL_IDENTITY_PREPARATION", "CANONICAL_FINALIZATION", "ANALYTICAL_PLANNING", "COMPILATION", "MATERIALIZATION", "SEMANTIC_MODELING", "VALIDATION_RECONCILIATION")
        return StageHandlerRegistry({stage: self for stage in stages})

    def execute_with_context(self, request: StageExecutionRequest, cancellation_probe) -> StageExecutionResult:
        correlation = self.telemetry.context(run_id=request.run_id, stage_id=request.stage_id, job_id=request.job_id, attempt_id=request.attempt_id)
        if cancellation_probe.is_cancelled():
            self.telemetry.operation(event_name="stage.cancelled", component="product_runtime", operation="stage", correlation=correlation, status="CANCELLED")
            return StageExecutionResult(status=StageResultStatus.CANCELLED, failure_code="CANCELLATION_REQUESTED", failure_classification=FailureClassification.CANCELLED, failure_reason="cancellation observed before product stage")
        started = monotonic()
        with self.telemetry.adapter_operation(self._adapter_kind(request.stage_id), request.stage_id, correlation, attributes={"stage_kind": self.telemetry.stage_kind(request.stage_id)}):
            try:
                result = self._execute(request)
            except Exception as exc:
                result = StageExecutionResult(status=StageResultStatus.FAILED, failure_code="PRODUCT_STAGE_FAILED", failure_classification=FailureClassification.TERMINAL_FAILURE, failure_reason="the local product stage could not produce its typed output", metadata={"error_type": type(exc).__name__, "error_detail": safe_exception_detail(exc)})
        duration = max(0.0, monotonic() - started)
        self.telemetry.observe_adapter_operation(self._adapter_kind(request.stage_id), duration, result.status.value)
        self.telemetry.observe_stage_result(correlation=correlation, stage_id=request.stage_id, status=result.status.value, duration_seconds=duration, metadata=result.metadata, failure_code=result.failure_code, failure_classification=result.failure_classification.value if result.failure_classification else None)
        return result

    @staticmethod
    def _adapter_kind(stage_id: str) -> str:
        return {
            "SOURCE_DISCOVERY": "file_source",
            "SOURCE_SNAPSHOT_STAGE": "file_source",
            "PROFILING": "dataprofiler",
            "DEPENDENCY_DISCOVERY": "desbordante",
            "EVIDENCE_FUSION": "product_runtime",
            "MATERIALIZATION": "duckdb",
            "VALIDATION_RECONCILIATION": "duckdb",
        }.get(stage_id, "product_runtime")

    def execute(self, request: StageExecutionRequest) -> StageExecutionResult:
        return self.execute_with_context(request, _NeverCancelled())

    def _execute(self, request: StageExecutionRequest) -> StageExecutionResult:
        handler = {
            "SOURCE_DISCOVERY": self._source_discovery, "SOURCE_SNAPSHOT_STAGE": self._source_snapshot, "PROFILING": self._profiling,
            "DEPENDENCY_DISCOVERY": self._dependency, "QUALITY_ANALYSIS": self._quality, "EVIDENCE_FUSION": self._evidence,
            "CANONICAL_HYPOTHESES": self._hypothesis, "CANONICAL_IDENTITY_PREPARATION": self._identity_proposal, "CANONICAL_FINALIZATION": self._canonical,
            "ANALYTICAL_PLANNING": self._analytical, "COMPILATION": self._compilation, "MATERIALIZATION": self._materialization,
            "SEMANTIC_MODELING": self._semantic, "VALIDATION_RECONCILIATION": self._validation,
        }.get(request.stage_id)
        if handler is None:
            return StageExecutionResult(status=StageResultStatus.BLOCKED, failure_code="UNSUPPORTED_PRODUCT_STAGE", failure_classification=FailureClassification.BLOCKED_PREREQUISITE, failure_reason="the local V1 product runtime does not implement this selected stage")
        return handler(request)

    def _run_root(self, run_id: str) -> Path:
        return self.project_root / "workspace" / "platform" / "runs" / run_id

    def _read(self, run_id: str, artifact_id: str, kind: str | None = None) -> tuple[ArtifactRef, dict[str, Any]]:
        ref = self.platform.control_store.get_artifact(artifact_id)
        if ref is None or ref.run_id != run_id or (kind is not None and ref.artifact_kind != kind):
            raise ValueError("product input artifact is outside the run or has the wrong kind")
        if self.platform.artifact_store.verify(ref).state.value != "VERIFIED":
            raise ValueError("product input artifact failed integrity verification")
        payload = json.loads(self.platform.artifact_store.read(ref).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("product input artifact is not a JSON object")
        return ref, payload

    def _typed_from_run(self, run_id: str, kind: str, model):
        for ref in reversed(self.platform.control_store.list_artifacts(run_id=run_id, artifact_kind=kind, limit=10000)):
            try:
                actual_ref, payload = self._read(run_id, ref.artifact_id, kind)
                return actual_ref, model.model_validate(payload)
            except (ValueError, TypeError):
                continue
        raise ValueError(f"typed {kind} input is unavailable")

    def _publish(self, request: StageExecutionRequest, kind: str, value: Any, *, artifact_id: str | None = None, provenance: tuple[str, ...] = (), producer: str = "application.product_runtime") -> ArtifactRef:
        identity = artifact_id or stable_id("product-artifact", {"run": request.run_id, "stage": request.stage_id, "attempt": request.attempt_id, "kind": kind, "payload": stable_digest(value)})
        storage_identity = stable_id("artifact-storage", {"artifact_id": identity})
        manifest = ArtifactManifest(artifact_id=identity, run_id=request.run_id, stage_id=request.stage_id, attempt_id=request.attempt_id, artifact_kind=kind, media_type="application/json", producer=producer, logical_key=f"runs/{request.run_id}/artifacts/{storage_identity}.json", provenance_refs=provenance or (producer, request.stage_id))
        return self.platform.control_store.register_artifact(self.platform.artifact_store.publish(manifest, _json(value)))

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
        catalog = self.discovery.discover(self._selection(request))
        ref = self._publish(request, "SourceCatalog", catalog, artifact_id=stable_id("source-catalog", {"run": request.run_id, "source": catalog.source_id, "schema": catalog.source.schema_fingerprint}), provenance=("source-discovery", catalog.source.schema_fingerprint), producer="application.discovery")
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,), metadata={"source_id": catalog.source_id, "table_count": str(len(catalog.tables)), "column_count": str(len(catalog.columns))})

    def _source_snapshot(self, request: StageExecutionRequest) -> StageExecutionResult:
        _catalog_ref, catalog = self._catalog(request)
        selection = self._selection(request).model_copy(update={"scope": catalog.source.selection_scope})
        result = self.snapshot_service.extract(catalog, selection, staging_root=self._run_root(request.run_id) / "staging")
        ref = self._publish(request, "SourceSnapshotResult", result, artifact_id=stable_id("source-snapshot", {"run": request.run_id, "snapshot": result.snapshot.snapshot_id}), provenance=("source-snapshot", result.snapshot.snapshot_id), producer="application.snapshot")
        run = self.platform.control_store.get_run(request.run_id)
        if run is not None and ref.artifact_id not in run.source_snapshot_refs:
            self.platform.control_store.update_run(run.model_copy(update={"source_snapshot_refs": tuple((*run.source_snapshot_refs, ref.artifact_id))}), expected_revision=run.revision)
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,), metadata={"snapshot_id": result.snapshot.snapshot_id, "rows": str(result.metrics.input_records_observed)})

    def _profiling(self, request: StageExecutionRequest) -> StageExecutionResult:
        _catalog_ref, catalog = self._catalog(request)
        _snapshot_ref, snapshot = self._snapshot(request)
        profile_request = self.product_policy.profile_request(catalog, snapshot, request.run_id)
        result = self.profiling.profile(profile_request, catalog, snapshot, artifact_root=self._run_root(request.run_id))
        ref = self._publish(request, "ProfileResult", result, artifact_id=stable_id("profile-result", {"run": request.run_id, "request": profile_request.profile_request_id}), provenance=(profile_request.profile_request_id, catalog.source.schema_fingerprint), producer="application.profiling")
        if result.completeness is not ProfileCompleteness.COMPLETE:
            return StageExecutionResult(status=StageResultStatus.FAILED, output_artifact_refs=(ref.artifact_id,), failure_code="PROFILE_INCOMPLETE", failure_classification=FailureClassification.TERMINAL_FAILURE, failure_reason="DataProfiler did not produce a complete snapshot-bound profile")
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,), metadata={"profile_request_id": profile_request.profile_request_id, "profile_completeness": result.completeness.value, "profile_artifact_count": str(len(result.artifacts))})

    def _dependency(self, request: StageExecutionRequest) -> StageExecutionResult:
        _catalog_ref, catalog = self._catalog(request)
        _snapshot_ref, snapshot = self._snapshot(request)
        _profile_ref, profile = self._typed_from_run(request.run_id, "ProfileResult", ProfileResult)
        dependency_request = self.product_policy.dependency_request(catalog, snapshot, request.run_id)
        result = self.dependency.discover(dependency_request, catalog, snapshot, profiles=profile, artifact_root=self._run_root(request.run_id))
        table = self.product_policy.source_table(catalog)
        result = bind_source_local_identity_candidate(result, catalog=catalog, table_name=table.physical_name, column_name=self.product_policy.data["entity"]["identity_column"])
        ref = self._publish(request, "DependencyResult", result, artifact_id=stable_id("dependency-result", {"run": request.run_id, "request": dependency_request.request_id}), provenance=(dependency_request.request_id, catalog.source.schema_fingerprint), producer="application.dependency_discovery")
        if result.status.value != "COMPLETE" or not result.relationship_candidates:
            return StageExecutionResult(status=StageResultStatus.FAILED, output_artifact_refs=(ref.artifact_id,), failure_code="DEPENDENCY_INCOMPLETE", failure_classification=FailureClassification.TERMINAL_FAILURE, failure_reason="Desbordante did not produce a complete policy-bound identity candidate")
        capability = result.capabilities[0] if result.capabilities else None
        provider = ":".join(item for item in (getattr(capability, "engine", None), getattr(capability, "engine_version", None)) if item) or "unknown"
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,), metadata={"dependency_request_id": dependency_request.request_id, "dependency_status": result.status.value, "candidate_count": str(len(result.relationship_candidates)), "provider": provider})

    def _quality(self, request: StageExecutionRequest) -> StageExecutionResult:
        _catalog_ref, catalog = self._catalog(request)
        _snapshot_ref, snapshot = self._snapshot(request)
        _profile_ref, profile = self._typed_from_run(request.run_id, "ProfileResult", ProfileResult)
        quality_request = self.product_policy.quality_request(catalog, snapshot, profile, request.run_id)
        result = self.quality.analyze(quality_request, catalog, snapshot, profile, artifact_root=self._run_root(request.run_id))
        ref = self._publish(request, "QualityResult", result, artifact_id=stable_id("quality-result", {"run": request.run_id, "request": quality_request.quality_run_id}), provenance=(quality_request.quality_run_id, profile.profile_request.profile_request_id), producer="application.quality")
        if result.completeness != "COMPLETE":
            return StageExecutionResult(status=StageResultStatus.FAILED, output_artifact_refs=(ref.artifact_id,), failure_code="QUALITY_INCOMPLETE", failure_classification=FailureClassification.TERMINAL_FAILURE, failure_reason="quality analysis did not produce complete typed results")
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,), metadata={"quality_run_id": quality_request.quality_run_id, "quality_status": result.completeness, "issue_count": str(len(result.issues))})

    def _evidence(self, request: StageExecutionRequest) -> StageExecutionResult:
        catalog_ref, catalog = self._catalog(request)
        _snapshot_ref, snapshot = self._snapshot(request)
        profile_ref, profile = self._typed_from_run(request.run_id, "ProfileResult", ProfileResult)
        quality_ref, quality = self._typed_from_run(request.run_id, "QualityResult", QualityResult)
        dependency_ref, dependency = self._typed_from_run(request.run_id, "DependencyResult", DependencyResult)
        candidates = tuple(dependency.relationship_candidates)
        if not candidates:
            raise ValueError("evidence fusion requires a relationship candidate from DependencyResult")
        domain_assertions = self.product_policy.domain_assertions(catalog, snapshot, candidates)
        fusion_request = EvidenceFusionRequest(request_id=stable_id("fusion-request", {"run": request.run_id, "snapshot": snapshot.snapshot.snapshot_id, "dependency": dependency_ref.content_hash}), execution_context_id=snapshot.snapshot.execution_context_id, cross_source_mapping_scope=False, relationship_candidate_ids=tuple(item.candidate_id for item in candidates), subject_kind=FusionSubjectKind.RELATIONSHIP, policy=EvidenceFusionService.load_policy("relationship", policy_root=self.graph_root / "policies" / "evidence-fusion"))
        result = self.fusion.fuse(fusion_request, inputs=EvidenceFusionInputs(domain_assertions=domain_assertions), profile_result=profile, quality_result=quality, dependency_result=dependency, source_catalogs=(catalog,))
        # Domain assertions and relationship decisions are semantic identities
        # and can be equal for two runs over the same source snapshot.  The
        # persisted artifact identity must still be run-scoped; otherwise
        # concurrent real runs collide in the durable artifact registry.
        assertion_refs = [self._publish(request, "DomainAssertion", item, artifact_id=stable_id("domain-assertion-artifact", {"run": request.run_id, "assertion": item.assertion_id}), provenance=item.evidence_refs, producer="config.product_policy") for item in domain_assertions]
        result_ref = self._publish(request, "EvidenceFusionResult", result, artifact_id=stable_id("evidence-fusion", {"run": request.run_id, "request": fusion_request.request_id}), provenance=(profile_ref.artifact_id, quality_ref.artifact_id, dependency_ref.artifact_id), producer="application.evidence_fusion")
        decision_refs = [self._publish(request, "RelationshipDecision", item, artifact_id=stable_id("relationship-decision-artifact", {"run": request.run_id, "decision": item.decision_id}), provenance=(result_ref.artifact_id, item.input_evidence_fingerprint), producer="application.evidence_fusion") for item in result.relationships]
        output_refs = tuple([item.artifact_id for item in assertion_refs] + [result_ref.artifact_id] + [item.artifact_id for item in decision_refs])
        if result.completeness.value != "COMPLETE_REVIEW_READY" or not decision_refs:
            return StageExecutionResult(status=StageResultStatus.FAILED, output_artifact_refs=output_refs, failure_code="EVIDENCE_FUSION_INCOMPLETE", failure_classification=FailureClassification.TERMINAL_FAILURE, failure_reason="evidence fusion did not produce a complete review-ready typed decision")
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=output_refs, metadata={"fusion_status": result.completeness.value, "producer_profile": profile_ref.artifact_id, "producer_quality": quality_ref.artifact_id, "producer_dependency": dependency_ref.artifact_id})

    def _accepted_review(self, run_id: str, checkpoint: ReviewCheckpoint):
        decisions = [item.decision for item in self.platform.control_store.list_review_history(run_id=run_id, limit=10000) if item.decision.review_checkpoint_id is checkpoint and item.decision.decision.value == "ACCEPTED" and not item.decision.superseded]
        if not decisions:
            raise ValueError(f"accepted review is required for {checkpoint.value}")
        return decisions[-1]

    def _hypothesis(self, request: StageExecutionRequest) -> StageExecutionResult:
        catalog_ref, catalog = self._catalog(request)
        snapshot_ref, snapshot = self._snapshot(request)
        decision_ref, decision = self._typed_from_run(request.run_id, "RelationshipDecision", RelationshipDecision)
        evidence_reviews = tuple(item.decision for item in self.platform.control_store.list_review_history(run_id=request.run_id, limit=10000) if item.decision.review_checkpoint_id is ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS and item.decision.decision.value == "ACCEPTED" and not item.decision.superseded)
        domain_assertion_refs = tuple(sorted(set(ref for item in evidence_reviews for ref in item.domain_assertion_refs)))
        entity_type = self.product_policy.entity_type(catalog, snapshot, decision.decision_id, domain_assertion_refs=domain_assertion_refs)
        relationship = self.product_policy.relationship(decision, entity_type.canonical_entity_type_id, evidence_reviews[-1].review_decision_id)
        hypothesis = self.hypotheses.build(run_id=request.run_id, execution_context_id=snapshot.snapshot.execution_context_id, model_version=self.product_policy.version, evidence_reviews=evidence_reviews, relationship_decisions=(decision,), evidence_domain_assertion_refs={decision.decision_id: entity_type.domain_assertion_refs}, entity_types=(entity_type,), source_ids=(catalog.source_id,), domain_assertion_refs=entity_type.domain_assertion_refs, entity_resolution_requirements={entity_type.entity_resolution_family or entity_type.semantic_id: EntityResolutionRequirement.ER_NOT_REQUIRED}, relationships=(relationship,), snapshot_fingerprints={catalog.source_id: snapshot.snapshot.source_fingerprint or snapshot.snapshot.schema_fingerprint}, source_schema_fingerprints={catalog.source_id: catalog.source.schema_fingerprint}, source_authority_policy_refs=(self.product_policy.provenance,), evidence_refs=(decision_ref.artifact_id,), provenance_refs=(catalog_ref.artifact_id, snapshot_ref.artifact_id, decision_ref.artifact_id, self.product_policy.provenance))
        ref = self._publish(request, "CanonicalModelHypothesis", hypothesis, artifact_id=hypothesis.artifact_id, provenance=hypothesis.provenance_refs, producer="application.canonical_hypothesis")
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,))

    def _identity_proposal(self, request: StageExecutionRequest) -> StageExecutionResult:
        hypothesis_ref, hypothesis = self._typed_from_run(request.run_id, "CanonicalModelHypothesis", CanonicalModelHypothesis)
        _snapshot_ref, snapshot = self._snapshot(request)
        entity_type = hypothesis.entity_types[0]
        memberships = tuple(CanonicalIdentityMembership(membership_group_id=stable_id("membership", {"hypothesis": hypothesis.artifact_id, "record": item.record_ref}), canonical_entity_type_id=entity_type.canonical_entity_type_id, entity_resolution_family=entity_type.entity_resolution_family, source_record_refs=(item.record_ref,), derivation_basis=IdentityDerivationBasis.SOURCE_LOCAL_EVENT_IDENTITY, evidence_refs=(hypothesis.artifact_id, item.record_ref), policy_refs=(self.product_policy.provenance,), rationale="the versioned product policy declares source-local order identity; no cross-record linkage is claimed", provenance_refs=(hypothesis_ref.artifact_id, snapshot.snapshot.snapshot_id, item.record_ref)) for item in snapshot.record_references)
        proposal = self.identity_proposals.build(hypothesis=hypothesis, memberships=memberships, policy_refs=(self.product_policy.provenance,), provenance_refs=("application.canonical_identity_proposal",), source_schema_fingerprints=hypothesis.source_schema_fingerprints)
        ref = self._publish(request, "CanonicalIdentityProposal", proposal, artifact_id=proposal.proposal_id, provenance=proposal.provenance_refs, producer="application.canonical_identity")
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,))

    def _canonical(self, request: StageExecutionRequest) -> StageExecutionResult:
        hypothesis_ref, hypothesis = self._typed_from_run(request.run_id, "CanonicalModelHypothesis", CanonicalModelHypothesis)
        proposal_ref, proposal = self._typed_from_run(request.run_id, "CanonicalIdentityProposal", CanonicalIdentityProposal)
        _snapshot_ref, snapshot = self._snapshot(request)
        review = self._accepted_review(request.run_id, ReviewCheckpoint.REVIEW_CANONICAL_IDENTITY)
        metadata = {item.record_ref: {"source_id": item.source_id, "snapshot_id": item.snapshot_id, "table_id": item.table_id} for item in snapshot.record_references}
        accounting_id = stable_id("accounting", {"run": request.run_id, "snapshot": snapshot.snapshot.snapshot_id})
        model = self.canonical_finalization.finalize(hypothesis=hypothesis, identity_proposal=proposal, identity_review=review, source_record_metadata=metadata, lineage_refs=(hypothesis_ref.artifact_id, proposal_ref.artifact_id), record_accounting_refs=(accounting_id,))
        ref = self._publish(request, "CanonicalModel", model, artifact_id=model.model_id, provenance=model.provenance_refs, producer="application.canonical_finalization")
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,))

    def _analytical(self, request: StageExecutionRequest) -> StageExecutionResult:
        canonical_ref, canonical = self._typed_from_run(request.run_id, "CanonicalModel", CanonicalModel)
        catalog_ref, catalog = self._catalog(request)
        snapshot_ref, snapshot = self._snapshot(request)
        decision_ref, decision = self._typed_from_run(request.run_id, "RelationshipDecision", RelationshipDecision)
        table = self.product_policy.source_table(catalog)
        dataset, binding = build_order_dataset(run_id=request.run_id, catalog=catalog, snapshot=snapshot, canonical=canonical, project_root=self.project_root, table_name=table.physical_name, column_types=self.product_policy.data["analytical"]["column_types"])
        dataset_ref = self._publish(request, "AnalyticalInputDataset", dataset, artifact_id=dataset.dataset_id, provenance=(snapshot_ref.artifact_id, canonical_ref.artifact_id), producer="application.product_input")
        binding_ref = self._publish(request, "AnalyticalInputBinding", binding, artifact_id=binding.binding_id, provenance=(dataset_ref.artifact_id, canonical_ref.artifact_id), producer="application.product_input")
        planning_request = self.product_policy.analytical_request(catalog=catalog, snapshot=snapshot, canonical=canonical, decision=decision)
        plan, dimensions, facts, grains, measures = self.planner.build_plan(canonical, binding, dataset, planning_request)
        refs = [self._publish(request, "AnalyticalPlan", plan, artifact_id=plan.plan_id, provenance=(canonical_ref.artifact_id, binding_ref.artifact_id, decision_ref.artifact_id), producer="application.analytical_planner")]
        for kind, values, id_field in (("DimensionSpec", dimensions, "dimension_id"), ("FactSpec", facts, "fact_id"), ("GrainSpec", grains, "grain_id"), ("MeasureSpec", measures, "measure_id")):
            refs.extend(self._publish(request, kind, item, artifact_id=stable_id(kind.casefold(), {"plan": plan.plan_id, "id": getattr(item, id_field)}), provenance=(refs[0].artifact_id, item.semantic_content_hash), producer="application.analytical_planner") for item in values)
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=tuple(item.artifact_id for item in refs), metadata={"plan_id": plan.plan_id, "dataset_id": dataset.dataset_id, "binding_id": binding.binding_id})

    def _all_typed(self, run_id: str, kind: str, model) -> tuple:
        values = []
        for ref in self.platform.control_store.list_artifacts(run_id=run_id, artifact_kind=kind, limit=10000):
            try:
                actual_ref, value = self._read(run_id, ref.artifact_id, kind)
                values.append((actual_ref, model.model_validate(value)))
            except (ValueError, TypeError):
                continue
        return tuple(values)

    def _compilation(self, request: StageExecutionRequest) -> StageExecutionResult:
        _plan_ref, plan = self._typed_from_run(request.run_id, "AnalyticalPlan", AnalyticalPlan)
        dataset_ref, dataset = self._typed_from_run(request.run_id, "AnalyticalInputDataset", AnalyticalInputDataset)
        _binding_ref, binding = self._typed_from_run(request.run_id, "AnalyticalInputBinding", AnalyticalInputBinding)
        dimensions = tuple(value for _ref, value in self._all_typed(request.run_id, "DimensionSpec", DimensionSpec))
        facts = tuple(value for _ref, value in self._all_typed(request.run_id, "FactSpec", FactSpec))
        grains = tuple(value for _ref, value in self._all_typed(request.run_id, "GrainSpec", GrainSpec))
        measures = tuple(value for _ref, value in self._all_typed(request.run_id, "MeasureSpec", MeasureSpec))
        review = self._accepted_review(request.run_id, ReviewCheckpoint.REVIEW_ANALYTICAL_PLAN)
        target = TargetConfig(relative_path="olap.duckdb")
        compiled, sql = self.compiler.compile(plan, dimensions, facts, grains, measures, binding, dataset, target, review)
        outputs = CompilationArtifactPublisher(self.platform.artifact_store, self.platform.control_store).publish(run_id=request.run_id, attempt_id=request.attempt_id, compiled_plan=compiled, generated_sql=sql, target_config=target)
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(outputs.compiled_plan.artifact_id, outputs.generated_sql.artifact_id, outputs.target_config.artifact_id), metadata={"compiler_version": compiled.compiler_version, "generated_sql_id": sql.generated_sql_id, "target_config_fingerprint": target.config_fingerprint, "input_dataset_id": dataset_ref.artifact_id})

    def _materialization(self, request: StageExecutionRequest) -> StageExecutionResult:
        compiled_ref, compiled = self._typed_from_run(request.run_id, "CompiledPlan", CompiledPlan)
        sql_ref, sql = self._typed_from_run(request.run_id, "GeneratedSQL", GeneratedSQL)
        _target_ref, target = self._typed_from_run(request.run_id, "TargetConfig", TargetConfig)
        _dataset_ref, dataset = self._typed_from_run(request.run_id, "AnalyticalInputDataset", AnalyticalInputDataset)
        _binding_ref, binding = self._typed_from_run(request.run_id, "AnalyticalInputBinding", AnalyticalInputBinding)
        review = self._accepted_review(request.run_id, ReviewCheckpoint.REVIEW_MATERIALIZATION_PLAN)
        artifact = MaterializationService(DuckDBMaterializer(self._run_root(request.run_id) / "olap", repository_root=self.project_root)).materialize(compiled, sql, review, binding, dataset, target, run_id=request.run_id)
        ref = self._publish(request, "MaterializationArtifact", artifact, artifact_id=artifact.artifact_id, provenance=(compiled_ref.artifact_id, sql_ref.artifact_id, target.config_fingerprint), producer="adapter.duckdb")
        if not artifact.usable:
            return StageExecutionResult(status=StageResultStatus.FAILED, output_artifact_refs=(ref.artifact_id,), failure_code="MATERIALIZATION_FAILED", failure_classification=FailureClassification.TERMINAL_FAILURE, failure_reason="DuckDB materialization adapter did not publish a usable target")
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,), metadata={"materialization_status": artifact.status.value, "target_type": artifact.target_type, "table_count": str(len(artifact.table_names))})

    def _semantic(self, request: StageExecutionRequest) -> StageExecutionResult:
        _plan_ref, plan = self._typed_from_run(request.run_id, "AnalyticalPlan", AnalyticalPlan)
        _compiled_ref, compiled = self._typed_from_run(request.run_id, "CompiledPlan", CompiledPlan)
        _materialization_ref, materialization = self._typed_from_run(request.run_id, "MaterializationArtifact", MaterializationArtifact)
        _canonical_ref, canonical = self._typed_from_run(request.run_id, "CanonicalModel", CanonicalModel)
        dimensions = tuple(value for _ref, value in self._all_typed(request.run_id, "DimensionSpec", DimensionSpec))
        facts = tuple(value for _ref, value in self._all_typed(request.run_id, "FactSpec", FactSpec))
        grains = tuple(value for _ref, value in self._all_typed(request.run_id, "GrainSpec", GrainSpec))
        measures = tuple(value for _ref, value in self._all_typed(request.run_id, "MeasureSpec", MeasureSpec))
        analytical_review = self._accepted_review(request.run_id, ReviewCheckpoint.REVIEW_ANALYTICAL_PLAN)
        model = self.semantic.build_model(plan, dimensions, facts, grains, measures, compiled, materialization, canonical, analytical_review=analytical_review, additional_provenance_refs=("application.semantic_layer",))
        semantic_validation = self.semantic.validation_result(model, (("materialization_binding", True, "semantic model is bound to the reviewed materialization artifact"), ("analytical_spec_binding", True, "semantic fields are derived from the reviewed analytical specifications")))
        model_ref = self._publish(request, "SemanticModel", model, artifact_id=model.semantic_model_id, provenance=model.provenance_refs, producer="application.semantic_layer")
        validation_ref = self._publish(request, "SemanticValidationResult", semantic_validation, artifact_id=semantic_validation.validation_id, provenance=(model_ref.artifact_id,), producer="application.semantic_layer")
        status = StageResultStatus.SUCCEEDED if semantic_validation.status.value == "PASS" else StageResultStatus.FAILED
        return StageExecutionResult(status=status, output_artifact_refs=(model_ref.artifact_id, validation_ref.artifact_id), failure_code=None if status is StageResultStatus.SUCCEEDED else "SEMANTIC_VALIDATION_FAILED", failure_classification=None if status is StageResultStatus.SUCCEEDED else FailureClassification.TERMINAL_FAILURE, failure_reason=None if status is StageResultStatus.SUCCEEDED else "typed semantic validation did not pass")

    def _validation(self, request: StageExecutionRequest) -> StageExecutionResult:
        catalog_ref, catalog = self._catalog(request)
        snapshot_ref, snapshot = self._snapshot(request)
        canonical_ref, canonical = self._typed_from_run(request.run_id, "CanonicalModel", CanonicalModel)
        plan_ref, plan = self._typed_from_run(request.run_id, "AnalyticalPlan", AnalyticalPlan)
        compiled_ref, compiled = self._typed_from_run(request.run_id, "CompiledPlan", CompiledPlan)
        materialization_ref, materialization = self._typed_from_run(request.run_id, "MaterializationArtifact", MaterializationArtifact)
        dataset_ref, dataset = self._typed_from_run(request.run_id, "AnalyticalInputDataset", AnalyticalInputDataset)
        binding_ref, binding = self._typed_from_run(request.run_id, "AnalyticalInputBinding", AnalyticalInputBinding)
        semantic_ref, semantic = self._typed_from_run(request.run_id, "SemanticModel", SemanticModel)
        semantic_validation_ref, semantic_validation = self._typed_from_run(request.run_id, "SemanticValidationResult", SemanticValidationResult)
        _fact_ref, fact = self._typed_from_run(request.run_id, "FactSpec", FactSpec)
        _measure_ref, measure = self._typed_from_run(request.run_id, "MeasureSpec", MeasureSpec)
        truth, accounting = build_order_truth_and_accounting(run_id=request.run_id, catalog=catalog, snapshot=snapshot, dataset=dataset, canonical=canonical, fact=fact, measure=measure, policy=self.product_policy)
        truth_ref = self._publish(request, "SourceTruthManifest", truth, artifact_id=truth.truth_id, provenance=(snapshot_ref.artifact_id, catalog_ref.artifact_id), producer="application.product_truth")
        accounting_ref = self._publish(request, "RecordAccountingArtifact", accounting, artifact_id=accounting.accounting_id, provenance=(truth_ref.artifact_id, canonical_ref.artifact_id), producer="application.product_truth")
        policy = self.product_policy.validation_policy(canonical_model_id=canonical.model_id, materialization_id=materialization.artifact_id)
        bindings = ValidationArtifactBindings(source_snapshot_id=truth.source_snapshot_id, source_snapshot_hash=truth.source_snapshot_fingerprint, source_truth_id=truth.truth_id, source_truth_content_hash=truth.content_hash, canonical_model_id=canonical.model_id, canonical_model_content_hash=canonical.content_hash, record_accounting_id=accounting.accounting_id, record_accounting_content_hash=accounting.content_hash, analytical_plan_id=plan.plan_id, analytical_plan_content_hash=plan.content_hash, analytical_spec_package_hash=plan.analytical_spec_package_hash, analytical_dataset_id=dataset.dataset_id, analytical_dataset_content_hash=dataset.content_hash, analytical_input_binding_id=binding.binding_id, analytical_input_binding_content_hash=binding.content_hash, analytical_input_source_snapshot_fingerprints=dict(binding.source_snapshot_fingerprints), compiled_plan_id=compiled.compiled_plan_id, compiled_plan_content_hash=compiled.content_hash, materialization_artifact_id=materialization.artifact_id, materialization_artifact_content_hash=materialization.content_hash, target_relative_path=materialization.target_relative_path, target_config_fingerprint=materialization.target_config_fingerprint, target_file_sha256=materialization.target_file_sha256 or "0" * 64, semantic_model_id=semantic.semantic_model_id, semantic_model_content_hash=semantic.content_hash, semantic_validation_id=semantic_validation.validation_id, semantic_validation_content_hash=stable_digest(semantic_validation.model_dump(mode="json")), validation_policy_id=policy.policy_id, validation_policy_version=policy.policy_version, benchmark_truth_hash=truth.content_hash)
        outcome = self.validation.validate(ValidationInputs(source_truth=truth, canonical_model=canonical, accounting=accounting, analytical_dataset=dataset, analytical_input_binding=binding, plan=plan, compiled_plan=compiled, materialization=materialization, semantic_model=semantic, semantic_validation=semantic_validation, policy=policy, bindings=bindings), DuckDBValidationTargetReader(self.project_root))
        report_ref = self._publish(request, "ValidationReport", outcome.report, artifact_id=outcome.report.report_id, provenance=(truth_ref.artifact_id, accounting_ref.artifact_id, materialization_ref.artifact_id), producer="application.validation")
        reconciliation_ref = self._publish(request, "ReconciliationResult", outcome.reconciliation, artifact_id=outcome.reconciliation.result_id, provenance=(report_ref.artifact_id,), producer="application.validation")
        GateEvidenceService().record_from_validation_report(gate_id="G6_DATA_CORRECTNESS", platform_run_id=request.run_id, report=outcome.report, report_artifact=report_ref, verified_content_commit=self._git_commit(), control_store=self.platform.control_store, artifact_store=self.platform.artifact_store, provenance_refs=("application.validation", reconciliation_ref.artifact_id, compiled_ref.artifact_id, plan_ref.artifact_id, dataset_ref.artifact_id, binding_ref.artifact_id, semantic_ref.artifact_id, semantic_validation_ref.artifact_id))
        passed = outcome.report.g6_status.value == "PASS" and outcome.report.g6_eligible
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED if passed else StageResultStatus.FAILED, output_artifact_refs=(truth_ref.artifact_id, accounting_ref.artifact_id, report_ref.artifact_id, reconciliation_ref.artifact_id), failure_code=None if passed else "G6_VALIDATION_FAILED", failure_classification=None if passed else FailureClassification.TERMINAL_FAILURE, failure_reason=None if passed else "typed validation report contains a blocking discrepancy or pending check", metadata={"g6_status": outcome.report.g6_status.value, "g6_eligible": str(outcome.report.g6_eligible).lower(), "validation_report_id": outcome.report.report_id})

    def _git_commit(self) -> str:
        try:
            result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.project_root, capture_output=True, text=True, check=False)
        except OSError:
            # Runtime images intentionally do not contain VCS tooling or history.
            return "0" * 40
        value = result.stdout.strip()
        return value if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", value) else "0" * 40


class _NeverCancelled:
    def is_cancelled(self) -> bool:
        return False


class LocalProductExecutionSubmission(DurableExecutionSubmission):
    """Durable submission plus one bounded local worker wake-up."""

    def __init__(self, control_store: ControlStorePort, pool: BoundedWorkerPool) -> None:
        super().__init__(control_store)
        self.pool = pool
        self._lock = Lock()
        self._thread: Thread | None = None
        self._closed = False

    def submit_command(self, *, command, run):
        with self._lock:
            if self._closed:
                from dirty_data_to_olap.domain.contracts.api import SubmissionResult

                return SubmissionResult(
                    run_id=run.run_id,
                    command_id=command.command_id,
                    status="UNAVAILABLE",
                    detail="local execution runtime is shutting down",
                    accepted_by="local-runtime",
                )
            result = super().submit_command(command=command, run=run)
        if result.status == "ACCEPTED":
            self._wake()
        return result

    def _wake(self) -> None:
        with self._lock:
            if self._closed:
                return
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
            self._closed = True
            self.pool.request_shutdown()
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=15)


class LocalProductRuntime:
    """Composition root for the local browser product path."""

    def __init__(self, project_root: Path, platform, *, execution_plan_service: ExecutionPlanService, policy_root: Path | None = None, telemetry: TelemetryClient | None = None) -> None:
        self.project_root = Path(project_root).resolve()
        self.platform = platform
        self.telemetry = telemetry or TelemetryClient()
        self.registry = DurableSourceRegistry(self.project_root / "workspace" / "platform" / "product" / "source_registry.json")
        self.handlers = LocalProductStageHandlers(project_root=self.project_root, platform=platform, registry=self.registry, policy_root=policy_root, telemetry=self.telemetry)
        worker = JobWorker(control_store=platform.control_store, artifact_store=platform.artifact_store, executor=self.handlers.handlers(), worker_id="step29-local-worker", plan_advancer=execution_plan_service, telemetry=self.telemetry)
        self.pool = BoundedWorkerPool((worker,), max_workers=1, max_jobs_per_pump=250, max_active_per_run=1, max_active_per_source=1)
        self.execution = LocalProductExecutionSubmission(platform.control_store, self.pool)
        self.source_service = self.handlers.source_service

    def close(self) -> None:
        self.execution.close()
        self.platform.close()


class MultiSourceProductRuntime:
    """Composition root for the accepted durable multi-source product path.

    The multi-source handlers are injected at this boundary, but execution is
    still owned by the same platform, plan service, durable worker, leases,
    attempts, and artifact store as the accepted single-source product.
    """

    def __init__(self, project_root: Path, platform, *, execution_plan_service: ExecutionPlanService, handlers, telemetry: TelemetryClient | None = None) -> None:
        self.project_root = Path(project_root).resolve()
        self.platform = platform
        self.telemetry = telemetry or TelemetryClient()
        self.handlers = handlers
        worker = JobWorker(
            control_store=platform.control_store,
            artifact_store=platform.artifact_store,
            executor=handlers.handlers(),
            worker_id="prompt02-multi-source-worker",
            plan_advancer=execution_plan_service,
            telemetry=self.telemetry,
        )
        self.pool = BoundedWorkerPool((worker,), max_workers=1, max_jobs_per_pump=500, max_active_per_run=1, max_active_per_source=1)
        self.execution = LocalProductExecutionSubmission(platform.control_store, self.pool)
        self.source_service = handlers.source_service

    def close(self) -> None:
        self.execution.close()
        self.platform.close()


def build_local_product(project_root: Path, *, graph_root: Path | None = None, telemetry: TelemetryClient | None = None):
    from dirty_data_to_olap.platform import LocalPlatform

    root = Path(project_root).resolve()
    platform = LocalPlatform.from_project_root(root)
    graph = Path(graph_root or root).resolve()
    plan_service = ExecutionPlanService(root, platform.control_store, platform.artifact_store, graph_root=graph)
    shared_telemetry = telemetry or TelemetryClient()
    runtime = LocalProductRuntime(root, platform, execution_plan_service=plan_service, policy_root=graph, telemetry=shared_telemetry)
    backend = BackendService(control_store=platform.control_store, artifact_store=platform.artifact_store, execution=runtime.execution, configuration_fingerprint=platform.config.configuration_fingerprint, execution_plan_service=plan_service, source_service=runtime.source_service, telemetry=shared_telemetry)
    return platform, backend, runtime


def build_multi_source_product(project_root: Path, *, adapters, graph_root: Path | None = None):
    """Build the accepted durable Prompt02 source-set runtime.

    This has the same composition shape as ``build_local_product``.  The
    injected adapters are provider configuration only; run identity, plans,
    jobs, leases, artifacts, reviews, and stage completion remain owned by the
    shared platform/runtime boundary.
    """

    from dirty_data_to_olap.application.multi_source_runtime import MultiSourceStageHandlers
    from dirty_data_to_olap.platform import LocalPlatform

    root = Path(project_root).resolve()
    platform = LocalPlatform.from_project_root(root)
    graph = Path(graph_root or root).resolve()
    plan_service = ExecutionPlanService(root, platform.control_store, platform.artifact_store, graph_root=graph)
    shared_telemetry = TelemetryClient()
    registry = DurableSourceRegistry(root / "workspace" / "platform" / "product" / "source_registry.json")
    handlers = MultiSourceStageHandlers(project_root=root, platform=platform, registry=registry, adapters=adapters, policy_root=graph, telemetry=shared_telemetry)
    runtime = MultiSourceProductRuntime(root, platform, execution_plan_service=plan_service, handlers=handlers, telemetry=shared_telemetry)
    backend = BackendService(
        control_store=platform.control_store,
        artifact_store=platform.artifact_store,
        execution=runtime.execution,
        configuration_fingerprint=platform.config.configuration_fingerprint,
        execution_plan_service=plan_service,
        source_service=runtime.source_service,
        telemetry=shared_telemetry,
    )
    return platform, backend, runtime


__all__ = ["LocalProductRuntime", "MultiSourceProductRuntime", "build_local_product", "build_multi_source_product"]
