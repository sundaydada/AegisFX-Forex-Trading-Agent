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

PRACTICE_BASE_URL = "https://api-fxpractice.oanda.com"

USDCAD_SIGNAL = {
    "trade_bias": "LONG",
    "confidence": 85,
    "execution_allowed": True,
}

# The approval queue owns identity and lifecycle. A caller submitting a
# proposal must not supply any of these.
QUEUE_OWNED_FIELDS = (
    "proposal_id",
    "id",
    "status",
    "created_at",
    "approved_at",
    "executed_at",
)

# On the autonomous path the cycle owns proposal_id, because it needs a
# deterministic identity handoff to approve exactly what it created.
# Everything else below remains the queue's to assign.
AUTONOMOUS_QUEUE_OWNED_FIELDS = (
    "id",
    "status",
    "created_at",
    "approved_at",
    "executed_at",
)

# Fixed MVP stop rule for USD/CAD.
USDCAD_PIP_SIZE = 0.0001
AUTONOMOUS_STOP_DISTANCE_PIPS = 20.0

USDCAD_QUOTE = {
    "currency_pair": "USD/CAD",
    "bid": 1.40800,
    "ask": 1.40820,
    "timestamp": "2026-07-27T12:00:00Z",
}

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
    def __init__(
        self,
        open_positions,
        base_url=PRACTICE_BASE_URL,
        quote=None,
        call_order=None,
    ):
        self.base_url = base_url
        self._open_positions = list(open_positions)
        self._quote = quote
        self._call_order = call_order
        self.get_open_positions_calls = 0
        self.close_position_calls = []
        self.get_quote_calls = []
        self.place_order_calls = []

    def get_open_positions(self):
        self.get_open_positions_calls += 1
        return [dict(p) for p in self._open_positions]

    def get_quote(self, pair):
        self.get_quote_calls.append(pair)
        if self._call_order is not None:
            self._call_order.append("get_quote")
        if self._quote is None:
            return None
        return dict(self._quote)

    def place_order(self, order):
        self.place_order_calls.append(order)
        return {"execution_status": "Filled"}

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
    def __init__(self, signal=None):
        self._signal = USDCAD_SIGNAL if signal is None else signal
        self.calls = []
        self.returned = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        signal = dict(self._signal)
        self.returned.append(signal)
        return signal


class _RecordingProposalQueue:
    def __init__(
        self,
        add_result=None,
        approve_result=True,
        call_order=None,
        approved_proposals=None,
    ):
        # add_result is compared against None rather than tested for
        # truthiness so a configured 0 is honoured.
        self._add_result = add_result
        self._approve_result = approve_result
        self._call_order = call_order
        self._approved_proposals = (
            [] if approved_proposals is None else list(approved_proposals)
        )
        self.add_proposals_calls = []
        self.approve_proposal_calls = []
        self.get_approved_proposals_calls = 0
        self.returned_approved_proposals = []

    def get_approved_proposals(self):
        self.get_approved_proposals_calls += 1
        if self._call_order is not None:
            self._call_order.append("get_approved_proposals")
        returned = [dict(proposal) for proposal in self._approved_proposals]
        self.returned_approved_proposals.append(returned)
        return returned

    def add_proposals(self, proposals):
        self.add_proposals_calls.append(proposals)
        if self._call_order is not None:
            self._call_order.append("add_proposals")
        if self._add_result is None:
            return len(proposals)
        return self._add_result

    def approve_proposal(self, proposal_id):
        self.approve_proposal_calls.append(proposal_id)
        if self._call_order is not None:
            self._call_order.append("approve_proposal")
        return self._approve_result


class _RecordingExecutor:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {"success": True, "message": "must never be reached"}


class _RecordingBoundExecutor:
    """A pre-bound execute_reviewed_proposal_from_dashboard.

    Credentials, account id, practice URL, database paths, exposure
    limit, quote-age limit, and clock are already supplied externally,
    so the cycle passes only the proposal and its protective stop.
    """

    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, *, proposal, raw_stop_loss_price):
        self.calls.append(
            {
                "proposal": proposal,
                "raw_stop_loss_price": raw_stop_loss_price,
            }
        )
        return self.result


def _approved_record(proposal_id):
    """The APPROVED record the queue returns for a cycle's own proposal."""
    return {
        "proposal_id": proposal_id,
        "pair": "USD/CAD",
        "direction": "LONG",
        "suggested_size": 0.5,
        "confidence": 85,
        "strategy": "Autonomous_USDCAD_MVP",
        "reason": (
            "Autonomous USD/CAD MVP proposal generated from an accepted"
            " signal."
        ),
        "status": "APPROVED",
    }


def _filled_executor(proposal_id):
    """A pre-bound executor reporting a strict Filled success."""
    return _RecordingBoundExecutor(
        {
            "success": True,
            "message": "Approved",
            "request_id": f"AI-PROPOSAL-{proposal_id}",
            "execution_result": {
                "execution_status": "Filled",
                "broker_order_id": "999",
                "currency_pair": "USD/CAD",
                "direction": "Long",
                "units": 26000.0,
                "fill_price": 1.40821,
                "timestamp": "2026-07-27T12:00:00.000000000Z",
            },
        }
    )


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


@pytest.mark.parametrize(
    "label, base_url",
    [
        ("live_endpoint", "https://api-fxtrade.oanda.com"),
        ("missing_endpoint", None),
    ],
)
def test_cycle_blocks_non_practice_environment_before_state_or_trade_actions(
    label,
    base_url,
):
    cycle = importlib.import_module(MODULE_NAME)

    # Empty on both sides: counts agree, so only the environment guard
    # can block this cycle.
    broker = _RecordingBroker([], base_url=base_url)
    state_manager = _RecordingStateManager([])
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
    assert result.get("outcome") == "BLOCKED_NON_PRACTICE_ENVIRONMENT", (
        f"[{label}] a non-practice broker endpoint must block the cycle;"
        f" got outcome {result.get('outcome')!r}"
    )
    reason = _failure_reason(result)
    assert reason, (
        f"[{label}] a blocked cycle must explain the refusal in a"
        " non-empty reason/message field"
    )
    assert "practice" in reason.lower(), (
        f"[{label}] the reason must identify that autonomous trading"
        f" requires the OANDA practice environment; got {reason!r}"
    )

    assert broker.get_open_positions_calls == 0, (
        f"[{label}] the environment guard must run before any broker"
        " position read"
    )
    assert state_manager.get_all_trades_calls == 0, (
        f"[{label}] the environment guard must run before any local"
        " state read"
    )
    assert signal_provider.calls == [], (
        f"[{label}] no signal may be generated outside the practice"
        " environment"
    )
    assert proposal_queue.add_proposals_calls == [], (
        f"[{label}] no proposal may be created outside the practice"
        " environment"
    )
    assert proposal_queue.approve_proposal_calls == [], (
        f"[{label}] no proposal may be approved outside the practice"
        " environment"
    )
    assert executor.calls == [], (
        f"[{label}] no execution may occur outside the practice"
        " environment"
    )
    assert broker.close_position_calls == [], (
        f"[{label}] no broker close may be attempted outside the"
        " practice environment"
    )


