# Database Compatibility Matrix — Step32 compatibility evidence

This matrix reports executed evidence, not intended V1 scope alone.

| Engine | V1 intent | Step06 status | Evidence / limitation |
|---|---|---|---|
| SQLite | Local reference/demo | `REFERENCE_TESTED` | Step06 read-only connection, metadata, declared constraints, bounded sampling, EXPLAIN, cleanup and deadline tests pass; Step07 also exercises the dlt-backed SourceAdapter through discovery, chunked extraction, source-faithful Parquet staging and row accounting. |
| PostgreSQL | Required V1 target | `CI_LIVE_TEST_REQUIRED` | Generic dlt/SQLAlchemy path exists; Step32 CI must exercise discovery, extraction, staging and read-only negative behavior against a real PostgreSQL service before this becomes a live support claim. |
| MySQL | Required V1 target | `CI_LIVE_TEST_REQUIRED` | Generic dlt/SQLAlchemy path exists; Step32 CI must exercise discovery, extraction, staging and read-only negative behavior against a real MySQL service before this becomes a live support claim. |
| MariaDB | Required V1 target | `CI_LIVE_TEST_REQUIRED` | Generic dlt/SQLAlchemy path exists; Step32 CI must exercise discovery, extraction, staging and read-only negative behavior against a real MariaDB service before this becomes a live support claim. |
| Microsoft SQL Server | Required V1 target | `CI_LIVE_TEST_REQUIRED` | Generic dlt/SQLAlchemy path exists; Step32 CI must exercise discovery, extraction, staging and read-only negative behavior against a real SQL Server service before this becomes a live support claim. |
| Oracle | Deferred | `DEFERRED` | Outside the current V1 implementation slice. |

Identifier quoting tests validate project policy for SQLite, PostgreSQL,
MySQL/MariaDB and SQL Server. They do not establish live compatibility.

`CI_LIVE_TEST_REQUIRED` is an explicit Step32 content-phase status, not a
supported-release status. The Step32 closure may promote a row to
`LIVE_VERIFIED` only after the exact-head CI matrix has passed.

Step07 owns mapping this foundation into the complete SourceAdapter and
ingestion path.

Step11 security status: SQLite has live reference evidence for URI read-only,
query-only, authorizer, query guard and dlt/SQLAlchemy connection controls.
PostgreSQL, MySQL, MariaDB and SQL Server have policy/verifier contracts but
are NOT LIVE VERIFIED. Oracle is DEFERRED.
