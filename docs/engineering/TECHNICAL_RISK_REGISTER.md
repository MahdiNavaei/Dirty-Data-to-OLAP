# Technical Risk Register

| ID | Description | Likelihood | Impact | Detection | Mitigation | Owner | Future step | Gate relevance | Status |
|---|---|---|---|---|---|---|---|---|---|
| R-01 | Contract drift between product/domain/data/software layers | MEDIUM | HIGH | cross-spec validator | integration matrix and synchronized validators | Step05 | 6 | G2 | OPEN |
| R-02 | ER output accidentally becomes canonical truth | MEDIUM | HIGH | ER output negative test | evidence-only ER contract and review guard | Step14/19 | 14/19 | G5/G6 | OPEN |
| R-03 | Source writes through an adapter | LOW | CRITICAL | write-attempt negative test | read-only credentials and policy enforcement | Step11 | 11 | G3 | OPEN |
| R-04 | Heuristic scores presented as probability | MEDIUM | HIGH | claim/calibration audit | explicit score semantics and calibration gate | Step18 | 18 | G5 | OPEN |
| R-05 | Ambiguous fact grain produces incorrect aggregates | MEDIUM | HIGH | GrainSpec uniqueness test | block compilation until grain is validated | Step20/22 | 20/22 | G6 | OPEN |
| R-06 | Record loss hidden by output-row counts | MEDIUM | CRITICAL | boundary accounting reconciliation | one terminal disposition and contributor references | Step22 | 22 | G6 | OPEN |
| R-07 | ControlStore receives raw rows | LOW | HIGH | persistence boundary scan | metadata-only store and negative tests | Step06/10 | 6/10 | G2/G3 | OPEN |
| R-08 | Vendor API drift changes normalized results | HIGH | MEDIUM | adapter contract and compatibility tests | pinned versions and replacement tests | adapter owners | 12/32 | G4/G9 | OPEN |
| R-09 | Optional semantic provider outage blocks deterministic work | MEDIUM | MEDIUM | provider failure fixture | explicit SKIPPED/BLOCKED semantics | Step16 | 16 | G4 | OPEN |
| R-10 | Unbounded Python memory fails large sources | MEDIUM | HIGH | stage memory measurement | chunking, sampling and bounded query policy | Step37 | 37 | G12 | OPEN |
| R-11 | Stale review decision is replayed after subject change | MEDIUM | HIGH | compatibility fingerprint test | invalidate incompatible decisions | Step27 | 27 | G7 | OPEN |
| R-12 | Research clone becomes hidden runtime dependency | LOW | HIGH | deletion/import allowlist test | adapter-only OSS use and clone deletion test | Step12 | 12 | G4 | OPEN |
| R-13 | Review UI/API bypasses stage guard | LOW | CRITICAL | lifecycle integration test | state-machine and authorization enforcement | Step27/29 | 27/29 | G7 | OPEN |
| R-14 | Performance tuning masks correctness defects | MEDIUM | HIGH | gate-order audit | correctness gates precede performance gates | Step37/38 | 37/38 | G12 | OPEN |
| R-15 | Release docs overstate evidence | MEDIUM | HIGH | final limitation audit | bounded claims and Technical Writer review | Step41 | 41 | G15 | OPEN |
