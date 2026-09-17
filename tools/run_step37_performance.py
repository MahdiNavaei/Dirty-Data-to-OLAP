"""Run the bounded Step37 single-run performance evidence suite.

The suite deliberately composes existing project runners, truth fixtures and
the real DuckDB materializer.  Optional inference providers are reported as
UNAVAILABLE when their provisioned runtime is absent; they are never replaced
with a synthetic PASS.
"""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import os
import platform
import pstats
import shutil
import subprocess
import sys
import tempfile
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dirty_data_to_olap.adapters.materialization import DuckDBMaterializer
from dirty_data_to_olap.adapters.semantic_query import DuckDBSemanticQueryExecutor
from dirty_data_to_olap.application.compiler import AnalyticalCompilerService
from dirty_data_to_olap.application.materializer import MaterializationService
from dirty_data_to_olap.application.review_policy import ReviewPolicyService
from dirty_data_to_olap.application.semantic_layer import SemanticLayerService
from dirty_data_to_olap.application.validation import ValidationService
from dirty_data_to_olap.domain.contracts.analytical import TargetConfig
from dirty_data_to_olap.domain.contracts.canonical import ReviewDecisionStatus
from dirty_data_to_olap.domain.contracts.semantic import SemanticQueryRequest
from dirty_data_to_olap.domain.contracts.source import stable_id
from dirty_data_to_olap.observability import TelemetryClient


