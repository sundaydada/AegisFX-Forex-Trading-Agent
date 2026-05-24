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
from execution.performance_metrics import compute_performance_metrics, compute_daily_performance
from brokers.oanda_broker import OandaBroker
from ai.market_analysis_service import MarketAnalysisService
from ai.ai_analysis_history import AIAnalysisHistoryManager
from ai.strategy_recommendation_service import StrategyRecommendationService
from ai.regime_transition_tracker import RegimeTransitionTracker
from market_data.alpha_vantage_price_feed import get_fx_price

MAX_ALLOWED_EXPOSURE = 10.0


@st.cache_data(ttl=30)
def cached_ai_analysis(market_data_json: str) -> dict:
    """Cache AI analysis for 30 seconds keyed by market data JSON."""
    import json
    service = MarketAnalysisService()
    return service.analyze_market_context(json.loads(market_data_json))


@st.cache_data(ttl=30)
def cached_market_prices(pairs: tuple) -> dict:
    """Cache live price fetches for 30 seconds."""
    prices = {}
    for pair in pairs:
        price_data = get_fx_price(pair)
        if "error" not in price_data:
            prices[pair] = price_data.get("price", 0.0)
    return prices

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

# --- Daily Performance ---
st.subheader("Daily Performance (Consistency View)")

daily_metrics = compute_daily_performance(all_trades)

if daily_metrics:
    daily_table = []
    for day in daily_metrics:
        daily_table.append({
            "Date": day["date"],
            "Trades": day["trades"],
            "Win %": f"{day['win_rate']:.1f}%",
            "Pips": f"{day['pips']:.4f}",
            "Profit ($)": f"{day['profit']:.2f}",
        })
    st.dataframe(daily_table, use_container_width=True)
else:
    st.info("No closed trades available yet.")

st.divider()

# --- Equity Curve ---
st.subheader("Equity Curve")

equity_trades = [t for t in all_trades if t.get("status") == "CLOSED" and t.get("closed_at")]
equity_trades.sort(key=lambda t: t.get("closed_at", ""))

if equity_trades:
    cumulative = 0.0
    curve_data = []
    for t in equity_trades:
        entry_price = float(t.get("fill_price", 0.0))
        close_price = float(t.get("close_price", 0.0))
        direction = t.get("direction", "")
        size = float(t.get("position_size", t.get("units", 0)))

        if direction == "Long":
            pip_diff = close_price - entry_price
        elif direction == "Short":
            pip_diff = entry_price - close_price
        else:
            pip_diff = 0.0

        cumulative += pip_diff * size
        curve_data.append({
            "Time": t["closed_at"][:19],
            "Cumulative Profit": round(cumulative, 4),
        })

    curve_df = pd.DataFrame(curve_data)
    curve_df["Time"] = pd.to_datetime(curve_df["Time"])
    curve_df = curve_df.set_index("Time")
    st.line_chart(curve_df, use_container_width=True)
else:
    st.info("No equity curve data available yet.")

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

# --- Section C: AI Agreement (full width) ---

class _NullCtx:
    def __enter__(self): return self
    def __exit__(self, *a): return False

ai_col = _NullCtx()
alerts_col = _NullCtx()

