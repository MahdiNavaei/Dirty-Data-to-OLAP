"""Focused end-to-end proof for the Step29 local product path."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from dirty_data_to_olap.application.product_runtime import build_local_product
from dirty_data_to_olap.entrypoints.api import create_app


CSV = b"""order_id,customer_id,customer_id_ref,order_date,quantity,unit_price
O-100,C-1,C-1,2026-01-02,2,10.50
O-101,C-2,C-2,2026-01-03,1,7.25
O-102,C-3,C-3,2026-01-04,4,3.00
O-103,C-4,C-4,2026-01-05,3,12.00
"""


def test_real_csv_reaches_g6_after_four_review_checkpoints() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="step29-product-") as temporary:
        platform, backend, runtime = build_local_product(Path(temporary), graph_root=repository_root)
        try:
            client = TestClient(create_app(backend))
            headers = {"X-Local-Principal": "step29-focused-test"}
            configuration = client.get("/api/v1/product/configuration", headers=headers)
            assert configuration.status_code == 200, configuration.text

            imported = client.post(
                "/api/v1/sources/import",
                headers={**headers, "X-Source-Filename": "orders.csv", "Content-Type": "text/csv", "Idempotency-Key": "step29-test-import"},
                content=CSV,
            )
            assert imported.status_code == 201, imported.text
            source = imported.json()

            created = client.post(
                "/api/v1/runs",
                headers={**headers, "Idempotency-Key": "step29-test-run"},
                json={"project_id": "step29-focused", "configuration_fingerprint": configuration.json()["configuration_fingerprint"]},
            )
            assert created.status_code == 201, created.text
            run_id = created.json()["run_id"]

            bound = client.post(
                f"/api/v1/runs/{run_id}/source-selection",
                headers={**headers, "Idempotency-Key": "step29-test-bind"},
                json={
                    "registry_id": source["registry_id"],
                    "scope": {"included_objects": [], "excluded_objects": [], "include_views": False, "included_columns": {}},
                    "extraction": {"chunk_size": 1000, "max_rows": 10000, "max_rows_scope": "SOURCE_WIDE", "null_markers": [], "preserve_raw_values": True},
                    "execution_context_id": "step29-focused",
                },
            )
            assert bound.status_code in (200, 201), bound.text

            prepared = client.post(
                f"/api/v1/runs/{run_id}/execution/prepare",
                headers={**headers, "Idempotency-Key": "step29-test-prepare"},
                json={"intent": {"cross_source_mapping_requested": False, "entity_resolution_requested": False, "optional_semantic_evidence_enabled": False, "learned_evidence_enabled": False}},
            )
            assert prepared.status_code == 200, prepared.text
            submitted = client.post(f"/api/v1/runs/{run_id}/execution", headers={**headers, "Idempotency-Key": "step29-test-submit"})
            assert submitted.status_code in (200, 202), submitted.text

            final = None
            accepted_checkpoints: set[str] = set()
            deadline = time.monotonic() + 75
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
                            headers={**headers, "Idempotency-Key": f"step29-test-review-{len(accepted_checkpoints)}"},
                            json={"subject_artifact_id": review["subject_artifact_id"], "subject_content_hash": review["subject_content_hash"], "decision": "ACCEPTED", "rationale": "bounded focused product review", "expected_revision": review["revision"]},
                        )
                        assert action.status_code == 200, action.text
                        resumed = client.post(f"/api/v1/runs/{run_id}/resume", headers={**headers, "Idempotency-Key": f"step29-test-resume-{len(accepted_checkpoints)}"})
                        assert resumed.status_code in (200, 202), resumed.text
                        accepted_checkpoints.add(review["checkpoint"])
                if summary["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                    final = summary
                    break
                time.sleep(0.2)

            assert final is not None
            assert final["status"] == "SUCCEEDED", final
            assert accepted_checkpoints == {
                "REVIEW_EVIDENCE_DECISIONS",
                "REVIEW_CANONICAL_IDENTITY",
                "REVIEW_ANALYTICAL_PLAN",
                "REVIEW_MATERIALIZATION_PLAN",
            }
            assert final["data_condition"]["source_rows_observed"] == 4
            assert final["validation"]["g6_status"] == "PASS"
            assert final["validation"]["g6_eligible"] is True
            assert final["output"]["validated"] is True
        finally:
            runtime.close()
            platform.close()
