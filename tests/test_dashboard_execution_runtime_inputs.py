import importlib
import math
import sys
from datetime import datetime, timedelta, timezone

import pytest


_DEFAULT = object()
_NOW_UTC = datetime(2026, 7, 15, 16, 0, tzinfo=timezone.utc)
_PROPOSAL = {
    "proposal_id": "PROP-RUNTIME-1",
    "pair": "EUR/USD",
    "direction": "LONG",
    "status": "APPROVED",
}


class _RecordingBroker:
    def __init__(self, snapshot, error=None):
        self.snapshot = snapshot
        self.error = error
        self.calls = 0

    def get_account_snapshot(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.snapshot


class _RecordingQuoteProvider:
    def __init__(self, quote, error=None):
        self.quote = quote
        self.error = error
        self.calls = []

    def get_quote(self, pair):
        self.calls.append(pair)
        if self.error is not None:
            raise self.error
        return self.quote


class _RecordingDrawdownProvider:
    def __init__(self, drawdown, error=None):
        self.drawdown = drawdown
        self.error = error
        self.calls = []

    def get_drawdown_fraction(self, account_snapshot):
        self.calls.append(account_snapshot)
        if self.error is not None:
            raise self.error
        return self.drawdown


class _RecordingBridge:
    def __init__(self):
        self.calls = []
        self.result = {
            "success": True,
            "message": "Forwarded",
            "request_id": "AI-PROPOSAL-PROP-RUNTIME-1",
            "execution_result": {},
        }

    def __call__(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self.result


def _account_snapshot():
    from brokers.broker_interface import AccountSnapshot

    return AccountSnapshot(
        nav=100_000.0,
        balance=100_000.0,
        currency="USD",
        margin_available=95_000.0,
    )


def _quote(
    *,
    pair="EUR/USD",
    bid=1.0998,
    ask=1.1000,
    timestamp=_NOW_UTC,
):
    return {
        "currency_pair": pair,
        "bid": bid,
        "ask": ask,
        "timestamp": timestamp,
    }


def _execute(
    *,
    proposal=_DEFAULT,
    snapshot=_DEFAULT,
    snapshot_error=None,
    quote=_DEFAULT,
    quote_error=None,
    drawdown=0.02,
    drawdown_error=None,
    stop_loss_price=1.095,
    now_utc=_NOW_UTC,
    max_quote_age_seconds=60.0,
):
    from dashboard.execution_runtime_inputs import (
        execute_approved_proposal_with_runtime_inputs,
    )

    resolved_proposal = (
        dict(_PROPOSAL) if proposal is _DEFAULT else proposal
    )
    resolved_snapshot = (
        _account_snapshot() if snapshot is _DEFAULT else snapshot
    )
    resolved_quote = _quote() if quote is _DEFAULT else quote

    broker = _RecordingBroker(resolved_snapshot, snapshot_error)
    quote_provider = _RecordingQuoteProvider(resolved_quote, quote_error)
    drawdown_provider = _RecordingDrawdownProvider(
        drawdown,
        drawdown_error,
    )
    bridge = _RecordingBridge()
    orchestrator = object()
    state_manager = object()

    result = execute_approved_proposal_with_runtime_inputs(
        proposal=resolved_proposal,
        orchestrator=orchestrator,
        state_manager=state_manager,
        max_currency_exposure=100.0,
        broker=broker,
        quote_provider=quote_provider,
        drawdown_provider=drawdown_provider,
        stop_loss_price=stop_loss_price,
        now_utc=now_utc,
        max_quote_age_seconds=max_quote_age_seconds,
        bridge_execute=bridge,
    )
    return result, {
        "proposal": resolved_proposal,
        "snapshot": resolved_snapshot,
        "broker": broker,
        "quote_provider": quote_provider,
        "drawdown_provider": drawdown_provider,
        "bridge": bridge,
        "orchestrator": orchestrator,
        "state_manager": state_manager,
    }


def _assert_failure(result, harness, *evidence_terms):
    assert result["success"] is False
    assert isinstance(result["message"], str)
    message = result["message"].lower().replace("_", " ").replace("-", " ")
    assert any(term.lower() in message for term in evidence_terms)
    assert harness["bridge"].calls == []


def test_runtime_input_module_import_is_safe():
    before = set(sys.modules)

    module = importlib.import_module("dashboard.execution_runtime_inputs")

    newly_loaded = set(sys.modules) - before
    new_roots = {name.partition(".")[0] for name in newly_loaded}
    assert module.__name__ == "dashboard.execution_runtime_inputs"
    assert not (new_roots & {"streamlit", "market_data", "sqlite3", "dotenv"})
    assert "dashboard.app" not in newly_loaded
    assert "brokers.oanda_broker" not in newly_loaded


def test_execution_runtime_inputs_is_frozen_with_exact_fields():
    from dataclasses import FrozenInstanceError, fields

    from dashboard.execution_runtime_inputs import ExecutionRuntimeInputs

    snapshot = _account_snapshot()
    result = ExecutionRuntimeInputs(
        account_snapshot=snapshot,
        entry_price=1.1,
        stop_distance_pips=50.0,
        drawdown_fraction=0.02,
    )

    assert [field.name for field in fields(result)] == [
        "account_snapshot",
        "entry_price",
        "stop_distance_pips",
        "drawdown_fraction",
    ]
    assert result.account_snapshot is snapshot
    with pytest.raises(FrozenInstanceError):
        result.entry_price = 2.0


def test_long_uses_fresh_ask_and_forwards_resolved_inputs_once():
    quote = _quote(timestamp=_NOW_UTC - timedelta(seconds=60))

    result, harness = _execute(quote=quote, drawdown=0.03)

    assert result is harness["bridge"].result
    assert harness["broker"].calls == 1
    assert harness["quote_provider"].calls == ["EUR/USD"]
    assert harness["drawdown_provider"].calls == [harness["snapshot"]]
    assert len(harness["bridge"].calls) == 1

    call = harness["bridge"].calls[0]
    assert set(call) == {
        "proposal",
        "orchestrator",
        "state_manager",
        "max_currency_exposure",
        "account_snapshot",
        "entry_price",
        "stop_distance_pips",
        "drawdown_fraction",
    }
    assert call["proposal"] is harness["proposal"]
    assert call["orchestrator"] is harness["orchestrator"]
    assert call["state_manager"] is harness["state_manager"]
    assert call["max_currency_exposure"] == 100.0
    assert call["account_snapshot"] is harness["snapshot"]
    assert call["entry_price"] == 1.1000
    assert call["stop_distance_pips"] == pytest.approx(50.0)
    assert call["drawdown_fraction"] == 0.03


def test_short_uses_fresh_bid_and_jpy_pip_distance():
    proposal = dict(_PROPOSAL, pair="USD/JPY", direction="SHORT")
    quote = _quote(
        pair="USD/JPY",
        bid=149.98,
        ask=150.00,
    )

    _, harness = _execute(
        proposal=proposal,
        quote=quote,
        stop_loss_price=150.48,
    )

    assert harness["quote_provider"].calls == ["USD/JPY"]
    assert len(harness["bridge"].calls) == 1
    call = harness["bridge"].calls[0]
    assert call["entry_price"] == 149.98
    assert call["stop_distance_pips"] == pytest.approx(50.0)


def test_measured_zero_drawdown_is_forwarded_without_defaulting():
    result, harness = _execute(
        quote=_quote(bid=1.1, ask=1.1),
        drawdown=0.0,
    )

    assert result is harness["bridge"].result
    assert harness["drawdown_provider"].calls == [harness["snapshot"]]
    assert harness["bridge"].calls[0]["drawdown_fraction"] == 0.0

    _, upper_boundary = _execute(drawdown=1.0)
    assert upper_boundary["bridge"].calls[0]["drawdown_fraction"] == 1.0


def test_invalid_quote_evidence_fails_closed_before_drawdown_or_dispatch():
    result, harness = _execute(quote_error=RuntimeError("quote unavailable"))
    _assert_failure(result, harness, "quote")
    assert harness["quote_provider"].calls == ["EUR/USD"]
    assert harness["drawdown_provider"].calls == []

    missing_bid = _quote()
    missing_bid.pop("bid")
    missing_ask = _quote()
    missing_ask.pop("ask")
    missing_pair = _quote()
    missing_pair.pop("currency_pair")
    missing_timestamp = _quote()
    missing_timestamp.pop("timestamp")
    quote_cases = [
        (None, ("quote",)),
        ([], ("quote",)),
        (
            _quote(timestamp=_NOW_UTC - timedelta(seconds=60, microseconds=1)),
            ("timestamp", "stale"),
        ),
        (
            _quote(timestamp=_NOW_UTC + timedelta(microseconds=1)),
            ("timestamp", "future"),
        ),
        (_quote(timestamp=_NOW_UTC.replace(tzinfo=None)), ("timestamp",)),
        (_quote(timestamp="2026-07-15T16:00:00Z"), ("timestamp",)),
        (_quote(pair="GBP/USD"), ("pair",)),
        (_quote(pair=123), ("pair",)),
        (missing_pair, ("pair",)),
        (missing_bid, ("bid",)),
        (missing_ask, ("ask",)),
        (missing_timestamp, ("timestamp",)),
        (_quote(bid=1.2, ask=1.1), ("ask", "quote")),
    ]
    for field in ("bid", "ask"):
        for invalid_value in (
            None,
            True,
            "1.1",
            0.0,
            -1.0,
            math.nan,
            math.inf,
            -math.inf,
        ):
            invalid_quote = _quote()
            invalid_quote[field] = invalid_value
            quote_cases.append((invalid_quote, (field, "quote")))

    for invalid_quote, evidence_terms in quote_cases:
        result, harness = _execute(quote=invalid_quote)
        _assert_failure(result, harness, *evidence_terms)
        assert harness["bridge"].calls == []
        assert harness["drawdown_provider"].calls == []


def test_invalid_stop_evidence_fails_closed_without_fallback():
    invalid_long_stops = (
        None,
        True,
        "1.095",
        0.0,
        -1.0,
        math.nan,
        math.inf,
        -math.inf,
        1.1000,
        1.1001,
    )
    for invalid_stop in invalid_long_stops:
        result, harness = _execute(stop_loss_price=invalid_stop)
        _assert_failure(result, harness, "stop")
        assert harness["drawdown_provider"].calls == []

    short_proposal = dict(_PROPOSAL, direction="SHORT")
    for invalid_stop in (1.0998, 1.0997):
        result, harness = _execute(
            proposal=short_proposal,
            stop_loss_price=invalid_stop,
        )
        _assert_failure(result, harness, "stop")
        assert harness["drawdown_provider"].calls == []

    result, harness = _execute(
        quote=_quote(bid=sys.float_info.max, ask=sys.float_info.max),
        stop_loss_price=1.0,
    )
    _assert_failure(result, harness, "stop", "distance")
    assert harness["drawdown_provider"].calls == []


def test_invalid_drawdown_evidence_fails_closed_after_one_provider_call():
    result, harness = _execute(
        drawdown_error=RuntimeError("drawdown unavailable"),
    )
    _assert_failure(result, harness, "drawdown")
    assert harness["drawdown_provider"].calls == [harness["snapshot"]]

    for invalid_drawdown in (
        None,
        True,
        "0.0",
        -0.01,
        1.01,
        math.nan,
        math.inf,
        -math.inf,
    ):
        result, harness = _execute(drawdown=invalid_drawdown)
        _assert_failure(result, harness, "drawdown")
        assert harness["drawdown_provider"].calls == [harness["snapshot"]]


def test_invalid_snapshot_fails_before_quote_drawdown_or_dispatch():
    cases = [
        {"snapshot_error": RuntimeError("account unavailable")},
        {"snapshot": None},
        {"snapshot": object()},
        {"snapshot": {"nav": 100_000.0}},
    ]
    for case in cases:
        result, harness = _execute(**case)
        _assert_failure(result, harness, "account", "snapshot")
        assert harness["broker"].calls == 1
        assert harness["quote_provider"].calls == []
        assert harness["drawdown_provider"].calls == []


def test_invalid_proposal_pair_and_direction_never_dispatch():
    invalid_proposals = []
    for invalid_pair in (None, "", "EURUSD", "EU/USD", "EUR/USDD", 123):
        proposal = dict(_PROPOSAL)
        proposal["pair"] = invalid_pair
        invalid_proposals.append((proposal, ("pair",)))
    missing_pair = dict(_PROPOSAL)
    missing_pair.pop("pair")
    invalid_proposals.append((missing_pair, ("pair",)))

    for invalid_direction in (None, "", "long", "SHORTER", "BUY", True):
        proposal = dict(_PROPOSAL)
        proposal["direction"] = invalid_direction
        invalid_proposals.append((proposal, ("direction",)))
    missing_direction = dict(_PROPOSAL)
    missing_direction.pop("direction")
    invalid_proposals.append((missing_direction, ("direction",)))

    for proposal, evidence_terms in invalid_proposals:
        result, harness = _execute(proposal=proposal)
        _assert_failure(result, harness, *evidence_terms)


def test_invalid_clock_and_quote_age_policy_never_dispatch():
    for invalid_now in (None, "2026-07-15T16:00:00Z", _NOW_UTC.replace(tzinfo=None)):
        result, harness = _execute(now_utc=invalid_now)
        _assert_failure(result, harness, "now utc", "time")

    for invalid_age in (
        None,
        True,
        "60",
        0.0,
        -1.0,
        math.nan,
        math.inf,
        -math.inf,
    ):
        result, harness = _execute(max_quote_age_seconds=invalid_age)
        _assert_failure(result, harness, "quote age", "max quote age", "age")