# --- AI Agreement ---
with ai_col:
    st.subheader("AI Agreement")

    # Build placeholder market context (trend/volatility heuristics for now)
    import json as _json
    pairs_to_analyze = ("EUR/USD", "GBP/USD", "USD/JPY")
    live_prices = cached_market_prices(pairs_to_analyze)

    market_data = {}
    for pair in pairs_to_analyze:
        price = live_prices.get(pair, 0.0)
        if price <= 0:
            continue
        # Placeholder heuristics — replace with real indicators later
        market_data[pair] = {
            "price": price,
            "trend": "up",
            "volatility": "medium",
        }

    if market_data:
        ai_state = cached_ai_analysis(_json.dumps(market_data, sort_keys=True))
    else:
        ai_state = {"regime": "UNKNOWN", "summary": "No market data", "confidence": 0, "pair_analysis": {}}

    # Record analysis history (observational only — never read by execution)
    try:
        history_mgr = AIAnalysisHistoryManager(db_path="ai_analysis_history.db")
        history_mgr.record_analysis(ai_state)
        history_mgr.close()
    except Exception as e:
        print(f"WARNING: Failed to record AI analysis history: {e}")

    # Track regime transitions (observational only)
    try:
        transition_tracker = RegimeTransitionTracker(db_path="regime_transitions.db")
        _regime = ai_state.get("regime", "UNKNOWN")
        _confidence = int(ai_state.get("confidence", 0))
        transition_tracker.record_regime(_regime, _confidence)
        transition_tracker.close()
    except Exception as e:
        print(f"WARNING: Failed to record regime transition: {e}")

    regime = ai_state.get("regime", "UNKNOWN")
    summary = ai_state.get("summary", "")
    confidence = int(ai_state.get("confidence", 0))
    pair_analysis = ai_state.get("pair_analysis", {})

    # Action signal — agents_agree is True if regime is known
    agents_agree = regime != "UNKNOWN"
    if agents_agree and confidence > 70:
        ai_signal = "STRONG"
        ai_color = "#00CC66"
    elif agents_agree and confidence < 50:
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

    if regime == "UNKNOWN":
        st.warning("AI analysis unavailable")

    st.metric("Current Regime", regime)
    st.metric("Model Confidence", f"{confidence}%")

    # Deterministic strategy recommendation
    recommendation = StrategyRecommendationService.recommend_strategy(ai_state)

    risk_mode = recommendation["risk_mode"]
    if risk_mode == "NORMAL":
        risk_mode_color = "#00CC66"
    elif risk_mode == "REDUCED":
        risk_mode_color = "#FFAA00"
    else:
        risk_mode_color = "#FF4444"

    exec_color = "#00CC66" if recommendation["execution_allowed"] else "#FF4444"
    exec_text = "ALLOWED" if recommendation["execution_allowed"] else "BLOCKED"

    st.markdown(
        f"""
        <div style="margin-top:8px;">
            <span style="background-color:{risk_mode_color}; color:white; padding:3px 10px;
                         border-radius:4px; font-size:12px; font-weight:bold; margin-right:6px;">
                RISK: {risk_mode}
            </span>
            <span style="background-color:{exec_color}; color:white; padding:3px 10px;
                         border-radius:4px; font-size:12px; font-weight:bold;">
                EXEC: {exec_text}
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.metric("Recommended Strategy", recommendation["recommended_strategy"])
    st.metric("Trade Bias", recommendation["trade_bias"])
    st.caption(f"Reason: {recommendation['reason']}")

    if summary:
        st.caption("AI Summary")
        st.write(summary)

    if pair_analysis:
        st.caption("Per-Pair Analysis")
        for pair, note in pair_analysis.items():
            st.markdown(f"**{pair}** — {note}")

st.divider()

# --- AI Confidence Trend ---
st.subheader("AI Confidence Trend")

try:
    history_mgr = AIAnalysisHistoryManager(db_path="ai_analysis_history.db")
    confidence_trend = history_mgr.get_confidence_trend(limit=100)
    recent_analyses = history_mgr.get_recent_analysis(limit=5)
    history_mgr.close()
except Exception as e:
    print(f"WARNING: Failed to load AI analysis history: {e}")
    confidence_trend = []
    recent_analyses = []

if confidence_trend:
    trend_df = pd.DataFrame(confidence_trend)
    trend_df["timestamp"] = pd.to_datetime(trend_df["timestamp"])
    trend_df = trend_df.set_index("timestamp")
    st.line_chart(trend_df, use_container_width=True)

    if recent_analyses:
        st.caption("Recent Regime Changes & Summaries")
        recent_rows = []
        for r in recent_analyses:
            recent_rows.append({
                "Time": r["timestamp"][:19],
                "Regime": r["regime"],
                "Confidence": f"{r['confidence']}%",
                "Summary": r["summary"],
            })
        st.dataframe(recent_rows, use_container_width=True)
else:
    st.info("No AI analysis history available yet.")

st.divider()

# --- Alerts / System Status ---
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

    # --- Recent Regime Changes ---
    st.caption("Recent Regime Changes")
    try:
        transition_tracker = RegimeTransitionTracker(db_path="regime_transitions.db")
        recent_transitions = transition_tracker.get_recent_transitions(limit=5)
        transition_tracker.close()
    except Exception as e:
        print(f"WARNING: Failed to load regime transitions: {e}")
        recent_transitions = []

    if recent_transitions:
        for t in recent_transitions:
            arrow_text = f"{t['from_regime']} → {t['to_regime']}"
            ts_short = t["timestamp"][:19]
            to_regime = t["to_regime"]

            if to_regime == "Risk-Off":
                st.error(f"{arrow_text}  |  {ts_short}")
            elif to_regime == "Volatile":
                st.warning(f"{arrow_text}  |  {ts_short}")
            else:
                st.info(f"{arrow_text}  |  {ts_short}")
    else:
        st.write("_No regime changes recorded yet._")

# --- Cleanup and auto-refresh ---
state_manager.close()
time.sleep(2)
st.rerun()
