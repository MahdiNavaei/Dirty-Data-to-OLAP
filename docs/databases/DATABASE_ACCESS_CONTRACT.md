# Database Access Contract — Step06

## Boundary

Step06 supplies the low-level, read-only database substrate for Step07. It
does not implement `SourceAdapter`, source registry, discovery orchestration,
source snapshots, dlt, extraction, staging, profiling or any business,
canonical, entity-resolution or OLAP semantics.

The public foundation exposes narrow operations:

- open a protected read-only session;
- inspect database/table metadata;
- perform a bounded sample with explicit columns and an explicit row bound;
- produce an EXPLAIN plan for that generated bounded read;
- use an explicit read-only transaction context.

There is no public arbitrary `execute(sql)` or `run_any_query` operation.
Driver connections, cursors, rows and exceptions remain inside the SQL adapter.

## Contracts

`ConnectionProfileReference` contains a stable profile ID, engine, non-secret
locator/configuration and external credential reference. It rejects extra
secret fields and secret-bearing DSNs. `DatabaseCapabilities` distinguishes
`PLANNED_REQUIRED`, `REFERENCE_TESTED`, `LIVE_VERIFIED` and `DEFERRED` claims.
`DatabaseFailure` normalizes engine, operation, safe detail, retryability,
cause category and context without retaining raw exceptions or credentials.
All persisted contract models carry schema version `1.0`.

`DatabaseAccessPolicy` is read-only, forbids DML/DDL and states read-only
transaction intent. `TimeoutPolicy` keeps connection, lock/busy and statement
deadlines distinct. `PoolPolicy` validates later-provider pool semantics but
Step06 does not build a bespoke production pool or claim pool-exhaustion
runtime evidence.

## SQLite reference behavior

The reference adapter opens a filesystem database with SQLite URI `mode=ro`,
enables `PRAGMA query_only = ON`, uses autocommit plus an explicit read-only
transaction context, sets a busy timeout, and closes the connection in a
context manager. A progress-handler deadline normalizes interrupted work as a
`TIMEOUT` failure.

Metadata returns project-owned table, view, column and declared-constraint
models. It reports physical and normalized types, nullability, ordered
composite primary keys, declared foreign keys, unique indexes and NOT NULL
metadata. It does not infer hidden relationships.

Sampling generates a quoted `SELECT` with caller-provided columns and a bound
`LIMIT` value. A head sample is recorded as a bounded observation, never as a
representative random sample. EXPLAIN is generated only for the same bounded
read shape.

## Handoff and limitations

Step07 may map these typed results into `SourceDescriptor`, `TableDescriptor`,
`ColumnDescriptor`, `DeclaredConstraint`, `SourceSnapshot` and batch/reference
contracts. Step06 intentionally does not create those orchestration artifacts.

Only SQLite was executed. PostgreSQL, MySQL, MariaDB and SQL Server remain
required but unverified; Oracle is deferred. Cross-engine identifier tests are
policy tests, not live compatibility evidence. Comprehensive least privilege,
provider compatibility, source extraction, pooling runtime and G3 remain
later work.

## Step11 security composition

Before discovery or extraction, the source adapter obtains a passing
`DatabaseSecurityAssessment`. Source credentials are purpose-scoped as
`SOURCE_READ_ONLY`; target, administration and test credentials are distinct.
SQLite adds URI read-only mode, query-only, an authorizer and SQLAlchemy/dlt
connect initialization. Non-SQLite providers fail closed without effective
privilege verification. The security assessment contains references and
fingerprints only, never a DSN or secret.
