# Step33 Application Security Review

## Result

Scope: application security and G10 evidence for the local reference product.

Result: `PASS` for the reviewed scope after focused remediation and regression
testing. No unresolved applicable Critical or High finding remains in the
reviewed surface. This is not a penetration-test closure; Step39 owns that
broader adversarial claim.

Starting content baseline: `788bb4bdeed9341278194a3e67adf84dfc88f7ba`.
The accepted prior handoff is Step32/G9 `PASS`. Step34 is not started.

## Finding summary

| Severity | Found | Open | Disposition |
|---|---:|---:|---|
| Critical | 0 | 0 | None identified |
| High | 4 | 0 | Authorization BOLA, source ownership, filesystem escape, and runtime URL boundary were repaired |
| Medium | 4 | 0 | Proxy-source spoofing, unbounded import body, error/secret handling, and review/control-plane boundary checks were repaired or bounded |
| Low | 1 | 0 | Local-test authentication limitation is documented and isolated |

The baseline findings were established by reading the actual implementation and
reproducing the relevant denial/escape cases in the Step33 security tests. The
current result is based on the post-repair test run, not on a static scanner
alone.

## Required scenario matrix

| ID | Result | Evidence boundary |
|---|---|---|
| SEC-AUTH-001 | PASS | Cross-project run read returns 404 and list is filtered |
| SEC-AUTH-002 | PASS | Cross-project cancel returns 403 |
| SEC-AUTH-003 | PASS | Spoofed client header is ignored; untrusted resolver source returns 401 |
| SEC-AUTH-004 | PASS | Foreign jobs/artifacts are not visible through another project/run |
| SEC-REV-001 | PASS | Reused revision returns 409 |
| SEC-REV-002 | PASS | Review context cannot replay onto a different run |
| SEC-REV-003 | PASS | Mutated subject hash returns 409 |
| SEC-SQL-001 | PASS | Identifier structure comes from the typed semantic model |
| SEC-SQL-002 | PASS | Injection-like filter value remains a bound parameter |
| SEC-SQL-003 | PASS | Forged DuckDB file-function template is rejected |
| SEC-NET-001 | PASS | Unsupported runtime scheme is rejected before provider access |
| SEC-NET-002 | PASS | Credential-bearing URL is absent from the typed error |
| SEC-FS-001 | PASS | Traversal/escape locator is rejected |
| SEC-FS-002 | PASS | Absolute outside-root locator is rejected |
| SEC-FS-003 | PASS | Symlink escape is rejected where the host supports symlinks |
| SEC-FS-004 | PASS | Step23 cleanup permit remains required for deletion |
| SEC-DESER-001 | NOT_APPLICABLE | No request-reachable unsafe deserialization or arbitrary object loader |
| SEC-JOB-001 | PASS | Job access is bound to the authorized run/project |
| SEC-JOB-002 | PASS | Cancel/resume mutations require authorized run ownership |
| SEC-JOB-003 | PASS | Durable result visibility is run-bound; no public callback publisher exists |
| SEC-XSS-001 | PASS | React renders untrusted values as text; dangerous HTML APIs absent |
| SEC-API-001 | PASS | Import body and request JSON are bounded; oversized input returns 413 |
| SEC-API-002 | PASS | Malformed/error responses omit tracebacks and submitted secret canaries |
| SEC-SEC-001 | PASS | Secret-bearing runtime failure is redacted |
| SEC-DEP-001 | PASS | Locked Python and npm audits report no known vulnerabilities |

## Remediation record

### Authorization and isolation

`Principal` now carries explicit project claims for trusted principals. The
backend authorizes project and run access before resolving or mutating the
object, filters run/source listings, and stores an internal run owner that is
never accepted from caller metadata. Local-test access is owner-bound. Route
handlers pass the authenticated principal to every protected read and write.

### Review, artifact, and job security

Review subject context remains server-owned and is checked against run,
artifact, content hash, revision, and expected checkpoint state. Artifact,
job, validation, and visualization views are authorized against the run before
data is returned. Existing lease/fencing and idempotency controls remain
unchanged; the Step33 changes enforce the missing caller boundary around them.

### SQL, network, and filesystem boundaries

Semantic SQL remains structural and read-only. Values are parameters, and a
tampered template cannot introduce DuckDB file functions. SQL runtime URLs are
restricted to the profile's supported scheme and host before credentials are
resolved. File and SQLite locators resolve strictly under the project root;
outside paths and symlink escapes fail closed.

### API, frontend, and dependency controls

Request bodies have explicit bounds and source upload streaming is bounded.
Errors remain typed and generic. The frontend uses React text interpolation
for server values and does not call dangerous HTML APIs. Locked dependency
versions were reconciled to DataProfiler 0.14.0, cryptography 50.0.1, requests
2.34.2, pyarrow 23.0.1, pytest 9.1.1, setuptools 83.0.0, and the compatible
frontend toolchain; scoped Python and npm audits report no known vulnerability.

## Evidence and limitations

- Primary executable evidence: `tests/security/test_step33_application_security.py`.
- State negative controls: `tests/unit/test_step33_state_classification.py` and
  `tests/unit/test_step33_appsec_validator.py`.
- Machine-readable receipt: `output/step33_appsec_validation.json`.
- Gate validator: `tools/validate_step33_appsec.py`.
- No protected `tests/quality_unit_artifacts/` content is part of the evidence.
- A green dependency audit is not evidence that future or undisclosed
  vulnerabilities do not exist; it is a current locked-tree check.
