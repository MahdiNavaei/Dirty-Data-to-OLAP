"""Shared deterministic objects for Step20 contract and integration tests."""

from datetime import datetime, timezone

from dirty_data_to_olap.domain.contracts.analytical import AnalyticalInputBinding
from dirty_data_to_olap.domain.contracts.source import stable_id
from tools.run_step20_reference import build_canonical_model, build_fixture, build_reference_plan


def planned_flow():
    model = build_canonical_model()
    fixture = build_fixture(model)
    binding = AnalyticalInputBinding(
        binding_id=stable_id("abind", {"fixture_id": fixture.fixture_id, "fixture_hash": fixture.content_hash, "model": model.content_hash}),
        canonical_model_id=model.model_id,
        canonical_model_content_hash=model.content_hash,
        fixture_id=fixture.fixture_id,
        fixture_content_hash=fixture.content_hash,
        source_schema_fingerprints={"crm": "schema-crm-step20", "erp": "schema-erp-step20", "sales": "schema-sales-step20"},
        row_counts=fixture.row_counts,
        provenance_refs=("test-binding",),
    )
    return (model, fixture, binding, *build_reference_plan(model, binding, fixture, created_at=datetime(2026, 9, 11, tzinfo=timezone.utc)))
