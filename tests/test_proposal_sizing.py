import math
from dataclasses import FrozenInstanceError, fields

import pytest


DEFAULT_INPUTS = {
    "pair": "EUR/USD",
    "side": "LONG",
    "entry_price": 1.1000,
    "stop_distance_pips": 50.0,
    "drawdown_fraction": 0.0,
}
MISSING = object()


def _snapshot(**overrides):
    from brokers.broker_interface import AccountSnapshot

    values = {
        "nav": 100_000.0,
        "balance": 100_000.0,
        "currency": "USD",
        "margin_available": 95_000.0,
    }
    values.update(overrides)
    return AccountSnapshot(**values)


def _size(account_snapshot=MISSING, **overrides):
    from execution.proposal_sizing import size_trade_proposal

    inputs = dict(DEFAULT_INPUTS)
    inputs.update(overrides)
    if account_snapshot is MISSING:
        account_snapshot = _snapshot()
    return size_trade_proposal(
        account_snapshot=account_snapshot,
        **inputs,
    )


def test_sizes_standard_eurusd_into_exact_immutable_evidence():
    from execution.proposal_sizing import ProposalSizingResult

    result = _size(
        account_snapshot=_snapshot(currency=" usd "),
        pair=" eur/usd ",
        side=" long ",
    )

    assert type(result) is ProposalSizingResult
    assert [field.name for field in fields(result)] == [
        "pair",
        "side",
        "account_currency",
        "nav",
        "drawdown_fraction",
        "risk_fraction",
        "risk_amount",
        "entry_price",
        "stop_distance_pips",
        "stop_loss_price",
        "pip_size",
        "pip_value_per_unit",
        "loss_per_unit_at_stop",
        "units",
    ]
    assert result.pair == "EUR/USD"
    assert result.side == "LONG"
    assert result.account_currency == "USD"
    assert result.nav == pytest.approx(100_000.0)
    assert result.drawdown_fraction == pytest.approx(0.0)
    assert result.risk_fraction == pytest.approx(0.005)
    assert result.risk_amount == pytest.approx(500.0)
    assert result.entry_price == pytest.approx(1.1000)
    assert result.stop_distance_pips == pytest.approx(50.0)
    assert result.stop_loss_price == pytest.approx(1.0950)
    assert result.pip_size == pytest.approx(0.0001)
    assert result.pip_value_per_unit == pytest.approx(0.0001)
    assert result.loss_per_unit_at_stop == pytest.approx(0.005)
    assert result.units == 100_000

    with pytest.raises(FrozenInstanceError):
        result.units = 1


def test_reduces_eurusd_risk_at_moderate_drawdown():
    result = _size(drawdown_fraction=0.04)

    assert result.risk_fraction == pytest.approx(0.0025)
    assert result.risk_amount == pytest.approx(250.0)
    assert result.loss_per_unit_at_stop == pytest.approx(0.005)
    assert result.units == 50_000


def test_sizes_standard_usdjpy_with_base_currency_pip_conversion():
    result = _size(
        pair="USD/JPY",
        entry_price=150.00,
    )
    expected_pip_value = 0.01 / 150.00
    expected_loss_per_unit = 50.0 * expected_pip_value
    expected_units = math.floor(500.0 / expected_loss_per_unit)

    assert result.pip_size == pytest.approx(0.01)
    assert result.pip_value_per_unit == pytest.approx(expected_pip_value)
    assert result.stop_loss_price == pytest.approx(149.50)
    assert result.loss_per_unit_at_stop == pytest.approx(expected_loss_per_unit)
    assert result.units == expected_units


def test_short_stop_is_above_entry_and_fractional_units_round_down():
    result = _size(
        side="SHORT",
        entry_price=1.123456,
        stop_distance_pips=7.0,
    )
    expected_stop = 1.123456 + 7.0 * 0.0001

    assert result.stop_loss_price == pytest.approx(expected_stop)
    assert result.units == math.floor(
        result.risk_amount / result.loss_per_unit_at_stop
    )
    assert result.units * result.loss_per_unit_at_stop <= result.risk_amount
    assert (
        (result.units + 1) * result.loss_per_unit_at_stop
        > result.risk_amount
    )


def test_uses_nav_without_sizing_from_balance_or_margin_available():
    result = _size(
        account_snapshot=_snapshot(
            nav=100_000.0,
            balance=1.0,
            margin_available=0.0,
        )
    )

    assert result.nav == pytest.approx(100_000.0)
    assert result.risk_amount == pytest.approx(500.0)
    assert result.units == 100_000


def test_rejects_values_that_are_not_account_snapshots():
    for invalid_snapshot in (None, {}, object()):
        with pytest.raises(ValueError):
            _size(account_snapshot=invalid_snapshot)


def test_propagates_drawdown_pair_and_currency_validation_errors():
    for invalid_drawdown in (None, True, -0.01, math.nan, math.inf, 1.01):
        with pytest.raises(ValueError):
            _size(drawdown_fraction=invalid_drawdown)

    with pytest.raises(ValueError):
        _size(pair="EURUSD")

    with pytest.raises(ValueError):
        _size(
            account_snapshot=_snapshot(currency="USD"),
            pair="EUR/GBP",
        )


def test_propagates_invalid_nav_and_sub_unit_rejection():
    for invalid_nav in (None, True, 0.0, -1.0, math.nan, math.inf):
        with pytest.raises(ValueError):
            _size(account_snapshot=_snapshot(nav=invalid_nav))

    with pytest.raises(ValueError):
        _size(
            account_snapshot=_snapshot(nav=1.0),
            entry_price=100.0,
            stop_distance_pips=100_000.0,
        )
