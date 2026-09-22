"""Execute Prompt02 fault-injection controls and retain machine-verifiable results.

The controls deliberately reuse the product's accepted boundaries rather than
manufacturing status rows in the four-source receipt.  A JSON row is emitted
only after its named pytest scenario passes, alongside a JUnit result that the
acceptance validator reads independently.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET
import argparse


ROOT = Path(__file__).resolve().parents[1]

CONTROLS = (
    ("NC01", "required source unavailable", "remove selected CSV after discovery", "SourceSnapshotService.extract", "SourceIngestionError:ACCESS_FAILED", "tests/chaos/test_step36_chaos.py::test_chaos_source_001_disappears_after_discovery"),
    ("NC02", "source or snapshot fingerprint changed", "change file fingerprint after discovery", "SourceSnapshotService.extract", "SourceIngestionError:SNAPSHOT_INVALID", "tests/integration/sources/test_step07_source_pipelines.py::test_file_snapshot_rejects_change_between_discovery_and_extraction"),
    ("NC03", "runtime has no oracle access", "deny test oracle reads while composing runtime", "build_multi_source_product", "oracle path was not read", "tests/product_acceptance/test_prompt02_control_boundaries.py::test_nc03_runtime_composition_does_not_read_acceptance_oracle"),
    ("NC04", "stale persisted review hash or revision", "replay accepted review at stale revision", "BackendService.review", "REVIEW_REVISION_CONFLICT:409", "tests/api/test_step27_backend.py::test_review_exact_binding_idempotency_concurrency_and_actor_boundary"),
    ("NC05", "missing source-record disposition", "tamper source-to-canonical accounting disposition", "ValidationService.validate", "ValidationStatus.FAIL:source_record_accounting", "tests/integration/test_step22_data_correctness_flow.py::test_step22_detects_runtime_accounting_canonical_oracle_and_binding_mutations"),
    ("NC06", "invalid or overlapping canonical identity", "use an ER membership outside evaluated population", "CanonicalIdentityProposalService", "CanonicalizationError:INCOMPATIBLE_ER_RESULT", "tests/integration/test_step19_canonical_flow.py::test_required_er_cannot_be_bypassed_by_human_override_and_graph_is_exactly_connected"),
    ("NC07", "pre-authored or cross-run artifact substitution", "request another run's artifact and tamper a registered artifact", "BackendService.artifact", "ARTIFACT_INTEGRITY_FAILED:409", "tests/api/test_step27_backend.py::test_artifacts_are_scoped_verified_and_payloads_are_not_generic"),
    ("NC08", "missing mandatory server-owned stage", "construct a plan with an unselected hard dependency", "ExecutionPlan", "ValueError:unselected hard dependency", "tests/integration/test_step28_execution_plan_review_repair.py::test_execution_plan_rejects_inconsistent_selection_and_success_guards"),
    ("NC09", "undeclared monetary measure", "relabel quantity as revenue without a compatible reviewed plan", "AnalyticalCompilerService.compile", "AnalyticalCompilationError:SPEC_PACKAGE_STALE", "tests/contract/test_step20_review_and_compile.py::test_compiler_rejects_missing_dimension_reference_and_measure_reclassification"),
    ("NC10", "incompatible source-set mutation", "attempt second binding after durable source-set binding", "BackendService.bind_product_source_set", "SOURCE_ALREADY_BOUND", "tests/product_acceptance/test_prompt02_control_boundaries.py::test_nc10_rejects_source_set_mutation_after_binding"),
    ("NC11", "selected-source omission or single-source fallback", "construct a single-source source set", "SourceSetSelection", "ValueError:multi-source selection rejection", "tests/unit/test_prompt02_contracts.py::test_source_set_rejects_single_source_and_stale_fingerprint"),
    ("NC12", "fact-grain multiplication", "duplicate a fact row at the declared grain", "ValidationService.validate", "ValidationStatus.FAIL:fact grain", "tests/integration/test_step22_data_correctness_flow.py::test_adversarial_controls_detect_same_totals_count_and_fk_traps"),
    ("NC13", "hard-negative identity merge", "force a false canonical customer merge", "ValidationService.validate", "ValidationStatus.FAIL:canonical_membership", "tests/integration/test_step22_data_correctness_flow.py::test_step22_detects_runtime_accounting_canonical_oracle_and_binding_mutations"),
    ("NC14", "required human review omitted", "attempt downstream advancement with unresolved review", "JobWorker review checkpoint", "NEEDS_REVIEW and downstream job absent", "tests/integration/test_step28_execution_plan_review_repair.py::test_real_evidence_checkpoint_pauses_then_resumes_guarded_downstream"),
)


def _junit_passed(path: Path) -> bool:
    root = ET.parse(path).getroot()
    suites = (root,) if root.tag == "testsuite" else tuple(root.findall("testsuite"))
    return bool(suites) and all(
        int(suite.attrib.get("tests", "0")) == 1
        and int(suite.attrib.get("failures", "0")) == 0
        and int(suite.attrib.get("errors", "0")) == 0
        and int(suite.attrib.get("skipped", "0")) == 0
        for suite in suites
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--controls", help="comma-separated control IDs; defaults to NC01-NC14")
    args = parser.parse_args()
    requested = None if not args.controls else {item.strip() for item in args.controls.split(",") if item.strip()}
    if requested is not None and not requested.issubset({item[0] for item in CONTROLS}):
        parser.error("--controls contains an unknown Prompt02 control ID")
    evidence_dir = Path(os.environ.get("DDO_PROMPT02_EVIDENCE_DIR", ROOT / "workspace" / "tests" / "prompt02-negative-controls")).resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    failed = False
    for control_id, requirement, fault, boundary, expected, node in CONTROLS:
        if requested is not None and control_id not in requested:
            continue
        junit_name = f"PROMPT02_NEGATIVE_CONTROL_{control_id}.junit.xml"
        junit_path = evidence_dir / junit_name
        result = subprocess.run(
            [sys.executable, "-m", "pytest", node, "-q", f"--junitxml={junit_path}"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        passed = result.returncode == 0 and junit_path.is_file() and _junit_passed(junit_path)
        failed = failed or not passed
        rows.append({
            "control_id": control_id,
            "requirement": requirement,
            "injected_fault": fault,
            "execution_boundary": boundary,
            "expected_rejection": expected,
            "actual_rejection": expected if passed else "pytest control did not establish the expected rejection",
            "durable_evidence": (f"junit:{junit_name}", f"pytest:{node}"),
            "implementation_status": "IMPLEMENTED",
            "execution_status": "PASS" if passed else "BLOCKED",
            "acceptance_status": "PASS" if passed else "PENDING",
            "test_node": node,
            "pytest_return_code": result.returncode,
        })
    payload = {"schema_version": "prompt02-negative-controls-v1", "controls": rows}
    (evidence_dir / "PROMPT02_NEGATIVE_CONTROLS.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    if failed:
        print("PROMPT02_NEGATIVE_CONTROLS=BLOCKED")
        return 1
    print("PROMPT02_NEGATIVE_CONTROLS=PASS")
    print(f"controls={len(rows)}/{len(CONTROLS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
