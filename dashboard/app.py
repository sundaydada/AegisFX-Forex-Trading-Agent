import os
import sys
import time
import streamlit as st
import pandas as pd

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.persistent_trade_state_manager import PersistentTradeStateManager
from execution.risk_exposure import compute_risk_exposure
from execution.trading_control import is_trading_enabled, set_trading_enabled
from execution.performance_metrics import compute_performance_metrics
from brokers.oanda_broker import OandaBroker

MAX_ALLOWED_EXPOSURE = 10.0

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
from brokers.broker_health import BrokerHealthMonitor
broker_health = BrokerHealthMonitor()
broker = None
balance = 0.0
positions = []
try:
    broker = OandaBroker(
        api_key=os.getenv("OANDA_DEMO_API_KEY", ""),
        account_id=os.getenv("OANDA_ACCOUNT_ID", ""),
        base_url="https://api-fxpractice.oanda.com",
        health=broker_health,
    )
    balance = broker.get_account_balance()
    positions = broker.get_open_positions()
except Exception:
    pass

broker_connected = broker_health.connected

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

# --- Performance KPIs ---
perf = compute_performance_metrics(all_trades)

kpi_col1, kpi_col2, kpi_col3, kpi_col4 = st.columns(4)
kpi_col1.metric("Closed Trades", perf["total_trades"] if perf["total_trades"] > 0 else "-")
kpi_col2.metric("Win Rate", f"{perf['win_rate']:.1f}%" if perf["total_trades"] > 0 else "-")
kpi_col3.metric("Total Profit", f"${perf['total_profit']:,.4f}" if perf["total_trades"] > 0 else "-")
kpi_col4.metric("Total Pips", f"{perf['total_pips']:.1f}" if perf["total_trades"] > 0 else "-")

st.divider()

# --- Section B: Current Positions + Risk Exposure (side-by-side) ---
filled_trades = [t for t in all_trades if t.get("status") == "FILLED"]

pos_col, risk_col = st.columns(2)

# --- Left: Current Positions ---
with pos_col:
    st.subheader("Current Positions")

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

        # Close All button
        if st.button("Close All Positions", type="primary"):
            close_errors = []
            for t in filled_trades:
                rid = t.get("request_id")
                pair = t.get("currency_pair", "")
                direction = t.get("direction", "")
                units = float(t.get("position_size", t.get("units", 0)))

                if not rid or not broker:
                    continue

                print(f"Closing position at broker: {rid} | {pair} {direction} {units}")
                result = broker.close_position(pair, units, direction)

                if result.get("status") == "SUCCESS":
                    print(f"Broker close SUCCESS: {rid}")
                    state_manager.close_trade(rid)
                    print(f"State updated to CLOSED: {rid}")
                else:
                    print(f"Broker close FAILED: {rid} — {result.get('reason')}")
                    close_errors.append(f"{pair}: {result.get('reason')}")

            if close_errors:
                for err in close_errors:
                    st.error(f"Close failed: {err}")
            else:
                st.rerun()

        # Per-pair close buttons
        open_pairs = sorted(set(
            t.get("currency_pair", "") for t in filled_trades
        ))

        if open_pairs:
            st.caption("Close by pair")
            btn_cols = st.columns(len(open_pairs))
            for i, pair_name in enumerate(open_pairs):
                with btn_cols[i]:
                    if st.button(f"Close {pair_name}", key=f"close_{pair_name}"):
                        close_errors = []
                        for t in filled_trades:
                            if t.get("currency_pair") != pair_name:
                                continue

                            rid = t.get("request_id")
                            direction = t.get("direction", "")
                            units = float(t.get("position_size", t.get("units", 0)))

                            if not rid or not broker:
                                continue

                            print(f"Closing position at broker: {rid} | {pair_name} {direction} {units}")
                            result = broker.close_position(pair_name, units, direction)

                            if result.get("status") == "SUCCESS":
                                print(f"Broker close SUCCESS: {rid}")
                                state_manager.close_trade(rid)
                                print(f"State updated to CLOSED: {rid}")
                            else:
                                print(f"Broker close FAILED: {rid} — {result.get('reason')}")
                                close_errors.append(f"{pair_name}: {result.get('reason')}")

                        if close_errors:
                            for err in close_errors:
                                st.error(f"Close failed: {err}")
                        else:
                            st.rerun()
    else:
        st.info("No open positions.")

