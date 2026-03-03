import sqlite3
import json
from typing import List, Dict


class PersistentTradeStateManager:
    """
    SQLite-backed trade ledger with transactional guarantees.
    Drop-in replacement for TradeStateManager.
    No risk logic. No execution logic.
    """

    def __init__(self, db_path: str = "trade_state.db"):
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                currency_pair TEXT NOT NULL,
                direction TEXT NOT NULL,
                position_size REAL NOT NULL,
                fill_price REAL NOT NULL,
                stop_loss_price REAL NOT NULL,
                take_profit_price REAL NOT NULL,
                timestamp TEXT NOT NULL,
                execution_status TEXT NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_requests (
                request_id TEXT PRIMARY KEY,
                result_json TEXT NOT NULL
            )
        """)
        self._conn.commit()

    def record_trade(self, trade: Dict) -> None:
        if trade.get("execution_status") != "Filled":
            return

        self._conn.execute(
            """
            INSERT INTO trades (
                currency_pair, direction, position_size,
                fill_price, stop_loss_price, take_profit_price,
                timestamp, execution_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade["currency_pair"],
                trade["direction"],
                trade["position_size"],
                trade["fill_price"],
                trade["stop_loss_price"],
                trade["take_profit_price"],
                trade["timestamp"],
                trade["execution_status"],
            ),
        )
        self._conn.commit()

    def get_all_trades(self) -> List[Dict]:
        cursor = self._conn.execute(
            """
            SELECT currency_pair, direction, position_size,
                   fill_price, stop_loss_price, take_profit_price,
                   timestamp, execution_status
            FROM trades
            """
        )

        trades = []
        for row in cursor:
            trades.append({
                "currency_pair": row[0],
                "direction": row[1],
                "position_size": row[2],
                "fill_price": row[3],
                "stop_loss_price": row[4],
                "take_profit_price": row[5],
                "timestamp": row[6],
                "execution_status": row[7],
            })

        return trades

    def has_processed(self, request_id: str) -> bool:
        cursor = self._conn.execute(
            "SELECT 1 FROM processed_requests WHERE request_id = ?",
            (request_id,),
        )
        return cursor.fetchone() is not None

    def get_processed_result(self, request_id: str) -> Dict:
        cursor = self._conn.execute(
            "SELECT result_json FROM processed_requests WHERE request_id = ?",
            (request_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        return json.loads(row[0])

    def record_processed_result(self, request_id: str, result: Dict) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO processed_requests (request_id, result_json)
            VALUES (?, ?)
            """,
            (request_id, json.dumps(result)),
        )
        self._conn.commit()

    def begin_transaction(self):
        self._conn.execute("BEGIN")

    def commit_transaction(self):
        self._conn.commit()

    def rollback_transaction(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()
