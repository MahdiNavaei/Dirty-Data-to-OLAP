from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dirty_data_to_olap.adapters.platform import LocalArtifactStore, SQLiteControlStore
from dirty_data_to_olap.application.backend import BackendService
from dirty_data_to_olap.application.jobs import DurableExecutionSubmission, JobWorker, StageHandlerRegistry
from dirty_data_to_olap.application.review_subjects import ReviewCheckpointSubjectResolver
from dirty_data_to_olap.domain.contracts.api import ExecutionAction, ExecutionCommand
from dirty_data_to_olap.domain.contracts.canonical import ReviewCheckpoint, review_subject_key
from dirty_data_to_olap.domain.contracts.jobs import JobStatus, StageExecutionResult, StageResultStatus
from dirty_data_to_olap.domain.contracts.source import stable_digest, stable_id
from dirty_data_to_olap.entrypoints.api import create_app
from tests.integration.test_step28_execution_plan_review_repair import (
    AUTH,
    _checkpoint_plan,
    _publish_typed,
    _relationship_decision,
    _stores,
)


def _submit_command(control: SQLiteControlStore, run_id: str, key: str, action: ExecutionAction) -> None:
    command = ExecutionCommand(
        command_id=stable_id("prompt04-r1-readiness-command", {"run": run_id, "key": key, "action": action.value}),
        run_id=run_id,
        action=action,
        idempotency_scope=f"prompt04-r1-readiness:{run_id}:{action.value}",
        idempotency_key=key,
        request_fingerprint=stable_digest({"run": run_id, "key": key, "action": action.value}),
        principal_subject=AUTH["X-Local-Principal"],
        principal_source="PROMPT04_R1_READINESS_TEST",
    )
    DurableExecutionSubmission(control).submit_command(command=command, run=control.get_run(run_id))


def _setup_two_subject_run(tmp_path: Path, name: str):
    control, artifacts, run = _stores(tmp_path, name)
    decisions = (_relationship_decision(f"relationship-{name}-one"), _relationship_decision(f"relationship-{name}-two"))
    plan = _checkpoint_plan(
        run.run_id,
        checkpoint=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS,
        upstream_stage="EVIDENCE_FUSION",
        downstream_stage="CANONICAL_HYPOTHESES",
    )
    control.register_execution_plan(plan)

    class Fusion:
        def execute(self, request):
            refs = tuple(
                _publish_typed(
                    control,
                    artifacts,
                    run_id=request.run_id,
                    stage_id=request.stage_id,
                    attempt_id=request.attempt_id,
                    artifact_id=decision.decision_id,
                    artifact_kind="RelationshipDecision",
                    value=decision,
                )
                for decision in decisions
            )
            return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=tuple(ref.artifact_id for ref in refs))

    worker = JobWorker(
        control_store=control,
        artifact_store=artifacts,
        executor=StageHandlerRegistry({"evidence_fusion": Fusion()}),
        worker_id=f"prompt04-r1-{name}",
    )
    _submit_command(control, run.run_id, f"{name}-submit", ExecutionAction.SUBMIT)
    assert worker.run_once().status == JobStatus.SUCCEEDED.value
    assert worker.run_once().status == JobStatus.SUCCEEDED.value
    assert worker.run_once().status == JobStatus.NEEDS_REVIEW.value
    job = control.get_stage_job(run_id=run.run_id, stage_id="REVIEW_EVIDENCE_DECISIONS")
    assert job is not None and job.status is JobStatus.NEEDS_REVIEW and len(job.review_contexts) == 2
    return control, artifacts, run, worker, decisions, job


def _accept(client: TestClient, run_id: str, context, key: str) -> dict:
    response = client.post(
        f"/api/v1/runs/{run_id}/reviews/{ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value}/actions",
        headers={**AUTH, "Idempotency-Key": key},
        json={
            "action": "ACCEPT",
            "subject_artifact_id": context.subject_artifact_id,
            "subject_content_hash": context.subject_content_hash,
            "rationale": "Prompt04-R1 run-wide readiness test.",
            "expected_revision": 0,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_nc_r28_one_of_two_subjects_cannot_release_checkpoint(tmp_path: Path) -> None:
    control, artifacts, run, worker, _decisions, job = _setup_two_subject_run(tmp_path, "nc-r28")
    client = TestClient(create_app(BackendService(control_store=control, artifact_store=artifacts)), raise_server_exceptions=False)
    try:
        first, second = job.review_contexts
        result = _accept(client, run.run_id, first, "nc-r28-first")
        assert result["execution_eligible"] is False
        assert control.get_current_review(run_id=run.run_id, subject_key=review_subject_key(second)) is None

        _submit_command(control, run.run_id, "nc-r28-early-resume", ExecutionAction.RESUME)
        assert worker.run_once().status == JobStatus.BLOCKED.value
        assert control.get_stage_job(run_id=run.run_id, stage_id="REVIEW_EVIDENCE_DECISIONS").status is JobStatus.NEEDS_REVIEW
        assert control.get_stage_job(run_id=run.run_id, stage_id="CANONICAL_HYPOTHESES") is None

        complete = _accept(client, run.run_id, second, "nc-r28-second")
        assert complete["execution_eligible"] is True
        _submit_command(control, run.run_id, "nc-r28-resume", ExecutionAction.RESUME)
        assert worker.run_once().status == JobStatus.SUCCEEDED.value
        assert worker.run_once().status == JobStatus.SUCCEEDED.value
        assert control.get_stage_job(run_id=run.run_id, stage_id="REVIEW_EVIDENCE_DECISIONS").status is JobStatus.SUCCEEDED
        assert control.get_stage_job(run_id=run.run_id, stage_id="CANONICAL_HYPOTHESES") is not None
    finally:
        control.close()


def test_nc_r29_invalidation_between_accept_and_resume_blocks_worker(tmp_path: Path) -> None:
    control, artifacts, run, worker, _decisions, job = _setup_two_subject_run(tmp_path, "nc-r29")
    client = TestClient(create_app(BackendService(control_store=control, artifact_store=artifacts)), raise_server_exceptions=False)
    try:
        context = job.review_contexts[0]
        accepted = _accept(client, run.run_id, context, "nc-r29-accept")
        assert accepted["execution_eligible"] is False
        invalidated = client.post(
            f"/api/v1/runs/{run.run_id}/reviews/{ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value}/invalidate",
            headers={**AUTH, "Idempotency-Key": "nc-r29-invalidate"},
            json={
                "subject_artifact_id": context.subject_artifact_id,
                "subject_content_hash": context.subject_content_hash,
                "reason": "invalidate before resume",
                "expected_revision": accepted["resulting_revision"],
            },
        )
        assert invalidated.status_code == 200, invalidated.text
        key = review_subject_key(context)
        assert control.get_current_review(run_id=run.run_id, subject_key=key).decision.decision.value == "INVALIDATED"

        _submit_command(control, run.run_id, "nc-r29-resume", ExecutionAction.RESUME)
        assert worker.run_once().status == JobStatus.BLOCKED.value
        assert control.get_stage_job(run_id=run.run_id, stage_id="REVIEW_EVIDENCE_DECISIONS").status is JobStatus.NEEDS_REVIEW
        assert control.get_stage_job(run_id=run.run_id, stage_id="CANONICAL_HYPOTHESES") is None
    finally:
        control.close()
