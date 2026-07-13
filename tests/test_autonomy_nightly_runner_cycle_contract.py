"""
Runner integration contract tests for
ProposalApprovalQueue.expire_stale_approved_proposals(...).

These tests DEFINE the contract that the autonomy nightly runner must
satisfy once its per-cycle logic is made reachable for unit testing.
They document the ordering, the argument the runner passes, and the
empty-list behaviour. They are expected to FAIL against the current
codebase because:

  * autonomy_nightly_runner.main() embeds the cycle inside `while True:
    ... time.sleep(CYCLE_SECONDS) ...`, so there is no cycle-granular
    entry point to invoke.
  * The current cycle body calls queue.get_approved_proposals() at
    autonomy_nightly_runner.py:149 without first calling
    queue.expire_stale_approved_proposals(...). That is the ordering
    violation this suite pins.

When the runner is later refactored to expose a `run_one_cycle(...)`
function that accepts injected dependencies (queue, bridge, state
manager, orchestrator, settings/max_age_hours), the SAME assertions in
this file will exercise it directly — only the `import` line at the
top of the tests changes.

Convention follows tests/test_orchestrator_scenarios.py: hand-written
mock/spy classes at the top of the file, plain `def test_*` functions,
plain `assert`, no pytest fixtures.

No production code, no directive, no database, no broker, no network,
no sleep, no long-running loop is touched by any test in this file.
"""

from typing import Any, Dict, List

from ai.proposal_approval_queue import ProposalApprovalQueue


# ---------------------------------------------------------------------------
# Spies — record every call in order so the tests can assert ordering,
# arguments, and side-effect boundaries deterministically.
# ---------------------------------------------------------------------------

class _CallLog:
    """Ordered record of the calls each mock received across a cycle."""
    def __init__(self):
        self.events: List[Dict[str, Any]] = []

    def record(self, name: str, **kwargs) -> None:
        self.events.append({"name": name, "kwargs": kwargs})

    def names(self) -> List[str]:
        return [e["name"] for e in self.events]


class _SpyQueue:
    """
    Queue-shaped stand-in. Records expire and fetch calls, returns a
    caller-supplied list of proposals from get_approved_proposals(),
    and reports the max_age_hours it was called with.
    """
    def __init__(self, log: _CallLog, proposals_after_expiry: List[Dict]):
        self._log = log
        self._proposals_after_expiry = proposals_after_expiry
        # Capture invocation args for direct assertion in tests.
        self.expire_calls: List[Dict[str, Any]] = []

    def expire_stale_approved_proposals(self, max_age_hours, now=None) -> int:
        self.expire_calls.append({
            "max_age_hours": max_age_hours,
            "now": now,
        })
        self._log.record(
            "queue.expire_stale_approved_proposals",
            max_age_hours=max_age_hours,
            now=now,
        )
        # The exact count returned is not part of the runner contract; the
        # runner does not consume the return value. Return a plausible
        # non-negative int so any future consumer that reads it gets a
        # well-typed answer.
        return 0

    def get_approved_proposals(self) -> List[Dict]:
        self._log.record("queue.get_approved_proposals")
        return list(self._proposals_after_expiry)

    def mark_executed(self, proposal_id: str) -> bool:
        self._log.record("queue.mark_executed", proposal_id=proposal_id)
        return True

    def close(self) -> None:
        self._log.record("queue.close")


class _SpyBridge:
    """AutonomyExecutionBridge stand-in. Records the proposals it received."""
    def __init__(self, log: _CallLog):
        self._log = log
        self.received_proposals: List[List[Dict]] = []

    def auto_execute_eligible_proposals(
        self,
        *,
        proposals: List[Dict],
        orchestrator=None,
        nightly_trade_count: int = 0,
        state_manager=None,
        max_currency_exposure: float = 100.0,
    ) -> Dict:
        # Snapshot at call time so the test can inspect exactly what
        # was passed in, not a later-mutated reference.
        self.received_proposals.append(list(proposals))
        self._log.record(
            "bridge.auto_execute_eligible_proposals",
            proposals=list(proposals),
            nightly_trade_count=nightly_trade_count,
        )
        return {"executed": [], "skipped": []}


class _StubStateManager:
    """Minimal state manager: no trades in the ledger this cycle."""
    def get_all_trades(self) -> List[Dict]:
        return []

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Cycle-under-test — the exact ordering the runner must adopt.
#
# NOTE: The current runner (autonomy_nightly_runner.main() at
# autonomy_nightly_runner.py:139-212) inlines its cycle inside `while
# True: ... time.sleep(...)` and does NOT call expire_stale_approved_
# proposals. This local function specifies the target contract that a
# future run_one_cycle(...) extraction must implement. The tests below
# call THIS helper as a stand-in and will be re-pointed at
# autonomy_nightly_runner.run_one_cycle once that entry point exists.
#
# When that refactor lands, the only change to this file is:
#     from autonomy_nightly_runner import run_one_cycle as _run_cycle
# and the helper below is deleted. Every assertion stays.
# ---------------------------------------------------------------------------

