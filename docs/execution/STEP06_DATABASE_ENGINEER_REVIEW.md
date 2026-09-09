# Specialist Step06 — Database Engineer / DBA Review

## Status

`PASS` — the first production implementation slice is complete. Step07 is the
next authorized specialist. G3 remains `PENDING`.

Starting HEAD: `1c4ed5667fdb60b382184d1b6e649c8d60d30401`

Content commit: `5e943daa849eba918a5b9b4de33696403c7d2c4d`
Metadata commit: `ba8bb21a2c33b88fdcfe7c1a729797550481faff`

## Scope implemented

Step06 implements the minimum database access/introspection foundation:

- versioned Pydantic database contracts and stable structured identifiers;
- external connection-profile references with secret-bearing DSN rejection;
- conservative capability/status and normalized failure models;
- SQLite filesystem read-only sessions using URI `mode=ro` and
  `PRAGMA query_only = ON`;
- typed table/view, column, primary-key, composite-key, foreign-key, unique
  index and NOT NULL metadata;
- engine-aware identifier qualification for SQLite, PostgreSQL,
  MySQL/MariaDB and SQL Server policy tests;
- explicit read-only transaction intent and cleanup;
- distinct connection, busy/lock and statement-deadline timeout policy;
- validated pool configuration semantics without a bespoke runtime pool;
- explicit-column bounded head sampling with observation scope;
- EXPLAIN QUERY PLAN for generated bounded reads only.

## Package paths created

```text
src/dirty_data_to_olap/
  domain/contracts/
  adapters/sources/sql/
tests/{unit,contract,integration,architecture}/
```

`application/`, complete `SourceAdapter`, dlt, staging, profiling, semantic,
ER, canonical and OLAP modules were intentionally not created.

## Contracts introduced

`ConnectionProfileReference`, `DatabaseIdentifier`, `DatabaseCapabilities`,
`DatabaseAccessPolicy`, `SamplingPolicy`, `BoundedSampleRequest`,
`BoundedSampleObservation`, `TimeoutPolicy`, `PoolPolicy`, `DatabaseFailure`,
`DatabaseFailureKind`, `DatabaseMetadata`, `DatabaseTableMetadata`,
`DatabaseColumnMetadata`, `DatabaseDeclaredConstraint` and
`ExplainPlanStep`. All carry schema version `1.0`.

## SQLite evidence

The project-local fixture contains a single-PK table, composite-PK table,
declared FK, nullable column, unique fields, table, view, awkward identifiers
and several rows. The inspected output showed all expected object names;
metadata returned ordered composite PK columns, declared FK and unique
metadata; the bounded sample returned two explicitly requested columns and two
rows for a limit of two; EXPLAIN returned a generated `SCAN child` plan.

URI read-only mode plus `query_only` rejected INSERT, UPDATE, DELETE, CREATE
TABLE and DROP TABLE. The normalized result was
`READ_ONLY_VIOLATION` with no driver exception exposed. The session context
closed successfully. A recursive SQLite diagnostic exceeded the 1 ms
statement deadline and normalized to `TIMEOUT`.

## Security and boundary review

The public session has no arbitrary `execute` or `run_any_query` method. SQL is
generated only from structured/quoted identifiers and parameterized bounds.
Raw connections, cursors, rows and driver exceptions remain inside the SQL
adapter. Domain contracts import no driver or adapter modules, and no runtime
import references `research/oss`.

Serialized connection profiles contain only non-secret locators and external
credential references. Fake password/token values were absent from serialized
profiles, normalized failures, `str` and `repr` output. No logs were added.

## Tests actually run

Environment: Python `3.10.11`, Pydantic `2.12.5`, pytest `8.4.2`.

```text
python -m pytest tests/unit -q          -> 5 passed
python -m pytest tests/contract -q      -> 2 passed
python -m pytest tests/integration -q   -> 10 passed
python -m pytest tests/architecture -q  -> 4 passed
python -m pytest -q                     -> 21 passed
python -m compileall src tools          -> PASS
python tools/validate_domain_docs.py    -> PASS
python tools/validate_data_architecture.py -> PASS
python tools/validate_solution_architecture.py -> PASS
python tools/validate_engineering_plan.py --post-gate -> PASS (73 checks; 23/23 negative tests)
git diff --check                         -> PASS
```

## Dependencies

`pyproject.toml` declares Pydantic `>=2.12,<3` and optional test dependency
pytest `>=8.4,<9`. The executed environment used Pydantic `2.12.5` and pytest
`8.4.2`. No OSS repository was cloned and no third-party runtime import was
added.

## Known limitations

Only SQLite was live-exercised. PostgreSQL, MySQL, MariaDB and SQL Server are
required V1 targets but remain unverified; Oracle is deferred. Pool policy is
typed but pool-exhaustion runtime behavior is deferred. SQLite timeout evidence
covers the statement deadline path; cross-engine timeout semantics remain for
provider integration. Least-privilege proof belongs to Step11. SourceAdapter,
discovery, snapshot orchestration, ingestion, staging and G3 remain future work.

## Handoff to Step07

Step07 receives the typed database metadata, read-only policy, safe sampling and
query-cost boundary, normalized failures, capability/status semantics and the
explicit source-operation boundary. Step07 owns mapping these primitives into
the complete SourceAdapter, SourceCatalog/SourceSnapshot lifecycle and source
ingestion path.
