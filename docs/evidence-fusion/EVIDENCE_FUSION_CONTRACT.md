# Evidence Fusion Contract

Step17 consumes typed project-owned producer results and aggregate
`FusionEvidenceItem` observations. It emits `EvidenceFusionResult` containing
`RelationshipDecision`, `SemanticMappingDecision`, `Conflict`, signal and
bundle contracts. It does not read source/staged data, create `ReviewDecision`,
assign canonical identity, or execute repairs.

Every inferred decision is `REVIEW_REQUIRED`. Numeric values are
`UNCALIBRATED_DECISION_SCORE`, never probabilities.

`EvidenceFusionInputs` accepts collections of the actual project-owned
`ProfileResult`, `QualityResult`, `DependencyResult`, `SchemaMatchResult`,
`AppliedMLResult` and semantic-result contracts. Producer status retains each
result identity and state; expected result IDs can fail stale or missing
replays. Repairs are forwarded by proposal ID only.

Evidence uses `snapshot_by_source` for source-local scope. A cross-source
mapping therefore may contain different valid snapshots per source. Declared
catalog metadata uses the explicit `NOT_APPLICABLE_SCHEMA_METADATA` binding
and never invents an observed snapshot. Missing, unavailable, skipped,
privacy-blocked and failed evidence remains visible in the subject-local
bundle and is not converted to zero.

Semantic and learned results require explicit candidate-to-fusion-subject
binding. Directional relationship subjects and symmetric mapping subjects are
kept separate; a mapping policy cannot score a relationship candidate.
