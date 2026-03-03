from execution.persistent_trade_state_manager import PersistentTradeStateManager
from execution.trade_orchestrator import TradeOrchestrator


def run_simulation():
    state_manager = PersistentTradeStateManager(db_path="trading.db")

    # Seed portfolio only if database is empty
    if not state_manager.get_all_trades():
        state_manager.record_trade({
            "execution_status": "Filled",
            "currency_pair": "EUR/USD",
            "direction": "Long",
            "position_size": 1.0,
            "fill_price": 1.1000,
            "stop_loss_price": 1.0950,
            "take_profit_price": 1.1100,
            "timestamp": "2026-02-20T00:00:00Z",
        })

        state_manager.record_trade({
            "execution_status": "Filled",
            "currency_pair": "GBP/USD",
            "direction": "Short",
            "position_size": 2.0,
            "fill_price": 1.2500,
            "stop_loss_price": 1.2600,
            "take_profit_price": 1.2300,
            "timestamp": "2026-02-20T00:05:00Z",
        })

    # Proposed trade
    proposed_trade = {
        "currency_pair": "USD/JPY",
        "direction": "Long",
        "approved_position_size": 2.0,
        "entry_price": 110.50,
        "stop_loss_price": 109.80,
        "take_profit_price": 111.20,
    }

    result = TradeOrchestrator.process_trade(
        state_manager=state_manager,
        request_id="REQ-DB-001",
        proposed_trade=proposed_trade,
        max_currency_exposure=3.0,
        market_price=110.50,
    )

    print("=== ORCHESTRATOR RESULT ===")
    print(result)

    print("=== PERSISTED STATE ===")
    print(state_manager.get_all_trades())

    state_manager.close()


if __name__ == "__main__":
    run_simulation()
