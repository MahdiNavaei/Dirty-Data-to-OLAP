from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from dirty_data_to_olap.application.backend import BackendService, Principal
from dirty_data_to_olap.composition import build_local_backend
from dirty_data_to_olap.domain.contracts.canonical import ReviewCheckpoint, ReviewCompatibilityContext
from dirty_data_to_olap.domain.contracts.platform import ArtifactManifest
from dirty_data_to_olap.domain.contracts.source import stable_id
from dirty_data_to_olap.entrypoints.api import create_app


AUTH = {"X-Local-Principal": "review-owner"}


def _run(platform, backend: BackendService, key: str) -> str:
    return backend.create_run(
        project_id="review-project",
        configuration_fingerprint=platform.config.configuration_fingerprint,
        git_content_commit=None,
        metadata={},
        principal=Principal("review-owner", frozenset({"runs:write"}), "LOCAL_TEST_AUTH"),
        idempotency_key=key,
    )[0].run_id


def _subject(platform, run_id: str, name: str, *, checkpoint: ReviewCheckpoint = ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS):
    artifact_id = stable_id("prompt04-subject", {"run": run_id, "name": name})
    ref = platform.artifact_store.publish(
        ArtifactManifest(
            artifact_id=artifact_id,
            run_id=run_id,
            stage_id=checkpoint.value,
            attempt_id=f"attempt-{name}",
            artifact_kind="RelationshipDecision",
            media_type="application/json",
            producer="prompt04-test",
            storage_key=f"runs/{run_id}/artifacts/{artifact_id}.json",
        ),
        json.dumps({"subject": name, "from_table": "source_a", "to_table": "source_b", "supporting_signal_refs": ["signal-1"], "missing_evidence_refs": [], "conflict_refs": []}).encode(),
    )
    platform.control_store.register_artifact(ref)
    context = ReviewCompatibilityContext(
        review_checkpoint_id=checkpoint,
        subject_stage="EVIDENCE_FUSION",
        subject_artifact_id=ref.artifact_id,
        subject_content_hash=ref.content_hash,
        subject_schema_version=ref.schema_version,
        model_version="prompt04-model-v1",
        source_schema_fingerprints={"source": "prompt04-source-v1"},
        policy_version="prompt04-policy-v1",
        domain_assertion_refs=("prompt04-assertion",),
        subject_semantic_id=f"prompt04-semantic-{name}",
        applicability_fingerprint=f"prompt04-applicability-{name}",
    )
    platform.control_store.register_review_subject_context(run_id=run_id, context=context)
    return ref


def _action(client: TestClient, run_id: str, ref, action: str, *, key: str, expected_revision: int = 0, extra: dict | None = None, checkpoint: ReviewCheckpoint = ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS):
    body = {
        "action": action,
        "subject_artifact_id": ref.artifact_id,
        "subject_content_hash": ref.content_hash,
        "rationale": f"Prompt04 reviewed action {action} for bounded synthetic evidence.",
        "expected_revision": expected_revision,
    }
    if extra:
        body.update(extra)
    return client.post(
        f"/api/v1/runs/{run_id}/reviews/{checkpoint.value}/actions",
        headers={**AUTH, "Idempotency-Key": key},
        json=body,
    )


