from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from dirty_data_to_olap.adapters.platform import LocalArtifactStore, SQLiteControlStore
from dirty_data_to_olap.application.backend import BackendService
from dirty_data_to_olap.application.jobs import DurableExecutionSubmission, JobWorker, StageHandlerRegistry
from dirty_data_to_olap.application.evidence_fusion import EvidenceFusionService
from dirty_data_to_olap.application.review_policy import ReviewPolicyService
from dirty_data_to_olap.composition import build_local_backend
from dirty_data_to_olap.domain.contracts.api import ExecutionAction, ExecutionCommand
from dirty_data_to_olap.domain.contracts.canonical import CanonicalEntityKind, CanonicalEntityType, CanonicalModelHypothesis, EntityResolutionRequirement, ReviewCheckpoint, review_subject_key
from dirty_data_to_olap.domain.contracts.evidence_fusion import DecisionExplanation, DecisionState, FusionScore, RelationshipDecision
from dirty_data_to_olap.domain.contracts.jobs import (
    ExecutionPlan,
    ExecutionPlanIntent,
    ExecutionPlanSelection,
    JobStatus,
    StageExecutionResult,
    StageResultStatus,
    StageSelectionDecision,
    StageSpec,
)
from dirty_data_to_olap.domain.contracts.platform import ArtifactManifest, RunRecord
from dirty_data_to_olap.domain.contracts.source import AdapterReference, SelectionScope, SourceCatalog, SourceDescriptor, SourceType, stable_digest, stable_id
from dirty_data_to_olap.entrypoints.api import create_app
from tests.product_acceptance.prompt02_control_evidence import record_control_observation


AUTH = {"X-Local-Principal": "step28-plan-review-test"}


def _stores(tmp_path: Path, name: str = "run"):
    control = SQLiteControlStore(tmp_path / f"{name}.sqlite", project_root=tmp_path)
    artifacts = LocalArtifactStore(tmp_path / f"{name}-artifacts", project_root=tmp_path)
    run = control.create_run(RunRecord(run_id=f"run-{name}", project_id="step28", configuration_fingerprint="cfg", metadata={"_owner_subject": "step28-plan-review-test"}))
    return control, artifacts, run


def _command(run_id: str, key: str, action: ExecutionAction = ExecutionAction.SUBMIT) -> ExecutionCommand:
    return ExecutionCommand(
        command_id=stable_id("step28-test-command", {"run": run_id, "key": key, "action": action.value}),
        run_id=run_id,
        action=action,
        idempotency_scope=f"step28-test:{run_id}:{action.value}",
        idempotency_key=key,
        request_fingerprint=stable_digest({"run": run_id, "key": key, "action": action.value}),
        principal_subject="step28-plan-review-test",
        principal_source="STEP28_REPAIR_TEST",
    )


def _selection(run_id: str, decisions: tuple[StageSelectionDecision, ...]) -> ExecutionPlanSelection:
    return ExecutionPlanSelection(
        run_id=run_id,
        policy_ref="step28-test-selection-v1",
        scope="step28-test-run-scope",
        scope_fingerprint="step28-test-scope-fingerprint",
        decisions=decisions,
    )


def _decision(stage_id: str, *, selected: bool, reason: str | None = None) -> StageSelectionDecision:
    return StageSelectionDecision(
        stage_id=stage_id,
        selected=selected,
        policy_ref="step28-test-selection-v1",
        evidence_ref=f"evidence-{stage_id.lower()}",
        reason=reason or (f"explicitly selected {stage_id}" if selected else f"explicitly excluded {stage_id}"),
        scope="step28-test-run-scope",
        scope_fingerprint="step28-test-scope-fingerprint",
    )


def _relationship_decision(decision_id: str) -> RelationshipDecision:
    return RelationshipDecision(
        decision_id=decision_id,
        candidate_id=f"candidate-{decision_id}",
        subject_id=f"subject-{decision_id}",
        from_table="orders",
        from_columns=("customer_ref",),
        to_table="customers",
        to_columns=("customer_ref",),
        proposed_cardinality="MANY_TO_ONE",
        score=FusionScore(value=0.8, eligible_weight=1, observed_weight=1, evidence_coverage=1, sufficient=True),
        confidence_band="HIGH",
        decision_state=DecisionState.REVIEW_REQUIRED,
        policy=EvidenceFusionService.load_policy(),
        explanation=DecisionExplanation(supports=(f"signal-{decision_id}",)),
        input_evidence_fingerprint=f"input-{decision_id}",
        provenance="step28-real-checkpoint-test",
    )


