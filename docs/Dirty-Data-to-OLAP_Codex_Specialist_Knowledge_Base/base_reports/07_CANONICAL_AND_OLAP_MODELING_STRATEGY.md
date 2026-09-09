# 07 — Canonical & OLAP Modeling Strategy

## 1. Purpose

This report defines how Dirty Data to OLAP moves from discovered/matched source structures to a business-level canonical model and then to an OLAP-ready dimensional model.

The key separation is:

```text
Source schemas
     ↓
Canonical business model
     ↓
Analytical dimensional model
```

These are not the same thing.

---

## 2. Why a canonical layer is necessary

Suppose three systems contain:

```text
CRM.Customer
ERP.Client
Website.User
```

Directly building a warehouse from all three creates duplicated semantics and brittle joins.

Dirty Data to OLAP first proposes:

```text
CanonicalEntity: Customer
```

with mappings:

```text
CRM.Customer.CustomerCode → Customer.source/business identity
ERP.Client.ClientNo       → Customer.source/business identity
Website.User.UserId       → Customer.source/business identity
```

Only after this conceptual layer is stable should a `dim_customer` be created.

---

## 3. Canonical entity discovery

V1 should use evidence from:

- schema matching;
- hidden/declared relationships;
- table naming;
- candidate keys;
- shared value domains;
- entity-resolution feasibility;
- optionally LLM semantic suggestions.

An entity hypothesis must include:

- proposed entity name;
- contributing source tables;
- identity attributes;
- descriptive attributes;
- relationship neighborhood;
- confidence/review state.

---

## 4. Canonical attributes

For each entity, map physical fields to canonical attributes.

Example:

```text
CRM.mobile
ERP.phone_number
Web.cell
   ↓
Customer.phone
```

A canonical attribute contains:

- semantic name;
- normalized type;
- source mappings;
- transform/normalization rules;
- source-of-truth policy;
- lineage.

---

## 5. Canonical identity

Do not assume source IDs are globally stable.

Recommended canonical identity strategy:

```text
canonical surrogate ID
    ↕
source_record mappings
```

Example:

```text
canonical_customer_id = CUST_000182

maps:
CRM.CustomerCode 981
ERP.ClientNo C443
Website.UserId 8821
```

This lets Dirty Data to OLAP unify entities without rewriting source keys.

---

## 6. Source-of-truth and conflict resolution

When sources disagree, use explicit policies.

Possible policies:

- `PREFERRED_SOURCE`
- `MOST_RECENT_VALID`
- `MOST_COMPLETE`
- `CONSENSUS`
- `REVIEW_REQUIRED`

Example:

```text
CRM phone updated 2026-09-01
ERP phone updated 2023-03-10
```

If policy says CRM is preferred for customer contact data, CRM wins. The reason is stored.

No LLM should silently choose a value.

---

## 7. From canonical entities to dimensions

A dimension generally represents a descriptive business entity used to slice measures.

V1 candidates include:

- Customer
- Product
- Store/Branch
- Supplier if present
- Date

Dimension eligibility signals:

- stable descriptive attributes;
- one row per entity version/current entity;
- referenced by events/transactions;
- relatively lower cardinality than event facts (not a hard rule).

---

## 8. Fact detection

Facts represent measurable events/processes.

Candidate signals:

- transaction/event-like table;
- repeated foreign-key references to dimensions;
- timestamp/date;
- numeric measures;
- high row volume;
- row represents an occurrence rather than a descriptive entity.

Examples:

- order line;
- payment;
- shipment;
- inventory snapshot.

Do not infer fact status from table name alone.

---

## 9. Grain inference — critical V1 capability

For every fact, Dirty Data to OLAP must answer:

> What exactly does one row represent?

Example:

```text
fact_order_line
```

Possible grain:

```text
one row per (order_id, line_number)
```

Validation:

```text
(order_id, line_number) must be unique
```

If no stable grain can be identified, the fact model cannot be auto-accepted.

### Grain inference signals

- candidate keys;
- functional dependencies;
- duplicate patterns;
- parent-child relationships;
- semantics of numeric/date fields;
- known source PKs.

This is one of the highest-value Dirty Data to OLAP-owned reasoning components.

---

## 10. Measures

Measures require explicit aggregation semantics.

V1 measure types:

### Additive

Can sum across all normal dimensions.

Examples:

- quantity sold;
- transaction amount.

### Semi-additive

Can sum over some dimensions but not all, commonly time.

Example:

- account balance;
- inventory level snapshot.

### Non-additive

Should not simply sum.

Examples:

- ratio;
- unit price;
- percentage.

The planner may propose a type, but ambiguous cases require review.

---

## 11. Dimension keys

Each dimension should normally have:

- surrogate warehouse key;
- business/source keys stored as attributes/alternate keys;
- source lineage.

