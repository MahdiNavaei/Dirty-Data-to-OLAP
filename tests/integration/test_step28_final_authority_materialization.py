from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from dirty_data_to_olap.adapters.platform import LocalArtifactStore, SQLiteControlStore
from dirty_data_to_olap.application.backend import BackendService
from dirty_data_to_olap.application.compiler import AnalyticalCompilerService, CompilationArtifactPublisher
from dirty_data_to_olap.application.jobs import DurableExecutionSubmission, JobWorker, StageHandlerRegistry
from dirty_data_to_olap.application.planning_authority import ServerOwnedExecutionPlanSelectionResolver
from dirty_data_to_olap.application.review_policy import ReviewPolicyService
from dirty_data_to_olap.application.review_subjects import ReviewCheckpointSubjectResolver
from dirty_data_to_olap.domain.contracts.analytical import TargetConfig
from dirty_data_to_olap.domain.contracts.api import ExecutionAction, ExecutionCommand
from dirty_data_to_olap.domain.contracts.canonical import CanonicalEntityKind, CanonicalEntityType, CanonicalModelHypothesis, EntityResolutionRequirement, ReviewCheckpoint
from dirty_data_to_olap.domain.contracts.jobs import ExecutionPlan, ExecutionPlanIntent, ExecutionPlanSelection, JobStatus, StageExecutionResult, StageResultStatus, StageSelectionDecision, StageSpec
from dirty_data_to_olap.domain.contracts.platform import ArtifactManifest, RunRecord
from dirty_data_to_olap.domain.contracts.source import AdapterReference, SelectionScope, SourceCatalog, SourceDescriptor, SourceType, stable_digest, stable_id
from dirty_data_to_olap.entrypoints.api import create_app


ROOT = Path(__file__).resolve().parents[2]
AUTH = {"X-Local-Principal": "step28-authority-test"}


def _stores(tmp_path: Path, name: str):
    control = SQLiteControlStore(tmp_path / f"{name}.sqlite", project_root=tmp_path)
    artifacts = LocalArtifactStore(tmp_path / f"{name}-artifacts", project_root=tmp_path)
    run = control.create_run(RunRecord(run_id=f"run-{name}", project_id="step28", configuration_fingerprint="cfg"))
    return control, artifacts, run


def _command(run_id: str, key: str, action: ExecutionAction = ExecutionAction.SUBMIT) -> ExecutionCommand:
    return ExecutionCommand(
        command_id=stable_id("step28-authority-command", {"run": run_id, "key": key, "action": action.value}),
        run_id=run_id,
        action=action,
        idempotency_scope=f"step28-authority:{run_id}:{action.value}",
        idempotency_key=key,
        request_fingerprint=stable_digest({"run": run_id, "key": key, "action": action.value}),
        principal_subject="step28-authority-test",
        principal_source="STEP28_FINAL_AUTHORITY_TEST",
    )


def _catalog(source_id: str) -> SourceCatalog:
    return SourceCatalog(
        source=SourceDescriptor(
            source_id=source_id,
            display_name=source_id,
            source_type=SourceType.SQLITE,
            file_locator=f"{source_id}.sqlite",
            selection_scope=SelectionScope(),
            schema_fingerprint=f"schema-{source_id}",
            adapter_reference=AdapterReference(name="step28-authority-source", version="1", config_fingerprint="step28-source-v1"),
        ),
        tables=(),
        columns=(),
        declared_constraints=(),
    )


def _hypothesis(run_id: str, source_ids: tuple[str, ...], requirement: EntityResolutionRequirement) -> CanonicalModelHypothesis:
    return CanonicalModelHypothesis(
        artifact_id=stable_id("chyp", {"run": run_id, "sources": source_ids, "requirement": requirement.value}),
        run_id=run_id,
        execution_context_id="step28-authority-planning",
        model_version="canonical-v1",
        upstream_review_decision_refs=("trusted-review",),
        source_ids=source_ids,
        domain_assertion_refs=("trusted-domain",),
        entity_types=(CanonicalEntityType(
            canonical_entity_type_id="cet_customer",
            semantic_id="customer",
            business_name="Customer",
            kind=CanonicalEntityKind.IDENTITY,
            entity_resolution_family="customer",
            identity_strategy="trusted-planning-fixture",
            review_state="ACCEPTED_BY_REVIEW",
            provenance_refs=("step28-authority-test",),
        ),),
        entity_resolution_requirements={"customer": requirement},
        evidence_refs=("trusted-evidence",),
        provenance_refs=("step28-authority-test",),
        created_at=datetime(2026, 9, 13, tzinfo=timezone.utc),
    )


