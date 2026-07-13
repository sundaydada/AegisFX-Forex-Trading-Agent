"""Import-safe visual theme helpers for the AegisFX Streamlit dashboard."""

from html import escape


DEFAULT_PALETTE = {
    "page_background": "#0B1220",
    "page_background_bottom": "#111A2E",
    "card_background": "rgba(24, 34, 54, 0.72)",
    "card_border": "rgba(120, 140, 200, 0.18)",
    "primary": "#22D3EE",
    "secondary": "#A78BFA",
    "text": "#E6ECF5",
    "muted_text": "#8A97AE",
    "success": "#22C55E",
    "warning": "#F59E0B",
    "danger": "#EF4444",
    "expired": "#6B7280",
}

_PILL_TONES = {
    "success": DEFAULT_PALETTE["success"],
    "warning": DEFAULT_PALETTE["warning"],
    "danger": DEFAULT_PALETTE["danger"],
    "expired": DEFAULT_PALETTE["expired"],
    "primary": DEFAULT_PALETTE["primary"],
    "secondary": DEFAULT_PALETTE["secondary"],
    "muted": DEFAULT_PALETTE["muted_text"],
}


def build_global_css() -> str:
    """Return the deterministic global dashboard stylesheet."""
    palette = DEFAULT_PALETTE
    return f"""<style>
.stApp {{
    background: {palette["page_background"]};
    background-image: linear-gradient(
        180deg,
        {palette["page_background"]} 0%,
        {palette["page_background_bottom"]} 100%
    );
    color: {palette["text"]};
}}

.stApp p,
.stApp label,
.stApp h1,
.stApp h2,
.stApp h3 {{
    color: {palette["text"]};
}}

/* Reusable glass card treatment. */
.aegis-card,
[data-testid="stVerticalBlockBorderWrapper"] {{
    background: {palette["card_background"]};
    border: 1px solid {palette["card_border"]};
    border-radius: 14px;
    box-shadow: 0 12px 32px rgba(0, 0, 0, 0.18);
    backdrop-filter: blur(12px);
}}

.aegis-hero {{
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 0.85rem;
    padding: 1.75rem 2rem;
    overflow: hidden;
    background-color: {palette["card_background"]};
    background-image:
        radial-gradient(circle at 88% 12%, rgba(167, 139, 250, 0.2), transparent 38%),
        linear-gradient(120deg, rgba(34, 211, 238, 0.14), transparent 58%);
    border: 1px solid {palette["card_border"]};
    border-top: 2px solid {palette["primary"]};
    border-radius: 18px;
}}

.aegis-hero__eyebrow {{
    color: {palette["primary"]};
    font-size: 0.76rem;
    font-weight: 800;
    letter-spacing: 0.14em;
}}

.aegis-hero__subtitle {{
    max-width: 48rem;
    margin: 0;
    color: {palette["muted_text"]};
    font-size: 1rem;
    line-height: 1.6;
}}

.aegis-section-kicker {{
    display: block;
    margin: 0 0 0.65rem;
    color: {palette["muted_text"]};
    font-size: 0.72rem;
    font-weight: 800;
    letter-spacing: 0.12em;
    text-transform: uppercase;
}}

.aegis-proposal-card {{
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 0.7rem;
    width: 100%;
    min-height: 8rem;
    margin: 0 0 0.75rem;
    padding: 1rem;
    overflow-wrap: anywhere;
    background: {palette["card_background"]};
    border: 1px solid {palette["card_border"]};
    border-left-width: 3px;
    border-radius: 12px;
    box-shadow: 0 8px 22px rgba(0, 0, 0, 0.14);
    backdrop-filter: blur(12px);
    transition: border-color 140ms ease, box-shadow 140ms ease,
        transform 140ms ease;
}}

.aegis-proposal-card:hover {{
    box-shadow: 0 11px 26px rgba(0, 0, 0, 0.2);
    transform: translateY(-2px);
}}

.aegis-proposal-card__top {{
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 0.75rem;
    width: 100%;
}}

.aegis-proposal-card__pair {{
    color: {palette["text"]};
    font-size: 1.02rem;
    font-weight: 800;
    letter-spacing: 0.02em;
}}

.aegis-proposal-card__meta {{
    color: {palette["muted_text"]};
    font-size: 0.82rem;
    line-height: 1.5;
}}

.aegis-proposal-card--warning {{
    border-left-color: {palette["warning"]};
}}

.aegis-proposal-card--success {{
    border-left-color: {palette["success"]};
}}

.aegis-proposal-card--primary {{
    border-left-color: {palette["primary"]};
}}

.aegis-proposal-card--expired {{
    border-left-color: {palette["expired"]};
}}

.aegis-status-tile {{
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    justify-content: center;
    gap: 0.75rem;
    width: 100%;
    min-height: 7rem;
    padding: 1rem;
    background: {palette["card_background"]};
    border: 1px solid {palette["card_border"]};
    border-radius: 12px;
    backdrop-filter: blur(12px);
}}

.aegis-status-tile__label {{
    color: {palette["muted_text"]};
    font-size: 0.75rem;
    font-weight: 800;
    letter-spacing: 0.1em;
    text-transform: uppercase;
}}

/* Streamlit metric cards and values. */
[data-testid="stMetric"] {{
    background: {palette["card_background"]};
    border: 1px solid {palette["card_border"]};
    border-radius: 12px;
    padding: 0.85rem 1rem;
}}

[data-testid="stMetricValue"] {{
    color: {palette["text"]};
    font-weight: 700;
}}

[data-testid="stMetricLabel"],
[data-testid="stCaptionContainer"],
[data-testid="stCaptionContainer"] p {{
    color: {palette["muted_text"]};
}}

/* Base button treatment and cyan primary action. */
.stButton > button {{
    border-color: {palette["card_border"]};
    transition: border-color 140ms ease, box-shadow 140ms ease,
        transform 140ms ease;
}}

.stButton > button:hover {{
    border-color: {palette["primary"]};
    transform: translateY(-1px);
}}

.stButton > button[kind="primary"] {{
    background: {palette["primary"]};
    border-color: {palette["primary"]};
    color: {palette["page_background"]};
    font-weight: 700;
    box-shadow: 0 5px 16px rgba(34, 211, 238, 0.18);
}}

.stButton > button[kind="primary"]:hover {{
    background: {palette["primary"]};
    border-color: {palette["primary"]};
    box-shadow: 0 7px 20px rgba(34, 211, 238, 0.28);
}}

.stButton > button:focus-visible,
.stApp a:focus-visible,
.stApp input:focus-visible,
.stApp select:focus-visible,
.stApp textarea:focus-visible {{
    outline: 3px solid {palette["secondary"]};
    outline-offset: 3px;
}}

[data-testid="stDataFrame"] {{
    background: {palette["card_background"]};
    border: 1px solid {palette["card_border"]};
    border-radius: 10px;
    overflow: hidden;
}}

hr {{
    border: 0;
    border-top: 1px solid {palette["card_border"]};
    margin: 1rem 0;
}}

@media (prefers-reduced-motion: reduce) {{
    *,
    *::before,
    *::after {{
        scroll-behavior: auto !important;
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.01ms !important;
    }}
}}

@media (max-width: 768px) {{
    .aegis-card,
    [data-testid="stMetric"] {{
        border-radius: 10px;
        padding: 0.7rem 0.8rem;
    }}

    .aegis-hero {{
        gap: 0.7rem;
        padding: 1.15rem;
    }}

    .aegis-proposal-card,
    .aegis-status-tile {{
        width: 100%;
        min-height: auto;
        padding: 0.9rem;
    }}

    .aegis-proposal-card__top {{
        flex-direction: column;
        gap: 0.45rem;
    }}

    .aegis-proposal-card__meta {{
        max-width: 100%;
    }}

    [data-testid="stMetricValue"] {{
        font-size: 1.35rem;
    }}

    .stButton > button {{
        min-height: 2.75rem;
        width: 100%;
    }}
}}
</style>"""


def build_pill_html(label: str, tone: str) -> str:
    """Return an escaped, text-labelled status pill for a semantic tone."""
    tone_key = tone.strip().lower() if isinstance(tone, str) else ""
    if tone_key not in _PILL_TONES:
        raise ValueError(f"Unsupported pill tone: {tone!r}")

    color = _PILL_TONES[tone_key]
    safe_label = escape(label)
    return (
        f'<span class="aegis-status-pill aegis-status-pill--{tone_key}" '
        f'style="display: inline-flex; align-items: center; padding: 4px 10px; '
        f'border: 1px solid {color}; border-radius: 999px; color: {color}; '
        'background: transparent; font-size: 0.75rem; font-weight: 700; '
        f'line-height: 1.2; letter-spacing: 0.04em; white-space: nowrap;">'
        f"{safe_label}</span>"
    )


def apply_dashboard_theme(st_module) -> None:
    """Apply the stylesheet through a supplied Streamlit-compatible object."""
    st_module.markdown(
        build_global_css(),
        unsafe_allow_html=True,
    )
