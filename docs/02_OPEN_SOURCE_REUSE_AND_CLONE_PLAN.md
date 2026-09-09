# 02 — Open Source Reuse & Clone Plan

## 1. Objective

The project must not reimplement mature algorithms or infrastructure unless there is a clear reason. This report maps each external repository to a specific responsibility in DataFoundry V1 and defines how it is integrated without creating tight coupling.

The central rule is:

> **Reuse engines; own the orchestration, contracts, evidence fusion, canonical reasoning and analytical planning.**

We are not building a wrapper that blindly chains libraries. We are building a system whose core intelligence combines multiple independent signals into auditable decisions.

---

## 2. Clone layout

Recommended repository layout during research and integration:

```text
DataFoundry/
├── src/
├── tests/
├── docs/
├── benchmark/
├── research/oss/
│   ├── dlt/
│   ├── DataProfiler/
│   ├── desbordante-core/
│   ├── valentine/
│   ├── splink/
│   └── star-schema-generator/
└── pyproject.toml
```

`research/oss/` is for reading, testing and comparing upstream implementations. It is disposable reference material and is **not** imported directly by production code or required at runtime.

Production integration should use normal package dependencies, a subprocess/sidecar where appropriate, or an adapter around an installed package.

---

## 3. Pinned review snapshots

The following commits are the exact upstream snapshots reviewed for the initial design:

| Project | Repository | Default branch at review | Reviewed commit | Main V1 role |
|---|---|---|---|---|
| dlt | https://github.com/dlt-hub/dlt | `devel` | `a1c530114cc347496d1f00f38891475a047b6d05` | source reflection/extraction/schema handling |
| DataProfiler | https://github.com/capitalone/DataProfiler | `main` | `4b5ab37bb28a2104d0898d21a8c9681b5c5deed1` | column/table profiling |
| Desbordante | https://github.com/Desbordante/desbordante-core | `main` | `b211961f3f272ed8815ef1ffbda90573b11e1116` | dependency/key/relationship discovery |
| Valentine | https://github.com/delftdata/valentine | `master` | `5d5163f04da304985bd51a476ccf7653de3979c3` | cross-table/schema matching |
| Splink | https://github.com/moj-analytical-services/splink | `master` | `ca89ee92d5472b5e5de71cff3001193e04faf0e7` | probabilistic entity resolution |
| star-schema-generator | https://github.com/Ali-datasmith/star-schema-generator | `main` | `8bdc51846e669874400d2a532ca2849287fbac24` | reference only for output contracts/modeling patterns |

Licenses are binding constraints on direct code reuse, including for this non-commercial portfolio project. Inspect the exact license and revision before reuse. Reuse useful upstream implementation only when the license permits the intended use and all attribution/redistribution obligations are satisfied. When direct reuse is not permitted or appropriate, study the upstream engineering deeply and implement the required behavior independently for Dirty Data to OLAP. Record the decision and provenance in the project reuse ledger.

---

## 4. Clone commands for reproducible research

```bash
git clone https://github.com/dlt-hub/dlt research/oss/dlt
git -C research/oss/dlt checkout a1c530114cc347496d1f00f38891475a047b6d05

git clone https://github.com/capitalone/DataProfiler research/oss/DataProfiler
git -C research/oss/DataProfiler checkout 4b5ab37bb28a2104d0898d21a8c9681b5c5deed1

git clone https://github.com/Desbordante/desbordante-core research/oss/desbordante-core
git -C research/oss/desbordante-core checkout b211961f3f272ed8815ef1ffbda90573b11e1116

git clone https://github.com/delftdata/valentine research/oss/valentine
git -C research/oss/valentine checkout 5d5163f04da304985bd51a476ccf7653de3979c3

git clone https://github.com/moj-analytical-services/splink research/oss/splink
git -C research/oss/splink checkout ca89ee92d5472b5e5de71cff3001193e04faf0e7

git clone https://github.com/Ali-datasmith/star-schema-generator research/oss/star-schema-generator
git -C research/oss/star-schema-generator checkout 8bdc51846e669874400d2a532ca2849287fbac24
```

These clones are for source review and integration tests. The application itself must not depend on the local research clones or Git submodules containing them.

---

# 5. dlt

## 5.1 Why it is useful

dlt solves boring but difficult infrastructure work around loading and schema reflection. DataFoundry should not maintain one custom connector per SQL dialect in V1.

Relevant reviewed paths include:

```text
dlt/sources/sql_database/__init__.py
dlt/sources/sql_database/helpers.py
dlt/common/schema/schema.py
dlt/common/schema/typing.py
```

The SQL source exposes `sql_database(...)`, supports SQLAlchemy-based reflection, configurable reflection levels, chunked reading and optional declared foreign-key resolution.

## 5.2 Use it for

