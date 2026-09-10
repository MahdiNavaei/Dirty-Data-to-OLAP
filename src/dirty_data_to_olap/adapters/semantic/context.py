"""Build minimal aggregate-only semantic context."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping

from dirty_data_to_olap.domain.contracts.semantic_ai import (
    SemanticContextItem,
    SemanticContextManifest,
    SemanticEvidenceRequest,
)
from dirty_data_to_olap.domain.contracts.source import stable_digest
from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService


class SemanticContextBudgetError(ValueError):
    pass


class SemanticContextBuilder:
    version = "semantic-context-builder-v1"

    def __init__(self, privacy_policy: PrivacyPolicyService | None = None) -> None:
        self.privacy_policy = privacy_policy or PrivacyPolicyService()

    def _sanitize(self, value):
        if isinstance(value, Mapping):
            return {str(key): self._sanitize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._sanitize(item) for item in value]
        if not isinstance(value, str):
            return value
        sanitized = str(self.privacy_policy.sanitize_safe_metadata(value))
        sanitized = re.sub(r"(?i)(?:api[_-]?key|password|passwd|secret|token|credential)\s*[:=]\s*[^\s,;]+", "<REDACTED_SECRET>", sanitized)
        sanitized = re.sub(r"(?<!\d)(?:\+98|0098|98)?9\d{9,10}(?!\d)", "<REDACTED_PHONE>", sanitized)
        sanitized = re.sub(r"(?<!\d)\d{9,12}(?!\d)", "<REDACTED_IDENTIFIER>", sanitized)
        return sanitized

    def build(self, request: SemanticEvidenceRequest, items: Iterable[SemanticContextItem]) -> SemanticContextManifest:
        selected = tuple(SemanticContextItem(item_ref=item.item_ref, item_kind=item.item_kind, safe_fields=self._sanitize(item.safe_fields), redaction=item.redaction) for item in sorted(items, key=lambda item: (item.item_kind, item.item_ref)))
        if len(selected) > request.budget.max_context_items:
            raise SemanticContextBudgetError("context item budget exceeded")
        if any(item.item_ref not in request.evidence_refs for item in selected):
            raise SemanticContextBudgetError("context item is not listed in the requested evidence references")
        payload = [item.model_dump(mode="json", exclude={"schema_version"}) for item in selected]
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if len(encoded) > request.budget.max_input_chars:
            raise SemanticContextBudgetError("context input character budget exceeded")
        return SemanticContextManifest(
            manifest_id="context_" + stable_digest({"request": request.request_id, "items": payload})[:24],
            subject_refs=request.subject_refs,
            requested_evidence_refs=request.evidence_refs,
            provided_context_item_refs=tuple(item.item_ref for item in selected),
            allowed_provider_evidence_refs=tuple(item.item_ref for item in selected),
            items=selected,
            input_char_count=len(encoded),
            context_builder_version=self.version,
            input_fingerprint=stable_digest({"subjects": request.subject_refs, "evidence": request.evidence_refs, "items": payload}),
        )
