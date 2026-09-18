"""Execute one real Step37 provider-stage baseline in its locked environment.

The stage integrations are intentionally kept separate because the repository
declares a profiling/matching conflict and the provider environments must not
silently collapse into one unverified installation.  Each selected test calls
the project-owned adapter and its project-owned service boundary.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TARGETS = {
    "dependency": ("tests/integration/dependencies/test_step12_real_provider.py", "test_step12_real_provider_executes_ucc_fd_ind"),
    "schema_matching": ("tests/integration/matching/test_step13_real_valentine.py", "test_step13_real_valentine_instance_and_schema_modes_are_bounded_and_aggregate_only"),
    "entity_resolution": ("tests/integration/entity_resolution/test_step14_real_splink.py", "test_step14_real_splink_is_bounded_local_and_candidate_only"),
}


def _provider_identity() -> dict[str, str | None]:
    image = os.environ.get("DESBORDANTE_PROVIDER_IMAGE")
    if not image:
        return {"image": None, "image_id": None}
    try:
        result = subprocess.run(["docker", "image", "inspect", image, "--format={{.Id}}"], cwd=ROOT, capture_output=True, text=True, check=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return {"image": image, "image_id": None}
    image_id = result.stdout.strip()
    return {"image": image, "image_id": image_id if image_id.startswith("sha256:") else None}


def _rss_bytes() -> int | None:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        if ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return int(counters.WorkingSetSize)
        return None
    status = Path("/proc/self/status")
    if status.is_file():
        for line in status.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    return None


def _load_test(path: Path):
    module_name = "step37_stage_" + path.stem
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load stage integration: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(stage: str, output: Path) -> int:
    if stage not in TARGETS:
        raise SystemExit(f"unsupported stage: {stage}")
    # The historical integration modules use these paths as an explicit
    # optional-runtime marker.  The packages themselves come from the locked
    # environment selected by the caller.
    for relative in ("workspace/test-temp/valentine-runtime", "workspace/test-temp/entity-resolution/splink-runtime"):
        (ROOT / relative).mkdir(parents=True, exist_ok=True)
    relative, function_name = TARGETS[stage]
    started = time.perf_counter()
    cpu_started = time.process_time()
    tracemalloc.start()
    status = "PASS"
    error = None
    captured: dict[str, object] = {}
    patched: tuple[object, str, object] | None = None
    try:
        module = _load_test(ROOT / relative)
        if stage == "dependency":
            from dirty_data_to_olap.application.dependency_discovery import DependencyDiscoveryService

            original = DependencyDiscoveryService.discover

            def capture_dependency(service: object, *args: object, **kwargs: object) -> object:
                result = original(service, *args, **kwargs)
                captured["result"] = result
                return result

            DependencyDiscoveryService.discover = capture_dependency  # type: ignore[method-assign]
            patched = (DependencyDiscoveryService, "discover", original)
        elif stage == "schema_matching":
            from dirty_data_to_olap.application.schema_matching import SchemaMatchingService

            original = SchemaMatchingService.match

            def capture_schema(service: object, *args: object, **kwargs: object) -> object:
                result = original(service, *args, **kwargs)
                captured["result"] = result
                return result

            SchemaMatchingService.match = capture_schema  # type: ignore[method-assign]
            patched = (SchemaMatchingService, "match", original)
        else:
            from dirty_data_to_olap.adapters.entity_resolution import SplinkEntityResolutionAdapter

            original = SplinkEntityResolutionAdapter.run

            def capture_entity(adapter: object, *args: object, **kwargs: object) -> object:
                result = original(adapter, *args, **kwargs)
                captured["result"] = result
                return result

            SplinkEntityResolutionAdapter.run = capture_entity  # type: ignore[method-assign]
            patched = (SplinkEntityResolutionAdapter, "run", original)
        getattr(module, function_name)()
    except BaseException as exc:  # the receipt must make a provider failure explicit
        status = "FAILED"
        error = f"{type(exc).__name__}: {str(exc)[:240]}"
    finally:
        if patched is not None:
            setattr(patched[0], patched[1], patched[2])
    _current, python_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    details = {
        "integration": relative.replace("\\", "/"),
        "test_function": function_name,
        "execution_boundary": "project-owned adapter and service",
        "provider_image": os.environ.get("DESBORDANTE_PROVIDER_IMAGE") if stage == "dependency" else None,
        "candidate_generation": "real provider/service output asserted by integration" if stage == "dependency" else "real adapter/service output asserted by integration",
        "rows_or_records": {"dependency": 20, "schema_matching": 8, "entity_resolution": 8}[stage],
        "provider_source_revision": "b211961f3f272ed8815ef1ffbda90573b11e1116" if stage == "dependency" else None,
        "provider_image_id": _provider_identity()["image_id"] if stage == "dependency" else None,
        "execution_environment": "Docker provider container" if stage == "dependency" else "separate locked Python environment",
        "network_allowed": False if stage == "dependency" else None,
        "container_read_only": True if stage == "dependency" else None,
        "input_mount": "bind-readonly:/input" if stage == "dependency" else None,
    }
    result = captured.get("result")
    if result is not None:
        capabilities = getattr(result, "capabilities", ())
        if capabilities:
            details["provider"] = capabilities[0].engine
            details["provider_version"] = capabilities[0].engine_version
        if stage == "dependency":
            search = result.search_stats.model_dump(mode="json")  # type: ignore[union-attr]
            observation = result.observation_scope.model_dump(mode="json")  # type: ignore[union-attr]
            details.update({
                "input_scale": {"tables": search.get("input_tables"), "columns": search.get("input_columns"), "rows": sum(observation.get("staged_rows_by_table", {}).values())},
                "candidate_counts": {"column_pairs": search.get("candidate_column_pairs"), "emitted": search.get("emitted_candidates"), "key_candidates": len(result.key_candidates)},  # type: ignore[union-attr]
                "provider_calls": search.get("provider_calls"),
                "evaluated_pairs": search.get("evaluated_pairs"),
                "provider_search_stats": search,
            })
        elif stage == "schema_matching":
            pruning = result.pruning.model_dump(mode="json")  # type: ignore[union-attr]
            details.update({
                "input_scale": {"source_pairs": pruning.get("source_pairs_evaluated"), "table_pairs": pruning.get("table_pairs_evaluated"), "provider_visible_column_pairs": pruning.get("provider_visible_column_pairs_by_matcher")},
                "candidate_counts": {"returned": len(result.candidates), "emitted": pruning.get("output_candidates_emitted")},  # type: ignore[union-attr]
                "provider_calls": sum(pruning.get("provider_table_pair_calls_by_matcher", {}).values()),
                "evaluated_pairs": pruning.get("column_pairs_evaluated"),
                "provider_search_stats": pruning,
            })
        else:
            metrics = result.metrics.model_dump(mode="json")  # type: ignore[union-attr]
            details.update({
                "input_scale": {"records": metrics.get("records_read"), "all_pairs": metrics.get("all_pairs")},
                "candidate_counts": {"candidate_pairs": metrics.get("candidate_pairs"), "predictions": metrics.get("predictions_emitted"), "clusters": metrics.get("clusters_emitted")},
                "provider_calls": metrics.get("predictions_emitted"),
                "evaluated_pairs": metrics.get("candidate_pairs"),
                "provider_search_stats": metrics,
            })
    if error:
        details["error"] = error
    payload = {
        "schema_version": "1.0",
        "step": 37,
        "stage": stage,
        "status": status,
        "wall_seconds": round(time.perf_counter() - started, 8),
        "cpu_seconds": round(time.process_time() - cpu_started, 8),
        "peak_memory": {"method": "python_tracemalloc_peak", "bytes": int(python_peak), "process_rss_before_bytes": None, "process_rss_after_bytes": _rss_bytes()},
        "details": details,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"stage": stage, "status": status, "output": str(output), "wall_seconds": payload["wall_seconds"]}, sort_keys=True))
    return 0 if status == "PASS" else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=tuple(TARGETS))
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    return run(args.stage, Path(args.output).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