def _publish_typed(control: SQLiteControlStore, artifacts: LocalArtifactStore, *, run_id: str, stage_id: str, attempt_id: str, artifact_id: str, artifact_kind: str, value):
    payload = value.model_dump_json().encode("utf-8")
    ref = artifacts.publish(
        ArtifactManifest(
            artifact_id=artifact_id,
            run_id=run_id,
            stage_id=stage_id,
            attempt_id=attempt_id,
            artifact_kind=artifact_kind,
            media_type="application/json",
            producer="step28-real-checkpoint-test",
            storage_key=f"runs/{run_id}/artifacts/{artifact_id}.json",
        ),
        payload,
    )
    control.register_artifact(ref)
    return ref


def _trusted_catalog(source_id: str) -> SourceCatalog:
    return SourceCatalog(
        source=SourceDescriptor(
            source_id=source_id,
            display_name=source_id,
            source_type=SourceType.SQLITE,
            file_locator=f"{source_id}.sqlite",
            selection_scope=SelectionScope(),
            schema_fingerprint=f"schema-{source_id}",
            adapter_reference=AdapterReference(name="step28-test-source", version="1", config_fingerprint="step28-source-config"),
        ),
        tables=(),
        columns=(),
        declared_constraints=(),
    )


def _trusted_hypothesis(run_id: str, source_ids: tuple[str, ...], requirement: EntityResolutionRequirement) -> CanonicalModelHypothesis:
    return CanonicalModelHypothesis(
        artifact_id=stable_id("chyp", {"run_id": run_id, "source_ids": source_ids, "requirement": requirement.value}),
        run_id=run_id,
        execution_context_id="step28-trusted-planning",
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
            provenance_refs=("step28-test",),
        ),),
        entity_resolution_requirements={"customer": requirement},
        evidence_refs=("trusted-evidence",),
        provenance_refs=("step28-trusted-planning",),
        created_at=datetime(2026, 9, 13, tzinfo=timezone.utc),
    )


def _checkpoint_plan(run_id: str, *, checkpoint: ReviewCheckpoint, upstream_stage: str, downstream_stage: str | None = None) -> ExecutionPlan:
    checkpoint_stage = {
        ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS: "REVIEW_EVIDENCE_DECISIONS",
        ReviewCheckpoint.REVIEW_ANALYTICAL_PLAN: "REVIEW_ANALYTICAL_PLAN",
    }[checkpoint]
    stages = [
        StageSpec(stage_id=upstream_stage, handler_key=upstream_stage.lower(), final_validation=False),
        StageSpec(
            stage_id=checkpoint_stage,
            required=True,
            conditional=True,
            selected=True,
            selection_reason=f"explicitly selected {checkpoint_stage}",
            dependencies=(upstream_stage,),
            handler_key=checkpoint_stage.lower(),
            review_checkpoint=checkpoint,
        ),
    ]
    if downstream_stage is not None:
        stages.append(
            StageSpec(
                stage_id=downstream_stage,
                required=True,
                dependencies=(upstream_stage, checkpoint_stage),
                required_review_checkpoint=checkpoint,
                handler_key=downstream_stage.lower(),
            )
        )
    selection = _selection(run_id, (_decision(checkpoint_stage, selected=True, reason=f"explicitly selected {checkpoint_stage}"),))
    return ExecutionPlan(plan_id=stable_id("step28-checkpoint-plan", {"run": run_id, "checkpoint": checkpoint.value}), run_id=run_id, stages=tuple(stages), selection=selection)


