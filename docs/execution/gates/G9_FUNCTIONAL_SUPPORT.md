# Gate G9 — Functional Support

Status during Step32 content phase: `PENDING`

Meaning: Claims Match Tested Support.

Required evidence:

- current claim inventory and source-of-truth matrix;
- real SQLite and file adapter boundary tests;
- real PostgreSQL, MySQL, MariaDB and SQL Server CI fixtures for the required
  V1 targets;
- explicit Oracle deferred status;
- negative evidence for missing provider configuration and source writes;
- exact-head CI evidence for both the content and closure commits.

The gate must not be promoted from `PENDING` until the exact content-head CI
has passed the full Step32 matrix. `tools/validate_step32_compatibility.py`
fails closed when a required live provider is absent.
