# Step29 Real-Pipeline G7 Integrity Closure

Status: `PASS` after the repaired real browser run.

This is a surgical post-Step29 repair. Step30 was not started. The previous
Step29/G7 PASS is superseded by this receipt because the earlier product
runtime used a parallel miniature implementation for downstream stages.

## Scope and baseline

- Starting repository baseline: `d05092d6285c3b1fabcab435c7dff93b17e8e3bd`.
- Product scope: the existing managed CSV upload, source binding, server-owned
  execution plan, durable worker, four review checkpoints, output projection
  and browser gate.
- `tests/quality_unit_artifacts/` remained unread, untouched, unstaged and
  uncommitted.
- No Step30 implementation, deployment, tag, rebase or force push was used.

## Root cause and repair

`LocalProductStageHandlers` had directly reread CSV rows and constructed a
second profiling, quality, dependency, fusion, analytical, materialization and
validation path. That could produce a successful-looking product result
without proving that the accepted typed services and providers owned the run.

The runtime is now a composition root over the existing boundaries:

- source discovery and snapshot extraction use the managed source and staging
  services;
- `ProfilingService` uses `DataProfilerAdapter` against the actual snapshot;
- `QualityAnalysisService` uses `ParquetQualityStagedReader` and the actual
  profile, snapshot and policy rules;
- `DependencyDiscoveryService` uses the privacy-bound
  `DesbordanteDependencyAdapter` and actual snapshot/profile inputs;
- `EvidenceFusionService` receives the typed producer results and constructs
  the review-only decisions;
- canonical services, `AnalyticalPlannerService`,
  `AnalyticalCompilerService`/publisher, `MaterializationService` with
  `DuckDBMaterializer`, `SemanticLayerService` and `ValidationService` own
  their corresponding stages;
- analytical input is a typed dataset/binding read from the immutable staged
  snapshot, and G6 uses `SourceTruthManifest`, typed accounting and the
  `DuckDBValidationTargetReader`.

The single-source product policy is explicit: the provider-observed unique
`order_id` key is projected as source-local event identity. It is not claimed
as a cross-table inclusion dependency, and no source values or relationship
decision are fabricated by the product runtime.

## Executed evidence

`tools/validate_step29_frontend.py` passed all of the following:

- OpenAPI generation and Python compilation;
- architecture regression proving the runtime contains no CSV reread,
  DuckDB connection or direct downstream decision construction;
- real-provider integration tests with typed artifact/provenance assertions;
- frontend typecheck, lint, tests and build;
- a fresh Playwright CLI browser session against the local FastAPI API and Vite
  preview.

The integration suite passed `3` tests. The happy path observed four source
rows, four accepted checkpoints in order, three materialized tables with four
rows each, a typed semantic validation PASS and an eligible typed G6 PASS.
The provenance assertions verified actual profile, dependency, quality,
fusion, canonical, analytical, compiler, materialization, semantic and
validation artifacts, including their run-bound hashes and producer metadata.

The full regression passed `461` tests with `2` optional-provider skips and
`41` warnings under Python 3.10 using `--ignore=tests/quality_unit_artifacts`.
The skips are the already-optional official Splink and Valentine runtimes; the
known dlt/SQLite cursor-cleanup traceback remains non-fatal and occurs after
pytest completion.

The browser run uploaded the existing fixture through the UI, created and
bound a run, accepted all four review checkpoints, and ended with visible
`SUCCEEDED`, `G6 PASS - eligible` and `Validated OLAP output available`.
Browser console errors were `0`; no raw SQL, source-row values, file locator,
target path or direct SQLite/DuckDB request was exposed.

## Negative control

A duplicate `order_id` upload was accepted by the source-import boundary, then
failed at the real dependency stage with `DEPENDENCY_INCOMPLETE` because the
provider no longer observed the required source-local unique key. This proves
the failure is downstream of upload validation and fail-closed; the product
does not fabricate an identity candidate to force planning or G6 to pass.

## Limitations

The evidence is local and bounded: SQLite/filesystem control state, a local
DuckDB target and the locally provisioned Desbordante Docker provider. It does
not claim production authentication, broker/HA or multi-node execution,
capacity, observability, deployment, physical cross-source integration or
release readiness. Optional external provider integrations remain outside this
Step29 product path.
