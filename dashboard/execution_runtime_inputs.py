"""Resolve live runtime evidence for an APPROVED proposal before dispatch.

Acquires the account snapshot, a fresh quote, and the measured drawdown
from explicitly injected providers, validates every input, and only then
forwards the resolved evidence to the injected bridge executor. Any
missing or invalid evidence fails closed with a structured result and
never reaches the bridge.
"""

import math
import numbers
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional

from brokers.broker_interface import AccountSnapshot

_VALID_DIRECTIONS = ("LONG", "SHORT")
_JPY_PIP_SIZE = 0.01
_DEFAULT_PIP_SIZE = 0.0001


@dataclass(frozen=True)
class ExecutionRuntimeInputs:
    account_snapshot: AccountSnapshot
    entry_price: float
    stop_distance_pips: float
    drawdown_fraction: float


def _failure(message: str) -> Dict:
    return {"success": False, "message": message}


def _finite_float(value) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return None
    try:
        numeric = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _finite_positive_float(value) -> Optional[float]:
    numeric = _finite_float(value)
    if numeric is None or numeric <= 0.0:
        return None
    return numeric


def _is_aware_datetime(value) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _normalized_pair(value) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.upper()
    base, separator, quote = normalized.partition("/")
    if separator != "/":
        return None
    if len(base) != 3 or len(quote) != 3:
        return None
    if not (base.isalpha() and quote.isalpha()):
        return None
    return normalized


def execute_approved_proposal_with_runtime_inputs(
    *,
    proposal: Dict,
    orchestrator,
    state_manager,
    max_currency_exposure: float,
    broker,
    quote_provider,
    drawdown_provider,
    stop_loss_price: float,
    now_utc: datetime,
    max_quote_age_seconds: float,
    bridge_execute,
) -> Dict:
    """Validate runtime evidence and dispatch once via bridge_execute."""

    if not isinstance(proposal, dict):
        return _failure(
            "Invalid proposal: expected a dictionary with pair and direction"
        )

    pair = _normalized_pair(proposal.get("pair"))
    if pair is None:
        return _failure(
            "Invalid proposal pair: expected BASE/QUOTE with"
            " three-letter currencies"
        )

    direction = proposal.get("direction")
    if not isinstance(direction, str) or direction not in _VALID_DIRECTIONS:
        return _failure(
            "Invalid proposal direction: expected exactly LONG or SHORT"
        )

    if not _is_aware_datetime(now_utc):
        return _failure(
            "Invalid now_utc: expected a timezone-aware datetime"
        )

    max_age_seconds = _finite_positive_float(max_quote_age_seconds)
    if max_age_seconds is None:
        return _failure(
            "Invalid max_quote_age_seconds: expected a finite number"
            " greater than zero"
        )

    try:
        account_snapshot = broker.get_account_snapshot()
    except Exception:
        return _failure(
            "Account snapshot evidence unavailable:"
            " broker.get_account_snapshot failed"
        )
    if not isinstance(account_snapshot, AccountSnapshot):
        return _failure(
            "Invalid account snapshot evidence: expected an AccountSnapshot"
        )

    try:
        quote = quote_provider.get_quote(pair)
    except Exception:
        return _failure(
            "Quote evidence unavailable: quote_provider.get_quote failed"
        )

    if not isinstance(quote, dict):
        return _failure("Invalid quote evidence: expected a quote dictionary")

    quote_pair = _normalized_pair(quote.get("currency_pair"))
    if quote_pair is None or quote_pair != pair:
        return _failure(
            "Invalid quote evidence: currency_pair must exactly match"
            " the proposal pair"
        )

    bid = _finite_positive_float(quote.get("bid"))
    if bid is None:
        return _failure(
            "Invalid quote evidence: bid must be a finite positive number"
        )

    ask = _finite_positive_float(quote.get("ask"))
    if ask is None:
        return _failure(
            "Invalid quote evidence: ask must be a finite positive number"
        )

    if ask < bid:
        return _failure("Invalid quote evidence: ask must not be below bid")

    timestamp = quote.get("timestamp")
    if not _is_aware_datetime(timestamp):
        return _failure(
            "Invalid quote evidence: timestamp must be a timezone-aware"
            " datetime"
        )
    if timestamp > now_utc:
        return _failure(
            "Invalid quote evidence: timestamp is in the future of now_utc"
        )
    age_seconds = (now_utc - timestamp).total_seconds()
    if age_seconds > max_age_seconds:
        return _failure(
            "Invalid quote evidence: timestamp is stale beyond"
            " max_quote_age_seconds"
        )

    entry_price = ask if direction == "LONG" else bid

    stop_price = _finite_positive_float(stop_loss_price)
    if stop_price is None:
        return _failure(
            "Invalid stop evidence: stop_loss_price must be a finite"
            " positive number"
        )
    if direction == "LONG" and stop_price >= entry_price:
        return _failure(
            "Invalid stop evidence: LONG stop_loss_price must be below"
            " the entry price"
        )
    if direction == "SHORT" and stop_price <= entry_price:
        return _failure(
            "Invalid stop evidence: SHORT stop_loss_price must be above"
            " the entry price"
        )

    quote_currency = pair.partition("/")[2]
    pip_size = _JPY_PIP_SIZE if quote_currency == "JPY" else _DEFAULT_PIP_SIZE
    stop_distance_pips = abs(entry_price - stop_price) / pip_size
    if not math.isfinite(stop_distance_pips) or stop_distance_pips <= 0.0:
        return _failure(
            "Invalid stop evidence: stop_distance_pips must be finite"
            " and greater than zero"
        )

    try:
        drawdown = drawdown_provider.get_drawdown_fraction(account_snapshot)
    except Exception:
        return _failure(
            "Drawdown evidence unavailable:"
            " drawdown_provider.get_drawdown_fraction failed"
        )
    drawdown_fraction = _finite_float(drawdown)
    if drawdown_fraction is None or not 0.0 <= drawdown_fraction <= 1.0:
        return _failure(
            "Invalid drawdown evidence: drawdown_fraction must be a finite"
            " number between 0.0 and 1.0 inclusive"
        )

    resolved = ExecutionRuntimeInputs(
        account_snapshot=account_snapshot,
        entry_price=entry_price,
        stop_distance_pips=stop_distance_pips,
        drawdown_fraction=drawdown_fraction,
    )

    return bridge_execute(
        proposal=proposal,
        orchestrator=orchestrator,
        state_manager=state_manager,
        max_currency_exposure=max_currency_exposure,
        account_snapshot=resolved.account_snapshot,
        entry_price=resolved.entry_price,
        stop_distance_pips=resolved.stop_distance_pips,
        drawdown_fraction=resolved.drawdown_fraction,
    )
