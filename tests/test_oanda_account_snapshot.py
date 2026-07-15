import math
from dataclasses import FrozenInstanceError, fields

import pytest


VALID_ACCOUNT = {
    "NAV": "100000.25",
    "balance": "99800.00",
    "currency": "USD",
    "marginAvailable": "95000.50",
}
MISSING = object()


def _response_with(field=None, value=MISSING):
    account = dict(VALID_ACCOUNT)
    if field is not None:
        if value is MISSING:
            account.pop(field, None)
        else:
            account[field] = value
    return {"account": account}


def _broker_with_response(monkeypatch, response):
    from brokers.oanda_broker import OandaBroker

    calls = []
    broker = OandaBroker(
        api_key="unused-test-key",
        account_id="unused-test-account",
        base_url="https://example.invalid",
    )

    def fake_request(endpoint, method="GET", body=None):
        calls.append((endpoint, method, body))
        return response

    monkeypatch.setattr(broker, "_make_request", fake_request)
    return broker, calls


def _get_snapshot(monkeypatch, response):
    broker, calls = _broker_with_response(monkeypatch, response)
    return broker.get_account_snapshot(), calls


def test_valid_summary_returns_one_immutable_account_snapshot(monkeypatch):
    from brokers.broker_interface import AccountSnapshot

    snapshot, calls = _get_snapshot(
        monkeypatch,
        {"account": dict(VALID_ACCOUNT)},
    )

    assert type(snapshot) is AccountSnapshot
    assert [field.name for field in fields(snapshot)] == [
        "nav",
        "balance",
        "currency",
        "margin_available",
    ]
    assert snapshot.nav == 100000.25
    assert snapshot.balance == 99800.00
    assert snapshot.currency == "USD"
    assert snapshot.margin_available == 95000.50
    assert calls == [("/summary", "GET", None)]

    with pytest.raises(FrozenInstanceError):
        snapshot.nav = 1.0


def test_currency_is_stripped_and_normalized_to_uppercase(monkeypatch):
    snapshot, _ = _get_snapshot(
        monkeypatch,
        _response_with("currency", " usd "),
    )

    assert snapshot.currency == "USD"


def test_zero_balance_and_margin_available_are_valid(monkeypatch):
    response = _response_with("balance", "0")
    response["account"]["marginAvailable"] = 0.0

    snapshot, _ = _get_snapshot(monkeypatch, response)

    assert snapshot.balance == 0.0
    assert snapshot.margin_available == 0.0


def test_invalid_numeric_account_values_raise_value_error(monkeypatch):
    invalid_values = [
        MISSING,
        None,
        True,
        False,
        "not-a-number",
        math.nan,
        math.inf,
        -math.inf,
        "nan",
        "inf",
        "-inf",
    ]

    for field in ("NAV", "balance", "marginAvailable"):
        for invalid_value in invalid_values:
            with pytest.raises(ValueError):
                _get_snapshot(
                    monkeypatch,
                    _response_with(field, invalid_value),
                )


def test_out_of_range_numeric_account_values_raise_value_error(monkeypatch):
    invalid_values = [
        ("NAV", 0.0),
        ("NAV", -1.0),
        ("balance", -1.0),
        ("marginAvailable", -1.0),
    ]

    for field, invalid_value in invalid_values:
        with pytest.raises(ValueError):
            _get_snapshot(
                monkeypatch,
                _response_with(field, invalid_value),
            )


def test_invalid_currency_values_raise_value_error(monkeypatch):
    for invalid_currency in (
        MISSING,
        None,
        "",
        "   ",
        True,
        123,
        [],
        {},
    ):
        with pytest.raises(ValueError):
            _get_snapshot(
                monkeypatch,
                _response_with("currency", invalid_currency),
            )


def test_missing_or_malformed_account_mapping_raises_value_error(monkeypatch):
    for response in (
        {},
        {"account": None},
        {"account": True},
        {"account": []},
        {"account": "not-a-mapping"},
    ):
        with pytest.raises(ValueError):
            _get_snapshot(monkeypatch, response)
