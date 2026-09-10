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

## Final Step17 Scoring Integrity Closure

This is a second surgical Step17 closure. The JSON policies remain runtime
authoritative and the human YAML mirrors now include normalization metrics,
dimension bindings, conflict eligibility and required-producer declarations.
Every score dimension is checked against its normalization rule; relationship
and mapping policies remain separate.

Optional declared-FK evidence is denominator-inclusive only when observed.
Incompatible type compatibility uses the explicit signed compatibility
normalization and therefore contributes a nonzero negative value. Coma and
Cupid matcher ranks are separate optional dimensions; native matcher scores
are never averaged, and matcher order does not change the decision identity.

The real SchemaMatchResult path is covered by a valid contract integration and
reaches a review-ready mapping with matcher, type and qualitative signals.
Profile tables, quality issues and repair proposals bind to exact source,
snapshot, table and column topology; substring lookalikes do not attach or
forward repair references. Conflict rules are metric/family eligible, so
unrelated quality signals cannot create declared-data or semantic-structural
conflicts.

Expected producer identities enforce family, producer, result and snapshot
scope. Conflicting duplicate fingerprints become explicit stale evidence;
NOT_CONFIGURED remains distinct from UNAVAILABLE. Semantic evidence must bind
to exactly one fusion subject, and multi-subject bindings fail closed.

The executable benchmark remains runtime-input-only and separate from
expected controls, covering A-O and M1-M8, including clean/relevant declared
FK cases and matcher/ML disagreement visibility. The fusion validator now
reports 21 behavioral checks. The final repository verification recorded unit
`102 passed`, contract `7 passed`, integration `33 passed, 2 optional skips`,
architecture `9 passed`, security `24 passed`, full regression `175 passed,
2 optional skips, 41 warnings`, compileall PASS, diff-check PASS, all
validators PASS and engineering post-gate `73` checks PASS.

Step18 implementation, calibration, G5 inference-validity work, canonical
modeling, ReviewDecision generation and repair execution were not started.
G4 and G4A remain PASS; G5-G15 remain PENDING. A known dlt/SQLite cursor
cleanup traceback is emitted after successful source integration/full-suite
exit; it does not change the zero exit status or test result.

## Post-Step17 Evidence Fusion Integrity Closure

This is a surgical Step17 repair only. Step18 implementation, calibration,
G5 inference-validity work, canonical modeling, ReviewDecision generation and
repair execution were not started.

The closure makes the JSON policy artifacts runtime-authoritative and binds
typed normalization, score dimensions, band policy, conflict rules, required
producer families and automation-disabled/G5-gated status into every request.
Relationship and mapping policies remain separate. Inclusion coverage and
orphan ratio share one score dimension; declared metadata is not a data
observation; qualitative semantic/domain/ML evidence is retained without
numeric votes.

Actual producer-result collections are consumed for profiling, quality,
dependency, schema matching, applied ML and semantic evidence. Candidate-local
subject binding, repair-proposal forwarding, multiple result identities,
expected-result stale detection, source-local snapshot maps, explicit
not-applicable catalog metadata, non-observed evidence, bundle missingness and
material replay fingerprints are enforced. Bounds produce explicit discarded
input failures. Conflict families remain distinct and all outputs are
review-only.

The executable benchmark runs runtime inputs separately from expected
controls, covering A-O and M1-M8. The validator uses behavioral checks for
policy status, score dimensions, replay identity, scope/missingness,
review-only behavior, G4/G5 state and Step18 absence. Final executed counts
were focused fusion `20 passed`, full regression `169 passed, 2 optional
skips, 41 warnings`, all repository validators PASS, compileall PASS and
diff-check PASS. Commit identities are recorded in the specialist log and
repository state.
