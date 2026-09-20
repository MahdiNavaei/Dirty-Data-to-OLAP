# Current claim / evidence matrix

This matrix is the release routing table. “Evidence” names the strongest
current evidence actually used; it does not broaden the claim beyond that
evidence.

| Claim | Current statement | Evidence | Boundary |
|---|---|---|---|
| Product path | Managed CSV import through reviewed, durable, validated OLAP output | Step29 G7 receipt and browser/system evidence | Local/reference path; not deployment or HA |
| Developer demo | Health, configuration, run creation, and run read through the real API boundary | Step40 review, focused tests, exact-head CI | Control-plane smoke; not full OLAP |
| G5 inference | Review-only validated evidence | Step18 corrected v5 report and state | Scores uncalibrated; no threshold or automation authorization |
| Canonical identity | Review-gated, provenance-preserving identity membership | Step19 receipts, G6 evidence | ER cluster is not automatic canonical truth |
| OLAP model | Typed fact/dimension/grain/measure/materialization plan | Step20/21/22 receipts | No invented revenue; DuckDB/reference target |
| G6 correctness | Typed source-to-canonical-to-OLAP accounting and reconciliation gate | Step22 current receipt/state | Scoped reference evidence; not a universal lossless claim |
| Source support | CSV UI path; additional adapters as separately tested | Step32 matrix and CI run `35034150663` | UI support is narrower; Oracle deferred |
| API | Versioned bounded FastAPI `/api/v1` with generated OpenAPI | `docs/api/README.md`, tracked OpenAPI, Step27/29 evidence | Local auth integration modes, not production identity |
| Security | Bounded local threat model and tested controls | Step33/G10 and Step39/G13 receipts | No universal security or live deployment IAM claim |
| Resilience | Local fault/recovery controls and bounded durable semantics | Step36/G11 receipt | No HA, exactly-once, or disaster-recovery guarantee |
| Performance | Local reference measurements and bounded load evidence | Step37/38/G12 reports | No unmeasured production scale or SLA |
| Reproducibility | Locked build, clean-room and CI evidence | Step30/G8 receipt and exact-head CI | Host may lack exact pinned runtime |
| Usability | Verified setup, demo, Windows workflow, and current docs | Step40/G14 receipt plus Step41 docs evidence | Usability PASS is not production certification |

## Prohibited upgrades

This release must not turn an uncalibrated score into a probability, an ER
cluster into canonical truth, a UI adapter matrix into UI support, a local
benchmark into a production SLA, an image scan with `--ignore-unfixed` into a
zero-vulnerability statement, or a G15 PASS into universal readiness.