def test_cycle_requests_one_usdcad_signal_when_practice_and_flat(monkeypatch):
    cycle = importlib.import_module(MODULE_NAME)

    expected_proposal_id = "PROP-AUTO-TEST-SIGNAL-0000000001"
    monkeypatch.setattr(
        cycle,
        "_new_proposal_id",
        lambda: expected_proposal_id,
    )

    # Practice endpoint by default, and flat on both sides: the
    # environment guard and the reconciliation check both pass.
    broker = _RecordingBroker([], quote=USDCAD_QUOTE)
    state_manager = _RecordingStateManager([])
    signal_provider = _RecordingSignalProvider()
    proposal_queue = _RecordingProposalQueue(
        approved_proposals=[_approved_record(expected_proposal_id)],
    )
    executor = _filled_executor(expected_proposal_id)

    result = cycle.run_cycle(
        broker=broker,
        state_manager=state_manager,
        signal_provider=signal_provider,
        proposal_queue=proposal_queue,
        executor=executor,
    )

    assert isinstance(result, dict), (
        "run_cycle must return a structured result mapping"
    )
    assert result.get("outcome") == "PROPOSAL_EXECUTED", (
        "a practice, reconciled, flat cycle must evaluate one signal and"
        " create, approve and execute one proposal;"
        f" got outcome {result.get('outcome')!r}"
    )
    assert _failure_reason(result), (
        "the result must carry a non-empty reason/message field"
    )
    assert result.get("pair") == "USD/CAD", (
        f"the cycle must report the USD/CAD pair; got {result.get('pair')!r}"
    )

    assert len(signal_provider.calls) == 1, (
        "the signal provider must be called exactly once;"
        f" got {len(signal_provider.calls)} call(s)"
    )
    call_args, call_kwargs = signal_provider.calls[0]
    assert call_args == (), (
        f"the signal provider must receive no positional arguments;"
        f" got {call_args!r}"
    )
    assert call_kwargs == {"pair": "USD/CAD"}, (
        "the signal provider must be called as signal_provider("
        f'pair="USD/CAD"); got {call_kwargs!r}'
    )

    assert result.get("signal") == signal_provider.returned[0], (
        "the result must carry the exact signal the provider returned"
    )
    assert result.get("signal") == USDCAD_SIGNAL, (
        f"the returned signal must be unaltered; got {result.get('signal')!r}"
    )

    assert broker.get_open_positions_calls == 1, (
        "the broker's open positions must be read exactly once;"
        f" got {broker.get_open_positions_calls}"
    )
    assert state_manager.get_all_trades_calls == 1, (
        "the local ledger must be read exactly once;"
        f" got {state_manager.get_all_trades_calls}"
    )
    assert result.get("broker_open_count") == 0, (
        f"got {result.get('broker_open_count')!r}"
    )
    assert result.get("local_filled_count") == 0, (
        f"got {result.get('local_filled_count')!r}"
    )

    assert len(proposal_queue.add_proposals_calls) == 1, (
        "an accepted signal must submit proposals exactly once;"
        f" got {len(proposal_queue.add_proposals_calls)} call(s)"
    )
    submitted_batch = proposal_queue.add_proposals_calls[0]
    assert isinstance(submitted_batch, list), (
        f"add_proposals must receive a list; got {type(submitted_batch)!r}"
    )
    assert len(submitted_batch) == 1, (
        f"exactly one proposal must be submitted; got {len(submitted_batch)}"
    )
    assert submitted_batch[0].get("pair") == "USD/CAD", (
        f"got submitted pair {submitted_batch[0].get('pair')!r}"
    )
    submitted_id = submitted_batch[0].get("proposal_id")
    assert isinstance(submitted_id, str) and submitted_id.strip(), (
        f"the submitted proposal must carry a proposal_id;"
        f" got {submitted_id!r}"
    )
    assert submitted_id.startswith("PROP-AUTO-"), (
        f"the cycle-owned id must be namespaced; got {submitted_id!r}"
    )
    assert proposal_queue.approve_proposal_calls == [submitted_id], (
        "approve_proposal must be called exactly once with the submitted"
        f" proposal's id; got {proposal_queue.approve_proposal_calls!r}"
    )
    assert result.get("proposal_id") == submitted_id, (
        f"got result proposal_id {result.get('proposal_id')!r}"
    )
    assert result.get("approval_succeeded") is True, (
        f"got approval_succeeded {result.get('approval_succeeded')!r}"
    )

    assert proposal_queue.get_approved_proposals_calls == 1, (
        f"got {proposal_queue.get_approved_proposals_calls}"
    )
    approved_record = proposal_queue.returned_approved_proposals[0][0]
    assert broker.get_quote_calls == ["USD/CAD"], (
        f"got {broker.get_quote_calls!r}"
    )
    assert len(executor.calls) == 1, (
        f"got {len(executor.calls)} executor call(s)"
    )
    assert executor.calls[0]["proposal"] is approved_record, (
        "the executor must receive the queue-returned APPROVED record"
    )
    assert executor.calls[0]["raw_stop_loss_price"] == 1.40620, (
        f"got {executor.calls[0]['raw_stop_loss_price']!r}"
    )
    assert result.get("proposal") is approved_record
    assert result.get("stop_loss_price") == 1.40620, (
        f"got {result.get('stop_loss_price')!r}"
    )
    assert result.get("execution_succeeded") is True, (
        f"got {result.get('execution_succeeded')!r}"
    )
    assert broker.place_order_calls == [], (
        "the cycle must never submit an order directly through the broker"
    )
    assert broker.close_position_calls == [], (
        "no broker close may be attempted in this slice"
    )


