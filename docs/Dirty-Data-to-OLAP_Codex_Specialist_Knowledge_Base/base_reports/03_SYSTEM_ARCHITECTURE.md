# 03 — System Architecture Report

## 1. Architectural objective

Dirty Data to OLAP V1 must integrate several specialized engines without becoming a tightly coupled pipeline. The architecture is therefore organized around **stable internal artifacts** and a staged control plane.

The most important design decision is:

> Every stage reads and writes Dirty Data to OLAP contracts. No stage depends directly on another stage's third-party library types.

---

## 2. High-level architecture

```text
                ┌────────────────────────────┐
                │       SOURCE SYSTEMS       │
                │ SQL DBs / CSV / Parquet    │
                └─────────────┬──────────────┘
                              │
                    ┌─────────▼─────────┐
                    │ Source Adapters   │
                    │       dlt         │
                    └─────────┬─────────┘
                              │
                    Source Catalog + Samples
                              │
        ┌─────────────────────┼──────────────────────┐
        │                     │                      │
┌───────▼────────┐  ┌────────▼─────────┐  ┌────────▼──────────┐
│ Profiling      │  │ Dependency       │  │ Declared metadata │
│ DataProfiler   │  │ Desbordante      │  │ PK/FK/types       │
└───────┬────────┘  └────────┬─────────┘  └────────┬──────────┘
        │                     │                      │
        └──────────────┬──────┴──────────────┬──────┘
                       │                     │
               ┌───────▼─────────┐   ┌──────▼──────────┐
               │ Schema Matching │   │ Quality Engine   │
               │ Valentine       │   │ Dirty Data to OLAP      │
               └───────┬─────────┘   └──────┬──────────┘
                       │                     │
                       └──────────┬──────────┘
                                  │
                      ┌───────────▼────────────┐
                      │ Evidence Fusion Engine │
                      │     Dirty Data to OLAP Core   │
                      └───────────┬────────────┘
                                  │
                     Relationship/Mapping Decisions
                                  │
                      ┌───────────▼────────────┐
                      │ Canonical Model Engine │
                      └───────────┬────────────┘
                                  │
                 ┌────────────────┴────────────────┐
                 │                                 │
        ┌────────▼─────────┐              ┌────────▼─────────┐
        │ Entity Resolution│              │ Analytical Planner│
        │ Splink            │              │ Fact/Dim/Grain    │
        └────────┬──────────┘              └────────┬──────────┘
                 │                                  │
                 └────────────────┬─────────────────┘
                                  │
                     ┌────────────▼─────────────┐
                     │ Transform/SQL Compiler   │
                     └────────────┬─────────────┘
                                  │
                            DuckDB target
                                  │
                     ┌────────────▼─────────────┐
                     │ Validation & Reconcile   │
                     └──────────────────────────┘
```

---

## 3. Control plane vs data plane

### Control plane

Stores decisions and metadata:

- run manifests;
- profiles;
- candidates;
- evidence;
- review decisions;
- canonical model;
- analytical plan;
- validation results.

Recommended V1 control-store options:

- SQLite for local simplicity, or
- PostgreSQL if the UI/API requires concurrent access.

Recommendation: start with SQLite and design repositories/interfaces so PostgreSQL can replace it later.

### Data plane

Contains actual source samples and transformed analytical data:

- source DB streaming/chunks;
- Parquet staging;
- DuckDB analytical target.

Large raw datasets should **not** be copied into the control database.

---

## 4. Major components

### 4.1 Run Manager

Responsibilities:

- create `run_id`;
- pin configuration;
- track stage status;
- store start/end times;
- record failures;
- support restart from a completed stage.

Run states:

```text
CREATED
DISCOVERING
PROFILING
DISCOVERING_RELATIONSHIPS
MATCHING_SCHEMAS
FUSING_EVIDENCE
RESOLVING_ENTITIES
BUILDING_CANONICAL_MODEL
PLANNING_ANALYTICAL_MODEL
MATERIALIZING
VALIDATING
SUCCEEDED
FAILED
NEEDS_REVIEW
```

A stage failure is explicit; it must not silently produce partial “success”.

### 4.2 Source Registry

Stores connection metadata **without raw credentials in artifacts**.

Each source has:

- stable source ID;
- source type;
- logical name;
- connection profile reference;
- inclusion/exclusion rules;
- sampling policy.

### 4.3 Discovery Engine

Uses source adapters to build a normalized catalog.

Output:

- tables;
- columns;
- types;
- declared keys;
- row estimates;
- source fingerprints.

### 4.4 Profiling Engine

Produces deterministic/statistical profiles on configurable samples or full columns when feasible.

Sampling metadata must be stored so users can distinguish:

```text
full-table observation
```

from:

```text
sample-based estimate
```

### 4.5 Dependency Discovery Engine

Discovers candidate:

- functional dependencies;
- inclusion dependencies;
- candidate keys.

This stage may be expensive. It must support candidate pruning based on types, cardinalities and table sizes.

### 4.6 Schema Matching Engine

Generates ranked candidate column equivalences across tables/sources.

It does **not** directly mutate canonical schemas.

### 4.7 Quality Engine

Converts raw observations into normalized quality issues.

Example:

```text
profile: null_ratio = 0.14
column role hypothesis: required customer identifier
             ↓
quality issue:
MISSING_REQUIRED_IDENTIFIER
severity = HIGH
```

This distinction prevents a generic profiler from deciding business severity.

### 4.8 Evidence Fusion Engine

This is a core proprietary/portfolio component.

Input:

- profile evidence;
- structural dependencies;
- declared constraints;
- schema matcher outputs;
- naming semantics;
- value overlaps;
- reviewer knowledge.

