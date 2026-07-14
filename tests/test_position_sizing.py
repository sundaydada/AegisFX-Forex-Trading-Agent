import math
from collections.abc import Mapping

import pytest


BASE_INPUTS = {
    "nav": 100_000.0,
    "risk_fraction": 0.005,
    "stop_distance_pips": 50.0,
    "pip_value_per_unit": 0.0001,
}


def _calculate(**overrides):
    from execution.position_sizing import calculate_position_size

    inputs = dict(BASE_INPUTS)
    inputs.update(overrides)
    return calculate_position_size(**inputs)


def _field(result, name):
    if isinstance(result, Mapping):
        return result[name]
    return getattr(result, name)


def test_calculates_half_percent_risk_for_a_100k_account():
    result = _calculate()

    units = _field(result, "units")
    assert isinstance(units, int) and not isinstance(units, bool)
    assert units == 100_000
    assert units > 0
    assert _field(result, "risk_amount") == pytest.approx(500.0)
    assert _field(result, "loss_per_unit_at_stop") == pytest.approx(0.005)
    assert _field(result, "nav") == pytest.approx(100_000.0)
    assert _field(result, "risk_fraction") == pytest.approx(0.005)
    assert _field(result, "stop_distance_pips") == pytest.approx(50.0)
    assert _field(result, "pip_value_per_unit") == pytest.approx(0.0001)


def test_calculates_quarter_percent_risk_for_a_100k_account():
    result = _calculate(risk_fraction=0.0025)

    assert _field(result, "units") == 50_000
    assert _field(result, "risk_amount") == pytest.approx(250.0)
    assert _field(result, "loss_per_unit_at_stop") == pytest.approx(0.005)


def test_fractional_units_are_rounded_down_not_up():
    result = _calculate(
        nav=1_000.0,
        risk_fraction=0.01,
        stop_distance_pips=3.0,
        pip_value_per_unit=1.0,
    )

    units = _field(result, "units")
    risk_amount = _field(result, "risk_amount")
    loss_per_unit = _field(result, "loss_per_unit_at_stop")
    assert units == 3
    assert units * loss_per_unit <= risk_amount
    assert (units + 1) * loss_per_unit > risk_amount


@pytest.mark.parametrize(
    "invalid_nav",
    [None, 0.0, -1.0, math.nan, math.inf, -math.inf],
)
def test_rejects_invalid_nav(invalid_nav):
    with pytest.raises(ValueError):
        _calculate(nav=invalid_nav)


@pytest.mark.parametrize(
    "invalid_risk_fraction",
    [None, 0.0, -0.01, math.nan, math.inf, -math.inf],
)
def test_rejects_invalid_risk_fraction(invalid_risk_fraction):
    with pytest.raises(ValueError):
        _calculate(risk_fraction=invalid_risk_fraction)


@pytest.mark.parametrize(
    "invalid_stop_distance",
    [None, 0.0, -1.0, math.nan, math.inf, -math.inf],
)
def test_rejects_invalid_stop_distance(invalid_stop_distance):
    with pytest.raises(ValueError):
        _calculate(stop_distance_pips=invalid_stop_distance)


@pytest.mark.parametrize(
    "invalid_pip_value",
    [None, 0.0, -0.0001, math.nan, math.inf, -math.inf],
)
def test_rejects_invalid_pip_value(invalid_pip_value):
    with pytest.raises(ValueError):
        _calculate(pip_value_per_unit=invalid_pip_value)


def test_rejects_a_result_below_one_whole_unit():
    with pytest.raises(ValueError):
        _calculate(
            nav=100.0,
            risk_fraction=0.005,
            stop_distance_pips=50.0,
            pip_value_per_unit=1.0,
        )
