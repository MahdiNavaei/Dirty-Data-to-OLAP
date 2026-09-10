# Evidence Fusion Contract

Step17 consumes typed project-owned producer results and aggregate
`FusionEvidenceItem` observations. It emits `EvidenceFusionResult` containing
`RelationshipDecision`, `SemanticMappingDecision`, `Conflict`, signal and
bundle contracts. It does not read source/staged data, create `ReviewDecision`,
assign canonical identity, or execute repairs.

Every inferred decision is `REVIEW_REQUIRED`. Numeric values are
`UNCALIBRATED_DECISION_SCORE`, never probabilities.