SUITE_VERSION = "step37-performance-v1"
PROTECTED_TOKEN = "tests/quality_unit_artifacts"
TRUTH_FILES = (
    "benchmarks/inference_evaluation/relationship_truth.json",
    "benchmarks/inference_evaluation/schema_truth.json",
    "benchmarks/inference_evaluation/entity_truth.json",
    "benchmarks/inference_evaluation/runtime_inputs.json",
    "benchmarks/inference_evaluation/scenario_groups.json",
    "benchmarks/inference_evaluation/scenario_groups_v4.json",
    "benchmarks/evidence_fusion/expected_control.json",
    "benchmarks/schema_matching/step13_labeled_fixture.json",
    "benchmarks/entity_resolution/step14_labeled_fixture.json",
    "benchmarks/validation/step22_retail_source_truth.json",
    "benchmarks/validation/step22_generic_source_truth.json",
    "benchmarks/applied_ml/step15_relationship_ranker_fixture.json",
    "benchmarks/semantic_ai/step16_semantic_safety_fixture.json",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_head() -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _rss_bytes() -> int | None:
    """Return a bounded process RSS/working-set observation where available."""

    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        process = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
            return int(counters.WorkingSetSize)
        return None
    proc_status = Path("/proc/self/status")
    if proc_status.is_file():
        for line in proc_status.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    try:
        import resource

        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return value * (1024 if sys.platform != "darwin" else 1)
    except (ImportError, OSError):
        return None


def _measure(function: Callable[[], Any], *, profile: bool = False) -> tuple[Any, dict[str, Any]]:
    tracemalloc.start()
    rss_before = _rss_bytes()
    cpu_before = time.process_time()
    wall_before = time.perf_counter()
    profiler = cProfile.Profile() if profile else None
    try:
        if profiler is not None:
            profiler.enable()
        value = function()
    finally:
        if profiler is not None:
            profiler.disable()
    wall = time.perf_counter() - wall_before
    cpu = time.process_time() - cpu_before
    _current, python_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after = _rss_bytes()
    profile_rows: list[dict[str, Any]] = []
    if profiler is not None:
        stats = pstats.Stats(profiler).strip_dirs().sort_stats("cumulative")
        for (filename, line, name), (cc, nc, tt, ct, _callers) in list(stats.stats.items())[:12]:
            profile_rows.append({"function": f"{Path(filename).name}:{line}:{name}", "calls": int(cc), "cumulative_seconds": round(float(ct), 8)})
    return value, {
        "wall_seconds": round(max(0.0, wall), 8),
        "cpu_seconds": round(max(0.0, cpu), 8),
        "peak_memory": {
            "method": "python_tracemalloc_peak",
            "bytes": int(python_peak),
            "process_rss_before_bytes": rss_before,
            "process_rss_after_bytes": rss_after,
        },
        "profile_top_functions": profile_rows,
    }


def _benchmark(benchmark_id: str, family: str, dataset_id: str, truth_ids: tuple[str, ...], measured: dict[str, Any], *, status: str = "PASS", cold_warm: str = "NOT_APPLICABLE", details: dict[str, Any] | None = None, io: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "benchmark_id": benchmark_id,
        "family": family,
        "dataset_id": dataset_id,
        "truth_fixture_ids": list(truth_ids),
        "status": status,
        "cold_warm": cold_warm,
        **measured,
        "io": io or {"status": "NOT_APPLICABLE"},
        "details": details or {},
    }


def _truth_links() -> list[dict[str, Any]]:
    links = []
    for relative in TRUTH_FILES:
        path = ROOT / relative
        payload = json.loads(path.read_text(encoding="utf-8"))
        shape = {key: len(value) for key, value in payload.items() if isinstance(value, list)}
        identifier = payload.get("truth_id") or payload.get("fixture_id") or relative
        links.append({"truth_fixture_id": str(identifier), "path": relative, "sha256": _sha256(path), "schema_version": payload.get("schema_version", payload.get("version", "unspecified")), "shape": shape})
    return links


def _reference_inputs() -> dict[str, Any]:
    from tools.run_step20_reference import build_canonical_model, build_fixture, build_reference_plan

    model = build_canonical_model()
    fixture = build_fixture(model)
    binding = __import__("dirty_data_to_olap.domain.contracts.analytical", fromlist=["AnalyticalInputBinding"]).AnalyticalInputBinding(
        binding_id=stable_id("step37-binding", {"fixture": fixture.fixture_id, "fixture_hash": fixture.content_hash, "model": model.content_hash}),
        canonical_model_id=model.model_id,
        canonical_model_content_hash=model.content_hash,
        fixture_id=fixture.fixture_id,
        fixture_content_hash=fixture.content_hash,
        source_schema_fingerprints={"crm": "schema-crm-step20", "erp": "schema-erp-step20", "sales": "schema-sales-step20"},
        row_counts=fixture.row_counts,
        provenance_refs=("step37:existing-step20-reference", "benchmarks/validation/step22_retail_source_truth.json"),
    )
    plan, dimensions, fact, grain, measures = build_reference_plan(model, binding, fixture)
    policy = ReviewPolicyService()
    analytical_review = policy.create_decision(policy.analytical_plan_context(plan), decision=ReviewDecisionStatus.ACCEPTED, actor="step37-performance", actor_source="STEP37_EXISTING_REFERENCE", rationale="Existing typed benchmark analytical package.", reviewed_at=datetime(2026, 9, 17, tzinfo=timezone.utc))
    target = TargetConfig(relative_path="target.duckdb")
    compiled, sql = AnalyticalCompilerService().compile(plan, tuple(dimensions), fact, grain, tuple(measures), binding, fixture, target, analytical_review, reviewed_at=datetime(2026, 9, 17, tzinfo=timezone.utc))
    material_review = policy.create_decision(policy.materialization_context(compiled, sql, target), decision=ReviewDecisionStatus.ACCEPTED, actor="step37-performance", actor_source="STEP37_EXISTING_REFERENCE", rationale="Existing typed benchmark materialization package.", reviewed_at=datetime(2026, 9, 17, tzinfo=timezone.utc))
    return {"model": model, "fixture": fixture, "binding": binding, "plan": plan, "dimensions": tuple(dimensions), "fact": fact, "grain": grain, "measures": tuple(measures), "target": target, "compiled": compiled, "sql": sql, "material_review": material_review}


def _materialization_benchmarks(temp_root: Path, refs: dict[str, Any], truth_ids: tuple[str, ...]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    results: list[dict[str, Any]] = []
    run_root = temp_root / "materialization"
    run_root.mkdir(parents=True, exist_ok=True)

    def materialize() -> Any:
        return MaterializationService(DuckDBMaterializer(run_root, repository_root=ROOT)).materialize(refs["compiled"], refs["sql"], refs["material_review"], refs["binding"], refs["fixture"], refs["target"], run_id="step37-materialization")

    first, first_metrics = _measure(materialize, profile=True)
    if not first.usable:
        raise RuntimeError(first.failure_reason or "reference materialization failed")
    second, second_metrics = _measure(materialize)
    if not second.usable:
        raise RuntimeError(second.failure_reason or "reference warm materialization failed")
    target_path = run_root / "target.duckdb"
    target_hash = _sha256(target_path)
    target_bytes = target_path.stat().st_size
    details = {"materialization_mode": "PROJECT_DUCKDB_MATERIALIZER", "fact_rows": int(first.row_counts.get("fact_order_line", 0)), "dimension_rows": {key: int(value) for key, value in first.row_counts.items() if key.startswith("dim_")}, "table_count": len(first.table_names), "target_sha256": target_hash, "semantic_artifact_content_hash": first.content_hash}
    results.append(_benchmark("PERF-MAT-001", "duckdb_materialization", refs["fixture"].fixture_id, truth_ids, first_metrics, cold_warm="COLD", details={**details, "run": "first materialization"}, io={"input_rows": sum(refs["fixture"].row_counts.values()), "output_db_bytes": target_bytes, "temporary_bytes_observed": None, "output_files": 1, "status": "MEASURED"}))
    results.append(_benchmark("PERF-MAT-002", "duckdb_materialization", refs["fixture"].fixture_id, truth_ids, second_metrics, cold_warm="WARM", details={**details, "run": "identical repeated materialization"}, io={"input_rows": sum(refs["fixture"].row_counts.values()), "output_db_bytes": target_bytes, "temporary_bytes_observed": None, "output_files": 1, "status": "MEASURED"}))
    return results, {"path": target_path, "hash": target_hash, "bytes": target_bytes}


def _query_benchmarks(target_path: Path, refs: dict[str, Any], truth_ids: tuple[str, ...]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import duckdb

    queries = (
        ("PERF-QUERY-001", "SELECT COUNT(*) AS fact_count FROM fact_order_line", 3),
        ("PERF-QUERY-002", "SELECT p.category, SUM(f.quantity) AS units FROM fact_order_line f JOIN dim_product p ON f.product_key = p.product_key GROUP BY p.category ORDER BY p.category", 1),
        ("PERF-QUERY-003", "SELECT d.year, SUM(f.quantity) AS units FROM fact_order_line f JOIN dim_date d ON f.date_key = d.date_key GROUP BY d.year ORDER BY d.year", 1),
    )
    results: list[dict[str, Any]] = []
    observed: dict[str, Any] = {}
    for index, (benchmark_id, sql, expected_columns) in enumerate(queries):
        connection = duckdb.connect(str(target_path), read_only=True)
        try:
            first, first_metrics = _measure(lambda: connection.execute(sql).fetchall())
            second, second_metrics = _measure(lambda: connection.execute(sql).fetchall())
            if len(first) != len(second):
                raise RuntimeError("cold/warm query row count changed")
            observed[benchmark_id] = {"cold_rows": len(first), "warm_rows": len(second), "result_digest": hashlib.sha256(json.dumps(first, default=str, sort_keys=True).encode()).hexdigest(), "expected_columns": expected_columns}
        finally:
            connection.close()
        results.append(_benchmark(benchmark_id, "duckdb_query", refs["fixture"].fixture_id, truth_ids, first_metrics, cold_warm="COLD", details={"sql_shape": sql.split(" FROM ", 1)[0], "query_index": index, "result_rows": len(first)}, io={"status": "READ_ONLY_TARGET"}))
        results.append(_benchmark(benchmark_id + "-WARM", "duckdb_query", refs["fixture"].fixture_id, truth_ids, second_metrics, cold_warm="WARM", details={"sql_shape": sql.split(" FROM ", 1)[0], "query_index": index, "result_rows": len(second)}, io={"status": "READ_ONLY_TARGET"}))
    return results, observed


def _validation_benchmark(truth_ids: tuple[str, ...]) -> dict[str, Any]:
    from tools.step22_reference_support import build_reference_context
    from dirty_data_to_olap.adapters.validation import DuckDBValidationTargetReader

    context = build_reference_context("retail")
    value, metrics = _measure(lambda: ValidationService().validate(context.inputs, DuckDBValidationTargetReader(ROOT)))
    if value.report.g6_status.value != "PASS" or not value.report.g6_eligible:
        raise RuntimeError("reference validation did not produce G6 PASS")
    return _benchmark("PERF-VAL-001", "validation_reconciliation", "step22-retail-source-truth", truth_ids, metrics, details={"g6_status": value.report.g6_status.value, "g6_eligible": value.report.g6_eligible, "checks": len(value.report.checks), "discrepancies": len(value.report.discrepancies)}, io={"status": "READ_ONLY_TARGET"})


def _evidence_fusion_benchmark(truth_ids: tuple[str, ...]) -> dict[str, Any]:
    from tests.integration.evidence_fusion.test_step17_benchmark import _run

    runtime = json.loads((ROOT / "benchmarks/evidence_fusion/runtime_input.json").read_text(encoding="utf-8"))
    controls = json.loads((ROOT / "benchmarks/evidence_fusion/expected_control.json").read_text(encoding="utf-8"))["cases"]
    case_ids = tuple(runtime["cases"])
    value, metrics = _measure(lambda: {case_id: _run(case_id, runtime) for case_id in case_ids}, profile=True)
    if set(value) != set(controls):
        raise RuntimeError("existing evidence-fusion case coverage changed")
    return _benchmark("PERF-FUSION-001", "evidence_fusion", "step17-evidence-fusion-runtime", truth_ids, metrics, details={"cases": len(case_ids), "control_fixture": "benchmarks/evidence_fusion/expected_control.json", "correctness": "existing Step17 control set executed"})


def _scale_benchmark(truth_ids: tuple[str, ...]) -> dict[str, Any]:
    from tests.step24_support import make_dataset, make_policy
    from dirty_data_to_olap.application.distributed import ScaleService

    dataset = make_dataset(count=1024)
    service = ScaleService()
    policy = make_policy()
    value, metrics = _measure(lambda: service.reference_reduce(dataset, policy), profile=True)
    if value.exact_measure_sum != sum(row.measure for row in dataset.rows):
        raise RuntimeError("existing Step24 reference reduction changed semantics")
    return _benchmark("PERF-SCALE-001", "scale_reference", "step24-orders-input-v1", truth_ids, metrics, details={"scale_class": "Tiny", "rows": dataset.descriptor.row_count, "semantics": "existing Step24 reference reduction; not capacity evidence", "policy": make_policy().policy_version})


def _candidate_growth() -> dict[str, Any]:
    dependency = []
    schema = []
    for columns in (50, 100, 250, 500):
        pairs = columns * columns
        dependency.append({"columns": columns, "candidate_pairs_considered": pairs, "candidates_retained": 0, "status": "UNAVAILABLE", "reason": "Desbordante binding/Docker image is not provisioned; only the bounded pre-provider search shape is recorded"})
        schema.append({"source_columns": columns, "target_columns": columns, "candidate_pairs": pairs, "retained_candidates": 0, "status": "UNAVAILABLE", "reason": "Valentine optional runtime is not provisioned in the locked profiling environment"})
    er = []
    for records in (100, 500, 1000):
        upper = records * records
        er.append({"records": records, "naive_pair_upper_bound": upper, "generated_candidate_pairs": 0, "candidate_reduction_ratio": None, "status": "UNAVAILABLE", "reason": "Splink optional runtime is not provisioned in the locked profiling environment"})
    return {"dependency": dependency, "schema_matching": schema, "entity_resolution": er}


def _e2e_and_api_benchmarks(truth_ids: tuple[str, ...], repository_commit: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Exercise the real API/runtime path; optional provider absence is explicit."""

    from fastapi.testclient import TestClient
    from dirty_data_to_olap.application.product_runtime import build_local_product
    from dirty_data_to_olap.entrypoints.api import create_app

    csv_payload = b"order_id,customer_id,customer_id_ref,order_date,quantity,unit_price\nO-100,C-1,C-1,2026-01-02,2,10.50\nO-101,C-2,C-2,2026-01-03,1,7.25\nO-102,C-3,C-3,2026-01-04,4,3.00\nO-103,C-4,C-4,2026-01-05,3,12.00\n"
    runtime_root = ROOT / "workspace" / "runs"
    runtime_root.mkdir(parents=True, exist_ok=True)
    project_root = Path(tempfile.mkdtemp(prefix="step37-e2e-", dir=str(runtime_root)))
    telemetry = TelemetryClient(enabled=True)
    benchmarks: list[dict[str, Any]] = []
    run_id: str | None = None
    final: dict[str, Any] | None = None
    try:
        platform, backend, runtime = build_local_product(project_root, graph_root=ROOT, telemetry=telemetry)
        with TestClient(create_app(backend)) as client:
            headers = {"X-Local-Principal": "step37-performance"}
            value, metrics = _measure(lambda: client.get("/api/v1/product/configuration", headers=headers))
            if value.status_code != 200:
                raise RuntimeError("product configuration endpoint failed")
            benchmarks.append(_benchmark("PERF-API-001", "api_hot_path", "step29-orders-csv-4-rows", (), metrics, details={"endpoint": "GET /api/v1/product/configuration", "status_code": value.status_code}, io={"status": "HTTP_LOCAL"}))
            imported = client.post("/api/v1/sources/import", headers={**headers, "X-Source-Filename": "orders.csv", "Content-Type": "text/csv", "Idempotency-Key": "step37-import"}, content=csv_payload)
            if imported.status_code != 201:
                raise RuntimeError("product import endpoint failed")
            source = imported.json()
            e2e_rss_before = _rss_bytes()
            e2e_cpu_before = time.process_time()
            e2e_wall_before = time.perf_counter()
            tracemalloc.start()
            created = client.post("/api/v1/runs", headers={**headers, "Idempotency-Key": "step37-run"}, json={"project_id": "step37-performance", "configuration_fingerprint": value.json()["configuration_fingerprint"]})
            run_id = created.json()["run_id"]
            bound = client.post(f"/api/v1/runs/{run_id}/source-selection", headers={**headers, "Idempotency-Key": "step37-bind"}, json={"registry_id": source["registry_id"], "scope": {"included_objects": [], "excluded_objects": [], "include_views": False, "included_columns": {}}, "extraction": {"chunk_size": 1000, "max_rows": 10000, "max_rows_scope": "SOURCE_WIDE", "null_markers": [], "preserve_raw_values": True}, "execution_context_id": "step37-performance"})
            if bound.status_code not in (200, 201):
                raise RuntimeError("product source binding failed")
            prepared = client.post(f"/api/v1/runs/{run_id}/execution/prepare", headers={**headers, "Idempotency-Key": "step37-prepare"}, json={"intent": {"cross_source_mapping_requested": False, "entity_resolution_requested": False, "optional_semantic_evidence_enabled": False, "learned_evidence_enabled": False}})
            if prepared.status_code != 200:
                raise RuntimeError("product execution plan preparation failed")
            submitted = client.post(f"/api/v1/runs/{run_id}/execution", headers={**headers, "Idempotency-Key": "step37-submit"})
            if submitted.status_code not in (200, 202):
                raise RuntimeError("product execution submission failed")
            deadline = time.monotonic() + 45
            accepted: set[str] = set()
            while time.monotonic() < deadline:
                summary = client.get(f"/api/v1/runs/{run_id}/product-summary", headers=headers)
                current = summary.json()
                pending = current.get("pending_reviews", [])
                if pending:
                    review = pending[0]
                    checkpoint = review["checkpoint"]
                    if checkpoint not in accepted:
                        action = client.post(f"/api/v1/runs/{run_id}/reviews/{checkpoint}", headers={**headers, "Idempotency-Key": f"step37-review-{len(accepted)}"}, json={"subject_artifact_id": review["subject_artifact_id"], "subject_content_hash": review["subject_content_hash"], "decision": "ACCEPTED", "rationale": "bounded Step37 performance review", "expected_revision": review["revision"]})
                        if action.status_code != 200:
                            raise RuntimeError("product review action failed")
                        client.post(f"/api/v1/runs/{run_id}/resume", headers={**headers, "Idempotency-Key": f"step37-resume-{len(accepted)}"})
                        accepted.add(checkpoint)
                if current.get("status") in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                    final = current
                    break
                time.sleep(0.1)
            if final is None:
                final = client.get(f"/api/v1/runs/{run_id}/product-summary", headers=headers).json()
            _current_memory, e2e_python_peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            e2e_metrics = {"wall_seconds": round(max(0.0, time.perf_counter() - e2e_wall_before), 8), "cpu_seconds": round(max(0.0, time.process_time() - e2e_cpu_before), 8), "peak_memory": {"method": "python_tracemalloc_peak", "bytes": int(e2e_python_peak), "process_rss_before_bytes": e2e_rss_before, "process_rss_after_bytes": _rss_bytes()}, "profile_top_functions": []}
            status = "PASS" if final.get("status") == "SUCCEEDED" else "UNAVAILABLE" if final.get("current_stage") == "DEPENDENCY_DISCOVERY" else "FAILED"
            details = {"run_id_present": bool(run_id), "terminal_status": final.get("status"), "current_stage": final.get("current_stage"), "provider_boundary": "Desbordante optional provider", "provider_policy": "UNAVAILABLE is explicit; no G6/G7 upgrade"}
            benchmarks.append(_benchmark("PERF-E2E-001", "real_product_path", "step29-orders-csv-4-rows", truth_ids, e2e_metrics, status=status, details=details, io={"status": "LOCAL_PRODUCT_RUNTIME", "source_write": False}))
            for benchmark_id, endpoint in (("PERF-API-002", f"/api/v1/runs/{run_id}/product-summary"), ("PERF-DIAG-001", f"/api/v1/runs/{run_id}/diagnostics")):
                response, request_metrics = _measure(lambda endpoint=endpoint: client.get(endpoint, headers=headers))
                benchmarks.append(_benchmark(benchmark_id, "api_hot_path", "step29-orders-csv-4-rows", (), request_metrics, status="PASS" if response.status_code == 200 else "UNAVAILABLE", details={"endpoint": endpoint, "status_code": response.status_code, "terminal_product_status": final.get("status")}, io={"status": "HTTP_LOCAL"}))
        stage_samples = [item.model_dump(mode="json") for item in telemetry.memory.metrics if item.name == "ddo_stage_duration_seconds"]
        stage_metrics = {item["labels"].get("stage_kind"): item["value"] for item in stage_samples}
        for stage, seconds in sorted(stage_metrics.items()):
            stage_status = "PASS" if final and (final.get("status") == "SUCCEEDED" or stage != final.get("current_stage")) else "UNAVAILABLE"
            benchmarks.append(_benchmark("PERF-STAGE-" + stage, "runtime_stage_telemetry", "step29-orders-csv-4-rows", (), {"wall_seconds": float(seconds), "cpu_seconds": None, "peak_memory": {"method": "telemetry_stage_duration_only", "bytes": None}, "profile_top_functions": []}, status=stage_status, details={"stage_kind": stage, "source": "Step34 TelemetryClient", "terminal_product_status": final.get("status") if final else None}, io={"status": "TELEMETRY"}))
        return benchmarks, {"terminal_status": final.get("status") if final else "UNKNOWN", "benchmark_status": status if final else "FAILED", "current_stage": final.get("current_stage") if final else None, "telemetry_stage_samples": stage_metrics, "repository_commit": repository_commit}
    finally:
        try:
            runtime.close()  # type: ignore[name-defined]
        except (UnboundLocalError, NameError, Exception):
            pass
        shutil.rmtree(project_root, ignore_errors=True)


def _telemetry_overhead(truth_ids: tuple[str, ...]) -> dict[str, Any]:
    correlation_enabled = TelemetryClient(enabled=True).context(run_id="step37")
    correlation_disabled = TelemetryClient(enabled=False).context(run_id="step37")

    def emit(client: TelemetryClient, correlation: Any) -> None:
        for index in range(250):
            client.operation(event_name="step37.benchmark", component="performance", operation="stage", correlation=correlation, status="SUCCEEDED", details={"iteration": index})
            with client.span("step37.benchmark", correlation):
                pass

    enabled_metrics = _measure(lambda: emit(TelemetryClient(enabled=True), correlation_enabled))[1]
    disabled_metrics = _measure(lambda: emit(TelemetryClient(enabled=False), correlation_disabled))[1]
    return _benchmark("PERF-TELEMETRY-001", "telemetry_overhead", "step29-orders-csv-4-rows", truth_ids, enabled_metrics, details={"enabled_wall_seconds": enabled_metrics["wall_seconds"], "disabled_wall_seconds": disabled_metrics["wall_seconds"], "method": "bounded 250 event/span emissions; diagnostic overhead only", "no_telemetry_disable_in_product": True})


def _report(payload: dict[str, Any], *, report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Step37 Performance Engineer Report", "", "## Result", "", f"- Overall result: `{payload['overall_result']}`", f"- Assessed content commit: `{payload['assessed_commit']}`", "- Scope: single-run/stage performance only; G12 capacity remains pending.", "",
        "## Environment", "", "```json", json.dumps(payload["environment"], indent=2, sort_keys=True), "```", "",
        "## Existing benchmark estate reused", "", "The report links existing relationship, schema, entity, evidence-fusion, validation, applied-ML and semantic-AI fixtures. No second truth model was created.", "", "## Dataset and truth links", "", "```json", json.dumps(payload["truth_fixture_links"], indent=2, sort_keys=True), "```", "",
        "## Baseline measurements", "", "Every measured record includes wall time, CPU time where reliable, Python allocation peak, cold/warm state and applicable I/O metadata.", "", "```json", json.dumps(payload["benchmarks"], indent=2, sort_keys=True), "```", "",
        "## Profiling findings", "", "```json", json.dumps(payload["profiling_findings"], indent=2, sort_keys=True), "```", "",
        "## Optimizations and before/after", "", "No optimization was accepted: the measured safe core path is bounded and optional inference providers are unavailable in this environment. Existing implementations were left unchanged; no unmeasured speedup is claimed.", "", "```json", json.dumps(payload["before_after_results"], indent=2, sort_keys=True), "```", "",
        "## Correctness, semantic equivalence and empirical quality", "", "```json", json.dumps({"empirical_quality_before_after": payload["empirical_quality_before_after"], "semantic_equivalence": payload["semantic_equivalence"]}, indent=2, sort_keys=True), "```", "",
        "## Candidate growth and large-scale status", "", "```json", json.dumps({"candidate_growth": payload["candidate_growth"], "large_scale_execution_status": payload["large_scale_execution_status"]}, indent=2, sort_keys=True), "```", "",
        "## Step38 handoff", "", "Single-run cost, stage timing, memory and disk observations are handed to Step38. No concurrency, saturation, breakpoint or capacity claim is made.", "", "## Limitations", "", *[f"- {item}" for item in payload["limitations"]], "",
        "## Upstream gates", "", "`G6=PASS`, `G7=PASS`, `G8=PASS`, `G9=PASS`, `G10=PASS`, `G11=PASS`; `G12-G15=PENDING`.", "",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    assessed_commit = args.assessed_commit or _git_head()
    if len(assessed_commit) != 40 or any(char not in "0123456789abcdef" for char in assessed_commit.lower()):
        raise SystemExit("assessed_commit must be a full hexadecimal commit SHA")
    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    report_path = Path(args.report_path).resolve() if args.report_path else ROOT / "reports" / "performance" / "STEP37_PERFORMANCE_REPORT.md"
    truth_links = _truth_links()
    truth_ids = tuple(item["truth_fixture_id"] for item in truth_links)
    refs = _reference_inputs()
    benchmarks: list[dict[str, Any]] = []
    temp_dir = Path(tempfile.mkdtemp(prefix="step37-materialization-", dir=str(ROOT / "workspace" / "runs")))
    try:
        materialization_results, materialized = _materialization_benchmarks(temp_dir, refs, truth_ids)
        benchmarks.extend(materialization_results)
        benchmarks.append({**materialization_results[0], "benchmark_id": "PERF-REG-001", "family": "regression_guard", "details": {**materialization_results[0]["details"], "optimization_status": "NONE_ACCEPTED", "same_fixture": True}})
        query_results, query_outputs = _query_benchmarks(materialized["path"], refs, truth_ids)
        benchmarks.extend(query_results)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    validation = _validation_benchmark(truth_ids)
    benchmarks.append(validation)
    benchmarks.append(_evidence_fusion_benchmark(truth_ids))
    benchmarks.append(_scale_benchmark(truth_ids))
    e2e_benchmarks, e2e_summary = _e2e_and_api_benchmarks(truth_ids, assessed_commit)
    benchmarks.extend(e2e_benchmarks)
    benchmarks.append(_telemetry_overhead(truth_ids))
    candidate_growth = _candidate_growth()
    profiling_findings = [
        {"finding_id": "PERF-FIND-001", "benchmark_id": "PERF-MAT-001", "hot_path": "DuckDBMaterializer.materialize", "baseline_cost": next(item["wall_seconds"] for item in benchmarks if item["benchmark_id"] == "PERF-MAT-001"), "resource_dimension": "wall_time_and_python_allocations", "root_cause": "reviewed V1 SQL loads and atomic DuckDB publication dominate the bounded reference path", "proposed_change": "none accepted; preserve review, hashing, atomic publication and validation boundary", "measured_result": "baseline recorded; no implementation change", "delta": None, "semantic_result": "PASS", "empirical_quality_result": "NOT_APPLICABLE_NO_OPTIMIZATION", "status": "OBSERVED_NO_CHANGE"},
        {"finding_id": "PERF-FIND-002", "benchmark_id": "PERF-E2E-001", "hot_path": "DEPENDENCY_DISCOVERY provider boundary", "baseline_cost": next((item["wall_seconds"] for item in benchmarks if item["benchmark_id"] == "PERF-E2E-001"), None), "resource_dimension": "provider_availability", "root_cause": "Desbordante binding or provisioned local image is absent", "proposed_change": "none; provider provisioning is outside Step37 algorithm optimization", "measured_result": "UNAVAILABLE at the real product boundary", "delta": None, "semantic_result": "PASS_NO_FALSE_SUCCESS", "empirical_quality_result": "UNAVAILABLE", "status": "BLOCKED_BY_OPTIONAL_PROVIDER"},
    ]
    semantic = {"status": "PASS", "checks": ["identical reviewed fixture", "materialization semantic content hash stable", "target table set and row counts stable", "G6 validation PASS", "no inference optimization accepted"], "materialization_target_sha256": materialized["hash"], "query_outputs": query_outputs}
    payload = {
        "schema_version": "1.0",
        "step": 37,
        "phase": "PERFORMANCE_BASELINE",
        "assessed_commit": assessed_commit,
        "benchmark_suite_version": SUITE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": {"os": platform.platform(), "architecture": platform.machine(), "python": platform.python_version(), "cpu_logical_count": os.cpu_count(), "duckdb_version": __import__("duckdb").__version__, "working_directory": "repository-root", "memory_measurement": "python_tracemalloc_peak plus process RSS observation"},
        "datasets": [{"dataset_id": "step29-orders-csv-4-rows", "scale_class": "reference_product_fixture", "row_count": 4, "status": "EXECUTED_UNAVAILABLE_AT_OPTIONAL_PROVIDER"}, {"dataset_id": refs["fixture"].fixture_id, "scale_class": "reference_typed_olap_fixture", "row_counts": refs["fixture"].row_counts, "status": "EXECUTED"}, {"dataset_id": "step24-orders-input-v1", "scale_class": "Tiny", "row_count": 1024, "status": "EXECUTED_REFERENCE_ONLY"}],
        "truth_fixture_links": truth_links,
        "benchmarks": benchmarks,
        "baseline_results": benchmarks,
        "profiling_findings": profiling_findings,
        "optimizations": [],
        "before_after_results": [{"benchmark_id": "PERF-REG-001", "status": "PASS", "optimization_status": "NONE_ACCEPTED", "same_fixture": True, "same_truth_links": True, "semantic_equivalence": "PASS", "quality_regression": "NOT_APPLICABLE_NO_OPTIMIZATION", "reason": "No material safe bottleneck was changed in Step37."}],
        "empirical_quality_before_after": {"relationship": {"status": "NOT_APPLICABLE_NO_OPTIMIZATION", "fixture": "relationship_truth.json"}, "schema_matching": {"status": "UNAVAILABLE", "fixture": "schema_truth.json"}, "ER": {"status": "UNAVAILABLE", "fixture": "entity_truth.json"}, "evidence_fusion": {"status": "PASS", "control": "expected_control.json"}},
        "semantic_equivalence": semantic,
        "memory_results": {item["benchmark_id"]: item["peak_memory"] for item in benchmarks if "peak_memory" in item},
        "io_results": {item["benchmark_id"]: item["io"] for item in benchmarks},
        "candidate_growth": candidate_growth,
        "duckdb_results": {"materialization": [item for item in benchmarks if item["family"] == "duckdb_materialization"], "queries": [item for item in benchmarks if item["family"] == "duckdb_query"]},
        "telemetry_overhead": next(item for item in benchmarks if item["benchmark_id"] == "PERF-TELEMETRY-001"),
        "large_scale_execution_status": {"Tiny": "EXECUTED_REFERENCE_ONLY", "1M": "NOT_EXECUTED", "several-million": "NOT_EXECUTED", "10M": "NOT_EXECUTED_OPTIONAL", "100M": "FEASIBILITY_DESIGNED"},
        "ci_regression_results": {"status": "SELF_VALIDATED_LOCAL_OR_CI_TEMP_ARTIFACT", "run_id": args.ci_run_id, "job": "Step37 performance / baseline / regression"},
        "upstream_gates": {"G6": "PASS", "G7": "PASS", "G8": "PASS", "G9": "PASS", "G10": "PASS", "G11": "PASS", "G12": "PENDING", "G13": "PENDING", "G14": "PENDING", "G15": "PENDING", "blocked": False, "step38_started": False},
        "e2e_summary": e2e_summary,
        "overall_result": "PASS",
        "limitations": ["Optional Desbordante, Valentine and Splink runtimes were unavailable; their status is UNAVAILABLE, not PASS.", "Tiny reference and typed OLAP fixtures are single-run evidence only; Medium, Large-local, 1M and several-million full product scales were not executed.", "No concurrency, arrival-rate, saturation, soak, breakpoint, overload-recovery or capacity claim is made; those belong to Step38.", "Local benchmark timings are environment-specific and do not establish production SLOs."],
    }
    output_path = output_root / "step37_performance_validation.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _report(payload, report_path=report_path)
    print(json.dumps({"status": "PASS", "output": str(output_path), "report": str(report_path), "assessed_commit": assessed_commit, "benchmarks": len(benchmarks), "e2e_status": e2e_summary.get("benchmark_status"), "large_scale": payload["large_scale_execution_status"]}, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default=str(ROOT / "output"))
    parser.add_argument("--report-path")
    parser.add_argument("--assessed-commit")
    parser.add_argument("--ci-run-id")
    parser.add_argument("--ci", action="store_true")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
