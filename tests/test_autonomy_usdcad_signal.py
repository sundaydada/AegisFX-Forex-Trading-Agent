"""Cycle-compatibility contract for the USD/CAD signal adapter.

The adapter composes four injected collaborators — market-data fetch,
context builder, AI analysis, strategy recommendation — and returns the
exact three fields autonomy_usdcad_cycle.run_cycle consumes. Both real
network boundaries (Alpha Vantage, OpenAI) are injected, so this test is
entirely offline: no HTTP, no environment variable, no database.

The adapter module does not exist yet; it is imported inside the test so
collection stays clean either way.
"""

import importlib
import inspect

import pytest


CANDLES = [
    {
        "timestamp": "2026-07-27T11:50:00Z",
        "open": 1.40780,
        "high": 1.40830,
        "low": 1.40750,
        "close": 1.40810,
    },
    {
        "timestamp": "2026-07-27T11:55:00Z",
        "open": 1.40810,
        "high": 1.40870,
        "low": 1.40790,
        "close": 1.40850,
    },
    {
        "timestamp": "2026-07-27T12:00:00Z",
        "open": 1.40850,
        "high": 1.40900,
        "low": 1.40820,
        "close": 1.40880,
    },
]

MARKET_CONTEXT = {
    "price": 1.40880,
    "trend": "up",
    "volatility": "low",
    "range_percentile": 15.0,
    "position_in_range": "LOWER",
}

ANALYSIS = {
    "regime": "Trending",
    "summary": "USD/CAD momentum is positive.",
    "confidence": 85,
    "pair_analysis": {
        "USD/CAD": "Bullish momentum.",
    },
}

RECOMMENDATION = {
    "recommended_strategy": "Momentum_v1",
    "trade_bias": "LONG",
    "risk_mode": "NORMAL",
    "reason": "Trending regime with strong confidence.",
    "execution_allowed": True,
}

OPERATIONAL_ATTRIBUTES = (
    "add_proposals",
    "approve_proposal",
    "get_approved_proposals",
    "place_order",
    "close_position",
    "executor",
)


class _RecordingFetcher:
    """Stands in for market_data.alpha_vantage_price_feed.get_fx_intraday."""

    def __init__(self, result, call_order):
        self._result = result
        self._call_order = call_order
        self.calls = []
        self.returned = []

    def __call__(self, pair, interval="5min", outputsize="compact"):
        self.calls.append(
            {
                "pair": pair,
                "interval": interval,
                "outputsize": outputsize,
            }
        )
        self._call_order.append("fetch_intraday")
        self.returned.append(self._result)
        return self._result


class _RecordingContextBuilder:
    """Stands in for market_data.market_context.build_market_context."""

    def __init__(self, result, call_order):
        self._result = result
        self._call_order = call_order
        self.calls = []

    def __call__(self, pair, candles):
        self.calls.append({"pair": pair, "candles": candles})
        self._call_order.append("build_context")
        return self._result


class _RecordingAnalysisService:
    """Stands in for MarketAnalysisService (the OpenAI boundary)."""

    def __init__(self, result, call_order):
        self._result = result
        self._call_order = call_order
        self.calls = []

    def analyze_market_context(self, market_data):
        self.calls.append(market_data)
        self._call_order.append("analyze")
        return self._result


class _RecordingRecommender:
    """Stands in for StrategyRecommendationService.recommend_strategy."""

    def __init__(self, result, call_order):
        self._result = result
        self._call_order = call_order
        self.calls = []

    def __call__(self, ai_analysis, market_context=None):
        self.calls.append(
            {
                "ai_analysis": ai_analysis,
                "market_context": market_context,
            }
        )
        self._call_order.append("recommend")
        return self._result


class _RaisingFetcher:
    """A market-data fetch that records its call, then fails."""

    def __init__(self, call_order):
        self._call_order = call_order
        self.calls = []

    def __call__(self, pair, interval="5min", outputsize="compact"):
        self.calls.append(
            {
                "pair": pair,
                "interval": interval,
                "outputsize": outputsize,
            }
        )
        self._call_order.append("fetch_intraday")
        raise RuntimeError("fetch failed")


class _RaisingContextBuilder:
    """A context builder that records its call, then fails."""

    def __init__(self, call_order):
        self._call_order = call_order
        self.calls = []

    def __call__(self, pair, candles):
        self.calls.append({"pair": pair, "candles": candles})
        self._call_order.append("build_context")
        raise RuntimeError("context failed")


