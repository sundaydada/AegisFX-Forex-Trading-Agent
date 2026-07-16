import pytest


DAILY_LOSS_LIMIT = 0.02


class _RecordingBroker:
    def __init__(self):
        self.calls = []

    def place_order(self, order):
        self.calls.append(dict(order))
        return {
            "execution_status": "Filled",
            "broker_order_id": "TEST-DAILY-LOSS-ORDER-1",
            "currency_pair": order["currency_pair"],
            "direction": order["direction"],
            "units": order["position_size"],
            "fill_price": 1.1,
            "timestamp": "2026-07-16T12:00:00Z",
        }


class _RecordingStateManager:
    def __init__(self, trades=()):
        self._trades = [dict(trade) for trade in trades]
        self._processed_results = {}
        self.recorded_trades = []
        self.closed_request_ids = []

    def get_all_trades(self):
        return list(self._trades)

    def record_trade(self, trade):
        stored = dict(trade)
        self._trades.append(stored)
        self.recorded_trades.append(stored)

    def update_trade(self, request_id, execution_result, status):
        for trade in self._trades:
            if (
                trade.get("request_id") == request_id
                and trade.get("status") == "PENDING"
            ):
                trade.update(execution_result)
                trade["status"] = status
                return

    def close_trade(self, request_id):
        for trade in self._trades:
            if (
                trade.get("request_id") == request_id
                and trade.get("status") == "FILLED"
            ):
                trade["status"] = "CLOSED"
                self.closed_request_ids.append(request_id)
                return

    def has_processed(self, request_id):
        return request_id in self._processed_results

    def get_processed_result(self, request_id):
        return self._processed_results.get(request_id)

    def record_processed_result(self, request_id, result):
        self._processed_results[request_id] = result


class _StartOfDayNavProvider:
    def __init__(self, baseline=100_000.0, error=None):
        self.baseline = baseline
        self.error = error
        self.calls = []

    def get_start_of_day_nav(self, account_snapshot):
        self.calls.append(account_snapshot)
        if self.error is not None:
            raise self.error
        return self.baseline


class _FailIfCalledProvider:
    def __init__(self):
        self.calls = []

    def get_start_of_day_nav(self, account_snapshot):
        self.calls.append(account_snapshot)
        raise AssertionError("daily NAV provider must not run for full netting")


def _evaluate_daily_loss(*, start_of_day_nav, current_nav):
    from execution.daily_loss_gate import evaluate_daily_loss

    return evaluate_daily_loss(
        start_of_day_nav=start_of_day_nav,
        current_nav=current_nav,
        limit_fraction=DAILY_LOSS_LIMIT,
    )


def _account_snapshot(nav):
    from brokers.broker_interface import AccountSnapshot

    return AccountSnapshot(
        nav=nav,
        balance=100_000.0,
        currency="USD",
        margin_available=90_000.0,
    )


def _proposed_trade(account_snapshot, *, include_snapshot=True):
    trade = {
        "currency_pair": "EUR/USD",
        "direction": "Long",
        "approved_position_size": 10_000,
        "stop_loss_price": 1.095,
        "nav": account_snapshot.nav,
        "account_currency": account_snapshot.currency,
        "risk_fraction": 0.005,
        "risk_budget_amount": account_snapshot.nav * 0.005,
        "loss_per_unit_at_stop": 0.005,
    }
    if include_snapshot:
        trade["account_snapshot"] = account_snapshot
    return trade


def _process_trade(
    monkeypatch,
    *,
    request_id,
    account_snapshot,
    provider,
    state_manager=None,
    include_snapshot=True,
):
    import execution.trade_orchestrator as orchestrator_module
    from execution.trade_orchestrator import TradeOrchestrator

    monkeypatch.setattr(
        orchestrator_module,
        "is_trading_enabled",
        lambda: True,
    )

    broker = _RecordingBroker()
    state = state_manager or _RecordingStateManager()
    orchestrator = TradeOrchestrator(
        broker,
        start_of_day_nav_provider=provider,
    )
    result = orchestrator.process_trade(
        state_manager=state,
        request_id=request_id,
        proposed_trade=_proposed_trade(
            account_snapshot,
            include_snapshot=include_snapshot,
        ),
        max_currency_exposure=1_000_000.0,
    )
    return result, broker, state


def test_daily_loss_below_two_percent_allows_new_exposure():
    result = _evaluate_daily_loss(
        start_of_day_nav=100_000.0,
        current_nav=98_000.01,
    )

    assert result.daily_loss_amount == pytest.approx(1_999.99)
    assert result.daily_loss_fraction < DAILY_LOSS_LIMIT
    assert result.limit_fraction == pytest.approx(DAILY_LOSS_LIMIT)
    assert result.new_exposure_allowed is True
    assert result.reason == "Daily loss is below the daily-loss limit"


def test_daily_loss_at_two_percent_blocks_new_exposure():
    result = _evaluate_daily_loss(
        start_of_day_nav=100_000.0,
        current_nav=98_000.0,
    )

    assert result.daily_loss_amount == pytest.approx(2_000.0)
    assert result.daily_loss_fraction == pytest.approx(DAILY_LOSS_LIMIT)
    assert result.new_exposure_allowed is False
    assert result.reason == "Daily-loss limit reached or exceeded"


