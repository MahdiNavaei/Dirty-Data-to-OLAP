# Step20 Handoff Boundary

Step19 completed a review-gated `canonical-v1` model path. Step20 may consume the
project-owned `CanonicalModel`, canonical entity types/instances, accepted source
mappings and relationships, `SourceRecordCanonicalMap`, survivorship and conflict
artifacts, null/unknown semantics, record-accounting refs, lineage/provenance and
unresolved canonical ambiguity from:

`workspace/runs/step19-reference-run/canonical/`

The reference flow is synthetic and domain-reviewed. ER is linkage evidence
bound to an exact identity review; `EntityCluster.cluster_id` is not a canonical
ID, and source records are not deleted or overwritten.

Step20 must independently determine fact/dimension roles, explicit grain,
measures, aggregation semantics, analytical surrogate keys and analytical SCD
behavior. It must not equate canonical entities with dimensions, events with
facts, numeric attributes with measures, or source/canonical/ER identifiers with
warehouse keys.

Historical boundary: before Step19, this file recorded that no review decision
or canonical identity existed. That limitation is superseded by the Step19
report; G5 remains review-only PASS and G6 remains PENDING.
