# Specialist Step 04 — Software / Solution Architecture Review

Status: `PASS`

Owning specialist: `Step 04 — Software / Solution Architect`

This is a Step 04 completion receipt and architecture freeze. It is not Gate G2. G2 remains `PENDING` and must be evaluated by the later engineering sequence.

## Evidence

- [Software architecture contract](../architecture/SOFTWARE_ARCHITECTURE_CONTRACT.md)
- [Component model](../architecture/COMPONENT_MODEL.md)
- [Dependency rules](../architecture/DEPENDENCY_RULES.md)
- [Engine interfaces](../architecture/ENGINE_INTERFACES.md)
- [Run and stage lifecycle](../architecture/RUN_AND_STAGE_LIFECYCLE.md)
- [Artifact and cache lifecycle](../architecture/ARTIFACT_AND_CACHE_LIFECYCLE.md)
- [Persistence boundaries](../architecture/PERSISTENCE_BOUNDARIES.md)
- [Failure, retry, and idempotency](../architecture/FAILURE_RETRY_IDEMPOTENCY.md)
- [Runtime topology](../architecture/RUNTIME_TOPOLOGY.md)
- [Extension points](../architecture/EXTENSION_POINTS.md)
- [Component specification](../architecture/specs/components.yml)
- [Dependency specification](../architecture/specs/dependency_rules.yml)
- [Engine interface specification](../architecture/specs/engine_interfaces.yml)
- [Run state machine](../architecture/specs/run_state_machine.yml)
- [Stage graph](../architecture/specs/stage_graph.yml)
- [Artifact lifecycle](../architecture/specs/artifact_lifecycle.yml)
- [Architecture decision records](../adr/ADR-0001_PROJECT_OWNED_CONTRACTS.md)
- [System architecture report](../03_SYSTEM_ARCHITECTURE.md)
- [Architecture validator](../../tools/validate_solution_architecture.py)

## Frozen architecture counts

- Logical components: `34`
- Project-owned ports/adapters: `11`
- Runtime stages: `16`
- Run states: `7`
- Required unresolved record count for success: `0`
- Implementation created in Step 04: `false`

## Resolved inconsistencies

- Run outcomes and processing stage states are separate; `PARTIAL` is not a run state.
- The lifecycle is explicit: canonical hypotheses, conditional entity resolution, then canonical finalization.
- The active proposed namespace is `dirty_data_to_olap`; no implementation package or `src/` tree was created.
- Control Store metadata/index/state/decision/artifact references are separate from the Artifact/Data Plane's large and immutable data.
- Entrypoints, application services, project-owned ports, adapters, persistence, runtime, and composition wiring have explicit ownership and dependency direction.
- Compiler, materializer, and final validation are separate responsibilities.
- Required capability absence blocks; optional capability absence is a recorded skip. No fake fallback is permitted.

## Runtime walkthroughs

1. Source discovery and snapshot pin a bounded immutable input. Profiling, dependency discovery, matching, and quality stages write attempt-scoped artifacts; evidence fusion records candidates and conflicts.
2. Review decisions feed canonical hypotheses. If policy requires it, entity resolution produces separate links; finalization publishes accepted canonical records only after review and compatibility checks.
3. Analytical planning freezes grain, measures, lineage, and record-accounting expectations. Compilation creates a plan; materialization creates a controlled target; reconciliation/validation alone can satisfy the run success guards.

## Failure and restart walkthroughs

- A required provider unavailable before execution produces `BLOCKED`; after capability resolution, resume creates a new attempt while retaining the reason and prior evidence.
- An engine crash produces an explicit failed attempt. Retry uses a new attempt ID and cannot overwrite a prior artifact.
- A cancelled write remains non-consumable until a complete atomic publication; completed artifacts survive and the run is not silently resumed.
- A schema, grain, adapter, policy, or entity-resolution configuration change invalidates affected descendants and preserves historical artifacts.
- An incompatible review decision becomes `NEEDS_REVIEW`; it is not silently replayed.

## Validation evidence

The required validator is `python tools/validate_solution_architecture.py`. It parses all seven machine-readable specifications, checks component and stage DAG acyclicity, verifies project-owned outputs and dependency rules, checks ER ownership and conditional-family guards, checks logical-stage/attempt transitions, checks run/stage/artifact semantics, checks the Control/Data Plane boundary, validates namespace and implementation boundaries, audits execution-log paths, checks synchronized reports and manifest, and executes lifecycle `10/10`, dependency `10/10`, artifact/cache `7/7`, ER `8/8`, stage-lifecycle `10/10` and cross-contract `7/7` negative cases.

Existing regressions remain required:

- `python tools/validate_domain_docs.py`
- `python tools/validate_data_architecture.py`
- full Knowledge Base manifest verification
- paired system-report equality
- secret-like, source-tree, and OSS-clone scope checks

## Unresolved choices intentionally handed forward

- Concrete framework, database driver, queue, and deployment choices.
- Physical source keys and runtime mapping persistence.
- Exact review UI/API shape and authentication implementation.
- Production capacity limits and operational SLO measurements.

These are implementation or later-gate decisions, not reasons to weaken the frozen contracts.

## Gate and handoff

`G0 PASS`, `G1 PASS`, `G2 PENDING`, and `G3` through `G15 PENDING`. Step 04 is complete. Do not begin Step 05 in this execution receipt, and do not mark G2 PASS.

## Post-Step-04 Independent Review Correction

An independent review found four integrity defects in the pushed Step 04 architecture:

1. **Entity Resolution ownership:** the interface, adapter component, application service and runtime stage incorrectly listed `SourceRecordCanonicalMap`. They now emit linkage evidence only (`EntityMatchEdge`, `EntityCluster`). Canonical Finalization alone produces accepted canonical identity and `SourceRecordCanonicalMap`; cluster IDs cannot become canonical IDs.
2. **Conditional ER dependency:** the stage graph now records an entity-family guard. Required ER families need a complete acceptable ER artifact and accepted/review-acceptable linkage decision before finalization. Non-required families may proceed with absent or policy-recorded skipped ER. ER failure is family-scoped and cannot trigger a fake mapping fallback.
3. **Stage transitions:** `specs/stage_state_machine.yml` now distinguishes aggregate logical `StageStatus` from immutable `StageAttemptStatus`, defines legal transitions, invalidation, retries, cancellation and skip policy.
4. **Execution evidence paths:** the original Step 04 receipt cited nonexistent `docs/00_*` product files. The log now points to the actual `docs/product/` files. This correction is recorded here rather than silently rewriting history.

The cross-spec audit also aligned `RelationshipCandidate` across dependency-discovery interface, component and stage outputs, and made V1 capability-required versus per-run conditional semantics explicit for dependency discovery, schema matching, entity resolution and semantic evidence.

Corrective artifacts include the ER/interface/component/stage specs, stage state machine, internal contract clarification, synchronized system and internal-contract reports, narrative architecture updates, validator semantic checks and explicit negative tests. Step 04 remains `PASS`; this repair does not start Step 05 and does not change G2 from `PENDING`.
