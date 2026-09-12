# CRITICAL POST-STEP23 REPAIR

## Platform Identity, Cleanup Authorization and Gate Receipt Integrity Closure

This document records a surgical Step23 repair. It is not Primary Prompt
24/41, and Step24 was not started.

## GOAL RESULT

PASS for the requested local platform integrity closure. The original Step23
report remains preserved at `docs/execution/STEP23_DATA_PLATFORM_REVIEW.md`.

The repair closes three trust boundaries:

1. cleanup authorization is bound to the exact deterministic plan and exact
   per-artifact deletion context;
2. staged dataset identity is run/source/snapshot/table/version/schema scoped
   and its manifest is closed before persistence;
3. G6 gate evidence is derived only from the exact registered and verified
   typed Step22 `ValidationReport`.

No source system, raw source data, or accepted Step22 artifact was modified.

## REPOSITORY BASELINE

- starting branch: `main`
- starting `HEAD` and `origin/main`:
  `54b2de70a583be7e4967b1ba37f6e5b772d6da45`
- starting state had only the expected untracked protected path:
  `tests/quality_unit_artifacts/`
- protected path remained untouched, un-staged and uncommitted
- existing Step23 content and handoff history were preserved

## CLEANUP PLAN IDENTITY

`CleanupPlan.content_hash` is computed from canonical semantic content:
schema version, plan ID, run scope, policy reference, dry-run mode, sorted
candidate IDs, expected content hashes, sizes, retention/action fields and
normalized dependent IDs. Candidate order and volatile `created_at` do not
change the hash. A mode change or candidate-set mutation changes the hash.

## CLEANUP AUTHORIZATION BINDING

`CleanupAuthorization` carries `plan_content_hash` and execution requires
authorization for both the same plan ID and the same semantic hash. The
service mints a `CleanupDeletionPermit` for each candidate with the plan hash,
run scope, artifact ID, expected hash/size, retention class, action and exact
dependent set.

## DESTRUCTIVE ACTION SAFETY

Before deletion the service resolves the exact artifact reference from the
configured managed or staging store, checks run scope, metadata, retention,
pin/dependent/active-run protections and verifies bytes. The adapter verifies
the permit again and refuses generic authorization objects, mismatched hashes,
wrong runs, external artifacts and tampered bytes. Successful managed deletion
creates a tombstone and removes only an unshared blob.

## STAGED DATASET IDENTITY

Staged part artifact IDs include run, source, snapshot, table, dataset,
version, part and schema. Logical keys are canonical POSIX keys containing the
same scope. Same dataset/version/part names in different runs therefore have
different logical identities even when their physical bytes are identical.

## CROSS-RUN STAGING ISOLATION

SQLite now stores staged manifests under the composite primary key
`(run_id, dataset_id, dataset_version)`, and `get_staged_dataset` requires the
run ID. Cross-run part injection and source/table scope substitution are
rejected before persistence. Same-name staged manifests for two runs were
persisted and independently recovered in the behavioral validator.

## STAGED MANIFEST CLOSURE

Manifest registration revalidates exact part IDs, logical keys, artifact IDs,
run, `STAGED_DATASET_PART` kind, `PUBLISHED` state, `MANAGED` storage,
schema fingerprint and known row-count reconciliation. It also compares the
control-store reference with the artifact-store reference and verifies bytes.

## SQLITE SCHEMA MIGRATION

The control store is schema version `3`. Forward migration from the prior V1
layout preserves data, converts the staged primary key to run scope, adds
separate gate report run/report fields, marks legacy staged rows as
`legacy-v1`, and marks legacy gate rows with
`legacy:gate-evidence-unverified`. Newer unsupported schemas still fail closed
without reset.

## GATE EVIDENCE BINDING

G6 persistence requires the exact registered artifact reference, exact
`ValidationReport` kind, published state, artifact-store metadata equality,
verified bytes and a byte-for-byte typed report match. The report's own run ID
and report ID are stored separately from the platform run ID. Wrong artifact
kind, stale/mismatched references, invalid payload bytes and status laundering
are rejected.

## VALIDATION REPORT DERIVATION

Gate status, eligibility and policy version are derived from
`ValidationReport.g6_status`, `g6_eligible` and `policy.policy_version`.
`no_blocking_discrepancy` is not a gate authority. Typed FAIL and PENDING
reports persist as FAIL/PENDING and cannot be promoted to PASS.

## G6 RESTART RECOVERY

The current Step22 report remains G6 `PASS` and eligible. Its exact external
file bytes are re-hashed, parsed as `ValidationReport`, registered and bound
to the G6 receipt. A fresh SQLite adapter recovered the platform run, stage
attempt, dependencies, cache and gate receipt without recomputing G6 from
platform output. The accepted Step20 DuckDB remains an external controlled
reference and was not copied or mutated.

## EXTERNAL ARTIFACT LOCATOR BINDING

External registration resolves a safe project-relative locator and requires
the call argument to equal `manifest.external_locator`. Alternate safe files,
traversal, absolute paths, drive-qualified paths, symlinks into protected
areas and missing files remain rejected.

## CACHE / DEPENDENCY REGRESSION

The Step23 validator continued to verify deterministic cache keys, exact input
hashes, policy/schema invalidation, missing/corrupt output invalidation and
stale dependency detection. Cleanup and identity changes did not weaken these
checks.

## RESOURCE BUDGET REGRESSION

The bounded local disk and artifact publication checks remain active. Known
size over-quota publication fails closed, and the validator continued to
exercise the configured local resource budget.

## SECURITY NEGATIVE CASES

The focused security suite and validator cover generic authorization rejection,
plan mutation, candidate injection, cross-run deletion, active/pinned/
dependent protection, permit hash/size mismatch, tampered bytes, external
locator mismatch, protected path escape, staged scope/kind/publication/schema/
row-count mismatch, wrong gate artifact kind, typed status laundering and
future-schema rejection.

## TEST AND VALIDATOR EVIDENCE

- focused Step23 unit/contract/integration/architecture/security suites:
  `27 passed`
- full regression: `316 passed, 2 skipped, 41 warnings`
- skipped tests are the optional Valentine and Splink runtimes, unavailable in
  this environment
- compileall: `PASS`
- `git diff --check`: `PASS`
- repository validators: all `23/23 PASS`
- Step23 behavioral validator: `50 checks PASS`

## EXECUTION STATE

- `last_completed_step=23`
- `last_completed_role=data_platform_engineer`
- `current_step=24`
- `current_role=distributed_data_engineer`
- `step24_status=NOT_STARTED`
- G5: `PASS`
- G6: `PASS`
- G7: `PENDING`

## GIT

- content commit:
  `496ca80647a7a5b752803dab6d50491f5663e4a7`
- content message: `fix: close Step23 platform integrity gaps`
- metadata commit: final metadata-only closure commit recorded in Git
- no force push, reset, rebase or tag operation
- protected untracked path remains outside the commit

## KNOWN LIMITATIONS

This closure verifies the local SQLite/filesystem adapter and one-process
reopen model. It does not claim PostgreSQL, S3, HA, multi-node locking,
cross-process publication fencing, Kubernetes, queues, workers or distributed
execution. Those remain Step24/future boundaries. The optional Valentine and
Splink runtimes were not installed, so their tests remain skips.