# A signal is tradeable only when all three hold:
#   trade_bias is exactly "LONG" or "SHORT"
#   confidence is numeric and >= 70
#   execution_allowed is exactly True
# Each case below violates exactly one of them.
@pytest.mark.parametrize(
    "label, signal, reason_terms",
    [
        (
            "neutral_bias",
            {
                "trade_bias": "NEUTRAL",
                "confidence": 85,
                "execution_allowed": True,
            },
            ("bias", "neutral"),
        ),
        (
            "low_confidence",
            {
                "trade_bias": "LONG",
                "confidence": 69,
                "execution_allowed": True,
            },
            ("confidence",),
        ),
        (
            "execution_not_allowed",
            {
                "trade_bias": "LONG",
                "confidence": 85,
                "execution_allowed": False,
            },
            ("execution", "allowed"),
        ),
    ],
)
def test_cycle_rejects_non_tradeable_signal_without_downstream_actions(
    label,
    signal,
    reason_terms,
):
    cycle = importlib.import_module(MODULE_NAME)

    broker = _RecordingBroker([])
    state_manager = _RecordingStateManager([])
    signal_provider = _RecordingSignalProvider(signal)
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
    assert result.get("outcome") == "SIGNAL_REJECTED_NO_ACTION", (
        f"[{label}] a non-tradeable signal must be rejected;"
        f" got outcome {result.get('outcome')!r}"
    )
    reason = _failure_reason(result)
    assert reason, (
        f"[{label}] a rejected signal must carry a non-empty"
        " reason/message field"
    )
    assert any(term in reason.lower() for term in reason_terms), (
        f"[{label}] the reason must identify the rejection cause"
        f" (expected one of {reason_terms}); got {reason!r}"
    )

    assert result.get("pair") == "USD/CAD", (
        f"[{label}] got pair {result.get('pair')!r}"
    )
    assert result.get("signal") == signal_provider.returned[0], (
        f"[{label}] the result must carry the exact signal the provider"
        " returned"
    )
    assert result.get("signal") == signal, (
        f"[{label}] the rejected signal must be unaltered;"
        f" got {result.get('signal')!r}"
    )
    assert result.get("broker_open_count") == 0, (
        f"[{label}] got {result.get('broker_open_count')!r}"
    )
    assert result.get("local_filled_count") == 0, (
        f"[{label}] got {result.get('local_filled_count')!r}"
    )

    assert len(signal_provider.calls) == 1, (
        f"[{label}] the signal provider must be called exactly once;"
        f" got {len(signal_provider.calls)} call(s)"
    )
    call_args, call_kwargs = signal_provider.calls[0]
    assert call_args == (), (
        f"[{label}] the signal provider must receive no positional"
        f" arguments; got {call_args!r}"
    )
    assert call_kwargs == {"pair": "USD/CAD"}, (
        f"[{label}] the signal provider must be called as"
        f' signal_provider(pair="USD/CAD"); got {call_kwargs!r}'
    )

    assert proposal_queue.add_proposals_calls == [], (
        f"[{label}] a rejected signal must not create a proposal"
    )
    assert proposal_queue.approve_proposal_calls == [], (
        f"[{label}] a rejected signal must not approve a proposal"
    )
    assert executor.calls == [], (
        f"[{label}] a rejected signal must not reach execution"
    )
    assert broker.close_position_calls == [], (
        f"[{label}] a rejected signal must not trigger a broker close"
    )


def test_cycle_creates_one_pending_proposal_for_tradeable_signal(monkeypatch):
    cycle = importlib.import_module(MODULE_NAME)

    expected_proposal_id = "PROP-AUTO-TEST-CREATE-0000000001"
    monkeypatch.setattr(
        cycle,
        "_new_proposal_id",
        lambda: expected_proposal_id,
    )

    broker = _RecordingBroker([], quote=USDCAD_QUOTE)
    state_manager = _RecordingStateManager([])
    signal_provider = _RecordingSignalProvider()  # LONG / 85 / True
    proposal_queue = _RecordingProposalQueue(
        approved_proposals=[_approved_record(expected_proposal_id)],
    )
    executor = _filled_executor(expected_proposal_id)

    result = cycle.run_cycle(
        broker=broker,
        state_manager=state_manager,
        signal_provider=signal_provider,
        proposal_queue=proposal_queue,
        executor=executor,
    )

    assert isinstance(result, dict), (
        "run_cycle must return a structured result mapping"
    )
    assert result.get("outcome") == "PROPOSAL_EXECUTED", (
        "a tradeable signal must produce one submitted, approved and"
        f" executed proposal; got outcome {result.get('outcome')!r}"
    )
    assert _failure_reason(result), (
        "the result must carry a non-empty reason/message field"
    )

    # --- exactly one submission carrying exactly one proposal ---
    assert len(proposal_queue.add_proposals_calls) == 1, (
        "add_proposals must be called exactly once;"
        f" got {len(proposal_queue.add_proposals_calls)} call(s)"
    )
    submitted_batch = proposal_queue.add_proposals_calls[0]
    assert isinstance(submitted_batch, list), (
        f"add_proposals must receive a list; got {type(submitted_batch)!r}"
    )
    assert len(submitted_batch) == 1, (
        f"exactly one proposal must be submitted; got {len(submitted_batch)}"
    )
    submitted = submitted_batch[0]
    assert isinstance(submitted, dict), (
        f"the submitted proposal must be a dictionary; got {type(submitted)!r}"
    )

    signal = signal_provider.returned[0]

    # --- pinned proposal content ---
    assert "pair" not in signal, (
        "the fixture signal carries no pair, so a correct USD/CAD value"
        " proves the pair came from the fixed MVP pair"
    )
    assert submitted.get("pair") == "USD/CAD", (
        f"got pair {submitted.get('pair')!r}"
    )
    assert submitted.get("direction") == signal["trade_bias"], (
        "direction must come from the signal's trade_bias;"
        f" got {submitted.get('direction')!r}"
    )
    assert submitted.get("direction") == "LONG"
    assert submitted.get("suggested_size") == 0.5, (
        "the real ProposalApprovalQueue reads suggested_size;"
        f" got {submitted.get('suggested_size')!r}"
    )
    assert "size" not in submitted, (
        "the autonomous proposal must use suggested_size, not the"
        " ignored size field"
    )
    assert submitted.get("confidence") == signal["confidence"], (
        "confidence must come from the signal;"
        f" got {submitted.get('confidence')!r}"
    )
    assert submitted.get("confidence") == 85
    assert submitted.get("strategy") == "Autonomous_USDCAD_MVP", (
        f"got strategy {submitted.get('strategy')!r}"
    )
    submitted_reason = submitted.get("reason")
    assert isinstance(submitted_reason, str) and submitted_reason.strip(), (
        f"the proposal reason must be a non-empty string;"
        f" got {submitted_reason!r}"
    )
    assert "autonomous" in submitted_reason.lower(), (
        f"the reason must identify the trade as autonomous;"
        f" got {submitted_reason!r}"
    )
    assert "usd/cad" in submitted_reason.lower(), (
        f"the reason must name the USD/CAD pair; got {submitted_reason!r}"
    )

    # --- queue-owned lifecycle fields must not be supplied ---
    # proposal_id is intentionally absent from this set: the cycle owns
    # identity so it can approve exactly the proposal it created.
    for field in AUTONOMOUS_QUEUE_OWNED_FIELDS:
        assert field not in submitted, (
            f"the caller must not supply the queue-owned field {field!r};"
            f" got {submitted.get(field)!r}"
        )
    submitted_id = submitted.get("proposal_id")
    assert isinstance(submitted_id, str) and submitted_id.strip(), (
        f"the submitted proposal must carry a proposal_id;"
        f" got {submitted_id!r}"
    )
    assert submitted_id.startswith("PROP-AUTO-"), (
        f"the cycle-owned id must be namespaced; got {submitted_id!r}"
    )

    # --- result shape ---
    assert result.get("pair") == "USD/CAD"
    assert result.get("signal") == signal, (
        "the result must carry the unaltered provider signal"
    )
    assert proposal_queue.get_approved_proposals_calls == 1, (
        f"got {proposal_queue.get_approved_proposals_calls}"
    )
    approved_record = proposal_queue.returned_approved_proposals[0][0]
    assert result.get("proposal") is approved_record, (
        "the result must carry the exact APPROVED record that was executed"
    )
    assert result.get("proposal_count") == 1, (
        f"got proposal_count {result.get('proposal_count')!r}"
    )
    assert result.get("stop_loss_price") == 1.40620, (
        f"got {result.get('stop_loss_price')!r}"
    )
    assert result.get("execution_succeeded") is True, (
        f"got {result.get('execution_succeeded')!r}"
    )
    assert result.get("broker_open_count") == 0
    assert result.get("local_filled_count") == 0

    # --- collaborator call counts ---
    assert broker.get_open_positions_calls == 1, (
        f"got {broker.get_open_positions_calls}"
    )
    assert state_manager.get_all_trades_calls == 1, (
        f"got {state_manager.get_all_trades_calls}"
    )
    assert len(signal_provider.calls) == 1, (
        f"got {len(signal_provider.calls)} signal call(s)"
    )
    call_args, call_kwargs = signal_provider.calls[0]
    assert call_args == ()
    assert call_kwargs == {"pair": "USD/CAD"}
    assert proposal_queue.approve_proposal_calls == [submitted_id], (
        "approve_proposal must be called exactly once with the submitted"
        f" proposal's id; got {proposal_queue.approve_proposal_calls!r}"
    )
    assert result.get("proposal_id") == submitted_id, (
        f"got result proposal_id {result.get('proposal_id')!r}"
    )
    assert result.get("approval_succeeded") is True, (
        f"got approval_succeeded {result.get('approval_succeeded')!r}"
    )

    assert broker.get_quote_calls == ["USD/CAD"], (
        f"got {broker.get_quote_calls!r}"
    )
    assert len(executor.calls) == 1, (
        f"got {len(executor.calls)} executor call(s)"
    )
    assert executor.calls[0]["proposal"] is approved_record, (
        "the executor must receive the queue-returned APPROVED record"
    )
    assert executor.calls[0]["raw_stop_loss_price"] == 1.40620, (
        f"got {executor.calls[0]['raw_stop_loss_price']!r}"
    )
    assert broker.place_order_calls == [], (
        "the cycle must never submit an order directly through the broker"
    )
    assert broker.close_position_calls == [], (
        "no broker close may be attempted in this slice"
    )


