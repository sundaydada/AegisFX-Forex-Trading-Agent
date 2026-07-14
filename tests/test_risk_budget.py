import math

import pytest


def _risk_fraction(drawdown_fraction):
    from execution.risk_budget import risk_fraction_for_drawdown

    return risk_fraction_for_drawdown(drawdown_fraction)


def test_zero_drawdown_uses_half_percent_risk():
    assert _risk_fraction(0.0) == 0.005


def test_drawdown_immediately_below_threshold_uses_half_percent_risk():
    assert _risk_fraction(math.nextafter(0.04, 0.0)) == 0.005


def test_drawdown_at_threshold_uses_quarter_percent_risk():
    assert _risk_fraction(0.04) == 0.0025


def test_drawdown_above_threshold_uses_quarter_percent_risk():
    assert _risk_fraction(0.10) == 0.0025


@pytest.mark.parametrize(
    "invalid_drawdown",
    [-0.0001, math.nan, math.inf, -math.inf, 1.0001],
)
def test_rejects_invalid_drawdown(invalid_drawdown):
    with pytest.raises(ValueError):
        _risk_fraction(invalid_drawdown)
