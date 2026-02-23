from execution.trade_validator import validate_trade_approval


def test_valid_trade_approval():
    valid_trade = {
        "approval_status": "Approved",
        "currency_pair": "EUR/USD",
        "direction": "Long",
        "approved_position_size": 1.0,
        "stop_loss_price": 1.1000,
        "take_profit_price": 1.1100,
        "risk_rationale": "All risk parameters satisfied",
        "timestamp": "2026-01-01T00:00:00Z",
    }

    assert validate_trade_approval(valid_trade) is True