def _publish(control: SQLiteControlStore, artifacts: LocalArtifactStore, *, run_id: str, stage_id: str, attempt_id: str, artifact_id: str, artifact_kind: str, value, provenance_refs: tuple[str, ...] = ()):
    ref = artifacts.publish(
        ArtifactManifest(
            artifact_id=artifact_id,
            run_id=run_id,
            stage_id=stage_id,
            attempt_id=attempt_id,
            artifact_kind=artifact_kind,
            media_type="application/json",
            producer="step28-final-authority-test",
            logical_key=f"runs/{run_id}/artifacts/{artifact_id}.json",
            provenance_refs=provenance_refs,
        ),
        value.model_dump_json().encode("utf-8"),
    )
    control.register_artifact(ref)
    return ref


def _seed_planning(control, artifacts, run, *, source_ids=("crm",), requirement=EntityResolutionRequirement.ER_NOT_REQUIRED):
    for source_id in source_ids:
        _publish(control, artifacts, run_id=run.run_id, stage_id="SOURCE_DISCOVERY", attempt_id="planning", artifact_id=f"catalog-{source_id}", artifact_kind="SourceCatalog", value=_catalog(source_id))
    hypothesis = _hypothesis(run.run_id, tuple(source_ids), requirement)
    _publish(control, artifacts, run_id=run.run_id, stage_id="CANONICAL_HYPOTHESES", attempt_id="planning", artifact_id=hypothesis.artifact_id, artifact_kind="CanonicalModelHypothesis", value=hypothesis)
    return hypothesis


def test_server_authority_overrides_client_disable_for_schema_and_required_er(tmp_path: Path) -> None:
    control, artifacts, run = _stores(tmp_path, "authority-required")
    _seed_planning(control, artifacts, run, source_ids=("crm", "erp"), requirement=EntityResolutionRequirement.ER_REQUIRED)
    resolution = ServerOwnedExecutionPlanSelectionResolver(control, artifacts, ROOT).resolve(
        run=run,
        intent=ExecutionPlanIntent(cross_source_mapping_requested=False, entity_resolution_requested=False),
    )
    assert resolution.selection is not None
    selection = resolution.selection
    assert selection.policy_ref == "execution-plan-authority-v2"
    assert selection.decision_for("SCHEMA_MATCHING").selected is True
    assert selection.decision_for("ENTITY_RESOLUTION").selected is True
    assert all(item.policy_ref == selection.policy_ref and item.scope_fingerprint == selection.scope_fingerprint and item.evidence_ref for item in selection.decisions)
    control.close()


def test_server_authority_allows_er_not_required_and_legitimate_optional_exclusion(tmp_path: Path) -> None:
    control, artifacts, run = _stores(tmp_path, "authority-optional")
    _seed_planning(control, artifacts, run, source_ids=("crm",), requirement=EntityResolutionRequirement.ER_NOT_REQUIRED)
    resolution = ServerOwnedExecutionPlanSelectionResolver(control, artifacts, ROOT).resolve(
        run=run,
        intent=ExecutionPlanIntent(entity_resolution_requested=False, optional_semantic_evidence_enabled=False, learned_evidence_enabled=False),
    )
    assert resolution.selection is not None
    assert resolution.selection.decision_for("SCHEMA_MATCHING").selected is False
    assert resolution.selection.decision_for("ENTITY_RESOLUTION").selected is False
    assert resolution.selection.decision_for("OPTIONAL_SEMANTIC_EVIDENCE").selected is False
    assert resolution.selection.decision_for("LEARNED_EVIDENCE").selected is False
    control.close()


