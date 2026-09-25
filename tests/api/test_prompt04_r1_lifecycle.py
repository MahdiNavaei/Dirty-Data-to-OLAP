from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dirty_data_to_olap.adapters.platform import SQLiteControlStore
from dirty_data_to_olap.application.backend import BackendError, BackendService, Principal
from dirty_data_to_olap.composition import build_local_backend
from dirty_data_to_olap.domain.contracts.canonical import ReviewCheckpoint
from dirty_data_to_olap.domain.contracts.canonical import review_subject_key
from dirty_data_to_olap.domain.contracts.review_actions import ReviewAction, ReviewLockPayload
from dirty_data_to_olap.entrypoints.api import create_app
from tests.api.test_prompt04_review_actions import AUTH, _action, _run, _subject


def _legacy_review(client: TestClient, run_id: str, ref, *, decision: str, key: str, expected_revision: int):
    return client.post(
        f"/api/v1/runs/{run_id}/reviews/{ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value}",
        headers={**AUTH, "Idempotency-Key": key},
        json={
            "subject_artifact_id": ref.artifact_id,
            "subject_content_hash": ref.content_hash,
            "decision": decision,
            "rationale": "Prompt04-R1 lifecycle control.",
            "expected_revision": expected_revision,
        },
    )


def test_nc_r21_legacy_review_cannot_mutate_locked_subject(tmp_path: Path) -> None:
    platform, backend = build_local_backend(tmp_path)
    client = TestClient(create_app(backend), raise_server_exceptions=False)
    try:
        run_id = _run(platform, backend, "nc-r21")
        ref = _subject(platform, run_id, "nc-r21")
        assert _action(client, run_id, ref, "ACCEPT", key="nc-r21-accept").status_code == 200
        assert _action(client, run_id, ref, "LOCK", key="nc-r21-lock", expected_revision=1, extra={"lock": {"schema_version": "1.0", "scope": "evidence", "confirm": True}}).status_code == 200
        response = _legacy_review(client, run_id, ref, decision="REJECTED", key="nc-r21-legacy", expected_revision=1)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "REVIEW_LOCKED"
    finally:
        platform.close()


def test_nc_r22_invalidation_clears_current_lock_without_rewriting_history(tmp_path: Path) -> None:
    platform, backend = build_local_backend(tmp_path)
    client = TestClient(create_app(backend), raise_server_exceptions=False)
    try:
        run_id = _run(platform, backend, "nc-r22")
        ref = _subject(platform, run_id, "nc-r22")
        assert _action(client, run_id, ref, "ACCEPT", key="nc-r22-accept").status_code == 200
        assert _action(client, run_id, ref, "LOCK", key="nc-r22-lock", expected_revision=1, extra={"lock": {"schema_version": "1.0", "scope": "evidence", "confirm": True}}).status_code == 200
        invalidated = client.post(
            f"/api/v1/runs/{run_id}/reviews/{ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value}/invalidate",
            headers={**AUTH, "Idempotency-Key": "nc-r22-invalidate"},
            json={"subject_artifact_id": ref.artifact_id, "subject_content_hash": ref.content_hash, "reason": "new evidence", "expected_revision": 1},
        )
        assert invalidated.status_code == 200
        key = platform.control_store.list_review_history(run_id=run_id)[0].subject_key
        current = platform.control_store.get_current_review(run_id=run_id, subject_key=key)
        state = platform.control_store.get_review_action_state(run_id=run_id, subject_key=key)
        assert current is not None and current.decision.decision.value == "INVALIDATED"
        assert state is not None and state.locked is False and state.action_revision == 3
        assert [item.decision.decision.value for item in platform.control_store.list_review_history(run_id=run_id, subject_key=key)] == ["ACCEPTED", "INVALIDATED"]
        assert [item.action.action.value for item in platform.control_store.list_review_action_history(run_id=run_id, subject_key=key)] == ["ACCEPT", "LOCK"]
    finally:
        platform.close()


