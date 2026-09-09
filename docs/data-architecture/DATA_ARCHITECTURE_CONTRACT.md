# Dirty Data to OLAP — Data Architecture Contract

Status: `DEFINED` for logical architecture; no application implementation is claimed.

## 1. Purpose and boundary

This contract protects semantic correctness from physical source representation through evidence, decisions, canonical modeling, analytical planning, materialization and validation. It is an architectural contract, not a Python, database, API or package design.

The universal architectural rules here apply to any Dirty Data to OLAP runtime project. The source-authority examples and entity meanings from `docs/domain/` remain scoped to the **REFERENCE BENCHMARK DOMAIN TRUTH**. Runtime customer authority requires a scoped **RUNTIME CUSTOMER DOMAIN ASSERTION**.

## 2. Layer separation

```text
Physical source representation
  -> source snapshot / immutable observation
  -> measured evidence and hypotheses
  -> domain assertions, policy and human decisions
  -> canonical semantic model
  -> canonical entity instances and source mappings
  -> analytical dimensional plan
  -> materialized analytical target
  -> validation and reconciliation
```

The layers are not interchangeable:

- a `RelationshipCandidate` is not a relationship truth;
- an `EntityCluster` is not automatically a canonical ID;
- a canonical entity type is not automatically a dimension;
- a canonical event is not automatically a fact;
- generated SQL is not a validated target.

## 3. Source and snapshot contract

V1 sources are read-only. Every source, table/file, column and snapshot receives stable project references. A snapshot identifies source state, schema fingerprint, run/extraction context, selected scope, observation scope and adapter context. A row without a durable source key receives an auditable snapshot-bound `SourceRecordReference`; it is never presented as a globally durable identity.

Raw values and source references remain recoverable where practical. Normalization and canonicalization produce new representations and never silently overwrite the only source representation.

## 4. Identity and modeling contract

Physical keys, business identifiers, candidate keys, canonical identities, canonical surrogate IDs, analytical surrogate keys, degenerate business identifiers and source-record references are distinct categories. Entity-resolution clusters are linkage evidence/results; an accepted cluster may contribute to canonical identity only under entity scope, provenance, conflict state and review policy.

Canonical and analytical models remain separate. A fact requires human-readable and machine-testable grain. Measures require meaning, type, grain context, aggregation semantics, units/currency where relevant, lineage and validation.

## 5. Authority, conflict and absence contract

Source authority is never global. Survivorship is a scoped, versioned selection policy that preserves losing values and rationale. Conflicts remain explicit. Source NULL/missing, not captured, unknown, not applicable, invalid/unparseable, withheld/redacted and quarantined/unaccepted states must not collapse accidentally into one NULL.

Unknown-member behavior is never an implicit `-1`, `0` or `UNKNOWN` mapping. For the reference benchmark, orphan relationships are corruption/evaluation cases and cannot be silently legitimized.

## 6. Provenance, lineage and accounting contract

Every persisted semantic artifact records origin, time/snapshot/run, engine/policy/actor, evidence and upstream dependencies. Lineage separately tracks table/object, column/attribute, record/entity, transformation and decision paths. “Reversible traceability” means an analytical result can be traced back through canonical mappings and decisions to source/evidence; it does not promise mathematical inversion of every transformation.

Record accounting is scoped to one transformation boundary and accounting scope; heterogeneous source row counts must not be combined into one vague global count. The machine-readable contract is [record_accounting.yml](specs/record_accounting.yml).

For each input record in that scope, exactly one mutually exclusive terminal disposition is required: `EMITTED_DIRECT`, `CONSOLIDATED`, `AGGREGATED`, `FILTERED_EXPLICIT`, `QUARANTINED` or `UNRESOLVED`. Each disposition carries an explicit reason and provenance. `MAPPED`, `LINKED`, `NORMALIZED`, `MATCHED`, `PROFILED` and `REVIEWED` are orthogonal processing annotations, not terminal accounting outcomes. `REJECTED` is a review/decision state; if it excludes a record, the terminal disposition must be `FILTERED_EXPLICIT` or `QUARANTINED` with the applicable reason/policy.

When a record contributes to downstream output, an output reference is required. `CONSOLIDATED` and `AGGREGATED` records additionally retain contributor references, an output or group reference, a transformation/policy reference and provenance. Required `UNRESOLVED` records prevent a run from being marked `Completed and validated` unless a versioned product/domain policy classifies them into another accepted terminal disposition. Output-row counts are reconciled separately from input-contributor counts.

## 7. Temporal and evolution boundary

V1 supports snapshot/rebuild semantics compatible with a current Type 1 analytical representation and may carry `valid_from`, `valid_to` and `is_current` fields where appropriate. It must not fabricate history or claim full incremental SCD2. Semantic-model, mapping, plan and contract changes require visible versioning and must not silently reinterpret old runs.

## 8. Implementation boundary

This pass defines logical requirements only. Pydantic models, dataclasses, database schemas, serializers, REST contracts, package/module boundaries, drivers and execution engines belong to later specialists. G2 remains pending until the complete architecture sequence is evaluated.
