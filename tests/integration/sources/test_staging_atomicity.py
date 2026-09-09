from __future__ import annotations

from pathlib import Path
from uuid import uuid4
import shutil

import pytest

from dirty_data_to_olap.adapters.sources import staging
from dirty_data_to_olap.adapters.sources.staging import SourceFaithfulParquetStager
from dirty_data_to_olap.domain.contracts.source import AdapterReference, SourceIngestionError


def test_failed_parquet_publication_removes_partial_and_complete_outputs(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[3] / "workspace" / "tests" / f"atomic_{uuid4().hex}"
    root.mkdir(parents=True)
    stager = SourceFaithfulParquetStager(root, adapter_reference=AdapterReference(name="test", version="1", config_fingerprint="cfg"))

    def fail_write(*args: object, **kwargs: object) -> None:
        raise OSError("simulated interrupted write")

    monkeypatch.setattr(staging.pq, "write_table", fail_write)
    with pytest.raises(SourceIngestionError):
        stager.stage_rows(source_id="src", snapshot_id="snap", table_id="tbl", batch_index=0, first_ordinal=0, rows=[{"id": 1}], schema_fingerprint="schema", staging_root=root / "staging")
    assert not list(root.rglob("*.partial"))
    assert not list(root.rglob("*.parquet"))
    shutil.rmtree(root, ignore_errors=True)
