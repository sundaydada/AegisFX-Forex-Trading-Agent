import os
from typing import Dict, List
from datetime import datetime, timezone
from brokers.broker_interface import BrokerInterface
from execution.persistent_trade_state_manager import PersistentTradeStateManager
from execution.trade_orchestrator import TradeOrchestrator

DB_PATH = "test_crash_recovery.db"


class MockBrokerCrashRecovery(BrokerInterface):
    """
    Mock broker that tracks whether place_order was called.
    get_order_status returns Filled to simulate broker having executed the trade.
    """

    def __init__(self):
        self.place_order_called = False

    def place_order(self, order: Dict) -> Dict:
        self.place_order_called = True
        return {
            "execution_status": "Filled",
            "broker_order_id": "MOCK-TXN-100",
            "currency_pair": order["currency_pair"],
            "direction": order["direction"],
            "units": order["position_size"],
            "fill_price": 1.1050,
            "timestamp": "2026-04-01T12:00:00Z",
        }

    def get_open_positions(self) -> List:
        raise NotImplementedError

    def get_account_balance(self) -> float:
        raise NotImplementedError

    def get_order_status(self, request_id: str) -> Dict:
        return {
            "execution_status": "Filled",
            "broker_order_id": "MOCK-TXN-100",
            "details": {
                "instrument": "EUR_USD",
                "units": "1",
                "price": "1.1050",
                "time": "2026-04-01T12:00:00Z",
            },
        }


def cleanup():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)


def run_test():
    cleanup()

    print("=" * 60)
    print("CRASH RECOVERY TEST")
    print("=" * 60)

    # --- PHASE 1: Simulate pre-crash state ---
    print("\n--- PHASE 1: Simulating crash state ---")

    state_manager = PersistentTradeStateManager(db_path=DB_PATH)

    # Manually insert a PENDING trade as if orchestrator crashed
    # after record_trade() but before update_trade()
    pending_trade = {
        "request_id": "REQ-CRASH-001",
        "currency_pair": "EUR/USD",
        "direction": "Long",
        "position_size": 1.0,
        "status": "PENDING",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    state_manager.record_trade(pending_trade)

    print("Inserted PENDING trade (simulating crash after broker call)")
    print(f"Trade state: {state_manager.get_all_trades()}")
    print(f"Processed result exists: {state_manager.has_processed('REQ-CRASH-001')}")

    # Close connection — simulating process crash
    state_manager.close()
    print("\n--- Process crashed (connection closed) ---")

    # --- PHASE 2: Restart system ---
    print("\n--- PHASE 2: System restart ---")

    # New state manager instance (reads from same DB)
    state_manager_2 = PersistentTradeStateManager(db_path=DB_PATH)

    # Verify PENDING trade survived the crash
    trades_after_restart = state_manager_2.get_all_trades()
    print(f"Trades after restart: {len(trades_after_restart)}")
    print(f"Trade status: {trades_after_restart[0]['status']}")
    assert trades_after_restart[0]["status"] == "PENDING", "Trade should still be PENDING"

    # Verify no processed result exists
    assert not state_manager_2.has_processed("REQ-CRASH-001"), "Should have no processed result"

    # --- PHASE 3: Reconciliation ---
    print("\n--- PHASE 3: Running reconciliation ---")

    broker = MockBrokerCrashRecovery()
    orchestrator = TradeOrchestrator(broker)

    orchestrator.reconcile_pending_trades(state_manager_2)

    # --- PHASE 4: Verification ---
    print("\n--- PHASE 4: Verification ---")

    trades_after_reconcile = state_manager_2.get_all_trades()
    print(f"Trade state after reconciliation: {trades_after_reconcile[0]}")

    # Verify trade resolved
    assert trades_after_reconcile[0]["status"] == "FILLED", \
        f"Expected FILLED, got {trades_after_reconcile[0]['status']}"
    print("[PASS] Trade moved from PENDING -> FILLED")

    # Verify only 1 trade exists (no duplicates)
    assert len(trades_after_reconcile) == 1, \
        f"Expected 1 trade, got {len(trades_after_reconcile)}"
    print("[PASS] No duplicate trades")

    # Verify broker.place_order was NOT called
    assert not broker.place_order_called, \
        "place_order should NOT have been called during reconciliation"
    print("[PASS] Broker was NOT called again (no duplicate execution)")

    # --- PHASE 5: Verify orchestrator won't re-process ---
    print("\n--- PHASE 5: Verify idempotency after recovery ---")

    proposed_trade = {
        "currency_pair": "EUR/USD",
        "direction": "Long",
        "approved_position_size": 1.0,
    }

    # Try to process same request_id — crash recovery check should catch it
    result = orchestrator.process_trade(
        state_manager=state_manager_2,
        request_id="REQ-CRASH-001",
        proposed_trade=proposed_trade,
        max_currency_exposure=10.0,
    )

    assert not broker.place_order_called, \
        "place_order should still NOT have been called"
    print(f"[PASS] Re-processing blocked (returned cached/recovery result)")
    print(f"Result: {result}")

    state_manager_2.close()
    cleanup()

    print("\n" + "=" * 60)
    print("ALL CRASH RECOVERY TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    run_test()
