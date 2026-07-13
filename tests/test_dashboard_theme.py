"""Behavioral contract for the future import-safe ``dashboard.theme`` module."""

import pytest


def test_global_css_covers_core_components_accessibility_and_responsiveness():
    from dashboard.theme import DEFAULT_PALETTE, build_global_css

    css = build_global_css()
    normalized = css.lower()

    assert isinstance(css, str)
    assert DEFAULT_PALETTE["page_background"].lower() in normalized
    assert "gradient" in normalized
    assert "card" in normalized
    assert "metric" in normalized
    assert "button" in normalized and "primary" in normalized
    assert ":focus-visible" in normalized
    assert "@media (prefers-reduced-motion: reduce)" in normalized
    assert "@media" in normalized and "max-width" in normalized


def test_default_palette_exposes_approved_semantic_colors():
    from dashboard.theme import DEFAULT_PALETTE

    expected = {
        "primary": "#22D3EE",
        "secondary": "#A78BFA",
        "text": "#E6ECF5",
        "success": "#22C55E",
        "warning": "#F59E0B",
        "danger": "#EF4444",
        "expired": "#6B7280",
    }

    assert {
        "page_background",
        "card_background",
        "primary",
        "secondary",
        "text",
        "muted_text",
        "success",
        "warning",
        "danger",
        "expired",
    } <= DEFAULT_PALETTE.keys()
    assert {key: DEFAULT_PALETTE[key].upper() for key in expected} == expected

    page_rgb = tuple(
        int(DEFAULT_PALETTE["page_background"].lstrip("#")[index:index + 2], 16)
        for index in (0, 2, 4)
    )
    assert page_rgb[0] <= 20 and page_rgb[1] <= 40 and page_rgb[2] <= 60
    assert page_rgb[2] > page_rgb[0]


def test_status_pills_keep_visible_labels_and_use_semantic_colors():
    from dashboard.theme import DEFAULT_PALETTE, build_pill_html

    approved = build_pill_html("APPROVED", "success")
    expired = build_pill_html("EXPIRED", "expired")

    assert "APPROVED" in approved
    assert DEFAULT_PALETTE["success"].lower() in approved.lower()
    assert "border-radius" in approved.lower() and "999" in approved
    assert "EXPIRED" in expired
    assert DEFAULT_PALETTE["expired"].lower() in expired.lower()
    assert expired != approved


def test_status_pill_rejects_unsupported_tones():
    from dashboard.theme import build_pill_html

    with pytest.raises(ValueError):
        build_pill_html("UNKNOWN", "made-up-tone")


def test_apply_dashboard_theme_makes_one_unsafe_markdown_call():
    from dashboard.theme import apply_dashboard_theme, build_global_css

    class MarkdownSpy:
        def __init__(self):
            self.calls = []

        def markdown(self, body, **kwargs):
            self.calls.append((body, kwargs))

    spy = MarkdownSpy()
    apply_dashboard_theme(spy)

    assert spy.calls == [(build_global_css(), {"unsafe_allow_html": True})]


def test_hero_builder_returns_complete_escaped_theme_markup():
    from dashboard.theme import build_hero_html

    html = build_hero_html(
        "Preview <Desk>",
        "Broker & database free",
        "HEALTHY > IDLE",
        "success",
    )

    assert isinstance(html, str)
    assert "aegis-card aegis-hero" in html
    assert "aegis-hero__eyebrow" in html
    assert "aegis-hero__subtitle" in html
    assert "aegis-status-pill" in html
    assert "Preview" in html and "Broker" in html and "HEALTHY" in html
    assert "&lt;Desk&gt;" in html
    assert "Broker &amp; database free" in html
    assert "HEALTHY &gt; IDLE" in html

    with pytest.raises(ValueError):
        build_hero_html("Preview", "Offline", "UNKNOWN", "unsupported")


def test_proposal_card_builder_returns_escaped_optional_markup():
    from dashboard.theme import build_proposal_card_html

    html = build_proposal_card_html(
        "EUR/USD <demo>",
        "PENDING & REVIEW",
        "warning",
        direction="LONG > FLAT",
        confidence="82% & steady",
    )

    assert "aegis-proposal-card" in html
    assert "aegis-proposal-card--warning" in html
    assert "aegis-proposal-card__top" in html
    assert "aegis-proposal-card__pair" in html
    assert "aegis-proposal-card__meta" in html
    assert "aegis-status-pill" in html
    assert all(text in html for text in ("EUR/USD", "LONG", "82%", "PENDING"))
    assert "&lt;demo&gt;" in html
    assert "PENDING &amp; REVIEW" in html
    assert "LONG &gt; FLAT" in html
    assert "82% &amp; steady" in html
    assert "None" not in build_proposal_card_html("USD/JPY", "EXPIRED", "expired")

    with pytest.raises(ValueError):
        build_proposal_card_html("USD/JPY", "UNKNOWN", "unsupported")


def test_status_tile_builder_returns_escaped_value_hierarchy():
    from dashboard.theme import build_status_tile_html

    html = build_status_tile_html(
        "Broker <Primary>",
        "Connected & ready > idle",
        "success",
    )

    assert "aegis-status-tile" in html
    assert "aegis-status-tile__label" in html
    assert "aegis-status-tile__value" in html
    assert "aegis-status-pill" in html
    assert "Broker" in html and "Connected" in html
    assert "Broker &lt;Primary&gt;" in html
    assert "Connected &amp; ready &gt; idle" in html

    with pytest.raises(ValueError):
        build_status_tile_html("Broker", "Unknown", "unsupported")
