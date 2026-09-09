# V1 Source Support Matrix

| Source | Status | Evidence / limitation |
|---|---|---|
| SQLite | REFERENCE_TESTED | Full registry -> discovery -> dlt extraction -> Parquet staging path; read-only negative test |
| PostgreSQL | IMPLEMENTED_NOT_LIVE_VERIFIED | Generic dlt SQL path and contract; no provider executed |
| MySQL | IMPLEMENTED_NOT_LIVE_VERIFIED | Generic dlt SQL path and contract; no provider executed |
| MariaDB | IMPLEMENTED_NOT_LIVE_VERIFIED | Generic dlt SQL path and contract; no provider executed |
| SQL Server | IMPLEMENTED_NOT_LIVE_VERIFIED | Generic dlt SQL path and contract; no provider executed |
| CSV | REFERENCE_TESTED | UTF-8/BOM, quoting, empty values, literal null marker, duplicates and malformed-row failure |
| Parquet | REFERENCE_TESTED | PyArrow schema, multiple row groups, projection, bounded batches and read-back |
| XLSX | OPTIONAL_TESTED | Read-only openpyxl worksheet path with bounded extraction |
| Oracle | DEFERRED | Outside the current V1 implementation evidence |

`IMPLEMENTED_NOT_LIVE_VERIFIED` is not a live compatibility claim.
