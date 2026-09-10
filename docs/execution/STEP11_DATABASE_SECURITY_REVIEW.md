# Specialist Step11 Database Security Review

## GOAL RESULT

`PASS` for the Step11 database-security implementation scope. Formal
`G3_SOURCE_SAFETY` is accepted based on executed SQLite source safety,
privacy hardening and fail-closed provider policy evidence. Step12 is only a
handoff; no Step12 implementation was started.

## REPOSITORY BASELINE

Starting branch was `main`, at the Step10 handoff with G0/G1/G2 and G3A/G3B
PASS, formal G3 pending, and no Step11 implementation. The Step10 content
commit was preserved as the upstream baseline.

## REQUIRED READING COMPLETED

Reviewed the pasted Step11 goal, Step11 and Step12 specialist guidance, the
knowledge-base governance/base reports, product/domain/data/architecture and
engineering contracts, Step10 privacy handoff/review, database access docs,
and the actual SQLite, dlt, SQLAlchemy, source-adapter, profiling, privacy,
test and validator code.

## UPSTREAM STEP10 HARDENING

Unknown values now fail scans outside restricted staging; numeric identifier
payloads require an explicit aggregate-safe metric (with compatibility only
for clearly aggregate-named legacy metrics); recursive unknown strings are
redacted; debug bundles inherit sanitization; cleanup is project-authorized;
privacy YAML is runtime-authoritative; classification results return evidence;
and Step08 profile patterns are consumed without raw rereads.

## THREAT MODEL

Added `docs/security/DATABASE_THREAT_MODEL.md` covering mutation, credential
confusion, arbitrary SQL, dlt/SQLAlchemy bypass, export, leakage and unsafe
cleanup. Live scope is SQLite; network-provider live verification and Oracle
are not claimed.

## DATABASE SECURITY CONTRACTS

Added driver-neutral contracts for principal references, privilege
requirements/findings, policy, assessment/assurance, query classes/decisions,
driver policy, safe audit events and security failures. Persisted models carry
references/fingerprints only.

## CREDENTIAL PURPOSE / ISOLATION / RUNTIME CREDENTIAL RESOLUTION

Purposes are `SOURCE_READ_ONLY`, `TARGET_WRITE`, `ADMINISTRATION` and
`TEST_ONLY`. Runtime SQL credentials are redacted in representations and the
resolver is scoped by profile, purpose and source ID. Wrong-purpose,
cross-source and unverified credentials fail before dlt.

## SECURITY ASSURANCE / PROVIDER VERIFICATION

Assurance binds source, profile, credential reference/version, engine,
selection, policy and driver. SQLite has executed reference assurance.
PostgreSQL, MySQL, MariaDB and SQL Server have least-privilege policy and
verifier contracts but are `NOT LIVE VERIFIED`; Oracle is `DEFERRED`.

## SQLITE SECURITY / QUERY GUARDRAILS

SQLite uses URI `mode=ro`, `query_only`, a deny-by-default authorizer, safe
metadata PRAGMAs only, SQLAlchemy connect initialization and the narrow
generated metadata/sample/explain API. DML, DDL, attachment, dangerous
PRAGMA, export, procedure, mutation CTE and multi-statement cases are blocked
and audited by safe fingerprint/class only.

## DLT / SQLALCHEMY SECURITY INTEGRATION

The actual installed versions were inspected (`dlt 1.30.0`, `SQLAlchemy
2.0.46`). dlt callbacks are explicitly disabled and adapter configuration is
allowlisted. SQLite creator and connect-hook paths receive the required
initialization before dlt discovery/extraction.

## PROVIDER SECURITY MATRIX

Added provider role/grant policy and documentation-only PostgreSQL,
MySQL/MariaDB and SQL Server templates. No real username, password, admin
credential or grant was created or executed.

## SOURCE / FILE IMMUTABILITY

SQLite attack tests verify the source bytes remain unchanged. Existing source
and staging regressions continue to cover immutable CSV, Parquet and XLSX
boundaries; no source-writing path was added.

## PRIVACY + DATABASE SECURITY COMPOSITION

Database assurance is required before SQL discovery/snapshot. Privacy remains
the cross-cutting output boundary for raw staging, profiles, logs, debug,
exports and external processing.

