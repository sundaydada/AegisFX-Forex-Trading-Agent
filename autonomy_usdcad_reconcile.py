"""Mark one locally-FILLED USD/CAD trade closed after a broker close.

Read-only against the broker: it looks up one Trade and never submits,
closes, retries, or estimates. Anything short of a single unambiguous
match returns NO_RECONCILIATION and leaves local state untouched.

Importing this module defines one function and does nothing else.
"""

NO_RECONCILIATION = {"outcome": "NO_RECONCILIATION"}


def reconcile_closed_usdcad_trade(
    *,
    broker,
    state_manager,
    broker_positions,
    local_filled,
):
    """Close one local row when the broker confirms that exact Trade."""

    if broker_positions or len(local_filled) != 1:
        return dict(NO_RECONCILIATION)

    local = local_filled[0]
    request_id = local.get("request_id")
    broker_trade_id = local.get("broker_trade_id")

    if (
        local.get("currency_pair") != "USD/CAD"
        or local.get("status") != "FILLED"
        or not broker_trade_id
    ):
        return dict(NO_RECONCILIATION)

    details = broker.get_trade_details(broker_trade_id)

    average_close_price = details.get("average_close_price")
    close_time = details.get("close_time")

    if (
        details.get("lookup_status") != "FOUND"
        or details.get("broker_trade_id") != broker_trade_id
        or details.get("currency_pair") != "USD/CAD"
        or details.get("state") != "CLOSED"
        or average_close_price is None
        or not close_time
    ):
        return dict(NO_RECONCILIATION)

    state_manager.close_trade(
        request_id,
        close_price=average_close_price,
        exit_timestamp=close_time,
    )

    return {
        "outcome": "RECONCILED_CLOSED_TRADE",
        "request_id": request_id,
        "broker_trade_id": broker_trade_id,
        "close_price": average_close_price,
        "exit_timestamp": close_time,
        "realized_pl": details["realized_pl"],
    }
