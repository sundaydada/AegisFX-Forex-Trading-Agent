"""Read-only Trade-details lookup contract for OandaBroker.

get_trade_details reads one Trade by its OANDA Trade ID and normalizes
the response into repository conventions: slash-form pairs, float
numerics with the unit sign preserved, and verbatim timestamps. It must
never submit an order, close a position, or otherwise mutate anything.

_make_request is patched in every test, so no HTTP request is possible.
"""


TRADE_RESPONSE = {
    "trade": {
        "id": "777",
        "instrument": "USD_CAD",
        "price": "1.40124",
        "openTime": "2026-08-02T23:57:18.326354060Z",
        "state": "CLOSED",
        "initialUnits": "-249694",
        "currentUnits": "0",
        "realizedPL": "-424.48",
        "averageClosePrice": "1.40294",
        "closingTransactionIDs": ["265"],
        "closeTime": "2026-08-03T01:29:59.000000000Z",
    },
    "lastTransactionID": "265",
}

EXPECTED_KEYS = {
    "lookup_status",
    "broker_trade_id",
    "currency_pair",
    "state",
    "open_price",
    "open_time",
    "initial_units",
    "current_units",
    "realized_pl",
    "average_close_price",
    "close_time",
    "closing_transaction_ids",
}


def test_get_trade_details_returns_normalized_closed_trade(monkeypatch):
    """One GET, normalized out, and nothing written to the broker."""

    from brokers.oanda_broker import OandaBroker

    broker = OandaBroker(
        api_key="unused-test-key",
        account_id="unused-test-account",
        base_url="https://example.invalid",
    )

    calls = []

    def fake_request(endpoint, method="GET", body=None):
        calls.append((endpoint, method, body))
        return TRADE_RESPONSE

    def never_place_order(*args, **kwargs):
        raise AssertionError("a trade lookup must not submit an order")

    def never_close_position(*args, **kwargs):
        raise AssertionError("a trade lookup must not close a position")

    monkeypatch.setattr(broker, "_make_request", fake_request)
    monkeypatch.setattr(broker, "place_order", never_place_order)
    monkeypatch.setattr(broker, "close_position", never_close_position)

    result = broker.get_trade_details("777")

    # --- exactly one GET-style request, no body, exact endpoint ---
    assert calls == [
        ("/trades/777", "GET", None),
    ], f"got requests {calls!r}"

    # --- normalized identity and state ---
    assert result["lookup_status"] == "FOUND", (
        f"got lookup_status {result['lookup_status']!r}"
    )
    assert result["broker_trade_id"] == "777"
    assert result["currency_pair"] == "USD/CAD", (
        f"USD_CAD must normalize to USD/CAD; got"
        f" {result['currency_pair']!r}"
    )
    assert result["state"] == "CLOSED"

    # --- numeric strings become floats, sign preserved ---
    assert result["open_price"] == 1.40124
    assert result["initial_units"] == -249694.0, (
        f"the unit sign must be preserved; got {result['initial_units']!r}"
    )
    assert result["current_units"] == 0.0
    assert result["realized_pl"] == -424.48
    assert result["average_close_price"] == 1.40294

    # --- timestamps are forwarded verbatim ---
    assert result["open_time"] == "2026-08-02T23:57:18.326354060Z"
    assert result["close_time"] == "2026-08-03T01:29:59.000000000Z"

    # --- closing transaction ids stay a list of strings ---
    assert result["closing_transaction_ids"] == ["265"]

    # --- no raw OANDA field leaks into the normalized result ---
    assert set(result) == EXPECTED_KEYS, (
        f"got result keys {sorted(result)!r}"
    )


def test_get_trade_details_rejects_invalid_trade_ids_without_request(
    monkeypatch,
):
    """An unusable trade_id must be refused before any request.

    The patched request raises unconditionally, so reaching the network
    boundary at all is the failure. An empty id would otherwise hit the
    list-trades endpoint, and a coerced None or int would look like a
    real lookup.
    """

    from brokers.oanda_broker import OandaBroker

    broker = OandaBroker(
        api_key="unused-test-key",
        account_id="unused-test-account",
        base_url="https://example.invalid",
    )

    def never_request(*args, **kwargs):
        raise AssertionError(
            "an invalid trade_id must be rejected before any request"
        )

    monkeypatch.setattr(broker, "_make_request", never_request)

    for case_name, trade_id in (
        ("none", None),
        ("empty", ""),
        ("whitespace", "   "),
        ("non-string", 123),
    ):
        result = broker.get_trade_details(trade_id)

        assert result == {
            "lookup_status": "INVALID_REQUEST",
            "reason": "trade_id must be a non-empty string",
        }, f"[{case_name}] got {result!r}"


