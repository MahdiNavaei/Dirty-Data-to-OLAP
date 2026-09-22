from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil

import pytest

from dirty_data_to_olap.adapters.materialization import DuckDBMaterializer
from dirty_data_to_olap.adapters.platform import LocalArtifactStore, SQLiteControlStore
from dirty_data_to_olap.adapters.sources.files import FileSourceAdapter
from dirty_data_to_olap.application.backend import BackendError, BackendService, Principal
from dirty_data_to_olap.application.jobs import (
    DurableExecutionSubmission,
    InjectedWorkerCrash,
    JobWorker,
    StageHandlerRegistry,
)
from dirty_data_to_olap.application.reliability import (
    RecoveryDisposition,
    build_recovery_projection,
    classify_recovery,
)
from dirty_data_to_olap.application.snapshot import SourceSnapshotService
from dirty_data_to_olap.application.discovery import SourceDiscoveryService
from dirty_data_to_olap.application.source_registry import InMemorySourceRegistry
from dirty_data_to_olap.domain.contracts.api import ExecutionAction, ExecutionCommand
from dirty_data_to_olap.domain.contracts.analytical import (
    AnalyticalInputDataset,
    CompiledOperation,
    CompiledPlan,
    GeneratedSQL,
    MaterializationStatus,
    TargetConfig,
)
from dirty_data_to_olap.domain.contracts.database import DatabaseEngine
from dirty_data_to_olap.domain.contracts.jobs import (
    ExecutionPlan,
    FailureClassification,
    JobStatus,
    StageExecutionResult,
    StageResultStatus,
    StageSpec,
)
from dirty_data_to_olap.domain.contracts.platform import (
    ArtifactIntegrityState,
    ArtifactManifest,
    ArtifactStorageMode,
    RetentionClass,
    RunRecord,
    StageStatus,
)
from dirty_data_to_olap.domain.contracts.source import (
    ExtractionPolicy,
    SelectionScope,
    SourceFailure,
    SourceFailureKind,
    SourceIngestionError,
    SourceRegistryRecord,
    SourceSelection,
    SourceType,
    stable_id,
)
from tests.product_acceptance.prompt02_control_evidence import record_control_observation
from dirty_data_to_olap.observability import TelemetryClient
from dirty_data_to_olap.application.platform import ArtifactConflictError, ConcurrencyConflictError


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime.now(timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class ResultHandler:
    def __init__(self, result: StageExecutionResult | None = None) -> None:
        self.result = result or StageExecutionResult(status=StageResultStatus.SUCCEEDED)
        self.calls = 0

    def execute(self, _request):
        self.calls += 1
        return self.result


class FaultingControlStore(SQLiteControlStore):
    def __init__(self, path: Path, *, project_root: Path, faults: dict[str, int] | None = None) -> None:
        super().__init__(path, project_root=project_root)
        self.faults = dict(faults or {})

    def _take(self, name: str) -> None:
        if self.faults.get(name, 0) > 0:
            self.faults[name] -= 1
            raise OSError(f"CHAOS-CONTROL-{name}")

    def claim_next_job(self, **kwargs):
        self._take("claim")
        return super().claim_next_job(**kwargs)

    def mark_handler_delivery_started(self, **kwargs):
        self._take("delivery_marker")
        return super().mark_handler_delivery_started(**kwargs)

    def record_stage_result(self, **kwargs):
        self._take("result_record")
        return super().record_stage_result(**kwargs)


class FailingTelemetrySink:
    def emit_event(self, _value):
        raise OSError("telemetry log sink unavailable")

    def observe_metric(self, _value):
        raise OSError("telemetry metric sink unavailable")

    def record_span(self, _value):
        raise OSError("telemetry trace sink unavailable")


class FailingFileAdapter(FileSourceAdapter):
    def __init__(self, *, project_root: Path, failure_kind: SourceFailureKind) -> None:
        super().__init__(SourceType.CSV, project_root=project_root)
        self.failure_kind = failure_kind

    def create_bounded_snapshot(self, *args, **kwargs):
        raise SourceIngestionError(SourceFailure(
            kind=self.failure_kind,
            operation="extract_csv",
            detail=f"deterministic Step36 {self.failure_kind.value.lower()} fixture",
            retryable=self.failure_kind is SourceFailureKind.TIMEOUT,
            records_observed_before_failure=1,
        ))


def _command(run_id: str, key: str = "submit", *, action: ExecutionAction = ExecutionAction.SUBMIT) -> ExecutionCommand:
    return ExecutionCommand(
        command_id=stable_id("step36-command", {"run": run_id, "key": key}),
        run_id=run_id,
        action=action,
        idempotency_scope=f"step36:{run_id}",
        idempotency_key=key,
        request_fingerprint="a" * 64,
        principal_subject="step36-test",
        principal_source="TEST",
    )


def _runtime(tmp_path: Path, *, handler=None, control=None, clock=None, telemetry=None, lease_seconds: int = 5):
    root = tmp_path.resolve()
    control = control or SQLiteControlStore(root / "control.sqlite", project_root=root)
    artifacts = LocalArtifactStore(root / "artifacts", project_root=root)
    run = control.create_run(RunRecord(run_id="run-step36", project_id="project", configuration_fingerprint="cfg"))
    plan = control.register_execution_plan(ExecutionPlan(
        plan_id="plan-step36",
        run_id=run.run_id,
        stages=(StageSpec(stage_id="WORK", handler_key="work"),),
    ))
    DurableExecutionSubmission(control).submit_command(command=_command(run.run_id), run=run)
    clock = clock or MutableClock()
    handler = handler or ResultHandler()
    worker = JobWorker(
        control_store=control,
        artifact_store=artifacts,
        executor=StageHandlerRegistry({"work": handler}),
        worker_id="worker-a",
        lease_seconds=lease_seconds,
        clock=clock,
        telemetry=telemetry,
    )
    # The submission command is a separate durable job.  Consume that control
    # plane command here so each scenario starts at the real stage boundary.
    saved_faults = dict(getattr(control, "faults", {}))
    if hasattr(control, "faults"):
        control.faults.clear()
    bootstrap = JobWorker(
        control_store=control,
        artifact_store=artifacts,
        executor=StageHandlerRegistry({"work": handler}),
        worker_id="step36-bootstrap",
        lease_seconds=lease_seconds,
        clock=clock,
        telemetry=telemetry,
    )
    assert bootstrap.run_once().status == JobStatus.SUCCEEDED.value
    if hasattr(control, "faults"):
        control.faults.update(saved_faults)
    return control, artifacts, run, plan, clock, handler, worker


def _restart(tmp_path: Path, clock: MutableClock, handler=None, *, worker_id: str = "worker-b"):
    control = SQLiteControlStore(tmp_path / "control.sqlite", project_root=tmp_path)
    artifacts = LocalArtifactStore(tmp_path / "artifacts", project_root=tmp_path)
    handler = handler or ResultHandler()
    worker = JobWorker(
        control_store=control,
        artifact_store=artifacts,
        executor=StageHandlerRegistry({"work": handler}),
        worker_id=worker_id,
        lease_seconds=5,
        clock=clock,
    )
    return control, artifacts, worker, handler


def _no_silent_success(control: SQLiteControlStore, artifacts: LocalArtifactStore, run_id: str) -> None:
    jobs = control.list_jobs(run_id=run_id, limit=10_000)
    for job in jobs:
        if job.status is JobStatus.SUCCEEDED:
            assert job.delivery_phase.value == "FINALIZED"
            assert job.failure_classification is None
            for artifact_id in job.result_refs:
                artifact = control.get_artifact(artifact_id)
                assert artifact is not None
                assert artifacts.verify(artifact).state is ArtifactIntegrityState.VERIFIED
    assert all(job.status.value != "PARTIAL" for job in jobs)


def _publish(control, artifacts, run_id: str, *, artifact_id: str, payload: bytes, stage_id: str = "WORK", attempt_id: str = "attempt"):
    ref = artifacts.publish(ArtifactManifest(
        artifact_id=artifact_id,
        run_id=run_id,
        stage_id=stage_id,
        attempt_id=attempt_id,
        artifact_kind="CHAOS_OUTPUT",
        media_type="application/json",
        producer="step36-chaos",
        storage_mode=ArtifactStorageMode.MANAGED,
        retention_class=RetentionClass.RUN_SCOPED,
    ), payload)
    control.register_artifact(ref)
    return ref


def test_chaos_worker_001_kill_before_claim_is_safe_to_resume(tmp_path: Path) -> None:
    control = FaultingControlStore(tmp_path / "control.sqlite", project_root=tmp_path, faults={"claim": 1})
    control, artifacts, run, _plan, clock, handler, worker = _runtime(tmp_path, control=control)
    try:
        with pytest.raises(OSError, match="CHAOS-CONTROL-claim"):
            worker.run_once()
        stage_job = control.get_stage_job(run_id=run.run_id, stage_id="WORK")
        assert stage_job is not None and stage_job.status is JobStatus.QUEUED
        control.faults.clear()
        assert worker.run_once().status == JobStatus.SUCCEEDED.value
        assert worker.run_once().status == "IDLE"
        assert handler.calls == 1
        _no_silent_success(control, artifacts, run.run_id)
    finally:
        control.close()


def test_chaos_worker_002_kill_after_attempt_reconstructs_without_duplicate_attempt(tmp_path: Path) -> None:
    control, artifacts, run, _plan, clock, _handler, worker = _runtime(tmp_path)
    crashed = False

    def inject(point, _job, _attempt):
        nonlocal crashed
        if point == "after_stage_attempt" and not crashed:
            crashed = True
            raise InjectedWorkerCrash("after durable attempt creation")

    try:
        crashing = JobWorker(control_store=control, artifact_store=artifacts, executor=StageHandlerRegistry({"work": ResultHandler()}), worker_id="worker-a", lease_seconds=5, clock=clock, fault_injector=inject)
        with pytest.raises(InjectedWorkerCrash):
            crashing.run_once()
        assert len(control.list_stage_attempts(run_id=run.run_id)) == 1
        clock.advance(6)
        control.close()
        control, artifacts, recovered, handler = _restart(tmp_path, clock)
        assert recovered.run_once().status == JobStatus.SUCCEEDED.value
        assert len(control.list_stage_attempts(run_id=run.run_id)) == 1
        assert handler.calls == 1
        _no_silent_success(control, artifacts, run.run_id)
    finally:
        control.close()


def test_chaos_worker_003_kill_after_delivery_enters_reconciliation(tmp_path: Path) -> None:
    control, artifacts, run, _plan, clock, handler, worker = _runtime(tmp_path)

    def inject(point, _job, _attempt):
        if point == "after_handler_delivery_marker":
            raise InjectedWorkerCrash("after HANDLER_DELIVERY_STARTED")

    try:
        crashing = JobWorker(control_store=control, artifact_store=artifacts, executor=StageHandlerRegistry({"work": handler}), worker_id="worker-a", lease_seconds=5, clock=clock, fault_injector=inject)
        with pytest.raises(InjectedWorkerCrash):
            crashing.run_once()
        clock.advance(6)
        control.close()
        control, artifacts, recovered, handler = _restart(tmp_path, clock, handler=handler)
        assert recovered.run_once().status == JobStatus.FAILED.value
        job = control.get_stage_job(run_id=run.run_id, stage_id="WORK")
        assert job is not None and job.failure_classification is FailureClassification.UNKNOWN_SIDE_EFFECT
        assert job.failure_code == "RECONCILIATION_REQUIRED"
        assert handler.calls == 0
        assert any(event["event_type"] == "stage_job_finalized" and event["status"] == "FAILED" for event in control.list_audit_events(run_id=run.run_id))
    finally:
        control.close()


def test_chaos_worker_004_result_recorded_restarts_without_reexecution(tmp_path: Path) -> None:
    control, artifacts, run, _plan, clock, handler, worker = _runtime(tmp_path)
    crashed = False

    def inject(point, _job, _attempt):
        nonlocal crashed
        if point == "after_result_record_before_finalization" and not crashed:
            crashed = True
            raise InjectedWorkerCrash("after RESULT_RECORDED")

    try:
        crashing = JobWorker(control_store=control, artifact_store=artifacts, executor=StageHandlerRegistry({"work": handler}), worker_id="worker-a", lease_seconds=5, clock=clock, fault_injector=inject)
        with pytest.raises(InjectedWorkerCrash):
            crashing.run_once()
        assert handler.calls == 1
        clock.advance(6)
        control.close()
        control, artifacts, recovered, _same_handler = _restart(tmp_path, clock, handler=handler)
        assert recovered.run_once().status == JobStatus.SUCCEEDED.value
        assert handler.calls == 1
        assert control.get_stage_job(run_id=run.run_id, stage_id="WORK").delivery_phase.value == "FINALIZED"
        _no_silent_success(control, artifacts, run.run_id)
    finally:
        control.close()


def test_chaos_worker_005_stale_worker_publication_is_fenced(tmp_path: Path) -> None:
    control, artifacts, run, _plan, clock, _handler, worker = _runtime(tmp_path)
    try:
        first = control.claim_next_job(worker_id="worker-a", now=clock(), lease_seconds=5)
        assert first is not None
        attempt = control.ensure_stage_attempt(job_id=first.job_id, worker_id="worker-a", lease_generation=first.lease_generation, now=clock())
        clock.advance(6)
        second = control.claim_next_job(worker_id="worker-b", now=clock(), lease_seconds=5)
        assert second is not None and second.lease_generation == first.lease_generation + 1
        with pytest.raises(ConcurrencyConflictError):
            control.finalize_stage_job(job_id=first.job_id, worker_id="worker-a", lease_generation=first.lease_generation, attempt=attempt, result=StageExecutionResult(status=StageResultStatus.SUCCEEDED), status=JobStatus.SUCCEEDED.value, now=clock())
        assert control.get_job(first.job_id).status is JobStatus.RUNNING
    finally:
        control.close()


def _source_fixture(tmp_path: Path) -> tuple[Path, SourceRegistryRecord, SourceSelection]:
    path = tmp_path / "source.csv"
    path.write_text("id,value\n1,ok\n2,ok\n", encoding="utf-8")
    record = SourceRegistryRecord(registry_id="source-1", display_name="Step36 source", source_type=SourceType.CSV, file_locator=str(path), adapter_name="file_source", adapter_version="1.0.0")
    selection = SourceSelection(registry_id=record.registry_id, scope=SelectionScope(), extraction=ExtractionPolicy(chunk_size=1), execution_context_id="step36-source")
    return path, record, selection


def test_chaos_source_001_disappears_after_discovery(tmp_path: Path) -> None:
    path, record, selection = _source_fixture(tmp_path)
    registry = InMemorySourceRegistry()
    registry.register(record)
    adapter = FileSourceAdapter(SourceType.CSV, project_root=tmp_path)
    catalog = SourceDiscoveryService(registry, {"file_source": adapter}).discover(selection)
    before = path.read_bytes()
    path.unlink()
    with pytest.raises(SourceIngestionError) as raised:
        SourceSnapshotService(registry, {"file_source": adapter}).extract(catalog, selection, staging_root=tmp_path / "staging")
    assert raised.value.failure.kind is SourceFailureKind.ACCESS_FAILED
    assert not list((tmp_path / "staging").rglob("*.parquet"))
    assert not path.exists() and before.startswith(b"id,")
    record_control_observation("NC01", f"SourceIngestionError:{raised.value.failure.kind.value}", "tests/chaos/test_step36_chaos.py:365")


@pytest.mark.parametrize(
    ("scenario_id", "failure_kind"),
    (("CHAOS-SOURCE-002", SourceFailureKind.TIMEOUT), ("CHAOS-SOURCE-003", SourceFailureKind.ACCESS_FAILED)),
)
def test_chaos_source_002_003_fail_explicitly_and_preserve_source(tmp_path: Path, scenario_id: str, failure_kind: SourceFailureKind) -> None:
    path, record, selection = _source_fixture(tmp_path)
    registry = InMemorySourceRegistry()
    registry.register(record)
    adapter = FailingFileAdapter(project_root=tmp_path, failure_kind=failure_kind)
    catalog = SourceDiscoveryService(registry, {"file_source": adapter}).discover(selection)
    before = path.read_bytes()
    with pytest.raises(SourceIngestionError) as raised:
        SourceSnapshotService(registry, {"file_source": adapter}).extract(catalog, selection, staging_root=tmp_path / scenario_id / "staging")
    assert raised.value.failure.kind is failure_kind
    assert path.read_bytes() == before
    assert not list((tmp_path / scenario_id).rglob("*.parquet"))


@pytest.mark.parametrize("failure_code", ("PROVIDER_UNAVAILABLE", "PROVIDER_TIMEOUT"))
def test_chaos_engine_001_002_provider_failure_is_explicit(tmp_path: Path, failure_code: str) -> None:
    classification = FailureClassification.TERMINAL_FAILURE if failure_code == "PROVIDER_UNAVAILABLE" else FailureClassification.RETRYABLE_TRANSIENT
    result = StageExecutionResult(status=StageResultStatus.FAILED, failure_code=failure_code, failure_classification=classification, failure_reason="deterministic provider boundary fault")
    control, artifacts, run, _plan, clock, _handler, worker = _runtime(tmp_path, handler=ResultHandler(result))
    try:
        outcome = worker.run_once()
        assert outcome.status in {JobStatus.FAILED.value, JobStatus.RETRY_WAIT.value}
        job = control.get_stage_job(run_id=run.run_id, stage_id="WORK")
        assert job is not None and job.failure_code == failure_code
        assert job.status is not JobStatus.SUCCEEDED
        _no_silent_success(control, artifacts, run.run_id)
    finally:
        control.close()


def test_chaos_engine_003_retry_exhaustion_is_terminal(tmp_path: Path) -> None:
    result = StageExecutionResult(status=StageResultStatus.FAILED, failure_code="PROVIDER_TIMEOUT", failure_classification=FailureClassification.RETRYABLE_TRANSIENT, failure_reason="bounded timeout")
    control, _artifacts, run, _plan, clock, handler, worker = _runtime(tmp_path, handler=ResultHandler(result))
    try:
        assert worker.run_once().status == JobStatus.RETRY_WAIT.value
        clock.advance(2)
        assert worker.run_once().status == JobStatus.RETRY_WAIT.value
        clock.advance(4)
        assert worker.run_once().status == JobStatus.FAILED.value
        job = control.get_stage_job(run_id=run.run_id, stage_id="WORK")
        assert job is not None and job.retry_count == 2 and job.status is JobStatus.FAILED
        assert handler.calls == 3
    finally:
        control.close()


def test_chaos_control_001_read_unavailable_does_not_claim(tmp_path: Path) -> None:
    control = FaultingControlStore(tmp_path / "control.sqlite", project_root=tmp_path, faults={"claim": 1})
    control, artifacts, run, _plan, _clock, _handler, worker = _runtime(tmp_path, control=control)
    try:
        with pytest.raises(OSError):
            worker.run_once()
        job = control.get_stage_job(run_id=run.run_id, stage_id="WORK")
        assert job is not None
        assert job.status is JobStatus.QUEUED
        assert job.delivery_phase.value == "ATTEMPT_CREATED"
        _no_silent_success(control, artifacts, run.run_id)
    finally:
        control.close()


def test_chaos_control_002_write_unavailable_before_delivery_is_explicit(tmp_path: Path) -> None:
    control = FaultingControlStore(tmp_path / "control.sqlite", project_root=tmp_path, faults={"delivery_marker": 1})
    control, artifacts, run, _plan, clock, handler, worker = _runtime(tmp_path, control=control)
    try:
        assert worker.run_once().status == JobStatus.FAILED.value
        job = control.get_stage_job(run_id=run.run_id, stage_id="WORK")
        assert job is not None and job.status is JobStatus.FAILED and job.failure_code == "STAGE_OUTCOME_UNKNOWN"
        assert job.delivery_phase.value == "FINALIZED"
        _no_silent_success(control, artifacts, run.run_id)
    finally:
        control.close()


def test_chaos_control_003_transition_write_unknown_requires_reconciliation(tmp_path: Path) -> None:
    control = FaultingControlStore(tmp_path / "control.sqlite", project_root=tmp_path, faults={"result_record": 1})
    control, artifacts, run, _plan, clock, handler, worker = _runtime(tmp_path, control=control)
    try:
        with pytest.raises(OSError):
            worker.run_once()
        job = control.get_stage_job(run_id=run.run_id, stage_id="WORK")
        assert job is not None and job.delivery_phase.value == "HANDLER_DELIVERY_STARTED"
        clock.advance(6)
        projection = build_recovery_projection(control_store=control, artifact_store=artifacts, run_id=run.run_id, now=clock())
        stage_projection = next(item for item in projection["jobs"] if item["job"].get("stage_id") == "WORK")
        assert stage_projection["recovery_disposition"] == RecoveryDisposition.REQUIRES_RECONCILIATION.value
    finally:
        control.close()


def test_chaos_control_004_reopen_preserves_recovery_classification(tmp_path: Path) -> None:
    control, artifacts, run, _plan, clock, handler, worker = _runtime(tmp_path)
    try:
        def inject(point, _job, _attempt):
            if point == "after_handler_delivery_marker":
                raise InjectedWorkerCrash("control-store reopen boundary")
        crashing = JobWorker(control_store=control, artifact_store=artifacts, executor=StageHandlerRegistry({"work": handler}), worker_id="worker-a", lease_seconds=5, clock=clock, fault_injector=inject)
        with pytest.raises(InjectedWorkerCrash):
            crashing.run_once()
        clock.advance(6)
        control.close()
        reopened = SQLiteControlStore(tmp_path / "control.sqlite", project_root=tmp_path)
        try:
            projection = build_recovery_projection(control_store=reopened, artifact_store=artifacts, run_id=run.run_id, now=clock())
            stage_projection = next(item for item in projection["jobs"] if item["job"].get("stage_id") == "WORK")
            assert stage_projection["recovery_disposition"] == RecoveryDisposition.REQUIRES_RECONCILIATION.value
            assert reopened.list_audit_events(run_id=run.run_id)
        finally:
            reopened.close()
    finally:
        if control.path.exists():
            control.close()


def test_chaos_queue_001_lease_expiry_is_reclaimed(tmp_path: Path) -> None:
    control, _artifacts, run, _plan, clock, _handler, worker = _runtime(tmp_path)
    try:
        first = control.claim_next_job(worker_id="worker-a", now=clock(), lease_seconds=2)
        assert first is not None
        clock.advance(3)
        second = control.claim_next_job(worker_id="worker-b", now=clock(), lease_seconds=2)
        assert second is not None and second.lease_generation == 2
        assert classify_recovery(first.model_copy(update={"lease_expires_at": clock()}), now=clock()) is RecoveryDisposition.SAFE_TO_RESUME
    finally:
        control.close()


def test_chaos_queue_002_stale_lease_is_rejected(tmp_path: Path) -> None:
    control, _artifacts, run, _plan, clock, _handler, worker = _runtime(tmp_path)
    try:
        first = control.claim_next_job(worker_id="worker-a", now=clock(), lease_seconds=2)
        assert first is not None
        attempt = control.ensure_stage_attempt(job_id=first.job_id, worker_id="worker-a", lease_generation=first.lease_generation, now=clock())
        clock.advance(3)
        assert control.claim_next_job(worker_id="worker-b", now=clock(), lease_seconds=2) is not None
        with pytest.raises(ConcurrencyConflictError):
            control.heartbeat_job(job_id=first.job_id, worker_id="worker-a", lease_generation=first.lease_generation, now=clock(), lease_seconds=2)
        assert attempt.status is StageStatus.RUNNING
    finally:
        control.close()


def test_chaos_queue_003_bounded_backlog_recovers_without_duplicates(tmp_path: Path) -> None:
    telemetry = TelemetryClient()
    control = SQLiteControlStore(tmp_path / "control.sqlite", project_root=tmp_path)
    artifacts = LocalArtifactStore(tmp_path / "artifacts", project_root=tmp_path)
    clock = MutableClock()
    runs = []
    try:
        for index in range(3):
            run = control.create_run(RunRecord(run_id=f"run-step36-{index}", project_id="project", configuration_fingerprint="cfg"))
            plan = control.register_execution_plan(ExecutionPlan(plan_id=f"plan-step36-{index}", run_id=run.run_id, stages=(StageSpec(stage_id="WORK", handler_key="work"),)))
            control.enqueue_stage_job(run_id=run.run_id, plan_id=plan.plan_id, stage_id="WORK")
            runs.append(run)
        worker = JobWorker(control_store=control, artifact_store=artifacts, executor=StageHandlerRegistry({"work": ResultHandler()}), worker_id="backlog-worker", clock=clock, telemetry=telemetry)
        queue_jobs = tuple(job for run in runs for job in control.list_jobs(run_id=run.run_id))
        telemetry.queue_snapshot(queue_jobs)
        clock.advance(5)
        for _ in range(3):
            assert worker.run_once().status == JobStatus.SUCCEEDED.value
        assert len([sample for sample in telemetry.memory.metrics if sample.name == "ddo_queue_jobs"]) > 0
        for run in runs:
            assert control.get_stage_job(run_id=run.run_id, stage_id="WORK").delivery_count == 1
    finally:
        control.close()


def test_chaos_disk_001_failed_artifact_stream_never_publishes_partial(tmp_path: Path) -> None:
    control, artifacts, run, _plan, clock, _handler, worker = _runtime(tmp_path)

    class FailingStream:
        def __init__(self):
            self.calls = 0
        def read(self, _size=-1):
            self.calls += 1
            if self.calls == 1:
                return b"partial"
            raise OSError("controlled disk write failure")

    class ArtifactFailureHandler:
        def execute(self, request):
            try:
                _publish(control, artifacts, request.run_id, artifact_id="disk-failure", payload=FailingStream(), attempt_id=request.attempt_id)
            except Exception:
                return StageExecutionResult(status=StageResultStatus.FAILED, failure_code="DISK_WRITE_FAILED", failure_classification=FailureClassification.TERMINAL_FAILURE, failure_reason="artifact publication failed")
            raise AssertionError("fault stream unexpectedly published")

    worker.executor = StageHandlerRegistry({"work": ArtifactFailureHandler()})
    try:
        assert worker.run_once().status == JobStatus.FAILED.value
        assert control.list_artifacts(run_id=run.run_id) == ()
        assert not list(artifacts.root.rglob("*.partial"))
    finally:
        control.close()


def _invalid_materialization_inputs():
    operations = tuple(CompiledOperation(operation_id=f"op-{index}", operation_name=name, statement_kind="DDL", sql_hash="a" * 64) for index, name in enumerate(("create_schema", "load_date", "load_dimensions", "load_facts"), 1))
    compiled = CompiledPlan.model_construct(
        compiled_plan_id="cplan_" + "a" * 32,
        plan_id="plan-step36-materialization",
        plan_content_hash="b" * 64,
        compiler_version="step20-test",
        dialect="duckdb",
        operations=operations,
        generated_sql_id="sql-step36",
        generated_sql_hash="c" * 64,
        target_config_fingerprint="target-step36",
        input_binding_id="binding-step36",
        input_binding_content_hash="d" * 64,
        canonical_model_id="canonical-step36",
        canonical_model_content_hash="e" * 64,
        table_names=("expected_table",),
        provenance_refs=("test:step36",),
        created_at=datetime.now(timezone.utc),
    )
    generated = GeneratedSQL(
        generated_sql_id="sql-step36",
        plan_id=compiled.plan_id,
        plan_content_hash=compiled.plan_content_hash,
        compiler_version="step20-test",
        create_schema_sql="CREATE TABLE actual_table(value INTEGER);",
        load_date_sql="SELECT 1;",
        load_dimensions_sql="SELECT 1;",
        load_facts_sql="SELECT 1;",
        provenance_refs=("test:step36",),
    )
    input_data = AnalyticalInputDataset.model_construct(allow_literal_sql=True)
    return compiled, generated, input_data


def test_chaos_disk_002_materialization_failure_is_nonusable_and_unpublished(tmp_path: Path) -> None:
    compiled, generated, input_data = _invalid_materialization_inputs()
    controlled = tmp_path / "run"
    controlled.mkdir()
    (controlled / "blocked").write_text("not a directory", encoding="utf-8")
    artifact = DuckDBMaterializer(controlled, repository_root=tmp_path).materialize(compiled, generated, input_data, TargetConfig(relative_path="blocked/target.duckdb"), run_id="run-step36")
    assert artifact.status is MaterializationStatus.FAILED
    assert artifact.usable is False
    assert not (controlled / "blocked" / "target.duckdb").exists()
    assert not list(controlled.rglob("*.tmp"))


def test_chaos_art_001_missing_artifact_requires_reconciliation(tmp_path: Path) -> None:
    control, artifacts, run, _plan, _clock, _handler, _worker = _runtime(tmp_path)
    try:
        ref = _publish(control, artifacts, run.run_id, artifact_id="artifact-missing", payload=b"stable")
        artifacts._blob_path(ref.content_hash).unlink()
        assert artifacts.verify(ref).state is ArtifactIntegrityState.MISSING
        projection = build_recovery_projection(control_store=control, artifact_store=artifacts, run_id=run.run_id)
        assert projection["summary"]["unverified_artifact_count"] == 1
    finally:
        control.close()


@pytest.mark.parametrize("mutation", ("hash", "truncate"))
def test_chaos_art_002_003_corruption_is_rejected_and_identity_cannot_be_reused(tmp_path: Path, mutation: str) -> None:
    control, artifacts, run, _plan, _clock, _handler, _worker = _runtime(tmp_path)
    try:
        payload = b"immutable analytical bytes"
        ref = _publish(control, artifacts, run.run_id, artifact_id=f"artifact-{mutation}", payload=payload)
        blob = artifacts._blob_path(ref.content_hash)
        blob.write_bytes(b"corrupt" if mutation == "hash" else payload[:3])
        assert artifacts.verify(ref).state in {ArtifactIntegrityState.HASH_MISMATCH, ArtifactIntegrityState.SIZE_MISMATCH}
        with pytest.raises(ArtifactConflictError):
            artifacts.publish(ArtifactManifest(artifact_id=ref.artifact_id, run_id=run.run_id, stage_id="WORK", attempt_id="attempt", artifact_kind="CHAOS_OUTPUT", media_type="application/json", producer="step36-chaos"), b"different bytes")
    finally:
        control.close()


class ReviewHandler:
    def __init__(self, control, artifacts):
        self.control = control
        self.artifacts = artifacts
        self.calls = 0
        self.context = None

    def execute(self, request):
        self.calls += 1
        ref = _publish(self.control, self.artifacts, request.run_id, artifact_id=f"review-{self.calls}", payload=json.dumps({"semantic": "reviewed", "version": self.calls}).encode(), attempt_id=request.attempt_id)
        if self.calls == 1:
            from dirty_data_to_olap.domain.contracts.canonical import ReviewCheckpoint, ReviewCompatibilityContext
            self.context = ReviewCompatibilityContext(review_checkpoint_id=ReviewCheckpoint.REVIEW_EVIDENCE_DECISIONS, subject_stage=request.stage_id, subject_artifact_id=ref.artifact_id, subject_content_hash=ref.content_hash, subject_schema_version="1", model_version="step36-review", source_schema_fingerprints={"source": "step36"}, policy_version="step36-review-v1", subject_semantic_id="step36-review-subject", applicability_fingerprint="step36-review-fingerprint")
            return StageExecutionResult(status=StageResultStatus.NEEDS_REVIEW, output_artifact_refs=(ref.artifact_id,), review_context=self.context)
        return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,))


