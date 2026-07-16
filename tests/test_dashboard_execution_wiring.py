import importlib
import sys
from collections.abc import Mapping
from datetime import datetime, timezone

import pytest


_NOW_UTC = datetime(2026, 7, 16, 14, 0, 0, tzinfo=timezone.utc)

_REQUIRED_ACTION_KWARGS = {
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
}

_FORBIDDEN_ACTION_KWARGS = {
    "proposal",
    "raw_stop_loss_price",
    "stop_loss_price",
    "entry_price",
    "stop_distance_pips",
    "account_snapshot",
    "drawdown_fraction",
    "quote",
    "suggested_size",
    "units",
}


def _snapshot(nav):
    from brokers.broker_interface import AccountSnapshot

    return AccountSnapshot(
        nav=nav,
        balance=nav,
        currency="USD",
        margin_available=nav,
    )


def _build_wiring(
    tmp_path,
    *,
    api_key="TEST-API-KEY",
    account_id="TEST-ACCOUNT",
    prefix="wiring",
    drawdown_db_path=None,
    start_of_day_nav_db_path=None,
    approval_db_path=None,
):
    module = importlib.import_module("dashboard.execution_wiring")
    factory_kwargs = dict(
        api_key=api_key,
        account_id=account_id,
        base_url="https://example.invalid",
        trade_state_db_path=str(tmp_path / f"{prefix}-trade-state.db"),
        drawdown_db_path=(
            drawdown_db_path
            if drawdown_db_path is not None
            else str(tmp_path / f"{prefix}-drawdown.db")
        ),
        approval_db_path=(
            approval_db_path
            if approval_db_path is not None
            else str(tmp_path / f"{prefix}-approvals.db")
        ),
        max_currency_exposure=100.0,
        max_quote_age_seconds=60.0,
        now_utc=_NOW_UTC,
    )
    if start_of_day_nav_db_path is not None:
        factory_kwargs["start_of_day_nav_db_path"] = (
            start_of_day_nav_db_path
        )
    return module.build_reviewed_execution_wiring(**factory_kwargs)


def test_wiring_module_import_is_safe():
    before = set(sys.modules)

    module = importlib.import_module("dashboard.execution_wiring")

    newly_loaded = set(sys.modules) - before
    new_roots = {name.partition(".")[0] for name in newly_loaded}
    assert module.__name__ == "dashboard.execution_wiring"
    assert not (new_roots & {"streamlit", "market_data", "dotenv"})
    assert "dashboard.app" not in newly_loaded


def test_factory_builds_complete_reviewed_action_kwargs(tmp_path):
    from ai.proposal_execution_bridge import ProposalExecutionBridge
    from brokers.oanda_broker import OandaBroker
    from execution.persistent_drawdown_provider import (
        PersistentHighWaterDrawdownProvider,
    )
    from execution.persistent_trade_state_manager import (
        PersistentTradeStateManager,
    )
    from execution.trade_orchestrator import TradeOrchestrator

    wiring = _build_wiring(tmp_path)
    try:
        kwargs = wiring.action_kwargs
        assert isinstance(kwargs, Mapping)
        assert set(kwargs) == _REQUIRED_ACTION_KWARGS

        assert isinstance(kwargs["broker"], OandaBroker)
        assert kwargs["quote_provider"] is kwargs["broker"]

        assert isinstance(kwargs["orchestrator"], TradeOrchestrator)
        assert kwargs["orchestrator"]._broker is kwargs["broker"]

        assert isinstance(
            kwargs["state_manager"],
            PersistentTradeStateManager,
        )
        assert isinstance(
            kwargs["drawdown_provider"],
            PersistentHighWaterDrawdownProvider,
        )
        assert kwargs["bridge_execute"] is (
            ProposalExecutionBridge.execute_approved_proposal
        )
        assert kwargs["now_utc"] is _NOW_UTC
        assert kwargs["max_quote_age_seconds"] == 60.0
        assert kwargs["max_currency_exposure"] == 100.0
    finally:
        wiring.close()