def test_cycle_auto_approves_exactly_the_proposal_it_created(monkeypatch):
    cycle = importlib.import_module(MODULE_NAME)

    # The production implementation will own proposal identity through a
    # _new_proposal_id() factory; pin it so the test needs no randomness.
    expected_proposal_id = "PROP-AUTO-TEST-0000000000000001"
    monkeypatch.setattr(
        cycle,
        "_new_proposal_id",
        lambda: expected_proposal_id,
        raising=False,
    )

    broker = _RecordingBroker([], quote=USDCAD_QUOTE)
    state_manager = _RecordingStateManager([])
    signal_provider = _RecordingSignalProvider()  # LONG / 85 / True
    proposal_queue = _RecordingProposalQueue(
        approved_proposals=[_approved_record(expected_proposal_id)],
    )
    executor = _filled_executor(expected_proposal_id)

    result = cycle.run_cycle(
        broker=broker,
        state_manager=state_manager,
        signal_provider=signal_provider,
        proposal_queue=proposal_queue,
        executor=executor,
    )

    assert isinstance(result, dict), (
        "run_cycle must return a structured result mapping"
    )
    assert result.get("outcome") == "PROPOSAL_EXECUTED", (
        "an accepted signal must create, approve and execute exactly one"
        f" proposal; got outcome {result.get('outcome')!r}"
    )
    assert _failure_reason(result), (
        "the result must carry a non-empty reason/message field"
    )

    # --- exactly one submission carrying exactly one proposal ---
    assert len(proposal_queue.add_proposals_calls) == 1, (
        "add_proposals must be called exactly once;"
        f" got {len(proposal_queue.add_proposals_calls)} call(s)"
    )
    submitted_batch = proposal_queue.add_proposals_calls[0]
    assert isinstance(submitted_batch, list), (
        f"add_proposals must receive a list; got {type(submitted_batch)!r}"
    )
    assert len(submitted_batch) == 1, (
        f"exactly one proposal must be submitted; got {len(submitted_batch)}"
    )
    submitted_proposal = submitted_batch[0]
    assert isinstance(submitted_proposal, dict), (
        "the submitted proposal must be a dictionary;"
        f" got {type(submitted_proposal)!r}"
    )

    signal = signal_provider.returned[0]

    # --- the cycle supplies its own deterministic identity ---
    assert submitted_proposal.get("proposal_id") == expected_proposal_id, (
        "the submitted proposal must carry the id from _new_proposal_id();"
        f" got {submitted_proposal.get('proposal_id')!r}"
    )

    # --- preserved proposal content ---
    assert submitted_proposal.get("pair") == "USD/CAD", (
        f"got pair {submitted_proposal.get('pair')!r}"
    )
    assert submitted_proposal.get("direction") == signal["trade_bias"], (
        "direction must come from the signal's trade_bias;"
        f" got {submitted_proposal.get('direction')!r}"
    )
    assert submitted_proposal.get("direction") == "LONG"
    assert submitted_proposal.get("suggested_size") == 0.5, (
        "the real ProposalApprovalQueue reads suggested_size;"
        f" got {submitted_proposal.get('suggested_size')!r}"
    )
    assert "size" not in submitted_proposal, (
        "the autonomous proposal must use suggested_size, not the"
        " ignored size field"
    )
    assert submitted_proposal.get("confidence") == signal["confidence"], (
        "confidence must come from the signal;"
        f" got {submitted_proposal.get('confidence')!r}"
    )
    assert submitted_proposal.get("confidence") == 85
    assert submitted_proposal.get("strategy") == "Autonomous_USDCAD_MVP", (
        f"got strategy {submitted_proposal.get('strategy')!r}"
    )
    submitted_reason = submitted_proposal.get("reason")
    assert isinstance(submitted_reason, str) and submitted_reason.strip(), (
        f"the proposal reason must be a non-empty string;"
        f" got {submitted_reason!r}"
    )
    assert "autonomous" in submitted_reason.lower(), (
        f"the reason must identify the trade as autonomous;"
        f" got {submitted_reason!r}"
    )
    assert "usd/cad" in submitted_reason.lower(), (
        f"the reason must name the USD/CAD pair; got {submitted_reason!r}"
    )

    # --- lifecycle fields still owned by the queue must be omitted ---
    for field in AUTONOMOUS_QUEUE_OWNED_FIELDS:
        assert field not in submitted_proposal, (
            f"the caller must not supply the queue-owned field {field!r};"
            f" got {submitted_proposal.get(field)!r}"
        )

    # --- approval targets exactly the created proposal ---
    assert proposal_queue.approve_proposal_calls == [
        submitted_proposal["proposal_id"]
    ], (
        "approve_proposal must be called exactly once with the id stored"
        " in the submitted proposal;"
        f" got {proposal_queue.approve_proposal_calls!r}"
    )
    assert proposal_queue.approve_proposal_calls == [expected_proposal_id]

    # --- result shape ---
    assert result.get("pair") == "USD/CAD"
    assert result.get("signal") == signal, (
        "the result must carry the unaltered provider signal"
    )
    assert proposal_queue.get_approved_proposals_calls == 1, (
        f"got {proposal_queue.get_approved_proposals_calls}"
    )
    approved_record = proposal_queue.returned_approved_proposals[0][0]
    assert result.get("proposal") is approved_record, (
        "the result must carry the exact APPROVED record that was executed"
    )
    assert result.get("proposal_id") == submitted_proposal["proposal_id"], (
        "the reported proposal_id must match the submitted proposal;"
        f" got {result.get('proposal_id')!r}"
    )
    assert result.get("proposal_id") == expected_proposal_id
    assert result.get("proposal_count") == 1, (
        f"got proposal_count {result.get('proposal_count')!r}"
    )
    assert result.get("approval_succeeded") is True, (
        f"got approval_succeeded {result.get('approval_succeeded')!r}"
    )
    assert result.get("stop_loss_price") == 1.40620, (
        f"got {result.get('stop_loss_price')!r}"
    )
    assert result.get("execution_succeeded") is True, (
        f"got {result.get('execution_succeeded')!r}"
    )
    assert result.get("broker_open_count") == 0
    assert result.get("local_filled_count") == 0

    # --- collaborator call counts ---
    assert broker.get_open_positions_calls == 1, (
        f"got {broker.get_open_positions_calls}"
    )
    assert state_manager.get_all_trades_calls == 1, (
        f"got {state_manager.get_all_trades_calls}"
    )
    assert len(signal_provider.calls) == 1, (
        f"got {len(signal_provider.calls)} signal call(s)"
    )
    call_args, call_kwargs = signal_provider.calls[0]
    assert call_args == (), (
        f"the signal provider must receive no positional arguments;"
        f" got {call_args!r}"
    )
    assert call_kwargs == {"pair": "USD/CAD"}
    assert broker.get_quote_calls == ["USD/CAD"], (
        f"got {broker.get_quote_calls!r}"
    )
    assert len(executor.calls) == 1, (
        f"got {len(executor.calls)} executor call(s)"
    )
    assert executor.calls[0]["proposal"] is approved_record, (
        "the executor must receive the queue-returned APPROVED record"
    )
    assert executor.calls[0]["raw_stop_loss_price"] == 1.40620, (
        f"got {executor.calls[0]['raw_stop_loss_price']!r}"
    )
    assert broker.place_order_calls == [], (
        "the cycle must never submit an order directly through the broker"
    )
    assert broker.close_position_calls == [], (
        "no broker close may be attempted in this slice"
    )


