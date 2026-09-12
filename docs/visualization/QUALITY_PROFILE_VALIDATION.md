# Quality, profile, and validation views

Quality cells require numerator and denominator for measured states and always carry observation scope, reliability, evidence state, severity, accessible text, and provenance. Unavailable, privacy-blocked, not-evaluated, or not-applicable states may omit counts but must remain explicit.

Profile charts are only emitted for observed, transferable evidence. Unordered categorical values can use bars or a table. Histograms and box plots require ordered numeric observations. Privacy-blocked or restricted profiles produce `NONE` with no points. The view retains sample/full scope and reliability.

Validation view status is computed from visible checks. Any failure yields `FAIL`; required review or not-evaluated checks prevent a green status. G6 eligibility is true only when the derived overall status is `PASS` and required checks pass. Expected, observed, discrepancy, evidence, and provenance references remain inspectable.