def test_drawdown_provider_is_persistent_and_account_scoped(tmp_path):
    drawdown_db_path = str(tmp_path / "shared-drawdown.db")

    first = _build_wiring(
        tmp_path,
        prefix="first",
        drawdown_db_path=drawdown_db_path,
    )
    try:
        provider = first.action_kwargs["drawdown_provider"]
        assert provider.get_drawdown_fraction(_snapshot(100_000.0)) == 0.0
    finally:
        first.close()

    second = _build_wiring(
        tmp_path,
        prefix="second",
        drawdown_db_path=drawdown_db_path,
    )
    try:
        provider = second.action_kwargs["drawdown_provider"]
        assert provider.get_drawdown_fraction(
            _snapshot(95_000.0)
        ) == pytest.approx(0.05)
    finally:
        second.close()

    other_account = _build_wiring(
        tmp_path,
        prefix="other",
        account_id="OTHER-ACCOUNT",
        drawdown_db_path=drawdown_db_path,
    )
    try:
        provider = other_account.action_kwargs["drawdown_provider"]
        assert provider.get_drawdown_fraction(_snapshot(95_000.0)) == 0.0
    finally:
        other_account.close()


def test_missing_identity_or_key_fails_before_persistence_creation(
    tmp_path,
):
    invalid_cases = (
        {"api_key": ""},
        {"api_key": "   "},
        {"api_key": True},
        {"api_key": 123},
        {"account_id": ""},
        {"account_id": "   "},
        {"account_id": True},
        {"account_id": 123},
    )
    for case_index, overrides in enumerate(invalid_cases):
        module = importlib.import_module("dashboard.execution_wiring")

        trade_db = tmp_path / f"case{case_index}-trade-state.db"
        drawdown_db = tmp_path / f"case{case_index}-drawdown.db"
        approval_db = tmp_path / f"case{case_index}-approvals.db"

        factory_kwargs = dict(
            api_key="TEST-API-KEY",
            account_id="TEST-ACCOUNT",
            base_url="https://example.invalid",
            trade_state_db_path=str(trade_db),
            drawdown_db_path=str(drawdown_db),
            approval_db_path=str(approval_db),
            max_currency_exposure=100.0,
            max_quote_age_seconds=60.0,
            now_utc=_NOW_UTC,
        )
        factory_kwargs.update(overrides)

        with pytest.raises(ValueError) as excinfo:
            module.build_reviewed_execution_wiring(**factory_kwargs)

        expected_term = "api key" if "api_key" in overrides else "account"
        normalized_message = str(excinfo.value).lower().replace("_", " ")
        assert expected_term in normalized_message

        assert not trade_db.exists()
        assert not drawdown_db.exists()
        assert not approval_db.exists()


def test_mark_executed_targets_only_injected_approval_database(tmp_path):
    from ai.proposal_approval_queue import ProposalApprovalQueue

    approval_db_path = str(tmp_path / "approvals.db")

    queue = ProposalApprovalQueue(db_path=approval_db_path)
    try:
        added = queue.add_proposals([
            {
                "pair": "EUR/USD",
                "direction": "LONG",
                "suggested_size": 1000.0,
                "confidence": 90,
                "strategy": "Trend Following",
                "reason": "wiring mark_executed red test",
            }
        ])
        assert added == 1
        pending = queue.get_pending_proposals()
        assert len(pending) == 1
        proposal_id = pending[0]["proposal_id"]
        assert queue.approve_proposal(proposal_id) is True
    finally:
        queue.close()

    wiring = _build_wiring(tmp_path, approval_db_path=approval_db_path)
    try:
        mark_executed = wiring.action_kwargs["mark_executed"]

        assert mark_executed(proposal_id) is True

        verify_queue = ProposalApprovalQueue(db_path=approval_db_path)
        try:
            assert verify_queue.get_approved_proposals() == []
            recent = verify_queue.get_recent_decisions(limit=5)
            statuses = {
                decision["proposal_id"]: decision["status"]
                for decision in recent
            }
            assert statuses[proposal_id] == "EXECUTED"
        finally:
            verify_queue.close()

        assert mark_executed(proposal_id) is False

        created_databases = {
            path.name for path in tmp_path.iterdir() if path.suffix == ".db"
        }
        assert created_databases <= {
            "approvals.db",
            "wiring-trade-state.db",
            "wiring-drawdown.db",
        }
    finally:
        wiring.close()


