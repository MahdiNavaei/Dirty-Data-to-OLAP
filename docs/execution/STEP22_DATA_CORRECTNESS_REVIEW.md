# Step22 — Data Correctness Review

Status: `IMPLEMENTED_BOUNDARY_BLOCKED_G6_PENDING`

Date: `2026-09-12`

Starting HEAD: `d16b86e4e6d28d0853945723ed4e27c488d9887e`

Step21 content remained at `e8933a5b07199acbd5ef6cf26c037375210f39b6`.  The
protected untracked `tests/quality_unit_artifacts/` directory was not edited,
deleted, staged or moved. Step23 was not implemented or started.

## Scope and boundary

Step22 adds the project-owned `application.validation` boundary, validation
contracts, a read-only DuckDB target adapter, an independent source/domain
truth package, deterministic reconciliation policy, and the executable
validator `tools/validate_step22_data_correctness.py`.

The application service accepts typed `SourceTruthManifest`, canonical,
accounting, analytical, materialization and semantic artifacts plus exact
hash bindings. It never imports DuckDB and never accepts caller SQL. The
adapter opens only the bound `.duckdb` target in read-only mode, checks its
SHA-256 before and after inspection, and reads only the reviewed table
allowlist using generated safe identifiers.

The source truth is an independent JSON oracle under
`benchmarks/validation/`; it is loaded by the QA harness only and is not a
runtime transformation input. Same-target semantic results are therefore not
treated as source truth.

## Required checks

The report contract distinguishes `PASS`, `FAIL`, `REVIEW_REQUIRED`,
`NOT_APPLICABLE` and `NOT_EVALUATED`, and distinguishes `G6_BLOCKING`,
`WARNING` and `INFORMATIONAL` severity. Required `NOT_EVALUATED` checks keep
G6 ineligible. The implementation covers:

- source snapshot universe and schema fingerprints;
- exact source record accounting with one terminal disposition per input;
- canonical membership and deduplication explainability;
- canonical entity/event/relationship checks;
- fact count, exact business values, grain and duplicate grain;
- dimension warehouse/alternate-key uniqueness;
- required foreign-key and orphan policy;
- date coverage and date-role resolution;
- global and sliced measure reconciliation;
- source-to-target and target-to-source lineage;
- downstream semantic measure/aggregation cross-check;
- exact artifact IDs, hashes, target path/configuration and policy binding;
- explicit `NOT_APPLICABLE` monetary/revenue status because no contract
  establishes currency, amount, settlement or revenue authority.

## Reference runs

Both reference runs use the existing Step20 planner/compiler/materializer and
the existing Step21 semantic projection, then run Step22 against the
independent truth package:

| Run | Truth records | Checks | PASS | FAIL | REVIEW_REQUIRED | NOT_EVALUATED | G6 |
|---|---:|---:|---:|---:|---:|---:|---|
| retail | 11 | 21 | 16 | 0 | 1 | 3 | `PENDING` |
| generic Device/Location/Reading | 7 | 21 | 16 | 0 | 1 | 3 | `PENDING` |

Retail target evidence includes fact count `3`, exact `(order_event_id,
line_sequence)` values, quantity `6`, product slices `5/1`, branch slices
`3/3`, zero required orphans and bidirectional source lineage for all
contributing records. Generic evidence includes three reading facts and the
reviewed semi-additive temperature `MAX` aggregates.

## G6 blocker

The current bound Step20 `CanonicalModel` is a typed reviewed model with
`instances=()` and `source_record_maps=()`. It therefore cannot independently
prove source-to-canonical membership, canonical entity counts, canonical event
membership or legitimate deduplication. Those three required checks are
`NOT_EVALUATED`, and the derived no-blocking-discrepancy check is
`REVIEW_REQUIRED`; G6 is consequently `PENDING` and not promoted.

This is an upstream evidence limitation, not a target row-count assertion.
The validation boundary does not copy the target into a golden source or infer
canonical membership from same-target results. A later canonical artifact must
provide source snapshot-bound `CanonicalEntityInstance` and
`SourceRecordCanonicalMap` evidence before G6 can be reconsidered.

## Negative controls

The executable validator detects all controls below without changing the
source or published target:

- same-total but wrong product allocation;
- same-count remove-and-duplicate grain;
- wrong but valid customer foreign key;
- quantity compensation;
- source lineage loss;
- unexplained fact filtering;
- required orphan foreign key;
- warehouse-key collision;
- stale canonical, analytical-plan and semantic bindings;
- stale target file hash.

A separate positive deduplication control accounts two source customer records
as `CONSOLIDATED` into one canonical output group. Its input count remains two,
so legitimate deduplication is not confused with source-record loss.

## Artifacts

Reference evidence is generated under:

- `workspace/runs/step22-reference-run/validation/`
- `workspace/runs/step22-generic-reference-run/validation/`

Each directory contains the versioned policy, source snapshot/truth,
artifact bindings, scoped accounting, canonical/relationship/fact/dimension/
grain/referential-integrity/aggregate/date/lineage/semantic reconciliation,
negative controls, discrepancies, report, reconciliation result, G6 gate
evidence and run manifest.

Retail truth content hash: `e6216c743d9ee964be1e9864956e0f005617238d8f4828d17a7bdd2401380cba`.

Generic truth content hash: `660dcfb60cf78eb4b0232aaa6a593fffa4ce6dfb770e709500bf6cffc61f3e5e`.

Retail target SHA-256: `88ba6c6b4f223dfee9d49d8f5524dd50921a6e8ba85ecfcc4040aa5865bea794`.

Generic target SHA-256: `daf9cc303ade7366185b007dbcd48c2f027cbc123eeacc420bed4b4c96d535df`.

## Verification

Executed after the Step22 implementation and metadata updates:

- `python tools/validate_step22_data_correctness.py` — PASS; 16 behavioral
  assertions, 21 checks for each reference run, zero discrepancies, and both
  G6 reports emitted as `PENDING`/ineligible;
- focused Step22 suites — `14 passed`;
- full regression — `282 passed, 2 skipped`; the skips are the optional
  Valentine and Splink runtimes, which are not installed in this environment;
- all repository validators — `22/22 PASS`, including engineering (`73`
  checks), Step20 (`49` checks), and Step21 (`38` checks);
- `python -m compileall -q src tools tests` — PASS;
- `git diff --check` — PASS.

Pytest reported 41 non-failing dependency/profiling warnings. The successful
source integration run also emitted a non-fatal dlt/SQLite cursor-cleanup
traceback during generator finalization; the process exit code remained zero
and no test failed.

## State and handoff

G5 remains `PASS`. G6 remains `PENDING`. `last_completed_step` remains `21`;
`current_step` remains `22` and the current role remains Data QA Engineer.
Step22 validation code is implemented, but the specialist acceptance is
blocked on the exact canonical source-membership evidence described above.
Step23 implementation is `NOT_STARTED`.
