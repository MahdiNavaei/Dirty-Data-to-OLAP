"""Focused Prompt02 controls that must not need the disposable SQL estate."""

from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from dirty_data_to_olap.adapters.sources.files import FileSourceAdapter
from dirty_data_to_olap.application.backend import BackendError, Principal
from dirty_data_to_olap.application.product_runtime import build_multi_source_product
from dirty_data_to_olap.application.product_sources import ProductSourceService
from dirty_data_to_olap.domain.contracts.jobs import ExecutionPlanIntent
from dirty_data_to_olap.domain.contracts.source import ExtractionPolicy, SelectionScope, SourceRegistryRecord, SourceType


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
    finally:
        runtime.close()
        platform.control_store.close()
