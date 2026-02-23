from execution.trade_validator import validate_trade_approval


def test_reject_missing_field():
    invalid_trade = {
        "approval_status": "Approved",
        "currency_pair": "EUR/USD",
        "direction": "Long",
        # Missing approved_position_size
        "stop_loss_price": 1.1000,
        "take_profit_price": 1.1100,
        "risk_rationale": "Test case",
        "timestamp": "2026-01-01T00:00:00Z",
    }

    assert validate_trade_approval(invalid_trade) is False


def test_reject_invalid_direction():
    invalid_trade = {
        "approval_status": "Approved",
        "currency_pair": "EUR/USD",
        "direction": "BUY",  # Invalid direction
        "approved_position_size": 1.0,
        "stop_loss_price": 1.1000,
        "take_profit_price": 1.1100,
        "risk_rationale": "Test case",
        "timestamp": "2026-01-01T00:00:00Z",
    }

    assert validate_trade_approval(invalid_trade) is False


def test_reject_rejected_with_nonzero_size():
    invalid_trade = {
        "approval_status": "Rejected",
        "currency_pair": "EUR/USD",
        "direction": "Long",
        "approved_position_size": 1.0,  # Must be 0.0 if rejected
        "stop_loss_price": 1.1000,
        "take_profit_price": 1.1100,
        "risk_rationale": "Risk breach",
        "timestamp": "2026-01-01T00:00:00Z",
    }

    assert validate_trade_approval(invalid_trade) is False
