from typing import Dict


class StrategyRecommendationService:
    """
    Deterministic rule-based strategy recommender.
    Translates probabilistic AI analysis into a structured recommendation.
    Read-only — never calls broker, orchestrator, or risk modules.
    """

    @staticmethod
    def recommend_strategy(ai_analysis: Dict, market_context: Dict = None) -> Dict:
        """
        Map AI analysis to a deterministic strategy recommendation.

        Args:
            ai_analysis: Dict with regime, confidence, summary, pair_analysis
            market_context: Optional per-pair market context. When the AI returns
                "Ranging", the strategy rule consults each pair's
                position_in_range (UPPER/LOWER/MIDDLE) to decide directional bias.
                Schema:
                    {
                        "EUR/USD": {"position_in_range": "UPPER", ...},
                        "GBP/USD": {"position_in_range": "LOWER", ...},
                        ...
                    }
                If omitted, Ranging falls back to NEUTRAL (legacy behavior).

        Returns:
            {
                "recommended_strategy": str,
                "trade_bias": "LONG" | "SHORT" | "NEUTRAL",
                "risk_mode": "NORMAL" | "REDUCED" | "AVOID",
                "reason": str,
                "execution_allowed": bool,
                "per_pair_bias": Dict[str, str]  # only present for Ranging w/ context
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

        # Rule 4: Ranging regime — range-aware mean reversion
        if regime == "Ranging":
            per_pair_bias = {}
            upper_count = lower_count = middle_count = 0

            if market_context:
                for pair, ctx in market_context.items():
                    pos = ctx.get("position_in_range", "MIDDLE")
                    if pos == "UPPER":
                        per_pair_bias[pair] = "SHORT"
                        upper_count += 1
                    elif pos == "LOWER":
                        per_pair_bias[pair] = "LONG"
                        lower_count += 1
                    else:
                        per_pair_bias[pair] = "NEUTRAL"
                        middle_count += 1

            # Top-level bias: pick dominant directional signal across pairs.
            # When pairs disagree (some UPPER, some LOWER), top-level stays NEUTRAL
            # but per_pair_bias still carries the actionable per-pair signals.
            if upper_count > 0 and lower_count == 0:
                top_bias = "SHORT"
            elif lower_count > 0 and upper_count == 0:
                top_bias = "LONG"
            else:
                top_bias = "NEUTRAL"

            actionable = upper_count + lower_count
            recommendation.update({
                "recommended_strategy": "MeanReversion_v1",
                "trade_bias": top_bias,
                "risk_mode": "REDUCED",  # mean reversion always REDUCED — fading moves
                "reason": (
                    f"Ranging — {actionable} pair(s) at range extremes "
                    f"(upper={upper_count}, lower={lower_count}, mid={middle_count})"
                    if market_context else
                    f"Ranging market — no per-pair range context available"
                ),
                "execution_allowed": True,
                "per_pair_bias": per_pair_bias,
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
