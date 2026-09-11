# Entity Resolution Contract

Step14 implements bounded probabilistic linkage as an evidence producer. The
runtime accepts a project-owned `EntityResolutionSpec`, catalog and pinned
`SourceSnapshotResult`/`BatchReference`/`SourceRecordReference` inputs,
`IdentityFieldSpecification`, and an exact policy-issued
`EntityResolutionAuthorization`. Optional evidence references are descriptive
inputs only.

The runtime stage remains `ENTITY_RESOLUTION` after `CANONICAL_HYPOTHESES`.
Step14 does not produce the `EntityResolutionSpec`; that producer remains
owned by Step19. Step14 emits `EntityMatchEdge`, `EntityCluster`, the normalized
`EntityResolutionResult` envelope, diagnostics, capability and failure evidence.
The result is consumed by the Step19 identity-proposal preparation boundary. It
never emits a canonical entity ID, survivorship value, accepted merge, or
`SourceRecordCanonicalMap`.

Splink is isolated behind `SplinkEntityResolutionAdapter`. Raw identity values
are read only from complete, hash-verified project staging into private local
memory/DuckDB. Normalized contract outputs contain record references and
aggregate evidence only. Private work is removed in `finally`.

The implementation supports `LINK_ONLY`, `DEDUPE_ONLY` and
`LINK_AND_DEDUPE`; explicit versioned blocking rules are bounded before
prediction. No Cartesian fallback is permitted. `match_weight` remains a
log-Bayes-factor-like model weight, while `match_probability` remains the
model-implied pair probability under the configured linkage assumptions; it is
not a calibrated business confidence.

Normalization is conservative: NFKC, casefolding and whitespace handling are
allowed. Transliteration and country/phonetic guessing are forbidden. Phone
digit normalization requires an explicit country/context policy. Null and
placeholder values cannot supply positive evidence.
