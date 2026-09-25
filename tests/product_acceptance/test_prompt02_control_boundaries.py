"""Focused Prompt02 controls that must not need the disposable SQL estate."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil

import pytest

from dirty_data_to_olap.adapters.sources.files import FileSourceAdapter
from dirty_data_to_olap.application.backend import BackendError, Principal
from dirty_data_to_olap.application.canonical import CanonicalIdentityProposalService, CanonicalizationError
from dirty_data_to_olap.application.product_runtime import build_multi_source_product
from dirty_data_to_olap.application.product_sources import ProductSourceError, ProductSourceService
from dirty_data_to_olap.domain.contracts.canonical import (
    CanonicalEntityKind,
    CanonicalEntityType,
    CanonicalIdentityMembership,
    CanonicalModelHypothesis,
    EntityResolutionRequirement,
    IdentityDerivationBasis,
)
from dirty_data_to_olap.domain.contracts.entity_resolution import (
    ERBlockingRule,
    ERClusteringPolicy,
    ERComparisonSpecification,
    ERThresholdPolicy,
    ERTrainingPolicy,
    EntityMatchEdge,
    EntityMatchPredictionBand,
    EntityResolutionCapability,
    EntityResolutionCapabilityStatus,
    EntityResolutionEngineReference,
    EntityResolutionMode,
    EntityResolutionModelEvidence,
    EntityResolutionNormalizationRule,
    EntityResolutionObservationScope,
    EntityResolutionResult,
    EntityResolutionRunMetrics,
    EntityResolutionSpec,
    EntityResolutionStatus,
    IdentityFieldSpecification,
)
from dirty_data_to_olap.domain.contracts.source import AdapterReference, ExtractionPolicy, SelectionScope, SourceRegistryRecord, SourceType, stable_id
from tests.product_acceptance.prompt02_control_evidence import record_control_observation


ROOT = Path(__file__).resolve().parents[2]
ORACLE = ROOT / "tests" / "product_acceptance" / "oracle" / "multi_source_v1_truth.yml"


def _runtime(root: Path):
    shutil.copytree(ROOT / "config", root / "config")
    shutil.copytree(ROOT / "docs" / "architecture", root / "docs" / "architecture")
    return build_multi_source_product(
        root,
        adapters={"file_source": FileSourceAdapter(SourceType.CSV, project_root=root)},
    )


def test_nc03_runtime_composition_does_not_read_acceptance_oracle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The product composition root must not consume the test-only oracle."""

    original_read_text = Path.read_text

    def deny_oracle(path: Path, *args, **kwargs):
        if path.resolve() == ORACLE.resolve():
            raise AssertionError("application runtime attempted to read the independent oracle")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", deny_oracle)
    platform, backend, runtime = _runtime(tmp_path)
    try:
        assert platform is not None and backend is not None and runtime is not None
        record_control_observation("NC03", "oracle_access_denied_to_runtime", "tests/product_acceptance/test_prompt02_control_boundaries.py:43")
    finally:
        runtime.close()
        platform.control_store.close()


def test_nc10_rejects_source_set_mutation_after_binding(tmp_path: Path) -> None:
    """A run has exactly one durable product source-set binding."""

    platform, backend, runtime = _runtime(tmp_path)
    principal = Principal(subject="prompt02-control", source="LOCAL_TEST_AUTH", scopes=frozenset({"runs:write", "runs:read"}))
    try:
        service = ProductSourceService(tmp_path, runtime.source_service.registry)
        for registry_id in ("left", "right", "third"):
            path = tmp_path / f"{registry_id}.csv"
            path.write_text("id,value\n1,ok\n", encoding="utf-8")
            service.register_read_only_source(
                SourceRegistryRecord(registry_id=registry_id, source_id=f"source-{registry_id}", display_name=registry_id, source_type=SourceType.CSV, file_locator=str(path), adapter_name="file_source", adapter_version="1"),
                owner_subject=principal.subject,
            )
        run, _ = backend.create_run(project_id="prompt02-control", configuration_fingerprint=platform.config.configuration_fingerprint, git_content_commit=None, metadata={}, principal=principal, idempotency_key="create")
        backend.bind_product_source_set(run_id=run.run_id, registry_ids=("left", "right"), scope=SelectionScope(), extraction=ExtractionPolicy(chunk_size=1), execution_context_id="control", principal=principal, idempotency_key="bind")
        with pytest.raises(BackendError) as raised:
            backend.bind_product_source_set(run_id=run.run_id, registry_ids=("left", "third"), scope=SelectionScope(), extraction=ExtractionPolicy(chunk_size=1), execution_context_id="mutated", principal=principal, idempotency_key="mutate")
        assert raised.value.code == "SOURCE_ALREADY_BOUND"
        assert backend.product_summary(run_id=run.run_id, principal=principal).status == "CREATED"
        record_control_observation("NC10", f"{raised.value.code}:binding_unchanged", "tests/product_acceptance/test_prompt02_control_boundaries.py:72")
    finally:
        runtime.close()
        platform.control_store.close()


