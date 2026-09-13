from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from dirty_data_to_olap.application.backend import BackendError, BackendService, Principal
from dirty_data_to_olap.composition import build_local_backend
from dirty_data_to_olap.domain.contracts.api import SubmissionResult
from dirty_data_to_olap.domain.contracts.canonical import ReviewCheckpoint, ReviewCompatibilityContext, ReviewDecisionStatus
from dirty_data_to_olap.domain.contracts.platform import ArtifactManifest
from dirty_data_to_olap.entrypoints.api import create_app
from dirty_data_to_olap.platform import LocalPlatform


AUTH = {"X-Local-Principal": "repair-reviewer"}


def _publish(platform: LocalPlatform, run_id: str, artifact_id: str):
    ref = platform.artifact_store.publish(
        ArtifactManifest(
            artifact_id=artifact_id,
            run_id=run_id,
            stage_id="EVIDENCE_FUSION",
            attempt_id="attempt-1",
            artifact_kind="ReviewSubject",
            media_type="application/json",
            producer="step27-repair-test",
            storage_key=f"runs/{run_id}/artifacts/{artifact_id}.json",
        ),
        b"{}",
    )
    platform.control_store.register_artifact(ref)
    return ref


def _context(ref, *, semantic: str = "server-semantic", applicability: str = "server-applicability") -> ReviewCompatibilityContext:
    return ReviewCompatibilityContext(
        review_checkpoint_id=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS,
        subject_stage="EVIDENCE_FUSION",
        subject_artifact_id=ref.artifact_id,
        subject_content_hash=ref.content_hash,
        subject_schema_version=ref.schema_version,
        model_version="server-model-v1",
        source_schema_fingerprints={"source": "server-source-fingerprint"},
        policy_version="server-policy-v1",
        domain_assertion_refs=("server-assertion",),
        subject_semantic_id=semantic,
        applicability_fingerprint=applicability,
    )


def _run(platform: LocalPlatform, backend: BackendService, key: str = "repair-run") -> str:
    return backend.create_run(
        project_id="repair-project",
        configuration_fingerprint=platform.config.configuration_fingerprint,
        git_content_commit=None,
        metadata={},
        principal=Principal("repair-reviewer", frozenset({"runs:write"}), "LOCAL_TEST_AUTH"),
        idempotency_key=key,
    )[0].run_id


def _review_body(ref, *, context: ReviewCompatibilityContext | None = None, rationale: str = "server-authoritative review") -> dict:
    body = {
        "subject_artifact_id": ref.artifact_id,
        "subject_content_hash": ref.content_hash,
        "decision": "ACCEPTED",
        "rationale": rationale,
        "expected_revision": 0,
    }
    if context is not None:
        body["context"] = context.model_dump(mode="json")
    return body


def test_review_context_is_server_derived_and_forgery_cannot_create_parallel_stream(tmp_path: Path) -> None:
    platform, backend = build_local_backend(tmp_path)
    client = TestClient(create_app(backend), raise_server_exceptions=False)
    try:
        run_id = _run(platform, backend)
        ref = _publish(platform, run_id, "repair-subject")
        authoritative = _context(ref)
        platform.control_store.register_review_subject_context(run_id=run_id, context=authoritative)
        path = f"/api/v1/runs/{run_id}/reviews/{ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value}"
        accepted = client.post(path, headers={**AUTH, "Idempotency-Key": "authoritative"}, json=_review_body(ref))
        assert accepted.status_code == 200
        decision = accepted.json()["decision"]
        for field, value in {
            "subject_stage": "FORGED_STAGE",
            "subject_schema_version": "FORGED_SCHEMA",
            "model_version": "FORGED_MODEL",
            "source_schema_fingerprints": {"source": "FORGED_SOURCE"},
            "policy_version": "FORGED_POLICY",
            "domain_assertion_refs": ("FORGED_ASSERTION",),
            "subject_semantic_id": "FORGED_SEMANTIC",
            "applicability_fingerprint": "FORGED_APPLICABILITY",
        }.items():
            forged = authoritative.model_copy(update={field: value})
            response = client.post(path, headers={**AUTH, "Idempotency-Key": f"forged-{field}"}, json=_review_body(ref, context=forged))
            assert response.status_code == 409, (field, response.text)
            assert response.json()["error"]["code"] == "REVIEW_CONTEXT_MISMATCH"
        assert all(decision[field] == authoritative.model_dump(mode="json")[field] for field in ("subject_stage", "subject_schema_version", "model_version", "source_schema_fingerprints", "policy_version", "domain_assertion_refs", "subject_semantic_id", "applicability_fingerprint"))
        assert client.get(f"/api/v1/runs/{run_id}/reviews", headers=AUTH).json()["items"]
        assert len(client.get(f"/api/v1/runs/{run_id}/reviews", headers=AUTH).json()["items"]) == 1
        unsupported = client.post(path, headers={**AUTH, "Idempotency-Key": "skip"}, json={**_review_body(ref), "decision": "SKIPPED"})
        assert unsupported.status_code == 422
        assert unsupported.json()["error"]["code"] == "INVALID_REQUEST"
        assert "SKIPPED" not in client.get("/api/v1/openapi.json").json()["components"]["schemas"]["ReviewActionDecision"].get("enum", [])
    finally:
        platform.close()


