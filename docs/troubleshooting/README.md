# Troubleshooting

## `doctor` reports a missing exact tool

Install or select Python 3.11.16, Node 22.14.0, npm, and uv 0.11.26, then run
`doctor` again. The check is intentionally fail-closed. Do not make an
unversioned global installation part of the evidence record.

## `bootstrap` cannot resolve an optional provider

The core profile does not install every optional provider. Choose only the
profile needed for the task; matching and profiling have a declared uv
conflict. A skipped optional provider is not evidence that the adapter is
broken or that its capability is live in the current environment.

## `demo` rejects `--state-root`

Use a directory below the repository's `.ddo` directory. The rejection is a
security boundary, not a permission workaround. Do not bypass it with a
symlink or path traversal.

## OpenAPI or generated frontend types differ

Run `python tools/ddo.py frontend openapi`. Candidates are written below
`.ddo/tmp` and compared with tracked outputs. The command refuses a non-pinned
Python interpreter and does not rewrite tracked files automatically.

## A run remains in review

Read the server-owned pending checkpoint and its revision. Submit a rationale
through the API/browser action for that checkpoint. Do not write review rows or
invent a client-side execution plan. Stale revisions must be refreshed.

## Output is not available

Check terminal status, materialization, validation, G6 status/eligibility, and
the bound validation artifact. The UI intentionally withholds output for
failed, cancelled, incomplete, or non-eligible runs.

## Host versus CI

Some provider and OpenAPI checks require the exact pinned runtime or services
available in GitHub Actions. Report a host limitation as a limitation; do not
upgrade a static or partial local result into a CI or production claim.
