# Step23 Data Platform Engineer Review

## GOAL RESULT

PASS for the bounded Step23 local data-platform scope. The repository now has
durable local V1 platform primitives for run metadata, stage attempts,
immutable artifacts, dependencies, staging metadata, cache metadata, cleanup,
integrity scanning, capability truthfulness and exact G6 receipt recovery.

Step24 distributed data execution was not started. G5 remains PASS, G6 remains
PASS and G7 remains PENDING. No source system was modified and no formal
Step23 gate was invented.

## REPOSITORY BASELINE

- starting branch: `main`
- starting HEAD and `origin/main`: `7442fd28b8c38fe0dd00da8f69967dbb1e162011`
- expected pre-existing untracked path: `tests/quality_unit_artifacts/`
- protected path remained unmodified, un-staged and uncommitted
- verified Step23 content commit: `a35944fda71f93cba6cdacf8a7fdee5aa565e513`

## AUTHORITATIVE INPUTS REVIEWED

The specialist routing, execution protocol, shared invariants, master build
sequence and `specialists/35_DATA_PLATFORM_ENGINEER.md` were reviewed. The
eight base reports were reviewed, together with the architecture interfaces,
persistence boundaries, artifact/cache lifecycle, run/stage lifecycle and
failure/retry/idempotency documents. The machine-readable components,
interfaces, stage graph/state machines, artifact lifecycle, review checkpoints,
implementation plan, integration matrix, ownership map and test matrix were
also inspected.

## G6 HANDOFF

Step22 remains the correctness authority. The exact existing
`workspace/runs/step22-reference-run/validation/validation_report.json` was
registered as a read-only external `ValidationReport` reference and its
SHA-256 was bound to `GateEvidence`. The accepted Step22 content commit is
`f5ddeb6dbd2a1759b8d92a4dacf47400d6b580c2`. The Step22 validator was rerun:
both retail and generic runs were `G6 PASS`, eligible, with 25 checks and zero
discrepancies. The accepted Step20 DuckDB target was registered as a controlled
external `MaterializationArtifact`; it was not copied or mutated.

G6 persistence requires `ValidationReport.g6_status` and
`ValidationReport.g6_eligible`. `ReconciliationResult.no_blocking_discrepancy`
is not treated as a gate authority.

## PLATFORM ARCHITECTURE

`LocalPlatform` composes a SQLite control store, separate managed-artifact and
staging-artifact stores, a versioned staging policy adapter and a local
capability registry under `workspace/platform/`. The platform is cross-cutting
infrastructure; no business stage was added to the transformation DAG.

## PLATFORM CONFIG

`LocalPlatformConfig` binds explicit absolute project/workspace/control,
artifact, staging and cache roots plus a typed `ResourceBudget`. A
configuration fingerprint is persisted with each run and mismatched run
configuration is rejected.

## ARTIFACT STORE PORT

`ArtifactStorePort` covers reserve, atomic publish, external registration,
read/open, existence, verification, listing and authorized deletion. The
application boundary uses typed artifact references and does not expose
arbitrary host path manipulation.

## LOCAL ARTIFACT STORE

`LocalArtifactStore` writes bytes to a temporary file, fsyncs them and atomically
publishes content-addressed blobs under `blobs/sha256/<prefix>/<hash>`. Hashed
sidecars hold logical references. Same-ID different-content publication is an
explicit conflict; same content across IDs shares a blob without merging the
logical references.

## MANAGED / EXTERNAL ARTIFACTS

Managed bytes are owned by the configured artifact root. Existing Step20/22
files are represented as controlled project-relative external references and
are re-hashed on verification. Absolute paths, drive-qualified paths,
traversal, symlink escapes, protected paths and unregistered arbitrary files
are rejected.

## CONTENT ADDRESSING

The content hash and byte size are required for publication and verification.
Published references are immutable and cannot be repointed. Missing bytes,
hash mismatch, size mismatch, unreadable bytes and unavailable external files
have explicit integrity states.

## CONTROL STORE PORT

`ControlStorePort` persists typed run/stage lifecycle metadata, artifact
indexes, dependency edges, cache entries, staged manifests, gate evidence and
safe audit events. It has no arbitrary SQL or raw-row storage interface.

## SQLITE CONTROL STORE

`SQLiteControlStore` uses short-lived parameterized connections, foreign keys,
busy timeout, WAL and full synchronous mode. Tables cover schema metadata and
migrations, runs, stage attempts, artifacts, dependencies, cache entries, gate
evidence, staged datasets and audit events. Raw source rows, Parquet payloads
and DuckDB files remain outside SQLite.

