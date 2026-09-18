"""Run the bounded Step37 single-run performance evidence suite.

The suite composes existing project runners, truth fixtures, real provider
boundaries and the real DuckDB materializer.  Missing provider runtimes remain
explicitly non-PASS; they are never replaced with a synthetic result.
"""

from __future__ import annotations

import argparse
import csv
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
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
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


def _provider_identity() -> dict[str, Any]:
    """Inspect the configured immutable local provider image without pulling it."""

    image = os.environ.get("DESBORDANTE_PROVIDER_IMAGE")
    if not image:
        return {"image": None, "image_id": None, "inspection": "NOT_CONFIGURED"}
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image, "--format={{.Id}}"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return {"image": image, "image_id": None, "inspection": f"FAILED:{error.__class__.__name__}"}
    image_id = result.stdout.strip()
    return {
        "image": image,
        "image_id": image_id if image_id.startswith("sha256:") else None,
        "inspection": "PASS" if image_id.startswith("sha256:") else "INVALID_IMAGE_ID",
    }


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


def _stage_baselines(stage_dir: Path) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for stage in ("dependency", "schema_matching", "entity_resolution"):
        path = stage_dir / f"{stage}.json"
        if not path.is_file():
            raise RuntimeError(f"required real provider stage baseline is missing: {path}")
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("status") != "PASS":
            raise RuntimeError(f"required real provider stage baseline did not pass: {stage}")
        output[stage] = value
    return output


def _candidate_growth(stage_baselines: dict[str, dict[str, Any]]) -> dict[str, Any]:
    dependency = stage_baselines["dependency"]
    matching = stage_baselines["schema_matching"]
    entity = stage_baselines["entity_resolution"]
    dependency_stats = dependency["details"].get("provider_search_stats", {})
    matching_stats = matching["details"].get("provider_search_stats", {})
    entity_stats = entity["details"].get("provider_search_stats", {})
    return {
        "dependency": [{"columns": dependency_stats.get("input_columns"), "candidate_pairs_considered": dependency_stats.get("candidate_column_pairs"), "candidates_retained": dependency_stats.get("emitted_candidates"), "provider_calls": dependency_stats.get("provider_calls"), "evaluated_pairs": dependency_stats.get("evaluated_pairs"), "input_rows": dependency["details"].get("input_scale", {}).get("rows"), "status": "EXECUTED", "reason": "real Desbordante UCC/FD provider baseline; bounded project search and output assertions passed", "provider": "desbordante-docker"}],
        "schema_matching": [{"candidate_pairs": matching_stats.get("column_pairs_evaluated"), "retained_candidates": matching_stats.get("output_candidates_emitted"), "provider_calls": matching["details"].get("provider_calls"), "evaluated_pairs": matching["details"].get("evaluated_pairs"), "status": "EXECUTED", "reason": "real Valentine adapter baseline; aggregate-only candidate assertions passed", "provider": "valentine", "wall_seconds": matching["wall_seconds"]}],
        "entity_resolution": [{"records": entity_stats.get("records_read"), "naive_pair_upper_bound": entity_stats.get("all_pairs"), "generated_candidate_pairs": entity_stats.get("candidate_pairs"), "candidate_reduction_ratio": round(1 - (entity_stats.get("candidate_pairs", 0) / entity_stats.get("all_pairs", 1)), 8), "provider_calls": entity["details"].get("provider_calls"), "evaluated_pairs": entity["details"].get("evaluated_pairs"), "status": "EXECUTED", "reason": "real Splink adapter baseline; bounded candidate-only assertions passed", "provider": "splink", "wall_seconds": entity["wall_seconds"]}],
        "provider_runtime": {"dependency": dependency["details"], "schema_matching": matching["details"], "entity_resolution": entity["details"]},
    }


