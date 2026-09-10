"""Leakage-safe feature construction from project-owned evidence contracts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from dirty_data_to_olap.domain.contracts.applied_ml import (
    MLFeatureDefinition,
    MLFeatureFamily,
    MLFeatureSchema,
    MLFeatureSourceReference,
    MLFeatureVector,
    MLTask,
)


def _defs() -> tuple[MLFeatureDefinition, ...]:
    specs = (
        ("declared_fk_present", MLFeatureFamily.DECLARED_METADATA, "declared constraint evidence; absence is unobserved unless metadata explicitly says false", 0.0, 1.0, "declared metadata contract"),
        ("inclusion_coverage", MLFeatureFamily.DEPENDENCY, "no evaluated inclusion evidence is missing, not zero", 0.0, 1.0, "InclusionDependencyEvidence.coverage_ratio"),
        ("ind_unmatched_ratio", MLFeatureFamily.DEPENDENCY, "no evaluated inclusion evidence is missing, not zero", 0.0, 1.0, "InclusionDependencyEvidence.violation_ratio"),
        ("target_uniqueness_ratio", MLFeatureFamily.DEPENDENCY, "candidate measured value", 0.0, 1.0, "RelationshipCandidate.target_uniqueness_ratio"),
        ("dependency_exact_scope", MLFeatureFamily.SCOPE, "scope indicator from dependency evidence", 0.0, 1.0, "DependencyObservationScope"),
        ("dependency_bounded_scope", MLFeatureFamily.SCOPE, "bounded/truncated dependency observation indicator", 0.0, 1.0, "DependencyObservationScope"),
        ("type_compatible", MLFeatureFamily.DEPENDENCY, "explicit project compatibility flag", 0.0, 1.0, "RelationshipCandidate.type_compatible"),
        ("low_cardinality_risk", MLFeatureFamily.DEPENDENCY, "explicit low-cardinality risk flag", 0.0, 1.0, "RelationshipCandidate.low_cardinality_risk"),
        ("matcher_support_count", MLFeatureFamily.SCHEMA_MATCH, "number of independent matcher scores", 0.0, None, "SchemaMatchScore"),
        ("matcher_disagreement_flag", MLFeatureFamily.SCHEMA_MATCH, "matcher score disagreement over observed matcher values", 0.0, 1.0, "SchemaMatchScore"),
        ("schema_instance_evidence_present", MLFeatureFamily.SCHEMA_MATCH, "instance-aware signal presence", 0.0, 1.0, "SchemaMatchSignal"),
        ("schema_sample_only", MLFeatureFamily.SCOPE, "schema observation was reduced/sample scope", 0.0, 1.0, "SchemaMatchObservationScope"),
        ("source_missing_fraction", MLFeatureFamily.PROFILE, "missing profile is explicit, not zero missing fraction", 0.0, 1.0, "ColumnProfile observed missing counts"),
        ("target_missing_fraction", MLFeatureFamily.PROFILE, "missing profile is explicit, not zero missing fraction", 0.0, 1.0, "ColumnProfile observed missing counts"),
        ("source_distinct_ratio", MLFeatureFamily.PROFILE, "missing profile is explicit, not zero distinct ratio", 0.0, 1.0, "ColumnProfile.observed_distinct_ratio"),
        ("target_distinct_ratio", MLFeatureFamily.PROFILE, "missing profile is explicit, not zero distinct ratio", 0.0, 1.0, "ColumnProfile.observed_distinct_ratio"),
        ("referential_quality_issue_present", MLFeatureFamily.QUALITY, "quality issue evidence for the candidate columns", 0.0, 1.0, "QualityIssue"),
        ("inconclusive_quality_scope", MLFeatureFamily.QUALITY, "inconclusive/missing quality evidence", 0.0, 1.0, "QualityResult"),
    )
    output = []
    for feature_id, family, missing, minimum, maximum, source in specs:
        output.append(MLFeatureDefinition(feature_id=feature_id, family=family, missing_semantics=missing, minimum=minimum, maximum=maximum, source_contract=source))
    # Matcher families are independent features.  The IDs are the project-owned
    # default matcher IDs; custom matcher IDs are added by callers in a future
    # schema version rather than being collapsed into a cross-matcher statistic.
    for matcher_id in ("valentine-coma-schema-v1", "valentine-cupid-schema-v1"):
        safe = matcher_id.replace("-", "_")
        for suffix, minimum, maximum in (("score", None, None), ("rank", 0.0, None)):
            output.append(MLFeatureDefinition(feature_id=f"matcher_{safe}_{suffix}", family=MLFeatureFamily.SCHEMA_MATCH, missing_semantics=f"{matcher_id} {suffix} is missing, not zero", minimum=minimum, maximum=maximum, source_contract=f"SchemaMatchScore[{matcher_id}].{suffix}"))
            output.append(MLFeatureDefinition(feature_id=f"matcher_{safe}_{suffix}_missing", family=MLFeatureFamily.SCHEMA_MATCH, missing_semantics=f"explicit missing indicator for {matcher_id} {suffix}", minimum=0.0, maximum=1.0, nullable=False, source_contract=f"SchemaMatchScore[{matcher_id}].{suffix}"))
    for feature_id, family, missing, minimum, maximum, source in specs:
        if feature_id not in {"matcher_support_count", "matcher_disagreement_flag"}:
            output.append(MLFeatureDefinition(feature_id=f"{feature_id}_missing", family=family, missing_semantics=f"explicit missing indicator for {feature_id}", minimum=0.0, maximum=1.0, nullable=False, source_contract=source))
    return tuple(output)


def default_feature_schema() -> MLFeatureSchema:
    definitions = _defs()
    return MLFeatureSchema(
        schema_id="relationship-ranker-features-v2",
        version="2",
        task=MLTask.RELATIONSHIP_CANDIDATE_RANKING,
        features=definitions,
        feature_order=tuple(item.feature_id for item in definitions),
    )


def _items(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        return tuple(value.values())
    if isinstance(value, (tuple, list, set)):
        return tuple(value)
    return (value,)


def _column_profile(profiles: Any, column: str) -> Any | None:
    for profile in _items(profiles):
        if getattr(profile, "column_id", None) == column or str(getattr(profile, "column_id", "")).endswith("." + column) or str(getattr(profile, "column_id", "")).endswith("-" + column):
            return profile
    return None


def _dependency_evidence(dependencies: Any, candidate: Any) -> tuple[Any, ...]:
    left = set(getattr(candidate, "from_columns", ()))
    right = set(getattr(candidate, "to_columns", ()))
    output = []
    for result in _items(dependencies):
        for item in getattr(result, "inclusion_dependencies", ()):
            if set(getattr(item, "left_columns", ())) == left and set(getattr(item, "right_columns", ())) == right:
                output.append(item)
            elif set(getattr(item, "left_columns", ())) == right and set(getattr(item, "right_columns", ())) == left:
                output.append(item)
    return tuple(output)


def _schema_scores(schema_candidates: Any, schema_scores: Any, candidate: Any) -> tuple[Any, ...]:
    left = set(getattr(candidate, "from_columns", ()))
    right = set(getattr(candidate, "to_columns", ()))
    candidate_ids = set()
    for item in _items(schema_candidates):
        if {getattr(item, "source_column_id", ""), getattr(item, "target_column_id", "")} & left and {getattr(item, "source_column_id", ""), getattr(item, "target_column_id", "")} & right:
            candidate_ids.add(getattr(item, "candidate_id", ""))
    scores = []
    for score in _items(schema_scores):
        if candidate_ids and getattr(score, "score_id", "") in {ref for item in _items(schema_candidates) for ref in getattr(item, "score_refs", ())}:
            scores.append(score)
        elif {getattr(score, "source_column_id", ""), getattr(score, "target_column_id", "")} & left and {getattr(score, "source_column_id", ""), getattr(score, "target_column_id", "")} & right:
            scores.append(score)
    return tuple(scores)


def _set(values: dict[str, float], missing: list[str], feature_id: str, value: float | None) -> None:
    if value is None:
        values[feature_id] = 0.0
        missing.append(feature_id)
    else:
        values[feature_id] = float(value)


def build_feature_vector(
    candidate: Any,
    *,
    schema_candidates: Any = None,
    schema_scores: Any = None,
    schema_signals: Any = None,
    profiles: Any = None,
    quality: Any = None,
    dependencies: Any = None,
    declared_fk_present: bool | None = None,
) -> MLFeatureVector:
    """Build only aggregate/project-contract features; never reads staged rows."""

    values: dict[str, float] = {}
    missing: list[str] = []
    refs: list[MLFeatureSourceReference] = []
    risk_flags: list[str] = []
    dep_items = _dependency_evidence(dependencies, candidate)
    dep = dep_items[0] if dep_items else None
    _set(values, missing, "declared_fk_present", None if declared_fk_present is None else float(declared_fk_present))
    _set(values, missing, "inclusion_coverage", getattr(dep, "coverage_ratio", None))
    _set(values, missing, "ind_unmatched_ratio", getattr(dep, "violation_ratio", None))
    _set(values, missing, "target_uniqueness_ratio", getattr(candidate, "target_uniqueness_ratio", None))
    _set(values, missing, "dependency_exact_scope", 1.0 if dep is not None and str(getattr(getattr(dep, "observation_scope", None), "mode", "")).upper().endswith("FULL") else None)
    bounded = getattr(getattr(dep, "observation_scope", None), "provider_scope_semantics", "") if dep is not None else None
    _set(values, missing, "dependency_bounded_scope", None if dep is None else float("TRUNCATED" in str(getattr(dep, "state", "")) or "bounded" in str(bounded).lower()))
    _set(values, missing, "type_compatible", float(bool(getattr(candidate, "type_compatible", False))))
    _set(values, missing, "low_cardinality_risk", float(bool(getattr(candidate, "low_cardinality_risk", False))))
    if not getattr(candidate, "type_compatible", True):
        risk_flags.append("hard_type_conflict")
    if getattr(candidate, "low_cardinality_risk", False):
        risk_flags.append("low_cardinality_risk")
    if dep is not None:
        refs.append(MLFeatureSourceReference(artifact_id=getattr(dep, "evidence_id", "dependency-evidence"), artifact_type="InclusionDependencyEvidence", evidence_family="DEPENDENCY", scope_semantics=str(getattr(getattr(dep, "observation_scope", None), "mode", "unknown"))))
    scores = _schema_scores(schema_candidates, schema_scores, candidate)
    by_matcher: dict[str, Any] = {}
    for score in scores:
        by_matcher[str(getattr(getattr(score, "matcher", None), "matcher_id", "matcher"))] = score
    _set(values, missing, "matcher_support_count", float(len(by_matcher)) if scores else None)
    # Disagreement is deliberately scale-independent: compare rank order and
    # top-K membership, never raw score subtraction across matcher families.
    ranks = [int(getattr(score, "rank", 0)) for score in by_matcher.values()]
    _set(values, missing, "matcher_disagreement_flag", None if not ranks else float(len(set(ranks)) > 1))
    for matcher_id in ("valentine-coma-schema-v1", "valentine-cupid-schema-v1"):
        safe = matcher_id.replace("-", "_")
        score = by_matcher.get(matcher_id)
        _set(values, missing, f"matcher_{safe}_score", getattr(score, "raw_native_score", None))
        _set(values, missing, f"matcher_{safe}_rank", getattr(score, "rank", None))
    for score in by_matcher.values():
        refs.append(MLFeatureSourceReference(artifact_id=str(getattr(score, "score_id", "schema-score")), artifact_type="SchemaMatchScore", evidence_family="SCHEMA_MATCH", scope_semantics=str(getattr(getattr(score, "observation_scope", None), "sample_mode", "unknown"))))
    instance = any(str(getattr(getattr(signal, "family", ""), "value", getattr(signal, "family", ""))) == "INSTANCE" for signal in _items(schema_signals))
    _set(values, missing, "schema_instance_evidence_present", float(instance) if schema_signals is not None else None)
    schema_scope = getattr(next(iter(_items(schema_candidates)), None), "observation_scope", None)
    _set(values, missing, "schema_sample_only", None if schema_scope is None else float(bool(getattr(schema_scope, "reduced_scope", False) or "SAMPLE" in str(getattr(schema_scope, "sample_mode", "")).upper())))
    source_profile = _column_profile(profiles, (getattr(candidate, "from_columns", ()) or (None,))[0])
    target_profile = _column_profile(profiles, (getattr(candidate, "to_columns", ()) or (None,))[0])
    for feature_id, profile in (("source_missing_fraction", source_profile), ("target_missing_fraction", target_profile)):
        _set(values, missing, feature_id, None if profile is None or not getattr(profile, "rows_observed", 0) else (getattr(profile, "physical_null_count", 0) + getattr(profile, "configured_null_marker_count", 0)) / getattr(profile, "rows_observed", 1))
    _set(values, missing, "source_distinct_ratio", getattr(source_profile, "observed_distinct_ratio", None))
    _set(values, missing, "target_distinct_ratio", getattr(target_profile, "observed_distinct_ratio", None))
    issues = _items(getattr(quality, "issues", None))
    issue_columns = set(getattr(candidate, "from_columns", ())) | set(getattr(candidate, "to_columns", ()))
    issue_present = any(issue_columns.intersection(getattr(issue, "column_ids", ())) for issue in issues)
    _set(values, missing, "referential_quality_issue_present", float(issue_present) if quality is not None else None)
    _set(values, missing, "inconclusive_quality_scope", None if quality is None else float(bool(getattr(quality, "failures", ()))) )
    for feature_id in default_feature_schema().feature_order:
        if feature_id.endswith("_missing"):
            base = feature_id[:-8]
            values[feature_id] = 1.0 if base in missing else 0.0
    if issue_present:
        risk_flags.append("quality_issue_evidence")
    scope = {"dependency": "observed" if dep is not None else "unobserved", "schema": "observed" if scores else "unobserved", "quality": "observed" if quality is not None else "unobserved"}
    logical_parts = (
        str(getattr(candidate, "from_table", "")),
        *(str(value) for value in getattr(candidate, "from_columns", ())),
        str(getattr(candidate, "to_table", "")),
        *(str(value) for value in getattr(candidate, "to_columns", ())),
    )
    logical = "|".join(sorted(logical_parts))
    return MLFeatureVector(candidate_id=str(candidate.candidate_id), logical_pair_key=logical, values=values, missing_feature_ids=tuple(sorted(set(missing))), feature_source_refs=tuple(refs), scope_evidence=scope, risk_flags=tuple(sorted(set(risk_flags))))
