# Step33 Application Security Threat Model

## Scope and decision

Step33 reviews the request-reachable application and data-control surfaces of
Dirty Data to OLAP. The review covers the FastAPI entrypoint, the application
backend and durable control plane, project/run/source/artifact/review/job
boundaries, semantic SQL and DuckDB execution, filesystem source adapters,
runtime SQL connection validation, frontend rendering, dependency manifests,
and CI security checks.

The review is bounded to the local reference deployment and the repository's
typed contracts. It does not claim a production identity provider, public
deployment, cloud metadata access, physical multi-node acceptance, or the
Step39 penetration-test closure.

## System and trust boundaries

```text
untrusted HTTP client
        |
        v
FastAPI request limits + typed validation + auth resolver
        |
        v
application backend / durable control store
   |             |                 |
   v             v                 v
source registry  run/job/review    artifact store
   |             |                 |
   v             v                 v
confined file/SQL adapters       run-bound manifests/payloads
                                      |
                                      v
                         semantic compiler -> local DuckDB target
```

The client never supplies an authoritative owner, run owner, review subject,
artifact payload path, SQL template, or execution command. A trusted proxy may
provide a principal only through the configured resolver and the resolver's
`TRUSTED_PROXY` source marker. The `LOCAL_TEST_AUTH` mode is an explicitly
local test seam; it is not production authentication.

## Assets and threats

| Asset | Threat | Security property |
|---|---|---|
| Project/run state | Cross-project object access or mutation | Project and run authorization |
| Review decisions | Replay, stale acceptance, subject substitution | Revision, run, and content binding |
| Source credentials | URL or exception disclosure | Redaction and typed failure |
| Source files and databases | Path escape, unsafe scheme, writes | Root confinement, allowlists, read-only adapters |
| Semantic target | SQL injection or file-function escape | Structural re-rendering and execution guard |
| Durable jobs | Cross-run replay, unauthorized control, stale publication | Run authorization, idempotency, fencing |
| Browser projection | XSS and sensitive internal disclosure | React text rendering and server-owned projection |
| Dependency supply chain | Known vulnerable packages | Locked dependency audit and CI gate |

## Abuse cases and controls

| Area | Abuse case | Control and evidence |
|---|---|---|
| Authorization | Principal reads or cancels another project's run | Explicit project claims for trusted principals; local-test owner binding; `SEC-AUTH-001/002/004` |
| Proxy identity | Client spoofs a trusted identity header | Client identity headers are ignored in trusted-proxy mode; resolver source is fail-closed; `SEC-AUTH-003` |
| Reviews | Old, foreign-run, or mutated subject is accepted | Server-owned subject context, revision checks, and content hashes; `SEC-REV-001/002/003` |
| SQL | Values alter a query or a forged template invokes a file function | Parameters carry values; structural SQL is re-rendered and DuckDB rejects file functions; `SEC-SQL-001/002/003` |
| Network | HTTP or credential-smuggling runtime URL reaches SQLAlchemy | Supported scheme and profile-host checks precede provider verification; `SEC-NET-001/002` |
| Filesystem | Absolute, traversal, or symlink path leaves project root | Strict resolution and `relative_to(project_root)` confinement; `SEC-FS-001/002/003` |
| Cleanup | Caller deletes a resource without a bound permit | Existing Step23 cleanup authorization and deletion-permit contract; `SEC-FS-004` |
| Jobs | Caller replays or controls another run's durable job | Run-bound job identity, authorization, idempotency, lease/fencing; `SEC-JOB-001/002/003` |
| API | Large body, malformed JSON, or internal error leaks detail | Content-length/streaming bounds, typed validation, generic error responses; `SEC-API-001/002` |
| Frontend | Untrusted labels or errors become markup | React interpolation only; no dangerous HTML APIs; `SEC-XSS-001` |

## Explicit non-applicable surfaces

`SEC-DESER-001` is not applicable to the request-reachable Step33 surface:
the API/backend contain no request-reachable pickle loader, unsafe YAML loader,
or arbitrary object deserializer, and job execution uses typed durable records
rather than caller-supplied commands. The repository still records this as an
explicit scenario with architecture evidence; it is not silently omitted.

There is no public result-publication endpoint in the current API. The durable
job/result state is exposed only through authorized run-bound views, so
`SEC-JOB-003` is tested as stale/run-bound result visibility rather than as a
public callback receiver.

## Residual risk and ownership

- `LOCAL_TEST_AUTH` is suitable only for local deterministic tests. A production
  deployment must use an authenticated trusted resolver and explicit project
  claims.
- The filesystem and runtime SQL tests are local/reference tests. They do not
  prove cloud-network SSRF behavior or a physical production deployment.
- The frontend XSS evidence is a rendering/static contract test, not a claim of
  completion of the later adversarial penetration-test step.
- G10 closes only the applicable Critical/High findings found in this review.
  Capacity, resilience, adversarial security, usability, and release remain
  owned by G11-G15.
