# Developer workflow

This guide is the focused Step40 developer-experience contract. The canonical
implementation is `src/dirty_data_to_olap/devx.py`; `tools/ddo.py` is the
pre-install source-checkout shim and the `ddo` project script is the installed
form.

## Commands

| Command | Behavior |
| --- | --- |
| `python tools/ddo.py version` | Prints package and pinned tool versions. |
| `python tools/ddo.py config` | Prints safe paths, profiles, and product entrypoints. |
| `python tools/ddo.py doctor` | Checks exact Python, Node, npm, uv, and repository prerequisites. |
| `python tools/ddo.py bootstrap --profile core` | Creates the project-local environment and locked frontend dependencies. |
| `python tools/ddo.py demo` | Runs a deterministic real-product boundary smoke. |
| `python tools/ddo.py check --tests` | Runs tracked-file compile checks and focused DX tests. |
| `python tools/ddo.py frontend openapi` | Verifies OpenAPI and generated TypeScript deterministically. |
| `python tools/ddo.py frontend diagnose` | Runs bounded npm diagnostics without installation by default. |
| `python tools/ddo.py dev serve` | Starts the existing Step29 local API entrypoint. |

All disposable state is project-local: `.venv/`, `.ddo/`, and ignored frontend
dependencies. No global virtualenv, global npm cache, or user home state is a
runtime dependency. On Windows, use the same commands in PowerShell; on Linux
or CI, use the same commands in a POSIX shell.

## Reproducible API contract

`frontend/scripts/generate-api.mjs` accepts only an interpreter whose version
matches `.python-version`. It prefers the project-local `.venv` and otherwise
checks available Python commands. Generation is bounded to six minutes and
fails with an actionable message rather than using an unpinned interpreter.

The verification command writes candidate JSON and TypeScript files beneath
`.ddo/tmp/`, compares bytes and SHA-256 values with the tracked outputs, and
leaves the frontend working tree unchanged.

## Limitations

`doctor` is intentionally a real environment check. A missing exact pinned
interpreter, Node/npm, or uv is a failed prerequisite, not a warning. Optional
provider profiles may remain unavailable until explicitly bootstrapped. A
successful local demo proves the local application boundary only; it does not
prove CI, deployment, production authentication, or external data access.
