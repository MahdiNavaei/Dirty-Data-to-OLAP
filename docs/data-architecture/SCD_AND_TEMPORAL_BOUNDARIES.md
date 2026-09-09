# Dirty Data to OLAP — SCD and Temporal Boundaries

## V1 boundary

V1 supports snapshot/rebuild behavior compatible with a current Type 1 analytical representation. A run may produce a current canonical/dimensional view from a bounded source snapshot, with source snapshot provenance preserved.

The logical model may carry `valid_from`, `valid_to` and `is_current` where an explicit source/domain policy supports them. These fields are future-ready contract fields, not evidence that historical versions exist.

## Prohibited inference

The architecture must not:

- fabricate historical versions from one snapshot;
- infer change history solely from differing current values;
- backdate authority without evidence;
- turn optional SCD2-compatible fields into incremental historical ETL;
- overwrite historical source evidence when entity linkage changes.

Full incremental SCD2 maintenance is deferred beyond V1. A later specialist must define source change capture, effective-time semantics, restatement policy and validation before claiming it.

## Reference benchmark temporal semantics

The current benchmark is a static/batch synthetic estate. Customer profile, product and branch authority examples are snapshot-scoped; legacy records may be historical representations, but exact validity intervals are unresolved. `dim_date` is a deterministic calendar-date concept without fiscal, timezone, holiday or regional calendar semantics.

## Snapshot comparison

If two snapshots show different customer phone values, the system preserves both source values and snapshot provenance, applies only a scoped survivorship policy for the current analytical representation, and does not manufacture an SCD2 history.
