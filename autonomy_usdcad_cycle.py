"""First slice of the supervised USD/CAD autonomous cycle.

The cycle refuses to run anywhere but the OANDA practice environment,
then reconciles broker and local position state and stops there.
When the broker's open-position count and the local FILLED-trade count
disagree, true exposure is unknown, so the cycle fails closed and takes
no autonomous action of any kind. When the counts agree and both are
zero it requests one USD/CAD signal and either rejects it as
non-tradeable or submits exactly one uniquely identified proposal,
approves that exact proposal, re-reads it by identity, and executes it
once with a mandatory protective stop through the injected reviewed
executor; when they agree that a position is open it reports that and
stops. Position closure is not implemented yet.

Importing this module constructs nothing, reads no environment
variables, opens no database, makes no network call, and starts no loop.
"""

from math import isfinite
from typing import Dict, List
from uuid import uuid4

PRACTICE_BASE_URL = "https://api-fxpractice.oanda.com"
USDCAD_PIP_SIZE = 0.0001
AUTONOMOUS_STOP_DISTANCE_PIPS = 20.0
PRICE_DECIMAL_PLACES = 5


def _as_list(value) -> List:
    """Treat a missing or None collection as empty."""
    if value is None:
        return []
    return list(value)


def _new_proposal_id() -> str:
    """Return a unique identity for one autonomous proposal."""
    return f"PROP-AUTO-{uuid4().hex}"


