"""
Autonomy Execution Bridge — Phase 3 of AegisFX autonomy build.

This module is a thin orchestration layer. For each APPROVED AI proposal,
it:
    1. Consults the AutonomyGate (pure decision).
    2. If gated allowed, hands the proposal to the existing
       ProposalExecutionBridge (which already routes through the
       deterministic orchestrator).
    3. Records skipped or executed outcomes for the caller.

This bridge has NO direct broker access, NO direct orchestrator state
manipulation, and NO trade-construction code. It is a controller that
combines two existing components.
"""

import logging
from typing import Dict, List

from execution.autonomy_settings import AutonomySettingsManager
from execution.autonomy_gate import AutonomyGate
from ai.proposal_execution_bridge import ProposalExecutionBridge

logger = logging.getLogger("aegisfx.autonomy_bridge")


class AutonomyExecutionBridge:
    """
    Loops APPROVED proposals through autonomy policy + existing
    manual-execution bridge.

    The bridge owns no state and persists nothing. The caller is
    responsible for:
      - tracking the nightly_trade_count
      - supplying a configured orchestrator
      - supplying a state_manager (forwarded to ProposalExecutionBridge)
      - marking the proposal_id as executed in the approval queue if needed
    """

    def __init__(self, settings_path: str = "autonomy_settings.json"):
        self._settings_mgr = AutonomySettingsManager(settings_path=settings_path)

    def auto_execute_eligible_proposals(
        self,
        proposals: List[Dict],
        orchestrator,
        nightly_trade_count: int,
        state_manager=None,
        max_currency_exposure: float = 100.0,
    ) -> Dict:
        """
        Loop over proposals, gate each, execute eligible ones.

        Args:
            proposals: List of proposal dicts from the approval queue.
            orchestrator: TradeOrchestrator instance.
            nightly_trade_count: Trades already auto-executed tonight.
            state_manager: Trade state manager passed through to the
                proposal execution bridge.
            max_currency_exposure: Forwarded to the orchestrator (same
                value the dashboard's manual path uses).

        Returns:
            {
                "executed": [
                    {
                        "proposal_id": str,
                        "result": <ProposalExecutionBridge result dict>,
                    },
                    ...
                ],
                "skipped": [
                    {
                        "proposal_id": str,
                        "reason": str,
                        "checks": Dict[str, bool],
                    },
                    ...
                ]
            }
        """

        settings = self._settings_mgr.load_settings()
        executed: List[Dict] = []
        skipped: List[Dict] = []

        # Only APPROVED proposals are candidates. PENDING and REJECTED are
        # not eligible — autonomy must never short-circuit the human
        # approval step or override an operator rejection.
        candidates = [
            p for p in (proposals or [])
            if p.get("status") == "APPROVED"
        ]

        logger.info({
            "event": "autonomy_execution_started",
            "approved_count": len(candidates),
            "raw_proposal_count": len(proposals or []),
            "starting_nightly_count": nightly_trade_count,
            "auto_trade_enabled": settings.get("auto_trade_enabled"),
        })

        # If autonomy master switch is OFF we still iterate so each
        # proposal gets a recorded skip with a structured reason.
        running_nightly_count = int(nightly_trade_count)

        for proposal in candidates:
            proposal_id = proposal.get("proposal_id", "")

            decision = AutonomyGate.can_auto_execute(
                proposal=proposal,
                settings=settings,
                nightly_trade_count=running_nightly_count,
            )

            if not decision["allowed"]:
                skipped.append({
                    "proposal_id": proposal_id,
                    "reason": decision["reason"],
                    "checks": decision["checks"],
                })
                logger.info({
                    "event": "autonomy_proposal_skipped",
                    "proposal_id": proposal_id,
                    "reason": decision["reason"],
                    "checks": decision["checks"],
                })
                continue

            # Hand off to the existing manual-execute bridge, which routes
            # through the orchestrator's full safety stack (idempotency,
            # circuit breaker, rate limit, netting, risk evaluation,
            # broker health, trading_enabled toggle).
            try:
                exec_result = ProposalExecutionBridge.execute_approved_proposal(
                    proposal=proposal,
                    orchestrator=orchestrator,
                    state_manager=state_manager,
                    max_currency_exposure=max_currency_exposure,
                )
            except Exception as e:
                exec_result = {
                    "success": False,
                    "message": f"Execution bridge exception: {str(e)}",
                    "request_id": "",
                    "execution_result": {},
                }

            executed.append({
                "proposal_id": proposal_id,
                "result": exec_result,
            })

            logger.info({
                "event": "autonomy_proposal_executed",
                "proposal_id": proposal_id,
                "success": exec_result.get("success"),
                "message": exec_result.get("message"),
                "request_id": exec_result.get("request_id"),
            })

            # Only successful (or netted) results count against the
            # nightly budget. A failed orchestrator call doesn't burn the
            # operator's nightly allowance.
            if exec_result.get("success"):
                running_nightly_count += 1

        logger.info({
            "event": "autonomy_execution_completed",
            "executed_count": len(executed),
            "skipped_count": len(skipped),
            "ending_nightly_count": running_nightly_count,
        })

        return {
            "executed": executed,
            "skipped": skipped,
        }


