import sqlite3
import hashlib
from datetime import datetime, timezone
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

    def _create_table(self):
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
                reviewed_at TEXT
            )
        """)
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

            try:
                self._conn.execute(
                    """
                    INSERT INTO approval_queue (
                        proposal_id, pair, direction, suggested_size,
                        confidence, strategy, reason, status, created_at, reviewed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
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
                    ),
                )
                added += 1
            except sqlite3.IntegrityError:
                # Duplicate proposal_id — silently ignore
                continue

        self._conn.commit()
        return added

    def get_pending_proposals(self) -> List[Dict]:
        """Return all PENDING proposals, oldest first."""
        cursor = self._conn.execute(
            """
            SELECT proposal_id, pair, direction, suggested_size, confidence,
                   strategy, reason, status, created_at, reviewed_at
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
            """
            SELECT proposal_id, pair, direction, suggested_size, confidence,
                   strategy, reason, status, created_at, reviewed_at
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
        }

    def close(self):
        self._conn.close()
