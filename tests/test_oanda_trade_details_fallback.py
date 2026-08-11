"""404 NO_SUCH_TRADE fallback contract for get_trade_details.

A closed trade can vanish from /trades/{id}. Only that exact signal
earns a read-only transaction-history reconstruction, and only when the
evidence names the exact trade id on USD/CAD. Every other error, and
every incomplete or ambiguous history, fails closed.

_make_request is patched in every test, so no HTTP request is possible.
"""

import pytest

PRACTICE_BASE_URL = "https://api-fxpractice.oanda.com"

NO_SUCH_TRADE_ERROR = (
    'OANDA API error 404: {"errorCode":"NO_SUCH_TRADE",'
    '"errorMessage":"The Trade specified does not exist"}'
)

# Trade 288: opened by fill 288, closed by fill 291 on the take profit.
OPENING_FILL = {
    "id": "288",
    "type": "ORDER_FILL",
    "instrument": "USD_CAD",
    "time": "2026-08-10T09:00:00.000000000Z",
    "price": "1.39373",
    "tradeOpened": {"tradeID": "288", "units": "-247000"},
}

CLOSING_FILL = {
    "id": "291",
    "type": "ORDER_FILL",
    "instrument": "USD_CAD",
    "time": "2026-08-10T11:42:31.000000000Z",
    "reason": "TAKE_PROFIT_ORDER",
    "price": "1.39172",
    "tradesClosed": [
        {
            "tradeID": "288",
            "units": "247000",
            "price": "1.39172",
            "realizedPL": "574.4323",
        }
    ],
}

CANCEL_TRANSACTION = {
    "id": "292",
    "type": "ORDER_CANCEL",
    "orderID": "290",
    "reason": "LINKED_TRADE_CLOSED",
}


def _broker():
    from brokers.oanda_broker import OandaBroker

    return OandaBroker(
        api_key="unused-test-key",
        account_id="unused-test-account",
        base_url=PRACTICE_BASE_URL,
    )


def _patch(monkeypatch, broker, handler):
    calls = []

    def fake_request(endpoint, method="GET", body=None):
        calls.append((endpoint, method, body))
        return handler(endpoint)

    monkeypatch.setattr(broker, "_make_request", fake_request)
    return calls


def test_404_no_such_trade_with_exact_closing_transaction_rebuilds_closed(
    monkeypatch,
):
    broker = _broker()

    def handler(endpoint):
        if endpoint.startswith("/trades/"):
            raise RuntimeError(NO_SUCH_TRADE_ERROR)
        return {
            "transactions": [
                OPENING_FILL,
                CLOSING_FILL,
                CANCEL_TRANSACTION,
            ],
            "lastTransactionID": "292",
        }

    calls = _patch(monkeypatch, broker, handler)

    result = broker.get_trade_details("288")

    assert result["lookup_status"] == "FOUND", f"got {result!r}"
    assert result["broker_trade_id"] == "288"
    assert result["currency_pair"] == "USD/CAD"
    assert result["state"] == "CLOSED"
    assert result["average_close_price"] == 1.39172
    assert result["close_time"] == "2026-08-10T11:42:31.000000000Z"
    assert result["realized_pl"] == 574.4323
    assert result["closing_transaction_ids"] == ["291"]

    # Opening evidence enriches the ledger when the history carries it.
    assert result["open_price"] == 1.39373
    assert result["initial_units"] == -247000.0
    assert result["open_time"] == "2026-08-10T09:00:00.000000000Z"
    assert result["current_units"] == 0.0

    # Exactly two reads, both GET, both read-only.
    assert [call[0] for call in calls] == [
        "/trades/288",
        "/transactions/sinceid?id=287",
    ], f"got {calls!r}"
    assert all(call[1] == "GET" and call[2] is None for call in calls)

    # The reconstructed shape satisfies the existing reconciler.
    from autonomy_usdcad_reconcile import reconcile_closed_usdcad_trade

    class _Ledger:
        def __init__(self):
            self.calls = []

        def close_trade(self, request_id, *, close_price, exit_timestamp):
            self.calls.append((request_id, close_price, exit_timestamp))

    ledger = _Ledger()
    reconciliation = reconcile_closed_usdcad_trade(
        broker=broker,
        state_manager=ledger,
        broker_positions=[],
        local_filled=[
            {
                "request_id": "AI-PROPOSAL-288",
                "currency_pair": "USD/CAD",
                "status": "FILLED",
                "broker_trade_id": "288",
            }
        ],
    )
    assert reconciliation["outcome"] == "RECONCILED_CLOSED_TRADE"
    assert reconciliation["close_price"] == 1.39172
    assert reconciliation["realized_pl"] == 574.4323
    assert ledger.calls == [
        (
            "AI-PROPOSAL-288",
            1.39172,
            "2026-08-10T11:42:31.000000000Z",
        )
    ]


