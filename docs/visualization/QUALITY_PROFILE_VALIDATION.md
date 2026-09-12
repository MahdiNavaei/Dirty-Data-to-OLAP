# Quality, profile, and validation views

Quality cells require numerator and denominator for measured states and always carry observation scope, reliability, evidence state, severity, accessible text, and provenance. Unavailable, privacy-blocked, not-evaluated, or not-applicable states may omit counts but must remain explicit.

Profile charts are only emitted for observed, transferable evidence. Unordered categorical values can use bars or a table. Histograms and box plots require ordered numeric observations. Privacy-blocked or restricted profiles produce `NONE` with no points. The view retains sample/full scope and reliability.

Exploratory validation subsets retain visible check status and references but are explicitly non-authoritative and cannot claim global `PASS` or G6 eligibility. The authoritative view is a complete, content-hash-bound projection of `ValidationReport`; it preserves the report's policy, check universe, statuses, and G6 fields without re-deriving them. Expected, observed, discrepancy, evidence, and provenance references remain inspectable.
