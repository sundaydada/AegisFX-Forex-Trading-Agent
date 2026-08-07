"""Happy-path contract for USD/CAD close reconciliation.

One locally-FILLED trade, no broker position, and one confirmed
broker-side close: the local row is marked closed with the broker's own
exit evidence. Both collaborators are fakes, so no OANDA call and no
database access occur.
"""

from autonomy_usdcad_reconcile import reconcile_closed_usdcad_trade


class _RecordingBroker:
    def __init__(self, details):
        self._details = details
        self.calls = []

    def get_trade_details(self, trade_id):
        self.calls.append(trade_id)
        return self._details


class _RecordingStateManager:
    def __init__(self):
        self.calls = []

    def close_trade(self, request_id, *, close_price, exit_timestamp):
        self.calls.append(
            (
                request_id,
                {
                    "close_price": close_price,
                    "exit_timestamp": exit_timestamp,
                },
            )
        )


def test_reconciles_one_confirmed_closed_usdcad_trade():
    broker = _RecordingBroker(
        {
            "lookup_status": "FOUND",
            "broker_trade_id": "777",
            "currency_pair": "USD/CAD",
            "state": "CLOSED",
            "average_close_price": 1.40294,
            "close_time": "2026-08-03T01:29:59.000000000Z",
            "realized_pl": -424.48,
        }
    )
    state_manager = _RecordingStateManager()

    result = reconcile_closed_usdcad_trade(
        broker=broker,
        state_manager=state_manager,
        broker_positions=[],
        local_filled=[
            {
                "request_id": "AI-PROPOSAL-TEST",
                "currency_pair": "USD/CAD",
                "status": "FILLED",
                "broker_trade_id": "777",
            }
        ],
    )

    assert broker.calls == ["777"], f"got broker calls {broker.calls!r}"

    assert state_manager.calls == [
        (
            "AI-PROPOSAL-TEST",
            {
                "close_price": 1.40294,
                "exit_timestamp": (
                    "2026-08-03T01:29:59.000000000Z"
                ),
            },
        )
    ], f"got close_trade calls {state_manager.calls!r}"

    assert result == {
        "outcome": "RECONCILED_CLOSED_TRADE",
        "request_id": "AI-PROPOSAL-TEST",
        "broker_trade_id": "777",
        "close_price": 1.40294,
        "exit_timestamp": "2026-08-03T01:29:59.000000000Z",
        "realized_pl": -424.48,
    }, f"got {result!r}"
