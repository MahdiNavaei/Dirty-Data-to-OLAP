# Dirty Data to OLAP — Reference Source-System Map

Scope: **REFERENCE BENCHMARK DOMAIN TRUTH only**. Authority is property/process/time scoped; no source is globally “truth.”

| Source | Technology | Role and represented data | Reference authority scope | Explicit non-authority scope | Temporal role / limits | Known semantic traps |
|---|---|---|---|---|---|---|
| CRM | PostgreSQL | Current customer profile, customer-address representations and campaign memberships. | For this benchmark snapshot, CRM is the reference for current customer display name, email, phone and CRM address representation when those attributes are present and valid. It owns campaign-membership events. | Not authoritative for orders, order lines, payments, ERP product/branch master data, or canonical identity by itself. | Current profile-oriented snapshot; historical validity intervals are not defined. | `customer_id` is not assumed equal to `client_no`; stale/null contact values remain evidence conflicts. |
| Sales | Microsoft SQL Server | Commercial orders, order items and payment events. | Sales owns the benchmark commercial order event, order-line event and payment-event representations. | Not authoritative for customer master attributes, product master attributes or branch master attributes. | Event snapshot; order/payment lifecycle code meanings remain source-specific until generator mapping exists. | `orders.status` is not `customers.status`; order header is not order-line grain; payment count is not constrained to one per order. |
| ERP | MySQL | Legacy client representations plus product and branch master representations. | ERP is the benchmark reference for product master and branch master properties within the snapshot; `clients_legacy` supplies historical customer identifiers/attributes only. | Not globally authoritative for customer profile, order/payment events or every conflicting attribute. | Product/branch snapshot plus legacy-client history; exact effective periods are unresolved. | `client_no` is an identifier candidate, not canonical identity; product/branch codes may be renamed or aliased. |
| Legacy files | CSV/Parquet | Historical customer records, product aliases and branch-code aliases. | Files are reference evidence for historical/alias mappings only; each file has a narrow property scope. | Not authoritative for current customer contact, product master, branch master, orders or payments. | Historical or auxiliary snapshot; file timestamps/validity windows are unresolved. | `product_aliases.csv` and `branch_codes.parquet` are mappings, not master entities; `old_customers.csv` may contain stale/partial representations. |

## Attribute/process expectations

| Subject | Expected reference owner | Decision boundary |
|---|---|---|
| Customer canonical identity | Hidden benchmark truth generated later | No source ID or contact field independently proves identity. |
| Current customer display name/email/phone | CRM, when present and valid in the benchmark snapshot | Conflicting or invalid values remain lineage-bearing evidence; runtime projects require their own assertion. |
| Customer address representation | CRM `customer_addresses` | This does not make address an identity key or define address history. |
| Historical customer identifiers | ERP `clients_legacy` and `old_customers.csv` as scoped representations | Neither is current profile authority by default. |
| Campaign membership event | CRM | Campaign code/status semantics are source-specific and generator-defined. |
| Order and order-line events | Sales | Does not imply Sales owns customer/product/branch master attributes. |
| Payment events | Sales | Does not imply revenue recognition, settlement or refund semantics. |
| Product master | ERP `products` | `product_aliases.csv` supplies alias evidence only. |
| Branch master | ERP `branches` | `branch_codes.parquet` supplies alias evidence only. |
| Calendar date | Derived from event dates | No fiscal calendar or timezone policy is asserted. |

## Runtime boundary

These are reference expectations, not default behavior for customer data. A real dataset needs a scoped `SOURCE_AUTHORITY` assertion with an attributable domain owner, evidence and effective time before a downstream component selects a survivor.
