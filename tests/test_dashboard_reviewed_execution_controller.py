import importlib
import inspect
import sys
from datetime import datetime, timezone

import pytest


_NOW_UTC = datetime(2026, 7, 16, 15, 0, 0, tzinfo=timezone.utc)

_WIRING_KEYS = (
    "broker",
    "quote_provider",
    "drawdown_provider",
    "bridge_execute",
    "mark_executed",
    "now_utc",
    "max_quote_age_seconds",
    "max_currency_exposure",
    "orchestrator",
    "state_manager",
)

_FACTORY_KEYS = {
    "api_key",
    "account_id",
    "base_url",
    "trade_state_db_path",
    "drawdown_db_path",
    "approval_db_path",
    "max_currency_exposure",
    "max_quote_age_seconds",
    "now_utc",
}

_PROPOSAL = {
    "proposal_id": "PROP-CONTROLLER-1",
    "pair": "EUR/USD",
    "direction": "LONG",
    "status": "APPROVED",
}


class _FakeWiring:
    def __init__(self, action_kwargs, call_order=None):
        self.action_kwargs = action_kwargs
        self.close_calls = 0
        self._call_order = call_order

    def close(self):
        self.close_calls += 1
        if self._call_order is not None:
            self._call_order.append("close")


class _RecordingFactory:
    def __init__(self, wiring, call_order=None):
        self.wiring = wiring
        self.calls = []
        self._call_order = call_order

    def __call__(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self._call_order is not None:
            self._call_order.append("factory")
        return self.wiring


class _RecordingAction:
    def __init__(self, result=None, error=None, call_order=None):
        self.result = (
            result
            if result is not None
            else {"success": True, "message": "ok"}
        )
        self.error = error
        self.calls = []
        self._call_order = call_order

    def __call__(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self._call_order is not None:
            self._call_order.append("action")
        if self.error is not None:
            raise self.error
        return self.result


def _sentinel_action_kwargs():
    return {key: object() for key in _WIRING_KEYS}


def _controller_module():
    return importlib.import_module(
        "dashboard.reviewed_execution_controller"
    )


def _patched_controller(monkeypatch, *, factory, action):
    module = _controller_module()
    monkeypatch.setattr(
        module,
        "build_reviewed_execution_wiring",
        factory,
    )
    monkeypatch.setattr(
        module,
        "execute_reviewed_proposal_action",
        action,
    )
    return module


def _call_controller(module, **overrides):
    controller_kwargs = dict(
        proposal=dict(_PROPOSAL),
        raw_stop_loss_price="1.07250",
        api_key="TEST-API-KEY",
        account_id="TEST-ACCOUNT",
        base_url="https://example.invalid",
        trade_state_db_path="unused-trade-state.db",
        drawdown_db_path="unused-drawdown.db",
        approval_db_path="unused-approvals.db",
        max_currency_exposure=100.0,
        max_quote_age_seconds=60.0,
        now_utc=_NOW_UTC,
    )
    if "start_of_day_nav_db_path" in inspect.signature(
        module.execute_reviewed_proposal_from_dashboard
    ).parameters:
        controller_kwargs["start_of_day_nav_db_path"] = (
            "unused-start-of-day-nav.db"
        )
    controller_kwargs.update(overrides)
    return module.execute_reviewed_proposal_from_dashboard(
        **controller_kwargs
    )


def test_controller_module_import_is_safe():
    before = set(sys.modules)

    module = _controller_module()

    newly_loaded = set(sys.modules) - before
    new_roots = {name.partition(".")[0] for name in newly_loaded}
    assert module.__name__ == "dashboard.reviewed_execution_controller"
    assert not (new_roots & {"streamlit", "market_data", "dotenv"})
    assert "dashboard.app" not in newly_loaded


def test_controller_builds_wiring_with_exact_configuration(monkeypatch):
    wiring = _FakeWiring(_sentinel_action_kwargs())
    factory = _RecordingFactory(wiring)
    action = _RecordingAction()
    module = _patched_controller(monkeypatch, factory=factory, action=action)

    _call_controller(module)

    assert len(factory.calls) == 1
    call = factory.calls[0]
    expected_factory_keys = set(_FACTORY_KEYS)
    if "start_of_day_nav_db_path" in inspect.signature(
        module.execute_reviewed_proposal_from_dashboard
    ).parameters:
        expected_factory_keys.add("start_of_day_nav_db_path")
    assert set(call) == expected_factory_keys
    assert call["api_key"] == "TEST-API-KEY"
    assert call["account_id"] == "TEST-ACCOUNT"
    assert call["base_url"] == "https://example.invalid"
    assert call["trade_state_db_path"] == "unused-trade-state.db"
    assert call["drawdown_db_path"] == "unused-drawdown.db"
    assert call["approval_db_path"] == "unused-approvals.db"
    assert call["max_currency_exposure"] == 100.0
    assert call["max_quote_age_seconds"] == 60.0
    assert call["now_utc"] is _NOW_UTC
    assert "proposal" not in call
    assert "raw_stop_loss_price" not in call


def test_controller_invokes_reviewed_action_once_with_exact_raw_stop(
    monkeypatch,
):
    sentinel_kwargs = _sentinel_action_kwargs()
    wiring = _FakeWiring(sentinel_kwargs)
    factory = _RecordingFactory(wiring)
    action = _RecordingAction()
    module = _patched_controller(monkeypatch, factory=factory, action=action)

    proposal = dict(_PROPOSAL)
    _call_controller(
        module,
        proposal=proposal,
        raw_stop_loss_price="1.07250",
    )

    assert len(action.calls) == 1
    call = action.calls[0]
    assert call["proposal"] is proposal
    assert type(call["raw_stop_loss_price"]) is str
    assert call["raw_stop_loss_price"] == "1.07250"
    for key in _WIRING_KEYS:
        assert call[key] is sentinel_kwargs[key]
    assert set(call) == {"proposal", "raw_stop_loss_price", *_WIRING_KEYS}


def test_controller_returns_exact_action_result_and_closes_wiring(
    monkeypatch,
):
    call_order = []
    wiring = _FakeWiring(_sentinel_action_kwargs(), call_order=call_order)
    factory = _RecordingFactory(wiring, call_order=call_order)
    distinctive_result = {
        "success": True,
        "message": "distinctive controller result",
    }
    action = _RecordingAction(
        result=distinctive_result,
        call_order=call_order,
    )
    module = _patched_controller(monkeypatch, factory=factory, action=action)

    result = _call_controller(module)

    assert result is distinctive_result
    assert wiring.close_calls == 1
    assert call_order == ["factory", "action", "close"]


def test_controller_closes_wiring_when_action_raises(monkeypatch):
    wiring = _FakeWiring(_sentinel_action_kwargs())
    factory = _RecordingFactory(wiring)
    action = _RecordingAction(error=ValueError("reviewed action failed"))
    module = _patched_controller(monkeypatch, factory=factory, action=action)

    with pytest.raises(ValueError, match="reviewed action failed"):
        _call_controller(module)

    assert len(action.calls) == 1
    assert wiring.close_calls == 1


def test_controller_does_not_duplicate_execution_marking(monkeypatch):
    import ai.proposal_approval_queue as queue_module

    mark_calls = []

    def sentinel_mark_executed(proposal_id):
        mark_calls.append(proposal_id)
        return True

    action_kwargs = _sentinel_action_kwargs()
    action_kwargs["mark_executed"] = sentinel_mark_executed
    wiring = _FakeWiring(action_kwargs)
    factory = _RecordingFactory(wiring)
    distinctive_result = {
        "success": True,
        "message": "marked by adapter only",
    }
    action = _RecordingAction(result=distinctive_result)
    module = _patched_controller(monkeypatch, factory=factory, action=action)

    constructed_queues = []
    original_queue_init = queue_module.ProposalApprovalQueue.__init__

    def recording_queue_init(self, *args, **kwargs):
        constructed_queues.append(True)
        original_queue_init(self, *args, **kwargs)

    monkeypatch.setattr(
        queue_module.ProposalApprovalQueue,
        "__init__",
        recording_queue_init,
    )

    result = _call_controller(module)

    assert mark_calls == []
    assert len(action.calls) == 1
    assert action.calls[0]["mark_executed"] is sentinel_mark_executed
    assert constructed_queues == []
    assert result is distinctive_result


def test_controller_forwards_explicit_start_of_day_nav_db_path(
    monkeypatch,
    tmp_path,
):
    call_order = []
    action_kwargs = _sentinel_action_kwargs()
    wiring = _FakeWiring(action_kwargs, call_order=call_order)
    factory = _RecordingFactory(wiring, call_order=call_order)
    action = _RecordingAction(call_order=call_order)
    module = _patched_controller(monkeypatch, factory=factory, action=action)
    proposal = dict(_PROPOSAL)
    daily_nav_db_path = str(tmp_path / "start-of-day-nav.db")

    _call_controller(
        module,
        proposal=proposal,
        raw_stop_loss_price="1.07250",
        start_of_day_nav_db_path=daily_nav_db_path,
    )

    assert len(factory.calls) == 1
    factory_call = factory.calls[0]
    assert factory_call["start_of_day_nav_db_path"] is daily_nav_db_path

    assert len(action.calls) == 1
    action_call = action.calls[0]
    assert action_call["proposal"] is proposal
    assert action_call["raw_stop_loss_price"] == "1.07250"
    assert "start_of_day_nav_db_path" not in action_call
    assert "start_of_day_nav_provider" not in action_call
    for key in _WIRING_KEYS:
        assert action_call[key] is action_kwargs[key]

    assert wiring.close_calls == 1
    assert call_order == ["factory", "action", "close"]


_PREVIEW_DEPENDENCY_KEYS = (
    "broker",
    "quote_provider",
    "drawdown_provider",
    "now_utc",
    "max_quote_age_seconds",
)


def _call_preview_controller(module, **overrides):
    controller_kwargs = dict(
        proposal=dict(_PROPOSAL),
        raw_stop_loss_price="1.07250",
        api_key="TEST-API-KEY",
        account_id="TEST-ACCOUNT",
        base_url="https://example.invalid",
        trade_state_db_path="unused-trade-state.db",
        drawdown_db_path="unused-drawdown.db",
        start_of_day_nav_db_path="unused-start-of-day-nav.db",
        approval_db_path="unused-approvals.db",
        max_currency_exposure=100.0,
        max_quote_age_seconds=60.0,
        now_utc=_NOW_UTC,
    )
    controller_kwargs.update(overrides)
    return module.preview_reviewed_proposal_from_dashboard(
        **controller_kwargs
    )


def test_preview_controller_returns_evidence_and_closes_wiring_without_execution(
    monkeypatch,
):
    call_order = []
    action_kwargs = _sentinel_action_kwargs()
    mark_calls = []

    def sentinel_mark_executed(proposal_id):
        mark_calls.append(proposal_id)
        return True

    action_kwargs["mark_executed"] = sentinel_mark_executed
    wiring = _FakeWiring(action_kwargs, call_order=call_order)
    factory = _RecordingFactory(wiring, call_order=call_order)
    execute_action = _RecordingAction(call_order=call_order)
    preview_evidence = {
        "proposal_id": "PROP-CONTROLLER-1",
        "units": 12345,
    }
    preview_action = _RecordingAction(
        result=preview_evidence,
        call_order=call_order,
    )
    module = _patched_controller(
        monkeypatch,
        factory=factory,
        action=execute_action,
    )
    monkeypatch.setattr(
        module,
        "preview_reviewed_proposal_action",
        preview_action,
        raising=False,
    )

    proposal = dict(_PROPOSAL)
    result = _call_preview_controller(
        module,
        proposal=proposal,
        raw_stop_loss_price="1.07250",
    )

    assert result is preview_evidence

    assert len(factory.calls) == 1
    factory_call = factory.calls[0]
    assert set(factory_call) == _FACTORY_KEYS | {"start_of_day_nav_db_path"}
    assert factory_call["api_key"] == "TEST-API-KEY"
    assert factory_call["account_id"] == "TEST-ACCOUNT"
    assert factory_call["base_url"] == "https://example.invalid"
    assert factory_call["trade_state_db_path"] == "unused-trade-state.db"
    assert factory_call["drawdown_db_path"] == "unused-drawdown.db"
    assert factory_call["start_of_day_nav_db_path"] == (
        "unused-start-of-day-nav.db"
    )
    assert factory_call["approval_db_path"] == "unused-approvals.db"
    assert factory_call["max_currency_exposure"] == 100.0
    assert factory_call["max_quote_age_seconds"] == 60.0
    assert factory_call["now_utc"] is _NOW_UTC
    assert "proposal" not in factory_call
    assert "raw_stop_loss_price" not in factory_call

    assert len(preview_action.calls) == 1
    preview_call = preview_action.calls[0]
    assert preview_call["proposal"] is proposal
    assert type(preview_call["raw_stop_loss_price"]) is str
    assert preview_call["raw_stop_loss_price"] == "1.07250"
    for key in _PREVIEW_DEPENDENCY_KEYS:
        assert preview_call[key] is action_kwargs[key]
    assert set(preview_call) == {
        "proposal",
        "raw_stop_loss_price",
        *_PREVIEW_DEPENDENCY_KEYS,
    }

    assert execute_action.calls == []
    assert mark_calls == []
    assert wiring.close_calls == 1
    assert call_order == ["factory", "action", "close"]
