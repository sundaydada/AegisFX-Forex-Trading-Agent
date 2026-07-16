import importlib
import inspect
import math
import sys
from datetime import datetime, timezone

import pytest


_DEFAULT = object()
_NOW_UTC = datetime(2026, 7, 15, 16, 0, tzinfo=timezone.utc)
_PROPOSAL = {
    "proposal_id": "PROP-ACTION-1",
    "pair": "EUR/USD",
    "direction": "LONG",
    "status": "APPROVED",
}


class _RecordingDelegate:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self.result


class _RecordingBroker:
    def __init__(self):
        self.calls = 0

    def get_account_snapshot(self):
        self.calls += 1
        return None


class _RecordingQuoteProvider:
    def __init__(self):
        self.calls = []

    def get_quote(self, pair):
        self.calls.append(pair)
        return None


class _RecordingDrawdownProvider:
    def __init__(self):
        self.calls = []

    def get_drawdown_fraction(self, account_snapshot):
        self.calls.append(account_snapshot)
        return None


class _RecordingMarkExecuted:
    def __init__(self):
        self.calls = []

    def __call__(self, proposal_id):
        self.calls.append(proposal_id)
        return True


def _success_result():
    return {
        "success": True,
        "message": "Forwarded",
        "request_id": "AI-PROPOSAL-PROP-ACTION-1",
        "execution_result": {},
    }


def _run_action(
    monkeypatch,
    *,
    proposal=_DEFAULT,
    raw_stop_loss_price="1.0950",
    delegate_result=_DEFAULT,
):
    import dashboard.proposal_execution_action as action_module

    resolved_proposal = (
        dict(_PROPOSAL) if proposal is _DEFAULT else proposal
    )
    resolved_result = (
        _success_result() if delegate_result is _DEFAULT else delegate_result
    )

    delegate = _RecordingDelegate(resolved_result)
    monkeypatch.setattr(
        action_module,
        "execute_approved_proposal_with_runtime_inputs",
        delegate,
    )

    broker = _RecordingBroker()
    quote_provider = _RecordingQuoteProvider()
    drawdown_provider = _RecordingDrawdownProvider()
    bridge = object()
    mark_executed = _RecordingMarkExecuted()
    orchestrator = object()
    state_manager = object()

    result = action_module.execute_reviewed_proposal_action(
        proposal=resolved_proposal,
        raw_stop_loss_price=raw_stop_loss_price,
        broker=broker,
        quote_provider=quote_provider,
        drawdown_provider=drawdown_provider,
        bridge_execute=bridge,
        mark_executed=mark_executed,
        now_utc=_NOW_UTC,
        max_quote_age_seconds=60.0,
        max_currency_exposure=100.0,
        orchestrator=orchestrator,
        state_manager=state_manager,
    )
    return result, {
        "proposal": resolved_proposal,
        "delegate": delegate,
        "broker": broker,
        "quote_provider": quote_provider,
        "drawdown_provider": drawdown_provider,
        "bridge": bridge,
        "mark_executed": mark_executed,
        "orchestrator": orchestrator,
        "state_manager": state_manager,
    }


def _assert_local_failure(result, harness, *evidence_terms):
    assert isinstance(result, dict)
    assert result["success"] is False
    assert isinstance(result["message"], str)
    message = result["message"].lower().replace("_", " ").replace("-", " ")
    assert any(term.lower() in message for term in evidence_terms)
    assert harness["delegate"].calls == []
    assert harness["broker"].calls == 0
    assert harness["quote_provider"].calls == []
    assert harness["drawdown_provider"].calls == []
    assert harness["mark_executed"].calls == []


