# Dirty Data to OLAP — Lineage and Provenance

## Provenance

Provenance answers where, when, by what process and under which decision context an artifact was produced. Persisted semantic artifacts must retain, where applicable:

- source metadata provenance;
- source snapshot/run and sampling scope;
- engine/version/configuration for measured evidence;
- relationship/key and schema-matching evidence;
- entity-resolution configuration and decision;
- human/domain assertion actor and assertion time;
- canonical mapping and survivor policy;
- analytical mapping, transformation and materialization plan;
- validation/reconciliation result and upstream artifact references.

Every artifact must be able to answer: where did it come from, when/in which snapshot, which engine/policy/person produced it, what evidence supported it, and which upstream artifacts it depends on?

## Lineage layers

Lineage is distinct from provenance and must support:

```text
analytical fact/dimension
  -> canonical entity/attribute
  -> mapping/decision
  -> source table/column
  -> source record where applicable
```

Required lineage levels:

- table/object lineage;
- column/attribute lineage;
- record/entity lineage where applicable;
- transformation lineage;
- decision lineage.

## Reversible traceability

Reversible traceability means an analytical result can be followed back through target row, analytical mapping, canonical representation, decision/evidence and source snapshot/record. It does not mean every normalization or aggregation is mathematically invertible. Aggregations must preserve contributing-record references or a reconciled aggregation manifest.

## Record accounting

Accounting is evaluated per transformation boundary and accounting scope, not as one global count across heterogeneous sources. The normative machine-readable contract is [record_accounting.yml](specs/record_accounting.yml).

Each input record in a scope has exactly one mutually exclusive terminal disposition:

- `EMITTED_DIRECT` — represented directly in accepted downstream output;
- `CONSOLIDATED` — contributes to a many-source-record to canonical/entity representation;
- `AGGREGATED` — contributes to a many-record to analytical aggregate output;
- `FILTERED_EXPLICIT` — excluded by an explicit versioned rule or policy;
- `QUARANTINED` — retained outside accepted output because validity or safety requirements failed;
- `UNRESOLVED` — no final safe disposition exists yet.

`MAPPED`, `LINKED`, `NORMALIZED`, `MATCHED`, `PROFILED` and `REVIEWED` are orthogonal processing annotations and are never terminal accounting outcomes. `REJECTED` remains a decision/review state; when it excludes a record, the terminal disposition must be `FILTERED_EXPLICIT` or `QUARANTINED` with an explicit reason.

Every disposition requires reason and provenance. A contributing record requires an output reference. `CONSOLIDATED` and `AGGREGATED` additionally require input contributor references, an output/group reference and a transformation/policy reference. Required `UNRESOLVED` records block `Completed and validated` unless a versioned product/domain policy classifies them into another accepted terminal disposition. Reconciliation counts input records by terminal disposition; output-row counts are validated separately.
