"""
Contract tests for the not-yet-implemented queue operation:

    ProposalApprovalQueue.expire_stale_approved_proposals(max_age_hours, now=None)

These tests DEFINE the contract before the production code exists. They are
expected to fail on the current codebase (the method does not exist yet).

Design constraints enforced by these tests:
  * Freshness is measured from `created_at`.
  * The caller supplies `max_age_hours`; the method does not hard-code 24.
  * Inclusive threshold: `age <= max_age_hours` is still fresh
    (matches the existing gate's `<=` rule in execution/autonomy_gate.py).
  * Only APPROVED rows transition to a new terminal status "EXPIRED".
  * PENDING, REJECTED, and EXECUTED rows are never modified, regardless of age.
  * The original `reviewed_at` of an APPROVED row is preserved when it expires
    so audit history (WHEN the human approved) is not overwritten.
  * The operation is idempotent — a second call transitions zero additional rows.
  * The operation returns the integer count of rows transitioned this call.
  * Time is injectable via the `now` parameter — no wall-clock dependency.
  * All tests use an isolated tempfile SQLite database. The real
    proposal_approvals.db is never opened.

Test naming and structure follow the repo convention already established by
test_trade_validator_rejection.py and test_crash_recovery.py — plain pytest
`test_*` functions, no framework fixtures beyond the standard library.
"""

import contextlib
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

from ai.proposal_approval_queue import ProposalApprovalQueue


# ---------------------------------------------------------------------------
# Helpers — kept local so this file has no cross-test coupling.
# ---------------------------------------------------------------------------

FIXED_NOW = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


@contextlib.contextmanager
def _isolated_queue():
    """
    Yield a (queue, db_path) pair backed by a private tempfile SQLite DB.

    Lifecycle guarantees:
      1. A fresh temp file path is chosen; the empty file created by
         tempfile.mkstemp is removed so ProposalApprovalQueue's __init__
         is what actually materializes the schema.
      2. The queue is opened before yield.
      3. queue.close() runs in the finally block, so it fires even if the
         test body raises (including the AttributeError from calling the
         not-yet-implemented expire_stale_approved_proposals method).
      4. AFTER the queue is closed, the on-disk files are removed —
         main .db, plus WAL journal (-wal) and shared-memory index (-shm)
         SQLite may have created because __init__ enables WAL journaling.
      5. Cleanup failures are NOT swallowed. If a file cannot be removed
         (permission, still-locked, missing) the exception propagates so
         the test run surfaces the leak rather than hiding it.
    """
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="aegisfx_queue_test_")
    os.close(fd)
    # Remove the empty file so ProposalApprovalQueue creates the schema itself.
    os.remove(db_path)

    queue = ProposalApprovalQueue(db_path=db_path)
    try:
        yield queue, db_path
    finally:
        # Step 1: close the DB connection so the OS releases the file lock
        # and any -wal / -shm files can be safely removed on Windows.
        queue.close()

        # Step 2: remove the SQLite files. We iterate over every companion
        # file WAL mode may have created. os.remove raises FileNotFoundError
        # if a file was never created; that specific case is expected (WAL
        # sidecars only appear after a write cycle) and is silently OK.
        # Every OTHER error must surface.
        for suffix in ("", "-wal", "-shm"):
            p = db_path + suffix
            try:
                os.remove(p)
            except FileNotFoundError:
                # WAL sidecar never materialized -- normal, ignore.
                pass


