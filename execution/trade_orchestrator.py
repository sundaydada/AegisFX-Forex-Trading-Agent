from typing import Dict, List
from brokers.broker_interface import BrokerInterface
from execution.trade_state_manager import TradeStateManager
from execution.portfolio_risk_evaluator import PortfolioRiskEvaluator


class TradeOrchestrator:
    """
    Enforces approval → execution → state persistence flow.
    This is the ONLY valid trade execution entry point.
    """

    def __init__(self, broker: BrokerInterface):
        self._broker = broker

    def process_trade(
        self,
        state_manager: TradeStateManager,
        request_id: str,
        proposed_trade: Dict,
        max_currency_exposure: float,
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

        # Step 2: Execute Trade via broker (no state mutation)
        order = {
            "currency_pair": proposed_trade["currency_pair"],
            "direction": proposed_trade["direction"],
            "position_size": proposed_trade["approved_position_size"],
        }

        execution_result = self._broker.place_order(order)

        # Build final result before any state mutation
        final_result = {
            "approval_status": "Approved",
            "reason": risk_decision["reason"],
            "execution_result": execution_result,
        }

        # Step 3: Atomic Commit
        if execution_result["execution_status"] == "Filled":
            state_manager.record_trade(execution_result)
        state_manager.record_processed_result(request_id, final_result)

        return final_result
