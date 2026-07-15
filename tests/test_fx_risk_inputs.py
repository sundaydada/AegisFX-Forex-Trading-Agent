import math
import sys
from dataclasses import FrozenInstanceError, fields

import pytest


BASE_INPUTS = {
    "pair": "EUR/USD",
    "side": "LONG",
    "account_currency": "USD",
    "entry_price": 1.1000,
    "stop_distance_pips": 50.0,
}


def _build(**overrides):
    from execution.fx_risk_inputs import build_fx_risk_inputs

    inputs = dict(BASE_INPUTS)
    inputs.update(overrides)
    return build_fx_risk_inputs(**inputs)


def test_returns_exact_immutable_normalized_risk_inputs():
    from execution.fx_risk_inputs import FxRiskInputs

    result = _build(
        pair=" eur/usd ",
        side=" long ",
        account_currency=" usd ",
    )

    assert type(result) is FxRiskInputs
    assert [field.name for field in fields(result)] == [
        "pair",
        "side",
        "account_currency",
        "entry_price",
        "stop_distance_pips",
        "pip_size",
        "pip_value_per_unit",
        "stop_loss_price",
    ]
    assert result.pair == "EUR/USD"
    assert result.side == "LONG"
    assert result.account_currency == "USD"
    assert result.entry_price == pytest.approx(1.1000)
    assert result.stop_distance_pips == pytest.approx(50.0)
    assert result.pip_size == pytest.approx(0.0001)
    assert result.pip_value_per_unit == pytest.approx(0.0001)
    assert result.stop_loss_price == pytest.approx(1.0950)

    with pytest.raises(FrozenInstanceError):
        result.entry_price = 2.0


def test_builds_required_usd_and_jpy_long_and_short_examples():
    examples = [
        (
            {
                "pair": "EUR/USD",
                "side": "LONG",
                "account_currency": "USD",
                "entry_price": 1.1000,
            },
            0.0001,
            0.0001,
            1.0950,
        ),
        (
            {
                "pair": "EUR/USD",
                "side": "SHORT",
                "account_currency": "USD",
                "entry_price": 1.1000,
            },
            0.0001,
            0.0001,
            1.1050,
        ),
        (
            {
                "pair": "USD/JPY",
                "side": "LONG",
                "account_currency": "USD",
                "entry_price": 150.00,
            },
            0.01,
            0.01 / 150.00,
            149.50,
        ),
        (
            {
                "pair": "USD/JPY",
                "side": "SHORT",
                "account_currency": "USD",
                "entry_price": 150.00,
            },
            0.01,
            0.01 / 150.00,
            150.50,
        ),
    ]

    for inputs, pip_size, pip_value_per_unit, stop_loss_price in examples:
        result = _build(**inputs)
        assert result.pip_size == pytest.approx(pip_size)
        assert result.pip_value_per_unit == pytest.approx(pip_value_per_unit)
        assert result.stop_loss_price == pytest.approx(stop_loss_price)


def test_rejects_third_currency_without_a_conversion_rate():
    with pytest.raises(ValueError):
        _build(pair="EUR/GBP", account_currency="USD")


def test_rejects_invalid_numeric_inputs():
    invalid_values = [
        None,
        True,
        False,
        "not-a-number",
        0.0,
        -1.0,
        math.nan,
        math.inf,
        -math.inf,
    ]

    for field_name in ("entry_price", "stop_distance_pips"):
        for invalid_value in invalid_values:
            with pytest.raises(ValueError):
                _build(**{field_name: invalid_value})


def test_rejects_missing_or_malformed_pair_side_and_account_currency():
    invalid_inputs = [
        ("pair", None),
        ("pair", ""),
        ("pair", "EURUSD"),
        ("pair", "EU/USD"),
        ("pair", "EUR/USDD"),
        ("pair", "EU1/USD"),
        ("pair", "EUR-USD"),
        ("pair", "EUR/USD/JPY"),
        ("pair", True),
        ("side", None),
        ("side", ""),
        ("side", "BUY"),
        ("side", "SELL"),
        ("side", True),
        ("account_currency", None),
        ("account_currency", ""),
        ("account_currency", "   "),
        ("account_currency", "US"),
        ("account_currency", "USDD"),
        ("account_currency", "U1D"),
        ("account_currency", True),
    ]

    for field_name, invalid_value in invalid_inputs:
        with pytest.raises(ValueError):
            _build(**{field_name: invalid_value})


def test_rejects_nonpositive_or_nonfinite_stop_loss_prices():
    invalid_calculations = [
        {
            "pair": "EUR/USD",
            "side": "LONG",
            "entry_price": 0.005,
            "stop_distance_pips": 50.0,
        },
        {
            "pair": "USD/JPY",
            "side": "SHORT",
            "entry_price": sys.float_info.max,
            "stop_distance_pips": sys.float_info.max,
        },
    ]

    for inputs in invalid_calculations:
        with pytest.raises(ValueError):
            _build(**inputs)
