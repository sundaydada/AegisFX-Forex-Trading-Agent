"""
Import-safe loader for the dashboard's approval-queue snapshot.

This module has ZERO top-level executable behavior beyond the one import
and the one function definition. Importing it does not open a database,
touch the filesystem, load .env, construct a broker, or import Streamlit.
That is what makes it testable without spinning up the full dashboard.

The single public function `load_approval_queue_snapshot` performs the
persistent APPROVED -> EXPIRED transition FIRST, then reads the three
lists the dashboard renders. The caller owns the freshness threshold
(no default), so the runner, gate, and dashboard can all pass the same
value from execution.autonomy_gate.DEFAULT_PROPOSAL_MAX_AGE_HOURS
without this module coupling to it.
"""

from ai.proposal_approval_queue import ProposalApprovalQueue


def load_approval_queue_snapshot(
    db_path: str,
    max_age_hours: float,
) -> dict:
    """
    Expire stale APPROVED rows, then return the three lists the dashboard
    renders in the "AI Approval Queue" section.

    Args:
        db_path: Filesystem path to the approval-queue SQLite database.
        max_age_hours: Freshness threshold. Rows with status = 'APPROVED'
            older than this value are transitioned to 'EXPIRED' BEFORE the
            approved list is fetched, so a stale row never appears in the
            "awaiting execution" count.

    Returns:
        {
            "pending":  List[Dict],   # rows still awaiting operator decision
            "approved": List[Dict],   # fresh APPROVED rows (post-expiry)
            "recent":   List[Dict],   # last 10 non-PENDING decisions
        }

    Raises:
        Any exception from ProposalApprovalQueue.__init__, from
        expire_stale_approved_proposals, or from the three read methods
        propagates unchanged. Callers see failures.
    """
    queue = ProposalApprovalQueue(db_path=db_path)
    try:
        queue.expire_stale_approved_proposals(max_age_hours=max_age_hours)
        pending = queue.get_pending_proposals()
        approved = queue.get_approved_proposals()
        recent = queue.get_recent_decisions(limit=10)
        return {
            "pending": pending,
            "approved": approved,
            "recent": recent,
        }
    finally:
        queue.close()
