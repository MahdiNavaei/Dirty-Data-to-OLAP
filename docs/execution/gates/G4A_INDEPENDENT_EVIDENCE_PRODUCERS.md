# G4A — Independent Evidence Producers

Decision: PASS as an intermediate Step14 milestone. G4 remains formally
PENDING; G5–G15 remain PENDING.

Step14 exercised an independent probabilistic evidence producer through
`EntityResolutionService -> SplinkEntityResolutionAdapter -> official Splink
4.0.17`. The adapter performed bounded blocking, random-u estimation, EM
m-training, prediction and threshold clustering. It retained no Splink native
objects in project contracts and did not fuse Step13 schema scores into ER
weights or probabilities.

Evidence covered:

- source and snapshot identity, complete staged-batch hashes and record
  references;
- exact local-only authorization bound to policy ID/version, entity family,
  spec ID/fingerprint, source/snapshot/table/identity-column and batch scope;
- model provenance separating match weight from model-implied probability;
- candidate-pair and all-pair diagnostics, blocking-rule counts and hard
  execution budgets;
- independent field-agreement references, placeholder/null protection,
  transitive bridge and cluster-size diagnostics;
- raw-value privacy canary and private DuckDB cleanup;
- benchmark labels kept separately under `benchmarks/entity_resolution/`.

This milestone does not establish calibrated business confidence, canonical
identity, survivorship, deletion, or formal G4 intelligence acceptance.
