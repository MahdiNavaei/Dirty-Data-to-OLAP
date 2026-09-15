# Step32 Compatibility Test Engineer Review

## Scope and gate

This review owns the source/environment compatibility matrix and is evaluated
by `G9_FUNCTIONAL_SUPPORT` (Claims Match Tested Support). It does not add a
database connector, change the SourceAdapter contract, alter product behavior,
or begin Step33.

The source of truth is the combination of:

- `docs/databases/COMPATIBILITY_MATRIX.md` for database capability status;
- `docs/data-engineering/SOURCE_SUPPORT_MATRIX.md` for the source-boundary
  status;
- `tests/compatibility/test_step32_compatibility.py` for executable evidence;
- `tools/validate_step32_compatibility.py` for the CI fail-closed gate and
  machine-readable receipt.

## Claim inventory

| Claim surface | Current claim | Test boundary | Content-phase status |
|---|---|---|---|
| User-facing Step29 import | Managed CSV import with the real product path | Existing Step29 product path plus Step32 CSV source-boundary case | `REFERENCE_TESTED` |
| Source adapter | SQLite, PostgreSQL, MySQL, MariaDB, SQL Server, CSV, Parquet and optional XLSX | `SourceDiscoveryService` -> `SourceSnapshotService` -> project-owned staging contracts | SQLite/files `REFERENCE_TESTED`; SQL engines `LIVE_VERIFIED` by exact-head CI |
| Analytical target | Local DuckDB reference target | Existing upstream G6/G7/G8 evidence | Preserved; not re-scoped by Step32 |
| Oracle | Deferred by V1 | No test is presented as support evidence | `DEFERRED` |

The current product registration service accepts CSV only. The broader SQL and
file rows describe the existing SourceAdapter boundary and preserve the V1
requirements; they are not silently converted into user-facing release claims.

## Database matrix

| Engine | Adapter path | Required test | Content-phase result |
|---|---|---|---|
| SQLite | dlt/SQLAlchemy reference with SQLite read-only controls | discovery, Unicode/decimal rows, staging and source-write rejection | `REFERENCE_TESTED` |
| PostgreSQL | dlt/SQLAlchemy | real service, read-only role, discovery, extraction, Parquet staging, write rejection | `LIVE_VERIFIED` |
| MySQL | dlt/SQLAlchemy | real service, read-only role, discovery, extraction, Parquet staging, write rejection | `LIVE_VERIFIED` |
| MariaDB | dlt/SQLAlchemy | real service, read-only role, discovery, extraction, Parquet staging, write rejection | `LIVE_VERIFIED` |
| SQL Server | dlt/SQLAlchemy | real service, read-only role, discovery, extraction, Parquet staging, write rejection | `LIVE_VERIFIED` |
| Oracle | none in current V1 slice | not applicable | `DEFERRED` |

The CI fixtures use disposable databases and a separately provisioned source
principal. Runtime URLs are supplied through the CI environment and never
enter contracts, reports or logs. The provider verifier queries the live
principal's effective grants before the dlt operation is allowed.

## File matrix

| Format | Cases exercised through the actual adapter boundary |
|---|---|
| CSV | UTF-8 BOM, quoted Unicode/comma content, row accounting and Parquet staging |
| Excel | XLSX worksheet discovery, Unicode values, bounded extraction and staging |
| Parquet | schema discovery, multiple row groups, projection, bounded extraction and staging |

## Runtime matrix

| Environment | Evidence |
|---|---|
| Windows developer host | Python 3.10.11 local reference run; SQLite/files always executable |
| GitHub Actions | Ubuntu runner, locked Python 3.11.16, disposable real database services and pinned project dependencies |
| Container runtime | Upstream Step30/G8 container evidence remains authoritative; Step32 does not change the runtime image |

## Closure evidence

- Implementation commit: `12fdfe7e87ecf78a01384e73137d937d3ad08fa7`
- Final content-head test commit: `29f5f77eed2b0a946aa348462ec1b009fe5c8264`
- Exact-head CI run: `35034150663`; Secret scan, G8, image scan, Step31 QA
  and Step32 compatibility jobs all passed.
- Compatibility receipt: `tools/validate_step32_compatibility.py --ci`
  reported `8 passed, 0 skipped` across the required live database matrix.
- G9 result: `PASS`.

## Negative evidence and limitations

- A missing CI provider environment fails `--ci`; it is never reported as a
  skipped pass.
- Oracle remains deferred and is not represented as supported.
- Policy/identifier tests alone do not establish provider compatibility.
- Compatibility evidence covers the source boundary, not HA, capacity,
  production deployment or application security (G10+).

G9 is `PASS` after exact content-head CI run `35034150663` completed green.
The evidence is limited to the tested source boundary; Oracle and G10-G15
remain outside this closure.
