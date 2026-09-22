from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dirty_data_to_olap.application.backend import BackendService
from dirty_data_to_olap.composition import build_local_backend
from dirty_data_to_olap.domain.contracts.canonical import ReviewCheckpoint, ReviewCompatibilityContext, ReviewDecisionStatus
from dirty_data_to_olap.domain.contracts.platform import ArtifactManifest, ArtifactPublicationState, RunRecord, StageAttemptRecord, StageStatus
from dirty_data_to_olap.entrypoints.api import create_app
from tests.product_acceptance.prompt02_control_evidence import record_control_observation
from dirty_data_to_olap.platform import LocalPlatform


AUTH = {"X-Local-Principal": "reviewer-1"}


@pytest.fixture
def api_bundle(tmp_path: Path):
    platform, backend = build_local_backend(tmp_path)
    client = TestClient(create_app(backend), raise_server_exceptions=False)
    try:
        yield platform, backend, client
    finally:
        platform.close()


def create_run(bundle, *, key: str = "run-key", project_id: str = "project") -> dict:
    platform, _backend, client = bundle
    response = client.post(
        "/api/v1/runs",
        headers={**AUTH, "Idempotency-Key": key},
        json={"project_id": project_id, "configuration_fingerprint": platform.config.configuration_fingerprint},
    )
    assert response.status_code == 201, response.text
    return response.json()


def publish_artifact(bundle, run_id: str, artifact_id: str, *, kind: str = "ReviewSubject", payload: bytes = b"{}"):
    platform, _backend, _client = bundle
    manifest = ArtifactManifest(
        artifact_id=artifact_id,
        run_id=run_id,
        stage_id="EVIDENCE_FUSION",
        attempt_id="attempt-1",
        artifact_kind=kind,
        media_type="application/json",
        producer="step27-test",
        storage_key=f"runs/{run_id}/artifacts/{artifact_id}.json",
    )
    ref = platform.artifact_store.publish(manifest, payload)
    platform.control_store.register_artifact(ref)
    return ref


def review_context(ref, checkpoint: ReviewCheckpoint = ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS):
    return ReviewCompatibilityContext(
        review_checkpoint_id=checkpoint,
        subject_stage="EVIDENCE_FUSION",
        subject_artifact_id=ref.artifact_id,
        subject_content_hash=ref.content_hash,
        subject_schema_version=ref.schema_version,
        model_version="model-v1",
        source_schema_fingerprints={"source": "source-fingerprint"},
        policy_version="policy-v1",
        domain_assertion_refs=("assertion-1",),
        subject_semantic_id="subject-semantic-1",
        applicability_fingerprint="applicability-1",
    )


def test_run_control_is_idempotent_bounded_and_status_is_not_patchable(api_bundle) -> None:
    _platform, _backend, client = api_bundle
    run = create_run(api_bundle)
    replay = client.post(
        "/api/v1/runs",
        headers={**AUTH, "Idempotency-Key": "run-key"},
        json={"project_id": "project", "configuration_fingerprint": run["configuration_fingerprint"]},
    )
    assert replay.status_code == 201
    assert replay.json() == run
    assert replay.headers["Idempotency-Replayed"] == "true"
    changed = client.post(
        "/api/v1/runs",
        headers={**AUTH, "Idempotency-Key": "run-key"},
        json={"project_id": "other", "configuration_fingerprint": run["configuration_fingerprint"]},
    )
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert client.get("/api/v1/runs/unknown", headers=AUTH).status_code == 404
    listed = client.get("/api/v1/runs", headers=AUTH, params={"page_size": 1})
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1
    assert client.patch(f"/api/v1/runs/{run['run_id']}", json={"status": "SUCCEEDED"}).status_code == 405
    assert client.get("/api/v1/runs", headers=AUTH, params={"project_id": "../foreign"}).status_code == 400


