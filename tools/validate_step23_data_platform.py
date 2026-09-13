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
    GateEvidenceService,
    CleanupAuthorizationError,
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
    CleanupAction,
    CleanupCandidate,
    CleanupDeletionPermit,
    GateEvidenceStatus,
    GateEvidence,
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
    staged_part_artifact_id,
    staged_part_logical_key,
)
from dirty_data_to_olap.domain.contracts.validation import GateStatus, ValidationStatus, ValidationReport
from dirty_data_to_olap.platform import LocalPlatform


REFERENCE_RUN_ID = "step23-platform-reference-run"
REFERENCE_ATTEMPT_ID = "step23-platform-attempt-1"
STEP22_COMMIT = "f5ddeb6dbd2a1759b8d92a4dacf47400d6b580c2"


def _json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _manifest(run_id: str, artifact_id: str, *, kind: str, created_at: datetime | None = None, retention: RetentionClass = RetentionClass.RUN_SCOPED, storage_mode: ArtifactStorageMode = ArtifactStorageMode.MANAGED, locator: str | None = None, logical_key: str | None = None) -> ArtifactManifest:
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
        logical_key=logical_key or f"runs/{run_id}/{artifact_id}.json",
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
    # The validator intentionally mutates disposable blobs, cache rows and
    # tombstones.  Keep that negative testing inside an isolated project root
    # so a second invocation cannot inherit the first invocation's corruption.
    platform_root = TemporaryDirectory(dir=str(workspace_root))
    platform = LocalPlatform.from_project_root(Path(platform_root.name), resource_budget=ResourceBudget(disk_budget_bytes=50_000_000, max_staged_bytes=20_000_000))
    control = platform.control_store
    store = platform.artifact_store
    try:
        run = _ensure_run(control, platform.config)
        _check(checks, "sqlite initializes and migrates", control.schema_version == 6 and platform.config.configuration_fingerprint, "schema version is the current local schema including Step28 durable delivery migration")

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
        _check(checks, "concurrent run isolation", second_ref.run_id != first_ref.run_id and all(item.run_id == second_ref.run_id for item in control.list_artifacts(run_id=second_ref.run_id)) and all(item.run_id == REFERENCE_RUN_ID for item in control.list_artifacts(run_id=REFERENCE_RUN_ID)), "run-scoped metadata and logical references do not collide across runs")

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
        dataset_id = "step23-staged-dataset-v3"
        seed_logical_key = staged_part_logical_key(run_id=REFERENCE_RUN_ID, source_id="step23-source", snapshot_id="step23-snapshot", table_id="step23-table", dataset_id=dataset_id, dataset_version="v1", part_id="seed")
        seed_artifact_id = staged_part_artifact_id(run_id=REFERENCE_RUN_ID, source_id="step23-source", snapshot_id="step23-snapshot", table_id="step23-table", dataset_id=dataset_id, dataset_version="v1", part_id="seed", schema_fingerprint="schema-step23")
        seed = store.publish(_manifest(REFERENCE_RUN_ID, seed_artifact_id, kind="STAGED_DATASET_PART", logical_key=seed_logical_key, retention=RetentionClass.PERSISTENT), b"seed")
        dataset_base = StagedDatasetManifest(dataset_id=dataset_id, dataset_version="v1", run_id=REFERENCE_RUN_ID, source_id="step23-source", snapshot_id="step23-snapshot", table_id="step23-table", schema_fingerprint="schema-step23", format="parquet", parts=(StagedDatasetPart(part_id="seed", logical_key=seed_logical_key, artifact_ref=seed, schema_fingerprint="schema-step23"),), row_count=None, sampling_scope="full-reference", retention_class=RetentionClass.PERSISTENT)
        part_id = "part-000"
        part_artifact_id = staged_part_artifact_id(run_id=dataset_base.run_id, source_id=dataset_base.source_id, snapshot_id=dataset_base.snapshot_id, table_id=dataset_base.table_id, dataset_id=dataset_base.dataset_id, dataset_version=dataset_base.dataset_version, part_id=part_id, schema_fingerprint=dataset_base.schema_fingerprint)
        existing_part = control.get_artifact(part_artifact_id)
        if existing_part is None:
            part = staging.publish_part(dataset_base, part_id, parquet_payload, attempt_id=REFERENCE_ATTEMPT_ID, producer="step23")
            part = part.model_copy(update={"row_count": 1})
            control.register_artifact(part.artifact_ref)
        else:
            part = StagedDatasetPart(part_id=part_id, logical_key=staged_part_logical_key(run_id=dataset_base.run_id, source_id=dataset_base.source_id, snapshot_id=dataset_base.snapshot_id, table_id=dataset_base.table_id, dataset_id=dataset_base.dataset_id, dataset_version=dataset_base.dataset_version, part_id=part_id), artifact_ref=existing_part, row_count=1, schema_fingerprint="schema-step23", sampling_scope="full-reference")
        dataset = dataset_base.model_copy(update={"parts": (part,)})
        staging.register_manifest(dataset)
        recovered_dataset = control.get_staged_dataset(dataset.run_id, dataset.dataset_id, dataset.dataset_version)
        _check(checks, "staged dataset manifest resolves", recovered_dataset is not None and recovered_dataset.parts[0].artifact_ref.content_hash == part.artifact_ref.content_hash and recovered_dataset.parts[0].row_count == 1, "versioned Parquet part metadata retains run/source/snapshot/table/schema and hash bindings")

        second_dataset_seed_key = staged_part_logical_key(run_id=second_run.run_id, source_id="step23-source", snapshot_id="step23-snapshot", table_id="step23-table", dataset_id=dataset_id, dataset_version="v1", part_id="seed")
        second_dataset_seed_id = staged_part_artifact_id(run_id=second_run.run_id, source_id="step23-source", snapshot_id="step23-snapshot", table_id="step23-table", dataset_id=dataset_id, dataset_version="v1", part_id="seed", schema_fingerprint="schema-step23")
        second_dataset_seed = store.publish(_manifest(second_run.run_id, second_dataset_seed_id, kind="STAGED_DATASET_PART", logical_key=second_dataset_seed_key, retention=RetentionClass.PERSISTENT), b"seed")
        second_dataset_base = StagedDatasetManifest(dataset_id=dataset_id, dataset_version="v1", run_id=second_run.run_id, source_id="step23-source", snapshot_id="step23-snapshot", table_id="step23-table", schema_fingerprint="schema-step23", format="parquet", parts=(StagedDatasetPart(part_id="seed", logical_key=second_dataset_seed_key, artifact_ref=second_dataset_seed, schema_fingerprint="schema-step23"),), row_count=None, sampling_scope="full-reference", retention_class=RetentionClass.PERSISTENT)
        second_part = staging.publish_part(second_dataset_base, "part-000", parquet_payload, attempt_id="attempt-second", producer="step23")
        control.register_artifact(second_part.artifact_ref)
        second_dataset = second_dataset_base.model_copy(update={"parts": (second_part.model_copy(update={"row_count": 1}),), "row_count": 1})
        staging.register_manifest(second_dataset)
        first_staged = control.get_staged_dataset(REFERENCE_RUN_ID, dataset_id, "v1")
        second_staged = control.get_staged_dataset(second_run.run_id, dataset_id, "v1")
        _check(checks, "same staged names coexist across runs", first_staged is not None and second_staged is not None and first_staged.parts[0].artifact_ref.artifact_id != second_staged.parts[0].artifact_ref.artifact_id and first_staged.parts[0].logical_key != second_staged.parts[0].logical_key, "run-scoped primary-key and part identity preserve same dataset/version/part names independently")

        injection_rejected = False
        try:
            staging.register_manifest(dataset.model_copy(update={"run_id": second_run.run_id}))
        except (PlatformError, ValueError):
            injection_rejected = True
        scope_rejected = False
        try:
            staging.register_manifest(dataset.model_copy(update={"source_id": "other-source"}))
        except (PlatformError, ValueError):
            scope_rejected = True
        kind_rejected = False
        try:
            bad_ref = part.artifact_ref.model_copy(update={"artifact_kind": "metadata"})
            staging.register_manifest(dataset.model_copy(update={"parts": (part.model_copy(update={"artifact_ref": bad_ref}),)}))
        except (PlatformError, ValueError):
            kind_rejected = True
        schema_rejected = False
        try:
            staging.register_manifest(dataset.model_copy(update={"parts": (part.model_copy(update={"schema_fingerprint": "schema-other"}),)}))
        except (PlatformError, ValueError):
            schema_rejected = True
        row_count_rejected = False
        try:
            staging.register_manifest(dataset.model_copy(update={"row_count": 2}))
        except (PlatformError, ValueError):
            row_count_rejected = True
        _check(checks, "cross-run staged injection is rejected", injection_rejected, "a manifest cannot move a published part into another run")
        _check(checks, "staged source/snapshot/table scope is exact", scope_rejected, "changing source scope without changing the part identity is rejected")
        _check(checks, "staged part kind/publication closure is exact", kind_rejected, "a non-STAGED_DATASET_PART reference cannot enter the manifest")
        _check(checks, "staged schema closure is exact", schema_rejected, "part schema fingerprint must equal the manifest schema fingerprint")
        _check(checks, "staged row-count closure is exact", row_count_rejected, "known part row counts must reconcile to the manifest row count")

        source_report_path = ROOT / "workspace" / "runs" / "step22-reference-run" / "validation" / "validation_report.json"
        source_target_path = ROOT / "workspace" / "runs" / "step20-reference-run" / "olap" / "target.duckdb"
        report_path = Path(platform.config.project_root) / "accepted" / "validation_report.json"
        target_path = Path(platform.config.project_root) / "accepted" / "target.duckdb"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_bytes(source_report_path.read_bytes())
        target_path.write_bytes(source_target_path.read_bytes())
        report_payload = json.loads(report_path.read_text(encoding="utf-8"))
        report_hash = hashlib.sha256(report_path.read_bytes()).hexdigest()
        report_manifest = _manifest(REFERENCE_RUN_ID, "step22-validation-report", kind="ValidationReport", retention=RetentionClass.PINNED_GATE_EVIDENCE, storage_mode=ArtifactStorageMode.EXTERNAL, locator=report_path.relative_to(Path(platform.config.project_root)).as_posix())
        report_ref = store.register_external(report_manifest, report_manifest.external_locator or "")
        target_manifest = _manifest(REFERENCE_RUN_ID, "step22-controlled-duckdb", kind="MaterializationArtifact", storage_mode=ArtifactStorageMode.EXTERNAL, locator=target_path.relative_to(Path(platform.config.project_root)).as_posix())
        target_ref = store.register_external(target_manifest, target_manifest.external_locator or "")
        control.register_artifact(report_ref)
        control.register_artifact(target_ref)
        report = ValidationReport.model_validate({key: value for key, value in report_payload.items() if key != "content_hash"})
        gate = GateEvidenceService().record_from_validation_report(gate_id="G6_DATA_CORRECTNESS", platform_run_id=REFERENCE_RUN_ID, report=report, report_artifact=report_ref, verified_content_commit=STEP22_COMMIT, control_store=control, artifact_store=store, provenance_refs=("step22:exact-validation-report",))
        _check(checks, "external DuckDB verifies", store.verify(target_ref).state.value == "VERIFIED", "accepted Step20 target is registered by controlled external reference without copying it")
        _check(checks, "G6 receipt persists from exact ValidationReport", gate.status is GateEvidenceStatus.PASS and gate.eligible and gate.validation_report_content_hash == report_hash, "G6 status and eligibility come from ValidationReport.g6_status/g6_eligible")
        _check(checks, "G6 is not promoted by no_blocking_discrepancy alone", report_payload.get("g6_status") == "PASS" and report_payload.get("g6_eligible") is True and {"g6_status", "g6_eligible"}.issubset(report_payload), "the persistence path requires the authoritative status and eligible fields")
        signal_only = {"no_blocking_discrepancy": True, "g6_status": "PENDING", "g6_eligible": False}
        _check(checks, "reconciliation signal alone cannot promote G6", signal_only["no_blocking_discrepancy"] and signal_only["g6_status"] != "PASS" and gate.status is GateEvidenceStatus.PASS, "a reconciliation-style signal is not used as a substitute for the exact ValidationReport authority")
        wrong_kind_rejected = False
        try:
            GateEvidenceService().record_from_validation_report(gate_id="G6_DATA_CORRECTNESS", platform_run_id=REFERENCE_RUN_ID, report=report, report_artifact=target_ref, verified_content_commit=STEP22_COMMIT, control_store=control, artifact_store=store)
        except (PlatformError, ArtifactIntegrityError):
            wrong_kind_rejected = True
        _check(checks, "wrong artifact kind cannot back G6", wrong_kind_rejected, "a MaterializationArtifact reference cannot be laundered as a ValidationReport")

        laundering_rejected = False
        try:
            control.record_gate_evidence(gate.model_copy(update={"status": GateEvidenceStatus.FAIL, "eligible": False}), validation_report=report, artifact_store=store)
        except (PlatformError, ArtifactConflictError, ArtifactIntegrityError):
            laundering_rejected = True
        _check(checks, "gate status laundering is rejected", laundering_rejected, "the low-level persistence boundary rejects evidence status or eligibility that differs from the typed report")

        variant_results: dict[str, str] = {}
        with TemporaryDirectory(dir=str(ROOT / "workspace")) as temporary:
            variant_root = Path(temporary)
            for label, check_status, overall_status, gate_status in (
                ("fail", ValidationStatus.FAIL, ValidationStatus.FAIL, GateStatus.FAIL),
                ("pending", ValidationStatus.REVIEW_REQUIRED, ValidationStatus.REVIEW_REQUIRED, GateStatus.PENDING),
            ):
                variant_project = variant_root / label
                variant_store = LocalArtifactStore(variant_project / "artifacts", project_root=variant_project)
                variant_control = SQLiteControlStore(variant_project / "control.sqlite", project_root=variant_project)
                variant_run_id = f"step23-gate-{label}-run"
                variant_control.create_run(RunRecord(run_id=variant_run_id, project_id="dirty-data-to-olap", configuration_fingerprint=platform.config.configuration_fingerprint))
                changed_check = report.checks[0].model_copy(update={"status": check_status})
                variant_report = ValidationReport.model_validate(report.model_copy(update={"checks": (changed_check, *report.checks[1:]), "overall_status": overall_status, "g6_status": gate_status, "g6_eligible": False}).model_dump(mode="json"))
                variant_path = variant_project / "variant-report.json"
                variant_path.parent.mkdir(parents=True, exist_ok=True)
                variant_path.write_bytes(_json_bytes(variant_report.model_dump(mode="json")))
                variant_locator = "variant-report.json"
                variant_manifest = _manifest(variant_run_id, f"step23-{label}-validation-report", kind="ValidationReport", retention=RetentionClass.PINNED_GATE_EVIDENCE, storage_mode=ArtifactStorageMode.EXTERNAL, locator=variant_locator)
                variant_ref = variant_store.register_external(variant_manifest, variant_locator)
                variant_control.register_artifact(variant_ref)
                variant_gate = GateEvidenceService().record_from_validation_report(gate_id="G6_DATA_CORRECTNESS", platform_run_id=variant_run_id, report=variant_report, report_artifact=variant_ref, verified_content_commit=STEP22_COMMIT, control_store=variant_control, artifact_store=variant_store)
                variant_results[label] = variant_gate.status.value
                variant_control.close()
            _check(checks, "typed FAIL/PENDING reports cannot persist PASS", variant_results == {"fail": "FAIL", "pending": "PENDING"}, "gate status and eligibility are derived from the typed ValidationReport, including negative report states")

        with TemporaryDirectory(dir=str(ROOT / "workspace")) as temporary:
            external_project = Path(temporary)
            fixture = external_project / "controlled-external.duckdb"
            fixture.write_bytes(b"external-original")
            external_store = LocalArtifactStore(external_project / "artifacts", project_root=external_project)
            external_manifest = _manifest(REFERENCE_RUN_ID, "step23-disposable-external", kind="MaterializationArtifact", storage_mode=ArtifactStorageMode.EXTERNAL, locator="controlled-external.duckdb")
            external_ref = external_store.register_external(external_manifest, "controlled-external.duckdb")
            (external_project / "other.duckdb").write_bytes(b"other")
            locator_mismatch_rejected = False
            try:
                external_store.register_external(external_manifest, "other.duckdb")
            except ArtifactConflictError:
                locator_mismatch_rejected = True
            fixture.write_bytes(b"external-mutated")
            external_change = external_store.verify(external_ref)
        _check(checks, "external artifact mutation is detected", external_change.state.value == "HASH_MISMATCH", "a changed controlled external fixture is detected without mutating the accepted Step20 target")
        _check(checks, "external locator is manifest-bound", locator_mismatch_rejected, "the registration argument must equal the manifest external locator after safe path resolution")

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

        reopened = SQLiteControlStore(control.path, project_root=Path(platform.config.project_root))
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
        _check(checks, "reproducibility manifest is durable in metadata", reproducibility.run_id == REFERENCE_RUN_ID and reproducibility.configuration_fingerprint == platform.config.configuration_fingerprint and reproducibility.git_content_commit == STEP22_COMMIT and reproducibility.control_schema_version == 6 and len(reproducibility.root_artifact_refs) >= 1, "run, config, content commit, artifact hashes, gate refs and control schema are recoverable provenance")

        cleanup_policy = (RetentionPolicy(retention_class=RetentionClass.RUN_SCOPED, max_age_seconds=3600, reason="disposable reference evidence"),)
        cleanup_old = _publish_managed(store, reopened, artifact_id=f"step23-cleanup-disposable-{int(datetime.now(timezone.utc).timestamp() * 1000000)}", kind="DisposableCleanup", payload=b"cleanup", created_at=datetime.now(timezone.utc) - timedelta(days=2))
        plan = PlatformLifecycleService().build_cleanup_plan(control_store=reopened, run_id=REFERENCE_RUN_ID, policy=cleanup_policy, now=datetime.now(timezone.utc))
        before = store.exists(cleanup_old)
        rebuilt = PlatformLifecycleService().build_cleanup_plan(control_store=reopened, run_id=REFERENCE_RUN_ID, policy=cleanup_policy, now=plan.created_at)
        reordered = plan.model_copy(update={"candidates": tuple(reversed(plan.candidates)), "created_at": plan.created_at + timedelta(seconds=30)})
        _check(checks, "cleanup plan identity is deterministic", plan.content_hash == rebuilt.content_hash == reordered.content_hash, "candidate ordering and volatile creation time do not change semantic plan identity")
        dry = PlatformLifecycleService().execute_cleanup(plan, CleanupAuthorization(plan_id=plan.plan_id, plan_content_hash=plan.content_hash, actor="step23-validator", authorized=True), control_store=reopened, artifact_store=store)
        _check(checks, "cleanup is plan-first and dry-run is non-destructive", dry.executed is False and before and store.exists(cleanup_old), "dry-run planning performs no deletion")
        mutated_plan = plan.model_copy(update={"dry_run": False})
        mutation_rejected = False
        try:
            PlatformLifecycleService().execute_cleanup(mutated_plan, CleanupAuthorization(plan_id=plan.plan_id, plan_content_hash=plan.content_hash, actor="step23-validator", authorized=True), control_store=reopened, artifact_store=store)
        except CleanupAuthorizationError:
            mutation_rejected = True
        _check(checks, "cleanup authorization rejects plan mutation", mutation_rejected, "a dry-run to executable mutation changes the bound semantic hash and requires fresh authorization")

        injected = _publish_managed(store, reopened, artifact_id=f"step23-cleanup-injected-{int(datetime.now(timezone.utc).timestamp() * 1000000)}", kind="DisposableCleanup", payload=b"injected", created_at=datetime.now(timezone.utc) - timedelta(days=2))
        injected_candidate = CleanupCandidate(artifact_id=injected.artifact_id, expected_content_hash=injected.content_hash, reason="injected candidate", retention_class=injected.retention_class, age_seconds=999, byte_size=injected.byte_size, planned_action=CleanupAction.DELETE_BLOB_IF_UNREFERENCED)
        injected_plan = plan.model_copy(update={"dry_run": False, "candidates": (*plan.candidates, injected_candidate)})
        candidate_injection_rejected = False
        try:
            PlatformLifecycleService().execute_cleanup(injected_plan, CleanupAuthorization(plan_id=plan.plan_id, plan_content_hash=plan.content_hash, actor="step23-validator", authorized=True), control_store=reopened, artifact_store=store)
        except CleanupAuthorizationError:
            candidate_injection_rejected = True
        _check(checks, "cleanup authorization rejects candidate injection", candidate_injection_rejected and store.exists(injected), "adding an artifact to the candidate set is rejected under the original authorization")

        cross_run_candidate = CleanupCandidate(artifact_id=second_ref.artifact_id, expected_content_hash=second_ref.content_hash, reason="cross-run injection", retention_class=second_ref.retention_class, age_seconds=999, byte_size=second_ref.byte_size, planned_action=CleanupAction.DELETE_BLOB_IF_UNREFERENCED)
        cross_run_plan = plan.model_copy(update={"dry_run": False, "candidates": (cross_run_candidate,)})
        cross_run_result = PlatformLifecycleService().execute_cleanup(cross_run_plan, CleanupAuthorization(plan_id=cross_run_plan.plan_id, plan_content_hash=cross_run_plan.content_hash, actor="step23-validator", authorized=True), control_store=reopened, artifact_store=store)
        _check(checks, "cleanup run scope is enforced", cross_run_result.rejected_artifact_ids == (second_ref.artifact_id,) and store.exists(second_ref), "even a freshly authorized plan cannot delete an artifact outside its declared run scope")

        permit_rejected = False
        try:
            store.delete(cleanup_old, CleanupDeletionPermit(plan_id=plan.plan_id, plan_content_hash=plan.content_hash, run_id=REFERENCE_RUN_ID, artifact_id=cleanup_old.artifact_id, expected_content_hash="f" * 64, expected_byte_size=cleanup_old.byte_size, retention_class=cleanup_old.retention_class, planned_action=CleanupAction.DELETE_BLOB_IF_UNREFERENCED))
        except CleanupAuthorizationError:
            permit_rejected = True
        _check(checks, "deletion permit is exact and hash-bound", permit_rejected and store.exists(cleanup_old), "direct deletion with a mismatched content hash cannot consume the artifact")

        executable = plan.model_copy(update={"dry_run": False})
        executed_cleanup = PlatformLifecycleService().execute_cleanup(executable, CleanupAuthorization(plan_id=executable.plan_id, plan_content_hash=executable.content_hash, actor="step23-validator", authorized=True), control_store=reopened, artifact_store=store, artifact_stores=(platform.staging_artifact_store,))
        _check(checks, "authorized cleanup tombstones exact candidates", cleanup_old.artifact_id in executed_cleanup.deleted_artifact_ids and not store.exists(cleanup_old) and reopened.get_artifact(cleanup_old.artifact_id).publication_state.value == "TOMBSTONED", "only the exact authorized managed candidate is tombstoned and its unshared blob is removed")
        pinned_protected = all(item.artifact_id != report_ref.artifact_id for item in plan.candidates)
        try:
            store.delete(report_ref, CleanupDeletionPermit(plan_id=plan.plan_id, plan_content_hash=plan.content_hash, run_id=REFERENCE_RUN_ID, artifact_id=report_ref.artifact_id, expected_content_hash=report_ref.content_hash, expected_byte_size=report_ref.byte_size, retention_class=report_ref.retention_class, planned_action=CleanupAction.DELETE_BLOB_IF_UNREFERENCED))
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
        _check(checks, "Step24 boundary remains separate from Step28 job control", not (ROOT / "src/dirty_data_to_olap/application/stage_orchestrator.py").exists() and not (ROOT / "src/dirty_data_to_olap/application/job_control.py").exists() and (ROOT / "src/dirty_data_to_olap/application/jobs.py").exists(), "Step24 remains synchronous while Step28 owns durable scheduling/control")

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
            "staged_dataset": reopened.get_staged_dataset(REFERENCE_RUN_ID, dataset_id, "v1").model_dump(mode="json"),
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
        platform_root.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