## SCHEMA MIGRATIONS

Fresh databases create schema version 1 transactionally. A supported version 0
is migrated forward and recorded. A newer or malformed schema fails closed;
the adapter never resets it destructively. Reopen is idempotent.

## RUN STATE PERSISTENCE

Run records retain status, configuration fingerprint, content commit, root
artifact references, source snapshot references, gate references and revision.
Updates use compare-and-swap revision checks.

## STAGE ATTEMPT PERSISTENCE

Stage attempts retain run/stage identity, attempt number, status, policy and
resource references, input/output artifact references, failure fields and
revision. A fresh adapter recovers the completed reference attempt.

## OPTIMISTIC CONCURRENCY

Run and attempt updates reject stale revisions. Artifact publication is guarded
by a local adapter lock and atomic file operations: identical concurrent writes
converge and conflicting writes receive an immutable conflict. Separate run
references remain isolated.

## ARTIFACT DEPENDENCIES

Dependency rows store explicit downstream/upstream IDs, relationship kind and
expected upstream hash. Resolution reports `STALE_DEPENDENCY` when the current
upstream hash differs. Artifact-plus-dependency registration is transactional
and leaves no half-registered row after failure.

## STAGED DATASET LAYOUT

The staged logical key is:

`runs/<run>/source/<source>/snapshot/<snapshot>/table/<table>/dataset/<dataset>/version/<version>/part/<part>.parquet`

The manifest stores source/snapshot/table identity, dataset version, schema
fingerprint, row count where known and part artifact references. Staged bytes
are stored in the separate staging artifact area; the control DB stores only
the manifest and references.

## PARQUET STRATEGY

The reference validator writes one tiny deterministic Parquet part using the
already available `pyarrow` capability. Its stored metadata includes the part
hash, size, schema fingerprint and row count. No new data engine was added.

## CACHE SEMANTICS

`CacheKey` hashes typed ordered inputs, artifact IDs and hashes, policy and
contract versions, configuration, engine/code versions, seed and optional
domain/review applicability fingerprints. A hit requires exact key binding,
output metadata match and fresh byte verification. Missing or corrupt output
invalidates the cache entry and is never returned.

## GATE EVIDENCE PERSISTENCE

Gate evidence is tied to a registered `ValidationReport` artifact of the
correct kind and exact content hash. Repeated recording of the same stable
evidence is idempotent; changed evidence conflicts. G6 evidence remains
pinned and is not a cleanup candidate.

## G6 RESTART RECOVERY

The Step23 reference run closes its stores, reopens a new SQLite adapter,
recovers the run, attempt, dependencies, cache and gate, then verifies the
external target and exact ValidationReport reference. The G6 status is not
regenerated from platform output.

## RESOURCE BUDGETS

`ResourceBudget` binds worker slots, memory, disk, temporary space, staged
bytes, artifact bytes and timeout limits. The local artifact stores fail
closed when configured byte quotas would be exceeded.

## RETENTION / CLEANUP

Cleanup first creates a dry-run plan. Only aged, non-pinned, non-dependent
artifacts from completed runs are candidates. Active-run artifacts, retained
dependents and external files are protected. Execution requires authorization
for the exact plan; managed deletion writes a tombstone and removes an
unshared blob only. Pinned G6 evidence is rejected even on direct deletion.

## INTEGRITY SCANNING

`PlatformIntegrityService` scans only artifacts registered for the requested
run, across the configured artifact stores. The reference run detects the
intentionally missing disposable blob as `MISSING` and records a safe
verification-failure audit event.

## CRASH / RESTART RECOVERY

Atomic temporary publication prevents partial files from becoming published
references. SQLite metadata is durable across close/reopen and invalid bundle
registration rolls back. The tested guarantee is local process/reopen
recovery, not crash-consistent multi-node storage.

## CONCURRENT RUN ISOLATION

Run IDs, attempt IDs, logical keys and control rows are scoped explicitly.
Identical physical content may deduplicate, but logical references and
run-filtered metadata remain independent.

## OBJECT STORE FUTURE BOUNDARY

The local content-addressed key maps directly to an object-store key. A future
adapter must provide conditional immutable publication, metadata/hash
verification and typed cleanup. S3 is documented as `FUTURE_NOT_EXECUTED` and
is not claimed as implemented.

## POSTGRES FUTURE BOUNDARY

