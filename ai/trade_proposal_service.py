from typing import Dict, List


class TradeProposalService:
    """
    Deterministic trade proposal generator.
    Combines AI analysis with strategy recommendation to produce
    structured trade suggestions for the operator to review.

    Read-only — never calls broker, orchestrator, or risk modules.
    """

    @staticmethod
    def generate_trade_proposals(
        ai_analysis: Dict,
        strategy_recommendation: Dict,
    ) -> List[Dict]:
        """
        Produce a list of trade proposals.

        Returns:
            List of proposal dicts. Empty list if execution not allowed
            or trade bias is neutral.
        """

        execution_allowed = bool(strategy_recommendation.get("execution_allowed", False))
        trade_bias = strategy_recommendation.get("trade_bias", "NEUTRAL")
        risk_mode = strategy_recommendation.get("risk_mode", "AVOID")
        strategy_name = strategy_recommendation.get("recommended_strategy", "None")
        recommendation_reason = strategy_recommendation.get("reason", "")

        # Block proposals when execution not allowed or risk mode says avoid
        if not execution_allowed or risk_mode == "AVOID":
            return []

        # Neutral bias — no proposals
        if trade_bias == "NEUTRAL":
            return []

        # Position sizing by risk mode
        if risk_mode == "NORMAL":
            suggested_size = 1.0
        elif risk_mode == "REDUCED":
            suggested_size = 0.5
        else:
            return []

        try:
            confidence = int(ai_analysis.get("confidence", 0))
        except (ValueError, TypeError):
            confidence = 0

        pair_analysis = ai_analysis.get("pair_analysis", {})
        if not isinstance(pair_analysis, dict) or not pair_analysis:
            return []

        proposals = []
        for pair, note in pair_analysis.items():
            proposals.append({
                "pair": pair,
                "direction": trade_bias,
                "suggested_size": suggested_size,
                "confidence": confidence,
                "strategy": strategy_name,
                "reason": f"{recommendation_reason} | {note}" if note else recommendation_reason,
                "execution_allowed": execution_allowed,
            })

        return proposals
