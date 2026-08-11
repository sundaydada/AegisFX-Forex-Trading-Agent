"""One failed poll must not end an unattended campaign.

A broker, network, or ledger exception is logged and skipped; the next
normal poll is the only recovery attempt. A failed pass must take no
trading action and change no campaign state, and a completed campaign
must still stop without touching any collaborator.

No OANDA call, no network, no real database, no real sleeping.
"""

import pytest

import autonomy_usdcad_campaign as campaign
from autonomy_usdcad_forward_test import poll_once, run_campaign


class _StopLoop(Exception):
    """Raised by the fake sleep to end the loop under test."""


class _CountingSleep:
    """Stands in for time.sleep; ends the loop after N intervals."""

    def __init__(self, stop_after):
        self._stop_after = stop_after
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)
        if len(self.calls) >= self._stop_after:
            raise _StopLoop()


class _RaisingBroker:
    """Every broker read fails, as an OANDA 401 did in production."""

    def __init__(self, error):
        self._error = error
        self.get_open_positions_calls = 0
        self.place_order_calls = []
        self.close_position_calls = []

    def get_open_positions(self):
        self.get_open_positions_calls += 1
        raise self._error


class _FlatBroker:
    """A healthy broker reporting no open position."""

    def __init__(self):
        self.get_open_positions_calls = 0
        self.place_order_calls = []
        self.close_position_calls = []

    def get_open_positions(self):
        self.get_open_positions_calls += 1
        return []


class _PositionBroker:
    """A healthy broker reporting one open USD/CAD position."""

    def __init__(self):
        self.get_open_positions_calls = 0
        self.place_order_calls = []
        self.close_position_calls = []

    def get_open_positions(self):
        self.get_open_positions_calls += 1
        return [{"currency_pair": "USD/CAD", "direction": "Short"}]


class _Ledger:
    def __init__(self, trades=None):
        self._trades = list(trades or [])
        self.get_all_trades_calls = 0
        self.close_trade_calls = []

    def get_all_trades(self):
        self.get_all_trades_calls += 1
        return [dict(trade) for trade in self._trades]

    def close_trade(self, request_id, *, close_price, exit_timestamp):
        self.close_trade_calls.append(request_id)


class _NeverCycle:
    """Any cycle collaborator access fails the test."""

    def __call__(self, *args, **kwargs):
        raise AssertionError("no cycle may run")


def _deps(broker, ledger):
    return {
        "broker": broker,
        "state_manager": ledger,
        "signal_provider": _NeverCycle(),
        "proposal_queue": _NeverCycle(),
        "executor": _NeverCycle(),
    }


def test_failed_pass_is_survived_and_next_pass_proceeds(tmp_path, capsys):
    """A raising pass is logged and skipped, then polling continues."""

    class _FirstCallFails:
        def __init__(self):
            self.get_open_positions_calls = 0
            self.place_order_calls = []

        def get_open_positions(self):
            self.get_open_positions_calls += 1
            if self.get_open_positions_calls == 1:
                raise RuntimeError(
                    "Failed to get open positions: OANDA API error 401:"
                    ' {"errorMessage":"Insufficient authorization"}'
                )
            return [{"currency_pair": "USD/CAD", "direction": "Short"}]

    broker = _FirstCallFails()
    ledger = _Ledger([{"status": "FILLED", "broker_trade_id": "288"}])
    state = campaign.new_campaign()
    sleep = _CountingSleep(stop_after=2)

    with pytest.raises(_StopLoop):
        run_campaign(
            dependencies=_deps(broker, ledger),
            state=state,
            state_path=str(tmp_path / "state.json"),
            sleep=sleep,
        )

    # The runner survived the failure and attempted a second pass.
    assert broker.get_open_positions_calls == 2
    assert sleep.calls == [60.0, 60.0], (
        f"the normal interval must be used; got {sleep.calls!r}"
    )

    output = capsys.readouterr().out
    assert "campaign_pass_failed" in output
    assert "RuntimeError" in output
    assert "401" in output
    # The healthy second pass reported a normal status.
    assert "POSITION_PRESENT" in output


