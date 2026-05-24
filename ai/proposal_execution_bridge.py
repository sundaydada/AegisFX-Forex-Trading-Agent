import logging
from typing import Dict

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

        proposed_trade = {
            "currency_pair": proposal.get("pair", ""),
            "direction": orchestrator_direction,
            "approved_position_size": float(proposal.get("suggested_size", 0.0)),
        }

        # Sanity check on required fields
        if not proposed_trade["currency_pair"] or not proposed_trade["direction"]:
            return {
                "success": False,
                "message": "Proposal missing required fields",
                "request_id": request_id,
                "execution_result": {},
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
