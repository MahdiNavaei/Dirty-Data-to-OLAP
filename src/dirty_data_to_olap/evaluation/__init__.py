"""Offline, project-owned inference evaluation sidecar."""

from .contracts import InferenceValidityAssessment, InferenceValidityStatus
from .metrics import (
    average_precision,
    binary_classification_metrics,
    entity_resolution_metrics,
    ranking_metrics,
)

__all__ = [
    "InferenceValidityAssessment",
    "InferenceValidityStatus",
    "average_precision",
    "binary_classification_metrics",
    "entity_resolution_metrics",
    "ranking_metrics",
]
