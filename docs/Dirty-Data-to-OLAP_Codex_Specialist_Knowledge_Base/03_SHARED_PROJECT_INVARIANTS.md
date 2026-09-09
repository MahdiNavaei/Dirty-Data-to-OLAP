---
doc_type: shared_project_invariants
project: "Dirty Data to OLAP"
priority: highest
---

# Shared Project Invariants

These invariants are cross-role release blockers unless deliberately changed by an ADR and updated base reports.

0. **License-aware OSS reuse.** Direct reuse is permitted only when the upstream license allows the intended use and obligations are satisfied; otherwise implementation is independent. Research clones are disposable, live only under `research/oss/`, and are never runtime dependencies.
1. **Tabular V1 only.** SQL databases, CSV, Excel and Parquet are the intended source family.
2. **Evidence-first.** External tools emit evidence; project contracts define meaning.
3. **Stable IDs and provenance.** Source/table/column/record/entity/candidate/run identifiers must survive handoffs.
4. **Declared ≠ inferred.** A declared FK is not the same artifact as an inferred relationship candidate.
5. **Sample ≠ full observation.** Sampling metadata travels with every derived statistic/evidence item.
6. **IND ≠ FK.** Inclusion supports a relationship hypothesis; uniqueness/cardinality/semantics/conflicts still matter.
7. **Matcher score ≠ probability.** Valentine/native similarity scores remain evidence until calibrated decisioning.
8. **Record linkage ≠ deletion.** Source records remain traceable after entity resolution.
9. **Canonical value ≠ loss of disagreement.** Conflicts/provenance must remain inspectable.
10. **Fact requires grain.** Grain must be explicit and testable.
11. **No silent measure semantics.** Numeric columns are not automatically additive measures.
12. **Source read-only by default.** No destructive source writes in V1.
13. **LLM is optional semantic assistance.** Core deterministic pipeline must not depend on hallucinated free-form output.
14. **Every transformation has validation.** Row/entity accounting and relevant aggregates/constraints are checked.
15. **Confidence automation is calibrated.** Auto-accept thresholds require benchmark evidence; arbitrary weights are not probabilities.
16. **Third-party libraries are isolated.** Native types do not become internal cross-module contracts.
17. **Failures are explicit.** Partial work must not be marked SUCCEEDED.
18. **Runs are reproducible.** Configuration, engine/model versions, seeds and sampling scope are captured where relevant.
19. **Sensitive data is minimized.** Logs, metrics, reports and UI previews avoid unnecessary raw PII/credentials.
20. **Claims require matching evidence.** Functional, ML, security, performance and reliability claims each require their appropriate verification method.
