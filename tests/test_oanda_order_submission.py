import math


VALID_ORDER = {
    "currency_pair": "EUR/USD",
    "direction": "Long",
    "position_size": 100_000,
    "stop_loss_price": 1.095,
}


def _broker_with_calls(monkeypatch):
    from brokers.oanda_broker import OandaBroker

    calls = []
    broker = OandaBroker(
        api_key="unused-test-key",
        account_id="unused-test-account",
        base_url="https://example.invalid",
    )

    def fake_request(endpoint, method="GET", body=None):
        calls.append((endpoint, method, body))
        return {
            "orderFillTransaction": {
                "id": "TEST-ORDER-1",
                "units": body["order"]["units"],
                "price": "1.1000",
                "time": "2026-07-15T12:00:00Z",
            }
        }

    monkeypatch.setattr(broker, "_make_request", fake_request)
    return broker, calls


def _assert_rejected_without_request(monkeypatch, order):
    broker, calls = _broker_with_calls(monkeypatch)

    result = broker.place_order(order)

    assert result["execution_status"] == "Rejected"
    assert calls == []


def test_long_order_submits_exact_payload_with_protective_stop(monkeypatch):
    broker, calls = _broker_with_calls(monkeypatch)

    result = broker.place_order(dict(VALID_ORDER))

    assert result["execution_status"] == "Filled"
    assert calls == [
        (
            "/orders",
            "POST",
            {
                "order": {
                    "type": "MARKET",
                    "instrument": "EUR_USD",
                    "units": "100000",
                    "timeInForce": "FOK",
                    "positionFill": "DEFAULT",
                    "stopLossOnFill": {
                        "price": "1.095",
                    },
                }
            },
        )
    ]


def test_short_order_submits_exact_negative_integer_units(monkeypatch):
    broker, calls = _broker_with_calls(monkeypatch)
    order = dict(VALID_ORDER)
    order["direction"] = "Short"

    result = broker.place_order(order)

    assert result["execution_status"] == "Filled"
    assert len(calls) == 1
    assert calls[0][0:2] == ("/orders", "POST")
    assert calls[0][2]["order"]["units"] == "-100000"


def test_rejects_invalid_position_sizes_before_request(monkeypatch):
    missing_size = dict(VALID_ORDER)
    missing_size.pop("position_size")
    _assert_rejected_without_request(monkeypatch, missing_size)

    invalid_sizes = [
        None,
        True,
        False,
        0,
        -1,
        100_000.0,
        100_000.5,
        "100000",
        math.nan,
        math.inf,
        -math.inf,
    ]
    for invalid_size in invalid_sizes:
        order = dict(VALID_ORDER)
        order["position_size"] = invalid_size
        _assert_rejected_without_request(monkeypatch, order)


def test_rejects_missing_or_invalid_stop_loss_before_request(monkeypatch):
    missing_stop = dict(VALID_ORDER)
    missing_stop.pop("stop_loss_price")
    _assert_rejected_without_request(monkeypatch, missing_stop)

    invalid_stops = [
        None,
        True,
        False,
        0,
        -1,
        "1.095",
        math.nan,
        math.inf,
        -math.inf,
    ]
    for invalid_stop in invalid_stops:
        order = dict(VALID_ORDER)
        order["stop_loss_price"] = invalid_stop
        _assert_rejected_without_request(monkeypatch, order)


def test_rejects_noncanonical_directions_before_request(monkeypatch):
    for invalid_direction in (
        "LONG",
        "SHORT",
        "long",
        "short",
        "",
        "Buy",
        " Long ",
        None,
    ):
        order = dict(VALID_ORDER)
        order["direction"] = invalid_direction
        _assert_rejected_without_request(monkeypatch, order)


def test_preserves_stop_loss_precision_without_rounding(monkeypatch):
    broker, calls = _broker_with_calls(monkeypatch)
    order = dict(VALID_ORDER)
    order["stop_loss_price"] = 1.0956789

    result = broker.place_order(order)

    assert result["execution_status"] == "Filled"
    assert len(calls) == 1
    assert calls[0][2]["order"]["stopLossOnFill"] == {
        "price": "1.0956789",
    }