def test_chaos_review_001_review_survives_restart_and_resume(tmp_path: Path) -> None:
    control = SQLiteControlStore(tmp_path / "control.sqlite", project_root=tmp_path)
    artifacts = LocalArtifactStore(tmp_path / "artifacts", project_root=tmp_path)
    control, artifacts, run, _plan, clock, _unused, _worker = _runtime(tmp_path, control=control)
    handler = ReviewHandler(control, artifacts)
    worker = JobWorker(control_store=control, artifact_store=artifacts, executor=StageHandlerRegistry({"work": handler}), worker_id="worker-a", clock=clock)
    try:
        assert worker.run_once().status == JobStatus.NEEDS_REVIEW.value
        context = handler.context
        assert context is not None
        control.close()
        control = SQLiteControlStore(tmp_path / "control.sqlite", project_root=tmp_path)
        artifacts = LocalArtifactStore(tmp_path / "artifacts", project_root=tmp_path)
        backend = BackendService(control_store=control, artifact_store=artifacts)
        principal = Principal(subject="reviewer", scopes=frozenset({"reviews:write"}), project_ids=frozenset({"project"}))
        backend.review(run_id=run.run_id, checkpoint=context.review_checkpoint_id, subject_artifact_id=context.subject_artifact_id, subject_content_hash=context.subject_content_hash, decision="ACCEPTED", rationale="bounded restart review", expected_revision=0, principal=principal, idempotency_key="review-001")
        DurableExecutionSubmission(control).submit_command(command=_command(run.run_id, key="resume", action=ExecutionAction.RESUME), run=run)
        clock.value = datetime.now(timezone.utc) + timedelta(seconds=5)
        resumed = JobWorker(control_store=control, artifact_store=artifacts, executor=StageHandlerRegistry({"work": handler}), worker_id="worker-b", clock=clock)
        assert resumed.run_once().status == JobStatus.SUCCEEDED.value
        assert resumed.run_once().status == JobStatus.SUCCEEDED.value
        assert control.get_stage_job(run_id=run.run_id, stage_id="WORK").status is JobStatus.SUCCEEDED
    finally:
        control.close()


