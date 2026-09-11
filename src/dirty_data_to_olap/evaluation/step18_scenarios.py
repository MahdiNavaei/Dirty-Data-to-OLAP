"""Single deterministic source for the Step18 relationship scenarios."""

from __future__ import annotations

from typing import Mapping

from dirty_data_to_olap.domain.contracts.source import stable_digest


def relationship_tables(scenario: Mapping[str, object]) -> dict[str, tuple[dict[str, object], ...]]:
    """Build the exact scenario tables consumed by every relationship stage."""

    kind = str(scenario["kind"])
    count = int(scenario.get("row_count", 200 if kind.startswith("orphan") else 100))
    source: list[dict[str, object]] = []
    customers: list[dict[str, object]] = []
    for index in range(count):
        key = f"customer-{index}"
        source.append({"row_id": f"order-{index}", "customer_id": key, "region_code": f"r-{index % 5}", "tenant_id": f"t-{index % 3}"})
        customers.append({"customer_id": key, "region_code": f"r-{index % 5}", "tenant_id": f"t-{index % 3}", "label": f"label-{index}"})

    if kind == "orphan":
        orphan_rate = float(scenario["orphan_rate"])
        orphan_count = round(count * orphan_rate)
        for row in source[-orphan_count:] if orphan_count else ():
            row["customer_id"] = f"orphan-{row['row_id']}"
    elif kind == "duplicate_target":
        customers.append(dict(customers[0]))
    elif kind == "null_heavy":
        for row in source[: int(count * 0.85)]:
            row["customer_id"] = None
    elif kind == "type_mismatch":
        # Preserve the true endpoint and add a separate incompatible lexical trap.
        for row in source:
            row["customer_id_numeric"] = int(str(row["customer_id"]).split("-")[-1])
        for row in customers:
            row["customer_id_numeric"] = f"numeric-{str(row['customer_id']).split('-')[-1]}"
    elif kind == "low_cardinality":
        # Keep the true FK; the low-cardinality region code is the trap.
        pass
    elif kind == "same_domain":
        # Keep the true FK; order_ref is a same-domain unrelated trap.
        for index, row in enumerate(source):
            row["order_ref"] = f"order-{index}"
        for index, row in enumerate(customers):
            row["order_ref"] = f"order-{index}"
    elif kind == "composite":
        pass
    elif kind == "partial_composite":
        for row in source:
            row["tenant_id"] = "t-constant"
    elif kind == "missing_candidate":
        for row in source:
            row["customer_id"] = f"hidden-{row['row_id']}"
    elif kind == "multiple_target":
        legacy = tuple({**row, "label": "legacy"} for row in customers)
        return {"orders": tuple(source), "customers": tuple(customers), "legacy_customers": legacy}

    if kind in {"composite", "partial_composite"}:
        source = [{"row_id": row["row_id"], "tenant_id": row["tenant_id"], "customer_id": row["customer_id"]} for row in source]
        customers = [{"tenant_id": row["tenant_id"], "customer_id": row["customer_id"], "label": row["label"]} for row in customers]
    return {"orders": tuple(source), "customers": tuple(customers)}


def scenario_content_fingerprint(scenario: Mapping[str, object]) -> str:
    tables = relationship_tables(scenario)
    return stable_digest({"scenario": dict(scenario), "tables": {name: rows for name, rows in sorted(tables.items())}})


def observed_orphan_rate(scenario: Mapping[str, object]) -> float | None:
    if str(scenario.get("kind")) != "orphan":
        return None
    tables = relationship_tables(scenario)
    rows = tables["orders"]
    return sum(str(row.get("customer_id", "")).startswith("orphan-") for row in rows) / len(rows)


def population_identity(scenarios: list[Mapping[str, object]]) -> dict[str, object]:
    return {str(scenario["scenario_group_id"]): {"source_id": f"source-{scenario['scenario_group_id']}", "snapshot_id": f"snapshot-{scenario['scenario_group_id']}", "table_ids": sorted(f"{name}-{scenario['scenario_group_id']}" for name in relationship_tables(scenario))} for scenario in scenarios}


def population_fingerprint(scenarios: list[Mapping[str, object]]) -> str:
    return stable_digest(population_identity(scenarios))
