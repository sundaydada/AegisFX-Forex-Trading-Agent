"""Import-safe controller for the operator-reviewed execution path.

Composes the two existing public boundaries: the execution-wiring
factory (configuration in, owned dependency bundle out) and the
reviewed-proposal action adapter (proposal plus raw operator stop in,
structured execution result out). The controller forwards the proposal
object and the operator's raw stop untouched, never acquires or
fabricates runtime evidence, never marks execution itself, and always
attempts to close the wiring bundle exactly once after successful
construction — preserving an action failure even if cleanup also fails.

Importing this module constructs nothing, reads no environment
variables, opens no database, and makes no external call.
"""

from dashboard.execution_wiring import (
    build_reviewed_execution_wiring,
)
from dashboard.proposal_execution_action import (
    execute_reviewed_proposal_action,
    preview_reviewed_proposal_action,
)


def execute_reviewed_proposal_from_dashboard(
    *,
    proposal,
    raw_stop_loss_price,
    api_key,
    account_id,
    base_url,
    trade_state_db_path,
    drawdown_db_path,
    start_of_day_nav_db_path,
    approval_db_path,
    max_currency_exposure,
    max_quote_age_seconds,
    now_utc,
):
    """Build wiring from configuration, run the reviewed action once.

    Returns the action adapter's result unchanged by identity. The
    proposal and raw_stop_loss_price pass through untouched — parsing,
    validation, fresh-quote acquisition, stop-distance derivation, and
    execution marking all remain owned by the adapter and the layers
    beneath it. Factory-construction failures propagate unchanged; an
    action failure is re-raised with its original type and traceback
    even when wiring cleanup also fails.
    """

    wiring = build_reviewed_execution_wiring(
        api_key=api_key,
        account_id=account_id,
        base_url=base_url,
        trade_state_db_path=trade_state_db_path,
        drawdown_db_path=drawdown_db_path,
        start_of_day_nav_db_path=start_of_day_nav_db_path,
        approval_db_path=approval_db_path,
        max_currency_exposure=max_currency_exposure,
        max_quote_age_seconds=max_quote_age_seconds,
        now_utc=now_utc,
    )

    try:
        result = execute_reviewed_proposal_action(
            proposal=proposal,
            raw_stop_loss_price=raw_stop_loss_price,
            **wiring.action_kwargs,
        )
    except BaseException:
        try:
            wiring.close()
        except BaseException:
            pass
        raise
    else:
        wiring.close()
        return result


def preview_reviewed_proposal_from_dashboard(
    *,
    proposal,
    raw_stop_loss_price,
    api_key,
    account_id,
    base_url,
    trade_state_db_path,
    drawdown_db_path,
    start_of_day_nav_db_path,
    approval_db_path,
    max_currency_exposure,
    max_quote_age_seconds,
    now_utc,
):
    """Build wiring from configuration, run the preview action once.

    Uses the same wiring factory and cleanup convention as the execution
    controller, but forwards only the resolving-and-sizing dependencies
    to the non-submitting preview action and returns its result
    unchanged by identity. Never calls the execute action, mark_executed,
    the orchestrator, or the broker's order path.
    """

    wiring = build_reviewed_execution_wiring(
        api_key=api_key,
        account_id=account_id,
        base_url=base_url,
        trade_state_db_path=trade_state_db_path,
        drawdown_db_path=drawdown_db_path,
        start_of_day_nav_db_path=start_of_day_nav_db_path,
        approval_db_path=approval_db_path,
        max_currency_exposure=max_currency_exposure,
        max_quote_age_seconds=max_quote_age_seconds,
        now_utc=now_utc,
    )

    try:
        action_kwargs = wiring.action_kwargs
        result = preview_reviewed_proposal_action(
            proposal=proposal,
            raw_stop_loss_price=raw_stop_loss_price,
            broker=action_kwargs["broker"],
            quote_provider=action_kwargs["quote_provider"],
            drawdown_provider=action_kwargs["drawdown_provider"],
            now_utc=action_kwargs["now_utc"],
            max_quote_age_seconds=action_kwargs["max_quote_age_seconds"],
        )
    except BaseException:
        try:
            wiring.close()
        except BaseException:
            pass
        raise
    else:
        wiring.close()
        return result