def test_chaos_review_002_stale_review_after_artifact_mutation_is_rejected(tmp_path: Path) -> None:
    control, artifacts, run, _plan, clock, _unused, _worker = _runtime(tmp_path)
    handler = ReviewHandler(control, artifacts)
    worker = JobWorker(control_store=control, artifact_store=artifacts, executor=StageHandlerRegistry({"work": handler}), worker_id="worker-a", clock=clock)
    try:
        assert worker.run_once().status == JobStatus.NEEDS_REVIEW.value
        context = handler.context
        assert context is not None
        blob = artifacts._blob_path(context.subject_content_hash)
        blob.write_bytes(b"changed review bytes")
        with pytest.raises(BackendError, match="could not be verified"):
            BackendService(control_store=control, artifact_store=artifacts).review(run_id=run.run_id, checkpoint=context.review_checkpoint_id, subject_artifact_id=context.subject_artifact_id, subject_content_hash=context.subject_content_hash, decision="ACCEPTED", rationale="must reject stale bytes", expected_revision=0, principal=Principal(subject="reviewer", scopes=frozenset({"reviews:write"}), project_ids=frozenset({"project"})), idempotency_key="review-002")
        assert control.get_current_review(run_id=run.run_id, subject_key="missing") is None
    finally:
        control.close()


