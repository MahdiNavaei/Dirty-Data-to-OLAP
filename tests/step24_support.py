from __future__ import annotations

from decimal import Decimal

from dirty_data_to_olap.application.distributed import ScaleService
from dirty_data_to_olap.domain.contracts.distributed import (
    ScaleInputDataset,
    ScaleInputDescriptor,
    ScaleInputRow,
    ScalePolicy,
    StageScaleSupport,
    record_digest,
)
from dirty_data_to_olap.domain.contracts.platform import GateEvidence, GateEvidenceStatus, ResourceBudget
from dirty_data_to_olap.domain.contracts.source import stable_digest


GATE_RUN = "step23-platform-reference-run"
REPORT_ARTIFACT = "step22-validation-report"
REPORT_RUN = "step22-retail-runtime-run"
REPORT_ID = "vreport_d0165bbbb9c4f11e1c0fb0364e18e149"
REPORT_HASH = "b0960c4d443f362696da40863d8b6b2e5a8fb453e1074c8656bb2cc3738c44d1"


def make_dataset(*, stage_id: str = "profiling", count: int = 12, candidate_fixture: bool = False, row_order: list[int] | None = None) -> ScaleInputDataset:
    order = row_order or list(range(count))
    rows = []
    for index in order:
        block = f"block-{index % 3}" if candidate_fixture else None
        rows.append(
            ScaleInputRow(
                row_ref=f"row-{index:04d}",
                table_id="orders",
                schema_fingerprint="schema-orders-v1",
                partition_key=f"customer-{index % 5}",
                group_key=f"customer-{index % 5}",
                block_key=block,
                blocking_keys=(block,) if block else (),
                measure=Decimal(index + 1),
                profile_values={"status": "ok" if index % 2 else None},
                canonical_entity_id=f"entity-{index % 4}",
                source_record_refs=(f"source/orders/{index:04d}",),
                fact_grain_key=f"fact-{index:04d}",
                warehouse_key=f"warehouse-{index:04d}",
                dependency_lhs=f"fd-{index % 2}",
                dependency_rhs="left" if index < count // 2 else "right",
            )
        )
    source_hash = stable_digest(sorted(record_digest(row) for row in rows))
    descriptor = ScaleInputDescriptor(
        input_id="orders-input-v1",
        run_id="step24-reference-run",
        source_id="retail-source",
        snapshot_id="retail-snapshot-v1",
        table_id="orders",
        dataset_id="orders-lines",
        dataset_version="v1",
        stage_id=stage_id,
        schema_fingerprint="schema-orders-v1",
        content_hash=source_hash,
        row_count=count,
        estimated_bytes=count * 500,
        artifact_refs=(),
        source_refs=("source-snapshot:retail-snapshot-v1",),
        reference_gate_run_id=GATE_RUN,
        reference_validation_report_artifact_id=REPORT_ARTIFACT,
        reference_validation_report_run_id=REPORT_RUN,
        reference_validation_report_id=REPORT_ID,
        reference_validation_report_hash=REPORT_HASH,
        provenance_refs=("step22:exact-validation-report",),
    )
    return ScaleInputDataset(descriptor=descriptor, rows=tuple(rows))


def make_policy(*, stage_id: str = "profiling", workers: int = 2, partitions: int = 3, skew_action: str = "REVIEW_REQUIRED", sample_ratio: float = 1.0, sample_limit: int | None = None, max_join_pairs: int = 100_000) -> ScalePolicy:
    return ScalePolicy(
        policy_version="step24-test-policy-v1",
        local_row_threshold=0,
        local_byte_threshold=0,
        max_partitions=max(partitions, 8),
        max_worker_slots=workers,
        resource_budget=ResourceBudget(max_worker_slots=workers, memory_budget_mb=128, temporary_space_budget_bytes=10_000_000),
        stage_support={stage_id: StageScaleSupport.PARTITION_SAFE if stage_id == "profiling" else StageScaleSupport.GLOBAL_CANDIDATE_GENERATION},
        skew_ratio_threshold=100.0,
        skew_action=skew_action,
        sample_ratio=sample_ratio,
        sample_limit=sample_limit,
        max_join_pairs=max_join_pairs,
    )


def make_gate() -> GateEvidence:
    return GateEvidence(
        gate_id="G6_DATA_CORRECTNESS",
        run_id=GATE_RUN,
        status=GateEvidenceStatus.PASS,
        eligible=True,
        validation_report_artifact_id=REPORT_ARTIFACT,
        validation_report_run_id=REPORT_RUN,
        validation_report_id=REPORT_ID,
        validation_report_content_hash=REPORT_HASH,
        policy_version="step22-g6-policy-v1",
        verified_content_commit="f5ddeb6dbd2a1759b8d92a4dacf47400d6b580c2",
        provenance_refs=("step22:exact-validation-report",),
    )


def service() -> ScaleService:
    return ScaleService()
