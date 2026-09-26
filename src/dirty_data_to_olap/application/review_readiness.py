"""Authoritative readiness checks for review-gated execution transitions."""

from __future__ import annotations

from dataclasses import dataclass

from dirty_data_to_olap.application.platform import ArtifactStorePort, ControlStorePort, PlatformError
from dirty_data_to_olap.application.review_policy import ReviewCompatibilityError, ReviewPolicyService
from dirty_data_to_olap.domain.contracts.canonical import ReviewCompatibilityContext, ReviewDecision, ReviewDecisionStatus, review_subject_key
from dirty_data_to_olap.domain.contracts.jobs import JobKind, JobStatus, ExecutionPlan
from dirty_data_to_olap.domain.contracts.platform import ArtifactIntegrityState, ArtifactPublicationState, RunStatus


@dataclass(frozen=True)
class ReviewReadiness:
    eligible: bool
    reason: str
    job_id: str | None = None
    unresolved_subject_keys: tuple[str, ...] = ()


def _dependencies_succeeded(control_store: ControlStorePort, plan: ExecutionPlan, stage_id: str) -> bool:
    stage = plan.stage(stage_id)
    if stage is None or not stage.selected:
        return False
    for dependency_id in stage.dependencies:
        dependency = control_store.get_stage_job(run_id=plan.run_id, stage_id=dependency_id)
        if dependency is None or dependency.status is not JobStatus.SUCCEEDED:
            return False
    for dependency_id in stage.conditional_dependencies:
        dependency_stage = plan.stage(dependency_id)
        if dependency_stage is not None and dependency_stage.selected:
            dependency = control_store.get_stage_job(run_id=plan.run_id, stage_id=dependency_id)
            if dependency is None or dependency.status is not JobStatus.SUCCEEDED:
                return False
    return True


def evaluate_review_readiness(
    control_store: ControlStorePort,
    artifact_store: ArtifactStorePort,
    *,
    run_id: str,
    candidate_job_id: str | None = None,
    focus_subject_key: str | None = None,
    prospective_review: tuple[ReviewCompatibilityContext, ReviewDecision] | None = None,
) -> ReviewReadiness:
    """Evaluate the next durable review transition from current server state.

    This intentionally evaluates the selected review job and every subject in
    that job. It does not use a global "all reviews ever" rule and does not
    require G6 before pre-G6 stages.
    """

    run = control_store.get_run(run_id)
    if run is None:
        return ReviewReadiness(False, "run is unavailable")
    if run.status in {RunStatus.CANCELLED, RunStatus.SUCCEEDED, RunStatus.FAILED}:
        return ReviewReadiness(False, "terminal run cannot resume")
    plan = control_store.get_execution_plan(run_id)
    if plan is None:
        return ReviewReadiness(False, "execution plan is unavailable")
    jobs = [job for job in control_store.list_jobs(run_id=run_id, limit=10000) if job.job_kind is JobKind.STAGE and job.status is JobStatus.NEEDS_REVIEW]
    if candidate_job_id is not None:
        jobs = [job for job in jobs if job.job_id == candidate_job_id]
    elif focus_subject_key is not None:
        jobs = [job for job in jobs if any(review_subject_key(context) == focus_subject_key for context in (job.review_contexts or ((job.review_context,) if job.review_context is not None else ())))]
    if not jobs:
        return ReviewReadiness(False, "no current review job contains the requested subject")
    policy = ReviewPolicyService()
    for job in jobs:
        stage = plan.stage(job.stage_id or "")
        if stage is None or not stage.selected:
            return ReviewReadiness(False, "review stage is not selected", job_id=job.job_id)
        if not _dependencies_succeeded(control_store, plan, stage.stage_id):
            return ReviewReadiness(False, "review stage prerequisites are not complete", job_id=job.job_id)
        contexts = job.review_contexts or ((job.review_context,) if job.review_context is not None else ())
        if not contexts:
            return ReviewReadiness(False, "review job has no authoritative subjects", job_id=job.job_id)
        unresolved: list[str] = []
        for context in contexts:
            subject_key = review_subject_key(context)
            subject = control_store.get_artifact(context.subject_artifact_id)
            if subject is None or subject.run_id != run_id or subject.publication_state is not ArtifactPublicationState.PUBLISHED:
                unresolved.append(subject_key)
                continue
            try:
                stored = artifact_store.stat(subject)
                integrity = artifact_store.verify(subject)
            except (KeyError, OSError, PlatformError):
                unresolved.append(subject_key)
                continue
            if stored != subject or integrity.state is not ArtifactIntegrityState.VERIFIED or subject.content_hash != context.subject_content_hash:
                unresolved.append(subject_key)
                continue
            current = control_store.get_current_review(run_id=run_id, subject_key=subject_key)
            current_decision = prospective_review[1] if prospective_review is not None and prospective_review[0] == context else (None if current is None else current.decision)
            if current_decision is None or current_decision.decision not in {ReviewDecisionStatus.ACCEPTED, ReviewDecisionStatus.SKIPPED}:
                unresolved.append(subject_key)
                continue
            action_state = control_store.get_review_action_state(run_id=run_id, subject_key=subject_key)
            if action_state is not None and (
                action_state.current_subject_artifact_id != context.subject_artifact_id
                or action_state.current_subject_content_hash != context.subject_content_hash
            ):
                unresolved.append(subject_key)
                continue
            try:
                policy.require_compatible(current_decision, context)
            except ReviewCompatibilityError:
                unresolved.append(subject_key)
        if unresolved:
            return ReviewReadiness(False, "accepted review is required; every required subject in the current review job must be accepted and compatible", job_id=job.job_id, unresolved_subject_keys=tuple(sorted(unresolved)))
        return ReviewReadiness(True, "the current review job is fully authorized for resume", job_id=job.job_id)
    return ReviewReadiness(False, "no review transition is currently eligible")


__all__ = ["ReviewReadiness", "evaluate_review_readiness"]
