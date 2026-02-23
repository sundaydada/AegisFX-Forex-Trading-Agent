from typing import Dict
from datetime import datetime


def format_trade_approval(
    approval_status: str,
    currency_pair: str,
    direction: str,
    position_size: float,
    stop_loss: float,
    take_profit: float,
    risk_rationale: str,
) -> Dict:

    if approval_status == "Rejected":
        position_size = 0.0

    return {
        "approval_status": approval_status,
        "currency_pair": currency_pair,
        "direction": direction,
        "approved_position_size": float(position_size),
        "stop_loss_price": float(stop_loss),
        "take_profit_price": float(take_profit),
        "risk_rationale": risk_rationale,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
