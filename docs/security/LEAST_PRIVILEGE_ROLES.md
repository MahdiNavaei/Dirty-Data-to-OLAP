# Least-Privilege Source Roles

Role templates are policy/documentation artifacts only; no real role was
created or executed by this pass. Create a dedicated source principal using
the provider's approved administrative process, then verify its effective
grants before use.

| Engine | Required | Optional only when measured as needed | Forbidden |
|---|---|---|---|
| PostgreSQL | CONNECT, USAGE, SELECT | VIEW DEFINITION | SUPERUSER, CREATEDB, CREATEROLE, REPLICATION, BYPASSRLS, DML, DDL, EXECUTE, TRUNCATE, TEMP by default |
| MySQL/MariaDB | SELECT | metadata, SHOW VIEW | INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, EXECUTE, FILE, PROCESS, SUPER, GRANT OPTION, CREATE USER, TRIGGER, EVENT, RELOAD |
| SQL Server | CONNECT, SELECT | VIEW DEFINITION | CONTROL, ALTER, DML, EXECUTE, ownership/impersonation, db_owner, sysadmin |
| SQLite | no server grant | none | source path write access and all mutation/attachment/export operations |

Unknown grants, provider superuser/administrative flags, missing required
grants and public/PUBLIC access that defeats the role are blocking findings.
RLS and security-definer behavior must be reviewed explicitly for PostgreSQL.
Oracle is deferred for V1.
