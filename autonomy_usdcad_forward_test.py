"""Controlled 50-CLOSED-trade USD/CAD practice forward test.

Drives the proven MVP unchanged: build_dependencies for practice-only
wiring, check_readiness as a hard gate, run_cycle for one autonomous
decision, and reconcile_closed_usdcad_trade to confirm a closure. It
adds no trading, sizing, or risk logic of its own.

A trade counts only when the broker confirms CLOSED and the local row
is reconciled. The campaign stops permanently at the target and never
opens trade 51.

Importing this module defines constants and functions and does nothing
else; nothing runs until main() is called.
"""

import json
import os
import time

from dotenv import load_dotenv

from autonomy_usdcad_campaign import (
    CAMPAIGN_ID,
    TARGET_CLOSED_TRADES,
    campaign_summary,
    is_complete,
    load_campaign,
    may_open_new_trade,
    record_closed_trade,
    remember_open_context,
    save_campaign,
)
from autonomy_usdcad_cycle import run_cycle
from autonomy_usdcad_reconcile import reconcile_closed_usdcad_trade
from autonomy_usdcad_wiring import build_dependencies, check_readiness

CAMPAIGN_STATE_PATH = "autonomy_usdcad_campaign_state.json"
POLL_SECONDS = 60.0

# Persistence-only shaping for the observational context. Model text is
# unbounded and the state file is rewritten every pass, so it is capped
# on the way to disk; the spread is rounded only when stored. Neither
# rule touches the values run_cycle returned.
CONTEXT_TEXT_MAX_CHARS = 300
SPREAD_PIPS_DECIMALS = 4


def _capped_text(value, limit=CONTEXT_TEXT_MAX_CHARS):
    """Cap persisted model text. Non-strings pass through untouched."""
    if isinstance(value, str) and len(value) > limit:
        return value[:limit]
    return value


