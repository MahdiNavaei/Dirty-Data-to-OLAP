# Specialist Step17 — Evidence Fusion Engineer

Status: PASS for the bounded project-owned evidence-fusion implementation.

Starting HEAD: `1d3a49a59cfd43cc9a1921a8df68144f682c9e5d`.

Content commits: `4223437d0c46c18cf72e7a9aecb827df5603e693` (`feat: implement provenance-aware evidence fusion`) and `9f70d67250bb0f7a813caf2647355d48f9a5b4a3` (`fix: close evidence fusion handoff validation`).

Metadata commit: `1f3a90cb41e709d7459c2c3f6714a4a6f5be5783` (`docs: record evidence fusion handoff`).

## Scope and upstream hardening

Required governance, product/domain, architecture, engineering, Step17 and
Step18 handoff material was reviewed. Step16 generation provenance now records
the exact temperature, seed, token bound, character bound separation,
timeout, retry count, stream/thinking modes and structured schema identity.
Safety reference-rate denominator and transport-failure semantics were
corrected; capability output distinguishes unavailable from available-but-
invalid. Semantic artifact tests use project-owned `workspace/test-temp`.
Step15 benchmark labels are optional training inputs, not normal inference
inputs. The runtime DAG remains free of an ER-before-canonical cycle.

## Fusion behavior

The service consumes typed Step08–16 result boundaries plus explicit declared
constraints and domain assertions. It builds directional relationship and
symmetric mapping bundles, preserves lineage/correlation groups, prevents
candidate/ML/LLM double counting, distinguishes missing from zero, enforces
required producer completeness and conditional Schema Matching, and emits
first-class conflicts. Repairs are forwarded by reference only.

Policy is `relationship-fusion-v1` / `mapping-fusion-v1`, version `1.0`,
`UNCALIBRATED`, automation-disabled and G5-gated. Decisions remain
`REVIEW_REQUIRED`; scores are not probabilities. Benchmark cases and mapping
case descriptions are stored without runtime truth leakage under
`benchmarks/evidence_fusion/`.

## Verification and limitations

Focused Step17 tests: `34 passed`; actual DependencyResult integration,
artifact hashing, privacy canary, architecture boundary checks and the fusion
validator passed. Full regression: `158 passed, 2 skipped`; compileall,
diff-check and all repository validators passed for the final receipt.
Optional provider availability remains environment-dependent; Step18
evaluation, calibration, canonical modeling, review UI, repair execution and
release claims remain future work.

Handoff: `Step18 — ML Evaluation Engineer`. Do not begin Step18 in this
execution.
