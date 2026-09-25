# Prompt02-R Architecture Recovery Plan

Status: `HISTORICAL PHASE_B SNAPSHOT — PROMPT02 ACCEPTANCE LATER PASSED`

This document preserves the earlier recovery plan and its historical
limitations. The later accepted Prompt02 result is bound to commit
`7786ea301e3482b374e57447974b73d9a0176f8c` and run `36081128931`; current
Prompt03-branch rerun status is tracked in `07_PRODUCT_ACCEPTANCE_STATE.yml`.

Starting committed HEAD: `eb6f9aa81bacabf5b2c05e59fdcf3d4e28e0c304`

The migration was completed in the current local worktree but is not yet a
product acceptance result. The worktree is intentionally uncommitted while the
required heterogeneous provider run remains unavailable.

## Historical root cause

The pre-recovery Prompt02 path was a bespoke vertical slice. It called real
providers, but `_stage()` fabricated success envelopes, `_review()` created
accepted decisions in memory, and `_materialize()` directly constructed a
DuckDB target. That path was not authoritative product execution.

## Current architecture

`build_multi_source_product()` now composes the accepted local platform,
`ExecutionPlanService`, `BackendService`, `MultiSourceProductRuntime`,
`JobWorker`, `BoundedWorkerPool`, `StageHandlerRegistry`, durable control and
artifact stores, and the shared review/planner/compiler/materializer/semantic/
validation services.

The Prompt02-specific code is split between reusable source-role/provider
helpers and `MultiSourceStageHandlers`. The handlers publish typed artifacts;
the worker owns attempts, leases, result recording, review checkpoints and
plan advancement. The server-owned plan selects `SCHEMA_MATCHING` and
`ENTITY_RESOLUTION` from trusted source-set and canonical evidence.

Review checkpoint stages are handled by the existing worker review boundary;
they are not auto-accepted by the multi-source handler. Analytical planning,
compilation, materialization and G6 validation use the existing accepted
service chain. The application does not construct DuckDB tables itself.

## Boundary status

| Boundary | Current status | Evidence boundary |
|---|---|---|
| Source registration and immutable source-set binding | Implemented through `ProductSourceService` and `BackendService` | Local code inspection; live run pending |
| Server-owned planning | Implemented through `ExecutionPlanService` and trusted selection resolution | Architecture test and source inspection |
| Stage registration and dispatch | Multi-source handlers registered for every selected non-review product stage | `4 passed` architecture-equivalence test |
| Durable attempts, jobs, leases and artifacts | Owned by shared `JobWorker`/platform path | Not executed end-to-end locally |
| Durable review | Existing review subject derivation and `BackendService.review`/`resume` path | Harness is wired; live evidence pending |
| Cross-source profiling, dependency, quality and schema matching | Source-scoped provider calls in registered handlers | Provider run pending; Desbordante unavailable locally |
| Conditional ER | Splink authorization and execution are handler-owned and review-gated | Live evidence pending |
| Canonical identity and accounting | Role-aware registry/event mapping and multi-snapshot truth contracts | Contract/unit checks; live G6 pending |
| Analytical planning and typed compilation | Shared planner/compiler and durable output publication | Live evidence pending |
| DuckDB materialization | Shared `MaterializationService(DuckDBMaterializer)` | Live evidence pending |
| Semantic and G6 validation | Shared semantic stage and `ValidationService` with multi-source snapshot map | Contract/unit checks; live evidence pending |
| Independent oracle | Loaded only by acceptance test after the run | No completed run to compare |
| Negative controls | Stale review and source-set mutation exercise real APIs in the harness | Not executed because live estate is absent |

## Retained and migrated work

- Source-set contracts, fingerprints, read-only adapters and credential
  boundaries were retained.
- CRM/ERP registry roles are distinct from Sales/CSV event roles.
- The fixture now includes a same-name/different-contact hard negative,
  unresolved event references, duplicate ERP email candidate evidence, and a
  null-marked orphan event value.
- `SourceTruthManifest` preserves a source-to-snapshot map instead of
  collapsing independent source snapshots into one fictitious snapshot.
- Record accounting covers source-to-canonical and canonical-to-analytical
  boundaries with explicit event quarantine for unresolved customer links.
- The oracle remains test-only and is not imported by application code.

## Validation ladder result

Passed locally:

- `python -m compileall -q src tests`
- `python -m pytest -q tests/architecture/test_prompt02_architecture_equivalence.py` — `4 passed`
- Prompt02/source/canonical/analytical contract and policy tests — `19 passed`
- `git diff --check`

The acceptance module collects with `python -m pytest`, but skips because the
required Prompt02 database URLs are not configured. The local Step29 focused
regression was also run and did not complete: the observed run reached
`DEPENDENCY_DISCOVERY=FAILED` in this environment, where the Desbordante
provider module is unavailable. This is retained as a regression limitation,
not upgraded to a Prompt02 result.

## Remaining blockers

- Run the real disposable PostgreSQL CRM, MySQL/MariaDB ERP, SQL Server Sales,
  and CSV estate with the required provider runtime, including Desbordante.
- Verify durable stage attempts, review records, artifact hashes, canonical
  memberships, target rows and G6 through the live acceptance harness.
- Execute the real negative controls and retain aggregate-only evidence.
- Re-run the existing Step29 focused path in a provider-capable environment.
- Only after those checks may a reviewed recovery commit and bounded CI attempt
  be considered.

At the time of this historical plan Prompt02 remained `BLOCKED`. That statement
is superseded by the accepted Prompt02 evidence above. Prompt03 is now the
current bounded scope; Prompt04 and Step42 remain `NOT_STARTED`.