def test_chaos_cancel_001_cancellation_during_retry_preserves_failure_audit(tmp_path: Path) -> None:
    result = StageExecutionResult(status=StageResultStatus.FAILED, failure_code="PROVIDER_TIMEOUT", failure_classification=FailureClassification.RETRYABLE_TRANSIENT, failure_reason="retry was pending")
    control, _artifacts, run, _plan, clock, _handler, worker = _runtime(tmp_path, handler=ResultHandler(result))
    try:
        assert worker.run_once().status == JobStatus.RETRY_WAIT.value
        control.request_run_cancellation(run_id=run.run_id, now=clock())
        job = control.get_stage_job(run_id=run.run_id, stage_id="WORK")
        assert job is not None and job.status is JobStatus.CANCELLED
        assert any(event["event_type"] == "run_cancellation_requested" for event in control.list_audit_events(run_id=run.run_id))
    finally:
        control.close()


def test_chaos_telem_001_exporter_failure_does_not_change_durable_semantics(tmp_path: Path) -> None:
    telemetry = TelemetryClient(sinks=(FailingTelemetrySink(),))
    control, artifacts, run, _plan, _clock, _handler, worker = _runtime(tmp_path, telemetry=telemetry)
    try:
        assert worker.run_once().status == JobStatus.SUCCEEDED.value
        assert worker.run_once().status == "IDLE"
        assert telemetry.exporter_failures > 0
        assert telemetry.memory.events
        assert control.get_stage_job(run_id=run.run_id, stage_id="WORK").status is JobStatus.SUCCEEDED
        _no_silent_success(control, artifacts, run.run_id)
    finally:
        control.close()


