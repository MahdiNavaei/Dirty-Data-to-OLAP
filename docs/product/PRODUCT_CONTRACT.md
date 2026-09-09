# Dirty Data to OLAP V1 Product Contract

Status: `DEFINED` — this document freezes product intent and acceptance expectations. It does not claim that the application is implemented.

## 1. Product identity

- Canonical name: **Dirty Data to OLAP**
- Version: **V1**
- Legacy working title: `DataFoundry`; this is not a separate product.
- Product category: evidence-first pre-warehouse data understanding and analytical-model materialization.

## 2. Problem

Data teams receive fragmented tabular data whose schemas, constraints, naming, values, relationships and entity identities are inconsistent or partially broken. Before a trustworthy warehouse or analytical layer can be built, they must discover what the sources contain, understand quality and relationship evidence, resolve ambiguity, and preserve lineage while producing a validated analytical target.

Dirty Data to OLAP V1 addresses this pre-warehouse ambiguity. It turns dirty and fragmented tabular sources into an evidence-backed, reviewed canonical model and a validated OLAP-ready analytical package. It is not a generic data platform or a production enterprise ETL orchestrator.

## 3. Primary users

### Data Engineer

Provides read-only source access or file inputs, controls inclusion and sampling policy, inspects discovery and quality evidence, and decides which source-level issues require attention.

### Analytics Engineer

Reviews semantic mappings, canonical-to-analytical mappings, fact grain and measure semantics, then validates the analytical result for downstream use.

### Data Architect

Reviews canonical entities, keys, lineage, relationship interpretations, grain and source-to-target traceability. Organization-specific business meaning remains subject to domain review in Specialist Step 02.

### Designated human reviewer

Reviews ambiguous relationships, mappings, entity linkages, repairs, canonical conflicts and analytical interpretations. A reviewer may accept, reject, override, label or lock a decision where the workflow supports it.

## 4. What the user provides

The user provides one or more of:

- read-only connection profiles for supported SQL sources;
- CSV or Parquet file inputs;
- a SQLite reference/demo source for local reproducible tests;
- optional XLSX input when the conditional connector path is available;
- source inclusion/exclusion rules and sampling policy;
- human decisions that should be replayed on later compatible runs.

Connection credentials are supplied through a future secret-safe configuration mechanism and must not be placed in committed artifacts. This contract does not define a credential implementation.

## 5. Input conditions V1 must tolerate

The product must make the following observable conditions explicit rather than hiding them behind a generic “dirty data” label:

- missing or misleading primary/foreign-key declarations;
- weak, nullable, duplicated or near-unique key candidates;
- undeclared relationships and orphan references;
- inconsistent physical and logical types;
- mixed phone, date, timestamp, category, currency or unit representations;
- duplicate rows, duplicate business keys and probable duplicate entities as distinct issue classes;
- partial overlap between source systems;
- misleading or differently named equivalent columns;
- same-name/different-meaning columns;
- mild schema drift between snapshots;
- transaction-oriented structures that require analytical modeling;
- invalid or unparseable values requiring quarantine or review.

## 6. System responsibilities

The product is responsible for:

1. discovering supported source catalogs and declared metadata;
2. profiling columns and tables with sample/full observation scope;
3. generating key, dependency and relationship evidence;
4. generating cross-source schema-match candidates;
5. diagnosing classified data-quality issues;
6. optionally resolving selected entity families while preserving source records;
7. proposing canonical entities, attributes and source mappings with provenance;
8. proposing dimensions, facts, explicit grain and measure semantics;
9. exposing ambiguous decisions for human review;
10. compiling an approved analytical plan into executable transformations;
11. materializing a controlled DuckDB analytical target;
12. validating constraints, grain, aggregates, lineage and record accounting.

External engines are evidence producers behind project-owned adapters. Their native objects, scores and probabilities do not become project truth or cross-stage contracts.

## 7. Human responsibilities

The user/reviewer remains responsible for:

- confirming organization-specific business meaning;
- resolving ambiguous mappings and entity merges;
- accepting or rejecting repair proposals;
- reviewing source-of-truth and conflict policies;
- approving a materialization plan when review is required;
- deciding whether unresolved issues are acceptable for the intended analytical use;
- interpreting validation failures and correcting inputs or decisions.

The product must never imply that an uncalibrated score, LLM suggestion or plausible schema is a confirmed business fact.

## 8. Source mutation policy

V1 source systems are read-only by default and the product has no destructive source-write authority. It may read, introspect, sample, extract, profile, propose repairs, generate SQL/transformation artifacts and materialize transformed data into controlled analytical targets.

