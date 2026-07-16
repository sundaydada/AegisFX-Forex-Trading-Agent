import logging
from typing import Dict

from execution.proposal_sizing import size_trade_proposal

logger = logging.getLogger("aegisfx.proposal_bridge")


class ProposalExecutionBridge:
    """
    Routes APPROVED AI proposals through the deterministic orchestrator.
    Does NOT call broker directly. Does NOT bypass risk or governance.
    Requires explicit operator action — never invoked autonomously.
    """

    @staticmethod
    def execute_approved_proposal(
        proposal: Dict,
        orchestrator,
        state_manager,
        max_currency_exposure: float = 10.0,
        account_snapshot=None,
        entry_price=None,
        stop_distance_pips=None,
        drawdown_fraction=None,
    ) -> Dict:
        """
        Execute an APPROVED proposal via the orchestrator pipeline.

        Args:
            proposal: Approval queue record with status "APPROVED"
            orchestrator: TradeOrchestrator instance
            state_manager: Trade state manager
            max_currency_exposure: Risk limit passed to orchestrator

        Returns:
            {
                "success": bool,
                "message": str,
                "request_id": str,
                "execution_result": dict
            }
        """

        proposal_id = proposal.get("proposal_id", "")
        request_id = f"AI-PROPOSAL-{proposal_id}"

        logger.info({
            "event": "proposal_execution_requested",
            "proposal_id": proposal_id,
            "request_id": request_id,
        })

        status = proposal.get("status", "")
        if status != "APPROVED":
            logger.warning({
                "event": "proposal_execution_result",
                "proposal_id": proposal_id,
                "result": "rejected_by_bridge",
                "reason": f"status is {status}, not APPROVED",
            })
            return {
                "success": False,
                "message": f"Cannot execute proposal with status: {status}",
                "request_id": request_id,
                "execution_result": {},
            }

        # Map proposal fields to orchestrator trade schema
        ai_direction = proposal.get("direction", "")
        # Bridge AI direction labels (LONG/SHORT) to orchestrator labels (Long/Short)
        direction_map = {"LONG": "Long", "SHORT": "Short"}
        orchestrator_direction = direction_map.get(ai_direction, ai_direction)

        # Sanity check on required fields
        if not proposal.get("pair", "") or not orchestrator_direction:
            return {
                "success": False,
                "message": "Proposal missing required fields",
                "request_id": request_id,
                "execution_result": {},
            }

        sizing_inputs = {
            "account_snapshot": account_snapshot,
            "entry_price": entry_price,
            "stop_distance_pips": stop_distance_pips,
            "drawdown_fraction": drawdown_fraction,
        }
        missing_sizing_inputs = [
            name for name, value in sizing_inputs.items()
            if value is None
        ]
        if missing_sizing_inputs:
            return {
                "success": False,
                "message": (
                    "Missing required sizing input: "
                    + ", ".join(missing_sizing_inputs)
                ),
                "request_id": request_id,
                "execution_result": {},
            }

        sizing = size_trade_proposal(
            account_snapshot=account_snapshot,
            pair=proposal["pair"],
            side=proposal["direction"],
            entry_price=entry_price,
            stop_distance_pips=stop_distance_pips,
            drawdown_fraction=drawdown_fraction,
        )
        proposed_trade = {
            "currency_pair": sizing.pair,
            "direction": direction_map.get(sizing.side, sizing.side),
            "approved_position_size": sizing.units,
            "stop_loss_price": sizing.stop_loss_price,
            "account_snapshot": account_snapshot,
            "nav": sizing.nav,
            "account_currency": sizing.account_currency,
            "risk_fraction": sizing.risk_fraction,
            "risk_budget_amount": sizing.risk_amount,
            "loss_per_unit_at_stop": sizing.loss_per_unit_at_stop,
        }

        # Hand off to orchestrator — orchestrator enforces all deterministic controls
        try:
            result = orchestrator.process_trade(
                state_manager=state_manager,
                request_id=request_id,
                proposed_trade=proposed_trade,
                max_currency_exposure=max_currency_exposure,
            )
        except Exception as e:
            logger.error({
                "event": "proposal_execution_result",
                "proposal_id": proposal_id,
                "result": "orchestrator_exception",
                "error": str(e),
            })
            return {
                "success": False,
                "message": f"Orchestrator error: {str(e)}",
                "request_id": request_id,
                "execution_result": {},
            }

        approval_status = result.get("approval_status", "")
        success = approval_status in ("Approved", "Netted")

        logger.info({
            "event": "proposal_execution_result",
            "proposal_id": proposal_id,
            "request_id": request_id,
            "approval_status": approval_status,
            "reason": result.get("reason", ""),
        })

        return {
            "success": success,
            "message": result.get("reason", ""),
            "request_id": request_id,
            "execution_result": result.get("execution_result") or {},
        }
