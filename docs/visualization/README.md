# Step26 visualization architecture

Step26 owns the semantic visualization boundary between the evidence-first domain and a future product surface. The boundary is implemented by `VisualizationService` and the contracts in `domain/contracts/visualization.py`.

The service emits bounded, deterministic, project-owned view models. It never passes raw domain objects to a browser, executes SQL, changes a review decision, turns a raw score into a probability, or decides that a cluster is a canonical identity. A renderer may consume these view models later; the renderer is not part of Step26.

## Supported views

- source/schema and table-column relationship graphs, preserving declared versus inferred relationships;
- bounded neighborhood and focused lineage paths with explicit direction;
- evidence-family breakdowns with separate supporting, contradicting, human-assertion, and conflict fields;
- schema-mapping, entity-resolution, canonical, and OLAP graphs through the same typed graph projection;
- quality heatmaps with numerator, denominator, scope, reliability, state, and provenance;
- profile distributions only when the profile contract justifies the chart type and transfer;
- validation views that derive overall status and G6 eligibility from visible checks.

## Boundaries and handoff

Graph IDs are stable hashes of the visualization ID and domain reference. Layout hints are presentation metadata and are never identity. Every rendered node has an accessible row; hidden and aggregated counts are reconciled and exposed. Colors are optional decoration, never the only encoding.

Step25 remains the owner of workflow semantics and interaction states. Step27 owns API/backend delivery. Step28 owns orchestration, and Step29 owns the frontend shell. Step26 does not implement any of those surfaces.

The representative benchmark is synthetic and local. It demonstrates bounded projection and deterministic progressive disclosure only; it is not a browser FPS, production capacity, or user-research claim.
