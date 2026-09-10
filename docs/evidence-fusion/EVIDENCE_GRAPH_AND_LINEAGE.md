# Evidence Graph and Lineage

`EvidenceBundle` is a JSON-friendly graph projection. Signals retain the
producer, evidence ID, source/snapshot/scope, correlation group and
`derived_from_refs`. Candidate containers, learned ranking and semantic
hypotheses are visible but do not become independent structural votes.

Relationship subjects are directional; source-column mapping subjects are
symmetric. Evidence ID collisions fail explicitly.

Lineage records preserve the source-local `snapshot_by_source` map and an
explicit snapshot binding. The graph retains multiple producer result IDs,
derived references, quality repair proposal references and non-observed
availability states. Required producer absence is represented as bundle
missingness, while optional absence is represented as unavailable evidence.
Replay fingerprints include the policy content identity, contract version,
candidate competitors, producer states and material signal inputs.
