# Dirty Data to OLAP V1 User Journeys

System actions and human decisions are deliberately separated. A plausible intermediate artifact is not a validated final result.

## Primary journey: fragmented sources to validated analytical target

| Step | System action | Human action / decision | Required product state |
|---:|---|---|---|
| 1 | Open or create a project and record configuration identity. | Select project purpose and working inputs. | Project exists; no source mutation authority is granted. |
| 2 | Connect to a read-only SQL profile or import CSV/Parquet/SQLite reference data. | Provide authorized inputs and inclusion/exclusion rules. | Inputs are classified as supported, conditional or blocked. |
| 3 | Discover tables, columns, types, row estimates and declared constraints. | Inspect the physical catalog and exclude irrelevant objects. | Catalog artifact with source IDs and provenance. |
| 4 | Sample/profile columns and tables with observation scope. | Inspect missingness, cardinality, patterns and quality signals. | Profiles distinguish sample estimates from full observations. |
| 5 | Generate candidate keys, dependencies, relationships and quality issues. | Review evidence and identify questions requiring domain input. | Candidates are hypotheses, not confirmed truth. |
| 6 | Generate cross-source schema-match candidates and preserve raw matcher evidence. | Accept, reject, override, label or defer mappings. | Mapping decisions have evidence and review state. |
| 7 | Propose optional entity-resolution specifications for selected entity families. | Approve identity fields, blocking/review policy and unresolved conflicts. | Source records remain traceable; no source deletion occurs. |
| 8 | Propose canonical entities, attributes, conflicts and lineage. | Review canonical concepts and source mappings without inventing domain truth. | Canonical proposal is explicit about uncertainty. |
| 9 | Propose dimensions, facts, keys, grain and measures. | Review grain, aggregation semantics and ambiguous interpretations. | No fact is accepted without explicit validated grain. |
| 10 | Compile the approved plan into executable SQL/transforms. | Approve materialization when review is required. | Transformation plan is inspectable and controlled. |
| 11 | Materialize the analytical target in DuckDB. | Do not treat SQL success alone as product success. | Target exists with artifact lineage. |
| 12 | Run validation, reconciliation and record-loss accounting. | Inspect failures, quarantines and unresolved issues. | Final result is either validated or an explicit non-success state. |
| 13 | Present the analytical package and summary report. | Use the result only within its evidence and review status. | OLAP-ready means plan + executable transforms + materialized target + passing validation. |

## Ambiguous evidence journey

1. The system detects conflicting semantic and structural evidence.
2. It records a conflict and emits `review_required`; it does not average the conflict into a definitive truth.
3. The reviewer inspects evidence, scope, provenance and explanation.
4. The reviewer accepts, rejects, overrides, labels or defers the decision.
5. A human decision is recorded as human evidence and replayed only when compatible with the relevant source fingerprint/policy.

## Failed validation journey

1. Materialization completes but a grain, referential-integrity, reconciliation or record-accounting check fails.
2. The run becomes `validation failed`, retaining artifacts and exact failure evidence.
3. The system does not present the target as validated or OLAP-ready.
4. The user corrects input, policy or review decisions and reruns the affected stages.

## Unsupported source journey

1. The user selects an excluded or deferred source type.
2. The system reports `blocked by unsupported input` with the source classification and supported alternatives.
3. No source write, silent conversion or partial-success claim occurs.

## Source access failure journey

1. Connection, authorization, timeout or safe-introspection failure occurs.
2. The affected stage records an explicit access failure and preserves prior valid artifacts.
3. The run is blocked or failed according to scope; downstream stages cannot claim complete success from missing input.

## Review/reject/override journey

1. A candidate decision is shown with evidence, observation scope, provenance, conflicts and current state.
2. The reviewer chooses accept, reject, override, label or lock where supported.
3. The decision records actor/reason and remains distinct from algorithmic evidence.
4. Downstream materialization consumes only decisions permitted by policy.

## Unresolved semantics journey

If business meaning, source precedence, unit/currency interpretation, entity identity or measure semantics remains unresolved, the system says `blocked by unresolved semantics` or `review required`. It does not invent domain truth or silently choose a canonical value.
