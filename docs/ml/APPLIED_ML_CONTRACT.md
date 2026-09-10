# Applied ML Contract

Step15 owns optional learned evidence for RELATIONSHIP_CANDIDATE_RANKING.
The service ranks dependency/schema candidates; it does not accept a
relationship, assign canonical identity, repair source data, or emit a
business probability.

Inputs are project-owned aggregate contracts: RelationshipCandidate,
InclusionDependencyEvidence, SchemaMatchCandidate, SchemaMatchScore,
SchemaMatchSignal, ColumnProfile, and QualityResult. Entity resolution
is not required. Explicit benchmark labels are a separate input plane and
never enter feature construction.

The service always evaluates a deterministic baseline first. The optional
scikit-learn adapter may add an experimental linear ranking score. Missing
evidence is recorded as missing and imputed only according to the versioned
feature schema; missing evidence is never silently treated as positive
evidence. Hard type conflicts, scope limitations, and quality risks remain
visible on the output.

The learned output is UNCALIBRATED_RANKING_SCORE. It is evidence for review,
with contributions, rank stability, and non-mutating active-learning
suggestions. It is not a decision or calibrated confidence.