def test_cycle_does_not_approve_when_proposal_was_not_inserted(monkeypatch):
    cycle = importlib.import_module(MODULE_NAME)

    expected_proposal_id = "PROP-AUTO-TEST-COLLISION-00000001"
    monkeypatch.setattr(
        cycle,
        "_new_proposal_id",
        lambda: expected_proposal_id,
        raising=False,
    )

    broker = _RecordingBroker([])
    state_manager = _RecordingStateManager([])
    signal_provider = _RecordingSignalProvider()  # LONG / 85 / True
    # add_proposals reports zero rows inserted (duplicate id or a failed
    # insert). approve_proposal stays truthy on purpose, so a stray call
    # would succeed and this test proves it never happened rather than
    # proving it merely failed.
    proposal_queue = _RecordingProposalQueue(
        add_result=0,
        approve_result=True,
    )
    executor = _RecordingExecutor()

    result = cycle.run_cycle(
        broker=broker,
        state_manager=state_manager,
        signal_provider=signal_provider,
        proposal_queue=proposal_queue,
        executor=executor,
    )

    assert isinstance(result, dict), (
        "run_cycle must return a structured result mapping"
    )
    assert result.get("outcome") == "BLOCKED_PROPOSAL_NOT_CREATED", (
        "a proposal that was not inserted must block the cycle;"
        f" got outcome {result.get('outcome')!r}"
    )
    reason = _failure_reason(result)
    assert reason, (
        "a blocked cycle must carry a non-empty reason/message field"
    )
    assert any(
        term in reason.lower()
        for term in ("insert", "creat", "duplicate", "collision")
    ), (
        "the reason must identify the failed insertion or duplicate;"
        f" got {reason!r}"
    )

    # --- exactly one submission carrying exactly one proposal ---
    assert len(proposal_queue.add_proposals_calls) == 1, (
        "add_proposals must be called exactly once;"
        f" got {len(proposal_queue.add_proposals_calls)} call(s)"
    )
    submitted_batch = proposal_queue.add_proposals_calls[0]
    assert isinstance(submitted_batch, list), (
        f"add_proposals must receive a list; got {type(submitted_batch)!r}"
    )
    assert len(submitted_batch) == 1, (
        f"exactly one proposal must be submitted; got {len(submitted_batch)}"
    )
    submitted_proposal = submitted_batch[0]
    assert isinstance(submitted_proposal, dict), (
        "the submitted proposal must be a dictionary;"
        f" got {type(submitted_proposal)!r}"
    )

    signal = signal_provider.returned[0]

    # --- the attempted proposal still carries its own identity ---
    assert submitted_proposal.get("proposal_id") == expected_proposal_id, (
        "the submitted proposal must carry the id from _new_proposal_id();"
        f" got {submitted_proposal.get('proposal_id')!r}"
    )
    assert submitted_proposal.get("pair") == "USD/CAD", (
        f"got pair {submitted_proposal.get('pair')!r}"
    )
    assert submitted_proposal.get("direction") == "LONG", (
        f"got direction {submitted_proposal.get('direction')!r}"
    )
    assert submitted_proposal.get("suggested_size") == 0.5, (
        "the real ProposalApprovalQueue reads suggested_size;"
        f" got {submitted_proposal.get('suggested_size')!r}"
    )
    assert submitted_proposal.get("confidence") == 85, (
        f"got confidence {submitted_proposal.get('confidence')!r}"
    )
    assert submitted_proposal.get("strategy") == "Autonomous_USDCAD_MVP", (
        f"got strategy {submitted_proposal.get('strategy')!r}"
    )

    # --- nothing downstream may run when nothing was inserted ---
    assert proposal_queue.approve_proposal_calls == [], (
        "a proposal that was not inserted must never be approved;"
        f" got {proposal_queue.approve_proposal_calls!r}"
    )
    assert executor.calls == [], (
        "a proposal that was not inserted must never reach execution"
    )
    assert broker.close_position_calls == [], (
        "a proposal that was not inserted must not trigger a broker close"
    )

    # --- result shape ---
    assert result.get("pair") == "USD/CAD"
    assert result.get("signal") == signal, (
        "the result must carry the unaltered provider signal"
    )
    assert result.get("proposal") is submitted_proposal, (
        "the result must carry the exact submitted proposal object"
    )
    assert result.get("proposal_id") == submitted_proposal["proposal_id"], (
        "the reported proposal_id must match the attempted proposal;"
        f" got {result.get('proposal_id')!r}"
    )
    assert result.get("proposal_id") == expected_proposal_id
    assert result.get("proposal_count") == 0, (
        f"got proposal_count {result.get('proposal_count')!r}"
    )
    assert result.get("approval_succeeded") is False, (
        f"got approval_succeeded {result.get('approval_succeeded')!r}"
    )
    assert result.get("broker_open_count") == 0
    assert result.get("local_filled_count") == 0

    # --- collaborator call counts ---
    assert broker.get_open_positions_calls == 1, (
        f"got {broker.get_open_positions_calls}"
    )
    assert state_manager.get_all_trades_calls == 1, (
        f"got {state_manager.get_all_trades_calls}"
    )
    assert len(signal_provider.calls) == 1, (
        f"got {len(signal_provider.calls)} signal call(s)"
    )
    call_args, call_kwargs = signal_provider.calls[0]
    assert call_args == (), (
        f"the signal provider must receive no positional arguments;"
        f" got {call_args!r}"
    )
    assert call_kwargs == {"pair": "USD/CAD"}