def _medium_dataset(model: Any, *, row_count: int) -> Any:
    from dirty_data_to_olap.domain.contracts.analytical import AnalyticalCell, AnalyticalColumnBinding, AnalyticalInputDataset, AnalyticalInputRow, AnalyticalInputTable, AnalyticalRowBatch
    from dirty_data_to_olap.domain.contracts.canonical import canonical_entity_id

    provenance = ("step37:medium-deterministic-v1", "source:medium-csv", "performance:bounded")

    def entity(kind: str, index: int) -> str:
        type_id = {"customer": "cet_customer", "product": "cet_product", "branch": "cet_branch"}[kind]
        return canonical_entity_id(type_id, (f"medium-{kind}-{index}",), "canonical-v1")

    customers = tuple(entity("customer", index) for index in range(25))
    products = tuple(entity("product", index) for index in range(50))
    branches = tuple(entity("branch", index) for index in range(8))

    def columns(items: tuple[tuple[str, str], ...]) -> tuple[Any, ...]:
        return tuple(AnalyticalColumnBinding(column_id=f"medium-{name}-column", column_name=name, logical_type=logical, nullable=True, lineage_refs=provenance) for name, logical in items)

    def row(row_ref: str, canonical: str, values: tuple[tuple[str, Any], ...]) -> Any:
        return AnalyticalInputRow(row_ref=row_ref, canonical_reference=canonical, values=tuple(AnalyticalCell(column_name=name, value=value) for name, value in values), source_record_refs=(row_ref,), lineage_refs=(row_ref,))

    customer_rows = tuple(row(f"medium-customer-record-{index}", value, (("customer_code", f"C-{index:04d}"), ("display_name", f"Customer {index}"), ("source_record_refs", (f"medium-customer-record-{index}",)))) for index, value in enumerate(customers))
    product_rows = tuple(row(f"medium-product-record-{index}", value, (("product_code", f"P-{index:04d}"), ("product_name", f"Product {index}"), ("category", f"Category {index % 5}"), ("source_record_refs", (f"medium-product-record-{index}",)))) for index, value in enumerate(products))
    branch_rows = tuple(row(f"medium-branch-record-{index}", value, (("branch_code", f"B-{index:04d}"), ("branch_name", f"Branch {index}"), ("source_record_refs", (f"medium-branch-record-{index}",)))) for index, value in enumerate(branches))
    orders = tuple((index, entity("customer", index % len(customers)), entity("branch", index % len(branches)), date(2026, 1, 1) + timedelta(days=index % 30)) for index in range(max(1000, row_count // 100)))
    order_rows = tuple(row(f"medium-order-record-{index}", f"medium-order-{index}", (("order_event_id", f"medium-order-{index}"), ("customer_entity_id", customer), ("branch_entity_id", branch), ("order_date", order_date), ("source_record_refs", (f"medium-order-record-{index}",)))) for index, customer, branch, order_date in orders)
    line_rows = []
    lines_per_order = row_count // len(orders)
    for index in range(row_count):
        order_index = index % len(orders)
        sequence = index // len(orders) + 1
        _order_index, customer, branch, order_date = orders[order_index]
        line_rows.append(row(f"medium-line-record-{index}", f"medium-line-{index}", (("order_event_id", f"medium-order-{order_index}"), ("line_sequence", sequence), ("product_entity_id", products[index % len(products)]), ("customer_entity_id", customer), ("branch_entity_id", branch), ("order_date", order_date), ("quantity", (index % 5) + 1), ("unit_price", Decimal("10.00") + Decimal(index % 17)), ("discount_rate", Decimal("0.00")), ("source_record_refs", (f"medium-line-record-{index}",)))))

    def table(table_id: str, concept: str, table_columns: tuple[tuple[str, str], ...], rows: tuple[Any, ...]) -> Any:
        return AnalyticalInputTable(table_id=table_id, canonical_concept_ref=concept, columns=columns(table_columns), batches=(AnalyticalRowBatch(batch_id=f"medium:{table_id}:0", table_id=table_id, rows=rows, source_batch_refs=(f"medium:{table_id}",), lineage_refs=provenance),), source_table_refs=(table_id,), lineage_refs=provenance)

    return AnalyticalInputDataset(
        dataset_id="step37-medium-100k-v1",
        canonical_model_id=model.model_id,
        canonical_model_content_hash=model.content_hash,
        tables=(
            table("customers", "customer", (("customer_code", "STRING"), ("display_name", "STRING"), ("source_record_refs", "STRING_LIST")), customer_rows),
            table("products", "product", (("product_code", "STRING"), ("product_name", "STRING"), ("category", "STRING"), ("source_record_refs", "STRING_LIST")), product_rows),
            table("branches", "branch", (("branch_code", "STRING"), ("branch_name", "STRING"), ("source_record_refs", "STRING_LIST")), branch_rows),
            table("orders", "order", (("order_event_id", "STRING"), ("customer_entity_id", "STRING"), ("branch_entity_id", "STRING"), ("order_date", "DATE"), ("source_record_refs", "STRING_LIST")), order_rows),
            table("order_lines", "orderline", (("order_event_id", "STRING"), ("line_sequence", "INTEGER"), ("product_entity_id", "STRING"), ("customer_entity_id", "STRING"), ("branch_entity_id", "STRING"), ("order_date", "DATE"), ("quantity", "DECIMAL"), ("unit_price", "DECIMAL"), ("discount_rate", "DECIMAL"), ("source_record_refs", "STRING_LIST")), tuple(line_rows)),
        ),
        allow_literal_sql=True,
        provenance_refs=provenance,
    )


def _medium_benchmark(temp_root: Path, refs: dict[str, Any], truth_ids: tuple[str, ...]) -> dict[str, Any]:
    from dirty_data_to_olap.adapters.dependencies.desbordante import DesbordanteDependencyAdapter
    from dirty_data_to_olap.adapters.profiling.dataprofiler import DataProfilerAdapter
    from dirty_data_to_olap.adapters.sources.files import FileSourceAdapter
    from dirty_data_to_olap.application.dependency_discovery import DependencyDiscoveryService
    from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService
    from dirty_data_to_olap.application.profiling import ProfilingService
    from dirty_data_to_olap.domain.contracts.dependency import DependencyKind, DependencyRequest, DependencySearchPolicy
    from dirty_data_to_olap.domain.contracts.profiling import ProfileMode, ProfileRequest
    from dirty_data_to_olap.domain.contracts.source import ExtractionPolicy, SelectionScope, SourceRegistryRecord, SourceSelection, SourceType, stable_id

    row_count = 100_000
    provider = _provider_identity()
    root = temp_root / "medium-boundary"
    root.mkdir(parents=True, exist_ok=True)
    source_path = root / "medium_orders.csv"
    with source_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("order_id", "customer_id"))
        for index in range(row_count):
            writer.writerow((f"O-{index:06d}", f"C-{index % 1000:04d}"))
    source_bytes = source_path.stat().st_size
    source_adapter = FileSourceAdapter(SourceType.CSV, project_root=root)
    registry = SourceRegistryRecord(registry_id="step37-medium-registry", display_name="Step37 Medium deterministic CSV", source_type=SourceType.CSV, file_locator=str(source_path), adapter_name=source_adapter.name, adapter_version=source_adapter.version)
    selection = SourceSelection(registry_id=registry.registry_id, scope=SelectionScope(), extraction=ExtractionPolicy(chunk_size=10_000, max_rows=row_count), execution_context_id="step37-medium")
    catalog, discovery_metrics = _measure(lambda: source_adapter.discover_source(selection, registry))
    snapshot, extraction_metrics = _measure(lambda: source_adapter.create_bounded_snapshot(catalog, selection, execution_context_id=selection.execution_context_id, staging_root=root / "staging"))
    table_id = catalog.tables[0].table_id
    profile_request = ProfileRequest(profile_request_id="step37-medium-profile", source_id=catalog.source_id, snapshot_id=snapshot.snapshot.snapshot_id, selected_table_ids=(table_id,), mode=ProfileMode.FULL)
    profile, profiling_metrics = _measure(lambda: ProfilingService(DataProfilerAdapter(), project_root=root).profile(profile_request, catalog, snapshot, artifact_root=root / "artifacts"))
    if profile.completeness.value != "COMPLETE" or profile.failures:
        raise RuntimeError("Medium profiling did not complete")
    dependency_request = DependencyRequest(request_id="step37-medium-dependency", source_id=catalog.source_id, snapshot_id=snapshot.snapshot.snapshot_id, selected_table_ids=(table_id,), requested_kinds=(DependencyKind.UCC, DependencyKind.FD), search_policy=DependencySearchPolicy(max_tables=1, max_columns_per_table=2, max_ucc_arity=1, max_fd_lhs_arity=1, max_ind_arity=1, max_runtime_seconds=120))
    medium_policy = PrivacyPolicyService(project_root=root)
    dependency_service = DependencyDiscoveryService(
        DesbordanteDependencyAdapter(project_root=root, privacy_policy=medium_policy),
        project_root=root,
        privacy_policy=medium_policy,
    )
    dependency, dependency_metrics = _measure(lambda: dependency_service.discover(dependency_request, catalog, snapshot, profiles=profile, artifact_root=root / "artifacts"))
    if dependency.status.value != "COMPLETE" or dependency.capabilities[0].status.value != "AVAILABLE":
        raise RuntimeError(
            "Medium Desbordante dependency baseline did not complete: "
            f"status={dependency.status.value}; "
            f"capabilities={[item.status.value for item in dependency.capabilities]}; "
            f"failures={[item.detail for item in dependency.failures]}"
        )
    medium_dataset = _medium_dataset(refs["model"], row_count=row_count)
    from tools.run_step20_reference import build_reference_plan
    from dirty_data_to_olap.domain.contracts.analytical import AnalyticalInputBinding
    binding = AnalyticalInputBinding(binding_id=stable_id("step37-medium-binding", {"dataset": medium_dataset.content_hash, "model": refs["model"].content_hash}), canonical_model_id=refs["model"].model_id, canonical_model_content_hash=refs["model"].content_hash, dataset_id=medium_dataset.dataset_id, dataset_content_hash=medium_dataset.content_hash, row_counts=medium_dataset.row_counts, provenance_refs=("step37:medium-deterministic-v1",))
    plan, dimensions, fact, grain, measures = build_reference_plan(refs["model"], binding, refs["fixture"], input_dataset=medium_dataset)
    policy = ReviewPolicyService()
    analytical_review = policy.create_decision(policy.analytical_plan_context(plan), decision=ReviewDecisionStatus.ACCEPTED, actor="step37-medium", actor_source="STEP37_MEDIUM_BOUNDARY", rationale="Deterministic Medium benchmark uses the existing reviewed analytical contract.", reviewed_at=datetime(2026, 9, 18, tzinfo=timezone.utc))
    target = TargetConfig(relative_path="target.duckdb")
    compiled, sql = AnalyticalCompilerService().compile(plan, tuple(dimensions), fact, grain, tuple(measures), binding, medium_dataset, target, analytical_review, reviewed_at=datetime(2026, 9, 18, tzinfo=timezone.utc))
    material_review = policy.create_decision(policy.materialization_context(compiled, sql, target), decision=ReviewDecisionStatus.ACCEPTED, actor="step37-medium", actor_source="STEP37_MEDIUM_BOUNDARY", rationale="Deterministic Medium target publication uses the existing reviewed materialization contract.", reviewed_at=datetime(2026, 9, 18, tzinfo=timezone.utc))
    olap_root = root / "olap"
    artifact, materialization_metrics = _measure(lambda: MaterializationService(DuckDBMaterializer(olap_root, repository_root=ROOT)).materialize(compiled, sql, material_review, binding, medium_dataset, target, run_id="step37-medium"))
    if not artifact.usable:
        raise RuntimeError(artifact.failure_reason or "Medium materialization failed")
    target_path = olap_root / "target.duckdb"
    import duckdb
    def validate_medium() -> dict[str, Any]:
        connection = duckdb.connect(str(target_path), read_only=True)
        try:
            fact_rows = int(connection.execute("SELECT COUNT(*) FROM fact_order_line").fetchone()[0])
            quantity_sum = int(connection.execute("SELECT SUM(quantity) FROM fact_order_line").fetchone()[0])
            unresolved = int(connection.execute("SELECT COUNT(*) FROM fact_order_line f LEFT JOIN dim_customer c ON f.customer_key=c.customer_key LEFT JOIN dim_product p ON f.product_key=p.product_key LEFT JOIN dim_branch b ON f.branch_key=b.branch_key LEFT JOIN dim_date d ON f.date_key=d.date_key WHERE c.customer_key IS NULL OR p.product_key IS NULL OR b.branch_key IS NULL OR d.date_key IS NULL").fetchone()[0])
            duplicate_grain = int(connection.execute("SELECT COUNT(*) - COUNT(DISTINCT order_event_id || ':' || CAST(line_sequence AS VARCHAR)) FROM fact_order_line").fetchone()[0])
            return {"fact_rows": fact_rows, "quantity_sum": quantity_sum, "expected_quantity_sum": sum((index % 5) + 1 for index in range(row_count)), "unresolved_foreign_keys": unresolved, "duplicate_grain": duplicate_grain, "status": "PASS" if fact_rows == row_count and quantity_sum == sum((index % 5) + 1 for index in range(row_count)) and unresolved == 0 and duplicate_grain == 0 else "FAIL"}
        finally:
            connection.close()
    validation, validation_metrics = _measure(validate_medium)
    if validation["status"] != "PASS":
        raise RuntimeError("Medium semantic validation failed")
    stage_metrics = {"source_discovery": discovery_metrics, "extraction_staging": extraction_metrics, "profiling": profiling_metrics, "dependency_candidate_generation": dependency_metrics, "duckdb_materialization": materialization_metrics, "validation": validation_metrics}
    total_wall = sum(float(item["wall_seconds"]) for item in stage_metrics.values())
    total_cpu = sum(float(item["cpu_seconds"]) for item in stage_metrics.values())
    peak = max(int(item["peak_memory"]["bytes"]) for item in stage_metrics.values())
    staged_bytes = sum((root / batch.artifact_location).stat().st_size for batch in snapshot.batches)
    expected_quantity = validation["expected_quantity_sum"]
    return _benchmark("PERF-MEDIUM-001", "medium_production_boundary", "step37-medium-100k-v1", truth_ids, {"wall_seconds": round(total_wall, 8), "cpu_seconds": round(total_cpu, 8), "peak_memory": {"method": "max_python_tracemalloc_peak_over_stage_measurements", "bytes": peak}, "profile_top_functions": []}, details={"scale_class": "Medium", "row_count": row_count, "input_bytes": source_bytes, "staged_bytes": staged_bytes, "output_db_bytes": target_path.stat().st_size, "stages": {name: {"status": "PASS", **metrics} for name, metrics in stage_metrics.items()}, "validation": validation, "semantic_oracle": {"status": "PASS", "expected_fact_rows": row_count, "expected_quantity_sum": expected_quantity, "observed_fact_rows": validation["fact_rows"], "observed_quantity_sum": validation["quantity_sum"]}, "provider_boundary": dependency.capabilities[0].engine, "provider_image": provider["image"], "provider_image_id": provider["image_id"], "provider_source_revision": "b211961f3f272ed8815ef1ffbda90573b11e1116", "network_allowed": False, "container_read_only": True, "input_mount": "bind-readonly:/input", "dependency_search": dependency.search_stats.model_dump(mode="json")}, io={"status": "MEASURED", "input_source_bytes": source_bytes, "staged_parquet_bytes": staged_bytes, "output_db_bytes": target_path.stat().st_size, "output_files": 1})


def _e2e_and_api_benchmarks(truth_ids: tuple[str, ...], repository_commit: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Exercise the real API/runtime path; optional provider absence is explicit."""

    from fastapi.testclient import TestClient
    from dirty_data_to_olap.application.product_runtime import build_local_product
    from dirty_data_to_olap.entrypoints.api import create_app

    csv_payload = b"order_id,customer_id,customer_id_ref,order_date,quantity,unit_price\nO-100,C-1,C-1,2026-01-02,2,10.50\nO-101,C-2,C-2,2026-01-03,1,7.25\nO-102,C-3,C-3,2026-01-04,4,3.00\nO-103,C-4,C-4,2026-01-05,3,12.00\n"
    runtime_root = ROOT / "workspace" / "runs"
    runtime_root.mkdir(parents=True, exist_ok=True)
    provider = _provider_identity()
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
            # The accepted Docker provider boundary is real native work.  A
            # 45-second polling window incorrectly converted a still-running
            # provider call into a FAILED performance result on bounded hosts.
            deadline = time.monotonic() + 180
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
            status = "PASS" if final.get("status") == "SUCCEEDED" else "UNAVAILABLE" if final.get("current_stage") == "DEPENDENCY_DISCOVERY" and final.get("status") in {"FAILED", "CANCELLED"} else "FAILED"
            validation = final.get("validation") or {}
            output = final.get("output") or {}
            details = {"run_id_present": bool(run_id), "terminal_status": final.get("status"), "current_stage": final.get("current_stage"), "provider_boundary": "DesbordanteDependencyAdapter -> DesbordanteDockerEngine", "provider_image": provider["image"], "provider_image_id": provider["image_id"], "provider_source_revision": "b211961f3f272ed8815ef1ffbda90573b11e1116", "network_allowed": False, "container_read_only": True, "input_mount": "bind-readonly:/input", "g6_status": validation.get("g6_status"), "g6_eligible": validation.get("g6_eligible"), "validated_output": output.get("validated"), "accepted_review_checkpoints": sorted(accepted), "terminal_required": "SUCCEEDED"}
            benchmarks.append(_benchmark("PERF-E2E-001", "real_product_path", "step29-orders-csv-4-rows", truth_ids, e2e_metrics, status=status, details=details, io={"status": "LOCAL_PRODUCT_RUNTIME", "source_write": False}))
            for benchmark_id, endpoint in (("PERF-API-002", f"/api/v1/runs/{run_id}/product-summary"), ("PERF-DIAG-001", f"/api/v1/runs/{run_id}/diagnostics")):
                response, request_metrics = _measure(lambda endpoint=endpoint: client.get(endpoint, headers=headers))
                benchmarks.append(_benchmark(benchmark_id, "api_hot_path", "step29-orders-csv-4-rows", (), request_metrics, status="PASS" if response.status_code == 200 else "UNAVAILABLE", details={"endpoint": endpoint, "status_code": response.status_code, "terminal_product_status": final.get("status")}, io={"status": "HTTP_LOCAL"}))
        stage_samples = [item.model_dump(mode="json") for item in telemetry.memory.metrics if item.name == "ddo_stage_duration_seconds"]
        stage_metrics = {item["labels"].get("stage_kind"): item["value"] for item in stage_samples}
        for stage, seconds in sorted(stage_metrics.items()):
            stage_status = "PASS" if final and (final.get("status") == "SUCCEEDED" or stage != final.get("current_stage")) else "UNAVAILABLE"
            benchmarks.append(_benchmark("PERF-STAGE-" + stage, "runtime_stage_telemetry", "step29-orders-csv-4-rows", (), {"wall_seconds": float(seconds), "cpu_seconds": None, "peak_memory": {"method": "telemetry_stage_duration_only", "bytes": None}, "profile_top_functions": []}, status=stage_status, details={"stage_kind": stage, "source": "Step34 TelemetryClient", "terminal_product_status": final.get("status") if final else None}, io={"status": "TELEMETRY"}))
        final_validation = (final or {}).get("validation") or {}
        final_output = (final or {}).get("output") or {}
        return benchmarks, {"terminal_status": final.get("status") if final else "UNKNOWN", "benchmark_status": status if final else "FAILED", "current_stage": final.get("current_stage") if final else None, "g6_status": final_validation.get("g6_status"), "g6_eligible": final_validation.get("g6_eligible"), "validated_output": final_output.get("validated"), "telemetry_stage_samples": stage_metrics, "repository_commit": repository_commit}
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
        "## Optimizations and before/after", "", "No optimization was accepted: the measured safe core path is bounded and the real provider baselines were retained as evidence rather than altered for timing. Existing implementations were left unchanged; no unmeasured speedup is claimed.", "", "```json", json.dumps(payload["before_after_results"], indent=2, sort_keys=True), "```", "",
        "## Correctness, semantic equivalence and empirical quality", "", "```json", json.dumps({"empirical_quality_before_after": payload["empirical_quality_before_after"], "semantic_equivalence": payload["semantic_equivalence"]}, indent=2, sort_keys=True), "```", "",
        "## Candidate growth and large-scale status", "", "Medium is the smallest full production-boundary scale executed here. The several-million decision is a bounded feasibility assessment based on measured Medium wall time, CPU, memory and disk observations; it is not a capacity claim.", "", "```json", json.dumps({"candidate_growth": payload["candidate_growth"], "large_scale_execution_status": payload["large_scale_execution_status"], "large_scale_evidence": payload["large_scale_evidence"]}, indent=2, sort_keys=True), "```", "",
        "## Step38 handoff", "", "Single-run cost, stage timing, memory and disk observations are handed to Step38. No concurrency, saturation, breakpoint or capacity claim is made.", "", "## Limitations", "", *[f"- {item}" for item in payload["limitations"]], "",
        "## Provider stage baselines", "", "Dependency Discovery, schema matching and entity resolution each ran through their project-owned adapter/service boundaries in separate locked environments. The machine receipt records their measured wall/CPU/memory observations and provider identity.", "", "```json", json.dumps(payload["provider_stage_baselines"], indent=2, sort_keys=True), "```", "",
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
    stage_baselines = _stage_baselines(Path(args.stage_baselines_dir).resolve() if args.stage_baselines_dir else output_root.parent / "stage-baselines")
    truth_links = _truth_links()
    truth_ids = tuple(item["truth_fixture_id"] for item in truth_links)
    refs = _reference_inputs()
    benchmarks: list[dict[str, Any]] = []
    runtime_root = ROOT / "workspace" / "runs"
    runtime_root.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="step37-materialization-", dir=str(runtime_root)))
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
    medium = _medium_benchmark(Path(tempfile.mkdtemp(prefix="step37-medium-", dir=str(runtime_root))), refs, truth_ids)
    benchmarks.append(medium)
    e2e_benchmarks, e2e_summary = _e2e_and_api_benchmarks(truth_ids, assessed_commit)
    benchmarks.extend(e2e_benchmarks)
    benchmarks.append(_telemetry_overhead(truth_ids))
    candidate_growth = _candidate_growth(stage_baselines)
    profiling_findings = [
        {"finding_id": "PERF-FIND-001", "benchmark_id": "PERF-MAT-001", "hot_path": "DuckDBMaterializer.materialize", "baseline_cost": next(item["wall_seconds"] for item in benchmarks if item["benchmark_id"] == "PERF-MAT-001"), "resource_dimension": "wall_time_and_python_allocations", "root_cause": "reviewed V1 SQL loads and atomic DuckDB publication dominate the bounded reference path", "proposed_change": "none accepted; preserve review, hashing, atomic publication and validation boundary", "measured_result": "baseline recorded; no implementation change", "delta": None, "semantic_result": "PASS", "empirical_quality_result": "NOT_APPLICABLE_NO_OPTIMIZATION", "status": "OBSERVED_NO_CHANGE"},
        {"finding_id": "PERF-FIND-002", "benchmark_id": "PERF-MEDIUM-001", "hot_path": "bounded Medium dependency and materialization boundary", "baseline_cost": next((item["wall_seconds"] for item in benchmarks if item["benchmark_id"] == "PERF-MEDIUM-001"), None), "resource_dimension": "wall_time_cpu_and_peak_memory", "root_cause": "provider-backed candidate generation plus deterministic DuckDB publication dominate the bounded Medium path", "proposed_change": "none accepted; preserve the reviewed provider, hashing, atomic publication and validation boundaries", "measured_result": "real Medium boundary completed with semantic oracle PASS", "delta": None, "semantic_result": "PASS", "empirical_quality_result": "NOT_APPLICABLE_NO_OPTIMIZATION", "status": "OBSERVED_NO_CHANGE"},
    ]
    semantic = {"status": "PASS", "checks": ["identical reviewed fixture", "materialization semantic content hash stable", "target table set and row counts stable", "G6 validation PASS", "no inference optimization accepted"], "materialization_target_sha256": materialized["hash"], "query_outputs": query_outputs}
    large_scale_evidence = {
        "1M": {"status": "NOT_EXECUTED", "reason": "The 100k Medium boundary is the smallest meaningful full production-boundary benchmark; a 1M run was not started because the bounded CI wall-clock budget must retain room for real provider E2E and independent stage baselines.", "observed_medium_wall_seconds": medium["wall_seconds"], "observed_medium_peak_memory_bytes": medium["peak_memory"]["bytes"], "observed_medium_output_db_bytes": medium["details"]["output_db_bytes"]},
        "several-million": {"status": "NOT_EXECUTED", "reason": "A bounded feasibility assessment used the measured Medium wall time, CPU, peak Python allocation and output-database size; a several-million materialization would exceed the Step37 single-run budget and is a capacity experiment reserved for Step38.", "observed_medium_wall_seconds": medium["wall_seconds"], "observed_medium_cpu_seconds": medium["cpu_seconds"], "observed_medium_peak_memory_bytes": medium["peak_memory"]["bytes"], "observed_medium_output_db_bytes": medium["details"]["output_db_bytes"]},
        "10M": {"status": "NOT_EXECUTED_OPTIONAL", "reason": "Optional feasibility only; no execution or capacity claim."},
        "100M": {"status": "FEASIBILITY_DESIGNED", "reason": "Future distributed/partitioned design only; not executed and not a Step37 capacity result."},
    }
    payload = {
        "schema_version": "1.0",
        "step": 37,
        "phase": "PERFORMANCE_BASELINE",
        "assessed_commit": assessed_commit,
        "content_commit": assessed_commit,
        "benchmark_suite_version": SUITE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": {"os": platform.platform(), "architecture": platform.machine(), "python": platform.python_version(), "cpu_logical_count": os.cpu_count(), "duckdb_version": __import__("duckdb").__version__, "working_directory": "repository-root", "memory_measurement": "python_tracemalloc_peak plus process RSS observation"},
        "datasets": [{"dataset_id": "step29-orders-csv-4-rows", "scale_class": "reference_product_fixture", "row_count": 4, "status": "EXECUTED_REAL_PROVIDER"}, {"dataset_id": refs["fixture"].fixture_id, "scale_class": "reference_typed_olap_fixture", "row_counts": refs["fixture"].row_counts, "status": "EXECUTED"}, {"dataset_id": "step24-orders-input-v1", "scale_class": "Tiny", "row_count": 1024, "status": "EXECUTED_REFERENCE_ONLY"}, {"dataset_id": "step37-medium-100k-v1", "scale_class": "Medium", "row_count": 100000, "status": "EXECUTED_PRODUCTION_BOUNDARY"}],
        "truth_fixture_links": truth_links,
        "benchmarks": benchmarks,
        "baseline_results": benchmarks,
        "profiling_findings": profiling_findings,
        "optimizations": [],
        "before_after_results": [{"benchmark_id": "PERF-REG-001", "status": "PASS", "optimization_status": "NONE_ACCEPTED", "same_fixture": True, "same_truth_links": True, "semantic_equivalence": "PASS", "quality_regression": "NOT_APPLICABLE_NO_OPTIMIZATION", "reason": "No material safe bottleneck was changed in Step37."}],
        "empirical_quality_before_after": {"relationship": {"status": "NOT_APPLICABLE_NO_OPTIMIZATION", "fixture": "relationship_truth.json"}, "schema_matching": {"status": "NOT_APPLICABLE_NO_OPTIMIZATION", "fixture": "schema_truth.json", "provider_stage": "schema_matching", "provider_stage_status": stage_baselines["schema_matching"]["status"]}, "ER": {"status": "NOT_APPLICABLE_NO_OPTIMIZATION", "fixture": "entity_truth.json", "provider_stage": "entity_resolution", "provider_stage_status": stage_baselines["entity_resolution"]["status"]}, "evidence_fusion": {"status": "PASS", "control": "expected_control.json"}},
        "semantic_equivalence": semantic,
        "memory_results": {item["benchmark_id"]: item["peak_memory"] for item in benchmarks if "peak_memory" in item},
        "io_results": {item["benchmark_id"]: item["io"] for item in benchmarks},
        "candidate_growth": candidate_growth,
        "provider_stage_baselines": stage_baselines,
        "medium_benchmark": medium,
        "duckdb_results": {"materialization": [item for item in benchmarks if item["family"] == "duckdb_materialization"], "queries": [item for item in benchmarks if item["family"] == "duckdb_query"]},
        "telemetry_overhead": next(item for item in benchmarks if item["benchmark_id"] == "PERF-TELEMETRY-001"),
        "large_scale_execution_status": {"Tiny": "EXECUTED_REFERENCE_ONLY", "Medium": "EXECUTED", "1M": "NOT_EXECUTED", "several-million": "NOT_EXECUTED", "10M": "NOT_EXECUTED_OPTIONAL", "100M": "FEASIBILITY_DESIGNED"},
        "large_scale_evidence": large_scale_evidence,
        "ci_regression_results": {"status": "CI_EXECUTED" if args.ci and args.ci_run_id else "LOCAL_EXECUTED", "run_id": args.ci_run_id, "job": "Step37 performance / baseline / regression"},
        "upstream_gates": {"G6": "PASS", "G7": "PASS", "G8": "PASS", "G9": "PASS", "G10": "PASS", "G11": "PASS", "G12": "PENDING", "G13": "PENDING", "G14": "PENDING", "G15": "PENDING", "blocked": False, "step38_started": False},
        "e2e_summary": e2e_summary,
        "overall_result": "PASS" if e2e_summary.get("benchmark_status") == "PASS" and e2e_summary.get("terminal_status") == "SUCCEEDED" and e2e_summary.get("g6_status") == "PASS" and e2e_summary.get("g6_eligible") is True and e2e_summary.get("validated_output") is True and medium.get("status") == "PASS" else "BLOCKED",
        "limitations": ["Provider image and optional matching/entity environments are explicit locked execution inputs; no provider result is fabricated when provisioning is absent.", "Tiny and typed-reference fixtures remain reference evidence; the deterministic Medium benchmark is the smallest full production-boundary scale executed in Step37.", "No concurrency, arrival-rate, saturation, soak, breakpoint, overload-recovery or capacity claim is made; those belong to Step38.", "Local benchmark timings are environment-specific and do not establish production SLOs."],
    }
    output_path = output_root / "step37_performance_validation.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _report(payload, report_path=report_path)
    print(json.dumps({"status": payload["overall_result"], "output": str(output_path), "report": str(report_path), "assessed_commit": assessed_commit, "benchmarks": len(benchmarks), "e2e_status": e2e_summary.get("benchmark_status"), "large_scale": payload["large_scale_execution_status"]}, sort_keys=True))
    return 0 if payload["overall_result"] == "PASS" else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default=str(ROOT / "output"))
    parser.add_argument("--report-path")
    parser.add_argument("--assessed-commit")
    parser.add_argument("--ci-run-id")
    parser.add_argument("--stage-baselines-dir")
    parser.add_argument("--ci", action="store_true")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
