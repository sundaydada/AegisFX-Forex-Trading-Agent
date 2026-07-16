"""Persistent start-of-day NAV baseline provider.

SQLite-backed, immutable first-valid NAV observation per
(account_id, account_currency, UTC calendar date), following the
conventions of execution.persistent_drawdown_provider. The future 2%
daily-loss gate consumes the returned baseline; this module contains no
gate formula, no realized-P&L tracking, and no deposit adjustment.

The first valid observation of a UTC day persists that day's baseline
inside a BEGIN IMMEDIATE transaction (first-write-wins under the write
lock; the composite primary key guards duplicates). Every later
observation on the same UTC day returns the original stored baseline
unchanged, regardless of the later NAV. A new UTC day bootstraps its
own row. Corrupt or incompatible persisted evidence raises and is never
repaired, recreated, or re-baselined.
"""

import math
import sqlite3
from datetime import datetime, timezone
from numbers import Real
from typing import Tuple

from brokers.broker_interface import AccountSnapshot

_EXPECTED_SCHEMA = [
    ("account_id", "TEXT", 1, None, 1),
    ("account_currency", "TEXT", 1, None, 2),
    ("utc_date", "TEXT", 1, None, 3),
    ("start_of_day_nav", "REAL", 1, None, 0),
    ("observed_at", "TEXT", 1, None, 0),
]

_CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS start_of_day_nav (
        account_id TEXT NOT NULL,
        account_currency TEXT NOT NULL,
        utc_date TEXT NOT NULL,
        start_of_day_nav REAL NOT NULL,
        observed_at TEXT NOT NULL,
        PRIMARY KEY (account_id, account_currency, utc_date)
    )
"""


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


def _normalized_table_info(rows) -> list:
    return [
        (row[1], str(row[2]).upper(), row[3], row[4], row[5])
        for row in rows
    ]


def _finite_positive_float(name: str, value) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(
            f"Invalid start-of-day NAV evidence: {name} must be a real"
            " number"
        )

    try:
        numeric = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(
            f"Invalid start-of-day NAV evidence: {name} must be a real"
            " number"
        ) from exc

    if not math.isfinite(numeric) or numeric <= 0.0:
        raise ValueError(
            f"Invalid start-of-day NAV evidence: {name} must be finite"
            " and greater than zero"
        )
    return numeric


def _validate_snapshot(account_snapshot) -> Tuple[float, str]:
    if not isinstance(account_snapshot, AccountSnapshot):
        raise ValueError(
            "Invalid account snapshot evidence: expected an AccountSnapshot"
        )

    nav = _finite_positive_float("nav", account_snapshot.nav)

    currency = account_snapshot.currency
    if not isinstance(currency, str):
        raise ValueError(
            "Invalid account snapshot evidence: currency must be a string"
        )
    normalized_currency = currency.strip().upper()
    if len(normalized_currency) != 3 or not normalized_currency.isalpha():
        raise ValueError(
            "Invalid account snapshot evidence: currency must be exactly"
            " three alphabetic characters"
        )

    return nav, normalized_currency


class PersistentStartOfDayNavProvider:
    """
    Durable immutable first-valid NAV baseline, keyed by
    (account_id, account_currency, utc_date). One SQLite connection per
    instance, WAL journal mode enabled only after schema validation,
    explicit transactions, explicit close(). No broker, network,
    dashboard, trade, or proposal coupling.
    """

    def __init__(self, *, db_path: str, account_id: str, clock=None):
        if not isinstance(account_id, str) or not account_id.strip():
            raise ValueError(
                "Invalid account identity evidence: account_id must be a"
                " nonempty string"
            )
        self._account_id = account_id.strip()
        self._clock = clock if clock is not None else _default_clock

        self._conn = sqlite3.connect(db_path, isolation_level=None)
        try:
            self._ensure_compatible_schema()
            self._conn.execute("PRAGMA journal_mode=WAL")
        except BaseException:
            self._conn.close()
            raise

    def _ensure_compatible_schema(self) -> None:
        rows = self._conn.execute(
            "PRAGMA table_info(start_of_day_nav)"
        ).fetchall()

        if not rows:
            self._conn.execute(_CREATE_TABLE_SQL)
            rows = self._conn.execute(
                "PRAGMA table_info(start_of_day_nav)"
            ).fetchall()

        if _normalized_table_info(rows) != _EXPECTED_SCHEMA:
            raise sqlite3.DatabaseError(
                "start_of_day_nav table has an incompatible schema;"
                " refusing to migrate, repair, or recreate it"
            )

    def _validated_utc_moment(self) -> datetime:
        moment = self._clock()
        if (
            not isinstance(moment, datetime)
            or moment.tzinfo is None
            or moment.utcoffset() is None
        ):
            raise ValueError(
                "Invalid clock evidence: clock must return a"
                " timezone-aware datetime"
            )
        return moment.astimezone(timezone.utc)

    def get_start_of_day_nav(self, account_snapshot) -> float:
        current_nav, currency = _validate_snapshot(account_snapshot)

        utc_moment = self._validated_utc_moment()
        utc_date = utc_moment.date().isoformat()

        self._conn.execute("BEGIN IMMEDIATE")
        try:
            rows = self._conn.execute(
                "SELECT start_of_day_nav FROM start_of_day_nav"
                " WHERE account_id = ? AND account_currency = ?"
                " AND utc_date = ?",
                (self._account_id, currency, utc_date),
            ).fetchall()

            if not rows:
                self._conn.execute(
                    "INSERT INTO start_of_day_nav ("
                    " account_id, account_currency, utc_date,"
                    " start_of_day_nav, observed_at"
                    ") VALUES (?, ?, ?, ?, ?)",
                    (
                        self._account_id,
                        currency,
                        utc_date,
                        current_nav,
                        utc_moment.isoformat(),
                    ),
                )
                baseline_nav = current_nav
            elif len(rows) > 1:
                raise ValueError(
                    "Invalid stored start-of-day NAV evidence: duplicate"
                    " baseline rows for one account, currency, and date"
                )
            else:
                baseline_nav = _finite_positive_float(
                    "stored start_of_day_nav",
                    rows[0][0],
                )

            self._conn.execute("COMMIT")
        except BaseException:
            try:
                self._conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

        return float(baseline_nav)

    def close(self) -> None:
        self._conn.close()
