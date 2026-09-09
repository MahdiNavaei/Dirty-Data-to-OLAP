# Dirty Data to OLAP — Step 02 Semantic Walkthrough

Scope: **REFERENCE BENCHMARK DOMAIN TRUTH**. This is a contract walkthrough, not an execution of an application pipeline.

## Customer multi-source path

`CRM customers` + `ERP clients_legacy` + `old_customers.csv` form candidate representations of a potential canonical Customer.

- The records are not declared the same merely because names, phones or addresses match.
- The later generator supplies hidden canonical Customer IDs for evaluation; a runtime project requires linkage evidence plus an attributable domain assertion.
- CRM may supply the reference current display name/email/phone for this benchmark snapshot when valid; ERP and legacy records remain historical representations for those attributes unless a scoped rule says otherwise.
- A conflicting phone or stale address is preserved as evidence and does not silently overwrite another source.
- `crm.customer_id` and `erp.client_no` are separate source identifiers; equality is not assumed.

Result: the path is semantically coherent, reviewable and safe for later entity-resolution evaluation without fabricating clusters.

## Order → OrderLine → Product path

Sales `orders` supplies an Order-header event. Sales `order_items` supplies one OrderLine event per product-level line. Each line belongs to one Order and concerns one Product. An Order may contain many lines; lines for different products are not duplicates merely because they share an Order.

ERP `products` is the benchmark product-master reference. `product_aliases.csv` supplies alias evidence only. A product-code/SKU match requires source context, value/structure evidence and the domain mapping; text similarity alone cannot create a Product.

The intended OrderLine business grain is Order identity plus line sequence. `quantity` is units and is additive at valid line grain; `unit_price` and `discount_rate` are non-additive. Exact physical columns and currency/unit metadata remain generator-defined or unresolved.

Result: the header/line trap is explicit and downstream OLAP work has a domain grain and measure meaning without receiving a SQL design.

## Payment → Order path

Sales `payments` represents payment events. Each valid Payment belongs to one Order, while an Order may have zero or more Payment events. The benchmark deliberately does not impose one-payment-per-order. An orphan payment is a referential or incomplete-snapshot issue, not evidence of a refund, settlement or recognized revenue.

Result: payment event grain is defined without inventing accounting semantics.

## Order → Branch path

ERP `branches` is the benchmark branch-master reference. `branch_codes.parquet` supplies legacy alias evidence. A valid Order has one Branch attribution; null, orphan or ambiguous codes remain corruption/mapping failures. A branch code is not a generic integer relationship and does not establish customer identity.

Result: branch attribution and alias handling are explicit without making the ERP globally authoritative.

## Representative business questions

| Question | Contract answer |
|---|---|
| Does `crm.customer_id` necessarily equal `erp.client_no`? | No. They are source-scoped identifiers that may map through hidden benchmark truth or reviewed linkage evidence. |
| Does `orders.status` mean the same thing as `customers.status`? | No. They are source/process-specific domains; actual codes remain unresolved until mapped. |
| If two customers share a phone, are they one Customer? | Not automatically. Shared contact values are evidence and may be stale/shared. |
| Can one Order contain many OrderLines? | Yes. That is the reference benchmark order/header-to-line semantics. |
| Can one Customer have many Orders? | Yes. The relationship is many Orders to one Customer. |
| Can one Order have multiple Payments? | Yes. Zero or more Payment events are allowed; one-payment-per-order is not assumed. |
| Is `unit_price` additive? | No. It is a per-unit value. |
| Is `quantity` additive at valid OrderLine grain? | Yes, as ordered units, subject to unresolved value-domain checks and valid grain. |
| Does duplicate data necessarily mean bad data? | No. Exact rows, key duplicates, multi-source representations and repeated events are distinct classes. |
| Can a domain assertion silently override contradictory evidence? | No. Both assertion and observation remain visible and the subject becomes conflicted/reviewable until resolved. |

## Valid and invalid interpretations

Invalid:

- “Both columns are named `status`, therefore they are equivalent.”
- “97% inclusion means this is definitely an FK.”
- “The same phone number means the same Customer.”
- “CRM is always correct” or “ERP is always truth.”
- “Every numeric sales column is additive.”
- “Duplicate order rows are necessarily errors.”

Valid:

- “The reference benchmark defines Sales as the scoped owner of order events; source evidence and the benchmark specification are cited.”
- “A candidate Customer linkage has overlapping identifiers, normalized contact evidence, source scope and a hidden benchmark label or attributable runtime assertion.”
- “An OrderLine relationship is accepted only with the domain meaning, parent/line evidence, explicit grain and review state.”
- “A conflicting domain assertion and measured observation are both retained with provenance and effective time.”

## Walkthrough result

`PASS` for the Step 02 semantic contract: all required paths produce explicit meaning or an explicit unresolved/review state; no benchmark choice is represented as universal customer-domain truth.
