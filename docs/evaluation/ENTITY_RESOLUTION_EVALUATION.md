# Entity Resolution Evaluation

The ER benchmark is an aggregate-safe anonymized-token fixture. The evaluator applies an exact email-or-phone anchor baseline while rejecting placeholder/missing tokens, then joins predictions to truth only for scoring. It reports pairwise and cluster metrics, false merges, false splits, purity, completeness, and transitive-bridge behavior.

The real Splink 4.0.17 adapter executed locally through the project-owned authorization and staged-input boundary. Its normalized receipt is aggregate-safe and hashed. The held-out ER cases in this run produced pairwise precision, recall, and F1 of `1.00` with zero false merges; this is benchmark evidence, not a production identity or canonicalization claim.
