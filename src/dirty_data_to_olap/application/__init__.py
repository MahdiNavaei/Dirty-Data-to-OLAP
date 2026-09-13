"""Application services for source lifecycle orchestration."""

from .dependency_discovery import DependencyDiscoveryService
from .entity_resolution import EntityResolutionService
from .evidence_fusion import EvidenceFusionService
from .canonical import CanonicalFinalizationService, CanonicalHypothesisService, CanonicalIdentityProposalService, CanonicalizationError
from .review_policy import ReviewCompatibilityError, ReviewPolicyService
from .execution_plan import ExecutionPlanService
from .review_subjects import ReviewCheckpointSubjectResolver, ReviewSubjectDerivation, ReviewSubjectDerivationPort
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
from .jobs import BoundedWorkerPool, DurableExecutionSubmission, ExecutionPlanAdvancerPort, ExecutionPlanSelectionError, JobWorker, StageExecutorPort, StageHandlerRegistry, load_authoritative_execution_plan
from .compiler import CompilationArtifactPublisher, CompilationOutputRefs
from .planning_authority import ExecutionPlanningContext, SelectionResolution, ServerOwnedExecutionPlanSelectionResolver, TrustedExecutionPlanningState

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
    "ExecutionPlanService",
    "ReviewCheckpointSubjectResolver",
    "ReviewSubjectDerivation",
    "ReviewSubjectDerivationPort",
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
    "ExecutionPlanAdvancerPort",
    "ExecutionPlanSelectionError",
    "CompilationArtifactPublisher",
    "CompilationOutputRefs",
    "ExecutionPlanningContext",
    "SelectionResolution",
    "ServerOwnedExecutionPlanSelectionResolver",
    "TrustedExecutionPlanningState",
    "JobWorker",
    "StageExecutorPort",
    "StageHandlerRegistry",
    "load_authoritative_execution_plan",
]
