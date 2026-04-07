import os
import sys
import time
import streamlit as st

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.persistent_trade_state_manager import PersistentTradeStateManager
from brokers.oanda_broker import OandaBroker

# Load .env
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ[key.strip()] = value.strip()

# --- Page Config ---
st.set_page_config(
    page_title="AegisFX Dashboard",
    layout="wide",
)

st.title("AegisFX Trading Dashboard")

# --- Connect to state ---
DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "dry_run_sustained.db",
)

state_manager = PersistentTradeStateManager(db_path=DB_PATH)
all_trades = state_manager.get_all_trades()
state_manager.close()

# --- Compute Metrics ---
total = len(all_trades)
filled = sum(1 for t in all_trades if t.get("status") == "FILLED")
failed = sum(1 for t in all_trades if t.get("status") == "FAILED")
pending = sum(1 for t in all_trades if t.get("status") == "PENDING")
failure_rate = (failed / total * 100) if total > 0 else 0.0

# --- Section A: Metrics ---
st.subheader("System Metrics")

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Total Trades", total)
col2.metric("Successful", filled)
col3.metric("Failed", failed)
col4.metric("Pending", pending)
col5.metric("Failure Rate", f"{failure_rate:.1f}%")

st.divider()

# --- Section B: Trade Table ---
st.subheader("Trade Ledger")

if all_trades:
    table_data = []
    for t in all_trades:
        table_data.append({
            "Request ID": t.get("request_id", ""),
            "Pair": t.get("currency_pair", ""),
            "Direction": t.get("direction", ""),
            "Size": t.get("position_size", ""),
            "Status": t.get("status", ""),
            "Exec Status": t.get("execution_status", ""),
            "Fill Price": t.get("fill_price", ""),
            "Created": t.get("created_at", ""),
        })
    st.dataframe(table_data, use_container_width=True)
else:
    st.info("No trades recorded yet. Start a dry run to see data.")

st.divider()

# --- Section C: Open Positions ---
st.subheader("Broker Open Positions")

try:
    broker = OandaBroker(
        api_key=os.getenv("OANDA_DEMO_API_KEY", ""),
        account_id=os.getenv("OANDA_ACCOUNT_ID", ""),
        base_url="https://api-fxpractice.oanda.com",
    )

    positions = broker.get_open_positions()
    balance = broker.get_account_balance()

    st.metric("Account Balance", f"${balance:,.2f}")

    if positions:
        pos_data = []
        for p in positions:
            pos_data.append({
                "Pair": p.get("currency_pair", ""),
                "Direction": p.get("direction", ""),
                "Units": p.get("units", ""),
                "Avg Price": p.get("average_price", ""),
                "Unrealized P&L": p.get("unrealized_pl", ""),
            })
        st.dataframe(pos_data, use_container_width=True)
    else:
        st.info("No open positions.")

except Exception as e:
    st.error(f"Broker connection error: {e}")

# --- Auto-refresh ---
time.sleep(2)
st.rerun()
