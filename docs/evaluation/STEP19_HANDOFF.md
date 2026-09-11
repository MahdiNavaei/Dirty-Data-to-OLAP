# Step20 Handoff Boundary

Step19 completed a review-gated `canonical-v1` model path. Step20 may consume the
project-owned `CanonicalModel`, canonical entity types/instances, accepted source
mappings and relationships, `SourceRecordCanonicalMap`, survivorship and conflict
artifacts, null/unknown semantics, record-accounting refs, lineage/provenance and
unresolved canonical ambiguity from:

`workspace/runs/step19-reference-run/canonical/`

The reference flow is synthetic and domain-reviewed. ER is linkage evidence
bound to an exact identity proposal and identity review; `EntityCluster.cluster_id`
is not a canonical ID, and source records are not deleted or overwritten.

Step20 must independently determine fact/dimension roles, explicit grain,
measures, aggregation semantics, analytical surrogate keys and analytical SCD
behavior. It must not equate canonical entities with dimensions, events with
facts, numeric attributes with measures, or source/canonical/ER identifiers with
warehouse keys.

Historical boundary: before Step19, this file recorded that no review decision
or canonical identity existed. That limitation is superseded by the Step19
report; G5 remains review-only PASS and G6 remains PENDING.

## Critical Step19 integrity repair

The post-Step19 audit found that an umbrella evidence review could authorize an
unrelated relationship or mapping, free-form memberships could be supplied after
identity review, ER compatibility was under-checked, and event memberships could
disappear during finalization. These defects are repaired in the current
implementation. `CanonicalIdentityProposal` is now the sole membership input to
finalization; every consumed `RelationshipDecision` and `SemanticMappingDecision`
must bind to its exact accepted review context; required ER binds family, spec,
source/snapshot/table scope, authorized edges and cluster policy; and
`SOURCE_LOCAL_EVENT_IDENTITY` emits explicit event instances and maps.

The repair remains synthetic/reference evidence only. It does not promote G6,
assign warehouse keys, or start Step20.

## Critical Step19 repair 2

The second integrity closure enforces the temporal ER guard. Every
`ER_REQUIRED` family must have a `COMPLETE` result compatible with the exact
declared spec before either an ER-derived membership or a human/domain override
can be proposed or finalized. Human review can change membership interpretation,
but cannot bypass ER execution, and its record refs must remain within the ER
evaluated population. The proposal records the ER semantic hash used by the
identity review.

ER-derived membership edges are now required to form one connected component
covering exactly the proposed records; disconnected components, partial groups,
outside endpoints and extraneous selected edges fail closed. The machine-readable
DAG records a non-primary-step `CANONICAL_IDENTITY_PREPARATION` boundary after
conditional ER output and before `REVIEW_CANONICAL_IDENTITY`.
