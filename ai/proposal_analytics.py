from typing import Dict


class ProposalAnalytics:
    """
    Read-only analytics on AI proposal lifecycle:
    generation, approval, execution, and profitability outcomes.
    """

    @staticmethod
    def compute_proposal_metrics(proposal_queue, trade_state_manager) -> Dict:
        """
        Compute end-to-end proposal analytics.

        Args:
            proposal_queue: ProposalApprovalQueue instance
            trade_state_manager: PersistentTradeStateManager instance

        Returns:
            Metrics dict with totals, conversion rates, and profitability.
        """

        # --- Pull queue data ---
        try:
            pending = proposal_queue.get_pending_proposals()
        except Exception:
            pending = []

        try:
            approved = proposal_queue.get_approved_proposals()
        except Exception:
            approved = []

        try:
            decisions = proposal_queue.get_recent_decisions(limit=10_000)
        except Exception:
            decisions = []

        # Bucket by status
        rejected_count = sum(1 for d in decisions if d.get("status") == "REJECTED")
        approved_count = sum(1 for d in decisions if d.get("status") == "APPROVED") + len(approved)
        executed_count = sum(1 for d in decisions if d.get("status") == "EXECUTED")

        total_proposals = len(pending) + rejected_count + approved_count + executed_count

        approval_rate = 0.0
        if total_proposals > 0:
            decided_total = approved_count + rejected_count + executed_count
            if decided_total > 0:
                approval_rate = round(
                    (approved_count + executed_count) / decided_total * 100, 1
                )

        execution_rate = 0.0
        if (approved_count + executed_count) > 0:
            execution_rate = round(
                executed_count / (approved_count + executed_count) * 100, 1
            )

        # --- Profitability from CLOSED trades linked by request_id ---
        all_trades = trade_state_manager.get_all_trades()

        ai_closed = [
            t for t in all_trades
            if t.get("status") == "CLOSED"
            and str(t.get("request_id", "")).startswith("AI-PROPOSAL-")
        ]

        wins = 0
        total_profit = 0.0
        trade_count = len(ai_closed)

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

            trade_profit = pip_diff * size
            total_profit += trade_profit
            if trade_profit > 0:
                wins += 1

        executed_win_rate = round((wins / trade_count * 100), 1) if trade_count > 0 else 0.0
        average_profit = round((total_profit / trade_count), 4) if trade_count > 0 else 0.0

        return {
            "total_proposals": total_proposals,
            "approved_proposals": approved_count,
            "rejected_proposals": rejected_count,
            "executed_proposals": executed_count,
            "approval_rate": approval_rate,
            "execution_rate": execution_rate,
            "executed_win_rate": executed_win_rate,
            "average_profit": average_profit,
            "total_realized_profit": round(total_profit, 4),
        }
