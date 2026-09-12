from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_step24_is_a_data_execution_boundary_not_step28_job_control() -> None:
    source = (ROOT / "src/dirty_data_to_olap/application/distributed.py").read_text(encoding="utf-8")
    assert "PartitionExecutorPort" in source
    assert "StageExecutorPort" not in source
    assert "def cancel" not in source.lower()
    assert "celery" not in source.lower()
    assert "redis" not in source.lower()
    assert "kafka" not in source.lower()


def test_step24_has_no_unbounded_executor_submission_or_provider_dependency() -> None:
    source = (ROOT / "src/dirty_data_to_olap/application/distributed.py").read_text(encoding="utf-8")
    assert "ThreadPoolExecutor" in source
    assert "for _ in range(slots)" in source
    assert "spark" not in source.lower()
    assert "import ray" not in source.lower()
    assert "dask" not in source.lower()


def test_step25_and_step28_implementations_remain_absent() -> None:
    assert not (ROOT / "src/dirty_data_to_olap/application/stage_orchestrator.py").exists()
    assert not (ROOT / "src/dirty_data_to_olap/application/job_control.py").exists()
    assert not (ROOT / "src/dirty_data_to_olap/application/ux.py").exists()


def test_scale_contracts_do_not_import_third_party_executor_types() -> None:
    source = (ROOT / "src/dirty_data_to_olap/domain/contracts/distributed.py").read_text(encoding="utf-8")
    assert all(name not in source for name in ("pyspark", "ray", "dask", "import pickle", "cloudpickle", "dill"))