def test_cycle_executes_approved_proposal_once_with_mandatory_stop(
    monkeypatch,
):
    cycle = importlib.import_module(MODULE_NAME)

    expected_proposal_id = "PROP-AUTO-TEST-EXECUTE-00000001"
    monkeypatch.setattr(
        cycle,
        "_new_proposal_id",
        lambda: expected_proposal_id,
    )

    fake_execution_result = {
        "success": True,
        "message": "Approved",
        "request_id": f"AI-PROPOSAL-{expected_proposal_id}",
        "execution_result": {
            "execution_status": "Filled",
            "broker_order_id": "999",
            "currency_pair": "USD/CAD",
            "direction": "Long",
            "units": 26000.0,
            "fill_price": 1.40821,
            "timestamp": "2026-07-27T12:00:00.000000000Z",
        },
    }

    # One shared sequence, because ordering between two separate fakes
    # cannot be inferred from their independent call lists.
    call_order = []
    broker = _RecordingBroker(
        [],
        quote=USDCAD_QUOTE,
        call_order=call_order,
    )
    state_manager = _RecordingStateManager([])
    signal_provider = _RecordingSignalProvider()  # LONG / 85 / True

    # The reviewed controller requires status == "APPROVED", which the
    # caller submission deliberately omits, so the cycle must execute the
    # record the queue returns after approval.
    approved_proposal = {
        "proposal_id": expected_proposal_id,
        "pair": "USD/CAD",
        "direction": "LONG",
        "suggested_size": 0.5,
        "confidence": 85,
        "strategy": "Autonomous_USDCAD_MVP",
        "reason": (
            "Autonomous USD/CAD MVP proposal generated from an accepted"
            " signal."
        ),
        "status": "APPROVED",
    }
    proposal_queue = _RecordingProposalQueue(
        add_result=1,
        approve_result=True,
        call_order=call_order,
        approved_proposals=[approved_proposal],
    )
    executor = _RecordingBoundExecutor(fake_execution_result)

    result = cycle.run_cycle(
        broker=broker,
        state_manager=state_manager,
        signal_provider=signal_provider,
        proposal_queue=proposal_queue,
        executor=executor,
    )

    # --- the mandatory stop, derived from the fixed MVP rule ---
    expected_stop = round(
        USDCAD_QUOTE["ask"] - AUTONOMOUS_STOP_DISTANCE_PIPS * USDCAD_PIP_SIZE,
        5,
    )
    assert expected_stop == 1.40620, (
        "a LONG stop is 20.0 pips below the ask, rounded to 5 places;"
        f" got {expected_stop!r}"
    )

    assert isinstance(result, dict), (
        "run_cycle must return a structured result mapping"
    )
    assert result.get("outcome") == "PROPOSAL_EXECUTED", (
        "an approved autonomous proposal must be executed once;"
        f" got outcome {result.get('outcome')!r}"
    )
    assert _failure_reason(result), (
        "the result must carry a non-empty reason/message field"
    )

    # --- one quote, requested only after approval succeeded ---
    assert broker.get_quote_calls == ["USD/CAD"], (
        "the cycle must request exactly one USD/CAD quote;"
        f" got {broker.get_quote_calls!r}"
    )
    assert call_order == [
        "add_proposals",
        "approve_proposal",
        "get_approved_proposals",
        "get_quote",
    ], (
        "the approved record must be read after approval, and the quote"
        " requested only after that;"
        f" got {call_order!r}"
    )

    # --- exactly one execution through the injected executor ---
    assert len(executor.calls) == 1, (
        f"the executor must be called exactly once; got {len(executor.calls)}"
    )
    executor_call = executor.calls[0]
    assert set(executor_call) == {"proposal", "raw_stop_loss_price"}, (
        "the pre-bound executor must receive only the proposal and its"
        f" protective stop; got {sorted(executor_call)!r}"
    )

    submitted_batch = proposal_queue.add_proposals_calls[0]
    submitted_proposal = submitted_batch[0]
    assert submitted_proposal.get("proposal_id") == expected_proposal_id, (
        "the submission must carry the cycle-owned id;"
        f" got {submitted_proposal.get('proposal_id')!r}"
    )
    for field in ("status", "created_at", "approved_at", "executed_at"):
        assert field not in submitted_proposal, (
            f"the caller must not supply the queue-owned field {field!r};"
            f" got {submitted_proposal.get(field)!r}"
        )

    # --- the executor receives the APPROVED record, not the submission ---
    assert proposal_queue.get_approved_proposals_calls == 1, (
        "the approved record must be read exactly once;"
        f" got {proposal_queue.get_approved_proposals_calls}"
    )
    returned_approved = proposal_queue.returned_approved_proposals[0]
    assert len(returned_approved) == 1, (
        "exactly one approved record must be returned;"
        f" got {len(returned_approved)}"
    )
    approved_record = returned_approved[0]

    assert approved_record["proposal_id"] == expected_proposal_id, (
        f"got approved proposal_id {approved_record['proposal_id']!r}"
    )
    assert approved_record["status"] == "APPROVED", (
        "the reviewed controller requires status APPROVED;"
        f" got {approved_record.get('status')!r}"
    )
    assert executor_call["proposal"] is approved_record, (
        "the executor must receive the exact APPROVED record returned by"
        " the queue"
    )
    assert executor_call["proposal"] is not submitted_proposal, (
        "the executor must not receive the original submission, which"
        " omits the status the reviewed controller requires"
    )

    stop_sent = executor_call["raw_stop_loss_price"]
    assert stop_sent == expected_stop, (
        f"the executor must receive the mandatory stop {expected_stop!r};"
        f" got {stop_sent!r}"
    )
    assert stop_sent == 1.40620
    assert isinstance(stop_sent, (int, float)), (
        f"the stop must be numeric; got {type(stop_sent)!r}"
    )
    assert not isinstance(stop_sent, bool)
    assert 0.0 < stop_sent < float("inf"), (
        f"the stop must be finite and positive; got {stop_sent!r}"
    )
    assert stop_sent < USDCAD_QUOTE["ask"], (
        "a LONG protective stop must sit below the entry ask;"
        f" got {stop_sent!r} against {USDCAD_QUOTE['ask']!r}"
    )

    # --- result shape ---
    signal = signal_provider.returned[0]
    assert result.get("pair") == "USD/CAD"
    assert result.get("signal") == signal, (
        "the result must carry the unaltered provider signal"
    )
    assert result.get("proposal") is approved_record, (
        "the result must carry the exact APPROVED record that was executed"
    )
    assert result.get("proposal_id") == expected_proposal_id
    assert result.get("proposal_count") == 1
    assert result.get("approval_succeeded") is True
    assert result.get("stop_loss_price") == expected_stop, (
        f"got stop_loss_price {result.get('stop_loss_price')!r}"
    )
    assert result.get("execution_succeeded") is True, (
        f"got execution_succeeded {result.get('execution_succeeded')!r}"
    )
    assert result.get("execution_result") == fake_execution_result, (
        "the result must carry the executor's result unchanged"
    )
    assert result.get("broker_open_count") == 0
    assert result.get("local_filled_count") == 0

    # --- strict execution success ---
    reported = result["execution_result"]
    assert reported["success"] is True, (
        f"got success {reported['success']!r}"
    )
    nested = reported.get("execution_result")
    assert isinstance(nested, dict) and nested, (
        f"the nested execution_result must be present; got {nested!r}"
    )
    assert nested.get("execution_status") == "Filled", (
        f"got execution_status {nested.get('execution_status')!r}"
    )

    # --- exactly-once collaborator calls ---
    assert broker.get_open_positions_calls == 1, (
        f"got {broker.get_open_positions_calls}"
    )
    assert state_manager.get_all_trades_calls == 1, (
        f"got {state_manager.get_all_trades_calls}"
    )
    assert len(signal_provider.calls) == 1, (
        f"got {len(signal_provider.calls)} signal call(s)"
    )
    signal_args, signal_kwargs = signal_provider.calls[0]
    assert signal_args == ()
    assert signal_kwargs == {"pair": "USD/CAD"}
    assert len(proposal_queue.add_proposals_calls) == 1, (
        f"got {len(proposal_queue.add_proposals_calls)} add_proposals call(s)"
    )
    assert proposal_queue.approve_proposal_calls == [expected_proposal_id], (
        f"got {proposal_queue.approve_proposal_calls!r}"
    )

    # --- the broker is never an execution path ---
    assert broker.place_order_calls == [], (
        "the cycle must never submit an order directly through the broker;"
        f" got {broker.place_order_calls!r}"
    )
    assert broker.close_position_calls == [], (
        "the cycle must not close any position in this slice"
    )


