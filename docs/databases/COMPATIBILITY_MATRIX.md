# Database Compatibility Matrix — Step06

This matrix reports executed evidence, not intended V1 scope alone.

| Engine | V1 intent | Step06 status | Evidence / limitation |
|---|---|---|---|
| SQLite | Local reference/demo | `REFERENCE_TESTED` | Read-only connection, metadata, declared constraints, bounded sampling, EXPLAIN, cleanup and deadline tests pass against the project-local fixture. |
| PostgreSQL | Required V1 target | `PLANNED_REQUIRED` | Contract and identifier-policy coverage only; no live PostgreSQL server was exercised. |
| MySQL | Required V1 target | `PLANNED_REQUIRED` | Contract and identifier-policy coverage only; no live MySQL server was exercised. |
| MariaDB | Required V1 target | `PLANNED_REQUIRED` | Contract and identifier-policy coverage only; no live MariaDB server was exercised. |
| Microsoft SQL Server | Required V1 target | `PLANNED_REQUIRED` | Contract and identifier-policy coverage only; no live SQL Server was exercised. |
| Oracle | Deferred | `DEFERRED` | Outside the current V1 implementation slice. |

Identifier quoting tests validate project policy for SQLite, PostgreSQL,
MySQL/MariaDB and SQL Server. They do not establish live compatibility.

Step07 owns mapping this foundation into the complete SourceAdapter and
ingestion path.
