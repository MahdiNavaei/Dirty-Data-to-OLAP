# Final Step28 Authority and Materialization Checkpoint Closure

## Result

`PASS` - final surgical Step28 closure. Step29 remains not started.

- starting HEAD: `43026cc0ff5dc2dd04a77bd2fd0a81ba9e127f96`
- prior Step28 content commit: `6242fb094f8dce435b3d545ef6b5dac362625166`
- current control schema: `6`
- content commit: `684856cf36f8398ac3c4146b27b8453fe09715b1`
- protected `tests/quality_unit_artifacts/` was not read, used, staged or committed
- all three earlier Step28 receipts were preserved unchanged:
  `STEP28_DISTRIBUTED_JOB_PROCESSING_REVIEW.md`,
  `STEP28_DISTRIBUTED_JOB_PROCESSING_INTEGRITY_REPAIR.md`, and
  `STEP28_EXECUTION_PLAN_AND_REVIEW_CHECKPOINT_REPAIR.md`

## Server-owned execution-plan authority

The public preparation request accepts only bounded `ExecutionPlanIntent`
booleans. Client-supplied stage selections, policy references, evidence
references, scope values and fingerprints are not authority inputs and are
rejected when sent as extra fields.

`TrustedExecutionPlanningState` derives planning facts only from exact
registered, published and independently verified typed artifacts in the
current run: `SourceCatalog`, `SourceSnapshotResult` and
`CanonicalModelHypothesis`. Missing, cross-run, mutated or conflicting inputs
produce typed unresolved stages and a `BLOCKED` preparation result. The server
resolver owns the policy reference, conditional-stage decisions, reasons,
evidence references, scope and scope fingerprint.

The executed cases prove that:

- trusted multi-source scope selects `SCHEMA_MATCHING` even when the client
  requests false;
- an `ER_REQUIRED` canonical hypothesis selects `ENTITY_RESOLUTION` even when
  the client requests false;
- a trusted single-source, `ER_NOT_REQUIRED` run permits the bounded optional
  exclusions; and
- missing planning artifacts and unknown state fail closed without guessing.

The authoritative graph remains `docs/architecture/specs/stage_graph.yml`.
Its conditional dependencies are readiness-gated by the persisted run plan;
an explicitly unselected dependency is not required, while a selected stage
that has not succeeded is not treated as ready.

## Compilation and materialization authority

Compilation now has one project-owned publication boundary for the exact
run/attempt: `CompiledPlan`, `GeneratedSQL` and `TargetConfig`. The publisher
checks the generated-SQL semantic hash against `GeneratedSQL` and the target
configuration fingerprint against `TargetConfig`, then publishes and registers
the exact bytes with stage and attempt binding.

Materialization review derives its context only when all of these bindings
hold: the exact registered published artifacts are independently transport-
verified; compiled-plan and generated-SQL IDs and semantic hashes agree; the
three artifacts share run, stage and attempt identity; and the compiled target
fingerprint agrees with the exact `TargetConfig`. The existing
`ReviewPolicyService.materialization_context()` remains the semantic review
authority; artifact-store SHA-256 verification remains the transport
integrity check.

The end-to-end test publishes real typed compilation artifacts, pauses the
worker at `REVIEW_MATERIALIZATION_PLAN` with no materialization handler call,
accepts the review through the FastAPI product path, resumes durably with a
new attempt, and verifies that the materializer receives the bound
`TargetConfig`. Missing target, wrong target fingerprint, wrong generated SQL,
wrong compiled plan, cross-run target, and mutated compiled bytes all produce
no review context and no materialization authorization.

## Verification

- Step28 validator: `49` behavioral scenarios plus `1` documentation check PASS
- new authority/materialization integration coverage: `5 passed`
- complete Step28 focused suite: `42 passed`
- Step20 contract/runtime/security suite: `18 passed`
- Step27/Step23 API, architecture and security suite: `27 passed`
- full regression under Python 3.10: `450 passed, 2 skipped, 41 warnings`
- optional skips: official Splink and Valentine integrations are not installed
  in the clean environment
- all repository validators: `28/28 PASS`
- compileall for source and tools: PASS
- `git diff --check`: PASS

The full pytest run also emitted the existing non-fatal dlt/SQLite cursor
cleanup traceback after test completion; pytest exited successfully and no
assertion failed.

## Gates and limitations

`G5=PASS`, `G6=PASS`, `G7A=PASS`, `G7B=PASS`, `G7=PENDING`, and
`blocked=false`. This closure establishes the local SQLite/filesystem
reference path only. It does not claim broker/HA/multi-node execution,
production authentication, frontend/browser usability, deployment,
observability completion, physical distributed execution, production
capacity, or G7 product completion. Unknown external side effects still
require reconciliation.

Final state is `last_completed_step=28` with role
`distributed_job_processing_engineer`; `current_step=29`,
`current_role=frontend_engineer`, `step29_started=false`, and
`step29_status=NOT_STARTED`.
