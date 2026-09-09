# Specialist Step 03 — Data Architecture Review

Status: `PASS`

Owning specialist: `Step 03 — Principal Data Architect`

This is a Step 03 completion receipt, not Gate G2. G2 remains `PENDING` and is owned by the later architecture/engineering sequence.

## Evidence

- [Data architecture contract](../data-architecture/DATA_ARCHITECTURE_CONTRACT.md)
- [Source representation](../data-architecture/SOURCE_REPRESENTATION.md)
- [Key strategy](../data-architecture/KEY_STRATEGY.md)
- [Canonical model principles](../data-architecture/CANONICAL_MODEL_PRINCIPLES.md)
- [Conflict and survivorship](../data-architecture/CONFLICT_AND_SURVIVORSHIP.md)
- [Lineage and provenance](../data-architecture/LINEAGE_AND_PROVENANCE.md)
- [Dimensional modeling rules](../data-architecture/DIMENSIONAL_MODELING_RULES.md)
- [Null and unknown semantics](../data-architecture/NULL_AND_UNKNOWN_SEMANTICS.md)
- [SCD and temporal boundaries](../data-architecture/SCD_AND_TEMPORAL_BOUNDARIES.md)
- [Reference logical model](../data-architecture/REFERENCE_BENCHMARK_LOGICAL_MODEL.md)
- [Architecture invariants](../data-architecture/specs/architecture_invariants.yml)
- [Reference logical model specification](../data-architecture/specs/reference_logical_model.yml)
- [Architecture validator](../../tools/validate_data_architecture.py)

## Architecture invariant result

`DA-001` through `DA-027` are defined with scope, statement, rationale, downstream risk, verification expectation and status. They cover source immutability, snapshots, source records, key categories, canonical identity, cluster separation, scoped authority, conflicts, absence states, provenance, lineage, record accounting, model separation, grains, measures, conformance, unknown/orphan policy, SCD boundaries, cleaning layers, ER history safety and model evolution.

## Cross-artifact consistency review

- Product Contract: read-only sources, OLAP-ready target, evidence/review and no score-as-truth semantics are preserved.
- Domain Contract: benchmark authority remains benchmark-only; runtime authority remains assertion-driven.
- Internal Data Contracts: stable IDs, provenance, observation scope and evidence/decision separation are reinforced.
- Canonical/OLAP strategy: canonical and analytical layers remain separate; facts require grain; measures require aggregation semantics.
- Data Architecture: no contradictory source authority, identity, grain, SCD or record-loss rule was introduced.

## Architecture walkthroughs

### A. Customer canonicalization — PASS

CRM customer, ERP legacy client and `old_customers.csv` remain individually traceable. Identifiers are not equated by assumption. An ER cluster is not a canonical ID without an accepted identity decision. Benchmark CRM contact authority is scoped to the reference snapshot. Losing values and conflicts remain visible. `dim_customer` is derived from canonical semantics rather than directly from one source table.

### B. Order-line fact — PASS

Sales Order, OrderLine, Product, Customer and Branch remain separate concepts. `fact_order_line` means one product-level OrderLine event. The conceptual grain is Order identity plus line sequence, while the physical line-sequence field remains unresolved. Quantity is additive at valid line grain; unit price and discount rate are non-additive. Orphan relationships cannot silently become valid.

### C. Payment fact — PASS

Payment remains a distinct event. Multiple payments per order are legal. No one-payment-per-order, revenue, settlement or refund meaning was invented. The physical payment-event key remains generator-defined.

### D. Conflicted customer attribute — PASS

CRM, ERP and legacy phone values retain source references. Authority is scoped; a survivor requires policy and rationale. Conflicts remain visible and no score silently overrides domain authority.

### E. SCD boundary — PASS

Different snapshots may produce different current values while preserving snapshot provenance. V1 may rebuild a current representation, but no full SCD2 history is fabricated from a snapshot.

## Negative architecture checks

The following 14 proposals were rejected or marked invalid by the contract:

1. `cluster_42` used directly as `customer_key`.
2. `crm.customer_id` used as universal canonical Customer ID.
3. `client_no` joined to `customer_id` only because values overlap.
4. CRM declared globally authoritative.
5. `fact_order_line` without grain.
6. `fact_order_line` assigned Order rather than OrderLine grain.
7. `unit_price` summed directly.
8. `discount_rate` treated as additive.
9. Orphan Product mapped automatically to `-1`.
10. Source rows deleted after entity resolution.
11. Canonical phone retaining only the winner and discarding conflicts.
12. One snapshot transformed into fabricated SCD2 history.
13. Matcher score stored as business probability.
14. Analytical row without canonical/source lineage.

Result: `14/14 rejected or explicitly review-required`.

## Validation evidence

```text
python tools/validate_domain_docs.py
PASS: domain_docs=10 benchmark_specs=5 entities=6 relationships=5 business_rules=11 ambiguities=14

python tools/validate_data_architecture.py
PASS: architecture_docs=10 invariants=27 entities=6 relationships=5 dimensions=4 facts=2

git diff --check
PASS
```

Known unresolved architecture items are exact generator/source physical keys, record-level hidden IDs, runtime unknown-member policy, full temporal/SCD history, currency/unit metadata and payment amount semantics. These remain explicit and prevent premature physical implementation; they do not invalidate the logical architecture contract.
