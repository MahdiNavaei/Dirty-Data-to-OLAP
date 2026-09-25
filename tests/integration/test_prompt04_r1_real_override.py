"""Prompt04-R1 proof that a reviewed override reaches the real product stage."""

from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from dirty_data_to_olap.application.product_runtime import build_local_product
from dirty_data_to_olap.domain.contracts.canonical import CanonicalModelHypothesis, ReviewCheckpoint
from dirty_data_to_olap.entrypoints.api import create_app
from tests.integration.test_step29_product_path import CSV, _start_run


AUTH = {"X-Local-Principal": "prompt04-r1-real-override"}


def _wait_for_summary(client: TestClient, run_id: str, predicate, *, timeout: float = 120.0) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/runs/{run_id}/product-summary", headers=AUTH)
        assert response.status_code == 200, response.text
        last = response.json()
        if predicate(last):
            return last
        if last["status"] == "FAILED":
            raise AssertionError(json.dumps(last, indent=2, sort_keys=True))
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for product state: {json.dumps(last, sort_keys=True)}")


def _pending(summary: dict, *, subject_id: str | None = None) -> dict:
    candidates = [
        item
        for item in summary["pending_reviews"]
        if item["checkpoint"] == ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value
        and (subject_id is None or item["subject_artifact_id"] == subject_id)
    ]
    assert len(candidates) == 1, candidates
    return candidates[0]


