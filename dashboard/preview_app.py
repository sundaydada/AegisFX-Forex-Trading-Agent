"""Deterministic, side-effect-free visual preview for the dashboard."""

from collections.abc import Mapping

from dashboard.theme import apply_dashboard_theme, build_pill_html


def build_preview_model() -> dict[str, object]:
    """Return fresh representative data for the visual preview."""
    return {
        "headline": {
            "title": "AegisFX Trading Dashboard",
            "eyebrow": "AUTONOMOUS FX INTELLIGENCE",
            "subtitle": "Human-supervised strategy, risk and execution command center",
            "status": "SYSTEM HEALTHY",
        },
        "metrics": [
            {
                "label": "Account Balance",
                "value": "$104,820",
                "delta": "+4.8%",
                "delta_color": "normal",
            },
            {
                "label": "Unrealized P&L",
                "value": "+$1,286",
                "delta": "+$342 today",
                "delta_color": "normal",
            },
            {
                "label": "Win Rate",
                "value": "64.2%",
                "delta": "+3.1%",
                "delta_color": "normal",
            },
            {
                "label": "Risk Utilization",
                "value": "37%",
                "delta": "Within limits",
                "delta_color": "off",
            },
        ],
        "positions": [
            {
                "Pair": "EUR/USD",
                "Direction": "LONG",
                "Units": "25,000",
                "Entry": "1.0832",
                "Current": "1.0876",
                "P&L": "+$110.00",
            },
            {
                "Pair": "GBP/USD",
                "Direction": "SHORT",
                "Units": "18,000",
                "Entry": "1.2741",
                "Current": "1.2698",
                "P&L": "+$77.40",
            },
        ],
        "approval_queue": {
            "pending": [
                {
                    "pair": "USD/JPY",
                    "direction": "SELL",
                    "confidence": "82%",
                    "status": "PENDING",
                }
            ],
            "approved": [
                {
                    "pair": "EUR/GBP",
                    "direction": "BUY",
                    "confidence": "76%",
                    "status": "APPROVED",
                }
            ],
            "recent": [
                {"pair": "AUD/USD", "status": "EXECUTED"},
                {"pair": "NZD/USD", "status": "EXPIRED"},
            ],
        },
        "system_status": [
            {"label": "Broker", "value": "Connected", "tone": "success"},
            {"label": "Risk Engine", "value": "Healthy", "tone": "success"},
            {"label": "AI Consensus", "value": "Strong", "tone": "primary"},
            {
                "label": "Autonomy",
                "value": "Human supervised",
                "tone": "secondary",
            },
        ],
    }


def _build_proposal_card_html(
    proposal: Mapping[str, str],
    tone: str,
) -> str:
    pill = build_pill_html(proposal["status"], tone)
    metadata = []
    if proposal.get("direction"):
        metadata.append(f'<span>{proposal["direction"]}</span>')
    if proposal.get("confidence"):
        metadata.append(f'<span>Confidence {proposal["confidence"]}</span>')
    meta_html = (
        f'<div class="aegis-proposal-card__meta">{" · ".join(metadata)}</div>'
        if metadata
        else ""
    )
    return (
        f'<div class="aegis-proposal-card aegis-proposal-card--{tone}">'
        '<div class="aegis-proposal-card__top">'
        f'<span class="aegis-proposal-card__pair">{proposal["pair"]}</span>'
        f"{pill}</div>{meta_html}</div>"
    )


def render_preview_dashboard(
    st_module,
    model: Mapping[str, object],
) -> None:
    """Render the supplied preview model with Streamlit-compatible methods."""
    headline = model["headline"]
    metrics = model["metrics"]
    positions = model["positions"]
    approval_queue = model["approval_queue"]
    system_status = model["system_status"]

    st_module.title(headline["title"])
    hero_status = build_pill_html(headline["status"], "success")
    st_module.markdown(
        f"""
        <div class="aegis-card aegis-hero">
            <span class="aegis-hero__eyebrow">{headline["eyebrow"]}</span>
            <p class="aegis-hero__subtitle">{headline["subtitle"]}</p>
            {hero_status}
        </div>
        """,
        unsafe_allow_html=True,
    )
    st_module.caption(
        "Deterministic offline preview — no broker, network, or database access."
    )

    metric_columns = st_module.columns(4)
    for column, metric in zip(metric_columns, metrics):
        column.metric(
            metric["label"],
            metric["value"],
            delta=metric["delta"],
            delta_color=metric["delta_color"],
        )

    st_module.divider()
    st_module.subheader("Current Positions")
    st_module.caption("Representative open positions for layout review.")
    st_module.dataframe(positions, width="stretch")

    st_module.divider()
    st_module.subheader("AI Approval Queue")
    approval_columns = st_module.columns(3)
    pending_column, approved_column, recent_column = approval_columns

    pending_column.markdown(
        '<span class="aegis-section-kicker">Pending</span>',
        unsafe_allow_html=True,
    )
    for proposal in approval_queue["pending"]:
        pending_column.markdown(
            _build_proposal_card_html(proposal, "warning"),
            unsafe_allow_html=True,
        )

    approved_column.markdown(
        '<span class="aegis-section-kicker">Approved</span>',
        unsafe_allow_html=True,
    )
    for proposal in approval_queue["approved"]:
        approved_column.markdown(
            _build_proposal_card_html(proposal, "success"),
            unsafe_allow_html=True,
        )

    recent_column.markdown(
        '<span class="aegis-section-kicker">Recent</span>',
        unsafe_allow_html=True,
    )
    recent_tones = {"EXECUTED": "primary", "EXPIRED": "expired"}
    for proposal in approval_queue["recent"]:
        tone = recent_tones[proposal["status"]]
        recent_column.markdown(
            _build_proposal_card_html(proposal, tone),
            unsafe_allow_html=True,
        )

    st_module.divider()
    st_module.subheader("System Status")
    status_columns = st_module.columns(4)
    for column, status in zip(status_columns, system_status):
        pill = build_pill_html(status["value"], status["tone"])
        column.markdown(
            f"""
            <div class="aegis-status-tile">
                <span class="aegis-status-tile__label">{status["label"]}</span>
                {pill}
            </div>
            """,
            unsafe_allow_html=True,
        )

    st_module.caption("Visual-only control — no action is connected.")
    st_module.button(
        "Preview Control",
        type="primary",
    )


def main(st_module) -> None:
    """Configure and render the isolated preview."""
    st_module.set_page_config(
        page_title="AegisFX Dashboard Preview",
        layout="wide",
    )
    apply_dashboard_theme(st_module)
    render_preview_dashboard(st_module, build_preview_model())


if __name__ == "__main__":
    import streamlit as st

    main(st)
