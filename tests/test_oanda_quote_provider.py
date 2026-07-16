import json
from datetime import datetime, timedelta, timezone

import pytest


_PRICING_ENDPOINT = "/pricing?instruments=EUR_USD"
_EXPECTED_TIMESTAMP = datetime(2026, 7, 15, 16, 0, 0, 123456, tzinfo=timezone.utc)
_VALID_PRICE_ENTRY = {
    "instrument": "EUR_USD",
    "time": "2026-07-15T16:00:00.123456789Z",
    "closeoutBid": "1.09975",
    "closeoutAsk": "1.10005",
}
_MISSING = object()


def _pricing_response(field=None, value=_MISSING):
    entry = dict(_VALID_PRICE_ENTRY)
    if field is not None:
        if value is _MISSING:
            entry.pop(field, None)
        else:
            entry[field] = value
    return {"prices": [entry]}


def _broker_with_response(monkeypatch, response, error=None):
    from brokers.oanda_broker import OandaBroker

    calls = []
    broker = OandaBroker(
        api_key="unused-test-key",
        account_id="unused-test-account",
        base_url="https://example.invalid",
    )

    def fake_request(endpoint, method="GET", body=None):
        calls.append((endpoint, method, body))
        if error is not None:
            raise error
        return response

    monkeypatch.setattr(broker, "_make_request", fake_request)
    return broker, calls


def _get_quote(monkeypatch, response):
    broker, calls = _broker_with_response(monkeypatch, response)
    return broker.get_quote("EUR/USD"), calls


def _assert_quote_rejected(monkeypatch, response, *evidence_terms):
    broker, calls = _broker_with_response(monkeypatch, response)

    with pytest.raises(ValueError) as excinfo:
        broker.get_quote("EUR/USD")

    message = str(excinfo.value).lower()
    assert any(term.lower() in message for term in evidence_terms)
    assert calls == [(_PRICING_ENDPOINT, "GET", None)]


def test_valid_quote_returns_exact_contract_with_one_request(monkeypatch):
    quote, calls = _get_quote(monkeypatch, _pricing_response())

    assert calls == [(_PRICING_ENDPOINT, "GET", None)]
    assert set(quote) == {"currency_pair", "bid", "ask", "timestamp"}
    assert quote["currency_pair"] == "EUR/USD"
    assert type(quote["bid"]) is float
    assert quote["bid"] == 1.09975
    assert type(quote["ask"]) is float
    assert quote["ask"] == 1.10005

    timestamp = quote["timestamp"]
    assert isinstance(timestamp, datetime)
    assert timestamp.tzinfo is not None
    assert timestamp.utcoffset() == timedelta(0)


def test_pair_is_requested_as_instrument_and_returned_in_repo_format(
    monkeypatch,
):
    quote, calls = _get_quote(monkeypatch, _pricing_response())

    assert calls == [("/pricing?instruments=EUR_USD", "GET", None)]
    assert quote["currency_pair"] == "EUR/USD"
    assert quote["currency_pair"] != "EUR_USD"


def test_rfc3339_nanosecond_z_time_from_price_entry_not_envelope(monkeypatch):
    response = _pricing_response()
    response["time"] = "2001-01-01T00:00:00.000000000Z"

    quote, _ = _get_quote(monkeypatch, response)

    assert quote["timestamp"] == _EXPECTED_TIMESTAMP
    assert quote["timestamp"].utcoffset() == timedelta(0)


def test_invalid_pair_inputs_are_rejected_before_any_request(monkeypatch):
    invalid_pairs = (
        None,
        True,
        123,
        "",
        "EURUSD",
        "EU/USD",
        "EUR/USDD",
        "EUR//USD",
        "EUR-USD",
        "EUR/",
        "/USD",
    )
    for invalid_pair in invalid_pairs:
        broker, calls = _broker_with_response(
            monkeypatch,
            _pricing_response(),
        )

        with pytest.raises(ValueError) as excinfo:
            broker.get_quote(invalid_pair)

        assert "pair" in str(excinfo.value).lower()
        assert calls == []


