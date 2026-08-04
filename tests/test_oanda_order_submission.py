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


def test_place_order_returns_opened_trade_id_distinct_from_fill_transaction(
    monkeypatch,
):
    """The opened Trade ID is not the fill transaction ID.

    OANDA returns orderFillTransaction.id (the fill transaction) and
    orderFillTransaction.tradeOpened.tradeID (the trade it opened) as
    independent identifiers. The fake response below gives them
    deliberately different values so an implementation that returns one
    for both cannot pass.
    """

    from brokers.oanda_broker import OandaBroker

    broker = OandaBroker(
        api_key="unused-test-key",
        account_id="unused-test-account",
        base_url="https://example.invalid",
    )

    def fake_request(endpoint, method="GET", body=None):
        return {
            "orderFillTransaction": {
                "id": "900",
                "tradeOpened": {
                    "tradeID": "777",
                },
                "units": "5000",
                "price": "1.40880",
                "time": "2026-07-27T12:00:00.000000000Z",
            }
        }

    monkeypatch.setattr(broker, "_make_request", fake_request)

    order = {
        "currency_pair": "USD/CAD",
        "direction": "Long",
        "position_size": 5000,
        "stop_loss_price": 1.40680,
    }

    result = broker.place_order(order)

    assert result["execution_status"] == "Filled"
    assert result["broker_order_id"] == "900"
    assert result["broker_trade_id"] == "777"
    assert result["broker_order_id"] != result["broker_trade_id"], (
        "the fill transaction id and the opened trade id must not be"
        " taken from the same response field"
    )

    assert result["currency_pair"] == "USD/CAD"
    assert result["direction"] == "Long"
    assert result["units"] == 5000.0
    assert result["fill_price"] == 1.40880
    assert result["timestamp"] == "2026-07-27T12:00:00.000000000Z"

    assert set(result) == {
        "execution_status",
        "broker_order_id",
        "broker_trade_id",
        "currency_pair",
        "direction",
        "units",
        "fill_price",
        "timestamp",
    }, f"got result keys {sorted(result)!r}"


def test_place_order_returns_empty_trade_id_when_trade_opened_is_unavailable(
    monkeypatch,
):
    """A fill that opens no trade must yield an empty trade id.

    OANDA omits tradeOpened on fills that do not open a trade, and can
    also return it explicitly as null. Both must degrade to an empty
    broker_trade_id: the fill must still be reported as Filled, and the
    fill transaction id must never be substituted for the missing trade
    id. Every other result field must be unaffected.
    """

    from brokers.oanda_broker import OandaBroker

    base_fill = {
        "id": "900",
        "units": "5000",
        "price": "1.40880",
        "time": "2026-07-27T12:00:00.000000000Z",
    }

    missing_fill = dict(base_fill)

    null_fill = dict(base_fill)
    null_fill["tradeOpened"] = None

    order = {
        "currency_pair": "USD/CAD",
        "direction": "Long",
        "position_size": 5000,
        "stop_loss_price": 1.40680,
    }

    for case_name, fill in (
        ("missing tradeOpened", missing_fill),
        ("null tradeOpened", null_fill),
    ):
        broker = OandaBroker(
            api_key="unused-test-key",
            account_id="unused-test-account",
            base_url="https://example.invalid",
        )

        def fake_request(endpoint, method="GET", body=None, fill=fill):
            return {"orderFillTransaction": fill}

        monkeypatch.setattr(broker, "_make_request", fake_request)

        result = broker.place_order(order)

        assert result["execution_status"] == "Filled", (
            f"[{case_name}] an unavailable tradeOpened must not reject the"
            f" fill; got {result!r}"
        )
        assert result["broker_order_id"] == "900", (
            f"[{case_name}] got broker_order_id"
            f" {result['broker_order_id']!r}"
        )
        assert result["broker_trade_id"] == "", (
            f"[{case_name}] an unavailable tradeOpened must yield an empty"
            " broker_trade_id, never the fill transaction id; got"
            f" {result['broker_trade_id']!r}"
        )

        assert result["currency_pair"] == "USD/CAD", (
            f"[{case_name}] got currency_pair {result['currency_pair']!r}"
        )
        assert result["direction"] == "Long", (
            f"[{case_name}] got direction {result['direction']!r}"
        )
        assert result["units"] == 5000.0, (
            f"[{case_name}] got units {result['units']!r}"
        )
        assert result["fill_price"] == 1.40880, (
            f"[{case_name}] got fill_price {result['fill_price']!r}"
        )
        assert result["timestamp"] == "2026-07-27T12:00:00.000000000Z", (
            f"[{case_name}] got timestamp {result['timestamp']!r}"
        )
