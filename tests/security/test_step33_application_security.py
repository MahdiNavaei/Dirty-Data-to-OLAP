from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dirty_data_to_olap.adapters.sources.files import FileSourceAdapter
from dirty_data_to_olap.adapters.sources.sql.dlt_sql import DltSqlSourceAdapter, RuntimeSqlCredentials
from dirty_data_to_olap.application.backend import BackendError, Principal
from dirty_data_to_olap.application.product_sources import ProductSourceError, ProductSourceService
from dirty_data_to_olap.application.source_registry import DurableSourceRegistry
from dirty_data_to_olap.composition import build_local_backend
from dirty_data_to_olap.domain.contracts.database import ConnectionProfileReference, DatabaseEngine
from dirty_data_to_olap.domain.contracts.platform import ArtifactManifest
from dirty_data_to_olap.domain.contracts.source import (
    ExtractionPolicy,
    SelectionScope,
    SourceIngestionError,
    SourceRegistryRecord,
    SourceSelection,
    SourceType,
)
from dirty_data_to_olap.entrypoints.api import create_app


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LOCAL_SCOPES = frozenset(
    {
        "runs:read",
        "runs:write",
        "attempts:read",
        "jobs:read",
        "reviews:read",
        "reviews:write",
        "artifacts:read",
        "artifacts:write",
        "validation:read",
        "visualizations:read",
    }
)
CSV = b"order_id,customer_id,customer_id_ref,order_date,quantity,unit_price\nO-1,C-1,C-1,2026-01-01,1,2.0\n"


def _run(client: TestClient, *, subject: str, project_id: str, key: str, configuration: str) -> str:
    response = client.post(
        "/api/v1/runs",
        headers={"X-Local-Principal": subject, "Idempotency-Key": key},
        json={"project_id": project_id, "configuration_fingerprint": configuration},
    )
    assert response.status_code == 201, response.text
    return response.json()["run_id"]


def test_sec_auth_001_002_004_cross_project_reads_mutations_and_run_isolation(tmp_path: Path) -> None:
    platform, backend = build_local_backend(tmp_path)
    client = TestClient(create_app(backend), raise_server_exceptions=False)
    try:
        configuration = platform.config.configuration_fingerprint
        alice_run = _run(client, subject="alice", project_id="project-a", key="alice-run", configuration=configuration)
        bob_run = _run(client, subject="bob", project_id="project-b", key="bob-run", configuration=configuration)

        alice = {"X-Local-Principal": "alice"}
        bob = {"X-Local-Principal": "bob"}
        assert client.get(f"/api/v1/runs/{bob_run}", headers=alice).status_code == 404
        assert client.get("/api/v1/runs", headers=alice).json()["items"]
        assert {item["run_id"] for item in client.get("/api/v1/runs", headers=alice).json()["items"]} == {alice_run}
        assert client.post(f"/api/v1/runs/{alice_run}/cancel", headers={**bob, "Idempotency-Key": "cross-project-cancel"}).status_code == 403
        assert client.get(f"/api/v1/runs/{alice_run}/jobs", headers=bob).status_code == 404
        assert client.get(f"/api/v1/runs/{alice_run}/artifacts", headers=bob).status_code == 404
    finally:
        platform.close()


def test_sec_auth_003_trusted_proxy_ignores_client_identity_and_requires_trusted_source(tmp_path: Path) -> None:
    platform, backend = build_local_backend(tmp_path)
    run_id = _run(
        TestClient(create_app(backend)),
        subject="owner",
        project_id="proxy-project",
        key="proxy-run",
        configuration=platform.config.configuration_fingerprint,
    )
    try:
        resolver = lambda _request: Principal("proxy-reader", frozenset({"runs:read"}), "TRUSTED_PROXY", frozenset({"proxy-project"}))
        client = TestClient(create_app(backend, auth_mode="trusted_proxy", principal_resolver=resolver), raise_server_exceptions=False)
        response = client.get(f"/api/v1/runs/{run_id}", headers={"X-Local-Principal": "attacker"})
        assert response.status_code == 200
        assert TestClient(create_app(backend, auth_mode="trusted_proxy", principal_resolver=lambda _request: Principal("proxy-reader", frozenset({"runs:read"}), "LOCAL_TEST_AUTH")), raise_server_exceptions=False).get(f"/api/v1/runs/{run_id}").status_code == 401
    finally:
        platform.close()


def test_sec_auth_source_registry_is_owner_bound(tmp_path: Path) -> None:
    registry = DurableSourceRegistry(tmp_path / "workspace" / "source-registry.json")
    service = ProductSourceService(tmp_path, registry)
    record = service.import_csv(registry_id="registry-alice", filename="orders.csv", payload=CSV, owner_subject="alice")
    assert service.list(owner_subject="alice") == (record,)
    assert service.list(owner_subject="bob") == ()
    with pytest.raises(ProductSourceError):
        service.get_for_owner(record.registry_id, owner_subject="bob")


