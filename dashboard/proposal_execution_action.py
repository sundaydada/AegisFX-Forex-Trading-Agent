"""Operator-reviewed execution action for an APPROVED dashboard proposal.

Thin, import-safe adapter between the dashboard UI layer and the
runtime-input resolver. It validates only locally entered evidence (the
proposal identity and the operator's raw absolute stop price), then
delegates exactly once to execute_approved_proposal_with_runtime_inputs,
which owns fresh-quote acquisition, directional stop validation, and
stop-distance derivation. On a strictly successful result the proposal
is marked executed exactly once. The adapter never generates, infers,
defaults, or substitutes a stop.
"""

import math
from collections.abc import Mapping
from datetime import datetime
from numbers import Real
from typing import Dict, Optional

from dashboard.execution_runtime_inputs import (
    execute_approved_proposal_with_runtime_inputs,
)
from execution.proposal_sizing import size_trade_proposal


def _failure(message: str) -> Dict:
    return {"success": False, "message": message}


def _parse_reviewed_stop(raw_stop_loss_price) -> Optional[float]:
    if isinstance(raw_stop_loss_price, bool):
        return None

    if isinstance(raw_stop_loss_price, str):
        text = raw_stop_loss_price.strip()
        if not text:
            return None
        try:
            parsed = float(text)
        except ValueError:
            return None
    elif isinstance(raw_stop_loss_price, Real):
        try:
            parsed = float(raw_stop_loss_price)
        except (OverflowError, ValueError):
            return None
    else:
        return None

    if not math.isfinite(parsed) or parsed <= 0.0:
        return None
    return parsed


def execute_reviewed_proposal_action(
    *,
    proposal,
    raw_stop_loss_price,
    broker,
    quote_provider,
    drawdown_provider,
    bridge_execute,
    mark_executed,
    now_utc: datetime,
    max_quote_age_seconds: float,
    max_currency_exposure: float,
    orchestrator,
    state_manager,
):
    """Validate operator-entered evidence, delegate once, mark once.

    Returns the delegate result unchanged by identity, except for local
    input-validation failures, which return the runtime-input resolver's
    structured failure style before any delegation occurs.

    orchestrator and state_manager are forwarded by identity to the
    runtime-input resolver, which itself only forwards them into
    bridge_execute — this adapter never inspects or invokes either.
    """

    if not isinstance(proposal, Mapping):
        return _failure(
            "Invalid proposal evidence: expected a proposal mapping"
        )

    proposal_id = proposal.get("proposal_id")
    if not isinstance(proposal_id, str) or not proposal_id.strip():
        return _failure(
            "Invalid proposal evidence: proposal_id must be a"
            " nonempty string"
        )

    parsed_stop = _parse_reviewed_stop(raw_stop_loss_price)
    if parsed_stop is None:
        return _failure(
            "Invalid stop evidence: the reviewed stop_loss_price must be"
            " an explicit finite price greater than zero"
        )

    result = execute_approved_proposal_with_runtime_inputs(
        proposal=proposal,
        orchestrator=orchestrator,
        state_manager=state_manager,
        max_currency_exposure=max_currency_exposure,
        broker=broker,
        quote_provider=quote_provider,
        drawdown_provider=drawdown_provider,
        stop_loss_price=parsed_stop,
        now_utc=now_utc,
        max_quote_age_seconds=max_quote_age_seconds,
        bridge_execute=bridge_execute,
    )

    if isinstance(result, Mapping) and result.get("success") is True:
        mark_executed(proposal_id)

    return result


def preview_reviewed_proposal_action(
    *,
    proposal,
    raw_stop_loss_price,
    broker,
    quote_provider,
    drawdown_provider,
    now_utc,
    max_quote_age_seconds,
):
    """Resolve and size an APPROVED proposal without submitting it.

    Runs the same runtime-input resolver as the execute action, but with
    a local non-submitting callback that sizes the trade via the
    existing sizing path and returns the preview evidence as a new plain
    dict. Never invokes an orchestrator, broker order, approval queue,
    execution marking, or trade-state persistence, and returns the
    resolver's structured failure unchanged when any evidence is
    invalid. The operator's raw stop text is preserved verbatim for
    later changed-input detection.
    """

    if not isinstance(proposal, Mapping):
        return _failure(
            "Invalid proposal evidence: expected a proposal mapping"
        )

    proposal_id = proposal.get("proposal_id")
    if not isinstance(proposal_id, str) or not proposal_id.strip():
        return _failure(
            "Invalid proposal evidence: proposal_id must be a"
            " nonempty string"
        )

    parsed_stop = _parse_reviewed_stop(raw_stop_loss_price)
    if parsed_stop is None:
        return _failure(
            "Invalid stop evidence: the reviewed stop_loss_price must be"
            " an explicit finite price greater than zero"
        )

    # The resolver validates the quote's timestamp but does not forward
    # it, so the single get_quote call is captured in passing; the
    # provider object itself is handed to the resolver unchanged.
    captured_quotes = []
    original_get_quote = quote_provider.get_quote

    def _capturing_get_quote(pair):
        quote = original_get_quote(pair)
        captured_quotes.append(quote)
        return quote

    def _preview_sizing_callback(
        *,
        proposal,
        orchestrator,
        state_manager,
        max_currency_exposure,
        account_snapshot,
        entry_price,
        stop_distance_pips,
        drawdown_fraction,
    ):
        sizing = size_trade_proposal(
            account_snapshot=account_snapshot,
            pair=proposal["pair"],
            side=proposal["direction"],
            entry_price=entry_price,
            stop_distance_pips=stop_distance_pips,
            drawdown_fraction=drawdown_fraction,
        )
        return {
            "proposal_id": proposal_id,
            "pair": sizing.pair,
            "direction": sizing.side,
            "entry_price": sizing.entry_price,
            "units": sizing.units,
            "risk_fraction": sizing.risk_fraction,
            "risk_amount": sizing.risk_amount,
            "stop_loss_price": parsed_stop,
            "drawdown_fraction": sizing.drawdown_fraction,
            "quote_timestamp": captured_quotes[0]["timestamp"],
            "raw_stop_loss_price": raw_stop_loss_price,
        }

    quote_provider.get_quote = _capturing_get_quote
    try:
        return execute_approved_proposal_with_runtime_inputs(
            proposal=proposal,
            orchestrator=None,
            state_manager=None,
            max_currency_exposure=None,
            broker=broker,
            quote_provider=quote_provider,
            drawdown_provider=drawdown_provider,
            stop_loss_price=parsed_stop,
            now_utc=now_utc,
            max_quote_age_seconds=max_quote_age_seconds,
            bridge_execute=_preview_sizing_callback,
        )
    finally:
        del quote_provider.get_quote
