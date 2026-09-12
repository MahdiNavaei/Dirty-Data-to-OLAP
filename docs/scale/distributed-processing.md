# Step24 Distributed Data Semantics

Step24 adds a bounded, local-first execution strategy over the existing
validated data contracts. It is an execution overlay, not a new semantic stage
and not a second DAG. `LOCAL_REFERENCE` remains the semantic authority;
`PARTITIONED_LOCAL` is the executable reference used to prove that routing,
reduction and merge preserve meaning.

## Boundary and authorization

`ScaleInputDataset`, `ScalePolicy`, `ScaleDecision`, `PartitionPlan`,
`PartitionResult`, `PartitionMergeManifest` and `ScaleEquivalenceReport` are
project-owned immutable contracts. Partition identity binds run, source,
snapshot, table, dataset, version, schema, operation, strategy, index, seed and
policy. It never uses worker number, wall-clock time, completion order or a
temporary path.

Scale execution may be authorized only by the exact typed Step22
`G6_DATA_CORRECTNESS` receipt. Legacy report IDs and the
`legacy:gate-evidence-unverified` provenance marker are rejected. Step24 does
not recompute, promote or replace G6.

## Routing and reduction

Stable SHA-256 routing supports hash partitions. Explicit lexicographic range
boundaries are available when a range policy is supplied. Comparison work uses
blocking-key routing so records sharing a blocking domain reach the same logical
partition. Pair IDs are unordered and scoped; multiple blocking routes are
deduplicated. Candidate fanout above the configured bound fails closed.

Profile fields proven merge-safe in the reference are row count, null/non-null
counts, min, max and exact `Decimal` sum. Distinct counts, quantiles, top-K and
distribution sketches are not invented by local summation. Dependency validity
is evaluated globally because a dependency can be true in every partition and
false in the union. Ranked candidates require deterministic score-descending,
stable-ID-ascending global ordering.

Sampling uses a stable hash of record identity, seed and policy, with a global
limit applied after deterministic selection. It is therefore independent of
partition count, input order and worker count.

## Bounded execution and failure

The local executor submits at most the configured worker slots at a time. The
coordinator retains manifests, hashes, counts and small aggregate states; it
does not collect a large raw dataset or all candidate pairs as the merge
contract. Partition byte/row limits, memory/temp budgets and skew thresholds
are explicit policy fields. Logical shuffle metrics record routed instances,
replication, candidate pairs and bytes; they are not network-throughput claims.

A missing, failed, stale, foreign, schema-mismatched or conflicting result
cannot satisfy the merge barrier. Identical same-hash re-submission is
idempotent. A failed partition can be re-executed under the same plan and
produces the same semantic result when successful. Successful immutable
partition artifacts remain available after a failure; no partial merged target
is marked consumable.

## Stage classification

Profiling supports an exact bounded aggregate subset. Entity candidate
generation supports block routing and pair deduplication; the ER engine itself
is not reimplemented or claimed distributed. Dependency discovery keeps exact
global validation as a barrier. Schema candidate generation requires complete
coverage and deterministic global ranking. Analytical planning and
materialization remain governed by the reviewed Step20 contracts; this Step24
reference preserves fact grain and warehouse-key identity but does not replace
the Step20 atomic DuckDB materializer. Validation is a global accounting and
reconciliation reduction.

## Capability and reproduction boundary

The reference capability is `distributed.partitioned_local` with
`REFERENCE_TESTED` status. Spark, Ray, Dask and multi-node execution are
`FUTURE_NOT_EXECUTED`. No production row-rate, cluster capacity, HA, network
throughput or large-load claim follows from G7A.

Generated benchmark evidence is ignored under
`workspace/runs/step24-scale-benchmark-run/scale/`. Reproduce it with:

```text
python tools/run_step24_scale_benchmark.py
python tools/validate_step24_distributed_data.py
```

Step24 owns how data work partitions and merges. Step28 still owns how jobs and
stages are scheduled, retried, cancelled and resumed. Step25 UX work is not
part of this slice.
