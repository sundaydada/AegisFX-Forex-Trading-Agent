from abc import ABC, abstractmethod
from typing import Dict, List


class BrokerInterface(ABC):
    """
    Abstract interface for broker connectivity.
    All broker implementations must conform to this contract.
    """

    @abstractmethod
    def place_order(self, order: Dict) -> Dict:
        """
        Place a trade order with the broker.

        Args:
            order: Order details (pair, direction, size, stop_loss, take_profit)

        Returns:
            Order result (status, fill_price, order_id, timestamp)
        """
        pass

    @abstractmethod
    def get_open_positions(self) -> List:
        """
        Retrieve all currently open positions from the broker.

        Returns:
            List of open position dictionaries.
        """
        pass

    @abstractmethod
    def get_account_balance(self) -> float:
        """
        Retrieve current account balance from the broker.

        Returns:
            Account balance as float.
        """
        pass
