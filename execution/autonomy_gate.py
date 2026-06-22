"""
Autonomy Decision Gate — Phase 2 of AegisFX autonomy build.

A pure read-only gate that decides whether a single AI proposal is
eligible for autonomous execution under the operator's current autonomy
settings.

This module has NO side effects:
- No broker calls
- No orchestrator calls
- No database writes
- No file I/O
- No network access

It receives a proposal dict, a settings dict, and the current
nightly trade count. It returns a structured decision.
Actual execution is the caller's responsibility (and is gated again
by the orchestrator's deterministic safety stack).
"""

from typing import Dict


class AutonomyGate:
    """
    Decides whether an AI proposal may be auto-executed.

    All seven checks must pass for `allowed = True`. The returned dict
    always contains every check key so the caller (and the dashboard)
    can show exactly which gate(s) blocked the proposal.
    """

    @staticmethod
    def can_auto_execute(
        proposal: Dict,
        settings: Dict,
        nightly_trade_count: int,
    ) -> Dict:
        """
        Evaluate a proposal against autonomy settings.

        Args:
            proposal: Dict produced by TradeProposalService — expected fields:
                pair, direction, suggested_size, confidence,
                strategy, execution_allowed, and (optionally) risk_mode.
                When proposal lacks an explicit risk_mode (the current
                TradeProposalService output does not include one), the
                check is satisfied automatically — risk gating happens
                upstream in StrategyRecommendationService.
            settings: Dict loaded from AutonomySettingsManager.
            nightly_trade_count: How many autonomous trades have already
                fired this night. Caller maintains this counter.

        Returns:
            {
                "allowed": bool,
                "reason": str,
                "checks": {
                    "auto_trade_enabled": bool,
                    "confidence": bool,
                    "risk_mode": bool,
                    "pair_allowed": bool,
                    "size_allowed": bool,
                    "nightly_limit": bool,
                    "execution_allowed": bool,
                }
            }
        """

        # Defensive extraction with safe defaults
        proposal = proposal or {}
        settings = settings or {}

        # --- Individual checks ---

        # 1. Operator master switch must be enabled
        auto_trade_enabled = bool(settings.get("auto_trade_enabled", False))

        # 2. AI confidence must clear operator threshold
        try:
            proposal_confidence = float(proposal.get("confidence", 0))
        except (ValueError, TypeError):
            proposal_confidence = 0.0
        try:
            min_confidence = float(settings.get("min_confidence", 100))
        except (ValueError, TypeError):
            min_confidence = 100.0
        confidence_ok = proposal_confidence >= min_confidence

        # 3. Risk mode must be in operator allow-list.
        # If the proposal has no explicit risk_mode, the check passes
        # (risk mode is enforced upstream by the strategy layer).
        allowed_risk_modes = settings.get("allowed_risk_modes", [])
        if not isinstance(allowed_risk_modes, list):
            allowed_risk_modes = []
        proposal_risk_mode = proposal.get("risk_mode")
        if proposal_risk_mode is None:
            risk_mode_ok = True
        else:
            risk_mode_ok = proposal_risk_mode in allowed_risk_modes

        # 4. Pair must be in operator allow-list
        allowed_pairs = settings.get("allowed_pairs", [])
        if not isinstance(allowed_pairs, list):
            allowed_pairs = []
        proposal_pair = proposal.get("pair", "")
        pair_allowed = proposal_pair in allowed_pairs

        # 5. Suggested size must not exceed operator cap
        try:
            suggested_size = float(proposal.get("suggested_size", 0))
        except (ValueError, TypeError):
            suggested_size = 0.0
        try:
            max_position_size = float(settings.get("max_position_size", 0))
        except (ValueError, TypeError):
            max_position_size = 0.0
        size_allowed = (suggested_size > 0) and (suggested_size <= max_position_size)

        # 6. Nightly trade budget must not be exhausted
        try:
            count = int(nightly_trade_count)
        except (ValueError, TypeError):
            count = 0
        try:
            nightly_max = int(settings.get("max_trades_per_night", 0))
        except (ValueError, TypeError):
            nightly_max = 0
        nightly_limit_ok = count < nightly_max

        # 7. Proposal itself must permit execution
        execution_allowed_ok = bool(proposal.get("execution_allowed", False))

        # --- Aggregate ---

        checks = {
            "auto_trade_enabled": auto_trade_enabled,
            "confidence": confidence_ok,
            "risk_mode": risk_mode_ok,
            "pair_allowed": pair_allowed,
            "size_allowed": size_allowed,
            "nightly_limit": nightly_limit_ok,
            "execution_allowed": execution_allowed_ok,
        }

        allowed = all(checks.values())

        if allowed:
            reason = (
                f"All autonomy checks passed (conf={proposal_confidence:.0f}>={min_confidence:.0f}, "
                f"size={suggested_size}<={max_position_size}, "
                f"nightly={count}/{nightly_max})"
            )
        else:
            failed = [k for k, v in checks.items() if not v]
            reason = "Blocked by: " + ", ".join(failed)

        return {
            "allowed": allowed,
            "reason": reason,
            "checks": checks,
        }


