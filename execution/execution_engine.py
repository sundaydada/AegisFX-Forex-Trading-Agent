from typing import Dict
from datetime import datetime


class ExecutionEngine:
    @staticmethod
    def execute_trade(trade: Dict, market_price: float) -> Dict:
        """
        Deterministically simulate trade execution.
        Expects execution-ready trade object.
        """

        required_fields = [
            "currency_pair",
            "direction",
            "position_size",
            "stop_loss_price",
            "take_profit_price",
        ]

        for field in required_fields:
            if field not in trade:
                return {
                    "execution_status": "Rejected",
                    "reason": f"Missing required field: {field}",
                }

        return {
            "execution_status": "Filled",
            "currency_pair": trade["currency_pair"],
            "direction": trade["direction"],
            "position_size": trade["position_size"],
            "fill_price": float(market_price),
            "stop_loss_price": trade["stop_loss_price"],
            "take_profit_price": trade["take_profit_price"],
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
