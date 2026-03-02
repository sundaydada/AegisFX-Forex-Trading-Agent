from typing import Dict, List
from execution.execution_engine import ExecutionEngine
from execution.trade_state_manager import TradeStateManager
from execution.portfolio_risk_evaluator import PortfolioRiskEvaluator


class TradeOrchestrator:
    """
    Enforces approval → execution → state persistence flow.
    This is the ONLY valid trade execution entry point.
    """

    @staticmethod
    def process_trade(
        state_manager: TradeStateManager,
        request_id: str,
        proposed_trade: Dict,
        max_currency_exposure: float,
        market_price: float,
    ) -> Dict:
        """
        Returns structured result:
        {
            "approval_status": str,
            "reason": str,
            "execution_result": Dict | None
        }
        """

        # Idempotency check
        if state_manager.has_processed(request_id):
            return state_manager.get_processed_result(request_id)

        current_trades = state_manager.get_all_trades()

        # Step 1: Risk Evaluation
        risk_decision = PortfolioRiskEvaluator.evaluate_trade(
            current_trades=current_trades,
            proposed_trade=proposed_trade,
            max_currency_exposure=max_currency_exposure,
        )

        if risk_decision["approval_status"] == "Rejected":
            result = {
                "approval_status": "Rejected",
                "reason": risk_decision["reason"],
                "execution_result": None,
            }
            state_manager.record_processed_result(request_id, result)
            return result

        # Step 2: Execute Trade
        execution_trade = {
            "currency_pair": proposed_trade["currency_pair"],
            "direction": proposed_trade["direction"],
            "position_size": proposed_trade["approved_position_size"],
            "entry_price": proposed_trade["entry_price"],
            "stop_loss_price": proposed_trade["stop_loss_price"],
            "take_profit_price": proposed_trade["take_profit_price"],
        }

        execution_result = ExecutionEngine.execute_trade(
            execution_trade,
            market_price,
        )

        # Step 3: Persist Only If Filled
        if execution_result["execution_status"] == "Filled":
            state_manager.record_trade(execution_result)

        result = {
            "approval_status": "Approved",
            "reason": risk_decision["reason"],
            "execution_result": execution_result,
        }
        state_manager.record_processed_result(request_id, result)
        return result
