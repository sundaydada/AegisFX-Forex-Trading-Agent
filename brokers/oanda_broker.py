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

    def _make_request(self, endpoint: str) -> Dict:
        url = f"{self._base_url}/v3/accounts/{self._account_id}{endpoint}"

        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {self._api_key}")
        req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
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
        raise NotImplementedError("OANDA get_open_positions not yet implemented")

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
