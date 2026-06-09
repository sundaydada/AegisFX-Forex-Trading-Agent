# AegisFX — Architecture Summary

A reference document for explaining the AegisFX system to managers, investors, and technical reviewers.

---

## What The System Does

AegisFX is an AI-assisted forex trading system that uses live market data, AI regime analysis, and deterministic risk governance to surface trade ideas, allow a human operator to approve them, and execute them on the OANDA brokerage. It is designed to never act autonomously — every trade requires explicit human approval.

The system produces three operational outputs:

1. **A live operator dashboard** showing portfolio state, AI signals, risk exposure, and system health
2. **A real trading account** on OANDA's demo platform that executes approved trades
3. **Investor-grade performance reports** (CSV and HTML/PDF) summarizing trading and AI performance

---

## Main Architecture Layers

| # | Layer | Purpose |
|---|-------|---------|
| 1 | External Data Sources | Alpha Vantage, OpenAI, OANDA — the three external services the system depends on |
| 2 | Market Data Layer | Converts raw price candles into trend, volatility, and range-percentile signals |
| 3 | AI Intelligence Layer | Sends market context to OpenAI, classifies the regime, and applies deterministic strategy rules to produce trade proposals |
| 4 | Execution and Safety Layer | Wraps every trade attempt in idempotency, rate limiting, circuit breaker, position netting, risk evaluation, broker health, and operator toggle gates |
| 5 | Broker Integration | OANDA REST client for placing market orders, closing positions, and reading order status |
| 6 | Persistence Layer | Four SQLite databases — trade ledger, AI history, proposal queue, regime transitions — provide durable state across restarts |
| 7 | Dashboard / Operator Layer | Streamlit-based real-time UI with eleven panels covering portfolio, risk, AI, alerts, and reporting |
| 8 | Reporting / Investor Layer | CSV and HTML export functions consolidating closed-trade and AI performance into investor-grade documents |
| 9 | External Outputs | What stakeholders actually see and use |

---

## What Is Already Built

### Core Trading Pipeline
- ✅ Trade orchestrator with full lifecycle management (PENDING → FILLED / FAILED / CLOSED)
- ✅ Idempotency check (prevents duplicate execution)
- ✅ Crash recovery (resolves pending trades after restart)
- ✅ Rate limiter (100 trades per minute cap)
- ✅ Circuit breaker (auto-halt when failure rate exceeds threshold)
- ✅ Portfolio risk evaluator (per-currency and total exposure caps)
- ✅ Position netting (FIFO close of opposite positions before opening new)
- ✅ Broker health monitor (system-wide DISCONNECTED state)
- ✅ Trading ON/OFF operator toggle (file-flag, persistent across restarts)

### Broker Integration
- ✅ OANDA REST API client (auth, balance, positions, order placement, order status, close)
- ✅ Live OANDA demo account connected with $100,000 starting balance
- ✅ Real trades placed and closed on the practice broker

### Market Data Integration
- ✅ Alpha Vantage realtime FX prices and intraday candles
- ✅ Cached 5-minute interval fetching with rate-limit-friendly TTL
- ✅ Computed trend, volatility, range percentile, and position-in-range (UPPER / MIDDLE / LOWER)

### AI Layer
- ✅ OpenAI integration (model: gpt-4.1-mini) for market regime classification
- ✅ Deterministic strategy recommendation rules (Trending, Ranging, Volatile, Risk-On, Risk-Off)
- ✅ Range-aware mean reversion for Ranging regime (UPPER → SHORT, LOWER → LONG, MIDDLE → NEUTRAL)
- ✅ Trade proposal service producing pair-specific suggestions
- ✅ Persistent approval queue with deterministic proposal IDs
- ✅ Proposal execution bridge (manual operator click → orchestrator)
- ✅ AI analysis history database (every AI call recorded)
- ✅ Regime transition tracker (records every regime change)

### Dashboard
- ✅ P&L panel (dominant, color-coded action signal)
- ✅ Performance KPIs (closed trades, win rate, total profit, total pips)
- ✅ Daily Performance consistency table
- ✅ Equity curve chart
- ✅ Current Positions table with state-level and broker-level close
- ✅ Risk Exposure panel with LOW / MEDIUM / HIGH / CRITICAL signal
- ✅ AI Agreement panel (live regime, strategy, confidence, summary, per-pair analysis)
- ✅ AI Trade Proposals panel
- ✅ AI Approval Queue with Approve / Reject / Execute buttons
- ✅ AI Proposal Analytics with approval funnel
- ✅ Strategy Attribution by Regime (expandable per-regime sections)
- ✅ AI Recommendation Accuracy panel
- ✅ AI Confidence Trend chart with recent regime change log
- ✅ Alerts / System Status (circuit breaker, broker connection, pending trades, last successful trade)

