# Visualization architecture

## Projection boundary

`VisualizationGraphInput` is a deliberately narrow, already-normalized input. It contains labels, state, evidence state, reliability, observation scope, reviewability, provenance, and relationship provenance. `VisualizationService` validates closure and uniqueness, applies explicit filters, derives their disclosure, selects a bounded node set, and emits `VisualizationGraph`. Accessible rows form an exact one-to-one closure over rendered nodes.

No raw source row, raw value, SQL string, credential, secret, or third-party renderer object crosses this boundary. A future API may serialize these contracts, but that API is Step27 work.

## Stable identity and determinism

`visual_node_id` is derived from `(visualization_id, domain_ref)` and `visual_edge_id` from `(visualization_id, edge_ref, source_ref, target_ref)`. Domain references are unique within an input. Nodes, edges, lineage references, provenance references, filters, and accessible rows use stable ordering. Coordinates, if added by a renderer, are not identity.

The content hash is derived from the emitted semantic payload, excluding the hash field itself. Re-running the same input therefore produces the same IDs, ordering, and hash.

## Domain view mapping

Source, snapshot, table, view, column, constraint, schema candidate, ER record/cluster, canonical entity/event/attribute, fact/dimension/grain/measure/materialization, quality subject, and validation check are represented by explicit node types. Relationship types carry declared/inferred/candidate/accepted distinctions. An ER cluster is a cluster view, not a canonical identity; canonical nodes are separate.

The same projection supports a source/schema graph and specialized views. Specialized builders add semantic guards for lineage, evidence, profile, quality, validation, and measures; they do not reimplement domain truth. Authoritative validation is projected only from a complete hash-bound `ValidationReport`, and authoritative measures only from a reviewed `MeasureSpec` plus `AnalyticalPlan` context. Exploratory subsets and unbound measure inputs cannot claim those semantics.