def _insert_row(
    db_path: str,
    *,
    proposal_id: str,
    status: str,
    created_at: datetime,
    reviewed_at: datetime = None,
    pair: str = "EUR/USD",
    direction: str = "LONG",
    suggested_size: float = 1.0,
    confidence: int = 85,
    strategy: str = "MeanReversion_v1",
    reason: str = "test seed",
    execution_allowed: int = 1,
    risk_mode: str = "REDUCED",
) -> None:
    """
    Insert one row directly via SQL so tests can plant rows with any
    (status, created_at, reviewed_at) combination — including states the
    normal API cannot construct (e.g. an APPROVED row with a specific
    historical created_at).
    """
    # Wrap in contextlib.closing so the OS-level SQLite file handle is
    # released deterministically on scope exit. Python's built-in
    # `with sqlite3.connect(...) as conn:` only commits/rolls back the
    # transaction; it does NOT close the connection. Leaving the handle
    # open holds a Windows file lock and blocks temp-file cleanup.
    #
    # The nested `with conn:` block is kept because it is what actually
    # performs the transaction commit on normal exit (and rollback on
    # exception) — the outer closing() only owns handle disposal.
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        with conn:
            conn.execute(
                """
                INSERT INTO approval_queue (
                    proposal_id, pair, direction, suggested_size, confidence,
                    strategy, reason, status, created_at, reviewed_at,
                    execution_allowed, risk_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal_id, pair, direction, suggested_size, confidence,
                    strategy, reason, status,
                    created_at.isoformat(),
                    reviewed_at.isoformat() if reviewed_at is not None else None,
                    execution_allowed, risk_mode,
                ),
            )


def _read_row(db_path: str, proposal_id: str) -> dict:
    # See _insert_row for why contextlib.closing() is required here.
    # No nested `with conn:` is needed on a read-only URI connection —
    # there is no transaction to commit and mode=ro forbids writes anyway.
    with contextlib.closing(
        sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    ) as conn:
        cur = conn.execute(
            "SELECT proposal_id, status, created_at, reviewed_at "
            "FROM approval_queue WHERE proposal_id = ?",
            (proposal_id,),
        )
        row = cur.fetchone()
    assert row is not None, f"row {proposal_id} disappeared"
    return {
        "proposal_id": row[0],
        "status": row[1],
        "created_at": row[2],
        "reviewed_at": row[3],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_approved_older_than_max_age_becomes_expired():
    """APPROVED + created 25h ago -> EXPIRED under a 24h threshold."""
    with _isolated_queue() as (queue, db_path):
        _insert_row(
            db_path,
            proposal_id="PROP-OLD-APPROVED",
            status="APPROVED",
            created_at=FIXED_NOW - timedelta(hours=25),
            reviewed_at=FIXED_NOW - timedelta(hours=20),
        )

        transitioned = queue.expire_stale_approved_proposals(
            max_age_hours=24, now=FIXED_NOW
        )

        assert transitioned == 1
        row = _read_row(db_path, "PROP-OLD-APPROVED")
        assert row["status"] == "EXPIRED"


def test_approved_exactly_at_threshold_stays_approved():
    """
    APPROVED at exactly the threshold is STILL fresh.

    Matches the existing gate's inclusive `age <= max_age_hours` rule in
    execution/autonomy_gate.py (freshness check on lines 167-189).
    """
    with _isolated_queue() as (queue, db_path):
        _insert_row(
            db_path,
            proposal_id="PROP-EDGE-24H",
            status="APPROVED",
            created_at=FIXED_NOW - timedelta(hours=24),
            reviewed_at=FIXED_NOW - timedelta(hours=23),
        )

        transitioned = queue.expire_stale_approved_proposals(
            max_age_hours=24, now=FIXED_NOW
        )

        assert transitioned == 0
        row = _read_row(db_path, "PROP-EDGE-24H")
        assert row["status"] == "APPROVED"


def test_fresh_approved_proposal_is_untouched():
    """APPROVED and 1h old -> stays APPROVED."""
    with _isolated_queue() as (queue, db_path):
        _insert_row(
            db_path,
            proposal_id="PROP-FRESH",
            status="APPROVED",
            created_at=FIXED_NOW - timedelta(hours=1),
            reviewed_at=FIXED_NOW - timedelta(minutes=30),
        )

        transitioned = queue.expire_stale_approved_proposals(
            max_age_hours=24, now=FIXED_NOW
        )

        assert transitioned == 0
        row = _read_row(db_path, "PROP-FRESH")
        assert row["status"] == "APPROVED"


def test_non_approved_statuses_are_never_expired_regardless_of_age():
    """PENDING, REJECTED, and EXECUTED rows are protected even when ancient."""
    with _isolated_queue() as (queue, db_path):
        ancient = FIXED_NOW - timedelta(days=30)

        _insert_row(
            db_path,
            proposal_id="PROP-OLD-PENDING",
            status="PENDING",
            created_at=ancient,
        )
        _insert_row(
            db_path,
            proposal_id="PROP-OLD-REJECTED",
            status="REJECTED",
            created_at=ancient,
            reviewed_at=ancient + timedelta(minutes=5),
        )
        _insert_row(
            db_path,
            proposal_id="PROP-OLD-EXECUTED",
            status="EXECUTED",
            created_at=ancient,
            reviewed_at=ancient + timedelta(minutes=5),
        )

        transitioned = queue.expire_stale_approved_proposals(
            max_age_hours=24, now=FIXED_NOW
        )

        assert transitioned == 0
        assert _read_row(db_path, "PROP-OLD-PENDING")["status"] == "PENDING"
        assert _read_row(db_path, "PROP-OLD-REJECTED")["status"] == "REJECTED"
        assert _read_row(db_path, "PROP-OLD-EXECUTED")["status"] == "EXECUTED"


def test_expiration_preserves_original_reviewed_at():
    """
    reviewed_at was set when the operator approved. Expiring the row must
    NOT overwrite that timestamp — the audit trail of "when did the human
    say yes" is more important than "when did the system time it out."
    """
    with _isolated_queue() as (queue, db_path):
        original_reviewed_at = FIXED_NOW - timedelta(hours=48)
        _insert_row(
            db_path,
            proposal_id="PROP-PRESERVE-REVIEWED",
            status="APPROVED",
            created_at=FIXED_NOW - timedelta(hours=50),
            reviewed_at=original_reviewed_at,
        )

        transitioned = queue.expire_stale_approved_proposals(
            max_age_hours=24, now=FIXED_NOW
        )

        assert transitioned == 1
        row = _read_row(db_path, "PROP-PRESERVE-REVIEWED")
        assert row["status"] == "EXPIRED"
        assert row["reviewed_at"] == original_reviewed_at.isoformat()


def test_idempotent_second_call_transitions_zero_rows():
    """
    First call transitions the stale APPROVED row. Second call, on the
    same DB with the same `now`, must transition nothing.
    """
    with _isolated_queue() as (queue, db_path):
        _insert_row(
            db_path,
            proposal_id="PROP-IDEMPOTENT",
            status="APPROVED",
            created_at=FIXED_NOW - timedelta(hours=30),
            reviewed_at=FIXED_NOW - timedelta(hours=25),
        )

        first = queue.expire_stale_approved_proposals(
            max_age_hours=24, now=FIXED_NOW
        )
        second = queue.expire_stale_approved_proposals(
            max_age_hours=24, now=FIXED_NOW
        )

        assert first == 1
        assert second == 0
        row = _read_row(db_path, "PROP-IDEMPOTENT")
        assert row["status"] == "EXPIRED"


def test_returns_count_of_rows_transitioned():
    """
    Contract: the operation returns the integer number of rows it moved
    from APPROVED to EXPIRED on THIS call (not a cumulative total).
    """
    with _isolated_queue() as (queue, db_path):
        # Three stale APPROVED, one fresh APPROVED, one old PENDING.
        _insert_row(
            db_path, proposal_id="PROP-STALE-1",
            status="APPROVED",
            created_at=FIXED_NOW - timedelta(hours=25),
        )
        _insert_row(
            db_path, proposal_id="PROP-STALE-2",
            status="APPROVED",
            created_at=FIXED_NOW - timedelta(hours=48),
        )
        _insert_row(
            db_path, proposal_id="PROP-STALE-3",
            status="APPROVED",
            created_at=FIXED_NOW - timedelta(days=7),
        )
        _insert_row(
            db_path, proposal_id="PROP-FRESH-1",
            status="APPROVED",
            created_at=FIXED_NOW - timedelta(hours=2),
        )
        _insert_row(
            db_path, proposal_id="PROP-OLD-PENDING-1",
            status="PENDING",
            created_at=FIXED_NOW - timedelta(days=10),
        )

        transitioned = queue.expire_stale_approved_proposals(
            max_age_hours=24, now=FIXED_NOW
        )

        assert transitioned == 3


def test_max_age_hours_is_caller_supplied_not_hard_coded():
    """
    Contract: the method must NOT hard-code 24. A caller passing
    max_age_hours=1 with a 2h-old APPROVED row must expire that row.
    """
    with _isolated_queue() as (queue, db_path):
        _insert_row(
            db_path,
            proposal_id="PROP-TIGHT-THRESHOLD",
            status="APPROVED",
            created_at=FIXED_NOW - timedelta(hours=2),
        )

        transitioned = queue.expire_stale_approved_proposals(
            max_age_hours=1, now=FIXED_NOW
        )

        assert transitioned == 1
        assert _read_row(db_path, "PROP-TIGHT-THRESHOLD")["status"] == "EXPIRED"


def test_now_parameter_is_injectable_no_wall_clock_dependency():
    """
    Same row, two DIFFERENT `now` values, different results.
    Confirms the method takes `now` seriously rather than calling
    datetime.now() internally.
    """
    with _isolated_queue() as (queue, db_path):
        _insert_row(
            db_path,
            proposal_id="PROP-INJECTABLE-TIME",
            status="APPROVED",
            created_at=FIXED_NOW - timedelta(hours=12),
        )

        # `now` at the created_at moment -> row is 0h old -> fresh.
        early_transitioned = queue.expire_stale_approved_proposals(
            max_age_hours=24,
            now=FIXED_NOW - timedelta(hours=12),
        )
        assert early_transitioned == 0
        assert _read_row(db_path, "PROP-INJECTABLE-TIME")["status"] == "APPROVED"

        # `now` 48h after created_at -> row is 48h old -> expired.
        late_transitioned = queue.expire_stale_approved_proposals(
            max_age_hours=24,
            now=FIXED_NOW + timedelta(hours=36),
        )

        assert late_transitioned == 1
        assert _read_row(db_path, "PROP-INJECTABLE-TIME")["status"] == "EXPIRED"
