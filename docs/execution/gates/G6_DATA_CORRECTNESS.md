# G6 — Data Correctness

Status: `PASS`

This gate is owned by Specialist Step 22. This is the critical post-Step22
integrity closure only; it is not Primary Prompt 23/41 and Step23 has not
started. Step20 provides the controlled OLAP artifacts, while Step19 runtime
canonical finalization and Step22 independent QA provide the missing
source-to-canonical-to-OLAP evidence.

## Step22 current evidence — 2026-09-12

The corrected validation boundary executes the project-owned
`CanonicalFinalizationService`, produces finalized instances and maps, binds
the actual typed analytical dataset/input binding, derives runtime accounting
for both accounting boundaries, and evaluates every compiled fact scope.
Independent truth is loaded only at the QA boundary and is never used as a
transformation input.

| Run | Truth records | Canonical instances/maps | Accounting | Fact rows | Required checks | G6 |
|---|---:|---:|---|---:|---:|---|
| retail | 12 | 11 / 12 | source 12; canonical 11 | 3 | 24/24 PASS | PASS |
| generic Device/Location/Reading | 7 | 7 / 7 | source 7; canonical 7 | 3 | 24/24 PASS | PASS |

Each report contains 25 checks: 24 required blocking checks and one
informational monetary `NOT_APPLICABLE` check with a domain-contract reason.
`no_blocking_discrepancy` is `PASS`, both reports are eligible, and each has
zero discrepancies. Typed aggregate expectations include retail quantity
`SUM` global/product/branch slices and generic temperature `MAX`
global/location slices. Canonical membership, exact group counts, events,
relationships, facts, grain, keys, FKs, dates, lineage, semantic classes and
all artifact/hash/target bindings are included.

Retail's reviewed duplicate group is two source records mapped to one
canonical instance, with both source dispositions `CONSOLIDATED`; it is not
record loss. The validator detects allocation, grain, valid-but-wrong FK,
measure compensation, lineage, filtering, orphan, key, stale-binding,
accounting, canonical-membership, oracle-mismatch, removed-instance and
multi-fact controls. Evidence is recorded in the two validation run
directories and the full temporal history, including the original blocked
attempt, is retained in `STEP22_DATA_CORRECTNESS_REVIEW.md` and
`SPECIALIST_EXECUTION_LOG.md`.
