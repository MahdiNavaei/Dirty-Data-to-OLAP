# Dirty Data to OLAP — Reference Relationship Semantics

Scope: **REFERENCE BENCHMARK DOMAIN TRUTH only**. Desbordante/IND output can support these relations but cannot define their meaning.

| Relationship | Business meaning | Direction and conceptual cardinality | Optionality in valid benchmark | Corruption interpretation |
|---|---|---|---|---|
| `Order.customer → Customer` | A commercial Order is placed by the Customer represented by the benchmark mapping. | Many Orders to one Customer; one Customer may have zero or many Orders. | A valid benchmark Order has exactly one Customer. | Null/orphan/mismatched references are injected corruption or incomplete extraction in the benchmark snapshot; do not silently reinterpret them as anonymous orders. |
| `OrderLine.order → Order` | An OrderLine is a product-level event belonging to one Order header. | Many OrderLines to one Order; an Order may have many lines. | Every valid OrderLine has exactly one parent Order. | Missing/orphan parent is a relationship-quality failure; a line is not collapsed into its Order header. |
| `OrderLine.product → Product` | Each OrderLine concerns one sellable Product. | Many OrderLines to one Product; a Product may occur on many lines. | Every valid OrderLine has exactly one Product. | Alias or code mismatch is a mapping problem; an orphan does not create a new Product. |
| `Order.branch → Branch` | The Order is attributed to the selling Branch recorded by the benchmark. | Many Orders to one Branch; a Branch may have many Orders. | One valid Order has one Branch attribution. | Null/orphan/ambiguous branch codes are corruption or mapping failure; legacy aliases require evidence. |
| `Payment.order → Order` | A Payment record is a payment event associated with one Order. | Many Payment events to one Order; an Order may have zero or many Payments. | Payment-to-order association is required for a valid benchmark Payment; one-payment-per-order is not a rule. | Orphan payment is a referential/incomplete-snapshot issue; do not invent refund, settlement or revenue semantics. |

## Relationship evidence boundary

An inclusion dependency, name match, shared value domain or cardinality estimate is evidence for a candidate only. Acceptance requires the domain meaning above plus structural evidence, provenance and the applicable review policy. Approximate inclusion is expected after injected orphan corruption and does not change the underlying benchmark relationship.

## Grain implications

- Order grain is one commercial order event.
- OrderLine grain is one product-level line event, conceptually keyed by Order identity plus line sequence.
- Payment grain is one payment event; exact physical event-key fields remain generator-defined.

These are domain semantics, not final SQL schemas or surrogate-key instructions.
