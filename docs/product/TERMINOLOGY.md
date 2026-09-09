# Dirty Data to OLAP V1 Terminology

These terms are operational product language. They do not replace later domain decisions.

| Term | Operational definition |
|---|---|
| Dirty Data to OLAP | The canonical product name for V1. `DataFoundry` is a legacy working title, not a second product. |
| Source | An authorized SQL connection or file input from which the system reads a snapshot/batch. |
| Source snapshot | The identifiable, bounded observation of a source used by a run, including fingerprint/time/configuration where available. |
| Declared constraint | A PK, FK, UNIQUE or NOT NULL fact reported by source metadata; it is distinct from inferred evidence. |
| Inferred relationship | A project hypothesis supported by measured structural, semantic or value evidence; it is not automatically a business FK. |
| Relationship candidate | A proposed relationship with stable ID, scope, evidence references, cardinality hypothesis and review state. |
| Evidence | A measured observation, estimate, algorithm output or human assertion with provenance and semantics. |
| Confidence score | An operational ranking/decision signal whose semantics and calibration must be documented; it is not automatically a probability. |
| Probability | A calibrated statistical quantity supported by evaluation; a matcher score or heuristic confidence is not one by default. |
| `review_required` | An explicit state meaning available evidence is insufficient, conflicting or policy requires a human decision. |
| Canonical entity | A project-proposed business-level concept that groups source representations while preserving source-record mappings and disagreement. It is not domain truth merely because it is canonical. |
| Entity resolution | Evidence-based linkage of source records to candidate real-world entities; it does not delete source records. |
| Duplicate | A classified condition: exact row duplicate, business-key duplicate or probable entity duplicate. These are separate problems. |
| Repair proposal | A suggested transformation or quarantine action based on an observed issue; it is not approval or execution. |
| Accepted transformation | A reviewed or policy-authorized transformation applied only to controlled analytical/staging data with lineage and validation. |
| Dimension | A descriptive analytical structure used to slice facts, with explicit key and lineage rules. |
| Fact | An analytical event/process structure whose row meaning and measures are explicit. |
| Grain | The exact statement of what one fact row represents, plus machine-testable uniqueness keys. |
| Measure | A modeled analytical value with explicit additive, semi-additive or non-additive aggregation semantics. |
| OLAP-ready | A package with an approved plan, executable transformations, materialized DuckDB target, explicit grain/measures/lineage and passing validation/reconciliation. |
| Validation | Executed checks of structural, semantic, key, grain, reconciliation and record-accounting conditions. |
| Reconciliation | Comparison of source/canonical/analytical counts and aggregates with explicit tolerances and reasons for differences. |
| Provenance | The origin and execution context of an artifact/evidence item, including source, run, engine/configuration and scope. |
| Lineage | Traceability from analytical/canonical output back to source tables, columns and records where applicable. |
| Unsupported | An input or action outside the frozen source/product boundary; it must result in an explicit blocked state. |
