# Technical Risk Register

| ID | Risk | Impact | Owner | Mitigation | Gate |
|---|---|---|---|---|---|
| R-01 | Contract drift between product/domain/data/software layers | wrong implementation | Step05 | integration matrix and validators | G2 |
| R-02 | ER output accidentally becomes canonical truth | false merges | Step14/19 | output negative test and review guard | G5/G6 |
| R-03 | Source writes through an adapter | operational damage | Step11 | read-only credentials and write-attempt tests | G3 |
| R-04 | Heuristic scores presented as probability | false confidence | Step18 | calibration semantics and claim audit | G5 |
| R-05 | Ambiguous fact grain | incorrect aggregates | Step20/22 | GrainSpec before compiler/materializer | G6 |
| R-06 | Record loss hidden by output-row counts | unreconciled data | Step22 | boundary accounting contract | G6 |
| R-07 | ControlStore receives raw rows | privacy/storage failure | Step06/10 | persistence boundary tests | G2/G3 |
| R-08 | Vendor API drift | silent result changes | adapter owners | pinned versions and adapter contracts | G4/G9 |
| R-09 | Optional semantic provider outage | blocked runs | Step16 | explicit SKIPPED/BLOCKED semantics | G4 |
| R-10 | Unbounded Python memory | local failure | Step37 | chunking, sampling and stage measurements | G12 |
| R-11 | Stale review decision replay | invalid approval | Step27 | compatibility fingerprint and invalidation | G7 |
| R-12 | Research clone becomes hidden runtime dependency | brittle licensing/runtime | Step12 | deletion test and import allowlist | G4 |
| R-13 | Review UI/API bypasses stage guard | unsafe execution | Step27/29 | state-machine integration tests | G7 |
| R-14 | Performance tuning masks correctness defects | wrong release | Step37/38 | correctness gates precede performance | G12 |
| R-15 | Release docs overstate evidence | misleading users | Step41 | final limitation audit | G15 |
