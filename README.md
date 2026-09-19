# Dirty Data to OLAP

Dirty Data to OLAP turns fragmented, dirty tabular data into an evidence-backed, validated OLAP-ready analytical layer.

Developer quickstart: run `python tools/ddo.py bootstrap --profile core`, then
`python tools/ddo.py doctor`, `python tools/ddo.py demo`, and
`python tools/ddo.py check --tests`. The canonical cross-platform interface is
the same `ddo` command after bootstrap (`uv run ddo ...`); see
[CONTRIBUTING.md](CONTRIBUTING.md) and the [developer workflow](docs/development/DEVELOPER_WORKFLOW.md).

The Step 05 architecture and engineering baseline is complete.

Current status: Steps 01-21 are complete for their bounded specialist scopes; corrected Step18 v5 empirical evaluation is formally G5 PASS in `REVIEW_ONLY_VALIDATED` mode, Step19 provides the review-gated canonical model boundary, Step20 provides a review-gated, materializable DuckDB star-schema package, and Step21 provides a generic, review-bound semantic projection with bounded read-only query plans. Step21 does not change Step20 grain or aggregation classes, invent revenue, or certify G6. The critical post-Step19 integrity repairs are complete: evidence reviews bind to each concrete relationship/mapping decision, identity memberships are proposal-owned, every ER_REQUIRED family must have a compatible COMPLETE ER result even for HUMAN_DOMAIN_REVIEW, authorized ER membership edges must form one connected component, and ER_NOT_REQUIRED events are finalized explicitly. The earlier v4 PASS is historical and superseded by the v5 ER metric/cluster-policy repair. G0/G1/G2/G3 PASS; G4/G5 PASS; G3A/G3B/G4A PASS; G6-G15 remain PENDING. Step15 remains experimental and uncalibrated. Step16 provides optional, local-only Ollama semantic evidence, Step17 provides provenance-aware Evidence Fusion with first-class conflicts and explicitly UNCALIBRATED scores, Step18 binds actual provider outputs, Step19 preserves reviewed identity, mappings, survivorship, conflicts and source-record accounting, and Step20 preserves those canonical references while assigning separate deterministic warehouse keys. No automatic acceptance, repair execution, source write, revenue inference, or automation authorization is performed.

Next: Step22 Data QA Engineer owns independent source-to-canonical-to-OLAP reconciliation. G6 remains pending and no automation threshold has been selected; automation remains not authorized.

The latest offline continuation verified Valentine `1.0.0` and Splink `4.0.17` from the project-local wheelhouse and local NLTK resources. Coma and Cupid each completed all 11 schema groups; joint Schema Fusion used the combined result with the real 22/22/22 producer estate; Splink trained with phone comparison and complementary EM rules over 24 records. The corrected v5 report evaluates 10 TEST records as 45 pairs (8 positive, 37 negative), uses complete predicted partitions, and enforces final project edge bands. G5 is `PASS` / `REVIEW_ONLY_VALIDATED`, remains review-only with no selected threshold. Step20 evidence is under `workspace/runs/step20-reference-run/olap/`; Step21 semantic evidence is under `workspace/runs/step21-reference-run/semantic/` and `workspace/runs/step21-generic-reference-run/semantic/`, all synthetic/domain-reviewed and local-only.

- Project documentation: [docs/00_README.md](docs/00_README.md)
- Execution state: [docs/execution/MASTER_EXECUTION_STATE.yml](docs/execution/MASTER_EXECUTION_STATE.yml)
