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

    @abstractmethod
    def get_order_status(self, request_id: str) -> Dict:
        """
        Check the status of a previously placed order.

        Args:
            request_id: The unique identifier for the trade request.

        Returns:
            Order status dict with at minimum:
            {
                "execution_status": str ("Filled", "Rejected", "Pending", "Unknown"),
                ...additional broker-specific fields
            }
        """
        pass