def test_failed_pass_makes_no_trade_and_no_count_change(tmp_path):
    """Nothing is counted, traded, or written by a failed pass."""

    broker = _RaisingBroker(RuntimeError("OANDA API error 401"))
    ledger = _Ledger()
    state = campaign.new_campaign()
    state_path = tmp_path / "state.json"
    sleep = _CountingSleep(stop_after=1)

    with pytest.raises(_StopLoop):
        run_campaign(
            dependencies=_deps(broker, ledger),
            state=state,
            state_path=str(state_path),
            sleep=sleep,
        )

    assert campaign.completed_closed_trades(state) == 0
    assert state["open_context"] == {}
    assert state["status"] == "RUNNING"
    assert broker.place_order_calls == []
    assert ledger.close_trade_calls == []
    # A failed pass writes no campaign state at all.
    assert not state_path.exists()


def test_repeated_exceptions_never_create_trades(tmp_path, capsys):
    """Five consecutive failures still open nothing and count nothing."""

    broker = _RaisingBroker(ConnectionError("network down"))
    ledger = _Ledger()
    state = campaign.new_campaign()
    sleep = _CountingSleep(stop_after=5)

    with pytest.raises(_StopLoop):
        run_campaign(
            dependencies=_deps(broker, ledger),
            state=state,
            state_path=str(tmp_path / "state.json"),
            sleep=sleep,
        )

    assert broker.get_open_positions_calls == 5
    assert broker.place_order_calls == []
    assert campaign.completed_closed_trades(state) == 0
    assert state["status"] == "RUNNING"
    assert capsys.readouterr().out.count("campaign_pass_failed") == 5


def test_healthy_pass_proceeds_normally(tmp_path, capsys):
    """With a position open the runner waits, exactly as before."""

    broker = _PositionBroker()
    ledger = _Ledger([{"status": "FILLED", "broker_trade_id": "288"}])
    state = campaign.new_campaign()
    state_path = tmp_path / "state.json"
    sleep = _CountingSleep(stop_after=1)

    with pytest.raises(_StopLoop):
        run_campaign(
            dependencies=_deps(broker, ledger),
            state=state,
            state_path=str(state_path),
            sleep=sleep,
        )

    assert "POSITION_PRESENT" in capsys.readouterr().out
    assert campaign.completed_closed_trades(state) == 0
    assert state_path.exists(), "a successful pass persists campaign state"


def test_complete_campaign_exits_without_touching_dependencies(tmp_path):
    """A finished campaign stops and reaches no collaborator at all."""

    state = campaign.new_campaign()
    for number in range(50):
        campaign.record_closed_trade(
            state, {"broker_trade_id": str(number), "realized_pl": 1.0}
        )
    assert state["status"] == "COMPLETE"

    def _never_sleep(seconds):
        raise AssertionError("a complete campaign must not poll")

    # dependencies=None: any collaborator access would raise TypeError.
    summary = run_campaign(
        dependencies=None,
        state=state,
        state_path=str(tmp_path / "state.json"),
        sleep=_never_sleep,
    )

    assert summary["status"] == "COMPLETE"
    assert summary["completed_closed_trades"] == 50
    assert summary["remaining_trades"] == 0

    # Defence in depth: a direct pass is refused before any read.
    assert poll_once(dependencies=None, state=state) == "COMPLETE"


def test_keyboard_interrupt_is_not_swallowed(tmp_path):
    """Only Exception is caught; operator termination still works."""

    class _InterruptingBroker:
        def get_open_positions(self):
            raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        run_campaign(
            dependencies=_deps(_InterruptingBroker(), _Ledger()),
            state=campaign.new_campaign(),
            state_path=str(tmp_path / "state.json"),
            sleep=_CountingSleep(stop_after=1),
        )


def test_system_exit_is_not_swallowed(tmp_path):
    """SystemExit still terminates the runner."""

    class _ExitingBroker:
        def get_open_positions(self):
            raise SystemExit(1)

    with pytest.raises(SystemExit):
        run_campaign(
            dependencies=_deps(_ExitingBroker(), _Ledger()),
            state=campaign.new_campaign(),
            state_path=str(tmp_path / "state.json"),
            sleep=_CountingSleep(stop_after=1),
        )