def test_review_atomic_mutation_replays_after_post_commit_failure_and_reopen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    platform, backend = build_local_backend(tmp_path)
    client = TestClient(create_app(backend), raise_server_exceptions=False)
    run_id = _run(platform, backend)
    ref = _publish(platform, run_id, "atomic-subject")
    context = _context(ref)
    platform.control_store.register_review_subject_context(run_id=run_id, context=context)
    original = platform.control_store.record_review_with_idempotency
    failed = False

    def fail_after_commit(*args, **kwargs):
        nonlocal failed
        result = original(*args, **kwargs)
        if not failed:
            failed = True
            raise RuntimeError("injected post-commit process failure")
        return result

    monkeypatch.setattr(platform.control_store, "record_review_with_idempotency", fail_after_commit)
    path = f"/api/v1/runs/{run_id}/reviews/{ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value}"
    body = _review_body(ref, context=context)
    first = client.post(path, headers={**AUTH, "Idempotency-Key": "atomic-review"}, json=body)
    assert first.status_code == 500
    committed_decision_id = platform.control_store.list_review_history(run_id=run_id)[0].decision.review_decision_id
    platform.close()
    reopened = LocalPlatform.from_project_root(tmp_path)
    try:
        backend2 = BackendService(control_store=reopened.control_store, artifact_store=reopened.artifact_store, configuration_fingerprint=reopened.config.configuration_fingerprint)
        replay = TestClient(create_app(backend2), raise_server_exceptions=False).post(path, headers={**AUTH, "Idempotency-Key": "atomic-review"}, json=body)
        assert replay.status_code == 200
        assert replay.json()["decision"]["review_decision_id"] == committed_decision_id
        history = backend2.control_store.list_review_history(run_id=run_id)
        assert len(history) == 1 and history[0].revision == 1
    finally:
        reopened.close()


def test_run_atomic_idempotency_replays_after_post_commit_failure_and_reopen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    platform, backend = build_local_backend(tmp_path)
    client = TestClient(create_app(backend), raise_server_exceptions=False)
    original = platform.control_store.create_run_with_idempotency
    failed = False

    def fail_after_commit(*args, **kwargs):
        nonlocal failed
        result = original(*args, **kwargs)
        if not failed:
            failed = True
            raise RuntimeError("injected post-commit run failure")
        return result

    monkeypatch.setattr(platform.control_store, "create_run_with_idempotency", fail_after_commit)
    body = {"project_id": "repair-project", "configuration_fingerprint": platform.config.configuration_fingerprint}
    first = client.post("/api/v1/runs", headers={**AUTH, "Idempotency-Key": "atomic-run"}, json=body)
    assert first.status_code == 500
    platform.close()
    reopened = LocalPlatform.from_project_root(tmp_path)
    try:
        backend2 = BackendService(control_store=reopened.control_store, artifact_store=reopened.artifact_store, configuration_fingerprint=reopened.config.configuration_fingerprint)
        replay = TestClient(create_app(backend2), raise_server_exceptions=False).post("/api/v1/runs", headers={**AUTH, "Idempotency-Key": "atomic-run"}, json=body)
        assert replay.status_code == 201
        assert len(backend2.control_store.list_runs()) == 1
    finally:
        reopened.close()