def test_sec_rev_001_002_003_stale_cross_run_and_mutated_subjects_are_rejected(tmp_path: Path) -> None:
    platform, backend = build_local_backend(tmp_path)
    client = TestClient(create_app(backend), raise_server_exceptions=False)
    try:
        configuration = platform.config.configuration_fingerprint
        run_one = _run(client, subject="reviewer", project_id="review-a", key="review-run-1", configuration=configuration)
        run_two = _run(client, subject="reviewer", project_id="review-b", key="review-run-2", configuration=configuration)
        ref = platform.artifact_store.publish(
            ArtifactManifest(
                artifact_id="review-subject-step33",
                run_id=run_one,
                stage_id="EVIDENCE_FUSION",
                attempt_id="attempt-1",
                artifact_kind="ReviewSubject",
                media_type="application/json",
                producer="step33-security-test",
                storage_key=f"runs/{run_one}/artifacts/review-subject-step33.json",
            ),
            b"{}",
        )
        platform.control_store.register_artifact(ref)
        from dirty_data_to_olap.domain.contracts.canonical import ReviewCheckpoint, ReviewCompatibilityContext

        context = ReviewCompatibilityContext(
            review_checkpoint_id=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS,
            subject_stage="EVIDENCE_FUSION",
            subject_artifact_id=ref.artifact_id,
            subject_content_hash=ref.content_hash,
            subject_schema_version=ref.schema_version,
            model_version="step33-test-model",
            source_schema_fingerprints={"source": "step33-test-source"},
            policy_version="step33-test-policy",
            domain_assertion_refs=("step33-test-assertion",),
            subject_semantic_id="step33-test-subject",
            applicability_fingerprint="step33-test-applicability",
        )
        platform.control_store.register_review_subject_context(run_id=run_one, context=context)
        headers = {"X-Local-Principal": "reviewer"}
        path = f"/api/v1/runs/{run_one}/reviews/{context.review_checkpoint_id.value}"
        body = {"subject_artifact_id": ref.artifact_id, "subject_content_hash": ref.content_hash, "decision": "ACCEPTED", "rationale": "bounded security review", "expected_revision": 0}
        assert client.post(path, headers={**headers, "Idempotency-Key": "review-first"}, json=body).status_code == 200
        stale = client.post(path, headers={**headers, "Idempotency-Key": "review-stale"}, json=body)
        assert stale.status_code == 409
        mutated = client.post(path, headers={**headers, "Idempotency-Key": "review-mutated"}, json={**body, "subject_content_hash": "0" * 64})
        assert mutated.status_code == 409
        replay = client.post(f"/api/v1/runs/{run_two}/reviews/{context.review_checkpoint_id.value}", headers={**headers, "Idempotency-Key": "review-cross-run"}, json=body)
        assert replay.status_code == 404
    finally:
        platform.close()


def test_sec_sql_001_002_003_structural_sql_and_values_are_separate(tmp_path: Path) -> None:
    from tests.step21_support import retail_semantic_flow
    from dirty_data_to_olap.application.semantic_layer import SemanticLayerService
    from dirty_data_to_olap.domain.contracts.semantic import SemanticQueryRequest

    context, model = retail_semantic_flow()
    metric = model.metrics[0]
    request = SemanticQueryRequest(request_id="step33_sql_values", metric_ids=(metric.metric_id,))
    compilation = SemanticLayerService().resolve_query(model, request, analytical_plan=context.plan, compiled_plan=context.compiled_plan, materialization=context.materialization)
    assert "read_csv" not in compilation.query_plan.sql_template.lower()
    assert "SELECT" in compilation.query_plan.sql_template
    assert all(";" not in compilation.query_plan.sql_template for _ in (0,))
    product_code = next(attribute for dimension in model.dimensions for attribute in dimension.attributes if attribute.physical_column_ref == "product_code")
    injected_value = "P-001' OR 1=1 --"
    injected = SemanticLayerService().resolve_query(model, SemanticQueryRequest(request_id="step33_sql_injection", metric_ids=(metric.metric_id,), filters=({"attribute_id": product_code.semantic_attribute_id, "operator": "EQUALS", "values": (injected_value,)},)), analytical_plan=context.plan, compiled_plan=context.compiled_plan, materialization=context.materialization)
    assert injected.parameters == (injected_value,)
    assert injected_value not in injected.query_plan.sql_template

    from dirty_data_to_olap.adapters.semantic_query import DuckDBSemanticQueryExecutor, SemanticQueryExecutionError

    forged = compilation.model_copy(update={"query_plan": compilation.query_plan.model_copy(update={"sql_template": "SELECT * FROM read_csv('outside.csv')"})})
    with pytest.raises(SemanticQueryExecutionError, match="STRUCTURAL_QUERY_REJECTED"):
        DuckDBSemanticQueryExecutor(Path(".")).execute(model, forged)


