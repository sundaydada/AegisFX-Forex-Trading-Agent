"""Production signal adapter for the autonomous USD/CAD cycle.

UsdCadSignalProvider composes four injected collaborators — market-data
fetch, context builder, AI analysis, strategy recommendation — into the
three-field signal shape autonomy_usdcad_cycle.run_cycle consumes.

Both real network boundaries (Alpha Vantage and OpenAI) live inside the
injected collaborators, so this module reads no environment variable,
constructs no service, opens no database, and makes no network call. The
future wiring module binds the production defaults.

Importing this module defines one class and does nothing else.
"""


def _neutral_signal() -> dict:
    """Return a fresh non-tradeable signal."""
    return {
        "trade_bias": "NEUTRAL",
        "confidence": 0,
        "execution_allowed": False,
    }


class UsdCadSignalProvider:
    """Compose market analysis into the signal shape run_cycle consumes."""

    def __init__(
        self,
        *,
        fetch_intraday,
        build_context,
        analysis_service,
        recommend_strategy,
    ):
        self._fetch_intraday = fetch_intraday
        self._build_context = build_context
        self._analysis_service = analysis_service
        self._recommend_strategy = recommend_strategy

    def __call__(self, *, pair: str) -> dict:
        try:
            # The pair is threaded through unchanged; only the Alpha
            # Vantage feed splits it, inside the injected fetcher.
            market_data = self._fetch_intraday(
                pair,
                interval="5min",
                outputsize="compact",
            )
            candles = (
                market_data.get("candles")
                if isinstance(market_data, dict)
                else None
            )
            # Stop here on unusable market data: analysing an empty
            # series would spend the model call to learn nothing.
            if not candles:
                return _neutral_signal()

            context = self._build_context(pair, candles)
            market_context = {
                pair: context,
            }

            analysis = self._analysis_service.analyze_market_context(
                market_context
            )
            recommendation = self._recommend_strategy(
                analysis,
                market_context,
            )

            # confidence comes from the analysis: the recommendation
            # service consumes it for its rules but does not return it.
            return {
                "trade_bias": recommendation["trade_bias"],
                "confidence": analysis["confidence"],
                "execution_allowed": bool(
                    recommendation["execution_allowed"]
                ),
            }
        except Exception:
            return _neutral_signal()
