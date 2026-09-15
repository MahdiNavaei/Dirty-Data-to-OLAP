# Database Compatibility Matrix — Step32 compatibility evidence

This matrix reports executed evidence, not intended V1 scope alone.

| Engine | V1 intent | Step06 status | Evidence / limitation |
|---|---|---|---|
| SQLite | Local reference/demo | `REFERENCE_TESTED` | Step06 read-only connection, metadata, declared constraints, bounded sampling, EXPLAIN, cleanup and deadline tests pass; Step07 also exercises the dlt-backed SourceAdapter through discovery, chunked extraction, source-faithful Parquet staging and row accounting. |
| PostgreSQL | Required V1 target | `LIVE_VERIFIED` | Step32 exact-head CI run `35034150663` passed discovery, extraction, Parquet staging and read-only write rejection against a real PostgreSQL service. |
| MySQL | Required V1 target | `LIVE_VERIFIED` | Step32 exact-head CI run `35034150663` passed discovery, extraction, Parquet staging and read-only write rejection against a real MySQL service. |
| MariaDB | Required V1 target | `LIVE_VERIFIED` | Step32 exact-head CI run `35034150663` passed discovery, extraction, Parquet staging and read-only write rejection against a real MariaDB service. |
| Microsoft SQL Server | Required V1 target | `LIVE_VERIFIED` | Step32 exact-head CI run `35034150663` passed discovery, extraction, Parquet staging and read-only write rejection against a real SQL Server service. |
| Oracle | Deferred | `DEFERRED` | Outside the current V1 implementation slice. |

Identifier quoting tests validate project policy for SQLite, PostgreSQL,
MySQL/MariaDB and SQL Server. They do not establish live compatibility.

The SQL rows are `LIVE_VERIFIED` for the tested source boundary after exact
content-head CI run `35034150663` passed with `8 passed, 0 skipped`. This does
not claim Oracle support or production deployment compatibility.

Step07 owns mapping this foundation into the complete SourceAdapter and
ingestion path.

Step11 security status: SQLite has live reference evidence for URI read-only,
query-only, authorizer, query guard and dlt/SQLAlchemy connection controls.
PostgreSQL, MySQL, MariaDB and SQL Server have live source-boundary evidence
with read-only verifier controls. Oracle is DEFERRED.
