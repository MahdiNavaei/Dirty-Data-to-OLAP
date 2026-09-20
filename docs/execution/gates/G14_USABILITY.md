# G14 — Usability

Status: `PASS`

Step40 established the verified developer-experience boundary. The repository
has a project-local `ddo` interface for version/configuration/doctor,
locked-profile bootstrap, a deterministic real-API control-plane demo, test
checks, OpenAPI verification, frontend diagnostics, and a local server
entrypoint. The PowerShell workflow is documented in
[`docs/getting-started/README.md`](../../getting-started/README.md) and
[`docs/development/DEVELOPER_WORKFLOW.md`](../../development/DEVELOPER_WORKFLOW.md).

Evidence: Step40 focused tests passed, the project-local state-boundary repair
was validated, and exact-head CI run `35464747467` passed the G8-G14
regression, Step40 validator, security scan, and image scan jobs.

The PASS is bounded. The demo is a control-plane smoke rather than the full
OLAP product path; exact pinned host prerequisites remain fail-closed; and no
production deployment, authentication, HA, or capacity claim follows from
G14.
