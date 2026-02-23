from execution.trade_formatter import format_trade_approval
from execution.trade_validator import validate_trade_approval


def test_formatter_output_passes_validator():
    trade = format_trade_approval(
        approval_status="Approved",
        currency_pair="EUR/USD",
        direction="Long",
        position_size=1.0,
        stop_loss=1.1000,
        take_profit=1.1100,
        risk_rationale="All checks passed",
    )

    assert validate_trade_approval(trade) is True


def test_rejected_trade_auto_zero_position():
    trade = format_trade_approval(
        approval_status="Rejected",
        currency_pair="EUR/USD",
        direction="Long",
        position_size=5.0,  # Should be overridden to 0.0
        stop_loss=1.1000,
        take_profit=1.1100,
        risk_rationale="Risk threshold breached",
    )

    assert trade["approved_position_size"] == 0.0
    assert validate_trade_approval(trade) is True
