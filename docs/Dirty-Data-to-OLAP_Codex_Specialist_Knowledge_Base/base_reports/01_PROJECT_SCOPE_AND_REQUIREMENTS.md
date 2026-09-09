# 01 — Project Scope & Requirements Report

## 1. Executive definition

Dirty Data to OLAP V1 receives one or more **tabular data sources** whose schemas, relationships, naming conventions and data quality may be inconsistent or partially broken. It discovers the data estate, profiles columns, infers hidden keys and relationships, matches semantically equivalent fields across sources, resolves duplicate entities when evidence is sufficient, proposes a canonical business model, and materializes a validated **OLAP-ready analytical layer**.

The shortest correct definition is:

> **Dirty and fragmented tabular data → evidence-backed canonical model → validated fact/dimension analytical layer.**

V1 is not a generic data platform and is not a production Data Warehouse orchestrator. It is a system for understanding and restructuring messy tabular data into a trustworthy analytical target.

---

## 2. Problem statement

Organizations often have data distributed across multiple operational systems:

- CRM
- ERP
- sales databases
- legacy databases
- manually maintained Excel/CSV files
- duplicated historical systems

Common problems include:

- foreign keys do not exist or are not declared;
- primary keys are missing, duplicated or poorly named;
- the same business concept has different names in different systems;
- one real-world customer/product appears multiple times under different identifiers;
- phone, date, currency and code formats are inconsistent;
- data contains invalid, orphaned or contradictory records;
- table structures were designed for transactions, not analytics;
- nobody has a reliable map of how systems relate.

The result is that building a Data Warehouse or an AI/ML system starts with weeks or months of manual discovery and cleaning.

Dirty Data to OLAP V1 targets this **pre-warehouse ambiguity**.

---

## 3. V1 input scope

### 3.1 Supported source categories

Required for V1:

- PostgreSQL
- MySQL / MariaDB
- Microsoft SQL Server
- SQLite for local tests and demos
- CSV
- Parquet

Optional if inexpensive to support through existing connectors:

- Excel (`.xlsx`)

Deferred unless implementation cost is negligible:

- Oracle

### 3.2 Data requirements

V1 accepts data that is:

- relational or naturally representable as tables;
- static snapshot or batch extract;
- structured enough that rows and columns have stable meaning within a source snapshot.

V1 must tolerate:

- missing constraints;
- wrong/misleading column names;
- nullable keys;
- mixed value formats;
- duplicate records;
- partial overlap between systems;
- orphan references;
- mild schema drift across snapshots.

### 3.3 Explicitly excluded inputs

Not in V1:

- Kafka/event streams
- continuous CDC
- PDFs
- images
- audio
- video
- free-form documents
- graph databases as primary source
- arbitrary nested JSON as a first-class source type

Nested JSON may be supported only if an existing ingestion library deterministically flattens it into tables. JSON understanding itself is not a V1 objective.

---

## 4. V1 output scope

The final output is an **OLAP-ready analytical package**, not merely a report.

Required outputs:

```text
output/
├── catalog/
│   ├── sources.json
│   ├── tables.json
│   ├── columns.json
│   └── declared_constraints.json
├── profiling/
│   ├── column_profiles.parquet
│   └── table_profiles.parquet
├── relationships/
│   ├── key_candidates.json
│   ├── relationship_candidates.json
│   └── accepted_relationships.json
├── semantic_mapping/
│   ├── column_matches.json
│   └── entity_hypotheses.json
├── entity_resolution/
│   ├── clusters.parquet
│   └── source_to_canonical_ids.parquet
├── canonical/
│   ├── canonical_entities.yml
│   ├── canonical_attributes.yml
│   └── source_mappings.yml
├── analytical_model/
│   ├── dimensions.yml
│   ├── facts.yml
│   ├── grains.yml
│   └── measures.yml
├── sql/
│   ├── ddl/
│   └── transformations/
├── validation/
│   ├── tests.yml
│   └── validation_report.json
└── reports/
    └── summary.html
```

At least one concrete analytical target must be materialized in V1. **DuckDB** is the recommended reference target because it is local, reproducible, fast, easy to test and naturally suited to analytical workloads. ClickHouse can be added later as a second target.

---

## 5. V1 functional capabilities

### FR-01 — Source discovery

The system must enumerate:

- databases/schemas where applicable;
- tables and views;
- columns;
- native data types;
- declared primary keys;
- declared foreign keys;
- row-count estimates or sampled counts.

### FR-02 — Data profiling

For every relevant column, collect at minimum:

- null count/ratio;
- distinct count/ratio;
- duplicate characteristics;
- min/max where meaningful;
- basic distribution summaries;
- value length summaries for strings;
- type plausibility;
- candidate semantic hints such as identifier, date, phone, email, amount, category.

### FR-03 — Candidate key discovery

The system must identify likely:

- unique keys;
- composite keys where computationally feasible;
- business identifiers;
- near-unique keys with explicit violation rates.

### FR-04 — Hidden relationship discovery

The system must discover candidate relationships even when no FK exists.

Each candidate must contain evidence such as:

- inclusion/overlap ratio;
- target uniqueness;
- source orphan ratio;
- type compatibility;
- name/semantic similarity;
- cardinality estimate.

