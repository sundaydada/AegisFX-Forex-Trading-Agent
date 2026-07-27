"""Fail-closed contract for the USD/CAD autonomous cycle.

When the broker's open positions and the local trade ledger disagree,
the system cannot know its true exposure. The cycle must stop and
report: it may not generate a signal, create or approve a proposal,
execute, or close anything until an operator resolves the mismatch.

The cycle module does not exist yet, so it is imported inside the test
rather than at module scope — collection stays clean either way.

Every collaborator here is an in-memory fake. This test touches no
network, environment variable, SQLite database, or market data.
"""

import importlib

import pytest


MODULE_NAME = "autonomy_usdcad_cycle"

BROKER_POSITION = {
    "currency_pair": "USD/CAD",
    "direction": "Long",
    "units": 55383.0,
    "unrealized_pl": 0.0,
    "average_price": 1.41271,
}

LOCAL_FILLED_TRADE = {
    "request_id": "AI-PROPOSAL-PROP-TEST-MISMATCH",
    "currency_pair": "USD/CAD",
    "direction": "Long",
    "position_size": 55383,
    "status": "FILLED",
    "fill_price": 1.41271,
}


class _RecordingBroker:
    def __init__(self, open_positions):
        self._open_positions = list(open_positions)
        self.get_open_positions_calls = 0
        self.close_position_calls = []

    def get_open_positions(self):
        self.get_open_positions_calls += 1
        return [dict(p) for p in self._open_positions]

    def close_position(self, *args, **kwargs):
        self.close_position_calls.append((args, kwargs))
        return {
            "status": "SUCCESS",
            "close_price": 1.40000,
            "units_closed": 1.0,
            "timestamp": "1970-01-01T00:00:00.000000000Z",
        }


class _RecordingStateManager:
    def __init__(self, trades):
        self._trades = list(trades)
        self.get_all_trades_calls = 0

    def get_all_trades(self):
        self.get_all_trades_calls += 1
        return [dict(t) for t in self._trades]


class _RecordingSignalProvider:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {
            "trade_bias": "LONG",
            "confidence": 85,
            "execution_allowed": True,
        }


class _RecordingProposalQueue:
    def __init__(self):
        self.add_proposals_calls = []
        self.approve_proposal_calls = []

    def add_proposals(self, proposals):
        self.add_proposals_calls.append(proposals)
        return len(proposals)

    def approve_proposal(self, proposal_id):
        self.approve_proposal_calls.append(proposal_id)
        return True


class _RecordingExecutor:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {"success": True, "message": "must never be reached"}


def _failure_reason(result):
    for key in ("reason", "message", "failure_reason"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


@pytest.mark.parametrize(
    "label, open_positions, local_trades",
    [
        ("broker_open_local_empty", [BROKER_POSITION], []),
        ("broker_flat_local_filled", [], [LOCAL_FILLED_TRADE]),
    ],
)
def test_cycle_fails_closed_on_broker_local_position_mismatch(
    label,
    open_positions,
    local_trades,
):
    cycle = importlib.import_module(MODULE_NAME)

    broker = _RecordingBroker(open_positions)
    state_manager = _RecordingStateManager(local_trades)
    signal_provider = _RecordingSignalProvider()
    proposal_queue = _RecordingProposalQueue()
    executor = _RecordingExecutor()

    result = cycle.run_cycle(
        broker=broker,
        state_manager=state_manager,
        signal_provider=signal_provider,
        proposal_queue=proposal_queue,
        executor=executor,
    )

    assert isinstance(result, dict), (
        f"[{label}] run_cycle must return a structured result mapping"
    )
    assert result.get("outcome") == "BLOCKED_STATE_MISMATCH", (
        f"[{label}] a broker/local position mismatch must block the cycle;"
        f" got outcome {result.get('outcome')!r}"
    )
    assert _failure_reason(result), (
        f"[{label}] a blocked cycle must explain the mismatch in a"
        " non-empty reason/message field"
    )

    assert signal_provider.calls == [], (
        f"[{label}] no signal may be generated while exposure is unknown"
    )
    assert proposal_queue.add_proposals_calls == [], (
        f"[{label}] no proposal may be created while exposure is unknown"
    )
    assert proposal_queue.approve_proposal_calls == [], (
        f"[{label}] no proposal may be approved while exposure is unknown"
    )
    assert executor.calls == [], (
        f"[{label}] no execution may occur while exposure is unknown"
    )
    assert broker.close_position_calls == [], (
        f"[{label}] a mismatch must be reported, never auto-resolved by"
        " sending a broker close"
    )
