from __future__ import annotations

import pytest

from dirty_data_to_olap.domain.contracts.platform import (
    ArtifactIntegrityState,
    ArtifactManifest,
    ArtifactPublicationState,
    ArtifactStorageMode,
    GateEvidence,
    GateEvidenceStatus,
    CleanupAction,
    CleanupCandidate,
    CleanupPlan,
    RetentionClass,
)


def test_artifact_manifest_round_trips_with_explicit_identity() -> None:
    manifest = ArtifactManifest(
        artifact_id="artifact-1",
        run_id="run-1",
        stage_id="METADATA",
        attempt_id="attempt-1",
        artifact_kind="ValidationReport",
        media_type="application/json",
        producer="application.validation",
        content_hash="a" * 64,
        byte_size=12,
        logical_key="runs/run-1/validation/report.json",
        retention_class=RetentionClass.PINNED_GATE_EVIDENCE,
        publication_state=ArtifactPublicationState.PUBLISHED,
    )
    restored = ArtifactManifest.model_validate(manifest.model_dump(mode="json"))
    assert restored.artifact_id == manifest.artifact_id
    assert restored.content_hash == "a" * 64
    assert restored.storage_mode is ArtifactStorageMode.MANAGED


def test_gate_evidence_is_explicit_and_not_derived_from_reconciliation() -> None:
    evidence = GateEvidence(
        gate_id="G6_DATA_CORRECTNESS",
        run_id="run-1",
        status=GateEvidenceStatus.PASS,
        eligible=True,
        validation_report_artifact_id="vreport-1",
        validation_report_run_id="step22-run",
        validation_report_id="report-1",
        validation_report_content_hash="b" * 64,
        policy_version="step22-data-correctness-v2",
        verified_content_commit="f5ddeb6dbd2a1759b8d92a4dacf47400d6b580c2",
    )
    assert evidence.status is GateEvidenceStatus.PASS
    assert not hasattr(evidence, "no_blocking_discrepancy")
    assert ArtifactIntegrityState.HASH_MISMATCH.value == "HASH_MISMATCH"


def test_cleanup_plan_hash_binds_semantic_scope_and_gate_eligibility_is_closed() -> None:
    candidate = CleanupCandidate(artifact_id="artifact-1", expected_content_hash="a" * 64, reason="expired", retention_class=RetentionClass.RUN_SCOPED, age_seconds=10, byte_size=4, planned_action=CleanupAction.DELETE_BLOB_IF_UNREFERENCED)
    plan = CleanupPlan(plan_id="plan-1", run_id="run-1", candidates=(candidate,), policy_ref="policy", dry_run=True)
    assert plan.model_copy(update={"created_at": plan.created_at.replace(year=2020)}).content_hash == plan.content_hash
    assert plan.model_copy(update={"dry_run": False}).content_hash != plan.content_hash
    with pytest.raises(ValueError):
        GateEvidence(gate_id="G6_DATA_CORRECTNESS", run_id="run-1", status=GateEvidenceStatus.FAIL, eligible=True, validation_report_artifact_id="vreport-1", validation_report_run_id="step22-run", validation_report_id="report-1", validation_report_content_hash="b" * 64, policy_version="policy", verified_content_commit="f5ddeb6dbd2a1759b8d92a4dacf47400d6b580c2")