def test_empty_or_malformed_prices_evidence_raises_value_error(monkeypatch):
    malformed_responses = (
        None,
        ["not-a-dictionary"],
        {},
        {"prices": None},
        {"prices": "EUR_USD"},
        {"prices": []},
        {"prices": ["not-a-dictionary"]},
        {"prices": [None]},
    )
    for malformed_response in malformed_responses:
        _assert_quote_rejected(
            monkeypatch,
            malformed_response,
            "price",
            "quote",
        )


def test_instrument_mismatch_raises_value_error(monkeypatch):
    _assert_quote_rejected(
        monkeypatch,
        _pricing_response("instrument", "GBP_USD"),
        "instrument",
    )


def test_invalid_bid_and_ask_evidence_raises_value_error(monkeypatch):
    invalid_values = (
        _MISSING,
        None,
        True,
        "",
        "not-a-number",
        "nan",
        "inf",
        "-inf",
        0,
        -1.5,
    )
    for field in ("closeoutBid", "closeoutAsk"):
        for invalid_value in invalid_values:
            _assert_quote_rejected(
                monkeypatch,
                _pricing_response(field, invalid_value),
                field,
            )


def test_crossed_market_ask_below_bid_raises_value_error(monkeypatch):
    response = _pricing_response("closeoutBid", "1.10005")
    response["prices"][0]["closeoutAsk"] = "1.09975"

    _assert_quote_rejected(monkeypatch, response, "ask", "crossed")


def test_invalid_timestamp_evidence_raises_value_error(monkeypatch):
    invalid_times = (
        _MISSING,
        None,
        123,
        "not-a-timestamp",
        "2026-07-15T16:00:00",
    )
    for invalid_time in invalid_times:
        _assert_quote_rejected(
            monkeypatch,
            _pricing_response("time", invalid_time),
            "time",
        )


def test_transport_failure_propagates_as_runtime_error(monkeypatch):
    broker, calls = _broker_with_response(
        monkeypatch,
        None,
        error=RuntimeError("OANDA API error 503: service unavailable"),
    )

    with pytest.raises(RuntimeError):
        broker.get_quote("EUR/USD")

    assert calls == [(_PRICING_ENDPOINT, "GET", None)]


def test_practice_url_auth_header_and_timeout_via_fake_urlopen(monkeypatch):
    import urllib.request

    from brokers.oanda_broker import OandaBroker

    captured = []

    class _FakeResponse:
        def read(self):
            return json.dumps(_pricing_response()).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    def fake_urlopen(request, data=None, timeout=None):
        captured.append(
            {
                "url": request.full_url,
                "method": request.get_method(),
                "headers": {
                    name.lower(): value
                    for name, value in request.header_items()
                },
                "data": data,
                "timeout": timeout,
            }
        )
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    broker = OandaBroker(
        api_key="TEST-TOKEN",
        account_id="TEST-ACCOUNT",
        base_url="https://api-fxpractice.oanda.com",
    )

    quote = broker.get_quote("EUR/USD")

    assert len(captured) == 1
    request = captured[0]
    assert request["url"] == (
        "https://api-fxpractice.oanda.com"
        "/v3/accounts/TEST-ACCOUNT/pricing?instruments=EUR_USD"
    )
    assert request["method"] == "GET"
    assert request["headers"]["authorization"] == "Bearer TEST-TOKEN"
    assert request["timeout"] == 10
    assert request["data"] is None

    assert set(quote) == {"currency_pair", "bid", "ask", "timestamp"}
    assert quote["currency_pair"] == "EUR/USD"
    assert type(quote["bid"]) is float
    assert quote["bid"] == 1.09975
    assert type(quote["ask"]) is float
    assert quote["ask"] == 1.10005
    assert quote["timestamp"].utcoffset() == timedelta(0)
