import math
import re
from dataclasses import dataclass
from numbers import Real


_PAIR_PATTERN = re.compile(r"[A-Z]{3}/[A-Z]{3}")
_CURRENCY_PATTERN = re.compile(r"[A-Z]{3}")
_SUPPORTED_SIDES = {"LONG", "SHORT"}


@dataclass(frozen=True)
class FxRiskInputs:
    pair: str
    side: str
    account_currency: str
    entry_price: float
    stop_distance_pips: float
    pip_size: float
    pip_value_per_unit: float
    stop_loss_price: float


def _normalize_text(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")

    normalized = value.strip().upper()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized


def _positive_finite_float(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a real number")

    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a real number") from exc

    if not math.isfinite(numeric) or numeric <= 0.0:
        raise ValueError(f"{name} must be finite and greater than zero")
    return numeric


def build_fx_risk_inputs(
    *,
    pair: str,
    side: str,
    account_currency: str,
    entry_price: float,
    stop_distance_pips: float,
) -> FxRiskInputs:
    normalized_pair = _normalize_text("pair", pair)
    if _PAIR_PATTERN.fullmatch(normalized_pair) is None:
        raise ValueError("pair must use the AAA/BBB format")

    normalized_side = _normalize_text("side", side)
    if normalized_side not in _SUPPORTED_SIDES:
        raise ValueError("side must be LONG or SHORT")

    normalized_account_currency = _normalize_text(
        "account_currency",
        account_currency,
    )
    if _CURRENCY_PATTERN.fullmatch(normalized_account_currency) is None:
        raise ValueError("account_currency must be a three-letter currency code")

    normalized_entry_price = _positive_finite_float(
        "entry_price",
        entry_price,
    )
    normalized_stop_distance = _positive_finite_float(
        "stop_distance_pips",
        stop_distance_pips,
    )

    base_currency, quote_currency = normalized_pair.split("/")
    pip_size = 0.01 if quote_currency == "JPY" else 0.0001

    if normalized_account_currency == quote_currency:
        pip_value_per_unit = pip_size
    elif normalized_account_currency == base_currency:
        try:
            pip_value_per_unit = pip_size / normalized_entry_price
        except OverflowError as exc:
            raise ValueError("pip value must be finite and greater than zero") from exc
    else:
        raise ValueError(
            "third-currency pip-value conversion is not supported"
        )

    if (
        not math.isfinite(pip_value_per_unit)
        or pip_value_per_unit <= 0.0
    ):
        raise ValueError("pip value must be finite and greater than zero")

    stop_offset = normalized_stop_distance * pip_size
    if normalized_side == "LONG":
        stop_loss_price = normalized_entry_price - stop_offset
    else:
        stop_loss_price = normalized_entry_price + stop_offset

    if not math.isfinite(stop_loss_price) or stop_loss_price <= 0.0:
        raise ValueError("stop-loss price must be finite and greater than zero")

    return FxRiskInputs(
        pair=normalized_pair,
        side=normalized_side,
        account_currency=normalized_account_currency,
        entry_price=normalized_entry_price,
        stop_distance_pips=normalized_stop_distance,
        pip_size=pip_size,
        pip_value_per_unit=pip_value_per_unit,
        stop_loss_price=stop_loss_price,
    )
