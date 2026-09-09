# 08 — Benchmark & Validation Plan

## 1. Purpose

Dirty Data to OLAP can easily produce an impressive demo while being wrong. Therefore the project must have a benchmark estate with known ground truth before major claims are made.

The benchmark has two jobs:

1. measure the algorithms;
2. validate that final analytical output preserves the intended business facts.

---

## 2. Benchmark design principle

Use a **synthetic but realistic multi-source company** where we intentionally introduce known corruption.

Because we created the source truth, we know:

- real entity mappings;
- true hidden relationships;
- true source equivalences;
- intended dimensional model;
- expected analytical aggregates.

This enables precision/recall rather than subjective inspection.

---

## 3. Reference benchmark estate

Working scenario: retail/e-commerce company.

### Source A — CRM / PostgreSQL

Tables:

```text
customers
customer_addresses
campaign_memberships
```

### Source B — Sales / SQL Server

Tables:

```text
orders
order_items
payments
```

### Source C — ERP / MySQL

Tables:

```text
clients_legacy
products
branches
```

### Source D — Legacy files

```text
old_customers.csv
product_aliases.csv
branch_codes.parquet
```

Optional Excel can be added after baseline.

---

## 4. Ground-truth canonical model

Hidden from the algorithms during evaluation:

```text
Customer
Product
Branch
Order
OrderLine
Payment
```

True relationships:

```text
Order.customer → Customer
OrderLine.order → Order
OrderLine.product → Product
Order.branch → Branch
Payment.order → Order
```

Target analytical model:

```text
dim_customer
dim_product
dim_branch
dim_date
fact_order_line
fact_payment
```

---

## 5. Injected corruption catalogue

The benchmark generator must create corruption deterministically with fixed seeds.

### 5.1 Missing FK declarations

Remove selected schema constraints while preserving underlying value relationships.

### 5.2 Orphan references

Inject configurable rates:

```text
0%
0.5%
2%
10%
```

This tests approximate relationship reasoning.

### 5.3 Key duplicates

Create duplicate candidate keys in controlled amounts.

### 5.4 Renamed columns

Examples:

```text
customer_id → client_no
phone → cell
product_code → sku
```

### 5.5 Value-format inconsistency

Phone:

```text
0912...
+98912...
0098912...
```

Dates:

```text
ISO
US/EU ambiguous formats where controlled
string timestamps
```

### 5.6 Null markers

```text
NULL
""
N/A
none
-
```

### 5.7 Entity duplicates

Generate duplicate customer representations across sources with:

- transliteration;
- spelling variation;
- normalized/un-normalized phone;
- missing email;
- stale addresses.

Ground-truth cluster membership is stored separately.

### 5.8 False semantic traps

Important for preventing superficial matching.

Examples:

```text
orders.status
customers.status
```

same name, different meaning.

Also create columns with similar value domains but no relationship, e.g. small integer codes.

### 5.9 Fact-grain traps

Create order header and order-line tables so the system must distinguish:

```text
order grain
```

from:

```text
order-line grain
```

### 5.10 Measure traps

Include:

- `unit_price` (non-additive across arbitrary rows);
- `quantity` (additive);
- `discount_rate` (non-additive);
- `inventory_balance` if snapshot scenario is added (semi-additive).

---

## 6. Benchmark sizes

At least three scales:

### Tiny

Purpose: unit/integration tests.

```text
~1k–10k rows/table
```

### Medium

Purpose: local realistic demo.

```text
~100k–1M rows in major fact tables
```

### Large-local

Purpose: performance/stress on developer machine.

```text
several million fact rows
```

The benchmark generator should be configurable rather than storing huge generated datasets in Git.

---

## 7. Evaluation by stage

# 7.1 Source discovery

Metrics:

- tables discovered / expected;
- columns discovered / expected;
- declared constraints preserved.

Expected: essentially 100% for supported source types.

# 7.2 Candidate key discovery

Ground truth includes true keys.

Metrics:

```text
precision = true key candidates / all proposed key candidates
recall = true detected keys / all true keys
```

Also report near-key candidates separately.

# 7.3 Hidden relationship discovery

Metrics:

- precision;
- recall;
- F1;
- false-positive count by trap type;
- recall vs orphan rate.

This is one of the most important plots/tables in the project.

# 7.4 Schema matching

Metrics:

- precision@1;
- precision@K;
- recall;
- mean reciprocal rank if useful;
- performance on renamed/semantically different columns.

# 7.5 Entity resolution

Metrics:

- pairwise precision/recall/F1;
- cluster metrics where appropriate;
- false merge rate;
- false split rate.

False merge rate should receive special attention because merging different real people/entities is more damaging than leaving some duplicates unresolved.

# 7.6 Data quality detection

For injected issues:

- detection precision;
- detection recall;
- severity correctness if ground truth defines severity;
- repair success/failure.

# 7.7 Canonical mapping

Measure whether source fields map to intended canonical attributes.

# 7.8 Analytical model

Evaluate:

- correct fact classification;
- correct dimension classification;
- grain correctness;
- measure classification;
- relationship integrity.

Some semantic modeling metrics may require benchmark-specific assertions rather than generic precision/recall.

