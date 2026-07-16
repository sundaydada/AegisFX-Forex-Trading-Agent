import math
from dataclasses import dataclass
from numbers import Real


@dataclass(frozen=True)
class DailyLossResult:
    daily_loss_amount: float
    daily_loss_fraction: float
    limit_fraction: float
    new_exposure_allowed: bool
    reason: str


def _validate_number(
    field_name: str,
    value,
    *,
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
    if normalized <= 0.0:
        raise ValueError(f"{field_name} must be greater than zero")
    if maximum is not None and normalized > maximum:
        raise ValueError(f"{field_name} must not exceed {maximum}")
    return normalized


def evaluate_daily_loss(
    *,
    start_of_day_nav,
    current_nav,
    limit_fraction,
) -> DailyLossResult:
    validated_start_of_day_nav = _validate_number(
        "start_of_day_nav",
        start_of_day_nav,
    )
    validated_current_nav = _validate_number("current_nav", current_nav)
    validated_limit_fraction = _validate_number(
        "limit_fraction",
        limit_fraction,
        maximum=1.0,
    )

    daily_loss_amount = max(
        0.0,
        validated_start_of_day_nav - validated_current_nav,
    )
    daily_loss_fraction = daily_loss_amount / validated_start_of_day_nav
    new_exposure_allowed = daily_loss_fraction < validated_limit_fraction

    if new_exposure_allowed:
        reason = "Daily loss is below the daily-loss limit"
    else:
        reason = "Daily-loss limit reached or exceeded"

    return DailyLossResult(
        daily_loss_amount=daily_loss_amount,
        daily_loss_fraction=daily_loss_fraction,
        limit_fraction=validated_limit_fraction,
        new_exposure_allowed=new_exposure_allowed,
        reason=reason,
    )
