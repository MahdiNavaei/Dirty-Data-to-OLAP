"""Independent, bounded red-team probes for the executable V1 local product.

These tests attack the HTTP/control-plane and the two highest-risk local data
boundaries with synthetic disposable state.  They deliberately do not contact
external services, cloud metadata endpoints, or production systems.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dirty_data_to_olap.adapters.semantic_query import (
    DuckDBSemanticQueryExecutor,
    SemanticQueryExecutionError,
)
from dirty_data_to_olap.adapters.sources.sql.dlt_sql import (
    DltSqlSourceAdapter,
    RuntimeSqlCredentials,
    _validate_runtime_connection_url,
)
from dirty_data_to_olap.application.backend import Principal
from dirty_data_to_olap.application.product_sources import ProductSourceError, ProductSourceService
from dirty_data_to_olap.application.semantic_layer import SemanticLayerService
from dirty_data_to_olap.application.source_registry import DurableSourceRegistry
from dirty_data_to_olap.composition import build_local_backend
from dirty_data_to_olap.domain.contracts.canonical import ReviewCheckpoint, ReviewCompatibilityContext
from dirty_data_to_olap.domain.contracts.database import ConnectionProfileReference, DatabaseEngine
from dirty_data_to_olap.domain.contracts.platform import ArtifactManifest
from dirty_data_to_olap.domain.contracts.semantic import SemanticQueryRequest
from dirty_data_to_olap.domain.contracts.source import SourceRegistryRecord, SourceSelection, SourceType
from dirty_data_to_olap.entrypoints.api import create_app


CSV = b"order_id,customer_id,customer_id_ref,order_date,quantity,unit_price\nO-1,C-1,C-1,2026-01-01,1,2.0\n"


def _run(client: TestClient, *, subject: str, project_id: str, key: str, configuration: str) -> str:
    response = client.post(
        "/api/v1/runs",
        headers={"X-Local-Principal": subject, "Idempotency-Key": key},
        json={"project_id": project_id, "configuration_fingerprint": configuration},
    )
    assert response.status_code == 201, response.text
    return response.json()["run_id"]


def test_rt_auth_bola_and_control_plane_mutations_are_isolated(tmp_path: Path) -> None:
    """Attack missing auth, forged identity, cross-run reads, and mutations."""

    platform, backend = build_local_backend(tmp_path)
    client = TestClient(create_app(backend), raise_server_exceptions=False)
    try:
        configuration = platform.config.configuration_fingerprint
        alice_run = _run(client, subject="alice", project_id="project-a", key="alice-run", configuration=configuration)
        bob_run = _run(client, subject="bob", project_id="project-b", key="bob-run", configuration=configuration)

        assert client.get("/api/v1/runs").status_code == 401
        assert client.get("/api/v1/runs", headers={"X-Local-Principal": "alice/../bob"}).status_code == 401
        alice = {"X-Local-Principal": "alice"}
        bob = {"X-Local-Principal": "bob"}
        assert client.get(f"/api/v1/runs/{bob_run}", headers=alice).status_code == 404
        assert client.get(f"/api/v1/runs/{alice_run}/jobs", headers=bob).status_code == 404
        assert client.get(f"/api/v1/runs/{alice_run}/artifacts", headers=bob).status_code == 404
        assert client.post(
            f"/api/v1/runs/{alice_run}/cancel",
            headers={**bob, "Idempotency-Key": "cross-run-cancel"},
        ).status_code == 403
        assert client.get("/api/v1/projects/project-b/runs", headers=alice).json()["items"] == []
    finally:
        platform.close()


def test_rt_client_cannot_forge_execution_authority_or_replay_another_request(tmp_path: Path) -> None:
    """The execution endpoint must derive authority from durable server state."""

    platform, backend = build_local_backend(tmp_path)
    client = TestClient(create_app(backend), raise_server_exceptions=False)
    try:
        run_id = _run(
            client,
            subject="authority-owner",
            project_id="authority-project",
            key="authority-run",
            configuration=platform.config.configuration_fingerprint,
        )
        headers = {"X-Local-Principal": "authority-owner", "Idempotency-Key": "forged-command"}
        forged = client.post(
            f"/api/v1/runs/{run_id}/execution",
            headers=headers,
            json={"status": "ACCEPTED", "plan_id": "client-forged-plan", "accepted_by": "client"},
        )
        assert forged.status_code == 409
        body = forged.json()
        assert body["status"] == "BLOCKED"
        assert "client-forged-plan" not in forged.text
        replay = client.post(
            f"/api/v1/runs/{run_id}/execution",
            headers=headers,
            json={"status": "ACCEPTED", "plan_id": "different-forged-plan"},
        )
        assert replay.status_code == 409
        assert replay.json()["command_id"] == body["command_id"]
    finally:
        platform.close()


def test_rt_review_replay_and_cross_run_subject_substitution_fail_closed(tmp_path: Path) -> None:
    """Replay, stale revision, and cross-run review-subject substitution are rejected."""

    platform, backend = build_local_backend(tmp_path)
    client = TestClient(create_app(backend), raise_server_exceptions=False)
    try:
        run_one = _run(client, subject="reviewer", project_id="review-a", key="review-one", configuration=platform.config.configuration_fingerprint)
        run_two = _run(client, subject="reviewer", project_id="review-b", key="review-two", configuration=platform.config.configuration_fingerprint)
        artifact = platform.artifact_store.publish(
            ArtifactManifest(
                artifact_id="step39-review-subject",
                run_id=run_one,
                stage_id="EVIDENCE_FUSION",
                attempt_id="attempt-1",
                artifact_kind="ReviewSubject",
                media_type="application/json",
                producer="step39-red-team",
                storage_key=f"runs/{run_one}/artifacts/step39-review-subject.json",
            ),
            b"{}",
        )
        platform.control_store.register_artifact(artifact)
        context = ReviewCompatibilityContext(
            review_checkpoint_id=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS,
            subject_stage="EVIDENCE_FUSION",
            subject_artifact_id=artifact.artifact_id,
            subject_content_hash=artifact.content_hash,
            subject_schema_version=artifact.schema_version,
            model_version="step39-red-team",
            source_schema_fingerprints={"source": "step39"},
            policy_version="step39-policy",
            domain_assertion_refs=("step39-assertion",),
            subject_semantic_id="step39-subject",
            applicability_fingerprint="step39-applicability",
        )
        platform.control_store.register_review_subject_context(run_id=run_one, context=context)
        headers = {"X-Local-Principal": "reviewer"}
        path = f"/api/v1/runs/{run_one}/reviews/{context.review_checkpoint_id.value}"
        body = {
            "subject_artifact_id": artifact.artifact_id,
            "subject_content_hash": artifact.content_hash,
            "decision": "ACCEPTED",
            "rationale": "bounded Step39 review probe",
            "expected_revision": 0,
        }
        assert client.post(path, headers={**headers, "Idempotency-Key": "review-first"}, json=body).status_code == 200
        assert client.post(path, headers={**headers, "Idempotency-Key": "review-stale"}, json=body).status_code == 409
        assert client.post(
            path,
            headers={**headers, "Idempotency-Key": "review-mutated"},
            json={**body, "subject_content_hash": "0" * 64},
        ).status_code == 409
        cross_run = client.post(
            f"/api/v1/runs/{run_two}/reviews/{context.review_checkpoint_id.value}",
            headers={**headers, "Idempotency-Key": "review-cross-run"},
            json=body,
        )
        assert cross_run.status_code == 404
    finally:
        platform.close()


def test_rt_artifact_identity_and_path_traversal_are_confined(tmp_path: Path) -> None:
    """Artifact identities cannot become paths or cross a run boundary."""

    platform, backend = build_local_backend(tmp_path)
    client = TestClient(create_app(backend), raise_server_exceptions=False)
    try:
        run_id = _run(client, subject="artifact-owner", project_id="artifact-project", key="artifact-run", configuration=platform.config.configuration_fingerprint)
        artifact = platform.artifact_store.publish(
            ArtifactManifest(
                artifact_id="step39-artifact",
                run_id=run_id,
                stage_id="SOURCE_DISCOVERY",
                attempt_id="attempt-1",
                artifact_kind="SecurityProbe",
                media_type="application/octet-stream",
                producer="step39-red-team",
                storage_key="runs/step39/artifacts/step39-artifact.bin",
            ),
            b"synthetic-secret-canary",
        )
        assert client.post(f"/api/v1/runs/{run_id}/artifacts/register", headers={"X-Local-Principal": "artifact-owner"}, json={"artifact_id": artifact.artifact_id}).status_code == 201
        assert client.get(f"/api/v1/artifacts/{artifact.artifact_id}?run_id={run_id}", headers={"X-Local-Principal": "artifact-owner"}).status_code == 200
        assert client.get(f"/api/v1/runs/{run_id}/artifacts/../artifacts/step39-artifact/content", headers={"X-Local-Principal": "artifact-owner"}).status_code in {403, 404}
        with pytest.raises(Exception):
            platform.artifact_store.stat("../outside")
        assert client.get(
            f"/api/v1/runs/{run_id}/artifacts/{artifact.artifact_id}/content",
            headers={"X-Local-Principal": "artifact-owner"},
        ).status_code == 403
    finally:
        platform.close()


def test_rt_semantic_sql_injection_and_file_function_payloads_are_rejected() -> None:
    """Generated SQL is structurally rebound; attacker SQL never reaches DuckDB."""

    from tests.step21_support import retail_semantic_flow

    context, model = retail_semantic_flow()
    metric = model.metrics[0]
    compilation = SemanticLayerService().resolve_query(
        model,
            SemanticQueryRequest(request_id="step39_base", metric_ids=(metric.metric_id,)),
        analytical_plan=context.plan,
        compiled_plan=context.compiled_plan,
        materialization=context.materialization,
    )
    assert ";" not in compilation.query_plan.sql_template
    product_code = next(attribute for dimension in model.dimensions for attribute in dimension.attributes if attribute.physical_column_ref == "product_code")
    injected = "P-001' OR 1=1 --"
    bound = SemanticLayerService().resolve_query(
        model,
        SemanticQueryRequest(
            request_id="step39_injection",
            metric_ids=(metric.metric_id,),
            filters=(({"attribute_id": product_code.semantic_attribute_id, "operator": "EQUALS", "values": (injected,)}),),
        ),
        analytical_plan=context.plan,
        compiled_plan=context.compiled_plan,
        materialization=context.materialization,
    )
    assert bound.parameters == (injected,)
    assert injected not in bound.query_plan.sql_template
    for malicious_sql in ("SELECT 1; DROP TABLE facts", "SELECT * FROM read_csv('\\\\attacker\\\\secret.csv')"):
        forged = compilation.model_copy(update={"query_plan": compilation.query_plan.model_copy(update={"sql_template": malicious_sql})})
        with pytest.raises(SemanticQueryExecutionError, match="STRUCTURAL_QUERY_REJECTED"):
            DuckDBSemanticQueryExecutor(Path("." )).execute(model, forged)


def test_rt_connector_ssrf_and_credential_exfiltration_guards(tmp_path: Path) -> None:
    """Only profile-bound SQL schemes/hosts are accepted and secrets redact."""

    profile = ConnectionProfileReference(
        profile_id="step39-network",
        database_engine=DatabaseEngine.POSTGRESQL,
        host="127.0.0.1",
        port=5432,
        database_name="source",
        credential_reference="vault://step39",
    )
    with pytest.raises(ValueError):
        _validate_runtime_connection_url(profile, "http://127.0.0.1:5432/source?password=STEP39_SECRET")
    with pytest.raises(ValueError):
        _validate_runtime_connection_url(profile, "postgresql://169.254.169.254:5432/source")
    credentials = RuntimeSqlCredentials("postgresql://127.0.0.1/source?password=STEP39_SECRET")
    assert "STEP39_SECRET" not in repr(credentials) and "STEP39_SECRET" not in str(credentials)
    record = SourceRegistryRecord(
        registry_id="step39-network",
        source_id="step39-network-source",
        display_name="synthetic-network-source",
        source_type=SourceType.POSTGRESQL,
        connection_profile=profile,
        adapter_name="dlt_sql_source",
        adapter_version="1",
    )
    class Resolver:
        def resolve(self, _profile, *, required_purpose, source_id):
            return RuntimeSqlCredentials(
                "postgresql://169.254.169.254:5432/source?password=STEP39_SECRET",
                credential_purpose=required_purpose,
                source_id=source_id,
            )

    adapter = DltSqlSourceAdapter(project_root=tmp_path, credential_resolver=Resolver())
    with pytest.raises(Exception) as raised:
        adapter.discover_source(SourceSelection(registry_id=record.registry_id), record)
    assert "STEP39_SECRET" not in str(raised.value)


def test_rt_upload_filename_and_error_canaries_do_not_escape_or_echo(tmp_path: Path) -> None:
    """Upload traversal, HTML canaries, oversized bodies, and malformed JSON stay bounded."""

    platform, backend = build_local_backend(tmp_path)
    client = TestClient(create_app(backend), raise_server_exceptions=False)
    try:
        service = ProductSourceService(tmp_path, DurableSourceRegistry(tmp_path / "workspace" / "source-registry.json"))
        for index, filename in enumerate(("../escape.csv", "..\\escape.csv", "<script>alert(1)</script>.csv")):
            with pytest.raises(ProductSourceError) as raised:
                service.import_csv(registry_id=f"step39-upload-{index}", filename=filename, payload=CSV, owner_subject="upload-attacker")
            assert "<script>" not in str(raised.value) and "escape.csv" not in str(raised.value)
        common = {"X-Local-Principal": "upload-attacker", "Idempotency-Key": "upload-probe"}
        malformed = client.post(
            "/api/v1/runs",
            headers={"X-Local-Principal": "upload-attacker", "Idempotency-Key": "bad-json", "Content-Type": "application/json"},
            content=b"{not-json",
        )
        assert malformed.status_code == 422
        assert "traceback" not in malformed.text.lower() and "not-json" not in malformed.text
        oversized = client.post(
            "/api/v1/sources/import",
            headers={**common, "X-Source-Filename": "orders.csv"},
            content=b"x" * (5 * 1024 * 1024 + 1),
        )
        assert oversized.status_code == 413
    finally:
        platform.close()


def test_rt_source_registry_owner_and_symlink_escape_guards(tmp_path: Path) -> None:
    """A source record and its file locator remain owner- and root-bound."""

    registry = DurableSourceRegistry(tmp_path / "workspace" / "source-registry.json")
    service = ProductSourceService(tmp_path, registry)
    record = service.import_csv(registry_id="step39-owned", filename="orders.csv", payload=CSV, owner_subject="alice")
    assert service.list(owner_subject="bob") == ()
    with pytest.raises(ProductSourceError):
        service.get_for_owner(record.registry_id, owner_subject="bob")
    outside = tmp_path.parent / "step39-outside.csv"
    outside.write_bytes(CSV)
    try:
        linked = tmp_path / "link.csv"
        try:
            linked.symlink_to(outside)
        except (OSError, NotImplementedError):
            return
        malicious = record.model_copy(update={"registry_id": "step39-linked", "file_locator": str(linked)})
        with pytest.raises(Exception):
            from dirty_data_to_olap.adapters.sources.files import FileSourceAdapter

            FileSourceAdapter(SourceType.CSV, project_root=tmp_path).discover_source(
                malicious.selection if hasattr(malicious, "selection") else None,
                malicious,
            )
    finally:
        outside.unlink(missing_ok=True)
