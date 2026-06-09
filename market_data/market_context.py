from typing import Dict, List


def compute_trend(candles: List[Dict]) -> str:
    """
    Determine trend from candle closes.
    Compares last close to N-period simple moving average.

    Returns: "up", "down", or "flat"
    """
    if len(candles) < 5:
        return "flat"

    closes = [c["close"] for c in candles if c.get("close", 0) > 0]
    if len(closes) < 5:
        return "flat"

    recent_avg = sum(closes[-5:]) / 5
    older_avg = sum(closes[-20:-5]) / 15 if len(closes) >= 20 else sum(closes[:-5]) / max(1, len(closes) - 5)

    if older_avg == 0:
        return "flat"

    pct_change = (recent_avg - older_avg) / older_avg * 100

    if pct_change > 0.05:
        return "up"
    if pct_change < -0.05:
        return "down"
    return "flat"


def compute_volatility(candles: List[Dict]) -> str:
    """
    Classify volatility from average true range (ATR-like) over recent candles.
    Returns: "low", "medium", or "high"
    """
    if len(candles) < 5:
        return "low"

    recent = candles[-20:] if len(candles) >= 20 else candles

    ranges = []
    for c in recent:
        high = c.get("high", 0)
        low = c.get("low", 0)
        if high > 0 and low > 0:
            ranges.append(high - low)

    if not ranges:
        return "low"

    avg_range = sum(ranges) / len(ranges)
    avg_price = sum(c["close"] for c in recent if c.get("close", 0) > 0) / max(1, len(recent))

    if avg_price == 0:
        return "low"

    # Range as a percentage of price
    range_pct = (avg_range / avg_price) * 100

    if range_pct > 0.15:
        return "high"
    if range_pct > 0.05:
        return "medium"
    return "low"


def compute_range_percentile(candles: List[Dict], lookback: int = 20) -> float:
    """
    Compute current close as a percentile within the rolling N-period high/low range.

    Returns: 0.0 to 100.0 (0 = at low, 100 = at high). Returns 50.0 if range is flat.
    """
    if len(candles) < lookback:
        return 50.0

    window = candles[-lookback:]
    highs = [c.get("high", 0) for c in window if c.get("high", 0) > 0]
    lows = [c.get("low", 0) for c in window if c.get("low", 0) > 0]

    if not highs or not lows:
        return 50.0

    high_n = max(highs)
    low_n = min(lows)
    current = candles[-1].get("close", 0.0)

    if high_n <= low_n:
        return 50.0

    pct = (current - low_n) / (high_n - low_n) * 100
    return max(0.0, min(100.0, pct))


def classify_position_in_range(range_percentile: float) -> str:
    """
    Bucket a range percentile into a discrete position label.

    UPPER  : >= 80% (top of range — mean-reversion SHORT candidate)
    LOWER  : <= 20% (bottom of range — mean-reversion LONG candidate)
    MIDDLE : everything else
    """
    if range_percentile >= 80.0:
        return "UPPER"
    if range_percentile <= 20.0:
        return "LOWER"
    return "MIDDLE"


def build_market_context(pair: str, candles: List[Dict]) -> Dict:
    """
    Build a market context dict for a pair using recent candles.

    Returns:
        {
            "price": float (latest close),
            "trend": "up" | "down" | "flat",
            "volatility": "low" | "medium" | "high",
            "range_percentile": float (0-100),
            "position_in_range": "UPPER" | "MIDDLE" | "LOWER"
        }
    """
    if not candles:
        return {
            "price": 0.0,
            "trend": "flat",
            "volatility": "low",
            "range_percentile": 50.0,
            "position_in_range": "MIDDLE",
        }

    latest_close = candles[-1].get("close", 0.0)
    range_pct = compute_range_percentile(candles)

    return {
        "price": latest_close,
        "trend": compute_trend(candles),
        "volatility": compute_volatility(candles),
        "range_percentile": round(range_pct, 1),
        "position_in_range": classify_position_in_range(range_pct),
    }
