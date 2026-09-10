# Query Guardrails

The source boundary generates metadata and bounded reads from structured
identifiers. It does not expose `execute`, `query`, `run_sql` or raw SQL to
callers. The internal guard classifies query shapes as READ_QUERY,
METADATA_QUERY or TRANSACTION_CONTROL_ALLOWED; all other classes are blocked:
write DML, DDL, procedure execution, filesystem export, multi-statement and
UNKNOWN. Mutation CTEs, comments hiding a second statement, `SELECT INTO`,
`OUTFILE`, `COPY TO`, `CALL/EXEC/DO`, `ATTACH/DETACH`, writable-schema PRAGMAs
and `VACUUM INTO` are negative cases.

The guard is defense in depth, not a replacement for provider authorization.
SQLAlchemy/dlt connections receive required initialization hooks, and a hook
failure prevents source use.
