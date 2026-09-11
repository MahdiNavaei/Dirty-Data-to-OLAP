"""Application services for source lifecycle orchestration."""

from .dependency_discovery import DependencyDiscoveryService
from .entity_resolution import EntityResolutionService
from .evidence_fusion import EvidenceFusionService
from .canonical import CanonicalFinalizationService, CanonicalHypothesisService, CanonicalIdentityProposalService, CanonicalizationError
from .review_policy import ReviewCompatibilityError, ReviewPolicyService

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
]
