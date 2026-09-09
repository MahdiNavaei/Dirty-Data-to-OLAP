# Dirty Data to OLAP — Reference Benchmark Logical Model

Scope: **REFERENCE BENCHMARK DOMAIN TRUTH plus universal architecture invariants**. This is a logical model, not SQL or DDL.

## Canonical concepts

`Customer`, `Product`, `Branch`, `Order`, `OrderLine` and `Payment` are the benchmark canonical concepts. Their domain meanings and source roles are defined in `docs/domain/`.

## Relationships

The model retains exactly the five Step 02 relationships:

- `Order.customer → Customer`;
- `OrderLine.order → Order`;
- `OrderLine.product → Product`;
- `Order.branch → Branch`;
- `Payment.order → Order`.

They require accepted domain/evidence decisions, source-record lineage and explicit corruption handling.

## Analytical concepts

| Concept | Semantic source | Row meaning | Key category | Lineage/validation requirement | Temporal expectation |
|---|---|---|---|---|---|
| `dim_customer` | Customer | One accepted analytical representation of a canonical Customer under the chosen snapshot policy. | Analytical surrogate plus preserved canonical/source IDs. | Trace to canonical mapping, source records, authority and survivorship decision. | V1 current snapshot/Type 1-compatible; no fabricated history. |
| `dim_product` | Product | One accepted analytical representation of a canonical Product. | Analytical surrogate plus canonical/source product identifiers. | Trace to ERP/alias evidence and accepted mapping. | V1 snapshot; history unresolved. |
| `dim_branch` | Branch | One accepted analytical representation of a canonical Branch. | Analytical surrogate plus canonical/source branch identifiers. | Trace to ERP/legacy alias evidence and accepted mapping. | V1 snapshot; history unresolved. |
| `dim_date` | Calendar Date | One deterministic calendar-date representation used to group events. | Analytical date key plus date value. | Trace to source event date and calendar derivation policy. | No fiscal/timezone semantics. |
| `fact_order_line` | OrderLine | One product-level OrderLine event. | Analytical surrogate references plus retained event/degenerate identifiers where justified. | Trace to OrderLine, Order, Product, Customer and Branch mappings. | Snapshot/rebuild; no fabricated history. |
| `fact_payment` | Payment | One Payment event associated with one Order. | Analytical surrogate references plus payment event reference. | Trace to Payment, Order and accepted downstream relationships. | Snapshot/rebuild; accounting meaning unresolved. |

## Fact grains

- `fact_order_line`: one product-level OrderLine event; conceptual business key is Order identity plus line sequence. Physical line-sequence field is `UNRESOLVED — benchmark generator specification required`.
- `fact_payment`: one Payment event associated with one Order. Physical payment-event key is `UNRESOLVED — benchmark generator specification required`.

Until concrete source/generator mappings establish machine-testable keys, these facts cannot be automatically materialized as accepted facts.

## Measures

- `quantity`: ordered units; additive at valid OrderLine event grain subject to row accounting.
- `unit_price`: per-unit value; `NON_ADDITIVE`.
- `discount_rate`: rate; `NON_ADDITIVE`.
- Payment amount, currency conversion, recognized revenue and gross margin: unresolved/outside current benchmark.

## Unresolved items

The logical model intentionally leaves exact generator columns, record-level hidden IDs, status codes, currency/unit metadata, full validity history and unknown-member policy for runtime projects unresolved. These are not reasons to invent physical architecture.
