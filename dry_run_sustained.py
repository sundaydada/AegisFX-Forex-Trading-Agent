import os
import time
import random
import uuid
from datetime import datetime, timezone
from brokers.oanda_broker import OandaBroker
from execution.persistent_trade_state_manager import PersistentTradeStateManager
from execution.trade_orchestrator import TradeOrchestrator
from execution.monitor import display_metrics
from market_data.alpha_vantage_price_feed import get_fx_price

# --- Configuration ---
TRADE_INTERVAL_SECONDS = 300  # 5 minutes between trades
DB_PATH = "dry_run_sustained.db"

PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY"]
DIRECTIONS = ["Long", "Short"]

# Load .env
with open(".env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ[key.strip()] = value.strip()

# Initialize system — DEMO ONLY
broker = OandaBroker(
    api_key=os.getenv("OANDA_DEMO_API_KEY"),
    account_id=os.getenv("OANDA_ACCOUNT_ID"),
    base_url="https://api-fxpractice.oanda.com",
)

print("DB PATH:", os.path.abspath(DB_PATH))
state_manager = PersistentTradeStateManager(db_path=DB_PATH)
orchestrator = TradeOrchestrator(broker)

# Startup
print("=" * 60)
print("AegisFX CONTINUOUS TRADING SYSTEM")
print(f"Trade interval: {TRADE_INTERVAL_SECONDS} seconds")
print(f"Started: {datetime.now(timezone.utc).isoformat()}")
print("Press Ctrl+C to stop")
print("=" * 60)

# Reconcile any pending trades from previous runs
print("\n--- Startup Reconciliation ---")
orchestrator.reconcile_pending_trades(state_manager)

print(f"\nAccount Balance: ${broker.get_account_balance():.2f}")

start_time = time.time()
trade_counter = 0
cycle = 0

try:
    while True:
        cycle += 1
        elapsed_minutes = (time.time() - start_time) / 60

        print(f"\n{'=' * 60}")
        print(f"CYCLE {cycle} | Elapsed: {elapsed_minutes:.1f}m | {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC")
        print(f"{'=' * 60}")

        # Fetch live prices
        print("\n--- Live Prices ---")
        for pair in PAIRS:
            price_data = get_fx_price(pair)
            if "error" in price_data:
                print(f"  {pair}: ERROR - {price_data['error']}")
            else:
                print(f"  {pair}: {price_data['price']}")

        # Pick a random trade
        pair = random.choice(PAIRS)
        direction = random.choice(DIRECTIONS)
        trade_counter += 1
        request_id = f"SUSTAINED-{uuid.uuid4()}"

        proposed_trade = {
            "currency_pair": pair,
            "direction": direction,
            "approved_position_size": 1.0,
        }

        print(f"\n--- Executing Trade ---")
        print(f"  {request_id} | {pair} {direction} | Size: 1 unit")

        result = orchestrator.process_trade(
            state_manager=state_manager,
            request_id=request_id,
            proposed_trade=proposed_trade,
            max_currency_exposure=10.0,
        )

        exec_result = result.get("execution_result", {})
        exec_status = exec_result.get("execution_status", "N/A") if exec_result else "N/A"
        print(f"  Result: {result['approval_status']} | Execution: {exec_status}")

        # Dashboard
        print(f"\n--- Dashboard ---")
        display_metrics(orchestrator.get_metrics())

        # Open positions
        print(f"\n--- Open Positions ---")
        try:
            positions = broker.get_open_positions()
            if not positions:
                print("  None")
            else:
                for pos in positions:
                    print(f"  {pos['currency_pair']} {pos['direction']} | "
                          f"Units: {pos['units']} | P&L: {pos['unrealized_pl']}")
        except Exception as e:
            print(f"  Error fetching positions: {e}")

        # Account balance
        try:
            balance = broker.get_account_balance()
            print(f"\n  Account Balance: ${balance:.2f}")
        except Exception as e:
            print(f"\n  Balance error: {e}")

        # Trade state summary
        all_trades = state_manager.get_all_trades()
        filled = sum(1 for t in all_trades if t.get("status") == "FILLED")
        failed = sum(1 for t in all_trades if t.get("status") == "FAILED")
        pending = sum(1 for t in all_trades if t.get("status") == "PENDING")
        closed = sum(1 for t in all_trades if t.get("status") == "CLOSED")
        print(f"  Trade Ledger: {len(all_trades)} total | "
              f"{filled} filled | {failed} failed | {pending} pending | {closed} closed")

        # Wait for next cycle
        print(f"\n  Next trade in {TRADE_INTERVAL_SECONDS} seconds...")
        time.sleep(TRADE_INTERVAL_SECONDS)

except KeyboardInterrupt:
    print("\n\n--- System stopped by operator (Ctrl+C) ---")

# Final report
print(f"\n{'=' * 60}")
print("AegisFX SHUTDOWN REPORT")
print(f"{'=' * 60}")
print(f"Total runtime: {(time.time() - start_time) / 60:.1f} minutes")
print(f"Trades attempted: {trade_counter}")
display_metrics(orchestrator.get_metrics())

all_trades = state_manager.get_all_trades()
filled = sum(1 for t in all_trades if t.get("status") == "FILLED")
failed = sum(1 for t in all_trades if t.get("status") == "FAILED")
pending = sum(1 for t in all_trades if t.get("status") == "PENDING")
closed = sum(1 for t in all_trades if t.get("status") == "CLOSED")
print(f"\nFinal Ledger: {len(all_trades)} total | "
      f"{filled} filled | {failed} failed | {pending} pending | {closed} closed")

try:
    print(f"Final Balance: ${broker.get_account_balance():.2f}")
except Exception:
    pass

print(f"\nOutstanding PENDING trades: {pending}")
if pending == 0:
    print("INTEGRITY CHECK PASSED: All trades resolved")
else:
    print("INTEGRITY CHECK FAILED: Unresolved trades detected")

state_manager.close()
print("\nClean shutdown complete.")
