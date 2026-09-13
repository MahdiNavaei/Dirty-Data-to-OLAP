# Final Step28 Fresh-Run Bootstrap and Project-Owner Closure

- execution_step: `28`
- role_id: `distributed_job_processing_engineer`
- status: `PASS`
- starting_head: `f718a4744785c9e56d5c91665eabd7a0a82a0d7e`
- latest_prior_step28_content: `684856cf36f8398ac3c4146b27b8453fe09715b1`
- content_commit: `af80a16add349988b0678f9bea1b0f61d56a87b2`
- protected_path: `tests/quality_unit_artifacts/` was not read, modified, used as evidence, or staged
- step29_started: `false`

## Root cause

`ExecutionPlanService.prepare()` previously required `SourceCatalog`/
`SourceSnapshotResult` and `CanonicalModelHypothesis` before it could create a
plan, although those artifacts are produced by later stages of the same run.
Product-path tests hid the cycle by publishing future-stage artifacts before
plan preparation. That was not a valid fresh-run architecture.

## Architecture chosen

The existing SQLite v6 execution-plan row now persists a phased lifecycle; no
schema migration was needed.

1. `BOOTSTRAP` contains only `SOURCE_DISCOVERY` and is created from bounded
   `ExecutionPlanIntent`.
2. After a succeeded `SOURCE_DISCOVERY` attempt publishes source truth, the
   same stable plan identity is compare-and-swapped to `SOURCE_RESOLVED`.
   Server-owned source scope selects `SCHEMA_MATCHING` for multi-source scope
   and permits explicit exclusion for single-source scope. The source/evidence
   prefix then runs through its real evidence review and
   `CANONICAL_HYPOTHESES` stage.
3. After a succeeded `CANONICAL_HYPOTHESES` attempt publishes a valid typed
   hypothesis, the same plan identity is compare-and-swapped to `COMPLETE`.
   Server-owned entity-family requirements select or exclude
   `ENTITY_RESOLUTION` and retain the existing downstream conditional guards.

Planning artifacts are eligible only when attached to the succeeded owning
stage job and its attempt result set, with exact publication and transport
integrity. Missing, corrupt, conflicting or stale truth remains unresolved;
it never becomes `false`. SQLite CAS rejects a stale plan writer.

## Pre-execution and runtime authority boundary

The public API supplies only bounded `ExecutionPlanIntent`. It does not accept
authoritative stage decisions, policy references, evidence references or scope
fingerprints. Runtime-derived source scope, evidence-review subjects and
canonical hypotheses are produced by project-owned stage handlers and are
resolved by server policy only after their owning stage succeeds. The frontend
does not manufacture planning authority.

## Fresh-run evidence

The product-path tests start with `POST /runs`, prepare with bounded intent,
assert an empty artifact set, submit through the API, and then run a controlled
`JobWorker` with `ExecutionPlanService` as the durable plan advancer. No
`ExecutionPlan`, `SourceCatalog`, `SourceSnapshotResult`,
`CanonicalModelHypothesis`, `StageSelectionDecision` or review context is
pre-registered in those fresh paths.

- Multi-source + `ER_REQUIRED`: `SOURCE_DISCOVERY` publishes two catalogs;
  server selection records `SCHEMA_MATCHING=true`; evidence review pauses and
  resumes through the Step27 API; `CANONICAL_HYPOTHESES` publishes the
  requirement; final selection records `ENTITY_RESOLUTION=true`, queues ER,
  and does not queue identity preparation before ER succeeds.
- Single-source + `ER_NOT_REQUIRED`: source selection records
  `SCHEMA_MATCHING=false`; the hypothesis records the not-required family;
  final selection records `ENTITY_RESOLUTION=false`; ER is absent and
  canonical identity preparation can be queued through the existing policy.
- Restart: after source truth and source-phase selection are durable, close
  and reopen the local platform before ER resolution. The selection hash and
  decision cardinality are unchanged, source discovery is not re-executed,
  and the evidence checkpoint is reached again.
- Missing source truth: a succeeded discovery job with no catalog leaves the
  bootstrap plan with pending `SCHEMA_MATCHING`/`ENTITY_RESOLUTION`, keeps the
  run `BLOCKED` without falsely failing discovery, and preserves that state
  after reopen.
