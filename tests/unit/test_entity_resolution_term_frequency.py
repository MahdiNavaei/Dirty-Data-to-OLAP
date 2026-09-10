import pytest

from dirty_data_to_olap.adapters.entity_resolution.splink import SplinkEntityResolutionAdapter, _ERTraining


class _Configured:
    def __init__(self):
        self.args = None

    def configure(self, **kwargs):
        self.args = kwargs
        return self


def test_term_frequency_configuration_is_behavioral_not_declarative():
    comparison = _Configured()
    result = SplinkEntityResolutionAdapter._configure_term_frequency_adjustment(comparison, "cmp-email")
    assert result is comparison
    assert comparison.args == {"term_frequency_adjustments": True}


def test_term_frequency_claim_fails_closed_when_official_api_is_missing():
    with pytest.raises(_ERTraining):
        SplinkEntityResolutionAdapter._configure_term_frequency_adjustment(object(), "cmp-email")
