"""Execute the real Desbordante adapter over Step18 v3 scenarios."""

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
from dirty_data_to_olap.application.dependency_discovery import DependencyDiscoveryService
from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService
from dirty_data_to_olap.domain.contracts.dependency import DependencyKind, DependencyRequest
from dirty_data_to_olap.evaluation.step18_scenarios import population_fingerprint, scenario_content_fingerprint
from step18_v3_fixture import build_fixture


MANIFEST = ROOT / "benchmarks" / "inference_evaluation" / "provider_scenarios" / "relationships" / "scenarios_v3.json"
RUN = ROOT / "workspace" / "runs" / "step18-inference-baseline-v3" / "evaluation" / "dependency_discovery"


def _receipt_hash(value: dict) -> str:
    payload = dict(value)
    payload.pop("receipt_content_hash", None)
    return hashlib.sha256((json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest()


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    results = []
    for scenario in manifest["scenarios"]:
        catalog, snapshot, population_hash = build_fixture(scenario, RUN)
        request = DependencyRequest(request_id=f"step18-v3-{scenario['scenario_group_id']}", source_id=catalog.source_id, snapshot_id=snapshot.snapshot.snapshot_id, selected_table_ids=tuple(table.table_id for table in catalog.tables), requested_kinds=(DependencyKind.UCC, DependencyKind.FD, DependencyKind.IND))
        policy = PrivacyPolicyService(project_root=RUN)
        decision = policy.authorize_dependency_analysis(request.privacy_context, source_id=request.source_id, snapshot_id=request.snapshot_id, table_ids=request.selected_table_ids, artifact_ids=tuple(batch.batch_id for batch in snapshot.batches))
        if not decision.allowed:
            raise RuntimeError(f"dependency authorization denied: {decision.reason}")
        result = DependencyDiscoveryService(DesbordanteDependencyAdapter(project_root=RUN, reader=DependencyStagedReader(), privacy_policy=policy), project_root=RUN, privacy_policy=policy).discover(request, catalog, snapshot, artifact_root=RUN / "provider-artifacts")
        results.append({"scenario_group_id": scenario["scenario_group_id"], "task_id": scenario["task_id"], "kind": scenario["kind"], "scenario_content_fingerprint": scenario_content_fingerprint(scenario), "result": result.model_dump(mode="json")})
    RUN.mkdir(parents=True, exist_ok=True)
    data = (json.dumps({"schema_version":"3","provider":"desbordante","adapter":"DesbordanteDependencyAdapter","version":"2.4.1","manifest_hash":hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),"results":results}, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    output = RUN / "normalized_provider_results.json"
    output.write_bytes(data)
    receipt = {"component":"dependency_discovery","provider":"desbordante","version":"2.4.1","adapter":"DesbordanteDependencyAdapter","execution_result":"COMPLETE","scenario_group_ids":[item["scenario_group_id"] for item in manifest["scenarios"]],"scenario_fixture_hashes":{"manifest":hashlib.sha256(MANIFEST.read_bytes()).hexdigest()},"scenario_content_fingerprints":{item["scenario_group_id"]:item["scenario_content_fingerprint"] for item in results},"population_fingerprint":population_fingerprint(manifest["scenarios"]),"output_artifact":output.relative_to(ROOT).as_posix(),"output_hash":hashlib.sha256(data).hexdigest()}
    receipt["receipt_content_hash"] = _receipt_hash(receipt)
    (RUN / "provider_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output":str(output),"scenario_count":len(results),"output_hash":receipt["output_hash"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
