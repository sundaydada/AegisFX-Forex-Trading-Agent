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


def _execute_proposed_trade(monkeypatch, proposed_trade):
    import execution.trade_orchestrator as orchestrator_module
    from execution.trade_orchestrator import TradeOrchestrator
    from execution.trade_state_manager import TradeStateManager

    def preserve_approved_size(state_manager, trade):
        return trade["approved_position_size"], 0

    def approve_portfolio_risk(**kwargs):
        return {
            "approval_status": "Approved",
            "reason": "Portfolio exposure within limits",
        }

    monkeypatch.setattr(
        orchestrator_module,
        "net_position",
        preserve_approved_size,
    )
    monkeypatch.setattr(
        orchestrator_module.PortfolioRiskEvaluator,
        "evaluate_trade",
        staticmethod(approve_portfolio_risk),
    )
    monkeypatch.setattr(
        orchestrator_module,
        "is_trading_enabled",
        lambda: True,
    )

    broker = _RecordingBroker()
    orchestrator = TradeOrchestrator(broker)
    result = orchestrator.process_trade(
        state_manager=TradeStateManager(),
        request_id=f"REQ-{proposed_trade['direction'].upper()}",
        proposed_trade=proposed_trade,
        max_currency_exposure=1_000_000.0,
    )

    assert result["approval_status"] == "Approved"
    return broker


def test_orchestrator_forwards_long_size_and_stop_unchanged(monkeypatch):
    broker = _execute_proposed_trade(
        monkeypatch,
        {
            "currency_pair": "EUR/USD",
            "direction": "Long",
            "approved_position_size": 100_000,
            "stop_loss_price": 1.095,
        },
    )

    assert broker.calls == [
        {
            "currency_pair": "EUR/USD",
            "direction": "Long",
            "position_size": 100_000,
            "stop_loss_price": 1.095,
        }
    ]
    assert type(broker.calls[0]["position_size"]) is int
    assert broker.calls[0]["position_size"] == 100_000
    assert broker.calls[0]["stop_loss_price"] == 1.095


def test_orchestrator_forwards_short_positive_size_and_stop_unchanged(monkeypatch):
    broker = _execute_proposed_trade(
        monkeypatch,
        {
            "currency_pair": "EUR/USD",
            "direction": "Short",
            "approved_position_size": 50_000,
            "stop_loss_price": 1.1056789,
        },
    )

    assert broker.calls == [
        {
            "currency_pair": "EUR/USD",
            "direction": "Short",
            "position_size": 50_000,
            "stop_loss_price": 1.1056789,
        }
    ]
    assert type(broker.calls[0]["position_size"]) is int
    assert broker.calls[0]["position_size"] == 50_000
    assert broker.calls[0]["position_size"] > 0
    assert broker.calls[0]["stop_loss_price"] == 1.1056789
