import urllib.request
import json
from typing import Dict, List
from brokers.broker_interface import BrokerInterface
from brokers.broker_health import BrokerHealthMonitor


class OandaBroker(BrokerInterface):
    """
    OANDA broker implementation.
    Conforms to BrokerInterface contract.
    """

    def __init__(self, api_key: str, account_id: str, base_url: str, health: BrokerHealthMonitor = None):
        self._api_key = api_key
        self._account_id = account_id
        self._base_url = base_url
        self.health = health or BrokerHealthMonitor()

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
                result = json.loads(response.read().decode("utf-8"))
                self.health.report_success()
                return result
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            self.health.report_failure(f"API error {e.code}: {error_body}")
            raise RuntimeError(
                f"OANDA API error {e.code}: {error_body}"
            )
        except urllib.error.URLError as e:
            self.health.report_failure(f"Connection error: {str(e.reason)}")
            raise RuntimeError(
                f"OANDA connection error: {str(e.reason)}"
            )

    def place_order(self, order: Dict) -> Dict:
        required_fields = ["currency_pair", "direction", "position_size"]
        for field in required_fields:
            if field not in order:
                return {
                    "execution_status": "Rejected",
                    "reason": f"Missing required field: {field}",
                }

        instrument = order["currency_pair"].replace("/", "_")
        units = order["position_size"]
        if order["direction"] == "Short":
            units = -units

        payload = {
            "order": {
                "type": "MARKET",
                "instrument": instrument,
                "units": str(int(units)),
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
            }
        }

        try:
            data = self._make_request("/orders", method="POST", body=payload)
        except RuntimeError as e:
            return {
                "execution_status": "Rejected",
                "reason": f"Order API error: {str(e)}",
            }

        if "orderRejectTransaction" in data:
            reject = data["orderRejectTransaction"]
            return {
                "execution_status": "Rejected",
                "reason": reject.get("rejectReason", "Order rejected by broker"),
            }

        if "orderCancelTransaction" in data:
            cancel = data["orderCancelTransaction"]
            return {
                "execution_status": "Rejected",
                "reason": cancel.get("reason", "Order cancelled by broker"),
            }

        fill = data.get("orderFillTransaction")
        if not fill:
            return {
                "execution_status": "Rejected",
                "reason": "Malformed response: missing orderFillTransaction",
            }

        try:
            return {
                "execution_status": "Filled",
                "broker_order_id": fill.get("id", ""),
                "currency_pair": order["currency_pair"],
                "direction": order["direction"],
                "units": abs(float(fill.get("units", 0))),
                "fill_price": float(fill.get("price", 0)),
                "timestamp": fill.get("time", ""),
            }
        except (ValueError, TypeError) as e:
            return {
                "execution_status": "Rejected",
                "reason": f"Failed to parse fill data: {str(e)}",
            }

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

    def get_order_status(self, request_id: str) -> Dict:
        """
        Look up a transaction by broker_order_id.
        request_id here is expected to be the broker_order_id
        stored in the trade record from place_order().
        """

        try:
            data = self._make_request(f"/transactions/{request_id}")
        except RuntimeError as e:
            error_msg = str(e)
            if "404" in error_msg:
                return {
                    "execution_status": "NOT_FOUND",
                }
            return {
                "execution_status": "ERROR",
                "error_message": error_msg,
            }

        transaction = data.get("transaction")
        if not transaction:
            return {
                "execution_status": "NOT_FOUND",
            }

        tx_type = transaction.get("type", "")

        if tx_type == "ORDER_FILL":
            return {
                "execution_status": "Filled",
                "broker_order_id": transaction.get("id", ""),
                "details": {
                    "instrument": transaction.get("instrument", ""),
                    "units": transaction.get("units", ""),
                    "price": transaction.get("price", ""),
                    "time": transaction.get("time", ""),
                    "pl": transaction.get("pl", ""),
                },
            }

        if tx_type in ("ORDER_CANCEL", "ORDER_CLIENT_EXTENSIONS_MODIFY"):
            return {
                "execution_status": "Cancelled",
                "broker_order_id": transaction.get("id", ""),
                "details": {
                    "reason": transaction.get("reason", ""),
                    "time": transaction.get("time", ""),
                },
            }

        return {
            "execution_status": "Rejected",
            "broker_order_id": transaction.get("id", ""),
            "details": {
                "type": tx_type,
                "reason": transaction.get("rejectReason", transaction.get("reason", "")),
                "time": transaction.get("time", ""),
            },
        }

    def close_position(self, currency_pair: str, units: float, direction: str) -> Dict:
        """
        Close a position by sending the opposite market order.
        Long → send Short. Short → send Long.
        """

        instrument = currency_pair.replace("/", "_")

        # Opposite direction to close
        if direction == "Long":
            close_units = -abs(units)
        else:
            close_units = abs(units)

        payload = {
            "order": {
                "type": "MARKET",
                "instrument": instrument,
                "units": str(int(close_units)),
                "timeInForce": "FOK",
                "positionFill": "REDUCE_ONLY",
            }
        }

        try:
            data = self._make_request("/orders", method="POST", body=payload)
        except RuntimeError as e:
            return {
                "status": "FAILED",
                "reason": f"Broker API error: {str(e)}",
            }

        if "orderCancelTransaction" in data:
            cancel = data["orderCancelTransaction"]
            return {
                "status": "FAILED",
                "reason": cancel.get("reason", "Order cancelled by broker"),
            }

        if "orderRejectTransaction" in data:
            reject = data["orderRejectTransaction"]
            return {
                "status": "FAILED",
                "reason": reject.get("rejectReason", "Order rejected by broker"),
            }

        fill = data.get("orderFillTransaction")
        if not fill:
            return {
                "status": "FAILED",
                "reason": "Malformed response: missing orderFillTransaction",
            }

        return {
            "status": "SUCCESS",
            "close_price": float(fill.get("price", 0)),
            "units_closed": abs(float(fill.get("units", 0))),
            "timestamp": fill.get("time", ""),
        }