### Reporting
- ✅ CSV investor report export (consolidated 4-section)
- ✅ HTML browser-printable PDF-style investor report
- ✅ Daily consistency view
- ✅ Strategy attribution analytics
- ✅ Recommendation accuracy analytics

---

## What Is Still Being Validated

The infrastructure is complete. What we still need to *prove* is whether the AI adds positive trading edge.

| Item | Status |
|------|--------|
| Whether AI-driven trades beat baseline (random) trades | Not yet measurable — no AI trades have closed |
| Whether strategy attribution per regime is stable | Needs more executed AI trades to populate |
| Whether recommendation accuracy improves over time | Empty until AI trades close |
| Whether range-aware mean reversion produces expected signals during Ranging periods | Just deployed; awaiting first market window |
| Whether the system survives extended (multi-day) continuous operation | Not yet stress-tested |
| Whether operator workflow is ergonomic during high-activity periods | Untested during volatile regimes |

The diagnostic from the last session showed 1,887 consecutive Ranging classifications with zero proposals generated. The range-aware fix just deployed will produce proposals at range extremes, which is the next data point needed to evaluate AI quality.

---

## Why Alpha Vantage Is Currently Needed

Alpha Vantage is the system's source of raw price data. Without it, the system cannot:

- Compute trend (we need recent closes)
- Compute volatility (we need recent OHLC ranges)
- Compute position-in-range (we need a 20-period high/low window)
- Provide OpenAI with quantitative market context — OpenAI on its own does not have realtime FX prices

OANDA also provides prices, but its REST API is more rate-limited for tick streaming and is not designed for historical candle queries. Alpha Vantage gives us 100+ recent candles in one call.

**If Alpha Vantage were removed**, the AI Agreement panel would have no input, no proposals would be generated, and the system would be operationally inactive even with everything else working.

**Long term**, the same data can be sourced from OANDA's pricing endpoints (also free on the practice account) or from a paid feed like Polygon, EOD Historical Data, or directly from a broker's streaming API. Alpha Vantage was chosen because it has a generous free tier suitable for development and demo.

---

## Why OpenAI Is Needed

OpenAI provides the *interpretation* layer between raw market numbers and a strategy decision.

Without OpenAI, the system would still work end-to-end — but the AI Agreement panel, regime classification, per-pair analysis, and AI Confidence Trend would all be empty or fall back to UNKNOWN. The strategy recommendation engine would have no input to act on.

OpenAI specifically contributes:

- **Regime classification** ("Trending", "Ranging", "Volatile", "Risk-On", "Risk-Off") from quantitative inputs
- **Confidence scoring** (0–100%) for the classification
- **Per-pair narrative analysis** ("EUR/USD shows flat trend and low volatility...")
- **Cross-pair synthesis** (a one-sentence market summary)

The alternative — building these classifiers ourselves with classical models — would require significant feature engineering and labeled training data. OpenAI provides a zero-training baseline.

**Cost note:** OpenAI calls are cached for 30 seconds; the dashboard refreshes every 2 seconds. Worst-case API consumption is about 2 calls per minute per dashboard session — on the order of cents per hour at gpt-4.1-mini pricing.

**If we wanted to remove OpenAI** later, we would substitute a deterministic regime classifier (moving-average crossovers, ATR breakout, ADX threshold). The strategy recommendation, proposal generation, and execution layers would not need changes — they consume a structured dict, not OpenAI-specific output.

---

## Current MVP Status

| Component | Status |
|-----------|--------|
| End-to-end trade execution on demo broker | Working |
| AI signal generation | Working |
| Human-in-the-loop approval workflow | Working |
| Deterministic safety gates | Working |
| Persistent state | Working |
| Real-time operator dashboard | Working |
| Investor reporting (CSV + HTML) | Working |
| Crash recovery | Working |
| Range-aware mean reversion | Just deployed |
| Live AI trading validation | Pending market window |
| Multi-day continuous run validation | Pending |
| Real-money deployment | Not approved |

**Verdict:** The system is at MVP for the demo platform. It is **not** at production grade — the deployment checklist still requires extended live validation, reconciliation testing, and a documented promotion path before real capital is at risk.

