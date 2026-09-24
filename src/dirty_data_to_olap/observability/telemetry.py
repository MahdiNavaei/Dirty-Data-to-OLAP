"""Safe project-owned telemetry for the local reference runtime.

This module deliberately does not depend on Prometheus or OpenTelemetry.
It provides typed records and exporter adapters that can be replaced later
without making third-party objects part of application contracts.  The
default sink is in-memory and bounded; an exporter failure is recorded as a
diagnostic fact and is never raised into the product operation.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
import hashlib
import re
from time import monotonic
from typing import Any, Iterator, Mapping, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator



_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_HEX = re.compile(r"^[a-f0-9]{8,128}$")
_EMAIL = re.compile(r"\b[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\b")
_SECRET = re.compile(
    r"(?i)(?:password|passwd|secret|token|api[_-]?key|authorization|credential|connection[_-]?string|raw[_-]?dsn)"
    r"\s*(?:=|:|)\s*[^\s,;]+"
)
_URL_CREDENTIAL = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)([^/@\s:]+):([^/@\s]+)@")
_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|/)(?:[^\s\\/]+[\\/])+[^\s]+")
_CANARY = re.compile(r"(?i)(?:step\d+_[a-z0-9_]+_canary|secret[_-]?canary|pii[_-]?canary)")
_EXCEPTION_DETAIL_SECRET = re.compile(
    r"(?i)(?:password|passwd|secret|token|api[_-]?key|authorization|credential|connection[_-]?string|raw[_-]?dsn)"
    r"\s*(?:=|:)?\s*[^\s,;]+"
)
_EXCEPTION_DETAIL_UNSAFE = re.compile(r"(?i)(?:raw[_-]?row|stack[_-]?trace|traceback|exception[_-]?text|request[_-]?body)")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def redact_value(value: Any) -> Any:
    """Recursively redact secret/PII-like strings before they reach a sink."""

    if isinstance(value, Mapping):
        return {str(key): redact_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [redact_value(item) for item in value]
    if not isinstance(value, str):
        return value
    text = _URL_CREDENTIAL.sub(r"\1<redacted>:<redacted>@", value)
    text = _SECRET.sub(lambda match: f"{match.group(0).split(':', 1)[0].split('=', 1)[0]}=<redacted>", text)
    text = _EMAIL.sub("<redacted-email>", text)
    text = _CANARY.sub("<redacted-canary>", text)
    # Paths are useful as a category but not as a diagnostic payload.  Do not
    # redact stable logical keys such as runs/<id>/artifacts/<id>.
    if "\\" in text or (":" in text and _PATH.search(text)):
        text = _PATH.sub("<redacted-path>", text)
    return text[:512]


def safe_exception_detail(error: BaseException) -> str:
    """Return bounded durable exception detail without raw traceback or secrets."""

    detail = str(error) or repr(error)
    detail = str(redact_value(detail))
    detail = "".join(character if character.isprintable() else " " for character in detail)
    detail = re.sub(r"\s+", " ", detail).strip()
    detail = _EXCEPTION_DETAIL_SECRET.sub("<redacted>", detail)
    detail = _EXCEPTION_DETAIL_UNSAFE.sub("<redacted-detail>", detail)
    return detail[:512] or "<empty>"


class BoundedErrorClass(str, Enum):
    AUTHORIZATION = "AUTHORIZATION"
    SOURCE_CONNECTION = "SOURCE_CONNECTION"
    SOURCE_TIMEOUT = "SOURCE_TIMEOUT"
    SOURCE_PERMISSION = "SOURCE_PERMISSION"
    SOURCE_FORMAT = "SOURCE_FORMAT"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    VALIDATION = "VALIDATION"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
    LEASE_LOST = "LEASE_LOST"
    STALE_WORKER = "STALE_WORKER"
    CANCELLED = "CANCELLED"
    MATERIALIZATION = "MATERIALIZATION"
    INTERNAL = "INTERNAL"
    UNKNOWN = "UNKNOWN"


def classify_error(code: str | None = None, classification: str | None = None, error: BaseException | None = None) -> BoundedErrorClass:
    """Map product failure vocabulary to a bounded operational class."""

    text = " ".join(item for item in (code or "", classification or "", type(error).__name__ if error else "") if item).upper()
    if any(item in text for item in ("AUTH", "FORBIDDEN", "UNAUTH")):
        return BoundedErrorClass.AUTHORIZATION
    if "CANCEL" in text:
        return BoundedErrorClass.CANCELLED
    if "REVIEW" in text:
        return BoundedErrorClass.REVIEW_REQUIRED
    if any(item in text for item in ("LEASE", "FENCE", "CONCURRENCY", "STALE")):
        return BoundedErrorClass.LEASE_LOST if "LEASE" in text or "FENCE" in text else BoundedErrorClass.STALE_WORKER
    if "TIMEOUT" in text:
        return BoundedErrorClass.PROVIDER_TIMEOUT if "PROVIDER" in text or "ENGINE" in text else BoundedErrorClass.SOURCE_TIMEOUT
    if any(item in text for item in ("SOURCE", "CONNECTION", "ACCESS", "EXTRACTION")):
        if "PERMISSION" in text or "ACCESS" in text:
            return BoundedErrorClass.SOURCE_PERMISSION
        if "FORMAT" in text or "PARSE" in text:
            return BoundedErrorClass.SOURCE_FORMAT
        return BoundedErrorClass.SOURCE_CONNECTION
    if any(item in text for item in ("PROVIDER", "DESBORDANTE", "DATAPROFILER", "VALENTINE", "SPLINK")):
        return BoundedErrorClass.PROVIDER_UNAVAILABLE
    if any(item in text for item in ("VALIDATION", "RECONCILIATION", "G6")):
        return BoundedErrorClass.VALIDATION
    if any(item in text for item in ("MATERIAL", "DUCKDB", "TARGET")):
        return BoundedErrorClass.MATERIALIZATION
    if "RETRY" in text:
        return BoundedErrorClass.RETRY_EXHAUSTED
    return BoundedErrorClass.INTERNAL if error is not None else BoundedErrorClass.UNKNOWN


class _TelemetryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class CorrelationContext(_TelemetryModel):
    """Portable correlation, safe to reconstruct after a process restart."""

    trace_id: str
    span_id: str | None = None
    parent_span_id: str | None = None
    request_id: str | None = None
    project_id: str | None = None
    run_id: str | None = None
    stage_id: str | None = None
    job_id: str | None = None
    attempt_id: str | None = None
    command_id: str | None = None
    artifact_id: str | None = None
    checkpoint: str | None = None

    @field_validator("trace_id", "span_id", "parent_span_id")
    @classmethod
    def valid_trace_ids(cls, value: str | None) -> str | None:
        if value is not None and not _HEX.fullmatch(value):
            raise ValueError("trace identifiers must be bounded hexadecimal strings")
        return value

    @field_validator("request_id", "project_id", "run_id", "stage_id", "job_id", "attempt_id", "command_id", "artifact_id", "checkpoint")
    @classmethod
    def valid_context_ids(cls, value: str | None) -> str | None:
        if value is not None and not _TOKEN.fullmatch(value):
            raise ValueError("correlation values must be bounded safe identifiers")
        return value


class StructuredEvent(_TelemetryModel):
    schema_version: str = "1.0"
    timestamp: datetime = Field(default_factory=utc_now)
    level: str = Field(pattern=r"^(DEBUG|INFO|WARNING|ERROR)$")
    event_name: str = Field(min_length=1, max_length=96, pattern=r"^[a-z][a-z0-9_.-]{0,95}$")
    component: str = Field(min_length=1, max_length=96, pattern=r"^[a-z][a-z0-9_.-]{0,95}$")
    operation: str = Field(min_length=1, max_length=96, pattern=r"^[a-z][a-z0-9_.-]{0,95}$")
    correlation: CorrelationContext
    status: str | None = Field(default=None, max_length=32)
    error_class: BoundedErrorClass | None = None
    retry_count: int | None = Field(default=None, ge=0, le=1000)
    duration_ms: float | None = Field(default=None, ge=0, le=86_400_000)
    details: Mapping[str, Any] = Field(default_factory=dict)

    @field_validator("status")
    @classmethod
    def status_is_safe(cls, value: str | None) -> str | None:
        return None if value is None else redact_value(value)

    @field_validator("details")
    @classmethod
    def details_are_safe(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return redact_value(dict(value))


class MetricDefinition(_TelemetryModel):
    name: str = Field(pattern=r"^ddo_[a-z0-9_]+$")
    metric_type: str = Field(pattern=r"^(counter|gauge|histogram)$")
    unit: str = Field(min_length=1, max_length=32)
    description: str = Field(min_length=1, max_length=256)
    aggregation: str = Field(min_length=1, max_length=256)
    allowed_labels: tuple[str, ...] = ()
    cardinality_rationale: str = Field(min_length=1, max_length=256)
    instrumentation_point: str = Field(min_length=1, max_length=256)


FORBIDDEN_METRIC_LABELS = frozenset({
    "run_id", "project_id", "source_id", "job_id", "attempt_id", "artifact_id",
    "table_name", "column_name", "filename", "error_message", "sql_text",
})
ALLOWED_METRIC_LABELS = frozenset({
    "stage_kind", "job_status", "job_kind", "run_status", "adapter_kind", "source_kind",
    "engine_family", "error_class", "retry_reason_class", "review_checkpoint", "result_class",
    "queue_state", "activity", "signal", "exporter", "observation",
})

_METRIC_LABEL_VALUES = {
    "run_status": frozenset({"CREATED", "RUNNING", "NEEDS_REVIEW", "BLOCKED", "FAILED", "CANCELLED", "SUCCEEDED", "OTHER"}),
    "stage_kind": frozenset({"SOURCE_DISCOVERY", "SOURCE_SNAPSHOT_STAGE", "PROFILING", "DEPENDENCY_DISCOVERY", "QUALITY_ANALYSIS", "EVIDENCE_FUSION", "CANONICAL_HYPOTHESES", "CANONICAL_IDENTITY_PREPARATION", "CANONICAL_FINALIZATION", "ANALYTICAL_PLANNING", "COMPILATION", "MATERIALIZATION", "SEMANTIC_MODELING", "VALIDATION_RECONCILIATION", "OTHER"}),
    "job_kind": frozenset({"COMMAND", "STAGE", "OTHER"}),
    "job_status": frozenset({"QUEUED", "RUNNING", "RETRY_WAIT", "NEEDS_REVIEW", "BLOCKED", "FAILED", "CANCELLED", "SUCCEEDED", "OTHER"}),
    "result_class": frozenset({"SUCCEEDED", "FAILED", "BLOCKED", "CANCELLED", "NEEDS_REVIEW", "RETRY_WAIT", "ACCEPTED", "REJECTED", "DEFERRED", "SKIPPED", "INVALIDATED", "OK", "ERROR", "OTHER"}),
    "retry_reason_class": frozenset({*(item.value for item in BoundedErrorClass), "UNKNOWN"}),
    "review_checkpoint": frozenset({"REVIEW_EVIDENCE_DECISIONS", "REVIEW_CANONICAL_IDENTITY", "REVIEW_ANALYTICAL_PLAN", "REVIEW_MATERIALIZATION_PLAN", "OTHER"}),
    "adapter_kind": frozenset({"file_source", "dlt_sql_source", "dataprofiler", "desbordante", "valentine", "splink", "duckdb", "product_runtime", "other"}),
    "queue_state": frozenset({"QUEUED", "RUNNING", "RETRY_WAIT", "NEEDS_REVIEW", "BLOCKED", "FAILED", "CANCELLED", "SUCCEEDED", "OTHER"}),
    "activity": frozenset({"idle", "claimed", "attempted", "finalized", "OTHER"}),
    "signal": frozenset({"log", "metric", "trace", "OTHER"}),
    "exporter": frozenset({"external", "OTHER"}),
    "observation": frozenset({"observed", "sampled", "OTHER"}),
    "source_kind": frozenset({"file", "sql", "api", "OTHER"}),
    "engine_family": frozenset({"duckdb", "python", "OTHER"}),
}


METRIC_DEFINITIONS: tuple[MetricDefinition, ...] = (
    MetricDefinition(name="ddo_run_lifecycle_total", metric_type="counter", unit="runs", description="Run lifecycle transitions observed by status.", aggregation="sum of transitions by bounded run status", allowed_labels=("run_status",), cardinality_rationale="RunStatus is a finite project enum.", instrumentation_point="worker run-status transition"),
    MetricDefinition(name="ddo_stage_duration_seconds", metric_type="histogram", unit="seconds", description="Observed duration of a product stage boundary.", aggregation="histogram of stage boundary durations", allowed_labels=("stage_kind", "result_class"), cardinality_rationale="Stage kind is mapped to the finite architecture stage set; result class is bounded.", instrumentation_point="LocalProductStageHandlers stage boundary"),
    MetricDefinition(name="ddo_job_lifecycle_total", metric_type="counter", unit="jobs", description="Durable job lifecycle transitions.", aggregation="sum of worker job transitions", allowed_labels=("job_kind", "job_status"), cardinality_rationale="JobKind and JobStatus are finite enums.", instrumentation_point="JobWorker claim/finalization"),
    MetricDefinition(name="ddo_job_execution_duration_seconds", metric_type="histogram", unit="seconds", description="Observed worker attempt duration.", aggregation="histogram from attempt start through finalization", allowed_labels=("result_class",), cardinality_rationale="Result class is bounded.", instrumentation_point="JobWorker stage execution boundary"),
    MetricDefinition(name="ddo_job_retries_total", metric_type="counter", unit="retries", description="Retry attempts scheduled by classification.", aggregation="sum of bounded retry reasons", allowed_labels=("retry_reason_class",), cardinality_rationale="Retry reason class is a bounded operational class.", instrumentation_point="JobWorker stage finalization"),
    MetricDefinition(name="ddo_review_lifecycle_total", metric_type="counter", unit="reviews", description="Review pause, decision and resume transitions.", aggregation="sum by checkpoint and result class", allowed_labels=("review_checkpoint", "result_class"), cardinality_rationale="The four review checkpoints and result classes are bounded.", instrumentation_point="review worker and backend boundaries"),
    MetricDefinition(name="ddo_adapter_operation_duration_seconds", metric_type="histogram", unit="seconds", description="Observed project-owned adapter/stage operation duration.", aggregation="histogram by adapter family and result class", allowed_labels=("adapter_kind", "result_class"), cardinality_rationale="Adapter family is a finite configured taxonomy.", instrumentation_point="LocalProductStageHandlers adapter boundary"),
    MetricDefinition(name="ddo_source_records_observed_total", metric_type="counter", unit="records", description="Authoritative source records reported by the snapshot stage.", aggregation="sum of already-known observed records only", allowed_labels=("observation",), cardinality_rationale="Observation semantics are bounded and no source identity is a label.", instrumentation_point="SOURCE_SNAPSHOT_STAGE result metadata"),
    MetricDefinition(name="ddo_validation_outcomes_total", metric_type="counter", unit="validations", description="Validation/reconciliation outcomes.", aggregation="sum by bounded result class", allowed_labels=("result_class",), cardinality_rationale="Result class is bounded.", instrumentation_point="VALIDATION_RECONCILIATION stage result"),
    MetricDefinition(name="ddo_queue_jobs", metric_type="gauge", unit="jobs", description="Observed local queue jobs by state.", aggregation="point-in-time bounded local queue snapshot", allowed_labels=("queue_state",), cardinality_rationale="Queue state is a finite JobStatus projection.", instrumentation_point="JobWorker queue snapshot"),
    MetricDefinition(name="ddo_queue_oldest_runnable_age_seconds", metric_type="gauge", unit="seconds", description="Age of the oldest runnable local job in an observed queue snapshot.", aggregation="maximum age among runnable jobs", allowed_labels=(), cardinality_rationale="No identifiers are exported as labels.", instrumentation_point="JobWorker queue snapshot"),
    MetricDefinition(name="ddo_worker_activity_total", metric_type="counter", unit="events", description="Bounded worker activity outcomes.", aggregation="sum by activity kind", allowed_labels=("activity",), cardinality_rationale="Activity is a finite worker taxonomy.", instrumentation_point="JobWorker run_once"),
    MetricDefinition(name="ddo_telemetry_exporter_failures_total", metric_type="counter", unit="failures", description="Exporter failures captured without affecting product state.", aggregation="sum by signal and exporter family", allowed_labels=("signal", "exporter"), cardinality_rationale="Signal and exporter values are bounded controlled taxonomies.", instrumentation_point="TelemetryClient exporter guard"),
)


_METRIC_BY_NAME = {item.name: item for item in METRIC_DEFINITIONS}


class MetricSample(_TelemetryModel):
    name: str
    value: float = Field(ge=0)
    labels: Mapping[str, str] = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_metric_labels(self) -> "MetricSample":
        definition = _METRIC_BY_NAME.get(self.name)
        if definition is None:
            raise ValueError("metric is not defined by the project telemetry contract")
        if set(self.labels) != set(self.labels) & set(definition.allowed_labels):
            raise ValueError("metric contains a label outside its definition")
        if FORBIDDEN_METRIC_LABELS.intersection(self.labels):
            raise ValueError("metric contains a prohibited high-cardinality label")
        if not set(self.labels) <= ALLOWED_METRIC_LABELS:
            raise ValueError("metric label is not in the bounded label taxonomy")
        if any(not _TOKEN.fullmatch(str(key)) or not _TOKEN.fullmatch(str(value)) for key, value in self.labels.items()):
            raise ValueError("metric labels must be bounded safe tokens")
        if any(str(value) not in _METRIC_LABEL_VALUES.get(str(key), frozenset()) for key, value in self.labels.items()):
            raise ValueError("metric label value is outside the bounded taxonomy")
        return self


class TraceSpan(_TelemetryModel):
    schema_version: str = "1.0"
    name: str = Field(min_length=1, max_length=96, pattern=r"^[a-z][a-z0-9_.-]{0,95}$")
    correlation: CorrelationContext
    started_at: datetime
    finished_at: datetime
    duration_ms: float = Field(ge=0, le=86_400_000)
    status: str = Field(pattern=r"^(OK|ERROR|UNSET)$")
    attributes: Mapping[str, Any] = Field(default_factory=dict)

    @field_validator("attributes")
    @classmethod
    def attributes_are_safe(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return redact_value(dict(value))


class TelemetrySink(Protocol):
    def emit_event(self, event: StructuredEvent) -> None: ...
    def observe_metric(self, sample: MetricSample) -> None: ...
    def record_span(self, span: TraceSpan) -> None: ...


class InMemoryTelemetrySink:
    """Deterministic sink used by local operation and executable tests."""

    def __init__(self, *, max_records: int = 10_000) -> None:
        if max_records < 1:
            raise ValueError("max_records must be positive")
        self.max_records = max_records
        self.events: list[StructuredEvent] = []
        self.metrics: list[MetricSample] = []
        self.spans: list[TraceSpan] = []

    def _append(self, collection: list[Any], value: Any) -> None:
        collection.append(value)
        if len(collection) > self.max_records:
            del collection[: len(collection) - self.max_records]

    def emit_event(self, event: StructuredEvent) -> None:
        self._append(self.events, event)

    def observe_metric(self, sample: MetricSample) -> None:
        self._append(self.metrics, sample)

    def record_span(self, span: TraceSpan) -> None:
        self._append(self.spans, span)


class NoopTelemetrySink:
    def emit_event(self, event: StructuredEvent) -> None:
        del event

    def observe_metric(self, sample: MetricSample) -> None:
        del sample

    def record_span(self, span: TraceSpan) -> None:
        del span


class _SpanScope:
    def __init__(self, client: "TelemetryClient", name: str, context: CorrelationContext, attributes: Mapping[str, Any]) -> None:
        self.client = client
        self.name = name
        self.context = context
        self.attributes = attributes
        self.started_at = client.clock()
        self.started_mono = monotonic()

    def __enter__(self) -> CorrelationContext:
        return self.context

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, _traceback: Any) -> bool:
        finished = self.client.clock()
        status = "ERROR" if exc is not None else "OK"
        span = TraceSpan(
            name=self.name,
            correlation=self.context,
            started_at=self.started_at,
            finished_at=finished,
            duration_ms=round(max(0.0, monotonic() - self.started_mono) * 1000, 3),
            status=status,
            attributes=self.attributes,
        )
        self.client.record_span(span)
        if exc is not None:
            self.client.emit_event(
                event_name="telemetry.operation_failed",
                component="observability",
                operation=self.name.replace(".", "_"),
                correlation=self.context,
                level="ERROR",
                error_class=classify_error(error=exc),
                details={"error_type": type(exc).__name__},
            )
        return False


class DiagnosticBundle(_TelemetryModel):
    schema_version: str = "1.0"
    bundle_kind: str = "RUN_DIAGNOSTIC"
    run_id: str
    project_id: str
    run_status: str
    configuration_fingerprint: str
    stages: tuple[Mapping[str, Any], ...] = ()
    jobs: tuple[Mapping[str, Any], ...] = ()
    attempts: tuple[Mapping[str, Any], ...] = ()
    reviews: tuple[Mapping[str, Any], ...] = ()
    artifacts: tuple[Mapping[str, Any], ...] = ()
    events: tuple[StructuredEvent, ...] = ()
    metric_names: tuple[str, ...] = ()
    trace_names: tuple[str, ...] = ()
    telemetry_degraded: bool = False
    degradation_notes: tuple[str, ...] = ()


class TelemetryClient:
    """Project-owned facade with exporter isolation and bounded records."""

    VERSION = "1.0"

    def __init__(
        self,
        *,
        sinks: Sequence[TelemetrySink] = (),
        enabled: bool = True,
        sample_rate: float = 1.0,
        clock: Any = utc_now,
        max_records: int = 10_000,
    ) -> None:
        if not 0 <= sample_rate <= 1:
            raise ValueError("sample_rate must be between zero and one")
        self.enabled = enabled
        self.sample_rate = sample_rate
        self.clock = clock
        self.memory = InMemoryTelemetrySink(max_records=max_records)
        self.sinks = tuple(sinks)
        self.exporter_failures = 0
        self.degradation_notes: list[str] = []

    def context(self, **ids: str | None) -> CorrelationContext:
        trace_seed = {key: value for key, value in ids.items() if value is not None and key not in {"trace_id", "span_id", "parent_span_id"}}
        trace_id = ids.get("trace_id") or hashlib.sha256(("ddo-trace:" + repr(sorted(trace_seed.items()))).encode()).hexdigest()[:32]
        return CorrelationContext(trace_id=trace_id, **{key: value for key, value in ids.items() if key != "trace_id"})

    def _export(self, signal: str, method: str, value: Any) -> None:
        for sink in self.sinks:
            try:
                getattr(sink, method)(value)
            except Exception as exc:  # exporter failures must not escape the product boundary
                self.exporter_failures += 1
                note = f"{signal} exporter degraded: {type(exc).__name__}"
                if note not in self.degradation_notes:
                    self.degradation_notes.append(note)
                # Do not emit through the failing sink or recursively log.
                self.memory.observe_metric(MetricSample(name="ddo_telemetry_exporter_failures_total", value=1, labels={"signal": signal, "exporter": "external"}))

    def emit_event(self, *, event_name: str, component: str, operation: str, correlation: CorrelationContext, level: str = "INFO", status: str | None = None, error_class: BoundedErrorClass | None = None, retry_count: int | None = None, duration_ms: float | None = None, details: Mapping[str, Any] | None = None) -> StructuredEvent:
        event = StructuredEvent(level=level, event_name=event_name, component=component, operation=operation, correlation=correlation, status=status, error_class=error_class, retry_count=retry_count, duration_ms=duration_ms, details=details or {})
        if self.enabled:
            self.memory.emit_event(event)
            self._export("log", "emit_event", event)
        return event

    def metric(self, name: str, value: float, *, labels: Mapping[str, str] = {}) -> MetricSample:
        sample = MetricSample(name=name, value=value, labels=dict(labels))
        if self.enabled:
            self.memory.observe_metric(sample)
            self._export("metric", "observe_metric", sample)
        return sample

    def record_span(self, span: TraceSpan) -> None:
        if not self.enabled:
            return
        digest = int(hashlib.sha256(span.correlation.trace_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        if digest <= self.sample_rate:
            self.memory.record_span(span)
            self._export("trace", "record_span", span)

    def span(self, name: str, correlation: CorrelationContext, *, attributes: Mapping[str, Any] = {}) -> _SpanScope:
        span_id = hashlib.sha256((correlation.trace_id + ":" + name + ":" + str(correlation.span_id)).encode()).hexdigest()[:16]
        scoped = correlation.model_copy(update={"span_id": span_id, "parent_span_id": correlation.span_id})
        return _SpanScope(self, name, scoped, attributes)

    def operation(self, *, correlation: CorrelationContext, event_name: str, component: str, operation: str, status: str, details: Mapping[str, Any] = {}, error_class: BoundedErrorClass | None = None, retry_count: int | None = None) -> None:
        self.emit_event(event_name=event_name, component=component, operation=operation, correlation=correlation, status=status, error_class=error_class, retry_count=retry_count, details=details)

    def observe_stage_result(self, *, correlation: CorrelationContext, stage_id: str, status: str, duration_seconds: float | None = None, metadata: Mapping[str, Any] = {}, failure_code: str | None = None, failure_classification: str | None = None) -> None:
        result_class = "FAILED" if status in {"FAILED", "BLOCKED"} else status
        self.metric("ddo_stage_duration_seconds", duration_seconds or 0, labels={"stage_kind": self.stage_kind(stage_id), "result_class": self.result_class(result_class)}) if duration_seconds is not None else None
        if stage_id == "SOURCE_SNAPSHOT_STAGE" and isinstance(metadata.get("rows"), str) and metadata["rows"].isdigit():
            self.metric("ddo_source_records_observed_total", float(metadata["rows"]), labels={"observation": "observed"})
        if stage_id == "VALIDATION_RECONCILIATION":
            self.metric("ddo_validation_outcomes_total", 1, labels={"result_class": self.result_class(result_class)})
        self.emit_event(event_name="stage.finished", component="product_runtime", operation="stage", correlation=correlation, status=status, error_class=classify_error(failure_code, failure_classification) if failure_code else None, details={"stage_kind": self.stage_kind(stage_id), "known_metadata": dict(metadata)})

    def queue_snapshot(self, jobs: Sequence[Any]) -> None:
        if not jobs:
            return
        counts: dict[str, int] = {}
        for job in jobs:
            state = getattr(getattr(job, "status", None), "value", str(getattr(job, "status", "UNKNOWN")))
            counts[state] = counts.get(state, 0) + 1
        for state, count in counts.items():
            self.metric("ddo_queue_jobs", float(count), labels={"queue_state": self.queue_state(state)})
        now = self.clock()
        runnable = [job for job in jobs if getattr(getattr(job, "status", None), "value", "") in {"QUEUED", "RETRY_WAIT"}]
        if runnable:
            ages = [(now - getattr(job, "created_at", now)).total_seconds() for job in runnable]
            self.metric("ddo_queue_oldest_runnable_age_seconds", max(0.0, max(ages)))

    @staticmethod
    def stage_kind(stage_id: str) -> str:
        known = {"SOURCE_DISCOVERY", "SOURCE_SNAPSHOT_STAGE", "PROFILING", "DEPENDENCY_DISCOVERY", "QUALITY_ANALYSIS", "EVIDENCE_FUSION", "CANONICAL_HYPOTHESES", "CANONICAL_IDENTITY_PREPARATION", "CANONICAL_FINALIZATION", "ANALYTICAL_PLANNING", "COMPILATION", "MATERIALIZATION", "SEMANTIC_MODELING", "VALIDATION_RECONCILIATION"}
        return stage_id if stage_id in known else "OTHER"

    @staticmethod
    def result_class(value: str) -> str:
        return value if value in {"SUCCEEDED", "FAILED", "BLOCKED", "CANCELLED", "NEEDS_REVIEW", "RETRY_WAIT", "OK", "ERROR"} else "OTHER"

    @staticmethod
    def queue_state(value: str) -> str:
        return value if value in {"QUEUED", "RUNNING", "RETRY_WAIT", "NEEDS_REVIEW", "BLOCKED", "FAILED", "CANCELLED", "SUCCEEDED"} else "OTHER"

    @contextmanager
    def traced_operation(self, name: str, correlation: CorrelationContext, *, attributes: Mapping[str, Any] = {}) -> Iterator[CorrelationContext]:
        with self.span(name, correlation, attributes=attributes) as child:
            yield child

    @contextmanager
    def adapter_operation(self, adapter_kind: str, operation: str, correlation: CorrelationContext, *, attributes: Mapping[str, Any] = {}) -> Iterator[CorrelationContext]:
        try:
            with self.span("adapter.operation", correlation, attributes={"adapter_kind": adapter_kind, "operation": operation, **dict(attributes)}) as child:
                yield child
        except Exception:
            raise

    def observe_adapter_operation(self, adapter_kind: str, duration_seconds: float, result_class: str) -> None:
        self.metric(
            "ddo_adapter_operation_duration_seconds",
            max(0.0, duration_seconds),
            labels={"adapter_kind": self.adapter_kind(adapter_kind), "result_class": self.result_class(result_class)},
        )

    @staticmethod
    def adapter_kind(value: str) -> str:
        known = {"file_source", "dlt_sql_source", "dataprofiler", "desbordante", "valentine", "splink", "duckdb", "product_runtime"}
        return value if value in known else "other"

    def build_diagnostic_bundle(self, *, run_id: str, control_store: Any, artifact_store: Any | None = None) -> DiagnosticBundle:
        run = control_store.get_run(run_id)
        if run is None:
            raise KeyError("run was not found")
        jobs = tuple(control_store.list_jobs(run_id=run_id, limit=10_000))
        attempts = tuple(control_store.list_stage_attempts(run_id=run_id, limit=10_000))
        reviews = tuple(control_store.list_review_history(run_id=run_id, limit=10_000))
        artifacts = tuple(control_store.list_artifacts(run_id=run_id, limit=10_000))
        stage_rows: dict[str, dict[str, Any]] = {}
        for job in jobs:
            if job.stage_id:
                stage_rows[job.stage_id] = {"stage_id": job.stage_id, "status": job.status.value, "job_id": job.job_id, "attempt_id": job.attempt_id, "retry_count": job.retry_count}
        safe_jobs = tuple({"job_id": item.job_id, "run_id": item.run_id, "job_kind": item.job_kind.value, "stage_id": item.stage_id, "attempt_id": item.attempt_id, "status": item.status.value, "retry_count": item.retry_count, "delivery_phase": item.delivery_phase.value, "failure_code": item.failure_code, "failure_classification": item.failure_classification.value if item.failure_classification else None} for item in jobs)
        safe_attempts = tuple({"attempt_id": item.attempt_id, "run_id": item.run_id, "stage_id": item.stage_id, "attempt_number": item.attempt_number, "status": item.status.value, "input_artifact_refs": item.input_artifact_refs, "output_artifact_refs": item.output_artifact_refs, "failure_code": item.failure_code, "delivery_phase": item.delivery_phase} for item in attempts)
        safe_reviews = tuple({"run_id": item.run_id, "subject_key": item.subject_key, "revision": item.revision, "checkpoint": item.decision.review_checkpoint_id.value, "decision": item.decision.decision.value} for item in reviews)
        safe_artifact_rows: list[dict[str, Any]] = []
        for item in artifacts:
            integrity_state = "NOT_CHECKED"
            if artifact_store is not None:
                try:
                    integrity_state = artifact_store.verify(item).state.value
                except Exception:
                    integrity_state = "NOT_CHECKED"
            safe_artifact_rows.append({"artifact_id": item.artifact_id, "artifact_kind": item.artifact_kind, "stage_id": item.stage_id, "attempt_id": item.attempt_id, "content_hash": item.content_hash, "byte_size": item.byte_size, "provenance_refs": item.provenance_refs, "integrity_state": integrity_state})
        safe_artifacts = tuple(safe_artifact_rows)
        run_events = tuple(item for item in self.memory.events if item.correlation.run_id == run_id)
        return DiagnosticBundle(run_id=run_id, project_id=run.project_id, run_status=run.status.value, configuration_fingerprint=run.configuration_fingerprint, stages=tuple(stage_rows.values()), jobs=safe_jobs, attempts=safe_attempts, reviews=safe_reviews, artifacts=safe_artifacts, events=run_events, metric_names=tuple(sorted({item.name for item in self.memory.metrics})), trace_names=tuple(sorted({item.name for item in self.memory.spans if item.correlation.run_id == run_id})), telemetry_degraded=bool(self.exporter_failures), degradation_notes=tuple(self.degradation_notes))


__all__ = [
    "ALLOWED_METRIC_LABELS", "METRIC_DEFINITIONS", "BoundedErrorClass", "CorrelationContext", "DiagnosticBundle", "FORBIDDEN_METRIC_LABELS", "InMemoryTelemetrySink", "MetricDefinition", "MetricSample", "NoopTelemetrySink", "StructuredEvent", "TelemetryClient", "TelemetrySink", "TraceSpan", "classify_error", "redact_value", "safe_exception_detail",
]
