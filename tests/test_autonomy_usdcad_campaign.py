"""Safety contract for the 50-CLOSED-trade USD/CAD forward test.

Counting, restart de-duplication, the stop-at-target rule, and the
take-profit geometry. No OANDA call, no network, no real database.
"""

import autonomy_usdcad_campaign as campaign
from autonomy_usdcad_forward_test import run_one_pass


def _closed(broker_trade_id, realized_pl=10.0):
    return {
        "broker_trade_id": broker_trade_id,
        "direction": "SHORT",
        "entry_price": 1.39411,
        "exit_price": 1.39211,
        "units": -248633.0,
        "opened_time": "2026-08-07T19:56:02.768965902Z",
        "closed_time": "2026-08-07T21:00:00.000000000Z",
        "realized_pl": realized_pl,
    }


class _NeverCycleDeps:
    """Dependencies whose every operational call fails the test."""

    def __init__(self):
        self.calls = []

    def __getitem__(self, key):
        self.calls.append(key)
        raise AssertionError(f"no collaborator may be used: {key!r}")


def test_campaign_starts_at_zero_of_fifty():
    state = campaign.new_campaign()

    summary = campaign.campaign_summary(state)
    assert summary["campaign_id"] == "USDCAD_MVP_50_01"
    assert summary["target_trades"] == 50
    assert summary["completed_closed_trades"] == 0
    assert summary["remaining_trades"] == 50
    assert summary["status"] == "RUNNING"
    assert campaign.is_complete(state) is False
    assert campaign.may_open_new_trade(state) is True


def test_reconciled_closed_trade_increments_exactly_once():
    state = campaign.new_campaign()

    assert campaign.record_closed_trade(state, _closed("777")) is True
    assert campaign.completed_closed_trades(state) == 1

    # Same broker trade id observed again must not be counted twice.
    assert campaign.record_closed_trade(state, _closed("777")) is False
    assert campaign.completed_closed_trades(state) == 1


def test_restart_does_not_double_count_the_same_broker_trade(tmp_path):
    path = str(tmp_path / "campaign.json")

    state = campaign.new_campaign()
    campaign.record_closed_trade(state, _closed("777"))
    campaign.save_campaign(path, state)

    reloaded = campaign.load_campaign(path)
    assert campaign.completed_closed_trades(reloaded) == 1

    assert campaign.record_closed_trade(reloaded, _closed("777")) is False
    assert campaign.completed_closed_trades(reloaded) == 1

    # A genuinely different trade still counts.
    assert campaign.record_closed_trade(reloaded, _closed("778")) is True
    assert campaign.completed_closed_trades(reloaded) == 2


def test_open_trade_does_not_increment_and_blocks_a_new_order():
    state = campaign.new_campaign()

    status = run_one_pass(
        dependencies=_NeverCycleDeps(),
        state=state,
        broker_positions=[{"currency_pair": "USD/CAD"}],
        filled=[{"status": "FILLED", "broker_trade_id": "777"}],
    )

    assert status == "POSITION_PRESENT"
    assert campaign.completed_closed_trades(state) == 0


def test_fiftieth_trade_completes_and_blocks_run_cycle():
    state = campaign.new_campaign()
    for number in range(50):
        assert campaign.record_closed_trade(state, _closed(str(number)))

    assert campaign.completed_closed_trades(state) == 50
    assert campaign.is_complete(state) is True
    assert state["status"] == "COMPLETE"
    assert campaign.may_open_new_trade(state) is False

    # Flat broker and flat local, yet no cycle may run: trade 51 is
    # unreachable. _NeverCycleDeps raises if any collaborator is used.
    status = run_one_pass(
        dependencies=_NeverCycleDeps(),
        state=state,
        broker_positions=[],
        filled=[],
    )
    assert status == "COMPLETE"
    assert campaign.completed_closed_trades(state) == 50


def test_pre_campaign_open_trade_is_reconciled_but_not_counted():
    """Rule B: only trades this runner opened join the 50-trade sample.

    Trade 267 opened before take-profit support, so it never carried the
    frozen 1R exit rule and must not contaminate the sample. Local state
    is still healed by reconciliation.
    """

    class _ClosedBroker:
        base_url = "https://api-fxpractice.oanda.com"

        def get_trade_details(self, trade_id):
            return {
                "lookup_status": "FOUND",
                "broker_trade_id": trade_id,
                "currency_pair": "USD/CAD",
                "state": "CLOSED",
                "average_close_price": 1.39211,
                "close_time": "2026-08-07T21:00:00.000000000Z",
                "realized_pl": 12.34,
            }

    class _Ledger:
        def __init__(self):
            self.close_trade_calls = []

        def close_trade(self, request_id, *, close_price, exit_timestamp):
            self.close_trade_calls.append(request_id)

    ledger = _Ledger()
    dependencies = {"broker": _ClosedBroker(), "state_manager": ledger}

    state = campaign.new_campaign()
    assert state["open_context"] == {}

    status = run_one_pass(
        dependencies=dependencies,
        state=state,
        broker_positions=[],
        filled=[
            {
                "request_id": "AI-PROPOSAL-PRE-CAMPAIGN",
                "currency_pair": "USD/CAD",
                "status": "FILLED",
                "broker_trade_id": "267",
            }
        ],
    )

    assert status == "PRE_CAMPAIGN_TRADE_NOT_COUNTED"
    assert campaign.completed_closed_trades(state) == 0
    assert ledger.close_trade_calls == ["AI-PROPOSAL-PRE-CAMPAIGN"]


