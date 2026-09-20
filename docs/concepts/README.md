# Concepts and trust boundaries

## Evidence before acceptance

Schema matches, dependency candidates, fusion scores, and entity-resolution
clusters are evidence. A high score is not a calibrated probability; a schema
match is not semantic proof; an ER cluster is not canonical identity. G5 is
`REVIEW_ONLY_VALIDATED`, with no selected automation threshold and automation
not authorized.

## Canonical identity

Canonical identity is created only through the reviewed, typed identity
workflow. Source records remain attributable and inspectable. Cluster IDs,
warehouse surrogate keys, source IDs, and canonical IDs are distinct. Human
review cannot bypass an `ER_REQUIRED` guard, and an ER proposal does not become
canonical truth merely because it is connected.

See the [evidence-fusion boundary](../evidence-fusion/REVIEW_BOUNDARY.md) and
the [product acceptance criteria](../product/ACCEPTANCE_CRITERIA.md).

## OLAP and measures

An analytical plan declares a fact grain, dimensions, measures, aggregation
semantics, lineage, and orphan policy. Additive, semi-additive, and
non-additive measure classes are explicit. Quantity may be additive at the
reviewed grain; unit price and discount rate are not blindly summed. No
revenue or GMV value is invented when the source does not provide a reviewed
semantic basis.

## Semantic layer

The semantic query layer is bounded and read-only. It renders only validated,
review-bound structures and does not replace the source-to-canonical-to-OLAP
correctness gate. A semantic query result is not by itself a production
correctness or performance guarantee.

## G6 correctness

G6 covers source-to-canonical and canonical-to-OLAP accounting, intentional
consolidation, grain uniqueness, referential integrity, orphan/quarantine
policy, reconciliation, lineage, and negative controls. “Lossless” is used
only for a scoped accounting result, never as a universal deduplication claim.
