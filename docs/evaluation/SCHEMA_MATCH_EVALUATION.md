# Schema Matching Evaluation

Schema evaluation treats matcher output as candidate/ranking evidence, not accepted mapping. It records classification, AP, ranking eligibility, no-match false positives, matcher disagreement, multiple-target ambiguity, renamed columns, multilingual cases, and type/name hard negatives.

The real Valentine 1.0.0 schema-only adapter completed on the bounded synthetic provider fixture and persisted a normalized, hashed result. Provider-native scores remain native ranking scores; they are not probabilities. Coma/Cupid-style matcher behavior is compared as evidence families rather than collapsed into one unexplained score.