def test_summary_reports_wins_losses_and_net_pl():
    state = campaign.new_campaign()
    campaign.record_closed_trade(state, _closed("1", realized_pl=100.0))
    campaign.record_closed_trade(state, _closed("2", realized_pl=-40.0))

    summary = campaign.campaign_summary(state)
    assert summary["completed_closed_trades"] == 2
    assert summary["remaining_trades"] == 48
    assert summary["wins"] == 1
    assert summary["losses"] == 1
    assert summary["net_realized_pl"] == 60.0
    assert summary["win_rate"] == 0.5


def test_take_profit_geometry_and_mandatory_stop_for_both_directions():
    """1R target on the correct side, stop unchanged and mandatory."""

    from brokers.oanda_broker import OandaBroker

    broker = OandaBroker(
        api_key="unused-test-key",
        account_id="unused-test-account",
        base_url="https://api-fxpractice.oanda.com",
    )

    calls = []

    def fake_request(endpoint, method="GET", body=None):
        calls.append((endpoint, method, body))
        return {
            "orderFillTransaction": {
                "id": "900",
                "tradeOpened": {"tradeID": "777"},
                "units": body["order"]["units"],
                "price": "1.40000",
                "time": "2026-08-07T12:00:00.000000000Z",
            }
        }

    import pytest

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(broker, "_make_request", fake_request)
    try:
        # LONG: entry 1.40000, stop below, take profit above.
        broker.place_order(
            {
                "currency_pair": "USD/CAD",
                "direction": "Long",
                "position_size": 5000,
                "stop_loss_price": 1.39800,
                "take_profit_price": 1.40200,
            }
        )
        long_order = calls[-1][2]["order"]
        assert long_order["stopLossOnFill"] == {"price": "1.398"}
        assert long_order["takeProfitOnFill"] == {"price": "1.402"}

        # SHORT: entry 1.40000, stop above, take profit below.
        broker.place_order(
            {
                "currency_pair": "USD/CAD",
                "direction": "Short",
                "position_size": 5000,
                "stop_loss_price": 1.40200,
                "take_profit_price": 1.39800,
            }
        )
        short_order = calls[-1][2]["order"]
        assert short_order["stopLossOnFill"] == {"price": "1.402"}
        assert short_order["takeProfitOnFill"] == {"price": "1.398"}

        # No target supplied: the stop is still attached, unconditionally.
        broker.place_order(
            {
                "currency_pair": "USD/CAD",
                "direction": "Long",
                "position_size": 5000,
                "stop_loss_price": 1.39800,
            }
        )
        plain_order = calls[-1][2]["order"]
        assert plain_order["stopLossOnFill"] == {"price": "1.398"}
        assert "takeProfitOnFill" not in plain_order

        # A missing stop is still rejected before any request.
        before = len(calls)
        rejected = broker.place_order(
            {
                "currency_pair": "USD/CAD",
                "direction": "Long",
                "position_size": 5000,
                "take_profit_price": 1.40200,
            }
        )
        assert rejected["execution_status"] == "Rejected"
        assert len(calls) == before
    finally:
        monkeypatch.undo()


def test_practice_endpoint_remains_mandatory():
    """The campaign inherits the cycle's practice-only refusal."""

    import autonomy_usdcad_cycle as cycle
    import autonomy_usdcad_wiring as wiring

    assert cycle.PRACTICE_BASE_URL == "https://api-fxpractice.oanda.com"
    assert wiring.PRACTICE_BASE_URL == "https://api-fxpractice.oanda.com"

    class _LiveBroker:
        base_url = "https://api-fxtrade.oanda.com"

        def get_open_positions(self):
            raise AssertionError("a live endpoint must be refused first")

    def _never(*args, **kwargs):
        raise AssertionError("a live endpoint must take no action")

    result = cycle.run_cycle(
        broker=_LiveBroker(),
        state_manager=_never,
        signal_provider=_never,
        proposal_queue=_never,
        executor=_never,
    )
    assert result["outcome"] == "BLOCKED_NON_PRACTICE_ENVIRONMENT"
