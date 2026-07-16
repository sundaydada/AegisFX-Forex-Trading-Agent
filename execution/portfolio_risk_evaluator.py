import math
from dataclasses import dataclass
from numbers import Real
from typing import Dict, List

from execution.portfolio_exposure_calculator import PortfolioExposureCalculator


@dataclass(frozen=True)
class PortfolioRiskAtStopResult:
    approval_status: str
    reason: str
    nav: float
    proposed_risk_amount: float
    existing_portfolio_risk_amount: float
    existing_same_currency_risk_amount: float
    resulting_portfolio_risk_amount: float
    resulting_same_currency_risk_amount: float
    max_portfolio_risk_amount: float
    max_same_currency_risk_amount: float


def _validate_risk_number(
    field_name: str,
    value,
    *,
    strictly_positive: bool,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{field_name} must be a real number")

    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field_name} must be a finite real number") from exc

    if not math.isfinite(normalized):
        raise ValueError(f"{field_name} must be finite")
    if strictly_positive and normalized <= 0.0:
        raise ValueError(f"{field_name} must be greater than zero")
    if not strictly_positive and normalized < 0.0:
        raise ValueError(f"{field_name} must not be negative")
    if maximum is not None and normalized > maximum:
        raise ValueError(f"{field_name} must not exceed {maximum}")
    return normalized


def evaluate_risk_at_stop(
    *,
    nav: float,
    proposed_risk_amount: float,
    existing_portfolio_risk_amount: float,
    existing_same_currency_risk_amount: float,
    max_portfolio_risk_fraction: float = 0.015,
    max_same_currency_risk_fraction: float = 0.01,
) -> PortfolioRiskAtStopResult:
    validated_nav = _validate_risk_number(
        "nav",
        nav,
        strictly_positive=True,
    )
    validated_proposed_risk = _validate_risk_number(
        "proposed_risk_amount",
        proposed_risk_amount,
        strictly_positive=True,
    )
    validated_existing_portfolio_risk = _validate_risk_number(
        "existing_portfolio_risk_amount",
        existing_portfolio_risk_amount,
        strictly_positive=False,
    )
    validated_existing_same_currency_risk = _validate_risk_number(
        "existing_same_currency_risk_amount",
        existing_same_currency_risk_amount,
        strictly_positive=False,
    )
    validated_max_portfolio_fraction = _validate_risk_number(
        "max_portfolio_risk_fraction",
        max_portfolio_risk_fraction,
        strictly_positive=True,
        maximum=1.0,
    )
    validated_max_same_currency_fraction = _validate_risk_number(
        "max_same_currency_risk_fraction",
        max_same_currency_risk_fraction,
        strictly_positive=True,
        maximum=1.0,
    )

    if (
        validated_existing_same_currency_risk
        > validated_existing_portfolio_risk
    ):
        raise ValueError(
            "existing_same_currency_risk_amount must not exceed "
            "existing_portfolio_risk_amount"
        )

    max_portfolio_risk_amount = (
        validated_nav * validated_max_portfolio_fraction
    )
    max_same_currency_risk_amount = (
        validated_nav * validated_max_same_currency_fraction
    )
    resulting_portfolio_risk_amount = (
        validated_existing_portfolio_risk + validated_proposed_risk
    )
    resulting_same_currency_risk_amount = (
        validated_existing_same_currency_risk + validated_proposed_risk
    )

    if resulting_portfolio_risk_amount > max_portfolio_risk_amount:
        approval_status = "Rejected"
        reason = "Portfolio risk at stop exceeds the portfolio risk limit"
    elif resulting_same_currency_risk_amount > max_same_currency_risk_amount:
        approval_status = "Rejected"
        reason = "Same-currency risk at stop exceeds the same-currency risk limit"
    else:
        approval_status = "Approved"
        reason = "Monetary risk at stop is within limits"

    return PortfolioRiskAtStopResult(
        approval_status=approval_status,
        reason=reason,
        nav=validated_nav,
        proposed_risk_amount=validated_proposed_risk,
        existing_portfolio_risk_amount=validated_existing_portfolio_risk,
        existing_same_currency_risk_amount=(
            validated_existing_same_currency_risk
        ),
        resulting_portfolio_risk_amount=resulting_portfolio_risk_amount,
        resulting_same_currency_risk_amount=(
            resulting_same_currency_risk_amount
        ),
        max_portfolio_risk_amount=max_portfolio_risk_amount,
        max_same_currency_risk_amount=max_same_currency_risk_amount,
    )


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
        max_total_exposure: float = 100.0,
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