It must never silently update or delete source rows, merge source records destructively, create production constraints, or rewrite source schemas. Human approval does not grant source-write authority. Entity resolution maps source records; it does not delete them.

## 9. Human review policy

The following remain reviewable product decisions when evidence is ambiguous, conflicting or below a future calibrated policy:

- inferred relationships and cardinality;
- cross-source schema mappings;
- entity merges and linkage thresholds;
- data repair proposals and category mappings;
- semantic labels;
- canonical conflicts and source precedence;
- fact/dimension interpretation, grain and measure semantics;
- transformation/materialization plan;
- validation or reconciliation failures.

Supported review actions are accept, reject, override, label and lock/persist where the specific workflow supports them. Until empirical evaluation establishes calibrated automation, scores are not probabilities and no arbitrary numeric auto-accept threshold is part of this contract.

## 10. Output package and OLAP-ready definition

An OLAP-ready V1 result is not a schema suggestion, a profiler report, a YAML-only model or successful SQL execution. It is a package containing:

```text
catalog/
profiling/
relationships/
semantic_mapping/
entity_resolution/
canonical/
analytical_model/
sql/
validation/
reports/
```

The package must include, as applicable to the run:

- source/table/column catalog and declared constraints;
- profile artifacts with observation scope;
- relationship and mapping evidence plus decisions;
- entity-linkage artifacts with source-record traceability;
- canonical entities, attributes, mappings and provenance;
- dimensions, facts, explicit grain, keys and measure aggregation semantics;
- executable SQL/transformation artifacts;
- at least one materialized DuckDB analytical target;
- validation and reconciliation results;
- accounting for accepted, aggregated, filtered and quarantined records.

The result is product-successful only when the approved plan is materialized and post-materialization validation/reconciliation passes. A result with unresolved review items, blocked inputs or failed validation must use an explicit non-success state.

## 11. Product-level run states

These are product semantics, not a backend enum prescription:

- **Completed and validated:** required review is complete, target materialized, validation/reconciliation passed.
- **Completed with unresolved review items:** deterministic work exists, but required decisions remain; not a validated final result.
- **Validation failed:** materialization ran but one or more required checks failed; preserve artifacts and reasons.
- **Blocked by source/access issue:** input is unsupported, inaccessible or unsafe to use.
- **Blocked by unresolved semantics:** evidence conflicts or business meaning is insufficient for a safe decision.

Partial work must never masquerade as full product success.

## 12. V1 functional contract

The stable functional requirements are FR-01 through FR-12 in `REQUIREMENTS_TRACEABILITY.csv`. They cover discovery, profiling, candidate keys, hidden relationships, schema matching, quality diagnosis, optional entity resolution, canonical modeling, analytical modeling, materialization, validation and human review. Each has an acceptance criterion and a future verification owner; none is claimed implemented by this pass.

## 13. Non-functional product requirements

V1 requires reproducible run metadata, inspectable evidence, source safety, configurable sampling/chunking, deterministic methods where sufficient, replaceable external adapters and deterministic tests for major stages. Numeric latency, throughput and capacity targets require later benchmark evidence and are not invented here.

## 14. Product-level Definition of Done

V1 is complete only when the benchmark estate demonstrates all of the following:

1. At least three source systems plus one file source are ingested or otherwise exercised through the supported reference path.
2. Supported source catalogs, tables and columns are discovered.
3. Profiling is produced with scope and provenance.
4. Hidden key/relationship discovery is measured against ground truth.
5. Cross-source schema matching is measured against ground truth.
6. At least one entity family is resolved across sources with false-merge behavior measured.
7. A canonical model preserves source lineage.
8. At least one fact and three dimensions are generated where the benchmark supports them.
9. Every fact has validated explicit grain and every measure has aggregation semantics.
10. The model is materialized into DuckDB.
11. Validation/reconciliation passes or reports precise failures.
12. Accepted inferred relationships have inspectable evidence.
13. No unexplained record loss remains.
14. A fresh clone reproduces the reference demonstration using documented commands.

The relevant later gates, benchmarks and compatibility evidence must pass before these statements become release claims.

## 15. V1 non-goals

V1 is not:

- a destructive source cleaner;
- a production CDC or Kafka streaming system;
- an Airflow, Dagster or dbt replacement;
- a generic data lakehouse or enterprise warehouse orchestrator;
- an unstructured document, PDF, image, audio or video platform;
- a BI dashboard product;
- a causal business-analysis engine;
- an autonomous authority for business truth;
- a system that silently performs continuous sync or unrestricted workflow orchestration;
- a guarantee of semantic correctness when evidence is insufficient.
