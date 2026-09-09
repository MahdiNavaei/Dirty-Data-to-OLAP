# Metrics and Observation Scope

`FULL` means every available row in the supplied staged snapshot was observed.
It does not mean the original source was unbounded. A bounded Step07 source
snapshot remains bounded and records its source table observation status.

`SAMPLE` uses deterministic reservoir sampling (`deterministic_reservoir_v1`)
with a request seed, sample limit, record-reference list and sample identity.
Sample distinctness is an observation, not an exact source-wide uniqueness
claim. Full distinctness is exact only on the full staged scope and remains
bounded by the configured accumulator limit.

Per-column metrics include primitive observations, physical nulls, configured
markers, bounded distinctness, lengths, numeric summaries, parseable datetime
observations, categorical aggregates and anchored value-pattern summaries.
Raw values and category labels are not published.

Source-wide extraction caps are preserved through Step07 table observation
statuses: `NOT_OBSERVED`, `PARTIALLY_OBSERVED` and `FULLY_OBSERVED`. Profiling
does not treat an unobserved table as empty.