def _prompt02_er_baseline() -> tuple[CanonicalModelHypothesis, EntityResolutionResult, EntityMatchEdge]:
    """Build the minimum typed ER state required by the canonical boundary.

    This is deliberately local to Prompt02 negative controls.  It does not
    read or regenerate any historical Step18 provider artifact.
    """

    source_ids = ("prompt02-crm", "prompt02-erp")
    table_ids = {"prompt02-crm": ("crm_customers",), "prompt02-erp": ("erp_customers",)}
    snapshot_ids = {source_id: f"snapshot-{source_id}" for source_id in source_ids}
    fields = tuple(
        IdentityFieldSpecification(
            field_id=field,
            source_id=source_id,
            snapshot_id=snapshot_ids[source_id],
            table_id=table_ids[source_id][0],
            column_id=f"{source_id}-{field}",
            physical_name=field,
            semantic_role=field,
            normalization_rule_id=f"normalize-{field}",
        )
        for source_id in source_ids
        for field in ("name", "email", "phone")
    )
    spec = EntityResolutionSpec(
        spec_id="prompt02-negative-control-er-v1",
        entity_family="person",
        mode=EntityResolutionMode.LINK_ONLY,
        source_ids=source_ids,
        snapshot_ids=snapshot_ids,
        table_ids_by_source=table_ids,
        identity_fields=fields,
        normalization_rules=tuple(EntityResolutionNormalizationRule(rule_id=f"normalize-{field}", version="1", applies_to=(field,)) for field in ("name", "email", "phone")),
        blocking_rules=(ERBlockingRule(rule_id="block-name", version="1", field_ids=("name",), sql_expression="l.name = r.name"),),
        comparisons=(
            ERComparisonSpecification(comparison_id="compare-name", field_id="name", method="exact"),
            ERComparisonSpecification(comparison_id="compare-email", field_id="email", method="exact"),
            ERComparisonSpecification(comparison_id="compare-phone", field_id="phone", method="exact"),
        ),
        training_policy=ERTrainingPolicy(em_blocking_rule_ids=("block-name",)),
        threshold_policy=ERThresholdPolicy(),
        clustering_policy=ERClusteringPolicy(threshold_policy_id="er-threshold-v1"),
    )
    observation_scope = EntityResolutionObservationScope(
        source_ids=source_ids,
        snapshot_ids=snapshot_ids,
        table_ids_by_source=table_ids,
        input_batch_ids=("batch-prompt02-crm", "batch-prompt02-erp"),
        input_batch_hashes=("hash-prompt02-crm", "hash-prompt02-erp"),
        available_staged_rows_by_table={"crm_customers": 2, "erp_customers": 2},
        records_read=4,
        records_sampled=4,
        sample_seed=20260922,
        sample_algorithm_version="prompt02-negative-control-fixture-v1",
    )
    engine = EntityResolutionEngineReference(
        engine="prompt02-test-er",
        engine_version="1",
        adapter=AdapterReference(name="prompt02-test-fixture", version="1", config_fingerprint="prompt02-negative-control-fixture-v1"),
        runtime_dependency="project-owned-contract-fixture",
        source_revision="prompt02-negative-control-fixture-v1",
    )
    model = EntityResolutionModelEvidence(
        model_id="prompt02-negative-control-model",
        training_policy_id=spec.training_policy.policy_id,
        u_training_method="deterministic_fixture",
        m_training_method="deterministic_fixture",
        random_seed=20260922,
        training_blocking_rule_ids=("block-name",),
        trained=False,
        training_provenance="prompt02-negative-control-fixture",
        model_config_hash=spec.fingerprint,
        input_spec_fingerprint=spec.fingerprint,
    )
    capability = EntityResolutionCapability(
        capability_id="prompt02-negative-control-capability",
        status=EntityResolutionCapabilityStatus.AVAILABLE,
        engine="prompt02-test-er",
        engine_version="1",
        detail="typed local fixture for canonical compatibility validation",
    )
    metrics = EntityResolutionRunMetrics(
        available_staged_records=4,
        records_read=4,
        records_sampled=4,
        all_pairs=4,
        candidate_pairs=1,
        candidate_pairs_by_rule={"block-name": 1},
        pairs_rejected_by_budget=0,
        predictions_emitted=1,
        clusters_emitted=1,
    )
    accepted_edge = EntityMatchEdge(
        edge_id="prompt02-accepted-edge",
        left_record_ref="prompt02-crm-accepted",
        right_record_ref="prompt02-erp-accepted",
        left_source_id="prompt02-crm",
        right_source_id="prompt02-erp",
        left_snapshot_id=snapshot_ids["prompt02-crm"],
        right_snapshot_id=snapshot_ids["prompt02-erp"],
        match_weight=12.0,
        match_probability=0.99,
        model_prediction_band=EntityMatchPredictionBand.STRONG_LINK_EVIDENCE,
        blocking_rule_ids=("block-name",),
        model_evidence_ref=model.model_id,
        comparison_evidence_refs=("comparison-name-agree", "comparison-email-agree", "comparison-phone-agree"),
        independent_evidence_refs=("prompt02-independent-identity-evidence",),
    )
    hard_negative_edge = accepted_edge.model_copy(update={
        "edge_id": "prompt02-hard-negative-edge",
        "left_record_ref": "prompt02-crm-hard-negative",
        "right_record_ref": "prompt02-erp-hard-negative",
        "match_weight": -10.0,
        "match_probability": 0.10,
        "model_prediction_band": EntityMatchPredictionBand.BELOW_EVIDENCE_THRESHOLD,
        "comparison_evidence_refs": ("comparison-name-agree", "comparison-email-conflict", "comparison-phone-conflict"),
        "risk_flags": ("same_name_different_contact",),
    })
    result = EntityResolutionResult(
        spec=spec,
        observation_scope=observation_scope,
        engine=engine,
        model=model,
        edges=(accepted_edge,),
        capabilities=(capability,),
        metrics=metrics,
        status=EntityResolutionStatus.COMPLETE,
    )
    hypothesis = CanonicalModelHypothesis(
        artifact_id=stable_id("chyp", {"fixture": "prompt02-negative-controls"}),
        run_id="prompt02-negative-control-run",
        execution_context_id="prompt02-negative-control-context",
        model_version="prompt02-negative-control-v1",
        upstream_review_decision_refs=("prompt02-negative-control-review",),
        source_ids=source_ids,
        domain_assertion_refs=("prompt02:identity-fixture",),
        entity_types=(CanonicalEntityType(
            canonical_entity_type_id="entity_customer",
            semantic_id="customer",
            business_name="Customer",
            kind=CanonicalEntityKind.IDENTITY,
            entity_resolution_family="person",
            identity_strategy="ER_AUTHORIZED_LINKAGE",
            identity_attribute_ids=("customer_name", "customer_email", "customer_phone"),
            review_state="REVIEW_REQUIRED",
            domain_assertion_refs=("prompt02:identity-fixture",),
            provenance_refs=("prompt02-negative-control-fixture",),
        ),),
        entity_resolution_requirements={"person": EntityResolutionRequirement.ER_REQUIRED},
        entity_resolution_specs=(spec,),
        evidence_refs=("prompt02-negative-control-fixture",),
        provenance_refs=("prompt02-negative-control-fixture",),
        created_at=datetime(2026, 9, 22, tzinfo=timezone.utc),
    )
    return hypothesis, result, hard_negative_edge


