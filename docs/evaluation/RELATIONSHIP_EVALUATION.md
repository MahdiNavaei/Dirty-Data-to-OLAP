# Relationship Evaluation

Candidate generation is measured before fusion: test truth queries, truth-proposed queries, candidate recall, and missing candidate query IDs are retained. The missing-candidate scenario therefore cannot be hidden by a later ranking score.

Fusion is executed through `EvidenceFusionService` using typed aggregate evidence and the frozen Step17 policy. The evaluation records band counts, conflict/incomplete behavior, classification, AP, and ranking separately. Current held-out relationship results are precision `0.50`, recall `1.00`, F1 `0.667`, AP `0.50`, MRR `0.50`, Recall@3 `1.00`; low-cardinality and type-mismatch false positives remain visible.

Transparent controls include raw structural/decision score, target-uniqueness proxy, type-only behavior, and Step15 baseline artifacts. None is promoted to a product rule.
