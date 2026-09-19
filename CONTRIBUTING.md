# Contributing

Dirty Data to OLAP uses a project-local, pinned developer workflow. The
repository is source-read-only with respect to upstream data systems; local
demo state and caches belong under `.ddo/`.

## First setup

From the repository root, run the source-checkout command:

```text
python tools/ddo.py bootstrap --profile core
python tools/ddo.py doctor
```

The bootstrap uses `.python-version` (`3.11.16`), `.node-version` (`22.14.0`),
and the locked `uv` version (`0.11.26`). It stores the virtual environment,
uv-managed Python, uv cache, and npm cache under the repository. It fails
closed when the exact pinned interpreter is unavailable; it does not silently
substitute another Python version.

After bootstrap, the equivalent installed entry point is:

```text
uv run ddo --help
```

Optional provider profiles are explicit and independent: `matching`,
`profiling`, `entity_resolution`, and `ml`. Heavy providers are not required
for the core API workflow.

## Daily workflow

```text
python tools/ddo.py check --tests
python tools/ddo.py demo
python tools/ddo.py dev serve
python tools/ddo.py frontend openapi
python tools/ddo.py frontend diagnose
```

`demo` exercises the existing FastAPI control-plane boundary: health,
configuration, run creation, and run read. It uses only project-local state and
does not modify an upstream system. The full product server remains available
through the accepted Step29 entrypoint when the relevant API/files/profiling
profile is installed. `frontend openapi` generates
both files into a project-local temporary directory and compares them with the
tracked contract without modifying tracked frontend files. `frontend diagnose`
uses a bounded npm dry-run; use `--install` only when dependency installation
is intended.

## Quality boundary

Focused tests and repository validators are the source of truth. Do not turn
static reports, historical receipts, synthetic evidence, or a local-only demo
into a browser, CI, physical, live-provider, or public-release claim. The
protected `tests/quality_unit_artifacts/` tree is not part of the developer
workflow and must remain untouched.
