# Evidence Graph and Lineage

`EvidenceBundle` is a JSON-friendly graph projection. Signals retain the
producer, evidence ID, source/snapshot/scope, correlation group and
`derived_from_refs`. Candidate containers, learned ranking and semantic
hypotheses are visible but do not become independent structural votes.

Relationship subjects are directional; source-column mapping subjects are
symmetric. Evidence ID collisions fail explicitly.
