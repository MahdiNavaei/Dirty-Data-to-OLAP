# V1 Source Support Matrix

| Source | Status | Evidence / limitation |
|---|---|---|
| SQLite | REFERENCE_TESTED | Full registry -> discovery -> dlt extraction -> Parquet staging path; read-only negative test |
| PostgreSQL | LIVE_VERIFIED | Exact-head CI run `35034150663`: real discovery, extraction, staging and source-write rejection passed |
| MySQL | LIVE_VERIFIED | Exact-head CI run `35034150663`: real discovery, extraction, staging and source-write rejection passed |
| MariaDB | LIVE_VERIFIED | Exact-head CI run `35034150663`: real discovery, extraction, staging and source-write rejection passed |
| SQL Server | LIVE_VERIFIED | Exact-head CI run `35034150663`: real discovery, extraction, staging and source-write rejection passed |
| CSV | REFERENCE_TESTED | UTF-8/BOM, quoting, empty values, literal null marker, duplicates and malformed-row failure |
| Parquet | REFERENCE_TESTED | PyArrow schema, multiple row groups, projection, bounded batches and read-back |
| XLSX | OPTIONAL_TESTED | Read-only openpyxl worksheet path with bounded extraction |
| Oracle | DEFERRED | Outside the current V1 implementation evidence |

`LIVE_VERIFIED` is limited to the tested source boundary and CI service
versions; it is not a production deployment, HA or capacity claim. Oracle
remains deferred.
