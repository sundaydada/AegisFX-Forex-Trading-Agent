"""
Runner-level regression suite for the autonomy nightly runner cycle.

Every behavioral test in this file invokes the REAL
    autonomy_nightly_runner.main()
for exactly one cycle. There is no local reference implementation, no
`run_one_cycle` requirement, and no test-local orchestration. If the
runner is wrong, these tests are red; if the runner is right, they are
green.

Mechanism
---------
1. Every runner-module symbol that could touch broker network I/O,
   SQLite files, filesystem writes, or the operator's real settings is
   patched to a hand-written spy or stub.
2. A private sentinel class `_EndCycle` derived DIRECTLY from
   BaseException is raised the first time the patched `time.sleep`
   runs, which occurs at the very end of one full cycle
   (autonomy_nightly_runner.py:212).
3. `_EndCycle` is NOT an Exception subclass, so it is not intercepted
   by the four `except Exception:` handlers inside the cycle body
   (autonomy_nightly_runner.py:151, 158, 177, 207). It is also not
   KeyboardInterrupt, so the outer `except KeyboardInterrupt:` at
   autonomy_nightly_runner.py:214 does not intercept it either.
4. `main()` therefore exits after exactly one cycle with `_EndCycle`
   propagating out, which `pytest.raises(_EndCycle)` catches.

Max-age settings source
-----------------------
The existing production symbol
    execution.autonomy_gate.DEFAULT_PROPOSAL_MAX_AGE_HOURS
is the freshness threshold the autonomy gate already uses as its
fallback. Because the AutonomySettingsManager filters unknown keys out
of load_settings() (autonomy_settings.py:60), the gate's `settings.get(
"proposal_max_age_hours", DEFAULT_PROPOSAL_MAX_AGE_HOURS)` collapses to
that constant in practice. Mirroring the same constant in the runner
is therefore the smallest, safest production change and it invents no
test-only API. Each test monkeypatches
    autonomy_nightly_runner.DEFAULT_PROPOSAL_MAX_AGE_HOURS
to a distinctive non-default value (7.5) and asserts that value
reaches queue.expire_stale_approved_proposals(). `raising=False` is
required because the current runner has not yet imported the symbol —
that import is exactly the tiny production change these tests demand.

Safety
------
No sqlite3 import. No sqlite3.connect. No temp DB. No real
proposal_approvals.db or dry_run_sustained.db. No OANDA construction
that opens a socket. No real time.sleep. No infinite loop. No
filesystem write. Convention follows tests/test_orchestrator_scenarios
.py: hand-written mock/spy classes at the top, plain `def test_*`
functions, plain `assert`.
"""

from typing import Any, Dict, List

import pytest

import autonomy_nightly_runner


# ---------------------------------------------------------------------------
# Sentinel — inherits DIRECTLY from BaseException so it escapes every
# `except Exception:` block inside the cycle body, and it is not
# KeyboardInterrupt so the outer handler in main() does not catch it.
# ---------------------------------------------------------------------------

class _EndCycle(BaseException):
    """Raised from the first patched `time.sleep` call to end main()
    after exactly one cycle."""


# ---------------------------------------------------------------------------
# Shared ordered call log
# ---------------------------------------------------------------------------

class _CallLog:
    def __init__(self):
        self.events: List[Dict[str, Any]] = []

    def record(self, name: str, **kwargs) -> None:
        self.events.append({"name": name, "kwargs": kwargs})

    def names(self) -> List[str]:
        return [e["name"] for e in self.events]


# ---------------------------------------------------------------------------
# Spies and stubs
#
# Constructors accept *args, **kwargs so the real production keyword
# arguments (api_key=, account_id=, base_url=, health=, db_path=,
# settings_path=, ...) pass through cleanly without a signature drift.
# Only the methods that main() actually invokes are implemented.
# ---------------------------------------------------------------------------

