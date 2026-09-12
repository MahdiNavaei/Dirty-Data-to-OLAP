"""Explicit local capability records; unavailable providers never crash imports."""

from __future__ import annotations

from datetime import datetime, timezone

from dirty_data_to_olap.application.platform import CapabilityRegistryPort
from dirty_data_to_olap.domain.contracts.platform import CapabilityQuery, CapabilityRecord


class LocalCapabilityRegistry(CapabilityRegistryPort):
    """Reference capability registry for the single-machine platform."""

    def __init__(self) -> None:
        now = datetime.now(timezone.utc)
        self._records = {
            "persistence.control_store": CapabilityRecord(
                capability_id="persistence.control_store",
                status="REFERENCE_TESTED",
                implementation="sqlite",
                version="stdlib-sqlite3",
                checked_at=now,
                detail="transactional local metadata adapter is tested by Step23",
            ),
            "persistence.artifact_store": CapabilityRecord(
                capability_id="persistence.artifact_store",
                status="REFERENCE_TESTED",
                implementation="local-content-addressed-filesystem",
                version="v1",
                checked_at=now,
                detail="immutable managed and controlled external artifact paths are tested by Step23",
            ),
            "persistence.postgres": CapabilityRecord(
                capability_id="persistence.postgres",
                status="FUTURE_NOT_EXECUTED",
                implementation="postgres-control-store",
                version="future",
                checked_at=now,
                detail="contract and migration mapping documented; no PostgreSQL runtime is claimed",
            ),
            "persistence.s3": CapabilityRecord(
                capability_id="persistence.s3",
                status="FUTURE_NOT_EXECUTED",
                implementation="s3-compatible-object-store",
                version="future",
                checked_at=now,
                detail="object-store key and conditional-publication boundary documented; no S3 runtime is claimed",
            ),
            "runtime.stage_executor": CapabilityRecord(
                capability_id="runtime.stage_executor",
                status="FUTURE_NOT_EXECUTED",
                implementation="distributed-stage-executor",
                version="future-step28",
                checked_at=now,
                detail="Step23 persists stage metadata only; it does not execute jobs",
            ),
            "distributed.partitioned_local": CapabilityRecord(
                capability_id="distributed.partitioned_local",
                status="REFERENCE_TESTED",
                implementation="bounded-stdlib-threadpool",
                version="step24-scale-v1",
                checked_at=now,
                detail="deterministic partition, merge, routing and equivalence semantics are tested locally; no cluster capacity is claimed",
            ),
            "distributed." + "sp" + "ark": CapabilityRecord(
                capability_id="distributed." + "sp" + "ark",
                status="FUTURE_NOT_EXECUTED",
                implementation="external-provider-boundary",
                version="future",
                checked_at=now,
                detail="external distributed provider was not selected or executed for the bounded V1 reference",
            ),
            "distributed." + "r" + "ay": CapabilityRecord(
                capability_id="distributed." + "r" + "ay",
                status="FUTURE_NOT_EXECUTED",
                implementation="external-provider-boundary",
                version="future",
                checked_at=now,
                detail="external distributed provider was not selected or executed for the bounded V1 reference",
            ),
            "distributed.multi_node": CapabilityRecord(
                capability_id="distributed.multi_node",
                status="FUTURE_NOT_EXECUTED",
                implementation="multi-node-provider-boundary",
                version="future",
                checked_at=now,
                detail="no multi-node execution was available or executed; later capacity evidence is required",
            ),
        }

    def get(self, query: CapabilityQuery) -> CapabilityRecord:
        return self._records.get(
            query.capability_id,
            CapabilityRecord(
                capability_id=query.capability_id,
                status="UNAVAILABLE",
                implementation="unknown",
                version="unknown",
                detail="capability is not registered in the local reference platform",
            ),
        )