def test_prompt04_all_six_actions_have_durable_server_effects(tmp_path: Path) -> None:
    platform, backend = build_local_backend(tmp_path)
    client = TestClient(create_app(backend), raise_server_exceptions=False)
    try:
        run_id = _run(platform, backend, "six-actions")
        accepted_ref = _subject(platform, run_id, "accepted")
        accepted = _action(client, run_id, accepted_ref, "ACCEPT", key="action-accept")
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["guard_satisfied"] is True
        assert accepted.json()["action"] == "ACCEPT"

        locked = _action(client, run_id, accepted_ref, "LOCK", key="action-lock", expected_revision=1, extra={"lock": {"schema_version": "1.0", "scope": "evidence-checkpoint", "confirm": True}})
        assert locked.status_code == 200, locked.text
        assert locked.json()["subject_state"] == "LOCKED"
        blocked_label = _action(client, run_id, accepted_ref, "LABEL", key="action-locked-label", expected_revision=2, extra={"label": {"schema_version": "1.0", "namespace": "EVIDENCE", "value": "DOMAIN_REVIEWED"}})
        assert blocked_label.status_code == 409
        assert blocked_label.json()["error"]["code"] == "REVIEW_LOCKED"

        rejected_ref = _subject(platform, run_id, "rejected")
        rejected = _action(client, run_id, rejected_ref, "REJECT", key="action-reject")
        assert rejected.status_code == 200 and rejected.json()["resulting_decision"] == "REJECTED"
        assert rejected.json()["guard_satisfied"] is False

        deferred_ref = _subject(platform, run_id, "deferred")
        deferred = _action(client, run_id, deferred_ref, "DEFER", key="action-defer")
        assert deferred.status_code == 200 and deferred.json()["resulting_decision"] == "DEFERRED"
        assert deferred.json()["guard_satisfied"] is False

        override_ref = _subject(platform, run_id, "override")
        override = _action(client, run_id, override_ref, "OVERRIDE", key="action-override", extra={"override": {"schema_version": "1.0", "target": "RELATIONSHIP_DISPOSITION", "replacement": "RETAIN_CANDIDATE", "old_value_ref": override_ref.artifact_id}})
        assert override.status_code == 200, override.text
        assert override.json()["resulting_subject_artifact_id"]
        assert override.json()["next_required_action"] == "REVIEW_REPLACEMENT_SUBJECT"

        label_ref = _subject(platform, run_id, "label")
        label = _action(client, run_id, label_ref, "LABEL", key="action-label", extra={"label": {"schema_version": "1.0", "namespace": "EVIDENCE", "value": "NEEDS_EVIDENCE"}})
        assert label.status_code == 200 and label.json()["guard_satisfied"] is False
        assert "EVIDENCE:NEEDS_EVIDENCE" in label.json()["labels"]

        history = client.get(f"/api/v1/runs/{run_id}/reviews/actions", headers=AUTH)
        assert history.status_code == 200
        assert {item["action"]["action"] for item in history.json()["items"]} == {"ACCEPT", "LOCK", "REJECT", "DEFER", "OVERRIDE", "LABEL"}
    finally:
        platform.close()