class _SpyQueue:
    """
    Stand-in for ProposalApprovalQueue. Records every method main()
    invokes on it, returns a caller-supplied post-expiry list from
    get_approved_proposals(), and captures the max_age_hours value the
    runner passes to expire_stale_approved_proposals().
    """
    def __init__(self, log: _CallLog, proposals_after_expiry: List[Dict]):
        self._log = log
        self._proposals_after_expiry = proposals_after_expiry
        self.expire_calls: List[Dict[str, Any]] = []

    def expire_stale_approved_proposals(self, max_age_hours, now=None) -> int:
        self.expire_calls.append({"max_age_hours": max_age_hours, "now": now})
        self._log.record(
            "queue.expire_stale_approved_proposals",
            max_age_hours=max_age_hours,
            now=now,
        )
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
    """
    Stand-in for AutonomyExecutionBridge. Records the proposals it
    received. Returns an empty executed/skipped result so the runner's
    mark_executed branch does not fire (kept out of the test surface
    intentionally — those are separate contracts).
    """
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
        # Snapshot the list at call time so a later mutation elsewhere
        # cannot back-fill what the test observed.
        self.received_proposals.append(list(proposals))
        self._log.record(
            "bridge.auto_execute_eligible_proposals",
            proposals=list(proposals),
            nightly_trade_count=nightly_trade_count,
        )
        return {"executed": [], "skipped": []}


class _StubStateManager:
    def __init__(self, *args, **kwargs):
        pass

    def get_all_trades(self) -> List[Dict]:
        return []

    def close(self) -> None:
        pass


class _StubBroker:
    def __init__(self, *args, **kwargs):
        # Absorb (api_key=, account_id=, base_url=, health=) without
        # ever building an HTTP client or reaching out to OANDA.
        pass


class _StubHealth:
    def __init__(self, *args, **kwargs):
        pass


class _StubOrchestrator:
    def __init__(self, *args, **kwargs):
        pass


# ---------------------------------------------------------------------------
# Runner harness — patches every real-infrastructure symbol on the
# autonomy_nightly_runner module, wires the sentinel-raising sleep,
# and returns (queue_spy, bridge_spy, log) so assertions have handles.
# ---------------------------------------------------------------------------

def _install_runner_stubs(
    monkeypatch,
    *,
    proposals_after_expiry: List[Dict],
    max_age_hours: float,
):
    log = _CallLog()
    queue_spy = _SpyQueue(log, proposals_after_expiry=proposals_after_expiry)
    bridge_spy = _SpyBridge(log)

    # Queue and bridge are the observation surfaces. The factory
    # lambdas absorb the runner's real constructor kwargs (db_path=...,
    # settings_path=...) and return the shared spy each call.
    monkeypatch.setattr(
        autonomy_nightly_runner, "ProposalApprovalQueue",
        lambda *a, **kw: queue_spy,
    )
    monkeypatch.setattr(
        autonomy_nightly_runner, "AutonomyExecutionBridge",
        lambda *a, **kw: bridge_spy,
    )

    # Everything else the runner constructs at startup is replaced
    # with an inert stub whose __init__ absorbs any production kwargs.
    monkeypatch.setattr(
        autonomy_nightly_runner, "PersistentTradeStateManager",
        _StubStateManager,
    )
    monkeypatch.setattr(
        autonomy_nightly_runner, "OandaBroker", _StubBroker,
    )
    monkeypatch.setattr(
        autonomy_nightly_runner, "BrokerHealthMonitor", _StubHealth,
    )
    monkeypatch.setattr(
        autonomy_nightly_runner, "TradeOrchestrator", _StubOrchestrator,
    )
    monkeypatch.setattr(
        autonomy_nightly_runner, "log_db_path_once",
        lambda *a, **kw: None,
    )

    # Max-age source. Mirrors the existing production constant
    # execution.autonomy_gate.DEFAULT_PROPOSAL_MAX_AGE_HOURS. The
    # smallest safe runner change is:
    #     from execution.autonomy_gate import DEFAULT_PROPOSAL_MAX_AGE_HOURS
    # and then passing it to expire_stale_approved_proposals(). This
    # patch stands in for that future import; raising=False permits
    # setting an attribute the current runner has not yet imported.
    monkeypatch.setattr(
        autonomy_nightly_runner, "DEFAULT_PROPOSAL_MAX_AGE_HOURS",
        max_age_hours, raising=False,
    )

    # Sentinel-raising sleep. autonomy_nightly_runner.time is the
    # stdlib `time` module (imported at autonomy_nightly_runner.py:26);
    # monkeypatch restores the original sleep at test teardown.
    def _sentinel_sleep(_seconds):
        raise _EndCycle
    monkeypatch.setattr(autonomy_nightly_runner.time, "sleep", _sentinel_sleep)

    return queue_spy, bridge_spy, log


