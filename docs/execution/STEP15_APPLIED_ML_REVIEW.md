# Step15 — Applied ML Engineer Review

## Decision

Step15 is complete for the optional, experimental
RELATIONSHIP_CANDIDATE_RANKING learned-evidence branch. The repository is
handed to Step16 — LLM / Semantic AI Engineer. Step16 has not started.

## Implemented boundary

The project now owns feature, grouped-dataset, split, baseline, model
evidence, contribution, rank-stability, calibration-experiment, and
active-learning contracts. The feature builder consumes normalized aggregate
evidence from project-owned profiling, quality, dependency, schema-matching,
metadata, and assertion contracts. Entity resolution is not required.

The deterministic structural baseline runs first and remains available when
the optional scikit-learn 1.7.2 runtime is unavailable or training is
insufficient. The real adapter uses LogisticRegression behind the
LearnedEvidenceAdapter boundary and writes only atomic JSON coefficients,
intercept, schema/model/dataset/split provenance, and experimental status.

## Leakage and safety controls

Labels are supplied separately from features. Base-scenario groups and
reverse-direction logical pairs cannot cross splits. Identifier/source
permutation and label-shuffle negative controls are tested. Missing evidence
is retained explicitly, hard conflicts remain risk flags, active learning is
non-mutating, and the model emits uncalibrated ranking evidence only.

## Verification

- Step15 validator: PASS, 21 checks.
- Existing validators: PASS, including domain/data/solution architecture,
  engineering plan, source ingestion, profiling, quality, privacy, database
  security, dependency discovery, schema matching, and entity resolution.
- Unit tests: 57 passed.
- Contract tests: 5 passed.
- Integration tests: 27 passed, 2 optional-provider skips (Splink and
  Valentine are not installed in this clean environment).
- Architecture tests: 7 passed.
- Security tests: 23 passed.
- Full suite: 118 passed, 2 optional-provider skips.
- compileall and git diff --check: PASS.

## Limitations and gate state

The ML fixture is synthetic aggregate evidence, not production truth. The
score is not a calibrated probability, temporal/production performance is
unverified, and no acceptance or canonical identity decision is emitted.
Formal G4 remains PENDING; G4A remains PASS; G5-G15 remain PENDING.
