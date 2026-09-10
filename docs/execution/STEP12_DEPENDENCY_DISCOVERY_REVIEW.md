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

## Post-Step12 Independent Integrity Closure

The original Step12 `PASS` required correction. Its recorded content SHA was
`0b6e303f220f85d0e5c756039c8f0e88d670ba27`, which is not a Git commit in this
repository; the actual original content commit is
`0b6e3032183c09296b2ba7c0e3c4cd36545ca73b`. The production adapter could not execute a mature
provider on this Windows host, and the first implementation had integrity
defects in UCC/key separation, stable identifiers, configuration fingerprinting,
privacy authorization, null/provider semantics, approximate metrics, orphan
provenance, relationship gating, and search completeness.

The surgical closure repaired those defects. The adapter now requires a
policy-issued exact-scope `DependencyAuthorization`, uses stable table and
column IDs, separates `UniqueColumnCombinationEvidence` from derived
`KeyCandidate`, records native metric availability without fabrication, keeps
orphan references aligned, gates relationship candidates on observed target
uniqueness/type/low-cardinality evidence, and reports independent UCC/FD/IND
arity and pruning bounds. On Windows it invokes the externally/local provisioned
Desbordante image through a network-disabled, read-only Docker process with a
real subprocess timeout. Containerization does not remove AGPL obligations;
image distribution authorization remains a project-policy limitation.

Executed closure evidence:

- `python -m pytest tests/unit/test_dependency_discovery.py -q --disable-warnings` -> `10 passed`.
- `python -m pytest tests/integration/dependencies/test_step12_real_provider.py -q --disable-warnings` -> `1 passed`; actual chain was `DependencyDiscoveryService -> DesbordanteDependencyAdapter -> desbordante-docker`, producing UCC, FD and IND contracts.
- `python -m pytest -q --disable-warnings --maxfail=1` -> `107 passed, 41 warnings`; the existing dlt/SQLAlchemy cursor-finalizer traceback occurs after successful completion and is non-failing.
- `python -m compileall -q src tools tests` -> PASS; `git diff --check` -> PASS.
- Validators -> domain docs, data architecture, solution architecture, engineering post-gate, source ingestion, profiling, data quality, privacy, database security and dependency discovery all PASS. Dependency validator: `21` behavioral checks; engineering post-gate: `73` checks and `23/23` negative tests; solution architecture: `10/10` dependency negative tests.
- Published artifact inspection -> UCC evidence, derived key reference, FD, IND, runtime/null provenance, rejected relationship cases and aggregate-only JSON verified; raw fixture values absent.

The original content commit remains `0b6e3032183c09296b2ba7c0e3c4cd36545ca73b`. The integrity-closure source commit is `b93a502a5fa07d2667f1f61d2ec7f9bba6f3a0f1`; the metadata follow-up records it separately while preserving the original content SHA. Step13 implementation was not started; G4/G4A and later gates remain pending.