def _action(client: TestClient, run_id: str, review: dict, action: str, *, key: str, override: dict | None = None) -> dict:
    body = {
        "action": action,
        "subject_artifact_id": review["subject_artifact_id"],
        "subject_content_hash": review["subject_content_hash"],
        "rationale": f"Prompt04-R1 real product action {action}.",
        "expected_revision": review["action_revision"],
    }
    if override is not None:
        body["override"] = override
    response = client.post(
        f"/api/v1/runs/{run_id}/reviews/{ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value}/actions",
        headers={**AUTH, "Idempotency-Key": key},
        json=body,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _resume(client: TestClient, run_id: str, key: str) -> None:
    response = client.post(f"/api/v1/runs/{run_id}/resume", headers={**AUTH, "Idempotency-Key": key})
    assert response.status_code in {200, 202}, response.text


def _hypothesis(platform, run_id: str) -> tuple[str, CanonicalModelHypothesis]:
    job = platform.control_store.get_stage_job(run_id=run_id, stage_id="CANONICAL_HYPOTHESES")
    assert job is not None and job.status.value == "SUCCEEDED", job
    assert len(job.result_refs) == 1, job.result_refs
    artifact = platform.control_store.get_artifact(job.result_refs[0])
    assert artifact is not None
    assert platform.artifact_store.verify(artifact).state.value == "VERIFIED"
    value = CanonicalModelHypothesis.model_validate(json.loads(platform.artifact_store.read(artifact).decode("utf-8")))
    return artifact.artifact_id, value


def _finish_remaining_reviews(client: TestClient, run_id: str, *, suffix: str, timeout: float = 180.0) -> dict:
    """Accept each newly reached checkpoint through the public review boundary."""

    deadline = time.monotonic() + timeout
    resume_number = 0
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/runs/{run_id}/product-summary", headers=AUTH)
        assert response.status_code == 200, response.text
        summary = response.json()
        if summary["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            return summary
        pending = summary["pending_reviews"]
        if pending:
            review = pending[0]
            if review["decision"] is None:
                accepted = client.post(
                    f"/api/v1/runs/{run_id}/reviews/{review['checkpoint']}",
                    headers={**AUTH, "Idempotency-Key": f"{suffix}-review-{resume_number}"},
                    json={
                        "subject_artifact_id": review["subject_artifact_id"],
                        "subject_content_hash": review["subject_content_hash"],
                        "decision": "ACCEPTED",
                        "rationale": "Prompt04-R1 downstream validation review.",
                        "expected_revision": review["revision"],
                    },
                )
                assert accepted.status_code == 200, accepted.text
            resumed = client.post(
                f"/api/v1/runs/{run_id}/resume",
                headers={**AUTH, "Idempotency-Key": f"{suffix}-resume-{resume_number}"},
            )
            assert resumed.status_code in {200, 202}, resumed.text
            resume_number += 1
        time.sleep(0.2)
    raise AssertionError(f"timed out finishing reviews for {run_id}")


def test_real_product_override_is_reviewed_and_consumed_by_hypothesis_stage(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    platform, backend, runtime = build_local_product(tmp_path, graph_root=repository_root)
    try:
        client = TestClient(create_app(backend))
        run_id = _start_run(client, headers=AUTH, payload=CSV, suffix="r1-override")

        first = _wait_for_summary(
            client,
            run_id,
            lambda summary: any(item["checkpoint"] == ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value for item in summary["pending_reviews"]),
        )
        original = _pending(first)
        original_id = original["subject_artifact_id"]

        accepted = _action(client, run_id, original, "ACCEPT", key="r1-original-accept")
        assert accepted["resulting_decision"] == "ACCEPTED"
        _resume(client, run_id, "r1-original-resume")
        _wait_for_summary(
            client,
            run_id,
            lambda summary: next((stage["status"] for stage in summary["stages"] if stage["stage_id"] == "CANONICAL_HYPOTHESES"), None) == "SUCCEEDED",
        )
        first_hypothesis_id, first_hypothesis = _hypothesis(platform, run_id)
        assert first_hypothesis.relationships
        _wait_for_summary(
            client,
            run_id,
            lambda summary: any(
                item["checkpoint"] != ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value
                for item in summary["pending_reviews"]
            ),
        )

        invalidated = client.post(
            f"/api/v1/runs/{run_id}/reviews/{ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS.value}/invalidate",
            headers={**AUTH, "Idempotency-Key": "r1-original-invalidate"},
            json={
                "subject_artifact_id": original_id,
                "subject_content_hash": original["subject_content_hash"],
                "reason": "real product review evidence requires a bounded relationship disposition revision",
                "expected_revision": accepted["resulting_revision"],
            },
        )
        assert invalidated.status_code == 200, invalidated.text
        assert invalidated.json()["decision"]["decision"] == "INVALIDATED"

        after_invalidation = _wait_for_summary(
            client,
            run_id,
            lambda summary: any(
                item["subject_artifact_id"] == original_id
                and item["decision"] == "INVALIDATED"
                and item["action_revision"] == accepted["resulting_revision"] + 1
                for item in summary["pending_reviews"]
            ),
        )
        original_again = _pending(after_invalidation, subject_id=original_id)
        override = _action(
            client,
            run_id,
            original_again,
            "OVERRIDE",
            key="r1-original-override",
            override={
                "target": "RELATIONSHIP_DISPOSITION",
                "replacement": "EXCLUDE_CANDIDATE",
                "old_value_ref": original_id,
                "evidence_ref": "prompt04-r1-real-review-evidence",
            },
        )
        replacement_id = override["resulting_subject_artifact_id"]
        assert replacement_id and replacement_id != original_id
        assert override["next_required_action"] == "REVIEW_REPLACEMENT_SUBJECT"

        replacement_summary = _wait_for_summary(
            client,
            run_id,
            lambda summary: any(item["subject_artifact_id"] == replacement_id for item in summary["pending_reviews"]),
        )
        replacement = next(item for item in replacement_summary["pending_reviews"] if item["subject_artifact_id"] == replacement_id)
        assert replacement["state"] == "REVIEW_REQUIRED"
        assert replacement["decision"] is None

        replacement_accept = _action(client, run_id, replacement, "ACCEPT", key="r1-replacement-accept")
        assert replacement_accept["resulting_decision"] == "ACCEPTED"
        _resume(client, run_id, "r1-replacement-resume")
        _wait_for_summary(
            client,
            run_id,
            lambda summary: next((stage["status"] for stage in summary["stages"] if stage["stage_id"] == "CANONICAL_HYPOTHESES"), None) == "SUCCEEDED",
        )
        second_hypothesis_id, second_hypothesis = _hypothesis(platform, run_id)
        current_hypothesis_job = platform.control_store.get_stage_job(run_id=run_id, stage_id="CANONICAL_HYPOTHESES")
        assert current_hypothesis_job is not None
        assert current_hypothesis_job.result_refs == (second_hypothesis_id,)
        assert first_hypothesis_id not in current_hypothesis_job.result_refs

        assert second_hypothesis_id != first_hypothesis_id
        assert second_hypothesis.relationships == ()
        assert first_hypothesis.relationships != second_hypothesis.relationships
        assert platform.control_store.get_artifact(original_id) is not None
        proposal = platform.control_store.get_artifact(replacement_id)
        assert proposal is not None and proposal.artifact_kind == "ReviewOverrideProposal"
        assert original_id in proposal.provenance_refs
        assert replacement_id in second_hypothesis.provenance_refs

        final = _finish_remaining_reviews(client, run_id, suffix="r1-final")
        # EXCLUDE_CANDIDATE intentionally changes the relationship set relative
        # to the unchanged independent oracle.  Materialization still occurs,
        # but G6 must reject the incompatible downstream result rather than
        # silently treating it as a valid accepted output.
        assert final["status"] == "FAILED", json.dumps(final, indent=2, sort_keys=True)
        assert final["validation"]["g6_status"] == "FAIL"
        assert final["validation"]["g6_eligible"] is False
        assert final["output"]["validated"] is False
        assert final["output"]["row_counts"] == {"dim_date": 4, "dim_order": 4, "fact_orders": 4}
        materialization_job = platform.control_store.get_stage_job(run_id=run_id, stage_id="MATERIALIZATION")
        assert materialization_job is not None and materialization_job.status.value == "SUCCEEDED"
        materialization = platform.control_store.get_artifact(materialization_job.result_refs[0])
        assert materialization is not None and materialization.artifact_kind == "MaterializationArtifact"
    finally:
        runtime.close()
        platform.close()
