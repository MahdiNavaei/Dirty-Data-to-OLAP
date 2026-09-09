# Cross-Artifact Consistency Audit

The original Step 05 self-review did not detect all cross-spec contradictions. An independent post-push review found the following defects and this repair records them rather than treating the first audit as complete.

| Independent-review defect | Resolution | Status |
|---|---|---|
| Component model reversed `SOURCE_DISCOVERY` and `SOURCE_SNAPSHOT_STAGE` and made discovery require `SourceSnapshot`. | Discovery now consumes source selection/registry metadata and produces `SourceCatalog`; snapshot consumes `SourceCatalog` plus `SamplingPolicy`. | RESOLVED |
| `SourceAdapter` did not distinguish discovery from bounded snapshot operations. | Added explicit `discover_source` and `create_bounded_snapshot` operation contracts. | RESOLVED |
| Components retained `implemented_by: deferred to Step 05` and disagreed with the ownership map. | Added implementation owner/step/status fields and validator equality checks; all components are PLANNED. | RESOLVED |
| Step 06 plan was generic scaffold-first and did not match the DBA Master Sequence. | Step 06 now owns the tested DB access/introspection substrate and hands it to Step 07; semantic ownership remains later-specialist scoped. | RESOLVED |
| Engineering gate names/evidence drifted from formal G0-G15 semantics. | Gate map rebuilt from canonical names, after-step ownership and evidence classes. | RESOLVED |
| Risk register omitted likelihood, detection, future step and status; post-gate output was not persisted. | Added machine-readable risk schema and persisted the exact post-gate result in all three receipts. | RESOLVED |

| Contract family | Producer/stage | Consumer | Storage | Gate/invalidator | Result |
|---|---|---|---|---|---|
| Source catalog/snapshot | discovery / source snapshot | profiling, dependency, matching, quality | ArtifactStore | source identity or snapshot change | PASS |
| Evidence and decisions | dependency/matching/quality → fusion | review and canonical hypotheses | ArtifactStore + ControlStore decisions | evidence/policy change | PASS |
| ReviewDecision | review policy at four checkpoints | guarded downstream stages | ControlStore metadata + immutable artifact | subject hash/compatibility change | PASS |
| ER linkage | entity resolution | canonical review/finalization | ArtifactStore | ER config/artifact change | PASS; evidence only |
| Canonical identity | canonical finalization | analytical planning/validation | ArtifactStore | identity review/survivorship change | PASS; sole map producer |
| Analytical plan | analytical planner | compilation/review/validation | ArtifactStore | canonical/grain/measure change | PASS |
| Compiled/materialized output | compiler → materializer | validation | ArtifactStore/controlled target | SQL/target change | PASS |
| Accounting/reconciliation | boundary entries → validation | run/gate status | ArtifactStore + ControlStore status | boundary/policy/target change | PASS |

The earlier Splink contradiction was corrected in both reports: both now state that `SourceRecordCanonicalMap` is downstream-owned by canonical finalization. No product or domain truth was changed.
