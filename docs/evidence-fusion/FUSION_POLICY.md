# Fusion Policy

The committed policies are `relationship-fusion-v1` and `mapping-fusion-v1`.
The JSON files are the runtime-authoritative policy artifacts; the YAML files
are human-readable mirrors validated for identity and scoring dimensions.
Both policies are version `1.0`, `UNCALIBRATED`, automation-disabled and
require G5 before any future promotion.

Each policy declares typed normalization rules, score dimensions and weights,
band thresholds, conflict rules, required producer families and subject kind.
Bounded [0,1] structural metrics use the declared identity transform; matcher
evidence uses matcher-specific ordinal rank; LLM and learned evidence are
qualitative/derived by default and cannot become numeric votes. Relationship
inclusion coverage and orphan ratio are two observations of one declared
`inclusion` dimension, not independent votes. The relationship policy's
sample/full conflict threshold is declared in the policy artifact.

Policy content is hashed and included in the request and decision identity.
