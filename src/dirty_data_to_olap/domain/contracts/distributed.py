"""Provider-neutral contracts for bounded, meaning-preserving data scale.

These models describe data work only.  They deliberately do not contain
executor objects, callables, pickle payloads, queue state or scheduler state.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import Field, field_validator, model_validator

from .platform import ResourceBudget
from .source import _SourceModel, stable_digest, stable_id


_HASH_LENGTH = 64


def _content_hash(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.lower().removeprefix("sha256:")
    if len(value) != _HASH_LENGTH or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("content hash must be a SHA-256 hexadecimal digest")
    return value


def _safe_token(value: str, *, name: str) -> str:
    if not value or "\x00" in value or "\\" in value or "/" in value or value in {".", ".."}:
        raise ValueError(f"unsafe {name}")
    return value


class ScaleExecutionMode(str, Enum):
    LOCAL_REFERENCE = "LOCAL_REFERENCE"
    PARTITIONED_LOCAL = "PARTITIONED_LOCAL"
    EXTERNAL_DISTRIBUTED = "EXTERNAL_DISTRIBUTED"


class ScaleFallbackPolicy(str, Enum):
    FALLBACK_TO_LOCAL = "FALLBACK_TO_LOCAL"
    REJECT_UNSUPPORTED = "REJECT_UNSUPPORTED"


class StageScaleSupport(str, Enum):
    PARTITION_SAFE = "PARTITION_SAFE"
    SHUFFLE_REQUIRED = "SHUFFLE_REQUIRED"
    GLOBAL_REDUCTION = "GLOBAL_REDUCTION"
    GLOBAL_CANDIDATE_GENERATION = "GLOBAL_CANDIDATE_GENERATION"
    GLOBAL_BARRIER = "GLOBAL_BARRIER"
    LOCAL_ONLY = "LOCAL_ONLY"
    EXTERNAL_ENGINE_BOUND = "EXTERNAL_ENGINE_BOUND"
    NOT_EVALUATED = "NOT_EVALUATED"


class PartitionStrategy(str, Enum):
    SINGLE_NODE = "SINGLE_NODE"
    HASH = "HASH"
    RANGE = "RANGE"
    BLOCK_KEY = "BLOCK_KEY"
    EXISTING_STAGED_PART = "EXISTING_STAGED_PART"


class PartitionResultStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"


class ScaleExecutionStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    INCOMPLETE = "INCOMPLETE"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class SkewStatus(str, Enum):
    BALANCED = "BALANCED"
    SKEWED = "SKEWED"
    EMPTY = "EMPTY"


class SkewAction(str, Enum):
    ALLOW = "ALLOW"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REJECT = "REJECT"


class EquivalenceStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


class _ScaleModel(_SourceModel):
    """Strict, immutable scale contract base."""


class ScaleInputDescriptor(_ScaleModel):
    input_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    table_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    stage_id: str = Field(min_length=1)
    schema_fingerprint: str = Field(min_length=1)
    content_hash: str
    row_count: int = Field(ge=0)
    estimated_bytes: int | None = Field(default=None, ge=0)
    artifact_refs: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    reference_gate_run_id: str = Field(min_length=1)
    reference_validation_report_artifact_id: str = Field(min_length=1)
    reference_validation_report_run_id: str = Field(min_length=1)
    reference_validation_report_id: str = Field(min_length=1)
    reference_validation_report_hash: str
    provenance_refs: tuple[str, ...] = ()

    _validate_content_hash = field_validator("content_hash", "reference_validation_report_hash")(_content_hash)

    @field_validator("input_id", "run_id", "source_id", "snapshot_id", "table_id", "dataset_id", "dataset_version", "stage_id")
    @classmethod
    def validate_scope_tokens(cls, value: str, info: Any) -> str:
        return _safe_token(value, name=info.field_name)

    @model_validator(mode="after")
    def unique_refs(self) -> "ScaleInputDescriptor":
        if len(set(self.artifact_refs)) != len(self.artifact_refs):
            raise ValueError("input artifact references must be unique")
        return self


class ScaleInputRow(_ScaleModel):
    row_ref: str = Field(min_length=1)
    table_id: str = Field(min_length=1)
    schema_fingerprint: str = Field(min_length=1)
    partition_key: str = Field(min_length=1)
    group_key: str | None = None
    block_key: str | None = None
    blocking_keys: tuple[str, ...] = ()
    measure: Decimal | None = None
    profile_values: Mapping[str, str | int | float | None] = Field(default_factory=dict)
    canonical_entity_id: str | None = None
    source_record_refs: tuple[str, ...] = ()
    fact_grain_key: str | None = None
    warehouse_key: str | None = None
    dependency_lhs: str | None = None
    dependency_rhs: str | None = None

    @model_validator(mode="after")
    def blocking_shape(self) -> "ScaleInputRow":
        keys = tuple(dict.fromkeys((*self.blocking_keys, *("" if self.block_key is None else (self.block_key,)))))
        if "" in keys:
            keys = tuple(item for item in keys if item)
        object.__setattr__(self, "blocking_keys", keys)
        return self


class ScaleInputDataset(_ScaleModel):
    descriptor: ScaleInputDescriptor
    rows: tuple[ScaleInputRow, ...] = ()

    @model_validator(mode="after")
    def validate_row_closure(self) -> "ScaleInputDataset":
        if len(self.rows) != self.descriptor.row_count:
            raise ValueError("input row count does not close against its descriptor")
        refs = [row.row_ref for row in self.rows]
        if len(refs) != len(set(refs)):
            raise ValueError("input row references must be unique")
        for row in self.rows:
            if row.table_id != self.descriptor.table_id:
                raise ValueError("input row table scope does not match descriptor")
            if row.schema_fingerprint != self.descriptor.schema_fingerprint:
                raise ValueError("input row schema does not match descriptor")
        return self

    @property
    def semantic_hash(self) -> str:
        return stable_digest(
            {
                "descriptor": self.descriptor.model_dump(mode="json"),
                "rows": [row.model_dump(mode="json") for row in sorted(self.rows, key=lambda item: item.row_ref)],
            }
        )


class ScalePolicy(_ScaleModel):
    policy_version: str = Field(min_length=1)
    local_row_threshold: int = Field(default=10_000, ge=0)
    local_byte_threshold: int | None = Field(default=25_000_000, ge=0)
    target_partition_rows: int = Field(default=2_000, gt=0)
    max_partition_rows: int | None = Field(default=None, gt=0)
    max_partition_bytes: int | None = Field(default=None, gt=0)
    max_partitions: int = Field(default=128, ge=1)
    max_worker_slots: int = Field(default=1, ge=1)
    resource_budget: ResourceBudget = Field(default_factory=ResourceBudget)
    requested_mode: ScaleExecutionMode = ScaleExecutionMode.PARTITIONED_LOCAL
    fallback_policy: ScaleFallbackPolicy = ScaleFallbackPolicy.FALLBACK_TO_LOCAL
    stage_support: Mapping[str, StageScaleSupport] = Field(default_factory=dict)
    partition_strategy: PartitionStrategy | None = None
    range_boundaries: tuple[str, ...] = ()
    deterministic_seed: int = 0
    sample_ratio: float = Field(default=1.0, gt=0, le=1)
    sample_limit: int | None = Field(default=None, gt=0)
    skew_ratio_threshold: float = Field(default=2.0, gt=0)
    skew_action: SkewAction = SkewAction.REVIEW_REQUIRED
    max_join_pairs: int = Field(default=100_000, gt=0)
    provisional: bool = True
    provenance_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def budget_shape(self) -> "ScalePolicy":
        if self.max_worker_slots > self.resource_budget.max_worker_slots:
            raise ValueError("policy worker slots exceed ResourceBudget.max_worker_slots")
        if self.requested_mode is ScaleExecutionMode.EXTERNAL_DISTRIBUTED:
            raise ValueError("external distributed execution has no Step24 V1 adapter")
        return self

    @property
    def configuration_fingerprint(self) -> str:
        return stable_digest(self.model_dump(mode="json"))


class ScaleDecision(_ScaleModel):
    decision_id: str = Field(min_length=1)
    component_id: str = "application.distributed_scale"
    stage_id: str = Field(min_length=1)
    input_id: str = Field(min_length=1)
    input_content_hash: str
    estimated_row_count: int = Field(ge=0)
    estimated_byte_count: int | None = Field(default=None, ge=0)
    measured_row_count: int | None = Field(default=None, ge=0)
    measured_byte_count: int | None = Field(default=None, ge=0)
    resource_budget: ResourceBudget
    requested_parallelism: int = Field(ge=1)
    selected_mode: ScaleExecutionMode
    partitioning_required: bool
    reason: str = Field(min_length=1)
    capability_evidence: tuple[str, ...] = ()
    fallback_mode: ScaleExecutionMode | None = None
    policy_version: str = Field(min_length=1)
    provenance_refs: tuple[str, ...] = ()

    _validate_input_hash = field_validator("input_content_hash")(_content_hash)


class PartitionDescriptor(_ScaleModel):
    partition_id: str = Field(min_length=1)
    partition_plan_id: str = Field(min_length=1)
    partition_plan_hash: str
    logical_partition_index: int = Field(ge=0)
    strategy: PartitionStrategy
    routing_key: str = Field(min_length=1)
    range_start: str | None = None
    range_end: str | None = None
    input_id: str = Field(min_length=1)
    input_content_hash: str
    run_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    table_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    schema_fingerprint: str = Field(min_length=1)
    deterministic_seed: int
    expected_row_count: int = Field(ge=0)
    expected_row_digest: str
    expected_resource: ResourceBudget
    provenance_refs: tuple[str, ...] = ()

    _validate_hashes = field_validator("partition_plan_hash", "input_content_hash", "expected_row_digest")(_content_hash)


class SkewAssessment(_ScaleModel):
    partition_count: int = Field(ge=1)
    record_counts: tuple[int, ...]
    byte_counts: tuple[int | None, ...] = ()
    minimum: int = Field(ge=0)
    maximum: int = Field(ge=0)
    mean: float = Field(ge=0)
    max_mean_ratio: float = Field(ge=0)
    empty_partition_count: int = Field(ge=0)
    status: SkewStatus
    action: SkewAction
    detail: str = Field(min_length=1)
    provenance_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def counts_close(self) -> "SkewAssessment":
        if len(self.record_counts) != self.partition_count:
            raise ValueError("skew record counts must cover every partition")
        return self


class ScaleProfileStats(_ScaleModel):
    row_count: int = Field(ge=0)
    non_null_count: int = Field(ge=0)
    null_count: int = Field(ge=0)
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    exact_sum: Decimal = Decimal("0")

    @model_validator(mode="after")
    def close_counts(self) -> "ScaleProfileStats":
        if self.non_null_count + self.null_count != self.row_count:
            raise ValueError("profile counts do not close")
        return self


class ScalePartitionOutput(_ScaleModel):
    record_refs: tuple[str, ...] = ()
    source_record_refs: tuple[str, ...] = ()
    canonical_entity_ids: tuple[str, ...] = ()
    fact_grain_keys: tuple[str, ...] = ()
    warehouse_keys: tuple[str, ...] = ()
    profile: ScaleProfileStats
    exact_measure_sum: Decimal = Decimal("0")
    dependency_pairs: tuple[tuple[str, str], ...] = ()
    candidate_pairs: tuple[str, ...] = ()
    sampled_record_refs: tuple[str, ...] = ()
    unique_input_records: int = Field(ge=0)
    routed_record_instances: int = Field(ge=0)
    replication_count: int = Field(ge=0)
    logical_shuffle_records: int = Field(ge=0)
    logical_shuffle_bytes: int | None = Field(default=None, ge=0)

    @property
    def semantic_hash(self) -> str:
        return stable_digest(self.semantic_content)

    @property
    def semantic_content(self) -> Mapping[str, Any]:
        value = self.model_dump(mode="json")
        return {
            "record_refs": sorted(self.record_refs),
            "source_record_refs": sorted(self.source_record_refs),
            "canonical_entity_ids": sorted(self.canonical_entity_ids),
            "fact_grain_keys": sorted(self.fact_grain_keys),
            "warehouse_keys": sorted(self.warehouse_keys),
            "profile": value["profile"],
            "exact_measure_sum": value["exact_measure_sum"],
            "dependency_pairs": sorted(self.dependency_pairs),
            "candidate_pairs": sorted(self.candidate_pairs),
            "sampled_record_refs": sorted(self.sampled_record_refs),
            "unique_input_records": self.unique_input_records,
            "routed_record_instances": self.routed_record_instances,
            "replication_count": self.replication_count,
            "logical_shuffle_records": self.logical_shuffle_records,
            "logical_shuffle_bytes": self.logical_shuffle_bytes,
        }


class PartitionResult(_ScaleModel):
    result_id: str = Field(min_length=1)
    partition_id: str = Field(min_length=1)
    partition_plan_id: str = Field(min_length=1)
    partition_plan_hash: str
    operation_id: str = Field(min_length=1)
    operation_version: str = Field(min_length=1)
    status: PartitionResultStatus
    input_id: str = Field(min_length=1)
    input_content_hash: str
    schema_fingerprint: str = Field(min_length=1)
    output: ScalePartitionOutput | None = None
    output_artifact_refs: tuple[str, ...] = ()
    output_content_hash: str | None = None
    record_count: int = Field(ge=0)
    byte_count: int | None = Field(default=None, ge=0)
    candidate_pair_count: int = Field(default=0, ge=0)
    shuffle_records: int = Field(default=0, ge=0)
    shuffle_bytes: int | None = Field(default=None, ge=0)
    peak_memory_mb: int | None = Field(default=None, ge=0)
    failure_code: str | None = None
    failure_reason: str | None = None
    attempt_semantic_identity: str = Field(min_length=1)
    provenance_refs: tuple[str, ...] = ()

    _validate_hashes = field_validator("partition_plan_hash", "input_content_hash", "output_content_hash")(_content_hash)

    @model_validator(mode="after")
    def status_shape(self) -> "PartitionResult":
        if self.status is PartitionResultStatus.SUCCEEDED and self.output is None:
            raise ValueError("successful partition results require output")
        if self.status is not PartitionResultStatus.SUCCEEDED and not self.failure_code:
            raise ValueError("failed or rejected partition results require a failure code")
        if self.output is not None and self.record_count != self.output.profile.row_count:
            raise ValueError("partition record count does not match profile output")
        return self

    @property
    def semantic_result_hash(self) -> str:
        return stable_digest(
            {
                "partition_id": self.partition_id,
                "partition_plan_id": self.partition_plan_id,
                "partition_plan_hash": self.partition_plan_hash,
                "operation_id": self.operation_id,
                "operation_version": self.operation_version,
                "status": self.status.value,
                "input_id": self.input_id,
                "input_content_hash": self.input_content_hash,
                "schema_fingerprint": self.schema_fingerprint,
                "output": None if self.output is None else self.output.semantic_content,
                "record_count": self.record_count,
                "candidate_pair_count": self.candidate_pair_count,
                "shuffle_records": self.shuffle_records,
                "shuffle_bytes": self.shuffle_bytes,
                "failure_code": self.failure_code,
            }
        )


class PartitionPlan(_ScaleModel):
    partition_plan_id: str = Field(min_length=1)
    partition_plan_hash: str
    operation_id: str = Field(min_length=1)
    operation_version: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    input_id: str = Field(min_length=1)
    input_content_hash: str
    input_artifact_refs: tuple[str, ...] = ()
    source_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    table_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    schema_fingerprint: str = Field(min_length=1)
    strategy: PartitionStrategy
    partition_key_specification: str = Field(min_length=1)
    partition_count: int = Field(ge=1)
    deterministic_seed: int
    resource_budget_fingerprint: str
    policy_version: str = Field(min_length=1)
    partitions: tuple[PartitionDescriptor, ...]
    expected_partition_ids: tuple[str, ...]
    expected_merge_strategy: str = Field(min_length=1)
    skew: SkewAssessment
    provenance_refs: tuple[str, ...] = ()

    _validate_hashes = field_validator("partition_plan_hash", "input_content_hash", "resource_budget_fingerprint")(_content_hash)

    @model_validator(mode="after")
    def partition_closure(self) -> "PartitionPlan":
        ids = tuple(item.partition_id for item in self.partitions)
        if ids != self.expected_partition_ids or len(ids) != self.partition_count or len(set(ids)) != len(ids):
            raise ValueError("partition plan expected partition closure is invalid")
        for item in self.partitions:
            if item.partition_plan_id != self.partition_plan_id:
                raise ValueError("partition descriptor belongs to another plan")
            if item.input_content_hash != self.input_content_hash or item.schema_fingerprint != self.schema_fingerprint:
                raise ValueError("partition descriptor input closure is invalid")
        return self

    @property
    def semantic_content(self) -> Mapping[str, Any]:
        return {
            "schema_version": self.schema_version,
            "operation_id": self.operation_id,
            "operation_version": self.operation_version,
            "run_id": self.run_id,
            "input_id": self.input_id,
            "input_content_hash": self.input_content_hash,
            "input_artifact_refs": sorted(self.input_artifact_refs),
            "source_id": self.source_id,
            "snapshot_id": self.snapshot_id,
            "table_id": self.table_id,
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "schema_fingerprint": self.schema_fingerprint,
            "strategy": self.strategy.value,
            "partition_key_specification": self.partition_key_specification,
            "partition_count": self.partition_count,
            "deterministic_seed": self.deterministic_seed,
            "resource_budget_fingerprint": self.resource_budget_fingerprint,
            "policy_version": self.policy_version,
            "expected_partition_ids": sorted(self.expected_partition_ids),
            "partitions": [
                {
                    "partition_id": item.partition_id,
                    "logical_partition_index": item.logical_partition_index,
                    "strategy": item.strategy.value,
                    "routing_key": item.routing_key,
                    "range_start": item.range_start,
                    "range_end": item.range_end,
                    "expected_row_count": item.expected_row_count,
                    "expected_row_digest": item.expected_row_digest,
                }
                for item in sorted(self.partitions, key=lambda value: value.partition_id)
            ],
            "expected_merge_strategy": self.expected_merge_strategy,
        }

    @property
    def semantic_hash(self) -> str:
        return stable_digest(self.semantic_content)


class PartitionMergeManifest(_ScaleModel):
    merge_manifest_id: str = Field(min_length=1)
    partition_plan_id: str = Field(min_length=1)
    partition_plan_hash: str
    expected_partition_ids: tuple[str, ...]
    result_hashes: Mapping[str, str] = Field(default_factory=dict)
    merge_reduction_version: str = Field(min_length=1)
    merge_ordering: str = Field(min_length=1)
    semantic_output_hash: str | None = None
    missing_partitions: tuple[str, ...] = ()
    duplicate_partitions: tuple[str, ...] = ()
    unexpected_partitions: tuple[str, ...] = ()
    conflicting_results: tuple[str, ...] = ()
    aggregate_record_count: int = Field(default=0, ge=0)
    status: ScaleExecutionStatus
    failure_reason: str | None = None
    provenance_refs: tuple[str, ...] = ()

    _validate_hashes = field_validator("partition_plan_hash", "semantic_output_hash")(_content_hash)

    @property
    def semantic_hash(self) -> str:
        return stable_digest(
            {
                "partition_plan_id": self.partition_plan_id,
                "partition_plan_hash": self.partition_plan_hash,
                "expected_partition_ids": sorted(self.expected_partition_ids),
                "result_hashes": dict(sorted(self.result_hashes.items())),
                "merge_reduction_version": self.merge_reduction_version,
                "merge_ordering": self.merge_ordering,
                "semantic_output_hash": self.semantic_output_hash,
                "missing_partitions": sorted(self.missing_partitions),
                "duplicate_partitions": sorted(self.duplicate_partitions),
                "unexpected_partitions": sorted(self.unexpected_partitions),
                "conflicting_results": sorted(self.conflicting_results),
                "aggregate_record_count": self.aggregate_record_count,
                "status": self.status.value,
            }
        )


class ScaleMergedOutput(_ScaleModel):
    input_id: str = Field(min_length=1)
    input_content_hash: str
    record_refs: tuple[str, ...] = ()
    source_record_refs: tuple[str, ...] = ()
    canonical_entity_ids: tuple[str, ...] = ()
    fact_grain_keys: tuple[str, ...] = ()
    warehouse_keys: tuple[str, ...] = ()
    profile: ScaleProfileStats
    exact_measure_sum: Decimal = Decimal("0")
    dependency_pairs: tuple[tuple[str, str], ...] = ()
    dependency_valid: bool | None = None
    candidate_pairs: tuple[str, ...] = ()
    sampled_record_refs: tuple[str, ...] = ()
    input_record_count: int = Field(ge=0)
    emitted_record_count: int = Field(ge=0)
    filtered_record_count: int = Field(default=0, ge=0)
    quarantined_record_count: int = Field(default=0, ge=0)
    unresolved_record_count: int = Field(default=0, ge=0)
    unique_input_records: int = Field(ge=0)
    routed_record_instances: int = Field(ge=0)
    replication_count: int = Field(ge=0)
    logical_shuffle_records: int = Field(ge=0)
    logical_shuffle_bytes: int | None = Field(default=None, ge=0)

    _validate_hash = field_validator("input_content_hash")(_content_hash)

    @property
    def semantic_content(self) -> Mapping[str, Any]:
        value = self.model_dump(mode="json")
        return {
            "input_id": self.input_id,
            "input_content_hash": self.input_content_hash,
            "record_refs": sorted(self.record_refs),
            "source_record_refs": sorted(self.source_record_refs),
            "canonical_entity_ids": sorted(self.canonical_entity_ids),
            "fact_grain_keys": sorted(self.fact_grain_keys),
            "warehouse_keys": sorted(self.warehouse_keys),
            "profile": value["profile"],
            "exact_measure_sum": value["exact_measure_sum"],
            "dependency_pairs": sorted(self.dependency_pairs),
            "dependency_valid": self.dependency_valid,
            "candidate_pairs": sorted(self.candidate_pairs),
            "sampled_record_refs": sorted(self.sampled_record_refs),
            "input_record_count": self.input_record_count,
            "emitted_record_count": self.emitted_record_count,
            "filtered_record_count": self.filtered_record_count,
            "quarantined_record_count": self.quarantined_record_count,
            "unresolved_record_count": self.unresolved_record_count,
            "unique_input_records": self.unique_input_records,
            "routed_record_instances": self.routed_record_instances,
            "replication_count": self.replication_count,
            "logical_shuffle_records": self.logical_shuffle_records,
            "logical_shuffle_bytes": self.logical_shuffle_bytes,
        }

    @property
    def semantic_hash(self) -> str:
        return stable_digest(self.semantic_content)


class ScaleExecutionResult(_ScaleModel):
    execution_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    mode: ScaleExecutionMode
    input_id: str = Field(min_length=1)
    input_content_hash: str
    partition_plan_id: str = Field(min_length=1)
    partition_plan_hash: str
    status: ScaleExecutionStatus
    partition_results: tuple[PartitionResult, ...] = ()
    merge_manifest: PartitionMergeManifest
    merged_output: ScaleMergedOutput | None = None
    observed_worker_slots: int = Field(ge=0)
    peak_memory_mb: int | None = Field(default=None, ge=0)
    failure_code: str | None = None
    failure_reason: str | None = None
    output_artifact_refs: tuple[str, ...] = ()
    provenance_refs: tuple[str, ...] = ()

    _validate_hashes = field_validator("input_content_hash", "partition_plan_hash")(_content_hash)


class ScaleEquivalenceReport(_ScaleModel):
    report_id: str = Field(min_length=1)
    verified_content_commit: str = Field(default="0" * 40, pattern=r"^[0-9a-f]{40}$")
    operation_id: str = Field(min_length=1)
    local_reference_execution_id: str = Field(min_length=1)
    local_reference_output_hash: str
    partitioned_execution_id: str = Field(min_length=1)
    partitioned_output_hash: str | None = None
    partition_plan_id: str = Field(min_length=1)
    partition_plan_hash: str
    partition_count: int = Field(ge=1)
    comparison_policy: str = Field(min_length=1)
    record_accounting: Mapping[str, int | bool] = Field(default_factory=dict)
    key_topology_equivalence: bool
    aggregate_equivalence: bool
    lineage_equivalence: bool
    ordering_policy: str = Field(min_length=1)
    determinism_evidence: Mapping[str, bool] = Field(default_factory=dict)
    skew_evidence: Mapping[str, Any] = Field(default_factory=dict)
    shuffle_evidence: Mapping[str, Any] = Field(default_factory=dict)
    failure_test_evidence: Mapping[str, bool] = Field(default_factory=dict)
    materialization_equivalence: bool | None = None
    status: EquivalenceStatus
    g7a_eligible: bool
    provenance_refs: tuple[str, ...] = ()

    _validate_hashes = field_validator("local_reference_output_hash", "partitioned_output_hash", "partition_plan_hash")(_content_hash)

    @field_validator("verified_content_commit")
    @classmethod
    def normalize_commit(cls, value: str) -> str:
        return value.lower()

    @model_validator(mode="after")
    def eligibility_shape(self) -> "ScaleEquivalenceReport":
        if self.g7a_eligible != (self.status is EquivalenceStatus.PASS):
            raise ValueError("G7A eligibility must match equivalence status")
        return self


def record_digest(row: ScaleInputRow | Mapping[str, Any]) -> str:
    """Stable row identity/content digest used by partition closure checks."""

    if isinstance(row, ScaleInputRow):
        value = row.model_dump(mode="json")
    else:
        value = dict(row)
    return stable_digest(value)


def partition_id_for(*, run_id: str, source_id: str, snapshot_id: str, table_id: str, dataset_id: str, dataset_version: str, input_id: str, stage_id: str, schema_fingerprint: str, strategy: PartitionStrategy | str, partition_index: int, routing_key: str, deterministic_seed: int, policy_version: str) -> str:
    return stable_id(
        "partition",
        {
            "run_id": run_id,
            "source_id": source_id,
            "snapshot_id": snapshot_id,
            "table_id": table_id,
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
            "input_id": input_id,
            "stage_id": stage_id,
            "schema_fingerprint": schema_fingerprint,
            "strategy": getattr(strategy, "value", strategy),
            "partition_index": partition_index,
            "routing_key": routing_key,
            "deterministic_seed": deterministic_seed,
            "policy_version": policy_version,
        },
    )


def partition_plan_id_for(value: Mapping[str, Any] | None = None, **kwargs: Any) -> str:
    payload = dict(value or {})
    payload.update(kwargs)
    return stable_id("partition-plan", payload)


def partition_result_id_for(*, partition_plan_id: str, partition_id: str, operation_id: str, operation_version: str, input_content_hash: str, policy_version: str, deterministic_seed: int) -> str:
    return stable_id(
        "partition-result",
        {
            "partition_plan_id": partition_plan_id,
            "partition_id": partition_id,
            "operation_id": operation_id,
            "operation_version": operation_version,
            "input_content_hash": input_content_hash,
            "policy_version": policy_version,
            "deterministic_seed": deterministic_seed,
        },
    )


def pair_id_for(*, run_id: str, source_id: str, snapshot_id: str, table_id: str, left_row_ref: str, right_row_ref: str) -> str:
    left, right = sorted((left_row_ref, right_row_ref))
    if left == right:
        raise ValueError("comparison pair requires two distinct records")
    return stable_id("candidate-pair", {"run_id": run_id, "source_id": source_id, "snapshot_id": snapshot_id, "table_id": table_id, "left": left, "right": right})


__all__ = [
    "EquivalenceStatus",
    "PartitionDescriptor",
    "PartitionMergeManifest",
    "PartitionResult",
    "PartitionResultStatus",
    "PartitionStrategy",
    "ScaleDecision",
    "ScaleEquivalenceReport",
    "ScaleExecutionMode",
    "ScaleExecutionStatus",
    "ScaleInputDataset",
    "ScaleInputDescriptor",
    "ScaleInputRow",
    "ScaleMergedOutput",
    "ScalePartitionOutput",
    "ScalePolicy",
    "ScaleProfileStats",
    "ScaleFallbackPolicy",
    "SkewAction",
    "SkewAssessment",
    "SkewStatus",
    "StageScaleSupport",
    "pair_id_for",
    "partition_id_for",
    "partition_plan_id_for",
    "partition_result_id_for",
    "record_digest",
]