def test_attempt_states_are_preserved_and_cross_run_attempts_are_hidden(api_bundle) -> None:
    platform, _backend, client = api_bundle
    run = create_run(api_bundle)
    for number, status in enumerate(StageStatus, start=1):
        platform.control_store.create_stage_attempt(
            StageAttemptRecord(
                attempt_id=f"attempt-{number}",
                run_id=run["run_id"],
                stage_id=f"STAGE-{number}",
                attempt_number=1,
                status=status,
                policy_config_fingerprint="policy-v1",
            )
        )
    response = client.get(f"/api/v1/runs/{run['run_id']}/attempts", headers=AUTH, params={"page_size": 100})
    assert response.status_code == 200
    assert {item["status"] for item in response.json()["items"]} == {status.value for status in StageStatus}
    assert client.get(f"/api/v1/runs/{run['run_id']}/attempts/unknown", headers=AUTH).status_code == 404


def test_artifacts_are_scoped_verified_and_payloads_are_not_generic(api_bundle) -> None:
    platform, _backend, client = api_bundle
    first = create_run(api_bundle)
    second = client.post(
        "/api/v1/runs",
        headers={**AUTH, "Idempotency-Key": "run-key-2"},
        json={"project_id": "project", "configuration_fingerprint": platform.config.configuration_fingerprint},
    ).json()
    ref = publish_artifact(api_bundle, first["run_id"], "artifact-1")
    listed = client.get(f"/api/v1/runs/{first['run_id']}/artifacts", headers=AUTH, params={"page_size": 1})
    assert listed.status_code == 200
    assert listed.json()["items"][0]["artifact_id"] == ref.artifact_id
    assert "storage_key" not in listed.json()["items"][0]
    assert client.get(f"/api/v1/artifacts/{ref.artifact_id}", headers=AUTH, params={"run_id": second["run_id"]}).status_code == 404
    assert client.get(f"/api/v1/runs/{first['run_id']}/artifacts/{ref.artifact_id}/content", headers=AUTH).status_code == 403
    assert client.post(f"/api/v1/runs/{first['run_id']}/artifacts/register", headers=AUTH, json={"artifact_id": "../escape"}).status_code == 422

    blob = platform.artifact_store._blob_path(ref.content_hash)
    blob.write_bytes(b"tampered")
    corrupted = client.get(f"/api/v1/artifacts/{ref.artifact_id}", headers=AUTH, params={"run_id": first["run_id"]})
    assert corrupted.status_code == 409
    assert corrupted.json()["error"]["code"] == "ARTIFACT_INTEGRITY_FAILED"
    record_control_observation("NC07", f"ARTIFACT_INTEGRITY_FAILED:{corrupted.status_code}", "tests/api/test_step27_backend.py:141")


def test_review_exact_binding_idempotency_concurrency_and_actor_boundary(api_bundle) -> None:
    platform, _backend, client = api_bundle
    run = create_run(api_bundle)
    ref = publish_artifact(api_bundle, run["run_id"], "review-subject")
    context = review_context(ref)
    platform.control_store.register_review_subject_context(run_id=run["run_id"], context=context)
    body = {"context": context.model_dump(mode="json"), "decision": "ACCEPTED", "rationale": "reviewed exact evidence", "expected_revision": 0}
    first = client.post(f"/api/v1/runs/{run['run_id']}/reviews/{ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value}", headers={**AUTH, "Idempotency-Key": "review-key"}, json=body)
    assert first.status_code == 200
    assert first.json()["decision"]["decision"] == "ACCEPTED"
    replay = client.post(f"/api/v1/runs/{run['run_id']}/reviews/{ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value}", headers={**AUTH, "Idempotency-Key": "review-key"}, json=body)
    assert replay.status_code == 200
    assert replay.json() == first.json()
    stale = client.post(f"/api/v1/runs/{run['run_id']}/reviews/{ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value}", headers={**AUTH, "Idempotency-Key": "review-key-2"}, json=body)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "REVIEW_REVISION_CONFLICT"
    record_control_observation("NC04", f"REVIEW_REVISION_CONFLICT:{stale.status_code}", "tests/api/test_step27_backend.py:159")
    spoof = {**body, "actor": "attacker"}
    assert client.post(f"/api/v1/runs/{run['run_id']}/reviews/{ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value}", headers={**AUTH, "Idempotency-Key": "review-key-3"}, json=spoof).status_code == 422
    assert client.post(f"/api/v1/runs/{run['run_id']}/reviews/{ReviewCheckpoint.REVIEW_CANONICAL_IDENTITY.value}", headers={**AUTH, "Idempotency-Key": "review-key-4"}, json=body).status_code == 422
    assert client.post(f"/api/v1/runs/{run['run_id']}/reviews/{ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value}", json=body).status_code == 401
    history = client.get(f"/api/v1/runs/{run['run_id']}/reviews", headers=AUTH)
    assert history.status_code == 200 and len(history.json()["items"]) == 1