def test_get_trade_details_returns_normalized_open_trade(monkeypatch):
    """An OPEN trade is a successful lookup with no close evidence.

    OANDA omits averageClosePrice, closeTime and closingTransactionIDs
    while a trade is still open. Each must become an explicit absent
    value, never an estimate: a zero close price is indistinguishable
    from a real one and would corrupt any P&L derived from it.
    """

    from brokers.oanda_broker import OandaBroker

    broker = OandaBroker(
        api_key="unused-test-key",
        account_id="unused-test-account",
        base_url="https://example.invalid",
    )

    calls = []

    def fake_request(endpoint, method="GET", body=None):
        calls.append((endpoint, method, body))
        return {
            "trade": {
                "id": "778",
                "instrument": "USD_CAD",
                "price": "1.40124",
                "openTime": "2026-08-05T18:00:00.000000000Z",
                "state": "OPEN",
                "initialUnits": "-249694",
                "currentUnits": "-249694",
                "realizedPL": "0.00000",
            },
            "lastTransactionID": "266",
        }

    def never_place_order(*args, **kwargs):
        raise AssertionError("a trade lookup must not submit an order")

    def never_close_position(*args, **kwargs):
        raise AssertionError("a trade lookup must not close a position")

    monkeypatch.setattr(broker, "_make_request", fake_request)
    monkeypatch.setattr(broker, "place_order", never_place_order)
    monkeypatch.setattr(broker, "close_position", never_close_position)

    result = broker.get_trade_details("778")

    assert calls == [
        ("/trades/778", "GET", None),
    ], f"got requests {calls!r}"

    assert result == {
        "lookup_status": "FOUND",
        "broker_trade_id": "778",
        "currency_pair": "USD/CAD",
        "state": "OPEN",
        "open_price": 1.40124,
        "open_time": "2026-08-05T18:00:00.000000000Z",
        "initial_units": -249694.0,
        "current_units": -249694.0,
        "realized_pl": 0.0,
        "average_close_price": None,
        "close_time": "",
        "closing_transaction_ids": [],
    }, f"got {result!r}"

    assert result["average_close_price"] is None, (
        "an absent close price must be None, never an estimate;"
        f" got {result['average_close_price']!r}"
    )


def test_get_trade_details_returns_close_when_tradeable_without_close_evidence(
    monkeypatch,
):
    """CLOSE_WHEN_TRADEABLE is scheduled to close, not yet closed.

    No closing fill has occurred, so OANDA reports no close price, no
    close time and no closing transactions. The lookup still succeeds
    and must carry the same explicit absent values an open trade does,
    never the required-close-evidence treatment CLOSED receives.
    """

    from brokers.oanda_broker import OandaBroker

    broker = OandaBroker(
        api_key="unused-test-key",
        account_id="unused-test-account",
        base_url="https://example.invalid",
    )

    calls = []

    def fake_request(endpoint, method="GET", body=None):
        calls.append((endpoint, method, body))
        return {
            "trade": {
                "id": "779",
                "instrument": "USD_CAD",
                "price": "1.40124",
                "openTime": "2026-08-05T18:00:00.000000000Z",
                "state": "CLOSE_WHEN_TRADEABLE",
                "initialUnits": "-249694",
                "currentUnits": "-249694",
                "realizedPL": "0.00000",
            },
            "lastTransactionID": "267",
        }

    def never_place_order(*args, **kwargs):
        raise AssertionError("a trade lookup must not submit an order")

    def never_close_position(*args, **kwargs):
        raise AssertionError("a trade lookup must not close a position")

    monkeypatch.setattr(broker, "_make_request", fake_request)
    monkeypatch.setattr(broker, "place_order", never_place_order)
    monkeypatch.setattr(broker, "close_position", never_close_position)

    result = broker.get_trade_details("779")

    assert calls == [
        ("/trades/779", "GET", None),
    ], f"got requests {calls!r}"

    assert result == {
        "lookup_status": "FOUND",
        "broker_trade_id": "779",
        "currency_pair": "USD/CAD",
        "state": "CLOSE_WHEN_TRADEABLE",
        "open_price": 1.40124,
        "open_time": "2026-08-05T18:00:00.000000000Z",
        "initial_units": -249694.0,
        "current_units": -249694.0,
        "realized_pl": 0.0,
        "average_close_price": None,
        "close_time": "",
        "closing_transaction_ids": [],
    }, f"got {result!r}"

    assert result["state"] == "CLOSE_WHEN_TRADEABLE", (
        "the state must be reported verbatim, never collapsed to CLOSED;"
        f" got {result['state']!r}"
    )
    assert result["average_close_price"] is None, (
        "a trade that has not closed has no close price to report;"
        f" got {result['average_close_price']!r}"
    )