def test_api_rejects_client_authored_selection_authority_and_unknown_state_blocks(tmp_path: Path) -> None:
    from dirty_data_to_olap.composition import build_local_backend

    platform, backend = build_local_backend(tmp_path / "api-authority")
    client = TestClient(create_app(backend), raise_server_exceptions=False)
    try:
        created = client.post("/api/v1/runs", headers={**AUTH, "Idempotency-Key": "run"}, json={"project_id": "authority", "configuration_fingerprint": platform.config.configuration_fingerprint})
        assert created.status_code == 201
        run_id = created.json()["run_id"]
        fake = client.post(
            f"/api/v1/runs/{run_id}/execution/prepare",
            headers={**AUTH, "Idempotency-Key": "fake"},
            json={"intent": {"cross_source_mapping_requested": False, "policy_ref": "attacker-policy", "evidence_ref": "attacker-evidence", "scope_fingerprint": "attacker-scope"}},
        )
        assert fake.status_code == 422
        legacy = client.post(
            f"/api/v1/runs/{run_id}/execution/prepare",
            headers={**AUTH, "Idempotency-Key": "legacy"},
            json={"selection": {"run_id": run_id, "policy_ref": "attacker-policy", "scope": "attacker", "scope_fingerprint": "attacker", "decisions": []}},
        )
        assert legacy.status_code == 422
        blocked = client.post(
            f"/api/v1/runs/{run_id}/execution/prepare",
            headers={**AUTH, "Idempotency-Key": "unknown"},
            json={"intent": {"cross_source_mapping_requested": False, "entity_resolution_requested": False}},
        )
        assert blocked.status_code == 409
        assert blocked.json()["status"] == "BLOCKED"
        assert set(blocked.json()["unresolved_stage_ids"]) == {"SOURCE_SCOPE", "ENTITY_RESOLUTION"}
    finally:
        platform.close()


def _materialization_plan(run_id: str) -> ExecutionPlan:
    selection = ExecutionPlanSelection(
        run_id=run_id,
        policy_ref="test-materialization-selection",
        scope="test-materialization-scope",
        scope_fingerprint="test-materialization-scope-fingerprint",
        decisions=(StageSelectionDecision(
            stage_id="REVIEW_MATERIALIZATION_PLAN",
            selected=True,
            policy_ref="test-materialization-selection",
            reason="test selected materialization review",
            scope="test-materialization-scope",
            scope_fingerprint="test-materialization-scope-fingerprint",
        ),),
    )
    return ExecutionPlan(
        plan_id=stable_id("materialization-plan", run_id),
        run_id=run_id,
        stages=(
            StageSpec(stage_id="COMPILATION", handler_key="compilation"),
            StageSpec(stage_id="REVIEW_MATERIALIZATION_PLAN", required=True, conditional=True, selected=True, selection_reason="test selected materialization review", dependencies=("COMPILATION",), handler_key="review-materialization", review_checkpoint=ReviewCheckpoint.REVIEW_MATERIALIZATION_PLAN),
            StageSpec(stage_id="MATERIALIZATION", handler_key="materialization", dependencies=("COMPILATION", "REVIEW_MATERIALIZATION_PLAN"), required_review_checkpoint=ReviewCheckpoint.REVIEW_MATERIALIZATION_PLAN),
        ),
        selection=selection,
    )


def _compiled_fixture():
    from tests.step20_support import planned_flow

    _model, fixture, binding, plan, dimensions, fact, grain, measures = planned_flow()
    policy = ReviewPolicyService()
    analytical_review = policy.create_decision(
        policy.analytical_plan_context(plan),
        decision="ACCEPTED",
        actor="step28-materialization-test",
        rationale="approved typed analytical fixture",
        reviewed_at=datetime(2026, 9, 13, tzinfo=timezone.utc),
    )
    target = TargetConfig(relative_path="step28-materialization.duckdb")
    compiled, generated = AnalyticalCompilerService().compile(plan, dimensions, fact, grain, measures, binding, fixture, target, analytical_review, reviewed_at=datetime(2026, 9, 13, tzinfo=timezone.utc))
    return compiled, generated, target


