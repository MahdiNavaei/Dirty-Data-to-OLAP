# Measurement and Severity

Measurement semantics are explicit:

- `EXACT_ON_FULL_SNAPSHOT_SCOPE` means every available staged row in the full
  snapshot was measured.
- `EXACT_ON_OBSERVED_SCOPE` means the observed staged scope was measured but the
  source or table is bounded/partial.
- `SAMPLE_OBSERVATION` means only the pinned deterministic sample was measured.
- `INCONCLUSIVE` and `UNMEASURED` mean no defect-free conclusion is available.

Affected ratios use the rule's evaluated denominator. Requiredness evaluates
all rows; value validity excludes physical nulls and explicitly configured
missing markers. Uniqueness excludes missing key tuples. Pattern, domain, type,
range and normalization rules exclude missing values. Counts always include an
explicit scope and denominator meaning.

Severity (`INFO` through `CRITICAL`) expresses impact policy. Detection basis
expresses how the observation was obtained (`EXACT_MEASUREMENT`, sampled,
declared-constraint contradiction, asserted domain, user rule or inconclusive).
These are separate fields and cannot be collapsed into a score.

Incomplete profiles, missing prerequisites, invalid paths/hashes, detector
errors and partial FK target coverage remain visible in `QualityFailure` or an
inconclusive dimension. No absence of an issue means that unmeasured data is
clean.
