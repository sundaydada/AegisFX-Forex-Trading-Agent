import os
import sys
import time
import streamlit as st
import pandas as pd

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

# --- Connect to broker (used by multiple panels) ---
broker = None
balance = 0.0
positions = []
try:
    broker = OandaBroker(
        api_key=os.getenv("OANDA_DEMO_API_KEY", ""),
        account_id=os.getenv("OANDA_ACCOUNT_ID", ""),
        base_url="https://api-fxpractice.oanda.com",
    )
    balance = broker.get_account_balance()
    positions = broker.get_open_positions()
except Exception:
    pass

# --- P&L Panel (Top, Full Width, Dominant) ---
# NOTE: Dollar P&L sourced from broker until get_metrics() tracks dollar values.
# Broker data is supplemental — metrics remain source of truth for trade counts.
unrealized_pnl = sum(p.get("unrealized_pl", 0.0) for p in positions)
equity = balance + unrealized_pnl
peak_equity = max(equity, balance)
drawdown_pct = ((peak_equity - equity) / peak_equity * 100) if peak_equity > 0 else 0.0

# Action signal — drawdown overrides P&L (systemic health > current moment)
if drawdown_pct >= 4.0:
    signal_text = "DRAWDOWN WARNING"
    signal_color = "red"
    pnl_color = "#FF4444"
elif unrealized_pnl > 0:
    signal_text = "PROFIT"
    signal_color = "green"
    pnl_color = "#00CC66"
else:
    signal_text = "LOSS"
    signal_color = "orange"
    pnl_color = "#FFAA00"

pnl_sign = "+" if unrealized_pnl >= 0 else ""

st.markdown(
    f"""
    <div style="text-align:center; padding: 20px; border-radius: 10px;
                border: 2px solid {pnl_color}; margin-bottom: 20px;">
        <span style="background-color:{pnl_color}; color:white; padding:4px 16px;
                     border-radius:4px; font-size:14px; font-weight:bold;">
            {signal_text}
        </span>
        <div style="font-size:48px; font-weight:bold; color:{pnl_color}; margin:10px 0;">
            {pnl_sign}${unrealized_pnl:,.2f}
        </div>
        <div style="font-size:14px; color:#888;">Unrealized P&L</div>
    </div>
    """,
    unsafe_allow_html=True,
)

pnl_col1, pnl_col2, pnl_col3 = st.columns(3)
pnl_col1.metric("Account Balance", f"${balance:,.2f}")
pnl_col2.metric("Drawdown from Peak", f"{drawdown_pct:.2f}%")
pnl_col3.metric("Equity", f"${equity:,.2f}")

st.divider()

# --- Connect to state ---
DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "dry_run_sustained.db",
)

print("DB PATH:", os.path.abspath(DB_PATH))
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

# --- Section C: Performance Charts ---
st.subheader("Performance Charts")

if all_trades:
    chart_col1, chart_col2 = st.columns(2)

    # A. Trade Outcomes Bar Chart
    with chart_col1:
        st.caption("Trade Outcomes")
        outcomes_df = pd.DataFrame(
            [{"Success": filled, "Failed": failed}]
        )
        st.bar_chart(outcomes_df, use_container_width=True)

    # B. Trade Timeline Line Chart
    with chart_col2:
        st.caption("Trade Timeline (Cumulative)")

        # Sort trades by created_at and build cumulative count
        trades_with_time = [
            t for t in all_trades if t.get("created_at")
        ]
        trades_with_time.sort(key=lambda t: t["created_at"])

        if trades_with_time:
            timeline_data = []
            for i, t in enumerate(trades_with_time):
                timeline_data.append({
                    "Time": t["created_at"][:19],
                    "Cumulative Trades": i + 1,
                })

            timeline_df = pd.DataFrame(timeline_data)
            timeline_df["Time"] = pd.to_datetime(timeline_df["Time"])
            timeline_df = timeline_df.set_index("Time")
            st.line_chart(timeline_df, use_container_width=True)
        else:
            st.info("No timestamped trades yet.")
else:
    st.info("No trade data available for charts.")

st.divider()

# --- Section D: Current Positions (State Manager = source of truth) ---
st.subheader("Current Positions")

filled_trades = [t for t in all_trades if t.get("status") == "FILLED"]

if filled_trades:
    # Build broker lookup for live enrichment (pair+direction -> list of matches)
    broker_lookup = {}
    for p in positions:
        key = (p.get("currency_pair", ""), p.get("direction", ""))
        broker_lookup.setdefault(key, []).append(p)

    pos_data = []
    for t in filled_trades:
        pair = t.get("currency_pair", "")
        direction = t.get("direction", "")
        matches = broker_lookup.get((pair, direction), [])

        if len(matches) == 1:
            current_price = matches[0].get("average_price", "-")
            unrealized_pl = matches[0].get("unrealized_pl", "-")
        else:
            if len(matches) > 1:
                print(f"WARNING: Multiple broker positions for {pair} {direction}, skipping enrichment")
            current_price = "-"
            unrealized_pl = "-"

        pos_data.append({
            "Request ID": t.get("request_id", ""),
            "Pair": pair,
            "Direction": direction,
            "Units": t.get("position_size", t.get("units", "")),
            "Entry Price": t.get("fill_price", ""),
            "Current Price": current_price,
            "Unrealized P&L": unrealized_pl,
        })

    st.dataframe(pos_data, use_container_width=True)
else:
    st.info("No open positions.")

# --- Auto-refresh ---
time.sleep(2)
st.rerun()
