"""Safe product projections used by the Step29 browser boundary.

These are presentation contracts, not a second source of domain truth.  The
application layer builds them only from verified registered artifacts and
durable control-plane records.
"""

from __future__ import annotations

from typing import Mapping

from pydantic import Field

from .canonical import ReviewCompatibilityContext, _SourceModel


class ProductConfiguration(_SourceModel):
    configuration_fingerprint: str
    authentication_mode: str = "local_test_reference_only"


class ProductSourceView(_SourceModel):
    registry_id: str
    source_id: str | None = None
    display_name: str
    source_type: str
    adapter_name: str
    read_only: bool = True


class ProductSourceBinding(_SourceModel):
    run_id: str
    registry_id: str
    source_id: str | None = None
    source_display_name: str
    selection_artifact_id: str
    extraction_max_rows: int | None = None
    extraction_chunk_size: int


class ProductSourceSetBinding(_SourceModel):
    """Presentation-safe binding summary for a finalized multi-source run."""

    run_id: str
    registry_ids: tuple[str, ...] = Field(min_length=2)
    source_ids: tuple[str, ...] = Field(min_length=2)
    selection_artifact_id: str
    source_set_fingerprint: str
    extraction_max_rows: int | None = None
    extraction_chunk_size: int


class ProductStageView(_SourceModel):
    stage_id: str
    status: str
    selected: bool
    required: bool
    artifact_kinds: tuple[str, ...] = ()
    artifact_ids: tuple[str, ...] = ()


class ProductReviewView(_SourceModel):
    checkpoint: str
    subject_artifact_id: str
    subject_content_hash: str
    subject_semantic_id: str
    state: str
    decision: str | None = None
    revision: int = 0
    context: ReviewCompatibilityContext


class ProductDataConditionView(_SourceModel):
    source_rows_observed: int = 0
    staged_rows: int = 0
    column_count: int = 0
    null_columns: tuple[str, ...] = ()
    observation_scope: str = "unknown"
    state: str = "NOT_EVALUATED"


class ProductRelationshipView(_SourceModel):
    decision_id: str
    subject_id: str
    from_table: str
    from_columns: tuple[str, ...]
    to_table: str
    to_columns: tuple[str, ...]
    score_semantics: str
    score_value: float | None = None
    confidence_band: str
    decision_state: str
    supporting_signal_count: int = 0
    missing_evidence_count: int = 0


class ProductCanonicalView(_SourceModel):
    model_id: str | None = None
    hypothesis_id: str | None = None
    proposal_id: str | None = None
    entity_type_ids: tuple[str, ...] = ()
    membership_count: int = 0
    identity_basis: tuple[str, ...] = ()
    state: str = "NOT_EVALUATED"


class ProductAnalyticalView(_SourceModel):
    plan_id: str | None = None
    fact_ids: tuple[str, ...] = ()
    dimension_ids: tuple[str, ...] = ()
    grain_ids: tuple[str, ...] = ()
    measure_ids: tuple[str, ...] = ()
    measure_semantics: tuple[str, ...] = ()
    state: str = "NOT_EVALUATED"


class ProductMaterializationView(_SourceModel):
    artifact_id: str | None = None
    status: str = "NOT_EVALUATED"
    usable: bool = False
    table_names: tuple[str, ...] = ()
    row_counts: Mapping[str, int] = Field(default_factory=dict)
    target_type: str | None = None


class ProductValidationView(_SourceModel):
    report_id: str | None = None
    g6_status: str = "PENDING"
    g6_eligible: bool = False
    overall_status: str = "NOT_EVALUATED"
    passed_check_count: int = 0
    failed_check_count: int = 0
    not_evaluated_check_count: int = 0
    validation_artifact_id: str | None = None


class ProductOutputView(_SourceModel):
    materialization_artifact_id: str | None = None
    table_names: tuple[str, ...] = ()
    row_counts: Mapping[str, int] = Field(default_factory=dict)
    validated: bool = False


class ProductSummary(_SourceModel):
    run_id: str
    project_id: str
    status: str
    planning_phase: str | None = None
    current_stage: str | None = None
    source: ProductSourceView | None = None
    source_binding: ProductSourceBinding | None = None
    stages: tuple[ProductStageView, ...] = ()
    pending_reviews: tuple[ProductReviewView, ...] = ()
    relationships: tuple[ProductRelationshipView, ...] = ()
    data_condition: ProductDataConditionView
    canonical: ProductCanonicalView
    analytical: ProductAnalyticalView
    materialization: ProductMaterializationView
    validation: ProductValidationView
    output: ProductOutputView
    privacy_note: str = "Raw rows, file paths, SQL and credentials are not exposed by this product boundary."