def test_multi_instance_same_key_has_one_review_revision_and_changed_payload_conflicts(tmp_path: Path) -> None:
    platform, backend1 = build_local_backend(tmp_path)
    run_id = _run(platform, backend1, key="multi-instance-run")
    ref = _publish(platform, run_id, "multi-instance-subject")
    context = _context(ref)
    platform.control_store.register_review_subject_context(run_id=run_id, context=context)
    from dirty_data_to_olap.adapters.platform import SQLiteControlStore

    store2 = SQLiteControlStore(platform.control_store.path, project_root=tmp_path)
    backend2 = BackendService(control_store=store2, artifact_store=platform.artifact_store, configuration_fingerprint=platform.config.configuration_fingerprint)
    principal = Principal("multi-reviewer", frozenset({"reviews:write"}), "LOCAL_TEST_AUTH")

    def submit(backend: BackendService):
        return backend.review(run_id=run_id, checkpoint=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS, context=None, subject_artifact_id=ref.artifact_id, subject_content_hash=ref.content_hash, decision=ReviewDecisionStatus.ACCEPTED, rationale="same logical review", expected_revision=0, principal=principal, idempotency_key="same-review-key")

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = tuple(pool.map(submit, (backend1, backend2)))
        assert all(isinstance(result, tuple) for result in results)
        assert {result[0].decision.review_decision_id for result in results}.__len__() == 1
        history = backend1.control_store.list_review_history(run_id=run_id)
        assert len(history) == 1 and history[0].revision == 1
        with pytest.raises(BackendError, match="different semantic request"):
            backend2.review(run_id=run_id, checkpoint=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS, context=None, subject_artifact_id=ref.artifact_id, subject_content_hash=ref.content_hash, decision=ReviewDecisionStatus.ACCEPTED, rationale="changed logical review", expected_revision=0, principal=principal, idempotency_key="same-review-key")
    finally:
        store2.close()
        platform.close()


class _RecordingExecutor:
    def __init__(self, *, fail: bool = False) -> None:
        self.commands = []
        self.fail = fail

    def submit_command(self, *, command, run):
        self.commands.append(command)
        if self.fail:
            raise RuntimeError("executor accepted delivery then failed before response completion")
        return SubmissionResult(run_id=run.run_id, command_id=command.command_id, status="ACCEPTED", detail="accepted by deterministic test executor", submission_id="submission-1")


def test_execution_command_identity_survives_completion_failure_without_exactly_once_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    platform, backend = build_local_backend(tmp_path)
    executor = _RecordingExecutor()
    backend.execution = executor
    run_id = _run(platform, backend, key="execution-run")
    original = platform.control_store.complete_idempotency
    failed = False

    def fail_completion(record):
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("injected response finalization failure")
        return original(record)

    monkeypatch.setattr(platform.control_store, "complete_idempotency", fail_completion)
    principal = Principal("execution-user", frozenset({"runs:write"}), "LOCAL_TEST_AUTH")
    try:
        first, replayed = backend.submit(run_id=run_id, principal=principal, idempotency_key="execution-key")
        second, second_replayed = backend.submit(run_id=run_id, principal=principal, idempotency_key="execution-key")
        assert first.status == second.status == "DELIVERY_UNKNOWN"
        assert first.command_id == second.command_id
        assert replayed is False and second_replayed is True
        assert len(executor.commands) == 1
        assert executor.commands[0].command_id == first.command_id
    finally:
        platform.close()


