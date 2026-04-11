from typing import Dict, List


def compute_performance_metrics(all_trades: List[Dict]) -> Dict:
    """
    Compute trading performance KPIs from CLOSED trades only.

    Args:
        all_trades: Full trade list from state_manager.get_all_trades()

    Returns:
        {
            "total_trades": int,
            "win_rate": float,
            "total_profit": float,
            "total_pips": float
        }
    """

    closed_trades = [t for t in all_trades if t.get("status") == "CLOSED"]

    total_trades = len(closed_trades)

    if total_trades == 0:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "total_profit": 0.0,
            "total_pips": 0.0,
        }

    total_pips = 0.0
    total_profit = 0.0
    winning_trades = 0

    for trade in closed_trades:
        entry_price = float(trade.get("fill_price", 0.0))
        close_price = float(trade.get("close_price", 0.0))
        direction = trade.get("direction", "")
        size = float(trade.get("position_size", trade.get("units", 0)))

        if direction == "Long":
            pip_diff = close_price - entry_price
        elif direction == "Short":
            pip_diff = entry_price - close_price
        else:
            pip_diff = 0.0

        trade_profit = pip_diff * size
        total_pips += pip_diff
        total_profit += trade_profit

        if trade_profit > 0:
            winning_trades += 1

    win_rate = (winning_trades / total_trades) * 100

    return {
        "total_trades": total_trades,
        "win_rate": round(win_rate, 1),
        "total_profit": round(total_profit, 4),
        "total_pips": round(total_pips, 4),
    }