def test_prompt04_negative_controls_nc_r01_to_nc_r20(tmp_path: Path) -> None:
    platform, backend = build_local_backend(tmp_path)
    client = TestClient(create_app(backend), raise_server_exceptions=False)
    try:
        run_id = _run(platform, backend, "negative-controls")
        ref = _subject(platform, run_id, "negative")
        trusted = Principal("proxy-reader", frozenset({"runs:read", "reviews:read"}), "TRUSTED_PROXY", frozenset({"review-project"}))
        proxy = TestClient(create_app(backend, auth_mode="trusted_proxy", principal_resolver=lambda _request: trusted), raise_server_exceptions=False)
        assert proxy.post(f"/api/v1/runs/{run_id}/reviews/{ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value}/actions", headers={"Idempotency-Key": "nc-r01"}, json={"action": "ACCEPT", "subject_artifact_id": ref.artifact_id, "subject_content_hash": ref.content_hash, "rationale": "unauthorized", "expected_revision": 0}).status_code == 403
        assert proxy.post(f"/api/v1/runs/{run_id}/reviews/{ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value}/actions", headers={"Idempotency-Key": "nc-r02"}, json={"action": "OVERRIDE", "subject_artifact_id": ref.artifact_id, "subject_content_hash": ref.content_hash, "rationale": "unauthorized", "expected_revision": 0, "override": {"schema_version": "1.0", "target": "RELATIONSHIP_DISPOSITION", "replacement": "REQUIRE_REVISION", "old_value_ref": ref.artifact_id}}).status_code == 403
        assert proxy.post(f"/api/v1/runs/{run_id}/reviews/{ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value}/actions", headers={"Idempotency-Key": "nc-r03"}, json={"action": "LOCK", "subject_artifact_id": ref.artifact_id, "subject_content_hash": ref.content_hash, "rationale": "unauthorized", "expected_revision": 0, "lock": {"schema_version": "1.0", "scope": "x", "confirm": True}}).status_code == 403

        assert _action(client, run_id, ref, "ACCEPT", key="nc-r04", extra={"subject_content_hash": "0" * 64}).status_code == 409
        accepted = _action(client, run_id, ref, "ACCEPT", key="nc-r05-first")
        assert accepted.status_code == 200
        assert _action(client, run_id, ref, "DEFER", key="nc-r05-stale", expected_revision=0).status_code == 409

        other_run = _run(platform, backend, "negative-controls-other")
        other_ref = _subject(platform, other_run, "other")
        cross = _action(client, run_id, other_ref, "ACCEPT", key="nc-r06")
        assert cross.status_code in {403, 404}

        forged = client.post(f"/api/v1/runs/{run_id}/reviews/{ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value}/actions", headers={**AUTH, "Idempotency-Key": "nc-r07"}, json={"action": "ACCEPT", "subject_artifact_id": ref.artifact_id, "subject_content_hash": ref.content_hash, "rationale": "forged context", "expected_revision": 1, "context": {**backend.control_store.get_review_subject_context(run_id=run_id, checkpoint=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value, artifact_id=ref.artifact_id).model_dump(mode="json"), "policy_version": "changed"}})
        assert forged.status_code == 409

        reject_ref = _subject(platform, run_id, "reject-guard")
        reject = _action(client, run_id, reject_ref, "REJECT", key="nc-r08")
        assert reject.json()["guard_satisfied"] is False
        defer_ref = _subject(platform, run_id, "defer-guard")
        defer = _action(client, run_id, defer_ref, "DEFER", key="nc-r09")
        assert defer.json()["guard_satisfied"] is False
        label_ref = _subject(platform, run_id, "label-guard")
        label = _action(client, run_id, label_ref, "LABEL", key="nc-r10", extra={"label": {"schema_version": "1.0", "namespace": "EVIDENCE", "value": "NEEDS_EVIDENCE"}})
        assert label.json()["guard_satisfied"] is False

        lock_ref = _subject(platform, run_id, "lock-guard")
        assert _action(client, run_id, lock_ref, "LOCK", key="nc-r11", extra={"lock": {"schema_version": "1.0", "scope": "x", "confirm": True}}).status_code == 409
        assert _action(client, run_id, _subject(platform, run_id, "bad-override"), "OVERRIDE", key="nc-r12").status_code == 422
        assert client.post(f"/api/v1/runs/{run_id}/reviews/{ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value}/actions", headers={**AUTH, "Idempotency-Key": "nc-r13"}, json={"action": "OVERRIDE", "subject_artifact_id": lock_ref.artifact_id, "subject_content_hash": lock_ref.content_hash, "rationale": "unsupported field", "expected_revision": 0, "override": {"schema_version": "1.0", "target": "RELATIONSHIP_DISPOSITION", "replacement": "NON_ADDITIVE", "old_value_ref": lock_ref.artifact_id}}).status_code == 422

        lockable = _subject(platform, run_id, "locked-mutation")
        assert _action(client, run_id, lockable, "ACCEPT", key="nc-r14-accept").status_code == 200
        assert _action(client, run_id, lockable, "LOCK", key="nc-r14-lock", expected_revision=1, extra={"lock": {"schema_version": "1.0", "scope": "x", "confirm": True}}).status_code == 200
        assert _action(client, run_id, lockable, "OVERRIDE", key="nc-r14-override", expected_revision=2, extra={"override": {"schema_version": "1.0", "target": "RELATIONSHIP_DISPOSITION", "replacement": "REQUIRE_REVISION", "old_value_ref": lockable.artifact_id}}).status_code == 409

        prior = client.get(f"/api/v1/runs/{run_id}/reviews", headers=AUTH).json()["items"]
        invalidate = client.post(f"/api/v1/runs/{run_id}/reviews/{ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value}/invalidate", headers={**AUTH, "Idempotency-Key": "nc-r15"}, json={"subject_artifact_id": ref.artifact_id, "subject_content_hash": ref.content_hash, "reason": "new evidence supersedes prior decision", "expected_revision": 1})
        assert invalidate.status_code == 200
        after = client.get(f"/api/v1/runs/{run_id}/reviews", headers=AUTH).json()["items"]
        assert len(after) == len(prior) + 1 and any(item["decision"]["decision"] == "ACCEPTED" for item in after)

        replay_ref = _subject(platform, run_id, "replay")
        first = _action(client, run_id, replay_ref, "LABEL", key="nc-r16", extra={"label": {"schema_version": "1.0", "namespace": "EVIDENCE", "value": "NEEDS_EVIDENCE"}})
        replay = _action(client, run_id, replay_ref, "LABEL", key="nc-r16", extra={"label": {"schema_version": "1.0", "namespace": "EVIDENCE", "value": "NEEDS_EVIDENCE"}})
        assert first.status_code == replay.status_code == 200 and replay.json()["replayed"] is True
        changed = _action(client, run_id, replay_ref, "LABEL", key="nc-r16", extra={"label": {"schema_version": "1.0", "namespace": "IDENTITY", "value": "DO_NOT_MERGE"}})
        assert changed.status_code == 409

        corrupt = _subject(platform, run_id, "corrupt")
        platform.artifact_store._blob_path(corrupt.content_hash).write_bytes(b"tampered")
        assert _action(client, run_id, corrupt, "ACCEPT", key="nc-r18").json()["error"]["code"] == "ARTIFACT_INTEGRITY_FAILED"

        failed_g6 = _subject(platform, run_id, "failed-g6", checkpoint=ReviewCheckpoint.REVIEW_MATERIALIZATION_PLAN)
        validation_id = stable_id("prompt04-validation", run_id)
        validation_ref = platform.artifact_store.publish(ArtifactManifest(artifact_id=validation_id, run_id=run_id, stage_id="VALIDATION", attempt_id="validation-attempt", artifact_kind="ValidationReport", media_type="application/json", producer="prompt04-test", storage_key=f"runs/{run_id}/artifacts/{validation_id}.json"), b'{"g6_status":"FAIL"}')
        platform.control_store.register_artifact(validation_ref)
        assert _action(client, run_id, failed_g6, "ACCEPT", key="nc-r19", checkpoint=ReviewCheckpoint.REVIEW_MATERIALIZATION_PLAN).json()["error"]["code"] == "G6_FAILED_REVIEW_BLOCK"
        assert platform.artifact_store._blob_path(failed_g6.content_hash).read_bytes() != b"changed"
    finally:
        platform.close()


