"""Safe product projections used by the Step29 browser boundary.

These are presentation contracts, not a second source of domain truth.  The
application layer builds them only from verified registered artifacts and
durable control-plane records.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from pydantic import Field

from .canonical import ReviewCompatibilityContext, _SourceModel
from .review_actions import ReviewActionApplicability


class ProductPolicyBinding(_SourceModel):
    """Immutable identity of the domain policy selected for one run."""

    product_id: str = Field(pattern=r"^[a-z][a-z0-9_.:-]{0,63}$")
    version: str = Field(min_length=1, max_length=128)
    content_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    provenance_ref: str = Field(min_length=1, max_length=512)

    @property
    def policy_key(self) -> str:
        return f"{self.product_id}:{self.version}"


class ProductDomainPolicy(Protocol):
    """Typed domain capability boundary consumed by shared orchestration.

    The protocol deliberately describes policy capabilities, not workers,
    review persistence, artifact storage, compilation, or materialization.
    Implementations remain versioned and fingerprinted through
    ``ProductPolicyBinding``.
    """

    product_id: str
    version: str
    provenance: str
    content_fingerprint: str

    @property
    def binding(self) -> ProductPolicyBinding: ...

    def source_role(self, catalog: Any) -> str: ...
    def role_table(self, catalog: Any) -> Any: ...
    def logical_value(self, catalog: Any, table_id: str, values: Mapping[str, Any], logical_name: str) -> Any: ...
    def profile_request(self, catalog: Any, snapshot: Any, run_id: str) -> Any: ...
    def dependency_request(self, catalog: Any, snapshot: Any, run_id: str) -> Any: ...
    def dependency_result(self, result: Any, *, catalog: Any) -> Any: ...
    def quality_request(self, catalog: Any, snapshot: Any, profile: Any, run_id: str) -> Any: ...
    def relationship_candidates(self, catalogs: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]: ...
    def domain_assertions(self, candidates: Sequence[Mapping[str, Any]], catalogs: Mapping[str, Any], snapshots: Mapping[str, Any]) -> tuple[Any, ...]: ...
    def entity_resolution_spec(self, catalogs: Mapping[str, Any], snapshots: Mapping[str, Any]) -> Any | None: ...
    def entity_resolution_requirements(self, entity_types: Sequence[Any]) -> Mapping[str, Any]: ...
    def entity_types(self, catalogs: Mapping[str, Any], snapshots: Mapping[str, Any], relationships: Sequence[Any], domain_refs: tuple[str, ...]) -> tuple[Any, ...]: ...
    def canonical_relationships(self, relationships: Sequence[Any], review_decision_id_by_subject: Mapping[str, str]) -> tuple[Any, ...]: ...
    def identity_memberships(self, *, hypothesis: Any, snapshots: Mapping[str, Any], catalogs: Mapping[str, Any], policy_ref: str, entity_resolution_result: Any | None = None, entity_resolution_ref: str | None = None, domain_assertions: Sequence[Any] = ()) -> tuple[Any, ...]: ...
    def validate_identity_memberships(self, memberships: Sequence[Any]) -> None: ...
    def build_multi_source_dataset_and_request(self, *, run_id: str, canonical: Any, service: Any, catalogs: Mapping[str, Any], snapshots: Mapping[str, Any], relationships: Sequence[Any]) -> tuple[Any, Any, Any]: ...
    def source_records(self, *, service: Any, catalogs: Mapping[str, Any], snapshots: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]: ...
    def build_truth_and_accounting(self, **kwargs: Any) -> tuple[Any, Any]: ...
    def record_accounting_ref(self, run_id: str) -> str: ...
    def validation_policy(self, *, canonical_model_id: str, materialization_id: str) -> Any: ...


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
    action_revision: int = 0
    subject_type: str = "ReviewSubject"
    subject_description: str = "Server-owned review subject"
    source_scope: tuple[str, ...] = ()
    confidence_semantics: str = "Evidence is not a probability."
    supporting_evidence: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    provenance_summary: tuple[str, ...] = ()
    downstream_consequence: str = "The guarded stage remains paused until the server records a satisfying decision."
    next_required_action: str = "REVIEW"
    locked: bool = False
    labels: tuple[str, ...] = ()
    actions: tuple[ReviewActionApplicability, ...] = ()


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
