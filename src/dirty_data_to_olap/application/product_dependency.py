"""Product-scoped interpretation of actual dependency provider evidence."""

from __future__ import annotations

from dirty_data_to_olap.domain.contracts.dependency import DependencyResult, RelationshipCandidate
from dirty_data_to_olap.domain.contracts.source import SourceCatalog, stable_id


def bind_source_local_identity_candidate(
    result: DependencyResult,
    *,
    catalog: SourceCatalog,
    table_name: str,
    column_name: str,
) -> DependencyResult:
    """Project an observed provider UCC into the explicit V1 identity relation.

    The file product has one source table, so it has no cross-table IND.  The
    product policy explicitly treats a provider-observed unique order key as a
    source-local event identity relation.  No source values are compared here;
    the provider result remains the only evidence input.
    """

    table = next(item for item in catalog.tables if item.physical_name == table_name)
    column = next(item for item in catalog.columns if item.table_id == table.table_id and item.physical_name == column_name)
    ucc = next((item for item in result.ucc_evidence if item.table_id == table.table_id and item.column_ids == (column.column_id,) and item.uniqueness_ratio >= 1.0), None)
    if ucc is None:
        return result.model_copy(update={"relationship_candidates": ()})
    candidate = RelationshipCandidate(
        candidate_id=stable_id("rel", {"dependency": ucc.evidence_id, "projection": "SOURCE_LOCAL_UCC_IDENTITY"}),
        source_id=result.request.source_id,
        snapshot_id=result.request.snapshot_id,
        from_table=table.physical_name,
        from_columns=(column_name,),
        to_table=table.physical_name,
        to_columns=(column_name,),
        proposed_cardinality="MANY_TO_ONE",
        source_orphan_ratio=0.0,
        target_uniqueness_ratio=ucc.uniqueness_ratio,
        type_compatible=True,
        low_cardinality_risk=False,
        evidence_refs=(ucc.evidence_id,),
        state="CANDIDATE",
        final_acceptance_allowed=False,
    )
    return result.model_copy(update={"relationship_candidates": (candidate,)})