def test_daily_loss_above_two_percent_blocks_new_exposure():
    result = _evaluate_daily_loss(
        start_of_day_nav=100_000.0,
        current_nav=97_500.0,
    )

    assert result.daily_loss_fraction == pytest.approx(0.025)
    assert result.new_exposure_allowed is False


def test_daily_loss_gains_clamp_to_zero():
    result = _evaluate_daily_loss(
        start_of_day_nav=100_000.0,
        current_nav=101_000.0,
    )

    assert result.daily_loss_amount == pytest.approx(0.0)
    assert result.daily_loss_fraction == pytest.approx(0.0)
    assert result.new_exposure_allowed is True


def test_orchestrator_below_daily_limit_submits_one_order(monkeypatch):
    snapshot = _account_snapshot(98_000.01)
    provider = _StartOfDayNavProvider(100_000.0)

    result, broker, state = _process_trade(
        monkeypatch,
        request_id="REQ-DAILY-BELOW",
        account_snapshot=snapshot,
        provider=provider,
    )

    assert len(provider.calls) == 1
    assert provider.calls[0] is snapshot
    assert len(state.recorded_trades) == 1
    assert state.recorded_trades[0]["status"] == "FILLED"
    assert len(broker.calls) == 1
    assert result["approval_status"] == "Approved"
    assert result["reason"] == "Monetary risk at stop is within limits"
    assert result["execution_result"] == {
        "execution_status": "Filled",
        "broker_order_id": "TEST-DAILY-LOSS-ORDER-1",
        "currency_pair": "EUR/USD",
        "direction": "Long",
        "units": 10_000,
        "fill_price": 1.1,
        "timestamp": "2026-07-16T12:00:00Z",
    }


def test_orchestrator_at_daily_limit_rejects_before_pending_or_broker(
    monkeypatch,
):
    snapshot = _account_snapshot(98_000.0)
    provider = _StartOfDayNavProvider(100_000.0)

    result, broker, state = _process_trade(
        monkeypatch,
        request_id="REQ-DAILY-AT-LIMIT",
        account_snapshot=snapshot,
        provider=provider,
    )

    assert result == {
        "approval_status": "Rejected",
        "reason": "Daily-loss limit reached or exceeded",
        "execution_result": None,
    }
    assert len(provider.calls) == 1
    assert provider.calls[0] is snapshot
    assert state.recorded_trades == []
    assert broker.calls == []
    assert state.has_processed("REQ-DAILY-AT-LIMIT")
    assert state.get_processed_result("REQ-DAILY-AT-LIMIT") is result


def test_orchestrator_missing_or_invalid_daily_evidence_fails_closed(
    monkeypatch,
):
    cases = (
        (
            "provider raises",
            _StartOfDayNavProvider(error=RuntimeError("baseline unavailable")),
            True,
        ),
        ("snapshot missing", _StartOfDayNavProvider(), False),
        ("NaN baseline", _StartOfDayNavProvider(float("nan")), True),
        ("non-positive baseline", _StartOfDayNavProvider(0.0), True),
    )

    for index, (case_name, provider, include_snapshot) in enumerate(cases):
        snapshot = _account_snapshot(98_000.01)
        request_id = f"REQ-DAILY-INVALID-{index}"

        result, broker, state = _process_trade(
            monkeypatch,
            request_id=request_id,
            account_snapshot=snapshot,
            provider=provider,
            include_snapshot=include_snapshot,
        )

        assert result["approval_status"] == "Rejected", case_name
        assert "daily" in result["reason"].lower(), case_name
        assert result["execution_result"] is None, case_name
        assert state.recorded_trades == [], case_name
        assert broker.calls == [], case_name
        assert state.has_processed(request_id), case_name
        assert state.get_processed_result(request_id) is result, case_name
        if include_snapshot:
            assert len(provider.calls) == 1, case_name
            assert provider.calls[0] is snapshot, case_name
        else:
            assert provider.calls == [], case_name


def test_pure_risk_reducing_netting_bypasses_daily_loss_failure(monkeypatch):
    snapshot = _account_snapshot(98_000.0)
    provider = _FailIfCalledProvider()
    existing_trade = {
        "request_id": "EXISTING-SHORT",
        "currency_pair": "EUR/USD",
        "direction": "Short",
        "position_size": 10_000,
        "status": "FILLED",
        "created_at": "2026-07-16T11:00:00+00:00",
    }
    state = _RecordingStateManager((existing_trade,))

    result, broker, state = _process_trade(
        monkeypatch,
        request_id="REQ-DAILY-FULLY-NETTED",
        account_snapshot=snapshot,
        provider=provider,
        state_manager=state,
    )

    assert result == {
        "approval_status": "Netted",
        "reason": "Fully netted against 1 existing position(s)",
        "execution_result": None,
    }
    assert state.closed_request_ids == ["EXISTING-SHORT"]
    assert state.get_all_trades()[0]["status"] == "CLOSED"
    assert provider.calls == []
    assert broker.calls == []
    assert state.recorded_trades == []
