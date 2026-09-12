# Visual encoding and accessibility

The machine-readable contract is `specs/visualization_encoding.yml`.

Shapes identify node types. Line styles distinguish declared or accepted edges, inferred or lineage edges, and candidate/provisional edges. Every node and edge also carries state, evidence state, reliability, observation scope, review state, conflict state where applicable, and provenance. A legend and an accessible description explain these fields; no meaning depends on color alone.

Raw scores retain metric names and metric semantics. They are not probability, posterior, or likelihood unless a calibrated contract explicitly says so. Sampled evidence retains `SAMPLED_SCOPE`; a sample is never described as full. Human assertions have an explicit role and are not model evidence.

Failure, `NOT_EVALUATED`, unavailable, stale, invalidated, and privacy-blocked states remain visible. Validation status is derived from individual checks, and a failure cannot be laundered into a green view. Non-additive measures cannot be represented with default `SUM` semantics.