def _assert_forwarded_call(call, harness, expected_stop):
    assert call["proposal"] is harness["proposal"]
    assert type(call["stop_loss_price"]) is float
    assert call["stop_loss_price"] == expected_stop
    assert call["broker"] is harness["broker"]
    assert call["quote_provider"] is harness["quote_provider"]
    assert call["drawdown_provider"] is harness["drawdown_provider"]
    assert call["bridge_execute"] is harness["bridge"]
    assert call["orchestrator"] is harness["orchestrator"]
    assert call["state_manager"] is harness["state_manager"]
    assert call["now_utc"] is _NOW_UTC
    assert call["max_quote_age_seconds"] == 60.0
    assert call["max_currency_exposure"] == 100.0
    assert "stop_distance_pips" not in call


def test_action_module_import_is_safe():
    before = set(sys.modules)

    module = importlib.import_module("dashboard.proposal_execution_action")

    newly_loaded = set(sys.modules) - before
    new_roots = {name.partition(".")[0] for name in newly_loaded}
    assert module.__name__ == "dashboard.proposal_execution_action"
    assert not (new_roots & {"streamlit", "market_data", "sqlite3", "dotenv"})
    assert "dashboard.app" not in newly_loaded
    assert "brokers.oanda_broker" not in newly_loaded


def test_valid_long_stop_parses_forwards_and_marks_executed(monkeypatch):
    result, harness = _run_action(
        monkeypatch,
        raw_stop_loss_price="1.0950",
    )

    assert result is harness["delegate"].result
    assert len(harness["delegate"].calls) == 1
    _assert_forwarded_call(
        harness["delegate"].calls[0],
        harness,
        1.095,
    )
    assert harness["mark_executed"].calls == ["PROP-ACTION-1"]
    assert harness["broker"].calls == 0
    assert harness["quote_provider"].calls == []
    assert harness["drawdown_provider"].calls == []


def test_valid_short_stop_forwards_exact_reviewed_stop(monkeypatch):
    proposal = dict(_PROPOSAL, direction="SHORT")

    result, harness = _run_action(
        monkeypatch,
        proposal=proposal,
        raw_stop_loss_price="1.1050",
    )

    assert result is harness["delegate"].result
    assert len(harness["delegate"].calls) == 1
    _assert_forwarded_call(
        harness["delegate"].calls[0],
        harness,
        1.105,
    )
    assert harness["mark_executed"].calls == ["PROP-ACTION-1"]


def test_numeric_string_int_and_float_inputs_are_accepted(monkeypatch):
    accepted_inputs = (
        ("1.0950", 1.095),
        (2, 2.0),
        (1.095, 1.095),
    )
    for raw_value, expected_stop in accepted_inputs:
        result, harness = _run_action(
            monkeypatch,
            raw_stop_loss_price=raw_value,
        )

        assert result is harness["delegate"].result
        assert len(harness["delegate"].calls) == 1
        call = harness["delegate"].calls[0]
        assert type(call["stop_loss_price"]) is float
        assert call["stop_loss_price"] == expected_stop
        assert harness["mark_executed"].calls == ["PROP-ACTION-1"]

    for rejected_bool in (True, False):
        result, harness = _run_action(
            monkeypatch,
            raw_stop_loss_price=rejected_bool,
        )
        _assert_local_failure(result, harness, "stop")


def test_missing_blank_and_nonnumeric_stop_input_fails_closed(monkeypatch):
    for invalid_input in (
        None,
        "",
        "   ",
        "not-a-price",
    ):
        result, harness = _run_action(
            monkeypatch,
            raw_stop_loss_price=invalid_input,
        )
        _assert_local_failure(result, harness, "stop")


def test_invalid_finite_domain_stop_values_fail_closed(monkeypatch):
    for invalid_input in (
        True,
        False,
        float("nan"),
        float("inf"),
        float("-inf"),
        "nan",
        "inf",
        "-inf",
        0,
        -1,
        "0",
        "-1.25",
    ):
        result, harness = _run_action(
            monkeypatch,
            raw_stop_loss_price=invalid_input,
        )
        _assert_local_failure(result, harness, "stop")