# ----------------------------------------------------------------------
# Self-test / unit-test style verification
# Run directly with:  python execution/autonomy_gate.py
# ----------------------------------------------------------------------

def _run_self_tests() -> None:
    """Exercise every check path. Prints PASS/FAIL for each scenario."""

    base_settings = {
        "auto_trade_enabled": True,
        "min_confidence": 80,
        "max_trades_per_night": 5,
        "max_position_size": 1.0,
        "allowed_pairs": ["EUR/USD", "GBP/USD", "USD/JPY"],
        "allowed_risk_modes": ["NORMAL", "REDUCED"],
    }

    base_proposal = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "suggested_size": 1.0,
        "confidence": 85,
        "strategy": "Momentum_v1",
        "execution_allowed": True,
        "risk_mode": "NORMAL",
    }

    def case(name, proposal, settings, count, expect_allowed, expect_blocker=None):
        result = AutonomyGate.can_auto_execute(proposal, settings, count)
        ok = result["allowed"] == expect_allowed
        if expect_blocker is not None and not expect_allowed:
            ok = ok and not result["checks"].get(expect_blocker, True)
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {name}")
        if not ok:
            print(f"       got: {result}")

    # Happy path
    case("All checks pass", base_proposal, base_settings, 0, True)

    # auto_trade_enabled = False
    s = dict(base_settings); s["auto_trade_enabled"] = False
    case("Master switch OFF", base_proposal, s, 0, False, "auto_trade_enabled")

    # confidence below threshold
    p = dict(base_proposal); p["confidence"] = 70
    case("Confidence below min", p, base_settings, 0, False, "confidence")

    # confidence exactly at threshold should PASS (>=)
    p = dict(base_proposal); p["confidence"] = 80
    case("Confidence at exact threshold", p, base_settings, 0, True)

    # risk_mode disallowed
    p = dict(base_proposal); p["risk_mode"] = "AVOID"
    case("Risk mode AVOID rejected", p, base_settings, 0, False, "risk_mode")

    # risk_mode missing from proposal (current real-world case) -> pass
    p = dict(base_proposal); p.pop("risk_mode")
    case("Missing risk_mode passes (upstream-enforced)", p, base_settings, 0, True)

    # disallowed pair
    p = dict(base_proposal); p["pair"] = "AUD/USD"
    case("Pair not in allow-list", p, base_settings, 0, False, "pair_allowed")

    # size over cap
    p = dict(base_proposal); p["suggested_size"] = 1.5
    case("Size over cap", p, base_settings, 0, False, "size_allowed")

    # size at exact cap should pass
    p = dict(base_proposal); p["suggested_size"] = 1.0
    case("Size at exact cap passes", p, base_settings, 0, True)

    # zero size should fail
    p = dict(base_proposal); p["suggested_size"] = 0
    case("Zero size rejected", p, base_settings, 0, False, "size_allowed")

    # nightly limit hit
    case("Nightly limit exactly hit", base_proposal, base_settings, 5, False, "nightly_limit")

    # nightly limit one below
    case("Nightly limit one below", base_proposal, base_settings, 4, True)

    # execution_allowed = False
    p = dict(base_proposal); p["execution_allowed"] = False
    case("Execution not allowed", p, base_settings, 0, False, "execution_allowed")

    # multiple failures simultaneously
    p = dict(base_proposal); p["pair"] = "AUD/USD"; p["confidence"] = 50
    result = AutonomyGate.can_auto_execute(p, base_settings, 0)
    multi_ok = (not result["allowed"]
                and not result["checks"]["pair_allowed"]
                and not result["checks"]["confidence"])
    print(f"[{'PASS' if multi_ok else 'FAIL'}] Multiple failures surface together")

    # empty / malformed inputs should fail safely (not crash)
    result = AutonomyGate.can_auto_execute({}, {}, 0)
    safe_ok = result["allowed"] is False
    print(f"[{'PASS' if safe_ok else 'FAIL'}] Empty inputs fail safely (no crash)")

    print("\nDone.")


if __name__ == "__main__":
    _run_self_tests()
