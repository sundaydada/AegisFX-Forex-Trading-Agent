from typing import Dict, List
from execution.portfolio_exposure_calculator import PortfolioExposureCalculator


class PortfolioRiskEvaluator:
    """
    Evaluates hypothetical portfolio exposure constraints.
    No state mutation. No execution logic.
    """

    @staticmethod
    def evaluate_trade(
        current_trades: List[Dict],
        proposed_trade: Dict,
        max_currency_exposure: float,
    ) -> Dict:
        """
        Returns:
        {
            "approval_status": "Approved" or "Rejected",
            "reason": str
        }
        """

        # Build minimal synthetic filled trade for exposure calculation
        simulated_trade = {
            "execution_status": "Filled",
            "currency_pair": proposed_trade["currency_pair"],
            "direction": proposed_trade["direction"],
            "position_size": proposed_trade["approved_position_size"],
        }

        hypothetical_trades = list(current_trades) + [simulated_trade]

        currency_exposure = PortfolioExposureCalculator.net_currency_exposure(
            hypothetical_trades
        )

        for currency, exposure in currency_exposure.items():
            if abs(exposure) > max_currency_exposure:
                return {
                    "approval_status": "Rejected",
                    "reason": f"{currency} exposure limit breached",
                }

        return {
            "approval_status": "Approved",
            "reason": "Portfolio exposure within limits",
        }