def test_nc_r23_stale_action_revision_after_invalidation_is_rejected(tmp_path: Path) -> None:
    platform, backend = build_local_backend(tmp_path)
    client = TestClient(create_app(backend), raise_server_exceptions=False)
    try:
        run_id = _run(platform, backend, "nc-r23")
        ref = _subject(platform, run_id, "nc-r23")
        assert _action(client, run_id, ref, "ACCEPT", key="nc-r23-accept").status_code == 200
        invalidated = client.post(
            f"/api/v1/runs/{run_id}/reviews/{ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value}/invalidate",
            headers={**AUTH, "Idempotency-Key": "nc-r23-invalidate"},
            json={"subject_artifact_id": ref.artifact_id, "subject_content_hash": ref.content_hash, "reason": "superseded", "expected_revision": 1},
        )
        assert invalidated.status_code == 200
        stale = _action(client, run_id, ref, "LABEL", key="nc-r23-stale", expected_revision=1, extra={"label": {"schema_version": "1.0", "namespace": "EVIDENCE", "value": "DOMAIN_REVIEWED"}})
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "REVIEW_REVISION_CONFLICT"
    finally:
        platform.close()


def test_nc_r24_and_nc_r25_unaccepted_or_rejected_replacement_is_not_authorized(tmp_path: Path) -> None:
    platform, backend = build_local_backend(tmp_path)
    client = TestClient(create_app(backend), raise_server_exceptions=False)
    try:
        run_id = _run(platform, backend, "nc-r24-r25")
        ref = _subject(platform, run_id, "nc-r24-r25")
        override = _action(client, run_id, ref, "OVERRIDE", key="nc-r24-override", extra={"override": {"target": "RELATIONSHIP_DISPOSITION", "replacement": "EXCLUDE_CANDIDATE", "old_value_ref": ref.artifact_id}})
        assert override.status_code == 200
        replacement_id = override.json()["resulting_subject_artifact_id"]
        proposal_context = platform.control_store.get_review_subject_context(run_id=run_id, checkpoint=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value, artifact_id=replacement_id)
        assert proposal_context is not None
        proposal_key = review_subject_key(proposal_context)
        assert platform.control_store.get_current_review(run_id=run_id, subject_key=proposal_key) is None
        rejected = client.post(
            f"/api/v1/runs/{run_id}/reviews/{ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value}/actions",
            headers={**AUTH, "Idempotency-Key": "nc-r25-reject"},
            json={"action": "REJECT", "subject_artifact_id": replacement_id, "subject_content_hash": platform.control_store.get_artifact(replacement_id).content_hash, "rationale": "replacement rejected", "expected_revision": 0},
        )
        assert rejected.status_code == 200 and rejected.json()["resulting_decision"] == "REJECTED"
        assert platform.control_store.get_current_review(run_id=run_id, subject_key=proposal_key).decision.decision.value == "REJECTED"
    finally:
        platform.close()


def test_nc_r30_unsupported_override_target_is_rejected_and_not_advertised(tmp_path: Path) -> None:
    platform, backend = build_local_backend(tmp_path)
    client = TestClient(create_app(backend), raise_server_exceptions=False)
    try:
        run_id = _run(platform, backend, "nc-r30")
        ref = _subject(platform, run_id, "nc-r30")
        unsupported = _action(client, run_id, ref, "OVERRIDE", key="nc-r30", extra={"override": {"target": "IDENTITY_MEMBERSHIP", "replacement": "KEEP_SEPARATE", "old_value_ref": ref.artifact_id}})
        assert unsupported.status_code == 409
        assert unsupported.json()["error"]["code"] == "REVIEW_OVERRIDE_NOT_SUPPORTED"
        applicability = backend._review_action_applicability(
            run=platform.control_store.get_run(run_id),
            subject=ref,
            context=platform.control_store.get_review_subject_context(run_id=run_id, checkpoint=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value, artifact_id=ref.artifact_id),
            current=None,
            state=None,
            principal=Principal("review-owner", frozenset({"reviews:write"}), "LOCAL_TEST_AUTH"),
        )
        override_applicability = next(item for item in applicability if item.action.value == "OVERRIDE")
        assert override_applicability.supported_override_targets == ("RELATIONSHIP_DISPOSITION",) or tuple(item.value for item in override_applicability.supported_override_targets) == ("RELATIONSHIP_DISPOSITION",)
    finally:
        platform.close()


