# G8 — Reproducible Build

Status: `PASS`.

Owner: Specialist Step30 - DevOps Engineer.

## Evidence

- Content commit: `9857e390e48c4260e957ece796d05373f63dde2b`.
- Remote CI run:
  [`34900471499`](https://github.com/MahdiNavaei/Dirty-Data-to-OLAP/actions/runs/34900471499).
- Clean-room G8 / build / runtime: `PASS`, `check_count=62`.
- Secret scan: `PASS`.
- Container image vulnerability scan: `PASS` for the rebuilt backend and
  frontend tar inputs using pinned Trivy `0.74.0` with checksum verification.
- Clean worktree verification after clean-room validation: `PASS`.

The clean-room path used locked Python and Node installations, repository-owned
Playwright, source-built Desbordante revision
`b211961f3f272ed8815ef1ffbda90573b11e1116`, digest-pinned container bases,
Compose health/runtime controls, the actual FastAPI/durable-worker/Step29 path,
and no Docker socket or source bind mount.

The image scan job passed scan execution with `--exit-code 0 --ignore-unfixed`.
That is not a claim that all vulnerabilities are absent; the JSON scan outputs
were not uploaded as workflow artifacts. G9-G15 remain `PENDING`.

Step31 remains `NOT_STARTED`.