def _rounded_pips(value, decimals=SPREAD_PIPS_DECIMALS):
    """Round the persisted spread. A missing value stays missing."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    return round(value, decimals)


# Outcomes that mean "nothing to do right now" rather than a failure.
SAFE_NO_ACTION_OUTCOMES = frozenset(
    {
        "POSITION_PRESENT_NO_ACTION",
        "SIGNAL_REJECTED_NO_ACTION",
        "BLOCKED_NON_TRADEABLE_SIGNAL",
        "NO_RECONCILIATION",
    }
)


def local_filled_trades(state_manager) -> list:
    """The local FILLED rows, using the existing ledger reader."""
    return [
        trade for trade in (state_manager.get_all_trades() or [])
        if trade.get("status") == "FILLED"
    ]


def closed_trade_record(broker, request_id, broker_trade_id) -> dict:
    """Ledger fields for one closed trade, read from the broker."""
    details = broker.get_trade_details(str(broker_trade_id))
    return {
        "broker_trade_id": details.get("broker_trade_id"),
        "direction": "SHORT"
        if (details.get("initial_units") or 0) < 0
        else "LONG",
        "entry_price": details.get("open_price"),
        "exit_price": details.get("average_close_price"),
        "units": details.get("initial_units"),
        "opened_time": details.get("open_time"),
        "closed_time": details.get("close_time"),
        "realized_pl": details.get("realized_pl"),
        "request_id": request_id,
    }


def run_one_pass(*, dependencies, state, broker_positions, filled) -> str:
    """One decision. Returns a short status string for the log.

    The completion check is first and touches no collaborator, so a
    finished campaign cannot reach the broker, the ledger, or the cycle.
    """

    # 0. Target reached: stop permanently. Trade 51 is unreachable.
    if not may_open_new_trade(state):
        return "COMPLETE"

    # 1. A broker-confirmed closure heals local state, and counts only
    #    when this runner opened the trade (see open_context, rule B).
    if len(broker_positions) == 0 and len(filled) == 1:
        broker = dependencies["broker"]
        reconciliation = reconcile_closed_usdcad_trade(
            broker=broker,
            state_manager=dependencies["state_manager"],
            broker_positions=broker_positions,
            local_filled=filled,
        )
        if reconciliation.get("outcome") == "RECONCILED_CLOSED_TRADE":
            broker_trade_id = str(
                reconciliation.get("broker_trade_id") or ""
            )
            if broker_trade_id not in (state.get("open_context") or {}):
                # Opened before this campaign, so it never carried the
                # frozen 1R exit rule. Local state is healed; the
                # 50-trade sample stays on one exit rule.
                return "PRE_CAMPAIGN_TRADE_NOT_COUNTED"

            record = closed_trade_record(
                broker,
                reconciliation.get("request_id"),
                broker_trade_id,
            )
            counted = record_closed_trade(state, record)
            return "COUNTED" if counted else "ALREADY_COUNTED"
        return "AWAITING_CLOSE_CONFIRMATION"

    # 2. A live position means wait; never a second simultaneous trade.
    if broker_positions or filled:
        return "POSITION_PRESENT"

    # 3. Flat, and only then, one autonomous cycle.
    result = run_cycle(
        broker=dependencies["broker"],
        state_manager=dependencies["state_manager"],
        signal_provider=dependencies["signal_provider"],
        proposal_queue=dependencies["proposal_queue"],
        executor=dependencies["executor"],
    )
    outcome = result.get("outcome")

    if outcome == "PROPOSAL_EXECUTED":
        broker_result = (
            result.get("execution_result") or {}
        ).get("execution_result") or {}
        signal = result.get("signal") or {}
        remember_open_context(
            state,
            broker_result.get("broker_trade_id"),
            {
                # Unchanged by this slice.
                "confidence": signal.get("confidence"),
                "stop_loss_price": result.get("stop_loss_price"),
                "take_profit_price": result.get("take_profit_price"),
                # Observational context, so a closed trade can later be
                # explained. Nothing reads these back to make a decision.
                "regime": signal.get("regime"),
                "trend": signal.get("trend"),
                "volatility": signal.get("volatility"),
                "range_percentile": signal.get("range_percentile"),
                "position_in_range": signal.get("position_in_range"),
                "recommended_strategy": signal.get("recommended_strategy"),
                "reason": _capped_text(signal.get("reason")),
                "summary": _capped_text(signal.get("summary")),
                "proposal_id": result.get("proposal_id"),
                "entry_bid": result.get("entry_bid"),
                "entry_ask": result.get("entry_ask"),
                "entry_spread": result.get("entry_spread"),
                "entry_spread_pips": _rounded_pips(
                    result.get("entry_spread_pips")
                ),
            },
        )
    return outcome


def poll_once(*, dependencies, state) -> str:
    """Read broker and local state, then take one decision.

    Raises whatever the broker or ledger raises; the caller owns the
    failure boundary. Touches no collaborator once the campaign is
    complete.
    """

    if not may_open_new_trade(state):
        return "COMPLETE"

    broker_positions = dependencies["broker"].get_open_positions() or []
    filled = local_filled_trades(dependencies["state_manager"])

    return run_one_pass(
        dependencies=dependencies,
        state=state,
        broker_positions=broker_positions,
        filled=filled,
    )


def run_campaign(
    *,
    dependencies,
    state,
    state_path=CAMPAIGN_STATE_PATH,
    sleep=time.sleep,
) -> dict:
    """Poll until the campaign completes.

    A single broker, network, or ledger failure must not end an
    unattended campaign. A failed pass took no trading action and
    changed no campaign state, so it is logged and skipped; the next
    normal poll is the only recovery attempt. There is no retry here
    and none inside the broker, reconciliation, or the cycle.

    Only Exception is caught, so KeyboardInterrupt and SystemExit still
    terminate the runner normally.
    """

    while not is_complete(state):
        try:
            status = poll_once(dependencies=dependencies, state=state)
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "event": "campaign_pass_failed",
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                        "action": "pass skipped; no trade, no count change",
                    }
                ),
                flush=True,
            )
            sleep(POLL_SECONDS)
            continue

        save_campaign(state_path, state)

        summary = campaign_summary(state)
        print(
            f"{status} | "
            f"{summary['completed_closed_trades']}/{summary['target_trades']}"
            f" | net {summary['net_realized_pl']}",
            flush=True,
        )

        if is_complete(state):
            break

        sleep(POLL_SECONDS)

    save_campaign(state_path, state)
    return campaign_summary(state)


def main():
    load_dotenv()

    api_key = os.getenv("OANDA_DEMO_API_KEY", "").strip()
    account_id = os.getenv("OANDA_ACCOUNT_ID", "").strip()
    if not api_key or not account_id:
        print("CAMPAIGN_BLOCKED_MISSING_CREDENTIALS")
        return

    dependencies = build_dependencies(
        api_key=api_key,
        account_id=account_id,
    )

    readiness = check_readiness(dependencies)
    if readiness.get("ready") is not True:
        print("CAMPAIGN_BLOCKED_READINESS")
        print(readiness.get("failures"))
        return

    state = load_campaign(CAMPAIGN_STATE_PATH)
    print(f"Campaign {CAMPAIGN_ID} target {TARGET_CLOSED_TRADES}")
    print(campaign_summary(state))

    summary = run_campaign(dependencies=dependencies, state=state)

    print("CAMPAIGN_COMPLETE", flush=True)
    print(summary, flush=True)


if __name__ == "__main__":
    main()
