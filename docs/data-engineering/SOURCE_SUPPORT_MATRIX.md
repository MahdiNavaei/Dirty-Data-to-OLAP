# V1 Source Support Matrix

| Source | Status | Evidence / limitation |
|---|---|---|
| SQLite | REFERENCE_TESTED | Full registry -> discovery -> dlt extraction -> Parquet staging path; read-only negative test |
| PostgreSQL | CI_LIVE_TEST_REQUIRED | Generic dlt SQL path and contract; Step32 exact-head CI fixture is required before live support is claimed |
| MySQL | CI_LIVE_TEST_REQUIRED | Generic dlt SQL path and contract; Step32 exact-head CI fixture is required before live support is claimed |
| MariaDB | CI_LIVE_TEST_REQUIRED | Generic dlt SQL path and contract; Step32 exact-head CI fixture is required before live support is claimed |
| SQL Server | CI_LIVE_TEST_REQUIRED | Generic dlt SQL path and contract; Step32 exact-head CI fixture is required before live support is claimed |
| CSV | REFERENCE_TESTED | UTF-8/BOM, quoting, empty values, literal null marker, duplicates and malformed-row failure |
| Parquet | REFERENCE_TESTED | PyArrow schema, multiple row groups, projection, bounded batches and read-back |
| XLSX | OPTIONAL_TESTED | Read-only openpyxl worksheet path with bounded extraction |
| Oracle | DEFERRED | Outside the current V1 implementation evidence |

`IMPLEMENTED_NOT_LIVE_VERIFIED` is not a live compatibility claim.
