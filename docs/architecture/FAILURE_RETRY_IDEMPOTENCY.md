# Dirty Data to OLAP — Failure, Retry and Idempotency

## Attempt record

Each stage attempt has stage_id, attempt_id, input artifact refs/hashes, configuration hash, adapter/engine version, start/end time, result status, error reference, output artifact refs, cancellation state and cache key. A retry creates a new attempt and never overwrites the previous attempt record.

## Retry policy

Automatic retry is allowed only when inputs are pinned, the operation is safe and idempotent, side effects are controlled and no partial output can be mistaken for a published result. Non-atomic target materialization, unknown external side effects and destructive operations are not blindly retried.

Retries use a new attempt-local location and publish only after validation and hash checks. A prior failure remains visible and may be used in diagnosis.

## Error isolation

Every failure is represented at three levels:

- item-level: the affected column, table, relationship candidate or entity family;
- stage-level: whether the stage is SUCCEEDED, NEEDS_REVIEW, BLOCKED or FAILED;
- run-level: whether unrelated work can continue, the run needs review, is blocked or failed.

Examples:

- One profiler column failure may leave other profiles usable, but required completeness determines stage outcome.
- A Desbordante crash fails dependency discovery; no relationship is guessed.
- Valentine absence skips an optional matching stage with a reason or blocks when required by the selected plan.
- A Splink failure affects selected entity families; unrelated analytical work may continue only if its dependencies do not require those mappings.
- Disk failure during materialization leaves an incomplete non-consumable target and a FAILED attempt.
- Validation failure leaves the target as evidence, but the run cannot be SUCCEEDED.

## Cancellation

Cancellation is recorded in the Control Store and honored at safe checkpoints. Completed artifacts survive. WRITING artifacts cannot be published as COMPLETE. Native calls may not stop instantly; the attempt records that limitation rather than claiming instantaneous cancellation.

## Resume

NEEDS_REVIEW resumes after a compatible decision. BLOCKED resumes after a prerequisite/capability resolution. FAILED resumes only through a new attempt. Unrelated valid upstream artifacts are reused through cache/invalidation rules; earlier failure evidence is retained.