def test_real_evidence_checkpoint_pauses_then_resumes_guarded_downstream(tmp_path: Path) -> None:
    control, artifacts, run = _stores(tmp_path, "real-evidence")
    decision = _relationship_decision("relationship-real")
    plan = _checkpoint_plan(run.run_id, checkpoint=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS, upstream_stage="EVIDENCE_FUSION", downstream_stage="CANONICAL_HYPOTHESES")
    control.register_execution_plan(plan)

    class Fusion:
        def execute(self, request):
            ref = _publish_typed(control, artifacts, run_id=request.run_id, stage_id=request.stage_id, attempt_id=request.attempt_id, artifact_id=decision.decision_id, artifact_kind="RelationshipDecision", value=decision)
            return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,))

    worker = JobWorker(control_store=control, artifact_store=artifacts, executor=StageHandlerRegistry({"evidence_fusion": Fusion()}), worker_id="real-evidence-worker")
    DurableExecutionSubmission(control).submit_command(command=_command(run.run_id, "submit"), run=run)
    assert worker.run_once().status == JobStatus.SUCCEEDED.value
    assert worker.run_once().status == JobStatus.SUCCEEDED.value
    assert worker.run_once().status == JobStatus.NEEDS_REVIEW.value

    review_job = control.get_stage_job(run_id=run.run_id, stage_id="REVIEW_EVIDENCE_DECISIONS")
    assert review_job is not None and review_job.status is JobStatus.NEEDS_REVIEW
    assert control.get_run(run.run_id).status.value == "NEEDS_REVIEW"
    assert control.get_stage_job(run_id=run.run_id, stage_id="CANONICAL_HYPOTHESES") is None
    context = review_job.review_context
    assert context is not None
    subject = control.get_artifact(context.subject_artifact_id)
    assert subject is not None
    expected_context = ReviewPolicyService().evidence_context(decision, (), subject_content_hash=subject.content_hash).model_copy(update={"subject_artifact_id": subject.artifact_id})
    assert context == expected_context
    assert context.subject_content_hash == subject.content_hash
    assert control.get_current_review(run_id=run.run_id, subject_key=review_subject_key(context)) is None

    DurableExecutionSubmission(control).submit_command(command=_command(run.run_id, "resume-before-review", ExecutionAction.RESUME), run=run)
    blocked_resume = worker.run_once()
    assert blocked_resume.status == JobStatus.BLOCKED.value
    assert "accepted review is required" in blocked_resume.detail
    assert control.get_run(run.run_id).status.value == "NEEDS_REVIEW"
    assert control.get_stage_job(run_id=run.run_id, stage_id="CANONICAL_HYPOTHESES") is None
    record_control_observation("NC14", f"RESUME_NOT_AUTHORIZED:{blocked_resume.detail};downstream_absent", "tests/integration/test_step28_execution_plan_review_repair.py:218")

    client = TestClient(create_app(BackendService(control_store=control, artifact_store=artifacts)), raise_server_exceptions=False)
    reviewed = client.post(
        f"/api/v1/runs/{run.run_id}/reviews/{ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value}",
        headers={**AUTH, "Idempotency-Key": "real-evidence-review"},
        json={"subject_artifact_id": decision.decision_id, "subject_content_hash": subject.content_hash, "context": context.model_dump(mode="json"), "decision": "ACCEPTED", "rationale": "reviewed typed evidence subject", "expected_revision": 0},
    )
    assert reviewed.status_code == 200, reviewed.text
    assert control.get_current_review(run_id=run.run_id, subject_key=review_subject_key(context)) is not None

    DurableExecutionSubmission(control).submit_command(command=_command(run.run_id, "resume", ExecutionAction.RESUME), run=run)
    assert worker.run_once().status == JobStatus.SUCCEEDED.value
    assert worker.run_once().status == JobStatus.SUCCEEDED.value
    assert control.get_stage_job(run_id=run.run_id, stage_id="REVIEW_EVIDENCE_DECISIONS").status is JobStatus.SUCCEEDED
    assert control.get_stage_job(run_id=run.run_id, stage_id="CANONICAL_HYPOTHESES").status is JobStatus.QUEUED
    control.close()


def test_real_analytical_checkpoint_derives_context_without_provider_handler(tmp_path: Path) -> None:
    from tests.step20_support import planned_flow

    control, artifacts, run = _stores(tmp_path, "real-analytical")
    analytical_plan = planned_flow()[3]
    plan = _checkpoint_plan(run.run_id, checkpoint=ReviewCheckpoint.REVIEW_ANALYTICAL_PLAN, upstream_stage="ANALYTICAL_PLANNING")
    control.register_execution_plan(plan)

    class Planner:
        def execute(self, request):
            ref = _publish_typed(control, artifacts, run_id=request.run_id, stage_id=request.stage_id, attempt_id=request.attempt_id, artifact_id=analytical_plan.plan_id, artifact_kind="AnalyticalPlan", value=analytical_plan)
            return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,))

    worker = JobWorker(control_store=control, artifact_store=artifacts, executor=StageHandlerRegistry({"analytical_planning": Planner()}), worker_id="real-analytical-worker")
    DurableExecutionSubmission(control).submit_command(command=_command(run.run_id, "submit"), run=run)
    assert worker.run_once().status == JobStatus.SUCCEEDED.value
    assert worker.run_once().status == JobStatus.SUCCEEDED.value
    assert worker.run_once().status == JobStatus.NEEDS_REVIEW.value
    checkpoint_job = control.get_stage_job(run_id=run.run_id, stage_id="REVIEW_ANALYTICAL_PLAN")
    assert checkpoint_job is not None and checkpoint_job.status is JobStatus.NEEDS_REVIEW
    assert checkpoint_job.review_context == ReviewPolicyService().analytical_plan_context(analytical_plan)
    control.close()


