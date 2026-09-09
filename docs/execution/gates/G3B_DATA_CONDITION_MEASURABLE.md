# G3B - Data Condition Measurable

Status: `PASS` as a Step09 evidence milestone. Formal G3 Source Safety remains
`PENDING`.

## What was measured

The representative Step09 fixture measured a complete four-row staged CSV
snapshot with explicit rules for requiredness and an allowed domain. It produced
two rule-level issues: one required-value failure and one allowed-domain
failure. The quality vector reported completeness and validity independently;
uniqueness, consistency and referential integrity were explicitly unmeasured
for that run.

The same Step09 suite also exercised exact duplicate detection, expected
patterns, primitive types, numeric ranges, explicit unique rules, declared
foreign keys with two orphan references, partial-target FK inconclusive
behavior, normalization proposals and staged-batch hash failure.

## Where and how much

- Implementation: `src/dirty_data_to_olap/application/quality.py` and the
  project-owned quality contracts.
- Staged reader: `src/dirty_data_to_olap/adapters/quality/staged.py`.
- Representative execution: `tests/integration/quality/test_step09_quality.py`.
- Unit taxonomy and repair tests: `tests/unit/test_quality_engine.py`.
- Scope: 4/4 rows in the complete representative snapshot, with exact
  full-snapshot measurement semantics; no estimated rows and no overall score.
- Artifact inspection: quality JSON contains counts, safe record references,
  rule/provenance and scope metadata, but not raw values.

## Why and repairability

Requiredness, domain, pattern, type, range, uniqueness, duplicate and
normalization findings are emitted only from explicit rules. No null rate is
promoted to business requiredness, no uniqueness observation is promoted to a
key, no pattern is promoted to business meaning, and no foreign key is inferred.
Duplicate handling and quarantine are review-required; normalization can be
`AUTO_SAFE` only when its operation is explicit and reversible; business/domain
decisions remain manual or non-repairable.

## Validation and failure evidence

Every proposal carries a validation plan covering remeasurement, row accounting,
lineage, abort conditions and forbidden new issues. Tampering a staged Parquet
batch produced `INPUT_INTEGRITY_FAILED` and no clean issue result. A partially
covered FK target produced an `INCONCLUSIVE` issue rather than an orphan claim.
Provider-backed Step08 regression passed 4/4 with DataProfiler 0.13.4, and the
full repository suite passed 60 tests.

This evidence establishes measurable data-condition analysis, not final source
safety, privacy/security-gate completion or release readiness.