def test_nc06_rejects_membership_outside_er_population() -> None:
    """Canonical identity cannot authorize records absent from the evaluated ER population."""

    hypothesis, er, _hard_negative_edge = _prompt02_er_baseline()
    membership = CanonicalIdentityMembership(
        membership_group_id="prompt02-outside-population",
        canonical_entity_type_id=hypothesis.entity_types[0].canonical_entity_type_id,
        entity_resolution_family="person",
        source_record_refs=("outside-crm-record", "outside-erp-record"),
        derivation_basis=IdentityDerivationBasis.ER_AUTHORIZED_LINKAGE,
        authorized_edge_refs=(er.edges[0].edge_id,),
        evidence_refs=("prompt02-outside-population-fixture",),
        policy_refs=("prompt02-identity-policy",),
        rationale="fault-injected membership outside evaluated ER population",
        provenance_refs=("prompt02-negative-control-NC06",),
    )
    with pytest.raises(CanonicalizationError, match="INCOMPATIBLE_ER_RESULT") as raised:
        CanonicalIdentityProposalService.validate_er_membership(hypothesis, membership, er, "person")
    record_control_observation("NC06", f"CanonicalizationError:{raised.value.code}", "tests/product_acceptance/test_prompt02_control_boundaries.py:282")


def test_nc13_rejects_hard_negative_identity_membership() -> None:
    """A forced merge of the Prompt02 same-name/different-contact fixture is rejected by the ER boundary."""

    hypothesis, er, hard_negative_edge = _prompt02_er_baseline()
    hard_negative = {
        "crm": {"record_ref": hard_negative_edge.left_record_ref, "name": "Alice Smith", "email": "alice@example.test", "phone": "+1-202-555-0101"},
        "erp": {"record_ref": hard_negative_edge.right_record_ref, "name": "Alice Smith", "email": "bob@example.test", "phone": "+1-202-555-0199"},
    }
    assert hard_negative["crm"]["name"] == hard_negative["erp"]["name"]
    assert hard_negative["crm"]["email"] != hard_negative["erp"]["email"]
    assert hard_negative["crm"]["phone"] != hard_negative["erp"]["phone"]
    er = er.model_copy(update={"edges": (hard_negative_edge,)})
    membership = CanonicalIdentityMembership(
        membership_group_id="prompt02-hard-negative",
        canonical_entity_type_id=hypothesis.entity_types[0].canonical_entity_type_id,
        entity_resolution_family="person",
        source_record_refs=(hard_negative["crm"]["record_ref"], hard_negative["erp"]["record_ref"]),
        derivation_basis=IdentityDerivationBasis.ER_AUTHORIZED_LINKAGE,
        authorized_edge_refs=(hard_negative_edge.edge_id,),
        evidence_refs=("prompt02-hard-negative-fixture",),
        policy_refs=("prompt02-identity-policy",),
        rationale="fault-injected same-name/different-contact merge",
        provenance_refs=("prompt02-negative-control-NC13",),
    )
    with pytest.raises(CanonicalizationError, match="INCOMPATIBLE_ER_RESULT") as raised:
        CanonicalIdentityProposalService.validate_er_membership(hypothesis, membership, er, "person")
    record_control_observation("NC13", f"CanonicalizationError:{raised.value.code}", "tests/product_acceptance/test_prompt02_control_boundaries.py:311")


def test_nc11_rejects_single_source_selection(tmp_path: Path) -> None:
    """The actual Prompt02 source-selection service cannot fall back to one source."""

    platform, _backend, runtime = _runtime(tmp_path)
    try:
        with pytest.raises(ProductSourceError) as raised:
            runtime.source_service.source_set_selection(
                registry_ids=("only-source",),
                scope=SelectionScope(),
                extraction=ExtractionPolicy(chunk_size=1),
                execution_context_id="prompt02-nc11",
            )
        assert str(raised.value) == "a multi-source product run requires at least two unique sources"
        record_control_observation("NC11", f"ProductSourceError:{raised.value}", "tests/product_acceptance/test_prompt02_control_boundaries.py:135")
    finally:
        runtime.close()
        platform.control_store.close()
