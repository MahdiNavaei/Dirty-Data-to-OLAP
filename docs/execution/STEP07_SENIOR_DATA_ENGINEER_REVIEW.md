# Specialist Step07 — Senior Data Engineer Review

## Status

`PASS` — the complete V1 source selection, discovery, bounded extraction and
source-faithful staging foundation is implemented and verified. G3A Source
Usability is `PASS` as an intermediate milestone. Formal G3 Source Safety and
G4–G15 remain `PENDING`.

Starting HEAD: `9a4a69e3838fc8be8cb96270d532be16ccf98586`

Content commit: `0f49fd3a48ec0a989da7cc896149f236458fce1f`

Metadata commit: `c3293c60f5de6178d155d5926d358c6cb7d55a82`

## Scope implemented

- project-owned, versioned source contracts and stable identifiers;
- in-memory source registry plus discovery and snapshot application services;
- a vendor-neutral `SourceAdapter` protocol;
- dlt 1.30.0 wrapper at reviewed commit `a1c530114cc347496d1f00f38891475a047b6d05`;
- SQLite live reference path using the Step06 read-only foundation;
- generic PostgreSQL, MySQL, MariaDB and SQL Server dlt-backed implementation
  paths with injected credential resolution;
- streaming CSV, bounded PyArrow Parquet and optional read-only XLSX adapters;
- source catalog JSON, extraction manifest, source record references and row
  accounting;
- atomic source-faithful Parquet batches with partial-file cleanup and
  deterministic complete-artifact reuse.

Profiling, entity resolution, canonicalization, business semantics and OLAP
materialization were not implemented.

## Contracts and boundary review

`SourceSelection`, `SourceRegistryRecord`, `SourceDescriptor`,
`TableDescriptor`, `ColumnDescriptor`, `DeclaredConstraint`, `SourceCatalog`,
`SourceSnapshot`, `BatchReference`, `SourceRecordReference`, observation and
adapter provenance, failure and accounting contracts are project-owned and
schema-versioned. Stable IDs and fingerprints use canonical JSON/SHA-256.
SQLite primary-key nullability keeps raw declaration, schema nullability and
primary-key position distinct. Declared constraints remain metadata only.

Domain and application layers have no vendor imports. dlt, SQLAlchemy, PyArrow,
openpyxl and sqlite3 are confined to concrete adapters/staging. No runtime
import references `research/oss`; the reviewed clone was deleted before
completion. Credentials, native rows, cursors, connections and provider
exceptions do not cross the contract boundary.

## OSS research

The exact dlt commit was cloned and inspected under the project research
boundary, including SQL source helpers/schema types, SQLAlchemy integration,
reflection, chunking, resource lifecycle, backend behavior and relevant tests.
The repository license and package metadata were verified as Apache-2.0 and
version 1.30.0. The implementation wraps the official package and copies no
dlt source. The clone was deleted and the research ledger retains the exact
commit, license, inspected files/tests, reuse decision and deletion evidence.

## Executed source evidence

The representative SQLite fixture covered tables, a view, composite and single
primary keys, foreign keys, unique/not-null declarations, duplicate no-key
rows, an awkward table name and 11 input rows. The executed path was:

```text
registry -> discovery -> SourceCatalog -> dlt extraction
         -> transaction-scoped SourceSnapshot -> chunked Parquet
         -> SourceRecordReference -> manifest/catalog JSON -> accounting
```

Observed output included 11 input and 11 staged records, zero quarantined or
unresolved records, complete batches, readable Parquet values, ordered PK
references, snapshot-bound ordinal references for no-key rows, and no partial
files. SQLite writes were rejected through URI read-only mode plus
`PRAGMA query_only=ON`.

CSV evidence covered BOM/header handling, quoting, empty strings, literal null
markers, duplicates, raw string preservation, read-back and explicit malformed
row failure. Parquet evidence covered multiple row groups, projection,
bounded `iter_batches` reads, schema preservation, read-back and deterministic
restart behavior. XLSX evidence covered the optional read-only worksheet path.

## Tests actually run

Environment: Python `3.10.11`, Pydantic `2.12.5`, pytest `8.4.2`, dlt
`1.30.0`, SQLAlchemy `2.0.46`, PyArrow `22.0.0`, openpyxl `3.1.5`.

```text
.venv-step07\Scripts\python.exe -m pytest -q -> 38 passed, 2 warnings
.venv-step07\Scripts\python.exe -m pytest tests/integration/sources/test_step07_source_pipelines.py::test_sqlite_dlt_discovery_extraction_and_staging -q -s -> 1 passed, 1 warning
.venv-step07\Scripts\python.exe tools/validate_source_ingestion.py -> PASS
.venv-step07\Scripts\python.exe tools/validate_domain_docs.py -> PASS
.venv-step07\Scripts\python.exe tools/validate_data_architecture.py -> PASS
.venv-step07\Scripts\python.exe tools/validate_solution_architecture.py -> PASS
.venv-step07\Scripts\python.exe tools/validate_engineering_plan.py --post-gate -> PASS (73 checks; 23/23 negative tests)
.venv-step07\Scripts\python.exe -m compileall src tools -> PASS
git diff --check -> PASS
```

## Self-review and limitations

The focused rerun was performed after changing the tested SQLite consistency
claim from best-effort to transaction-scoped and after fixing fixture-handle
cleanup. Full regression and all validators passed afterward. The source
adapter contract, serialization, secret/path safety, read-only, malformed-row,
atomicity, projection, chunking and restart negative tests remain active.

SQLite, CSV, Parquet and optional XLSX are reference-tested. PostgreSQL,
MySQL, MariaDB and SQL Server are implemented but not live-verified. Oracle is
deferred. Cross-table transactional snapshots, provider-specific least
privilege, pool exhaustion, live credential-provider behavior and formal G3
remain future work. `IMPLEMENTED_NOT_LIVE_VERIFIED` is not a compatibility or
security claim.

## Handoff

Step07 is complete. The next authorized specialist is Step08 — Data Profiling
Specialist. Step08 may consume the SourceCatalog, SourceSnapshot, staged batch
and record-reference artifacts; it must not infer that formal G3 or provider
wide compatibility has passed.
