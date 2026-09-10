# V1 Database Threat Model

| Threat | Control | Evidence boundary |
|---|---|---|
| Source mutation | URI read-only, query-only, authorizer, provider role | SQLite executed; other providers policy/verifier only |
| Credential confusion or privilege escalation | purpose-scoped resolver and effective-grant findings | contract and negative tests |
| Arbitrary SQL or multi-statement injection | narrow generated API and query guard | classification and SQLite attack tests |
| dlt/SQLAlchemy connection bypass | creator/connect initialization hook | dlt SQLite extraction regression |
| Data export through SQL | blocked export/procedure/attachment classes | negative query tests |
| Secret or PII leakage | reference-only contracts, redacted failures, recursive sanitizer | canary tests |
| Unsafe cleanup | project/privacy-owned root validation | retention tests |

Out of scope for this pass: live network-provider penetration testing, Oracle
verification, deployment IAM, host compromise, and physical acceptance. G3
claims therefore distinguish executed SQLite evidence from provider policy.