def test_invalid_proposal_or_missing_id_fails_before_delegation(monkeypatch):
    missing_id = dict(_PROPOSAL)
    missing_id.pop("proposal_id")
    invalid_proposals = (
        None,
        "not-a-mapping",
        missing_id,
        dict(_PROPOSAL, proposal_id=""),
        dict(_PROPOSAL, proposal_id=None),
    )
    for invalid_proposal in invalid_proposals:
        result, harness = _run_action(
            monkeypatch,
            proposal=invalid_proposal,
        )
        _assert_local_failure(result, harness, "proposal")


def test_directionally_invalid_long_stop_returns_resolver_failure(
    monkeypatch,
):
    failure = {
        "success": False,
        "message": (
            "Invalid stop evidence: LONG stop_loss_price must be below"
            " the entry price"
        ),
    }

    result, harness = _run_action(
        monkeypatch,
        raw_stop_loss_price="1.1001",
        delegate_result=failure,
    )

    assert result is failure
    assert len(harness["delegate"].calls) == 1
    call = harness["delegate"].calls[0]
    assert call["stop_loss_price"] == 1.1001
    assert "stop_distance_pips" not in call
    assert harness["mark_executed"].calls == []


def test_directionally_invalid_short_stop_returns_resolver_failure(
    monkeypatch,
):
    failure = {
        "success": False,
        "message": (
            "Invalid stop evidence: SHORT stop_loss_price must be above"
            " the entry price"
        ),
    }

    result, harness = _run_action(
        monkeypatch,
        proposal=dict(_PROPOSAL, direction="SHORT"),
        raw_stop_loss_price="1.0997",
        delegate_result=failure,
    )

    assert result is failure
    assert len(harness["delegate"].calls) == 1
    call = harness["delegate"].calls[0]
    assert call["stop_loss_price"] == 1.0997
    assert "stop_distance_pips" not in call
    assert harness["mark_executed"].calls == []


def test_quote_moved_across_reviewed_stop_forwards_exact_stop(monkeypatch):
    failure = {
        "success": False,
        "message": (
            "Invalid stop evidence: LONG stop_loss_price must be below"
            " the entry price"
        ),
    }

    result, harness = _run_action(
        monkeypatch,
        raw_stop_loss_price="1.0950",
        delegate_result=failure,
    )

    assert result is failure
    assert len(harness["delegate"].calls) == 1
    call = harness["delegate"].calls[0]
    assert call["stop_loss_price"] == 1.095
    assert "stop_distance_pips" not in call
    assert harness["mark_executed"].calls == []


def test_stale_quote_or_provider_failure_never_marks_or_retries(monkeypatch):
    runtime_failures = (
        {
            "success": False,
            "message": (
                "Invalid quote evidence: timestamp is stale beyond"
                " max_quote_age_seconds"
            ),
        },
        {
            "success": False,
            "message": (
                "Account snapshot evidence unavailable:"
                " broker.get_account_snapshot failed"
            ),
        },
    )
    for failure in runtime_failures:
        result, harness = _run_action(
            monkeypatch,
            delegate_result=failure,
        )

        assert result is failure
        assert len(harness["delegate"].calls) == 1
        assert harness["mark_executed"].calls == []


def test_bridge_failure_result_is_returned_unchanged_without_mark(
    monkeypatch,
):
    failure = {
        "success": False,
        "message": "Cannot execute proposal with status: PENDING",
        "request_id": "AI-PROPOSAL-PROP-ACTION-1",
        "execution_result": {},
    }

    result, harness = _run_action(
        monkeypatch,
        delegate_result=failure,
    )

    assert result is failure
    assert len(harness["delegate"].calls) == 1
    assert harness["mark_executed"].calls == []