# ----------------------------------------------------------------------
# Smoke test
# Run directly with:  python execution/autonomy_execution_bridge.py
# ----------------------------------------------------------------------

def _run_smoke_test() -> None:
    """
    Quick sanity check using a fake orchestrator. Verifies that:
      - PENDING proposals are skipped
      - REJECTED proposals are skipped
      - APPROVED proposals that fail the gate are skipped with structured reason
      - APPROVED proposals that pass the gate reach the execution bridge
    """
    import os, tempfile

    class FakeOrchestrator:
        def __init__(self):
            self.calls = []

        def process_trade(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "approval_status": "Approved",
                "reason": "fake orchestrator success",
                "execution_result": {"execution_status": "Filled"},
            }

    class FakeStateManager:
        pass

    # Use a temporary settings file so we don't touch operator config
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        tmp_path = f.name

    try:
        bridge = AutonomyExecutionBridge(settings_path=tmp_path)
        # Enable autonomy for the test
        bridge._settings_mgr.save_settings({
            "auto_trade_enabled": True,
            "min_confidence": 80,
            "max_trades_per_night": 5,
            "max_position_size": 1.0,
            "allowed_pairs": ["EUR/USD", "GBP/USD", "USD/JPY"],
            "allowed_risk_modes": ["NORMAL", "REDUCED"],
        })

        proposals = [
            # 1. PENDING — must be skipped (not even considered)
            {"status": "PENDING", "proposal_id": "PROP-PENDING",
             "pair": "EUR/USD", "suggested_size": 1.0, "confidence": 90,
             "execution_allowed": True},
            # 2. REJECTED — must be skipped
            {"status": "REJECTED", "proposal_id": "PROP-REJECTED",
             "pair": "EUR/USD", "suggested_size": 1.0, "confidence": 90,
             "execution_allowed": True},
            # 3. APPROVED but low confidence — should be skipped by gate
            {"status": "APPROVED", "proposal_id": "PROP-LOWCONF",
             "pair": "EUR/USD", "suggested_size": 1.0, "confidence": 70,
             "execution_allowed": True},
            # 4. APPROVED disallowed pair — should be skipped
            {"status": "APPROVED", "proposal_id": "PROP-BADPAIR",
             "pair": "AUD/USD", "suggested_size": 1.0, "confidence": 90,
             "execution_allowed": True},
            # 5. APPROVED + passes gate — should reach orchestrator
            {"status": "APPROVED", "proposal_id": "PROP-OK-1",
             "pair": "EUR/USD", "direction": "LONG",
             "suggested_size": 1.0, "confidence": 90,
             "execution_allowed": True},
            # 6. APPROVED + passes gate — second one, also executes
            {"status": "APPROVED", "proposal_id": "PROP-OK-2",
             "pair": "GBP/USD", "direction": "SHORT",
             "suggested_size": 1.0, "confidence": 90,
             "execution_allowed": True},
        ]

        fake_orch = FakeOrchestrator()
        result = bridge.auto_execute_eligible_proposals(
            proposals=proposals,
            orchestrator=fake_orch,
            nightly_trade_count=0,
            state_manager=FakeStateManager(),
        )

        executed_ids = [r["proposal_id"] for r in result["executed"]]
        skipped_ids = [r["proposal_id"] for r in result["skipped"]]

        def check(label, cond):
            print(f"[{'PASS' if cond else 'FAIL'}] {label}")

        check("PENDING proposal never appears in result", "PROP-PENDING" not in executed_ids and "PROP-PENDING" not in skipped_ids)
        check("REJECTED proposal never appears in result", "PROP-REJECTED" not in executed_ids and "PROP-REJECTED" not in skipped_ids)
        check("APPROVED + low confidence is SKIPPED", "PROP-LOWCONF" in skipped_ids)
        check("APPROVED + disallowed pair is SKIPPED", "PROP-BADPAIR" in skipped_ids)
        check("APPROVED + gated-OK is EXECUTED", "PROP-OK-1" in executed_ids and "PROP-OK-2" in executed_ids)
        check("Orchestrator was called exactly 2 times", len(fake_orch.calls) == 2)
        check("Skipped entries carry structured reason/checks",
              all("reason" in s and "checks" in s for s in result["skipped"]))

        # Test: autonomy OFF -> everything skipped
        bridge._settings_mgr.update_setting("auto_trade_enabled", False)
        fake_orch.calls.clear()
        result_off = bridge.auto_execute_eligible_proposals(
            proposals=[p for p in proposals if p["status"] == "APPROVED"],
            orchestrator=fake_orch,
            nightly_trade_count=0,
            state_manager=FakeStateManager(),
        )
        check("Autonomy OFF skips all APPROVED proposals",
              len(result_off["executed"]) == 0 and len(fake_orch.calls) == 0)

        print("\nSmoke test complete.")
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


if __name__ == "__main__":
    _run_smoke_test()
