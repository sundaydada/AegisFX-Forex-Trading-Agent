from typing import Dict, Tuple


def net_position(state_manager, proposed_trade: Dict) -> Tuple[float, int]:
    """
    Net a proposed trade against existing FILLED positions.
    Closes opposite-direction trades (FIFO) before creating new exposure.

    Args:
        state_manager: TradeStateManager or PersistentTradeStateManager
        proposed_trade: Dict with currency_pair, direction, approved_position_size

    Returns:
        Tuple of:
            remaining_size: float — size that still needs a new trade (0.0 if fully netted)
            closed_count: int — number of trades closed during netting
    """

    pair = proposed_trade["currency_pair"]
    direction = proposed_trade["direction"]
    size = float(proposed_trade["approved_position_size"])

    opposite = "Short" if direction == "Long" else "Long"

    all_trades = state_manager.get_all_trades()

    # Find FILLED trades with opposite direction for same pair (oldest first)
    opposite_trades = [
        t for t in all_trades
        if t.get("status") == "FILLED"
        and t.get("currency_pair") == pair
        and t.get("direction") == opposite
    ]

    # Sort by created_at for FIFO
    opposite_trades.sort(key=lambda t: t.get("created_at", ""))

    remaining = size
    closed_count = 0

    for trade in opposite_trades:
        if remaining <= 0:
            break

        trade_size = float(trade.get("position_size", trade.get("units", 0)))
        request_id = trade.get("request_id")

        if not request_id:
            continue

        if trade_size <= remaining:
            # Fully close this trade
            state_manager.close_trade(request_id)
            remaining -= trade_size
            closed_count += 1
        else:
            # Partially close — close full trade, reduce remaining to 0
            # Partial fills not supported yet — close entire trade
            state_manager.close_trade(request_id)
            remaining = 0.0
            closed_count += 1

    return remaining, closed_count
