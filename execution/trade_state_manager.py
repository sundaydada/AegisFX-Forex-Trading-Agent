from typing import List, Dict


class TradeStateManager:
    """
    Deterministic in-memory trade ledger.
    Responsible ONLY for storing and retrieving trade records.
    No risk logic. No execution logic.
    """

    def __init__(self):
        self._trades: List[Dict] = []

    def record_trade(self, trade: Dict) -> None:
        if trade.get("execution_status") != "Filled":
            return

        self._trades.append(trade)

    def get_all_trades(self) -> List[Dict]:
        return list(self._trades)

    def get_open_exposure(self) -> float:
        """
        Returns total position size of filled trades.
        """
        return sum(trade["position_size"] for trade in self._trades)
