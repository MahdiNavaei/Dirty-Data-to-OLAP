# G0 — Product Contract

Status: `PASS`

This gate is owned by Specialist Step 01 — Product Manager / Technical Product Owner.

## Evidence

- [Product contract](../../product/PRODUCT_CONTRACT.md)
- [User journeys](../../product/USER_JOURNEYS.md)
- [Acceptance criteria](../../product/ACCEPTANCE_CRITERIA.md)
- [Scope boundary](../../product/SCOPE_BOUNDARY.md)
- [Terminology](../../product/TERMINOLOGY.md)
- [Requirements traceability](../../product/REQUIREMENTS_TRACEABILITY.csv)

## G0 conditions

| Condition | Evidence | Result |
|---|---|---|
| Scope is unambiguous | V1 is limited to evidence-first, static/batch, tabular, pre-warehouse work; required, conditional, deferred and excluded sources are classified in `SCOPE_BOUNDARY.md`. | PASS |
| OLAP-ready output is defined | `PRODUCT_CONTRACT.md` requires an approved plan, executable transformations, a materialized DuckDB target, explicit grain/measures/lineage, and passing validation/reconciliation. | PASS |
| Destructive source modification is excluded | The source-mutation policy and AC-015 prohibit source updates/deletes, destructive merges, source constraints and schema rewrites. | PASS |
| Streaming/CDC and unstructured inputs are excluded | Continuous CDC, Kafka/event streams, PDFs, images, audio, video and free-form documents are explicitly deferred or out of scope. | PASS |
| Acceptance criteria are testable | AC-001 through AC-030 define observable evidence, failure states, negative cases and ownership for later implementation gates. | PASS |

## Product identity decision

The canonical product name is `Dirty Data to OLAP`. `DataFoundry` is retained only as a clearly labeled legacy working title or technical placeholder where required by an existing package-layout example; it is not treated as a separate product.

## Limitations

This gate freezes product intent and acceptance expectations. It does not claim application implementation, source-adapter compatibility, benchmark completion, domain truth, or passage of G1-G15.
