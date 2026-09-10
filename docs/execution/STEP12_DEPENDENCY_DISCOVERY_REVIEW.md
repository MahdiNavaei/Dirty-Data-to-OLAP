# Specialist Step12 — Dependency Discovery Engineer

## Status

`PASS` for the bounded Step12 implementation and executed local evidence scope. G4/G4A remain pending; this receipt does not authorize Step13 implementation or claim final relationship acceptance.

## Scope and implementation

- Added project-owned UCC/key, FD, AFD, IND, approximate-IND, relationship-candidate, failure, capability, privacy, observation-scope and search-policy contracts.
- Added an integrity-checked staged Parquet reader. The dependency adapter reads only the supplied complete snapshot and never reconnects to a source.
- Added a replaceable Desbordante Python boundary, local ephemeral CSV handoff, atomic aggregate-only dependency artifacts, and an application service.
- Added deterministic unit/contract checks for known UCC/FD/IND evidence, null exclusion, orphan accounting, low-cardinality rejection, candidate-only relationships, bounded search, staged hash failure and cleanup.

## Open-source research

Desbordante 2.4.1 at `b211961f3f272ed8815ef1ffbda90573b11e1116` was inspected for FD/AFD, IND and UCC bindings, tests, package metadata and `COPYING`. The source declares AGPL-3.0-only and the published wheel metadata supports Linux/macOS, not this Windows host. The clone was research-only, no code was copied, and it is removed before final regression.

## Executed provider evidence

The pinned source was compiled in the disposable `dirty-data-to-olap-desbordante-step12` Linux image. A real provider invocation over two CSV tables returned one UCC (`(0,)`), one FD (`(0,) -> (1,)`) and two exact INDs with zero provider error, including the expected orders customer-id to customers customer-id direction. This is engine execution evidence, not a production/platform compatibility claim.

## Limitations

The current Windows host does not import Desbordante, so the host adapter reports `CAPABILITY_UNAVAILABLE` unless an approved compatible runtime is supplied. The provider smoke used a temporary Boost 1.83 compatibility substitution inside the disposable build because the pinned source requests Boost 1.85 while Debian stable exposed 1.83; this does not change repository code and should be resolved by a supported build image before distribution. The adapter does not assign PK/FK truth, perform entity resolution, or change source rows.

## Verification receipt

- `python -m pytest -q --disable-warnings` -> `100 passed, 41 warnings` (the existing dlt/SQLAlchemy cursor-finalizer traceback is emitted after pytest completion and is non-failing).
- `python -m pytest -q tests/unit/test_dependency_discovery.py --disable-warnings` -> `4 passed`.
- `python tools/validate_dependency_discovery.py` -> `PASS: dependency_checks=11`.
- Existing validators -> data architecture, data quality, database security, domain docs, privacy, profiling, solution architecture, source ingestion and engineering post-gate all PASS; engineering post-gate reports `73` checks and `23/23` negative tests, while solution architecture reports every negative-test family PASS.
- `python -m compileall -q src tools tests` and `git diff --check` -> PASS.