## SECRET / LOG CANARY

Fake DB secrets, DSNs, raw SQL, PII, Persian/Unicode unknown text, runtime
credential representations, database failures, source failures and security
failures were checked for safe output. No canary appeared in persisted or
formatted contract output.

## G3 EVIDENCE

G3 PASS is limited to the V1 evidence boundary: executed local SQLite and
file/privacy controls plus fail-closed provider policy. It is not a claim of
live PostgreSQL/MySQL/MariaDB/SQL Server execution, Oracle support, physical
acceptance, deployment IAM, encryption or legal compliance.

## TESTS

- Security suite: `18 passed`.
- Unit/contract/integration/architecture regression: `73 passed`.
- Full suite: `91 passed, 41 warnings`; warnings are dependency deprecations
  and DataProfiler statistical warnings, not test failures.
- Required validators: all PASS, including `validate_database_security.py`.
- `python -m compileall -q src tools`: PASS.
- `git diff --check`: PASS.

## OUTPUT INSPECTION / SELF-REVIEW

Security artifacts contain safe references/fingerprints and no raw SQL or
secret values. SQLite catalogs, bounded extraction and Parquet staging remain
project-owned. The managed dlt resource cleanup bridge was exercised; the
full suite exits successfully, although a combined run can still emit one
third-party cursor-finalizer traceback after pytest exit. Provider live status
remains honest.

## FILES CHANGED

Step11 changed database-security/privacy contracts and services, SQLite/dlt
enforcement, security configuration/docs/templates, architecture ownership
and integration specs, validators, and security tests. The user-owned
untracked `tests/quality_unit_artifacts/` directory was preserved and not
staged.

## STEP11 EVIDENCE / STEP12 HANDOFF

Original Step11 content commit: `b511e83bd1ea59bac56ebb424fdd04a01f976942`.
G3 integrity-closure content commit: `b659b6fdd29046e8132d8a4bbbe0ed514af88114`.
Step12 remains unimplemented and receives the explicit handoff only after G3.

## EXECUTION STATE / GIT

Metadata records `last_completed_step: 11`, `current_step: 12`,
`current_role: dependency_discovery_engineer`, `G3_SOURCE_SAFETY: PASS`, and
`blocked: false`. The metadata commit is separate from the content commit.

## KNOWN LIMITATIONS

No live non-SQL provider was exercised. Provider effective grants, RLS,
security-definer behavior, deployment identity and Oracle remain outside this
executed pass. The 41 non-failing suite warnings remain attributable to
installed third-party dependencies. dlt 1.30.0 may emit a non-failing
cursor-finalizer diagnostic after a combined pytest process exits; the
targeted source tests and full suite still return exit code 0.

## Post-Step11 Independent G3 Integrity Closure

This surgical closure repaired defects found after the original Step11
receipt; it is not a new specialist step and does not begin Step12.

- Corrected the stale canonical G3 gate and README status, and synchronized
  the execution state, gate, receipt, privacy handoff and execution log.
- Removed resolver-provided provider verification and privilege findings from
  `RuntimeSqlCredentials`; provider evidence now crosses an explicit,
  project-owned verifier port.
- Removed the credential-resolution TOCTOU split: one protected operation
  resolves once and passes the same runtime credential and assurance context
  through engine creation to dlt.
- Closed privilege finding states and required complete technical coverage:
  required privileges must be present, forbidden privileges explicitly absent,
  and missing, unknown, failed, incomplete or operator-attested evidence
  blocks.
- Added behavior tests and validator checks for self-assertion, incomplete
  coverage, forbidden/unknown findings, verifier failure, dlt reachability,
  single resolution and assurance identity binding.
- Replaced validator OS-temporary scratch usage with repository-owned
  `workspace/test-temp/`, cleaned after use, and preserved
  `tests/quality_unit_artifacts/`.
- Closure evidence: security `23 passed`; full suite `96 passed, 41 warnings`;
  required validators, compileall and diff check passed. Final G3 decision:
  `PASS` within the documented V1 boundary. A non-failing dlt/SQLAlchemy
  cursor-finalizer diagnostic may remain after combined test-process exit.
