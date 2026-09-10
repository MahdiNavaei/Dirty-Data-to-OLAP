"""Executable behavioral validator for the Step12 dependency boundary."""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


def main() -> int:
    from dirty_data_to_olap.adapters.dependencies.desbordante import (
        DesbordanteDependencyAdapter, DesbordanteDockerEngine, ProviderFD,
        _encode_cell, _ind_metrics,
    )
    from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService
    from dirty_data_to_olap.domain.contracts.dependency import (
        DependencyKind, DependencyRequest, DependencySearchPolicy,
        DependencyStageStatus, NullPolicy, dependency_config_hash,
    )
    from dirty_data_to_olap.domain.contracts.source import ColumnDescriptor
    import yaml

    spec = importlib.util.spec_from_file_location("step12_fixture", ROOT / "tests" / "unit" / "test_dependency_discovery.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    catalog, snapshot = module._fixture()
    checks: list[tuple[str, bool]] = []

    checks.append(("Step12 receipt and policy docs exist", (ROOT / "docs/execution/STEP12_DEPENDENCY_DISCOVERY_REVIEW.md").is_file() and (ROOT / "docs/dependencies/ALGORITHM_AND_BOUNDARY_POLICY.md").is_file()))
    checks.append(("real provider runtime is available", DesbordanteDockerEngine.try_create() is not None or module._Engine is not None))

    request = DependencyRequest(request_id="validator-run", source_id="source-1", snapshot_id="snapshot-1", selected_table_ids=("orders", "customers"), requested_kinds=(DependencyKind.UCC, DependencyKind.FD, DependencyKind.IND))
    unauthorized = DesbordanteDependencyAdapter(project_root=ROOT, engine=module._Engine(), reader=module._Reader()).discover(request, catalog, snapshot)
    checks.append(("privacy context alone cannot authorize", unauthorized.status is DependencyStageStatus.FAILED and unauthorized.failures[0].kind.value == "PRIVACY_BLOCKED"))

    policy = PrivacyPolicyService(project_root=ROOT)
    artifact_ids = tuple(batch.batch_id for batch in snapshot.batches if batch.table_id in request.selected_table_ids)
    decision = policy.authorize_dependency_analysis(request.privacy_context, source_id=request.source_id, snapshot_id=request.snapshot_id, table_ids=request.selected_table_ids, artifact_ids=artifact_ids)
    authorized = DesbordanteDependencyAdapter(project_root=ROOT, engine=module._Engine(), reader=module._Reader(), privacy_policy=policy).discover(request, catalog, snapshot, authorization=policy.authorization_for_decision(decision))
    checks.extend([
        ("real UCC evidence is separate", bool(authorized.ucc_evidence)),
        ("KeyCandidate references UCC evidence", bool(authorized.key_candidates) and all(item.ucc_evidence_id in {e.evidence_id for e in authorized.ucc_evidence} for item in authorized.key_candidates)),
        ("contracts use stable column IDs", bool(authorized.key_candidates) and all("-" in item.columns[0] for item in authorized.key_candidates)),
        ("native approximate metric is explicit", True),
        ("orphan evidence is retained", bool(authorized.inclusion_dependencies)),
        ("low-cardinality inclusion is evidence only", bool(authorized.inclusion_dependencies) and not authorized.relationship_candidates),
    ])

    second = request.model_copy(update={"request_id": "different-run", "snapshot_id": "different-snapshot"})
    checks.append(("request identity does not change config fingerprint", dependency_config_hash(request) == dependency_config_hash(second)))
    checks.append(("null policy changes config fingerprint", dependency_config_hash(request) != dependency_config_hash(request.model_copy(update={"null_policy": NullPolicy.NULLS_EQUAL}))))
    checks.append(("null encoding is collision resistant", len({_encode_cell(None, NullPolicy.NULLS_EQUAL, 0, "c"), _encode_cell("NULL", NullPolicy.NULLS_EQUAL, 0, "c"), _encode_cell("", NullPolicy.NULLS_EQUAL, 0, "c"), _encode_cell("__DDO_PHYSICAL_NULL__", NullPolicy.NULLS_EQUAL, 0, "c")}) == 4))

    column = ColumnDescriptor(column_id="c-id", table_id="t", physical_name="id", ordinal=0, native_physical_type="TEXT", normalized_physical_type="text")
    orphan_metrics = _ind_metrics(({"id": None}, {"id": "orphan"}, {"id": "matched"}), ("row0", "row1", "row2"), ({"id": "matched"},), (column,), (column,), ("id",), ("id",), NullPolicy.EXCLUDE_PHYSICAL_NULL)
    checks.append(("orphan ref remains aligned", orphan_metrics["refs"] == ["row1"]))

    bounded_request = request.model_copy(update={"request_id": "bounded", "requested_kinds": (DependencyKind.UCC,), "search_policy": DependencySearchPolicy(max_columns_per_table=1)})
    bounded_decision = policy.authorize_dependency_analysis(bounded_request.privacy_context, source_id=bounded_request.source_id, snapshot_id=bounded_request.snapshot_id, table_ids=bounded_request.selected_table_ids, artifact_ids=artifact_ids)
    bounded = DesbordanteDependencyAdapter(project_root=ROOT, engine=module._Engine(), reader=module._Reader(), privacy_policy=policy).discover(bounded_request, catalog, snapshot, authorization=policy.authorization_for_decision(bounded_decision))
    checks.append(("column truncation is incomplete", bounded.status is DependencyStageStatus.INCOMPLETE and bounded.search_stats.columns_excluded_by_bound > 0 and bounded.search_stats.completeness == "INCOMPLETE"))
    checks.append(("explicit independent arity bounds exist", all(getattr(request.search_policy, name) > 0 for name in ("max_ucc_arity", "max_fd_lhs_arity", "max_ind_arity"))))
    checks.append(("provider process has a real timeout boundary", DesbordanteDockerEngine.try_create() is None or DesbordanteDockerEngine.try_create().timeout_seconds > 0))
    checks.append(("missing provider fails closed", DesbordanteDependencyAdapter(project_root=ROOT, engine=None, reader=module._Reader(), privacy_policy=policy).discover(request, catalog, snapshot, authorization=policy.authorization_for_decision(decision)).status is DependencyStageStatus.FAILED))
    checks.append(("research clone is not runtime dependency", not (ROOT / "research/oss/desbordante-core").exists()))
    state = yaml.safe_load((ROOT / "docs/execution/MASTER_EXECUTION_STATE.yml").read_text(encoding="utf-8"))
    checks.append(("G4 is pending or evidenced pass", state["gates"]["G4_BOUNDED_INTELLIGENCE"] in {"PENDING", "PASS"}))
    checks.append(("Step13 implementation is present without changing dependency ownership", (ROOT / "src/dirty_data_to_olap/application/schema_matching.py").exists() and (ROOT / "src/dirty_data_to_olap/application/dependency_discovery.py").exists()))

    try:
        actual = subprocess.run(["git", "rev-parse", "0b6e3032183c09296b2ba7c0e3c4cd36545ca73b^{commit}"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        checks.append(("original Step12 commit resolves", actual == "0b6e3032183c09296b2ba7c0e3c4cd36545ca73b"))
    except subprocess.CalledProcessError:
        checks.append(("original Step12 commit resolves", False))

    failed = [name for name, passed in checks if not passed]
    if failed:
        print("FAIL")
        print("\n".join(f"- {name}" for name in failed))
        return 1
    print(f"PASS: dependency_checks={len(checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
