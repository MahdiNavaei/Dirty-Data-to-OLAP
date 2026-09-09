# Release Gate Map

G0 and G1 are PASS. Step 05 owns G2 and has passed it after the pre-gate readiness audit. G3–G15 remain PENDING and are not implied by the engineering plan. The complete gate contract, evidence and ordering constraints are in `specs/gate_map.yml`.

The major ordering constraints are mandatory: evidence producers before fusion; evaluation before canonical finalization; canonical before OLAP; correctness before performance; AppSec before Red Team; Technical Writer last.
