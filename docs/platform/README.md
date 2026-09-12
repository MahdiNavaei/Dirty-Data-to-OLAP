# Step23 Local Data Platform

Step23 provides the durable local V1 persistence boundary used by later run
and execution work. It is a local-first platform foundation, not a distributed
execution system.

## Topology

`LocalPlatform.from_project_root()` wires the following project-local roots:

```text
workspace/platform/
  control.sqlite       # typed control metadata only
  artifacts/
    blobs/sha256/      # content-addressed managed bytes
    refs/              # hashed logical-reference sidecars
    tmp/               # atomic publication scratch space
  staging/             # staged-dataset artifact store
  cache/               # reserved platform cache/temp root
```

Application and domain code use `ControlStorePort`, `ArtifactStorePort` and
the staging abstraction. They do not assemble host paths or issue arbitrary SQL.
The source adapters remain read-only; platform writes are limited to the
configured control, artifact, staging and temporary roots.

## Artifact semantics

An `ArtifactManifest` is a pre-publication envelope. A published
`ArtifactRef` binds a stable logical artifact ID to its run, stage, attempt,
schema version, SHA-256 content hash, byte size, retention class and
provenance. A reference is immutable: reusing its ID with different bytes or
identity metadata is rejected.

Managed artifacts are written to a temporary file, flushed and fsynced, then
atomically moved to `blobs/sha256/<prefix>/<hash>`. Identical bytes can share
one physical blob while retaining independent logical references. Claimed
hashes and sizes are checked before publication. Reference metadata is also
published atomically.

External artifacts are not copied. They are registered only as controlled,
project-relative POSIX locators. Absolute paths, drive-qualified paths,
traversal, symlink escapes and the protected
`tests/quality_unit_artifacts/` subtree are rejected. Verification re-reads
the external file and reports a hash or availability failure if it changes.
The registration locator must also exactly equal the manifest locator; a safe
alternate path cannot be substituted for the declared external artifact.

Only JSON/text/Parquet-style project data crosses this boundary. Executable
or pickle transport is not part of the platform contract.

## Control store and migrations

`SQLiteControlStore` owns typed metadata tables for schema migrations, runs,
stage attempts, artifact references, dependency edges, cache entries, gate
evidence, staged-dataset manifests and safe audit events. Raw source rows,
Parquet payloads and DuckDB files are never stored in SQLite.

Schema version 3 is initialized transactionally and recorded in
`schema_migrations`. Supported V1 databases migrate forward without reset:
staged-dataset identity becomes run-scoped and gate rows gain the separate
ValidationReport run/report identity. An unknown table layout or newer schema
fails closed. Metadata registration with dependencies is one transaction. Run
and attempt updates use compare-and-swap revisions, so stale writers receive
an explicit concurrency error.

## Staging layout

`LocalStagingStore` publishes a Parquet part through the artifact boundary and
records a `StagedDatasetManifest` in SQLite. The logical key is independent
of Windows path syntax:

```text
runs/<run>/source/<source>/snapshot/<snapshot>/table/<table>/dataset/<dataset>/version/<version>/part/<part>.parquet
```

The manifest binds each part to its artifact hash and size, schema fingerprint,
row count where known, source snapshot and table identity. Part artifact IDs
and logical keys include run, source, snapshot, table, dataset, version and
part scope; the control store primary key is `(run_id, dataset_id,
dataset_version)`. Registration revalidates exact part kind, publication,
managed storage, logical key, run/schema and row-count closure. The control
store contains the manifest and references; staged bytes remain in the
staging artifact store.

## Cache, retention and cleanup

Cache identity is the deterministic hash of typed stage/component inputs,
ordered artifact IDs and hashes, contract and policy versions, configuration,
engine/code versions, seed and applicable domain/review fingerprints. A cache
hit requires both the exact key and a fresh artifact integrity verification.
Missing, corrupt or metadata-mismatched outputs invalidate the entry and are
never returned.

Cleanup is plan-first. Run-scoped artifacts are eligible only after the age
policy, only for completed/non-active runs, only without retained dependents,
and only with an explicit authorization bound to the deterministic semantic
plan hash. A dry-run/executable-mode change, candidate injection, hash or
run-scope change therefore requires new authorization. Each deletion receives
an exact per-artifact permit containing the plan hash, expected hash/size,
retention, action and dependent set; bytes are verified immediately before
deletion. Pinned gate evidence is excluded and direct deletion is rejected.
Managed deletion writes a tombstone and removes a blob only when no other
local reference uses it; external bytes are never owned or deleted by the
platform.

## Resource and recovery boundaries

`ResourceBudget` carries worker slots, memory, disk, temporary-space, staged
bytes, artifact bytes and timeout limits. The V1 filesystem adapter enforces
its configured disk/staged-byte quota while writing. A process can close and
reopen SQLite and rediscover run state, attempts, references, dependencies,
cache metadata and gate evidence. Atomic temporary publication avoids exposing
partial managed bytes as published artifacts.

G6 gate evidence is accepted only from the exact registered, published,
verified `ValidationReport` artifact. The typed report bytes must parse and
equal the supplied report; status, eligibility and policy version are derived
from the report, while the platform run and the report's own run/report IDs
remain separate. A wrong artifact kind or status-laundered receipt fails
closed. Reopening the control store recovers the accepted G6 receipt without
recomputing it from reconciliation signals.

These guarantees are for one local checkout and the tested adapter process
model. They do not establish HA, multi-node locking, distributed safety,
production scale or cloud durability. Cross-process artifact publication
coordination and fault-injection recovery remain future hardening work.

## Future replacement mapping

The ports are intentionally provider-neutral:

- PostgreSQL can replace SQLite while preserving typed metadata, migrations,
  compare-and-swap revisions, parameterized queries and transaction semantics.
- S3-compatible storage can replace the managed filesystem using the same
  logical keys, SHA-256 metadata, conditional publication, immutable refs and
  explicit external-object ownership.
- A future distributed executor can consume the run/stage/dependency/cache
  contracts through `StageExecutorPort`.

No PostgreSQL, S3, Kubernetes, Redis, Kafka or distributed worker runtime is
implemented or claimed by Step23. Step24 owns distributed data execution and
scale semantics.
