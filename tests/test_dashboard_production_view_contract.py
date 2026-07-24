import importlib
import sys
from html import unescape


FORBIDDEN_IMPORT_ROOTS = {
    "streamlit",
    "brokers",
    "market_data",
    "ai",
    "execution",
    "sqlite3",
    "dotenv",
}

PENDING = {
    "proposal_id": "PROP-PENDING-001",
    "pair": "EUR/USD",
    "direction": "LONG",
    "suggested_size": 1.0,
    "confidence": 88,
    "strategy": "MeanReversion_v1",
    "reason": "Range reversion confirmed",
}

APPROVED = {
    "proposal_id": "PROP-APPROVED-001",
    "pair": "GBP/USD",
    "direction": "SHORT",
    "suggested_size": 2.0,
    "confidence": 91,
    "strategy": "Breakout_v1",
    "reason": "Momentum confirmation",
}

RECENT = {
    "pair": "AUD/USD",
    "direction": "BUY",
    "suggested_size": 1.5,
    "confidence": 79,
    "status": "EXECUTED",
    "reviewed_at": "2026-07-13T09:30:00+00:00",
}

STATUSES = [
    {
        "label": "Circuit Breaker",
        "value": "INACTIVE",
        "tone": "success",
    },
    {
        "label": "Broker Connection",
        "value": "CONNECTED",
        "tone": "success",
    },
    {
        "label": "Pending Trades",
        "value": "1",
        "tone": "warning",
    },
    {
        "label": "Last Successful Trade",
        "value": "2026-07-13 09:30:00",
        "tone": "muted",
    },
]


class _StreamlitSpy:
    def __init__(
        self,
        pressed_keys=None,
        *,
        calls=None,
        column_index=None,
    ):
        self.pressed_keys = set(pressed_keys or ())
        self.calls = [] if calls is None else calls
        self.column_index = column_index

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, self.column_index, args, kwargs))

    def markdown(self, *args, **kwargs):
        self._record("markdown", *args, **kwargs)

    def caption(self, *args, **kwargs):
        self._record("caption", *args, **kwargs)

    def columns(self, spec, **kwargs):
        self._record("columns", spec, **kwargs)
        count = spec if isinstance(spec, int) else len(spec)
        return [
            _StreamlitSpy(
                self.pressed_keys,
                calls=self.calls,
                column_index=index,
            )
            for index in range(count)
        ]

    def button(self, label, *, key=None, **kwargs):
        self._record("button", label, key=key, **kwargs)
        return key in self.pressed_keys


def _calls(spy, name):
    return [call for call in spy.calls if call[0] == name]


def test_production_view_import_loads_only_safe_dependencies():
    before = set(sys.modules)

    module = importlib.import_module("dashboard.production_view")

    newly_loaded = set(sys.modules) - before
    new_roots = {name.partition(".")[0] for name in newly_loaded}
    assert module.__name__ == "dashboard.production_view"
    assert not (new_roots & FORBIDDEN_IMPORT_ROOTS)
    assert not {
        "dashboard.app",
        "dashboard.preview_app",
    } & newly_loaded
    assert {
        name for name in newly_loaded if name.startswith("dashboard.")
    } <= {"dashboard.production_view", "dashboard.theme"}
    assert new_roots <= set(sys.stdlib_module_names) | {"dashboard"}