- connect to supported SQL databases;
- enumerate tables;
- reflect columns and native types;
- read declared PK/FK metadata;
- extract rows in chunks;
- normalize basic source schema metadata;
- provide stable ingestion behavior.

## 5.3 Do NOT use it for

- hidden FK discovery;
- semantic matching;
- duplicate entity resolution;
- canonical business modeling;
- fact/dimension inference.

## 5.4 Adapter boundary

Create:

```text
SourceAdapter
└── DltSqlSourceAdapter
```

The adapter converts dlt-specific objects into:

- `SourceDescriptor`
- `TableDescriptor`
- `ColumnDescriptor`
- `DeclaredConstraint`
- `BatchReference`

No other DataFoundry module imports dlt types.

## 5.5 Example

Source:

```text
Postgres.orders(customer_no, total, created_at)
```

dlt tells us:

```text
table = orders
columns = customer_no, total, created_at
native types = varchar, numeric, timestamp
row batches = ...
declared FK = none
```

It does **not** decide what `customer_no` means.

---

# 6. Capital One DataProfiler

## 6.1 Why it is useful

DataProfiler already implements mature structured-data profiling. The reviewed code contains `StructuredColProfiler` in:

```text
dataprofiler/profilers/profile_builder.py
```

with substantial associated profiler/test infrastructure.

## 6.2 Use it for

- null/missing-value behavior;
- cardinality and uniqueness indicators;
- primitive type profiles;
- categorical/numeric/date statistics;
- distributions and basic correlations where appropriate;
- candidate data labels/semantic hints when supported;
- profile serialization.

## 6.3 Do NOT use it for

- final data-quality verdicts;
- final semantic meaning;
- relationship inference;
- canonical mapping.

DataProfiler supplies **observations**, not business truth.

## 6.4 Adapter boundary

Create:

```text
ProfilingEngine
└── DataProfilerEngine
```

Output is normalized into:

```text
ColumnProfile
TableProfile
ValuePatternSummary
```

## 6.5 Example

Input:

```text
customers.mobile
09121234567
+989121234567
N/A
NULL
```

Normalized DataFoundry output may be:

```yaml
column: customers.mobile
null_ratio: 0.08
distinct_ratio: 0.91
observed_types:
  string: 1.0
patterns:
  phone_like: 0.89
quality_hints:
  - mixed_phone_representation
```

The `phone_like` semantic layer may combine DataProfiler output with our own pattern detectors.

---

# 7. Desbordante

## 7.1 Why it is useful

This is the strongest reusable component found for structural dependency discovery. The codebase contains dedicated algorithm families, including functional and inclusion dependencies. Relevant paths include:

```text
src/core/algorithms/fd/
src/core/algorithms/ind/
src/core/algorithms/cind/
src/python_bindings/
```

The IND subtree includes implementations/families such as:

```text
faida/
mind/
spider/
ind_verifier/
```

## 7.2 Use it for

- Functional Dependency discovery;
- Inclusion Dependency discovery;
- approximate dependency evidence;
- unique/candidate key evidence where exposed;
- verification of candidate relationships;
- structural evidence for hidden FK inference.

## 7.3 Important distinction

An Inclusion Dependency is **evidence of a possible relationship**, not proof of a business FK.

Example:

```text
values(orders.customer_no) ⊆ values(customers.customer_no)
```

may strongly support a relationship, but coincidental domains can create false positives.

Therefore DataFoundry must combine IND with:

- target uniqueness;
- type compatibility;
- semantic/name similarity;
- cardinality;
- orphan rate;
- source metadata.

## 7.4 Adapter boundary

Create:

```text
DependencyDiscoveryEngine
└── DesbordanteDependencyEngine
```

Output becomes:

- `KeyCandidate`
- `FunctionalDependencyEvidence`
- `InclusionDependencyEvidence`
- `RelationshipCandidate`

## 7.5 Runtime integration recommendation

Start with Python bindings if stable for the chosen environment. If binding/build friction is high on Windows, use a containerized/subprocess worker that exchanges JSON/Parquet artifacts with the main Python service.

The architecture must not assume in-process execution.

---

# 8. Valentine

## 8.1 Why it is useful

Valentine is specifically designed for schema matching and includes multiple matching strategies. This is useful when equivalent fields have different names and are in different tables/sources.

We use Valentine as a **candidate generator and scorer**, not as final authority.

## 8.2 Use it for

- column-name similarity;
- token-based schema similarity;
- instance/value-based similarity;
- ranked cross-table column matches;
- comparison of multiple matching algorithms.

## 8.3 Do NOT use it for

- deciding final business semantics;
- merging source schemas automatically without evidence fusion;
- deciding fact/dimension roles.

## 8.4 Adapter boundary

Create:

```text
SchemaMatchingEngine
└── ValentineSchemaMatchingEngine
```

Output:

```text
SchemaMatchCandidate
```

