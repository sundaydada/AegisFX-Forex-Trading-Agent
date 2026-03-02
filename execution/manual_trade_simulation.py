from execution.trade_state_manager import TradeStateManager
from execution.trade_orchestrator import TradeOrchestrator


def run_simulation():
    state_manager = TradeStateManager()

    # Seed portfolio with existing trades
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

    request_id = "REQ-001"

    result_1 = TradeOrchestrator.process_trade(
        state_manager=state_manager,
        request_id=request_id,
        proposed_trade=proposed_trade,
        max_currency_exposure=3.0,
        market_price=110.50,
    )

    result_2 = TradeOrchestrator.process_trade(
        state_manager=state_manager,
        request_id=request_id,  # Same ID
        proposed_trade=proposed_trade,
        max_currency_exposure=3.0,
        market_price=110.50,
    )

    print("=== FIRST CALL RESULT ===")
    print(result_1)

    print("=== SECOND CALL RESULT (SHOULD BE IDENTICAL, NO RE-EXECUTION) ===")
    print(result_2)

    print("=== CURRENT STATE ===")
    print(state_manager.get_all_trades())


if __name__ == "__main__":
    run_simulation()
