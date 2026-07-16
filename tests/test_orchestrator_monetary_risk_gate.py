import pytest


EVIDENCE_FIELDS = {
    "nav",
    "account_currency",
    "risk_fraction",
    "risk_budget_amount",
    "loss_per_unit_at_stop",
    "risk_at_stop_amount",
}


class _RecordingBroker:
    def __init__(self):
        self.calls = []

    def place_order(self, order):
        self.calls.append(dict(order))
        return {
            "execution_status": "Filled",
            "broker_order_id": "TEST-ORDER-1",
            "currency_pair": order["currency_pair"],
            "direction": order["direction"],
            "units": order["position_size"],
            "fill_price": 1.1,
            "timestamp": "2026-07-15T12:00:00Z",
        }


def _existing_trade(
    *,
    request_id,
    pair="EUR/USD",
    direction="Long",
    units=25_000,
    status="FILLED",
    risk_at_stop_amount=None,
):
    trade = {
        "request_id": request_id,
        "currency_pair": pair,
        "direction": direction,
        "position_size": units,
        "status": status,
        "created_at": "2026-07-15T11:00:00+00:00",
    }
    if risk_at_stop_amount is not None:
        trade["risk_at_stop_amount"] = risk_at_stop_amount
    return trade


def _execute(monkeypatch, *, existing_trades=()):
    import execution.portfolio_risk_evaluator as risk_module
    import execution.trade_orchestrator as orchestrator_module
    from ai.proposal_execution_bridge import ProposalExecutionBridge
    from brokers.broker_interface import AccountSnapshot
    from execution.proposal_sizing import size_trade_proposal
    from execution.trade_orchestrator import TradeOrchestrator
    from execution.trade_state_manager import TradeStateManager

    snapshot = AccountSnapshot(
        nav=100_000.0,
        balance=100_000.0,
        currency="USD",
        margin_available=100_000.0,
    )
    sizing = size_trade_proposal(
        account_snapshot=snapshot,
        pair="EUR/USD",
        side="LONG",
        entry_price=1.1000,
        stop_distance_pips=50.0,
        drawdown_fraction=0.0,
    )

    monetary_calls = []
    real_evaluate_risk_at_stop = risk_module.evaluate_risk_at_stop

    def record_monetary_risk(**kwargs):
        monetary_calls.append(dict(kwargs))
        return real_evaluate_risk_at_stop(**kwargs)

    monkeypatch.setattr(
        risk_module,
        "evaluate_risk_at_stop",
        record_monetary_risk,
    )
    monkeypatch.setattr(
        orchestrator_module,
        "evaluate_risk_at_stop",
        record_monetary_risk,
        raising=False,
    )

    legacy_calls = []
    legacy_evaluator = getattr(
        orchestrator_module,
        "PortfolioRiskEvaluator",
        None,
    )
    if legacy_evaluator is not None:
        real_legacy_risk = legacy_evaluator.evaluate_trade

        def record_legacy_risk(**kwargs):
            legacy_calls.append(dict(kwargs))
            return real_legacy_risk(**kwargs)

        monkeypatch.setattr(
            legacy_evaluator,
            "evaluate_trade",
            staticmethod(record_legacy_risk),
        )
    monkeypatch.setattr(orchestrator_module, "is_trading_enabled", lambda: True)

    state_manager = TradeStateManager()
    for trade in existing_trades:
        state_manager.record_trade(dict(trade))

    broker = _RecordingBroker()
    result = ProposalExecutionBridge.execute_approved_proposal(
        proposal={
            "id": 1,
            "proposal_id": "PROP-RISK-1",
            "pair": "EUR/USD",
            "direction": "LONG",
            "status": "APPROVED",
            "suggested_size": 1.0,
        },
        orchestrator=TradeOrchestrator(broker),
        state_manager=state_manager,
        max_currency_exposure=1_000_000.0,
        account_snapshot=snapshot,
        entry_price=1.1000,
        stop_distance_pips=50.0,
        drawdown_fraction=0.0,
    )
    return result, broker, state_manager, sizing, monetary_calls, legacy_calls