The SQLite schema and CAS predicates provide the compatibility target for a
future PostgreSQL adapter. PostgreSQL is documented as
`FUTURE_NOT_EXECUTED`; no PostgreSQL runtime was added or tested here.

## REFERENCE PLATFORM RUN

The deterministic run ID is `step23-platform-reference-run` with attempt
`step23-platform-attempt-1`. The run persisted five representative managed
project artifact families, the Step22 ValidationReport, the controlled Step20
target reference, dependency edges, one staged Parquet part, cache entries,
G6 evidence, integrity results and cleanup plan. The inspected reference
state used schema version 1 and 13 registered artifacts in the run inventory.

## G6 REGRESSION

`python tools/validate_step22_data_correctness.py` passed with 20 behavioral
assertions, 25 checks for each retail/generic run, zero discrepancies and G6
PASS/eligible for both runs.

## SECURITY NEGATIVE CASES

Tests cover logical-key traversal, absolute/drive paths, symlink/protected
path escape where supported, hash spoofing, external-artifact escape,
protected control-store paths, unsafe cleanup, future-schema rejection,
parameterized metadata filters and pickle prohibition in the platform layer.

## DETERMINISM / REPRODUCIBILITY

The persisted reproducibility manifest includes run ID, configuration
fingerprint, content commit, artifact references/hashes, gate references and
control schema version. The cache key and logical staging key are stable for
the same typed inputs.

## UNIT TESTS

`python -m pytest tests/unit -q`: 162 passed.

## CONTRACT TESTS

`python -m pytest tests/contract -q`: 33 passed; 2 non-failing dependency
warnings.

## INTEGRATION TESTS

`python -m pytest tests/integration -q`: 56 passed, 2 optional skips. The
skips are the unavailable official Valentine and Splink runtimes.

## ARCHITECTURE TESTS

`python -m pytest tests/architecture -q`: 19 passed.

## SECURITY TESTS

`python -m pytest tests/security -q`: 41 passed.

## FULL REGRESSION

`python -m pytest -q`: 311 passed, 2 optional skips. The baseline was 289
passed, 2 skips; Step23 added the platform coverage without changing the
optional-runtime semantics.

## VALIDATORS

The following all passed: domain validator, data-architecture validator,
solution-architecture validator (`41` components, `13` interfaces),
engineering-plan validator (`73` checks), Step22 G6 validator and the Step23
behavioral validator (`34` checks). `compileall -q src tools tests` and
`git diff --check` passed. Historical specialist validators were also rerun
after extending their later-handoff predicates through the explicit
Step23-to-Step24 state; they passed without changing any historical result.

## OUTPUT INSPECTION

The reference output was inspected programmatically after close/reopen:
SQLite schema version, run row, stage-attempt row, artifact inventory,
dependency resolution, cache status, exact G6 receipt, staged manifest,
reproducibility manifest, integrity scan, audit event types, cleanup plan and
external target reference were all present. The cleanup plan was dry-run and
did not delete data.

## KNOWN LIMITATIONS

- This proves durable local V1 primitives, not HA, multi-node consistency,
  cloud durability or production scale.
- Cross-process artifact locking beyond the tested local adapter process model
  remains future hardening.
- No PostgreSQL, S3, Kubernetes, Redis, Kafka or distributed worker runtime is
  implemented.
- No Backend RunManager, queue/job orchestration or distributed processing was
  introduced.
- The reference platform persists metadata and references; it does not
  regenerate the Step22 oracle or alter source systems.
- Optional Valentine/Splink runtime coverage remains SKIP in this environment.
- Existing dlt/SQLite cursor cleanup and dependency warnings are non-failing
  historical test-environment limitations.

## STEP24 HANDOFF

Step24 receives the `ArtifactStorePort` and `ControlStorePort` semantics,
staged-dataset layout, resource budgets, hash-pinned dependency graph, cache
key semantics, durable run/stage records, integrity/restart evidence and
object-store/PostgreSQL compatibility notes. Step24 may design distributed
computation behind the existing boundaries; it must preserve exact artifact
meaning and local-only limitations.

## EXECUTION STATE

Step23 implementation is complete for its local platform scope. Step24
implementation is NOT STARTED. G5 is PASS, G6 is PASS and G7 is PENDING.

## GIT

Content commit: `a35944fda71f93cba6cdacf8a7fdee5aa565e513`.
The protected quality-artifact directory remains untracked and unstaged.
The subsequent docs/state commit is metadata only; `last_verified_commit`
continues to identify the verified Step23 content commit above.
