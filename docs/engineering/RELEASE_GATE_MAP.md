# Release Gate Map

Current authoritative snapshot: G0 through G15 are `PASS`. The complete
canonical gate contract, evidence classes, ownership and after-step mapping
remain in `specs/gate_map.yml`.

The snapshot is not a rewrite of historical reports. A historical report may
say a later gate was pending because it predates that gate's closure.

G15 does not certify universal production readiness. The current bounded
release, adapter/UI distinction, Oracle deferral, uncalibrated-score policy,
and local-only operational/performance limits are recorded in
[`docs/release/CLAIM_EVIDENCE_MATRIX.md`](../release/CLAIM_EVIDENCE_MATRIX.md)
and [`docs/release/LIMITATIONS.md`](../release/LIMITATIONS.md).

The major ordering constraints are mandatory: evidence producers before fusion; evaluation before canonical finalization; canonical before OLAP; correctness before performance; AppSec before Red Team; Technical Writer last.