@pytest.mark.parametrize(
    "case_name, transactions",
    [
        ("no_transactions", []),
        ("only_the_opening_fill", [OPENING_FILL]),
        (
            "closes_a_different_trade",
            [
                {
                    **CLOSING_FILL,
                    "tradesClosed": [
                        {
                            "tradeID": "999",
                            "price": "1.39172",
                            "realizedPL": "574.4323",
                        }
                    ],
                }
            ],
        ),
        (
            "wrong_instrument",
            [{**CLOSING_FILL, "instrument": "EUR_USD"}],
        ),
        (
            "missing_close_price",
            [
                {
                    **CLOSING_FILL,
                    "tradesClosed": [
                        {"tradeID": "288", "realizedPL": "574.4323"}
                    ],
                }
            ],
        ),
        (
            "ambiguous_duplicate_closes",
            [CLOSING_FILL, {**CLOSING_FILL, "id": "293"}],
        ),
    ],
)
def test_404_without_unambiguous_closing_evidence_fails_closed(
    case_name, transactions, monkeypatch
):
    broker = _broker()

    def handler(endpoint):
        if endpoint.startswith("/trades/"):
            raise RuntimeError(NO_SUCH_TRADE_ERROR)
        return {"transactions": transactions}

    _patch(monkeypatch, broker, handler)

    result = broker.get_trade_details("288")

    assert result["lookup_status"] != "FOUND", (
        f"[{case_name}] insufficient evidence must not reconstruct a"
        f" CLOSED trade; got {result!r}"
    )
    assert result.get("state") != "CLOSED", f"[{case_name}] got {result!r}"
    assert result.get("reason"), f"[{case_name}] must explain the refusal"


@pytest.mark.parametrize(
    "case_name, message",
    [
        (
            "unrelated_404",
            'OANDA API error 404: {"errorCode":"NO_SUCH_ACCOUNT"}',
        ),
        ("bare_404", "OANDA API error 404: not found"),
        ("unauthorized", "OANDA API error 401: Insufficient authorization"),
        ("server_error", "OANDA API error 503: service unavailable"),
    ],
)
def test_non_no_such_trade_errors_fail_closed_without_history_lookup(
    case_name, message, monkeypatch
):
    """Only 404 NO_SUCH_TRADE earns a reconstruction attempt."""

    broker = _broker()
    calls = []

    def fake_request(endpoint, method="GET", body=None):
        calls.append(endpoint)
        raise RuntimeError(message)

    monkeypatch.setattr(broker, "_make_request", fake_request)

    result = broker.get_trade_details("288")

    assert result["lookup_status"] == "ERROR", (
        f"[{case_name}] got {result!r}"
    )
    assert result.get("state") != "CLOSED"
    assert calls == ["/trades/288"], (
        f"[{case_name}] no transaction history may be read; got {calls!r}"
    )


def test_successful_trade_details_behaviour_is_unchanged(monkeypatch):
    """A normal response still maps exactly as before, with one GET."""

    broker = _broker()

    def handler(endpoint):
        return {
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

    calls = _patch(monkeypatch, broker, handler)

    result = broker.get_trade_details("777")

    assert calls == [("/trades/777", "GET", None)], f"got {calls!r}"
    assert result == {
        "lookup_status": "FOUND",
        "broker_trade_id": "777",
        "currency_pair": "USD/CAD",
        "state": "CLOSED",
        "open_price": 1.40124,
        "open_time": "2026-08-02T23:57:18.326354060Z",
        "initial_units": -249694.0,
        "current_units": 0.0,
        "realized_pl": -424.48,
        "average_close_price": 1.40294,
        "close_time": "2026-08-03T01:29:59.000000000Z",
        "closing_transaction_ids": ["265"],
    }, f"got {result!r}"