with one or more raw matcher scores preserved in `evidence`.

## 8.5 Example

Input:

```text
CRM.customer_code
ERP.client_no
```

Valentine may return a strong match because values overlap and names/tokens are compatible. DataFoundry then combines that score with Desbordante/DataProfiler evidence before accepting a canonical mapping.

---

# 9. Splink

## 9.1 Why it is useful

Splink solves probabilistic record linkage/entity resolution. This is a specialized problem and should not be replaced by a few fuzzy-string rules.

## 9.2 Use it for

- record linkage across sources;
- probabilistic match scores;
- blocking;
- comparison features;
- EM-based parameter estimation where appropriate;
- clustering linked records into entity groups.

## 9.3 Do NOT use it for

- schema matching;
- discovering which columns should be compared without prior mapping;
- selecting authoritative attribute values after clustering;
- defining canonical business meaning.

## 9.4 Adapter boundary

Create:

```text
EntityResolutionEngine
└── SplinkEntityResolutionEngine
```

Input must already define:

- entity family;
- candidate identity fields;
- normalization rules;
- blocking rules or strategy;
- source provenance.

Output:

- `EntityMatchEdge`
- `EntityCluster`
- `SourceRecordCanonicalMap`

## 9.5 Example

```text
CRM:  C124, "مهدی نوایی", 0912...
Shop: U882, "Mahdi Navaei", +98912...
```

Splink estimates whether records represent the same person. DataFoundry later decides how the cluster becomes `canonical_customer_id` and which attributes survive.

---

# 10. star-schema-generator

## 10.1 Decision

**Reference project only. Not a core dependency.**

The useful part is the modeling/output-contract pattern, including explicit representation of:

- facts;
- dimensions;
- measures;
- keys;
- model validation.

## 10.2 Why we do not adopt its architecture

Our V1 should not reduce dimensional modeling to a single LLM generation step. We need an evidence-backed planner tied to discovered relationships, grains and source lineage.

## 10.3 What to borrow conceptually

- typed/Pydantic analytical model contracts;
- explicit measure categories;
- DDL compilation pattern;
- validation before materialization.

The implementation should be our own and governed by report 07.

---

## 11. Integration matrix

| DataFoundry capability | Reuse | Our responsibility |
|---|---|---|
| SQL source reflection | dlt | normalize metadata, policies |
| extraction/chunking | dlt | orchestration, run manifests |
| column profiling | DataProfiler | unified profile contract, extra semantic detectors |
| declared relationships | dlt/SQL metadata | normalize and preserve provenance |
| hidden dependencies | Desbordante | convert to relationship hypotheses |
| schema matching | Valentine | evidence fusion and acceptance policy |
| entity resolution | Splink | configure, choose canonical attributes, preserve lineage |
| evidence fusion | none | **DataFoundry core** |
| canonical business model | none | **DataFoundry core** |
| fact/dimension/grain inference | no adequate reusable core | **DataFoundry core** |
| materialization | DuckDB/SQL | DataFoundry compiler |
| validation | SQL + our tests | **DataFoundry core** |

---

## 12. Dependency isolation rules

1. No external object types cross an adapter boundary.
2. Raw external scores are preserved in evidence metadata.
3. Upgrading an external library must not change stored contracts without a migration.
4. Every adapter gets contract tests using fixed fixtures.
5. External-engine failure must produce a stage failure, not silently skip evidence.
6. The system must be able to run selected stages independently.
7. Optional algorithms should be feature-flagged.

---

## 13. Initial integration order

Recommended sequence:

```text
1. dlt adapter
2. DataProfiler adapter
3. internal contracts
4. Desbordante adapter
5. Valentine adapter
6. evidence fusion baseline
7. Splink adapter
8. canonical model
9. analytical planner
10. DuckDB materializer
```

Splink intentionally comes after schema/relationship understanding because entity-resolution configuration depends on knowing which attributes correspond.

---

## 14. Critical review applied before approval

### Problem A — “Clone everything and import it” would create dependency chaos

**Correction:** clones are research snapshots only; production code uses adapters and package/runtime integration.

### Problem B — IND could be mistaken for FK truth

**Correction:** Desbordante output is only one evidence family in relationship scoring.

### Problem C — Valentine could create plausible but wrong semantic matches

**Correction:** matcher scores cannot auto-accept a mapping without structural/value evidence or review.

### Problem D — Splink could be run before columns are understood

**Correction:** entity resolution occurs after schema matching and canonical entity hypotheses.

### Problem E — star-schema-generator could tempt us into “LLM designs the warehouse”

**Correction:** use only its contract/validation ideas; analytical planning is DataFoundry-owned.

### Problem F — upstream changes could make research irreproducible

**Correction:** exact reviewed SHAs are pinned in this report.

**Status: APPROVED AS THE OPEN-SOURCE REUSE BASELINE.**