Example:

```text
dim_customer
customer_key BIGINT  -- surrogate
canonical_customer_id STRING
crm_customer_code STRING
erp_client_no STRING
...
```

---

## 12. Slowly Changing Dimensions

V1 support should be deliberately limited.

Required:

- SCD Type 1: overwrite current analytical value in rebuilt snapshot.

Optional V1 / prepared contract:

- SCD Type 2 model fields.

Full incremental historical SCD2 maintenance belongs primarily to V2 production ETL/ELT.

However the model contract should support:

```text
valid_from
valid_to
is_current
```

so V1 does not need redesign later.

---

## 13. Date dimension

A generated `dim_date` is deterministic and useful for OLAP.

Fields may include:

- date key;
- full date;
- year;
- quarter;
- month;
- day;
- weekday;
- week number.

Locale-specific calendars can be added later; V1 should keep the reference implementation simple and explicit.

---

## 14. Fact foreign keys

Every fact FK to a dimension must be validated after materialization.

Unknown/missing dimension policy must be explicit.

Options:

- reject/quarantine fact row;
- map to a known `UNKNOWN` dimension member;
- keep NULL where analytically acceptable.

No implicit policy.

---

## 15. Analytical planning process

Recommended planner stages:

### Step 1 — Build canonical entity graph

Nodes:

- canonical entities/events.

Edges:

- accepted relationships.

### Step 2 — Classify event-like vs descriptive entities

Use structural/statistical features.

### Step 3 — Generate fact candidates

For each candidate, propose grain and measures.

### Step 4 — Validate grain against data

Reject/require review if proposed grain is not unique.

### Step 5 — Generate dimensions

Create surrogate-key strategy and mappings.

### Step 6 — Generate transform plan

Order transformations based on dependencies.

### Step 7 — Compile SQL

Materialize dimensions before facts.

### Step 8 — Reconcile and validate

Only validated models are considered ready.

---

## 16. Example end-to-end

Sources:

```text
CRM.customers(customer_code, name, mobile)
Sales.orders(order_id, client_no, order_date)
Sales.order_items(order_id, line_no, sku, qty, price)
ERP.products(product_code, title, category)
```

Evidence discovers:

```text
orders.client_no → customers.customer_code
order_items.order_id → orders.order_id
order_items.sku → products.product_code
```

Canonical model:

```text
Customer
Product
Order
OrderLine
```

Analytical model:

```text
dim_customer
dim_product
dim_date
fact_order_line
```

Fact grain:

```text
(order_id, line_no)
```

Measures:

```text
quantity = SUM(qty)
gross_amount = SUM(qty * price)
```

The `gross_amount` expression is a generic, **CONDITIONAL** modeling example only. It is not an accepted reference-benchmark measure and is not recognized revenue. A monetary expression can be accepted only after a versioned domain/generator contract defines currency/unit semantics, discount behavior where relevant and the exact measure meaning. The currently defined benchmark additive measure is `quantity` at valid OrderLine grain. This example illustrates an OLAP model shape; it does not claim a materialized benchmark output.

---

## 17. Canonical graph vs star schema

Dirty Data to OLAP should preserve both.

Canonical graph answers:

> What business concepts exist and how do source systems map to them?

Star schema answers:

> How should these concepts be organized for analytics?

Keeping both prevents the star schema from becoming the only semantic record.

---

## 18. When not to auto-generate a star schema

Require review if:

- fact grain is ambiguous;
- multiple plausible event models exist;
- measures have unclear aggregation semantics;
- currency/unit meaning is unclear;
- entity history requires SCD behavior not inferable from snapshot;
- relationship conflicts remain unresolved.

The system should proudly say “insufficient evidence” instead of producing a pretty but wrong model.

---

## 19. V1 output contracts

Minimum accepted analytical package:

```text
analytical_model.yml
canonical_model.yml
source_mappings.yml
transform_plan.yml
create_schema.sql
load_dimensions.sql
load_facts.sql
validation_tests.yml
target.duckdb
```

---

## 20. Critical review applied before approval

### Problem A — Canonical model and star schema were previously conflated

**Correction:** preserve a separate canonical semantic graph before dimensional modeling.

### Problem B — A fact table without an explicit grain is dangerous

**Correction:** no fact can auto-accept without a validated `GrainSpec`.

### Problem C — SCD2 could massively expand V1 scope

**Correction:** contract-ready, but full incremental SCD2 maintenance is deferred.

### Problem D — “Numeric column = measure” is wrong

IDs, codes and prices may not be additive measures.

**Correction:** measure classification includes semantic and aggregation rules.

### Problem E — Deduplication could erase source lineage

**Correction:** canonical IDs map source records; source identities remain traceable.

**Status: APPROVED AS V1 CANONICAL/OLAP MODELING STRATEGY.**
