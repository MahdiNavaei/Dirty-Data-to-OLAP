# ADR-0002: Control Store and Artifact/Data Plane

Status: Accepted for v1 architecture

## Context

Run coordination needs small, transactional metadata, while profiling samples,
snapshots, evidence tables, canonical outputs, and materialized data can be
large and immutable. Mixing them would make state transitions expensive and
would blur auditability.

## Decision

The Control Store contains run/stage/attempt metadata, state transitions,
configuration and input references, indexes, decisions, and artifact references.
The Artifact/Data Plane contains content-addressed snapshots, evidence,
canonical and analytical artifacts, materialized outputs, schemas, manifests,
and validation reports. `ControlStorePort` and `ArtifactStorePort` are separate
project-owned contracts.

## Consequences

- Control transactions stay small and auditable.
- Large data is immutable, hashed, and published atomically.
- A control record never substitutes for the underlying artifact.
- Local development can use SQLite plus a filesystem/Parquet store without
  changing application contracts.

## Rejected alternatives

- Storing raw rows or large tables in the Control Store.
- Treating a filename or database row as proof that an artifact is complete.