def test_factory_exposes_no_runtime_evidence_fallbacks(tmp_path):
    from execution.persistent_drawdown_provider import (
        PersistentHighWaterDrawdownProvider,
    )

    wiring = _build_wiring(tmp_path)
    try:
        kwargs = wiring.action_kwargs
        assert set(kwargs) == _REQUIRED_ACTION_KWARGS
        assert not (set(kwargs) & _FORBIDDEN_ACTION_KWARGS)

        assert kwargs["quote_provider"] is kwargs["broker"]
        assert isinstance(
            kwargs["drawdown_provider"],
            PersistentHighWaterDrawdownProvider,
        )

        numeric_values = {
            key: value
            for key, value in kwargs.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        assert numeric_values == {
            "max_quote_age_seconds": 60.0,
            "max_currency_exposure": 100.0,
        }
    finally:
        wiring.close()


def test_partial_construction_cleanup_preserves_original_failure(
    monkeypatch,
):
    module = importlib.import_module("dashboard.execution_wiring")

    construction_record = {
        "brokers": 0,
        "orchestrators": 0,
        "state_managers": [],
    }

    class _FakeBroker:
        def __init__(self, *, api_key, account_id, base_url):
            construction_record["brokers"] += 1

    class _FakeOrchestrator:
        def __init__(self, broker):
            construction_record["orchestrators"] += 1
            self._broker = broker

    class _FakeStateManager:
        def __init__(self, *, db_path):
            self.close_attempts = 0
            construction_record["state_managers"].append(self)

        def close(self):
            self.close_attempts += 1
            raise RuntimeError("state-manager cleanup failed")

    def _failing_drawdown_provider(*, db_path, account_id):
        raise ValueError("drawdown construction failed")

    monkeypatch.setattr(module, "OandaBroker", _FakeBroker)
    monkeypatch.setattr(module, "TradeOrchestrator", _FakeOrchestrator)
    monkeypatch.setattr(
        module,
        "PersistentTradeStateManager",
        _FakeStateManager,
    )
    monkeypatch.setattr(
        module,
        "PersistentHighWaterDrawdownProvider",
        _failing_drawdown_provider,
    )

    with pytest.raises(ValueError, match="drawdown construction failed"):
        module.build_reviewed_execution_wiring(
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

    assert construction_record["brokers"] == 1
    assert construction_record["orchestrators"] == 1
    assert len(construction_record["state_managers"]) == 1
    assert construction_record["state_managers"][0].close_attempts == 1


def test_wiring_constructs_one_daily_nav_provider_and_injects_same_instance(
    monkeypatch,
    tmp_path,
):
    module = importlib.import_module("dashboard.execution_wiring")
    daily_db_path = str(tmp_path / "daily-nav.db")
    providers = []
    orchestrators = []

    class _RecordingDailyProvider:
        def __init__(self, *, db_path, account_id, clock):
            self.db_path = db_path
            self.account_id = account_id
            self.clock = clock
            self.close_calls = 0
            providers.append(self)

        def close(self):
            self.close_calls += 1

    class _RecordingOrchestrator:
        def __init__(
            self,
            broker,
            *,
            start_of_day_nav_provider=None,
        ):
            self.broker = broker
            self.start_of_day_nav_provider = start_of_day_nav_provider
            orchestrators.append(self)

    monkeypatch.setattr(
        module,
        "PersistentStartOfDayNavProvider",
        _RecordingDailyProvider,
        raising=False,
    )
    monkeypatch.setattr(module, "TradeOrchestrator", _RecordingOrchestrator)

    wiring = _build_wiring(
        tmp_path,
        start_of_day_nav_db_path=daily_db_path,
    )
    try:
        assert len(providers) == 1
        provider = providers[0]
        assert provider.db_path == daily_db_path
        assert provider.account_id == "TEST-ACCOUNT"
        assert callable(provider.clock)
        clock_result = provider.clock()
        assert clock_result is _NOW_UTC
        assert clock_result.tzinfo is not None
        assert clock_result.utcoffset() is not None

        assert len(orchestrators) == 1
        orchestrator = orchestrators[0]
        assert orchestrator.start_of_day_nav_provider is provider
        assert orchestrator.broker is wiring.action_kwargs["broker"]
    finally:
        wiring.close()


def test_wiring_action_kwargs_remain_unchanged_with_daily_loss_enabled(
    tmp_path,
):
    wiring = _build_wiring(
        tmp_path,
        start_of_day_nav_db_path=str(tmp_path / "daily-nav.db"),
    )
    try:
        kwargs = wiring.action_kwargs
        assert isinstance(kwargs, Mapping)
        assert set(kwargs) == _REQUIRED_ACTION_KWARGS
        assert not (
            set(kwargs)
            & {
                "start_of_day_nav_provider",
                "start_of_day_nav_db_path",
                "daily_loss",
                "daily_loss_limit",
            }
        )
    finally:
        wiring.close()


def test_wiring_close_closes_daily_nav_provider_exactly_once(
    monkeypatch,
    tmp_path,
):
    module = importlib.import_module("dashboard.execution_wiring")
    resources = {
        "state_managers": [],
        "drawdown_providers": [],
        "daily_providers": [],
    }

    class _FakeBroker:
        def __init__(self, *, api_key, account_id, base_url):
            self.account_id = account_id

    class _RecordingStateManager:
        def __init__(self, *, db_path):
            self.close_calls = 0
            resources["state_managers"].append(self)

        def close(self):
            self.close_calls += 1

    class _RecordingDrawdownProvider:
        def __init__(self, *, db_path, account_id):
            self.close_calls = 0
            resources["drawdown_providers"].append(self)

        def close(self):
            self.close_calls += 1

    class _RecordingDailyProvider:
        def __init__(self, *, db_path, account_id, clock):
            self.close_calls = 0
            resources["daily_providers"].append(self)

        def close(self):
            self.close_calls += 1

    class _FakeOrchestrator:
        def __init__(
            self,
            broker,
            *,
            start_of_day_nav_provider=None,
        ):
            self._broker = broker
            self._start_of_day_nav_provider = start_of_day_nav_provider

    monkeypatch.setattr(module, "OandaBroker", _FakeBroker)
    monkeypatch.setattr(
        module,
        "PersistentTradeStateManager",
        _RecordingStateManager,
    )
    monkeypatch.setattr(
        module,
        "PersistentHighWaterDrawdownProvider",
        _RecordingDrawdownProvider,
    )
    monkeypatch.setattr(
        module,
        "PersistentStartOfDayNavProvider",
        _RecordingDailyProvider,
        raising=False,
    )
    monkeypatch.setattr(module, "TradeOrchestrator", _FakeOrchestrator)

    wiring = _build_wiring(
        tmp_path,
        start_of_day_nav_db_path=str(tmp_path / "daily-nav.db"),
    )
    wiring.close()
    wiring.close()

    assert len(resources["daily_providers"]) == 1
    assert resources["daily_providers"][0].close_calls == 1
    assert len(resources["state_managers"]) == 1
    assert resources["state_managers"][0].close_calls == 1
    assert len(resources["drawdown_providers"]) == 1
    assert resources["drawdown_providers"][0].close_calls == 1


def test_partial_construction_closes_daily_provider_and_preserves_original_failure(
    monkeypatch,
    tmp_path,
):
    module = importlib.import_module("dashboard.execution_wiring")
    resources = {
        "state_managers": [],
        "drawdown_providers": [],
        "daily_providers": [],
    }

    class _FakeBroker:
        def __init__(self, *, api_key, account_id, base_url):
            self.account_id = account_id

    class _RecordingStateManager:
        def __init__(self, *, db_path):
            self.close_calls = 0
            resources["state_managers"].append(self)

        def close(self):
            self.close_calls += 1

    class _RecordingDrawdownProvider:
        def __init__(self, *, db_path, account_id):
            self.close_calls = 0
            resources["drawdown_providers"].append(self)

        def close(self):
            self.close_calls += 1

    class _RecordingDailyProvider:
        def __init__(self, *, db_path, account_id, clock):
            self.close_calls = 0
            resources["daily_providers"].append(self)

        def close(self):
            self.close_calls += 1
            raise RuntimeError("daily-provider cleanup failed")

    class _FailingOrchestrator:
        def __init__(
            self,
            broker,
            *,
            start_of_day_nav_provider=None,
        ):
            assert start_of_day_nav_provider is resources[
                "daily_providers"
            ][0]
            raise RuntimeError("orchestrator construction failed")

    monkeypatch.setattr(module, "OandaBroker", _FakeBroker)
    monkeypatch.setattr(
        module,
        "PersistentTradeStateManager",
        _RecordingStateManager,
    )
    monkeypatch.setattr(
        module,
        "PersistentHighWaterDrawdownProvider",
        _RecordingDrawdownProvider,
    )
    monkeypatch.setattr(
        module,
        "PersistentStartOfDayNavProvider",
        _RecordingDailyProvider,
        raising=False,
    )
    monkeypatch.setattr(module, "TradeOrchestrator", _FailingOrchestrator)

    with pytest.raises(
        RuntimeError,
        match="orchestrator construction failed",
    ):
        _build_wiring(
            tmp_path,
            start_of_day_nav_db_path=str(tmp_path / "daily-nav.db"),
        )

    assert len(resources["daily_providers"]) == 1
    assert resources["daily_providers"][0].close_calls == 1
    assert len(resources["state_managers"]) == 1
    assert resources["state_managers"][0].close_calls == 1
    assert len(resources["drawdown_providers"]) == 1
    assert resources["drawdown_providers"][0].close_calls == 1
