# Source Database Access Security

V1 source access is a fail-closed, read-only boundary. A source credential is
resolved only for `SOURCE_READ_ONLY`, an assurance is bound to source/profile,
credential reference and version, engine, selection, driver and policy, and
the assurance must pass before discovery or dlt extraction.

SQLite is the executed reference implementation. It uses a filesystem URI
with `mode=ro`, `query_only`, a SQLite authorizer, SQLAlchemy connect hooks,
bounded generated reads and no public arbitrary SQL method. Provider-backed
engines require a provider verifier and are blocked when that assurance is not
available.

Security events contain IDs, query class, decision, policy, credential
reference, assurance status and a query fingerprint. They do not contain raw
SQL, literals, DSNs, passwords, tokens or PII.
