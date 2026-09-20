# Validation and evidence

## Evidence classes

The repository distinguishes static contract checks, deterministic local
reference tests, real-provider CI service evidence, browser/system evidence,
and bounded local performance evidence. A report is not promoted to a
stronger class merely because a later document links to it.

## Review and correctness

Evidence is inspectable before acceptance. G5 is a validated review-only
inference result; no automation threshold is selected. G6 is a typed,
run-bound receipt covering accounting, consolidation, grain, relationships,
orphans, reconciliation, lineage, and negative controls. G7 is the accepted
real-pipeline product path, not just synthetic contract construction.

## Current evidence anchors

- [Step29 real-pipeline G7 closure](../execution/STEP29_REAL_PIPELINE_G7_CLOSURE.md)
- [Step22 data correctness review](../execution/STEP22_DATA_CORRECTNESS_REVIEW.md)
- [Step32 compatibility review](../execution/STEP32_COMPATIBILITY_REVIEW.md)
- [Step39 red-team review](../execution/STEP39_RED_TEAM_REVIEW.md)
- [Step40 developer-experience review](../execution/STEP40_DEVELOPER_EXPERIENCE_REVIEW.md)
- [Current claim/evidence matrix](../release/CLAIM_EVIDENCE_MATRIX.md)

## Reading a PASS

A gate PASS means the bounded gate contract and its recorded evidence passed.
It does not mean universal production readiness. Current evidence does not
certify production authentication, HA, multi-node recovery, external Oracle
operation, million/ten-million/100-million-row execution, SLA, exactly-once
distributed execution, or universal security.