class _RaisingAnalysisService:
    """An analysis service that records its call, then fails."""

    def __init__(self, call_order):
        self._call_order = call_order
        self.calls = []

    def analyze_market_context(self, market_data):
        self.calls.append(market_data)
        self._call_order.append("analyze")
        raise RuntimeError("analysis failed")


class _RaisingRecommender:
    """A recommendation callable that records its call, then fails."""

    def __init__(self, call_order):
        self._call_order = call_order
        self.calls = []

    def __call__(self, ai_analysis, market_context=None):
        self.calls.append(
            {
                "ai_analysis": ai_analysis,
                "market_context": market_context,
            }
        )
        self._call_order.append("recommend")
        raise RuntimeError("recommendation failed")


def test_signal_provider_returns_cycle_compatible_usdcad_signal():
    signal_module = importlib.import_module("autonomy_usdcad_signal")
    provider_class = signal_module.UsdCadSignalProvider

    call_order = []
    fetch_intraday = _RecordingFetcher(
        {"currency_pair": "USD/CAD", "candles": CANDLES},
        call_order,
    )
    build_context = _RecordingContextBuilder(MARKET_CONTEXT, call_order)
    analysis_service = _RecordingAnalysisService(ANALYSIS, call_order)
    recommend_strategy = _RecordingRecommender(RECOMMENDATION, call_order)

    provider = provider_class(
        fetch_intraday=fetch_intraday,
        build_context=build_context,
        analysis_service=analysis_service,
        recommend_strategy=recommend_strategy,
    )

    result = provider(pair="USD/CAD")

    # --- exact collaborator order, each exactly once ---
    assert call_order == [
        "fetch_intraday",
        "build_context",
        "analyze",
        "recommend",
    ], f"got call order {call_order!r}"
    assert len(fetch_intraday.calls) == 1, (
        f"got {len(fetch_intraday.calls)} fetch call(s)"
    )
    assert len(build_context.calls) == 1, (
        f"got {len(build_context.calls)} context call(s)"
    )
    assert len(analysis_service.calls) == 1, (
        f"got {len(analysis_service.calls)} analysis call(s)"
    )
    assert len(recommend_strategy.calls) == 1, (
        f"got {len(recommend_strategy.calls)} recommendation call(s)"
    )

    # --- market-data fetch ---
    assert fetch_intraday.calls[0] == {
        "pair": "USD/CAD",
        "interval": "5min",
        "outputsize": "compact",
    }, f"got fetch call {fetch_intraday.calls[0]!r}"

    # --- context builder receives the pair and the fetched candles ---
    context_call = build_context.calls[0]
    assert context_call["pair"] == "USD/CAD", (
        f"got context pair {context_call['pair']!r}"
    )
    assert context_call["candles"] == fetch_intraday.returned[0]["candles"], (
        "the context builder must receive the fetched candle list"
    )
    assert context_call["candles"] == CANDLES

    # --- analysis receives exactly the pair-keyed context mapping ---
    analysis_argument = analysis_service.calls[0]
    assert analysis_argument == {"USD/CAD": MARKET_CONTEXT}, (
        f"got analysis argument {analysis_argument!r}"
    )

    # --- recommendation receives the exact analysis object ---
    recommend_call = recommend_strategy.calls[0]
    assert recommend_call["ai_analysis"] is ANALYSIS, (
        "the recommender must receive the exact analysis object returned"
        " by the analysis service"
    )
    assert recommend_call["market_context"] == {"USD/CAD": MARKET_CONTEXT}, (
        f"got market_context {recommend_call['market_context']!r}"
    )

    # --- the returned signal is exactly what run_cycle consumes ---
    assert isinstance(result, dict), (
        f"the provider must return a mapping; got {type(result)!r}"
    )
    assert set(result) == {
        "trade_bias",
        "confidence",
        "execution_allowed",
    }, f"got keys {sorted(result)!r}"
    assert result == {
        "trade_bias": "LONG",
        "confidence": 85,
        "execution_allowed": True,
    }, f"got signal {result!r}"
    assert result["trade_bias"] == RECOMMENDATION["trade_bias"], (
        "trade_bias must come from the recommendation"
    )
    assert result["confidence"] == ANALYSIS["confidence"], (
        "confidence must come from the AI analysis, which the"
        " recommendation does not return"
    )
    assert result["execution_allowed"] is True, (
        f"got execution_allowed {result['execution_allowed']!r}"
    )

    # --- the provider exposes exactly one keyword-only pair argument ---
    signature = inspect.signature(provider)
    parameters = list(signature.parameters.values())
    assert len(parameters) == 1, (
        f"the provider must accept exactly one argument;"
        f" got {[p.name for p in parameters]!r}"
    )
    assert parameters[0].name == "pair", (
        f"got parameter {parameters[0].name!r}"
    )
    assert parameters[0].kind is inspect.Parameter.KEYWORD_ONLY, (
        f"pair must be keyword-only; got {parameters[0].kind!r}"
    )

    # --- the adapter owns no proposal, broker or execution collaborator ---
    for attribute in OPERATIONAL_ATTRIBUTES:
        assert not hasattr(provider, attribute), (
            "the signal adapter must not expose the operational"
            f" attribute {attribute!r}"
        )


