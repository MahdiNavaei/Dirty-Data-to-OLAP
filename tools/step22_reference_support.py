"""Step22 QA wiring kept separate from the transformation fixture."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dirty_data_to_olap.application.semantic_layer import SemanticLayerService
from dirty_data_to_olap.application.validation import ValidationInputs
from dirty_data_to_olap.domain.contracts.semantic import SemanticValidationResult
from dirty_data_to_olap.domain.contracts.source import stable_digest, stable_id
from dirty_data_to_olap.domain.contracts.validation import (
    RecordDisposition,
    SourceTruthManifest,
    ValidationArtifactBindings,
    ValidationPolicy,
    ValidationStatus,
    validation_policy_id,
)
from tools.step21_reference_support import Step20Context
from tools.step22_transformation_support import build_transformation_context


VALIDATION_RUN_ID = "step22-reference-run"
POLICY_VERSION = "step22-data-correctness-v2"
REQUIRED_CHECK_IDS = (
    "artifact_binding",
    "source_snapshot_universe",
    "source_record_accounting",
    "canonical_membership",
    "dedup_explainability",
    "canonical_entity_counts",
    "canonical_event_relationships",
    "canonical_to_analytical_accounting",
    "analytical_input_binding",
    "relationship_allocation",
    "target_table_set",
    "fact_count",
    "fact_grain",
    "fact_duplicates",
    "fact_business_values",
    "foreign_key_integrity",
    "key_uniqueness",
    "date_coverage",
    "quantity_global",
    "quantity_slices",
    "lineage_source_to_target",
    "lineage_target_to_source",
    "semantic_downstream_cross_check",
    "no_blocking_discrepancy",
)


@dataclass(frozen=True)
class ReferenceValidationContext:
    name: str
    context: Step20Context
    truth: SourceTruthManifest
    inputs: ValidationInputs


def load_truth(name: str) -> SourceTruthManifest:
    path = ROOT / "benchmarks" / "validation" / f"step22_{name}_source_truth.json"
    return SourceTruthManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _semantic_artifacts(context: Step20Context, name: str):
    service = SemanticLayerService()
    additional = ("step22:retail-semantic-validation",) if name == "retail" else ("step22:generic-semantic-validation",)
    model = service.build_model(
        context.plan,
        context.dimensions,
        context.facts,
        context.grains,
        context.measures,
        context.compiled_plan,
        context.materialization,
        context.canonical_model,
        analytical_review=context.analytical_review,
        unresolved_semantic_items=("metric:revenue:NOT_DEFINED_BY_REVIEWED_EVIDENCE",) if name == "retail" else (),
        additional_provenance_refs=additional,
    )
    validation = service.validation_result(model, [("exact_upstream_binding", True, "semantic model remains bound to exact Step20 artifacts")])
    assert isinstance(validation, SemanticValidationResult)
    return model, validation


def build_policy() -> ValidationPolicy:
    payload = {
        "version": POLICY_VERSION,
        "required_check_ids": REQUIRED_CHECK_IDS,
        "orphan_policy": {"required_fk": "FAIL", "unknown_member": "reviewed_dimension_policy"},
        "monetary": "NOT_APPLICABLE_NO_CONTRACT",
    }
    return ValidationPolicy(
        policy_id=validation_policy_id(payload),
        policy_version=POLICY_VERSION,
        required_check_ids=REQUIRED_CHECK_IDS,
        allowed_terminal_dispositions=tuple(RecordDisposition),
        orphan_policy=payload["orphan_policy"],
        require_bidirectional_lineage=True,
        exact_numeric_comparison=True,
        monetary_status=ValidationStatus.NOT_APPLICABLE,
        monetary_reason="NOT_APPLICABLE: the reviewed domain contract establishes no currency, amount, revenue or settlement authority",
        deferred_concepts={"payment": "explicitly deferred by the reviewed Step20 analytical plan"},
        provenance_refs=("step22:policy", "domain-reviewed:monetary-not-defined"),
    )


def build_reference_context(name: str) -> ReferenceValidationContext:
    # The transformation branch is completed before the QA-only oracle is
    # loaded.  It cannot import or inspect benchmarks/validation files.
    context, accounting, _source = build_transformation_context(name)
    truth = load_truth(name)
    semantic_model, semantic_validation = _semantic_artifacts(context, name)
    policy = build_policy()
    bindings = ValidationArtifactBindings(
        source_snapshot_id=truth.source_snapshot_id,
        source_snapshot_hash=truth.source_snapshot_fingerprint,
        source_truth_id=truth.truth_id,
        source_truth_content_hash=truth.content_hash,
        canonical_model_id=context.canonical_model.model_id,
        canonical_model_content_hash=context.canonical_model.content_hash,
        record_accounting_id=accounting.accounting_id,
        record_accounting_content_hash=accounting.content_hash,
        analytical_plan_id=context.plan.plan_id,
        analytical_plan_content_hash=context.plan.content_hash,
        analytical_spec_package_hash=context.plan.analytical_spec_package_hash,
        analytical_dataset_id=context.dataset.dataset_id,
        analytical_dataset_content_hash=context.dataset.content_hash,
        analytical_input_binding_id=context.binding.binding_id,
        analytical_input_binding_content_hash=context.binding.content_hash,
        analytical_input_source_snapshot_fingerprints=dict(sorted(context.binding.source_snapshot_fingerprints.items())),
        compiled_plan_id=context.compiled_plan.compiled_plan_id,
        compiled_plan_content_hash=context.compiled_plan.content_hash,
        materialization_artifact_id=context.materialization.artifact_id,
        materialization_artifact_content_hash=context.materialization.content_hash,
        target_relative_path=context.materialization.target_relative_path,
        target_config_fingerprint=context.materialization.target_config_fingerprint,
        target_file_sha256=context.materialization.target_file_sha256 or "",
        semantic_model_id=semantic_model.semantic_model_id,
        semantic_model_content_hash=semantic_model.content_hash,
        semantic_validation_id=semantic_validation.validation_id,
        semantic_validation_content_hash=stable_digest(semantic_validation.model_dump(mode="json")),
        validation_policy_id=policy.policy_id,
        validation_policy_version=policy.policy_version,
        benchmark_truth_hash=truth.content_hash,
    )
    inputs = ValidationInputs(
        source_truth=truth,
        canonical_model=context.canonical_model,
        accounting=accounting,
        analytical_dataset=context.dataset,
        analytical_input_binding=context.binding,
        plan=context.plan,
        compiled_plan=context.compiled_plan,
        materialization=context.materialization,
        semantic_model=semantic_model,
        semantic_validation=semantic_validation,
        policy=policy,
        bindings=bindings,
    )
    return ReferenceValidationContext(name=name, context=context, truth=truth, inputs=inputs)

