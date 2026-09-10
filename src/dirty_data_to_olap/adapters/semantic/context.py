"""Build minimal aggregate-only semantic context."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping

from dirty_data_to_olap.domain.contracts.semantic_ai import (
    SemanticContextItem,
    SemanticContextManifest,
    SemanticEvidenceRequest,
)
from dirty_data_to_olap.domain.contracts.source import stable_digest


class SemanticContextBudgetError(ValueError):
    pass


class SemanticContextBuilder:
    version = "semantic-context-builder-v1"

    def build(self, request: SemanticEvidenceRequest, items: Iterable[SemanticContextItem]) -> SemanticContextManifest:
        selected = tuple(sorted(items, key=lambda item: (item.item_kind, item.item_ref)))
        if len(selected) > request.budget.max_context_items:
            raise SemanticContextBudgetError("context item budget exceeded")
        if any(item.item_ref not in request.evidence_refs for item in selected):
            raise SemanticContextBudgetError("context item is not listed in the request evidence references")
        payload = [item.model_dump(mode="json", exclude={"schema_version"}) for item in selected]
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if len(encoded) > request.budget.max_input_chars:
            raise SemanticContextBudgetError("context input character budget exceeded")
        return SemanticContextManifest(
            manifest_id="context_" + stable_digest({"request": request.request_id, "items": payload})[:24],
            subject_refs=request.subject_refs,
            evidence_refs=request.evidence_refs,
            items=selected,
            input_char_count=len(encoded),
            context_builder_version=self.version,
            input_fingerprint=stable_digest({"subjects": request.subject_refs, "evidence": request.evidence_refs, "items": payload}),
        )
