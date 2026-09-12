"""Project-owned visualization contracts.

Visualization models are projections of domain evidence for a future frontend.
They are deliberately not domain identity, canonical semantics, validation
truth, raw SQL, raw values, or third-party renderer objects.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Literal, Mapping

from pydantic import Field, field_validator, model_validator

from .source import _SourceModel, stable_digest, stable_id
from .validation import GateStatus, ValidationReport


class VisualizationKind(str, Enum):
    SOURCE_SCHEMA = "SOURCE_SCHEMA"
    NEIGHBORHOOD = "NEIGHBORHOOD"
    LINEAGE = "LINEAGE"
    EVIDENCE = "EVIDENCE"
    ENTITY_RESOLUTION = "ENTITY_RESOLUTION"
    CANONICAL = "CANONICAL"
    OLAP = "OLAP"
    QUALITY_HEATMAP = "QUALITY_HEATMAP"
    PROFILE_DISTRIBUTION = "PROFILE_DISTRIBUTION"
    VALIDATION = "VALIDATION"


class VisualizationDisclosureMode(str, Enum):
    OVERVIEW = "OVERVIEW"
    NEIGHBORHOOD = "NEIGHBORHOOD"
    FOCUSED_PATH = "FOCUSED_PATH"
    FULL_BOUNDED = "FULL_BOUNDED"


class VisualNodeType(str, Enum):
    SOURCE = "SOURCE"
    SNAPSHOT = "SNAPSHOT"
    TABLE = "TABLE"
    VIEW = "VIEW"
    COLUMN = "COLUMN"
    CONSTRAINT = "CONSTRAINT"
    RELATIONSHIP_CANDIDATE = "RELATIONSHIP_CANDIDATE"
    SCHEMA_MAPPING_CANDIDATE = "SCHEMA_MAPPING_CANDIDATE"
    ENTITY_RECORD = "ENTITY_RECORD"
    ENTITY_CLUSTER = "ENTITY_CLUSTER"
    CANONICAL_ENTITY = "CANONICAL_ENTITY"
    CANONICAL_EVENT = "CANONICAL_EVENT"
    CANONICAL_ATTRIBUTE = "CANONICAL_ATTRIBUTE"
    FACT = "FACT"
    DIMENSION = "DIMENSION"
    GRAIN = "GRAIN"
    MEASURE = "MEASURE"
    MATERIALIZATION = "MATERIALIZATION"
    VALIDATION_CHECK = "VALIDATION_CHECK"
    QUALITY_SUBJECT = "QUALITY_SUBJECT"


class VisualEdgeType(str, Enum):
    CONTAINS = "CONTAINS"
    DECLARED_CONSTRAINT = "DECLARED_CONSTRAINT"
    INFERRED_RELATIONSHIP = "INFERRED_RELATIONSHIP"
    ACCEPTED_RELATIONSHIP = "ACCEPTED_RELATIONSHIP"
    SCHEMA_MAPPING_CANDIDATE = "SCHEMA_MAPPING_CANDIDATE"
    ACCEPTED_SCHEMA_MAPPING = "ACCEPTED_SCHEMA_MAPPING"
    ER_CANDIDATE_LINK = "ER_CANDIDATE_LINK"
    ER_AUTHORIZED_LINKAGE = "ER_AUTHORIZED_LINKAGE"
    CANONICAL_SOURCE_MAPPING = "CANONICAL_SOURCE_MAPPING"
    CANONICAL_RELATIONSHIP = "CANONICAL_RELATIONSHIP"
    ANALYTICAL_FACT_DIMENSION = "ANALYTICAL_FACT_DIMENSION"
    LINEAGE = "LINEAGE"
    VALIDATION_DISCREPANCY = "VALIDATION_DISCREPANCY"


class VisualState(str, Enum):
    NO_EVIDENCE = "NO_EVIDENCE"
    NO_CANDIDATES = "NO_CANDIDATES"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    PROVIDER_FAILED = "PROVIDER_FAILED"
    TIMEOUT = "TIMEOUT"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    LONG_RUNNING = "LONG_RUNNING"
    TARGET_CHANGED = "TARGET_CHANGED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    OBSERVED = "OBSERVED"
    PROPOSED = "PROPOSED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    DEFERRED = "DEFERRED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"
    PRIVACY_BLOCKED = "PRIVACY_BLOCKED"
    STALE = "STALE"
    INVALIDATED = "INVALIDATED"
    SUPERSEDED = "SUPERSEDED"
    NOT_EVALUATED = "NOT_EVALUATED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    PASS = "PASS"
    FAIL = "FAIL"


class VisualEvidenceState(str, Enum):
    OBSERVED = "OBSERVED"
    NOT_OBSERVED = "NOT_OBSERVED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    SKIPPED = "SKIPPED"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"
    PRIVACY_BLOCKED = "PRIVACY_BLOCKED"
    INCOMPLETE = "INCOMPLETE"


class VisualReliabilityState(str, Enum):
    FULL = "FULL"
    BOUNDED = "BOUNDED"
    SAMPLED = "SAMPLED"
    NULL_REDUCED = "NULL_REDUCED"
    TEMPORALLY_UNALIGNED = "TEMPORALLY_UNALIGNED"
    INCOMPLETE = "INCOMPLETE"
    UNKNOWN = "UNKNOWN"


class VisualObservationScope(str, Enum):
    FULL_SNAPSHOT = "FULL_SNAPSHOT"
    BOUNDED_SNAPSHOT = "BOUNDED_SNAPSHOT"
    SAMPLED_SCOPE = "SAMPLED_SCOPE"
    OBSERVED_SUBSET = "OBSERVED_SUBSET"
    NOT_OBSERVED = "NOT_OBSERVED"
    UNMEASURED = "UNMEASURED"


class VisualReviewState(str, Enum):
    NOT_REVIEWED = "NOT_REVIEWED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    DEFERRED = "DEFERRED"
    SKIPPED = "SKIPPED"
    INVALIDATED = "INVALIDATED"


class VisualConflictState(str, Enum):
    NONE = "NONE"
    PRESENT = "PRESENT"
    UNRESOLVED = "UNRESOLVED"
    RESOLVED_WITH_ALTERNATIVES = "RESOLVED_WITH_ALTERNATIVES"


class VisualSensitivityState(str, Enum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    SENSITIVE = "SENSITIVE"
    RESTRICTED = "RESTRICTED"
    UNKNOWN = "UNKNOWN"
    REDACTED = "REDACTED"


class VisualAggregationClass(str, Enum):
    ADDITIVE = "ADDITIVE"
    SEMI_ADDITIVE = "SEMI_ADDITIVE"
    NON_ADDITIVE = "NON_ADDITIVE"


class VisualDirection(str, Enum):
    UPSTREAM = "UPSTREAM"
    DOWNSTREAM = "DOWNSTREAM"
    BOTH = "BOTH"


class VisualChartKind(str, Enum):
    BAR = "BAR"
    HISTOGRAM = "HISTOGRAM"
    BOX_PLOT = "BOX_PLOT"
    TABLE = "TABLE"
    NONE = "NONE"


class ValidationDisplayState(str, Enum):
    """Privacy outcome for an expected or observed validation value."""

    SHOWN = "SHOWN"
    MASKED = "MASKED"
    UNAVAILABLE = "UNAVAILABLE"
    PRIVACY_BLOCKED = "PRIVACY_BLOCKED"


class VisualNodeShape(str, Enum):
    CIRCLE = "CIRCLE"
    RECTANGLE = "RECTANGLE"
    DIAMOND = "DIAMOND"
    HEXAGON = "HEXAGON"
    PILL = "PILL"


class VisualLineStyle(str, Enum):
    SOLID = "SOLID"
    DASHED = "DASHED"
    DOTTED = "DOTTED"


_RAW_LABEL_PATTERNS = (
    re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+"),
    re.compile(r"(?i)(?:password|passwd|secret|token|api[_-]?key)\s*[:=]"),
    re.compile(r"(?<!\d)\d{10,}(?!\d)"),
)


class VisualLabel(_SourceModel):
    value: str = Field(min_length=1)
    accessible_text: str = Field(min_length=1)
    sensitivity: VisualSensitivityState = VisualSensitivityState.INTERNAL
    redacted: bool = False
    raw_value_allowed: Literal[False] = False

    @field_validator("value", "accessible_text")
    @classmethod
    def no_raw_sensitive_label(cls, value: str) -> str:
        if any(pattern.search(value) for pattern in _RAW_LABEL_PATTERNS):
            raise ValueError("visual labels cannot contain raw sensitive values")
        return value

    @model_validator(mode="after")
    def redaction_is_explicit(self) -> "VisualLabel":
        if self.sensitivity is VisualSensitivityState.REDACTED and not self.redacted:
            raise ValueError("redacted visual labels require redacted=True")
        return self


class VisualizationScope(_SourceModel):
    visualization_id: str = Field(min_length=1)
    visualization_version: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    stage_id: str = Field(min_length=1)
    scope_id: str = Field(min_length=1)
    scope_semantics: str = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)


class VisualReviewability(_SourceModel):
    review_state: VisualReviewState
    review_decision_refs: tuple[str, ...] = ()
    action_refs: tuple[str, ...] = ()
    consequence: str = Field(min_length=1)


class VisualNode(_SourceModel):
    visual_node_id: str = Field(pattern=r"^vnode_[a-f0-9]{32}$")
    domain_ref: str = Field(min_length=1)
    node_type: VisualNodeType
    label: VisualLabel
    state: VisualState
    evidence_state: VisualEvidenceState
    reliability: VisualReliabilityState
    observation_scope: VisualObservationScope
    conflict_state: VisualConflictState = VisualConflictState.NONE
    sensitivity_state: VisualSensitivityState = VisualSensitivityState.INTERNAL
    lineage_refs: tuple[str, ...] = ()
    provenance_refs: tuple[str, ...] = Field(min_length=1)
    reviewability: VisualReviewability
    layout_hint: VisualNodeShape
    accessible_description: str = Field(min_length=1)

    @model_validator(mode="after")
    def sensitivity_matches_label(self) -> "VisualNode":
        if self.label.sensitivity is VisualSensitivityState.REDACTED and self.sensitivity_state is not VisualSensitivityState.REDACTED:
            raise ValueError("redacted labels require redacted node sensitivity state")
        return self


class VisualEdge(_SourceModel):
    visual_edge_id: str = Field(pattern=r"^vedge_[a-f0-9]{32}$")
    source_visual_node_id: str = Field(pattern=r"^vnode_[a-f0-9]{32}$")
    target_visual_node_id: str = Field(pattern=r"^vnode_[a-f0-9]{32}$")
    domain_ref: str = Field(min_length=1)
    edge_type: VisualEdgeType
    state: VisualState
    evidence_state: VisualEvidenceState
    reliability: VisualReliabilityState
    observation_scope: VisualObservationScope
    conflict_state: VisualConflictState = VisualConflictState.NONE
    lineage_refs: tuple[str, ...] = ()
    provenance_refs: tuple[str, ...] = Field(min_length=1)
    reviewability: VisualReviewability
    line_style: VisualLineStyle
    accessible_description: str = Field(min_length=1)
    declared: bool = False
    inferred: bool = False

    @model_validator(mode="after")
    def declared_and_inferred_are_distinct(self) -> "VisualEdge":
        if self.declared and self.inferred:
            raise ValueError("an edge cannot be both declared and inferred")
        if self.edge_type is VisualEdgeType.DECLARED_CONSTRAINT and not self.declared:
            raise ValueError("declared constraint edges require declared=True")
        if self.edge_type in {VisualEdgeType.INFERRED_RELATIONSHIP, VisualEdgeType.SCHEMA_MAPPING_CANDIDATE, VisualEdgeType.ER_CANDIDATE_LINK} and self.declared:
            raise ValueError("candidate edges cannot be declared relationships")
        return self


class AccessibleGraphRow(_SourceModel):
    subject_visual_id: str = Field(min_length=1)
    subject_label: str = Field(min_length=1)
    subject_description: str = Field(min_length=1)
    related_visual_ids: tuple[str, ...] = ()


class DisclosureMetadata(_SourceModel):
    mode: VisualizationDisclosureMode
    total_node_count: int = Field(ge=0)
    total_edge_count: int = Field(ge=0)
    rendered_node_count: int = Field(ge=0)
    rendered_edge_count: int = Field(ge=0)
    hidden_node_count: int = Field(ge=0)
    hidden_edge_count: int = Field(ge=0)
    aggregated_node_count: int = Field(ge=0)
    aggregated_edge_count: int = Field(ge=0)
    truncated: bool
    truncation_reason: str | None = None
    show_more_available: bool
    active_filters: tuple[str, ...] = ()
    focus_ref: str | None = None
    hop_depth: int | None = Field(default=None, ge=0)
    accessible_summary: str = Field(min_length=1)

    @model_validator(mode="after")
    def counts_reconcile(self) -> "DisclosureMetadata":
        if self.rendered_node_count + self.hidden_node_count + self.aggregated_node_count != self.total_node_count:
            raise ValueError("disclosure metadata must account for every node")
        if self.rendered_edge_count + self.hidden_edge_count + self.aggregated_edge_count != self.total_edge_count:
            raise ValueError("disclosure metadata must account for every edge")
        has_hidden_content = bool(
            self.hidden_node_count or self.hidden_edge_count or self.aggregated_node_count or self.aggregated_edge_count
        )
        if self.truncated != has_hidden_content:
            raise ValueError("truncated must exactly reflect hidden or aggregated graph content")
        if has_hidden_content and not self.truncation_reason:
            raise ValueError("truncated views require a reason")
        if not has_hidden_content and self.truncation_reason:
            raise ValueError("a non-truncated view cannot carry a truncation reason")
        if self.show_more_available != has_hidden_content:
            raise ValueError("show_more_available must reflect hidden graph content")
        return self


class VisualizationGraph(_SourceModel):
    visualization_id: str = Field(min_length=1)
    kind: VisualizationKind
    scope: VisualizationScope
    nodes: tuple[VisualNode, ...]
    edges: tuple[VisualEdge, ...]
    disclosure: DisclosureMetadata
    legend: tuple[str, ...] = Field(min_length=1)
    accessible_rows: tuple[AccessibleGraphRow, ...]
    content_hash: str = Field(min_length=1)

    @model_validator(mode="after")
    def graph_references_are_closed(self) -> "VisualizationGraph":
        node_ids = {node.visual_node_id for node in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise ValueError("visual node IDs must be unique")
        edge_ids = {edge.visual_edge_id for edge in self.edges}
        if len(edge_ids) != len(self.edges):
            raise ValueError("visual edge IDs must be unique")
        if any(edge.source_visual_node_id not in node_ids or edge.target_visual_node_id not in node_ids for edge in self.edges):
            raise ValueError("visual edges must reference rendered nodes")
        if len(self.accessible_rows) != len(self.nodes):
            raise ValueError("every rendered node requires an accessible representation")
        accessible_ids = [row.subject_visual_id for row in self.accessible_rows]
        if len(accessible_ids) != len(set(accessible_ids)):
            raise ValueError("accessible subject IDs must be unique")
        if set(accessible_ids) != node_ids:
            raise ValueError("accessible rows must cover exactly the rendered visual nodes")
        if any(related_id not in node_ids for row in self.accessible_rows for related_id in row.related_visual_ids):
            raise ValueError("accessible related visual IDs must reference rendered nodes")
        return self


class VisualizationGraphInputNode(_SourceModel):
    domain_ref: str = Field(min_length=1)
    node_type: VisualNodeType
    label: VisualLabel
    state: VisualState = VisualState.OBSERVED
    evidence_state: VisualEvidenceState = VisualEvidenceState.OBSERVED
    reliability: VisualReliabilityState = VisualReliabilityState.UNKNOWN
    observation_scope: VisualObservationScope = VisualObservationScope.UNMEASURED
    conflict_state: VisualConflictState = VisualConflictState.NONE
    sensitivity_state: VisualSensitivityState = VisualSensitivityState.INTERNAL
    lineage_refs: tuple[str, ...] = ()
    provenance_refs: tuple[str, ...] = Field(min_length=1)
    reviewability: VisualReviewability = Field(default_factory=lambda: VisualReviewability(review_state=VisualReviewState.NOT_REVIEWED, consequence="informational visualization only"))
    parent_ref: str | None = None


class VisualizationGraphInputEdge(_SourceModel):
    domain_ref: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    target_ref: str = Field(min_length=1)
    edge_type: VisualEdgeType
    state: VisualState = VisualState.OBSERVED
    evidence_state: VisualEvidenceState = VisualEvidenceState.OBSERVED
    reliability: VisualReliabilityState = VisualReliabilityState.UNKNOWN
    observation_scope: VisualObservationScope = VisualObservationScope.UNMEASURED
    conflict_state: VisualConflictState = VisualConflictState.NONE
    lineage_refs: tuple[str, ...] = ()
    provenance_refs: tuple[str, ...] = Field(min_length=1)
    reviewability: VisualReviewability = Field(default_factory=lambda: VisualReviewability(review_state=VisualReviewState.NOT_REVIEWED, consequence="informational visualization only"))
    declared: bool = False
    inferred: bool = False

    @model_validator(mode="after")
    def relation_kind_is_consistent(self) -> "VisualizationGraphInputEdge":
        if self.declared and self.inferred:
            raise ValueError("input edge cannot be both declared and inferred")
        if self.edge_type is VisualEdgeType.DECLARED_CONSTRAINT and not self.declared:
            raise ValueError("declared constraint input must be declared")
        if self.edge_type in {VisualEdgeType.INFERRED_RELATIONSHIP, VisualEdgeType.SCHEMA_MAPPING_CANDIDATE, VisualEdgeType.ER_CANDIDATE_LINK} and self.declared:
            raise ValueError("inferred/candidate input cannot be declared")
        return self


class VisualizationGraphInput(_SourceModel):
    scope: VisualizationScope
    nodes: tuple[VisualizationGraphInputNode, ...] = Field(min_length=1)
    edges: tuple[VisualizationGraphInputEdge, ...] = ()


class VisualizationRequest(_SourceModel):
    visualization_id: str = Field(min_length=1)
    kind: VisualizationKind
    mode: VisualizationDisclosureMode = VisualizationDisclosureMode.OVERVIEW
    max_nodes: int = Field(default=250, ge=1, le=5000)
    max_edges: int = Field(default=500, ge=0, le=10000)
    max_hops: int = Field(default=2, ge=0, le=8)
    focus_ref: str | None = None
    node_types: tuple[VisualNodeType, ...] = ()
    edge_types: tuple[VisualEdgeType, ...] = ()
    evidence_states: tuple[VisualEvidenceState, ...] = ()
    review_states: tuple[VisualReviewState, ...] = ()
    active_filters: tuple[str, ...] = ()


class EvidenceVisualItem(_SourceModel):
    evidence_ref: str = Field(min_length=1)
    family: str = Field(min_length=1)
    role: str = Field(min_length=1)
    direction: str = Field(min_length=1)
    state: VisualEvidenceState
    reliability: VisualReliabilityState
    observation_scope: VisualObservationScope
    metric_name: str | None = None
    metric_value: float | None = None
    metric_semantics: str | None = None
    normalization: str | None = None
    supporting: bool = False
    contradicting: bool = False
    conflict_refs: tuple[str, ...] = ()
    provenance_refs: tuple[str, ...] = Field(min_length=1)
    human_assertion: bool = False
    calibrated: bool = False
    accessible_description: str = Field(min_length=1)

    @model_validator(mode="after")
    def score_language_is_safe(self) -> "EvidenceVisualItem":
        semantics = (self.metric_semantics or "").lower()
        if self.metric_value is not None and not self.metric_semantics:
            raise ValueError("numeric evidence requires metric semantics")
        if not self.calibrated and any(token in semantics for token in ("probability", "posterior", "likelihood")):
            raise ValueError("uncalibrated visual evidence cannot use probability semantics")
        if self.supporting and self.contradicting:
            raise ValueError("evidence cannot be both supporting and contradicting")
        if self.human_assertion and self.role not in {"HUMAN_OR_DOMAIN_ASSERTION", "human_assertion"}:
            raise ValueError("human assertions require an explicit human role")
        return self


class EvidenceBreakdownView(_SourceModel):
    visualization_id: str = Field(min_length=1)
    scope: VisualizationScope
    subject_ref: str = Field(min_length=1)
    subject_label: VisualLabel
    items: tuple[EvidenceVisualItem, ...]
    total_item_count: int = Field(ge=0)
    hidden_item_count: int = Field(ge=0)
    truncated: bool
    accessible_summary: str = Field(min_length=1)
    legend: tuple[str, ...] = Field(min_length=1)
    content_hash: str = Field(min_length=1)

    @model_validator(mode="after")
    def evidence_counts_reconcile(self) -> "EvidenceBreakdownView":
        if len(self.items) + self.hidden_item_count != self.total_item_count:
            raise ValueError("evidence view must account for every evidence item")
        return self


class LineageViewInput(_SourceModel):
    graph: VisualizationGraphInput
    direction: VisualDirection = VisualDirection.BOTH
    focus_ref: str | None = None
    max_depth: int = Field(default=6, ge=0, le=12)


class QualityHeatmapCellInput(_SourceModel):
    cell_id: str = Field(min_length=1)
    subject_ref: str = Field(min_length=1)
    dimension: str = Field(min_length=1)
    severity: str = Field(min_length=1)
    numerator: int | None = Field(default=None, ge=0)
    denominator: int | None = Field(default=None, ge=0)
    state: VisualState
    observation_scope: VisualObservationScope
    reliability: VisualReliabilityState
    evidence_state: VisualEvidenceState
    provenance_refs: tuple[str, ...] = Field(min_length=1)
    accessible_description: str = Field(min_length=1)

    @model_validator(mode="after")
    def denominator_is_explicit(self) -> "QualityHeatmapCellInput":
        if self.denominator is not None and self.numerator is not None and self.numerator > self.denominator:
            raise ValueError("quality numerator cannot exceed denominator")
        unavailable = {
            VisualState.NOT_EVALUATED,
            VisualState.NOT_APPLICABLE,
            VisualState.UNAVAILABLE,
            VisualState.PRIVACY_BLOCKED,
        }
        if self.state not in unavailable and (self.numerator is None or self.denominator is None):
            raise ValueError("measured quality cells require numerator and denominator")
        if self.numerator is not None and self.denominator is None:
            raise ValueError("quality numerator requires its denominator")
        if self.state in {VisualState.INCOMPLETE, VisualState.NOT_EVALUATED} and self.observation_scope is VisualObservationScope.FULL_SNAPSHOT:
            raise ValueError("incomplete/not-evaluated quality cells cannot claim full snapshot scope")
        return self


class QualityHeatmapView(_SourceModel):
    visualization_id: str = Field(min_length=1)
    scope: VisualizationScope
    cells: tuple[QualityHeatmapCellInput, ...]
    dimensions: tuple[str, ...] = Field(min_length=1)
    accessible_summary: str = Field(min_length=1)
    legend: tuple[str, ...] = Field(min_length=1)
    content_hash: str = Field(min_length=1)


class ProfilePoint(_SourceModel):
    bucket: str = Field(min_length=1)
    value: float = 0.0
    count: int = Field(ge=0)


class ProfileDistributionInput(_SourceModel):
    metric_id: str = Field(min_length=1)
    column_ref: str = Field(min_length=1)
    metric_kind: str = Field(min_length=1)
    points: tuple[ProfilePoint, ...] = ()
    chart_kind: VisualChartKind
    ordered: bool = False
    observation_scope: VisualObservationScope
    reliability: VisualReliabilityState
    evidence_state: VisualEvidenceState
    sensitivity_state: VisualSensitivityState
    provenance_refs: tuple[str, ...] = Field(min_length=1)
    accessible_description: str = Field(min_length=1)

    @model_validator(mode="after")
    def chart_semantics_are_safe(self) -> "ProfileDistributionInput":
        if self.chart_kind is VisualChartKind.HISTOGRAM and not self.ordered:
            raise ValueError("histograms require ordered numeric buckets")
        if self.chart_kind is VisualChartKind.BOX_PLOT and not self.ordered:
            raise ValueError("box plots require ordered numeric observations")
        if self.chart_kind is VisualChartKind.BAR and not self.ordered and self.metric_kind.lower() not in {"category", "categorical"}:
            raise ValueError("unordered bar charts are restricted to categorical profile metrics")
        if self.sensitivity_state is VisualSensitivityState.REDACTED and self.points:
            raise ValueError("redacted profile values cannot be transferred to chart points")
        return self


class ProfileDistributionView(_SourceModel):
    visualization_id: str = Field(min_length=1)
    scope: VisualizationScope
    metric_id: str = Field(min_length=1)
    column_ref: str = Field(min_length=1)
    chart_kind: VisualChartKind
    points: tuple[ProfilePoint, ...]
    observation_scope: VisualObservationScope
    reliability: VisualReliabilityState
    evidence_state: VisualEvidenceState
    sensitivity_state: VisualSensitivityState
    accessible_summary: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)


class ValidationVisualCheckInput(_SourceModel):
    check_id: str = Field(min_length=1)
    subject_ref: str = Field(min_length=1)
    status: Literal["PASS", "FAIL", "REVIEW_REQUIRED", "NOT_EVALUATED", "NOT_APPLICABLE"]
    severity: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    required: bool = True
    expected: str | None = None
    observed: str | None = None
    expected_display_state: ValidationDisplayState = ValidationDisplayState.UNAVAILABLE
    observed_display_state: ValidationDisplayState = ValidationDisplayState.UNAVAILABLE
    discrepancy_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def display_value_state_is_explicit(self) -> "ValidationVisualCheckInput":
        shown_values = {
            "PASS",
            "FAIL",
            "REVIEW_REQUIRED",
            "NOT_EVALUATED",
            "NOT_APPLICABLE",
            "True",
            "False",
        }
        masked_values = {
            "<REDACTED>",
            "<REDACTED_EMAIL>",
            "<REDACTED_PHONE>",
            "<REDACTED_SECRET>",
            "[STRUCTURED_VALUE_REDACTED]",
            "[SCALAR_VALUE_REDACTED]",
            "[PRIVACY_BLOCKED]",
        }
        numeric_pattern = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
        for label, value, state in (
            ("expected", self.expected, self.expected_display_state),
            ("observed", self.observed, self.observed_display_state),
        ):
            if state is ValidationDisplayState.UNAVAILABLE:
                if value is not None:
                    raise ValueError(f"{label} must be absent when its display state is UNAVAILABLE")
            elif state is ValidationDisplayState.SHOWN:
                if value is None or (value not in shown_values and numeric_pattern.fullmatch(value) is None):
                    raise ValueError(f"{label} SHOWN state is limited to safe status or numeric primitives")
            elif value not in masked_values:
                raise ValueError(f"{label} {state.value} state requires an explicit redaction placeholder")
        return self


class ValidationReportVisualizationBinding(_SourceModel):
    """Exact binding manifest for an authoritative validation projection."""

    validation_report_id: str = Field(min_length=1)
    validation_report_content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    validation_policy_id: str = Field(min_length=1)
    validation_policy_version: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    complete_check_ids: tuple[str, ...] = Field(min_length=1)
    required_check_ids: tuple[str, ...] = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def binding_is_closed(self) -> "ValidationReportVisualizationBinding":
        if len(set(self.complete_check_ids)) != len(self.complete_check_ids):
            raise ValueError("validation visualization complete check IDs must be unique")
        if len(set(self.required_check_ids)) != len(self.required_check_ids):
            raise ValueError("validation visualization required check IDs must be unique")
        if not set(self.required_check_ids).issubset(self.complete_check_ids):
            raise ValueError("validation visualization required checks must be in the complete check universe")
        return self

    @classmethod
    def from_report(cls, report: ValidationReport) -> "ValidationReportVisualizationBinding":
        return cls(
            validation_report_id=report.report_id,
            validation_report_content_hash=report.content_hash,
            validation_policy_id=report.policy.policy_id,
            validation_policy_version=report.policy.policy_version,
            run_id=report.run_id,
            complete_check_ids=tuple(sorted(item.check_id for item in report.checks)),
            required_check_ids=tuple(sorted(report.policy.required_check_ids)),
            provenance_refs=tuple(sorted(report.provenance_refs)),
        )


class ValidationView(_SourceModel):
    """Exploratory validation subset; never an authoritative G6 result."""

    visualization_id: str = Field(min_length=1)
    scope: VisualizationScope
    checks: tuple[ValidationVisualCheckInput, ...] = Field(min_length=1)
    overall_status: Literal["PASS", "FAIL", "REVIEW_REQUIRED", "NOT_EVALUATED"]
    g6_eligible: bool
    authoritative: Literal[False] = False
    accessible_summary: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)

    @model_validator(mode="after")
    def exploratory_status_is_safe(self) -> "ValidationView":
        if self.authoritative is not False:
            raise ValueError("subset validation views cannot be authoritative")
        if self.overall_status == "PASS" or self.g6_eligible:
            raise ValueError("exploratory validation subsets cannot claim global PASS or G6 eligibility")
        return self


class ValidationReportVisualization(_SourceModel):
    """Trusted, complete projection of a validated ValidationReport."""

    visualization_id: str = Field(min_length=1)
    scope: VisualizationScope
    validation_report_id: str = Field(min_length=1)
    validation_report_content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    validation_policy_id: str = Field(min_length=1)
    validation_policy_version: str = Field(min_length=1)
    complete_check_ids: tuple[str, ...] = Field(min_length=1)
    required_check_ids: tuple[str, ...] = Field(min_length=1)
    checks: tuple[ValidationVisualCheckInput, ...] = Field(min_length=1)
    overall_status: Literal["PASS", "FAIL", "REVIEW_REQUIRED", "NOT_EVALUATED"]
    g6_status: Literal["PASS", "FAIL", "PENDING"]
    g6_eligible: bool
    provenance_refs: tuple[str, ...] = Field(min_length=1)
    display_context: Literal["UI_PREVIEW"] = "UI_PREVIEW"
    authoritative: Literal[True] = True
    accessible_summary: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def projection_is_complete_and_consistent(self) -> "ValidationReportVisualization":
        check_ids = [item.check_id for item in self.checks]
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("authoritative validation visualization check IDs must be unique")
        if len(self.complete_check_ids) != len(set(self.complete_check_ids)):
            raise ValueError("authoritative validation visualization complete check IDs must be unique")
        if len(self.required_check_ids) != len(set(self.required_check_ids)):
            raise ValueError("authoritative validation visualization required check IDs must be unique")
        if set(check_ids) != set(self.complete_check_ids):
            raise ValueError("authoritative validation visualization must include the complete check universe")
        if not set(self.required_check_ids).issubset(self.complete_check_ids):
            raise ValueError("authoritative required check IDs must be in the complete check universe")
        if any(item.check_id in self.required_check_ids and not item.required for item in self.checks):
            raise ValueError("authoritative required checks must remain marked required")
        expected_overall = {"PASS": "PASS", "FAIL": "FAIL", "PENDING": "REVIEW_REQUIRED"}[self.g6_status]
        if self.overall_status != expected_overall:
            raise ValueError("authoritative validation status must preserve the report gate status")
        if self.g6_eligible != (self.g6_status == "PASS"):
            raise ValueError("authoritative G6 eligibility must preserve the report gate")
        return self


class AnalyticalMeasureVisualInput(_SourceModel):
    """Untrusted legacy input retained only for negative/exploratory controls."""

    measure_ref: str = Field(min_length=1)
    fact_ref: str = Field(min_length=1)
    label: VisualLabel
    aggregation_class: VisualAggregationClass
    aggregation_rule: str = Field(min_length=1)
    unit_semantics: str = Field(min_length=1)
    currency_semantics: str = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def no_default_sum_for_nonadditive(self) -> "AnalyticalMeasureVisualInput":
        if self.aggregation_class is VisualAggregationClass.NON_ADDITIVE and self.aggregation_rule.upper() in {"SUM", "DEFAULT_SUM"}:
            raise ValueError("non-additive measure cannot be visualized with additive SUM semantics")
        return self


class AnalyticalMeasureVisualization(_SourceModel):
    """Trusted projection of a reviewed MeasureSpec and AnalyticalPlan."""

    visualization_id: str = Field(min_length=1)
    measure_id: str = Field(min_length=1)
    fact_id: str = Field(min_length=1)
    grain_spec_id: str | None = None
    label: VisualLabel
    measure_semantic_content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    analytical_plan_id: str = Field(pattern=r"^aplan_[a-f0-9]{32}$")
    analytical_plan_version: str = Field(min_length=1)
    analytical_plan_content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    analytical_spec_package_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    fact_semantic_content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    review_decision_id: str = Field(min_length=1)
    review_decision_content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    review_checkpoint_id: str = Field(min_length=1)
    review_decision_status: str = Field(min_length=1)
    review_applicability_fingerprint: str = Field(min_length=1)
    aggregation_class: VisualAggregationClass
    aggregation_rule: str = Field(min_length=1)
    unit_semantics: str = Field(min_length=1)
    currency_semantics: str = Field(min_length=1)
    logical_type: str = Field(min_length=1)
    domain_assertion_refs: tuple[str, ...] = Field(min_length=1)
    provenance_refs: tuple[str, ...] = Field(min_length=1)
    measure_review_state: str = Field(min_length=1)
    plan_review_state: str = Field(min_length=1)
    fact_review_state: str | None = None
    authoritative: Literal[True] = True
    accessible_summary: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


def visual_node_id(visualization_id: str, domain_ref: str) -> str:
    return stable_id("vnode", {"visualization_id": visualization_id, "domain_ref": domain_ref})


def visual_edge_id(visualization_id: str, domain_ref: str, source_ref: str, target_ref: str) -> str:
    return stable_id("vedge", {"visualization_id": visualization_id, "domain_ref": domain_ref, "source_ref": source_ref, "target_ref": target_ref})


def visualization_content_hash(value: Any) -> str:
    return stable_digest(value)
