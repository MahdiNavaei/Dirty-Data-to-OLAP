"""Application services for source lifecycle orchestration."""

from .dependency_discovery import DependencyDiscoveryService
from .entity_resolution import EntityResolutionService
from .evidence_fusion import EvidenceFusionService
from .canonical import CanonicalFinalizationService, CanonicalHypothesisService, CanonicalIdentityProposalService, CanonicalizationError
from .review_policy import ReviewCompatibilityError, ReviewPolicyService
from .validation import ValidationInputs, ValidationOutcome, ValidationService
from .platform import GateEvidenceService
from .distributed import (
    LargeJoinFanoutError,
    LocalPartitionExecutor,
    PartitionExecutorPort,
    PartitionMergeError,
    ScaleAuthorizationError,
    ScaleError,
    ScaleService,
)
from .visualization import VisualizationInputError, VisualizationService
from .jobs import BoundedWorkerPool, DurableExecutionSubmission, JobWorker, StageExecutorPort, StageHandlerRegistry, load_authoritative_execution_plan

__all__ = [
    "DependencyDiscoveryService",
    "EntityResolutionService",
    "EvidenceFusionService",
    "CanonicalFinalizationService",
    "CanonicalHypothesisService",
    "CanonicalIdentityProposalService",
    "CanonicalizationError",
    "ReviewCompatibilityError",
    "ReviewPolicyService",
    "ValidationInputs",
    "ValidationOutcome",
    "ValidationService",
    "GateEvidenceService",
    "LargeJoinFanoutError",
    "LocalPartitionExecutor",
    "PartitionExecutorPort",
    "PartitionMergeError",
    "ScaleAuthorizationError",
    "ScaleError",
    "ScaleService",
    "VisualizationInputError",
    "VisualizationService",
    "BoundedWorkerPool",
    "DurableExecutionSubmission",
    "JobWorker",
    "StageExecutorPort",
    "StageHandlerRegistry",
    "load_authoritative_execution_plan",
]
