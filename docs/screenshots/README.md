# Screenshot Placement Guide

Save your screenshots in this folder with these exact filenames so they render in `AegisFX_Status_Report.md`:

| Filename | What It Shows | Source Screenshot |
|----------|--------------|-------------------|
| `01_pnl_panel.png` | Title + P&L panel + KPI row | Screenshot 1 |
| `02_daily_performance.png` | Daily Performance table | Screenshot 2 (top) |
| `03_equity_curve.png` | Equity Curve chart | Screenshot 2 (bottom) |
| `04_positions_risk.png` | Current Positions + Risk Exposure | Screenshot 3 |
| `05_ai_agreement.png` | AI Agreement panel (regime, confidence, strategy) | Screenshot 4 |
| `06_ai_proposals.png` | AI Trade Proposals (empty state) | Screenshot 4 (bottom) |
| `07_approval_queue.png` | AI Approval Queue (empty) | Screenshot 5 (top) |
| `08_proposal_analytics.png` | AI Proposal Analytics with funnel | Screenshot 5 (middle) |
| `09_strategy_attribution.png` | Strategy Attribution by Regime | Screenshot 5 (bottom) |
| `10_recommendation_accuracy.png` | AI Recommendation Accuracy | Screenshot 6 (top) |
| `11_investor_exports.png` | Investor Report Export + PDF Report | Screenshot 6 (middle) |
| `12_confidence_trend.png` | AI Confidence Trend chart + Recent Regime Changes table | Screenshot 7 (top) |
| `13_alerts_status.png` | Alerts / System Status panel | Screenshot 8 |

## How To Convert to PDF

After saving screenshots:

1. Open `AegisFX_Status_Report.md` in VS Code
2. Install the "Markdown PDF" extension (or "Markdown Preview Enhanced")
3. Right-click → "Markdown PDF: Export (pdf)"
4. The PDF will include all images automatically

Alternatively, paste the markdown into a tool like Pandoc:

```
pandoc AegisFX_Status_Report.md -o AegisFX_Status_Report.pdf
```

Or open the markdown in any browser-based markdown viewer and use "Print → Save as PDF".
