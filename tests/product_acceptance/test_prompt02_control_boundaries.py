"""Focused Prompt02 controls that must not need the disposable SQL estate."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

import pytest

from dirty_data_to_olap.adapters.sources.files import FileSourceAdapter
from dirty_data_to_olap.application.backend import BackendError, Principal
from dirty_data_to_olap.application.canonical import CanonicalIdentityProposalService, CanonicalizationError
from dirty_data_to_olap.application.product_runtime import build_multi_source_product
from dirty_data_to_olap.application.product_sources import ProductSourceError, ProductSourceService
from dirty_data_to_olap.domain.contracts.canonical import CanonicalIdentityMembership, IdentityDerivationBasis
from dirty_data_to_olap.domain.contracts.entity_resolution import EntityResolutionResult
from dirty_data_to_olap.domain.contracts.source import ExtractionPolicy, SelectionScope, SourceRegistryRecord, SourceType
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


def _canonical_reference() -> tuple[object, EntityResolutionResult]:
    reference = ROOT / "workspace" / "runs" / "step18-inference-baseline-v5" / "evaluation" / "entity_resolution" / "normalized_provider_result.json"
    payload = json.loads(reference.read_text(encoding="utf-8"))
    er = EntityResolutionResult.model_validate(payload["result"])
    import tools.run_step19_reference as step19_reference

    from dirty_data_to_olap.domain.contracts.canonical import CanonicalModelHypothesis

    with TemporaryDirectory(prefix="ddo-prompt02-nc06-") as isolated_root:
        original_run = step19_reference.RUN
        step19_reference.RUN = Path(isolated_root) / "canonical"
        try:
            result = step19_reference.main()
            if result != 0:
                raise AssertionError(f"isolated Step19 reference setup failed with exit code {result}")
            hypothesis_path = step19_reference.RUN / "canonical_model_hypothesis.json"
            hypothesis = CanonicalModelHypothesis.model_validate(json.loads(hypothesis_path.read_text(encoding="utf-8")))
        finally:
            step19_reference.RUN = original_run
    return hypothesis, er


def test_nc06_rejects_membership_outside_er_population() -> None:
    """Canonical identity cannot authorize records absent from the evaluated ER population."""

    hypothesis, er = _canonical_reference()
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
    record_control_observation("NC06", f"CanonicalizationError:{raised.value.code}", "tests/product_acceptance/test_prompt02_control_boundaries.py:101")


def test_nc13_rejects_hard_negative_identity_membership() -> None:
    """A forced merge of the Prompt02 same-name/different-contact fixture is rejected by the ER boundary."""

    hypothesis, er = _canonical_reference()
    hard_negative = {
        "crm": {"record_ref": "prompt02-crm-postgres:CRM-001", "name": "Alice Smith", "contact": "alice@example.test"},
        "erp": {"record_ref": "prompt02-erp-mysql:ERP-HARD-NEG", "name": "Alice Smith", "contact": "bob@example.test"},
    }
    assert hard_negative["crm"]["name"] == hard_negative["erp"]["name"]
    assert hard_negative["crm"]["contact"] != hard_negative["erp"]["contact"]
    membership = CanonicalIdentityMembership(
        membership_group_id="prompt02-hard-negative",
        canonical_entity_type_id=hypothesis.entity_types[0].canonical_entity_type_id,
        entity_resolution_family="person",
        source_record_refs=(hard_negative["crm"]["record_ref"], hard_negative["erp"]["record_ref"]),
        derivation_basis=IdentityDerivationBasis.ER_AUTHORIZED_LINKAGE,
        authorized_edge_refs=(er.edges[0].edge_id,),
        evidence_refs=("prompt02-hard-negative-fixture",),
        policy_refs=("prompt02-identity-policy",),
        rationale="fault-injected same-name/different-contact merge",
        provenance_refs=("prompt02-negative-control-NC13",),
    )
    with pytest.raises(CanonicalizationError, match="INCOMPATIBLE_ER_RESULT") as raised:
        CanonicalIdentityProposalService.validate_er_membership(hypothesis, membership, er, "person")
    record_control_observation("NC13", f"CanonicalizationError:{raised.value.code}", "tests/product_acceptance/test_prompt02_control_boundaries.py:135")


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
