# Step39 Adversarial Security Attack Plan

Status: `EXECUTED`

## Scope and rules

This is an independent red-team pass over the executable V1 local reference
product. Tests use only `TestClient`, disposable temporary directories, and
synthetic identifiers/canaries. No external host, cloud metadata endpoint,
production database, real credential, or destructive source operation was
used. `tests/quality_unit_artifacts/` was not read.

## Trust boundaries

| Boundary | Adversarial question |
|---|---|
| HTTP transport/auth | Does missing, malformed, or caller-controlled identity bypass the API boundary? |
| Project/run control plane | Can an actor read or mutate another project/run by changing IDs? |
| Reviews | Can stale, replayed, cross-run, or mutated review evidence be accepted? |
| Execution authority | Can request JSON forge an accepted plan, command, or status? |
| Artifacts | Can an artifact ID escape its run, substitute bytes, or become a filesystem path? |
| Sources/connectors | Can filenames, symlinks, URLs, hosts, or credentials escape their policy? |
| Semantic query | Can structural SQL, file functions, DDL/DML, or parameter values escape the reviewed model? |
| Diagnostics/errors | Do malformed or secret-bearing inputs echo raw data, secrets, or tracebacks? |

## Attack sequence

1. Establish unauthenticated and malformed-identity baselines.
2. Create two principals and two runs, then replay direct IDs across the trust boundary.
3. Submit forged execution JSON and replay idempotency keys with changed payloads.
4. Publish synthetic review/artifact state, then attempt stale, mutated, and cross-run replay.
5. Attempt SQL structural injection, DuckDB file-function injection, connector SSRF/host substitution, and credential disclosure.
6. Attempt upload traversal, HTML canary echo, oversize body, symlink escape, and registry owner substitution.
7. Validate the receipt itself with forged/missing scenarios, gate advancement, wrong commit, and Step40-start controls.

## Required evidence

The executable suite is `tests/redteam/test_step39_red_team.py`; receipt
validation is `tools/validate_step39_red_team.py`; receipt output is
`output/step39_red_team_validation.json`.
