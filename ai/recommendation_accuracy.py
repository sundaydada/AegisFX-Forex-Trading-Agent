from typing import Dict


class RecommendationAccuracyAnalytics:
    """
    Read-only accuracy scoring for executed AI recommendations.
    Measures how often AI-driven trades turn out profitable.
    """

    @staticmethod
    def compute_accuracy_metrics(proposal_queue, trade_state_manager) -> Dict:
        """
        Score the AI's recommendation accuracy based on CLOSED trades
        that originated from EXECUTED proposals.

        Args:
            proposal_queue: ProposalApprovalQueue instance
            trade_state_manager: PersistentTradeStateManager instance

        Returns:
            {
                "executed_recommendations": int,
                "profitable_recommendations": int,
                "accuracy_rate": float,
                "total_profit": float,
                "average_profit": float,
                "best_trade": float,
                "worst_trade": float
            }
        """

        # Identify EXECUTED proposal IDs
        try:
            decisions = proposal_queue.get_recent_decisions(limit=100_000)
        except Exception:
            decisions = []

        executed_proposal_ids = {
            d["proposal_id"]
            for d in decisions
            if d.get("status") == "EXECUTED"
        }

        # Empty fallback
        empty = {
            "executed_recommendations": 0,
            "profitable_recommendations": 0,
            "accuracy_rate": 0.0,
            "total_profit": 0.0,
            "average_profit": 0.0,
            "best_trade": 0.0,
            "worst_trade": 0.0,
        }

        if not executed_proposal_ids:
            return empty

        # Pull CLOSED AI trades
        all_trades = trade_state_manager.get_all_trades()
        ai_closed = []
        for t in all_trades:
            if t.get("status") != "CLOSED":
                continue
            request_id = str(t.get("request_id", ""))
            if not request_id.startswith("AI-PROPOSAL-"):
                continue
            proposal_id = request_id.replace("AI-PROPOSAL-", "", 1)
            if proposal_id in executed_proposal_ids:
                ai_closed.append(t)

        count = len(ai_closed)
        if count == 0:
            return empty

        profits = []
        for t in ai_closed:
            entry = float(t.get("fill_price", 0.0))
            close = float(t.get("close_price", 0.0))
            direction = t.get("direction", "")
            size = float(t.get("position_size", t.get("units", 0)))

            if direction == "Long":
                pip_diff = close - entry
            elif direction == "Short":
                pip_diff = entry - close
            else:
                pip_diff = 0.0

            profits.append(pip_diff * size)

        profitable = sum(1 for p in profits if p > 0)
        total_profit = sum(profits)
        average_profit = total_profit / count
        accuracy_rate = (profitable / count) * 100
        best_trade = max(profits)
        worst_trade = min(profits)

        return {
            "executed_recommendations": count,
            "profitable_recommendations": profitable,
            "accuracy_rate": round(accuracy_rate, 1),
            "total_profit": round(total_profit, 4),
            "average_profit": round(average_profit, 4),
            "best_trade": round(best_trade, 4),
            "worst_trade": round(worst_trade, 4),
        }
