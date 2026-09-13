"""Step27 backend application boundary.

The service exposes control-plane operations over existing project ports.  It
does not run data engines, execute SQL, or decide domain semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping, Protocol

from dirty_data_to_olap.application.platform import (
    ArtifactStorePort,
    ArtifactConflictError,
    ConcurrencyConflictError,
    ControlStorePort,
    PlatformError,
)
from dirty_data_to_olap.application.execution_plan import ExecutionPlanService
from dirty_data_to_olap.application.product_sources import ProductSourceError, ProductSourceService
from dirty_data_to_olap.application.review_policy import ReviewPolicyService
from dirty_data_to_olap.application.visualization import VisualizationInputError, VisualizationService
from dirty_data_to_olap.domain.contracts.api import (
    ExecutionAction,
    ExecutionCommand,
    IdempotencyRecord,
    ReviewHistoryRecord,
    ReviewRecord,
    SubmissionResult,
    idempotency_fingerprint,
    safe_metadata,
)
from dirty_data_to_olap.domain.contracts.canonical import (
    ReviewCheckpoint,
    ReviewCompatibilityContext,
    ReviewDecisionStatus,
    review_subject_key,
)
from dirty_data_to_olap.domain.contracts.jobs import ExecutionPlanIntent, ExecutionPlanPreparation, JobRecord, JobStatus, PlanPreparationStatus
from dirty_data_to_olap.domain.contracts.platform import (
    ArtifactIntegrityState,
    ArtifactPublicationState,
    ArtifactRef,
    RunRecord,
    RunStatus,
    StageAttemptRecord,
    StageStatus,
)
from dirty_data_to_olap.domain.contracts.validation import ValidationReport
from dirty_data_to_olap.domain.contracts.visualization import (
    ValidationReportVisualization,
    ValidationReportVisualizationBinding,
    VisualizationGraph,
    VisualizationScope,
)
from dirty_data_to_olap.domain.contracts.source import stable_id, utc_now
from dirty_data_to_olap.domain.contracts.source import ExtractionPolicy, SelectionScope, SourceSelection
from dirty_data_to_olap.domain.contracts.product import (
    ProductAnalyticalView,
    ProductCanonicalView,
    ProductConfiguration,
    ProductDataConditionView,
    ProductMaterializationView,
    ProductOutputView,
    ProductRelationshipView,
    ProductReviewView,
    ProductSourceBinding,
    ProductSourceView,
    ProductStageView,
    ProductSummary,
    ProductValidationView,
)
from dirty_data_to_olap.domain.contracts.analytical import AnalyticalPlan, MaterializationArtifact
from dirty_data_to_olap.domain.contracts.canonical import CanonicalIdentityProposal, CanonicalModel, CanonicalModelHypothesis
from dirty_data_to_olap.domain.contracts.evidence_fusion import RelationshipDecision
from dirty_data_to_olap.domain.contracts.source import SourceCatalog, SourceSnapshotResult
from dirty_data_to_olap.domain.contracts.validation import ValidationReport


@dataclass(frozen=True)
class Principal:
    """Trusted caller context supplied by the transport/composition boundary."""

    subject: str
    scopes: frozenset[str]
    source: str = "TRUSTED_PRINCIPAL"


class ExecutionSubmissionPort(Protocol):
    """The Step28 handoff; implementations own heavy execution elsewhere."""

    def submit_command(self, *, command: ExecutionCommand, run: RunRecord) -> SubmissionResult:
        ...


class ReviewSubjectResolverPort(Protocol):
    """Resolve review semantics from trusted project-owned contract state."""

    def resolve_context(self, *, run_id: str, checkpoint: ReviewCheckpoint, subject: ArtifactRef) -> ReviewCompatibilityContext | None:
        ...


class ControlStoreReviewSubjectResolver:
    """Reference resolver backed by trusted contexts registered in the control store."""

    def __init__(self, control_store: ControlStorePort) -> None:
        self.control_store = control_store

    def resolve_context(self, *, run_id: str, checkpoint: ReviewCheckpoint, subject: ArtifactRef) -> ReviewCompatibilityContext | None:
        context = self.control_store.get_review_subject_context(run_id=run_id, checkpoint=checkpoint.value, artifact_id=subject.artifact_id)
        if context is None:
            return None
        if context.review_checkpoint_id is not checkpoint or context.subject_artifact_id != subject.artifact_id:
            return None
        return context


class BackendError(RuntimeError):
    """Safe, transport-independent application error."""

    def __init__(self, code: str, message: str, *, status: int, retryable: bool = False) -> None:
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
        super().__init__(message)


class UnavailableExecutionSubmission:
    """Explicit reference behavior until Step28 supplies a durable executor."""

    def submit_command(self, *, command: ExecutionCommand, run: RunRecord) -> SubmissionResult:
        return SubmissionResult(run_id=run.run_id, command_id=command.command_id, status="UNAVAILABLE", detail="no execution backend is configured; Step28 owns durable execution")


@dataclass(frozen=True)
class PageResult:
    items: tuple[Any, ...]
    page_size: int
    offset: int
    next_offset: int | None
    order_by: str


def _safe_key(key: str) -> str:
    try:
        IdempotencyRecord(
            scope="validation",
            key=key,
            request_fingerprint="0" * 64,
            response_status=200,
        )
    except ValueError as exc:
        raise BackendError("INVALID_IDEMPOTENCY_KEY", "Idempotency-Key has an unsafe shape", status=400) from exc
    return key


def _safe_filter(value: str | None, *, name: str) -> str | None:
    if value is not None and (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value) or ".." in value):
        raise BackendError("INVALID_FILTER", f"{name} filter has an unsafe or unsupported shape", status=400)
    return value


class BackendService:
    """Use-case service for the bounded V1 backend/control-plane surface."""

    def __init__(
        self,
        *,
        control_store: ControlStorePort,
        artifact_store: ArtifactStorePort,
        execution: ExecutionSubmissionPort | None = None,
        configuration_fingerprint: str | None = None,
        max_page_size: int = 100,
        max_projection_bytes: int = 4_000_000,
        review_subject_resolver: ReviewSubjectResolverPort | None = None,
        execution_plan_service: ExecutionPlanService | None = None,
        source_service: ProductSourceService | None = None,
    ) -> None:
        if max_page_size < 1 or max_page_size > 1000:
            raise ValueError("max_page_size must be between 1 and 1000")
        if max_projection_bytes < 1:
            raise ValueError("max_projection_bytes must be positive")
        self.control_store = control_store
        self.artifact_store = artifact_store
        self.execution = execution or UnavailableExecutionSubmission()
        self.configuration_fingerprint = configuration_fingerprint
        self.max_page_size = max_page_size
        self.max_projection_bytes = max_projection_bytes
        self.review_policy = ReviewPolicyService()
        self.review_subject_resolver = review_subject_resolver or ControlStoreReviewSubjectResolver(control_store)
        self.execution_plan_service = execution_plan_service
        self.source_service = source_service

    def _require_scope(self, principal: Principal, scope: str) -> None:
        if not principal.subject or scope not in principal.scopes:
            raise BackendError("FORBIDDEN", "caller is not authorized for this operation", status=403)

    def authorize_read(self, principal: Principal, scope: str) -> None:
        """Apply the explicit control-plane read policy at the API boundary."""

        self._require_scope(principal, scope)

    def _page(self, items: tuple[Any, ...], *, page_size: int, offset: int, order_by: str) -> PageResult:
        if page_size < 1 or page_size > self.max_page_size or offset < 0:
            raise BackendError("INVALID_PAGE", "page_size exceeds the bounded API limit or offset is invalid", status=400)
        has_more = len(items) > page_size
        visible = items[:page_size]
        return PageResult(
            items=visible,
            page_size=page_size,
            offset=offset,
            next_offset=offset + page_size if has_more else None,
            order_by=order_by,
        )

    @staticmethod
    def _idempotent_response(record: IdempotencyRecord) -> dict[str, Any]:
        return dict(record.response_body)

    def _existing_idempotency(self, *, scope: str, key: str, fingerprint: str) -> IdempotencyRecord | None:
        existing = self.control_store.get_idempotency(scope=scope, key=key)
        if existing is not None and existing.request_fingerprint != fingerprint:
            raise BackendError("IDEMPOTENCY_KEY_REUSED", "Idempotency-Key is bound to a different semantic request", status=409)
        return existing

    def create_run(
        self,
        *,
        project_id: str,
        configuration_fingerprint: str,
        git_content_commit: str | None,
        metadata: Mapping[str, str],
        principal: Principal,
        idempotency_key: str,
    ) -> tuple[RunRecord, bool]:
        self._require_scope(principal, "runs:write")
        key = _safe_key(idempotency_key)
        try:
            safe = safe_metadata(metadata)
        except ValueError as exc:
            raise BackendError("INVALID_METADATA", "run metadata contains a restricted field", status=422) from exc
        request = {
            "project_id": project_id,
            "configuration_fingerprint": configuration_fingerprint,
            "git_content_commit": git_content_commit,
            "metadata": dict(sorted(safe.items())),
        }
        fingerprint = idempotency_fingerprint(request)
        scope = f"run-create:{principal.subject}"
        if self.configuration_fingerprint is not None and configuration_fingerprint != self.configuration_fingerprint:
            raise BackendError("CONFIGURATION_CONFLICT", "run configuration does not match the configured local platform", status=409)
        run = RunRecord(
            # A distinct idempotency key represents a new run command; the
            # key participates in resource identity but not fingerprinting.
            run_id=stable_id("run", {"scope": scope, "key": key, "request": request}),
            project_id=project_id,
            configuration_fingerprint=configuration_fingerprint,
            git_content_commit=git_content_commit,
            metadata=safe,
        )
        try:
            stored = IdempotencyRecord(
                scope=scope,
                key=key,
                request_fingerprint=fingerprint,
                response_status=201,
                response_body={},
                resource_id=run.run_id,
            )
            return self.control_store.create_run_with_idempotency(run, stored)
        except BackendError:
            raise
        except ArtifactConflictError as exc:
            raise BackendError("IDEMPOTENCY_KEY_REUSED", "Idempotency-Key is bound to a different semantic request", status=409) from exc
        except PlatformError as exc:
            raise BackendError("RUN_CREATION_REJECTED", "run could not be persisted", status=409) from exc

    def get_run(self, run_id: str) -> RunRecord:
        run = self.control_store.get_run(run_id)
        if run is None:
            raise BackendError("RUN_NOT_FOUND", "run was not found", status=404)
        return run

    def list_runs(self, *, project_id: str | None, status: str | None, page_size: int, offset: int) -> PageResult:
        project_id = _safe_filter(project_id, name="project_id")
        if status is not None:
            try:
                RunStatus(status)
            except ValueError as exc:
                raise BackendError("INVALID_RUN_STATUS", "status filter is not a supported run status", status=400) from exc
        rows = self.control_store.list_runs(project_id=project_id, status=status, limit=page_size + 1, offset=offset)
        return self._page(rows, page_size=page_size, offset=offset, order_by="created_at,run_id")

    def list_attempts(self, *, run_id: str, stage_id: str | None, status: str | None, page_size: int, offset: int) -> PageResult:
        self.get_run(run_id)
        stage_id = _safe_filter(stage_id, name="stage_id")
        if status is not None:
            try:
                StageStatus(status)
            except ValueError as exc:
                raise BackendError("INVALID_STAGE_STATUS", "status filter is not a supported stage status", status=400) from exc
        rows = self.control_store.list_stage_attempts(run_id=run_id, stage_id=stage_id, status=status, limit=page_size + 1, offset=offset)
        return self._page(rows, page_size=page_size, offset=offset, order_by="stage_id,attempt_number,attempt_id")

    def get_attempt(self, *, run_id: str, attempt_id: str) -> StageAttemptRecord:
        attempt = self.control_store.get_stage_attempt(attempt_id)
        if attempt is None or attempt.run_id != run_id:
            raise BackendError("ATTEMPT_NOT_FOUND", "stage attempt was not found", status=404)
        return attempt

    def list_jobs(self, *, run_id: str, status: str | None, page_size: int, offset: int) -> PageResult:
        """Expose only the safe durable job projection; no queue payloads."""

        self.get_run(run_id)
        if status is not None:
            try:
                JobStatus(status)
            except ValueError as exc:
                raise BackendError("INVALID_JOB_STATUS", "status filter is not a supported job status", status=400) from exc
        rows = self.control_store.list_jobs(run_id=run_id, status=status, limit=page_size + 1, offset=offset)
        return self._page(rows, page_size=page_size, offset=offset, order_by="created_at,job_id")

    def get_job(self, *, run_id: str, job_id: str) -> JobRecord:
        job = self.control_store.get_job(job_id)
        if job is None or job.run_id != run_id:
            raise BackendError("JOB_NOT_FOUND", "job was not found", status=404)
        return job

    @staticmethod
    def _product_source_view(record) -> ProductSourceView:
        return ProductSourceView(
            registry_id=record.registry_id,
            source_id=record.source_id,
            display_name=record.display_name,
            source_type=record.source_type.value,
            adapter_name=record.adapter_name,
            read_only=record.read_only,
        )

    def list_product_sources(self, *, principal: Principal) -> tuple[ProductSourceView, ...]:
        self._require_scope(principal, "runs:read")
        if self.source_service is None:
            return ()
        return tuple(self._product_source_view(item) for item in self.source_service.list())

    def product_configuration(self, *, principal: Principal) -> ProductConfiguration:
        self._require_scope(principal, "runs:read")
        return ProductConfiguration(configuration_fingerprint=self.configuration_fingerprint or "unconfigured")

    def import_product_source(
        self,
        *,
        filename: str,
        payload: bytes,
        principal: Principal,
        idempotency_key: str,
    ) -> tuple[ProductSourceView, bool]:
        self._require_scope(principal, "runs:write")
        if self.source_service is None:
            raise BackendError("SOURCE_PRODUCT_UNAVAILABLE", "managed source import is not configured", status=503, retryable=True)
        key = _safe_key(idempotency_key)
        payload_hash = hashlib.sha256(payload).hexdigest()
        request = {"filename": filename, "payload_sha256": payload_hash, "byte_size": len(payload)}
        scope = f"source-import:{principal.subject}"
        fingerprint = idempotency_fingerprint(request)
        existing = self._existing_idempotency(scope=scope, key=key, fingerprint=fingerprint)
        if existing is not None:
            registry_id = existing.resource_id
            if not registry_id:
                raise BackendError("SOURCE_IMPORT_REPLAY_INVALID", "source import replay metadata is incomplete", status=409)
            try:
                return self._product_source_view(self.source_service.get(registry_id)), True
            except ProductSourceError as exc:
                raise BackendError("SOURCE_IMPORT_REPLAY_INVALID", "source import replay cannot be reconciled", status=409) from exc
        registry_id = stable_id("registry", {"principal": principal.subject, "key": key, "request": request})
        try:
            record = self.source_service.import_csv(registry_id=registry_id, filename=filename, payload=payload)
            idempotency = IdempotencyRecord(
                scope=scope,
                key=key,
                request_fingerprint=fingerprint,
                response_status=201,
                response_body={"registry_id": record.registry_id},
                resource_id=record.registry_id,
            )
            self.control_store.record_idempotency(idempotency)
            return self._product_source_view(record), False
        except ProductSourceError as exc:
            raise BackendError("SOURCE_IMPORT_REJECTED", str(exc), status=422) from exc
        except ArtifactConflictError as exc:
            raise BackendError("IDEMPOTENCY_KEY_REUSED", "Idempotency-Key is bound to a different source import", status=409) from exc

    def bind_product_source(
        self,
        *,
        run_id: str,
        registry_id: str,
        scope: SelectionScope,
        extraction: ExtractionPolicy,
        execution_context_id: str,
        principal: Principal,
        idempotency_key: str,
    ) -> tuple[ProductSourceBinding, bool]:
        self._require_scope(principal, "runs:write")
        if self.source_service is None:
            raise BackendError("SOURCE_PRODUCT_UNAVAILABLE", "managed source selection is not configured", status=503, retryable=True)
        run = self.get_run(run_id)
        key = _safe_key(idempotency_key)
        request = {
            "run_id": run_id,
            "registry_id": registry_id,
            "scope": scope.model_dump(mode="json"),
            "extraction": extraction.model_dump(mode="json"),
            "execution_context_id": execution_context_id,
        }
        fingerprint = idempotency_fingerprint(request)
        idempotency_scope = f"source-bind:{run_id}:{principal.subject}"
        existing = self._existing_idempotency(scope=idempotency_scope, key=key, fingerprint=fingerprint)
        if existing is not None:
            body = self._idempotent_response(existing).get("binding")
            if not isinstance(body, dict):
                raise BackendError("SOURCE_BIND_REPLAY_INVALID", "source binding replay metadata is incomplete", status=409)
            return ProductSourceBinding.model_validate(body), True
        try:
            record = self.source_service.get(registry_id)
            selection = self.source_service.selection(
                registry_id=registry_id,
                scope=scope,
                extraction=extraction,
                execution_context_id=execution_context_id,
            )
        except (ProductSourceError, ValueError) as exc:
            raise BackendError("SOURCE_SELECTION_REJECTED", str(exc), status=422) from exc
        artifact_id = stable_id("source-selection", {"run_id": run_id, "selection": selection.model_dump(mode="json")})
        existing_root = next((ref for ref in run.root_artifact_refs if ref == artifact_id), None)
        if run.root_artifact_refs and existing_root is None:
            raise BackendError("SOURCE_ALREADY_BOUND", "this run already has a different source binding", status=409)
        try:
            artifact = self.control_store.get_artifact(artifact_id)
            if artifact is None:
                from dirty_data_to_olap.domain.contracts.platform import ArtifactManifest

                artifact = self.artifact_store.publish(
                    ArtifactManifest(
                        artifact_id=artifact_id,
                        run_id=run_id,
                        stage_id="SOURCE_SETUP",
                        attempt_id="source-input",
                        artifact_kind="SourceSelection",
                        media_type="application/json",
                        producer="step29-product-source",
                        logical_key=f"runs/{run_id}/source-selection/{artifact_id}.json",
                        provenance_refs=("step29-product-source", "managed-csv-import"),
                    ),
                    selection.model_dump_json().encode("utf-8"),
                )
                artifact = self.control_store.register_artifact(artifact)
            elif artifact.run_id != run_id or artifact.artifact_kind != "SourceSelection":
                raise BackendError("SOURCE_BINDING_CONFLICT", "source selection artifact is not scoped to this run", status=409)
            if artifact_id not in run.root_artifact_refs:
                self.control_store.update_run(
                    run.model_copy(update={"root_artifact_refs": tuple((*run.root_artifact_refs, artifact_id))}),
                    expected_revision=run.revision,
                )
            binding = ProductSourceBinding(
                run_id=run_id,
                registry_id=record.registry_id,
                source_id=record.source_id,
                source_display_name=record.display_name,
                selection_artifact_id=artifact_id,
                extraction_max_rows=extraction.max_rows,
                extraction_chunk_size=extraction.chunk_size,
            )
            idempotency = IdempotencyRecord(
                scope=idempotency_scope,
                key=key,
                request_fingerprint=fingerprint,
                response_status=200,
                response_body={"binding": binding.model_dump(mode="json")},
                resource_id=artifact_id,
            )
            self.control_store.record_idempotency(idempotency)
            return binding, False
        except BackendError:
            raise
        except ConcurrencyConflictError as exc:
            raise BackendError("SOURCE_BINDING_CONFLICT", "run changed while source binding was being persisted", status=409) from exc
        except PlatformError as exc:
            raise BackendError("SOURCE_BINDING_REJECTED", "source binding could not be durably persisted", status=409) from exc

    def product_summary(self, *, run_id: str, principal: Principal) -> ProductSummary:
        self._require_scope(principal, "runs:read")
        run = self.get_run(run_id)
        plan = self.control_store.get_execution_plan(run_id)
        jobs = self.control_store.list_jobs(run_id=run_id, limit=10000)
        artifacts = self.control_store.list_artifacts(run_id=run_id, limit=10000)
        verified: list[tuple[Any, dict[str, Any]]] = []
        for artifact in artifacts:
            try:
                _ref, payload = self._read_verified_json(run_id=run_id, artifact_id=artifact.artifact_id)
            except BackendError:
                continue
            verified.append((artifact, payload))

        def typed(kind: str, model):
            for artifact, payload in reversed(verified):
                if artifact.artifact_kind == kind:
                    try:
                        return artifact, model.model_validate(payload)
                    except ValueError:
                        continue
            return None, None

        catalog_artifact, catalog = typed("SourceCatalog", SourceCatalog)
        snapshot_artifact, snapshot = typed("SourceSnapshotResult", SourceSnapshotResult)
        hypothesis_artifact, hypothesis = typed("CanonicalModelHypothesis", CanonicalModelHypothesis)
        proposal_artifact, proposal = typed("CanonicalIdentityProposal", CanonicalIdentityProposal)
        canonical_artifact, canonical = typed("CanonicalModel", CanonicalModel)
        analytical_artifact, analytical = typed("AnalyticalPlan", AnalyticalPlan)
        materialization_artifact, materialization = typed("MaterializationArtifact", MaterializationArtifact)
        validation_artifact, validation = typed("ValidationReport", ValidationReport)

        source_binding = None
        if self.source_service is not None:
            for artifact, payload in verified:
                if artifact.artifact_kind != "SourceSelection":
                    continue
                try:
                    selection = SourceSelection.model_validate(payload)
                    record = self.source_service.get(selection.registry_id)
                except (ValueError, ProductSourceError):
                    continue
                source_binding = ProductSourceBinding(
                    run_id=run_id,
                    registry_id=record.registry_id,
                    source_id=record.source_id,
                    source_display_name=record.display_name,
                    selection_artifact_id=artifact.artifact_id,
                    extraction_max_rows=selection.extraction.max_rows,
                    extraction_chunk_size=selection.extraction.chunk_size,
                )
                break
        source_view = None
        if source_binding is not None and self.source_service is not None:
            try:
                source_view = self._product_source_view(self.source_service.get(source_binding.registry_id))
            except ProductSourceError:
                source_view = None
        stages: list[ProductStageView] = []
        if plan is not None:
            jobs_by_stage = {job.stage_id: job for job in jobs if job.stage_id}
            for stage in plan.stages:
                job = jobs_by_stage.get(stage.stage_id)
                refs = () if job is None else tuple(job.result_refs)
                kinds = tuple(sorted({artifact.artifact_kind for artifact in artifacts if artifact.artifact_id in refs}))
                stages.append(ProductStageView(stage_id=stage.stage_id, status="PENDING" if job is None else job.status.value, selected=stage.selected, required=stage.required, artifact_kinds=kinds, artifact_ids=refs))

        pending_reviews: list[ProductReviewView] = []
        for job in jobs:
            if job.status.value != "NEEDS_REVIEW":
                continue
            contexts = job.review_contexts or ((job.review_context,) if job.review_context is not None else ())
            for context in contexts:
                subject = self.control_store.get_artifact(context.subject_artifact_id)
                if subject is None or subject.run_id != run_id:
                    continue
                current = self.control_store.get_current_review(run_id=run_id, subject_key=review_subject_key(context))
                decision = None if current is None else current.decision.decision.value
                pending_reviews.append(ProductReviewView(
                    checkpoint=context.review_checkpoint_id.value,
                    subject_artifact_id=context.subject_artifact_id,
                    subject_content_hash=subject.content_hash,
                    subject_semantic_id=context.subject_semantic_id,
                    state="REVIEW_REQUIRED" if decision is None else decision,
                    decision=decision,
                    revision=0 if current is None else current.revision,
                    context=context,
                ))

        relationship_views: list[ProductRelationshipView] = []
        for artifact, payload in verified:
            if artifact.artifact_kind != "RelationshipDecision":
                continue
            try:
                decision = RelationshipDecision.model_validate(payload)
            except ValueError:
                continue
            relationship_views.append(ProductRelationshipView(
                decision_id=decision.decision_id,
                subject_id=decision.subject_id,
                from_table=decision.from_table,
                from_columns=decision.from_columns,
                to_table=decision.to_table,
                to_columns=decision.to_columns,
                score_semantics=decision.score.score_semantics,
                score_value=decision.score.value,
                confidence_band=decision.confidence_band.value,
                decision_state=decision.decision_state.value,
                supporting_signal_count=len(decision.supporting_signal_refs),
                missing_evidence_count=len(decision.missing_evidence_refs),
            ))

        source_rows = 0 if snapshot is None else snapshot.metrics.input_records_observed
        staged_rows = 0 if snapshot is None else snapshot.metrics.staged_records
        column_count = 0 if catalog is None else len(catalog.columns)
        data_state = "NOT_EVALUATED" if snapshot is None else "OBSERVED"
        materialization_view = ProductMaterializationView(
            artifact_id=None if materialization_artifact is None else materialization_artifact.artifact_id,
            status="NOT_EVALUATED" if materialization is None else materialization.status.value,
            usable=False if materialization is None else materialization.usable,
            table_names=() if materialization is None else materialization.table_names,
            row_counts={} if materialization is None else materialization.row_counts,
            target_type=None if materialization is None else materialization.target_type,
        )
        validation_view = ProductValidationView(
            report_id=None if validation is None else validation.report_id,
            g6_status="PENDING" if validation is None else validation.g6_status.value,
            g6_eligible=False if validation is None else validation.g6_eligible,
            overall_status="NOT_EVALUATED" if validation is None else validation.overall_status.value,
            passed_check_count=0 if validation is None else sum(item.status.value == "PASS" for item in validation.checks),
            failed_check_count=0 if validation is None else sum(item.status.value == "FAIL" for item in validation.checks),
            not_evaluated_check_count=0 if validation is None else sum(item.status.value in {"NOT_EVALUATED", "REVIEW_REQUIRED"} for item in validation.checks),
            validation_artifact_id=None if validation_artifact is None else validation_artifact.artifact_id,
        )
        return ProductSummary(
            run_id=run_id,
            project_id=run.project_id,
            status=run.status.value,
            planning_phase=None if plan is None else plan.planning_phase.value,
            current_stage=next((item.stage_id for item in stages if item.status in {"RUNNING", "NEEDS_REVIEW", "BLOCKED", "FAILED"}), None),
            source=source_view,
            source_binding=source_binding,
            stages=tuple(stages),
            pending_reviews=tuple(pending_reviews),
            relationships=tuple(sorted(relationship_views, key=lambda item: item.decision_id)),
            data_condition=ProductDataConditionView(source_rows_observed=source_rows, staged_rows=staged_rows, column_count=column_count, observation_scope="BOUNDED_SOURCE_SNAPSHOT" if snapshot is not None and snapshot.snapshot.observation_scope.mode.value == "bounded" else "FULL_SOURCE_SNAPSHOT" if snapshot is not None else "unknown", state=data_state),
            canonical=ProductCanonicalView(model_id=None if canonical is None else canonical.model_id, hypothesis_id=None if hypothesis is None else hypothesis.artifact_id, proposal_id=None if proposal is None else proposal.proposal_id, entity_type_ids=() if hypothesis is None else tuple(item.canonical_entity_type_id for item in hypothesis.entity_types), membership_count=0 if proposal is None else len(proposal.memberships), identity_basis=() if proposal is None else tuple(sorted({item.derivation_basis.value for item in proposal.memberships})), state="FINALIZED" if canonical is not None else "PROPOSAL_READY" if proposal is not None else "NOT_EVALUATED"),
            analytical=ProductAnalyticalView(plan_id=None if analytical is None else analytical.plan_id, fact_ids=() if analytical is None else analytical.materialized_fact_ids, dimension_ids=() if analytical is None else analytical.materialized_dimension_ids, grain_ids=() if analytical is None else analytical.grain_spec_ids, measure_ids=() if analytical is None else analytical.measure_spec_ids, measure_semantics=() if analytical is None else tuple(analytical.measure_spec_content_hashes.keys()), state="REVIEW_REQUIRED" if analytical is not None and analytical.review_state.value == "REVIEW_REQUIRED" else "READY" if analytical is not None else "NOT_EVALUATED"),
            materialization=materialization_view,
            validation=validation_view,
            output=ProductOutputView(materialization_artifact_id=materialization_view.artifact_id, table_names=materialization_view.table_names, row_counts=materialization_view.row_counts, validated=validation_view.g6_status == "PASS" and validation_view.g6_eligible and materialization_view.usable),
        )

    def _resolve_review_subject(
        self,
        *,
        run_id: str,
        checkpoint: ReviewCheckpoint,
        subject_artifact_id: str | None,
        subject_content_hash: str | None,
        context_assertion: ReviewCompatibilityContext | None,
    ) -> tuple[ArtifactRef, ReviewCompatibilityContext, str]:
        selected_artifact_id = subject_artifact_id or (context_assertion.subject_artifact_id if context_assertion is not None else None)
        if not selected_artifact_id:
            raise BackendError("REVIEW_SUBJECT_REQUIRED", "review requires a subject artifact identity", status=422)
        if context_assertion is not None and subject_artifact_id is not None and context_assertion.subject_artifact_id != subject_artifact_id:
            raise BackendError("REVIEW_SUBJECT_MISMATCH", "subject artifact identity conflicts with the context assertion", status=422)
        subject = self._verified_artifact(run_id=run_id, artifact_id=selected_artifact_id)
        if subject_content_hash is not None and subject_content_hash != subject.content_hash:
            raise BackendError("REVIEW_SUBJECT_MISMATCH", "review subject content hash is not current", status=409)
        if context_assertion is not None and context_assertion.subject_content_hash != subject.content_hash:
            raise BackendError("REVIEW_SUBJECT_MISMATCH", "review context assertion is not bound to the current artifact hash", status=409)
        authoritative = self.review_subject_resolver.resolve_context(run_id=run_id, checkpoint=checkpoint, subject=subject)
        if authoritative is None:
            raise BackendError("REVIEW_CONTEXT_UNAVAILABLE", "authoritative review context is not registered for this subject", status=409)
        if authoritative.review_checkpoint_id is not checkpoint or authoritative.subject_artifact_id != subject.artifact_id:
            raise BackendError("REVIEW_CONTEXT_INVALID", "server review context is not bound to the requested subject", status=409)
        if context_assertion is not None and context_assertion.model_dump(mode="json") != authoritative.model_dump(mode="json"):
            raise BackendError("REVIEW_CONTEXT_MISMATCH", "client context is only an expected binding assertion and did not match server truth", status=409)
        return subject, authoritative, review_subject_key(authoritative)

    def review(
        self,
        *,
        run_id: str,
        checkpoint: ReviewCheckpoint,
        context: ReviewCompatibilityContext | None = None,
        subject_artifact_id: str | None = None,
        subject_content_hash: str | None = None,
        decision: ReviewDecisionStatus,
        rationale: str,
        expected_revision: int,
        principal: Principal,
        idempotency_key: str,
    ) -> tuple[ReviewRecord, bool]:
        self._require_scope(principal, "reviews:write")
        decision = ReviewDecisionStatus(decision)
        if expected_revision < 0:
            raise BackendError("INVALID_REVIEW_REVISION", "expected_revision must be non-negative", status=422)
        if decision in {ReviewDecisionStatus.INVALIDATED, ReviewDecisionStatus.SKIPPED}:
            code = "REVIEW_SKIP_UNSUPPORTED" if decision is ReviewDecisionStatus.SKIPPED else "INVALID_REVIEW_ACTION"
            message = "SKIPPED is not supported by the Step27 API until a server-owned skip authorization source exists" if decision is ReviewDecisionStatus.SKIPPED else "invalidated is a lifecycle result, not a generic review action"
            raise BackendError(code, message, status=422)
        if context is not None and context.review_checkpoint_id is not checkpoint:
            raise BackendError("WRONG_REVIEW_CHECKPOINT", "review context checkpoint does not match the API resource", status=422)
        key = _safe_key(idempotency_key)
        run = self.get_run(run_id)
        _subject, authoritative, subject_key = self._resolve_review_subject(
            run_id=run_id,
            checkpoint=checkpoint,
            subject_artifact_id=subject_artifact_id,
            subject_content_hash=subject_content_hash,
            context_assertion=context,
        )
        scope = stable_id("review-scope", {
            "run_id": run_id,
            "subject_key": subject_key,
            "principal": principal.subject,
        })
        request = {
            "run_id": run_id,
            "checkpoint": checkpoint.value,
            "context": authoritative.model_dump(mode="json"),
            "decision": decision.value,
            "rationale": rationale,
            "expected_revision": expected_revision,
        }
        fingerprint = idempotency_fingerprint(request)
        current = self.control_store.get_current_review(run_id=run_id, subject_key=subject_key)
        actual_revision = 0 if current is None else current.revision
        if actual_revision != expected_revision:
            existing = self._existing_idempotency(scope=scope, key=key, fingerprint=fingerprint)
            if existing is None:
                raise BackendError("REVIEW_REVISION_CONFLICT", "review subject revision is stale", status=409)
        try:
            review_decision = self.review_policy.create_decision(
                authoritative,
                decision=decision,
                actor=principal.subject,
                actor_source=principal.source,
                rationale=rationale,
            )
            record = ReviewRecord(run_id=run_id, subject_key=subject_key, decision=review_decision, revision=expected_revision + 1)
            stored = IdempotencyRecord(
                scope=scope,
                key=key,
                request_fingerprint=fingerprint,
                response_status=200,
                response_body={},
                resource_id=record.decision.review_decision_id,
            )
            return self.control_store.record_review_with_idempotency(record, expected_revision=expected_revision, idempotency=stored)
        except BackendError:
            raise
        except ArtifactConflictError as exc:
            raise BackendError("IDEMPOTENCY_KEY_REUSED", "Idempotency-Key is bound to a different semantic request", status=409) from exc
        except ConcurrencyConflictError as exc:
            raise BackendError("REVIEW_REVISION_CONFLICT", "review subject revision is stale", status=409) from exc
        except (ValueError, PlatformError) as exc:
            raise BackendError("REVIEW_REJECTED", "review action could not be recorded", status=422) from exc

    def invalidate_review(
        self,
        *,
        run_id: str,
        checkpoint: ReviewCheckpoint,
        context: ReviewCompatibilityContext | None = None,
        subject_artifact_id: str | None = None,
        subject_content_hash: str | None = None,
        reason: str,
        expected_revision: int,
        principal: Principal,
        idempotency_key: str,
    ) -> tuple[ReviewRecord, bool]:
        self._require_scope(principal, "reviews:write")
        key = _safe_key(idempotency_key)
        _subject, authoritative, subject_key = self._resolve_review_subject(
            run_id=run_id,
            checkpoint=checkpoint,
            subject_artifact_id=subject_artifact_id,
            subject_content_hash=subject_content_hash,
            context_assertion=context,
        )
        scope = stable_id("review-invalidate-scope", {
            "run_id": run_id,
            "subject_key": subject_key,
            "principal": principal.subject,
        })
        fingerprint = idempotency_fingerprint({"run_id": run_id, "checkpoint": checkpoint.value, "context": authoritative.model_dump(mode="json"), "reason": reason, "expected_revision": expected_revision})
        current = self.control_store.get_current_review(run_id=run_id, subject_key=subject_key)
        if current is None:
            existing = self._existing_idempotency(scope=scope, key=key, fingerprint=fingerprint)
            if existing is None:
                raise BackendError("REVIEW_NOT_FOUND", "review subject has no current decision", status=404)
        elif current.revision != expected_revision:
            existing = self._existing_idempotency(scope=scope, key=key, fingerprint=fingerprint)
            if existing is None:
                raise BackendError("REVIEW_REVISION_CONFLICT", "review subject revision is stale", status=409)
        if not reason.strip():
            raise BackendError("INVALID_REVIEW_REASON", "review invalidation requires a reason", status=422)
        try:
            invalidated = self.review_policy.invalidate(current.decision if current is not None else ReviewRecord.model_validate(self._idempotent_response(existing)["review"]).decision, reason)
            record = ReviewRecord(run_id=run_id, subject_key=subject_key, decision=invalidated, revision=expected_revision + 1)
            stored = IdempotencyRecord(
                scope=scope,
                key=key,
                request_fingerprint=fingerprint,
                response_status=200,
                response_body={},
                resource_id=record.decision.review_decision_id,
            )
            return self.control_store.record_review_with_idempotency(record, expected_revision=expected_revision, idempotency=stored)
        except ArtifactConflictError as exc:
            raise BackendError("IDEMPOTENCY_KEY_REUSED", "Idempotency-Key is bound to a different semantic request", status=409) from exc
        except ConcurrencyConflictError as exc:
            raise BackendError("REVIEW_REVISION_CONFLICT", "review subject revision is stale", status=409) from exc
        except (ValueError, PlatformError) as exc:
            raise BackendError("REVIEW_REJECTED", "review invalidation could not be recorded", status=422) from exc

    def list_reviews(self, *, run_id: str, subject_key: str | None, page_size: int, offset: int) -> PageResult:
        self.get_run(run_id)
        rows = self.control_store.list_review_history(run_id=run_id, subject_key=subject_key, limit=page_size + 1, offset=offset)
        return self._page(rows, page_size=page_size, offset=offset, order_by="subject_key,revision")

    def _verified_artifact(self, *, run_id: str, artifact_id: str) -> ArtifactRef:
        artifact = self.control_store.get_artifact(artifact_id)
        if artifact is None or artifact.run_id != run_id:
            raise BackendError("ARTIFACT_NOT_FOUND", "artifact was not found", status=404)
        if artifact.publication_state is not ArtifactPublicationState.PUBLISHED:
            raise BackendError("ARTIFACT_NOT_CONSUMABLE", "artifact is not published and consumable", status=409)
        try:
            stored = self.artifact_store.stat(artifact)
            integrity = self.artifact_store.verify(artifact)
        except (KeyError, PlatformError, OSError) as exc:
            raise BackendError("ARTIFACT_INTEGRITY_FAILED", "registered artifact could not be verified", status=409) from exc
        if stored != artifact or integrity.state is not ArtifactIntegrityState.VERIFIED:
            raise BackendError("ARTIFACT_INTEGRITY_FAILED", "registered artifact could not be verified", status=409)
        return artifact

    def artifact(self, *, run_id: str, artifact_id: str) -> ArtifactRef:
        return self._verified_artifact(run_id=run_id, artifact_id=artifact_id)

    def list_artifacts(self, *, run_id: str, stage_id: str | None, artifact_kind: str | None, page_size: int, offset: int) -> PageResult:
        self.get_run(run_id)
        stage_id = _safe_filter(stage_id, name="stage_id")
        artifact_kind = _safe_filter(artifact_kind, name="artifact_kind")
        rows = self.control_store.list_artifacts(run_id=run_id, stage_id=stage_id, artifact_kind=artifact_kind, limit=page_size + 1, offset=offset)
        # The listing is metadata-only, but every returned reference is still
        # checked against the control/artifact stores before it is exposed.
        verified = tuple(self._verified_artifact(run_id=run_id, artifact_id=item.artifact_id) for item in rows)
        return self._page(verified, page_size=page_size, offset=offset, order_by="artifact_id")

    def register_artifact(self, *, run_id: str, artifact_id: str, principal: Principal) -> ArtifactRef:
        self._require_scope(principal, "artifacts:write")
        artifact = self._verified_artifact(run_id=run_id, artifact_id=artifact_id) if self.control_store.get_artifact(artifact_id) else None
        if artifact is not None:
            return artifact
        try:
            candidate = self.artifact_store.stat(artifact_id)
        except (KeyError, PlatformError) as exc:
            raise BackendError("ARTIFACT_NOT_FOUND", "artifact was not found in the configured artifact store", status=404) from exc
        if candidate.run_id != run_id or candidate.storage_mode.value != "MANAGED":
            raise BackendError("ARTIFACT_SCOPE_REJECTED", "artifact is outside the requested project run scope", status=403)
        if candidate.publication_state is not ArtifactPublicationState.PUBLISHED:
            raise BackendError("ARTIFACT_NOT_CONSUMABLE", "only published artifacts can be registered", status=409)
        try:
            if self.artifact_store.verify(candidate).state is not ArtifactIntegrityState.VERIFIED:
                raise BackendError("ARTIFACT_INTEGRITY_FAILED", "artifact content could not be verified", status=409)
            return self.control_store.register_artifact(candidate)
        except BackendError:
            raise
        except PlatformError as exc:
            raise BackendError("ARTIFACT_REGISTRATION_REJECTED", "artifact registration was rejected", status=409) from exc

    def artifact_payload(self, *, run_id: str, artifact_id: str, principal: Principal) -> bytes:
        """Generic content serving is intentionally denied at this boundary."""

        self._require_scope(principal, "artifacts:read")
        artifact = self._verified_artifact(run_id=run_id, artifact_id=artifact_id)
        raise BackendError("ARTIFACT_PAYLOAD_NOT_EXPOSED", "generic artifact payload access is not exposed by the V1 API", status=403)

    def _read_verified_json(self, *, run_id: str, artifact_id: str) -> tuple[ArtifactRef, dict[str, Any]]:
        artifact = self._verified_artifact(run_id=run_id, artifact_id=artifact_id)
        if artifact.byte_size > self.max_projection_bytes:
            raise BackendError("PROJECTION_TOO_LARGE", "trusted projection exceeds the bounded API payload limit", status=413)
        try:
            payload = json.loads(self.artifact_store.read(artifact).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, OSError, PlatformError) as exc:
            raise BackendError("ARTIFACT_PAYLOAD_INVALID", "registered artifact payload is not a valid project contract", status=409) from exc
        if not isinstance(payload, dict):
            raise BackendError("ARTIFACT_PAYLOAD_INVALID", "registered artifact payload is not a valid project contract", status=409)
        return artifact, payload

    def validation_view(self, *, run_id: str, artifact_id: str, visualization_id: str) -> ValidationReportVisualization:
        artifact, payload = self._read_verified_json(run_id=run_id, artifact_id=artifact_id)
        if artifact.artifact_kind != "ValidationReport":
            raise BackendError("WRONG_ARTIFACT_KIND", "artifact is not a ValidationReport", status=422)
        declared_hash = payload.pop("content_hash", None)
        try:
            report = ValidationReport.model_validate(payload)
            if declared_hash is not None and declared_hash != report.content_hash:
                raise ValueError("validation report hash mismatch")
            run = self.get_run(run_id)
            scope = VisualizationScope(
                visualization_id=visualization_id,
                visualization_version=VisualizationService.visualization_version,
                project_id=run.project_id,
                run_id=run_id,
                snapshot_id=report.bindings.source_snapshot_id,
                stage_id=artifact.stage_id,
                scope_id=artifact.artifact_id,
                scope_semantics="authoritative registered ValidationReport projection",
                provenance_refs=tuple(sorted(report.provenance_refs)),
            )
            binding = ValidationReportVisualizationBinding.from_report(report)
            return VisualizationService().build_validation_from_report(visualization_id=visualization_id, scope=scope, report=report, binding=binding)
        except (ValueError, VisualizationInputError) as exc:
            raise BackendError("VALIDATION_PROJECTION_REJECTED", "ValidationReport could not be projected through the trusted Step26 path", status=409) from exc

    def visualization_graph(self, *, run_id: str, artifact_id: str) -> VisualizationGraph:
        artifact, payload = self._read_verified_json(run_id=run_id, artifact_id=artifact_id)
        if artifact.artifact_kind != "VisualizationGraph":
            raise BackendError("WRONG_ARTIFACT_KIND", "artifact is not a VisualizationGraph", status=422)
        try:
            graph = VisualizationGraph.model_validate(payload)
        except ValueError as exc:
            raise BackendError("VISUALIZATION_PROJECTION_REJECTED", "visualization artifact does not validate as the Step26 contract", status=409) from exc
        if graph.scope.run_id != run_id:
            raise BackendError("VISUALIZATION_SCOPE_REJECTED", "visualization scope is not bound to this run", status=409)
        return graph

    def _submission(
        self,
        *,
        action: str,
        run_id: str,
        principal: Principal,
        idempotency_key: str,
    ) -> tuple[SubmissionResult, bool]:
        self._require_scope(principal, "runs:write")
        key = _safe_key(idempotency_key)
        run = self.get_run(run_id)
        scope = f"execution:{action}:{run_id}:{principal.subject}"
        fingerprint = idempotency_fingerprint({"action": action, "run_id": run_id})
        command = ExecutionCommand(
            command_id=stable_id("execution-command", {"scope": scope, "key": key, "fingerprint": fingerprint}),
            run_id=run_id,
            action=ExecutionAction(action),
            idempotency_scope=scope,
            idempotency_key=key,
            request_fingerprint=fingerprint,
            principal_subject=principal.subject,
            principal_source=principal.source,
        )
        unknown = SubmissionResult(run_id=run_id, command_id=command.command_id, status="DELIVERY_UNKNOWN", detail="command delivery outcome is unknown; Step28 must reconcile the stable command identity")
        reservation = IdempotencyRecord(
            scope=scope,
            key=key,
            request_fingerprint=fingerprint,
            state="RESERVED",
            response_status=503,
            response_body={"submission": unknown.model_dump(mode="json")},
            resource_id=command.command_id,
        )
        try:
            existing, reserved_by_caller = self.control_store.reserve_idempotency(reservation)
        except ArtifactConflictError as exc:
            raise BackendError("IDEMPOTENCY_KEY_REUSED", "Idempotency-Key is bound to a different semantic request", status=409) from exc
        if not reserved_by_caller:
            return SubmissionResult.model_validate(self._idempotent_response(existing)["submission"]), True
        try:
            if action == ExecutionAction.SUBMIT.value and self.control_store.get_execution_plan(run_id) is None:
                result = SubmissionResult(
                    run_id=run_id,
                    command_id=command.command_id,
                    status="BLOCKED",
                    detail="execution plan is not prepared; provide explicit run-specific conditional selection first",
                    accepted_by="execution-plan-service",
                )
            else:
                result = self.execution.submit_command(command=command, run=run)
            result = SubmissionResult.model_validate(result).model_copy(update={"command_id": command.command_id})
        except BackendError:
            raise
        except Exception:
            try:
                self.control_store.mark_idempotency_unknown(
                    reservation.model_copy(update={"state": "UNKNOWN", "response_body": {"submission": unknown.model_dump(mode="json")}})
                )
            except Exception:
                # The RESERVED row remains the durable replay fence if the
                # uncertainty update itself is unavailable.
                pass
            return unknown, False
        completed = reservation.model_copy(
            update={
                "state": "COMPLETED",
                "response_status": 503 if result.status in {"UNAVAILABLE", "DELIVERY_UNKNOWN"} else 409 if result.status in {"BLOCKED", "CONFLICT", "REVIEW_REQUIRED"} else 202,
                "response_body": {"submission": result.model_dump(mode="json")},
                "resource_id": command.command_id,
            }
        )
        try:
            self.control_store.complete_idempotency(completed)
        except Exception:
            try:
                self.control_store.mark_idempotency_unknown(
                    reservation.model_copy(update={"state": "UNKNOWN", "response_body": {"submission": unknown.model_dump(mode="json")}})
                )
            except Exception:
                pass
            return unknown, False
        return result, False

    def prepare_execution_plan(
        self,
        *,
        run_id: str,
        intent: ExecutionPlanIntent,
        principal: Principal,
        idempotency_key: str,
    ) -> tuple[ExecutionPlanPreparation, bool]:
        """Prepare a typed run plan before a durable submit command."""

        self._require_scope(principal, "runs:write")
        key = _safe_key(idempotency_key)
        run = self.get_run(run_id)
        scope = f"execution-plan:{run_id}:{principal.subject}"
        fingerprint = idempotency_fingerprint({"run_id": run_id, "intent": intent.model_dump(mode="json")})
        reservation = IdempotencyRecord(
            scope=scope,
            key=key,
            request_fingerprint=fingerprint,
            state="RESERVED",
            response_status=409,
            response_body={},
        )
        try:
            existing, reserved = self.control_store.reserve_idempotency(reservation)
        except ArtifactConflictError as exc:
            raise BackendError("IDEMPOTENCY_KEY_REUSED", "Idempotency-Key is bound to a different semantic request", status=409) from exc
        if not reserved:
            body = self._idempotent_response(existing).get("preparation")
            if not isinstance(body, dict):
                raise BackendError("PLAN_PREPARATION_REPLAY_INVALID", "plan preparation replay metadata is invalid", status=409)
            return ExecutionPlanPreparation.model_validate(body), True
        if self.execution_plan_service is None:
            result = ExecutionPlanPreparation(
                run_id=run_id,
                status=PlanPreparationStatus.BLOCKED,
                selection_fingerprint=intent.content_hash,
                unresolved_stage_ids=("PLAN_SERVICE",),
                detail="no trusted execution plan preparation service is configured",
            )
        else:
            result = self.execution_plan_service.prepare(run=run, intent=intent)
        completed = reservation.model_copy(
            update={
                "state": "COMPLETED",
                "response_status": 200 if result.status is PlanPreparationStatus.READY else 409,
                "response_body": {"preparation": result.model_dump(mode="json")},
                "resource_id": result.plan_id,
            }
        )
        self.control_store.complete_idempotency(completed)
        return result, False

    def submit(self, *, run_id: str, principal: Principal, idempotency_key: str) -> tuple[SubmissionResult, bool]:
        return self._submission(action="submit", run_id=run_id, principal=principal, idempotency_key=idempotency_key)

    def cancel(self, *, run_id: str, principal: Principal, idempotency_key: str) -> tuple[SubmissionResult, bool]:
        return self._submission(action="cancel", run_id=run_id, principal=principal, idempotency_key=idempotency_key)

    def resume(self, *, run_id: str, principal: Principal, idempotency_key: str) -> tuple[SubmissionResult, bool]:
        return self._submission(action="resume", run_id=run_id, principal=principal, idempotency_key=idempotency_key)


__all__ = [
    "BackendError",
    "BackendService",
    "ControlStoreReviewSubjectResolver",
    "ExecutionSubmissionPort",
    "PageResult",
    "Principal",
    "ReviewSubjectResolverPort",
    "UnavailableExecutionSubmission",
]
