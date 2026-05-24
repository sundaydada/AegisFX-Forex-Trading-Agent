import sqlite3
import os
from datetime import datetime, timezone
from typing import Dict, List


class AIAnalysisHistoryManager:
    """
    SQLite-backed AI analysis history.
    Read/write observational data ONLY — never consulted by execution.
    """

    def __init__(self, db_path: str = "ai_analysis_history.db"):
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_table()

    def _create_table(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS analysis_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                regime TEXT NOT NULL,
                confidence INTEGER NOT NULL,
                summary TEXT
            )
        """)
        self._conn.commit()

    def record_analysis(self, ai_result: Dict) -> None:
        """Persist an AI analysis result. Silently no-ops on UNKNOWN regime."""
        regime = ai_result.get("regime", "UNKNOWN")
        if regime == "UNKNOWN":
            return

        try:
            confidence = int(ai_result.get("confidence", 0))
        except (ValueError, TypeError):
            confidence = 0

        summary = str(ai_result.get("summary", ""))
        timestamp = datetime.now(timezone.utc).isoformat()

        self._conn.execute(
            "INSERT INTO analysis_history (timestamp, regime, confidence, summary) VALUES (?, ?, ?, ?)",
            (timestamp, regime, confidence, summary),
        )
        self._conn.commit()

    def get_recent_analysis(self, limit: int = 50) -> List[Dict]:
        """Return up to `limit` most recent analyses, newest first."""
        cursor = self._conn.execute(
            "SELECT timestamp, regime, confidence, summary FROM analysis_history "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        )

        results = []
        for row in cursor:
            results.append({
                "timestamp": row[0],
                "regime": row[1],
                "confidence": row[2],
                "summary": row[3],
            })
        return results

    def get_confidence_trend(self, limit: int = 100) -> List[Dict]:
        """Return confidence values over time (oldest first) for charting."""
        cursor = self._conn.execute(
            "SELECT timestamp, confidence FROM analysis_history "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        )

        rows = list(cursor)
        rows.reverse()  # oldest first for chart

        return [{"timestamp": r[0], "confidence": r[1]} for r in rows]

    def close(self):
        self._conn.close()
