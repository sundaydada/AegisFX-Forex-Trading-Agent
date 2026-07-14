import math
from dataclasses import dataclass
from numbers import Real


@dataclass(frozen=True)
class PositionSizeResult:
    units: int
    risk_amount: float
    loss_per_unit_at_stop: float
    nav: float
    risk_fraction: float
    stop_distance_pips: float
    pip_value_per_unit: float


def _positive_finite_number(name: str, value, *, maximum=None) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a number")

    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise ValueError(f"{name} must be finite and greater than zero")
    if maximum is not None and normalized > maximum:
        raise ValueError(f"{name} must not exceed {maximum}")
    return normalized


def calculate_position_size(
    *,
    nav: float,
    risk_fraction: float,
    stop_distance_pips: float,
    pip_value_per_unit: float,
) -> PositionSizeResult:
    validated_nav = _positive_finite_number("nav", nav)
    validated_risk_fraction = _positive_finite_number(
        "risk_fraction",
        risk_fraction,
        maximum=1.0,
    )
    validated_stop_distance = _positive_finite_number(
        "stop_distance_pips",
        stop_distance_pips,
    )
    validated_pip_value = _positive_finite_number(
        "pip_value_per_unit",
        pip_value_per_unit,
    )

    risk_amount = validated_nav * validated_risk_fraction
    loss_per_unit_at_stop = validated_stop_distance * validated_pip_value
    if not math.isfinite(risk_amount) or not math.isfinite(loss_per_unit_at_stop):
        raise ValueError("position-size calculation must produce finite values")

    raw_units = risk_amount / loss_per_unit_at_stop
    if not math.isfinite(raw_units):
        raise ValueError("position-size calculation must produce finite units")

    units = math.floor(raw_units)
    if units < 1:
        raise ValueError("position size must be at least one whole unit")

    return PositionSizeResult(
        units=units,
        risk_amount=risk_amount,
        loss_per_unit_at_stop=loss_per_unit_at_stop,
        nav=validated_nav,
        risk_fraction=validated_risk_fraction,
        stop_distance_pips=validated_stop_distance,
        pip_value_per_unit=validated_pip_value,
    )
