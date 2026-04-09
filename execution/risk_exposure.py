from typing import Dict, List, Tuple


def compute_risk_exposure(
    positions: List[Dict],
    max_allowed_exposure: float,
) -> Tuple[Dict[str, float], float, float]:
    """
    Compute portfolio risk exposure from FILLED trade positions.

    Args:
        positions: List of FILLED trade dicts with currency_pair,
                   direction, and position_size fields.
        max_allowed_exposure: Maximum allowed total exposure.

    Returns:
        Tuple of:
            net_exposure_per_currency: {"EUR": 2.0, "USD": -1.0, ...}
            total_exposure: Sum of absolute exposures
            utilization_pct: total_exposure / max_allowed_exposure
    """

    net_exposure = {}

    for trade in positions:
        pair = trade.get("currency_pair", "")
        direction = trade.get("direction", "")
        size = float(trade.get("position_size", trade.get("units", 0)))

        parts = pair.split("/")
        if len(parts) != 2:
            continue

        base, quote = parts

        if direction == "Long":
            net_exposure[base] = net_exposure.get(base, 0.0) + size
            net_exposure[quote] = net_exposure.get(quote, 0.0) - size
        elif direction == "Short":
            net_exposure[base] = net_exposure.get(base, 0.0) - size
            net_exposure[quote] = net_exposure.get(quote, 0.0) + size

    total_exposure = sum(abs(v) for v in net_exposure.values())

    utilization_pct = (
        (total_exposure / max_allowed_exposure * 100)
        if max_allowed_exposure > 0
        else 0.0
    )

    return net_exposure, total_exposure, utilization_pct