---

## 8. End-to-end validation

The final output must reconcile with ground-truth business facts.

Examples:

### Revenue reconciliation

```text
expected gross sales from source truth
=
SUM(fact_order_line.gross_amount)
```

within exact tolerance for integers/decimals or documented numeric tolerance.

### Order count

Distinct order count should match intended valid orders.

### Customer count

After entity resolution:

```text
canonical customer count
```

should match ground-truth real customer count within measured error.

### Referential integrity

Every accepted non-null fact foreign key must match a dimension row unless the model explicitly permits UNKNOWN/NULL.

### Grain uniqueness

Fact grain keys must be unique.

### Record-loss accounting

Every excluded/quarantined record must have a reason.

```text
input rows
=
accepted analytical rows
+ intentionally aggregated rows
+ quarantined rows
+ explicitly filtered rows
```

No unexplained disappearance.

---

## 9. Regression test suite

Every discovered failure becomes a fixture.

Example regression cases:

- two integer columns with same domain but no relationship;
- phone column accidentally matching ID column because both are strings;
- cyclic relationship candidates;
- duplicated target key breaking many-to-one assumption;
- multilingual/transliterated names;
- empty table;
- all-null column;
- constant column;
- one-row table;
- high-cardinality text;
- composite key.

---

## 10. Adapter contract tests

For each external engine, create tests that confirm Dirty Data to OLAP's normalized output remains stable.

Examples:

```text
test_dlt_adapter_reflects_declared_fk
test_dataprofiler_adapter_preserves_sample_scope
test_desbordante_adapter_normalizes_ind
test_valentine_adapter_keeps_raw_matcher_scores
test_splink_adapter_outputs_stable_record_refs
```

If upstream API changes, adapter tests should fail immediately.

---

## 11. Performance benchmark

Report per stage:

- elapsed time;
- peak memory;
- rows sampled/scanned;
- candidate-pair count;
- cache hit/miss.

Do not publish only total runtime because bottlenecks need to be diagnosable.

### Hardware record

Every performance report stores:

- CPU;
- RAM;
- OS;
- Python version;
- engine versions.

---

## 12. Determinism/reproducibility tests

For fixed source snapshot + seed + configuration:

- generated candidate ordering should be stable where algorithms allow;
- accepted decisions should be identical;
- final target aggregates should be identical;
- model artifacts should hash identically except timestamps/non-semantic metadata where possible.

Probabilistic algorithms must receive explicit seeds/config where supported.

---

## 13. Failure-injection tests

Test engineering resilience:

- source connection fails;
- one table cannot be read;
- profiler crashes on a type;
- dependency worker returns failure;
- disk space insufficient during materialization;
- validation fails after SQL execution.

Expected behavior:

- stage is marked failed;
- error is preserved;
- previous successful artifacts remain intact;
- rerun can resume appropriately.

---

## 14. Minimum acceptance gates for V1 release

Before calling V1 “complete”:

1. all supported-source adapter tests green;
2. benchmark generator reproducible;
3. hidden relationship evaluation published;
4. schema-match evaluation published;
5. entity-resolution evaluation published for at least Customer;
6. grain tests green for reference facts;
7. end-to-end reconciliation passes;
8. no unexplained record loss;
9. cold-start demo from fresh clone succeeds;
10. CI runs unit tests and a small end-to-end benchmark.

Numeric quality gates should be set after the first baseline run. Do not invent impressive thresholds before knowing the realistic baseline.

---

## 15. Demo scenario for GitHub

A strong demo should show **before → reasoning → after**.

### Before

- 3 databases + CSV;
- missing FKs;
- renamed IDs;
- duplicate customers;
- mixed phone formats.

### Reasoning

Show:

- discovered profile;
- hidden relationship candidate;
- evidence card;
- entity match;
- canonical entity graph;
- fact grain.

### After

Open DuckDB and run:

```sql
SELECT
    d.year,
    p.category,
    SUM(f.net_amount) AS revenue
FROM fact_order_line f
JOIN dim_date d ON f.date_key = d.date_key
JOIN dim_product p ON f.product_key = p.product_key
GROUP BY 1, 2;
```

The point is to prove the messy sources became a queryable OLAP model.

---

## 16. Critical review applied before approval

### Problem A — Synthetic benchmark could be too easy

**Correction:** include false semantic traps, orphan rates, duplicate target keys and misleading columns.

### Problem B — Accuracy without end-to-end reconciliation is insufficient

**Correction:** validate final business aggregates and record accounting.

### Problem C — Entity resolution can look good while making catastrophic false merges

**Correction:** report false merge rate explicitly, not only overall F1.

### Problem D — Performance claims are meaningless without hardware/context

**Correction:** stage-level runtime/memory plus hardware manifest is required.

### Problem E — Upstream dependency changes can silently alter outputs

**Correction:** adapter contract tests are release gates.

### Problem F — Arbitrary pre-set accuracy targets would be dishonest

**Correction:** first release establishes measured baseline; thresholds become evidence-based afterward.

**Status: APPROVED AS V1 BENCHMARK/VALIDATION PLAN.**
