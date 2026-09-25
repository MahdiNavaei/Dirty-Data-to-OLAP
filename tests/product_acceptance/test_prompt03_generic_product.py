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
    deadline = time.monotonic() + 360
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
            table_names = {
                row[0]
                for row in connection.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' ORDER BY table_name"
                ).fetchall()
            }
            assert table_names == {"dim_date", "dim_device", "dim_location", "fact_device_reading"}
            device_rows = int(connection.execute("SELECT COUNT(*) FROM dim_device").fetchone()[0])
            location_rows = int(connection.execute("SELECT COUNT(*) FROM dim_location").fetchone()[0])
            date_rows = int(connection.execute("SELECT COUNT(*) FROM dim_date").fetchone()[0])
            fact_rows, distinct_readings = connection.execute(
                "SELECT COUNT(*), COUNT(DISTINCT reading_id) FROM fact_device_reading"
            ).fetchone()
            fact_rows, distinct_readings = int(fact_rows), int(distinct_readings)
            date_values = tuple(
                str(row[0])
                for row in connection.execute("SELECT full_date FROM dim_date ORDER BY full_date").fetchall()
            )
            fact_values = tuple(
                (row[0], str(row[1]), Decimal(str(row[2])))
                for row in connection.execute(
                    """
                    SELECT f.reading_id, d.full_date, f.temperature
                    FROM fact_device_reading AS f
                    JOIN dim_date AS d ON d.date_key = f.date_key
                    ORDER BY f.reading_id
                    """
                ).fetchall()
            )
            max_temperature = Decimal(str(connection.execute("SELECT MAX(temperature) FROM fact_device_reading").fetchone()[0]))
            by_date = {
                str(row[0]): Decimal(str(row[1]))
                for row in connection.execute(
                    """
                    SELECT d.full_date, MAX(f.temperature)
                    FROM fact_device_reading AS f
                    JOIN dim_date AS d ON d.date_key = f.date_key
                    GROUP BY d.full_date
                    ORDER BY d.full_date
                    """
                ).fetchall()
            }
            same_name_ids = {
                row[0]
                for row in connection.execute(
                    "SELECT device_id FROM dim_device WHERE device_name = 'Pump A' ORDER BY device_id"
                ).fetchall()
            }
        oracle = yaml.safe_load(ORACLE.read_text(encoding="utf-8"))
        expected = oracle["expected"]
        assert device_rows == expected["canonical_devices"]
        assert location_rows == expected["canonical_locations"]
        assert date_rows == len(expected["temperature_by_date"])
        assert date_values == tuple(sorted(expected["temperature_by_date"]))
        assert fact_rows == expected["fact_rows"]
        assert distinct_readings == fact_rows
        assert tuple(item[0] for item in fact_values) == ("R-001", "R-002", "R-003")
        assert tuple(item[1] for item in fact_values) == ("2026-02-01", "2026-02-01", "2026-02-02")
        assert tuple(item[2] for item in fact_values) == (Decimal("10.5"), Decimal("11.0"), Decimal("9.5"))
        assert max_temperature == Decimal(expected["temperature_global_max"])
        assert by_date == {key: Decimal(value) for key, value in expected["temperature_by_date"].items()}
        assert same_name_ids == set(expected["same_name_device_ids"])
        truth_refs = platform.control_store.list_artifacts(run_id=run.run_id, artifact_kind="SourceTruthManifest", limit=10)
        assert len(truth_refs) == 1
        truth = yaml.safe_load(platform.artifact_store.read(truth_refs[0]).decode("utf-8"))
        assert len(truth["records"]) == expected["source_records"]
        assert {item["subject_type"] for item in truth["records"]} == {"device", "location", "reading"}
        assert sum(item["subject_type"] == "device" for item in truth["records"]) == expected["canonical_devices"]
        assert sum(item["subject_type"] == "location" for item in truth["records"]) == expected["canonical_locations"]
        assert sum(item["subject_type"] == "reading" for item in truth["records"]) == expected["canonical_readings"]
        assert {item["entity_type"] for item in truth["entities"]} == {"device", "location", "reading"}
        assert sum(item["entity_type"] == "device" for item in truth["entities"]) == expected["canonical_devices"]
        assert sum(item["entity_type"] == "location" for item in truth["entities"]) == expected["canonical_locations"]
        assert sum(item["entity_type"] == "reading" for item in truth["entities"]) == expected["canonical_readings"]
        assert len(truth["facts"]) == expected["fact_rows"]
        assert {item["grain_values"]["reading_id"] for item in truth["facts"]} == {"R-001", "R-002", "R-003"}
        assert all(record["grain_values"].get("reading_id") != "R-004" for record in truth["facts"])
        reading_ref_by_id = {
            item["values"]["reading_id"]: item["record_ref"]
            for item in truth["records"]
            if item["subject_type"] == "reading"
        }
        assert {item["from_record_ref"] for item in truth["relationships"]} == {
            reading_ref_by_id[key] for key in ("R-001", "R-002", "R-003")
        }
        assert reading_ref_by_id["R-004"] not in {item["from_record_ref"] for item in truth["relationships"]}
        accounting_refs = platform.control_store.list_artifacts(run_id=run.run_id, artifact_kind="RecordAccountingArtifact", limit=10)
        assert len(accounting_refs) == 1
        accounting = yaml.safe_load(platform.artifact_store.read(accounting_refs[0]).decode("utf-8"))
        scopes = {item["boundary"]: item for item in accounting["scopes"]}
        assert set(scopes) == {"SOURCE_TO_CANONICAL", "CANONICAL_TO_ANALYTICAL"}
        assert len(scopes["SOURCE_TO_CANONICAL"]["input_record_refs"]) == expected["source_records"]
        assert len(scopes["SOURCE_TO_CANONICAL"]["entries"]) == expected["source_records"]
        assert len(scopes["CANONICAL_TO_ANALYTICAL"]["input_record_refs"]) == expected["source_records"]
        assert len(scopes["CANONICAL_TO_ANALYTICAL"]["entries"]) == expected["source_records"]
        truth_record_by_id = {item["values"].get("reading_id"): item for item in truth["records"]}
        r004_record = truth_record_by_id["R-004"]
        assert r004_record["terminal_disposition"] == "EMITTED_DIRECT"
        source_entry = next(item for item in scopes["SOURCE_TO_CANONICAL"]["entries"] if item["input_record_ref"] == r004_record["record_ref"])
        assert source_entry["disposition"] == "EMITTED_DIRECT"
        assert source_entry["output_or_group_ref"] == r004_record["canonical_entity_id"]
        analytical_entry = next(item for item in scopes["CANONICAL_TO_ANALYTICAL"]["entries"] if item["input_record_ref"] == r004_record["canonical_entity_id"])
        assert analytical_entry["disposition"] == "QUARANTINED"
        assert analytical_entry["output_or_group_ref"] is None
        assert "quarantined" in analytical_entry["reason"].casefold()
        for expectation in truth["accounting_expectations"]:
            scope = scopes[expectation["boundary"]]
            entry = next(item for item in scope["entries"] if item["input_record_ref"] == expectation["input_record_ref"])
            assert entry["disposition"] == expectation["expected_disposition"]
            assert entry["output_or_group_ref"] == expectation["expected_output_or_group_ref"]
            assert expectation["reason_contains"] in entry["reason"].casefold()
        assert any(item.decision.review_checkpoint_id is ReviewCheckpoint.REVIEW_CANONICAL_IDENTITY for item in backend.list_reviews(run_id=run.run_id, subject_key=None, page_size=100, offset=0, principal=principal).items)
    finally:
        if runtime is not None:
            runtime.close()
        shutil.rmtree(workspace, ignore_errors=True)