def test_each_invocation_dispatches_and_marks_at_most_once(monkeypatch):
    first_result, first_harness = _run_action(monkeypatch)
    assert first_result is first_harness["delegate"].result
    assert len(first_harness["delegate"].calls) == 1
    assert first_harness["mark_executed"].calls == ["PROP-ACTION-1"]

    second_result, second_harness = _run_action(monkeypatch)
    assert second_result is second_harness["delegate"].result
    assert len(second_harness["delegate"].calls) == 1
    assert second_harness["mark_executed"].calls == ["PROP-ACTION-1"]


def test_malformed_or_untrue_results_never_mark_executed(monkeypatch):
    malformed_results = (
        None,
        ["not-a-mapping"],
        "not-a-mapping",
        {},
        {"message": "no success key"},
        {"success": "True"},
        {"success": 1},
    )
    for malformed_result in malformed_results:
        result, harness = _run_action(
            monkeypatch,
            delegate_result=malformed_result,
        )

        assert result is malformed_result
        assert len(harness["delegate"].calls) == 1
        assert harness["mark_executed"].calls == []


def test_no_generated_or_fallback_stop_is_ever_forwarded(monkeypatch):
    for invalid_input in (None, "", "not-a-price", 0, -1, True, math.nan):
        _, harness = _run_action(
            monkeypatch,
            raw_stop_loss_price=invalid_input,
        )
        assert harness["delegate"].calls == []

    failure = {"success": False, "message": "Invalid stop evidence"}
    _, harness = _run_action(
        monkeypatch,
        raw_stop_loss_price="1.0950",
        delegate_result=failure,
    )
    calls = harness["delegate"].calls
    assert len(calls) == 1
    assert calls[0]["stop_loss_price"] == 1.095
    assert "stop_distance_pips" not in calls[0]
    float_kwargs = {
        key for key, value in calls[0].items() if type(value) is float
    }
    assert float_kwargs == {
        "stop_loss_price",
        "max_quote_age_seconds",
        "max_currency_exposure",
    }


def test_action_forwards_required_orchestrator_and_state_manager(
    monkeypatch,
):
    import dashboard.proposal_execution_action as action_module

    delegate = _RecordingDelegate(_success_result())
    monkeypatch.setattr(
        action_module,
        "execute_approved_proposal_with_runtime_inputs",
        delegate,
    )

    proposal = dict(_PROPOSAL)
    broker = _RecordingBroker()
    quote_provider = _RecordingQuoteProvider()
    drawdown_provider = _RecordingDrawdownProvider()
    bridge = object()
    mark_executed = _RecordingMarkExecuted()
    orchestrator = object()
    state_manager = object()

    result = action_module.execute_reviewed_proposal_action(
        proposal=proposal,
        raw_stop_loss_price="1.0950",
        broker=broker,
        quote_provider=quote_provider,
        drawdown_provider=drawdown_provider,
        bridge_execute=bridge,
        mark_executed=mark_executed,
        now_utc=_NOW_UTC,
        max_quote_age_seconds=60.0,
        max_currency_exposure=100.0,
        orchestrator=orchestrator,
        state_manager=state_manager,
    )

    assert result is delegate.result
    assert len(delegate.calls) == 1
    call = delegate.calls[0]
    assert call["orchestrator"] is orchestrator
    assert call["state_manager"] is state_manager
    _assert_forwarded_call(
        call,
        {
            "proposal": proposal,
            "broker": broker,
            "quote_provider": quote_provider,
            "drawdown_provider": drawdown_provider,
            "bridge": bridge,
            "orchestrator": orchestrator,
            "state_manager": state_manager,
        },
        1.095,
    )
    assert mark_executed.calls == ["PROP-ACTION-1"]


def test_orchestrator_and_state_manager_are_required_keyword_arguments():
    import dashboard.proposal_execution_action as action_module

    signature = inspect.signature(
        action_module.execute_reviewed_proposal_action
    )
    for name in ("orchestrator", "state_manager"):
        assert name in signature.parameters
        parameter = signature.parameters[name]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty
