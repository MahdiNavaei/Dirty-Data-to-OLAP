# Dirty Data to OLAP — Dimensional Modeling Rules

## Fact versus dimension

A **dimension** is a descriptive analytical entity with explicit row meaning, identity and lineage. A **fact** is a measurable event/process or explicitly modeled snapshot with explicit grain and measures. Classification requires combined domain event meaning, relationship position, grain, key behavior, numerical semantics and temporal behavior. Table names, row counts and numeric columns alone are insufficient.

## Dimension invariants

Each dimension requires:

- explicit row/grain meaning;
- a project-owned analytical surrogate key;
- source, business and canonical identifiers preserved separately;
- lineage to canonical/source representations;
- explicit null/unknown policy;
- explicit SCD/temporal policy;
- no silent duplication of conflicting attributes across conformed dimensions.

Reference dimensions are `dim_customer`, `dim_product`, `dim_branch` and deterministic `dim_date`.

## Conformed dimensions

A dimension is conformed across facts only when it shares compatible canonical semantic entity, key strategy, attribute meanings, temporal/SCD interpretation, authority policy and domain scope. Matching column names such as `customer_id` or `product_id` do not establish conformance.

## Fact invariants

Every fact requires a human-readable grain and machine-testable grain keys/validation. Ambiguous or non-unique grain is `REVIEW_REQUIRED`; it cannot be auto-accepted or materialized as an accepted fact. Fact foreign keys must follow accepted relationships and retain lineage.

Reference facts are `fact_order_line` and `fact_payment`. Their semantic grains and unresolved physical key fields are defined in `REFERENCE_BENCHMARK_LOGICAL_MODEL.md`.

## Unknown and orphan policy

No implicit unknown-member mapping is allowed. A later model policy must explicitly select reject/quarantine, approved UNKNOWN, or nullable linkage. Reference benchmark orphan facts remain corruption/evaluation cases and cannot be turned into valid relationships merely to satisfy referential integrity.

No DDL, physical types or compiler behavior is defined here.