@pytest.mark.parametrize(
    "label, approved_proposals",
    [
        ("no_approved_records", []),
        (
            # Same pair, direction, size, confidence and strategy as the
            # cycle's own proposal — only the id differs. Selecting by any
            # non-unique field, or by list position, would execute this
            # stranger's proposal.
            "unrelated_approved_record",
            [
                {
                    "proposal_id": "PROP-AUTO-OTHER-0001",
                    "pair": "USD/CAD",
                    "direction": "LONG",
                    "suggested_size": 0.5,
                    "confidence": 85,
                    "strategy": "Autonomous_USDCAD_MVP",
                    "reason": "Unrelated approved proposal.",
                    "status": "APPROVED",
                }
            ],
        ),
    ],
)
def test_cycle_blocks_when_exact_approved_proposal_record_is_missing(
    label,
    approved_proposals,
    monkeypatch,
):
    cycle = importlib.import_module(MODULE_NAME)

    expected_proposal_id = "PROP-AUTO-TEST-MISSING-APPROVED-0001"
    monkeypatch.setattr(
        cycle,
        "_new_proposal_id",
        lambda: expected_proposal_id,
    )

    call_order = []
    # A valid quote is available...
    broker = _RecordingBroker(
        [],
        quote=USDCAD_QUOTE,
        call_order=call_order,
    )
    state_manager = _RecordingStateManager([])
    signal_provider = _RecordingSignalProvider()  # LONG / 85 / True
    proposal_queue = _RecordingProposalQueue(
        add_result=1,
        approve_result=True,
        call_order=call_order,
        approved_proposals=approved_proposals,
    )
    # ...and an executor that would report success if it were reached, so
    # a block here can only be caused by the missing exact record.
    executor = _RecordingBoundExecutor(
        {
            "success": True,
            "message": "must never be reached",
            "request_id": f"AI-PROPOSAL-{expected_proposal_id}",
            "execution_result": {"execution_status": "Filled"},
        }
    )

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
    assert result.get("outcome") == "BLOCKED_APPROVED_PROPOSAL_NOT_FOUND", (
        f"[{label}] a missing exact approved record must block the cycle;"
        f" got outcome {result.get('outcome')!r}"
    )
    reason = _failure_reason(result)
    assert reason, (
        f"[{label}] a blocked cycle must carry a non-empty reason/message"
    )
    lowered_reason = reason.lower()
    assert "approved" in lowered_reason, (
        f"[{label}] the reason must name the approved record;"
        f" got {reason!r}"
    )
    assert any(
        term in lowered_reason for term in ("exact", "matching", "proposal")
    ), (
        f"[{label}] the reason must identify that the exact matching"
        f" proposal was not found; got {reason!r}"
    )

    # --- creation and approval both happened ---
    assert len(proposal_queue.add_proposals_calls) == 1, (
        f"[{label}] got {len(proposal_queue.add_proposals_calls)}"
        " add_proposals call(s)"
    )
    submitted_batch = proposal_queue.add_proposals_calls[0]
    assert len(submitted_batch) == 1, (
        f"[{label}] got {len(submitted_batch)} submitted proposal(s)"
    )
    submitted_proposal = submitted_batch[0]
    assert submitted_proposal.get("proposal_id") == expected_proposal_id, (
        f"[{label}] got {submitted_proposal.get('proposal_id')!r}"
    )
    assert "status" not in submitted_proposal, (
        f"[{label}] the caller must not supply the queue-owned status;"
        f" got {submitted_proposal.get('status')!r}"
    )
    assert proposal_queue.approve_proposal_calls == [expected_proposal_id], (
        f"[{label}] got {proposal_queue.approve_proposal_calls!r}"
    )
    assert proposal_queue.get_approved_proposals_calls == 1, (
        f"[{label}] the approved record must be read exactly once;"
        f" got {proposal_queue.get_approved_proposals_calls}"
    )

    # --- no returned record carries the exact id ---
    returned_approved = proposal_queue.returned_approved_proposals[0]
    assert len(returned_approved) == len(approved_proposals), (
        f"[{label}] the queue fake must return what it was configured"
        f" with; got {returned_approved!r}"
    )
    assert all(
        record.get("proposal_id") != expected_proposal_id
        for record in returned_approved
    ), (
        f"[{label}] this case must supply no record matching"
        f" {expected_proposal_id!r}; got {returned_approved!r}"
    )

    # --- the cycle stops at the approved-record read ---
    assert call_order == [
        "add_proposals",
        "approve_proposal",
        "get_approved_proposals",
    ], (
        f"[{label}] the cycle must stop once the exact record is missing;"
        f" got {call_order!r}"
    )

    # --- nothing downstream ran ---
    assert broker.get_quote_calls == [], (
        f"[{label}] no quote may be requested without the exact approved"
        f" record; got {broker.get_quote_calls!r}"
    )
    assert executor.calls == [], (
        f"[{label}] the executor must never run without the exact"
        f" approved record; got {executor.calls!r}"
    )
    assert broker.place_order_calls == [], (
        f"[{label}] the cycle must never submit an order directly"
    )
    assert broker.close_position_calls == [], (
        f"[{label}] the cycle must not close any position"
    )

    # --- result shape ---
    signal = signal_provider.returned[0]
    assert result.get("pair") == "USD/CAD"
    assert result.get("signal") == signal, (
        f"[{label}] the result must carry the unaltered provider signal"
    )
    assert result.get("proposal") is submitted_proposal, (
        f"[{label}] the result must carry the exact submitted proposal"
    )
    assert result.get("proposal_id") == submitted_proposal["proposal_id"], (
        f"[{label}] got {result.get('proposal_id')!r}"
    )
    assert result.get("proposal_id") == expected_proposal_id
    assert result.get("proposal_count") == 1, (
        f"[{label}] got {result.get('proposal_count')!r}"
    )
    assert result.get("approval_succeeded") is True, (
        f"[{label}] got {result.get('approval_succeeded')!r}"
    )
    assert result.get("execution_succeeded") is False, (
        f"[{label}] got {result.get('execution_succeeded')!r}"
    )
    assert result.get("broker_open_count") == 0
    assert result.get("local_filled_count") == 0


