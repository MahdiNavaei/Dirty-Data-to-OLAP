# G6 — Data Correctness

Status: `PENDING`

This gate is owned by Specialist Step 22. Step20 now provides a review-gated
synthetic/domain-reviewed OLAP package and controlled DuckDB materialization,
but this is not source-to-canonical-to-OLAP acceptance. G6 remains `PENDING`
until Step22 independently reconciles source records, canonical mappings,
record accounting, grain, lineage, referential integrity and business facts.

## Step22 current evidence — 2026-09-12

The Step22 validation boundary and deterministic reconciliation policy are
implemented. Independent retail and Device/Location/Reading truth packages
were compared with the existing Step20 materializations and Step21 semantic
artifacts. Each run emitted 21 checks: 16 `PASS`, one monetary
`NOT_APPLICABLE`, one derived `REVIEW_REQUIRED` gate check, and three required
canonical checks as `NOT_EVALUATED`.

G6 remains `PENDING` because the bound Step20 `CanonicalModel` has no
`CanonicalEntityInstance` or `SourceRecordCanonicalMap` records. Target row
counts and same-target semantic results are not accepted as a substitute for
source-to-canonical membership and deduplication evidence. Step23 remains not
started.