def test_execution_reservation_race_delivers_at_most_one_command_identity(tmp_path: Path) -> None:
    platform, backend1 = build_local_backend(tmp_path)
    executor = _RecordingExecutor()
    backend1.execution = executor
    run_id = _run(platform, backend1, key="execution-race-run")
    from dirty_data_to_olap.adapters.platform import SQLiteControlStore

    store2 = SQLiteControlStore(platform.control_store.path, project_root=tmp_path)
    backend2 = BackendService(control_store=store2, artifact_store=platform.artifact_store, execution=executor, configuration_fingerprint=platform.config.configuration_fingerprint)
    principal = Principal("execution-race", frozenset({"runs:write"}), "LOCAL_TEST_AUTH")

    def submit(backend: BackendService):
        return backend.submit(run_id=run_id, principal=principal, idempotency_key="execution-race-key")[0]

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = tuple(pool.map(submit, (backend1, backend2)))
        assert len({result.command_id for result in results}) == 1
        assert len(executor.commands) == 1
    finally:
        store2.close()
        platform.close()


def test_trusted_proxy_requires_read_scopes_while_health_stays_public(tmp_path: Path) -> None:
    platform, backend = build_local_backend(tmp_path)
    run_id = _run(platform, backend, key="auth-run")
    try:
        anonymous = TestClient(create_app(backend, auth_mode="trusted_proxy", principal_resolver=lambda _request: None), raise_server_exceptions=False)
        assert anonymous.get(f"/api/v1/runs/{run_id}").status_code == 401
        insufficient = TestClient(create_app(backend, auth_mode="trusted_proxy", principal_resolver=lambda _request: Principal("reader", frozenset(), "TRUSTED_PROXY")), raise_server_exceptions=False)
        assert insufficient.get(f"/api/v1/runs/{run_id}").status_code == 403
        read_only = TestClient(create_app(backend, auth_mode="trusted_proxy", principal_resolver=lambda _request: Principal("reader", frozenset({"runs:read"}), "TRUSTED_PROXY")), raise_server_exceptions=False)
        response = read_only.get(f"/api/v1/runs/{run_id}")
        assert response.status_code == 200
        assert "scopes" not in response.text and "TRUSTED_PROXY" not in response.text
        assert read_only.post("/api/v1/runs", headers={"Idempotency-Key": "forbidden-mutation"}, json={"project_id": "p", "configuration_fingerprint": platform.config.configuration_fingerprint}).status_code == 403
        assert read_only.get("/api/v1/health").status_code == 200
    finally:
        platform.close()


def test_v3_store_migrates_to_v4_and_reopen_repairs_partial_capability(tmp_path: Path) -> None:
    platform, backend = build_local_backend(tmp_path)
    run_id = _run(platform, backend, key="migration-run")
    path = platform.control_store.path
    platform.close()
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE schema_meta SET schema_version = 3")
        connection.execute("DROP TABLE review_subject_contexts")
        connection.execute("ALTER TABLE api_idempotency RENAME TO api_idempotency_step27")
        connection.execute("CREATE TABLE api_idempotency (scope TEXT NOT NULL, idem_key TEXT NOT NULL, request_fingerprint TEXT NOT NULL, response_status INTEGER NOT NULL, response_body TEXT NOT NULL, resource_id TEXT, created_at TEXT NOT NULL, PRIMARY KEY(scope, idem_key))")
        connection.execute("INSERT INTO api_idempotency SELECT scope, idem_key, request_fingerprint, response_status, response_body, resource_id, created_at FROM api_idempotency_step27")
        connection.execute("DROP TABLE api_idempotency_step27")
    from dirty_data_to_olap.adapters.platform import SQLiteControlStore

    migrated = SQLiteControlStore(path, project_root=tmp_path)
    try:
        assert migrated.schema_version == 6
        assert migrated.get_run(run_id) is not None
        tables = {row[0] for row in sqlite3.connect(path).execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"api_idempotency", "review_current", "review_history", "review_subject_contexts", "execution_plans", "jobs"}.issubset(tables)
        migration_rows = sqlite3.connect(path).execute("SELECT version_from, version_to FROM schema_migrations WHERE version_from = 3 AND version_to = 4").fetchall()
        assert migration_rows
        assert sqlite3.connect(path).execute("SELECT 1 FROM schema_migrations WHERE version_from = 4 AND version_to = 5").fetchone()
    finally:
        migrated.close()
    reopened = SQLiteControlStore(path, project_root=tmp_path)
    try:
        assert reopened.schema_version == 6
        assert reopened.get_run(run_id) is not None
    finally:
        reopened.close()
