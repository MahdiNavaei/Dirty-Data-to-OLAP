"""Project-owned, local-first observability contracts.

The application imports this boundary rather than provider-native logging,
metrics, or tracing objects.  The default implementation is deterministic
and in-memory; exporters are optional and never authoritative.
"""

from .telemetry import (
    ALLOWED_METRIC_LABELS,
    METRIC_DEFINITIONS,
    BoundedErrorClass,
    CorrelationContext,
    DiagnosticBundle,
    FORBIDDEN_METRIC_LABELS,
    InMemoryTelemetrySink,
    MetricDefinition,
    MetricSample,
    NoopTelemetrySink,
    StructuredEvent,
    TelemetryClient,
    TelemetrySink,
    TraceSpan,
    classify_error,
    redact_value,
)

__all__ = [
    "ALLOWED_METRIC_LABELS",
    "METRIC_DEFINITIONS",
    "BoundedErrorClass",
    "CorrelationContext",
    "DiagnosticBundle",
    "FORBIDDEN_METRIC_LABELS",
    "InMemoryTelemetrySink",
    "MetricDefinition",
    "MetricSample",
    "NoopTelemetrySink",
    "StructuredEvent",
    "TelemetryClient",
    "TelemetrySink",
    "TraceSpan",
    "classify_error",
    "redact_value",
]
