"""Run real project producer contracts over the exact frozen schema estate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from dirty_data_to_olap.adapters.dependencies.desbordante import DesbordanteDependencyAdapter
from dirty_data_to_olap.adapters.dependencies.staged import DependencyStagedReader
from dirty_data_to_olap.adapters.profiling.dataprofiler import DataProfilerAdapter
from dirty_data_to_olap.adapters.quality.staged import ParquetQualityStagedReader
from dirty_data_to_olap.application.dependency_discovery import DependencyDiscoveryService
from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService
from dirty_data_to_olap.application.profiling import ProfilingService
from dirty_data_to_olap.application.quality import QualityAnalysisService
from dirty_data_to_olap.domain.contracts.dependency import DependencyKind, DependencyRequest
from dirty_data_to_olap.domain.contracts.profiling import NullMarkerPolicy, ProfileMode, ProfileRequest
from dirty_data_to_olap.domain.contracts.quality import QualityRequest, QualityRuleSet, quality_profile_fingerprint
from step18_provider_fixtures import schema_source, step18_privacy_policy


MANIFEST = ROOT / "benchmarks" / "inference_evaluation" / "provider_scenarios" / "schema_matching" / "scenarios_v3.json"
RUN = ROOT / "workspace" / "runs" / "step18-inference-baseline-v4" / "evaluation" / "schema_contracts"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _receipt_hash(value: dict) -> str:
    payload = dict(value)
    payload.pop("receipt_content_hash", None)
    return hashlib.sha256((json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest()


def _schema_population(manifest: dict) -> str:
    population = {}
    for scenario in manifest["scenarios"]:
        group = scenario["scenario_group_id"]
        source_table, target_table = f"crm_customers_{group}", f"erp_customers_{group}"
        columns = [f"{source_table}-{item['name']}" for item in scenario["source_columns"]] + [f"{target_table}-{item['name']}" for item in scenario["target_columns"]]
        population[group] = {"source_ids": ["crm-v3", "erp-v3"], "snapshot_ids": ["snapshot-crm-v3", "snapshot-erp-v3"], "table_ids": [source_table, target_table], "column_ids": sorted(columns)}
    return hashlib.sha256(json.dumps(population, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _quality_request(catalog, snapshot, profile) -> QualityRequest:
    return QualityRequest(
        quality_run_id=f"step18-v4-schema-quality-{snapshot.snapshot.snapshot_id}",
        source_id=catalog.source_id,
        snapshot_id=snapshot.snapshot.snapshot_id,
        rule_set=QualityRuleSet(rule_set_id="step18-v4-schema-quality-rules", version="1", rules=(), provenance="step18-v4-schema-quality-policy"),
        profile_result_fingerprint=quality_profile_fingerprint(profile),
        profile_refs=tuple(item.profile_id for item in profile.tables),
        batch_ids=tuple(batch.batch_id for batch in snapshot.batches),
        batch_hashes=tuple(batch.content_hash for batch in snapshot.batches),
        provenance="step18-v4-schema-real-quality-contract",
    )


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    fixture_hash = _sha(MANIFEST)
    profiles, qualities, dependencies = [], [], []
    policy = PrivacyPolicyService(policy=step18_privacy_policy(), project_root=RUN)
    for scenario in manifest["scenarios"]:
        group = scenario["scenario_group_id"]
        for source_id, column_items in (("crm-v3", scenario["source_columns"]), ("erp-v3", scenario["target_columns"])):
            table_id = f"{'crm' if source_id == 'crm-v3' else 'erp'}_customers_{group}"
            columns = tuple((item["name"], item["type"]) for item in column_items)
            catalog, snapshot = schema_source(source_id, table_id, columns, RUN)
            profile_request = ProfileRequest(profile_request_id=f"step18-v4-schema-profile-{source_id}-{group}", source_id=source_id, snapshot_id=snapshot.snapshot.snapshot_id, selected_table_ids=(table_id,), mode=ProfileMode.FULL, null_marker_policy=NullMarkerPolicy(configured_markers=("NULL",)))
            profile = ProfilingService(DataProfilerAdapter(), project_root=RUN).profile(profile_request, catalog, snapshot, artifact_root=RUN / "contract-artifacts")
            quality = QualityAnalysisService(ParquetQualityStagedReader(), project_root=RUN).analyze(_quality_request(catalog, snapshot, profile), catalog, snapshot, profile, artifact_root=RUN / "contract-artifacts")
            dependency_request = DependencyRequest(request_id=f"step18-v4-schema-dependency-{source_id}-{group}", source_id=source_id, snapshot_id=snapshot.snapshot.snapshot_id, selected_table_ids=(table_id,), requested_kinds=(DependencyKind.UCC, DependencyKind.FD, DependencyKind.IND))
            decision = policy.authorize_dependency_analysis(dependency_request.privacy_context, source_id=source_id, snapshot_id=snapshot.snapshot.snapshot_id, table_ids=(table_id,), artifact_ids=tuple(batch.batch_id for batch in snapshot.batches))
            if not decision.allowed:
                raise RuntimeError(f"schema dependency authorization denied: {decision.reason}")
            dependency = DependencyDiscoveryService(DesbordanteDependencyAdapter(project_root=RUN, reader=DependencyStagedReader(), privacy_policy=policy), project_root=RUN, privacy_policy=policy).discover(dependency_request, catalog, snapshot, artifact_root=RUN / "contract-artifacts")
            base = {"scenario_group_id": group, "source_id": source_id}
            profiles.append(base | {"result": profile.model_dump(mode="json")})
            qualities.append(base | {"result": quality.model_dump(mode="json")})
            dependencies.append(base | {"result": dependency.model_dump(mode="json")})
    RUN.mkdir(parents=True, exist_ok=True)
    outputs = {
        "profiling": ("dataprofiler", "DataProfilerAdapter", "0.13.4", profiles),
        "quality": ("project-owned", "QualityAnalysisService", "step09-contract-v1", qualities),
        "dependency": ("desbordante", "DesbordanteDependencyAdapter", "2.4.1", dependencies),
    }
    for component, (provider, adapter, version, results) in outputs.items():
        payload = {"schema_version": "1", "provider": provider, "adapter": adapter, "version": version, "manifest_hash": fixture_hash, "results": results}
        data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
        output = RUN / f"normalized_{component}_results.json"
        output.write_bytes(data)
        receipt = {"component": f"schema_{component}", "provider": provider, "version": version, "adapter": adapter, "execution_result": "COMPLETE", "scenario_group_ids": [item["scenario_group_id"] for item in manifest["scenarios"]], "scenario_fixture_hashes": {"manifest": fixture_hash}, "population_fingerprint": _schema_population(manifest), "output_artifact": output.relative_to(ROOT).as_posix(), "output_hash": _sha(output)}
        receipt["receipt_content_hash"] = _receipt_hash(receipt)
        (RUN / f"{component}_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"scenario_count": len(manifest["scenarios"]), "source_result_count": len(profiles), "profile_statuses": sorted({item["result"]["completeness"] for item in profiles}), "quality_statuses": sorted({item["result"]["completeness"] for item in qualities}), "dependency_statuses": sorted({item["result"]["status"] for item in dependencies})}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
