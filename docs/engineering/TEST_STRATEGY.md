# Test Strategy

Testing is layered from deterministic repository contracts to a fresh benchmark run. Current Step 05 evidence is TIER0 only: documentation/specification validators and repository-state checks. It is not runtime, browser, live-provider, physical or production evidence.

TIER1 covers unit, contract, architecture, security and lifecycle rules. TIER2 covers controlled local integrations and failure recovery. TIER3 covers deterministic end-to-end reconciliation and evaluation. TIER4 measures scale with hardware and environment recorded. The full stage and interface mapping is in `specs/test_matrix.yml`.

Required negative cases include source-write attempts, raw rows in ControlStore, vendor imports outside adapters, dependency reads of tampered/incomplete staging, unbounded dependency search, high-inclusion tiny-domain relationship traps, ER emitting canonical mappings, stale review decisions, unresolved required records, missing fact grain, unexplained record loss, optional-provider failure and research-clone deletion.
