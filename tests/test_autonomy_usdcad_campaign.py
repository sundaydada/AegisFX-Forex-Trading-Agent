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


# --- slice 4: observational context persisted into open_context ------

import autonomy_usdcad_forward_test as forward_test

EXPECTED_CONTEXT_KEYS = {
    "confidence",
    "stop_loss_price",
    "take_profit_price",
    "regime",
    "trend",
    "volatility",
    "range_percentile",
    "position_in_range",
    "recommended_strategy",
    "reason",
    "summary",
    "proposal_id",
    "entry_bid",
    "entry_ask",
    "entry_spread",
    "entry_spread_pips",
}

# A 2-pip spread as float arithmetic actually produces it.
RAW_SPREAD = 1.40820 - 1.40800
RAW_SPREAD_PIPS = RAW_SPREAD / 0.0001


def _executed_result(reason="Trending regime.", summary="Momentum up."):
    return {
        "outcome": "PROPOSAL_EXECUTED",
        "proposal_id": "PROP-AUTO-SLICE4-0001",
        "stop_loss_price": 1.40620,
        "take_profit_price": 1.41020,
        "entry_bid": 1.40800,
        "entry_ask": 1.40820,
        "entry_spread": RAW_SPREAD,
        "entry_spread_pips": RAW_SPREAD_PIPS,
        "signal": {
            "trade_bias": "LONG",
            "confidence": 85,
            "execution_allowed": True,
            "regime": "Trending",
            "trend": "up",
            "volatility": "low",
            "range_percentile": 15.0,
            "position_in_range": "LOWER",
            "recommended_strategy": "Momentum_v1",
            "reason": reason,
            "summary": summary,
        },
        "execution_result": {
            "execution_result": {
                "execution_status": "Filled",
                "broker_trade_id": "901",
            }
        },
    }


def _execute_one(monkeypatch, state, result):
    """Drive one flat pass whose cycle returns the given result."""
    monkeypatch.setattr(forward_test, "run_cycle", lambda **kwargs: result)
    return forward_test.run_one_pass(
        dependencies={
            "broker": object(),
            "state_manager": object(),
            "signal_provider": object(),
            "proposal_queue": object(),
            "executor": object(),
        },
        state=state,
        broker_positions=[],
        filled=[],
    )


def test_executed_trade_persists_observational_context(monkeypatch):
    state = campaign.new_campaign()
    result = _executed_result()

    status = _execute_one(monkeypatch, state, result)

    assert status == "PROPOSAL_EXECUTED"
    stored = state["open_context"]["901"]
    assert set(stored) == EXPECTED_CONTEXT_KEYS, (
        f"got context keys {sorted(stored)!r}"
    )

    # decision fields are byte-identical to the cycle result
    assert stored["confidence"] == 85
    assert stored["stop_loss_price"] == result["stop_loss_price"]
    assert stored["take_profit_price"] == result["take_profit_price"]

    # observational fields carried through unchanged
    assert stored["regime"] == "Trending"
    assert stored["trend"] == "up"
    assert stored["volatility"] == "low"
    assert stored["range_percentile"] == 15.0
    assert stored["position_in_range"] == "LOWER"
    assert stored["recommended_strategy"] == "Momentum_v1"
    assert stored["proposal_id"] == "PROP-AUTO-SLICE4-0001"
    assert stored["entry_bid"] == 1.40800
    assert stored["entry_ask"] == 1.40820

    # raw spread preserved exactly; pips rounded only at persistence
    assert stored["entry_spread"] == RAW_SPREAD, (
        f"entry_spread must stay unrounded; got {stored['entry_spread']!r}"
    )
    assert stored["entry_spread_pips"] == round(RAW_SPREAD_PIPS, 4)
    assert result["entry_spread_pips"] == RAW_SPREAD_PIPS, (
        "rounding must not alter the run_cycle return value"
    )

    # counting is untouched: an open trade is not a closed trade
    assert campaign.completed_closed_trades(state) == 0


def test_persisted_model_text_is_capped_without_altering_the_signal(
    monkeypatch,
):
    state = campaign.new_campaign()
    long_reason = "R" * 500
    long_summary = "S" * 500
    result = _executed_result(reason=long_reason, summary=long_summary)

    _execute_one(monkeypatch, state, result)

    stored = state["open_context"]["901"]
    assert len(stored["reason"]) == 300, f"got {len(stored['reason'])}"
    assert len(stored["summary"]) == 300, f"got {len(stored['summary'])}"
    assert stored["reason"] == long_reason[:300]
    assert stored["summary"] == long_summary[:300]

    # the signal provider's own values are never mutated
    assert len(result["signal"]["reason"]) == 500
    assert len(result["signal"]["summary"]) == 500

    # a short string is stored whole, not padded or truncated
    short_state = campaign.new_campaign()
    _execute_one(monkeypatch, short_state, _executed_result())
    assert short_state["open_context"]["901"]["reason"] == "Trending regime."


def test_missing_observational_values_persist_as_none(monkeypatch):
    """A sparse cycle result must not invent or backfill context."""

    state = campaign.new_campaign()
    sparse = {
        "outcome": "PROPOSAL_EXECUTED",
        "stop_loss_price": 1.40620,
        "take_profit_price": 1.41020,
        "signal": {"confidence": 85},
        "execution_result": {
            "execution_result": {"broker_trade_id": "902"}
        },
    }

    _execute_one(monkeypatch, state, sparse)

    stored = state["open_context"]["902"]
    assert set(stored) == EXPECTED_CONTEXT_KEYS
    for field in (
        "regime",
        "trend",
        "volatility",
        "range_percentile",
        "position_in_range",
        "recommended_strategy",
        "reason",
        "summary",
        "proposal_id",
        "entry_bid",
        "entry_ask",
        "entry_spread",
        "entry_spread_pips",
    ):
        assert stored[field] is None, (
            f"{field!r} must stay absent, not be invented;"
            f" got {stored[field]!r}"
        )
    assert stored["stop_loss_price"] == 1.40620
    assert stored["take_profit_price"] == 1.41020


def test_legacy_sparse_open_context_still_counts_exactly_once(tmp_path):
    """Pre-slice-4 entries with three keys remain valid and countable."""

    path = str(tmp_path / "legacy.json")
    state = campaign.new_campaign()
    # exactly the shape written before this slice
    state["open_context"]["777"] = {
        "confidence": 85,
        "stop_loss_price": 1.39573,
        "take_profit_price": 1.39173,
    }
    campaign.save_campaign(path, state)

    reloaded = campaign.load_campaign(path)
    assert set(reloaded["open_context"]["777"]) == {
        "confidence",
        "stop_loss_price",
        "take_profit_price",
    }

    assert campaign.record_closed_trade(reloaded, _closed("777")) is True
    assert campaign.completed_closed_trades(reloaded) == 1
    assert campaign.record_closed_trade(reloaded, _closed("777")) is False
    assert campaign.completed_closed_trades(reloaded) == 1

    row = reloaded["trades"][0]
    assert row["confidence"] == 85
    assert row["stop_price"] == 1.39573
    assert row["take_profit_price"] == 1.39173
    assert campaign.campaign_summary(reloaded)["completed_closed_trades"] == 1


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
