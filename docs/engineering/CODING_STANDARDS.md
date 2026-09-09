# Coding Standards

- Python package imports use `dirty_data_to_olap`; no vendor type crosses an adapter boundary.
- Use typed, versioned project contracts and deterministic serialization. IDs are stable and content hashes are explicit.
- Keep side effects at ports. Application services receive dependencies through composition; they do not open ad hoc connections.
- Keep source reads bounded and read-only. Writes target controlled staging/analytical stores only.
- Prefer small pure functions for normalization, scoring and validation. Make policy and thresholds configuration data, not hidden constants.
- Preserve `run_id`, source snapshot identity, stage/attempt identity, policy/model versions and provenance on evidence and decisions.
- Represent uncertainty explicitly: `NEEDS_REVIEW`, `BLOCKED`, `FAILED`, `SKIPPED` and `UNRESOLVED` are not silently coerced to success.
- Never log secrets, credentials, raw source records or unnecessary PII. Tests use safe fixtures.
- Every change adds focused tests and a reproducible command. Every material contract change updates its schema/version and handoff evidence.
