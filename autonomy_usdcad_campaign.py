"""Pure campaign state and ledger for the USD/CAD forward test.

Holds the target, the completed-trade ledger, and the summary. Contains
no broker, database, network, or clock access, so every rule here is
testable without OANDA.

A trade counts only when it is broker-confirmed CLOSED and reconciled
locally. Signals, cycles, proposals, rejections and open trades never
count. Once the target is reached the campaign is COMPLETE and refuses
to authorise another trade.

Importing this module defines constants and functions and does nothing
else.
"""

import json
import os

CAMPAIGN_ID = "USDCAD_MVP_50_01"
TARGET_CLOSED_TRADES = 50

STATUS_RUNNING = "RUNNING"
STATUS_COMPLETE = "COMPLETE"


def new_campaign() -> dict:
    """A fresh campaign at 0 of the target."""
    return {
        "campaign_id": CAMPAIGN_ID,
        "target_trades": TARGET_CLOSED_TRADES,
        "status": STATUS_RUNNING,
        "trades": [],
        "open_context": {},
    }


def load_campaign(path: str) -> dict:
    """Load persisted campaign state, or start a fresh one."""
    if not os.path.exists(path):
        return new_campaign()

    with open(path, "r", encoding="utf-8") as handle:
        state = json.load(handle)

    # Tolerate a file written by an older shape without losing progress.
    state.setdefault("campaign_id", CAMPAIGN_ID)
    state.setdefault("target_trades", TARGET_CLOSED_TRADES)
    state.setdefault("trades", [])
    state.setdefault("open_context", {})
    state.setdefault("status", STATUS_RUNNING)
    return state


def save_campaign(path: str, state: dict) -> None:
    """Persist campaign state so a restart resumes, never resets."""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)


def completed_closed_trades(state: dict) -> int:
    """How many broker-confirmed CLOSED trades have been recorded."""
    return len(state.get("trades") or [])


def is_complete(state: dict) -> bool:
    """True once the target is reached; never opens another trade."""
    return completed_closed_trades(state) >= state.get(
        "target_trades", TARGET_CLOSED_TRADES
    )


def may_open_new_trade(state: dict) -> bool:
    """The single authority for opening trade N+1."""
    return not is_complete(state)


def recorded_trade_ids(state: dict) -> set:
    """Broker trade ids already counted, for restart de-duplication."""
    return {
        str(trade.get("broker_trade_id"))
        for trade in (state.get("trades") or [])
    }


def remember_open_context(state: dict, broker_trade_id, context: dict) -> None:
    """Keep the decision evidence a closed trade cannot recover later."""
    if not broker_trade_id:
        return
    state.setdefault("open_context", {})[str(broker_trade_id)] = dict(context)


def record_closed_trade(state: dict, trade: dict) -> bool:
    """Append one broker-confirmed CLOSED trade. True if newly counted.

    De-duplicates on broker_trade_id, so a restart that re-observes the
    same closure cannot inflate the count.
    """

    broker_trade_id = str(trade.get("broker_trade_id") or "")
    if not broker_trade_id:
        return False

    if broker_trade_id in recorded_trade_ids(state):
        return False

    context = (state.get("open_context") or {}).get(broker_trade_id, {})

    realized_pl = trade.get("realized_pl")
    record = {
        "trade_number": completed_closed_trades(state) + 1,
        "broker_trade_id": broker_trade_id,
        "direction": trade.get("direction"),
        "confidence": context.get("confidence"),
        "entry_price": trade.get("entry_price"),
        "exit_price": trade.get("exit_price"),
        "stop_price": context.get("stop_loss_price"),
        "take_profit_price": context.get("take_profit_price"),
        "units": trade.get("units"),
        "opened_time": trade.get("opened_time"),
        "closed_time": trade.get("closed_time"),
        "realized_pl": realized_pl,
        "result": _win_or_loss(realized_pl),
    }

    state.setdefault("trades", []).append(record)
    state.get("open_context", {}).pop(broker_trade_id, None)

    if is_complete(state):
        state["status"] = STATUS_COMPLETE

    return True


def _win_or_loss(realized_pl):
    """WIN above zero, LOSS at or below zero, UNKNOWN when absent."""
    if realized_pl is None:
        return "UNKNOWN"
    return "WIN" if realized_pl > 0 else "LOSS"


def campaign_summary(state: dict) -> dict:
    """Campaign-level totals. No advanced analytics."""
    trades = state.get("trades") or []
    target = state.get("target_trades", TARGET_CLOSED_TRADES)
    completed = len(trades)

    wins = sum(1 for trade in trades if trade.get("result") == "WIN")
    losses = sum(1 for trade in trades if trade.get("result") == "LOSS")
    net_realized_pl = sum(
        trade.get("realized_pl") or 0.0 for trade in trades
    )

    return {
        "campaign_id": state.get("campaign_id", CAMPAIGN_ID),
        "status": state.get("status", STATUS_RUNNING),
        "target_trades": target,
        "completed_closed_trades": completed,
        "remaining_trades": max(target - completed, 0),
        "net_realized_pl": round(net_realized_pl, 5),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / completed, 4) if completed else 0.0,
    }