- Corrupt source or canonical truth: typed planning decode fails closed;
  source corruption remains bootstrap-pending, while corrupt hypothesis truth
  leaves the source-resolved plan pending ER and blocks the run rather than
  leaving an un-runnable `RUNNING` state.
- Negative future-artifact control: manually published future artifacts with
  no succeeded owning stage job cannot bypass bootstrap.
- CAS control: a second writer using the old content hash is rejected and
  cannot overwrite the winning pending decision.

## Review, materialization and G6 regressions

The complete Step28 integration suite passed `50` tests. It retains typed
subject derivation, Step27 API-compatible review, compatible resume, shared
review subject identity, artifact integrity, lease/fencing, retry and
cancellation behavior. The real materialization checkpoint still requires the
same-run/stage/attempt `CompiledPlan`, `GeneratedSQL` and `TargetConfig`; the
materializer is not called before compatible review. The exact G6 evidence
guard remains required for run success.

Focused cross-step suites passed:

- Step20 compiler/materialization/review: `23 passed`
- Step22 validation/G6: `21 passed` serial rerun
- Step23 platform: `22 passed`
- Step24 distributed data: `31 passed`
- Step27 backend/API integrity: `18 passed`

## Project-owner self-review

The independent review traced the complete path from empty run through API,
bootstrap plan, durable submit, worker claim, source discovery, source-derived
selection, evidence review, canonical hypothesis, ER-derived selection and
the existing canonical/analytical/materialization/validation guards.

Findings repaired before closure:

- pending ER metadata was initially treated as a whole-plan block; scheduling
  is now phase-aware, so an already-authorized source/evidence prefix runs
  while a future ER branch remains pending;
- resolver trust was strengthened to require a succeeded owning stage job,
  matching attempt and result reference, preventing manually registered future
  artifacts from bypassing bootstrap;
- corrupt canonical hypothesis truth could otherwise leave a source-resolved
  run `RUNNING` with no runnable work; the owner-stage boundary now records
  `BLOCKED` while retaining the pending ER decision;
- the Step28 validator's old pre-seeded product path and synchronized
  architecture documentation/manifest were repaired.

The negative-control search found direct `register_execution_plan()` calls only
in isolated synthetic plan tests and materialization/checkpoint fixtures. The
claimed fresh product paths contain no such call and publish planning artifacts
only inside the owning runtime handler. Resolver unit tests that use unowned
fixtures explicitly opt into isolated fixture mode; that bypass is not used by
the application plan service or fresh validator path.

## Verification

- Step28 validator: `tools/validate_step28_job_processing.py` -> `75` behavioral
  scenarios and `1` documentation check, `PASS`
- repository validators: `28/28 PASS`
- full feasible regression: `458 passed, 2 skipped, 41 warnings` under Python
  3.10, with `tests/quality_unit_artifacts/` ignored
- optional official Splink and Valentine integrations: skipped because the
  optional runtimes are not installed
- compileall over `src` and `tools`: `PASS`
- YAML/JSON parse: `26` non-protected files parsed; `19` protected files were
  excluded
- OpenAPI generation and determinism: `PASS` in Step27 validator
- `git diff --check`: `PASS`

## Final state

- `last_completed_step=28`
- `last_completed_role=distributed_job_processing_engineer`
- `step28_status=COMPLETED_DURABLE_JOB_PROCESSING`
- `step28_fresh_run_bootstrap_closure=PASS`
- `current_step=29`
- `current_role=frontend_engineer`
- `step29_started=false`
- `step29_status=NOT_STARTED`
- `G5=PASS`, `G6=PASS`, `G7A=PASS`, `G7B=PASS`, `G7=PENDING`
- `blocked=false`

## Known limitations

This is a local SQLite/filesystem reference path. It does not claim broker,
HA, multi-node, production authentication, frontend/browser usability,
deployment, full observability, production capacity, physical distributed
execution or G7 completion. Unknown external side effects still require
reconciliation. The existing dlt/SQLite cursor-cleanup traceback remains a
non-fatal post-test warning.