### FR-05 — Cross-source schema matching

The system must propose that columns from different sources represent the same concept, with ranked evidence and confidence.

### FR-06 — Data quality diagnosis

The system must classify issues rather than returning an unstructured list.

Examples:

- missingness;
- invalid format;
- inconsistent representation;
- duplicates;
- referential-integrity violations;
- schema anomalies;
- suspicious domain values.

### FR-07 — Entity resolution

For suitable entities such as Customer or Product, the system must be able to link records across sources and assign a canonical entity ID.

Entity resolution must be optional per entity. Not every table should be clustered.

### FR-08 — Canonical model proposal

The system must transform source-specific tables into business-level concepts, for example:

```text
CRM.Customer + ERP.Client + Web.User
                   ↓
             Canonical Customer
```

The proposal must keep source lineage.

### FR-09 — Analytical model proposal

The system must propose:

- dimensions;
- fact tables;
- grain of each fact;
- measures;
- surrogate keys;
- source-to-target mappings.

### FR-10 — Materialization

The accepted plan must compile into executable SQL/transforms that materialize the analytical layer in DuckDB.

### FR-11 — Validation

Materialization is successful only if validation passes.

Required checks include:

- expected row counts/ranges;
- uniqueness constraints;
- referential integrity;
- null constraints where expected;
- source-to-target aggregate reconciliation;
- accepted entity-resolution consistency;
- no unexplained record loss beyond declared policy.

### FR-12 — Human review

The system must expose ambiguous decisions for review. A reviewer can:

- accept;
- reject;
- override;
- provide a semantic label;
- lock a decision for subsequent runs.

---

## 6. Non-functional requirements

### NFR-01 — Reproducibility

Every run must store:

- Dirty Data to OLAP version;
- external adapter versions;
- model/algorithm settings;
- source snapshot identifiers where possible;
- random seeds;
- accepted/rejected human decisions.

### NFR-02 — Explainability

No inferred relationship or entity merge may exist only as a number. Evidence must be inspectable.

### NFR-03 — Source safety

All V1 connections are read-only. Transformations are executed only in controlled local/analytical targets.

### NFR-04 — Scalability by sampling

V1 must not require loading entire enterprise tables into Python memory. Profiling and discovery must support configurable sampling and chunked execution.

### NFR-05 — Deterministic core

Where deterministic/statistical methods are sufficient, they take precedence over LLMs.

### NFR-06 — Pluggability

External engines must sit behind interfaces so they can be replaced independently.

### NFR-07 — Testability

Every major stage must have a deterministic test fixture with known expected output.

---

## 7. What V1 is deliberately NOT

V1 does not attempt to:

- modify source databases;
- become Airflow/Dagster;
- become dbt;
- implement production CDC;
- schedule enterprise pipelines continuously;
- provide BI dashboards;
- perform causal business analysis;
- ingest unstructured media;
- autonomously define legal/business truth;
- replace Data Engineers;
- guarantee semantic correctness when evidence is insufficient.

This boundary is essential. Without it, V1 becomes a multi-year platform project.

---

## 8. Definition of Done

V1 is complete only when all conditions below are met on the benchmark estate:

1. At least three source systems plus one file source are ingested.
2. The system profiles all target tables and columns.
3. Hidden key and relationship discovery is measured against ground truth.
4. Cross-source schema matching is measured against ground truth.
5. At least one entity family is resolved across multiple sources.
6. A canonical model is generated and source lineage is preserved.
7. At least one fact table and three dimensions are generated.
8. Grain and measures are explicitly represented.
9. The model is materialized into DuckDB.
10. Validation passes or reports precise failures.
11. Every accepted non-declared relationship has inspectable evidence.
12. A fresh clone can reproduce the demo via documented commands.

---

## 9. Success metrics

V1 should report metrics, not vague claims.

### Discovery metrics

- source/table/column discovery completeness
- key-candidate precision/recall
- relationship precision/recall

### Semantic metrics

- schema-match precision@K
- schema-match recall
- entity-resolution precision/recall/F1

### Analytical correctness

- target reconciliation pass rate
- fact-grain correctness on benchmark
- dimensional-key integrity
- unexplained record-loss rate

### Engineering metrics

- peak memory on benchmark
- runtime by stage
- reproducibility between identical runs

No fixed numeric target is hard-coded before the baseline is measured. The first implementation milestone establishes the baseline; later milestones improve it.

---

## 10. Critical review applied before approval

The initial concept had four risks. They are explicitly corrected here:

### Risk A — Calling the result “OLAP” too early

**Correction:** V1 must actually materialize a dimensional analytical target, not merely output suggested schemas. DuckDB is the reference implementation.

### Risk B — Confusing V1 with full ETL/ELT

**Correction:** V1 includes one-shot extraction/transformation/materialization, but continuous production ETL/ELT, orchestration and CDC remain outside scope.

### Risk C — Making entity resolution mandatory everywhere

**Correction:** entity resolution is opt-in per canonical entity and is used only where duplicated real-world entities are plausible.

### Risk D — Letting LLMs define business truth

**Correction:** LLMs may generate semantic hypotheses, explanations and candidate labels; acceptance requires evidence and/or human review.

**Status: APPROVED FOR IMPLEMENTATION PLANNING.**