def test_production_view_passively_renders_shared_markup_and_controls():
    from dashboard.production_view import (
        render_approved_proposal_row,
        render_pending_proposal_row,
        render_production_hero,
        render_recent_decision_row,
        render_system_status_tiles,
    )

    action_calls = []

    def on_approve(proposal_id):
        action_calls.append(("approve", proposal_id))

    def on_reject(proposal_id):
        action_calls.append(("reject", proposal_id))

    def on_execute(proposal):
        action_calls.append(("execute", proposal))

    spy = _StreamlitSpy()
    render_production_hero(
        spy,
        label="UNREALIZED P&L",
        value="+$1,286.00",
        status_label="PROFIT",
        status_tone="success",
    )
    render_pending_proposal_row(
        spy,
        PENDING,
        on_approve=on_approve,
        on_reject=on_reject,
    )
    render_approved_proposal_row(
        spy,
        APPROVED,
        on_execute=on_execute,
    )
    render_recent_decision_row(spy, RECENT, tone="primary")
    render_system_status_tiles(spy, STATUSES)

    markdown = [str(args[0]) for _, _, args, _ in _calls(spy, "markdown")]
    captions = " ".join(
        str(args[0]) for _, _, args, _ in _calls(spy, "caption")
    )

    hero = next(fragment for fragment in markdown if "aegis-hero" in fragment)
    visible_hero = unescape(hero)
    for expected in (
        "aegis-card aegis-hero",
        "UNREALIZED P&L",
        "+$1,286.00",
        "PROFIT",
        "aegis-status-pill",
    ):
        assert expected in visible_hero

    pending = next(fragment for fragment in markdown if "EUR/USD" in fragment)
    approved = next(fragment for fragment in markdown if "GBP/USD" in fragment)
    for fragment, expected in (
        (pending, ("aegis-proposal-card", "LONG", "88", "PENDING")),
        (approved, ("aegis-proposal-card", "SHORT", "91", "APPROVED")),
    ):
        assert "aegis-status-pill" in fragment
        assert all(text in fragment for text in expected)

    for expected in (
        "size 1.0",
        "MeanReversion_v1",
        "Range reversion confirmed",
        "size 2.0",
        "Breakout_v1",
        "Momentum confirmation",
    ):
        assert expected in captions

    rendered_text = " ".join(markdown) + " " + captions
    for expected in (
        "AUD/USD",
        "BUY",
        "1.5",
        "79",
        "EXECUTED",
        "2026-07-13T09:30:00",
    ):
        assert expected in rendered_text

    status_tiles = [
        fragment for fragment in markdown if "aegis-status-tile" in fragment
    ]
    assert len(status_tiles) == 4
    assert all(
        all(
            class_name in fragment
            for class_name in (
                "aegis-status-tile__label",
                "aegis-status-tile__value",
                "aegis-status-pill",
            )
        )
        for fragment in status_tiles
    )

    button_calls = _calls(spy, "button")
    assert [(args[0], kwargs["key"]) for _, _, args, kwargs in button_calls] == [
        ("Approve", "approve_PROP-PENDING-001"),
        ("Reject", "reject_PROP-PENDING-001"),
        ("Execute Trade", "execute_PROP-APPROVED-001"),
    ]
    button_columns = {
        kwargs["key"]: column for _, column, _, kwargs in button_calls
    }
    assert button_columns == {
        "approve_PROP-PENDING-001": 1,
        "reject_PROP-PENDING-001": 2,
        "execute_PROP-APPROVED-001": 1,
    }
    for _, _, _, kwargs in button_calls:
        assert not any(key.startswith("on_") or key == "callback" for key in kwargs)
    assert action_calls == []


def test_production_view_dispatches_only_approve_when_pressed():
    from dashboard.production_view import render_pending_proposal_row

    action_calls = []
    spy = _StreamlitSpy({"approve_PROP-PENDING-001"})
    render_pending_proposal_row(
        spy,
        PENDING,
        on_approve=lambda proposal_id: action_calls.append(
            ("approve", proposal_id)
        ),
        on_reject=lambda proposal_id: action_calls.append(
            ("reject", proposal_id)
        ),
    )

    assert action_calls == [("approve", "PROP-PENDING-001")]


def test_production_view_dispatches_only_reject_when_pressed():
    from dashboard.production_view import render_pending_proposal_row

    action_calls = []
    spy = _StreamlitSpy({"reject_PROP-PENDING-001"})
    render_pending_proposal_row(
        spy,
        PENDING,
        on_approve=lambda proposal_id: action_calls.append(
            ("approve", proposal_id)
        ),
        on_reject=lambda proposal_id: action_calls.append(
            ("reject", proposal_id)
        ),
    )

    assert action_calls == [("reject", "PROP-PENDING-001")]


def test_production_view_dispatches_execute_once_when_pressed():
    from dashboard.production_view import render_approved_proposal_row

    action_calls = []
    spy = _StreamlitSpy({"execute_PROP-APPROVED-001"})
    render_approved_proposal_row(
        spy,
        APPROVED,
        on_execute=lambda proposal: action_calls.append(("execute", proposal)),
    )

    assert action_calls == [("execute", APPROVED)]
    assert action_calls[0][1] is APPROVED


