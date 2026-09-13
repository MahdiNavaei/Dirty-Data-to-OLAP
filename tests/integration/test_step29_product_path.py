"""Focused end-to-end proof for the Step29 local product path."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from dirty_data_to_olap.application.product_runtime import build_local_product
from dirty_data_to_olap.entrypoints.api import create_app
from dirty_data_to_olap.domain.contracts.analytical import AnalyticalInputBinding, AnalyticalInputDataset, AnalyticalPlan, CompiledPlan, MaterializationArtifact
from dirty_data_to_olap.domain.contracts.canonical import CanonicalIdentityProposal, CanonicalModel
from dirty_data_to_olap.domain.contracts.dependency import DependencyResult
from dirty_data_to_olap.domain.contracts.evidence_fusion import EvidenceFusionResult
from dirty_data_to_olap.domain.contracts.profiling import ProfileResult
from dirty_data_to_olap.domain.contracts.quality import QualityResult
from dirty_data_to_olap.domain.contracts.semantic import SemanticValidationResult
from dirty_data_to_olap.domain.contracts.source import SourceSnapshotResult
from dirty_data_to_olap.domain.contracts.validation import ValidationReport


CSV = b"""order_id,customer_id,customer_id_ref,order_date,quantity,unit_price
O-100,C-1,C-1,2026-01-02,2,10.50
O-101,C-2,C-2,2026-01-03,1,7.25
O-102,C-3,C-3,2026-01-04,4,3.00
O-103,C-4,C-4,2026-01-05,3,12.00
"""


def _start_run(client: TestClient, *, headers: dict[str, str], payload: bytes, suffix: str) -> str:
    configuration = client.get("/api/v1/product/configuration", headers=headers)
    assert configuration.status_code == 200, configuration.text
    imported = client.post(
        "/api/v1/sources/import",
        headers={**headers, "X-Source-Filename": "orders.csv", "Content-Type": "text/csv", "Idempotency-Key": f"step29-test-{suffix}-import"},
        content=payload,
    )
    assert imported.status_code == 201, imported.text
    source = imported.json()
    created = client.post(
        "/api/v1/runs",
        headers={**headers, "Idempotency-Key": f"step29-test-{suffix}-run"},
        json={"project_id": f"step29-{suffix}", "configuration_fingerprint": configuration.json()["configuration_fingerprint"]},
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["run_id"]
    bound = client.post(
        f"/api/v1/runs/{run_id}/source-selection",
        headers={**headers, "Idempotency-Key": f"step29-test-{suffix}-bind"},
        json={
            "registry_id": source["registry_id"],
            "scope": {"included_objects": [], "excluded_objects": [], "include_views": False, "included_columns": {}},
            "extraction": {"chunk_size": 1000, "max_rows": 10000, "max_rows_scope": "SOURCE_WIDE", "null_markers": [], "preserve_raw_values": True},
            "execution_context_id": f"step29-{suffix}",
        },
    )
    assert bound.status_code in (200, 201), bound.text
    prepared = client.post(
        f"/api/v1/runs/{run_id}/execution/prepare",
        headers={**headers, "Idempotency-Key": f"step29-test-{suffix}-prepare"},
        json={"intent": {"cross_source_mapping_requested": False, "entity_resolution_requested": False, "optional_semantic_evidence_enabled": False, "learned_evidence_enabled": False}},
    )
    assert prepared.status_code == 200, prepared.text
    submitted = client.post(f"/api/v1/runs/{run_id}/execution", headers={**headers, "Idempotency-Key": f"step29-test-{suffix}-submit"})
    assert submitted.status_code in (200, 202), submitted.text
    return run_id


def _poll_run(client: TestClient, *, headers: dict[str, str], run_id: str, suffix: str, timeout: int = 90) -> tuple[dict, set[str]]:
    final = None
    accepted_checkpoints: set[str] = set()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/runs/{run_id}/product-summary", headers=headers)
        assert response.status_code == 200, response.text
        summary = response.json()
        if summary["pending_reviews"]:
            review = summary["pending_reviews"][0]
            if review["checkpoint"] in accepted_checkpoints:
                time.sleep(0.2)
            else:
                action = client.post(
                    f"/api/v1/runs/{run_id}/reviews/{review['checkpoint']}",
                    headers={**headers, "Idempotency-Key": f"step29-test-{suffix}-review-{len(accepted_checkpoints)}"},
                    json={"subject_artifact_id": review["subject_artifact_id"], "subject_content_hash": review["subject_content_hash"], "decision": "ACCEPTED", "rationale": "bounded focused product review", "expected_revision": review["revision"]},
                )
                assert action.status_code == 200, action.text
                resumed = client.post(f"/api/v1/runs/{run_id}/resume", headers={**headers, "Idempotency-Key": f"step29-test-{suffix}-resume-{len(accepted_checkpoints)}"})
                assert resumed.status_code in (200, 202), resumed.text
                accepted_checkpoints.add(review["checkpoint"])
        if summary["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            final = summary
            break
        time.sleep(0.2)
    assert final is not None
    return final, accepted_checkpoints


def test_real_csv_reaches_g6_after_four_review_checkpoints() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="step29-product-") as temporary:
        platform, backend, runtime = build_local_product(Path(temporary), graph_root=repository_root)
        try:
            client = TestClient(create_app(backend))
            headers = {"X-Local-Principal": "step29-focused-test"}
            run_id = _start_run(client, headers=headers, payload=CSV, suffix="happy")
            final, accepted_checkpoints = _poll_run(client, headers=headers, run_id=run_id, suffix="happy")
            assert final["status"] == "SUCCEEDED", json.dumps(final, indent=2, sort_keys=True)
            assert accepted_checkpoints == {"REVIEW_EVIDENCE_DECISIONS", "REVIEW_CANONICAL_IDENTITY", "REVIEW_ANALYTICAL_PLAN", "REVIEW_MATERIALIZATION_PLAN"}
            assert final["data_condition"]["source_rows_observed"] == 4
            assert final["validation"]["g6_status"] == "PASS"
            assert final["validation"]["g6_eligible"] is True
            assert final["output"]["validated"] is True

            snapshot_ref, snapshot = runtime.handlers._typed_from_run(run_id, "SourceSnapshotResult", SourceSnapshotResult)
            artifact_refs = runtime.platform.control_store.list_artifacts(run_id=run_id, limit=10000)
            by_kind = {kind: tuple(ref for ref in artifact_refs if ref.artifact_kind == kind) for kind in {ref.artifact_kind for ref in artifact_refs}}
            assert {"SourceCatalog", "SourceSnapshotResult", "ProfileResult", "DependencyResult", "QualityResult", "EvidenceFusionResult", "CanonicalModel", "AnalyticalInputDataset", "AnalyticalInputBinding", "AnalyticalPlan", "CompiledPlan", "GeneratedSQL", "TargetConfig", "MaterializationArtifact", "SemanticValidationResult", "ValidationReport"} <= set(by_kind)

            profile_ref, profile = runtime.handlers._typed_from_run(run_id, "ProfileResult", ProfileResult)
            dependency_ref, dependency = runtime.handlers._typed_from_run(run_id, "DependencyResult", DependencyResult)
            quality_ref, quality = runtime.handlers._typed_from_run(run_id, "QualityResult", QualityResult)
            fusion_ref, fusion = runtime.handlers._typed_from_run(run_id, "EvidenceFusionResult", EvidenceFusionResult)
            canonical_ref, canonical = runtime.handlers._typed_from_run(run_id, "CanonicalModel", CanonicalModel)
            _proposal_ref, proposal = runtime.handlers._typed_from_run(run_id, "CanonicalIdentityProposal", CanonicalIdentityProposal)
            dataset_ref, dataset = runtime.handlers._typed_from_run(run_id, "AnalyticalInputDataset", AnalyticalInputDataset)
            binding_ref, binding = runtime.handlers._typed_from_run(run_id, "AnalyticalInputBinding", AnalyticalInputBinding)
            plan_ref, plan = runtime.handlers._typed_from_run(run_id, "AnalyticalPlan", AnalyticalPlan)
            compiled_ref, compiled = runtime.handlers._typed_from_run(run_id, "CompiledPlan", CompiledPlan)
            materialization_ref, materialization = runtime.handlers._typed_from_run(run_id, "MaterializationArtifact", MaterializationArtifact)
            semantic_validation_ref, semantic_validation = runtime.handlers._typed_from_run(run_id, "SemanticValidationResult", SemanticValidationResult)
            validation_ref, validation = runtime.handlers._typed_from_run(run_id, "ValidationReport", ValidationReport)

            assert profile_ref.producer == "application.profiling"
            assert profile.profile_request.snapshot_id == snapshot.snapshot.snapshot_id
            assert profile.completeness.value == "COMPLETE" and profile.artifacts
            assert dependency_ref.producer == "application.dependency_discovery"
            assert dependency.status.value == "COMPLETE" and dependency.capabilities
            assert all(cap.engine and cap.engine_version for cap in dependency.capabilities)
            candidate = dependency.relationship_candidates[0]
            assert candidate.evidence_refs and any(ucc.evidence_id in candidate.evidence_refs for ucc in dependency.ucc_evidence)
            assert quality_ref.producer == "application.quality"
            assert quality.snapshot_id == snapshot.snapshot.snapshot_id and quality.input_profile_refs == (profile.profile_request.profile_request_id,)
            assert fusion_ref.producer == "application.evidence_fusion"
            assert fusion.completeness.value == "COMPLETE_REVIEW_READY" and fusion.relationships and len(fusion.relationships[0].supporting_signal_refs) >= 3
            assert canonical_ref.producer == "application.canonical_finalization"
            assert canonical.finalized_at and len(canonical.instances) == 4
            assert all(item.derivation_basis.value == "SOURCE_LOCAL_EVENT_IDENTITY" for item in proposal.memberships)
            assert dataset_ref.producer == "application.product_input" and binding_ref.producer == "application.product_input"
            assert binding.dataset_id == dataset.dataset_id and binding.dataset_content_hash == dataset.content_hash and dataset.row_counts[dataset.tables[0].table_id] == 4
            assert plan_ref.producer == "application.analytical_planner" and plan.plan_id
            assert compiled_ref.producer == "application.compiler" and compiled.compiler_version == "duckdb-compiler-v1"
            assert materialization_ref.producer == "adapter.duckdb" and materialization.usable is True and materialization.row_counts == {"dim_date": 4, "dim_order": 4, "fact_orders": 4}
            assert semantic_validation_ref.producer == "application.semantic_layer" and semantic_validation.status.value == "PASS"
            assert validation_ref.producer == "application.validation" and validation.g6_status.value == "PASS" and validation.g6_eligible is True
            assert all(ref.content_hash and ref.byte_size > 0 for ref in artifact_refs)
            assert snapshot_ref.artifact_kind == "SourceSnapshotResult"
        finally:
            runtime.close()
            platform.close()


def test_duplicate_order_id_fails_after_upload_at_real_dependency_boundary() -> None:
    duplicate_csv = CSV.replace(b"O-103,C-4,C-4", b"O-100,C-4,C-4")
    repository_root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="step29-product-negative-") as temporary:
        platform, backend, runtime = build_local_product(Path(temporary), graph_root=repository_root)
        try:
            client = TestClient(create_app(backend))
            headers = {"X-Local-Principal": "step29-negative-test"}
            run_id = _start_run(client, headers=headers, payload=duplicate_csv, suffix="duplicate")
            final, _accepted = _poll_run(client, headers=headers, run_id=run_id, suffix="duplicate")
            dependency_job = platform.control_store.get_stage_job(run_id=run_id, stage_id="DEPENDENCY_DISCOVERY")
            assert final["status"] == "FAILED", json.dumps(final, indent=2, sort_keys=True)
            assert dependency_job is not None and dependency_job.status.value == "FAILED"
            assert dependency_job.failure_code == "DEPENDENCY_INCOMPLETE"
            assert final["current_stage"] == "DEPENDENCY_DISCOVERY"
        finally:
            runtime.close()
            platform.close()


def test_step29_runtime_is_a_thin_service_composition_root() -> None:
    source = (Path(__file__).resolve().parents[2] / "src" / "dirty_data_to_olap" / "application" / "product_runtime.py").read_text(encoding="utf-8")
    for forbidden in ("import csv", "csv.DictReader", "import duckdb", "duckdb.connect", "RelationshipDecision(", "SemanticMappingDecision(", "AnalyticalPlan(", "GeneratedSQL(", "MaterializationArtifact(", "ValidationReport("):
        assert forbidden not in source
    for accepted in ("ProfilingService", "DataProfilerAdapter", "QualityAnalysisService", "DependencyDiscoveryService", "DesbordanteDependencyAdapter", "EvidenceFusionService", "AnalyticalPlannerService", "AnalyticalCompilerService", "MaterializationService", "DuckDBMaterializer", "SemanticLayerService", "ValidationService", "DuckDBValidationTargetReader"):
        assert accepted in source
