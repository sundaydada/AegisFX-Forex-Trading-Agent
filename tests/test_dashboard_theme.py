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