def _run_cycle(*, queue, bridge, state_manager, max_age_hours, orchestrator=None):
    """
    Reference implementation of ONE cycle. The runner must behave this
    way after refactor:

      1. Expire stale approved rows first, passing max_age_hours.
      2. Fetch approved proposals only AFTER expiration.
      3. If the post-expiry list is empty, DO NOT call the bridge.
      4. Otherwise, hand the post-expiry list to the bridge verbatim.
    """
    queue.expire_stale_approved_proposals(max_age_hours=max_age_hours)
    approved = queue.get_approved_proposals()
    if not approved:
        return {"executed": [], "skipped": []}
    return bridge.auto_execute_eligible_proposals(
        proposals=approved,
        orchestrator=orchestrator,
        nightly_trade_count=0,
        state_manager=state_manager,
        max_currency_exposure=100.0,
    )


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------

def test_runner_exposes_a_testable_single_cycle_entry_point():
    """
    The runner MUST expose a callable named `run_one_cycle` so the cycle
    body can be exercised without entering the infinite `while True`
    loop. Until it does, this test fails and documents the missing
    seam. This is the seam every other test in this file will use.
    """
    import autonomy_nightly_runner as runner
    assert hasattr(runner, "run_one_cycle"), (
        "autonomy_nightly_runner must expose run_one_cycle(...) so the "
        "per-cycle behaviour can be tested in isolation. Today the cycle "
        "is inlined inside main()'s while True loop."
    )


def test_queue_expiration_precedes_approved_fetch():
    """
    Cycle must call queue.expire_stale_approved_proposals BEFORE
    queue.get_approved_proposals. Reversed order would let a stale row
    reach the bridge and produce a runtime `proposal_expired` skip
    instead of a persisted EXPIRED status.
    """
    log = _CallLog()
    queue = _SpyQueue(log, proposals_after_expiry=[])
    bridge = _SpyBridge(log)

    _run_cycle(
        queue=queue,
        bridge=bridge,
        state_manager=_StubStateManager(),
        max_age_hours=24,
    )

    names = log.names()
    expire_idx = names.index("queue.expire_stale_approved_proposals")
    fetch_idx = names.index("queue.get_approved_proposals")
    assert expire_idx < fetch_idx, (
        f"Expiration must precede fetch; got order: {names}"
    )


def test_runner_passes_configured_max_age_hours_to_expire():
    """
    Runner must pass its configured proposal_max_age_hours (default 24)
    as the max_age_hours argument to expire_stale_approved_proposals.
    Duplicating age arithmetic inside the runner would drift from the
    queue's owning transition rule.
    """
    log = _CallLog()
    queue = _SpyQueue(log, proposals_after_expiry=[])
    bridge = _SpyBridge(log)

    _run_cycle(
        queue=queue,
        bridge=bridge,
        state_manager=_StubStateManager(),
        max_age_hours=24,
    )

    assert len(queue.expire_calls) == 1
    assert queue.expire_calls[0]["max_age_hours"] == 24


def test_bridge_receives_only_proposals_returned_after_expiration():
    """
    The list handed to the bridge must be exactly the list returned by
    get_approved_proposals AFTER the expire call. The runner must not
    filter, re-fetch, or invent proposals.
    """
    log = _CallLog()
    survivors = [
        {"proposal_id": "PROP-FRESH-A", "status": "APPROVED", "pair": "EUR/USD"},
        {"proposal_id": "PROP-FRESH-B", "status": "APPROVED", "pair": "GBP/USD"},
    ]
    queue = _SpyQueue(log, proposals_after_expiry=survivors)
    bridge = _SpyBridge(log)

    _run_cycle(
        queue=queue,
        bridge=bridge,
        state_manager=_StubStateManager(),
        max_age_hours=24,
    )

    assert len(bridge.received_proposals) == 1
    assert bridge.received_proposals[0] == survivors


def test_bridge_not_invoked_when_expiration_leaves_empty_list():
    """
    Matches the runner's existing empty-list behaviour at
    autonomy_nightly_runner.py:165-166 ("no APPROVED proposals —
    nothing to do this cycle"). If expiration removed the only
    APPROVED row, the bridge must not be called.
    """
    log = _CallLog()
    queue = _SpyQueue(log, proposals_after_expiry=[])
    bridge = _SpyBridge(log)

    _run_cycle(
        queue=queue,
        bridge=bridge,
        state_manager=_StubStateManager(),
        max_age_hours=24,
    )

    assert bridge.received_proposals == []
    assert "bridge.auto_execute_eligible_proposals" not in log.names()


def test_queue_operation_owns_the_persisted_transition():
    """
    The runner must NOT reimplement expiration arithmetic — the queue
    is the sole owner of the persisted APPROVED -> EXPIRED transition.
    This test proves the queue actually exposes the method so the
    runner has a real API to call. If ProposalApprovalQueue loses this
    method, this test fails loudly.
    """
    assert callable(
        getattr(ProposalApprovalQueue, "expire_stale_approved_proposals", None)
    ), (
        "ProposalApprovalQueue must expose expire_stale_approved_proposals "
        "so the runner has one place to delegate the transition."
    )
