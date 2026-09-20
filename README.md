# Dirty Data to OLAP

Dirty Data to OLAP is an evidence-first V1 reference product for turning a
bounded, read-only tabular source into a reviewed and validated OLAP-ready
analytical layer. The product keeps source observations, evidence, review
decisions, canonical identity, analytical planning, materialization, and G6
correctness validation separate and inspectable.

This repository is a local/reference implementation. It does not claim
production deployment, high availability, a production SLA, universal source
security, or measured multi-million-row capacity.

The Step 05 architecture and engineering baseline remains preserved as
historical design evidence; this README describes the current implementation
and later gate state.

## Current product path

The user-facing browser path is a managed CSV import followed by source
binding, server-owned plan preparation, durable execution, four typed review
checkpoints, materialization, validation, and an output projection only after a
successful G6-eligible run. The wider adapter boundary also covers tested
SQLite, PostgreSQL, MySQL, MariaDB, SQL Server, CSV, Parquet, and optional XLSX
paths. Oracle is deferred.

The Step40 `ddo demo` is intentionally smaller: it is a deterministic,
project-local control-plane smoke through health, configuration, run creation,
and run read. It is not the full OLAP product journey.

## Quickstart

From PowerShell at the repository root:

```powershell
python tools/ddo.py bootstrap --profile core
python tools/ddo.py doctor
python tools/ddo.py demo
python tools/ddo.py check --tests
```

The same interface is available as `uv run ddo ...` after the locked
environment is bootstrapped. Disposable state is kept under project-local
`.ddo/` and `.venv/`; the demo refuses external or traversal state roots.

## Navigate the documentation

- [Documentation map](docs/README.md)
- [Getting started](docs/getting-started/README.md)
- [User guide and full workflow](docs/user-guide/README.md)
- [Concepts and trust boundaries](docs/concepts/README.md)
- [Architecture](docs/architecture/README.md)
- [API and authentication](docs/api/README.md)
- [Source support](docs/compatibility/README.md)
- [Validation and evidence](docs/validation/README.md)
- [Operations and recovery](docs/operations/README.md)
- [Troubleshooting](docs/troubleshooting/README.md)
- [Release notes and limitations](docs/release/README.md)
- [Developer workflow](docs/development/DEVELOPER_WORKFLOW.md)

The older [pre-implementation documentation pack](docs/00_README.md) remains
available as architectural history. It is not the current user guide.

The preserved specialist record covers Steps 01-21, including Step15, and
records the historical `G0/G1/G2/G3 PASS` checkpoints. Those references are
historical evidence; the current implementation and gate status above are
the authoritative user-facing summary.

## Release gates

G0 through G15 are PASS in the authoritative execution state. G15 is the
documentation and release-accuracy gate. A G15 PASS is not a universal
production-readiness certificate.

## Important boundaries

- Fusion scores are uncalibrated decision scores, not probabilities.
- No inference automation threshold is selected or enabled.
- Entity-resolution clusters are reviewed linkage evidence, not automatic
  canonical truth.
- The product does not invent revenue or GMV semantics.
- Source systems are treated as read-only by the V1 contract.
- No release tag, deployment, or external publication is performed by this
  repository workflow.

See [the release limitations](docs/release/LIMITATIONS.md) for the complete
scope and evidence boundary.
