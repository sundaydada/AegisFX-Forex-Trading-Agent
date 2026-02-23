from typing import Dict, Any

REQUIRED_FIELDS = {
    "approval_status": str,
    "currency_pair": str,
    "direction": str,
    "approved_position_size": float,
    "stop_loss_price": float,
    "take_profit_price": float,
    "risk_rationale": str,
    "timestamp": str,
}

VALID_DIRECTIONS = {"Long", "Short"}
VALID_APPROVAL_STATUS = {"Approved", "Rejected"}


def validate_trade_approval(trade: Dict[str, Any]) -> bool:
    """
    Deterministically validate a trade approval object.
    Returns True if valid, False otherwise.
    """

    # Check required fields
    for field, field_type in REQUIRED_FIELDS.items():
        if field not in trade:
            return False
        if not isinstance(trade[field], field_type):
            return False

    # Validate enumerated values
    if trade["approval_status"] not in VALID_APPROVAL_STATUS:
        return False

    if trade["direction"] not in VALID_DIRECTIONS:
        return False

    # If rejected, position size must be 0
    if trade["approval_status"] == "Rejected":
        if trade["approved_position_size"] != 0.0:
            return False

    return True
