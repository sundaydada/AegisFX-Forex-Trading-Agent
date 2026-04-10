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
        max_total_exposure: float = 10.0,
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

        # Check per-currency limits
        for currency, exposure in currency_exposure.items():
            if abs(exposure) > max_currency_exposure:
                return {
                    "approval_status": "Rejected",
                    "reason": f"{currency} exposure limit breached",
                }

        # Check total portfolio exposure limit
        total_exposure = sum(abs(v) for v in currency_exposure.values())
        if total_exposure > max_total_exposure:
            return {
                "approval_status": "Rejected",
                "reason": f"Total portfolio exposure limit breached ({total_exposure:.1f} > {max_total_exposure:.1f})",
            }

        return {
            "approval_status": "Approved",
            "reason": "Portfolio exposure within limits",
        }
