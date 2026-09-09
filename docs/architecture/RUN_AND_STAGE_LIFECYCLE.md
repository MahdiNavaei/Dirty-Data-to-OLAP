# Dirty Data to OLAP — Run and Stage Lifecycle

## RunStatus

CREATED, RUNNING, NEEDS_REVIEW, BLOCKED, FAILED, CANCELLED and SUCCEEDED are coarse run statuses. Processing stage names are not run statuses. PARTIAL is not a run state.

- CREATED: run manifest exists and no stage attempt has started.
- RUNNING: at least one required operation is active or resumable work is executing.
- NEEDS_REVIEW: evidence exists but a required human/domain decision is pending.
- BLOCKED: execution cannot proceed because a prerequisite, access condition or required capability is unavailable.
- FAILED: an attempted operation failed or validation proved the result invalid.
- CANCELLED: cancellation was accepted and no further work will execute for the attempt.
- SUCCEEDED: final validation passed and all success guards hold.

## StageStatus

PENDING, RUNNING, SUCCEEDED, NEEDS_REVIEW, BLOCKED, FAILED, CANCELLED, INVALIDATED and SKIPPED are stage statuses.

SKIPPED is legal only for a conditional stage with an explicit reason and policy reference. A required stage cannot be silently skipped. Stage SUCCEEDED means its own outputs are complete; it does not imply run SUCCEEDED.

Review checkpoints are required DAG boundaries with policy-conditional
completion. `REVIEW_EVIDENCE_DECISIONS` follows evidence fusion,
`REVIEW_CANONICAL_IDENTITY` follows canonical hypotheses and required ER output,
`REVIEW_ANALYTICAL_PLAN` follows analytical planning, and
`REVIEW_MATERIALIZATION_PLAN` follows compilation. A required unresolved
checkpoint keeps its stage and guarded downstream work in `NEEDS_REVIEW`.
Accepted decisions resume with a compatible new or resumed attempt; rejected or
deferred decisions do not satisfy the guard. A policy-recorded `SKIPPED` state
is valid only where the checkpoint contract permits it.

The complete machine-readable logical-stage and attempt-state contract is [stage_state_machine.yml](specs/stage_state_machine.yml). `StageStatus` is the aggregate logical-stage state; each immutable `StageAttempt` has its own attempt status and history. Logical invalidation never mutates an old attempt.

## Transitions

Run transitions:

- CREATED -> RUNNING when the first attempt is accepted.
- CREATED -> CANCELLED only before execution.
- RUNNING -> NEEDS_REVIEW when required decisions are pending.
- RUNNING -> BLOCKED when a prerequisite/capability/access dependency is unavailable.
- RUNNING -> FAILED when an attempted operation or required validation fails.
- RUNNING -> CANCELLED after cooperative cancellation.
- RUNNING -> SUCCEEDED only after final validation PASS and all guards.
- NEEDS_REVIEW -> RUNNING only after a compatible decision is recorded.
- NEEDS_REVIEW -> BLOCKED when the decision reveals an unavailable prerequisite.
- BLOCKED -> RUNNING only after prerequisite resolution is recorded.
- FAILED -> RUNNING only as a new attempt with prior failure evidence retained.
- CANCELLED and SUCCEEDED do not silently resume or transition back.

## Success guard

Run SUCCEEDED requires all required stages acceptable, required review complete, materialization complete, validation/reconciliation PASS, zero required UNRESOLVED record dispositions and no required artifact in an invalidated/incomplete state. SQL generation or target-file creation alone is insufficient.

## Attempts and cancellation

Every stage execution has stage_id and attempt_id. An attempt records pinned input artifact IDs/hashes, configuration hash, engine/adapter version, timestamps, result, error/cancellation reference, output references and cache key. Retries create new attempt IDs and retain previous attempts.

Cancellation is cooperative. The request is persisted, stages stop at safe checkpoints, completed artifacts remain available, incomplete artifacts cannot be published as COMPLETE, and cancellation evidence remains attached to the attempt.

## Failure meaning

- Item failure may allow unaffected items to continue, but stage completeness determines NEEDS_REVIEW versus FAILED.
- Required stage failure makes the run FAILED unless an explicit prerequisite condition makes it BLOCKED.
- Semantic uncertainty is NEEDS_REVIEW, not an engine crash.
- Validation failure is FAILED; its target remains evidence but cannot make the run successful.
- Review actions are not engine failures. A failed `ValidationReport` may be
  inspected, but cannot be promoted to PASS or to run `SUCCEEDED`; changed
  input, policy or decision invalidates descendants and requires a new
  validation result.