@pytest.mark.parametrize(
    "case_name, fetch_result",
    [
        (
            "empty_candles",
            {
                "currency_pair": "USD/CAD",
                "candles": [],
            },
        ),
        (
            "missing_candles_key",
            {
                "currency_pair": "USD/CAD",
                "error": "Market data unavailable.",
            },
        ),
    ],
)
def test_signal_provider_fails_closed_when_candles_are_unavailable(
    case_name, fetch_result
):
    """Unusable market data must stop the chain at the fetch boundary.

    The downstream collaborators are wired with the same valid values the
    success path uses, so reaching a neutral signal proves the adapter
    declined to continue rather than merely lacking a working analysis.
    """

    signal_module = importlib.import_module("autonomy_usdcad_signal")
    provider_class = signal_module.UsdCadSignalProvider

    call_order = []
    fetch_intraday = _RecordingFetcher(fetch_result, call_order)
    build_context = _RecordingContextBuilder(MARKET_CONTEXT, call_order)
    analysis_service = _RecordingAnalysisService(ANALYSIS, call_order)
    recommend_strategy = _RecordingRecommender(
        RECOMMENDATION,
        call_order,
    )

    provider = provider_class(
        fetch_intraday=fetch_intraday,
        build_context=build_context,
        analysis_service=analysis_service,
        recommend_strategy=recommend_strategy,
    )

    result = provider(pair="USD/CAD")

    # --- the exact fail-closed signal ---
    assert isinstance(result, dict), (
        f"[{case_name}] the provider must return a mapping;"
        f" got {type(result)!r}"
    )
    assert set(result) == {
        "trade_bias",
        "confidence",
        "execution_allowed",
    }, f"[{case_name}] got keys {sorted(result)!r}"
    assert result == {
        "trade_bias": "NEUTRAL",
        "confidence": 0,
        "execution_allowed": False,
    }, f"[{case_name}] got signal {result!r}"
    assert result["trade_bias"] == "NEUTRAL", (
        f"[{case_name}] got trade_bias {result['trade_bias']!r}"
    )
    assert result["confidence"] == 0, (
        f"[{case_name}] got confidence {result['confidence']!r}"
    )
    assert result["execution_allowed"] is False, (
        f"[{case_name}] got execution_allowed"
        f" {result['execution_allowed']!r}"
    )

    # --- the fetch still happened, exactly once, unchanged ---
    assert len(fetch_intraday.calls) == 1, (
        f"[{case_name}] got {len(fetch_intraday.calls)} fetch call(s)"
    )
    assert fetch_intraday.calls[0] == {
        "pair": "USD/CAD",
        "interval": "5min",
        "outputsize": "compact",
    }, f"[{case_name}] got fetch call {fetch_intraday.calls[0]!r}"

    # --- nothing downstream of the fetch ran ---
    assert call_order == ["fetch_intraday"], (
        f"[{case_name}] got call order {call_order!r}"
    )
    assert build_context.calls == [], (
        f"[{case_name}] the context builder must not be called when"
        f" candles are unavailable; got {build_context.calls!r}"
    )
    assert analysis_service.calls == [], (
        f"[{case_name}] the analysis service must not be called when"
        f" candles are unavailable; got {analysis_service.calls!r}"
    )
    assert recommend_strategy.calls == [], (
        f"[{case_name}] the recommendation callable must not be called"
        f" when candles are unavailable; got {recommend_strategy.calls!r}"
    )


