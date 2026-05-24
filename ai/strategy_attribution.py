from typing import Dict, List


class StrategyAttributionAnalytics:
    """
    Read-only attribution of executed AI trades by (regime, strategy).
    Measures performance — does not adjust strategy logic.
    """

    @staticmethod
    def compute_strategy_attribution(
        proposal_queue,
        trade_state_manager,
        ai_analysis_history,
    ) -> Dict:
        """
        Group CLOSED AI-executed trades by (regime, strategy) and compute KPIs.

        Args:
            proposal_queue: ProposalApprovalQueue instance
            trade_state_manager: PersistentTradeStateManager instance
            ai_analysis_history: AIAnalysisHistoryManager instance

        Returns:
            {
                "Trending": {
                    "Momentum_v1": {
                        "trade_count": int,
                        "win_rate": float,
                        "total_profit": float,
                        "average_profit": float
                    },
                    ...
                },
                ...
            }
        """

        # --- 1. Pull all decided/executed proposals so we know their strategy ---
        try:
            decisions = proposal_queue.get_recent_decisions(limit=100_000)
        except Exception:
            decisions = []

        # Map proposal_id -> strategy
        proposal_strategy = {
            d["proposal_id"]: d.get("strategy", "Unknown")
            for d in decisions
            if d.get("status") == "EXECUTED"
        }

        if not proposal_strategy:
            return {}

        # --- 2. Pull CLOSED AI-executed trades ---
        all_trades = trade_state_manager.get_all_trades()
        ai_closed = [
            t for t in all_trades
            if t.get("status") == "CLOSED"
            and str(t.get("request_id", "")).startswith("AI-PROPOSAL-")
        ]

        if not ai_closed:
            return {}

        # --- 3. Pull AI history sorted by timestamp for regime lookup ---
        try:
            history = ai_analysis_history.get_recent_analysis(limit=100_000)
        except Exception:
            history = []

        # history newest first — invert for chronological scan
        history_sorted = sorted(history, key=lambda h: h.get("timestamp", ""))

        def regime_at(timestamp: str) -> str:
            """Find the regime that was active at or just before `timestamp`."""
            if not timestamp:
                return "Unknown"
            last_match = "Unknown"
            for h in history_sorted:
                ts = h.get("timestamp", "")
                if ts and ts <= timestamp:
                    last_match = h.get("regime", "Unknown")
                else:
                    break
            return last_match

        # --- 4. Bucket trades by (regime, strategy) ---
        buckets: Dict[str, Dict[str, List[Dict]]] = {}

        for trade in ai_closed:
            request_id = trade.get("request_id", "")
            proposal_id = request_id.replace("AI-PROPOSAL-", "", 1)

            strategy = proposal_strategy.get(proposal_id, "Unknown")
            created_at = trade.get("created_at", "")
            regime = regime_at(created_at)

            buckets.setdefault(regime, {}).setdefault(strategy, []).append(trade)

        # --- 5. Compute KPIs per (regime, strategy) ---
        result: Dict[str, Dict[str, Dict]] = {}

        for regime, strategies in buckets.items():
            result[regime] = {}
            for strategy, trades in strategies.items():
                total_profit = 0.0
                wins = 0

                for t in trades:
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

                    trade_profit = pip_diff * size
                    total_profit += trade_profit
                    if trade_profit > 0:
                        wins += 1

                count = len(trades)
                win_rate = (wins / count * 100) if count > 0 else 0.0
                avg_profit = (total_profit / count) if count > 0 else 0.0

                result[regime][strategy] = {
                    "trade_count": count,
                    "win_rate": round(win_rate, 1),
                    "total_profit": round(total_profit, 4),
                    "average_profit": round(avg_profit, 4),
                }

        return result
