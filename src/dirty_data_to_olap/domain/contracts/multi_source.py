"""Prompt02 multi-source acceptance contracts.

These contracts contain safe run evidence only. Raw source values remain in
source-faithful staging and provider-private scopes; they never cross into the
receipt or durable review metadata.
"""

from __future__ import annotations

from datetime import datetime
from typing import Mapping

from pydantic import Field

from .source import _SourceModel, SourceType, stable_digest


class MultiSourceStageEvidence(_SourceModel):
    stage_id: str
    status: str
    attempts: int = Field(ge=1)
    providers: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    detail: str = ""


class MultiSourceRecordAccounting(_SourceModel):
    source_id: str
    input_records: int = Field(ge=0)
    emitted_records: int = Field(ge=0)
    consolidated_records: int = Field(ge=0)
    quarantined_records: int = Field(ge=0)
    unresolved_records: int = Field(ge=0)
    duplicate_key_candidates: int = Field(ge=0)
    orphan_records: int = Field(ge=0)
    disposition_complete: bool


class MultiSourceReviewEvidence(_SourceModel):
    checkpoint: str
    subject_id: str
    decision: str
    actor: str
    actor_source: str
    rationale: str
    reviewed_at: datetime


class MultiSourceSourceEvidence(_SourceModel):
    registry_id: str
    source_id: str
    source_type: SourceType
    source_config_fingerprint: str
    selection_fingerprint: str
    snapshot_id: str
    snapshot_fingerprint: str
    schema_fingerprint: str
    selected_tables: tuple[str, ...]
    extraction_max_rows: int | None
    input_records: int = Field(ge=0)
    staged_records: int = Field(ge=0)
    source_unchanged_before_after: bool


class MultiSourceAnalyticalEvidence(_SourceModel):
    fact_table: str
    fact_grain: str
    fact_row_count: int = Field(ge=0)
    dimensions: tuple[str, ...] = Field(min_length=3)
    dimension_row_counts: Mapping[str, int]
    measures: tuple[str, ...] = Field(min_length=1)
    non_measures: tuple[str, ...] = ()
    lineage_refs: tuple[str, ...] = Field(min_length=1)
    quantity_sum: str


class MultiSourceMaterializationEvidence(_SourceModel):
    duckdb_relative_path: str
    target_file_sha256: str
    table_names: tuple[str, ...]
    row_counts: Mapping[str, int]
    usable: bool


class MultiSourceIndependentOracleEvidence(_SourceModel):
    oracle_id: str
    oracle_version: str
    oracle_path: str
    loaded_after_product_run: bool
    matched_fact_rows: bool
    matched_aggregates: bool
    matched_dispositions: bool


class MultiSourceNegativeControlEvidence(_SourceModel):
    control_id: str = Field(pattern=r"^NC(?:0[1-9]|1[0-4])$")
    requirement: str = Field(min_length=1)
    injected_fault: str = Field(min_length=1)
    execution_boundary: str = Field(min_length=1)
    expected_rejection: str = Field(min_length=1)
    actual_rejection: str = Field(min_length=1)
    durable_evidence: tuple[str, ...] = Field(min_length=1)
    implementation_status: str = Field(pattern=r"^(IMPLEMENTED|NOT_IMPLEMENTED)$")
    execution_status: str = Field(pattern=r"^(PASS|BLOCKED|NOT_EXECUTED)$")
    acceptance_status: str = Field(pattern=r"^(PASS|PENDING)$")


class MultiSourceAcceptanceReceipt(_SourceModel):
    receipt_id: str
    run_id: str
    content_commit: str
    source_set_fingerprint: str
    sources: tuple[MultiSourceSourceEvidence, ...] = Field(min_length=2)
    stages: tuple[MultiSourceStageEvidence, ...] = Field(min_length=1)
    reviews: tuple[MultiSourceReviewEvidence, ...] = ()
    record_accounting: tuple[MultiSourceRecordAccounting, ...] = Field(min_length=2)
    analytical: MultiSourceAnalyticalEvidence | None = None
    materialization: MultiSourceMaterializationEvidence | None = None
    oracle: MultiSourceIndependentOracleEvidence | None = None
    negative_controls: Mapping[str, str] = Field(default_factory=dict)
    negative_control_evidence: tuple[MultiSourceNegativeControlEvidence, ...] = ()
    status: str
    blocked_reasons: tuple[str, ...] = ()

    @property
    def content_hash(self) -> str:
        return stable_digest(self.model_dump(mode="json"))
