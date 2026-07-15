import pytest


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


def _execute_sized_proposal(monkeypatch, drawdown_fraction):
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
        margin_available=95_000.0,
    )
    sizing = size_trade_proposal(
        account_snapshot=snapshot,
        pair="EUR/USD",
        side="LONG",
        entry_price=1.1000,
        stop_distance_pips=50.0,
        drawdown_fraction=drawdown_fraction,
    )

    def preserve_calculated_units(state_manager, proposed_trade):
        return proposed_trade["approved_position_size"], 0

    def approve_legacy_raw_unit_check(**kwargs):
        return {
            "approval_status": "Approved",
            "reason": "Portfolio exposure within limits",
        }

    # These patches isolate bridge wiring; they do not prove real netting or
    # portfolio-risk controls accept 100,000-unit positions.
    monkeypatch.setattr(
        orchestrator_module,
        "net_position",
        preserve_calculated_units,
    )
    monkeypatch.setattr(
        orchestrator_module.PortfolioRiskEvaluator,
        "evaluate_trade",
        staticmethod(approve_legacy_raw_unit_check),
    )
    monkeypatch.setattr(
        orchestrator_module,
        "is_trading_enabled",
        lambda: True,
    )

    broker = _RecordingBroker()
    result = ProposalExecutionBridge.execute_approved_proposal(
        proposal={
            "id": 1,
            "proposal_id": "PROP-1",
            "pair": "EUR/USD",
            "direction": "LONG",
            "status": "APPROVED",
            "suggested_size": 1.0,
        },
        orchestrator=TradeOrchestrator(broker),
        state_manager=TradeStateManager(),
        max_currency_exposure=1_000_000.0,
        account_snapshot=snapshot,
        entry_price=1.1000,
        stop_distance_pips=50.0,
        drawdown_fraction=drawdown_fraction,
    )
    return result, broker, sizing


def test_bridge_sizes_standard_risk_proposal_and_forwards_exact_order(monkeypatch):
    result, broker, sizing = _execute_sized_proposal(
        monkeypatch,
        drawdown_fraction=0.0,
    )

    assert result["success"] is True
    assert sizing.risk_fraction == pytest.approx(0.005)
    assert sizing.risk_amount == pytest.approx(500.0)
    assert sizing.units == 100_000
    assert sizing.stop_loss_price == pytest.approx(1.095)
    assert broker.calls == [
        {
            "currency_pair": "EUR/USD",
            "direction": "Long",
            "position_size": 100_000,
            "stop_loss_price": sizing.stop_loss_price,
        }
    ]
    assert type(broker.calls[0]["position_size"]) is int
    assert broker.calls[0]["position_size"] == 100_000
    assert broker.calls[0]["position_size"] != 1.0
    assert broker.calls[0]["position_size"] > 0
    assert broker.calls[0]["stop_loss_price"] == sizing.stop_loss_price


def test_bridge_applies_moderate_drawdown_budget_before_forwarding(monkeypatch):
    result, broker, sizing = _execute_sized_proposal(
        monkeypatch,
        drawdown_fraction=0.04,
    )

    assert result["success"] is True
    assert sizing.risk_fraction == pytest.approx(0.0025)
    assert sizing.risk_amount == pytest.approx(250.0)
    assert sizing.units == 50_000
    assert sizing.stop_loss_price == pytest.approx(1.095)
    assert broker.calls == [
        {
            "currency_pair": "EUR/USD",
            "direction": "Long",
            "position_size": 50_000,
            "stop_loss_price": sizing.stop_loss_price,
        }
    ]
    assert type(broker.calls[0]["position_size"]) is int
    assert broker.calls[0]["position_size"] == 50_000
    assert broker.calls[0]["position_size"] != 1.0
    assert broker.calls[0]["position_size"] > 0
    assert broker.calls[0]["stop_loss_price"] == sizing.stop_loss_price
