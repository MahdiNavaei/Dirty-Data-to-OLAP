# OSS Reuse Research Ledger

## dlt

- Repository: https://github.com/dlt-hub/dlt
- Reviewed revision: `a1c530114cc347496d1f00f38891475a047b6d05`
- License: Apache License 2.0, verified in `research/oss/dlt/LICENSE.txt`.
- Installed/tested version: `dlt==1.30.0`.
- Source inspected: `dlt/sources/sql_database/__init__.py`, `helpers.py`,
  `schema_types.py`, `arrow_helpers.py` and `dlt/common/libs/sql_alchemy.py`.
- Tests inspected: SQL database schema-type, engine-kwargs, reflection-cache,
  config-section, helper, source and backfill tests under
  `tests/sources/sql_database/` and `tests/load/sources/sql_database/`.
- Decision: use the official installed package through the project-owned
  `DltSqlSourceAdapter`; use `sql_database`/`sql_table` reflection and the
  SQLAlchemy backend with explicit chunk size. No dlt source was copied.
- Boundary: dlt resources and SQLAlchemy objects are consumed only inside the
  concrete adapter and normalized into project contracts.
- Obligations: retain Apache attribution and license obligations for any
  distributed runtime installation.
- Research clone deletion: after source inspection, the clone was removed;
  final tests and import scans run with `research/oss/dlt` absent.

## Other libraries

## Great Expectations (Step09 research)

- Repository: https://github.com/great-expectations/great_expectations
- Reviewed revision: `4b5dd52306872ec130f7bc0093eb4aebf6b7515b` (shallow research checkout).
- License: Apache License 2.0, verified in `research/oss/great_expectations/LICENSE`.
- Source inspected: `great_expectations/expectations/expectation.py`,
  `great_expectations/core/expectation_validation_result.py` and the
  distinct-values expectation implementation.
- Tests inspected: expectation and validation-result tests in the repository's
  `tests/` tree, including expectation configuration, validation counts,
  unexpected values and serializable result behavior.
- Decision: reference the mature expectation/validation-result design only;
  do not copy code or persist native Great Expectations objects. Dirty Data to
  OLAP retains project-owned QualityRule, QualityIssue and QualityResult
  contracts with its own staged-only and privacy boundaries.
- Boundary and license obligation: no Great Expectations runtime dependency is
  introduced by Step09; Apache attribution remains required if it is adopted in
  a future implementation.
- Research clone deletion: clone removed before final regression.

## Capital One DataProfiler

- Repository: https://github.com/capitalone/DataProfiler
- Reviewed revision: `4b5ab37bb28a2104d0898d21a8c9681b5c5deed1`.
- License: Apache License 2.0, verified in `research/oss/DataProfiler/LICENSE`.
- Installed/tested version: `DataProfiler==0.13.4` from the official package.
- Source inspected: profile builder, profiler options, column compilers,
  numerical/categorical/datetime/order profiles, JSON encoder/decoder and
  related profiler tests at the reviewed revision.
- Decision: use the official base package through `DataProfilerAdapter`; do not
  install ML/report extras and do not copy source code.
- Boundary: native profiler, pandas, NumPy and PyArrow objects remain inside
  the concrete adapter. Project contracts own null semantics, sampling,
  completeness, privacy-safe patterns and artifact persistence.
- Research clone deletion: required after review and before final regression.

| Library | Tested version | License | Decision |
|---|---:|---|---|
| SQLAlchemy | 2.0.46 | MIT | Runtime SQL engine boundary used only by dlt adapter |
| PyArrow | 22.0.0 | Apache-2.0 | Parquet discovery, iteration and narrow staging boundary |
| openpyxl | 3.1.5 | MIT | Optional read-only XLSX adapter |
| Python `csv` | 3.10.11 stdlib | PSF-2.0 | Streaming CSV parser with explicit malformed-row failure |