# Stated literally rather than derived from the expected call order, so
# multiplicity is checked independently of sequence.
EXPECTED_CALL_COUNTS = {
    "fetch_exception": {
        "fetch_intraday": 1,
        "build_context": 0,
        "analyze": 0,
        "recommend": 0,
    },
    "context_exception": {
        "fetch_intraday": 1,
        "build_context": 1,
        "analyze": 0,
        "recommend": 0,
    },
    "analysis_exception": {
        "fetch_intraday": 1,
        "build_context": 1,
        "analyze": 1,
        "recommend": 0,
    },
    "recommendation_exception": {
        "fetch_intraday": 1,
        "build_context": 1,
        "analyze": 1,
        "recommend": 1,
    },
}


@pytest.mark.parametrize(
    "case_name, expected_call_order",
    [
        (
            "fetch_exception",
            ["fetch_intraday"],
        ),
        (
            "context_exception",
            ["fetch_intraday", "build_context"],
        ),
        (
            "analysis_exception",
            ["fetch_intraday", "build_context", "analyze"],
        ),
        (
            "recommendation_exception",
            [
                "fetch_intraday",
                "build_context",
                "analyze",
                "recommend",
            ],
        ),
    ],
)
def test_signal_provider_fails_closed_when_a_collaborator_raises(
    case_name, expected_call_order
):
    """A raising collaborator must degrade to a neutral signal.

    Exactly one collaborator in an otherwise working chain is swapped for
    a raising one, so the neutral result can only come from the injected
    failure. The raising fakes record before they raise, proving the
    failing boundary was genuinely entered.
    """

    signal_module = importlib.import_module("autonomy_usdcad_signal")
    provider_class = signal_module.UsdCadSignalProvider

    call_order = []
    fetch_intraday = _RecordingFetcher(
        {
            "currency_pair": "USD/CAD",
            "candles": CANDLES,
        },
        call_order,
    )
    build_context = _RecordingContextBuilder(
        MARKET_CONTEXT,
        call_order,
    )
    analysis_service = _RecordingAnalysisService(
        ANALYSIS,
        call_order,
    )
    recommend_strategy = _RecordingRecommender(
        RECOMMENDATION,
        call_order,
    )

    if case_name == "fetch_exception":
        fetch_intraday = _RaisingFetcher(call_order)
    elif case_name == "context_exception":
        build_context = _RaisingContextBuilder(call_order)
    elif case_name == "analysis_exception":
        analysis_service = _RaisingAnalysisService(call_order)
    elif case_name == "recommendation_exception":
        recommend_strategy = _RaisingRecommender(call_order)
    else:
        raise AssertionError(f"unhandled failure boundary {case_name!r}")

    provider = provider_class(
        fetch_intraday=fetch_intraday,
        build_context=build_context,
        analysis_service=analysis_service,
        recommend_strategy=recommend_strategy,
    )

    result = provider(pair="USD/CAD")

    # --- the exact fail-closed signal ---
    assert isinstance(result, dict), (
        f"[{case_name}] the provider must return a mapping;"
        f" got {type(result)!r}"
    )
    assert set(result) == {
        "trade_bias",
        "confidence",
        "execution_allowed",
    }, f"[{case_name}] got keys {sorted(result)!r}"
    assert result == {
        "trade_bias": "NEUTRAL",
        "confidence": 0,
        "execution_allowed": False,
    }, f"[{case_name}] got signal {result!r}"
    assert result["trade_bias"] == "NEUTRAL", (
        f"[{case_name}] got trade_bias {result['trade_bias']!r}"
    )
    assert result["confidence"] == 0, (
        f"[{case_name}] got confidence {result['confidence']!r}"
    )
    assert result["execution_allowed"] is False, (
        f"[{case_name}] got execution_allowed"
        f" {result['execution_allowed']!r}"
    )

    # --- the chain stopped at the failing boundary, with no retry ---
    assert call_order == expected_call_order, (
        f"[{case_name}] expected call order {expected_call_order!r};"
        f" got {call_order!r}"
    )

    expected_counts = EXPECTED_CALL_COUNTS[case_name]
    actual_counts = {
        "fetch_intraday": len(fetch_intraday.calls),
        "build_context": len(build_context.calls),
        "analyze": len(analysis_service.calls),
        "recommend": len(recommend_strategy.calls),
    }
    assert actual_counts == expected_counts, (
        f"[{case_name}] expected call counts {expected_counts!r};"
        f" got {actual_counts!r}"
    )
