"""
Contract test for the not-yet-created import-safe dashboard helper:

    dashboard.approval_snapshot.load_approval_queue_snapshot(
        db_path: str,
        max_age_hours: float,
    ) -> dict

The helper must:
  1. Open a real ProposalApprovalQueue against db_path.
  2. Call queue.expire_stale_approved_proposals(max_age_hours=...) FIRST so
     the persistent APPROVED -> EXPIRED transition is durable, not a runtime
     filter.
  3. Fetch pending, approved (post-expiry), and recent decisions.
  4. Close the queue.
  5. Return {"pending": [...], "approved": [...], "recent": [...]}.

This test proves persistence, not visual filtering. After the helper runs, a
direct read-only reopen of the SQLite file must show the stale row's status
is EXPIRED on disk — the row is not merely hidden from the returned list.

Test-harness conventions follow tests/test_proposal_approval_queue_expiration.py:
  * contextlib.closing(sqlite3.connect(...)) so Windows releases the file
    handle deterministically (a bare `with sqlite3.connect(...) as conn`
    only commits/rolls back — it does NOT close).
  * Nested `with conn:` inside the closing context to preserve transaction
    commit/rollback semantics on the INSERT.
  * pytest tmp_path for isolation; pytest cleans it up after the test.
  * The real proposal_approvals.db is never opened.

The import of load_approval_queue_snapshot is placed INSIDE the test body so
pytest can collect this file even while the module does not exist. The first
run will fail deterministically with:
    ModuleNotFoundError: No module named 'dashboard.approval_snapshot'
which is the desired intentional-red signal.
"""

import contextlib
import sqlite3
from datetime import datetime, timedelta, timezone

from ai.proposal_approval_queue import ProposalApprovalQueue


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
    # contextlib.closing owns handle disposal on Windows. The nested `with
    # conn:` block preserves transaction commit/rollback semantics on the
    # INSERT — see tests/test_proposal_approval_queue_expiration.py for the
    # full rationale.
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