def test_multiple_real_evidence_subjects_are_explicitly_retained(tmp_path: Path) -> None:
    control, artifacts, run = _stores(tmp_path, "multiple-evidence")
    decisions = (_relationship_decision("relationship-one"), _relationship_decision("relationship-two"))
    plan = _checkpoint_plan(run.run_id, checkpoint=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS, upstream_stage="EVIDENCE_FUSION")
    control.register_execution_plan(plan)

    class Fusion:
        def execute(self, request):
            refs = tuple(
                _publish_typed(control, artifacts, run_id=request.run_id, stage_id=request.stage_id, attempt_id=request.attempt_id, artifact_id=value.decision_id, artifact_kind="RelationshipDecision", value=value)
                for value in decisions
            )
            return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=tuple(ref.artifact_id for ref in refs))

    worker = JobWorker(control_store=control, artifact_store=artifacts, executor=StageHandlerRegistry({"evidence_fusion": Fusion()}), worker_id="multiple-evidence-worker")
    DurableExecutionSubmission(control).submit_command(command=_command(run.run_id, "submit"), run=run)
    worker.run_once()
    worker.run_once()
    worker.run_once()
    job = control.get_stage_job(run_id=run.run_id, stage_id="REVIEW_EVIDENCE_DECISIONS")
    assert job is not None and job.status is JobStatus.NEEDS_REVIEW
    assert job.review_context is None
    assert len(job.review_contexts) == 2
    assert {context.subject_artifact_id for context in job.review_contexts} == {value.decision_id for value in decisions}
    control.close()


def test_conditional_dependency_requires_selected_stage_job_but_ignores_explicit_exclusion(tmp_path: Path) -> None:
    control, artifacts, run = _stores(tmp_path, "conditional-dependency")
    stages = (
        StageSpec(stage_id="OPTIONAL_INPUT", required=False, conditional=True, selected=True, selection_reason="explicitly selected OPTIONAL_INPUT", handler_key="optional_input"),
        StageSpec(stage_id="DOWNSTREAM", required=False, conditional_dependencies=("OPTIONAL_INPUT",), handler_key="downstream"),
    )
    plan = ExecutionPlan(run_id=run.run_id, plan_id="conditional-plan", stages=stages, selection=_selection(run.run_id, (_decision("OPTIONAL_INPUT", selected=True, reason="explicitly selected OPTIONAL_INPUT"),)))
    control.register_execution_plan(plan)
    DurableExecutionSubmission(control).submit_command(command=_command(run.run_id, "submit"), run=run)

    class Success:
        def execute(self, _request):
            return StageExecutionResult(status=StageResultStatus.SUCCEEDED)

    worker = JobWorker(control_store=control, artifact_store=artifacts, executor=StageHandlerRegistry({"optional_input": Success(), "downstream": Success()}), worker_id="conditional-worker")
    worker.run_once()
    assert control.get_stage_job(run_id=run.run_id, stage_id="OPTIONAL_INPUT") is not None
    assert control.get_stage_job(run_id=run.run_id, stage_id="DOWNSTREAM") is None
    worker.run_once()
    assert control.get_stage_job(run_id=run.run_id, stage_id="DOWNSTREAM") is not None

    control2, artifacts2, run2 = _stores(tmp_path, "conditional-excluded")
    excluded_plan = ExecutionPlan(
        run_id=run2.run_id,
        plan_id="conditional-excluded-plan",
        stages=(
            StageSpec(stage_id="OPTIONAL_INPUT", required=False, conditional=True, selected=False, selection_reason="explicitly excluded OPTIONAL_INPUT", handler_key="optional_input"),
            StageSpec(stage_id="DOWNSTREAM", required=False, conditional_dependencies=("OPTIONAL_INPUT",), handler_key="downstream"),
        ),
        selection=_selection(run2.run_id, (_decision("OPTIONAL_INPUT", selected=False, reason="explicitly excluded OPTIONAL_INPUT"),)),
    )
    control2.register_execution_plan(excluded_plan)
    DurableExecutionSubmission(control2).submit_command(command=_command(run2.run_id, "submit"), run=run2)
    worker2 = JobWorker(control_store=control2, artifact_store=artifacts2, executor=StageHandlerRegistry({"downstream": Success()}), worker_id="conditional-excluded-worker")
    worker2.run_once()
    assert control2.get_stage_job(run_id=run2.run_id, stage_id="OPTIONAL_INPUT") is None
    assert control2.get_stage_job(run_id=run2.run_id, stage_id="DOWNSTREAM") is not None
    control.close()
    control2.close()