def test_empty_portfolio_approves_and_persists_monetary_risk_evidence(monkeypatch):
    result, broker, state, sizing, monetary_calls, legacy_calls = _execute(
        monkeypatch
    )

    assert result["success"] is True
    assert len(broker.calls) == 1
    assert legacy_calls == []
    assert len(monetary_calls) == 1

    order = broker.calls[0]
    assert type(order["position_size"]) is int
    assert order["position_size"] > 0
    assert order["stop_loss_price"] == pytest.approx(sizing.stop_loss_price)

    risk_call = monetary_calls[0]
    expected_risk = order["position_size"] * sizing.loss_per_unit_at_stop
    assert risk_call["nav"] == pytest.approx(100_000.0)
    assert risk_call["existing_portfolio_risk_amount"] == pytest.approx(0.0)
    assert risk_call["existing_same_currency_risk_amount"] == pytest.approx(0.0)
    assert risk_call["proposed_risk_amount"] == pytest.approx(expected_risk)
    assert 0.0 < risk_call["proposed_risk_amount"] <= sizing.risk_amount

    recorded = state.get_all_trades()
    assert len(recorded) == 1
    assert EVIDENCE_FIELDS <= recorded[0].keys()
    assert recorded[0]["nav"] == pytest.approx(sizing.nav)
    assert recorded[0]["account_currency"] == sizing.account_currency
    assert recorded[0]["risk_fraction"] == pytest.approx(sizing.risk_fraction)
    assert recorded[0]["risk_budget_amount"] == pytest.approx(sizing.risk_amount)
    assert recorded[0]["loss_per_unit_at_stop"] == pytest.approx(
        sizing.loss_per_unit_at_stop
    )
    assert recorded[0]["risk_at_stop_amount"] == pytest.approx(expected_risk)


def test_filled_trade_missing_monetary_evidence_fails_closed(monkeypatch):
    existing = _existing_trade(request_id="EXISTING-FILLED")
    result, broker, state, _, monetary_calls, legacy_calls = _execute(
        monkeypatch,
        existing_trades=(existing,),
    )

    assert result["success"] is False
    assert "missing" in result["message"].lower()
    assert "risk" in result["message"].lower()
    assert broker.calls == []
    assert state.get_all_trades() == [existing]
    assert monetary_calls == []
    assert legacy_calls == []


def test_unresolved_pending_trade_fails_closed(monkeypatch):
    existing = _existing_trade(
        request_id="EXISTING-PENDING",
        status="PENDING",
    )
    result, broker, state, _, monetary_calls, legacy_calls = _execute(
        monkeypatch,
        existing_trades=(existing,),
    )

    assert result["success"] is False
    reason = result["message"].lower()
    assert any(word in reason for word in ("pending", "unresolved", "uncertain"))
    assert broker.calls == []
    assert state.get_all_trades() == [existing]
    assert monetary_calls == []
    assert legacy_calls == []


def test_existing_same_currency_risk_over_limit_is_rejected(monkeypatch):
    existing = _existing_trade(
        request_id="EXISTING-GBPUSD",
        pair="GBP/USD",
        risk_at_stop_amount=600.0,
    )
    result, broker, state, sizing, monetary_calls, legacy_calls = _execute(
        monkeypatch,
        existing_trades=(existing,),
    )

    assert result["success"] is False
    assert "same" in result["message"].lower()
    assert "currency" in result["message"].lower()
    assert broker.calls == []
    assert state.get_all_trades() == [existing]
    assert legacy_calls == []
    assert len(monetary_calls) == 1
    assert monetary_calls[0]["existing_portfolio_risk_amount"] == pytest.approx(
        600.0
    )
    assert monetary_calls[0]["existing_same_currency_risk_amount"] == pytest.approx(
        600.0
    )
    assert monetary_calls[0]["proposed_risk_amount"] == pytest.approx(
        sizing.units * sizing.loss_per_unit_at_stop
    )


def test_post_netting_risk_uses_remaining_integer_units(monkeypatch):
    existing = _existing_trade(
        request_id="EXISTING-SHORT",
        direction="Short",
        units=25_000,
        risk_at_stop_amount=125.0,
    )
    result, broker, state, sizing, monetary_calls, legacy_calls = _execute(
        monkeypatch,
        existing_trades=(existing,),
    )

    assert result["success"] is True
    assert legacy_calls == []
    assert len(monetary_calls) == 1

    remaining_units = sizing.units - existing["position_size"]
    expected_risk = remaining_units * sizing.loss_per_unit_at_stop
    original_risk = sizing.units * sizing.loss_per_unit_at_stop
    risk_call = monetary_calls[0]

    assert risk_call["proposed_risk_amount"] == pytest.approx(expected_risk)
    assert risk_call["proposed_risk_amount"] != pytest.approx(original_risk)
    assert risk_call["proposed_risk_amount"] != pytest.approx(sizing.risk_amount)
    assert len(broker.calls) == 1
    assert type(broker.calls[0]["position_size"]) is int
    assert broker.calls[0]["position_size"] == remaining_units
    assert state.get_all_trades()[0]["status"] == "CLOSED"
