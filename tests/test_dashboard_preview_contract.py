import importlib
import sys
import time


FORBIDDEN_IMPORT_ROOTS = {
    "streamlit",
    "brokers",
    "market_data",
    "ai",
    "execution",
    "sqlite3",
    "dotenv",
}


def _assert_no_callables(value):
    assert not callable(value)
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_no_callables(key)
            _assert_no_callables(item)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            _assert_no_callables(item)


class _PageConfigSpy:
    def __init__(self, events):
        self.events = events

    def set_page_config(self, **kwargs):
        self.events.append(("set_page_config", kwargs))


class _StreamlitSpy:
    def __init__(self):
        self.calls = []

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def set_page_config(self, **kwargs):
        self._record("set_page_config", **kwargs)

    def title(self, *args, **kwargs):
        self._record("title", *args, **kwargs)

    def subheader(self, *args, **kwargs):
        self._record("subheader", *args, **kwargs)

    def caption(self, *args, **kwargs):
        self._record("caption", *args, **kwargs)

    def markdown(self, *args, **kwargs):
        self._record("markdown", *args, **kwargs)

    def columns(self, spec, **kwargs):
        self._record("columns", spec, **kwargs)
        count = spec if isinstance(spec, int) else len(spec)
        return [self] * count

    def metric(self, *args, **kwargs):
        self._record("metric", *args, **kwargs)

    def dataframe(self, *args, **kwargs):
        self._record("dataframe", *args, **kwargs)

    def button(self, *args, **kwargs):
        self._record("button", *args, **kwargs)
        return False

    def divider(self, *args, **kwargs):
        self._record("divider", *args, **kwargs)


def test_preview_import_loads_only_safe_dependencies():
    before = set(sys.modules)

    module = importlib.import_module("dashboard.preview_app")

    newly_loaded = set(sys.modules) - before
    new_roots = {name.partition(".")[0] for name in newly_loaded}
    assert module.__name__ == "dashboard.preview_app"
    assert not (new_roots & FORBIDDEN_IMPORT_ROOTS)
    assert "dashboard.app" not in newly_loaded
    assert "dashboard.app" not in sys.modules
    assert {
        name for name in newly_loaded if name.startswith("dashboard.")
    } <= {"dashboard.preview_app", "dashboard.theme"}
    assert new_roots <= set(sys.stdlib_module_names) | {"dashboard"}


def test_preview_model_is_deterministic_data_without_callbacks():
    from dashboard.preview_app import build_preview_model

    first = build_preview_model()
    second = build_preview_model()

    assert isinstance(first, dict)
    assert isinstance(second, dict)
    assert first == second
    assert first is not second
    assert set(first) == {
        "headline",
        "metrics",
        "positions",
        "approval_queue",
        "system_status",
    }
    _assert_no_callables(first)


def test_main_configures_page_before_theme_and_render(monkeypatch):
    import dashboard.preview_app as preview_app

    events = []
    model = object()
    st_spy = _PageConfigSpy(events)
    monkeypatch.setattr(preview_app, "build_preview_model", lambda: model)
    monkeypatch.setattr(
        preview_app,
        "apply_dashboard_theme",
        lambda st_module: events.append(("apply_dashboard_theme", st_module)),
    )
    monkeypatch.setattr(
        preview_app,
        "render_preview_dashboard",
        lambda st_module, supplied_model: events.append(
            ("render_preview_dashboard", st_module, supplied_model)
        ),
    )

    preview_app.main(st_spy)

    assert [event[0] for event in events] == [
        "set_page_config",
        "apply_dashboard_theme",
        "render_preview_dashboard",
    ]
    assert events[0][1] == {
        "page_title": "AegisFX Dashboard Preview",
        "layout": "wide",
    }
    assert events[1][1] is st_spy
    assert events[2][1:] == (st_spy, model)


def test_preview_renderer_records_visual_components_without_callbacks():
    from dashboard.preview_app import build_preview_model, render_preview_dashboard

    st_spy = _StreamlitSpy()
    render_preview_dashboard(st_spy, build_preview_model())

    calls_by_name = {
        name: [(args, kwargs) for call_name, args, kwargs in st_spy.calls
               if call_name == name]
        for name in {call[0] for call in st_spy.calls}
    }
    assert len(calls_by_name.get("title", [])) == 1
    assert "AegisFX" in str(calls_by_name["title"][0][0][0])
    assert len(calls_by_name.get("metric", [])) >= 2
    assert calls_by_name.get("caption")
    assert calls_by_name.get("dataframe")

    pill_markdown = [
        (args, kwargs)
        for args, kwargs in calls_by_name.get("markdown", [])
        if kwargs.get("unsafe_allow_html") is True
        and args
        and "aegis-status-pill" in str(args[0])
    ]
    assert pill_markdown

    button_calls = calls_by_name.get("button", [])
    assert len([call for call in button_calls if call[1].get("type") == "primary"]) == 1
    for _args, kwargs in button_calls:
        assert not any(key.startswith("on_") or key == "callback" for key in kwargs)
        assert not any(callable(value) for value in _args)
        assert not any(callable(value) for value in kwargs.values())


def test_preview_has_no_sleep_or_explicit_rerun(monkeypatch):
    from dashboard.preview_app import (
        build_preview_model,
        main,
        render_preview_dashboard,
    )

    def fail_sleep(_seconds):
        raise AssertionError("Preview must not sleep")

    monkeypatch.setattr(time, "sleep", fail_sleep)
    st_spy = _StreamlitSpy()
    model = build_preview_model()

    main(st_spy)
    render_preview_dashboard(st_spy, model)