---

## Demo Talking Points

These are the points to emphasize when presenting the system to Ali (or any non-engineer stakeholder).

### Opening
- **"AegisFX is a working forex trading system built around a strict principle: the AI suggests, deterministic code decides, and a human must approve every trade. We've spent the last several weeks building this end-to-end."**

### What's Working
- The full pipeline is live: market data → AI → strategy → proposal → human approval → safety gates → broker execution → state persistence → dashboard → investor reports.
- Real trades have been placed and closed on the OANDA demo account.
- The dashboard shows every panel a trading operator needs: P&L, positions, risk exposure, AI signal, approval queue, alerts.

### Architecture Highlights
- **Two human clicks separate the AI from any trade.** No autonomous execution ever happens. The AI cannot place a trade on its own — by design.
- **Every trade attempt passes seven deterministic gates** (idempotency, rate limit, circuit breaker, netting, risk, broker health, trading toggle) before reaching the broker.
- **State persists in SQLite** so a crash or restart doesn't lose trades, and reconciliation resolves stuck PENDING entries on the next startup.
- **The AI is contained.** It produces structured output that deterministic code consumes. If OpenAI hallucinates, our rule engine rejects illegal regimes silently. If OpenAI is down, the system shows UNKNOWN and refuses to trade.

### Honest Numbers
- **37 closed trades** in the ledger. Most are from random-trade testing during early development, not AI-driven.
- **43.2% win rate**, **-$312 cumulative** at this point — the baseline before AI trading is fully validated.
- **0 AI-driven trades have closed yet.** That's the gap we're now in a position to close.

### What's Next
- Run live during active forex market hours to surface real AI proposals from the just-deployed range-aware strategy.
- Approve high-confidence proposals, reject low-confidence ones, let trades close, and measure recommendation accuracy.
- After several AI trade cycles, the Strategy Attribution and Recommendation Accuracy panels become meaningful, and we can finally answer: **does the AI add edge?**

### Risks To Flag
- Currently a single-strategy system — when the AI calls Ranging, we depend entirely on mean reversion working. Trending and Volatile regimes have not yet appeared in the AI history, so those code paths are unproven against real market events.
- Free-tier external dependencies (Alpha Vantage 5/min, OANDA practice account) impose limits suitable for development but not for production scaling.
- No real-money deployment until the deployment checklist is fully green — extended dry runs, reconciliation tests, and a documented escalation procedure for incidents.

### Closing
- **"The infrastructure is done. The AI is integrated. The dashboard is shippable. What we don't yet have is data that proves the AI adds positive trading edge — and the system is now positioned to generate that data."**

---

## File Inventory (For Reference)

| Path | Purpose |
|------|---------|
| `market_data/alpha_vantage_price_feed.py` | Realtime + intraday price fetch |
| `market_data/market_context.py` | Trend, volatility, range-percentile computation |
| `ai/openai_config.py` | OpenAI API key management |
| `ai/market_analysis_service.py` | OpenAI regime classifier |
| `ai/strategy_recommendation_service.py` | Deterministic strategy rule engine |
| `ai/trade_proposal_service.py` | Proposal generator |
| `ai/proposal_approval_queue.py` | Persistent approval queue |
| `ai/proposal_execution_bridge.py` | Approved-proposal → orchestrator handoff |
| `ai/ai_analysis_history.py` | AI call audit log |
| `ai/regime_transition_tracker.py` | Regime change log |
| `ai/proposal_analytics.py` | Proposal-funnel analytics |
| `ai/strategy_attribution.py` | (Regime, strategy) → outcome analytics |
| `ai/recommendation_accuracy.py` | AI accuracy scoring |
| `execution/trade_orchestrator.py` | Central trade pipeline |
| `execution/portfolio_risk_evaluator.py` | Per-currency + total exposure gates |
| `execution/position_netting.py` | FIFO netting before new entries |
| `execution/persistent_trade_state_manager.py` | SQLite trade ledger |
| `execution/trading_control.py` | Operator ON/OFF flag |
| `execution/performance_metrics.py` | KPI and daily performance |
| `execution/risk_exposure.py` | Risk exposure aggregator |
| `brokers/oanda_broker.py` | OANDA REST client |
| `brokers/broker_health.py` | System-wide broker connectivity state |
| `dashboard/app.py` | Streamlit operator dashboard |
| `dry_run_sustained.py` | Continuous-loop dry run driver |
