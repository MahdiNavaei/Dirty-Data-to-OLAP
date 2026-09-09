# Dirty Data to OLAP — Source Representation

## Source identity

Every source has a stable `source_id` scoped to the project. Every physical table, view or file has a stable representation reference tied to its source. Every column has a stable representation reference tied to its table/file. Mutable display names are labels, not the sole identity.

## SourceSnapshot

A `SourceSnapshot` is a bounded observation of one source state. Its logical envelope must include:

```yaml
source_id: src_<stable-id>
snapshot_id: snap_<stable-id>
run_id: run_<stable-id>
schema_fingerprint: <versioned fingerprint>
source_fingerprint: <optional source-state fingerprint>
observed_at: <extraction or observation time>
selection_scope: <included/excluded namespaces, tables or files>
observation_scope: full | sample
sampling_policy: <method, size and seed when sampled>
adapter_reference: <adapter name/version/configuration>
```

This is a semantic requirement, not a database-specific snapshot mechanism. DBA and ingestion specialists own implementation.

## Source records

`SourceRecordReference` must retain source ID, snapshot ID, table/file reference and a stable source-local locator where available. If a declared PK exists, the locator includes its values and key scope. For a composite PK, all components and their order are retained. A unique business identifier is recorded as a candidate/business reference, not automatically as the physical locator. Without a stable key, use a deterministic snapshot-bound row reference with extraction context and provenance; do not pretend it is globally durable.

Raw/original values, parse failures, null markers and source column references remain available to downstream quality and lineage work. A normalized helper or canonical value is a new representation.

## Structural change

Schema fingerprints identify changes to table/file structure, columns, physical types, nullability and declared constraints. A changed fingerprint invalidates or rechecks dependent evidence and decisions according to later execution policy. It must not silently reuse old semantic decisions.

## Safety and observation scope

Source access is read-only in V1. Sample-derived evidence carries sampling method, rows observed, estimated population and seed. Sample evidence is never silently promoted to a full-source fact.