def _semantic_handler(control, artifacts, payload: dict[str, object]):
    class Handler:
        def execute(self, request):
            ref = _publish(control, artifacts, request.run_id, artifact_id=f"semantic-{request.attempt_id}", payload=json.dumps(payload, sort_keys=True).encode(), attempt_id=request.attempt_id)
            return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,))
    return Handler()


def test_chaos_recovery_001_recovered_semantics_match_clean_control(tmp_path: Path) -> None:
    clean_root = tmp_path / "clean"
    fault_root = tmp_path / "fault"
    clean_root.mkdir()
    fault_root.mkdir()
    clean_control, clean_artifacts, clean_run, _plan, clean_clock, _handler, clean_worker = _runtime(clean_root)
    fault_control, fault_artifacts, fault_run, _plan, fault_clock, _handler, fault_worker = _runtime(fault_root)
    payload = {"canonical_identity_basis": "accepted", "grain": "one row per order line", "measure": "quantity", "row_count": 2, "g6_eligible": True}
    clean_handler = _semantic_handler(clean_control, clean_artifacts, payload)
    fault_handler = _semantic_handler(fault_control, fault_artifacts, payload)
    try:
        clean_worker.executor = StageHandlerRegistry({"work": clean_handler})
        fault_worker.executor = StageHandlerRegistry({"work": fault_handler})
        clean_worker.run_once()
        injected = False
        def inject(point, _job, _attempt):
            nonlocal injected
            if point == "after_result_record_before_finalization" and not injected:
                injected = True
                raise InjectedWorkerCrash("semantic recovery restart")
        crash_worker = JobWorker(control_store=fault_control, artifact_store=fault_artifacts, executor=StageHandlerRegistry({"work": fault_handler}), worker_id="worker-a", lease_seconds=5, clock=fault_clock, fault_injector=inject)
        with pytest.raises(InjectedWorkerCrash):
            crash_worker.run_once()
        fault_clock.advance(6)
        fault_control.close()
        fault_control = SQLiteControlStore(fault_root / "control.sqlite", project_root=fault_root)
        fault_artifacts = LocalArtifactStore(fault_root / "artifacts", project_root=fault_root)
        recovered = JobWorker(control_store=fault_control, artifact_store=fault_artifacts, executor=StageHandlerRegistry({"work": fault_handler}), worker_id="worker-b", clock=fault_clock)
        recovered.run_once()
        clean_ref = clean_control.get_stage_job(run_id=clean_run.run_id, stage_id="WORK").result_refs[0]
        fault_ref = fault_control.get_stage_job(run_id=fault_run.run_id, stage_id="WORK").result_refs[0]
        assert json.loads(clean_artifacts.read(clean_ref)) == json.loads(fault_artifacts.read(fault_ref)) == payload
        assert fault_control.get_stage_job(run_id=fault_run.run_id, stage_id="WORK").delivery_phase.value == "FINALIZED"
    finally:
        clean_control.close()
        fault_control.close()