def test_dashboard_snapshot_expires_stale_approved_before_counting_awaiting(tmp_path):
    """
    One focused regression: the dashboard's future import-safe loader must
    trigger the persistent APPROVED -> EXPIRED transition BEFORE returning
    the list the dashboard uses to render "N approved proposal(s) —
    awaiting execution."

    Seeded lifecycle states, all with timezone-aware UTC timestamps:
      * PROP-STALE-APPROVED  -- APPROVED, created 48h ago (> 24h threshold)
      * PROP-FRESH-APPROVED  -- APPROVED, created 1h ago  (<< 24h threshold)
      * PROP-OLD-PENDING     -- PENDING,  created 7d ago  (age is irrelevant
                                for PENDING; must never be touched)

    Assertions after one call to load_approval_queue_snapshot(..., 24):
      * snapshot.keys() == {"pending", "approved", "recent"}   (contract)
      * PROP-FRESH-APPROVED  is in snapshot["approved"]        (survives)
      * PROP-STALE-APPROVED  is NOT in snapshot["approved"]    (expired)
      * PROP-OLD-PENDING     is in snapshot["pending"]         (untouched)
      * PROP-STALE-APPROVED  is in snapshot["recent"] with status EXPIRED

    Persistence proof: reopen the same SQLite file read-only and confirm
    the stale row's on-disk status is EXPIRED (not a runtime filter) and
    that its original reviewed_at is preserved (audit history is intact).

    Expected red failure until dashboard/approval_snapshot.py exists:
        ModuleNotFoundError: No module named 'dashboard.approval_snapshot'
    """
    # Import is deliberately inside the test body so pytest can COLLECT
    # this file cleanly. When the module is absent the test fails at this
    # line with a clear ModuleNotFoundError, which is the intended red
    # signal. Do not lift this to the module top.
    from dashboard.approval_snapshot import load_approval_queue_snapshot

    db_path = str(tmp_path / "approvals.db")

    # Materialize the real production schema (including the WAL PRAGMA and
    # the migrate step) by constructing and immediately closing the real
    # queue. The seed rows below are then written via direct SQL so we can
    # plant historical created_at / reviewed_at values the public API does
    # not accept.
    queue = ProposalApprovalQueue(db_path=db_path)
    queue.close()

    now = datetime.now(timezone.utc)
    stale_created_at = now - timedelta(hours=48)
    stale_reviewed_at = now - timedelta(hours=40)
    fresh_created_at = now - timedelta(hours=1)
    fresh_reviewed_at = now - timedelta(minutes=30)
    old_pending_created_at = now - timedelta(days=7)

    _insert_row(
        db_path,
        proposal_id="PROP-STALE-APPROVED",
        status="APPROVED",
        created_at=stale_created_at,
        reviewed_at=stale_reviewed_at,
    )
    _insert_row(
        db_path,
        proposal_id="PROP-FRESH-APPROVED",
        status="APPROVED",
        created_at=fresh_created_at,
        reviewed_at=fresh_reviewed_at,
    )
    _insert_row(
        db_path,
        proposal_id="PROP-OLD-PENDING",
        status="PENDING",
        created_at=old_pending_created_at,
    )

    snapshot = load_approval_queue_snapshot(
        db_path=db_path,
        max_age_hours=24,
    )

    assert set(snapshot.keys()) == {"pending", "approved", "recent"}, (
        "Snapshot keys must be exactly {'pending', 'approved', 'recent'}; "
        f"got {sorted(snapshot.keys())!r}."
    )

    approved_ids = {p["proposal_id"] for p in snapshot["approved"]}
    assert "PROP-FRESH-APPROVED" in approved_ids, (
        "Fresh APPROVED row must appear in snapshot['approved']. "
        f"Got: {approved_ids!r}."
    )
    assert "PROP-STALE-APPROVED" not in approved_ids, (
        "Stale APPROVED row must NOT appear in snapshot['approved']; it "
        "should have been transitioned to EXPIRED before the fetch. "
        f"Got: {approved_ids!r}."
    )

    pending_ids = {p["proposal_id"] for p in snapshot["pending"]}
    assert "PROP-OLD-PENDING" in pending_ids, (
        "Old PENDING row must remain in snapshot['pending'] — expiration "
        f"must never touch PENDING rows. Got: {pending_ids!r}."
    )

    recent_stale = [
        r for r in snapshot["recent"]
        if r["proposal_id"] == "PROP-STALE-APPROVED"
    ]
    assert len(recent_stale) == 1, (
        "Stale proposal must appear exactly once in snapshot['recent']. "
        f"Got: {recent_stale!r}."
    )
    assert recent_stale[0]["status"] == "EXPIRED", (
        "Stale proposal in snapshot['recent'] must carry status EXPIRED; "
        f"got {recent_stale[0]['status']!r}."
    )

    # Persistence proof: reopen the SQLite file read-only and confirm the
    # transition is on-disk, not merely a runtime filter in the helper.
    # See _insert_row for why contextlib.closing() is required.
    with contextlib.closing(
        sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    ) as conn:
        cur = conn.execute(
            "SELECT status, reviewed_at FROM approval_queue "
            "WHERE proposal_id = ?",
            ("PROP-STALE-APPROVED",),
        )
        row = cur.fetchone()

    assert row is not None, "Stale proposal row must still exist on disk."
    assert row[0] == "EXPIRED", (
        "Persisted status of the stale row must be EXPIRED — the "
        "transition must be durable, not a runtime filter. "
        f"Got: {row[0]!r}."
    )
    assert row[1] == stale_reviewed_at.isoformat(), (
        "Original reviewed_at of the stale row must be preserved unchanged "
        "(audit history invariant). "
        f"Expected {stale_reviewed_at.isoformat()!r}, got {row[1]!r}."
    )
