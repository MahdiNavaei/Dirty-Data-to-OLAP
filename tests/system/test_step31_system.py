"""Independent Step31 QA against the running product HTTP boundary.

These tests deliberately do not import the application composition root or
touch SQLite/DuckDB.  The validator starts an isolated Compose product and
provides its URL through ``STEP31_BASE_URL``.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

import pytest


CHECKPOINTS = (
    "REVIEW_EVIDENCE_DECISIONS",
    "REVIEW_CANONICAL_IDENTITY",
    "REVIEW_ANALYTICAL_PLAN",
    "REVIEW_MATERIALIZATION_PLAN",
)
AUTH = {"X-Local-Principal": "step31-qa"}
ROOT = Path(__file__).resolve().parents[2]
VALID_FIXTURE = ROOT / "tests" / "fixtures" / "step31_orders.csv"
DUPLICATE_FIXTURE = ROOT / "tests" / "fixtures" / "step31_duplicate_orders.csv"


class BoundaryResponse:
    def __init__(self, status: int, headers: Any, body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    def json(self) -> dict[str, Any]:
        value = json.loads(self.body.decode("utf-8"))
        assert isinstance(value, dict), value
        return value


def _base_url() -> str:
    value = os.environ.get("STEP31_BASE_URL", "").rstrip("/")
    if not value:
        pytest.fail("STEP31_BASE_URL must point at the isolated product stack")
    return value


def _request(method: str, path: str, *, body: bytes | None = None, headers: dict[str, str] | None = None) -> BoundaryResponse:
    request = Request(_base_url() + path, data=body, method=method, headers=headers or {})
    try:
        with urlopen(request, timeout=15) as response:
            return BoundaryResponse(response.status, response.headers, response.read())
    except HTTPError as error:
        return BoundaryResponse(error.code, error.headers, error.read())
    except URLError as error:
        raise AssertionError(f"product boundary was unreachable for {method} {path}: {error}") from error


def _json_request(method: str, path: str, payload: dict[str, Any] | None = None, *, extra_headers: dict[str, str] | None = None) -> BoundaryResponse:
    headers = {**AUTH, "Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    else:
        body = None
    headers.update(extra_headers or {})
    return _request(method, path, body=body, headers=headers)


def _error_code(response: BoundaryResponse) -> str:
    payload = response.json()
    error = payload.get("error")
    assert isinstance(error, dict), payload
    code = error.get("code")
    assert isinstance(code, str), payload
    return code


def _import_source(payload: bytes, suffix: str) -> dict[str, Any]:
    key = f"step31-source-{suffix}-{uuid4().hex}"
    headers = {
        **AUTH,
        "Accept": "application/json",
        "Content-Type": "text/csv",
        "X-Source-Filename": "step31-orders.csv",
        "Idempotency-Key": key,
    }
    first = _request("POST", "/api/v1/sources/import", body=payload, headers=headers)
    assert first.status == 201, (first.status, first.body)
    replay = _request("POST", "/api/v1/sources/import", body=payload, headers=headers)
    assert replay.status == 201 and replay.headers.get("Idempotency-Replayed") == "true"
    assert replay.json() == first.json()
    return first.json()


def _start_run(payload: bytes, suffix: str) -> str:
    configuration = _json_request("GET", "/api/v1/product/configuration")
    assert configuration.status == 200, configuration.body
    source = _import_source(payload, suffix)
    project_id = f"step31-{suffix}-{uuid4().hex[:10]}"
    run_key = f"step31-run-{suffix}-{uuid4().hex}"
    created = _json_request(
        "POST",
        "/api/v1/runs",
        {
            "project_id": project_id,
            "configuration_fingerprint": configuration.json()["configuration_fingerprint"],
        },
        extra_headers={"Idempotency-Key": run_key},
    )
    assert created.status == 201, (created.status, created.body)
    run_id = created.json()["run_id"]
    binding = _json_request(
        "POST",
        f"/api/v1/runs/{run_id}/source-selection",
        {
            "registry_id": source["registry_id"],
            "scope": {"included_objects": [], "excluded_objects": [], "include_views": False, "included_columns": {}},
            "extraction": {"chunk_size": 1000, "max_rows": 10000, "max_rows_scope": "SOURCE_WIDE", "null_markers": [], "preserve_raw_values": True},
            "execution_context_id": f"step31-{suffix}-{uuid4().hex}",
        },
        extra_headers={"Idempotency-Key": f"step31-bind-{suffix}-{uuid4().hex}"},
    )
    assert binding.status in {200, 201}, (binding.status, binding.body)
    prepared = _json_request(
        "POST",
        f"/api/v1/runs/{run_id}/execution/prepare",
        {"intent": {"cross_source_mapping_requested": False, "entity_resolution_requested": False, "optional_semantic_evidence_enabled": False, "learned_evidence_enabled": False}},
        extra_headers={"Idempotency-Key": f"step31-prepare-{suffix}-{uuid4().hex}"},
    )
    assert prepared.status == 200, (prepared.status, prepared.body)
    submitted = _json_request(
        "POST",
        f"/api/v1/runs/{run_id}/execution",
        extra_headers={"Idempotency-Key": f"step31-submit-{suffix}-{uuid4().hex}"},
    )
    assert submitted.status in {202, 503}, (submitted.status, submitted.body)
    return run_id


def _summary(run_id: str) -> dict[str, Any]:
    response = _json_request("GET", f"/api/v1/runs/{run_id}/product-summary")
    assert response.status == 200, (response.status, response.body)
    return response.json()


def _wait_for_pending(run_id: str, *, timeout: float = 240.0, checkpoint: str | None = None) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        summary = _summary(run_id)
        if summary["pending_reviews"] and (checkpoint is None or summary["pending_reviews"][0]["checkpoint"] == checkpoint):
            return summary
        if summary["status"] in {"FAILED", "CANCELLED", "SUCCEEDED"}:
            pytest.fail(f"run reached {summary['status']} before a review checkpoint: {summary}")
        time.sleep(0.25)
    pytest.fail(f"run did not expose a review checkpoint before deadline: {run_id}")


def _wait_terminal(run_id: str, *, timeout: float = 300.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        summary = _summary(run_id)
        if summary["status"] in {"FAILED", "CANCELLED", "SUCCEEDED"}:
            return summary
        time.sleep(0.25)
    pytest.fail(f"run did not reach a terminal state before deadline: {run_id}")


def _review(run_id: str, review: dict[str, Any], *, key: str, content_hash: str | None = None, checkpoint: str | None = None) -> BoundaryResponse:
    return _json_request(
        "POST",
        f"/api/v1/runs/{run_id}/reviews/{checkpoint or review['checkpoint']}",
        {
            "subject_artifact_id": review["subject_artifact_id"],
            "subject_content_hash": content_hash or review["subject_content_hash"],
            "decision": "ACCEPTED",
            "rationale": "Independent Step31 system review",
            "expected_revision": review["revision"],
        },
        extra_headers={"Idempotency-Key": key},
    )


def _restart_backend() -> None:
    project = os.environ.get("STEP31_COMPOSE_PROJECT")
    compose_file = os.environ.get("STEP31_COMPOSE_FILE", "compose.yml")
    if not project:
        pytest.fail("STEP31_COMPOSE_PROJECT is required for the restart scenario")
    result = subprocess.run(["docker", "compose", "-p", project, "-f", compose_file, "restart", "backend"], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        response = _request("GET", "/api/v1/health", headers={"Accept": "application/json"})
        if response.status == 200:
            return
        time.sleep(0.5)
    pytest.fail("backend did not become healthy after the controlled restart")


@pytest.mark.system
def test_qa_sys_001_happy_path_reviews_restart_g6_output_and_source_safety() -> None:
    source_before = hashlib.sha256(VALID_FIXTURE.read_bytes()).hexdigest()
    run_id = _start_run(VALID_FIXTURE.read_bytes(), "happy")
    first = _wait_for_pending(run_id)
    assert first["status"] in {"RUNNING", "NEEDS_REVIEW"}
    assert first["pending_reviews"][0]["checkpoint"] == CHECKPOINTS[0]

    current = first["pending_reviews"][0]
    wrong_hash = _review(run_id, current, key="step31-wrong-hash", content_hash="0" * 64)
    assert wrong_hash.status == 409 and _error_code(wrong_hash) == "REVIEW_SUBJECT_MISMATCH"
    wrong_checkpoint = _review(run_id, current, key="step31-wrong-checkpoint", checkpoint=CHECKPOINTS[1])
    assert wrong_checkpoint.status in {409, 422}
    assert _error_code(wrong_checkpoint) in {"REVIEW_CONTEXT_UNAVAILABLE", "REVIEW_CONTEXT_INVALID", "WRONG_REVIEW_CHECKPOINT"}

    _restart_backend()
    after_restart = _wait_for_pending(run_id)
    assert after_restart["pending_reviews"][0]["checkpoint"] == CHECKPOINTS[0]

    accepted: list[str] = []
    for index, expected_checkpoint in enumerate(CHECKPOINTS):
        pending = _wait_for_pending(run_id, checkpoint=expected_checkpoint)
        review = pending["pending_reviews"][0]
        assert review["checkpoint"] == expected_checkpoint
        key = f"step31-review-{index}"
        accepted_response = _review(run_id, review, key=key)
        assert accepted_response.status == 200, (accepted_response.status, accepted_response.body)
        replay = _review(run_id, review, key=key)
        assert replay.status == 200 and replay.headers.get("Idempotency-Replayed") == "true"
        assert replay.json() == accepted_response.json()
        resume = _json_request("POST", f"/api/v1/runs/{run_id}/resume", extra_headers={"Idempotency-Key": f"step31-resume-{index}"})
        assert resume.status in {202, 503}, (resume.status, resume.body)
        accepted.append(expected_checkpoint)

    final = _wait_terminal(run_id)
    assert final["status"] == "SUCCEEDED", json.dumps(final, indent=2, sort_keys=True)
    assert accepted == list(CHECKPOINTS)
    assert final["data_condition"]["source_rows_observed"] == 4
    assert final["validation"]["g6_status"] == "PASS"
    assert final["validation"]["g6_eligible"] is True
    assert final["output"]["validated"] is True
    assert final["output"]["row_counts"] == {"dim_date": 4, "dim_order": 4, "fact_orders": 4}

    artifacts = _json_request("GET", f"/api/v1/runs/{run_id}/artifacts", extra_headers={"Accept": "application/json"})
    assert artifacts.status == 200
    validation = next(item for item in artifacts.json()["items"] if item["artifact_kind"] == "ValidationReport")
    validation_content = _request("GET", f"/api/v1/runs/{run_id}/artifacts/{validation['artifact_id']}/content", headers=AUTH)
    assert validation_content.status == 403
    assert _error_code(validation_content) == "ARTIFACT_PAYLOAD_NOT_EXPOSED"
    validation_view = _json_request("GET", f"/api/v1/runs/{run_id}/validation/{validation['artifact_id']}")
    assert validation_view.status == 200
    report = validation_view.json()
    assert report["g6_status"] == "PASS" and report["g6_eligible"] is True
    assert hashlib.sha256(VALID_FIXTURE.read_bytes()).hexdigest() == source_before


@pytest.mark.system
def test_qa_sys_002_duplicate_identity_fails_at_dependency_boundary() -> None:
    source_before = hashlib.sha256(DUPLICATE_FIXTURE.read_bytes()).hexdigest()
    run_id = _start_run(DUPLICATE_FIXTURE.read_bytes(), "duplicate")
    final = _wait_terminal(run_id)
    assert final["status"] == "FAILED", json.dumps(final, indent=2, sort_keys=True)
    assert final["current_stage"] == "DEPENDENCY_DISCOVERY"
    assert not final["pending_reviews"]
    jobs = _json_request("GET", f"/api/v1/runs/{run_id}/jobs")
    assert jobs.status == 200
    dependency = next(item for item in jobs.json()["items"] if item.get("stage_id") == "DEPENDENCY_DISCOVERY")
    assert dependency["status"] == "FAILED" and dependency["failure_code"] == "DEPENDENCY_INCOMPLETE"
    assert hashlib.sha256(DUPLICATE_FIXTURE.read_bytes()).hexdigest() == source_before


@pytest.mark.api
def test_qa_api_001_contract_errors_idempotency_cancellation_and_project_isolation() -> None:
    health = _request("GET", "/api/v1/health")
    assert health.status == 200 and health.json()["status"] == "ok"
    openapi = _json_request("GET", "/api/v1/openapi.json")
    assert openapi.status == 200
    paths = openapi.json()["paths"]
    for path in ("/api/v1/sources/import", "/api/v1/runs", "/api/v1/runs/{run_id}/reviews/{checkpoint}", "/api/v1/runs/{run_id}/product-summary"):
        assert path in paths

    configuration = _json_request("GET", "/api/v1/product/configuration").json()["configuration_fingerprint"]
    projects = (f"step31-a-{uuid4().hex[:8]}", f"step31-b-{uuid4().hex[:8]}")
    run_a_response = _json_request("POST", "/api/v1/runs", {"project_id": projects[0], "configuration_fingerprint": configuration}, extra_headers={"Idempotency-Key": "step31-api-run-a"})
    assert run_a_response.status == 201
    replay = _json_request("POST", "/api/v1/runs", {"project_id": projects[0], "configuration_fingerprint": configuration}, extra_headers={"Idempotency-Key": "step31-api-run-a"})
    assert replay.status == 201 and replay.headers.get("Idempotency-Replayed") == "true"
    assert replay.json() == run_a_response.json()
    conflict = _json_request("POST", "/api/v1/runs", {"project_id": projects[1], "configuration_fingerprint": configuration}, extra_headers={"Idempotency-Key": "step31-api-run-a"})
    assert conflict.status == 409 and _error_code(conflict) == "IDEMPOTENCY_KEY_REUSED"
    run_a = run_a_response.json()["run_id"]

    run_b_response = _json_request("POST", "/api/v1/runs", {"project_id": projects[1], "configuration_fingerprint": configuration}, extra_headers={"Idempotency-Key": "step31-api-run-b"})
    assert run_b_response.status == 201
    run_b = run_b_response.json()["run_id"]
    filtered_a = _json_request("GET", f"/api/v1/projects/{projects[0]}/runs")
    filtered_b = _json_request("GET", f"/api/v1/projects/{projects[1]}/runs")
    assert [item["run_id"] for item in filtered_a.json()["items"]] == [run_a]
    assert [item["run_id"] for item in filtered_b.json()["items"]] == [run_b]
    assert _json_request("GET", f"/api/v1/runs/{run_a}/product-summary").status == 200
    unknown = _json_request("GET", "/api/v1/runs/not-a-real-run")
    assert unknown.status == 404 and _error_code(unknown) == "RUN_NOT_FOUND"
    malformed = _request("POST", "/api/v1/runs", body=b"{}", headers={**AUTH, "Content-Type": "application/json", "Idempotency-Key": "step31-malformed"})
    assert malformed.status == 422 and _error_code(malformed) == "INVALID_REQUEST"

    blocked = _json_request("POST", f"/api/v1/runs/{run_a}/execution", extra_headers={"Idempotency-Key": "step31-unprepared"})
    assert blocked.status == 409 and blocked.json()["status"] == "BLOCKED"
    cross_run_review = _json_request(
        "POST",
        f"/api/v1/runs/{run_b}/reviews/{CHECKPOINTS[0]}",
        {"subject_artifact_id": "artifact-from-run-a", "subject_content_hash": "a" * 64, "decision": "ACCEPTED", "rationale": "cross-run rejection", "expected_revision": 0},
        extra_headers={"Idempotency-Key": "step31-cross-run-review"},
    )
    assert cross_run_review.status in {404, 409, 422}
    assert _error_code(cross_run_review) in {"ARTIFACT_NOT_FOUND", "ARTIFACT_SCOPE_REJECTED", "REVIEW_CONTEXT_UNAVAILABLE", "REVIEW_SUBJECT_MISMATCH"}

    cancelled = _json_request("POST", f"/api/v1/runs/{run_b}/cancel", extra_headers={"Idempotency-Key": "step31-cancel"})
    assert cancelled.status in {202, 503}
    terminal = _wait_terminal(run_b, timeout=120)
    assert terminal["status"] == "CANCELLED"
