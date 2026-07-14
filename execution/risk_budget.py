import math
from numbers import Real


STANDARD_RISK_FRACTION = 0.005
REDUCED_RISK_FRACTION = 0.0025
MODERATE_DRAWDOWN_THRESHOLD = 0.04
MAX_DRAWDOWN_FRACTION = 1.0


def risk_fraction_for_drawdown(drawdown_fraction: float) -> float:
    if isinstance(drawdown_fraction, bool) or not isinstance(
        drawdown_fraction,
        Real,
    ):
        raise ValueError("drawdown_fraction must be a number")

    normalized = float(drawdown_fraction)
    if not math.isfinite(normalized):
        raise ValueError("drawdown_fraction must be finite")
    if normalized < 0.0 or normalized > MAX_DRAWDOWN_FRACTION:
        raise ValueError("drawdown_fraction must be between 0.0 and 1.0")

    if normalized < MODERATE_DRAWDOWN_THRESHOLD:
        return STANDARD_RISK_FRACTION
    return REDUCED_RISK_FRACTION