def test_execution_plan_rejects_inconsistent_selection_and_success_guards(tmp_path: Path) -> None:
    run_id = "run-validation"
    with pytest.raises(ValueError, match="unselected hard dependency"):
        ExecutionPlan(
            run_id=run_id,
            plan_id="plan-hard-dependency",
            stages=(
                StageSpec(stage_id="OPTIONAL", required=False, conditional=True, selected=False, selection_reason="excluded", handler_key="optional"),
                StageSpec(stage_id="DOWNSTREAM", dependencies=("OPTIONAL",), handler_key="downstream"),
            ),
            selection=_selection(run_id, (_decision("OPTIONAL", selected=False, reason="excluded"),)),
        )
    record_control_observation("NC08", "ValueError:unselected hard dependency", "tests/integration/test_step28_execution_plan_review_repair.py:341")
    with pytest.raises(ValueError, match="selected review checkpoint guard"):
        ExecutionPlan(
            run_id=run_id,
            plan_id="plan-review-guard",
            stages=(
                StageSpec(stage_id="CHECKPOINT", required=False, conditional=True, selected=False, selection_reason="excluded", handler_key="checkpoint", review_checkpoint=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS),
                StageSpec(stage_id="DOWNSTREAM", handler_key="downstream", required_review_checkpoint=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS),
            ),
            selection=_selection(run_id, (_decision("CHECKPOINT", selected=False, reason="excluded"),)),
        )
    with pytest.raises(ValueError, match="final validation"):
        ExecutionPlan(
            run_id=run_id,
            plan_id="plan-no-final",
            stages=(StageSpec(stage_id="FINAL", required=False, selected=False, handler_key="final", final_validation=True),),
            success_guard_required=True,
        )


def _authoritative_selection(run_id: str, *, omit_stage: str | None = None) -> ExecutionPlanSelection:
    graph = yaml.safe_load((Path(__file__).resolve().parents[2] / "docs" / "architecture" / "specs" / "stage_graph.yml").read_text(encoding="utf-8"))
    decisions = []
    for raw in graph["stages"]:
        if not raw.get("conditional") or raw["stage_id"] == omit_stage:
            continue
        stage_id = str(raw["stage_id"])
        selected = bool(raw.get("required", False)) or stage_id == "SCHEMA_MATCHING"
        decisions.append(
            StageSelectionDecision(
                stage_id=stage_id,
                selected=selected,
                policy_ref="product-selection-v1",
                evidence_ref=f"product-evidence-{stage_id.lower()}",
                reason=f"explicit policy {'selected' if selected else 'excluded'} {stage_id}",
                scope="product-run-selection",
                scope_fingerprint="step28-test-scope-fingerprint",
            )
        )
    return ExecutionPlanSelection(
        run_id=run_id,
        policy_ref="product-selection-v1",
        scope="product-run-selection",
        scope_fingerprint="step28-test-scope-fingerprint",
        decisions=tuple(decisions),
    )


def test_public_api_bootstraps_plan_before_submit_without_future_artifacts(tmp_path: Path) -> None:
    platform, backend = build_local_backend(tmp_path / "product")
    client = TestClient(create_app(backend), raise_server_exceptions=False)
    try:
        created = client.post("/api/v1/runs", headers={**AUTH, "Idempotency-Key": "product-run"}, json={"project_id": "product", "configuration_fingerprint": platform.config.configuration_fingerprint})
        assert created.status_code == 201, created.text
        run_id = created.json()["run_id"]
        intent = ExecutionPlanIntent(cross_source_mapping_requested=False, entity_resolution_requested=False)
        prepared = client.post(f"/api/v1/runs/{run_id}/execution/prepare", headers={**AUTH, "Idempotency-Key": "product-plan"}, json={"intent": intent.model_dump(mode="json")})
        assert prepared.status_code == 200, prepared.text
        assert prepared.json()["status"] == "READY"
        assert prepared.json()["planning_phase"] == "BOOTSTRAP"
        plan = platform.control_store.get_execution_plan(run_id)
        assert plan is not None and plan.selection is None
        assert plan.planning_phase.value == "BOOTSTRAP"
        assert tuple(stage.stage_id for stage in plan.stages) == ("SOURCE_DISCOVERY",)

        submitted = client.post(f"/api/v1/runs/{run_id}/execution", headers={**AUTH, "Idempotency-Key": "product-submit"})
        assert submitted.status_code == 202, submitted.text
    finally:
        platform.close()
