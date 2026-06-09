"""
Generate the AegisFX architecture diagram as a PNG image.

Usage:
    python docs/generate_architecture_diagram.py

Output:
    docs/aegisfx_architecture_diagram.png
"""

import os
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(THIS_DIR, "aegisfx_architecture_diagram.png")

# ---------- Layer Definitions ----------
# Each layer: (title, color, items)
# Drawn top-to-bottom in this order.
LAYERS = [
    ("1. External Data Sources", "#dbe9ff",
     ["Alpha Vantage\nFX Market Data", "OANDA\nBroker State", "OpenAI API\nMarket Analysis"]),

    ("2. Market Data Layer", "#e0f2fe",
     ["Alpha Vantage\nPrice Feed", "Trend\nCalculation", "Volatility\nCalculation",
      "Range Percentile /\nPosition-in-Range", "Market Context\nBuilder"]),

    ("3. AI Intelligence Layer", "#fce7f3",
     ["OpenAI Market\nAnalysis Service", "Regime\nClassification",
      "Strategy\nRecommendation", "Trade Proposal\nService",
      "Proposal Approval\nQueue", "Accuracy &\nAttribution Analytics"]),

    ("4. Execution & Safety Layer", "#fef3c7",
     ["Trade\nOrchestrator", "Idempotency\n& Crash Recovery",
      "Rate Limiter &\nCircuit Breaker", "Portfolio Risk\nEvaluator",
      "Position\nNetting", "Broker Health\n& Trading Toggle"]),

    ("5. Broker Integration", "#ffedd5",
     ["OANDA Broker\nClient", "Market Order\nExecution",
      "Position Close", "Order Status\n& Health"]),

    ("6. Persistence Layer (SQLite)", "#e5e7eb",
     ["Trade State\nDatabase", "AI Analysis\nHistory DB",
      "Proposal Approval\nDatabase", "Regime Transition\nDatabase"]),

    ("7. Dashboard / Operator Layer (Streamlit)", "#dcfce7",
     ["P&L Panel", "Daily Performance", "Equity Curve",
      "Current Positions", "Risk Exposure", "AI Agreement",
      "Trade Proposals", "Approval Queue", "Alerts / Status"]),

    ("8. Reporting / Investor Layer", "#ede9fe",
     ["CSV Export", "HTML / PDF-Style\nInvestor Report",
      "Daily Consistency", "Proposal Analytics",
      "Strategy Attribution", "Recommendation\nAccuracy"]),

    ("9. External Outputs", "#fef2f2",
     ["Demo Dashboard\n(Operator UI)", "Investor Report\nDocument",
      "Operator\nDecisions", "Real Broker Trades\non OANDA Demo"]),
]


def draw_diagram():
    # Layout constants
    fig_width = 22
    fig_height = max(14, len(LAYERS) * 1.7)
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, len(LAYERS) * 10 + 4)
    ax.axis("off")

    # Title
    ax.text(50, len(LAYERS) * 10 + 2.6, "AegisFX System Architecture",
            ha="center", va="center", fontsize=22, fontweight="bold", color="#0f172a")
    ax.text(50, len(LAYERS) * 10 + 1.2,
            "End-to-end forex trading system  |  AI advises, deterministic rules govern, human approves",
            ha="center", va="center", fontsize=11, color="#475569", style="italic")

    # Draw layers top-to-bottom
    for i, (title, color, items) in enumerate(LAYERS):
        # y coordinate: layer 0 (External Data Sources) at top
        y_top = (len(LAYERS) - i) * 10 - 2

        # Layer background band
        layer_band = FancyBboxPatch(
            (1, y_top - 7), 98, 8,
            boxstyle="round,pad=0.3",
            linewidth=1.5,
            edgecolor="#94a3b8",
            facecolor=color,
            alpha=0.55,
        )
        ax.add_patch(layer_band)

        # Layer title (left side)
        ax.text(3, y_top - 0.5, title,
                ha="left", va="top", fontsize=12, fontweight="bold", color="#0f172a")

        # Draw items as boxes inside the layer
        n_items = len(items)
        item_area_left = 20
        item_area_right = 97
        item_area_width = item_area_right - item_area_left
        slot_width = item_area_width / n_items
        box_w = slot_width * 0.85
        box_h = 4.8

        for j, item in enumerate(items):
            x_center = item_area_left + slot_width * (j + 0.5)
            x = x_center - box_w / 2
            y = y_top - 6.5

            box = FancyBboxPatch(
                (x, y), box_w, box_h,
                boxstyle="round,pad=0.15",
                linewidth=1.2,
                edgecolor="#334155",
                facecolor="white",
                alpha=0.95,
            )
            ax.add_patch(box)
            ax.text(x_center, y + box_h / 2, item,
                    ha="center", va="center", fontsize=8.5, color="#0f172a")

    # ----- Inter-layer flow arrows -----
    # Vertical arrows showing data flow direction (top-down)
    arrow_color = "#1e293b"
    arrow_x_positions = [30, 55, 80]  # three arrow columns spanning width

    for i in range(len(LAYERS) - 1):
        # y_top of current layer
        y_top_current = (len(LAYERS) - i) * 10 - 2
        y_top_next = (len(LAYERS) - (i + 1)) * 10 - 2
        # Arrow from bottom of current layer to top of next
        y_start = y_top_current - 7  # bottom of current band
        y_end = y_top_next - 0.4  # top of next band

        for ax_x in arrow_x_positions:
            arrow = FancyArrowPatch(
                (ax_x, y_start - 0.1),
                (ax_x, y_end + 0.1),
                arrowstyle="-|>",
                mutation_scale=18,
                color=arrow_color,
                linewidth=1.3,
                alpha=0.55,
            )
            ax.add_patch(arrow)

    # ----- Side annotation: key flows -----
    flows_text = (
        "Key Flows:\n"
        "1. Alpha Vantage -> Market Context -> OpenAI -> Strategy -> Proposal\n"
        "2. Proposal -> Approval Queue -> Operator Click -> Orchestrator\n"
        "3. Orchestrator -> Safety Gates -> OANDA Broker\n"
        "4. Execution Result -> SQLite -> Dashboard & Reports\n"
        "5. Broker Health -> Alerts & Execution Guard"
    )
    ax.text(50, 1, flows_text,
            ha="center", va="bottom", fontsize=9, color="#1e293b",
            bbox=dict(boxstyle="round,pad=0.6", facecolor="#f8fafc", edgecolor="#94a3b8"))

    plt.tight_layout()
    plt.savefig(OUTPUT_PATH, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close()


if __name__ == "__main__":
    draw_diagram()
    print(f"Architecture diagram saved to: {OUTPUT_PATH}")
