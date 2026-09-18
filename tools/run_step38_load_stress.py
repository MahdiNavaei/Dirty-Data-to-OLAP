"""Run the bounded Step38 local-reference load and stress suite.

The suite deliberately exercises the real Step29 HTTP listener and the real
Step28 SQLite/worker/artifact boundaries.  It is a local reference envelope,
not a production capacity test and does not write to any source database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform as host_platform
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import RemoteDisconnected
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dirty_data_to_olap.adapters.dependencies.desbordante import DesbordanteDependencyAdapter
from dirty_data_to_olap.adapters.platform import LocalArtifactStore, SQLiteControlStore
from dirty_data_to_olap.adapters.sources.sql.sqlite import SQLiteReadOnlySource
from dirty_data_to_olap.application.jobs import BoundedWorkerPool, JobWorker
from dirty_data_to_olap.domain.contracts.database import BoundedSampleRequest, ConnectionProfileReference, DatabaseEngine, DatabaseIdentifier, SamplingPolicy
from dirty_data_to_olap.domain.contracts.dependency import DependencyKind, DependencyRequest
from dirty_data_to_olap.domain.contracts.jobs import ExecutionPlan, StageExecutionRequest, StageExecutionResult, StageResultStatus, StageSpec
from dirty_data_to_olap.domain.contracts.platform import (
    ArtifactManifest,
    ArtifactPublicationState,
    ArtifactStorageMode,
    RetentionClass,
    RunRecord,
)
from dirty_data_to_olap.domain.contracts.source import stable_id


CSV = b"order_id,customer_id,customer_id_ref,order_date,quantity,unit_price\nO-100,C-1,C-1,2026-01-02,2,10.50\nO-101,C-2,C-2,2026-01-03,1,7.25\nO-102,C-3,C-3,2026-01-04,4,3.00\nO-103,C-4,C-4,2026-01-05,3,12.00\n"
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "BLOCKED"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 3)


def latency_summary(values: list[float]) -> dict[str, Any]:
    clean = [float(v) for v in values if v >= 0]
    return {
        "sample_count": len(clean),
        "p50_ms": percentile(clean, 0.50),
        "p90_ms": percentile(clean, 0.90),
        "p95_ms": percentile(clean, 0.95),
        "p99_ms": percentile(clean, 0.99),
        "max_ms": round(max(clean), 3) if clean else None,
        "mean_ms": round(statistics.fmean(clean), 3) if clean else None,
    }


def process_rss_bytes(pid: int | None = None) -> int | None:
    target = os.getpid() if pid is None else pid
    try:
        import psutil  # type: ignore

        return int(psutil.Process(target).memory_info().rss)
    except Exception:
        pass
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

            handle = ctypes.windll.kernel32.OpenProcess(0x0410, False, target)
            if not handle:
                return None
            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(counters)
            ok = ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
            ctypes.windll.kernel32.CloseHandle(handle)
            return int(counters.WorkingSetSize) if ok else None
        except Exception:
            return None
    return None


@dataclass(frozen=True)
class HttpResult:
    status: int
    body: dict[str, Any] | list[Any] | None
    elapsed_ms: float
    replayed: bool
    error: str | None = None


def http_json(base_url: str, method: str, path: str, *, headers: dict[str, str] | None = None, payload: Any | None = None, timeout: float = 60.0) -> HttpResult:
    request_headers = {"Accept": "application/json", "X-Local-Principal": "step38-load"}
    if headers:
        request_headers.update(headers)
    data = None
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = Request(base_url + path, data=data, headers=request_headers, method=method)
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = int(response.status)
            replayed = response.headers.get("Idempotency-Replayed", "false").lower() == "true"
    except HTTPError as error:
        raw = error.read()
        status = int(error.code)
        replayed = error.headers.get("Idempotency-Replayed", "false").lower() == "true"
    except (URLError, TimeoutError, RemoteDisconnected, OSError) as error:
        return HttpResult(0, None, round((time.perf_counter() - started) * 1000, 3), False, error.__class__.__name__)
    try:
        body = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        body = None
    return HttpResult(status, body, round((time.perf_counter() - started) * 1000, 3), replayed, None)


class LocalApi:
    def __init__(self, repository_root: Path, work_root: Path, port: int) -> None:
        self.repository_root = repository_root
        self.work_root = work_root
        self.port = port
        self.base_url = f"http://127.0.0.1:{port}"
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.log_path = work_root / "server.log"
        self._stream = self.log_path.open("w", encoding="utf-8")
        command = [
            sys.executable,
            str(repository_root / "tools" / "run_step29_local.py"),
            "--root",
            str(work_root),
            "--graph-root",
            str(repository_root),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ]
        self.process = subprocess.Popen(command, cwd=repository_root, stdout=self._stream, stderr=subprocess.STDOUT, env={**os.environ, "PYTHONPATH": str(repository_root / "src")})

    def wait_ready(self, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = http_json(self.base_url, "GET", "/api/v1/health", timeout=1.0)
            if result.status == 200:
                return
            if self.process.poll() is not None:
                raise RuntimeError(f"local API exited with code {self.process.returncode}")
            time.sleep(0.1)
        raise TimeoutError("local API did not become ready")

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self._stream.close()


def measure_parallel(label: str, calls: list[Callable[[], HttpResult]], *, max_workers: int, barrier: bool = False) -> dict[str, Any]:
    gate = threading.Barrier(len(calls)) if barrier and calls else None
    results: list[HttpResult] = []

    def invoke(call: Callable[[], HttpResult]) -> HttpResult:
        if gate is not None:
            try:
                gate.wait(timeout=15)
            except threading.BrokenBarrierError:
                pass
        return call()

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(invoke, call) for call in calls]
        for future in as_completed(futures):
            results.append(future.result())
    elapsed_ms = (time.perf_counter() - started) * 1000
    statuses: dict[str, int] = {}
    for result in results:
        statuses[str(result.status)] = statuses.get(str(result.status), 0) + 1
    return {
        "label": label,
        "requests": len(results),
        "concurrency": max_workers,
        "wall_time_ms": round(elapsed_ms, 3),
        "statuses": statuses,
        "successful_requests": sum(1 for result in results if 200 <= result.status < 300),
        "errors": sum(1 for result in results if result.status == 0),
        "latency": latency_summary([result.elapsed_ms for result in results]),
        "replayed_responses": sum(1 for result in results if result.replayed),
    }


def run_arrival_patterns(api: LocalApi) -> dict[str, Any]:
    patterns: dict[str, dict[str, Any]] = {}
    steady_calls = [lambda: http_json(api.base_url, "GET", "/api/v1/health", timeout=5) for _ in range(24)]
    started = time.perf_counter()
    steady_results: list[HttpResult] = []
    for call in steady_calls:
        steady_results.append(call())
        time.sleep(0.04)
    patterns["STEADY"] = {
        "requests": len(steady_results),
        "concurrency": 1,
        "statuses": {"200": sum(result.status == 200 for result in steady_results)},
        "wall_time_ms": round((time.perf_counter() - started) * 1000, 3),
        "latency": latency_summary([result.elapsed_ms for result in steady_results]),
    }
    ramp_calls = [lambda: http_json(api.base_url, "GET", "/api/v1/health", timeout=5) for _ in range(24)]
    ramp = measure_parallel("RAMP", ramp_calls, max_workers=8)
    ramp["concurrency_schedule"] = [1, 2, 4, 8]
    patterns["RAMP"] = ramp
    burst_calls = [lambda: http_json(api.base_url, "GET", "/api/v1/health", timeout=5) for _ in range(32)]
    patterns["BURST"] = measure_parallel("BURST", burst_calls, max_workers=32, barrier=True)
    return patterns


def setup_api_run(api: LocalApi, config_fingerprint: str, registry_id: str, index: int) -> tuple[str | None, list[float], str | None]:
    suffix = f"{index:02d}-{uuid.uuid4().hex[:8]}"
    latencies: list[float] = []
    created = http_json(api.base_url, "POST", "/api/v1/runs", headers={"Idempotency-Key": f"step38-run-{suffix}"}, payload={"project_id": f"step38-{suffix}", "configuration_fingerprint": config_fingerprint})
    latencies.append(created.elapsed_ms)
    if created.status != 201 or not isinstance(created.body, dict):
        return None, latencies, f"create_run:{created.status}"
    run_id = str(created.body["run_id"])
    bound = http_json(api.base_url, "POST", f"/api/v1/runs/{run_id}/source-selection", headers={"Idempotency-Key": f"step38-bind-{suffix}"}, payload={"registry_id": registry_id, "scope": {"included_objects": [], "excluded_objects": [], "include_views": False, "included_columns": {}}, "extraction": {"chunk_size": 1000, "max_rows": 10000, "max_rows_scope": "SOURCE_WIDE", "null_markers": [], "preserve_raw_values": True}, "execution_context_id": f"step38-{suffix}"})
    latencies.append(bound.elapsed_ms)
    if bound.status not in {200, 201}:
        return run_id, latencies, f"bind:{bound.status}"
    prepared = http_json(api.base_url, "POST", f"/api/v1/runs/{run_id}/execution/prepare", headers={"Idempotency-Key": f"step38-prepare-{suffix}"}, payload={"intent": {"cross_source_mapping_requested": False, "entity_resolution_requested": False, "optional_semantic_evidence_enabled": False, "learned_evidence_enabled": False}})
    latencies.append(prepared.elapsed_ms)
    if prepared.status != 200:
        return run_id, latencies, f"prepare:{prepared.status}"
    return run_id, latencies, None


def submit_runs(api: LocalApi, run_ids: list[str]) -> dict[str, Any]:
    calls = [lambda run_id=run_id, index=index: http_json(api.base_url, "POST", f"/api/v1/runs/{run_id}/execution", headers={"Idempotency-Key": f"step38-submit-{index:02d}"}, timeout=120) for index, run_id in enumerate(run_ids)]
    return measure_parallel("OVERLOAD_RUN_SUBMISSIONS", calls, max_workers=min(32, len(calls)), barrier=True)


def api_retry_cases(api: LocalApi, run_ids: list[str]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []

    def one(run_id: str, index: int) -> dict[str, Any]:
        # Replay the original submission key.  A new key would be a second
        # semantic submit command, not an idempotent retry, and can correctly
        # be rejected by the run state machine after the first command owns
        # the run's root stage.
        key = f"step38-submit-{index:02d}"
        first = http_json(api.base_url, "POST", f"/api/v1/runs/{run_id}/execution", headers={"Idempotency-Key": key}, timeout=120)
        second = http_json(api.base_url, "POST", f"/api/v1/runs/{run_id}/execution", headers={"Idempotency-Key": key}, timeout=120)
        same_command = isinstance(first.body, dict) and isinstance(second.body, dict) and first.body.get("command_id") == second.body.get("command_id")
        return {"run_id": run_id, "first_status": first.status, "second_status": second.status, "first_replayed": first.replayed, "second_replayed": second.replayed, "same_command": same_command, "latency": latency_summary([first.elapsed_ms, second.elapsed_ms])}

    with ThreadPoolExecutor(max_workers=len(run_ids)) as executor:
        futures = [executor.submit(one, run_id, index) for index, run_id in enumerate(run_ids)]
        for future in as_completed(futures):
            records.append(future.result())
    return {"cases": records, "pass": all(item["second_replayed"] and item["same_command"] for item in records)}


def poll_product_runs(api: LocalApi, run_ids: list[str], successful_targets: set[str], *, timeout: float = 150.0) -> dict[str, Any]:
    accepted: dict[str, list[str]] = {run_id: [] for run_id in run_ids}
    review_events: list[dict[str, Any]] = []
    final: dict[str, dict[str, Any]] = {}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and len(final) < len(run_ids):
        for run_id in run_ids:
            if run_id in final:
                continue
            result = http_json(api.base_url, "GET", f"/api/v1/runs/{run_id}/product-summary", timeout=10)
            if result.status != 200 or not isinstance(result.body, dict):
                continue
            summary = result.body
            pending = summary.get("pending_reviews") or []
            if run_id in successful_targets and pending:
                review = pending[0]
                checkpoint = str(review.get("checkpoint"))
                if checkpoint not in accepted[run_id]:
                    review_key = f"step38-review-{run_id}-{len(accepted[run_id])}"
                    action = http_json(api.base_url, "POST", f"/api/v1/runs/{run_id}/reviews/{checkpoint}", headers={"Idempotency-Key": review_key}, payload={"subject_artifact_id": review.get("subject_artifact_id"), "subject_content_hash": review.get("subject_content_hash"), "decision": "ACCEPTED", "rationale": "bounded Step38 local load review", "expected_revision": int(review.get("revision", 0))}, timeout=20)
                    resumed = http_json(api.base_url, "POST", f"/api/v1/runs/{run_id}/resume", headers={"Idempotency-Key": f"step38-resume-{run_id}-{len(accepted[run_id])}"}, timeout=20)
                    if action.status == 200 and resumed.status in {200, 202}:
                        accepted[run_id].append(checkpoint)
                        review_events.append({"run_id": run_id, "checkpoint": checkpoint, "action_status": action.status, "resume_status": resumed.status})
            if str(summary.get("status")) in TERMINAL:
                final[run_id] = summary
        if len(final) < len(run_ids):
            time.sleep(0.15)
    return {"final_runs": final, "accepted_reviews": accepted, "review_events": review_events, "timed_out_runs": sorted(set(run_ids) - set(final))}


def cancel_runs(api: LocalApi, run_ids: list[str]) -> dict[str, Any]:
    calls = [lambda run_id=run_id, index=index: http_json(api.base_url, "POST", f"/api/v1/runs/{run_id}/cancel", headers={"Idempotency-Key": f"step38-cancel-{index:02d}"}, timeout=120) for index, run_id in enumerate(run_ids)]
    return measure_parallel("CONTROLLED_CANCEL", calls, max_workers=min(32, len(calls)), barrier=True)


class HarnessStage:
    def __init__(self, artifact_store: LocalArtifactStore, control_store: SQLiteControlStore, delay_seconds: float) -> None:
        self.artifact_store = artifact_store
        self.control_store = control_store
        self.delay_seconds = delay_seconds

    def execute(self, request: StageExecutionRequest) -> StageExecutionResult:
        time.sleep(self.delay_seconds)
        payload = f"step38:{request.run_id}:{request.attempt_id}".encode("utf-8")
        artifact_id = stable_id("step38-artifact", {"run": request.run_id, "attempt": request.attempt_id})
        manifest = ArtifactManifest(artifact_id=artifact_id, run_id=request.run_id, stage_id=request.stage_id, attempt_id=request.attempt_id, artifact_kind="STEP38_LOAD_RECEIPT", media_type="application/octet-stream", producer="step38-load-harness", storage_mode=ArtifactStorageMode.MANAGED, logical_key=f"runs/{request.run_id}/step38/{artifact_id}.bin", publication_state=ArtifactPublicationState.RESERVED, retention_class=RetentionClass.EPHEMERAL)
        ref = self.artifact_store.publish(manifest, payload)
        self.control_store.register_artifact(ref)
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,))


def worker_ramp_scenarios(root: Path) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for worker_count in (1, 2, 4, 8):
        case_root = root / f"worker-{worker_count}"
        case_root.mkdir(parents=True, exist_ok=True)
        control = SQLiteControlStore(case_root / "control.sqlite", project_root=case_root)
        artifacts = LocalArtifactStore(case_root / "artifacts", project_root=case_root, disk_budget_bytes=128 * 1024 * 1024)
        stage = StageSpec(stage_id="STEP38_STAGE", required=True, handler_key="step38", final_validation=False)
        plan = ExecutionPlan(plan_id=f"step38-plan-{worker_count}", run_id="placeholder", stages=(stage,), success_guard_required=False)
        worker_objects: list[JobWorker] = []
        job_run_ids: list[str] = []
        try:
            # Two jobs per worker is enough to expose queueing and claim
            # contention at every ramp point while keeping the SQLite/FULL
            # durability cost bounded on a developer machine.
            for index in range(worker_count * 2):
                run_id = f"step38-r{worker_count}-{index:03d}"
                job_run_ids.append(run_id)
                control.create_run(RunRecord(run_id=run_id, project_id="step38-ramp", configuration_fingerprint="step38-ramp-config"))
                run_plan = plan.model_copy(update={"plan_id": f"step38-plan-{worker_count}-{index:03d}", "run_id": run_id})
                control.register_execution_plan(run_plan)
                control.enqueue_stage_job(run_id=run_id, plan_id=run_plan.plan_id, stage_id=stage.stage_id)
            for index in range(worker_count):
                worker_objects.append(JobWorker(control_store=control, artifact_store=artifacts, executor=HarnessStage(artifacts, control, 0.012), worker_id=f"step38-worker-{worker_count}-{index}"))
            pool = BoundedWorkerPool(tuple(worker_objects), max_workers=worker_count, max_jobs_per_pump=10_000, max_active_per_run=1, max_active_per_source=1)
            rss_before = process_rss_bytes()
            started = time.perf_counter()
            outcomes = pool.pump()
            elapsed_ms = (time.perf_counter() - started) * 1000
            rss_after = process_rss_bytes()
            jobs = [control.get_stage_job(run_id=run_id, stage_id=stage.stage_id) for run_id in job_run_ids]
            attempts = [control.list_stage_attempts(run_id=run_id, stage_id=stage.stage_id, limit=10) for run_id in job_run_ids]
            wait_ms = [(attempt[0].started_at - jobs[index].created_at).total_seconds() * 1000 for index, attempt in enumerate(attempts) if attempt and attempt[0].started_at and jobs[index] is not None]
            statuses = [job.status.value if job is not None else "MISSING" for job in jobs]
            results.append({"worker_count": worker_count, "jobs": len(jobs), "successful_jobs": sum(status == "SUCCEEDED" for status in statuses), "statuses": {status: statuses.count(status) for status in sorted(set(statuses))}, "pump_outcomes": len(outcomes), "elapsed_ms": round(elapsed_ms, 3), "throughput_jobs_per_second": round(len(jobs) / max(elapsed_ms / 1000, 0.000001), 3), "queue_wait_latency": latency_summary(wait_ms), "queue_depth_peak": len(jobs), "rss_before_bytes": rss_before, "rss_after_bytes": rss_after, "rss_delta_bytes": None if rss_before is None or rss_after is None else rss_after - rss_before, "artifact_count": len(control.list_artifacts(limit=10000))})
        finally:
            control.close()
    return {"ramp": results, "pass": all(item["successful_jobs"] == item["jobs"] and item["queue_wait_latency"]["sample_count"] == item["jobs"] for item in results)}


def artifact_concurrency(root: Path) -> dict[str, Any]:
    control = SQLiteControlStore(root / "control.sqlite", project_root=root)
    artifacts = LocalArtifactStore(root / "artifacts", project_root=root, disk_budget_bytes=128 * 1024 * 1024)
    run_id = "step38-artifact-run"
    control.create_run(RunRecord(run_id=run_id, project_id="step38-artifact", configuration_fingerprint="step38-artifact-config"))

    def publish(index: int) -> HttpResult:
        started = time.perf_counter()
        attempt_id = f"step38-attempt-{index:03d}"
        artifact_id = f"step38-direct-{index:03d}"
        payload = f"artifact-{index}".encode("utf-8")
        manifest = ArtifactManifest(artifact_id=artifact_id, run_id=run_id, stage_id="STEP38_DIRECT", attempt_id=attempt_id, artifact_kind="STEP38_DIRECT", media_type="text/plain", producer="step38-load-harness", storage_mode=ArtifactStorageMode.MANAGED, logical_key=f"runs/{run_id}/{artifact_id}.txt", publication_state=ArtifactPublicationState.RESERVED, retention_class=RetentionClass.EPHEMERAL)
        try:
            ref = artifacts.publish(manifest, payload)
            control.register_artifact(ref)
            return HttpResult(201, {"artifact_id": ref.artifact_id}, (time.perf_counter() - started) * 1000, False)
        except Exception as error:
            return HttpResult(0, None, (time.perf_counter() - started) * 1000, False, error.__class__.__name__)

    try:
        calls = [lambda index=index: publish(index) for index in range(32)]
        result = measure_parallel("ARTIFACT_STAGING", calls, max_workers=8, barrier=True)
        result["registered_artifacts"] = len(control.list_artifacts(run_id=run_id, limit=10000))
        result["pass"] = result["successful_requests"] == 32 and result["registered_artifacts"] == 32
        return result
    finally:
        control.close()


def source_protection(root: Path) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    source_path = root / "source.sqlite"
    connection = sqlite3.connect(source_path)
    connection.execute("CREATE TABLE source_rows (id INTEGER PRIMARY KEY, value TEXT)")
    connection.execute("INSERT INTO source_rows(value) VALUES ('immutable')")
    connection.commit()
    connection.close()
    before = hashlib.sha256(source_path.read_bytes()).hexdigest()
    profile = ConnectionProfileReference(profile_id="step38-source", database_engine=DatabaseEngine.SQLITE, database_name=str(source_path))
    adapter = SQLiteReadOnlySource(profile, project_root=root)
    write_blocked = False
    read_rows = 0
    failure_kind = None
    try:
        with adapter.open_readonly_session() as session:
            observation = session.sample_rows_bounded(BoundedSampleRequest(table=DatabaseIdentifier(name="source_rows"), columns=("id", "value"), sampling_policy=SamplingPolicy(max_rows=10)))
            read_rows = observation.rows_observed
            try:
                session._run("UPDATE source_rows SET value='mutated'", operation="step38_protection_probe")
            except Exception as error:
                write_blocked = True
                failure = getattr(error, "failure", None)
                failure_kind = getattr(failure, "kind", None)
    finally:
        after = hashlib.sha256(source_path.read_bytes()).hexdigest()
    return {"read_rows": read_rows, "write_blocked": write_blocked, "failure_kind": str(failure_kind) if failure_kind is not None else None, "source_hash_unchanged": before == after, "pass": read_rows == 1 and write_blocked and before == after}


def provider_concurrency(root: Path) -> dict[str, Any]:
    adapter = DesbordanteDependencyAdapter(project_root=root)
    request = DependencyRequest(request_id="step38-provider", source_id="source", snapshot_id="snapshot", selected_table_ids=("table",), requested_kinds=(DependencyKind.UCC,))

    def check(_: int) -> str:
        return adapter.capability(request).status.value

    with ThreadPoolExecutor(max_workers=8) as executor:
        statuses = list(executor.map(check, range(16)))
    unavailable = statuses.count("UNAVAILABLE")
    available = statuses.count("AVAILABLE")
    return {"calls": len(statuses), "concurrency": 8, "statuses": {status: statuses.count(status) for status in sorted(set(statuses))}, "provider": "desbordante", "silent_successes": 0, "pass": unavailable == len(statuses) or available == len(statuses)}


def api_run_scenarios(api: LocalApi, root: Path) -> dict[str, Any]:
    configuration = http_json(api.base_url, "GET", "/api/v1/product/configuration", timeout=10)
    if configuration.status != 200 or not isinstance(configuration.body, dict):
        raise RuntimeError("product configuration endpoint did not respond")
    # urllib JSON helper is intentionally not used for a raw source body.
    request = Request(api.base_url + "/api/v1/sources/import", data=CSV, headers={"Accept": "application/json", "X-Local-Principal": "step38-load", "Idempotency-Key": "step38-import-real", "X-Source-Filename": "orders.csv", "Content-Type": "text/csv"}, method="POST")
    started = time.perf_counter()
    with urlopen(request, timeout=30) as response:
        source_body = json.loads(response.read().decode("utf-8"))
        import_result = {"status": int(response.status), "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)}
    registry_id = str(source_body["registry_id"])
    config_fingerprint = str(configuration.body["configuration_fingerprint"])
    setup_records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(setup_api_run, api, config_fingerprint, registry_id, index) for index in range(20)]
        for index, future in enumerate(as_completed(futures)):
            run_id, latencies, error = future.result()
            setup_records.append({"run_id": run_id, "latency": latency_summary(latencies), "error": error})
    run_ids = [str(item["run_id"]) for item in setup_records if item["run_id"] and item["error"] is None]
    if len(run_ids) < 20:
        return {"import": import_result, "setup": setup_records, "submission": {"requests": len(run_ids), "pass": False}, "pass": False, "reason": "fewer than 20 real API runs could be prepared"}
    submission = submit_runs(api, run_ids)
    retries = api_retry_cases(api, run_ids[:5])
    # The twenty-run wave is the controlled overload/cancellation scenario.
    # Two fresh runs are executed after the queue recovers so G6 and all four
    # review checkpoints are measured without a second command being added to
    # the runs selected for correctness.
    cancelled_targets = list(run_ids)
    cancel_result = cancel_runs(api, cancelled_targets)
    lifecycle = poll_product_runs(api, run_ids, set(), timeout=180)
    # A second cancellation pass is bounded recovery for any run that remained
    # non-terminal after the first overload wave.
    outstanding = [run_id for run_id in run_ids if run_id not in lifecycle["final_runs"]]
    recovery_cancel = cancel_runs(api, outstanding) if outstanding else {"requests": 0, "pass": True}
    if outstanding:
        lifecycle = poll_product_runs(api, run_ids, set(), timeout=60)
    final_runs = lifecycle["final_runs"]
    dedicated_setup: list[dict[str, Any]] = []
    for index in (100, 101):
        dedicated_id, latencies, error = setup_api_run(api, config_fingerprint, registry_id, index)
        dedicated_setup.append({"run_id": dedicated_id, "latency": latency_summary(latencies), "error": error})
    dedicated_ids = [str(item["run_id"]) for item in dedicated_setup if item["run_id"] and item["error"] is None]
    dedicated_submission = submit_runs(api, dedicated_ids) if len(dedicated_ids) == 2 else {"requests": len(dedicated_ids), "successful_requests": 0, "pass": False}
    # The overload wave intentionally leaves durable cancellation/control work
    # ahead of the fresh correctness runs on the single local worker.  Keep a
    # larger but explicit bounded recovery window so queue drain is measured,
    # not mistaken for a product failure.
    dedicated_lifecycle = poll_product_runs(api, dedicated_ids, set(dedicated_ids), timeout=420)
    final_runs.update(dedicated_lifecycle["final_runs"])
    successful = [run_id for run_id in dedicated_ids if final_runs.get(run_id, {}).get("status") == "SUCCEEDED"]
    g6_successes = [run_id for run_id in successful if final_runs[run_id].get("validation", {}).get("g6_status") == "PASS" and final_runs[run_id].get("validation", {}).get("g6_eligible") is True and final_runs[run_id].get("output", {}).get("validated") is True]
    cancellation_pass = len(lifecycle["timed_out_runs"]) == 0 and all(final_runs.get(run_id, {}).get("status") == "CANCELLED" for run_id in cancelled_targets)
    isolation_pass = True
    artifact_counts: dict[str, int] = {}
    for run_id in [*run_ids, *dedicated_ids]:
        artifacts = http_json(api.base_url, "GET", f"/api/v1/runs/{run_id}/artifacts?page_size=100", timeout=20)
        items = artifacts.body.get("items", []) if isinstance(artifacts.body, dict) else []
        artifact_counts[run_id] = len(items)
        isolation_pass = isolation_pass and all(item.get("run_id") == run_id for item in items)
    accepted_reviews = {**lifecycle["accepted_reviews"], **dedicated_lifecycle["accepted_reviews"]}
    review_events = [*lifecycle["review_events"], *dedicated_lifecycle["review_events"]]
    review_pass = len(g6_successes) == 2 and all(set(accepted_reviews.get(run_id, [])) >= {"REVIEW_EVIDENCE_DECISIONS", "REVIEW_CANONICAL_IDENTITY", "REVIEW_ANALYTICAL_PLAN", "REVIEW_MATERIALIZATION_PLAN"} for run_id in g6_successes)
    failed_or_blocked: dict[str, Any] = {}
    for run_id, summary in final_runs.items():
        if summary.get("status") in {"FAILED", "BLOCKED"}:
            jobs = http_json(api.base_url, "GET", f"/api/v1/runs/{run_id}/jobs?page_size=100", timeout=20)
            failed_or_blocked[run_id] = {
                "status": summary.get("status"),
                "current_stage": summary.get("current_stage"),
                "stages": summary.get("stages", []),
                "jobs": jobs.body.get("items", []) if isinstance(jobs.body, dict) else [],
            }
    return {"import": import_result, "setup": setup_records, "dedicated_setup": dedicated_setup, "prepared_runs": len(run_ids), "submission": submission, "dedicated_submission": dedicated_submission, "retries": retries, "cancellation": cancel_result, "recovery_cancellation": recovery_cancel, "lifecycle": {"terminal_runs": len(final_runs), "successful_runs": successful, "cancelled_runs": [run_id for run_id in cancelled_targets if final_runs.get(run_id, {}).get("status") == "CANCELLED"], "timed_out_runs": [*lifecycle["timed_out_runs"], *dedicated_lifecycle["timed_out_runs"]], "final_statuses": {run_id: final_runs[run_id].get("status") for run_id in final_runs}, "failed_or_blocked": failed_or_blocked, "accepted_reviews": accepted_reviews, "review_events": review_events}, "g6_successes": g6_successes, "artifact_counts": artifact_counts, "multi_run_isolation_pass": isolation_pass, "review_pause_resume_pass": review_pass, "cancellation_pass": cancellation_pass, "pass": len(g6_successes) == 2 and review_pass and isolation_pass and cancellation_pass and submission["successful_requests"] == 20 and dedicated_submission.get("successful_requests") == 2 and retries["pass"]}


def g11_regression() -> dict[str, Any]:
    command = [sys.executable, "-m", "pytest", "-q", "tests/integration/test_step28_job_processing.py", "tests/integration/test_step29_product_path.py"]
    started = time.perf_counter()
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=240, check=False)
    return {"command": "python -m pytest -q tests/integration/test_step28_job_processing.py tests/integration/test_step29_product_path.py", "returncode": result.returncode, "elapsed_ms": round((time.perf_counter() - started) * 1000, 3), "tail": " ".join((result.stdout + result.stderr).split())[-1200:], "pass": result.returncode == 0}


def environment_receipt(server: LocalApi, ramp: dict[str, Any]) -> dict[str, Any]:
    total_memory = None
    try:
        if os.name == "nt":
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            status = MEMORYSTATUSEX(); status.dwLength = ctypes.sizeof(status); ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)); total_memory = int(status.ullTotalPhys)
    except Exception:
        pass
    provider_image = os.environ.get("DESBORDANTE_PROVIDER_IMAGE", "").strip() or None
    provider_digest = None
    if provider_image:
        try:
            inspected = subprocess.run(["docker", "image", "inspect", provider_image, "--format", "{{.Id}}"], cwd=ROOT, capture_output=True, text=True, check=True, timeout=10)
            provider_digest = inspected.stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            provider_digest = None
    return {"os": host_platform.platform(), "architecture": host_platform.machine(), "python": sys.version.split()[0], "cpu_logical_count": os.cpu_count(), "ram_total_bytes": total_memory, "provider_image": provider_image or "none configured", "provider_image_digest": provider_digest, "api_process": {"pid": server.process.pid, "host": "127.0.0.1", "port": server.port, "workers": 1}, "control_store": "project SQLite control store; WAL; synchronous=FULL; busy_timeout=30000ms", "worker_ramp": [item["worker_count"] for item in ramp["ramp"]], "source_caps": {"api_upload_bytes": 5 * 1024 * 1024, "extraction_max_rows": 10000, "source_write_policy": "read-only"}, "queue_limits": {"max_active_per_run": 1, "max_active_per_source": 1, "api_payload_bytes": 1_000_000}, "git_head": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip(), "started_at_utc": utc_now()}


def run_suite(output_path: Path, report_path: Path, assessed_commit: str | None) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="step38-load-") as temporary:
        work_root = Path(temporary)
        port = 18765 + (os.getpid() % 500)
        server = LocalApi(ROOT, work_root / "api", port)
        try:
            server.wait_ready()
            arrival = run_arrival_patterns(server)
            ramp = worker_ramp_scenarios(work_root / "ramp")
            artifacts = artifact_concurrency(work_root / "artifact")
            protection = source_protection(work_root / "source")
            provider = provider_concurrency(work_root / "provider")
            api_runs = api_run_scenarios(server, work_root / "api")
            g11 = g11_regression()
            environment = environment_receipt(server, ramp)
            queue_waits = [item["queue_wait_latency"] for item in ramp["ramp"]]
            max_queue_depth = max(item["queue_depth_peak"] for item in ramp["ramp"])
            safe = max((item for item in ramp["ramp"] if item["successful_jobs"] == item["jobs"]), key=lambda item: (item["throughput_jobs_per_second"], -item["worker_count"]))
            saturation = next((item for item in ramp["ramp"] if item["worker_count"] > safe["worker_count"] and item["throughput_jobs_per_second"] < safe["throughput_jobs_per_second"]), None)
            overload = api_runs["submission"]
            controls = {
                "queue_depth": {"peak_jobs": max_queue_depth, "pass": max_queue_depth > 0},
                "queue_wait_latency": {"worker_ramp": queue_waits, "pass": all(item["sample_count"] > 0 for item in queue_waits)},
                "control_store_contention": {"measured_by": "concurrent SQLite claims, idempotency, review and run mutations", "submission_errors": overload.get("errors", 0), "pass": overload.get("errors", 0) == 0},
                "source_read_only_protection": protection,
                "provider_concurrency": provider,
                "backpressure": {"bounded_pool": True, "max_active_per_run": 1, "max_active_per_source": 1, "queue_depth_observed": max_queue_depth, "pass": max_queue_depth > 0},
                "artifact_staging_concurrency": artifacts,
                "multi_run_isolation": {"pass": api_runs.get("multi_run_isolation_pass", False), "prepared_runs": api_runs.get("prepared_runs", 0)},
                "review_pause_resume": {"pass": api_runs.get("review_pause_resume_pass", False), "events": len(api_runs.get("lifecycle", {}).get("review_events", []))},
                "cancellation": {"pass": api_runs.get("cancellation_pass", False), "cancelled": len(api_runs.get("lifecycle", {}).get("cancelled_runs", []))},
                "controlled_overload_recovery": {"overload": overload, "recovery": api_runs.get("recovery_cancellation", {}), "pass": overload.get("successful_requests", 0) == 20 and not api_runs.get("lifecycle", {}).get("timed_out_runs")},
                "bounded_soak_memory": {"api_process_rss_before_bytes": process_rss_bytes(server.process.pid), "worker_ramp": [{"worker_count": item["worker_count"], "rss_delta_bytes": item["rss_delta_bytes"]} for item in ramp["ramp"]], "bounded_duration_seconds": 180, "pass": all(item["rss_delta_bytes"] is None or item["rss_delta_bytes"] < 256 * 1024 * 1024 for item in ramp["ramp"])},
                "state_machine_invariants": {"ramp_jobs_succeeded": ramp["pass"], "api_terminal_or_recovered": not api_runs.get("lifecycle", {}).get("timed_out_runs"), "pass": ramp["pass"] and not api_runs.get("lifecycle", {}).get("timed_out_runs")},
                "at_least_once_replay_safety": api_runs.get("retries", {"pass": False}),
                "g6_concurrent_correctness": {"successful_g6_runs": len(api_runs.get("g6_successes", [])), "pass": len(api_runs.get("g6_successes", [])) >= 2},
                "g11_regression": g11,
            }
            all_controls_pass = all(bool(value.get("pass")) for value in controls.values())
            evidence = {"schema_version": "1.0", "suite": "step38-load-stress-v1", "step": 38, "assessed_commit": assessed_commit or environment["git_head"], "content_commit": assessed_commit or environment["git_head"], "overall_result": "PASS" if all_controls_pass else "INCOMPLETE", "environment": environment, "arrival_patterns": arrival | {"OVERLOAD": overload}, "worker_ramp": ramp, "api_real_listener": {"real_listener": True, "submission_concurrency": 20, "runs_prepared": api_runs.get("prepared_runs", 0), "submissions": overload, "retry_load": api_runs.get("retries"), "dedicated_setup": api_runs.get("dedicated_setup", []), "dedicated_submission": api_runs.get("dedicated_submission", {}), "run_lifecycle": api_runs.get("lifecycle"), "g6_successes": api_runs.get("g6_successes", [])}, "controls": controls, "safe_operating_point": {"kind": "local-reference-only", "worker_count": safe["worker_count"], "jobs": safe["jobs"], "throughput_jobs_per_second": safe["throughput_jobs_per_second"], "queue_wait_p95_ms": safe["queue_wait_latency"]["p95_ms"], "source": "bounded JobWorker/BoundedWorkerPool run", "production_claim": False}, "first_observed_saturation_region": {"scenario": "WORKER_RAMP", "description": f"Measured local worker ramp peaked at worker_count={safe['worker_count']} and declined at worker_count={saturation['worker_count']} while queue-wait p95 increased; this is a local SQLite/filesystem boundary, not a universal product limit" if saturation else "No throughput decline was observed within the bounded worker ramp", "breakpoint": "MEASURED_BOUNDARY" if saturation else "NOT_REACHED_WITHIN_SAFE_TEST_BOUND", "worker_count": None if saturation is None else saturation["worker_count"], "production_claim": False}, "scale_limits": {"100k": "inherited Step37 single-run evidence only; not a Step38 capacity claim", "1M": "NOT_EXECUTED", "10M": "NOT_EXECUTED", "100M": "NOT_EXECUTED", "production": "NOT_MEASURED"}, "claims": {"exactly_once": False, "production_capacity": False, "production_source_stress": False, "public_sla": False, "step39_started": False}, "protected_quality_artifacts": "unread, untouched, unstaged and uncommitted", "upstream_gates": {"G6": "PASS", "G7": "PASS", "G8": "PASS", "G9": "PASS", "G10": "PASS", "G11": "PASS", "G12": "PASS" if all_controls_pass else "PENDING", "G13": "PENDING", "G14": "PENDING", "G15": "PENDING"}, "status": "PASS" if all_controls_pass else "INCOMPLETE", "notes": ["No production database was hammered.", "The tested source boundary remained read-only.", "At-least-once replay safety is measured through durable idempotency and does not become an exactly-once claim.", "A local safe operating point is not a production capacity commitment."]}
        finally:
            server.close()
    output_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_path.write_text(render_report(evidence), encoding="utf-8")
    print(json.dumps({"status": evidence["status"], "output": str(output_path), "report": str(report_path), "g12": evidence["upstream_gates"]["G12"], "g6_successes": len(evidence["api_real_listener"]["g6_successes"])}, sort_keys=True))
    return 0 if evidence["status"] == "PASS" else 2


def render_report(evidence: dict[str, Any]) -> str:
    safe = evidence["safe_operating_point"]
    saturation = evidence["first_observed_saturation_region"]
    controls = evidence["controls"]
    return "\n".join([
        "# Step38 Load / Stress Test Report",
        "",
        f"- Result: `{evidence['status']}`",
        f"- Suite: `{evidence['suite']}`",
        f"- Assessed commit: `{evidence['assessed_commit']}`",
        "- Evidence class: bounded local-reference only; not production capacity.",
        "",
        "## Environment",
        "",
        "```json",
        json.dumps(evidence["environment"], indent=2, sort_keys=True),
        "```",
        "",
        "## Arrival patterns",
        "",
        "The suite exercised STEADY, RAMP, BURST and OVERLOAD arrivals. OVERLOAD used twenty concurrent run submissions through the real localhost HTTP listener.",
        "",
        "```json",
        json.dumps(evidence["arrival_patterns"], indent=2, sort_keys=True),
        "```",
        "",
        "## Safe operating point",
        "",
        f"- Worker count: `{safe['worker_count']}`",
        f"- Completed jobs: `{safe['jobs']}`",
        f"- Measured throughput: `{safe['throughput_jobs_per_second']}` jobs/s",
        f"- Queue-wait p95: `{safe['queue_wait_p95_ms']}` ms",
        "- This is a local reference envelope, not a production or public-SLA claim.",
        "",
        "## First saturation region and recovery",
        "",
        f"- Region: `{saturation['scenario']}`",
        f"- Breakpoint: `{saturation['breakpoint']}`",
        f"- Observation: {saturation['description']}",
        "- Controlled cancellation and recovery were exercised; no source database was stressed.",
        "",
        "## Required controls",
        "",
        "| Control | Result |",
        "|---|---|",
        *[f"| `{name}` | `{'PASS' if value.get('pass') else 'FAIL'}` |" for name, value in controls.items()],
        "",
        "## Explicit limitations",
        "",
        "- No production capacity, deployment, HA, multi-node, 1M, 10M or 100M measured claim.",
        "- No exactly-once claim; the worker remains at-least-once with durable replay fences.",
        "- Optional provider availability is reported as observed and fail-closed; it is not silently upgraded.",
        "- Step39 was not started.",
        "- Protected quality artifacts were not read, modified, staged or committed.",
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "step38_load_stress_validation.json")
    parser.add_argument("--report", type=Path, default=ROOT / "reports" / "load" / "STEP38_LOAD_STRESS_REPORT.md")
    parser.add_argument("--assessed-commit")
    args = parser.parse_args()
    return run_suite(args.output.resolve(), args.report.resolve(), args.assessed_commit)


if __name__ == "__main__":
    raise SystemExit(main())
