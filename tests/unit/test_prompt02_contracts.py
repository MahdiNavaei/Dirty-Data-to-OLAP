from __future__ import annotations

import pytest

from dirty_data_to_olap.domain.contracts.source import ExtractionPolicy, SourceSelection, SourceSetSelection, source_set_fingerprint


def _selection(registry_id: str) -> SourceSelection:
    return SourceSelection(registry_id=registry_id, execution_context_id="prompt02-test", extraction=ExtractionPolicy(chunk_size=2))


def test_source_set_fingerprint_is_order_independent_and_finalized() -> None:
    selections = (_selection("source-b"), _selection("source-a"))
    bound = SourceSetSelection(selections=selections, source_set_fingerprint=source_set_fingerprint(selections))
    assert tuple(item.registry_id for item in bound.ordered_selections) == ("source-a", "source-b")
    assert bound.finalized is True


def test_source_set_rejects_single_source_and_stale_fingerprint() -> None:
    with pytest.raises(ValueError):
        SourceSetSelection(selections=(_selection("source-a"),), source_set_fingerprint="stale")
    selections = (_selection("source-a"), _selection("source-b"))
    with pytest.raises(ValueError, match="fingerprint"):
        SourceSetSelection(selections=selections, source_set_fingerprint="stale")

