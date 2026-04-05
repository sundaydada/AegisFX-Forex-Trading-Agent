import os
import time
from brokers.oanda_broker import OandaBroker
from execution.persistent_trade_state_manager import PersistentTradeStateManager
from execution.trade_orchestrator import TradeOrchestrator
from execution.monitor import display_metrics
from market_data.alpha_vantage_price_feed import get_fx_price

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

state_manager = PersistentTradeStateManager(db_path="dry_run.db")
orchestrator = TradeOrchestrator(broker)

# Step 1: Reconcile any pending trades from previous runs
print("=== STARTUP RECONCILIATION ===")
orchestrator.reconcile_pending_trades(state_manager)

# Step 2: Check account
print(f"\n=== ACCOUNT BALANCE: ${broker.get_account_balance():.2f} ===")

# Step 3: Fetch live prices
print("\n=== LIVE PRICES ===")
pairs = ["EUR/USD", "GBP/USD", "USD/JPY"]
for pair in pairs:
    price_data = get_fx_price(pair)
    if "error" in price_data:
        print(f"  {pair}: ERROR - {price_data['error']}")
    else:
        print(f"  {pair}: {price_data['price']}")

# Step 4: Execute test trades (minimum size = 1 unit)
test_trades = [
    {
        "currency_pair": "EUR/USD",
        "direction": "Long",
        "approved_position_size": 1.0,
    },
    {
        "currency_pair": "GBP/USD",
        "direction": "Short",
        "approved_position_size": 1.0,
    },
]

print("\n=== EXECUTING TEST TRADES ===")
for i, trade in enumerate(test_trades):
    request_id = f"DRY-RUN-{i+1:03d}"

    result = orchestrator.process_trade(
        state_manager=state_manager,
        request_id=request_id,
        proposed_trade=trade,
        max_currency_exposure=10.0,
    )

    status = result["approval_status"]
    exec_result = result.get("execution_result", {})
    exec_status = exec_result.get("execution_status", "N/A") if exec_result else "N/A"

    print(f"  {request_id} | {trade['currency_pair']} {trade['direction']} | "
          f"Approval: {status} | Execution: {exec_status}")

# Step 5: Verify state
print("\n=== TRADE STATE ===")
all_trades = state_manager.get_all_trades()
for trade in all_trades:
    print(f"  {trade.get('request_id')} | {trade.get('currency_pair')} | "
          f"Status: {trade.get('status')}")

# Step 6: Check positions on broker
print("\n=== BROKER OPEN POSITIONS ===")
positions = broker.get_open_positions()
if not positions:
    print("  No open positions")
else:
    for pos in positions:
        print(f"  {pos['currency_pair']} {pos['direction']} | "
              f"Units: {pos['units']} | P&L: {pos['unrealized_pl']}")

# Step 7: Metrics
print()
display_metrics(orchestrator.get_metrics())

# Step 8: Idempotency check — re-run same trades
print("\n=== IDEMPOTENCY CHECK (re-running same request IDs) ===")
for i, trade in enumerate(test_trades):
    request_id = f"DRY-RUN-{i+1:03d}"
    result = orchestrator.process_trade(
        state_manager=state_manager,
        request_id=request_id,
        proposed_trade=trade,
        max_currency_exposure=10.0,
    )
    print(f"  {request_id}: {result['approval_status']} (should be cached, no broker call)")

print()
display_metrics(orchestrator.get_metrics())

state_manager.close()
print("\n=== DRY RUN COMPLETE ===")
