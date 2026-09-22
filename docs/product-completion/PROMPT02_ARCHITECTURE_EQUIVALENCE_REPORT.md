# Prompt02-R Architecture Equivalence Report

Status: `PARTIAL — STATIC/WIRING PASS, BEHAVIORAL ACCEPTANCE NOT EXECUTED`

Baseline committed HEAD: `eb6f9aa81bacabf5b2c05e59fdcf3d4e28e0c304`

## Equivalence gate

| Gate | Current finding | Evidence |
|---|---|---|
| A. Accepted control plane | `PASS_BOUNDED`: builder returns platform/backend/runtime and harness uses create/bind/submit | Source inspection and architecture test |
| B. Server-owned planner | `PASS_BOUNDED`: `ExecutionPlanService` remains the planning authority | Source inspection |
| C. Registered handlers | `PASS_BOUNDED`: multi-source registry covers selected product stages; review stages remain worker-owned | `4 passed` architecture test |
| D. Durable attempts | `WIRED_NOT_EXECUTED`: `JobWorker` owns execution and finalization | No live run |
| E. Durable artifacts | `WIRED_NOT_EXECUTED`: handlers publish through the platform artifact store | No live run |
| F. Artifact integrity | `WIRED_NOT_EXECUTED`: shared worker/output verification remains in force | No live run |
| G. Durable review | `WIRED_NOT_EXECUTED`: harness submits decisions through `BackendService.review` | No live run |
| H. Canonical review compatibility | `WIRED_NOT_EXECUTED`: canonical finalization consumes the accepted persisted checkpoint | No live run |
| I. Analytical planner | `WIRED_NOT_EXECUTED`: `AnalyticalPlannerService.build_plan` is called | No live run |
| J. Typed compiler | `WIRED_NOT_EXECUTED`: shared compiler and `CompilationArtifactPublisher` are called | No live run |
| K. Accepted materializer | `WIRED_NOT_EXECUTED`: shared `MaterializationService` and `DuckDBMaterializer` are called | No live run |
| L. G6 validation | `WIRED_NOT_EXECUTED`: shared `ValidationService` and target reader are called | No live run |
| M. Single-source regression | `FAIL_CURRENT_LOCAL_RUN`: focused Step29 run reached dependency-stage failure in the current provider-less environment | `tests/integration/test_step29_product_path.py` |

## Eliminated bypasses

The current `multi_source_product.py` contains reusable source-role/provider
helpers only. It no longer owns `_stage`, `_review`, `_materialize`, an
in-memory resume lifecycle, a bespoke receipt authority, or a direct DuckDB
connection. `multi_source_runtime.py` enters through the shared worker and
publishes typed outputs. Direct DuckDB access remains only in the independent
acceptance test, where it inspects the actual materialized target.

The current application materialization call is the accepted chain:

`reviewed plan -> AnalyticalPlannerService -> AnalyticalCompilerService ->
MaterializationService(DuckDBMaterializer) -> semantic stage -> ValidationService`.

## Remaining proof gap

The four-source harness is architecturally wired but has not produced a
durable run in this checkout. No provider-unavailable skip, static assertion,
or generated metadata is treated as acceptance evidence. Prompt02 therefore
cannot be declared PASS from the current local results or from the historical
CI failure/success chain.
