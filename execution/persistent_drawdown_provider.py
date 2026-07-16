"""Persistent high-water NAV drawdown evidence provider.

SQLite-backed, account-and-currency scoped high-water tracking for the
runtime-input resolver's injected drawdown_provider contract:

    provider.get_drawdown_fraction(account_snapshot) -> float

The provider observes validated NAV evidence from an AccountSnapshot,
maintains a monotonic nondecreasing high-water NAV per
(account_id, account_currency) key, and returns:

    max(0.0, (high_water_nav - current_nav) / high_water_nav)

in [0.0, 1.0). The first valid observation for a key persists the
current NAV as the measured baseline and returns 0.0. Corrupt stored
evidence is never repaired, overwritten, or re-baselined — it raises.
Writes run inside a BEGIN IMMEDIATE transaction that validates stored
evidence before any mutation, so a lower observation can never regress
a higher stored value, including across provider instances.
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
    ("high_water_nav", "REAL", 1, None, 0),
    ("initialized_at", "TEXT", 1, None, 0),
    ("updated_at", "TEXT", 1, None, 0),
]

_CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS drawdown_high_water (
        account_id TEXT NOT NULL,
        account_currency TEXT NOT NULL,
        high_water_nav REAL NOT NULL,
        initialized_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (account_id, account_currency)
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
            f"Invalid drawdown evidence: {name} must be a real number"
        )

    try:
        numeric = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(
            f"Invalid drawdown evidence: {name} must be a real number"
        ) from exc

    if not math.isfinite(numeric) or numeric <= 0.0:
        raise ValueError(
            f"Invalid drawdown evidence: {name} must be finite and"
            " greater than zero"
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


def _validate_stored_timestamp(name: str, value) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"Invalid stored drawdown evidence: {name} must be a nonempty"
            " ISO-8601 string"
        )

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"Invalid stored drawdown evidence: {name} is not a valid"
            " ISO-8601 datetime"
        ) from exc

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(
            f"Invalid stored drawdown evidence: {name} must be"
            " timezone-aware"
        )


def _validate_stored_row(row) -> float:
    stored_high_water, initialized_at, updated_at = row
    high_water_nav = _finite_positive_float(
        "stored high_water_nav",
        stored_high_water,
    )
    _validate_stored_timestamp("initialized_at", initialized_at)
    _validate_stored_timestamp("updated_at", updated_at)
    return high_water_nav


class PersistentHighWaterDrawdownProvider:
    """
    Durable monotonic high-water drawdown evidence, keyed by
    (account_id, account_currency). One SQLite connection per instance,
    WAL journal mode, explicit transactions, explicit close().
    No broker, network, dashboard, trade, or proposal coupling.
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
            "PRAGMA table_info(drawdown_high_water)"
        ).fetchall()

        if not rows:
            self._conn.execute(_CREATE_TABLE_SQL)
            rows = self._conn.execute(
                "PRAGMA table_info(drawdown_high_water)"
            ).fetchall()

        if _normalized_table_info(rows) != _EXPECTED_SCHEMA:
            raise sqlite3.DatabaseError(
                "drawdown_high_water table has an incompatible schema;"
                " refusing to migrate, repair, or recreate it"
            )

    def _validated_timestamp(self) -> str:
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
        return moment.astimezone(timezone.utc).isoformat()

    def get_drawdown_fraction(self, account_snapshot) -> float:
        current_nav, currency = _validate_snapshot(account_snapshot)

        self._conn.execute("BEGIN IMMEDIATE")
        try:
            rows = self._conn.execute(
                "SELECT high_water_nav, initialized_at, updated_at"
                " FROM drawdown_high_water"
                " WHERE account_id = ? AND account_currency = ?",
                (self._account_id, currency),
            ).fetchall()

            if not rows:
                timestamp = self._validated_timestamp()
                self._conn.execute(
                    "INSERT INTO drawdown_high_water ("
                    " account_id, account_currency, high_water_nav,"
                    " initialized_at, updated_at"
                    ") VALUES (?, ?, ?, ?, ?)",
                    (
                        self._account_id,
                        currency,
                        current_nav,
                        timestamp,
                        timestamp,
                    ),
                )
                high_water_nav = current_nav
            elif len(rows) > 1:
                raise ValueError(
                    "Invalid stored drawdown evidence: duplicate"
                    " high-water rows for one account and currency"
                )
            else:
                high_water_nav = _validate_stored_row(rows[0])
                if current_nav > high_water_nav:
                    timestamp = self._validated_timestamp()
                    self._conn.execute(
                        "UPDATE drawdown_high_water"
                        " SET high_water_nav = ?, updated_at = ?"
                        " WHERE account_id = ? AND account_currency = ?"
                        " AND ? > high_water_nav",
                        (
                            current_nav,
                            timestamp,
                            self._account_id,
                            currency,
                            current_nav,
                        ),
                    )
                    high_water_nav = current_nav

            self._conn.execute("COMMIT")
        except BaseException:
            try:
                self._conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

        drawdown_fraction = max(
            0.0,
            (high_water_nav - current_nav) / high_water_nav,
        )
        if (
            not math.isfinite(drawdown_fraction)
            or not 0.0 <= drawdown_fraction < 1.0
        ):
            raise ValueError(
                "Invalid drawdown evidence: drawdown_fraction must be"
                " within [0.0, 1.0)"
            )
        return float(drawdown_fraction)

    def close(self) -> None:
        self._conn.close()