Output:

- relationship decisions;
- mapping decisions;
- semantic hypotheses;
- uncertainty/conflict states.

Detailed design is in report 05.

### 4.9 Review/Policy Engine

Controls whether a decision is:

- auto-accepted;
- suggested;
- blocked pending review;
- rejected.

Human overrides are first-class artifacts and are replayed on future runs unless invalidated by schema changes.

### 4.10 Canonical Model Engine

Builds business concepts independent of source naming.

Example:

```text
crm.customer
sales.client
website.user
      ↓
CanonicalEntity(type=CUSTOMER)
```

It also records source attribute mappings and conflict policies.

### 4.11 Entity Resolution Engine

Runs only after canonical identity fields are known.

Output is a mapping from source records to canonical IDs, not destructive source deduplication.

### 4.12 Analytical Model Planner

Produces:

- facts;
- dimensions;
- grain;
- measures;
- keys;
- source lineage;
- transform plan.

Planner output must be a typed contract before SQL is generated.

### 4.13 Compiler/Materializer

Converts approved analytical plans into executable SQL and builds the target.

V1 reference target:

- DuckDB.

Optional later:

- ClickHouse;
- PostgreSQL analytical schema;
- dbt project generation.

### 4.14 Validation Engine

Runs after materialization and determines final success/failure.

It checks:

- structural constraints;
- reconciliation;
- data loss;
- key integrity;
- semantic invariants defined by the plan.

---

## 5. Storage architecture

Recommended V1 filesystem:

```text
workspace/
└── runs/
    └── <run_id>/
        ├── manifest.json
        ├── catalog/
        ├── samples/
        ├── profiles/
        ├── evidence/
        ├── decisions/
        ├── canonical/
        ├── analytical/
        ├── generated_sql/
        ├── target.duckdb
        └── validation/
```

Intermediate tabular artifacts should prefer Parquet for compactness and typed interchange.

JSON/YAML should be used for metadata and plans, not for large row datasets.

---

## 6. Execution model

V1 is a staged batch pipeline.

```text
Discover
  ↓
Sample/Profile
  ↓
Find structural dependencies
  ↓
Generate schema matches
  ↓
Fuse evidence
  ↓
Review uncertain decisions
  ↓
Build canonical model
  ↓
Resolve selected entity families
  ↓
Plan analytical model
  ↓
Dry-run / compile
  ↓
Materialize
  ↓
Validate
```

Stages must be restartable from persisted artifacts.

Example:

If entity-resolution settings change, the user should not be forced to repeat database profiling unless relevant source fingerprints changed.

---

## 7. Caching and invalidation

Each stage output should depend on fingerprints of:

- upstream artifact IDs/hashes;
- source schema fingerprint;
- sample fingerprint;
- algorithm config;
- adapter version.

If any dependency changes, downstream cached artifacts are invalidated.

This prevents dangerous reuse of old inference after a source schema changed.

---

## 8. LLM placement

LLM use is optional and tightly bounded.

Permitted uses:

- generate semantic label candidates;
- explain a relationship in readable language;
- suggest business entity names;
- propose candidate fact/dimension roles;
- rank ambiguous mappings as one additional signal.

Not permitted as sole authority for:

- key discovery;
- FK acceptance;
- entity merge;
- destructive cleaning;
- numeric reconciliation;
- final model validation.

LLM responses must be serialized as `LLMEvidence`, including model ID and prompt/version metadata if used.

---

## 9. Error isolation

External engines can fail independently.

Example policies:

- profiling failure for one column → mark column profile failed, continue other columns, run ends `PARTIAL/NEEDS_REVIEW` rather than silent success;
- Desbordante worker crash → relationship discovery stage fails, no guessed fallback;
- Valentine unavailable → declared relationships and structural evidence remain usable, but semantic mapping stage is incomplete;
- Splink failure → analytical modeling may proceed only for models not dependent on deduplicated entity keys.

---

## 10. Suggested Python package structure

```text
src/dirty-data-to-olap/
├── api/
├── cli/
├── domain/
│   ├── contracts/
│   ├── enums.py
│   └── policies.py
├── adapters/
│   ├── sources/dlt_sql.py
│   ├── profiling/dataprofiler.py
│   ├── dependencies/desbordante.py
│   ├── matching/valentine.py
│   └── entity_resolution/splink.py
├── discovery/
├── profiling/
├── quality/
├── evidence/
├── canonical/
├── entity_resolution/
├── analytical/
├── compiler/
├── validation/
├── persistence/
└── runs/
```

---

## 11. Security/safety baseline

Even as a portfolio project:

- source connection roles should be read-only;
- secrets must come from environment/secret files ignored by Git;
- samples may contain PII and should not be committed;
- generated demo datasets should use synthetic identities;
- logs must avoid dumping entire source rows by default.

---

## 12. Critical review applied before approval

### Problem A — Original pipeline was too linear

Some stages can fail or be rerun independently. A direct function chain would be fragile.

**Correction:** persistent stage artifacts + run manager + restartable execution.

### Problem B — External libraries could leak types everywhere

**Correction:** all external engines are adapters into Dirty Data to OLAP contracts.

### Problem C — Profiling full enterprise tables could exhaust memory

**Correction:** sampling/chunking is a first-class policy and sample provenance is stored.

### Problem D — Canonical model and entity resolution order was ambiguous

**Correction:** first form canonical entity hypotheses/identity fields, then run entity resolution, then finalize canonical instances and analytical mapping.

### Problem E — “Successful SQL generation” could be mistaken for success

**Correction:** only post-materialization validation can mark a run `SUCCEEDED`.

**Status: APPROVED AS V1 REFERENCE ARCHITECTURE.**
