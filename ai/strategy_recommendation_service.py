from typing import Dict


class StrategyRecommendationService:
    """
    Deterministic rule-based strategy recommender.
    Translates probabilistic AI analysis into a structured recommendation.
    Read-only — never calls broker, orchestrator, or risk modules.
    """

    @staticmethod
    def recommend_strategy(ai_analysis: Dict) -> Dict:
        """
        Map AI analysis to a deterministic strategy recommendation.

        Args:
            ai_analysis: Dict with regime, confidence, summary, pair_analysis

        Returns:
            {
                "recommended_strategy": str,
                "trade_bias": "LONG" | "SHORT" | "NEUTRAL",
                "risk_mode": "NORMAL" | "REDUCED" | "AVOID",
                "reason": str,
                "execution_allowed": bool
            }
        """

        regime = ai_analysis.get("regime", "UNKNOWN")
        try:
            confidence = int(ai_analysis.get("confidence", 0))
        except (ValueError, TypeError):
            confidence = 0

        # Default neutral / blocked state
        recommendation = {
            "recommended_strategy": "None",
            "trade_bias": "NEUTRAL",
            "risk_mode": "AVOID",
            "reason": "Default safe state",
            "execution_allowed": False,
        }

        # Rule 1: Low confidence — block trading regardless of regime
        if confidence < 40:
            recommendation.update({
                "recommended_strategy": "None",
                "trade_bias": "NEUTRAL",
                "risk_mode": "AVOID",
                "reason": f"Confidence too low ({confidence}%) — execution blocked",
                "execution_allowed": False,
            })
            return recommendation

        # Rule 2: Unknown regime — block
        if regime == "UNKNOWN":
            recommendation.update({
                "recommended_strategy": "None",
                "trade_bias": "NEUTRAL",
                "risk_mode": "AVOID",
                "reason": "Regime unknown — AI analysis unavailable",
                "execution_allowed": False,
            })
            return recommendation

        # Rule 3: Trending regime — momentum strategy
        if regime == "Trending":
            if confidence >= 70:
                recommendation.update({
                    "recommended_strategy": "Momentum_v1",
                    "trade_bias": "LONG",
                    "risk_mode": "NORMAL",
                    "reason": f"Strong trending regime with {confidence}% confidence",
                    "execution_allowed": True,
                })
            else:
                recommendation.update({
                    "recommended_strategy": "Momentum_v1",
                    "trade_bias": "LONG",
                    "risk_mode": "REDUCED",
                    "reason": f"Trending but moderate confidence ({confidence}%)",
                    "execution_allowed": True,
                })
            return recommendation

        # Rule 4: Ranging regime — mean reversion
        if regime == "Ranging":
            recommendation.update({
                "recommended_strategy": "MeanReversion_v1",
                "trade_bias": "NEUTRAL",
                "risk_mode": "NORMAL" if confidence >= 60 else "REDUCED",
                "reason": f"Ranging market — mean reversion applicable ({confidence}%)",
                "execution_allowed": True,
            })
            return recommendation

        # Rule 5: Volatile regime — defensive
        if regime == "Volatile":
            recommendation.update({
                "recommended_strategy": "Volatility_Defense",
                "trade_bias": "NEUTRAL",
                "risk_mode": "REDUCED",
                "reason": f"Volatile regime — reduce exposure ({confidence}%)",
                "execution_allowed": True,
            })
            return recommendation

        # Rule 6: Risk-Off — flight to safety
        if regime == "Risk-Off":
            recommendation.update({
                "recommended_strategy": "SafeHaven_v1",
                "trade_bias": "SHORT",
                "risk_mode": "REDUCED",
                "reason": f"Risk-off environment ({confidence}%)",
                "execution_allowed": True,
            })
            return recommendation

        # Rule 7: Risk-On
        if regime == "Risk-On":
            recommendation.update({
                "recommended_strategy": "RiskOn_v1",
                "trade_bias": "LONG",
                "risk_mode": "NORMAL" if confidence >= 60 else "REDUCED",
                "reason": f"Risk-on environment ({confidence}%)",
                "execution_allowed": True,
            })
            return recommendation

        # Fallback — unrecognized regime
        recommendation.update({
            "reason": f"Unrecognized regime: {regime}",
        })
        return recommendation
