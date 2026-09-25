"""Prompt03 local-only generic policy acceptance.

The oracle is deliberately read only after the product run has completed.
The test uses the public backend/source-set boundary and never calls a stage
handler, compiler, materializer, or truth builder directly.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import time
from decimal import Decimal
from uuid import uuid4

import pytest
import yaml

from dirty_data_to_olap.adapters.sources.files import FileSourceAdapter
from dirty_data_to_olap.application.backend import BackendService, Principal
from dirty_data_to_olap.application.product_policy import ProductPolicyRegistry
from dirty_data_to_olap.application.product_runtime import build_multi_source_product
from dirty_data_to_olap.application.product_sources import ProductSourceService
from dirty_data_to_olap.domain.contracts.canonical import ReviewCheckpoint, ReviewDecisionStatus
from dirty_data_to_olap.domain.contracts.jobs import ExecutionPlanIntent
from dirty_data_to_olap.domain.contracts.source import ExtractionPolicy, SelectionScope, SourceRegistryRecord, SourceType


ROOT = Path(__file__).resolve().parents[2]
ORACLE = ROOT / "tests" / "product_acceptance" / "oracle" / "telemetry_v1_truth.yml"


def _record(registry_id: str, source_id: str, path: Path) -> SourceRegistryRecord:
    return SourceRegistryRecord(registry_id=registry_id, source_id=source_id, display_name=registry_id, source_type=SourceType.CSV, file_locator=str(path), scope=SelectionScope(), adapter_name="file_source", adapter_version="1.0.0")


def _drive(backend: BackendService, run_id: str, principal: Principal):
    reviewed = []
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        summary = backend.product_summary(run_id=run_id, principal=principal)
        pending = [item for item in summary.pending_reviews if item.state == "REVIEW_REQUIRED"]
        for item in pending:
            checkpoint = ReviewCheckpoint(item.checkpoint)
            backend.review(run_id=run_id, checkpoint=checkpoint, context=item.context, subject_artifact_id=item.subject_artifact_id, subject_content_hash=item.subject_content_hash, decision=ReviewDecisionStatus.ACCEPTED, rationale=f"Prompt03 local acceptance review for {checkpoint.value}", expected_revision=item.revision, principal=principal, idempotency_key=f"prompt03-review-{item.checkpoint}-{item.subject_artifact_id}")
            reviewed.append(item.checkpoint)
        if pending:
            backend.resume(run_id=run_id, principal=principal, idempotency_key=f"prompt03-resume-{len(reviewed)}")
        elif summary.status in {"SUCCEEDED", "FAILED", "BLOCKED", "CANCELLED"}:
            return summary, reviewed
        else:
            time.sleep(0.05)
    raise AssertionError("Prompt03 local telemetry run did not reach a terminal state")


def test_prompt03_telemetry_uses_shared_product_path_and_g6(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DESBORDANTE_PROVIDER_IMAGE", "dirty-data-to-olap-desbordante-step37:local")
    workspace = ROOT / "workspace" / "tests" / f"prompt03_telemetry_{uuid4().hex}"
    workspace.mkdir(parents=True, exist_ok=True)
    devices = workspace / "devices.csv"
    devices.write_text("device_id,device_name\nD-001,Pump A\nD-002,Pump A\nD-003,Pump B\n", encoding="utf-8")
    locations = workspace / "locations.csv"
    locations.write_text("location_id,location_name\nL-001,North\nL-002,South\n", encoding="utf-8")
    readings = workspace / "readings.csv"
    readings.write_text("reading_id,device_ref,location_ref,observed_on,temperature\nR-001,D-001,L-001,2026-02-01,10.5\nR-002,D-001,L-001,2026-02-01,11.0\nR-003,D-002,L-002,2026-02-02,9.5\nR-004,D-404,L-002,2026-02-02,20.0\n", encoding="utf-8")

    platform = backend = runtime = None
    principal = Principal(subject="prompt03-local", source="LOCAL_TEST_AUTH", scopes=frozenset({"runs:write", "runs:read", "reviews:write"}))
    try:
        adapters = {"file_source": FileSourceAdapter(SourceType.CSV, project_root=ROOT)}
        platform, backend, runtime = build_multi_source_product(ROOT, adapters=adapters, policy_id="telemetry")
        source_service = ProductSourceService(ROOT, runtime.source_service.registry)
        nonce = uuid4().hex[:8]
        records = (
            _record(f"telemetry-devices-{nonce}", f"telemetry-devices-{nonce}", devices),
            _record(f"telemetry-locations-{nonce}", f"telemetry-locations-{nonce}", locations),
            _record(f"telemetry-readings-{nonce}", f"telemetry-readings-{nonce}", readings),
        )
        for record in records:
            source_service.register_read_only_source(record, owner_subject=principal.subject)
        extraction = ExtractionPolicy(chunk_size=2, null_markers=("",), preserve_raw_values=True)
        run, _ = backend.create_run(project_id="prompt03-telemetry", configuration_fingerprint=platform.config.configuration_fingerprint, git_content_commit=None, metadata={"acceptance": "prompt03-local"}, principal=principal, idempotency_key=f"prompt03-create-{uuid4().hex}")
        backend.bind_product_source_set(run_id=run.run_id, registry_ids=tuple(item.registry_id for item in records), scope=SelectionScope(), extraction=extraction, execution_context_id="prompt03-telemetry-local-v1", principal=principal, idempotency_key=f"prompt03-bind-{uuid4().hex}")
        preparation, _ = backend.prepare_execution_plan(run_id=run.run_id, intent=ExecutionPlanIntent(product_policy_id="telemetry", product_policy_version="telemetry-product-v1", cross_source_mapping_requested=True, entity_resolution_requested=False), principal=principal, idempotency_key=f"prompt03-plan-{uuid4().hex}")
        assert preparation.status.value == "READY"
        submission, _ = backend.submit(run_id=run.run_id, principal=principal, idempotency_key=f"prompt03-submit-{uuid4().hex}")
        assert submission.status in {"ACCEPTED", "BLOCKED"}
        summary, reviewed = _drive(backend, run.run_id, principal)
        assert summary.status == "SUCCEEDED", summary
        assert summary.validation.g6_status == "PASS" and summary.validation.g6_eligible

        binding = backend.get_run(run.run_id, principal=principal)
        assert binding.metadata["product_policy_id"] == "telemetry"
        assert binding.metadata["product_policy_version"] == "telemetry-product-v1"
        policy = ProductPolicyRegistry(ROOT).resolve(product_id="telemetry", version="telemetry-product-v1")
        plan = platform.control_store.get_execution_plan(run.run_id)
        assert plan is not None and plan.product_policy is not None
        assert plan.product_policy.content_fingerprint == policy.content_fingerprint
        assert len(reviewed) >= 4

        target = ROOT / "workspace" / "platform" / "runs" / run.run_id / "olap" / "olap.duckdb"
        import duckdb

        with duckdb.connect(str(target), read_only=True) as connection:
            fact_rows = int(connection.execute("SELECT COUNT(*) FROM fact_device_reading").fetchone()[0])
            max_temperature = Decimal(str(connection.execute("SELECT MAX(temperature) FROM fact_device_reading").fetchone()[0]))
            same_name = int(connection.execute("SELECT COUNT(*) FROM dim_device WHERE device_name = 'Pump A'").fetchone()[0])
        oracle = yaml.safe_load(ORACLE.read_text(encoding="utf-8"))
        expected = oracle["expected"]
        assert fact_rows == expected["fact_rows"]
        assert max_temperature == Decimal(expected["temperature_global_max"])
        assert same_name == len(expected["same_name_device_ids"])
        truth_refs = platform.control_store.list_artifacts(run_id=run.run_id, artifact_kind="SourceTruthManifest", limit=10)
        assert len(truth_refs) == 1
        truth = yaml.safe_load(platform.artifact_store.read(truth_refs[0]).decode("utf-8"))
        assert len(truth["records"]) == expected["source_records"]
        assert len(truth["facts"]) == expected["fact_rows"]
        assert all(record["grain_values"].get("reading_id") != "R-004" for record in truth["facts"])
        assert any(item.decision.review_checkpoint_id is ReviewCheckpoint.REVIEW_CANONICAL_IDENTITY for item in backend.list_reviews(run_id=run.run_id, subject_key=None, page_size=100, offset=0, principal=principal).items)
    finally:
        if runtime is not None:
            runtime.close()
        shutil.rmtree(workspace, ignore_errors=True)
