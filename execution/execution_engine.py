from typing import Dict
from execution.trade_validator import validate_trade_approval


def execute_trade(trade: Dict, market_price: float) -> Dict:
    """
    Deterministically simulate trade execution.
    """

    if not validate_trade_approval(trade):
        return {
            "execution_status": "Rejected",
            "reason": "Invalid trade approval object",
        }

    if trade["approval_status"] != "Approved":
        return {
            "execution_status": "Rejected",
            "reason": "Trade not approved by Risk & Capital Control",
        }

    return {
        "execution_status": "Filled",
        "currency_pair": trade["currency_pair"],
        "direction": trade["direction"],
        "position_size": trade["approved_position_size"],
        "fill_price": float(market_price),
        "stop_loss_price": trade["stop_loss_price"],
        "take_profit_price": trade["take_profit_price"],
        "timestamp": trade["timestamp"],
    }
