from typing import Dict, List
from brokers.broker_interface import BrokerInterface
from execution.trade_state_manager import TradeStateManager
from execution.trade_orchestrator import TradeOrchestrator


# --- Mock Brokers ---

class MockBrokerSuccess(BrokerInterface):
    def place_order(self, order: Dict) -> Dict:
        return {
            "execution_status": "Filled",
            "currency_pair": order["currency_pair"],
            "direction": order["direction"],
            "units": order["position_size"],
            "fill_price": 1.1050,
            "timestamp": "2026-03-30T12:00:00Z",
        }

    def get_open_positions(self) -> List:
        raise NotImplementedError

    def get_account_balance(self) -> float:
        raise NotImplementedError

    def get_order_status(self, request_id: str) -> Dict:
        raise NotImplementedError


class MockBrokerReject(BrokerInterface):
    def place_order(self, order: Dict) -> Dict:
        return {
            "execution_status": "Rejected",
            "reason": "Insufficient margin",
        }

    def get_open_positions(self) -> List:
        raise NotImplementedError

    def get_account_balance(self) -> float:
        raise NotImplementedError

    def get_order_status(self, request_id: str) -> Dict:
        raise NotImplementedError


class MockBrokerException(BrokerInterface):
    def place_order(self, order: Dict) -> Dict:
        raise Exception("API failure")

    def get_open_positions(self) -> List:
        raise NotImplementedError

    def get_account_balance(self) -> float:
        raise NotImplementedError

    def get_order_status(self, request_id: str) -> Dict:
        raise NotImplementedError


# --- Shared Test Data ---

PROPOSED_TRADE = {
    "currency_pair": "EUR/USD",
    "direction": "Long",
    "approved_position_size": 1.0,
}


def run_scenario(name, broker, request_id):
    print(f"\n{'=' * 60}")
    print(f"SCENARIO: {name}")
    print(f"{'=' * 60}")

    state_manager = TradeStateManager()
    orchestrator = TradeOrchestrator(broker)

    result = orchestrator.process_trade(
        state_manager=state_manager,
        request_id=request_id,
        proposed_trade=PROPOSED_TRADE,
        max_currency_exposure=10.0,
    )

    all_trades = state_manager.get_all_trades()

    print(f"\n--- Processed Result ---")
    print(result)

    print(f"\n--- Trade State ---")
    for trade in all_trades:
        print(trade)

    print(f"\n--- Total Trades in State Manager: {len(all_trades)} ---")

    return result, all_trades


if __name__ == "__main__":

    # SCENARIO 1: Successful Trade
    result, trades = run_scenario(
        "Successful Trade (PENDING -> FILLED)",
        MockBrokerSuccess(),
        "REQ-SUCCESS-001",
    )
    assert len(trades) == 1, f"Expected 1 trade, got {len(trades)}"
    assert trades[0]["status"] == "FILLED", f"Expected FILLED, got {trades[0]['status']}"
    assert result["approval_status"] == "Approved"
    print("[PASS] PASSED")

    # SCENARIO 2: Broker Rejection
    result, trades = run_scenario(
        "Broker Rejection (PENDING -> FAILED)",
        MockBrokerReject(),
        "REQ-REJECT-001",
    )
    assert len(trades) == 1, f"Expected 1 trade, got {len(trades)}"
    assert trades[0]["status"] == "FAILED", f"Expected FAILED, got {trades[0]['status']}"
    assert result["approval_status"] == "Failed"
    print("[PASS] PASSED")

    # SCENARIO 3: Broker Exception
    result, trades = run_scenario(
        "Broker Exception (PENDING -> FAILED, ERROR)",
        MockBrokerException(),
        "REQ-ERROR-001",
    )
    assert len(trades) == 1, f"Expected 1 trade, got {len(trades)}"
    assert trades[0]["status"] == "FAILED", f"Expected FAILED, got {trades[0]['status']}"
    assert result["execution_result"]["execution_status"] == "ERROR"
    assert result["approval_status"] == "Failed"
    print("[PASS] PASSED")

    print(f"\n{'=' * 60}")
    print("ALL SCENARIOS PASSED")
    print(f"{'=' * 60}")
