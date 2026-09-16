"""Executable behavioral validator for the Step27 backend boundary.

This validator creates only a disposable local reference platform under the
repository and exercises the HTTP application plus its injected ports.  It
never reads the protected quality artifact workspace.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fastapi.testclient import TestClient

from dirty_data_to_olap.application.backend import BackendError, BackendService, Principal
from dirty_data_to_olap.domain.contracts.api import SubmissionResult
from dirty_data_to_olap.application.visualization import VisualizationService
from dirty_data_to_olap.composition import build_local_backend
from dirty_data_to_olap.domain.contracts.canonical import CanonicalEntityKind, CanonicalEntityType, CanonicalModelHypothesis, EntityResolutionRequirement, ReviewCheckpoint, ReviewCompatibilityContext, ReviewDecisionStatus, review_subject_key
from dirty_data_to_olap.domain.contracts.jobs import ExecutionPlan, ExecutionPlanIntent, StageSpec
from dirty_data_to_olap.domain.contracts.platform import ArtifactManifest
from dirty_data_to_olap.domain.contracts.source import AdapterReference, SelectionScope, SourceCatalog, SourceDescriptor, SourceType, stable_id, utc_now
from dirty_data_to_olap.domain.contracts.validation import (
    RecordDisposition,
    ValidationArtifactBindings,
    ValidationCheck,
    ValidationPolicy,
    ValidationScope,
    ValidationSeverity,
    ValidationStatus,
    GateStatus,
    ValidationReport,
    validation_policy_id,
)
from dirty_data_to_olap.domain.contracts.visualization import (
    VisualEdgeType,
    VisualEvidenceState,
    VisualNodeType,
    VisualReliabilityState,
    VisualState,
    VisualLabel,
    VisualReviewState,
    VisualReviewability,
    VisualizationGraphInput,
    VisualizationGraphInputEdge,
    VisualizationGraphInputNode,
    VisualizationKind,
    VisualizationRequest,
    VisualizationScope,
)
from dirty_data_to_olap.entrypoints.api import create_app
from dirty_data_to_olap.platform import LocalPlatform


AUTH = {"X-Local-Principal": "step27-validator"}


def _check(condition: bool, name: str) -> None:
    if not condition:
        raise AssertionError(name)


def _table_names(path: Path) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        connection.close()


def _publish(bundle, run_id: str, artifact_id: str, *, kind: str = "ReviewSubject", payload: bytes = b"{}", sensitivity_ref: str | None = None):
    platform, _backend = bundle
    ref = platform.artifact_store.publish(
        ArtifactManifest(
            artifact_id=artifact_id,
            run_id=run_id,
            stage_id="EVIDENCE_FUSION",
            attempt_id="attempt-1",
            artifact_kind=kind,
            media_type="application/json",
            producer="step27-validator",
            storage_key=f"runs/{run_id}/artifacts/{artifact_id}.json",
            sensitivity_ref=sensitivity_ref,
        ),
        payload,
    )
    platform.control_store.register_artifact(ref)
    return ref


def _context(ref, checkpoint: ReviewCheckpoint = ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS):
    return ReviewCompatibilityContext(
        review_checkpoint_id=checkpoint,
        subject_stage="EVIDENCE_FUSION",
        subject_artifact_id=ref.artifact_id,
        subject_content_hash=ref.content_hash,
        subject_schema_version=ref.schema_version,
        model_version="validator-model-v1",
        source_schema_fingerprints={"source": "validator-source-fingerprint"},
        policy_version="validator-policy-v1",
        domain_assertion_refs=("validator-assertion",),
        subject_semantic_id=f"semantic-{ref.artifact_id}",
        applicability_fingerprint=f"applicability-{ref.artifact_id}",
    )


def _planning_catalog(source_id: str) -> SourceCatalog:
    return SourceCatalog(
        source=SourceDescriptor(
            source_id=source_id,
            display_name=source_id,
            source_type=SourceType.SQLITE,
            file_locator=f"{source_id}.sqlite",
            selection_scope=SelectionScope(),
            schema_fingerprint=f"validator-schema-{source_id}",
            adapter_reference=AdapterReference(name="step27-validator-source", version="1", config_fingerprint="step27-validator-source-v1"),
        ),
        tables=(),
        columns=(),
        declared_constraints=(),
    )


def _planning_hypothesis(run_id: str, source_ids: tuple[str, ...]) -> CanonicalModelHypothesis:
    return CanonicalModelHypothesis(
        artifact_id=stable_id("chyp", {"run_id": run_id, "source_ids": source_ids}),
        run_id=run_id,
        execution_context_id="step27-validator-planning",
        model_version="canonical-v1",
        upstream_review_decision_refs=("step27-planning-review",),
        source_ids=source_ids,
        domain_assertion_refs=("step27-planning-domain",),
        entity_types=(CanonicalEntityType(
            canonical_entity_type_id="cet_step27_customer",
            semantic_id="step27-customer",
            business_name="Step27 Customer",
            kind=CanonicalEntityKind.IDENTITY,
            entity_resolution_family="customer",
            identity_strategy="step27-trusted-planning",
            review_state="ACCEPTED_BY_REVIEW",
            provenance_refs=("step27-validator",),
        ),),
        entity_resolution_requirements={"customer": EntityResolutionRequirement.ER_NOT_REQUIRED},
        evidence_refs=("step27-planning-evidence",),
        provenance_refs=("step27-validator",),
        created_at=utc_now(),
    )


def _report(run_id: str, report_id: str, status: ValidationStatus, raw_value: str) -> ValidationReport:
    policy_payload = {"required": ["check"], "name": report_id}
    policy = ValidationPolicy(
        policy_id=validation_policy_id(policy_payload),
        policy_version="validator-policy-v1",
        required_check_ids=("check",),
        allowed_terminal_dispositions=tuple(RecordDisposition),
        orphan_policy={"required_fk": "FAIL"},
        monetary_reason="not applicable for this bounded validator",
        provenance_refs=("validator:policy",),
    )
    bindings = ValidationArtifactBindings(
        source_snapshot_id="snapshot-validator",
        source_snapshot_hash="source-snapshot-hash",
        source_truth_id="truth-validator",
        source_truth_content_hash="truth-content-hash",
        canonical_model_id="canonical-validator",
        canonical_model_content_hash="canonical-content-hash",
        record_accounting_id="accounting-validator",
        record_accounting_content_hash="accounting-content-hash",
        analytical_plan_id="plan-validator",
        analytical_plan_content_hash="plan-content-hash",
        analytical_spec_package_hash="spec-package-hash",
        analytical_dataset_id="dataset-validator",
        analytical_dataset_content_hash="dataset-content-hash",
        analytical_input_binding_id="binding-validator",
        analytical_input_binding_content_hash="binding-content-hash",
        analytical_input_source_snapshot_fingerprints={"source": "snapshot-fingerprint"},
        compiled_plan_id="compiled-validator",
        compiled_plan_content_hash="compiled-content-hash",
        materialization_artifact_id="materialization-validator",
        materialization_artifact_content_hash="materialization-content-hash",
        target_relative_path="targets/validator.duckdb",
        target_config_fingerprint="target-config-fingerprint",
        target_file_sha256="a" * 64,
        semantic_model_id="semantic-model-validator",
        semantic_model_content_hash="semantic-model-content-hash",
        semantic_validation_id="semantic-validation-validator",
        semantic_validation_content_hash="semantic-validation-content-hash",
        validation_policy_id=policy.policy_id,
        validation_policy_version=policy.policy_version,
    )
    check_status = ValidationStatus.FAIL if status is ValidationStatus.FAIL else ValidationStatus.NOT_EVALUATED
    return ValidationReport(
        report_id=report_id,
        run_id=run_id,
        bindings=bindings,
        policy=policy,
        checks=(ValidationCheck(
            check_id="check",
            name="bounded check",
            status=check_status,
            severity=ValidationSeverity.G6_BLOCKING,
            scope=ValidationScope.SOURCE_ACCOUNTING,
            required=True,
            details="validator check",
            expected=raw_value,
            observed=raw_value,
            evidence_refs=("validator-evidence",),
        ),),
        overall_status=ValidationStatus.FAIL if check_status is ValidationStatus.FAIL else ValidationStatus.REVIEW_REQUIRED,
        g6_status=GateStatus.FAIL if check_status is ValidationStatus.FAIL else GateStatus.PENDING,
        g6_eligible=False,
        generated_at="2026-01-01T00:00:00+00:00",
        provenance_refs=("validator:report",),
    )


def _graph(run_id: str):
    scope = VisualizationScope(
        visualization_id="validator-graph",
        visualization_version=VisualizationService.visualization_version,
        project_id="validator-project",
        run_id=run_id,
        snapshot_id="snapshot-validator",
        stage_id="EVIDENCE_FUSION",
        scope_id="graph-scope",
        scope_semantics="bounded validator graph",
        provenance_refs=("validator:graph",),
    )
    reviewability = VisualReviewability(review_state=VisualReviewState.NOT_REVIEWED, consequence="informational only")
    nodes = tuple(
        VisualizationGraphInputNode(
            domain_ref=ref,
            node_type=VisualNodeType.SOURCE,
            label=VisualLabel(value=label, accessible_text=label),
            state=VisualState.OBSERVED,
            evidence_state=VisualEvidenceState.OBSERVED,
            reliability=VisualReliabilityState.UNKNOWN,
            provenance_refs=("validator:graph",),
            reviewability=reviewability,
        )
        for ref, label in (("node-a", "Source A"), ("node-b", "Source B"))
    )
    edge = VisualizationGraphInputEdge(
        domain_ref="edge-a-b",
        source_ref="node-a",
        target_ref="node-b",
        edge_type=VisualEdgeType.INFERRED_RELATIONSHIP,
        state=VisualState.OBSERVED,
        evidence_state=VisualEvidenceState.OBSERVED,
        reliability=VisualReliabilityState.UNKNOWN,
        provenance_refs=("validator:graph",),
        reviewability=reviewability,
        inferred=True,
    )
    return VisualizationService().build_graph(
        VisualizationRequest(visualization_id=scope.visualization_id, kind=VisualizationKind.SOURCE_SCHEMA, max_nodes=1, max_edges=1),
        VisualizationGraphInput(scope=scope, nodes=nodes, edges=(edge,)),
    )


class _RepairRecordingExecutor:
    def __init__(self) -> None:
        self.commands = []

    def submit_command(self, *, command, run):
        self.commands.append(command)
        return SubmissionResult(run_id=run.run_id, command_id=command.command_id, status="ACCEPTED", detail="validator command accepted", submission_id="validator-submission")


def _repair_checks() -> int:
    """Exercise the post-Step27 trust-boundary and crash-recovery controls."""

    checks = 0
    with TemporaryDirectory(dir=ROOT) as temp:
        project_root = Path(temp)
        platform, backend = build_local_backend(project_root)
        client = TestClient(create_app(backend), raise_server_exceptions=False)
        try:
            create = client.post("/api/v1/runs", headers={**AUTH, "Idempotency-Key": "repair-run"}, json={"project_id": "repair-project", "configuration_fingerprint": platform.config.configuration_fingerprint})
            _check(create.status_code == 201, "repair run")
            run_id = create.json()["run_id"]
            ref = _publish((platform, backend), run_id, "repair-authoritative")
            authoritative = _context(ref)
            platform.control_store.register_review_subject_context(run_id=run_id, context=authoritative)
            review_path = f"/api/v1/runs/{run_id}/reviews/{ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value}"
            base = {"subject_artifact_id": ref.artifact_id, "subject_content_hash": ref.content_hash, "decision": "ACCEPTED", "rationale": "authoritative validator review", "expected_revision": 0}
            accepted = client.post(review_path, headers={**AUTH, "Idempotency-Key": "repair-authoritative-key"}, json=base)
            checks += 1; _check(accepted.status_code == 200, "server-derived authoritative review context")
            returned = accepted.json()["decision"]
            expected = authoritative.model_dump(mode="json")
            checks += 1; _check(all(returned[field] == expected[field] for field in ("subject_stage", "subject_schema_version", "model_version", "source_schema_fingerprints", "policy_version", "domain_assertion_refs", "subject_semantic_id", "applicability_fingerprint")), "authoritative review decision binding")
            mutations = {
                "subject_stage": "FORGED_STAGE",
                "subject_schema_version": "FORGED_SCHEMA",
                "model_version": "FORGED_MODEL",
                "source_schema_fingerprints": {"source": "FORGED_SOURCE"},
                "policy_version": "FORGED_POLICY",
                "domain_assertion_refs": ("FORGED_ASSERTION",),
                "subject_semantic_id": "FORGED_SEMANTIC",
                "applicability_fingerprint": "FORGED_APPLICABILITY",
            }
            for field, value in mutations.items():
                forged = authoritative.model_copy(update={field: value})
                response = client.post(review_path, headers={**AUTH, "Idempotency-Key": "repair-forged-" + field}, json={**base, "context": forged.model_dump(mode="json")})
                checks += 1; _check(response.status_code == 409 and response.json()["error"]["code"] == "REVIEW_CONTEXT_MISMATCH", "tampered " + field)
            forged_parallel = authoritative.model_copy(update={"subject_semantic_id": "parallel", "applicability_fingerprint": "parallel"})
            parallel = client.post(review_path, headers={**AUTH, "Idempotency-Key": "repair-parallel"}, json={**base, "context": forged_parallel.model_dump(mode="json")})
            checks += 1; _check(parallel.status_code == 409 and len(client.get(f"/api/v1/runs/{run_id}/reviews", headers=AUTH).json()["items"]) == 1, "forged context cannot create parallel review stream")
            skipped = client.post(review_path, headers={**AUTH, "Idempotency-Key": "repair-skip"}, json={**base, "decision": "SKIPPED"})
            checks += 1; _check(skipped.status_code == 422 and "SKIPPED" not in client.get("/api/v1/openapi.json").json()["components"]["schemas"]["ReviewActionDecision"].get("enum", []), "skip is explicitly unsupported")

            reopen_ref = _publish((platform, backend), run_id, "repair-reopen")
            reopen_context = _context(reopen_ref)
            platform.control_store.register_review_subject_context(run_id=run_id, context=reopen_context)
            reopen_body = {"subject_artifact_id": reopen_ref.artifact_id, "subject_content_hash": reopen_ref.content_hash, "decision": "ACCEPTED", "rationale": "reopen replay", "expected_revision": 0}
            reopen_path = f"/api/v1/runs/{run_id}/reviews/{ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value}"
            first = client.post(reopen_path, headers={**AUTH, "Idempotency-Key": "repair-reopen-key"}, json=reopen_body)
            checks += 1; _check(first.status_code == 200, "reopen review seed")
            platform.close()
            reopened = LocalPlatform.from_project_root(project_root)
            try:
                backend2 = BackendService(control_store=reopened.control_store, artifact_store=reopened.artifact_store, configuration_fingerprint=reopened.config.configuration_fingerprint)
                replay = TestClient(create_app(backend2), raise_server_exceptions=False).post(reopen_path, headers={**AUTH, "Idempotency-Key": "repair-reopen-key"}, json=reopen_body)
                checks += 1; _check(replay.status_code == 200 and len(backend2.control_store.list_review_history(run_id=run_id, subject_key=review_subject_key(reopen_context))) == 1, "review replay after store reopen")
            finally:
                reopened.close()
        finally:
            try:
                platform.close()
            except Exception:
                pass

    with TemporaryDirectory(dir=ROOT) as temp:
        project_root = Path(temp)
        platform, backend1 = build_local_backend(project_root)
        run_id = backend1.create_run(project_id="repair-project", configuration_fingerprint=platform.config.configuration_fingerprint, git_content_commit=None, metadata={}, principal=Principal("multi", frozenset({"runs:write"}), "LOCAL_TEST_AUTH"), idempotency_key="multi-run")[0].run_id
        ref = _publish((platform, backend1), run_id, "repair-multi")
        context = _context(ref)
        platform.control_store.register_review_subject_context(run_id=run_id, context=context)
        from dirty_data_to_olap.adapters.platform import SQLiteControlStore
        store2 = SQLiteControlStore(platform.control_store.path, project_root=project_root)
        backend2 = BackendService(control_store=store2, artifact_store=platform.artifact_store, configuration_fingerprint=platform.config.configuration_fingerprint)
        principal = Principal("multi", frozenset({"reviews:write"}), "LOCAL_TEST_AUTH")
        def submit(backend):
            return backend.review(run_id=run_id, checkpoint=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS, subject_artifact_id=ref.artifact_id, subject_content_hash=ref.content_hash, decision=ReviewDecisionStatus.ACCEPTED, rationale="same request", expected_revision=0, principal=principal, idempotency_key="multi-key")
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = tuple(pool.map(submit, (backend1, backend2)))
            checks += 1; _check(len({result[0].decision.review_decision_id for result in results}) == 1, "multi-instance same-key review replay")
            checks += 1; _check(len(backend1.control_store.list_review_history(run_id=run_id)) == 1, "multi-instance one review revision")
            try:
                submit_changed = backend2.review(run_id=run_id, checkpoint=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS, subject_artifact_id=ref.artifact_id, subject_content_hash=ref.content_hash, decision=ReviewDecisionStatus.ACCEPTED, rationale="changed request", expected_revision=0, principal=principal, idempotency_key="multi-key")
            except BackendError as exc:
                submit_changed = exc.code
            checks += 1; _check(submit_changed == "IDEMPOTENCY_KEY_REUSED", "changed same-key review conflict")
        finally:
            store2.close(); platform.close()

    with TemporaryDirectory(dir=ROOT) as temp:
        project_root = Path(temp)
        platform, backend = build_local_backend(project_root)
        executor = _RepairRecordingExecutor()
        backend.execution = executor
        run_id = backend.create_run(project_id="repair-project", configuration_fingerprint=platform.config.configuration_fingerprint, git_content_commit=None, metadata={}, principal=Principal("execution", frozenset({"runs:write"}), "LOCAL_TEST_AUTH"), idempotency_key="execution-run")[0].run_id
        platform.control_store.register_execution_plan(ExecutionPlan(plan_id="step27-validator-execution-plan", run_id=run_id, stages=(StageSpec(stage_id="WORK", handler_key="work", final_validation=True),)))
        original_complete = platform.control_store.complete_idempotency
        failed = False
        def fail_completion(record):
            nonlocal failed
            if not failed:
                failed = True
                raise RuntimeError("injected completion failure")
            return original_complete(record)
        platform.control_store.complete_idempotency = fail_completion
        principal = Principal("execution", frozenset({"runs:write"}), "LOCAL_TEST_AUTH")
        try:
            first, first_replayed = backend.submit(run_id=run_id, principal=principal, idempotency_key="execution-key")
            second, second_replayed = backend.submit(run_id=run_id, principal=principal, idempotency_key="execution-key")
            checks += 1; _check(first.status == second.status == "DELIVERY_UNKNOWN", "execution completion uncertainty is explicit")
            checks += 1; _check(first.command_id == second.command_id and first_replayed is False and second_replayed is True, "stable execution command identity on retry")
            checks += 1; _check(len(executor.commands) == 1, "no second execution command after injected failure")
        finally:
            platform.close()

    with TemporaryDirectory(dir=ROOT) as temp:
        project_root = Path(temp)
        platform, backend = build_local_backend(project_root)
        run_id = backend.create_run(project_id="repair-project", configuration_fingerprint=platform.config.configuration_fingerprint, git_content_commit=None, metadata={}, principal=Principal("auth", frozenset({"runs:write"}), "LOCAL_TEST_AUTH"), idempotency_key="auth-run")[0].run_id
        try:
            anonymous = TestClient(create_app(backend, auth_mode="trusted_proxy", principal_resolver=lambda _request: None), raise_server_exceptions=False)
            checks += 1; _check(anonymous.get(f"/api/v1/runs/{run_id}").status_code == 401, "anonymous trusted-proxy read rejected")
            insufficient = TestClient(create_app(backend, auth_mode="trusted_proxy", principal_resolver=lambda _request: Principal("reader", frozenset(), "TRUSTED_PROXY", frozenset({"repair-project"}))), raise_server_exceptions=False)
            checks += 1; _check(insufficient.get(f"/api/v1/runs/{run_id}").status_code == 403, "insufficient read scope rejected")
            authorized = TestClient(create_app(backend, auth_mode="trusted_proxy", principal_resolver=lambda _request: Principal("reader", frozenset({"runs:read"}), "TRUSTED_PROXY", frozenset({"repair-project"}))), raise_server_exceptions=False)
            authorized_response = authorized.get(f"/api/v1/runs/{run_id}")
            checks += 1; _check(authorized_response.status_code == 200 and "TRUSTED_PROXY" not in authorized_response.text and authorized.get("/api/v1/health").status_code == 200, "authorized read and public health")
            checks += 1; _check(authorized.post("/api/v1/runs", headers={"Idempotency-Key": "auth-write"}, json={"project_id": "p", "configuration_fingerprint": platform.config.configuration_fingerprint}).status_code == 403, "read and mutation scopes remain separate")
        finally:
            platform.close()

    with TemporaryDirectory(dir=ROOT) as temp:
        project_root = Path(temp)
        platform, backend = build_local_backend(project_root)
        run_id = backend.create_run(project_id="repair-project", configuration_fingerprint=platform.config.configuration_fingerprint, git_content_commit=None, metadata={}, principal=Principal("migration", frozenset({"runs:write"}), "LOCAL_TEST_AUTH"), idempotency_key="migration-run")[0].run_id
        path = platform.control_store.path
        platform.close()
        connection = sqlite3.connect(path)
        try:
            connection.execute("UPDATE schema_meta SET schema_version = 3")
            connection.execute("DROP TABLE review_subject_contexts")
            connection.execute("ALTER TABLE api_idempotency RENAME TO api_idempotency_old")
            connection.execute("CREATE TABLE api_idempotency (scope TEXT NOT NULL, idem_key TEXT NOT NULL, request_fingerprint TEXT NOT NULL, response_status INTEGER NOT NULL, response_body TEXT NOT NULL, resource_id TEXT, created_at TEXT NOT NULL, PRIMARY KEY(scope, idem_key))")
            connection.execute("INSERT INTO api_idempotency SELECT scope, idem_key, request_fingerprint, response_status, response_body, resource_id, created_at FROM api_idempotency_old")
            connection.execute("DROP TABLE api_idempotency_old")
        finally:
            connection.close()
        from dirty_data_to_olap.adapters.platform import SQLiteControlStore
        migrated = SQLiteControlStore(path, project_root=project_root)
        try:
            checks += 1; _check(migrated.schema_version == 6 and migrated.get_run(run_id) is not None, "v3 to v6 migration retains run")
            tables = _table_names(path)
            checks += 1; _check({"api_idempotency", "review_current", "review_history", "review_subject_contexts", "execution_plans", "jobs"}.issubset(tables), "v5 control capabilities available")
        finally:
            migrated.close()
        connection = sqlite3.connect(path)
        try:
            connection.execute("DROP TABLE review_subject_contexts")
        finally:
            connection.close()
        reopened = SQLiteControlStore(path, project_root=project_root)
        try:
            tables = _table_names(path)
            checks += 1; _check(reopened.schema_version == 6 and reopened.get_run(run_id) is not None and {"review_subject_contexts", "execution_plans", "jobs"}.issubset(tables), "v6 reopen repairs partial capability")
        finally:
            reopened.close()
    return checks


def main() -> int:
    checks = 0
    with TemporaryDirectory(dir=ROOT) as temp:
        project_root = Path(temp)
        bundle = build_local_backend(project_root)
        platform, backend = bundle
        app = create_app(backend)
        client = TestClient(app, raise_server_exceptions=False)
        try:
            # 1-8: app, OpenAPI and run control.
            checks += 1; _check(client.get("/api/v1/health").status_code == 200, "app starts")
            checks += 1; first_openapi = client.get("/api/v1/openapi.json"); _check(first_openapi.status_code == 200, "openapi generates")
            checks += 1; second_openapi = client.get("/api/v1/openapi.json"); _check(first_openapi.json() == second_openapi.json(), "openapi deterministic")
            checks += 1; _check("/api/v1/runs" in first_openapi.json()["paths"], "versioned run path")
            create = client.post("/api/v1/runs", headers={**AUTH, "Idempotency-Key": "run-1"}, json={"project_id": "validator-project", "configuration_fingerprint": platform.config.configuration_fingerprint})
            checks += 1; _check(create.status_code == 201, "create run")
            run = create.json(); run_id = run["run_id"]
            replay = client.post("/api/v1/runs", headers={**AUTH, "Idempotency-Key": "run-1"}, json={"project_id": "validator-project", "configuration_fingerprint": platform.config.configuration_fingerprint})
            checks += 1; _check(replay.status_code == 201 and replay.json() == run, "idempotent run")
            changed = client.post("/api/v1/runs", headers={**AUTH, "Idempotency-Key": "run-1"}, json={"project_id": "other", "configuration_fingerprint": platform.config.configuration_fingerprint})
            checks += 1; _check(changed.status_code == 409, "changed idempotency conflict")
            checks += 1; _check(client.get(f"/api/v1/runs/{run_id}", headers=AUTH).json()["run_id"] == run_id and client.get("/api/v1/runs/unknown", headers=AUTH).status_code == 404, "get and unknown run")
            for source_id in ("step27-crm", "step27-erp"):
                _publish(bundle, run_id, stable_id("step27-catalog", {"run_id": run_id, "source_id": source_id}), kind="SourceCatalog", payload=_planning_catalog(source_id).model_dump_json().encode("utf-8"))
            hypothesis = _planning_hypothesis(run_id, ("step27-crm", "step27-erp"))
            _publish(bundle, run_id, hypothesis.artifact_id, kind="CanonicalModelHypothesis", payload=hypothesis.model_dump_json().encode("utf-8"))

            # 9-15: attempts and artifact boundary.
            from dirty_data_to_olap.domain.contracts.platform import StageAttemptRecord, StageStatus
            for number, status in enumerate(StageStatus, start=1):
                platform.control_store.create_stage_attempt(StageAttemptRecord(attempt_id=f"validator-attempt-{number}", run_id=run_id, stage_id=f"STAGE-{number}", attempt_number=1, status=status, policy_config_fingerprint="validator-policy"))
            attempts = client.get(f"/api/v1/runs/{run_id}/attempts", headers=AUTH, params={"page_size": 100})
            checks += 1; _check({x["status"] for x in attempts.json()["items"]} == {x.value for x in StageStatus}, "stage status vocabulary")
            artifact = _publish(bundle, run_id, "validator-artifact")
            checks += 1; _check(artifact.artifact_id in {item["artifact_id"] for item in client.get(f"/api/v1/runs/{run_id}/artifacts", headers=AUTH, params={"page_size": 100}).json()["items"]}, "scoped artifact metadata")
            checks += 1; _check(client.get(f"/api/v1/runs/{run_id}/artifacts", headers=AUTH, params={"artifact_kind": "ReviewSubject", "page_size": 1}).status_code == 200, "artifact filtering")
            checks += 1; _check(client.get(f"/api/v1/artifacts/unknown-artifact", headers=AUTH, params={"run_id": run_id}).status_code == 404, "unregistered artifact rejection")
            checks += 1; _check(client.get(f"/api/v1/artifacts/{artifact.artifact_id}", headers=AUTH, params={"run_id": "foreign-run"}).status_code == 404, "cross-run artifact rejection")
            checks += 1; _check(client.post(f"/api/v1/runs/{run_id}/artifacts/register", headers=AUTH, json={"artifact_id": "../escape"}).status_code == 422, "path traversal rejected")
            restricted = _publish(bundle, run_id, "validator-restricted", sensitivity_ref="privacy:restricted")
            checks += 1; _check(client.get(f"/api/v1/runs/{run_id}/artifacts/{restricted.artifact_id}/content", headers=AUTH).status_code == 403, "restricted payload denied")
            corrupt = _publish(bundle, run_id, "validator-corrupt", payload=b"corrupt-original")
            platform.artifact_store._blob_path(corrupt.content_hash).write_bytes(b"changed")
            checks += 1; _check(client.get(f"/api/v1/artifacts/{corrupt.artifact_id}", headers=AUTH, params={"run_id": run_id}).status_code == 409, "integrity mismatch fails closed")

            # 16-21: exact review binding, stale CAS, race and non-authorizing states.
            review_ref = _publish(bundle, run_id, "validator-review")
            context = _context(review_ref)
            platform.control_store.register_review_subject_context(run_id=run_id, context=context)
            review_path = f"/api/v1/runs/{run_id}/reviews/{ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value}"
            review_body = {"context": context.model_dump(mode="json"), "decision": "ACCEPTED", "rationale": "exact review", "expected_revision": 0}
            accepted = client.post(review_path, headers={**AUTH, "Idempotency-Key": "review-1"}, json=review_body)
            checks += 1; _check(accepted.status_code == 200 and accepted.json()["decision"]["decision"] == "ACCEPTED", "exact accepted review")
            stale = client.post(review_path, headers={**AUTH, "Idempotency-Key": "review-2"}, json=review_body)
            checks += 1; _check(stale.status_code == 409 and stale.json()["error"]["code"] == "REVIEW_REVISION_CONFLICT", "stale review conflict")
            changed_review_key = client.post(review_path, headers={**AUTH, "Idempotency-Key": "review-1"}, json={**review_body, "rationale": "changed request"})
            checks += 1; _check(changed_review_key.status_code == 409 and changed_review_key.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED", "review idempotency conflict")
            race_ref = _publish(bundle, run_id, "validator-race")
            race_context = _context(race_ref)
            platform.control_store.register_review_subject_context(run_id=run_id, context=race_context)
            def race(key: str):
                try:
                    return backend.review(run_id=run_id, checkpoint=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS, context=race_context, decision=ReviewDecisionStatus.ACCEPTED, rationale="race", expected_revision=0, principal=Principal("step27-validator", frozenset({"reviews:write"}), "LOCAL_TEST_AUTH"), idempotency_key=key)
                except BackendError as exc:
                    return exc.code
            with ThreadPoolExecutor(max_workers=2) as pool:
                race_results = tuple(pool.map(race, ("race-a", "race-b")))
            checks += 1; _check(sum(isinstance(x, tuple) for x in race_results) == 1 and sum(x == "REVIEW_REVISION_CONFLICT" for x in race_results) == 1, "review race CAS")
            wrong_hash = context.model_copy(update={"subject_content_hash": "b" * 64})
            checks += 1; _check(client.post(review_path, headers={**AUTH, "Idempotency-Key": "wrong-hash"}, json={**review_body, "context": wrong_hash.model_dump(mode="json")}).status_code == 409, "wrong subject hash")
            checks += 1; _check(client.post(f"/api/v1/runs/{run_id}/reviews/{ReviewCheckpoint.REVIEW_CANONICAL_IDENTITY.value}", headers={**AUTH, "Idempotency-Key": "wrong-checkpoint"}, json=review_body).status_code == 422, "wrong checkpoint")
            checks += 1; _check(client.post(review_path, headers={**AUTH, "Idempotency-Key": "spoof"}, json={**review_body, "actor": "attacker"}).status_code == 422 and client.post(review_path, headers={"Idempotency-Key": "no-auth"}, json=review_body).status_code == 401, "actor and auth boundary")
            invalidated = client.post(f"{review_path}/invalidate", headers={**AUTH, "Idempotency-Key": "invalidate-1"}, json={"context": context.model_dump(mode="json"), "reason": "subject changed", "expected_revision": 1})
            checks += 1; _check(invalidated.status_code == 200 and invalidated.json()["decision"]["decision"] == "INVALIDATED", "invalidated review non-authorizing")
            rejected_ref = _publish(bundle, run_id, "validator-rejected")
            rejected_context = _context(rejected_ref)
            platform.control_store.register_review_subject_context(run_id=run_id, context=rejected_context)
            rejected = client.post(review_path, headers={**AUTH, "Idempotency-Key": "rejected-1"}, json={"context": rejected_context.model_dump(mode="json"), "decision": "REJECTED", "rationale": "rejected evidence", "expected_revision": 0})
            deferred_ref = _publish(bundle, run_id, "validator-deferred")
            deferred_context = _context(deferred_ref)
            platform.control_store.register_review_subject_context(run_id=run_id, context=deferred_context)
            deferred = client.post(review_path, headers={**AUTH, "Idempotency-Key": "deferred-1"}, json={"context": deferred_context.model_dump(mode="json"), "decision": "DEFERRED", "rationale": "deferred evidence", "expected_revision": 0})
            checks += 1; _check(rejected.status_code == 200 and deferred.status_code == 200 and rejected.json()["decision"]["decision"] == "REJECTED" and deferred.json()["decision"]["decision"] == "DEFERRED", "rejected and deferred reviews non-authorizing")

            # 22-26: trusted Step22/26 read models and disclosure.
            fail_report = _report(run_id, "validator-fail-report", ValidationStatus.FAIL, "canary@example.invalid")
            fail_artifact = _publish(bundle, run_id, "validator-validation-fail", kind="ValidationReport", payload=json.dumps({**fail_report.model_dump(mode="json"), "content_hash": fail_report.content_hash}).encode())
            fail_view = client.get(f"/api/v1/runs/{run_id}/validation/{fail_artifact.artifact_id}", headers=AUTH)
            checks += 1; _check(fail_view.status_code == 200 and fail_view.json()["overall_status"] == "FAIL" and fail_view.json()["g6_status"] == "FAIL", "validation FAIL preserved")
            pending_report = _report(run_id, "validator-pending-report", ValidationStatus.NOT_EVALUATED, "canary@example.invalid")
            pending_artifact = _publish(bundle, run_id, "validator-validation-pending", kind="ValidationReport", payload=json.dumps({**pending_report.model_dump(mode="json"), "content_hash": pending_report.content_hash}).encode())
            pending_view = client.get(f"/api/v1/runs/{run_id}/validation/{pending_artifact.artifact_id}", headers=AUTH)
            checks += 1; _check(pending_view.status_code == 200 and pending_view.json()["overall_status"] == "REVIEW_REQUIRED" and pending_view.json()["g6_status"] == "PENDING", "NOT_EVALUATED preserved")
            checks += 1; _check("canary@example.invalid" not in fail_view.text and "canary@example.invalid" not in pending_view.text, "validation privacy projection")
            graph = _graph(run_id)
            graph_artifact = _publish(bundle, run_id, "validator-visualization", kind="VisualizationGraph", payload=json.dumps(graph.model_dump(mode="json")).encode())
            graph_view = client.get(f"/api/v1/runs/{run_id}/visualizations/{graph_artifact.artifact_id}", headers=AUTH)
            checks += 1; _check(graph_view.status_code == 200 and graph_view.json()["disclosure"]["hidden_node_count"] == graph.disclosure.hidden_node_count and graph_view.json()["disclosure"]["truncated"] is True, "visualization disclosure preserved")
            checks += 1; _check(not any(isinstance(value, str) and value.startswith("<class") for value in graph_view.json().values()), "no provider-native response")

            # 27-36: request safety, submission boundary and page limits.
            checks += 1; _check(client.post("/api/v1/runs", headers={**AUTH, "Idempotency-Key": "malformed"}, json={"project_id": "validator-project"}).status_code == 422, "stable malformed error")
            checks += 1; _check(set(client.post("/api/v1/runs", headers={**AUTH, "Idempotency-Key": "malformed-2"}, json={"project_id": "validator-project"}).json()) == {"error"}, "error envelope")
            checks += 1; _check(client.get("/api/v1/runs", headers=AUTH, params={"page_size": 101}).status_code == 422, "hard page limit")
            checks += 1; _check(client.get("/api/v1/runs", headers=AUTH, params={"status": "PARTIAL"}).status_code == 400, "undefined run state rejected")
            prepared = client.post(f"/api/v1/runs/{run_id}/execution/prepare", headers={**AUTH, "Idempotency-Key": "prepare-1"}, json={"intent": ExecutionPlanIntent(cross_source_mapping_requested=False, entity_resolution_requested=False).model_dump(mode="json")})
            prepared_plan = platform.control_store.get_execution_plan(run_id)
            checks += 2; _check(prepared.status_code == 200 and prepared.json()["status"] == "READY" and prepared.json()["planning_phase"] == "BOOTSTRAP", "typed execution plan preparation")
            _check(prepared_plan is not None and prepared_plan.selection is None and tuple(stage.stage_id for stage in prepared_plan.stages) == ("SOURCE_DISCOVERY",), "server-owned bootstrap plan selection")
            submit = client.post(f"/api/v1/runs/{run_id}/execution", headers={**AUTH, "Idempotency-Key": "submit-1"})
            checks += 1; _check(submit.status_code == 202 and submit.json()["status"] == "ACCEPTED" and submit.json()["submission_id"], "durable command submission")
            checks += 1; _check(client.post(f"/api/v1/runs/{run_id}/execution", headers={**AUTH, "Idempotency-Key": "submit-1"}).json() == submit.json(), "submission idempotency")
            checks += 1; _check(client.post(f"/api/v1/runs/{run_id}/cancel", headers={**AUTH, "Idempotency-Key": "cancel-1"}).status_code == 202, "cancel command submission")
            checks += 1; _check(client.post(f"/api/v1/runs/{run_id}/resume", headers={**AUTH, "Idempotency-Key": "resume-1"}).status_code == 202, "resume command submission")
            checks += _repair_checks()
            backend.get_run = lambda _run_id: (_ for _ in ()).throw(RuntimeError("C:\\secret\\stack.sqlite"))
            sanitized = client.get(f"/api/v1/runs/{run_id}", headers=AUTH)
            checks += 1; _check(sanitized.status_code == 500 and "stack.sqlite" not in sanitized.text and "traceback" not in sanitized.text.lower(), "sanitized internal error")
            print(f"STEP27_VALIDATOR=PASS scenarios={checks}")
            return 0
        finally:
            platform.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"STEP27_VALIDATOR=FAIL reason={type(exc).__name__}")
        raise
