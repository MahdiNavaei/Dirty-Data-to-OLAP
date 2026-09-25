from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from dirty_data_to_olap.application.multi_source_product import MultiSourceProductService
from dirty_data_to_olap.application.multi_source_runtime import MultiSourceStageHandlers
from dirty_data_to_olap.application.product_policy import ProductPolicyRegistry
from dirty_data_to_olap.application.source_registry import DurableSourceRegistry
from dirty_data_to_olap.domain.contracts.jobs import ExecutionPlan, ExecutionPlanIntent, StageExecutionRequest, StageSpec
from dirty_data_to_olap.domain.contracts.platform import RunRecord
from dirty_data_to_olap.domain.contracts.product import ProductPolicyBinding
from dirty_data_to_olap.platform import LocalPlatform


ROOT = Path(__file__).resolve().parents[2]


class ConformingFixturePolicy:
    """Test-only policy proving the registry/runtime boundary is composable."""

    product_id = "fixture"
    version = "fixture-product-v1"
    provenance = "test:prompt03-fixture-policy"
    content_fingerprint = "a" * 64

    @property
    def binding(self) -> ProductPolicyBinding:
        return ProductPolicyBinding(
            product_id=self.product_id,
            version=self.version,
            content_fingerprint=self.content_fingerprint,
            provenance_ref=self.provenance,
        )

    def source_role(self, _catalog):
        return "fixture_role"

    def role_table(self, _catalog):
        return SimpleNamespace(table_id="fixture-table", physical_name="fixture_table")

    def logical_value(self, _catalog, _table_id, values, logical_name):
        return values.get(logical_name)


def test_test_only_policy_resolves_binds_and_publishes_through_shared_boundaries(tmp_path: Path) -> None:
    fixture = ConformingFixturePolicy()
    registry = ProductPolicyRegistry(ROOT, additional_policies={fixture.product_id: fixture})
    resolved = registry.resolve(product_id=fixture.product_id, version=fixture.version, fingerprint=fixture.content_fingerprint)
    assert resolved is fixture
    assert resolved.binding == fixture.binding

    catalog = SimpleNamespace(source_id="fixture-source")
    service = MultiSourceProductService(
        project_root=tmp_path,
        registry=DurableSourceRegistry(tmp_path / "workspace" / "source-registry.json"),
        adapters={},
        graph_root=ROOT,
        product_policy=resolved,
    )
    assert service.source_role(catalog) == "fixture_role"
    assert service.role_table(catalog).table_id == "fixture-table"
    assert service.logical_value(catalog, "fixture-table", {"fixture_key": "value"}, "fixture_key") == "value"

    platform = LocalPlatform.from_project_root(tmp_path)
    try:
        platform.create_run(
            RunRecord(
                run_id="run-prompt03-fixture",
                project_id="prompt03-fixture",
                configuration_fingerprint=platform.config.configuration_fingerprint,
                metadata={"product_policy_fingerprint": fixture.content_fingerprint},
            )
        )
        handlers = MultiSourceStageHandlers(
            project_root=tmp_path,
            platform=platform,
            registry=service.registry,
            adapters={},
            policy_root=ROOT,
            product_policy=resolved,
        )
        request = StageExecutionRequest(
            request_id="request-prompt03-fixture",
            job_id="job-prompt03-fixture",
            run_id="run-prompt03-fixture",
            stage_id="FIXTURE_STAGE",
            attempt_id="attempt-prompt03-fixture",
            plan_id="plan-prompt03-fixture",
            configuration_fingerprint=platform.config.configuration_fingerprint,
            policy_config_fingerprint=fixture.content_fingerprint,
            cancellation_token_id="cancel-prompt03-fixture",
        )
        artifact = handlers._publish(request, "FixtureArtifact", {"accepted": True})
        assert fixture.provenance in artifact.provenance_refs
        assert fixture.content_fingerprint in artifact.provenance_refs
    finally:
        platform.close()


def test_test_only_policy_rejects_incompatible_execution_plan_binding() -> None:
    fixture = ConformingFixturePolicy()
    with pytest.raises(ValueError, match="fingerprint does not match"):
        ExecutionPlan(
            plan_id="plan-prompt03-fixture-negative",
            run_id="run-prompt03-fixture-negative",
            stages=(StageSpec(stage_id="FIXTURE_STAGE", handler_key="fixture"),),
            planning_intent=ExecutionPlanIntent(
                product_policy_id=fixture.product_id,
                product_policy_version=fixture.version,
                product_policy_fingerprint="b" * 64,
            ),
            product_policy=fixture.binding,
        )
