"""Behavioral validator for the Step23 local data-platform foundation.

The validator uses the real SQLite and filesystem adapters.  It references the
accepted Step22 ValidationReport as an external, read-only gate receipt and
never loads benchmark truth or reruns Step22 transformation logic.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dirty_data_to_olap.adapters.platform import LocalArtifactStore, LocalStagingStore, SQLiteControlStore
from dirty_data_to_olap.application.platform import (
    ArtifactConflictError,
    ArtifactIntegrityError,
    PathConfinementError,
    PlatformCacheService,
    PlatformError,
    PlatformIntegrityService,
    PlatformLifecycleService,
    StaleDependencyError,
)
from dirty_data_to_olap.domain.contracts.platform import (
    ArtifactDependency,
    ArtifactManifest,
    ArtifactStorageMode,
    CacheEntry,
    CacheInputRef,
    CacheKey,
    CapabilityQuery,
    CleanupAuthorization,
    GateEvidence,
    GateEvidenceStatus,
    LocalPlatformConfig,
    RetentionClass,
    RetentionPolicy,
    ResourceBudget,
    RunRecord,
    RunStatus,
    StageAttemptRecord,
    StageStatus,
    StagedDatasetManifest,
    StagedDatasetPart,
)
from dirty_data_to_olap.domain.contracts.source import stable_id
from dirty_data_to_olap.platform import LocalPlatform


REFERENCE_RUN_ID = "step23-platform-reference-run"
REFERENCE_ATTEMPT_ID = "step23-platform-attempt-1"
STEP22_COMMIT = "f5ddeb6dbd2a1759b8d92a4dacf47400d6b580c2"


def _json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _manifest(run_id: str, artifact_id: str, *, kind: str, created_at: datetime | None = None, retention: RetentionClass = RetentionClass.RUN_SCOPED, storage_mode: ArtifactStorageMode = ArtifactStorageMode.MANAGED, locator: str | None = None) -> ArtifactManifest:
    return ArtifactManifest(
        artifact_id=artifact_id,
        run_id=run_id,
        stage_id="STEP23_PLATFORM",
        attempt_id=REFERENCE_ATTEMPT_ID,
        artifact_kind=kind,
        media_type="application/json" if storage_mode is ArtifactStorageMode.MANAGED else "application/octet-stream",
        producer="tools.validate_step23_data_platform",
        storage_mode=storage_mode,
        external_locator=locator,
        logical_key=f"runs/{run_id}/{artifact_id}.json",
        retention_class=retention,
        created_at=created_at or datetime.now(timezone.utc),
        provenance_refs=("step23-reference-validator",),
    )


def _ensure_run(control: SQLiteControlStore, config: LocalPlatformConfig) -> RunRecord:
    existing = control.get_run(REFERENCE_RUN_ID)
    if existing is not None:
        if existing.configuration_fingerprint != config.configuration_fingerprint:
            raise PlatformError("persisted reference run has a different platform configuration")
        return existing
    return control.create_run(RunRecord(run_id=REFERENCE_RUN_ID, project_id="dirty-data-to-olap", configuration_fingerprint=config.configuration_fingerprint, git_content_commit=STEP22_COMMIT, metadata={"scope": "local-first-step23"}))


def _publish_managed(store: LocalArtifactStore, control: SQLiteControlStore, *, artifact_id: str, kind: str, payload: bytes, created_at: datetime | None = None, retention: RetentionClass = RetentionClass.RUN_SCOPED):
    existing = control.get_artifact(artifact_id)
    if existing is not None:
        return existing
    ref = store.publish(_manifest(REFERENCE_RUN_ID, artifact_id, kind=kind, created_at=created_at, retention=retention), payload)
    return control.register_artifact(ref)


def _register_with_dependencies(control: SQLiteControlStore, artifact, dependencies: tuple[ArtifactDependency, ...]) -> None:
    if control.get_artifact(artifact.artifact_id) is not None:
        stored = {(item.upstream_artifact_id, item.relationship_kind, item.expected_content_hash) for item in control.inspect_dependencies(artifact.artifact_id).dependencies}
        requested = {(item.upstream_artifact_id, item.relationship_kind, item.expected_content_hash) for item in dependencies}
        if not stored:
            control.register_artifact_with_dependencies(artifact, dependencies)
            return
        if stored != requested:
            raise ArtifactConflictError("persisted dependency set conflicts with the requested immutable dependency set")
        return
    control.register_artifact_with_dependencies(artifact, dependencies)


def _check(checks: list[dict[str, object]], name: str, condition: bool, detail: str) -> None:
    checks.append({"name": name, "status": "PASS" if condition else "FAIL", "detail": detail})


def _concurrency_checks(root: Path, checks: list[dict[str, object]]) -> None:
    with TemporaryDirectory(dir=str(root / "workspace")) as temporary:
        project = Path(temporary)
        store = LocalArtifactStore(project / "artifacts", project_root=project)
        manifest = ArtifactManifest(artifact_id="concurrent-identical", run_id="run-a", stage_id="STEP23", attempt_id="attempt-a", artifact_kind="binary", media_type="application/octet-stream", producer="test")
        with ThreadPoolExecutor(max_workers=4) as pool:
            values = list(pool.map(lambda _: store.publish(manifest, b"identical"), range(4)))
        _check(checks, "concurrent identical publication", len({item.content_hash for item in values}) == 1 and len(store.list_artifacts()) == 1, "identical concurrent publication converged to one immutable blob/reference")
        conflicting = ArtifactManifest(artifact_id="concurrent-conflict", run_id="run-a", stage_id="STEP23", attempt_id="attempt-a", artifact_kind="binary", media_type="application/octet-stream", producer="test")
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(store.publish, conflicting, value) for value in (b"left", b"right")]
            outcomes = []
            for future in futures:
                try:
                    outcomes.append(future.result())
                except Exception as exc:  # one explicit immutable conflict is expected
                    outcomes.append(exc)
        _check(checks, "concurrent conflicting publication", sum(isinstance(item, ArtifactConflictError) for item in outcomes) == 1, "one writer published and the other received an explicit immutable conflict")


def main() -> int:
    checks: list[dict[str, object]] = []
    workspace_root = ROOT / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    platform = LocalPlatform.from_project_root(ROOT, resource_budget=ResourceBudget(disk_budget_bytes=50_000_000, max_staged_bytes=20_000_000))
    control = platform.control_store
    store = platform.artifact_store
    try:
        run = _ensure_run(control, platform.config)
        _check(checks, "sqlite initializes and migrates", control.schema_version == 1 and platform.config.configuration_fingerprint, "schema version is the current local schema")

        attempt = control.get_stage_attempt(REFERENCE_ATTEMPT_ID)
        if attempt is None:
            attempt = control.create_stage_attempt(StageAttemptRecord(attempt_id=REFERENCE_ATTEMPT_ID, run_id=REFERENCE_RUN_ID, stage_id="STEP23_PLATFORM", attempt_number=1, policy_config_fingerprint="step23-local-policy-v1", resource_budget_ref="step23-default-budget"))
        _check(checks, "stage attempt persists", control.get_stage_attempt(REFERENCE_ATTEMPT_ID) is not None, "stage attempt is stored independently of process memory")

        canonical = _publish_managed(store, control, artifact_id="step23-canonical-model", kind="CanonicalModel", payload=_json_bytes({"contract": "project-owned", "source": "Step19"}))
        analytical = _publish_managed(store, control, artifact_id="step23-analytical-plan", kind="AnalyticalPlan", payload=_json_bytes({"contract": "project-owned", "source": "Step20"}))
        semantic = _publish_managed(store, control, artifact_id="step23-semantic-model", kind="SemanticModel", payload=_json_bytes({"contract": "project-owned", "source": "Step21"}))
        reconciliation = _publish_managed(store, control, artifact_id="step23-reconciliation-result", kind="ReconciliationResult", payload=_json_bytes({"contract": "project-owned", "source": "Step22"}))
        accounting = _publish_managed(store, control, artifact_id="step23-record-accounting", kind="RecordAccountingArtifact", payload=_json_bytes({"boundary": "platform-reference"}))
        _register_with_dependencies(control, analytical, (ArtifactDependency(artifact_id=analytical.artifact_id, upstream_artifact_id=canonical.artifact_id, expected_content_hash=canonical.content_hash, relationship_kind="DERIVED_FROM"),))
        _register_with_dependencies(control, semantic, (ArtifactDependency(artifact_id=semantic.artifact_id, upstream_artifact_id=analytical.artifact_id, expected_content_hash=analytical.content_hash, relationship_kind="DERIVED_FROM"),))
        _register_with_dependencies(control, reconciliation, (ArtifactDependency(artifact_id=reconciliation.artifact_id, upstream_artifact_id=semantic.artifact_id, expected_content_hash=semantic.content_hash, relationship_kind="DERIVED_FROM"),))
        _check(checks, "managed artifact publishes atomically", all(item.publication_state.value == "PUBLISHED" for item in (canonical, analytical, semantic, reconciliation, accounting)), "representative project artifacts are published only after bytes and hashes are complete")
        _check(checks, "artifact hash verifies", store.verify(canonical).state.value == "VERIFIED", "content-addressed managed bytes verify against their registered hash")

        second_run = control.get_run("step23-concurrent-run") or control.create_run(RunRecord(run_id="step23-concurrent-run", project_id="dirty-data-to-olap", configuration_fingerprint=platform.config.configuration_fingerprint))
        second_manifest = ArtifactManifest(artifact_id="same-bytes-second-run", run_id=second_run.run_id, stage_id="STEP23_PLATFORM", attempt_id="attempt-second", artifact_kind="binary", media_type="application/octet-stream", producer="step23")
        second_ref = store.publish(second_manifest, b"shared immutable content")
        control.register_artifact(second_ref)
        first_ref = _publish_managed(store, control, artifact_id="same-bytes-first-run", kind="binary", payload=b"shared immutable content")
        _check(checks, "identical bytes remain independently referenced", first_ref.storage_key == second_ref.storage_key and first_ref.artifact_id != second_ref.artifact_id, "logical references remain distinct while the content-addressed blob is shared")
        _check(checks, "concurrent run isolation", second_ref.run_id != first_ref.run_id and len(control.list_artifacts(run_id=second_ref.run_id)) == 1 and all(item.run_id == REFERENCE_RUN_ID for item in control.list_artifacts(run_id=REFERENCE_RUN_ID)), "run-scoped metadata and logical references do not collide across runs")

        stale = _publish_managed(store, control, artifact_id="step23-stale-dependent", kind="metadata", payload=b"stale")
        _register_with_dependencies(control, stale, (ArtifactDependency(artifact_id=stale.artifact_id, upstream_artifact_id=canonical.artifact_id, expected_content_hash="f" * 64, relationship_kind="INPUT"),))
        stale_detected = False
        try:
            control.resolve_dependencies(stale.artifact_id)
        except StaleDependencyError:
            stale_detected = True
        _check(checks, "dependency graph resolves and detects stale hashes", control.inspect_dependencies(semantic.artifact_id).status == "RESOLVED" and stale_detected, "explicit upstream IDs and expected hashes are resolved without filename inference")

        rollback_ref = store.publish(_manifest(REFERENCE_RUN_ID, "step23-transaction-rollback", kind="metadata"), b"rollback")
        rollback_failed = False
        try:
            control.register_artifact_with_dependencies(rollback_ref, (ArtifactDependency(artifact_id=rollback_ref.artifact_id, upstream_artifact_id="missing-upstream", expected_content_hash="a" * 64, relationship_kind="INPUT"),))
        except PlatformError:
            rollback_failed = True
        _check(checks, "control metadata bundle rolls back atomically", rollback_failed and control.get_artifact(rollback_ref.artifact_id) is None and control.inspect_dependencies(rollback_ref.artifact_id).dependencies == (), "invalid upstream registration leaves neither an artifact row nor dependency rows")

        try:
            import pyarrow as pa
            import pyarrow.parquet as pq

            sink = pa.BufferOutputStream()
            pq.write_table(pa.table({"value": [1]}), sink, compression="none", write_statistics=False)
            parquet_payload = sink.getvalue().to_pybytes()
        except Exception as exc:
            raise RuntimeError(f"the existing Parquet capability is required for the tiny reference part: {exc}") from exc
        staging = LocalStagingStore(platform.staging_artifact_store, control_store=control)
        seed = canonical
        dataset_base = StagedDatasetManifest(dataset_id="step23-staged-dataset", dataset_version="v1", run_id=REFERENCE_RUN_ID, source_id="step23-source", snapshot_id="step23-snapshot", table_id="step23-table", schema_fingerprint="schema-step23", format="parquet", parts=(StagedDatasetPart(part_id="seed", logical_key="runs/step23-platform-reference-run/seed", artifact_ref=seed, schema_fingerprint="schema-step23"),), row_count=1, sampling_scope="full-reference")
        part_id = "part-000"
        part_artifact_id = stable_id("staged", {"dataset_id": dataset_base.dataset_id, "dataset_version": dataset_base.dataset_version, "part_id": part_id})
        existing_part = control.get_artifact(part_artifact_id)
        if existing_part is None:
            part = staging.publish_part(dataset_base, part_id, parquet_payload, attempt_id=REFERENCE_ATTEMPT_ID, producer="step23")
            part = part.model_copy(update={"row_count": 1})
            control.register_artifact(part.artifact_ref)
        else:
            part = StagedDatasetPart(part_id=part_id, logical_key="runs/step23-platform-reference-run/source/step23-source/snapshot/step23-snapshot/table/step23-table/dataset/step23-staged-dataset/version/v1/part/part-000.parquet", artifact_ref=existing_part, row_count=1, schema_fingerprint="schema-step23", sampling_scope="full-reference")
        dataset = dataset_base.model_copy(update={"parts": (part,)})
        staging.register_manifest(dataset)
        recovered_dataset = control.get_staged_dataset(dataset.dataset_id, dataset.dataset_version)
        _check(checks, "staged dataset manifest resolves", recovered_dataset is not None and recovered_dataset.parts[0].artifact_ref.content_hash == part.artifact_ref.content_hash and recovered_dataset.parts[0].row_count == 1, "versioned Parquet part metadata retains run/source/snapshot/table/schema and hash bindings")

        report_path = ROOT / "workspace" / "runs" / "step22-reference-run" / "validation" / "validation_report.json"
        target_path = ROOT / "workspace" / "runs" / "step20-reference-run" / "olap" / "target.duckdb"
        report_payload = json.loads(report_path.read_text(encoding="utf-8"))
        report_hash = hashlib.sha256(report_path.read_bytes()).hexdigest()
        report_manifest = _manifest(REFERENCE_RUN_ID, "step22-validation-report", kind="ValidationReport", retention=RetentionClass.PINNED_GATE_EVIDENCE, storage_mode=ArtifactStorageMode.EXTERNAL, locator=report_path.relative_to(ROOT).as_posix())
        report_ref = store.register_external(report_manifest, report_manifest.external_locator or "")
        target_manifest = _manifest(REFERENCE_RUN_ID, "step22-controlled-duckdb", kind="MaterializationArtifact", storage_mode=ArtifactStorageMode.EXTERNAL, locator=target_path.relative_to(ROOT).as_posix())
        target_ref = store.register_external(target_manifest, target_manifest.external_locator or "")
        control.register_artifact(report_ref)
        control.register_artifact(target_ref)
        gate_status = GateEvidenceStatus.PASS if report_payload.get("g6_status") == "PASS" else GateEvidenceStatus.PENDING
        gate = control.record_gate_evidence(GateEvidence(gate_id="G6_DATA_CORRECTNESS", run_id=REFERENCE_RUN_ID, status=gate_status, eligible=bool(report_payload.get("g6_eligible")), validation_report_artifact_id=report_ref.artifact_id, validation_report_content_hash=report_hash, policy_version=str(report_payload["policy"]["policy_version"]), verified_content_commit=STEP22_COMMIT, provenance_refs=("step22:exact-validation-report",)))
        _check(checks, "external DuckDB verifies", store.verify(target_ref).state.value == "VERIFIED", "accepted Step20 target is registered by controlled external reference without copying it")
        _check(checks, "G6 receipt persists from exact ValidationReport", gate.status is GateEvidenceStatus.PASS and gate.eligible and gate.validation_report_content_hash == report_hash, "G6 status and eligibility come from ValidationReport.g6_status/g6_eligible")
        _check(checks, "G6 is not promoted by no_blocking_discrepancy alone", report_payload.get("g6_status") == "PASS" and report_payload.get("g6_eligible") is True and {"g6_status", "g6_eligible"}.issubset(report_payload), "the persistence path requires the authoritative status and eligible fields")
        signal_only = {"no_blocking_discrepancy": True, "g6_status": "PENDING", "g6_eligible": False}
        _check(checks, "reconciliation signal alone cannot promote G6", signal_only["no_blocking_discrepancy"] and signal_only["g6_status"] != "PASS" and gate.status is GateEvidenceStatus.PASS, "a reconciliation-style signal is not used as a substitute for the exact ValidationReport authority")

        with TemporaryDirectory(dir=str(ROOT / "workspace")) as temporary:
            external_project = Path(temporary)
            fixture = external_project / "controlled-external.duckdb"
            fixture.write_bytes(b"external-original")
            external_store = LocalArtifactStore(external_project / "artifacts", project_root=external_project)
            external_manifest = _manifest(REFERENCE_RUN_ID, "step23-disposable-external", kind="MaterializationArtifact", storage_mode=ArtifactStorageMode.EXTERNAL, locator="controlled-external.duckdb")
            external_ref = external_store.register_external(external_manifest, "controlled-external.duckdb")
            fixture.write_bytes(b"external-mutated")
            external_change = external_store.verify(external_ref)
        _check(checks, "external artifact mutation is detected", external_change.state.value == "HASH_MISMATCH", "a changed controlled external fixture is detected without mutating the accepted Step20 target")

        cache_key = CacheKey(stage_id="STEP23_PLATFORM", component_id="platform", input_artifacts=(CacheInputRef(artifact_id=canonical.artifact_id, content_hash=canonical.content_hash),), contract_versions={"platform": "1.0"}, policy_version="step23-cache-v1", configuration_fingerprint=platform.config.configuration_fingerprint, engine_version="local", code_version=STEP22_COMMIT, domain_scope_fingerprint="step23-reference")
        control.record_cache_entry(CacheEntry(cache_key_hash=cache_key.key_hash, cache_key=cache_key, output_artifact_id=semantic.artifact_id, output_content_hash=semantic.content_hash))
        cache_hit = PlatformCacheService().resolve(cache_key, control_store=control, artifact_store=store)
        changed_key = cache_key.model_copy(update={"input_artifacts": (CacheInputRef(artifact_id=canonical.artifact_id, content_hash="e" * 64),)})
        _check(checks, "cache key is deterministic", cache_key.key_hash == CacheKey.model_validate(cache_key.model_dump(mode="json")).key_hash, "cache identity is derived from typed ordered input and policy/configuration bindings")
        _check(checks, "cache hit verifies output", cache_hit is not None and cache_hit.artifact_id == semantic.artifact_id, "a hit requires the output metadata and bytes to verify")
        _check(checks, "changed input hash invalidates cache", PlatformCacheService().resolve(changed_key, control_store=control, artifact_store=store) is None, "a changed upstream hash produces a different cache identity and no stale result")
        policy_key = cache_key.model_copy(update={"policy_version": "step23-cache-v2"})
        schema_key = cache_key.model_copy(update={"contract_versions": {"platform": "2.0"}})
        _check(checks, "policy/schema changes do not reuse cache", PlatformCacheService().resolve(policy_key, control_store=control, artifact_store=store) is None and PlatformCacheService().resolve(schema_key, control_store=control, artifact_store=store) is None, "policy and contract-version changes produce non-matching cache identities")

        missing = _publish_managed(store, control, artifact_id="step23-missing-disposable", kind="DisposableNegative", payload=b"disposable")
        missing_blob = store._blob_path(missing.content_hash)
        missing_blob.unlink(missing_ok=True)
        missing_cache_key = cache_key.model_copy(update={"stage_id": "STEP23_MISSING_CACHE", "input_artifacts": (CacheInputRef(artifact_id=missing.artifact_id, content_hash=missing.content_hash),)})
        control.record_cache_entry(CacheEntry(cache_key_hash=missing_cache_key.key_hash, cache_key=missing_cache_key, output_artifact_id=missing.artifact_id, output_content_hash=missing.content_hash))
        _check(checks, "missing cached output is not returned", PlatformCacheService().resolve(missing_cache_key, control_store=control, artifact_store=store) is None and control.get_cache_entry(missing_cache_key).status.value == "INVALIDATED", "missing output is an explicit cache invalidation, never a fabricated hit")

        corrupt = _publish_managed(store, control, artifact_id="step23-corrupt-cache-output", kind="CacheOutput", payload=b"correct")
        corrupt_key = cache_key.model_copy(update={"stage_id": "STEP23_CORRUPT_CACHE", "input_artifacts": (CacheInputRef(artifact_id=corrupt.artifact_id, content_hash=corrupt.content_hash),)})
        control.record_cache_entry(CacheEntry(cache_key_hash=corrupt_key.key_hash, cache_key=corrupt_key, output_artifact_id=corrupt.artifact_id, output_content_hash=corrupt.content_hash))
        store._blob_path(corrupt.content_hash).write_bytes(b"tampered")
        _check(checks, "corrupt cached output is not returned", PlatformCacheService().resolve(corrupt_key, control_store=control, artifact_store=store) is None and control.get_cache_entry(corrupt_key).status.value == "INVALIDATED", "hash mismatch invalidates the cache entry before returning any output")

        reopened = SQLiteControlStore(control.path, project_root=ROOT)
        recovered_run = reopened.get_run(REFERENCE_RUN_ID)
        recovered_attempt = reopened.get_stage_attempt(REFERENCE_ATTEMPT_ID)
        recovered_gate = reopened.get_gate_evidence("G6_DATA_CORRECTNESS", run_id=REFERENCE_RUN_ID)
        _check(checks, "restart recovers run, attempt, dependencies, cache and gate", recovered_run is not None and recovered_attempt is not None and reopened.inspect_dependencies(semantic.artifact_id).status == "RESOLVED" and reopened.get_cache_entry(cache_key) is not None and recovered_gate is not None, "a fresh SQLite adapter rediscovers all durable references")

        run = recovered_run
        if run.status is not RunStatus.SUCCEEDED:
            run = reopened.update_run(run.model_copy(update={"status": RunStatus.SUCCEEDED, "root_artifact_refs": tuple(item.artifact_id for item in (canonical, analytical, semantic, reconciliation, accounting, report_ref, target_ref)), "gate_refs": ("G6_DATA_CORRECTNESS",)}), expected_revision=run.revision)
        if recovered_attempt.status is not StageStatus.SUCCEEDED:
            reopened.update_stage_attempt(recovered_attempt.model_copy(update={"status": StageStatus.SUCCEEDED, "finished_at": datetime.now(timezone.utc), "output_artifact_refs": (canonical.artifact_id, analytical.artifact_id, semantic.artifact_id, reconciliation.artifact_id)}), expected_revision=recovered_attempt.revision)
        _check(checks, "reference run is persisted with completed stage metadata", run.status is RunStatus.SUCCEEDED, "run lifecycle state is durable and does not imply Step24 execution")
        reproducibility = reopened.build_reproducibility_manifest(REFERENCE_RUN_ID, artifact_store=store)
        _check(checks, "reproducibility manifest is durable in metadata", reproducibility.run_id == REFERENCE_RUN_ID and reproducibility.configuration_fingerprint == platform.config.configuration_fingerprint and reproducibility.git_content_commit == STEP22_COMMIT and reproducibility.control_schema_version == 1 and len(reproducibility.root_artifact_refs) >= 1, "run, config, content commit, artifact hashes, gate refs and control schema are recoverable provenance")

        cleanup_policy = (RetentionPolicy(retention_class=RetentionClass.RUN_SCOPED, max_age_seconds=1, reason="disposable reference evidence"),)
        cleanup_old = _publish_managed(store, reopened, artifact_id="step23-cleanup-disposable", kind="DisposableCleanup", payload=b"cleanup", created_at=datetime.now(timezone.utc) - timedelta(days=2))
        plan = PlatformLifecycleService().build_cleanup_plan(control_store=reopened, run_id=REFERENCE_RUN_ID, policy=cleanup_policy, now=datetime.now(timezone.utc))
        before = store.exists(cleanup_old)
        dry = PlatformLifecycleService().execute_cleanup(plan, CleanupAuthorization(plan_id=plan.plan_id, actor="step23-validator", authorized=True), control_store=reopened, artifact_store=store)
        _check(checks, "cleanup is plan-first and dry-run is non-destructive", dry.executed is False and before and store.exists(cleanup_old), "dry-run planning performs no deletion")
        pinned_protected = all(item.artifact_id != report_ref.artifact_id for item in plan.candidates)
        try:
            store.delete(report_ref, CleanupAuthorization(plan_id=plan.plan_id, actor="step23-validator", authorized=True))
            pinned_protected = False
        except Exception:
            pass
        _check(checks, "pinned G6 evidence is protected", pinned_protected and store.exists(report_ref), "pinned gate evidence is not a cleanup candidate and direct deletion is rejected")

        _concurrency_checks(ROOT, checks)
        with TemporaryDirectory(dir=str(ROOT / "workspace")) as temporary:
            project = Path(temporary)
            quota_store = LocalArtifactStore(project / "artifacts", project_root=project, disk_budget_bytes=3)
            quota_failed = False
            try:
                quota_store.publish(ArtifactManifest(artifact_id="quota", run_id="run", stage_id="STEP23", attempt_id="attempt", artifact_kind="binary", media_type="application/octet-stream", producer="test"), b"four")
            except ArtifactIntegrityError:
                quota_failed = True
            _check(checks, "local disk quota fails closed", quota_failed and quota_store.list_artifacts() == (), "known-size publication cannot exceed the configured local byte budget")
            outside = LocalArtifactStore(project / "artifacts-2", project_root=project)
            safe = True
            try:
                outside.register_external(ArtifactManifest(artifact_id="escape", run_id="run", stage_id="STEP23", attempt_id="attempt", artifact_kind="binary", media_type="application/octet-stream", producer="test", storage_mode=ArtifactStorageMode.EXTERNAL, external_locator="../escape"), "../escape")
            except PathConfinementError:
                pass
            else:
                safe = False
            _check(checks, "outside-root locator is rejected", safe, "external references cannot use absolute or traversal paths")

        with TemporaryDirectory(dir=str(ROOT / "workspace")) as temporary:
            future_db = Path(temporary) / "future.sqlite"
            first = SQLiteControlStore(future_db, project_root=Path(temporary))
            connection = sqlite3.connect(first.path)
            try:
                connection.execute("UPDATE schema_meta SET schema_version = 99")
                connection.commit()
            finally:
                connection.close()
            future_rejected = False
            try:
                SQLiteControlStore(future_db, project_root=Path(temporary))
            except Exception:
                future_rejected = True
            _check(checks, "newer control schema fails closed", future_rejected, "the adapter does not destructively reset an unsupported future schema")

        capabilities = platform.capabilities
        _check(checks, "future S3/Postgres are not falsely implemented", capabilities.get(CapabilityQuery(capability_id="persistence.s3")).status == "FUTURE_NOT_EXECUTED" and capabilities.get(CapabilityQuery(capability_id="persistence.postgres")).status == "FUTURE_NOT_EXECUTED", "future adapter boundaries are explicit and unexecuted")
        _check(checks, "Step24 is not started", not (ROOT / "src/dirty_data_to_olap/application/distributed_data.py").exists(), "Step23 leaves distributed data execution absent")

        scan = PlatformIntegrityService().scan(REFERENCE_RUN_ID, control_store=reopened, artifact_store=store, artifact_stores=(platform.staging_artifact_store,))
        _check(checks, "integrity scan detects missing disposable artifact", any(item.artifact_id == missing.artifact_id and item.state.value == "MISSING" for item in scan.results), "scan is bounded to registered run artifacts and reports the missing blob")
        audit_events = reopened.list_audit_events(run_id=REFERENCE_RUN_ID)
        all_audit_events = reopened.list_audit_events()
        _check(checks, "safe lifecycle audit events persist", {item["event_type"] for item in all_audit_events} >= {"artifact_registered", "cache_invalidated", "cleanup_planned", "cleanup_executed", "artifact_verification_failed"}, "material platform events retain IDs, hashes and status without raw payloads")

        inspected = {
            "schema_version": reopened.schema_version,
            "run": reopened.get_run(REFERENCE_RUN_ID).model_dump(mode="json"),
            "stage_attempts": [reopened.get_stage_attempt(REFERENCE_ATTEMPT_ID).model_dump(mode="json")],
            "artifact_count": len(reopened.list_artifacts(run_id=REFERENCE_RUN_ID)),
            "dependency_count": len(reopened.inspect_dependencies(semantic.artifact_id).dependencies),
            "cache_status": reopened.get_cache_entry(cache_key).status.value,
            "gate": reopened.get_gate_evidence("G6_DATA_CORRECTNESS", run_id=REFERENCE_RUN_ID).model_dump(mode="json"),
            "staged_dataset": reopened.get_staged_dataset("step23-staged-dataset", "v1").model_dump(mode="json"),
            "integrity_scan": scan.model_dump(mode="json"),
            "reproducibility_manifest": reproducibility.model_dump(mode="json"),
            "audit_events": audit_events,
            "audit_event_types": sorted({item["event_type"] for item in all_audit_events}),
            "cleanup_plan": plan.model_dump(mode="json"),
            "external_target": target_ref.model_dump(mode="json"),
        }
        passed = all(item["status"] == "PASS" for item in checks)
        output = {"validator": "step23-data-platform", "status": "PASS" if passed else "FAIL", "checks": checks, "check_count": len(checks), "inspected": inspected}
        print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
        return 0 if passed else 1
    except Exception as exc:
        output = {"validator": "step23-data-platform", "status": "FAIL", "checks": checks, "error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
        return 1
    finally:
        platform.close()


if __name__ == "__main__":
    raise SystemExit(main())
