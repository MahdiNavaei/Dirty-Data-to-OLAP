from decimal import Decimal

from dirty_data_to_olap.application.validation import _as_refs, _equal


def test_validation_comparison_is_exact_for_numeric_and_structured_values():
    assert _equal("6", Decimal("6.000"))
    assert not _equal("6", Decimal("6.001"))
    assert _equal({"quantity": "6"}, {"quantity": Decimal("6.000")})


def test_validation_lineage_accepts_json_reference_arrays_only_as_references():
    assert _as_refs('["source-1", "source-2"]') == ("source-1", "source-2")
    assert _as_refs("source-1") == ("source-1",)
