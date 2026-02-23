from execution.trade_formatter import format_trade_approval
from execution.execution_engine import execute_trade


def test_execution_rejects_invalid_trade():
    invalid_trade = {"invalid": "object"}

    result = execute_trade(invalid_trade, market_price=1.1000)

    assert result["execution_status"] == "Rejected"


def test_execution_rejects_non_approved_trade():
    trade = format_trade_approval(
        approval_status="Rejected",
        currency_pair="EUR/USD",
        direction="Long",
        position_size=1.0,
        stop_loss=1.1000,
        take_profit=1.1100,
        risk_rationale="Risk breach",
    )

    result = execute_trade(trade, market_price=1.1000)

    assert result["execution_status"] == "Rejected"


def test_execution_fills_approved_trade():
    trade = format_trade_approval(
        approval_status="Approved",
        currency_pair="EUR/USD",
        direction="Long",
        position_size=1.0,
        stop_loss=1.1000,
        take_profit=1.1100,
        risk_rationale="All checks passed",
    )

    result = execute_trade(trade, market_price=1.1050)

    assert result["execution_status"] == "Filled"
    assert result["fill_price"] == 1.1050
    assert result["position_size"] == 1.0
