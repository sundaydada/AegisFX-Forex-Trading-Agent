"""
Stale Proposal Cleanup Utility

Archives stale AI proposals in proposal_approvals.db by transitioning them
from PENDING/APPROVED -> REJECTED. Stale = older than 24 hours.

Usage:
    python tools/cleanup_stale_proposals.py --dry-run   # preview only
    python tools/cleanup_stale_proposals.py --apply     # commit changes

Safety guarantees enforced by this script:
    - EXECUTED proposals are NEVER touched
    - already-REJECTED proposals are NEVER touched
    - rows are NEVER deleted (audit history preserved)
    - dry-run is the default — accidental invocation does nothing
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

DB_PATH = "proposal_approvals.db"
STALE_THRESHOLD_HOURS = 24


def _parse_created_at(value):
    """Parse stored ISO-8601 created_at -> aware UTC datetime, or None."""
    if not value or not isinstance(value, str):
        return None
    try:
        normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
        dt = datetime.fromisoformat(normalized)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _age_hours(created_at_dt, now):
    if created_at_dt is None:
        return None
    seconds = (now - created_at_dt).total_seconds()
    return max(0.0, seconds / 3600.0)


def main():
    parser = argparse.ArgumentParser(
        description="Archive stale AI proposals (PENDING/APPROVED older than 24h) "
                    "by transitioning their status to REJECTED."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="(default) Show what would change without writing.")
    mode.add_argument("--apply", action="store_true",
                      help="Commit the status transitions to the database.")
    parser.add_argument("--db", default=DB_PATH,
                        help=f"Path to approval DB (default: {DB_PATH})")
    parser.add_argument("--threshold-hours", type=float, default=STALE_THRESHOLD_HOURS,
                        help=f"Staleness threshold in hours (default: {STALE_THRESHOLD_HOURS})")
    args = parser.parse_args()

    # If neither flag is passed, default to dry-run (safe default).
    is_dry_run = args.dry_run or not args.apply

    now = datetime.now(timezone.utc)

    print("=" * 72)
    print("AegisFX Stale Proposal Cleanup")
    print(f"Database:           {os.path.abspath(args.db)}")
    print(f"Now (UTC):          {now.isoformat()}")
    print(f"Staleness cutoff:   > {args.threshold_hours:.1f} hours old")
    print(f"Mode:               {'DRY-RUN (no writes)' if is_dry_run else 'APPLY (writes will commit)'}")
    print("=" * 72)

    if not os.path.exists(args.db):
        print()
        print(f"ERROR: database not found at {os.path.abspath(args.db)}")
        return 2

    # ---- READ phase ----
    # Open read-only first for the scan so we cannot accidentally write.
    ro_conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    ro_cur = ro_conn.cursor()

    ro_cur.execute(
        "SELECT proposal_id, pair, direction, confidence, strategy, status, created_at, reviewed_at "
        "FROM approval_queue"
    )
    rows = ro_cur.fetchall()
    ro_conn.close()

    total_scanned = len(rows)
    pending_total = 0
    approved_total = 0
    executed_total = 0
    rejected_total = 0

    stale_pending = []
    stale_approved = []
    fresh_pending = 0
    fresh_approved = 0

    for row in rows:
        proposal_id, pair, direction, confidence, strategy, status, created_at, reviewed_at = row
        if status == "PENDING":
            pending_total += 1
        elif status == "APPROVED":
            approved_total += 1
        elif status == "EXECUTED":
            executed_total += 1
        elif status == "REJECTED":
            rejected_total += 1

        if status not in ("PENDING", "APPROVED"):
            continue

        created_dt = _parse_created_at(created_at)
        age = _age_hours(created_dt, now)

        # Missing/malformed timestamps: treat as stale (cannot prove freshness)
        if age is None or age > args.threshold_hours:
            target = stale_pending if status == "PENDING" else stale_approved
            target.append({
                "proposal_id": proposal_id,
                "pair": pair,
                "direction": direction,
                "confidence": confidence,
                "strategy": strategy,
                "status": status,
                "created_at": created_at,
                "age_hours": age,
            })
        else:
            if status == "PENDING":
                fresh_pending += 1
            else:
                fresh_approved += 1

    # ---- Show what was found ----
    print()
    print("--- Scan ---")
    print(f"  Total rows scanned:           {total_scanned}")
    print(f"  PENDING (total):              {pending_total}")
    print(f"  APPROVED (total):             {approved_total}")
    print(f"  EXECUTED (total, untouched):  {executed_total}")
    print(f"  REJECTED (total, untouched):  {rejected_total}")
    print()
    print("--- Stale Candidates ---")
    print(f"  PENDING  -> stale: {len(stale_pending)}")
    print(f"  APPROVED -> stale: {len(stale_approved)}")

    def _print_sample(label, items, limit=5):
        if not items:
            return
        print(f"\n  {label} ({len(items)} total, showing up to {limit}):")
        for it in items[:limit]:
            age_disp = "?" if it["age_hours"] is None else f"{it['age_hours']:.1f}h"
            print(f"    {it['proposal_id']} | {it['pair']:7s} | {it['direction']:5s} | "
                  f"conf {it['confidence']:>3}% | {it['strategy']:18s} | age {age_disp}")
        if len(items) > limit:
            print(f"    ... and {len(items) - limit} more")

    _print_sample("Stale PENDING", stale_pending)
    _print_sample("Stale APPROVED", stale_approved)

    # ---- WRITE phase (if --apply) ----
    transitioned_pending = 0
    transitioned_approved = 0
    write_failed = []

    if is_dry_run:
        print()
        print("--- Dry Run: NO writes performed ---")
        print(f"  Would transition {len(stale_pending)} PENDING  -> REJECTED")
        print(f"  Would transition {len(stale_approved)} APPROVED -> REJECTED")
    else:
        print()
        print("--- Apply: committing status transitions ---")

        rw_conn = sqlite3.connect(args.db)
        rw_cur = rw_conn.cursor()
        now_iso = now.isoformat()

        try:
            # Single atomic transaction
            rw_cur.execute("BEGIN")
            for it in stale_pending + stale_approved:
                # Defensive: only update if still in expected status,
                # so a concurrent operator decision is never overwritten.
                rw_cur.execute(
                    "UPDATE approval_queue "
                    "SET status = 'REJECTED', reviewed_at = ? "
                    "WHERE proposal_id = ? AND status IN ('PENDING', 'APPROVED')",
                    (now_iso, it["proposal_id"]),
                )
                if rw_cur.rowcount == 1:
                    if it["status"] == "PENDING":
                        transitioned_pending += 1
                    else:
                        transitioned_approved += 1
                else:
                    write_failed.append(it["proposal_id"])
            rw_conn.commit()
        except Exception as e:
            rw_conn.rollback()
            print(f"  ERROR: transaction rolled back: {e}")
            rw_conn.close()
            return 3
        finally:
            rw_conn.close()

        print(f"  Transitioned PENDING  -> REJECTED: {transitioned_pending}")
        print(f"  Transitioned APPROVED -> REJECTED: {transitioned_approved}")
        if write_failed:
            print(f"  Skipped (state changed since scan): {len(write_failed)}")

    # ---- Summary ----
    remaining_pending = (pending_total
                         - (transitioned_pending if not is_dry_run else 0))
    remaining_approved = (approved_total
                          - (transitioned_approved if not is_dry_run else 0))

    print()
    print("=" * 72)
    print("Summary")
    print("=" * 72)
    print(f"  Total scanned:               {total_scanned}")
    print(f"  Stale PENDING rejected:      "
          f"{'(would be) ' if is_dry_run else ''}"
          f"{transitioned_pending if not is_dry_run else len(stale_pending)}")
    print(f"  Stale APPROVED rejected:     "
          f"{'(would be) ' if is_dry_run else ''}"
          f"{transitioned_approved if not is_dry_run else len(stale_approved)}")
    print(f"  Remaining PENDING:           "
          f"{'(unchanged) ' if is_dry_run else ''}{remaining_pending}")
    print(f"  Remaining APPROVED:          "
          f"{'(unchanged) ' if is_dry_run else ''}{remaining_approved}")
    print(f"  EXECUTED (untouched):        {executed_total}")
    print(f"  REJECTED (untouched):        {rejected_total}")
    print("=" * 72)

    if is_dry_run and (stale_pending or stale_approved):
        print()
        print("Re-run with --apply to commit these transitions.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
