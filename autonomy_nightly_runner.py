"""
AegisFX Autonomy Nightly Runner — Phase 4

Runs in the background during overnight FX sessions and automatically
executes only eligible APPROVED AI proposals through the
AutonomyExecutionBridge.

Run from project root:
    python autonomy_nightly_runner.py

The runner NEVER:
    - generates new AI proposals (the dashboard / market analysis service does that)
    - bypasses the approval queue (operator approval is still required upstream)
    - calls the broker directly (all trades route through TradeOrchestrator)
    - modifies orchestrator, risk, or dashboard code

The runner ONLY:
    - polls the queue for APPROVED proposals
    - delegates to AutonomyExecutionBridge for each cycle
    - respects autonomy_settings.json on every cycle
    - tracks how many AI trades have fired tonight
    - prints a per-cycle summary and a graceful shutdown report
"""

import os
import time
from datetime import datetime, timezone

# --- Load .env (same pattern as dry_run_sustained.py) ---
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip()

from brokers.oanda_broker import OandaBroker
from brokers.broker_health import BrokerHealthMonitor
from execution.persistent_trade_state_manager import PersistentTradeStateManager
from execution.startup_logging import log_db_path_once
from execution.trade_orchestrator import TradeOrchestrator
from execution.autonomy_execution_bridge import AutonomyExecutionBridge
from ai.proposal_approval_queue import ProposalApprovalQueue


# --- Configuration ---
CYCLE_SECONDS = 300  # 5 minutes between cycles
DB_PATH = "dry_run_sustained.db"
APPROVAL_DB_PATH = "proposal_approvals.db"
AUTONOMY_SETTINGS_PATH = "autonomy_settings.json"
MAX_CURRENCY_EXPOSURE = 100.0


def _today_utc_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# Trade statuses that represent a real position-opening event.
# FILLED  = trade is currently open at the broker.
# CLOSED  = trade opened and was subsequently closed — it still
#           consumed a nightly slot when it opened.
#
# Excluded by design:
#   FAILED    -> broker rejected or errored, no position was ever opened
#   PENDING   -> in-flight, broker has not confirmed; if it lingers,
#                crash recovery / freshness handle it
#   (any other transient/error state)
_NIGHTLY_QUOTA_STATUSES = ("FILLED", "CLOSED")


def _count_ai_trades_filled_tonight(state_manager) -> int:
    """
    Count AI-driven trades that actually OPENED A POSITION today (UTC).

    The nightly cap is a position-opening budget, not a broker-attempt
    budget. A trade qualifies only if all three are true:
        - request_id starts with "AI-PROPOSAL-"
        - created_at is today (UTC)
        - status is FILLED or CLOSED

    FAILED, PENDING, and other transient states are not counted —
    they did not consume the operator's risk budget.
    """
    today = _today_utc_date()
    all_trades = state_manager.get_all_trades()
    count = 0
    for t in all_trades:
        rid = str(t.get("request_id", ""))
        created = str(t.get("created_at", ""))
        status = t.get("status", "")
        if not rid.startswith("AI-PROPOSAL-"):
            continue
        if not created.startswith(today):
            continue
        if status not in _NIGHTLY_QUOTA_STATUSES:
            continue
        count += 1
    return count


def _print_header():
    print("=" * 70)
    print("AegisFX Autonomy Nightly Runner")
    print(f"Started: {datetime.now(timezone.utc).isoformat()}")
    print(f"Cycle interval: {CYCLE_SECONDS} seconds")
    print(f"Settings file: {AUTONOMY_SETTINGS_PATH}")
    print(f"Trade ledger:  {DB_PATH}")
    print(f"Approval DB:   {APPROVAL_DB_PATH}")
    print("Press Ctrl+C to stop")
    print("=" * 70)