def test_prompt04_action_order_and_telemetry_are_durable(tmp_path: Path) -> None:
    platform, backend = build_local_backend(tmp_path)
    client = TestClient(create_app(backend), raise_server_exceptions=False)
    try:
        run_id = _run(platform, backend, "order-telemetry")
        ref = _subject(platform, run_id, "order-telemetry")
        label = _action(client, run_id, ref, "LABEL", key="order-label", extra={"label": {"schema_version": "1.0", "namespace": "EVIDENCE", "value": "NEEDS_EVIDENCE"}})
        assert label.status_code == 200
        accepted = _action(client, run_id, ref, "ACCEPT", key="order-accept", expected_revision=1)
        assert accepted.status_code == 200 and accepted.json()["guard_satisfied"] is True

        action_history = client.get(f"/api/v1/runs/{run_id}/reviews/actions", headers=AUTH).json()["items"]
        assert [(item["revision"], item["action"]["action"]) for item in action_history] == [(1, "LABEL"), (2, "ACCEPT")]
        review_history = client.get(f"/api/v1/runs/{run_id}/reviews", headers=AUTH).json()["items"]
        assert [(item["revision"], item["decision"]["decision"]) for item in review_history] == [(1, "ACCEPTED")]

        events = [event for event in backend.telemetry.memory.events if event.correlation.run_id == run_id]
        recorded_names = [event.event_name for event in events]
        assert "review.recorded" in recorded_names and "review.action_recorded" in recorded_names
        accepted_review_index = next(index for index, event in enumerate(events) if event.event_name == "review.recorded" and event.status == "ACCEPTED")
        accepted_action_index = next(index for index, event in enumerate(events) if event.event_name == "review.action_recorded" and event.status == "ACCEPT")
        assert accepted_review_index < accepted_action_index
        action_event = next(event for event in events if event.event_name == "review.action_recorded" and event.status == "ACCEPT")
        assert action_event.correlation.checkpoint == ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value
        review_metrics = [metric for metric in backend.telemetry.memory.metrics if metric.name == "ddo_review_lifecycle_total"]
        assert {metric.labels["result_class"] for metric in review_metrics} == {"ACCEPTED", "OK"}
    finally:
        platform.close()
