"""Executable Step12 dependency-discovery contract and safety validator."""

from __future__ import annotations

import json
import importlib.util
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


def main() -> int:
    from dirty_data_to_olap.adapters.dependencies.desbordante import DesbordanteDependencyAdapter, DesbordantePythonEngine
    from dirty_data_to_olap.application.privacy_policy import PrivacyPolicyService
    from dirty_data_to_olap.domain.contracts.dependency import DependencyCapabilityStatus, DependencyKind, DependencyRequest, DependencyStageStatus
    from dirty_data_to_olap.domain.contracts.privacy import ExposureContext
    fixture_spec = importlib.util.spec_from_file_location("step12_test_fixture", ROOT / "tests" / "unit" / "test_dependency_discovery.py")
    fixture_module = importlib.util.module_from_spec(fixture_spec)
    assert fixture_spec.loader is not None
    fixture_spec.loader.exec_module(fixture_module)
    _Engine, _Reader, _fixture = fixture_module._Engine, fixture_module._Reader, fixture_module._fixture

    checks: list[tuple[str, bool]] = []
    required_docs = (
        ROOT / "docs" / "dependencies" / "ALGORITHM_AND_BOUNDARY_POLICY.md",
        ROOT / "docs" / "execution" / "STEP12_DEPENDENCY_DISCOVERY_REVIEW.md",
    )
    checks.append(("Step12 docs exist", all(path.is_file() for path in required_docs)))
    checks.append(("host capability is explicit", DesbordantePythonEngine.try_create() is None or DesbordantePythonEngine.try_create().name == "desbordante"))

    context = DependencyRequest(request_id="validator", source_id="s", snapshot_id="x", selected_table_ids=("t",)).privacy_context
    privacy = PrivacyPolicyService(project_root=ROOT)
    decision = privacy.authorize_dependency_analysis(context)
    checks.append(("dependency privacy context is authorized locally", decision.allowed and decision.required_transformation == "aggregate_project_owned_evidence"))
    blocked = context.model_copy(update={"network_allowed": True})
    checks.append(("network-enabled dependency context is rejected", not privacy.authorize_dependency_analysis(blocked).allowed))
    checks.append(("exposure enum names local boundary", ExposureContext.DEPENDENCY_LOCAL_ANALYSIS.value == "DEPENDENCY_LOCAL_ANALYSIS"))

    catalog, snapshot = _fixture()
    request = DependencyRequest(request_id="dependency-validator", source_id="source-1", snapshot_id="snapshot-1", selected_table_ids=("orders", "customers"), requested_kinds=(DependencyKind.UCC, DependencyKind.FD, DependencyKind.IND))
    root = ROOT / "tests" / "dependency_validator_artifacts"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    try:
        result = DesbordanteDependencyAdapter(project_root=root, engine=_Engine(), reader=_Reader()).discover(request, catalog, snapshot, artifact_root=root / "run")
        checks.append(("known dependency fixture produces complete evidence", result.status is DependencyStageStatus.COMPLETE and result.key_candidates and result.functional_dependencies and result.inclusion_dependencies))
        checks.append(("low-cardinality relationship trap is rejected", result.inclusion_dependencies[0].low_cardinality_risk and not result.relationship_candidates))
        serialized = json.dumps(result.model_dump(mode="json"), sort_keys=True)
        checks.append(("result contains no fixture raw values", all(value not in serialized for value in ("o1", "north", "good@example.com"))))
        checks.append(("ephemeral engine input is cleaned", not (root / "privacy_ephemeral" / "dependency_discovery" / request.request_id).exists()))
        checks.append(("published artifacts are hash-addressed references", bool(result.artifacts) and all((root / item.artifact_location).is_file() and len(item.content_hash) == 64 for item in result.artifacts)))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    unavailable_root = ROOT / "tests" / "dependency_capability_artifacts"
    shutil.rmtree(unavailable_root, ignore_errors=True)
    unavailable_root.mkdir(parents=True)
    try:
        unavailable = DesbordanteDependencyAdapter(project_root=unavailable_root, engine=None, reader=_Reader()).discover(request, catalog, snapshot)
        checks.append(("missing mature engine fails closed", unavailable.status is DependencyStageStatus.FAILED and unavailable.capabilities[0].status is DependencyCapabilityStatus.UNAVAILABLE))
    finally:
        shutil.rmtree(unavailable_root, ignore_errors=True)

    failed = [name for name, passed in checks if not passed]
    if failed:
        print("FAIL")
        print("\n".join(f"- {name}" for name in failed))
        return 1
    print(f"PASS: dependency_checks={len(checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
