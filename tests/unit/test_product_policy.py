from __future__ import annotations

from types import SimpleNamespace

from dirty_data_to_olap.application.product_policy import OrderProductPolicy


def test_product_policy_column_resolves_case_insensitive_source_aliases() -> None:
    catalog = SimpleNamespace(
        columns=(SimpleNamespace(table_id="orders", physical_name="CRM_CUSTOMER_ID", column_id="column-crm-customer-id"),),
    )

    column = OrderProductPolicy.column(catalog, "orders", ("customer_id", "crm_customer_id"))

    assert column is not None
    assert column.column_id == "column-crm-customer-id"
