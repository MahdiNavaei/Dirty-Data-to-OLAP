# Step16 Handoff

Step15 is complete as an optional, experimental learned-evidence branch.
Step16 may consume LearnedRankingEvidence as one bounded input alongside
semantic evidence. Step16 must not reinterpret the score as truth or
probability, and must preserve model/schema/dataset/split provenance,
missingness, risk flags, and candidate-only semantics.

The next specialist owns semantic evidence and optional provider policy.
Step15 does not implement LLM calls, semantic fusion, canonicalization, or
final relationship decisions.
