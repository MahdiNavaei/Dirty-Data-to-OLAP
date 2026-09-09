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

Every source record/entity receives explicit accounting status such as `ACCEPTED`, `MAPPED`, `LINKED`, `AGGREGATED`, `FILTERED_BY_EXPLICIT_RULE`, `QUARANTINED`, `REJECTED` or `UNRESOLVED`. Many input records mapped to one output row must be represented as intentional aggregation. No record may disappear without a reason, lineage and applicable reconciliation rule.