@pytest.mark.parametrize(
    "label, executor_result",
    [
        (
            # No execution_result key at all.
            "explicit_failure",
            {
                "success": False,
                "message": "Execution rejected.",
            },
        ),
        (
            # Truthy but not True: an `== True` check would wrongly pass.
            "truthy_but_not_true",
            {
                "success": 1,
                "message": "Invalid success type.",
                "execution_result": {
                    "execution_status": "Filled",
                },
            },
        ),
        (
            # Successful call, but the order was not filled.
            "not_filled",
            {
                "success": True,
                "message": "Order was not filled.",
                "execution_result": {
                    "execution_status": "Netted",
                },
            },
        ),
    ],
)
def test_cycle_blocks_when_executor_does_not_return_strict_filled_success(
    label,
    executor_result,
    monkeypatch,
):
    cycle = importlib.import_module(MODULE_NAME)

    expected_proposal_id = "PROP-AUTO-TEST-EXECUTION-FAILURE-0001"
    monkeypatch.setattr(
        cycle,
        "_new_proposal_id",
        lambda: expected_proposal_id,
    )

    # Every pre-execution step succeeds, so only the executor's result
    # can block this cycle.
    broker = _RecordingBroker([], quote=USDCAD_QUOTE)
    state_manager = _RecordingStateManager([])
    signal_provider = _RecordingSignalProvider()  # LONG / 85 / True
    proposal_queue = _RecordingProposalQueue(
        add_result=1,
        approve_result=True,
        approved_proposals=[_approved_record(expected_proposal_id)],
    )
    executor = _RecordingBoundExecutor(executor_result)

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
    assert result.get("outcome") == "BLOCKED_EXECUTION_FAILED", (
        f"[{label}] a non-strict Filled result must block the cycle;"
        f" got outcome {result.get('outcome')!r}"
    )

    reason = _failure_reason(result)
    assert reason, (
        f"[{label}] a blocked cycle must carry a non-empty reason/message"
    )
    lowered_reason = reason.lower()
    assert "execution" in lowered_reason, (
        f"[{label}] the reason must name execution; got {reason!r}"
    )
    assert any(
        term in lowered_reason
        for term in ("fail", "reject", "success", "filled")
    ), (
        f"[{label}] the reason must identify the failed or non-Filled"
        f" outcome; got {reason!r}"
    )

    # --- the approved record the executor was handed ---
    assert proposal_queue.get_approved_proposals_calls == 1, (
        f"[{label}] got {proposal_queue.get_approved_proposals_calls}"
    )
    returned_approved = proposal_queue.returned_approved_proposals[0]
    assert len(returned_approved) == 1, (
        f"[{label}] got {len(returned_approved)} approved record(s)"
    )
    approved_record = returned_approved[0]

    # --- result shape ---
    assert result.get("pair") == "USD/CAD"
    assert result.get("signal") == signal_provider.returned[0], (
        f"[{label}] the result must carry the unaltered provider signal"
    )
    assert result.get("proposal") is approved_record, (
        f"[{label}] the result must carry the exact APPROVED record"
    )
    assert result.get("proposal_id") == expected_proposal_id, (
        f"[{label}] got {result.get('proposal_id')!r}"
    )
    assert result.get("proposal_count") == 1, (
        f"[{label}] got {result.get('proposal_count')!r}"
    )
    assert result.get("approval_succeeded") is True, (
        f"[{label}] got {result.get('approval_succeeded')!r}"
    )
    assert result.get("stop_loss_price") == 1.40620, (
        f"[{label}] got {result.get('stop_loss_price')!r}"
    )
    assert result.get("execution_succeeded") is False, (
        f"[{label}] got {result.get('execution_succeeded')!r}"
    )
    assert result.get("execution_result") is executor_result, (
        f"[{label}] the result must carry the executor's exact result"
    )
    assert result.get("broker_open_count") == 0
    assert result.get("local_filled_count") == 0

    # --- exactly-once collaborator calls ---
    assert broker.get_open_positions_calls == 1, (
        f"[{label}] got {broker.get_open_positions_calls}"
    )
    assert state_manager.get_all_trades_calls == 1, (
        f"[{label}] got {state_manager.get_all_trades_calls}"
    )
    assert len(signal_provider.calls) == 1, (
        f"[{label}] got {len(signal_provider.calls)} signal call(s)"
    )
    signal_args, signal_kwargs = signal_provider.calls[0]
    assert signal_args == ()
    assert signal_kwargs == {"pair": "USD/CAD"}
    assert len(proposal_queue.add_proposals_calls) == 1, (
        f"[{label}] got {len(proposal_queue.add_proposals_calls)}"
        " add_proposals call(s)"
    )
    assert proposal_queue.approve_proposal_calls == [expected_proposal_id], (
        f"[{label}] got {proposal_queue.approve_proposal_calls!r}"
    )
    assert broker.get_quote_calls == ["USD/CAD"], (
        f"[{label}] exactly one quote must be requested;"
        f" got {broker.get_quote_calls!r}"
    )

    # --- exactly one execution attempt, no retry ---
    assert len(executor.calls) == 1, (
        f"[{label}] the executor must be called exactly once with no"
        f" retry; got {len(executor.calls)} call(s)"
    )
    executor_call = executor.calls[0]
    assert len(executor_call) == 2, (
        f"[{label}] got {len(executor_call)} keyword argument(s)"
    )
    assert set(executor_call) == {"proposal", "raw_stop_loss_price"}, (
        f"[{label}] got {sorted(executor_call)!r}"
    )
    assert executor_call["proposal"] is approved_record, (
        f"[{label}] the executor must receive the queue-returned APPROVED"
        " record"
    )
    assert executor_call["raw_stop_loss_price"] == 1.40620, (
        f"[{label}] got {executor_call['raw_stop_loss_price']!r}"
    )

    # --- the broker is never an execution path ---
    assert broker.place_order_calls == [], (
        f"[{label}] the cycle must never submit an order directly;"
        f" got {broker.place_order_calls!r}"
    )
    assert broker.close_position_calls == [], (
        f"[{label}] the cycle must not close any position"
    )
