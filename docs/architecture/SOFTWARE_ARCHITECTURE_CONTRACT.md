# Dirty Data to OLAP — Software Architecture Contract

Status: DEFINED for the V1 software architecture; application implementation remains deferred to later specialists.

## 1. Scope

This contract turns the frozen product, domain and data architecture into software boundaries. It defines ownership, dependency direction, lifecycle semantics, persistence boundaries, adapter isolation, restart behavior and local-first deployment shape. It does not define Python classes, Pydantic models, database migrations, source drivers, API endpoints or worker code.

The proposed future Python import namespace is dirty_data_to_olap and the proposed package root is src/dirty_data_to_olap/. Step 04 does not create that directory.

## 2. Architecture style

The system uses a project-owned ports-and-adapters style with explicit composition:

~~~
entrypoints
    -> application orchestration and services
    -> project-owned ports and domain contracts
    <- adapters implement ports and normalize external engines
    <- persistence implementations satisfy repository ports
composition root wires concrete implementations to ports
~~~

The arrows describe dependency direction. Core services depend on stable project-owned contracts and ports. Adapters and persistence implementations depend inward on those contracts. The composition boundary is the only place that selects concrete adapters, stores and executors.

## 3. Dependency invariants

- Domain contracts have no dependency on adapters, persistence, entrypoints or external libraries.
- Application services depend only on project-owned domain contracts, ports and other application services.
- Core/application code never imports a concrete third-party engine type.
- External engine objects are consumed and discarded inside their adapter boundary; project-owned contracts cross the boundary.
- Native engine objects are never persisted, pickled or exposed through a cross-module contract.
- Validation consumes project-owned artifacts and contracts; it does not mutate operational sources.
- The compiler translates an approved analytical plan but does not execute source cleaning.
- The materializer executes an approved compiled plan but does not redefine domain meaning, grain or measures.
- Entry points invoke application services or submit work through StageExecutor; they never invoke Splink, Valentine or another engine directly.
- Concrete wiring is explicit and per-run; no global mutable service registry is permitted.

## 4. Control plane and data plane

The Control Store is a metadata/index/state repository. It stores run and stage state, attempts, configuration identity, source registry metadata, artifact references, evidence and decision indexes, validation status and cache metadata. It does not store raw source tables or large intermediate row datasets.

The Artifact/Data Plane stores source snapshots or references, samples, large profiles/evidence tables, canonical/analytical row artifacts, generated SQL and the DuckDB target. V1 uses project-local filesystem storage, Parquet for large structured intermediates, JSON/YAML for small metadata and DuckDB for the analytical target.

## 5. Lifecycle boundary

Run status is coarse and separate from stage status. Stages own execution details, attempts and artifacts. A run can be RUNNING, NEEDS_REVIEW, BLOCKED or FAILED while individual stage artifacts remain available for restart. SUCCEEDED is reserved for a final validated product result.

Source access has an explicit two-operation boundary. Discovery introspects the selected source and produces `SourceCatalog`; bounded snapshot creation consumes that catalog and `SamplingPolicy` to produce immutable snapshot/batch/source-record references. No source snapshot is required to discover the catalog, and neither operation permits source writes.

An artifact is consumable only when its lifecycle status is COMPLETE, its schema and metadata validate, its content hash exists and it has been atomically registered. A materialized DuckDB file is evidence of materialization, not by itself a validated product.

## 6. Stage-scoped review checkpoints

Review / Policy is one reusable application service. It presents and records
evidence-backed review requests, persists reviewer actions, binds each decision
to the exact subject artifact/version/fingerprint, exposes unresolved state,
retains history and performs compatibility-checked replay. Runtime checkpoints
are distinct DAG boundaries that delegate to that service:

1. `REVIEW_EVIDENCE_DECISIONS` follows `EVIDENCE_FUSION` and guards canonical
   hypothesis generation.
2. `REVIEW_CANONICAL_IDENTITY` follows canonical hypotheses and, for an
   ER-required family, complete `ENTITY_RESOLUTION` output; it guards
   `CANONICAL_FINALIZATION`.
3. `REVIEW_ANALYTICAL_PLAN` follows `ANALYTICAL_PLANNING` and guards
   `COMPILATION`.
4. `REVIEW_MATERIALIZATION_PLAN` follows `COMPILATION` and guards
   `MATERIALIZATION` when policy requires approval.

An unresolved required checkpoint transitions the guarded stage and run to
`NEEDS_REVIEW`; accepted compatible review permits a new/resumed downstream
attempt. Rejected or deferred review cannot satisfy the guard. A policy may
record `SKIPPED` only when the checkpoint specification permits it, without
inventing numeric confidence thresholds. Entry points call the application
service and never mutate decision records directly.

## 7. Canonicalization boundary

Canonicalization is explicitly two-phase:

1. Canonical Hypothesis proposes entity types, attributes, identity candidates and mappings.
2. Conditional Entity Resolution produces linkage evidence for selected entity families.
3. Entity Resolution, when selected, produces linkage evidence (`EntityMatchEdge` and `EntityCluster`) only.
4. Canonical Finalization assigns accepted canonical instances, `SourceRecordCanonicalMap`, survivorship decisions and conflict-bearing values.

An EntityCluster never becomes a canonical ID automatically. When a selected entity family requires ER, finalization requires a complete acceptable linkage artifact and a compatible post-ER `REVIEW_CANONICAL_IDENTITY` linkage decision for that family. When ER is not required, its absence or policy-recorded skip and a policy-permitted identity-review skip are legal. Source records remain traceable.

## 8. Runtime topology

V1 is local-first:

~~~
one checkout
  + CLI/API entrypoint
  + local StageExecutor
  + SQLite Control Store
  + filesystem Artifact Store
  + Parquet intermediates
  + DuckDB target
~~~

The contracts permit later replacement of SQLite with PostgreSQL, filesystem storage with object storage, the local executor with workers and DuckDB with another target. Those replacements are extension options, not V1 implementation claims.

## 9. Non-functional architecture requirements

The architecture requires reproducibility, deterministic contract serialization where applicable, restartability, idempotency, explicit failure, read-only source interaction, bounded-memory processing, replaceable adapters, no global mutable state, no raw secrets in artifacts, project-local workspace, traceable provenance, correct cache invalidation, optional-engine isolation, testability and local-first operation. Numeric latency or availability targets are intentionally not invented.
