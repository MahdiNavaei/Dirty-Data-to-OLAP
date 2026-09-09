# Quality Contract

Status: implemented for Specialist Step09, with G3B evidence only. This is a
measurement contract, not a business truth or repair-execution contract.

Quality analysis consumes a source-owned `SourceCatalog`, a COMPLETE and
hash-verified `SourceSnapshotResult` with `BatchReference` and
`SourceRecordReference` values, the selected `ProfileResult` and explicit
`QualityRule`/`QualityRuleSet` values. Optional future `DependencyEvidence` may
be supplied, but no later specialist is required for the Step09 engine.

The staged reader reads only immutable project-local Parquet batches. It checks
source, snapshot, table, schema, content hash, row count and record-reference
bindings before returning rows. It never reconnects to a source or writes one.

Every rule has an applicability result and measurement semantics. A missing
prerequisite, incomplete profile, partial reference target, integrity mismatch
or detector failure is retained as an explicit failure or inconclusive result;
it is never converted to zero defects or a PASS. QualityResult contains a
dimension vector and no overall score.

Quality artifacts contain rule IDs, counts, ratios, safe record references,
provenance and validation metadata. They do not persist raw source values.
RepairProposal is a proposed derived-copy action only: it has no approval,
execution or source-mutation state.

The staged reader exposes an incremental iterator over bounded Parquet batches;
the quality service does not collect the entire table. Composite declared FKs
use the exact declared source and referenced-column order. All-null keys are
skipped, all-non-null keys are checked, and partial-null composite keys are
inconclusive unless a future explicit domain policy defines another meaning.
