import os
import urllib.request
import json
from typing import Dict


ALPHA_VANTAGE_BASE_URL = "https://www.alphavantage.co/query"


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