def test_submission_without_prepared_plan_is_truthfully_blocked_and_errors_are_sanitized(api_bundle, monkeypatch) -> None:
    _platform, backend, client = api_bundle
    run = create_run(api_bundle)
    unavailable = client.post(f"/api/v1/runs/{run['run_id']}/execution", headers={**AUTH, "Idempotency-Key": "submit-key"})
    assert unavailable.status_code == 409
    assert unavailable.json()["status"] == "BLOCKED"
    assert "plan" in unavailable.json()["detail"]
    assert unavailable.json()["submission_id"] is None
    replay = client.post(f"/api/v1/runs/{run['run_id']}/execution", headers={**AUTH, "Idempotency-Key": "submit-key"})
    assert replay.status_code == 409 and replay.json() == unavailable.json()
    jobs = client.get(f"/api/v1/runs/{run['run_id']}/jobs", headers=AUTH)
    assert jobs.status_code == 200 and jobs.json()["items"] == []
    malformed = client.post("/api/v1/runs", headers={**AUTH, "Idempotency-Key": "bad-payload"}, json={"project_id": "project"})
    assert malformed.status_code == 422
    assert set(malformed.json()) == {"error"}
    assert "traceback" not in malformed.text.lower()

    def explode(_run_id: str):
        raise RuntimeError("C:\\private\\secret\\database.sqlite")

    monkeypatch.setattr(backend, "get_run", explode)
    internal = client.get("/api/v1/runs/anything", headers=AUTH)
    assert internal.status_code == 500
    assert "database.sqlite" not in internal.text
    assert "traceback" not in internal.text.lower()


def test_openapi_is_executable_versioned_and_safe(api_bundle) -> None:
    _platform, _backend, client = api_bundle
    first = client.get("/api/v1/openapi.json")
    second = client.get("/api/v1/openapi.json")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    document = first.json()
    assert "/api/v1/runs" in document["paths"]
    serialized = json.dumps(document, sort_keys=True)
    assert "C:\\" not in serialized
    assert "password=" not in serialized.lower()


def test_control_metadata_and_idempotency_survive_store_reopen(tmp_path: Path) -> None:
    platform, backend = build_local_backend(tmp_path)
    client = TestClient(create_app(backend), raise_server_exceptions=False)
    response = client.post(
        "/api/v1/runs",
        headers={**AUTH, "Idempotency-Key": "persisted-run"},
        json={
            "project_id": "project",
            "configuration_fingerprint": platform.config.configuration_fingerprint,
            "metadata": {"safe_label": "reference"},
        },
    )
    assert response.status_code == 201
    run = response.json()
    platform.close()
    reopened = LocalPlatform.from_project_root(tmp_path)
    try:
        backend2 = BackendService(
            control_store=reopened.control_store,
            artifact_store=reopened.artifact_store,
            configuration_fingerprint=reopened.config.configuration_fingerprint,
        )
        client2 = TestClient(create_app(backend2), raise_server_exceptions=False)
        replay = client2.post(
            "/api/v1/runs",
            headers={**AUTH, "Idempotency-Key": "persisted-run"},
            json={"project_id": "project", "configuration_fingerprint": reopened.config.configuration_fingerprint, "metadata": {"safe_label": "reference"}},
        )
        assert replay.status_code == 201
        assert replay.json() == run
        assert "metadata" not in replay.json()
        assert client2.get(f"/api/v1/runs/{run['run_id']}", headers=AUTH).json()["run_id"] == run["run_id"]
    finally:
        reopened.close()