def test_nc_r31_compound_failure_does_not_leave_an_effective_untracked_review(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    platform, backend = build_local_backend(tmp_path)
    client = TestClient(create_app(backend), raise_server_exceptions=False)
    try:
        run_id = _run(platform, backend, "nc-r31")
        ref = _subject(platform, run_id, "nc-r31")
        original_audit = platform.control_store._audit

        def fail_after_compound_write(connection, event_type, **kwargs):
            if event_type == "review_lifecycle_synchronized":
                raise RuntimeError("injected compound review lifecycle failure")
            return original_audit(connection, event_type, **kwargs)

        monkeypatch.setattr(platform.control_store, "_audit", fail_after_compound_write)
        failed = _legacy_review(client, run_id, ref, decision="ACCEPTED", key="nc-r31", expected_revision=0)
        assert failed.status_code >= 500
        monkeypatch.undo()
        subject_context = platform.control_store.get_review_subject_context(run_id=run_id, checkpoint=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value, artifact_id=ref.artifact_id)
        assert subject_context is not None
        subject_key = review_subject_key(subject_context)
        assert platform.control_store.get_current_review(run_id=run_id, subject_key=subject_key) is None
        assert platform.control_store.get_review_action_state(run_id=run_id, subject_key=subject_key) is None
        assert platform.control_store.list_review_history(run_id=run_id, subject_key=subject_key) == ()
        assert platform.control_store.list_review_action_history(run_id=run_id, subject_key=subject_key) == ()
    finally:
        platform.close()


def test_nc_r32_concurrent_review_lock_and_invalidation_preserve_revisions(tmp_path: Path) -> None:
    platform, backend = build_local_backend(tmp_path)
    principal = Principal("review-owner", frozenset({"reviews:write", "runs:read"}), "LOCAL_TEST_AUTH")
    try:
        run_id = _run(platform, backend, "nc-r32")
        ref = _subject(platform, run_id, "nc-r32")

        def accept(key: str):
            return backend.review_action(run_id=run_id, checkpoint=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS, action=ReviewAction.ACCEPT, subject_artifact_id=ref.artifact_id, subject_content_hash=ref.content_hash, rationale="concurrent accept", expected_revision=0, principal=principal, idempotency_key=key)

        def capture_accept(key: str):
            try:
                return ("ok", accept(key))
            except BackendError as exc:
                return ("error", exc.code)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = tuple(pool.map(capture_accept, ("nc-r32-accept-a", "nc-r32-accept-b")))
        assert sum(kind == "ok" for kind, _value in results) == 1
        assert sum(kind == "error" for kind, _value in results) == 1
        assert next(value for kind, value in results if kind == "error") == "REVIEW_REVISION_CONFLICT"

        def lock():
            return backend.review_action(run_id=run_id, checkpoint=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS, action=ReviewAction.LOCK, subject_artifact_id=ref.artifact_id, subject_content_hash=ref.content_hash, rationale="concurrent lock", expected_revision=1, lock=ReviewLockPayload(scope="evidence", confirm=True), principal=principal, idempotency_key="nc-r32-lock")

        def invalidate():
            return backend.invalidate_review(run_id=run_id, checkpoint=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS, subject_artifact_id=ref.artifact_id, subject_content_hash=ref.content_hash, reason="concurrent invalidation", expected_revision=1, principal=principal, idempotency_key="nc-r32-invalidate")

        def capture(fn):
            try:
                return ("ok", fn())
            except BackendError as exc:
                return ("error", exc.code)

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = tuple(pool.map(capture, (lock, invalidate)))
        key = platform.control_store.list_review_history(run_id=run_id)[0].subject_key
        current = platform.control_store.get_current_review(run_id=run_id, subject_key=key)
        state = platform.control_store.get_review_action_state(run_id=run_id, subject_key=key)
        assert current is not None and state is not None
        if current.decision.decision.value == "INVALIDATED":
            assert state.locked is False
        else:
            assert current.decision.decision.value == "ACCEPTED" and state.locked is True
        assert len(platform.control_store.list_review_history(run_id=run_id, subject_key=key)) in {1, 2}
        assert sum(kind == "ok" for kind, _value in outcomes) == 1
        assert sum(kind == "error" for kind, _value in outcomes) == 1
        assert next(value for kind, value in outcomes if kind == "error") == "REVIEW_REVISION_CONFLICT"
    finally:
        platform.close()
