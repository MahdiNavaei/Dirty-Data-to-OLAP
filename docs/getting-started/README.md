# Getting started

The supported local workflow uses Python 3.11.16, Node 22.14.0, npm, and uv
0.11.26. The repository declares these versions in `.python-version`,
`.node-version`, and the Step40 developer-experience contract.

## PowerShell quickstart

Run from the repository root:

```powershell
python tools/ddo.py version
python tools/ddo.py config
python tools/ddo.py bootstrap --profile core
python tools/ddo.py doctor
python tools/ddo.py demo
python tools/ddo.py check --tests
```

`bootstrap` uses locked dependencies and project-local caches. It may create
`.venv/`, `.ddo/`, and ignored frontend dependency state. `doctor` is
fail-closed for missing exact tools; optional provider profiles are not
required for the core demo.

See the [configuration reference](../configuration/README.md) for profiles,
project-local paths, and authentication boundaries.

## What the demo proves

`ddo demo` uses the actual FastAPI boundary through a test client and checks
health, product configuration, idempotent run creation, and run retrieval. It
writes a small result beneath the project-local `.ddo/demo` state root. It does
not import a real source, execute the full OLAP pipeline, prove external
database connectivity, or establish production authentication.

## Full product path

For the complete browser path, use the [user guide](../user-guide/README.md)
and the local API entrypoint described in the [developer workflow](../development/DEVELOPER_WORKFLOW.md).
The accepted real-pipeline evidence uses the managed CSV path and the four
review checkpoints; it is local/reference evidence.

## If prerequisites are unavailable

Do not substitute an unpinned interpreter silently. Record the failed
prerequisite, use the pinned CI workflow for reproducibility evidence, and see
[troubleshooting](../troubleshooting/README.md).
