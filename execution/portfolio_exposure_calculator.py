from typing import Dict, List


class PortfolioExposureCalculator:
    """
    Computes derived exposure metrics from filled trades.
    No state mutation. No risk decisions.
    Pure deterministic calculations.
    """

    @staticmethod
    def exposure_by_pair(trades: List[Dict]) -> Dict[str, float]:
        exposure = {}

        for trade in trades:
            if trade.get("execution_status") != "Filled":
                continue

            pair = trade["currency_pair"]
            direction = trade["direction"]
            size = trade["position_size"]

            signed_size = size if direction == "Long" else -size

            exposure[pair] = exposure.get(pair, 0.0) + signed_size

        return exposure

    @staticmethod
    def net_currency_exposure(trades: List[Dict]) -> Dict[str, float]:
        exposure = {}

        for trade in trades:
            if trade.get("execution_status") != "Filled":
                continue

            base, quote = trade["currency_pair"].split("/")
            direction = trade["direction"]
            size = trade["position_size"]

            if direction == "Long":
                exposure[base] = exposure.get(base, 0.0) + size
                exposure[quote] = exposure.get(quote, 0.0) - size
            else:
                exposure[base] = exposure.get(base, 0.0) - size
                exposure[quote] = exposure.get(quote, 0.0) + size

        return exposure