def test_chaos_g11_001_corruption_oracle_rejects_unvalidated_output(tmp_path: Path) -> None:
    control, artifacts, run, _plan, _clock, _handler, worker = _runtime(tmp_path)
    try:
        class OracleHandler:
            def execute(self, request):
                ref = _publish(control, artifacts, request.run_id, artifact_id="oracle-artifact", payload=b"oracle", attempt_id=request.attempt_id)
                return StageExecutionResult(status=StageResultStatus.SUCCEEDED, output_artifact_refs=(ref.artifact_id,))

        worker.executor = StageHandlerRegistry({"work": OracleHandler()})
        assert worker.run_once().status == JobStatus.SUCCEEDED.value
        ref = control.get_artifact("oracle-artifact")
        assert ref is not None
        assert artifacts.verify(ref).state is ArtifactIntegrityState.VERIFIED
        artifacts._blob_path(ref.content_hash).write_bytes(b"corrupt")
        projection = build_recovery_projection(control_store=control, artifact_store=artifacts, run_id=run.run_id)
        job = next(item for item in projection["jobs"] if item["job"].get("stage_id") == "WORK")
        assert job["recovery_disposition"] == RecoveryDisposition.REQUIRES_RECONCILIATION.value
        assert job["result_artifact_integrity"][ref.artifact_id] != "VERIFIED"
    finally:
        control.close()
