# Dirty Data to OLAP — Reference Benchmark Domain

Status: `DOMAIN-REVIEWED SPECIFICATION v1`

This document is the compact overview of the **REFERENCE BENCHMARK DOMAIN TRUTH**. It is a synthetic retail/e-commerce scenario owned by the project and is not a universal business ontology.

## Estate

| Source | Technology | Tables/files | Semantic role |
|---|---|---|---|
| CRM | PostgreSQL | `customers`, `customer_addresses`, `campaign_memberships` | Customer profile/address representations and campaign-membership events. |
| Sales | Microsoft SQL Server | `orders`, `order_items`, `payments` | Commercial order, line and payment events. |
| ERP | MySQL | `clients_legacy`, `products`, `branches` | Legacy customer representations plus product and branch master representations. |
| Legacy files | CSV/Parquet | `old_customers.csv`, `product_aliases.csv`, `branch_codes.parquet` | Historical customer and alias/code representations. |

## Canonical concepts

The benchmark ground truth includes `Customer`, `Product`, `Branch`, `Order`, `OrderLine` and `Payment`. Supporting concepts are `Customer Address`, `Campaign Membership`, `Product Alias` and `Date / Calendar Date`.

## Processes and events

- customer registration/profile representation;
- order creation;
- order-line creation;
- payment event;
- product representation;
- branch attribution.

The benchmark does not define recognized revenue, gross margin, tax, refunds, returns, settlement, inventory movement or a fiscal calendar.

## Analytical semantic targets

The intended target concepts are `dim_customer`, `dim_product`, `dim_branch`, `dim_date`, `fact_order_line` and `fact_payment`. This domain document defines what their rows mean but does not define final SQL, surrogate keys or compiler behavior.

- `fact_order_line`: one product-level line event within one Order; quantity is units, unit price is per-unit and discount rate is a non-additive rate.
- `fact_payment`: one payment event associated with one Order; an Order may have zero or more payments.
- `dim_customer`, `dim_product`, `dim_branch`: analytical representations of the corresponding canonical concepts with source lineage.
- `dim_date`: derived calendar-date representation, without fiscal/time-zone semantics.

## Relationship truth

The intended relationships are `Order.customer → Customer`, `OrderLine.order → Order`, `OrderLine.product → Product`, `Order.branch → Branch` and `Payment.order → Order`. Full semantics, optionality and corruption handling are defined in `RELATIONSHIP_SEMANTICS.md` and `relationships.yml`.

## Generation boundary

The later generator must create deterministic source rows, corruption cases, source-record references, hidden canonical IDs and ground-truth mappings from these specifications. This pass does not fabricate rows, clusters, status-code dictionaries or record-level labels.
