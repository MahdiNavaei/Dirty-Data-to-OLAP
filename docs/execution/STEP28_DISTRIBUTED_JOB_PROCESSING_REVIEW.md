# Step28 - Distributed Job Processing Review

## GOAL RESULT

Status: PASS for the bounded local durable execution substrate owned by Step28.

Starting baseline: branch `main`, `HEAD=origin/main=42e7b90962cc56f0518b785971e8aaabfb951834`, with the latest verified Step27 integrity-repair content at `e840a11dfa128fb7a62afead783b0f25b72b8060`. The protected `tests/quality_unit_artifacts/` path was preserved, unread, unstaged and uncommitted.

Step28 owns durable command/job processing and the Step29 handoff. It does not claim exactly-once execution, production HA, external broker operation, frontend completion, deployment, or G7 completion.

## Architecture inspected

The Step28 playbook, global execution protocol and invariants, runtime topology, engine interfaces, authoritative stage graph, platform/control-store contracts, Step23 persistence boundary, Step24 synchronous scale boundary, Step25 review contracts, Step26 visualization contracts, Step27 backend API and its integrity repair, existing review-policy context builders, artifact publication/verification, tests and validators were inspected before implementation.

## OSS / queue decision

Huey, RQ, Taskiq and Dramatiq were evaluated for license, broker/storage assumptions, retry behavior and fit with the project-owned typed control store. No third-party queue dependency or copied implementation was introduced. The chosen runtime is a project-owned SQLite queue/control-store path so command identity, run-scoped plans, review state, artifact references, lease generation and safe metadata remain one authority. Research clones remain non-runtime and disposable.

## Execution architecture

`ExecutionCommand` is durably accepted into the existing SQLite `ControlStore`. `JobWorker` claims commands/stage jobs transactionally and dispatches typed `StageExecutionRequest` values to a project-owned `StageExecutorPort`. The authoritative DAG is projected from `docs/architecture/specs/stage_graph.yml`; Step28 does not create a second semantic DAG. `BoundedWorkerPool` provides bounded local pumping, and Step24 data execution remains synchronous under its existing boundary.

## Identity and delivery

The durable model distinguishes `command_id`, command job ID, stage job ID, stable stage execution request ID, `StageAttempt` ID, artifact IDs and the existing run ID. Duplicate command IDs with the same semantic fingerprint replay the same durable job; conflicting action/fingerprint reuse is rejected. Delivery is at-least-once compatible and exactly-once distributed execution is explicitly not claimed.

## Lease, fencing and recovery

Claims use `BEGIN IMMEDIATE`, an owner, expiry, heartbeat and monotonic `lease_generation`. Expired jobs are reclaimable. Heartbeat and finalization require the current owner/generation and a non-expired lease, so a stale worker cannot finalize after reclaim. A crash after claim or attempt creation leaves durable RUNNING/attempt history for expiry-based recovery; reclaim reuses the in-flight attempt identity where safe.

## Retry policy

Failure classes are typed. Retryable transient failures use bounded exponential backoff and a new `StageAttempt` after the retry is made available. Terminal, blocked-prerequisite, cancellation and unknown-side-effect outcomes are not retried blindly. Retry exhaustion is durable and visible.

## Cancellation and resume

Queued/running cancellation is persisted at run and job scope. Store finalization wins cancellation races, preventing a cancelled run from becoming complete or publishing a successful logical result. Resume is an explicit command: compatible accepted review decisions can authorize a new attempt; rejected, deferred, stale, incompatible and unknown-side-effect states remain blocked. Terminal cancelled/succeeded runs cannot be resumed. Review contexts are registered from stage results and checked through `ReviewPolicyService`.

## Stage executor coverage

Handlers are explicit registry entries. An absent handler produces `BLOCKED/STAGE_HANDLER_UNAVAILABLE`, never fake success. Output artifacts must be registered, run/stage/attempt bound, published and hash-verified before completion. A required final-validation stage is required before the run becomes `SUCCEEDED`; `PARTIAL` is not a job or run outcome.

## Backpressure and safe reads

The local worker pump has bounded worker count, jobs per pump, active-per-run and active-per-source configuration. Job query projections expose status, IDs, lease/retry/failure/review/result metadata only; raw rows, payloads, secrets, stack traces, host paths and provider-native queue objects are not exposed. Step27 backend routes remain the transport adapter.

## SQLite schema and artifacts

The control-store schema is version 5 with an explicit v4-to-v5 migration for `execution_plans` and `jobs`; existing run data survives migration and reopen. Command deduplication, plan hash verification, job claims, attempts, leases, cancellation and finalization are durable in this same database. Artifact publication uses the existing managed content-addressed store and attempt-scoped references; incomplete/unregistered/hash-mismatched output is non-consumable and fails closed.

## Failure-injection evidence

The Step28 integration suite includes deterministic worker-crash cases after claim, after stage-attempt creation, after artifact publication and before stage finalization. Reopen plus lease expiry recovers the durable job. A stale generation is rejected. Unregistered output becomes `ARTIFACT_INTEGRITY_FAILED`; a raised handler outcome becomes safe `UNKNOWN_SIDE_EFFECT` without raw exception text. These are local deterministic boundary tests, not a production crash-durability or external-side-effect certification.

## Validation evidence

- Step28 validator: `tools/validate_step28_job_processing.py`, `10` scenarios, PASS.
- Focused cross-step suite: `127 passed`.
- Full feasible regression: `408 passed, 2 skipped, 41 warnings`; the skips are unavailable optional Splink and Valentine runtimes.
- All repository validators: `28/28 PASS`.
- `compileall`, YAML/JSON/OpenAPI checks and `git diff --check`: PASS where applicable.
- The broad run retains the known non-fatal dlt/SQLAlchemy closed-cursor traceback after pytest cleanup.

## Gate and handoff

`G5=PASS`, `G6=PASS`, `G7A=PASS`, `G7B=PASS`, `G7_END_TO_END_PRODUCT=PENDING`, `blocked=false`. Step29 frontend work is not started. Content implementation commits are `578bd4458def77608e36aba25709de8e5f8f43b1`, fault-boundary repair `98555bbf2ac9d80b8274197f7e470c5b22cfc3c5`, and handoff-validator compatibility `37cf6ec0690f176258560f0ddd83c1f90e211854`. The metadata receipt/state/log commit is recorded after this document is committed.

## Known limitations

This is a local SQLite reference runtime, not a broker-backed or multi-node production queue. No external provider, production authentication, HA/failover, capacity benchmark, deployment, frontend/browser acceptance, full observability, or G7 product acceptance is claimed. Handlers beyond explicit registry wiring remain unavailable and therefore block. Unknown external side effects require reconciliation rather than blind retry. Optional Splink/Valentine runtimes remain unavailable in this environment.
