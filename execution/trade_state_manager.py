from typing import List, Dict


class TradeStateManager:
    """
    Deterministic in-memory trade ledger.
    Responsible ONLY for storing and retrieving trade records.
    No risk logic. No execution logic.
    """

    def __init__(self):
        self._trades: List[Dict] = []
        self._processed_requests = {}
        # Maps request_id → orchestrator result

    def record_trade(self, trade: Dict) -> None:
        self._trades.append(trade)

    def get_all_trades(self) -> List[Dict]:
        return list(self._trades)

    def get_open_exposure(self) -> float:
        """
        Returns total position size of filled trades.
        """
        return sum(trade["position_size"] for trade in self._trades)

    def has_processed(self, request_id: str) -> bool:
        return request_id in self._processed_requests

    def get_processed_result(self, request_id: str):
        return self._processed_requests.get(request_id)

    def record_processed_result(self, request_id: str, result: Dict):
        self._processed_requests[request_id] = result
