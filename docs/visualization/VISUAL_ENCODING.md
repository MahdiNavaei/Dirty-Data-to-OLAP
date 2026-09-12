# Visual encoding and accessibility

The machine-readable contract is `specs/visualization_encoding.yml`.

Shapes identify node types. Line styles distinguish declared, accepted, or authorized linkage evidence (solid), inferred or lineage edges (dashed), and candidate/provisional edges (dotted). ER candidate and authorized linkage descriptions remain distinct, and both explicitly state that linkage evidence is not canonical identity. Every node and edge also carries state, evidence state, reliability, observation scope, review state, conflict state where applicable, and provenance. A legend and an accessible description explain these fields; no meaning depends on color alone.

Raw scores retain metric names and metric semantics. They are not probability, posterior, or likelihood unless a calibrated contract explicitly says so. Sampled evidence retains `SAMPLED_SCOPE`; a sample is never described as full. Human assertions have an explicit role and are not model evidence.

Failure, `NOT_EVALUATED`, unavailable, stale, invalidated, and privacy-blocked states remain visible. Exploratory validation subsets are explicitly non-authoritative; authoritative validation status is projected only from a hash-bound `ValidationReport`, so a failure cannot be laundered into a green view. Authoritative measures are projected only from a reviewed `MeasureSpec` and `AnalyticalPlan`; non-additive measures cannot be represented with default `SUM` semantics.
