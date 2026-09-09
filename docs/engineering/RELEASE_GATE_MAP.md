# Release Gate Map

G0 Product Contract, G1 Domain Truth and G2 Architecture Ready are PASS. G3 Source Safety, G4 Bounded Intelligence, G5 Inference Validity, G6 Data Correctness, G7 End-to-End Product, G8 Reproducible Build, G9 Functional Support, G10 Application Security, G11 Resilience, G12 Capacity, G13 Adversarial Security, G14 Usability and G15 Release remain PENDING and are not implied by this engineering plan. The complete canonical gate contract, evidence classes, ownership and after-step mapping are in `specs/gate_map.yml`.

The major ordering constraints are mandatory: evidence producers before fusion; evaluation before canonical finalization; canonical before OLAP; correctness before performance; AppSec before Red Team; Technical Writer last.
