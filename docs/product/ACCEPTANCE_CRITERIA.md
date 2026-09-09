# Dirty Data to OLAP V1 Acceptance Criteria

These criteria are objectively verifiable product expectations. They describe future acceptance evidence; they do not claim current implementation.

## Product boundary and sources

- **AC-001 — Product boundary:** The product contract names Dirty Data to OLAP V1, identifies pre-warehouse ambiguity as the problem, and lists explicit non-goals. Evidence: `PRODUCT_CONTRACT.md` and `SCOPE_BOUNDARY.md` review.
- **AC-002 — Source classification:** Every source category in the scope is assigned exactly one product classification: required release, required reference/demo, optional/conditional, deferred or excluded. Evidence: `SCOPE_BOUNDARY.md` and traceability audit.
- **AC-003 — Dirty-data tolerance:** The product contract lists observable tolerated conditions including missing constraints, weak keys, orphan references, inconsistent values, duplicates, partial overlap and schema drift. Evidence: contract review and benchmark fixture inventory.

## Functional outcomes

- **AC-004 — Source discovery:** For each required source path, a representative run records sources, tables/views, columns, physical types, declared PK/FK metadata and row estimates or sampled counts. Evidence: source adapter contract tests.
- **AC-005 — Profiling:** Each relevant column profile records nulls, distinctness/cardinality, applicable distributions/patterns, physical type and sample/full observation scope. Evidence: profiling contract tests.
- **AC-006 — Keys and relationships:** Candidate keys and hidden relationships include evidence such as uniqueness, inclusion/overlap, orphan rate, type compatibility, semantics and cardinality; an IND or matcher score alone cannot be a confirmed FK. Evidence: dependency/evidence-fusion evaluation.
- **AC-007 — Schema matching:** Cross-source mapping candidates preserve raw matcher signals, value/structural evidence, provenance and review state. Evidence: schema-matching adapter and decision tests.
- **AC-008 — Quality diagnosis:** Quality findings distinguish observation, issue, repair proposal, approval and execution, and classify completeness, validity, consistency, duplication and referential problems. Evidence: quality fixtures.
- **AC-009 — Optional entity resolution:** Selected entity families can produce source-record linkage artifacts with match evidence and canonical-map traceability; source records are retained. Evidence: linkage evaluation and lineage check.
- **AC-010 — Canonical model:** A proposed canonical entity/attribute model records source mappings, transforms, conflicts, source authority policy and provenance. Evidence: canonical contract review.
- **AC-011 — Analytical model:** Proposed facts and dimensions include keys, explicit fact grain, measure semantics, lineage and validation rules. Evidence: analytical-model contract tests.
- **AC-012 — Materialization:** An accepted analytical plan compiles into executable SQL/transforms and materializes at least one controlled DuckDB target. Evidence: compiler/materializer integration test.
- **AC-013 — Validation:** Post-materialization validation checks grain, keys, referential integrity, expected counts/ranges, aggregate reconciliation and unexplained record loss. Evidence: Data QA reconciliation suite.
- **AC-014 — Human review:** Reviewable decisions support accept, reject, override, label and lock/persist where applicable, with human action distinct from algorithmic evidence. Evidence: review-flow acceptance tests.

## Safety, reproducibility and failure behavior

- **AC-015 — Source read-only:** Attempts to update/delete source rows, merge source records destructively, create source constraints or rewrite source schemas are rejected or impossible through the V1 product path. Evidence: database-security and negative acceptance tests.
- **AC-016 — Record accounting:** Input rows/entities reconcile to accepted analytical rows, intentional aggregation, explicit filters and quarantine; no unexplained disappearance is accepted. Evidence: end-to-end reconciliation.
- **AC-017 — Reproducibility:** A fixed source snapshot, configuration, engine/model versions, sampling scope and seed produce reproducible decisions and target aggregates within documented timestamp exceptions. Evidence: determinism tests.
- **AC-018 — Explainability:** Every accepted inferred relationship, mapping or merge exposes evidence references, scope, provenance, decision state and conflicts. Evidence: artifact schema inspection.
- **AC-019 — Explicit failure:** Unsupported input, inaccessible source, stage failure, unresolved review, validation failure and partial completion are distinguishable from validated success. Evidence: failure-path tests.

## Negative and ambiguity cases

- **AC-020 — Unsupported source blocked:** An excluded/deferred source produces an explicit blocked state and no unsupported-success claim.
- **AC-021 — Source access blocked:** An inaccessible or unsafe source produces an explicit access failure while preserving prior valid artifacts.
- **AC-022 — Conflicting evidence review:** Semantic/structural conflict produces `review_required` or equivalent explicit state regardless of a raw score.
- **AC-023 — Conflicting mapping:** Same-name/different-meaning or different-name/same-meaning traps cannot be auto-confirmed from name similarity alone.
- **AC-024 — Unsafe repair:** Failed parsing or ambiguous normalization is quarantined or reviewed; it is not silently coerced to NULL or written to the source.
- **AC-025 — Invalid grain:** A proposed fact with non-unique or ambiguous grain cannot be accepted as an OLAP-ready fact without review and correction.
- **AC-026 — Score semantics:** Uncalibrated scores are displayed/stored as scores or confidence bands, never as probabilities or confirmed truth.
- **AC-027 — Fresh-clone demonstration:** A clean clone can reproduce the documented reference demonstration without requiring a research clone or undeclared external local state. Evidence: later DX/CI acceptance.
- **AC-028 — No domain invention:** Product artifacts mark organization-specific semantics, business keys, source precedence and domain status meanings as unresolved until domain evidence is supplied. Evidence: Step 02 review.

## User and review acceptance

- **AC-029 — Primary journey:** A representative Data Engineer/Analytics Engineer/Data Architect user can follow the documented connect/import → discover → profile → infer → review → model → materialize → validate journey once the later implementation exists.
- **AC-030 — Review responsibility:** The product clearly identifies which actions are system-generated evidence and which decisions require human responsibility; no autonomous business-truth claim is made.
