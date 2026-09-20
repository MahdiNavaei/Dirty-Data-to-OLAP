# Compatibility and source support

There are two different claims:

1. **User-facing product path:** the accepted browser workflow currently uses
   managed CSV import.
2. **Adapter/source boundary:** the backend has reference-tested or CI-tested
   adapter paths for additional file and database sources.

These are not interchangeable. An adapter test does not mean the browser UI
offers that source as a first-class import flow.

| Source | Current adapter evidence | User-facing browser path |
|---|---|---|
| CSV | REFERENCE_TESTED; managed import path accepted | Supported V1 path |
| SQLite | REFERENCE_TESTED; read-only reference path | Adapter boundary; not the accepted browser upload path |
| PostgreSQL | LIVE_VERIFIED in CI service matrix `35034150663` | Adapter boundary; not the accepted browser upload path |
| MySQL | LIVE_VERIFIED in CI service matrix `35034150663` | Adapter boundary; not the accepted browser upload path |
| MariaDB | LIVE_VERIFIED in CI service matrix `35034150663` | Adapter boundary; not the accepted browser upload path |
| SQL Server | LIVE_VERIFIED in CI service matrix `35034150663` | Adapter boundary; not the accepted browser upload path |
| Parquet | REFERENCE_TESTED | Adapter boundary; not the accepted browser upload path |
| XLSX | OPTIONAL_TESTED with optional dependency | Adapter boundary; not the accepted browser upload path |
| Oracle | DEFERRED | Not supported in the current V1 evidence |

Database `LIVE_VERIFIED` means the tested source boundary and CI service
versions only. It is not a production deployment, HA, SLA, or scale claim.
Security qualification is narrower still: provider policy/verifier evidence
is required, and the security matrix does not turn compatibility evidence into
universal database security.

See the [detailed source matrix](../data-engineering/SOURCE_SUPPORT_MATRIX.md)
and [database compatibility matrix](../databases/COMPATIBILITY_MATRIX.md).
