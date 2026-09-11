"""Offline, project-owned inference evaluation sidecar."""

from .contracts import FormalGateStatus, InferenceValidityAssessment, InferenceValidityStatus, ProviderEvaluationBinding
from .provider_binding import bind_provider_output
from .metrics import (
    average_precision,
    binary_classification_metrics,
    entity_resolution_metrics,
    ranking_metrics,
)

__all__ = [
    "InferenceValidityAssessment",
    "InferenceValidityStatus",
    "FormalGateStatus",
    "ProviderEvaluationBinding",
    "bind_provider_output",
    "average_precision",
    "binary_classification_metrics",
    "entity_resolution_metrics",
    "ranking_metrics",
]
