from pathlib import Path


def test_fusion_service_has_no_runtime_entity_resolution_or_source_reader_dependency():
    source = Path("src/dirty_data_to_olap/application/evidence_fusion.py").read_text(encoding="utf-8")
    assert "entity_resolution" not in source.lower()
    assert "splink" not in source.lower()
    assert "pandas" not in source.lower()
    assert "pyarrow" not in source.lower()
    assert "dlt" not in source.lower()


def test_step17_does_not_create_review_or_canonical_contracts():
    source = Path("src/dirty_data_to_olap/application/evidence_fusion.py").read_text(encoding="utf-8")
    for forbidden in ("ReviewDecision", "CanonicalModelHypothesis", "CanonicalModel", "EntityResolutionSpec", "SourceRecordCanonicalMap"):
        assert forbidden not in source