def test_sec_net_001_002_unsupported_runtime_scheme_and_credentials_are_not_exposed(tmp_path: Path) -> None:
    profile = ConnectionProfileReference(profile_id="step33-network", database_engine=DatabaseEngine.POSTGRESQL, host="db.example", port=5432, database_name="source", credential_reference="vault://step33")

    class Resolver:
        def resolve(self, _profile, *, required_purpose, source_id):
            return RuntimeSqlCredentials("http://db.example:5432/source?password=STEP33_FAKE", credential_purpose=required_purpose, source_id=source_id)

    record = SourceRegistryRecord(registry_id="step33-network", source_id="step33-network-source", display_name="network", source_type=SourceType.POSTGRESQL, connection_profile=profile, adapter_name="dlt_sql_source", adapter_version="1")
    adapter = DltSqlSourceAdapter(project_root=tmp_path, credential_resolver=Resolver())
    with pytest.raises(SourceIngestionError) as raised:
        adapter.discover_source(SourceSelection(registry_id=record.registry_id), record)
    assert "STEP33_FAKE" not in str(raised.value)
    assert "http" not in str(raised.value).lower()


def test_sec_fs_001_002_003_file_sources_cannot_escape_project_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / "step33-outside.csv"
    outside.write_text("id,value\n1,secret\n", encoding="utf-8")
    try:
        record = SourceRegistryRecord(registry_id="outside-file", display_name="outside", source_type=SourceType.CSV, file_locator=str(outside), adapter_name="file_source", adapter_version="1")
        with pytest.raises(SourceIngestionError):
            FileSourceAdapter(SourceType.CSV, project_root=tmp_path).discover_source(SourceSelection(registry_id=record.registry_id), record)

        link = tmp_path / "escape.csv"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks are not available in this Windows test environment")
        linked = record.model_copy(update={"registry_id": "linked-file", "file_locator": str(link)})
        with pytest.raises(SourceIngestionError):
            FileSourceAdapter(SourceType.CSV, project_root=tmp_path).discover_source(SourceSelection(registry_id=linked.registry_id), linked)
    finally:
        outside.unlink(missing_ok=True)


def test_sec_api_001_002_bounded_requests_and_safe_errors(tmp_path: Path) -> None:
    platform, backend = build_local_backend(tmp_path)
    client = TestClient(create_app(backend), raise_server_exceptions=False)
    try:
        headers = {"X-Local-Principal": "api-security", "Idempotency-Key": "oversized-import", "X-Source-Filename": "orders.csv", "Content-Type": "text/csv"}
        oversized = client.post("/api/v1/sources/import", headers=headers, content=b"x" * (5 * 1024 * 1024 + 1))
        assert oversized.status_code == 413
        malformed = client.post("/api/v1/runs", headers={"X-Local-Principal": "api-security", "Idempotency-Key": "malformed", "Content-Type": "application/json"}, content=b"{not-json")
        assert malformed.status_code == 422
        assert "traceback" not in malformed.text.lower() and "not-json" not in malformed.text
        secret = client.post("/api/v1/runs", headers={"X-Local-Principal": "api-security", "Idempotency-Key": "secret-metadata"}, json={"project_id": "safe", "configuration_fingerprint": platform.config.configuration_fingerprint, "metadata": {"api_key": "STEP33_SECRET_CANARY"}})
        assert secret.status_code == 422 and "STEP33_SECRET_CANARY" not in secret.text
    finally:
        platform.close()


def test_sec_xss_001_deser_001_and_sec_001_source_surface_has_no_html_or_command_sink() -> None:
    frontend = (REPOSITORY_ROOT / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")
    api = (REPOSITORY_ROOT / "src" / "dirty_data_to_olap" / "entrypoints" / "api.py").read_text(encoding="utf-8")
    backend = (REPOSITORY_ROOT / "src" / "dirty_data_to_olap" / "application" / "backend.py").read_text(encoding="utf-8")
    assert "dangerouslySetInnerHTML" not in frontend and "innerHTML" not in frontend
    assert "subprocess" not in api and "pickle.loads" not in api and "yaml.load(" not in api
    assert "subprocess" not in backend and "pickle.loads" not in backend and "yaml.load(" not in backend
    assert "error.message" in frontend
