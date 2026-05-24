import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional


class RegimeTransitionTracker:
    """
    SQLite-backed tracker for regime transitions.
    Observational only — never consulted by execution.
    """

    def __init__(self, db_path: str = "regime_transitions.db"):
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS regime_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                regime TEXT NOT NULL,
                confidence INTEGER NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS transitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_regime TEXT NOT NULL,
                to_regime TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                confidence INTEGER NOT NULL
            )
        """)
        self._conn.commit()

    def record_regime(self, regime: str, confidence: int) -> Optional[Dict]:
        """
        Record a regime observation. If it differs from the last regime,
        also log a transition (ignoring UNKNOWN -> UNKNOWN).

        Returns the transition dict if one occurred, else None.
        """
        timestamp = datetime.now(timezone.utc).isoformat()

        latest = self.get_latest_regime()
        previous_regime = latest["regime"] if latest else None

        # Always log the regime observation
        self._conn.execute(
            "INSERT INTO regime_log (timestamp, regime, confidence) VALUES (?, ?, ?)",
            (timestamp, regime, int(confidence)),
        )

        transition = None
        if previous_regime is not None and previous_regime != regime:
            if not (previous_regime == "UNKNOWN" and regime == "UNKNOWN"):
                transition = {
                    "from_regime": previous_regime,
                    "to_regime": regime,
                    "timestamp": timestamp,
                    "confidence": int(confidence),
                }
                self._conn.execute(
                    "INSERT INTO transitions (from_regime, to_regime, timestamp, confidence) VALUES (?, ?, ?, ?)",
                    (previous_regime, regime, timestamp, int(confidence)),
                )

        self._conn.commit()
        return transition

    def get_latest_regime(self) -> Optional[Dict]:
        """Return the most recently recorded regime observation, or None."""
        cursor = self._conn.execute(
            "SELECT timestamp, regime, confidence FROM regime_log "
            "ORDER BY id DESC LIMIT 1"
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {"timestamp": row[0], "regime": row[1], "confidence": row[2]}

    def detect_transition(self, new_regime: str) -> bool:
        """
        Returns True if new_regime differs from the latest stored regime.
        Does not record anything. Ignores UNKNOWN -> UNKNOWN.
        """
        latest = self.get_latest_regime()
        if latest is None:
            return False
        previous = latest["regime"]
        if previous == new_regime:
            return False
        if previous == "UNKNOWN" and new_regime == "UNKNOWN":
            return False
        return True

    def get_recent_transitions(self, limit: int = 10) -> List[Dict]:
        """Return up to `limit` most recent transitions, newest first."""
        cursor = self._conn.execute(
            "SELECT from_regime, to_regime, timestamp, confidence FROM transitions "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        )

        results = []
        for row in cursor:
            results.append({
                "from_regime": row[0],
                "to_regime": row[1],
                "timestamp": row[2],
                "confidence": row[3],
            })
        return results

    def close(self):
        self._conn.close()
