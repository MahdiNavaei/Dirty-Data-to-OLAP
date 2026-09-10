# G3 — Source Safety

Status: `PASS`

This gate is owned by Specialist Step 11. The accepted V1 evidence boundary is
local SQLite source safety, privacy/source-exposure controls, and fail-closed
non-SQL provider policy. It does not claim live verification of every provider.

## Evidence

- G3A Source Usability: `PASS`.
- G3B Data Condition Measurable: `PASS`.
- Step10 privacy controls: unknown values fail closed outside restricted
  staging; logs/debug/exports are sanitized; aggregate-only external payloads
  are enforced; cleanup is project-authorized.
- SQLite source security: URI `mode=ro`, `query_only`, SQLite authorizer,
  SQLAlchemy connect setup, narrow generated-read API, query guard, and source
  immutability regression tests passed.
- Credential-purpose isolation: source credentials are scoped to
  `SOURCE_READ_ONLY`, source binding, profile, and credential version; target
  and administration purposes cannot authorize source access.
- Non-SQL fail-closed behavior: an independent provider verifier must return
  technically verified status with complete required-privilege coverage and
  explicit forbidden-privilege absence. Missing, failed, unknown, or operator-
  only evidence blocks before dlt initialization.
- Query guardrails: writes, DDL, attachment, dangerous PRAGMAs, procedure
  execution, filesystem export, mutation CTEs, and multi-statement SQL block.
- Source immutability: SQLite attack tests confirm source bytes remain
  unchanged; file-source boundaries remain read-only.
- Secret/log safety: contract artifacts retain references and fingerprints,
  never runtime secrets or raw SQL; canary tests passed.

## Provider limitation

G3 PASS does **not** mean PostgreSQL, MySQL, MariaDB, or SQL Server are live
verified. Their provider verifiers remain unavailable and runtime execution is
blocked by default. Oracle is deferred. No deployment IAM, encryption,
physical acceptance, or legal-compliance claim is made.