def test_production_view_formats_all_confidence_values_as_percentages():
    from dashboard.production_view import (
        render_approved_proposal_row,
        render_pending_proposal_row,
        render_recent_decision_row,
    )

    spy = _StreamlitSpy()

    render_pending_proposal_row(
        spy,
        PENDING,
        on_approve=lambda proposal_id: None,
        on_reject=lambda proposal_id: None,
    )
    render_approved_proposal_row(
        spy,
        APPROVED,
        on_execute=lambda proposal: None,
    )
    render_recent_decision_row(
        spy,
        RECENT,
        tone="primary",
    )

    markdown_fragments = [
        str(args[0]) for _, _, args, _ in _calls(spy, "markdown")
    ]
    pending_fragment = unescape(
        next(fragment for fragment in markdown_fragments if "EUR/USD" in fragment)
    )
    approved_fragment = unescape(
        next(fragment for fragment in markdown_fragments if "GBP/USD" in fragment)
    )
    recent_fragment = unescape(
        next(fragment for fragment in markdown_fragments if "AUD/USD" in fragment)
    )

    assert "Confidence 88%" in pending_fragment
    assert "Confidence 91%" in approved_fragment
    assert "Confidence 79%" in recent_fragment


PREVIEW_EVIDENCE = {
    "proposal_id": "PROP-APPROVED-001",
    "pair": "GBP/USD",
    "direction": "SHORT",
    "entry_price": 1.27456,
    "units": 3417,
    "risk_fraction": 0.005,
    "risk_amount": 61.83,
    "stop_loss_price": 1.28912,
    "drawdown_fraction": 0.0214,
    "quote_timestamp": "2026-07-22T15:59:57+00:00",
    "raw_stop_loss_price": "1.28912",
}


def test_approved_proposal_uses_review_then_confirm_practice_order():
    from dashboard.production_view import render_approved_proposal_row

    action_calls = []

    def on_review(proposal):
        action_calls.append(("review", proposal))

    def on_confirm(proposal):
        action_calls.append(("confirm", proposal))

    # Without preview evidence: Review Trade only, no one-click path.
    spy = _StreamlitSpy()
    render_approved_proposal_row(
        spy,
        APPROVED,
        on_review=on_review,
        on_confirm=on_confirm,
        preview=None,
    )

    labels = [args[0] for _, _, args, _ in _calls(spy, "button")]
    keys = [kwargs["key"] for _, _, _, kwargs in _calls(spy, "button")]
    assert "Execute Trade" not in labels
    assert labels.count("Review Trade") == 1
    assert "review_PROP-APPROVED-001" in keys
    assert "Confirm Practice Order" not in labels
    assert action_calls == []

    # Pressing Review Trade dispatches the review callback once.
    spy = _StreamlitSpy({"review_PROP-APPROVED-001"})
    render_approved_proposal_row(
        spy,
        APPROVED,
        on_review=on_review,
        on_confirm=on_confirm,
        preview=None,
    )
    assert action_calls == [("review", APPROVED)]
    assert action_calls[0][1] is APPROVED
    action_calls.clear()

    # With preview evidence: the final execution values are visible and
    # Confirm Practice Order appears; rendering alone dispatches nothing.
    spy = _StreamlitSpy()
    render_approved_proposal_row(
        spy,
        APPROVED,
        on_review=on_review,
        on_confirm=on_confirm,
        preview=PREVIEW_EVIDENCE,
    )

    labels = [args[0] for _, _, args, _ in _calls(spy, "button")]
    keys = [kwargs["key"] for _, _, _, kwargs in _calls(spy, "button")]
    assert "Execute Trade" not in labels
    assert labels.count("Confirm Practice Order") == 1
    assert "confirm_PROP-APPROVED-001" in keys

    rendered_text = unescape(
        " ".join(
            str(args[0])
            for name, _, args, _ in spy.calls
            if name in {"markdown", "caption"}
        )
    )
    for evidence_value in (
        "1.27456",
        "3417",
        "0.005",
        "61.83",
        "1.28912",
        "0.0214",
        "15:59:57",
        "size 2.0",
    ):
        assert evidence_value in rendered_text
    assert action_calls == []

    # Pressing Confirm Practice Order dispatches the confirm callback once.
    spy = _StreamlitSpy({"confirm_PROP-APPROVED-001"})
    render_approved_proposal_row(
        spy,
        APPROVED,
        on_review=on_review,
        on_confirm=on_confirm,
        preview=PREVIEW_EVIDENCE,
    )
    assert action_calls == [("confirm", APPROVED)]
    assert action_calls[0][1] is APPROVED