def main():
    # --- Broker setup (demo account only) ---
    broker_health = BrokerHealthMonitor()
    broker = OandaBroker(
        api_key=os.getenv("OANDA_DEMO_API_KEY", ""),
        account_id=os.getenv("OANDA_ACCOUNT_ID", ""),
        base_url="https://api-fxpractice.oanda.com",
        health=broker_health,
    )

    state_manager = PersistentTradeStateManager(db_path=DB_PATH)
    log_db_path_once("autonomy_nightly_runner", DB_PATH)

    orchestrator = TradeOrchestrator(broker)
    autonomy_bridge = AutonomyExecutionBridge(settings_path=AUTONOMY_SETTINGS_PATH)

    _print_header()

    # Counters for shutdown report
    total_cycles = 0
    total_executed = 0
    total_skipped = 0
    started_at = time.time()

    try:
        while True:
            total_cycles += 1
            now_utc = datetime.now(timezone.utc).isoformat()

            print(f"\n[cycle {total_cycles}] {now_utc}")

            # --- Pull current APPROVED proposals ---
            try:
                queue = ProposalApprovalQueue(db_path=APPROVAL_DB_PATH)
                approved_proposals = queue.get_approved_proposals()
                queue.close()
            except Exception as e:
                print(f"  ERROR loading approval queue: {e}")
                approved_proposals = []

            # --- Count AI trades that actually opened a position today ---
            try:
                nightly_count = _count_ai_trades_filled_tonight(state_manager)
            except Exception as e:
                print(f"  ERROR counting nightly trades: {e}")
                nightly_count = 0

            print(f"  proposals checked: {len(approved_proposals)}")
            print(f"  AI trades today:   {nightly_count}")

            if not approved_proposals:
                print("  no APPROVED proposals — nothing to do this cycle")
            else:
                # --- Delegate to bridge ---
                try:
                    result = autonomy_bridge.auto_execute_eligible_proposals(
                        proposals=approved_proposals,
                        orchestrator=orchestrator,
                        nightly_trade_count=nightly_count,
                        state_manager=state_manager,
                        max_currency_exposure=MAX_CURRENCY_EXPOSURE,
                    )
                except Exception as e:
                    print(f"  ERROR in autonomy bridge: {e}")
                    result = {"executed": [], "skipped": []}

                executed = result.get("executed", [])
                skipped = result.get("skipped", [])
                total_executed += len(executed)
                total_skipped += len(skipped)

                print(f"  executed this cycle: {len(executed)}")
                for e in executed:
                    success = e.get("result", {}).get("success", False)
                    msg = e.get("result", {}).get("message", "")
                    flag = "OK" if success else "FAIL"
                    print(f"    [{flag}] {e.get('proposal_id', '')} :: {msg}")

                print(f"  skipped this cycle: {len(skipped)}")
                for s in skipped:
                    print(f"    [SKIP] {s.get('proposal_id', '')} :: {s.get('reason', '')}")

                # Mark successfully-executed proposals as EXECUTED in the queue
                # so the dashboard reflects the change and the same proposal
                # is not picked up again.
                if executed:
                    try:
                        queue = ProposalApprovalQueue(db_path=APPROVAL_DB_PATH)
                        for e in executed:
                            if e.get("result", {}).get("success"):
                                queue.mark_executed(e.get("proposal_id", ""))
                        queue.close()
                    except Exception as e:
                        print(f"  WARNING: failed to mark proposals EXECUTED: {e}")

            # --- Sleep until next cycle ---
            print(f"  next cycle in {CYCLE_SECONDS}s...")
            time.sleep(CYCLE_SECONDS)

    except KeyboardInterrupt:
        print("\n\n--- Operator stop (Ctrl+C) ---")

    # --- Shutdown summary ---
    elapsed_min = (time.time() - started_at) / 60
    print("\n" + "=" * 70)
    print("Shutdown Report")
    print("=" * 70)
    print(f"Total runtime:    {elapsed_min:.1f} minutes")
    print(f"Total cycles:     {total_cycles}")
    print(f"Total executed:   {total_executed}")
    print(f"Total skipped:    {total_skipped}")
    print(f"Final UTC time:   {datetime.now(timezone.utc).isoformat()}")
    print("=" * 70)

    try:
        state_manager.close()
        print("State manager closed cleanly.")
    except Exception as e:
        print(f"WARNING: state manager close failed: {e}")


if __name__ == "__main__":
    main()