def test_real_materialization_checkpoint_uses_published_target_config_and_resumes(tmp_path: Path) -> None:
    control, artifacts, run = _stores(tmp_path, "materialization-real")
    control.register_execution_plan(_materialization_plan(run.run_id))
    compiled, generated, target = _compiled_fixture()
    publisher = CompilationArtifactPublisher(artifacts, control)
    calls: list[str] = []

    class Compilation:
        def execute(self, request):
            refs = publisher.publish(run_id=request.run_id, attempt_id=request.attempt_id, compiled_plan=compiled, generated_sql=generated, target_config=target)
            return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(refs.compiled_plan.artifact_id, refs.generated_sql.artifact_id, refs.target_config.artifact_id))

    class Materialization:
        def execute(self, request):
            input_refs = {control.get_artifact(artifact_id) for artifact_id in request.input_artifact_refs}
            assert any(ref is not None and ref.artifact_kind == "TargetConfig" for ref in input_refs)
            calls.append("materialization")
            return StageExecutionResult(status=StageResultStatus.SUCCEEDED)

    worker = JobWorker(control_store=control, artifact_store=artifacts, executor=StageHandlerRegistry({"compilation": Compilation(), "materialization": Materialization()}), worker_id="materialization-worker")
    DurableExecutionSubmission(control).submit_command(command=_command(run.run_id, "submit"), run=run)
    checkpoint = None
    for _ in range(8):
        worker.run_once()
        checkpoint = control.get_stage_job(run_id=run.run_id, stage_id="REVIEW_MATERIALIZATION_PLAN")
        if checkpoint is not None and checkpoint.status is JobStatus.NEEDS_REVIEW:
            break
    assert checkpoint is not None and checkpoint.status is JobStatus.NEEDS_REVIEW
    assert control.get_stage_job(run_id=run.run_id, stage_id="MATERIALIZATION") is None
    assert calls == []
    expected_context = ReviewPolicyService().materialization_context(compiled, generated, target)
    assert checkpoint.review_context == expected_context
    assert {ref.artifact_kind for ref in control.list_artifacts(run_id=run.run_id)} >= {"CompiledPlan", "GeneratedSQL", "TargetConfig"}

    client = TestClient(create_app(BackendService(control_store=control, artifact_store=artifacts)), raise_server_exceptions=False)
    accepted = client.post(
        f"/api/v1/runs/{run.run_id}/reviews/REVIEW_MATERIALIZATION_PLAN",
        headers={**AUTH, "Idempotency-Key": "materialization-review"},
        json={"subject_artifact_id": compiled.compiled_plan_id, "subject_content_hash": control.get_artifact(compiled.compiled_plan_id).content_hash, "decision": "ACCEPTED", "rationale": "approved exact compiled target", "expected_revision": 0},
    )
    assert accepted.status_code == 200, accepted.text
    DurableExecutionSubmission(control).submit_command(command=_command(run.run_id, "resume", ExecutionAction.RESUME), run=run)
    for _ in range(8):
        worker.run_once()
        materialization = control.get_stage_job(run_id=run.run_id, stage_id="MATERIALIZATION")
        if materialization is not None:
            break
    assert control.get_stage_job(run_id=run.run_id, stage_id="REVIEW_MATERIALIZATION_PLAN").status is JobStatus.SUCCEEDED
    assert materialization is not None and materialization.status is JobStatus.QUEUED
    assert calls == []
    assert worker.run_once().status == JobStatus.SUCCEEDED.value
    assert control.get_stage_job(run_id=run.run_id, stage_id="MATERIALIZATION").status is JobStatus.SUCCEEDED
    assert calls == ["materialization"]
    control.close()


