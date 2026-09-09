# 03 — System Architecture Report

## Status and scope

This report is the synchronized system-level summary of the v1 Software / Solution Architecture. It freezes boundaries and contracts only; it does not create application code or satisfy Gate G2. The proposed implementation namespace is `dirty_data_to_olap`, under `src/dirty_data_to_olap/` when implementation is authorized. The package tree is architectural only at this step.

## Architectural contract

The system is a local-first, staged batch product. Project-owned domain contracts, ports, and application services are stable. Entrypoints call application orchestration; adapters and persistence implementations depend inward; concrete wiring is isolated at the composition boundary. Third-party models and native driver types are translated at the boundary and never become core or persisted contracts.

```text
entrypoints -> application/orchestration -> domain contracts and ports
                                      ^                 ^
                              adapters/persistence -----+
                                      ^
                              composition / wiring
```

## Logical zones and components

- **Domain and contracts:** source identities, observations, hypotheses, decisions, canonical entities, analytical plans, artifacts, validation results, run/stage status, and policies.
- **Application services:** run manager, stage planner, evidence fusion, review/policy, canonical hypothesis/finalization, analytical planning, compilation, materialization, validation, resume, and cancellation.
- **Adapters:** source, profiling, dependency discovery, schema matching, optional semantic evidence, and entity resolution providers. Each implements a project-owned port.
- **Persistence:** Control Store repositories and Artifact/Data Plane stores. Control holds metadata, indexes, state, decisions, and artifact references; large or immutable data stays in the Artifact/Data Plane.
- **Runtime and entrypoints:** StageExecutor, capability registry, CLI/API, and composition root.

The authoritative component list and dependency graph are in [components.yml](/docs/architecture/specs/components.yml) and [COMPONENT_MODEL.md](/docs/architecture/COMPONENT_MODEL.md).

## Runtime stage DAG

```text
SOURCE_DISCOVERY -> SOURCE_SNAPSHOT_STAGE -> {PROFILING, DEPENDENCY_DISCOVERY,
SCHEMA_MATCHING, QUALITY_ANALYSIS} -> EVIDENCE_FUSION
-> REVIEW_EVIDENCE_DECISIONS -> CANONICAL_HYPOTHESES
-> [ENTITY_RESOLUTION] -> REVIEW_CANONICAL_IDENTITY
-> CANONICAL_FINALIZATION -> ANALYTICAL_PLANNING
-> REVIEW_ANALYTICAL_PLAN -> COMPILATION
-> REVIEW_MATERIALIZATION_PLAN -> MATERIALIZATION
-> VALIDATION_RECONCILIATION
```

Optional semantic evidence is a declared branch into evidence fusion. Each review checkpoint is a first-class stage boundary backed by the one reusable Review / Policy Service; it is entered only after its subject artifact exists. Evidence review covers relationships, mappings, conflicts and repairs before canonical hypotheses. Entity resolution is conditional and produces linkage evidence only: `EntityMatchEdge` and `EntityCluster`; identity/linkage review follows those artifacts when ER is required. Analytical-plan review follows `AnalyticalPlan`, and materialization approval follows `CompiledPlan`/`GeneratedSQL`. A required unresolved checkpoint pauses its guarded stage and run in `NEEDS_REVIEW`; a policy-recorded skip is explicit and versioned. Entity resolution never produces `canonical_entity_id` or `SourceRecordCanonicalMap`. Canonical Finalization is the sole producer of accepted canonical identity and `SourceRecordCanonicalMap`. For an ER-required entity family, finalization requires complete acceptable ER output and a compatible post-ER identity/linkage decision; for an ER-not-required family, absent or policy-recorded skipped ER and policy-permitted identity-review skip are legal. The graph is acyclic and all stage outputs are typed project-owned artifacts.

## Run and stage lifecycle

Run states are `CREATED`, `RUNNING`, `NEEDS_REVIEW`, `BLOCKED`, `FAILED`, `CANCELLED`, and `SUCCEEDED`. `PARTIAL` is explicitly rejected as a run state. Stage states are separate and include `PENDING`, `RUNNING`, `SUCCEEDED`, `NEEDS_REVIEW`, `BLOCKED`, `FAILED`, `CANCELLED`, `INVALIDATED`, and `SKIPPED`.

