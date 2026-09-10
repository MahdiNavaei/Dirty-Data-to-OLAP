# Source Privilege Guide — Step06

Dirty Data to OLAP accesses operational sources read-only. A source profile
must reference externally managed credentials; committed configuration must not
contain passwords, tokens, secret-bearing DSNs or credential material.

## Minimum expectation

The source identity used by a later provider should have only:

- connection permission to the named database;
- metadata/catalog visibility for the explicitly selected schemas and tables;
- `SELECT` permission on selected source tables and views;
- permission to inspect declared metadata where the provider exposes it.

The account must not have production DDL/DML permission, administrative/root/
superuser privilege, ownership of source objects, or permission to create
temporary objects in the protected source unless a later reviewed policy
explicitly requires and isolates that capability.

Step06 enforces the local SQLite reference with URI read-only mode and
`PRAGMA query_only = ON`. Step11 will prove least privilege and broader source
security for provider integrations; this document does not claim G3 PASS.

## Step11 security status

Use `docs/security/LEAST_PRIVILEGE_ROLES.md` for provider-specific policy.
Unknown or inherited administrative grants block registration. Source
credentials are `SOURCE_READ_ONLY` only; target-write and administration
credentials are never reused.