def _is_positive_price(value) -> bool:
    """True when value is a finite, positive, non-bool number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return isfinite(value) and value > 0.0


def run_cycle(
    *,
    broker,
    state_manager,
    signal_provider,
    proposal_queue,
    executor,
) -> Dict[str, object]:
    """Reconcile broker and local position state for one cycle.

    Refuses to proceed unless the broker's endpoint is exactly the OANDA
    practice URL. That check runs before any broker or ledger read, so a
    blocked environment touches no state at all.

    Otherwise reads the broker's open positions once and the local trade
    ledger once. A disagreement between the broker open-position count
    and the local FILLED-trade count blocks the cycle: no signal is
    requested, no proposal is created or approved, nothing is executed,
    and no broker close is attempted.

    When both counts are zero the cycle requests exactly one USD/CAD
    signal and returns it unmodified. A signal is tradeable only when
    trade_bias is exactly LONG or SHORT, confidence is a non-bool number
    of at least 70, and execution_allowed is exactly True; otherwise the
    cycle rejects it with a cause-specific reason. An accepted signal
    submits exactly one proposal carrying an identity this cycle
    generates, then approves that exact proposal, re-reads it from the
    queue by exact id and status, derives a mandatory protective stop
    from one fresh quote, and executes it once through the injected
    reviewed executor. Creation that does not insert exactly one row, an
    approval that does not return True, a missing exact approved record,
    an invalid quote or stop, or an execution result that is not a strict
    Filled success all fail closed without retry. No signal is requested
    on the blocked-environment, mismatch, or position-open paths.

    The broker is never used to submit or close an order; the injected
    executor is the only execution path.
    """

    broker_base_url = getattr(broker, "base_url", None)
    if broker_base_url != PRACTICE_BASE_URL:
        return {
            "outcome": "BLOCKED_NON_PRACTICE_ENVIRONMENT",
            "reason": (
                "Autonomous trading requires the OANDA practice environment; "
                f"received broker endpoint {broker_base_url!r}."
            ),
        }

    broker_positions = _as_list(broker.get_open_positions())
    local_trades = _as_list(state_manager.get_all_trades())

    local_filled = [
        trade for trade in local_trades
        if trade.get("status") == "FILLED"
    ]

    broker_open_count = len(broker_positions)
    local_filled_count = len(local_filled)

    if broker_open_count != local_filled_count:
        return {
            "outcome": "BLOCKED_STATE_MISMATCH",
            "reason": (
                f"Broker reports {broker_open_count} open position(s) but"
                f" local state reports {local_filled_count} FILLED"
                " trade(s); no autonomous action was taken because broker"
                " and local state disagree."
            ),
            "broker_open_count": broker_open_count,
            "local_filled_count": local_filled_count,
        }

    if broker_open_count == 0 and local_filled_count == 0:
        signal = signal_provider(pair="USD/CAD")

        trade_bias = signal.get("trade_bias")
        confidence = signal.get("confidence")
        execution_allowed = signal.get("execution_allowed")

        # Rules are checked in order; the first failure is reported. The
        # confidence type checks come before the numeric comparison so a
        # non-numeric value rejects instead of raising.
        rejection_reason = None
        if trade_bias not in {"LONG", "SHORT"}:
            rejection_reason = (
                f"USD/CAD signal bias is not tradeable: {trade_bias!r}."
            )
        elif (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or confidence < 70
        ):
            rejection_reason = (
                "USD/CAD signal confidence must be numeric and at least"
                f" 70; received {confidence!r}."
            )
        elif execution_allowed is not True:
            rejection_reason = "USD/CAD signal execution is not allowed."

        if rejection_reason is not None:
            return {
                "outcome": "SIGNAL_REJECTED_NO_ACTION",
                "reason": rejection_reason,
                "pair": "USD/CAD",
                "signal": signal,
                "broker_open_count": broker_open_count,
                "local_filled_count": local_filled_count,
            }

        # The cycle owns proposal identity so it can approve exactly what
        # it created. status and created_at remain the queue's to assign.
        proposal_id = _new_proposal_id()
        proposal = {
            "proposal_id": proposal_id,
            "pair": "USD/CAD",
            "direction": trade_bias,
            "suggested_size": 0.5,
            "confidence": confidence,
            "strategy": "Autonomous_USDCAD_MVP",
            "reason": (
                "Autonomous USD/CAD MVP proposal generated from an "
                "accepted signal."
            ),
        }
        proposal_count = proposal_queue.add_proposals([proposal])

        if proposal_count != 1:
            return {
                "outcome": "BLOCKED_PROPOSAL_NOT_CREATED",
                "reason": (
                    "The autonomous proposal was not inserted exactly once; "
                    "approval was not attempted because creation may have "
                    "failed or the proposal ID may be a duplicate."
                ),
                "pair": "USD/CAD",
                "signal": signal,
                "proposal": proposal,
                "proposal_id": proposal_id,
                "proposal_count": proposal_count,
                "approval_succeeded": False,
                "broker_open_count": broker_open_count,
                "local_filled_count": local_filled_count,
            }

        approval_succeeded = proposal_queue.approve_proposal(proposal_id)

        if approval_succeeded is not True:
            return {
                "outcome": "BLOCKED_PROPOSAL_NOT_APPROVED",
                "reason": (
                    "The exact autonomous proposal was created but approval "
                    "failed; execution was not attempted."
                ),
                "pair": "USD/CAD",
                "signal": signal,
                "proposal": proposal,
                "proposal_id": proposal_id,
                "proposal_count": proposal_count,
                "approval_succeeded": False,
                "broker_open_count": broker_open_count,
                "local_filled_count": local_filled_count,
            }

        # Identity is the only safe selector: the backlog can hold other
        # APPROVED USD/CAD proposals with identical fields.
        approved_records = proposal_queue.get_approved_proposals()
        matching_approved = [
            record
            for record in (approved_records or [])
            if isinstance(record, dict)
            and record.get("proposal_id") == proposal_id
            and record.get("status") == "APPROVED"
        ]

        if len(matching_approved) != 1:
            return {
                "outcome": "BLOCKED_APPROVED_PROPOSAL_NOT_FOUND",
                "reason": (
                    "The exact approved autonomous proposal record could "
                    "not be retrieved; quote retrieval and execution were "
                    "not attempted."
                ),
                "pair": "USD/CAD",
                "signal": signal,
                "proposal": proposal,
                "proposal_id": proposal_id,
                "proposal_count": proposal_count,
                "approval_succeeded": True,
                "execution_succeeded": False,
                "broker_open_count": broker_open_count,
                "local_filled_count": local_filled_count,
            }

        approved_proposal = matching_approved[0]

        quote = broker.get_quote("USD/CAD")
        bid = quote.get("bid") if isinstance(quote, dict) else None
        ask = quote.get("ask") if isinstance(quote, dict) else None

        if not (
            isinstance(quote, dict)
            and quote.get("currency_pair") == "USD/CAD"
            and _is_positive_price(bid)
            and _is_positive_price(ask)
            and ask >= bid
        ):
            return {
                "outcome": "BLOCKED_INVALID_QUOTE",
                "reason": (
                    "A valid USD/CAD quote was not available; execution "
                    "was not attempted."
                ),
                "pair": "USD/CAD",
                "signal": signal,
                "proposal": approved_proposal,
                "proposal_id": proposal_id,
                "proposal_count": proposal_count,
                "approval_succeeded": True,
                "execution_succeeded": False,
                "broker_open_count": broker_open_count,
                "local_filled_count": local_filled_count,
            }

        direction = approved_proposal.get("direction")
        stop_distance = AUTONOMOUS_STOP_DISTANCE_PIPS * USDCAD_PIP_SIZE

        if direction == "LONG":
            stop_loss_price = round(
                ask - stop_distance,
                PRICE_DECIMAL_PLACES,
            )
        elif direction == "SHORT":
            stop_loss_price = round(
                bid + stop_distance,
                PRICE_DECIMAL_PLACES,
            )
        else:
            return {
                "outcome": "BLOCKED_INVALID_APPROVED_PROPOSAL",
                "reason": (
                    "The approved autonomous proposal carries no tradeable "
                    f"direction: {direction!r}; execution was not attempted."
                ),
                "pair": "USD/CAD",
                "signal": signal,
                "proposal": approved_proposal,
                "proposal_id": proposal_id,
                "proposal_count": proposal_count,
                "approval_succeeded": True,
                "execution_succeeded": False,
                "broker_open_count": broker_open_count,
                "local_filled_count": local_filled_count,
            }

        if not _is_positive_price(stop_loss_price) or not (
            stop_loss_price < ask
            if direction == "LONG"
            else stop_loss_price > bid
        ):
            return {
                "outcome": "BLOCKED_INVALID_STOP",
                "reason": (
                    "The derived protective stop was not a valid price on "
                    "the protective side of the entry; execution was not "
                    "attempted."
                ),
                "pair": "USD/CAD",
                "signal": signal,
                "proposal": approved_proposal,
                "proposal_id": proposal_id,
                "proposal_count": proposal_count,
                "approval_succeeded": True,
                "stop_loss_price": stop_loss_price,
                "execution_succeeded": False,
                "broker_open_count": broker_open_count,
                "local_filled_count": local_filled_count,
            }

        execution_result = executor(
            proposal=approved_proposal,
            raw_stop_loss_price=stop_loss_price,
        )

        nested_result = (
            execution_result.get("execution_result")
            if isinstance(execution_result, dict)
            else None
        )
        if not (
            isinstance(execution_result, dict)
            and execution_result.get("success") is True
            and isinstance(nested_result, dict)
            and nested_result.get("execution_status") == "Filled"
        ):
            return {
                "outcome": "BLOCKED_EXECUTION_FAILED",
                "reason": (
                    "The reviewed execution path did not return a strict "
                    "Filled success; no retry was attempted."
                ),
                "pair": "USD/CAD",
                "signal": signal,
                "proposal": approved_proposal,
                "proposal_id": proposal_id,
                "proposal_count": proposal_count,
                "approval_succeeded": True,
                "stop_loss_price": stop_loss_price,
                "execution_succeeded": False,
                "execution_result": execution_result,
                "broker_open_count": broker_open_count,
                "local_filled_count": local_filled_count,
            }

        return {
            "outcome": "PROPOSAL_EXECUTED",
            "reason": (
                "The exact approved USD/CAD autonomous proposal was "
                "executed once with a mandatory protective stop."
            ),
            "pair": "USD/CAD",
            "signal": signal,
            "proposal": approved_proposal,
            "proposal_id": proposal_id,
            "proposal_count": proposal_count,
            "approval_succeeded": True,
            "stop_loss_price": stop_loss_price,
            "execution_succeeded": True,
            "execution_result": execution_result,
            "broker_open_count": broker_open_count,
            "local_filled_count": local_filled_count,
        }

    return {
        "outcome": "POSITION_PRESENT_NO_ACTION",
        "reason": (
            "Broker and local state agree that a position is open; "
            "position management is not implemented in this slice."
        ),
        "broker_open_count": broker_open_count,
        "local_filled_count": local_filled_count,
    }