# --- Right: Risk Exposure ---
with risk_col:
    st.subheader("Risk Exposure")

    net_exposure, total_exposure, utilization_pct = compute_risk_exposure(
        filled_trades, max_allowed_exposure=MAX_ALLOWED_EXPOSURE
    )

    # Action signal
    if utilization_pct > 90:
        risk_signal = "CRITICAL"
        risk_color = "#FF4444"
    elif utilization_pct > 70:
        risk_signal = "HIGH"
        risk_color = "#FF8800"
    elif utilization_pct > 40:
        risk_signal = "MEDIUM"
        risk_color = "#FFAA00"
    else:
        risk_signal = "LOW"
        risk_color = "#00CC66"

    st.markdown(
        f"""
        <span style="background-color:{risk_color}; color:white; padding:4px 12px;
                     border-radius:4px; font-size:14px; font-weight:bold;">
            {risk_signal}
        </span>
        """,
        unsafe_allow_html=True,
    )

    st.metric("Total Exposure", f"{total_exposure:.1f}")
    st.metric("Max Allowed", f"{MAX_ALLOWED_EXPOSURE:.1f}")
    st.metric("Utilization", f"{utilization_pct:.1f}%")

    if net_exposure:
        st.caption("Net Exposure by Currency")
        exposure_data = [{"Currency": k, "Exposure": v} for k, v in net_exposure.items()]
        st.dataframe(exposure_data, use_container_width=True)
    else:
        st.info("No exposure data.")

st.divider()

# --- Section C: AI Agreement + Alerts (side-by-side) ---
ai_col, alerts_col = st.columns(2)

# --- Left: AI Agreement ---
with ai_col:
    st.subheader("AI Agreement")

    # Mock data — replace with get_ai_state() when agents are live
    ai_state = {
        "regime": "Trending",
        "strategy": "Momentum_v1",
        "confidence": 72,
        "agents_agree": True,
    }

    # Action signal
    if ai_state["agents_agree"] and ai_state["confidence"] > 70:
        ai_signal = "STRONG"
        ai_color = "#00CC66"
    elif ai_state["agents_agree"] and ai_state["confidence"] < 50:
        ai_signal = "WEAK"
        ai_color = "#FFAA00"
    else:
        ai_signal = "DIVERGING"
        ai_color = "#FF4444"

    st.markdown(
        f"""
        <span style="background-color:{ai_color}; color:white; padding:4px 12px;
                     border-radius:4px; font-size:14px; font-weight:bold;">
            {ai_signal}
        </span>
        """,
        unsafe_allow_html=True,
    )

    st.metric("Current Regime", ai_state["regime"])
    st.metric("Active Strategy", ai_state["strategy"])
    st.metric("Model Confidence", f"{ai_state['confidence']}%")

# --- Right: Alerts / System Status ---
with alerts_col:
    st.subheader("Alerts / System Status")

    # Operator trading control
    trading_on = is_trading_enabled()
    new_state = st.toggle("Trading Enabled", value=trading_on, key="trading_toggle")
    if new_state != trading_on:
        set_trading_enabled(new_state)
        st.rerun()

    # Compute system health indicators
    total_trades = len(all_trades)
    failed_trades = sum(1 for t in all_trades if t.get("status") == "FAILED")
    pending_count = sum(1 for t in all_trades if t.get("status") == "PENDING")

    failure_threshold = 0.5
    min_trades_for_check = 3
    circuit_breaker_active = (
        total_trades >= min_trades_for_check
        and (failed_trades / total_trades) > failure_threshold
    ) if total_trades > 0 else False

    # Last successful trade timestamp
    filled_timestamps = [
        t.get("created_at", "") for t in all_trades if t.get("status") == "FILLED"
    ]
    last_success = max(filled_timestamps)[:19] if filled_timestamps else "None"

    # Rate limit — static max (dashboard doesn't share orchestrator state)
    max_trades_per_minute = 5
    rate_remaining = max(0, max_trades_per_minute - total_trades) if total_trades < max_trades_per_minute else 0

    # Panel background color — worst active condition wins
    if circuit_breaker_active or not broker_connected or not new_state:
        panel_bg = "#FF4444"
    elif pending_count > 0:
        panel_bg = "#FFAA00"
    else:
        panel_bg = "#00CC66"

    st.markdown(
        f"""
        <div style="background-color:{panel_bg}; color:white; padding:8px 12px;
                     border-radius:4px; font-size:14px; font-weight:bold; text-align:center;">
            {"SYSTEM ALERT" if panel_bg != "#00CC66" else "ALL CLEAR"}
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.metric("Circuit Breaker", "ACTIVE" if circuit_breaker_active else "INACTIVE")
    st.metric("Broker Connection", "CONNECTED" if broker_connected else "DISCONNECTED")
    st.metric("Pending Trades", pending_count)
    st.metric("Last Successful Trade", last_success)

    # Recent alerts — derived from current state
    alerts = []
    if not new_state:
        alerts.append("Trading DISABLED by operator")
    if circuit_breaker_active:
        alerts.append("Circuit breaker is ACTIVE — trading halted")
    if not broker_connected:
        alerts.append("Broker DISCONNECTED — no live data")
    if pending_count > 0:
        alerts.append(f"{pending_count} trade(s) stuck in PENDING")

    if alerts:
        st.caption("Active Alerts")
        for alert in alerts[-3:]:
            st.warning(alert)
    else:
        st.caption("No active alerts")

# --- Cleanup and auto-refresh ---
state_manager.close()
time.sleep(2)
st.rerun()
