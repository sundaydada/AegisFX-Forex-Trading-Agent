from execution.trade_formatter import format_trade_approval
from execution.execution_engine import execute_trade
from execution.trade_state_manager import TradeStateManager
from execution.portfolio_risk_evaluator import PortfolioRiskEvaluator


def run_simulation():
    state_manager = TradeStateManager()

    # Existing portfolio
    existing_trades = [
        ("EUR/USD", "Long", 1.0, 1.1050),
        ("GBP/USD", "Short", 2.0, 1.2500),
        ("USD/JPY", "Long", 1.5, 110.00),
    ]

    for pair, direction, size, price in existing_trades:
        trade = format_trade_approval(
            approval_status="Approved",
            currency_pair=pair,
            direction=direction,
            position_size=size,
            stop_loss=0.0,
            take_profit=0.0,
            risk_rationale="Initial portfolio build",
        )

        execution_result = execute_trade(trade, market_price=price)
        state_manager.record_trade(execution_result)

    current_trades = state_manager.get_all_trades()

    # Proposed trade that should breach USD limit
    proposed_trade = format_trade_approval(
        approval_status="Approved",
        currency_pair="USD/JPY",
        direction="Long",
        position_size=1.0,
        stop_loss=0.0,
        take_profit=0.0,
        risk_rationale="Test USD breach",
    )

    risk_decision = PortfolioRiskEvaluator.evaluate_trade(
        current_trades=current_trades,
        proposed_trade=proposed_trade,
        max_currency_exposure=3.0,
    )

    print("=== RISK DECISION ===")
    print(risk_decision)


if __name__ == "__main__":
    run_simulation()