# ---------------------------------------------------------------------------
# Behavior tests — each invokes autonomy_nightly_runner.main() directly.
# ---------------------------------------------------------------------------

def test_main_expires_before_fetching_and_passes_survivors_to_bridge(monkeypatch):
    """
    One real cycle of main() must:
      1. call queue.expire_stale_approved_proposals(...) BEFORE
         queue.get_approved_proposals();
      2. pass the module-level DEFAULT_PROPOSAL_MAX_AGE_HOURS value
         (7.5 under this monkeypatch) as max_age_hours;
      3. hand the exact list returned by get_approved_proposals() to
         bridge.auto_execute_eligible_proposals().
    """
    survivors = [
        {"proposal_id": "PROP-FRESH-A", "status": "APPROVED", "pair": "EUR/USD"},
        {"proposal_id": "PROP-FRESH-B", "status": "APPROVED", "pair": "GBP/USD"},
    ]
    queue_spy, bridge_spy, log = _install_runner_stubs(
        monkeypatch,
        proposals_after_expiry=survivors,
        max_age_hours=7.5,
    )

    with pytest.raises(_EndCycle):
        autonomy_nightly_runner.main()

    names = log.names()

    assert "queue.expire_stale_approved_proposals" in names, (
        "Runner did not call queue.expire_stale_approved_proposals "
        f"during the cycle. Observed call order: {names!r}"
    )
    assert "queue.get_approved_proposals" in names, (
        "Runner did not fetch approved proposals during the cycle. "
        f"Observed call order: {names!r}"
    )

    expire_idx = names.index("queue.expire_stale_approved_proposals")
    fetch_idx = names.index("queue.get_approved_proposals")
    assert expire_idx < fetch_idx, (
        "Expiration must precede fetch so a stale APPROVED row is "
        "persisted as EXPIRED before the runner considers it for "
        f"execution. Observed order: {names!r}"
    )

    assert queue_spy.expire_calls, (
        "queue.expire_stale_approved_proposals was not invoked at all."
    )
    assert queue_spy.expire_calls[0]["max_age_hours"] == 7.5, (
        "Runner must pass the configured DEFAULT_PROPOSAL_MAX_AGE_HOURS "
        "value to queue.expire_stale_approved_proposals; got "
        f"{queue_spy.expire_calls[0]['max_age_hours']!r}."
    )

    assert bridge_spy.received_proposals == [survivors], (
        "The list handed to bridge.auto_execute_eligible_proposals "
        "must be exactly the list returned by get_approved_proposals "
        "after expiration. The runner must not filter, re-fetch, or "
        "invent proposals. Received: "
        f"{bridge_spy.received_proposals!r}"
    )


def test_main_skips_bridge_when_post_expiry_list_is_empty(monkeypatch):
    """
    When the APPROVED list is empty after expiration (either because
    nothing was approved to begin with, or because every APPROVED row
    just expired), the runner must NOT invoke the bridge. Expiration
    still runs, and it still runs BEFORE the fetch — the empty-list
    branch does not exempt the runner from the persistence step.
    """
    queue_spy, bridge_spy, log = _install_runner_stubs(
        monkeypatch,
        proposals_after_expiry=[],
        max_age_hours=7.5,
    )

    with pytest.raises(_EndCycle):
        autonomy_nightly_runner.main()

    names = log.names()

    assert "queue.expire_stale_approved_proposals" in names, (
        "Expiration must run every cycle even when the post-expiry "
        f"APPROVED list is empty. Observed call order: {names!r}"
    )
    assert "queue.get_approved_proposals" in names, (
        f"Runner did not fetch approved proposals. Observed: {names!r}"
    )
    expire_idx = names.index("queue.expire_stale_approved_proposals")
    fetch_idx = names.index("queue.get_approved_proposals")
    assert expire_idx < fetch_idx, (
        f"Expiration must precede fetch. Observed order: {names!r}"
    )

    assert bridge_spy.received_proposals == [], (
        "Bridge must NOT be called when the post-expiry APPROVED list "
        f"is empty. Received: {bridge_spy.received_proposals!r}"
    )
    assert "bridge.auto_execute_eligible_proposals" not in names, (
        "bridge.auto_execute_eligible_proposals should not appear in "
        f"the call log when the approved list is empty. Got: {names!r}"
    )
