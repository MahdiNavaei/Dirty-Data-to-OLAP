# Configuration

The current configuration surface is the `ddo` CLI and the product's typed
API configuration. Use `python tools/ddo.py config` to print safe paths,
profiles, versions, and product entrypoints; it does not print credentials.

## Project-local paths

- `.venv/` — project environment.
- `.ddo/cache/uv` and `.ddo/cache/npm` — disposable dependency caches.
- `.ddo/demo` — default Step40 demo state.
- `.ddo/tmp` — OpenAPI/generated-file candidates.

The demo accepts a custom state root only when it resolves beneath
`<repository>/.ddo`. External absolute paths, traversal, symlink escapes,
invalid path types, and pre-existing non-directories fail closed before backend
construction or writes.

## Profiles

The CLI exposes `core`, `api`, `matching`, `profiling`, `entity_resolution`,
and `ml` profiles. Optional provider profiles are not implied by the core
profile. The matching/profiling uv conflict is declared in `pyproject.toml`.

## Authentication configuration

The API supports the documented `local_test` principal-header integration mode
and `trusted_proxy` injected-principal mode. These are local/reference
boundaries, not password, JWT, credential-store, or production IAM
configuration.
