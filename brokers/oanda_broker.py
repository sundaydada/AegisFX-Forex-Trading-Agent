import urllib.request
import json
from typing import Dict, List
from brokers.broker_interface import BrokerInterface


class OandaBroker(BrokerInterface):
    """
    OANDA broker implementation.
    Conforms to BrokerInterface contract.
    """

    def __init__(self, api_key: str, account_id: str, base_url: str):
        self._api_key = api_key
        self._account_id = account_id
        self._base_url = base_url

    def _make_request(self, endpoint: str, method: str = "GET", body: Dict = None) -> Dict:
        url = f"{self._base_url}/v3/accounts/{self._account_id}{endpoint}"

        req = urllib.request.Request(url, method=method)
        req.add_header("Authorization", f"Bearer {self._api_key}")
        req.add_header("Content-Type", "application/json")

        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")

        try:
            with urllib.request.urlopen(req, data, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            raise RuntimeError(
                f"OANDA API error {e.code}: {error_body}"
            )
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"OANDA connection error: {str(e.reason)}"
            )

    def place_order(self, order: Dict) -> Dict:
        raise NotImplementedError("OANDA place_order not yet implemented")

    def get_open_positions(self) -> List:
        try:
            data = self._make_request("/openPositions")
        except RuntimeError as e:
            raise RuntimeError(f"Failed to get open positions: {str(e)}")

        positions_data = data.get("positions")
        if positions_data is None:
            raise RuntimeError(
                "Malformed response: missing 'positions' field"
            )

        positions = []
        for pos in positions_data:
            instrument = pos.get("instrument", "")
            long_units = float(pos.get("long", {}).get("units", 0))
            short_units = float(pos.get("short", {}).get("units", 0))

            if long_units != 0:
                positions.append({
                    "currency_pair": instrument.replace("_", "/"),
                    "direction": "Long",
                    "units": long_units,
                    "unrealized_pl": float(pos.get("long", {}).get("unrealizedPL", 0)),
                    "average_price": float(pos.get("long", {}).get("averagePrice", 0)),
                })

            if short_units != 0:
                positions.append({
                    "currency_pair": instrument.replace("_", "/"),
                    "direction": "Short",
                    "units": abs(short_units),
                    "unrealized_pl": float(pos.get("short", {}).get("unrealizedPL", 0)),
                    "average_price": float(pos.get("short", {}).get("averagePrice", 0)),
                })

        return positions

    def get_account_balance(self) -> float:
        try:
            data = self._make_request("/summary")
        except RuntimeError as e:
            raise RuntimeError(f"Failed to get account balance: {str(e)}")

        account = data.get("account")
        if not account:
            raise RuntimeError(
                "Malformed response: missing 'account' field"
            )

        balance = account.get("balance")
        if balance is None:
            raise RuntimeError(
                "Malformed response: missing 'balance' field"
            )

        try:
            return float(balance)
        except (ValueError, TypeError) as e:
            raise RuntimeError(
                f"Failed to parse balance value: {str(e)}"
            )
