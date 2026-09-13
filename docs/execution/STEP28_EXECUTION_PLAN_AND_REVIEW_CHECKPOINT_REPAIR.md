# Step28 Final Execution-Plan and Review-Checkpoint Wiring Repair

## Result

`PASS` — final surgical Step28 repair. Step29 remains not started.

- starting HEAD: `82d71017d61111f1bf0bd0a77b996c7c27418b92`
- prior Step28 integrity content: `a41f2108951a4167684c8e3da2c8d86ffba0b725`
- current control schema: `6`
- content commit: `6242fb094f8dce435b3d545ef6b5dac362625166`
- protected `tests/quality_unit_artifacts/` was not read, used, staged or committed
- existing receipts preserved: `STEP28_DISTRIBUTED_JOB_PROCESSING_REVIEW.md` and `STEP28_DISTRIBUTED_JOB_PROCESSING_INTEGRITY_REPAIR.md`

## Real review-checkpoint evidence

The integration suite now executes a real control-plane checkpoint path:

`EVIDENCE_FUSION` succeeds and publishes a typed `RelationshipDecision` →
`REVIEW_EVIDENCE_DECISIONS` derives its context through
`ReviewPolicyService.evidence_context()` → the checkpoint reaches
`NEEDS_REVIEW` and the guarded `CANONICAL_HYPOTHESES` job is not queued →
the Step27 FastAPI review endpoint records `ACCEPTED` → a durable `RESUME`
command validates the exact persisted context and artifact integrity → the
checkpoint succeeds and `CANONICAL_HYPOTHESES` becomes eligible.

The same behavior is exercised for a typed `AnalyticalPlan` at
`REVIEW_ANALYTICAL_PLAN`. The tests also prove that two evidence subjects are
stored as two explicit review contexts; they are never collapsed into a fake
aggregate context. Review checkpoint stages do not invoke a provider handler.

`review_subject_key()` remains the single shared identity for the current
review decision and resume authorization. Builder semantic hashes and raw
artifact transport SHA-256 hashes are kept distinct: the resolver calls the
existing semantic builder, while the artifact store independently verifies the
registered published bytes before review or resume.

## Project-owned boundaries

- `ReviewSubjectDerivationPort` and `ReviewCheckpointSubjectResolver` accept
  trusted upstream `ArtifactRef` values, verify the exact registered and
  published artifact, decode the typed project contract, and call the existing
  evidence, canonical identity, analytical-plan or materialization builder.
- `ExecutionPlanSelection` and `StageSelectionDecision` provide explicit
  run-bound policy/evidence, reason, scope and fingerprint for every
  conditional stage. Missing or unknown decisions fail closed; required
  conditional stages cannot be excluded.
- `ExecutionPlanService` loads the authoritative stage graph, validates the
  typed selection and persists an immutable run plan through `ControlStorePort`.
  The public preparation boundary is
  `POST /api/v1/runs/{run_id}/execution/prepare`. Submit without a prepared
  plan returns typed `BLOCKED` and does not guess a plan.
- Selected conditional dependencies require an existing `SUCCEEDED` stage
  job. An explicitly unselected dependency is ignored, while a selected but
  not-yet-enqueued dependency is not treated as ready.
- `ExecutionPlan` rejects unselected hard dependencies, unselected required
  review guards and success-guard plans without a selected final validation
  stage.

## Verification

- real plan/review wiring tests: `6 passed`
- Step28 job-processing and integrity tests: `31 passed`
- Step27 API/integrity tests: `15 passed`
- cross-step focused suite: `129 passed`
- full feasible regression under Python 3.10: `445 passed, 2 skipped, 41 warnings`
- skipped optional runtimes: official Splink and Valentine integrations were
  not installed in the clean environment
- repository validators: `28/28 PASS`
- Step27 validator: `67` scenarios PASS
- Step28 validator: `49` behavioral scenarios PASS and `1` documentation check
- compileall for `src`, `tools` and the new integration test: PASS
- YAML/JSON parse: `3371` non-protected files PASS
- OpenAPI determinism and preparation route presence: PASS
- `git diff --check`: PASS

## Gates and limitations

`G5=PASS`, `G6=PASS`, `G7A=PASS`, `G7B=PASS`, `G7=PENDING`, and
`blocked=false`. G6 remains the typed `GateEvidence` plus verified
`ValidationReport` authority; Step28 does not recompute it.

This closes the local SQLite/filesystem reference path only. It does not claim
broker/HA/multi-node execution, production authentication, frontend/browser
usability, deployment, observability completion or G7 product completion.
External handler outcomes that are unknown still require reconciliation, and
the optional Splink/Valentine runtimes remain unexecuted in this environment.

Final state remains `last_completed_step=28` with role
`distributed_job_processing_engineer`; `current_step=29`,
`current_role=frontend_engineer`, `step29_started=false`, and
`step29_status=NOT_STARTED`.
