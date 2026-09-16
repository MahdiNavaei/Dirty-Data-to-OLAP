"""Executable Step34 telemetry contract and real-runtime scenarios."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import time

import pytest
from fastapi.testclient import TestClient

from dirty_data_to_olap.application.product_runtime import build_local_product
from dirty_data_to_olap.application.jobs import JobWorker
from dirty_data_to_olap.entrypoints.api import create_app
from dirty_data_to_olap.observability import (
    ALLOWED_METRIC_LABELS,
    BoundedErrorClass,
    FORBIDDEN_METRIC_LABELS,
    InMemoryTelemetrySink,
    METRIC_DEFINITIONS,
    TelemetryClient,
    redact_value,
)
from dirty_data_to_olap.domain.contracts.canonical import ReviewCheckpoint, ReviewCompatibilityContext
from dirty_data_to_olap.domain.contracts.jobs import JobKind, JobRecord, StageSpec, StageResultStatus


CSV = b"""order_id,customer_id,customer_id_ref,order_date,quantity,unit_price
O-100,C-1,C-1,2026-01-02,2,10.50
O-101,C-2,C-2,2026-01-03,1,7.25
O-102,C-3,C-3,2026-01-04,4,3.00
O-103,C-4,C-4,2026-01-05,3,12.00
"""


class FailingSink:
    def emit_event(self, _event) -> None:
        raise RuntimeError("sink unavailable")

    def observe_metric(self, _sample) -> None:
        raise RuntimeError("sink unavailable")

    def record_span(self, _span) -> None:
        raise RuntimeError("sink unavailable")


def _start_run(client: TestClient, *, headers: dict[str, str], suffix: str) -> str:
    configuration = client.get("/api/v1/product/configuration", headers=headers)
    assert configuration.status_code == 200, configuration.text
    imported = client.post(
        "/api/v1/sources/import",
        headers={**headers, "X-Source-Filename": "orders.csv", "Content-Type": "text/csv", "Idempotency-Key": f"obs-{suffix}-import"},
        content=CSV,
    )
    assert imported.status_code == 201, imported.text
    created = client.post(
        "/api/v1/runs",
        headers={**headers, "Idempotency-Key": f"obs-{suffix}-run", "X-Request-ID": f"obs-request-{suffix}"},
        json={"project_id": f"obs-{suffix}", "configuration_fingerprint": configuration.json()["configuration_fingerprint"]},
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["run_id"]
    bound = client.post(
        f"/api/v1/runs/{run_id}/source-selection",
        headers={**headers, "Idempotency-Key": f"obs-{suffix}-bind"},
        json={"registry_id": imported.json()["registry_id"], "execution_context_id": f"obs-{suffix}"},
    )
    assert bound.status_code in (200, 201), bound.text
    prepared = client.post(
        f"/api/v1/runs/{run_id}/execution/prepare",
        headers={**headers, "Idempotency-Key": f"obs-{suffix}-prepare"},
        json={"intent": {"cross_source_mapping_requested": False, "entity_resolution_requested": False, "optional_semantic_evidence_enabled": False, "learned_evidence_enabled": False}},
    )
    assert prepared.status_code == 200, prepared.text
    submitted = client.post(f"/api/v1/runs/{run_id}/execution", headers={**headers, "Idempotency-Key": f"obs-{suffix}-submit"})
    assert submitted.status_code in (200, 202), submitted.text
    return run_id


def _poll_run(client: TestClient, *, headers: dict[str, str], run_id: str, suffix: str) -> dict:
    accepted: set[str] = set()
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        summary_response = client.get(f"/api/v1/runs/{run_id}/product-summary", headers=headers)
        assert summary_response.status_code == 200, summary_response.text
        summary = summary_response.json()
        if summary["pending_reviews"]:
            review = summary["pending_reviews"][0]
            if review["checkpoint"] not in accepted:
                action = client.post(
                    f"/api/v1/runs/{run_id}/reviews/{review['checkpoint']}",
                    headers={**headers, "Idempotency-Key": f"obs-{suffix}-review-{len(accepted)}"},
                    json={"subject_artifact_id": review["subject_artifact_id"], "subject_content_hash": review["subject_content_hash"], "decision": "ACCEPTED", "rationale": "bounded observability scenario review", "expected_revision": review["revision"]},
                )
                assert action.status_code == 200, action.text
                resumed = client.post(f"/api/v1/runs/{run_id}/resume", headers={**headers, "Idempotency-Key": f"obs-{suffix}-resume-{len(accepted)}"})
                assert resumed.status_code in (200, 202), resumed.text
                accepted.add(review["checkpoint"])
        if summary["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            # A terminal run status is persisted before the bounded local
            # worker necessarily finishes finalizing already-queued jobs.
            # Wait for the durable job projection to quiesce so the semantic
            # equivalence assertion cannot compare one snapshot mid-pump with
            # another after the pump has drained.
            active_statuses = {"QUEUED", "RUNNING", "RETRY_WAIT"}
            if not any(item["status"] in active_statuses for item in summary["stages"]):
                return summary
        time.sleep(0.2)
    raise AssertionError(f"run did not reach a terminal state: {run_id}")


def test_obs_met_003_every_metric_has_bounded_labels() -> None:
    names = set()
    for definition in METRIC_DEFINITIONS:
        assert definition.name not in names
        names.add(definition.name)
        assert set(definition.allowed_labels) <= ALLOWED_METRIC_LABELS
        assert not FORBIDDEN_METRIC_LABELS.intersection(definition.allowed_labels)
        assert definition.metric_type and definition.unit and definition.description and definition.aggregation
    telemetry = TelemetryClient()
    with pytest.raises(ValueError):
        telemetry.metric("ddo_queue_jobs", 1, labels={"queue_state": "unbounded-user-value"})


def test_obs_log_003_redaction_removes_secret_pii_and_paths() -> None:
    canary = "password=STEP34_SECRET_CANARY email@example.com C:\\private\\source.csv"
    result = redact_value({"detail": canary, "nested": [canary]})
    encoded = json.dumps(result)
    assert "STEP34_SECRET_CANARY" not in encoded
    assert "email@example.com" not in encoded
    assert "private\\source.csv" not in encoded


def test_obs_fail_002_003_exporter_failure_isolated_and_degraded() -> None:
    telemetry = TelemetryClient(sinks=(FailingSink(),))
    correlation = telemetry.context(run_id="run-observability")
    telemetry.emit_event(event_name="test.event", component="test", operation="sink", correlation=correlation)
    telemetry.metric("ddo_worker_activity_total", 1, labels={"activity": "idle"})
    with telemetry.span("test.operation", correlation):
        pass
    assert telemetry.exporter_failures == 3
    assert telemetry.degradation_notes
    assert len(telemetry.memory.events) == 1
    assert telemetry.memory.metrics[-1].name == "ddo_telemetry_exporter_failures_total"
    assert len(telemetry.memory.spans) == 1


def test_obs_trace_003_sampling_is_deterministic_and_configured() -> None:
    always = TelemetryClient(sample_rate=1.0)
    never = TelemetryClient(sample_rate=0.0)
    correlation = always.context(run_id="run-trace")
    with always.span("test.boundary", correlation):
        pass
    with never.span("test.boundary", never.context(run_id="run-trace")):
        pass
    assert len(always.memory.spans) == 1
    assert len(never.memory.spans) == 0
    assert all("password" not in json.dumps(span.model_dump(mode="json")).lower() for span in always.memory.spans)


def test_obs_corr_003_review_pause_is_correlated_on_the_real_worker_boundary() -> None:
    class Deriver:
        def __init__(self, context: ReviewCompatibilityContext) -> None:
            self.context = context

        def derive(self, *, run_id: str, checkpoint: ReviewCheckpoint, upstream_artifacts: tuple):
            del run_id, checkpoint, upstream_artifacts
            return type("Derivation", (), {"contexts": (self.context,), "detail": ""})()

    class Store:
        def register_review_subject_context(self, *, run_id: str, context: ReviewCompatibilityContext):
            del run_id, context

        def get_current_review(self, *, run_id: str, subject_key: str):
            del run_id, subject_key
            return None

    telemetry = TelemetryClient()
    context = ReviewCompatibilityContext(
        review_checkpoint_id=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS,
        subject_stage="EVIDENCE_FUSION",
        subject_artifact_id="artifact-review",
        subject_content_hash="a" * 64,
        subject_schema_version="1.0",
        model_version="observability-test",
        source_schema_fingerprints={"source": "schema"},
        policy_version="policy-v1",
        subject_semantic_id="semantic-review",
        applicability_fingerprint="applicability-v1",
    )
    worker = JobWorker(control_store=Store(), artifact_store=object(), executor=object(), review_subject_deriver=Deriver(context), telemetry=telemetry)
    job = JobRecord(job_id="job-review", run_id="run-review", job_kind=JobKind.STAGE, stage_id="REVIEW_EVIDENCE_DECISIONS", plan_id="plan-review")
    result = worker._review_stage_result(job, StageSpec(stage_id="REVIEW_EVIDENCE_DECISIONS", handler_key="review", review_checkpoint=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS))
    assert result.status is StageResultStatus.NEEDS_REVIEW
    assert any(event.event_name == "review.waiting" and event.correlation.run_id == "run-review" for event in telemetry.memory.events)


def test_obs_corr_001_to_004_and_obs_diag_real_product_path() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    telemetry = TelemetryClient()
    with tempfile.TemporaryDirectory(prefix="step34-observability-") as temporary:
        platform, backend, runtime = build_local_product(Path(temporary), graph_root=repository_root, telemetry=telemetry)
        try:
            client = TestClient(create_app(backend))
            headers = {"X-Local-Principal": "obs-owner"}
            run_id = _start_run(client, headers=headers, suffix="real")
            final = _poll_run(client, headers=headers, run_id=run_id, suffix="real")
            event_names = {event.event_name for event in telemetry.memory.events if event.correlation.run_id == run_id}
            assert {"run.created", "job.claimed", "stage.attempt_created", "stage.delivery_started", "stage.result_recorded", "job.finalized"} <= event_names
            all_event_names = {event.event_name for event in telemetry.memory.events}
            assert "api.request" in all_event_names
            required_event_fields = {"timestamp", "level", "event_name", "component", "operation", "correlation", "status", "error_class", "retry_count", "duration_ms", "details"}
            assert all(required_event_fields <= set(event.model_dump()) for event in telemetry.memory.events)
            assert all(event.correlation.run_id == run_id for event in telemetry.memory.events if event.correlation.run_id == run_id)
            assert any(event.correlation.request_id == "obs-request-real" and event.event_name == "run.created" for event in telemetry.memory.events)
            assert any(span.name == "adapter.operation" and span.correlation.run_id == run_id for span in telemetry.memory.spans)
            assert any(sample.name == "ddo_queue_jobs" for sample in telemetry.memory.metrics)
            assert any(sample.name == "ddo_stage_duration_seconds" for sample in telemetry.memory.metrics)
            diagnostics = client.get(f"/api/v1/runs/{run_id}/diagnostics", headers=headers)
            assert diagnostics.status_code == 200, diagnostics.text
            body = diagnostics.json()
            assert body["run_id"] == run_id and body["run_status"] == final["status"]
            assert all(item["integrity_state"] in {"VERIFIED", "MISSING", "HASH_MISMATCH", "SIZE_MISMATCH", "UNREADABLE", "EXTERNAL_UNAVAILABLE", "NOT_CHECKED"} for item in body["artifacts"])
            serialized = json.dumps(body, sort_keys=True)
            assert "C-1" not in serialized and "password" not in serialized.lower()
            foreign = client.get(f"/api/v1/runs/{run_id}/diagnostics", headers={"X-Local-Principal": "obs-foreign"})
            assert foreign.status_code == 404
            if final["status"] == "SUCCEEDED":
                assert {"review.recorded", "review.resumed"} <= all_event_names
                assert any(item.name == "ddo_validation_outcomes_total" for item in telemetry.memory.metrics)
            else:
                # The local reference environment may not have the optional
                # Desbordante provider.  Its real failure remains evidence,
                # never a fabricated success or a skipped runtime.
                assert final["current_stage"] == "DEPENDENCY_DISCOVERY"
                assert any(event.error_class is not None for event in telemetry.memory.events if event.correlation.run_id == run_id)
                assert any(sample.name == "ddo_stage_duration_seconds" and sample.labels.get("result_class") == "FAILED" for sample in telemetry.memory.metrics)
                assert any(sample.name == "ddo_adapter_operation_duration_seconds" and sample.labels.get("result_class") == "FAILED" for sample in telemetry.memory.metrics)
        finally:
            runtime.close()
            platform.close()


def _semantic_product_projection(summary: dict) -> dict:
    return {
        "status": summary["status"],
        "current_stage": summary["current_stage"],
        "planning_phase": summary["planning_phase"],
        "data_condition": summary["data_condition"],
        "canonical": {key: summary["canonical"][key] for key in ("state", "identity_basis", "membership_count")},
        "analytical": {key: summary["analytical"][key] for key in ("state", "measure_semantics")},
        "materialization": {key: summary["materialization"][key] for key in ("status", "row_counts", "table_names", "target_type", "usable")},
        "output": {key: summary["output"][key] for key in ("row_counts", "table_names", "validated")},
        "validation": {key: summary["validation"][key] for key in ("overall_status", "passed_check_count", "failed_check_count", "g6_eligible")},
        "stages": tuple((item["stage_id"], item["status"], item["required"], item["selected"]) for item in summary["stages"]),
        "review_checkpoints": tuple(item["checkpoint"] for item in summary["pending_reviews"]),
    }


def test_obs_eqv_001_observability_on_and_off_preserve_real_product_semantics(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    projections = []
    for suffix, enabled in (("enabled", True), ("disabled", False)):
        root = tmp_path / suffix
        platform, backend, runtime = build_local_product(root, graph_root=repository_root, telemetry=TelemetryClient(enabled=enabled))
        try:
            client = TestClient(create_app(backend))
            headers = {"X-Local-Principal": f"eqv-{suffix}"}
            run_id = _start_run(client, headers=headers, suffix=f"eqv-{suffix}")
            projections.append(_semantic_product_projection(_poll_run(client, headers=headers, run_id=run_id, suffix=f"eqv-{suffix}")))
        finally:
            runtime.close()
            platform.close()
    assert projections[0] == projections[1]


def test_obs_eqv_001_observability_off_has_no_product_contract_dependency(tmp_path: Path) -> None:
    telemetry = TelemetryClient(enabled=False)
    correlation = telemetry.context(run_id="run-semantic")
    telemetry.operation(event_name="off", component="test", operation="disabled", correlation=correlation, status="OK")
    assert telemetry.memory.events == [] and telemetry.memory.metrics == [] and telemetry.memory.spans == []
    assert BoundedErrorClass.INTERNAL.value == "INTERNAL"


def test_obs_corr_004_durable_ids_survive_runtime_reconstruction(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    telemetry = TelemetryClient()
    platform, backend, runtime = build_local_product(tmp_path, graph_root=repository_root, telemetry=telemetry)
    try:
        client = TestClient(create_app(backend))
        headers = {"X-Local-Principal": "restart-owner"}
        configuration = client.get("/api/v1/product/configuration", headers=headers).json()
        created = client.post("/api/v1/runs", headers={**headers, "Idempotency-Key": "restart-run"}, json={"project_id": "restart-project", "configuration_fingerprint": configuration["configuration_fingerprint"]})
        assert created.status_code == 201
        run_id = created.json()["run_id"]
    finally:
        runtime.close()
        platform.close()
    platform2, backend2, runtime2 = build_local_product(tmp_path, graph_root=repository_root, telemetry=TelemetryClient())
    try:
        reopened = TestClient(create_app(backend2)).get(f"/api/v1/runs/{run_id}/diagnostics", headers={"X-Local-Principal": "restart-owner"})
        assert reopened.status_code == 200 and reopened.json()["run_id"] == run_id
    finally:
        runtime2.close()
        platform2.close()
