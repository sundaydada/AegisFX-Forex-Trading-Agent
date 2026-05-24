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


def build_market_context(pair: str, candles: List[Dict]) -> Dict:
    """
    Build a market context dict for a pair using recent candles.

    Returns:
        {
            "price": float (latest close),
            "trend": "up" | "down" | "flat",
            "volatility": "low" | "medium" | "high"
        }
    """
    if not candles:
        return {"price": 0.0, "trend": "flat", "volatility": "low"}

    latest_close = candles[-1].get("close", 0.0)

    return {
        "price": latest_close,
        "trend": compute_trend(candles),
        "volatility": compute_volatility(candles),
    }
