# UX / Product Design Contract — Step 25

This directory is the UX contract for the V1 Dirty Data to OLAP review experience. It describes information architecture, review decisions, uncertainty communication, safe actions, accessibility, and cognitive walkthroughs. It does not implement a frontend, backend, API, visualization, scheduler, or authorization service.

The contract is grounded in the current project-owned models:

- `ReviewDecision` and `ReviewCompatibilityContext` in `src/dirty_data_to_olap/domain/contracts/canonical.py`;
- evidence envelopes and `RelationshipDecision` / `SemanticMappingDecision` in `evidence_fusion.py`;
- quality issues and repair proposals in `quality.py`;
- canonical, analytical, materialization, and validation contracts;
- the four stage-scoped checkpoints in `docs/architecture/specs/review_checkpoints.yml`.

Machine-readable contracts:

- [`specs/review_experience.yml`](specs/review_experience.yml)
- [`specs/interaction_states.yml`](specs/interaction_states.yml)

The executable contract check is [`tools/validate_step25_ux.py`](../../tools/validate_step25_ux.py). These artifacts are design and test contracts only. Step 26 owns visualization implementation; later steps own application delivery and operational controls.
