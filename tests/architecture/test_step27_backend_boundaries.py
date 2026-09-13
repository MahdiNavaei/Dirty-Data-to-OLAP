from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_step27_domain_api_contract_has_no_transport_or_adapter_dependency() -> None:
    source = (ROOT / "src" / "dirty_data_to_olap" / "domain" / "contracts" / "api.py").read_text(encoding="utf-8")
    assert "fastapi" not in source.lower()
    assert "adapters" not in source.lower()
    assert "entrypoints" not in source.lower()


def test_step27_entrypoint_does_not_open_storage_or_invoke_engines() -> None:
    source = (ROOT / "src" / "dirty_data_to_olap" / "entrypoints" / "api.py").read_text(encoding="utf-8")
    assert "SQLiteControlStore" not in source
    assert "LocalArtifactStore" not in source
    assert "execute_sql" not in source
    assert "PARTIAL" not in source
    assert "QUEUED" not in source


def test_step27_backend_uses_ports_and_has_no_fastapi_dependency() -> None:
    source = (ROOT / "src" / "dirty_data_to_olap" / "application" / "backend.py").read_text(encoding="utf-8")
    assert "fastapi" not in source.lower()
    assert "sqlite3" not in source.lower()
    assert "ReviewPolicyService" in source
    assert "ArtifactStorePort" in source
    assert "ControlStorePort" in source
    assert "QUEUED" not in source
