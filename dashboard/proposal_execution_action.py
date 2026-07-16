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
