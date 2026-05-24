import os
import urllib.request
import json
from typing import Dict, List


ALPHA_VANTAGE_BASE_URL = "https://www.alphavantage.co/query"


def get_fx_intraday(pair: str, interval: str = "5min", outputsize: str = "compact") -> Dict:
    """
    Fetch recent intraday FX candles from Alpha Vantage.

    Args:
        pair: Currency pair in format "EUR/USD"
        interval: One of "1min", "5min", "15min", "30min", "60min"
        outputsize: "compact" (last 100 candles) or "full"

    Returns:
        {
            "currency_pair": str,
            "candles": [
                {"timestamp": str, "open": float, "high": float, "low": float, "close": float},
                ...  # oldest first
            ]
        }
        On failure, returns {"currency_pair": pair, "candles": [], "error": str}
    """

    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY")
    if not api_key:
        return {"currency_pair": pair, "candles": [], "error": "ALPHA_VANTAGE_API_KEY not set"}

    parts = pair.split("/")
    if len(parts) != 2:
        return {"currency_pair": pair, "candles": [], "error": f"Invalid pair: {pair}"}

    from_currency, to_currency = parts[0].strip(), parts[1].strip()

    url = (
        f"{ALPHA_VANTAGE_BASE_URL}"
        f"?function=FX_INTRADAY"
        f"&from_symbol={from_currency}"
        f"&to_symbol={to_currency}"
        f"&interval={interval}"
        f"&outputsize={outputsize}"
        f"&apikey={api_key}"
    )

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as e:
        return {"currency_pair": pair, "candles": [], "error": f"Request failed: {str(e)}"}

    series_key = f"Time Series FX ({interval})"
    series = data.get(series_key)
    if not series:
        msg = data.get("Note") or data.get("Error Message") or "Missing time series"
        return {"currency_pair": pair, "candles": [], "error": str(msg)}

    candles = []
    for ts, ohlc in series.items():
        try:
            candles.append({
                "timestamp": ts,
                "open": float(ohlc.get("1. open", 0)),
                "high": float(ohlc.get("2. high", 0)),
                "low": float(ohlc.get("3. low", 0)),
                "close": float(ohlc.get("4. close", 0)),
            })
        except (ValueError, TypeError):
            continue

    # Alpha Vantage returns newest-first; reverse to oldest-first
    candles.sort(key=lambda c: c["timestamp"])

    return {"currency_pair": pair, "candles": candles}


def get_fx_price(pair: str) -> Dict:
    """
    Fetch realtime FX price from Alpha Vantage.

    Args:
        pair: Currency pair in format "EUR/USD"

    Returns:
        {
            "currency_pair": str,
            "price": float,
            "timestamp": str
        }
    """

    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY")
    if not api_key:
        return {
            "currency_pair": pair,
            "price": 0.0,
            "timestamp": "",
            "error": "ALPHA_VANTAGE_API_KEY environment variable not set",
        }

    parts = pair.split("/")
    if len(parts) != 2:
        return {
            "currency_pair": pair,
            "price": 0.0,
            "timestamp": "",
            "error": f"Invalid pair format: {pair}. Expected format: EUR/USD",
        }

    from_currency, to_currency = parts[0].strip(), parts[1].strip()

    url = (
        f"{ALPHA_VANTAGE_BASE_URL}"
        f"?function=CURRENCY_EXCHANGE_RATE"
        f"&from_currency={from_currency}"
        f"&to_currency={to_currency}"
        f"&apikey={api_key}"
    )

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as e:
        return {
            "currency_pair": pair,
            "price": 0.0,
            "timestamp": "",
            "error": f"API request failed: {str(e)}",
        }

    rate_data = data.get("Realtime Currency Exchange Rate")
    if not rate_data:
        error_message = data.get("Note", data.get("Error Message", "Unknown API error"))
        return {
            "currency_pair": pair,
            "price": 0.0,
            "timestamp": "",
            "error": f"API response error: {error_message}",
        }

    try:
        price = float(rate_data.get("5. Exchange Rate", 0.0))
        timestamp = rate_data.get("6. Last Refreshed", "")
    except (ValueError, TypeError) as e:
        return {
            "currency_pair": pair,
            "price": 0.0,
            "timestamp": "",
            "error": f"Failed to parse price data: {str(e)}",
        }

    return {
        "currency_pair": pair,
        "price": price,
        "timestamp": timestamp,
    }
