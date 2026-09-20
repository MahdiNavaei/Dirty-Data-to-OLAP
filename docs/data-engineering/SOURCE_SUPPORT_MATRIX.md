# V1 Source Support Matrix

This matrix describes the adapter/source boundary. It does not mean that every
source is available through the current browser UI. The accepted user-facing
product path is managed CSV import; additional rows below are backend adapter
evidence.

| Source | Status | Evidence / limitation |
|---|---|---|
| SQLite | REFERENCE_TESTED | Full registry -> discovery -> dlt extraction -> Parquet staging path; read-only negative test; adapter boundary, not the accepted browser upload path |
| PostgreSQL | LIVE_VERIFIED | Exact-head CI run `35034150663`: real discovery, extraction, staging and source-write rejection passed; adapter boundary, not the accepted browser upload path |
| MySQL | LIVE_VERIFIED | Exact-head CI run `35034150663`: real discovery, extraction, staging and source-write rejection passed; adapter boundary, not the accepted browser upload path |
| MariaDB | LIVE_VERIFIED | Exact-head CI run `35034150663`: real discovery, extraction, staging and source-write rejection passed; adapter boundary, not the accepted browser upload path |
| SQL Server | LIVE_VERIFIED | Exact-head CI run `35034150663`: real discovery, extraction, staging and source-write rejection passed; adapter boundary, not the accepted browser upload path |
| CSV | REFERENCE_TESTED | UTF-8/BOM, quoting, empty values, literal null marker, duplicates and malformed-row failure; managed CSV import is the accepted browser path |
| Parquet | REFERENCE_TESTED | PyArrow schema, multiple row groups, projection, bounded batches and read-back; adapter boundary, not the accepted browser upload path |
| XLSX | OPTIONAL_TESTED | Read-only openpyxl worksheet path with bounded extraction; adapter boundary, not the accepted browser upload path |
| Oracle | DEFERRED | Outside the current V1 implementation evidence |

`LIVE_VERIFIED` is limited to the tested source boundary and CI service
versions; it is not a production deployment, HA or capacity claim. Oracle
remains deferred.