Every execution has a stage attempt with pinned inputs, upstream hashes, configuration, adapter version, error/cancellation details, and output references. Retries create a new attempt. A run can become `SUCCEEDED` only after final validation passes, required work is complete, required unresolved conditions are zero, and complete required artifacts are published.

The canonical path is deliberately two phase: evidence produces canonical hypotheses; optional entity resolution produces linkage evidence; the canonical identity checkpoint and policy validation allow Canonical Finalization to bind accepted source records to canonical instances. Review decisions are artifact-scoped and carry subject ID, content hash, schema/model version, semantic identity and applicability fingerprint. Hypotheses, clusters and accepted mappings are not interchangeable.

## Control plane and Artifact/Data Plane

The Control Store contains run, stage, attempt, capability, configuration, index, decision, and artifact-reference metadata. It does not contain raw rows or large analytical tables. The Artifact/Data Plane contains source snapshots, samples, profiles, evidence, decisions, canonical outputs, plans, compiled SQL, materialized targets, manifests, and validation reports. Artifacts are content-hashed and published atomically; only `COMPLETE` artifacts are consumable.

## Caching, replay, and failure semantics

Cache identity includes stage/version, upstream artifact references and hashes, source schema and sample fingerprints, configuration, adapter version, domain/policy version, and deterministic seed. Schema, grain, policy, adapter, configuration, or upstream changes invalidate descendants. Review decisions retain the exact subject artifact/version/fingerprint needed for compatibility-checked replay; incompatible decisions are retained as history but invalidated and cannot satisfy a guard. Required capability absence is `BLOCKED`; optional capability absence is an explicit skip with a reason. There is no fake fallback for missing evidence. External failures are isolated by stage/attempt and do not overwrite a valid prior artifact.

## Runtime topology and safety

The reference topology is one local coordinator with a Control Store, filesystem/Parquet Artifact/Data Plane, optional DuckDB analytical target, bounded workers, and optional external engines behind adapters. Source access is read-only by policy. Secrets remain outside committed artifacts, raw rows are not logged by default, and implementation is deferred until later specialist gates authorize it.

## Extension model

New source, profiler, dependency, matching, semantic, entity-resolution, materialization, or persistence providers implement the relevant project-owned port, register a capability, and pass contract/compatibility checks. New stages require a graph/spec/contract update and explicit invalidation semantics. No extension may add a direct adapter-to-adapter dependency or leak a provider-native type.

## Authoritative artifacts

- [Software architecture contract](/docs/architecture/SOFTWARE_ARCHITECTURE_CONTRACT.md)
- [Component model](/docs/architecture/COMPONENT_MODEL.md)
- [Dependency rules](/docs/architecture/DEPENDENCY_RULES.md)
- [Engine interfaces](/docs/architecture/ENGINE_INTERFACES.md)
- [Run and stage lifecycle](/docs/architecture/RUN_AND_STAGE_LIFECYCLE.md)
- [Artifact and cache lifecycle](/docs/architecture/ARTIFACT_AND_CACHE_LIFECYCLE.md)
- [Persistence boundaries](/docs/architecture/PERSISTENCE_BOUNDARIES.md)
- [Failure, retry, and idempotency](/docs/architecture/FAILURE_RETRY_IDEMPOTENCY.md)
- [Runtime topology](/docs/architecture/RUNTIME_TOPOLOGY.md)
- [Extension points](/docs/architecture/EXTENSION_POINTS.md)
- [Machine-readable specifications](/docs/architecture/specs/components.yml)
- [Stage state machine](/docs/architecture/specs/stage_state_machine.yml)
- [Review checkpoint specification](/docs/architecture/specs/review_checkpoints.yml)
- [Architecture decisions](/docs/adr/ADR-0001_PROJECT_OWNED_CONTRACTS.md)
- [Two-phase canonicalization ADR](/docs/adr/ADR-0006_TWO_PHASE_CANONICALIZATION.md)
- [Stage-scoped review checkpoints ADR](/docs/adr/ADR-0007_STAGE_SCOPED_REVIEW_CHECKPOINTS.md)

## Deferred implementation

No `src/` tree, concrete adapters, drivers, services, generated data, or research/OSS clone is created by this report. G2 remains `PENDING`; the next handoff is Step 05 — Technical Lead / Engineering Lead.
