# Dirty Data to OLAP — Persistence Boundaries

Step23 implements the local V1 boundaries described here. The implementation
is deliberately replaceable and does not claim cloud or distributed scale.

## Control Store

Step23 uses SQLite behind ControlStorePort. It stores:

- project and run manifests;
- pinned configuration metadata;
- source registry metadata and connection-profile references;
- stage attempts, transitions, checkpoints and cancellation records;
- artifact indexes, hashes, logical locations and statuses;
- evidence/decision indexes and review decisions;
- canonical/analytical plan metadata;
- validation status and cache metadata.

The Control Store does not store raw source tables, large samples, Parquet row data or the DuckDB file. It stores references and metadata for those artifacts.

## Artifact/Data Store

Step23 uses a bounded project-local filesystem behind ArtifactStorePort. The
platform-managed root is workspace/platform/, with separate control, managed
artifact, staging and temporary areas. Existing run artifacts remain under
workspace/runs/<run_id>/ and are referenced as controlled external artifacts
when the platform does not own their bytes. Attempt-local writes are separate
from published artifact locations.

- Parquet stores large tabular intermediates and profiles.
- JSON/YAML stores small envelopes, decisions and plans.
- DuckDB stores the controlled analytical target.
- Human-readable reports reference artifacts and hashes rather than embedding raw data.

Path resolution remains inside the Artifact Store. Core services use logical artifact references.

## Repository semantics

ControlStorePort supports create/get run, compare-and-transition run state, create/update stage attempt, register/read artifact metadata, record/retrieve decisions, record validation status and record cache metadata.

No dedicated `ReviewDecisionPort` is introduced in V1. Review decisions and
checkpoint status are control-plane metadata with the same transactional
lifecycles as stage attempts, artifact references and run transitions, so
`ControlStorePort` is the single persistence boundary. The application-level
Review / Policy Service remains the only semantic authority: entrypoints and
checkpoint stages call that service and never write decision records directly.

ArtifactStorePort supports reserve/publish complete artifacts, read published
artifacts, verify content hash and size, list references, register controlled
external files, and perform authorized tombstone cleanup. LocalStagingStore adds
versioned staged-dataset manifests while keeping Parquet bytes outside the
control database.

Step23 has tested the SQLite/filesystem reference path. PostgreSQL,
object-storage and other implementations remain future replacements and must
satisfy these semantics without changing application or domain contracts.

## Safety

Source access remains read-only. Controlled target writes are isolated from source credentials and source locations. Raw PII is minimized in metadata/logs and is not committed as a project artifact.
