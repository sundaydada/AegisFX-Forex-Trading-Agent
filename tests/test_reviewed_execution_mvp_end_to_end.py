"""Mocked end-to-end MVP validation of the reviewed execution chain.

Drives the real controller -> wiring -> action adapter -> runtime-input
resolver -> bridge -> orchestrator -> trade persistence path with only
the OANDA broker replaced by a deterministic local fake. No network,
no Streamlit, no dashboard.app, no real order.
"""

import sqlite3
import sys
from collections.abc import Mapping
from datetime import datetime, timezone

import pytest


_NOW_UTC = datetime(2026, 7, 16, 16, 0, 0, tzinfo=timezone.utc)


def test_reviewed_execution_mvp_completes_with_fake_broker(
    monkeypatch,
    tmp_path,
):
    import dashboard.execution_wiring as wiring_module
    import execution.trading_control as trading_control
    from ai.proposal_approval_queue import ProposalApprovalQueue
    from brokers.broker_interface import AccountSnapshot
    from dashboard.reviewed_execution_controller import (
        execute_reviewed_proposal_from_dashboard,
    )
    from execution.persistent_trade_state_manager import (
        PersistentTradeStateManager,
    )

    trade_db_path = str(tmp_path / "trade-state.db")
    drawdown_db_path = str(tmp_path / "drawdown.db")
    start_of_day_nav_db_path = str(tmp_path / "start-of-day-nav.db")
    approval_db_path = str(tmp_path / "approvals.db")

    # Isolate operator machine state: the orchestrator's real
    # is_trading_enabled logic still runs, but against a tmp_path flag
    # location (absent file -> trading enabled by default).
    monkeypatch.setattr(
        trading_control,
        "FLAG_FILE",
        str(tmp_path / "trading_enabled.flag"),
    )

    constructed_brokers = []
    account_snapshot_calls = []
    submitted_orders = []

    class _FakeBroker:
        def __init__(self, *, api_key, account_id, base_url):
            constructed_brokers.append(
                {"account_id": account_id, "base_url": base_url}
            )

        def get_account_snapshot(self):
            account_snapshot_calls.append(True)
            return AccountSnapshot(
                nav=100_000.0,
                balance=100_000.0,
                currency="USD",
                margin_available=95_000.0,
            )

        def get_quote(self, pair):
            return {
                "currency_pair": pair,
                "bid": 1.0998,
                "ask": 1.1000,
                "timestamp": _NOW_UTC,
            }

        def place_order(self, order):
            submitted_orders.append(dict(order))
            return {
                "execution_status": "Filled",
                "broker_order_id": "FAKE-ORDER-1",
                "currency_pair": order["currency_pair"],
                "direction": order["direction"],
                "units": float(order["position_size"]),
                "fill_price": 1.1000,
                "timestamp": _NOW_UTC.isoformat(),
            }

    monkeypatch.setattr(wiring_module, "OandaBroker", _FakeBroker)

    setup_queue = ProposalApprovalQueue(db_path=approval_db_path)
    try:
        added = setup_queue.add_proposals([
            {
                "pair": "EUR/USD",
                "direction": "LONG",
                "suggested_size": 1000.0,
                "confidence": 90,
                "strategy": "Trend Following",
                "reason": "mvp end-to-end validation",
            }
        ])
        assert added == 1
        proposal_id = setup_queue.get_pending_proposals()[0]["proposal_id"]
        assert setup_queue.approve_proposal(proposal_id) is True
        approved_proposals = setup_queue.get_approved_proposals()
        assert len(approved_proposals) == 1
        proposal = approved_proposals[0]
    finally:
        setup_queue.close()

    result = execute_reviewed_proposal_from_dashboard(
        proposal=proposal,
        raw_stop_loss_price="1.09500",
        api_key="TEST-API-KEY",
        account_id="TEST-ACCOUNT",
        base_url="https://example.invalid",
        trade_state_db_path=trade_db_path,
        drawdown_db_path=drawdown_db_path,
        start_of_day_nav_db_path=start_of_day_nav_db_path,
        approval_db_path=approval_db_path,
        max_currency_exposure=100.0,
        max_quote_age_seconds=60.0,
        now_utc=_NOW_UTC,
    )

    assert isinstance(result, Mapping)
    assert result.get("success") is True

    assert len(constructed_brokers) == 1
    assert constructed_brokers[0]["base_url"] == "https://example.invalid"
    assert len(account_snapshot_calls) == 1

    assert len(submitted_orders) == 1
    order = submitted_orders[0]
    assert order["currency_pair"] == "EUR/USD"
    assert order["direction"] == "Long"
    units = order["position_size"]
    assert type(units) is int
    assert units > 0
    assert units != 1000
    assert order["stop_loss_price"] == pytest.approx(1.095)

    verify_queue = ProposalApprovalQueue(db_path=approval_db_path)
    try:
        assert verify_queue.get_approved_proposals() == []
        statuses = {
            decision["proposal_id"]: decision["status"]
            for decision in verify_queue.get_recent_decisions(limit=5)
        }
        assert statuses[proposal_id] == "EXECUTED"
    finally:
        verify_queue.close()

    state_manager = PersistentTradeStateManager(db_path=trade_db_path)
    try:
        trades = state_manager.get_all_trades()
    finally:
        state_manager.close()

    assert len(trades) == 1
    trade = trades[0]
    assert trade["request_id"] == f"AI-PROPOSAL-{proposal_id}"
    assert trade["status"] == "FILLED"
    assert trade["position_size"] == units
    assert trade["nav"] == 100_000.0
    assert trade["account_currency"] == "USD"
    assert trade["risk_budget_amount"] == pytest.approx(500.0)
    assert trade["risk_at_stop_amount"] == pytest.approx(500.0, rel=1e-3)

    assert (tmp_path / "drawdown.db").exists()
    conn = sqlite3.connect(drawdown_db_path)
    try:
        drawdown_rows = conn.execute(
            "SELECT account_id, account_currency, high_water_nav"
            " FROM drawdown_high_water"
        ).fetchall()
    finally:
        conn.close()
    assert drawdown_rows == [("TEST-ACCOUNT", "USD", 100_000.0)]

    conn = sqlite3.connect(start_of_day_nav_db_path)
    try:
        daily_nav_rows = conn.execute(
            "SELECT account_id, account_currency, utc_date,"
            " start_of_day_nav FROM start_of_day_nav"
        ).fetchall()
    finally:
        conn.close()
    assert daily_nav_rows == [
        (
            "TEST-ACCOUNT",
            "USD",
            _NOW_UTC.date().isoformat(),
            100_000.0,
        )
    ]

    for db_path in (
        trade_db_path,
        drawdown_db_path,
        start_of_day_nav_db_path,
        approval_db_path,
    ):
        assert db_path.startswith(str(tmp_path))

    assert "dashboard.app" not in sys.modules
    assert "streamlit" not in sys.modules
