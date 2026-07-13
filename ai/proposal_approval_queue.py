import sqlite3
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Dict, List


class ProposalApprovalQueue:
    """
    Persistent approval queue for AI trade proposals.
    Stores proposals as PENDING; humans transition them to APPROVED or REJECTED.
    Observational only — does not call broker or orchestrator.
    """

    def __init__(self, db_path: str = "proposal_approvals.db"):
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_table()
        self._migrate_schema()

    def _create_table(self):
        # Note: new columns added here are also added defensively in
        # _migrate_schema() so existing databases pick them up too.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS approval_queue (
                proposal_id TEXT PRIMARY KEY,
                pair TEXT NOT NULL,
                direction TEXT NOT NULL,
                suggested_size REAL NOT NULL,
                confidence INTEGER NOT NULL,
                strategy TEXT NOT NULL,
                reason TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                reviewed_at TEXT,
                execution_allowed INTEGER NOT NULL DEFAULT 1,
                risk_mode TEXT NOT NULL DEFAULT 'REDUCED'
            )
        """)
        self._conn.commit()

    def _migrate_schema(self):
        """
        Idempotent ALTER TABLE migrations for existing databases.

        SQLite has no `ALTER TABLE ADD COLUMN IF NOT EXISTS`, so we check
        the current column list and only add what's missing. Existing rows
        receive the DEFAULT value automatically.
        """
        cursor = self._conn.execute("PRAGMA table_info(approval_queue)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        if "execution_allowed" not in existing_columns:
            self._conn.execute(
                "ALTER TABLE approval_queue "
                "ADD COLUMN execution_allowed INTEGER NOT NULL DEFAULT 1"
            )

        if "risk_mode" not in existing_columns:
            self._conn.execute(
                "ALTER TABLE approval_queue "
                "ADD COLUMN risk_mode TEXT NOT NULL DEFAULT 'REDUCED'"
            )

        self._conn.commit()

    @staticmethod
    def _build_proposal_id(proposal: Dict) -> str:
        """Deterministic hash from proposal fields — same proposal yields same ID."""
        seed = "|".join([
            str(proposal.get("pair", "")),
            str(proposal.get("direction", "")),
            str(proposal.get("suggested_size", "")),
            str(proposal.get("strategy", "")),
            str(proposal.get("confidence", "")),
            str(proposal.get("reason", "")),
        ])
        return "PROP-" + hashlib.sha256(seed.encode()).hexdigest()[:16]

    def add_proposals(self, proposals: List[Dict]) -> int:
        """
        Add a list of proposals to the queue.
        Duplicates (same proposal_id) are ignored safely.
        Returns count of newly added proposals.
        """
        added = 0
        now = datetime.now(timezone.utc).isoformat()

        for p in proposals:
            proposal_id = p.get("proposal_id") or self._build_proposal_id(p)

            # Defaults match the migration's column defaults so old code
            # paths that don't supply these keys still produce safe rows.
            execution_allowed_int = 1 if bool(p.get("execution_allowed", True)) else 0
            risk_mode_value = p.get("risk_mode") or "REDUCED"

            try:
                self._conn.execute(
                    """
                    INSERT INTO approval_queue (
                        proposal_id, pair, direction, suggested_size,
                        confidence, strategy, reason, status, created_at, reviewed_at,
                        execution_allowed, risk_mode
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                    """,
                    (
                        proposal_id,
                        p.get("pair", ""),
                        p.get("direction", ""),
                        float(p.get("suggested_size", 0.0)),
                        int(p.get("confidence", 0)),
                        p.get("strategy", ""),
                        p.get("reason", ""),
                        "PENDING",
                        now,
                        execution_allowed_int,
                        risk_mode_value,
                    ),
                )
                added += 1
            except sqlite3.IntegrityError:
                # Duplicate proposal_id — silently ignore
                continue

        self._conn.commit()
        return added

    # Single source of truth for the SELECT column order. _row_to_dict
    # below maps this exact tuple. Add new columns here AND in
    # _row_to_dict() together.
    _SELECT_COLUMNS = (
        "proposal_id, pair, direction, suggested_size, confidence, "
        "strategy, reason, status, created_at, reviewed_at, "
        "execution_allowed, risk_mode"
    )

    def get_pending_proposals(self) -> List[Dict]:
        """Return all PENDING proposals, oldest first."""
        cursor = self._conn.execute(
            f"""
            SELECT {self._SELECT_COLUMNS}
            FROM approval_queue
            WHERE status = 'PENDING'
            ORDER BY created_at ASC
            """
        )
        return [self._row_to_dict(row) for row in cursor]

    def approve_proposal(self, proposal_id: str) -> bool:
        """Approve a PENDING proposal. Returns True if state changed."""
        return self._transition(proposal_id, "APPROVED")

    def reject_proposal(self, proposal_id: str) -> bool:
        """Reject a PENDING proposal. Returns True if state changed."""
        return self._transition(proposal_id, "REJECTED")

    def mark_executed(self, proposal_id: str) -> bool:
        """Mark an APPROVED proposal as EXECUTED. Returns True if state changed."""
        now = datetime.now(timezone.utc).isoformat()
        cursor = self._conn.execute(
            """
            UPDATE approval_queue
            SET status = 'EXECUTED', reviewed_at = ?
            WHERE proposal_id = ? AND status = 'APPROVED'
            """,
            (now, proposal_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def expire_stale_approved_proposals(self, max_age_hours, now=None) -> int:
        """
        Transition APPROVED rows older than `max_age_hours` to status 'EXPIRED'.

        Only rows where status = 'APPROVED' AND created_at is non-NULL AND
        the age (now - created_at) is strictly greater than max_age_hours
        are affected. Rows exactly at the threshold remain APPROVED, so
        this method's inclusion rule stays consistent with the runtime
        gate's `age <= max_age_hours` freshness check in autonomy_gate.py.

        `reviewed_at` is NOT overwritten — the historical record of WHEN
        the operator approved the proposal is preserved as audit evidence.

        Args:
            max_age_hours: Numeric hours; ValueError if negative.
            now: Optional datetime. If None, timezone-aware UTC "now" is
                used. Aware datetimes are normalized to UTC. Naive
                datetimes are treated as UTC (matches the convention used
                elsewhere in this queue's persistence layer).

        Returns:
            Integer count of rows transitioned by THIS call. Subsequent
            calls with the same inputs return 0 (idempotent).
        """
        # Reject negative thresholds up front. float() coerces ints,
        # floats, and numeric strings; anything else raises TypeError
        # or ValueError, both of which are appropriate errors here.
        max_age_hours = float(max_age_hours)
        if max_age_hours < 0:
            raise ValueError(
                f"max_age_hours must be non-negative, got {max_age_hours!r}"
            )

        # Resolve "now": use caller-supplied when provided, otherwise
        # timezone-aware UTC. Normalize aware datetimes to UTC; treat
        # naive datetimes as UTC (matches how created_at is written).
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        else:
            now = now.astimezone(timezone.utc)

        cutoff = (now - timedelta(hours=max_age_hours)).isoformat()

        # One parameterized UPDATE. julianday() gives us date-aware
        # comparison instead of raw string ordering, so ISO variants
        # (fractional seconds, offsets) all compare correctly. The
        # `created_at IS NOT NULL` clause blocks accidental expiry of
        # malformed rows -- the runtime gate remains defense-in-depth
        # for those.
        cursor = self._conn.execute(
            """
            UPDATE approval_queue
            SET status = 'EXPIRED'
            WHERE status = 'APPROVED'
              AND created_at IS NOT NULL
              AND julianday(created_at) < julianday(?)
            """,
            (cutoff,),
        )
        self._conn.commit()
        return cursor.rowcount

    def get_approved_proposals(self) -> List[Dict]:
        """Return APPROVED proposals (awaiting execution)."""
        cursor = self._conn.execute(
            f"""
            SELECT {self._SELECT_COLUMNS}
            FROM approval_queue
            WHERE status = 'APPROVED'
            ORDER BY reviewed_at ASC
            """
        )
        return [self._row_to_dict(row) for row in cursor]

    def _transition(self, proposal_id: str, new_status: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        cursor = self._conn.execute(
            """
            UPDATE approval_queue
            SET status = ?, reviewed_at = ?
            WHERE proposal_id = ? AND status = 'PENDING'
            """,
            (new_status, now, proposal_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def get_recent_decisions(self, limit: int = 20) -> List[Dict]:
        """Return up to `limit` most recently reviewed proposals."""
        cursor = self._conn.execute(
            f"""
            SELECT {self._SELECT_COLUMNS}
            FROM approval_queue
            WHERE status != 'PENDING'
            ORDER BY reviewed_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [self._row_to_dict(row) for row in cursor]

    @staticmethod
    def _row_to_dict(row) -> Dict:
        # Order MUST match _SELECT_COLUMNS above.
        return {
            "proposal_id": row[0],
            "pair": row[1],
            "direction": row[2],
            "suggested_size": row[3],
            "confidence": row[4],
            "strategy": row[5],
            "reason": row[6],
            "status": row[7],
            "created_at": row[8],
            "reviewed_at": row[9],
            "execution_allowed": bool(row[10]),
            "risk_mode": row[11],
        }

    def close(self):
        self._conn.close()
