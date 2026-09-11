"""Execute real project Profiling and Quality contracts for Step18 v3."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from dirty_data_to_olap.adapters.profiling.dataprofiler import DataProfilerAdapter
from dirty_data_to_olap.adapters.quality.staged import ParquetQualityStagedReader
from dirty_data_to_olap.application.profiling import ProfilingService
from dirty_data_to_olap.application.quality import QualityAnalysisService
from dirty_data_to_olap.domain.contracts.profiling import NullMarkerPolicy, ProfileMode, ProfileRequest
from dirty_data_to_olap.domain.contracts.quality import QualityRequest, QualityRule, QualityRuleScope, QualityRuleSet, QualityRuleType, QualitySeverity, Repairability, quality_profile_fingerprint
from dirty_data_to_olap.evaluation.step18_scenarios import population_fingerprint, scenario_content_fingerprint
from step18_v3_fixture import build_fixture


MANIFEST = ROOT / "benchmarks" / "inference_evaluation" / "provider_scenarios" / "relationships" / "scenarios_v3.json"
RUN = ROOT / "workspace" / "runs" / "step18-inference-baseline-v3" / "evaluation" / "profiling_quality"


def _receipt_hash(value: dict) -> str:
    payload = dict(value)
    payload.pop("receipt_content_hash", None)
    return hashlib.sha256((json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest()


def _quality_request(catalog, snapshot, profile) -> QualityRequest:
    rules = tuple(QualityRule(rule_id=f"step18.required.{table.table_id}.customer_id", rule_version="1", rule_type=QualityRuleType.REQUIRED_VALUE, scope=QualityRuleScope.USER_POLICY, entity_type="column", table_id=table.table_id, column_ids=(next(column.column_id for column in catalog.columns if column.table_id == table.table_id and column.physical_name == "customer_id"),), severity=QualitySeverity.MEDIUM, repairability=Repairability.MANUAL_BUSINESS_DECISION, detector_config={"missing_markers": ("NULL",)}, provenance="step18-v3-quality-policy") for table in catalog.tables if any(column.table_id == table.table_id and column.physical_name == "customer_id" for column in catalog.columns))
    return QualityRequest(quality_run_id=f"step18-v3-quality-{snapshot.snapshot.snapshot_id}", source_id=catalog.source_id, snapshot_id=snapshot.snapshot.snapshot_id, rule_set=QualityRuleSet(rule_set_id="step18-v3-quality-rules", version="1", rules=rules, provenance="step18-v3-quality-policy"), profile_result_fingerprint=quality_profile_fingerprint(profile), profile_refs=tuple(item.profile_id for item in profile.tables), batch_ids=tuple(batch.batch_id for batch in snapshot.batches), batch_hashes=tuple(batch.content_hash for batch in snapshot.batches), provenance="step18-v3-real-quality-contract")


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    profiles, qualities = [], []
    for scenario in manifest["scenarios"]:
        catalog, snapshot, _ = build_fixture(scenario, RUN)
        request = ProfileRequest(profile_request_id=f"step18-v3-profile-{snapshot.snapshot.snapshot_id}", source_id=catalog.source_id, snapshot_id=snapshot.snapshot.snapshot_id, selected_table_ids=tuple(table.table_id for table in catalog.tables), mode=ProfileMode.FULL, null_marker_policy=NullMarkerPolicy(configured_markers=("NULL",)))
        profile = ProfilingService(DataProfilerAdapter(), project_root=RUN).profile(request, catalog, snapshot, artifact_root=RUN / "contract-artifacts")
        quality = QualityAnalysisService(ParquetQualityStagedReader(), project_root=RUN).analyze(_quality_request(catalog, snapshot, profile), catalog, snapshot, profile, artifact_root=RUN / "contract-artifacts")
        profiles.append({"scenario_group_id":scenario["scenario_group_id"],"scenario_content_fingerprint":scenario_content_fingerprint(scenario),"result":profile.model_dump(mode="json")})
        qualities.append({"scenario_group_id":scenario["scenario_group_id"],"scenario_content_fingerprint":scenario_content_fingerprint(scenario),"result":quality.model_dump(mode="json")})
    RUN.mkdir(parents=True, exist_ok=True)
    manifest_hash = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    for component, provider, adapter, version, results in (("profiling","dataprofiler","DataProfilerAdapter","0.13.4",profiles),("quality","project-owned","QualityAnalysisService","step09-contract-v1",qualities)):
        payload = {"schema_version":"3","provider":provider,"adapter":adapter,"version":version,"manifest_hash":manifest_hash,"results":results}
        data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
        output = RUN / f"normalized_{component}_results.json"
        output.write_bytes(data)
        receipt = {"component":component,"provider":provider,"version":version,"adapter":adapter,"execution_result":"COMPLETE","scenario_group_ids":[item["scenario_group_id"] for item in manifest["scenarios"]],"scenario_fixture_hashes":{"manifest":manifest_hash},"scenario_content_fingerprints":{item["scenario_group_id"]:item["scenario_content_fingerprint"] for item in results},"population_fingerprint":population_fingerprint(manifest["scenarios"]),"output_artifact":output.relative_to(ROOT).as_posix(),"output_hash":hashlib.sha256(data).hexdigest()}
        receipt["receipt_content_hash"] = _receipt_hash(receipt)
        (RUN / f"{component}_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"scenario_count":len(profiles),"profile_completeness":sorted({item["result"]["completeness"] for item in profiles}),"quality_completeness":sorted({item["result"]["completeness"] for item in qualities})}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