def _real_materialization_refs(tmp_path: Path, name: str):
    control, artifacts, run = _stores(tmp_path, name)
    compiled, generated, target = _compiled_fixture()
    refs = CompilationArtifactPublisher(artifacts, control).publish(run_id=run.run_id, attempt_id="compilation-attempt", compiled_plan=compiled, generated_sql=generated, target_config=target)
    return control, artifacts, run, compiled, generated, target, refs


def test_materialization_binding_and_transport_integrity_fail_closed(tmp_path: Path) -> None:
    control, artifacts, run, compiled, generated, target, refs = _real_materialization_refs(tmp_path, "materialization-bindings")
    resolver = ReviewCheckpointSubjectResolver(control, artifacts)

    bad_target = TargetConfig(relative_path="different-target.duckdb")
    bad_target_ref = _publish(control, artifacts, run_id=run.run_id, stage_id="COMPILATION", attempt_id="compilation-attempt", artifact_id="bad-target", artifact_kind="TargetConfig", value=bad_target)
    missing_target = resolver.derive(run_id=run.run_id, checkpoint=ReviewCheckpoint.REVIEW_MATERIALIZATION_PLAN, upstream_artifacts=(refs.compiled_plan, refs.generated_sql))
    wrong_target = resolver.derive(run_id=run.run_id, checkpoint=ReviewCheckpoint.REVIEW_MATERIALIZATION_PLAN, upstream_artifacts=(refs.compiled_plan, refs.generated_sql, bad_target_ref))
    assert not missing_target.contexts and refs.compiled_plan.artifact_id in missing_target.unresolved_subject_ids
    assert not wrong_target.contexts and refs.compiled_plan.artifact_id in wrong_target.unresolved_subject_ids

    wrong_sql = generated.model_copy(update={"generated_sql_id": stable_id("sql", "wrong-generated-sql"), "load_date_sql": generated.load_date_sql + " -- mismatched"})
    wrong_sql_ref = _publish(control, artifacts, run_id=run.run_id, stage_id="COMPILATION", attempt_id="compilation-attempt", artifact_id=wrong_sql.generated_sql_id, artifact_kind="GeneratedSQL", value=wrong_sql)
    wrong_generated = resolver.derive(run_id=run.run_id, checkpoint=ReviewCheckpoint.REVIEW_MATERIALIZATION_PLAN, upstream_artifacts=(refs.compiled_plan, wrong_sql_ref, refs.target_config))
    assert not wrong_generated.contexts

    wrong_compiled = compiled.model_copy(update={"compiled_plan_id": stable_id("cplan", "wrong-compiled-plan"), "generated_sql_hash": "0" * 64})
    wrong_compiled_ref = _publish(control, artifacts, run_id=run.run_id, stage_id="COMPILATION", attempt_id="compilation-attempt", artifact_id=wrong_compiled.compiled_plan_id, artifact_kind="CompiledPlan", value=wrong_compiled)
    wrong_plan = resolver.derive(run_id=run.run_id, checkpoint=ReviewCheckpoint.REVIEW_MATERIALIZATION_PLAN, upstream_artifacts=(wrong_compiled_ref, refs.generated_sql, refs.target_config))
    assert not wrong_plan.contexts

    other_control, other_artifacts, other_run, _other_compiled, _other_generated, _other_target, other_refs = _real_materialization_refs(tmp_path, "materialization-cross-run")
    cross_run = resolver.derive(run_id=run.run_id, checkpoint=ReviewCheckpoint.REVIEW_MATERIALIZATION_PLAN, upstream_artifacts=(refs.compiled_plan, refs.generated_sql, other_refs.target_config))
    assert not cross_run.contexts

    blob = artifacts.root / refs.compiled_plan.storage_key
    blob.write_bytes(b"tampered transport bytes")
    tampered = resolver.derive(run_id=run.run_id, checkpoint=ReviewCheckpoint.REVIEW_MATERIALIZATION_PLAN, upstream_artifacts=(refs.compiled_plan, refs.generated_sql, refs.target_config))
    assert not tampered.contexts and refs.compiled_plan.artifact_id in tampered.unresolved_subject_ids
    control.close()
    other_control.close()


__all__ = []
