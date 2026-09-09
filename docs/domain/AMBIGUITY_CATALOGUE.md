# Dirty Data to OLAP — Reference Ambiguity Catalogue

Scope: **REFERENCE BENCHMARK DOMAIN TRUTH only**. A high algorithmic score never removes an ambiguity entry; the required evidence and status must still be visible.

| ID | Example / trap | Why statistical matching alone is unsafe | Evidence needed | Status | Downstream risk | Future owner(s) |
|---|---|---|---|---|---|---|
| AMB-001 | `orders.status` vs `customers.status` | Same name, different process domains. | Source/domain definitions and code mapping. | `DEFINED_TRAP`; codes unresolved | Wrong lifecycle or customer segmentation. | Domain Expert, Schema Matching |
| AMB-002 | `customer_id` vs `client_no` | Different source identifiers may map to one Customer without being equal. | Key scope, overlap, linkage labels and domain assertion. | `DEFINED_TRAP` | False joins or false merges. | Domain Expert, Entity Resolution |
| AMB-003 | `phone` vs `cell` | Same/different contact channels and stale/shared values are possible. | Attribute meaning, normalization evidence and conflict review. | `DEFINED_TRAP` | False customer merges or wrong survivorship. | Domain Expert, Canonical Model |
| AMB-004 | `product_code` vs `sku` | Codes may be source-local, versioned or aliases. | Product-master scope and alias mapping. | `DEFINED_TRAP` | Wrong product joins and aggregates. | Domain Expert, Schema Matching |
| AMB-005 | Similar small integer code domains | Equal values can represent unrelated status/category/branch concepts. | Source table context and domain code lists. | `DEFINED_TRAP` | False relationships and category corruption. | Domain Expert, Evidence Fusion |
| AMB-006 | Customer duplicates across CRM/ERP/legacy | Multiple representations are expected, but not every similar record is one person. | Hidden benchmark ID or runtime assertion plus linkage evidence. | `DEFINED_TRAP` | False merge/false split. | Domain Expert, Entity Resolution |
| AMB-007 | Same-name/different-meaning columns | Names are weak evidence without process context. | Semantic definitions and relationship neighborhood. | `DEFINED_TRAP` | Wrong canonical attribute. | Domain Expert, Schema Matching |
| AMB-008 | Different-name/same-meaning columns | Name mismatch hides a valid equivalence. | Value/structure evidence plus domain review. | `DEFINED_TRAP` | Missed mapping and record loss. | Schema Matching, Domain Expert |
| AMB-009 | Nullable or stale customer contacts | Missingness or recency does not alone establish identity or authority. | Source scope, timestamps where available and attribute policy. | `DEFINED_TRAP`; history unresolved | Wrong survivor value. | Domain Expert, Canonical Model |
| AMB-010 | Product aliases | Alias text may identify a Product but can also be ambiguous or stale. | Versioned alias mapping and product-master evidence. | `DEFINED_TRAP`; row mapping later | Duplicate products or wrong line attribution. | Domain Expert, Schema Matching |
| AMB-011 | Branch-code aliases | Legacy code files are mapping aids, not branch master truth. | Branch authority and alias evidence. | `DEFINED_TRAP`; row mapping later | Wrong branch aggregates. | Domain Expert, Canonical Model |
| AMB-012 | Order header vs OrderLine grain | Shared order ID does not make line rows duplicates. | Parent/line semantics and line-sequence key. | `DEFINED_TRAP` | Double counting and invalid facts. | Domain Expert, OLAP Engineer |
| AMB-013 | Multiple Payments per Order | Payment events are not constrained to one per order. | Event identifiers and benchmark labels. | `DEFINED`; exact event fields unresolved | Incorrect payment counts or forced deduplication. | Domain Expert, Data QA |
| AMB-014 | Amount/currency/unit semantics | Numeric values are not automatically revenue or comparable currency. | Unit/currency metadata and explicit domain assertion. | `UNRESOLVED` | Invalid reconciliation and aggregation. | Domain Expert, OLAP, Data QA |

## Required interpretation behavior

When evidence cannot resolve an entry, the downstream state is `REVIEW_REQUIRED` or `UNRESOLVED`, not an invented canonical meaning. Runtime projects must attach a scoped domain assertion before converting an ambiguity into an accepted business rule.
